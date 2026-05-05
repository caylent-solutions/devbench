# Devbench-Remote Operator Runbook

## Prerequisites (one-time, on your laptop)

The Make targets shell out to `terraform`, `terragrunt`, the AWS CLI, the GitHub CLI, `ansible`, `uv`, `jq`, plus the usual `make` / `ssh` / `bash`. If you run devbench inside the project devcontainer, all of these are auto-installed by `.devcontainer/project-setup.sh`. Otherwise install them manually:

| Tool | Minimum version | Used for |
|---|---|---|
| `terraform` | >= 1.10 | Plan / apply infrastructure |
| `terragrunt` | >= 0.50 | Per-leaf wrapper that injects shared root config |
| `aws-cli` v2 | >= 2.13 | SSO login, `ec2 stop-instances`, SSM session |
| `gh` | >= 2.0 | GitHub auth flow used when seeding the GH PAT secret |
| `jq` | any recent | Parses release metadata in helper scripts |
| `ansible` (`ansible-playbook`) | >= 2.16 | Runs the bootstrap roles on the EC2 |
| `uv` | latest | Drives `uv run devbench …` and `uv sync --all-extras` |
| `make`, `ssh`, `bash` | system | Make targets and SSM tunneling |

1. Configure your AWS SSO profile:
   ```bash
   aws configure sso --profile devbench-remote
   ```
2. Create your AWS console key pair (EC2 -> Key pairs -> Create). Name it your email (e.g. `bob.smith@example.com`). Type: ed25519. Save the private key locally.

## First-time onboarding

**Critical:** the devbench repo never holds operator-specific values. Pick one of two install locations for your Terragrunt leaf:

### Recommended: out-of-repo personal config

Your leaf lives in `~/.devbench/instances/<slug>/` and survives `git clean`, branch switches, and re-clones of devbench. A single env-var pair tells `make ec2-*` where to find it.

```bash
mkdir -p ~/.devbench/instances/bob-smith
cp <devbench-checkout>/infra/terragrunt/instances/_template/terragrunt.hcl \
   ~/.devbench/instances/bob-smith/

# Add to your ~/.zshrc or ~/.bashrc so every shell sees them.
export DEVBENCH_INSTANCES_DIR="$HOME/.devbench/instances"
export DEVBENCH_DEVBENCH_REPO="<absolute path to your devbench checkout>"
export DEVBENCH_OWNER_EMAIL="bob.smith@example.com"
export DEVBENCH_LINUX_USER="bob"
export DEVBENCH_KEY_NAME="bob.smith@example.com"

# Required by the shared Terragrunt root (infra/terragrunt/_env/account.hcl).
export AWS_ACCOUNT_ID="<your aws account id>"
export AWS_REGION="<your aws region, e.g. us-east-1>"
export AWS_PROFILE="<your aws sso profile name>"
```

Then edit `~/.devbench/instances/bob-smith/terragrunt.hcl`. Re-clone devbench whenever you like — your leaf is unaffected.

### Alternative: in-repo (still gitignored)

```bash
cd <devbench-checkout>/infra/terragrunt/instances
cp -r _template bob-smith
```

`infra/terragrunt/instances/*` is in `.gitignore` (only `_template/` and `.gitkeep` are tracked), so your edits never get staged. Re-cloning the repo, however, deletes the leaf — option A is preferred.

In either case, edit `bob-smith/terragrunt.hcl` -- set `owner_email`, `linux_user`, `key_name`. Optionally override `instance_type` or `ebs_size_gb`.
3. Bootstrap the state bucket (one-time per account; skip if it already exists):
   ```bash
   # Run only if no existing state bucket: see infra/terraform/modules/state-bucket
   ```
4. Apply the singleton VPC (one-time per account; skip if `network/` already applied):
   ```bash
   cd <devbench-checkout>
   make ec2-network-apply
   ```
5. Upload your GitHub PAT and SSH private key into AWS Secrets Manager (one-time, rotatable later):
   ```bash
   export GH_TOKEN=ghp_...                               # or `source shell.env`
   export DEVBENCH_GITHUB_SSH_KEY=$HOME/.ssh/id_ed25519  # private key matching your registered GitHub key
   make ec2-secrets-sync
   ```
   Two secrets are created (or updated) under `devbench-remote/<your-email>/`:
   `github-token` and `github-ssh-key`. The EC2's IAM role grants
   `secretsmanager:GetSecretValue` on those two ARNs only.
