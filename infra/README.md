# devbench/infra

Per-developer remote-EC2 provisioning for the devbench toolchain.

- **Architecture:** [`docs/architecture.md`](docs/architecture.md)
- **Operator runbook:** [`docs/operator-runbook.md`](docs/operator-runbook.md)
- **Make targets:** see `devbench/Makefile` (`ec2-*`)

## Subtree layout

```
infra/
├── terraform/modules/         # network, ec2-dev-instance, state-bucket
├── terragrunt/                # root + _env + network singleton + instances/<owner>
├── ansible/                   # bootstrap.yml + 9 roles (base, languages, infra-tools,
│                              # docker, git-auth, claude-cli, kanon-cli, workspace,
│                              # devbench-sessions)
├── ssh/config.template        # operator's local ~/.ssh/config snippet for SSM tunnel
└── docs/                      # architecture + runbook
```

## Quickstart (existing operator)

```bash
export DEVBENCH_OWNER_EMAIL=<your-email>
export DEVBENCH_LINUX_USER=<your-linux-username>
export DEVBENCH_KEY_NAME=<your-aws-keypair-name>
export DEVBENCH_SSH_KEY=$HOME/.ssh/<your-aws-keypair-private-key>
export DEVBENCH_GITHUB_SSH_KEY=$HOME/.ssh/<your-github-private-key>
source shell.env                  # exports GH_TOKEN
aws sso login --profile devbench-remote

make ec2-secrets-sync             # one-time: upload GH_TOKEN + SSH key to AWS Secrets Manager
make ec2-init
make ec2-apply                    # provisions VM; cloud-init only does minimal user setup
make ec2-bootstrap                # SSH-over-SSM: scp playbook + run ansible (~3 min)
make ec2-ssh                      # connect
```

The split between `ec2-apply` (terraform) and `ec2-bootstrap` (ansible push)
keeps cloud-init minimal so the playbook can stay in a private repo and pull
operator-specific secrets via the EC2's IAM instance role.
