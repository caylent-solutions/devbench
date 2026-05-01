# Remote EC2 setup guide

This guide walks an operator from `aws sso login` to a running orchestrator on a remote EC2 dev box. It sequences the existing material under `infra/`, `infra/docs/`, and `tools/devbench_session.py` into a linear reader flow; each step links to the authoritative source rather than duplicating it.

## Table of contents

- [When to use a remote EC2 dev box](#when-to-use-a-remote-ec2-dev-box)
- [Prerequisites](#prerequisites)
- [1. Provision shared infrastructure](#1-provision-shared-infrastructure)
- [2. Stamp out a per-user instance](#2-stamp-out-a-per-user-instance)
- [3. Bootstrap the host with Ansible](#3-bootstrap-the-host-with-ansible)
- [4. Open a devbench session](#4-open-a-devbench-session)
- [5. Configure the session environment](#5-configure-the-session-environment)
- [6. Drive the orchestrator](#6-drive-the-orchestrator)
- [7. Refresh / update / tear down](#7-refresh--update--tear-down)
- [Troubleshooting](#troubleshooting)

## When to use a remote EC2 dev box

Local devcontainers are fine for editing and short-lived runs. Move to EC2 when any of the following are true:

- The orchestrator needs to run unattended for hours and you want it isolated from your laptop's sleep / network state.
- Multiple operators want to share a `JUDGE_WORKSPACE_ROOT` without juggling local filesystem mounts.
- You need to run multiple parallel orchestrate sessions per operator (the `devbench-session` launcher under `tools/devbench_session.py` is designed for this).
- Compliance requires the long-running build artefacts to live on company infra rather than personal hardware.

## Prerequisites

1. AWS SSO access to the devbench account. Confirm your profile name (`aws configure list-profiles | grep devbench`).
2. An AWS keypair (private key on your laptop, public key registered in EC2). Reference: [`infra/README.md`](../infra/README.md) `Quickstart` block.
3. A GitHub Personal Access Token (PAT) with `repo` + `read:org` scopes, exported as `GH_TOKEN` (the `make ec2-secrets-sync` target uploads it to AWS Secrets Manager).
4. An SSH key registered with GitHub for the EC2 host to clone private repos.
5. Locally installed: `terraform`, `terragrunt`, `ansible`, `gh`, `aws`. The infra `Makefile` validates each on first run and prints an actionable error if any are missing.

Set the per-operator env vars once (typically in `~/.bashrc`):

```bash
export DEVBENCH_OWNER_EMAIL=<you@caylent.com>
export DEVBENCH_LINUX_USER=<your-linux-username>
export DEVBENCH_KEY_NAME=<your-aws-keypair-name>
export DEVBENCH_SSH_KEY=$HOME/.ssh/<your-aws-keypair-private-key>
export DEVBENCH_GITHUB_SSH_KEY=$HOME/.ssh/<your-github-private-key>
```

## 1. Provision shared infrastructure

The state bucket and VPC are stamped out once per AWS account, by an operator with admin scope. Subsequent operators inherit them.

```bash
aws sso login --profile devbench-remote
cd /workspaces/rpm-migration/devbench
make ec2-init                  # terragrunt init for state-bucket + network
make ec2-apply MODULE=network  # one-time per account
```

References:

- Terragrunt root + env: [`infra/terragrunt/_env/`](../infra/terragrunt/_env/), [`infra/terragrunt/root.hcl`](../infra/terragrunt/root.hcl).
- State bucket + network modules: [`infra/terraform/modules/state-bucket/`](../infra/terraform/modules/state-bucket/), [`infra/terraform/modules/network/`](../infra/terraform/modules/network/).

## 2. Stamp out a per-user instance

Each operator gets a directory under `infra/terragrunt/instances/<owner>/`. Use the template:

```bash
cp -r infra/terragrunt/instances/_template infra/terragrunt/instances/$DEVBENCH_LINUX_USER
# Edit the copied terragrunt.hcl to set the owner-specific values.
make ec2-secrets-sync
make ec2-apply
```

References:

- Per-instance template: [`infra/terragrunt/instances/_template/`](../infra/terragrunt/instances/_template/).
- EC2 module: [`infra/terraform/modules/ec2-dev-instance/`](../infra/terraform/modules/ec2-dev-instance/).
- `user_data.sh.tpl` cloud-init bootstrap: [`infra/terraform/modules/ec2-dev-instance/user_data.sh.tpl`](../infra/terraform/modules/ec2-dev-instance/user_data.sh.tpl).

## 3. Bootstrap the host with Ansible

`make ec2-apply` only does minimal cloud-init -- the heavy lifting (Docker, languages, Claude CLI, kanon CLI, devbench-session launcher) lands via Ansible.

```bash
make ec2-bootstrap     # ssh-over-ssm + ansible push (~3 min on first run)
```

Roles applied (one per concern, in [`infra/ansible/roles/`](../infra/ansible/roles/)):

- `base` -- system packages, swap, time sync.
- `docker` -- Docker engine + non-root user permissions.
- `languages` -- Python (asdf), Node (asdf), Go.
- `infra-tools` -- terraform, terragrunt, ansible, awscli, gh.
- `git-auth` -- pulls the GitHub SSH key from Secrets Manager into `~/.ssh/`.
- `claude-cli` -- installs Claude Code with the devbench plugin.
- `kanon-cli` -- installs the kanon CLI for marketplace work.
- `workspace` -- creates `~/workspace/` with the right permissions.
- `devbench-sessions` -- installs the per-user multi-session launcher at `~/.local/bin/devbench-session`.

Playbooks:

- [`infra/ansible/playbooks/bootstrap.yml`](../infra/ansible/playbooks/bootstrap.yml) -- one-time application of every role.
- [`infra/ansible/playbooks/refresh.yml`](../infra/ansible/playbooks/refresh.yml) -- idempotent re-run when bumping tool versions.

Operator runbook for the SSH-over-SSM connection: [`infra/docs/operator-runbook.md`](../infra/docs/operator-runbook.md).

## 4. Open a devbench session

```bash
make ec2-ssh                                         # SSH-over-SSM
# on the host:
devbench-session new <session-name>                  # creates ~/workspace/devbench-session-N
devbench-session list                                # shows every active session
devbench-session attach <session-name>               # reconnect to an existing session
```

Each session is its own git clone of devbench under `~/workspace/devbench-session-<N>` so multiple long-running orchestrate loops can share the host without stepping on each other. Implementation: [`tools/devbench_session.py`](../tools/devbench_session.py).

## 5. Configure the session environment

Inside the session, set the same per-backlog env vars the laptop launch commands use:

```bash
export JUDGE_WORKSPACE_ROOT=/workspaces/<your-spec-repo>
export JUDGE_CLAUDE_MODEL=claude-opus-4-7
export JUDGE_ORCHESTRATOR_SESSION_ID=<unique-session-id>     # E230 hook-tail filter
```

The orchestrator's log file is resolved by precedence: `JUDGE_LOG_FILE` env var > `log_file:` in `backlog/config/devbench.yaml` > `<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log`. Setting `log_file:` in the YAML keeps reader and writer in sync; see [`docs/cli-reference.md` `report` section](cli-reference.md#report) and [`docs/architecture.md` Reporting & observability](architecture.md#reporting--observability).

## 6. Drive the orchestrator

Three panes (tmux is the usual harness):

1. **Orchestrator pane** -- the interactive Claude session.
   ```bash
   PATH=... JUDGE_CLAUDE_MODEL=claude-opus-4-7 JUDGE_WORKSPACE_ROOT=$JUDGE_WORKSPACE_ROOT \
     JUDGE_ORCHESTRATOR_SESSION_ID=$JUDGE_ORCHESTRATOR_SESSION_ID \
     claude --plugin-dir /path/to/devbench/plugin/devbench --dangerously-skip-permissions
   ```
   Inside Claude, run `/devbench:orchestrate` (or the equivalent skill).
2. **Live report pane** -- `uv run --project /path/to/devbench devbench report --watch 120`.
3. **Hook-tail pane** -- `uv run --project /path/to/devbench devbench hook-tail --orchestrator-only`. The `--orchestrator-only` flag (E230) filters to events stamped with `JUDGE_ORCHESTRATOR_SESSION_ID` so side-pane Claude sessions in the same workspace do not pollute the audit stream.

The `caylent-telemetry-spec/devbench-launch-commands.txt` file in this repo's sister workspace ships the exact incantation for each backlog; copy from there rather than typing the env-var pile by hand.

## 7. Refresh / update / tear down

| Operation | Command |
|---|---|
| Bump tool versions on the host | `make ec2-refresh` (runs [`refresh.yml`](../infra/ansible/playbooks/refresh.yml)) |
| Update devbench inside an active session | `cd ~/workspace/devbench-session-<N> && git pull && uv sync` |
| Cycle the EC2 instance (immutable redeploy) | `make ec2-destroy && make ec2-apply` (state survives in S3, sessions don't -- export anything you need first) |
| Tear down the per-user instance | `make ec2-destroy` |

## Troubleshooting

- `make ec2-apply` fails with credentials error: re-run `aws sso login --profile devbench-remote` and check `aws sts get-caller-identity` shows the expected account.
- `make ec2-bootstrap` hangs: confirm the instance is in `running` state via `aws ec2 describe-instances`, and that SSM agent is up via `aws ssm describe-instance-information`.
- Hook-tail is silent despite the orchestrator running: confirm `JUDGE_ORCHESTRATOR_SESSION_ID` matches between the orchestrator and hook-tail panes. With `--orchestrator-only`, mismatched session IDs silently suppress events. See [E230 in `docs/cli-reference.md`](cli-reference.md#hook-tail).
- Multiple orchestrators write to the same `hook-logs.jsonl`: this is by design when they share the workspace. Use `JUDGE_ORCHESTRATOR_SESSION_ID` + `--orchestrator-only` to scope the per-pane view.

For deeper architecture context (network topology, IAM roles, cloud-init responsibilities), read [`infra/docs/architecture.md`](../infra/docs/architecture.md). For day-2 operator commands beyond this guide, read [`infra/docs/operator-runbook.md`](../infra/docs/operator-runbook.md).