6. Provision your instance (all variables from the env block above must be exported in your shell):
   ```bash
   export DEVBENCH_SSH_KEY=$HOME/.ssh/<your-aws-keypair-private-key>
   aws sso login --profile "$AWS_PROFILE"
   make ec2-init
   make ec2-apply
   ```
7. Wait ~1 min for cloud-init to mark the box ready, then push the playbook + run it:
   ```bash
   make ec2-bootstrap
   ```
   This scp's the ansible roles over an SSH-over-SSM tunnel and runs them locally
   on the box. The `git-auth` role fetches your token + SSH key from Secrets Manager
   using the instance IAM role (no operator credentials transit).
8. SSH in:
   ```bash
   make ec2-ssh
   ```

## Daily lifecycle

| Task | Command |
|---|---|
| List your devbench instances | `make ec2-list` |
| SSH into your instance | `make ec2-ssh` |
| Stop the instance (save money) | `make ec2-stop` |
| Start a stopped instance | `make ec2-start` |
| Check instance status | `make ec2-status` |
| Re-run Ansible bootstrap (after roles update or secret rotation) | `make ec2-refresh` |
| Rotate your GitHub PAT or SSH key | `make ec2-secrets-sync && make ec2-refresh` |
| Tear down your instance | `make ec2-destroy` |

All Make targets read `DEVBENCH_OWNER_EMAIL` to identify your leaf and `AWS_ACCOUNT_ID` / `AWS_REGION` / `AWS_PROFILE` to talk to AWS. Set them once in your shell profile.

After a devbench reclone or devcontainer rebuild, run `make ec2-init` once before the other targets — it clears the leaf's stale `.terragrunt-cache` and rehydrates the backend pointer from S3. Apart from that, the lifecycle targets above (`ec2-stop`, `ec2-start`, etc.) work without further setup.

## Inside the instance

Every operator gets:

- `python3` (24.04 LTS), `node` (LTS), `go`, `uv`, `pipx`, `pip`
- `terraform`, `terragrunt`, `aws` CLI v2, `gh` (GitHub CLI)
- Rootless Docker engine (no sudo to use)
- `screen`, `vim`, `nmap`, `git`, `oh-my-zsh` (zsh as default shell)
- `kanon` (via pipx)
- `claude` (Claude Code CLI)
- `devbench-session` helper at `~/.local/bin/devbench-session`
- Empty `~/workspace` directory for your work

### devbench-session — multi-session launcher

```bash
devbench-session start 1                  # clone main into ~/workspace/devbench-session-1, screen-detached interactive shell
devbench-session start 2 --mode orchestrate   # second session, `uv run devbench start` orchestrate loop
devbench-session attach 1                 # re-attach to session 1
devbench-session list                     # show all active sessions
devbench-session stop 1                   # stop and clean up session 1
```

Each session has its own clone, so editing one branch in session-1 does not affect session-2's `main` checkout.

### Git auth

Three tiers wired automatically by the `git-auth` ansible role; everything below the OAuth tier is pre-loaded from AWS Secrets Manager on first bootstrap.

1. **GCM (primary):** the credential helper chain has `manager` first; first HTTPS push opens an OAuth flow in the system browser.
2. **`gh` token (fallback 1):** the box exports `GH_TOKEN` from `~/.{bashrc,zshrc}` (loaded from `devbench-remote/<email>/github-token`). The credential helper chain has `!gh auth git-credential` in second position, which uses the env var.
3. **SSH key + stored token (fallback 2):** the private key from `devbench-remote/<email>/github-ssh-key` is written to `~/.ssh/github_ed25519` (mode 0600); the matching public key already lives on GitHub. `~/.git-credentials` also holds `https://x-access-token:<PAT>@github.com` so non-interactive HTTPS callers (CI scripts, `git ls-remote` without a TTY) succeed without invoking GCM.

Rotate either secret with `make ec2-secrets-sync` (CLI-only, idempotent), then `make ec2-refresh` to pull the new value onto the running instance.

## Troubleshooting

