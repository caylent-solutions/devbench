# Devbench-Remote Infrastructure Architecture

## Goal

Provision a per-developer EC2 workstation pre-loaded with the full devbench toolchain, accessible via AWS SSM (no public ingress), with multi-session devbench support and a copy-and-edit Terragrunt onboarding flow.

## Layered design

```
                   AWS account (set via AWS_ACCOUNT_ID; e.g. 123456789012)
                                  |
                          (one VPC singleton)
                  +-------------------------------+
                  |   VPC (10.42.0.0/16)          |
                  |   Public subnet (10.42.0.0/24)|
                  |   Internet Gateway            |
                  |   SG: 0 ingress / all egress  |
                  +---------------+---------------+
                                  |
       +---------------+----------+----------+----------------+
       |               |                     |                |
   instances/    instances/             instances/     instances/
   <slug-1>/     <slug-2>/              <slug-3>/      ...
                 (each = 1 EC2 c8g.2xlarge ARM Graviton4)
                       |
       +-----------------------------------------------------+
       |  IAM role: AmazonSSMManagedInstanceCore             |
       |             + scoped GetSecretValue on               |
       |               devbench-remote/<email>/{token,key}   |
       |  EBS gp3 256 GiB, encrypted, public IP for egress   |
       |  cloud-init: create user, install ansible/git/      |
       |    python3-pip + AWS CLI v2; mark "ready"           |
       |  ec2-bootstrap (operator side): scp playbook over   |
       |    SSH-over-SSM, run ansible-playbook on the box    |
       |  9 roles: base, languages, infra-tools, docker,     |
       |    git-auth, claude-cli, kanon-cli, workspace,      |
       |    devbench-sessions                                |
       +-----------------------------------------------------+
```

## Tooling decisions

- **Terraform + Terragrunt + Ansible.** No Packer (cloud-init + SSM-pushed ansible boots and configures in ~3 min and gives faster iteration than baking AMIs).
- **Cloud-init** does the bare minimum: create the linux user, drop authorized keys, install `git ansible python3-pip` plus AWS CLI v2 (needed for the in-playbook Secrets Manager fetch), then write `/var/log/devbench-cloudinit-ready.done` to signal "ready for SSM push." It does **not** run `ansible-pull` — that path requires the devbench repo to be public (or a deploy token in cloud-init), neither of which we want.
- **`make ec2-bootstrap`** (operator-side, after `ec2-apply`) waits for the cloud-init ready marker, scp's the ansible roles + tools through an SSH-over-SSM tunnel, then runs `ansible-playbook` locally on the box. Idempotent; reused by `make ec2-refresh` for re-runs and secret rotation.
- **Ansible** does the rest: every tool listed in the operator runbook, plus rootless Docker, `/workspace`, and the `devbench-session` Python helper. The `git-auth` role pulls the operator's PAT and SSH private key from AWS Secrets Manager using the EC2's instance role — neither secret transits the operator's machine on every refresh.
- **State:** S3 bucket per account (`devbench-remote-state-<account-id>`), per-owner key prefix (`devbench-remote/<email>/<leaf>/`), S3-native locking (Terraform 1.10+), no DynamoDB lock table.

## Networking

- One VPC per account; the `network/` Terragrunt config is a singleton applied once before any instance leaf.
- One public subnet (`map_public_ip_on_launch = true`), one Internet Gateway with a default route.
- Instances launch with `associate_public_ip_address = true`. The public IP is **only used for outbound** (apt mirrors, AWS endpoints, GitHub for clones). Inbound is locked down by the SG.
- Security Group: zero ingress rules (implicit deny), all egress (`0.0.0.0/0`). SSM tunnels through outbound HTTPS, so no port 22 exposure is needed and the public IP cannot be used for ingress.

## Access pattern

```
laptop -> aws sso login --profile <p>
       -> ssh i-<instance-id>
              (ProxyCommand = aws ssm start-session --document-name AWS-StartSSHSession ...)
       -> sshd on EC2 sees an authenticated SSM session
       -> drops the operator into <linux_user>'s shell (zsh + oh-my-zsh)
```

The operator's AWS console-managed key pair (named after their email) is used by the SSH layer for authentication; AWS SSO + IAM controls who can `start-session` against which instance.

## Multi-session devbench

Inside the EC2, the `devbench-session` Python helper manages N concurrent screen sessions. Each session has its own `~/workspace/devbench-session-N/devbench/` clone, so the operator can edit one tree (e.g. swap branches) while another runs against `main`.

```
~/workspace/
  devbench-session-1/devbench/   <-- main, running orchestrate
  devbench-session-2/devbench/   <-- feat/foo, interactive shell
  devbench-session-3/devbench/   <-- main, running another orchestrate
```

## Per-user uniqueness

Every Terraform resource carries `Owner = <operator-email>` tag. Resource names are prefixed with the slugified email (e.g. `firstname-lastname-domain-com-`). State paths embed the owner email under `s3://<state-bucket>/devbench-remote/<owner-email>/<leaf>/terraform.tfstate`. Two operators applying simultaneously cannot collide.

## Secrets handling

- Two secrets per operator in AWS Secrets Manager:
  - `devbench-remote/<email>/github-token` — GitHub PAT (sourced from `shell.env` `GH_TOKEN`).
  - `devbench-remote/<email>/github-ssh-key` — private SSH key matching the operator's GitHub-registered public key.
- Uploaded by `make ec2-secrets-sync`, which is CLI-only (`aws secretsmanager create-secret` / `put-secret-value`); the operator's secrets are **never** in Terraform state and never on the operator's filesystem after `ec2-secrets-sync` exits.
- The EC2's IAM instance profile carries an inline policy granting `secretsmanager:GetSecretValue` on those two ARNs only — no broader access.
- Rotation: edit `shell.env` (or rotate the SSH key file), `make ec2-secrets-sync && make ec2-refresh`. The new value is fetched on the next playbook run and written into `~/.git-credentials`, `~/.{bashrc,zshrc}`, and `~/.ssh/github_ed25519`.

## Git auth chain (priority order)

The `git-auth` ansible role wires three tiers; first-helper-that-responds-wins.

1. **OAuth via Git Credential Manager (primary, interactive HTTPS).** GCM is at the head of `credential.https://github.com.helper`. First interactive `git push` opens the system browser for OAuth; result is cached so subsequent pushes are silent.
2. **`gh auth git-credential` (fallback 1).** `GH_TOKEN` is exported in `~/.{bashrc,zshrc}` from the secret-stored PAT, so `gh` knows about it without `gh auth login` ever running.
3. **Stored token + SSH key (fallback 2).** `~/.git-credentials` holds `https://x-access-token:<PAT>@github.com` for non-interactive HTTPS callers; `~/.ssh/github_ed25519` is the operator's GitHub-registered SSH private key for `git@github.com:...` URLs.

## CLAUDE.md compliance

- **Declarative state:** Terraform/Ansible only. No imperative bash beyond cloud-init's minimal first-boot script.
- **Environment-agnostic artifacts:** every value is a Terragrunt input or env var. The repo contains no account-specific data hard-coded.
- **Immutable deployments:** new instance == new Terragrunt leaf folder. To change a running box, edit and re-apply, never SSH-and-modify.
- **No secrets in code:** PAT and SSH key live only in AWS Secrets Manager. The operator's `shell.env` and laptop SSH key are read once by `ec2-secrets-sync` and never echoed, logged, or committed.
- **Fail-fast:** every Make target validates required env vars at the top; `ec2-secrets-sync` aborts if `GH_TOKEN` or `DEVBENCH_GITHUB_SSH_KEY` are unset.
- **Documentation in sync:** this doc + `operator-runbook.md` + `infra/README.md` + `docs/infrastructure.md` all ship together with the Terraform/Ansible code.