### Cloud-init or ansible bootstrap failed

```bash
make ec2-ssh
sudo cat /var/log/cloud-init-output.log | tail -200
sudo cat /var/log/devbench-cloudinit-ready.done   # exists when cloud-init finished
sudo cat /var/log/devbench-ansible.done           # exists when ansible finished
```

To re-run Ansible:
```bash
make ec2-refresh
```

### `aws ssm start-session` fails

- Confirm SSO is logged in: `aws sts get-caller-identity --profile <profile>`
- Confirm SSM agent is online: instance shows `Online` in EC2 console -> Systems Manager -> Fleet Manager.
- Confirm IAM role attached: `aws ec2 describe-instances --instance-ids i-xxx --query 'Reservations[].Instances[].IamInstanceProfile.Arn'`.

### `make ec2-ssh` says "session already in use"

```bash
aws ssm describe-sessions --state Active --filters key=Target,value=i-xxx
aws ssm terminate-session --session-id <sid>
```

### Adding a second instance for yourself

You can run multiple instances. Copy `instances/<your-slug>/` to `instances/<your-slug>-2/`, edit if needed, and `DEVBENCH_OWNER_EMAIL=<your-email-2>` (or set up a separate state-key prefix).

### Removing a teammate's stale instance

You shouldn't need to. Each operator's state lives at `s3://<state>/devbench-remote/<their-email>/`, and they own teardown. If they leave the company, an admin can `terragrunt destroy` against their leaf manually.

---

## Personal config strategy (deep dive)

**Rule:** The devbench repo never holds operator-specific values, AWS state, or secrets. Two locations are supported for your Terragrunt leaf:

| Location | Persistence across re-clones | Survives `git clean` | Setup |
|---|---|---|---|
| `~/.devbench/instances/<slug>/` (env vars `DEVBENCH_INSTANCES_DIR` + `DEVBENCH_DEVBENCH_REPO`) | yes | yes | recommended |
| `<repo>/infra/terragrunt/instances/<slug>/` (gitignored) | no | yes (gitignored) | alternative |

### Validate your config

`make ec2-doctor` prints the resolved leaf path and verifies your env vars without touching AWS. Run it after editing `~/.zshrc` to confirm everything is wired correctly:

```
$ make ec2-doctor
Owner email   : you@example.com
Linux user    : you
Key name      : you@example.com
Instances dir : /home/you/.devbench/instances
Resolved leaf : /home/you/.devbench/instances/you
Repo path     : /home/you/code/devbench
Config is healthy.
```

### Reconnecting after a fresh devbench clone

```bash
cd /tmp/fresh-devbench
git clone https://github.com/caylent-solutions/devbench.git
cd devbench

# Your existing personal config is reused as-is:
export DEVBENCH_INSTANCES_DIR="$HOME/.devbench/instances"
export DEVBENCH_DEVBENCH_REPO="$(pwd)"
export DEVBENCH_OWNER_EMAIL="you@example.com"
export DEVBENCH_LINUX_USER="you"
export DEVBENCH_KEY_NAME="you@example.com"
export AWS_ACCOUNT_ID="<your aws account id>"
export AWS_REGION="<your aws region>"
export AWS_PROFILE="<your aws sso profile name>"

aws sso login --profile "$AWS_PROFILE"
make ec2-init            # rehydrates the local terragrunt cache against S3 remote state
make ec2-doctor          # confirms config healthy
make ec2-list            # shows your existing instance
make ec2-start           # if it was stopped
make ec2-ssh             # connect via SSM
```

No re-apply, no state migration. `make ec2-init` always wipes the leaf's local `.terragrunt-cache` first and re-discovers the S3 remote state keyed off `devbench-remote/<your-email>/...`. After init, all other `ec2-*` targets (including `ec2-stop`) work without further setup.

### Rotating to a new region or instance type

Edit your personal `~/.devbench/instances/<slug>/terragrunt.hcl`, set `instance_type = "..."` or `region = "..."` in the inputs block, then `make ec2-apply`. Terraform replaces only what changed.

### Sharing config across machines

Symlink or sync `~/.devbench/instances/` via your dotfiles manager (chezmoi, yadm, plain git) — but **never** sync into the devbench repo itself.
