SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
unexport VIRTUAL_ENV

.PHONY: help install install-hooks plugin-install plugin-uninstall lint lint-ruff lint-bandit format format-check typecheck test test-unit test-coverage validate clean start start-interactive report report-session pre-commit-check pre-push-check watch watch-live

## help: Show available targets
help:
	@echo "DevBench -- Make Targets"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed \
	  -e 's/^## /  /' \
	  -e 's/\(start-interactive:.*\)/\1 [JUDGE_WORKSPACE_ROOT, JUDGE_CLAUDE_MODEL, JUDGE_SAFE_PERMISSIONS]/' \
	  -e 's/\(report-session:.*\)/\1 [SINCE]/' \
	  -e 's/\(watch-live:.*\)/\1 [INTERVAL]/'
	@echo ""

## install: Install runtime and dev dependencies
install:
	uv sync --all-extras

## install-hooks: Install pre-commit and pre-push git hooks
install-hooks:
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push

## plugin-install: Register devbench marketplace and install plugin (user scope)
plugin-install:
	claude plugin marketplace add ./plugin --scope user
	claude plugin install devbench --scope user

## plugin-uninstall: Uninstall devbench plugin and remove marketplace
plugin-uninstall:
	claude plugin uninstall devbench
	claude plugin marketplace remove devbench

## lint-ruff: Run ruff linter
lint-ruff:
	uv run ruff check .

## lint-bandit: Run bandit security scan
lint-bandit:
	uv run bandit -r . -ll --exclude ./tests,./.venv

## lint-no-duplicates: Fail if any "* (1).*" leftover-download files exist in the working tree.
## Uses find (not git ls-files) so the guard catches the duplicates even though they are
## .gitignore'd -- the goal is to flag the artifacts in the operator's checkout, not the index.
lint-no-duplicates:
	@dupes=$$(find . \
	  -path './.git' -prune -o \
	  -path './.venv' -prune -o \
	  -path '*/node_modules' -prune -o \
	  -path '*/.terragrunt-cache' -prune -o \
	  -path '*/.terraform' -prune -o \
	  -path '*/__pycache__' -prune -o \
	  -path '*/.pytest_cache' -prune -o \
	  -path '*/.ruff_cache' -prune -o \
	  -path '*/.mypy_cache' -prune -o \
	  -name '* (1)*' -print 2>/dev/null); \
	if [ -n "$$dupes" ]; then \
	  echo "ERROR: '(1)'-suffixed duplicate files found (browser/OS download artifacts):" >&2; \
	  echo "$$dupes" | sed 's/^/  /' >&2; \
	  echo "Delete them and re-run. The canonical, tracked file is the same path without the ' (1)' suffix." >&2; \
	  exit 1; \
	fi

## lint: Run all linters (ruff + bandit + no-duplicates guard)
lint: lint-ruff lint-bandit lint-no-duplicates

## format: Auto-format code with ruff
format:
	uv run ruff format .
	uv run ruff check . --fix

## format-check: Check formatting without modifying files
format-check:
	uv run ruff format --check .

## typecheck: Run mypy type checking
typecheck:
	uv run mypy .

## test-unit: Run unit tests
test-unit:
	uv run pytest tests/ -v --tb=short -q

## test-coverage: Run tests with coverage report (fails below 90%)
test-coverage:
	uv run pytest tests/ --cov=devbench --cov-report=term-missing --cov-fail-under=90

## test-coverage-new: Enforce 100% line coverage on modules introduced or hardened by the manifest-amendment, task-factory, watch, git-ops-assertion, hook-tail, auto-requeue, and report-perf-roadmap (issue #162) features
test-coverage-new:
	uv run pytest tests/ --cov=devbench.backlog.manifest --cov=devbench.backlog.amendment --cov=devbench.backlog.proposal --cov=devbench.backlog.manager --cov=devbench.activity --cov=devbench.github.git_ops --cov=devbench.hook_tail --cov=devbench.reporting.snapshot --cov=devbench.reporting.window_stats --cov=devbench.reporting.archive --cov=devbench.reporting.sharded_log --cov-report=term-missing --cov-fail-under=100

## test: Run all tests
test: test-unit

## validate: Full validation (all checks -- identical to CI and pre-push)
validate: lint-ruff lint-bandit lint-no-duplicates format-check typecheck test-coverage test-coverage-new
	@echo "All validations passed"

## pre-commit-check: Checks that run on every commit (fast)
pre-commit-check: lint-ruff format-check
	@echo "Pre-commit checks passed"

## pre-push-check: Checks that run before push (full -- identical to CI)
pre-push-check: validate
	@echo "Pre-push checks passed"

## clean: Remove build artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

## start: Run orchestrate skill non-interactively via Claude Agent SDK
start:
	uv run python -m devbench.cli start

## start-interactive: Launch interactive Claude session with devbench plugin loaded
ifeq ($(JUDGE_SAFE_PERMISSIONS),1)
start-interactive:
	claude --plugin-dir plugin/devbench
else
start-interactive:
	claude --dangerously-skip-permissions --plugin-dir plugin/devbench
endif

## report: Show backlog progress report (full session)
report:
	uv run python -m devbench.cli report

## report-session: Show progress since a timestamp (e.g. make report-session SINCE=2026-03-05T16:13:00Z)
report-session:
	uv run python -m devbench.cli report "$(SINCE)"

## watch: Show live dashboard of the currently-active orchestration (one-shot)
watch:
	uv run python -m devbench.cli watch

## watch-live: Refresh the activity dashboard every N seconds (default 5; override with INTERVAL=2)
watch-live:
	uv run python -m devbench.cli watch --watch $${INTERVAL:-5}

# -----------------------------------------------------------------------------
# Remote-EC2 provisioning (Terraform + Terragrunt + Ansible).
# See infra/docs/operator-runbook.md for details.
#
# Required env vars (validated at the top of each target):
#   DEVBENCH_OWNER_EMAIL    -- operator email; selects the per-user Terragrunt leaf
#   DEVBENCH_LINUX_USER     -- linux account on the box (SSH-over-SSM uses this)
#   DEVBENCH_KEY_NAME       -- name of the AWS console-managed key pair
# Required:
#   AWS_REGION              -- AWS region (e.g. us-east-2)
#   AWS_ACCOUNT_ID          -- AWS account id
#   DEVBENCH_STATE_BUCKET   -- e.g. devbench-remote-state-$AWS_ACCOUNT_ID
#   DEVBENCH_SSH_KEY        -- path to your AWS-keypair private key (used for SSH-over-SSM)
# Required for ec2-secrets-sync only:
#   GH_TOKEN                -- GitHub PAT (typically sourced from shell.env)
#   DEVBENCH_GITHUB_SSH_KEY -- path to the private SSH key paired with your GitHub-registered pub key
# Optional:
#   DEVBENCH_INSTANCE_TYPE  -- defaults to c8g.2xlarge (set in _env/common.hcl)
#   DEVBENCH_EBS_SIZE_GB    -- defaults to 256
#   DEVBENCH_INSTANCES_DIR  -- where the per-operator Terragrunt leaf lives (default: in-repo, gitignored)
#   DEVBENCH_DEVBENCH_REPO  -- absolute path to this checkout (required when DEVBENCH_INSTANCES_DIR is out-of-repo)
# -----------------------------------------------------------------------------

INFRA_DIR := infra/terragrunt
EC2_LEAF_SLUG := $(shell echo "$${DEVBENCH_OWNER_EMAIL:-}" | sed 's/@.*//; s/[^a-z0-9-]/-/g')
# DEVBENCH_INSTANCES_DIR overrides where Make targets look for the operator's
# Terragrunt leaf. Default keeps it in-repo (gitignored under
# infra/terragrunt/instances/<slug>/) so the repo stays clean. Recommended
# external location is ~/.devbench/instances so a fresh devbench checkout
# auto-rediscovers the leaf without re-cloning anything.
EC2_INSTANCES_DIR := $(if $(DEVBENCH_INSTANCES_DIR),$(DEVBENCH_INSTANCES_DIR),$(INFRA_DIR)/instances)
EC2_LEAF_DIR := $(EC2_INSTANCES_DIR)/$(EC2_LEAF_SLUG)
EC2_SSH_KEY_OPT := $(if $(DEVBENCH_SSH_KEY),-i "$(DEVBENCH_SSH_KEY)" -o IdentitiesOnly=yes,)

define _require_owner
	@if [ -z "$${DEVBENCH_OWNER_EMAIL:-}" ]; then \
	  echo "ERROR: DEVBENCH_OWNER_EMAIL is required (export your operator email)" >&2; \
	  exit 1; \
	fi
	@if [ ! -d "$(EC2_LEAF_DIR)" ]; then \
	  echo "ERROR: leaf $(EC2_LEAF_DIR) does not exist." >&2; \
	  echo "Set DEVBENCH_INSTANCES_DIR to your personal instances folder, e.g.:" >&2; \
	  echo "  export DEVBENCH_INSTANCES_DIR=\$$HOME/.devbench/instances" >&2; \
	  echo "Then copy the in-repo template once:" >&2; \
	  echo "  mkdir -p \"\$$DEVBENCH_INSTANCES_DIR/$(EC2_LEAF_SLUG)\"" >&2; \
	  echo "  cp infra/terragrunt/instances/_template/terragrunt.hcl \"\$$DEVBENCH_INSTANCES_DIR/$(EC2_LEAF_SLUG)/\"" >&2; \
	  echo "Edit the copy with your email/user/key, then re-run this target." >&2; \
	  exit 1; \
	fi
endef

## ec2-network-apply: One-time apply of the singleton VPC + SG (per AWS account)
ec2-network-apply:
	cd $(INFRA_DIR)/network && terragrunt apply $(TG_APPLY_ARGS)

## ec2-secrets-sync: upload operator's GH_TOKEN + SSH key to AWS Secrets Manager.
## Required env: GH_TOKEN (or sourced from shell.env) and DEVBENCH_GITHUB_SSH_KEY (path).
ec2-secrets-sync:
	$(call _require_owner)
	@if [ -z "$${GH_TOKEN:-}" ]; then echo "ERROR: GH_TOKEN is required (e.g. source shell.env)" >&2; exit 1; fi
	@if [ -z "$${DEVBENCH_GITHUB_SSH_KEY:-}" ]; then echo "ERROR: DEVBENCH_GITHUB_SSH_KEY=<path-to-private-key> is required" >&2; exit 1; fi
	@if [ ! -r "$$DEVBENCH_GITHUB_SSH_KEY" ]; then echo "ERROR: cannot read $$DEVBENCH_GITHUB_SSH_KEY" >&2; exit 1; fi
	@TOKEN_NAME="devbench-remote/$$DEVBENCH_OWNER_EMAIL/github-token"; \
	 KEY_NAME="devbench-remote/$$DEVBENCH_OWNER_EMAIL/github-ssh-key"; \
	 for entry in "$$TOKEN_NAME:GH_TOKEN" "$$KEY_NAME:DEVBENCH_GITHUB_SSH_KEY"; do \
	   name="$${entry%%:*}"; src="$${entry##*:}"; \
	   if [ "$$src" = "GH_TOKEN" ]; then payload="$$GH_TOKEN"; else payload="$$(cat "$$DEVBENCH_GITHUB_SSH_KEY")"; fi; \
	   if aws secretsmanager describe-secret --secret-id "$$name" >/dev/null 2>&1; then \
	     aws secretsmanager put-secret-value --secret-id "$$name" --secret-string "$$payload" >/dev/null; \
	     echo "updated: $$name"; \
	   else \
	     aws secretsmanager create-secret --name "$$name" --description "devbench-remote $$src for $$DEVBENCH_OWNER_EMAIL" --secret-string "$$payload" >/dev/null; \
	     echo "created: $$name"; \
	   fi; \
	 done

## ec2-bootstrap: push the ansible playbook + tools to the running EC2 via SSH-over-SSM,
## then run ansible-playbook locally on the box. Idempotent.
ec2-bootstrap:
	$(call _require_owner)
	@INSTANCE_ID=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw instance_id) && \
	 LINUX_USER=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw linux_user) && \
	 REGION="$${AWS_REGION:-us-east-1}" && \
	 TOKEN_NAME="devbench-remote/$$DEVBENCH_OWNER_EMAIL/github-token" && \
	 KEY_NAME="devbench-remote/$$DEVBENCH_OWNER_EMAIL/github-ssh-key" && \
	 echo ">> waiting for instance to be running + status checks ok..." && \
	 aws ec2 wait instance-running --instance-ids "$$INSTANCE_ID" && \
	 aws ec2 wait instance-status-ok --instance-ids "$$INSTANCE_ID" && \
	 echo ">> waiting for SSM agent (no-op SSM command)..." && \
	 SSM_CMD_ID=$$(aws ssm send-command --instance-ids "$$INSTANCE_ID" --document-name AWS-RunShellScript --parameters 'commands=["cloud-init status --wait || true; test -f /var/log/devbench-cloudinit-ready.done"]' --query 'Command.CommandId' --output text) && \
	 aws ssm wait command-executed --command-id "$$SSM_CMD_ID" --instance-id "$$INSTANCE_ID" && \
	 STATUS=$$(aws ssm get-command-invocation --command-id "$$SSM_CMD_ID" --instance-id "$$INSTANCE_ID" --query 'Status' --output text) && \
	 if [ "$$STATUS" != "Success" ]; then echo "ERROR: cloud-init readiness probe returned $$STATUS" >&2; exit 1; fi && \
	 echo ">> packing playbook + tools..." && \
	 TARFILE=$$(mktemp -t devbench-bootstrap.XXXX.tgz) && \
	 tar czf "$$TARFILE" -C $(CURDIR) infra/ansible tools/devbench_session.py && \
	 echo ">> uploading via scp..." && \
	 scp $(EC2_SSH_KEY_OPT) -F infra/ssh/config.template "$$TARFILE" "$${LINUX_USER}@$${INSTANCE_ID}:/tmp/devbench-bootstrap.tgz" && \
	 rm -f "$$TARFILE" && \
	 echo ">> running ansible-playbook on the box..." && \
	 ssh $(EC2_SSH_KEY_OPT) -F infra/ssh/config.template "$${LINUX_USER}@$${INSTANCE_ID}" \
	   "set -e; sudo install -d /opt/devbench-bootstrap && sudo tar xzf /tmp/devbench-bootstrap.tgz -C /opt/devbench-bootstrap && rm -f /tmp/devbench-bootstrap.tgz && cd /opt/devbench-bootstrap/infra/ansible && sudo ANSIBLE_FORCE_COLOR=0 ansible-playbook -i localhost, -c local playbooks/bootstrap.yml -e \"linux_user=$$LINUX_USER owner_email=$$DEVBENCH_OWNER_EMAIL aws_region=$$REGION github_token_secret_name=$$TOKEN_NAME github_ssh_key_secret_name=$$KEY_NAME\""

## ec2-doctor: validate operator config (env vars + leaf existence) without invoking AWS
ec2-doctor:
	$(call _require_owner)
	@echo "Owner email   : $$DEVBENCH_OWNER_EMAIL"
	@echo "Linux user    : $${DEVBENCH_LINUX_USER:-(unset)}"
	@echo "Key name      : $${DEVBENCH_KEY_NAME:-(unset)}"
	@echo "Instances dir : $(EC2_INSTANCES_DIR)"
	@echo "Resolved leaf : $(EC2_LEAF_DIR)"
	@echo "Repo path     : $${DEVBENCH_DEVBENCH_REPO:-(in-repo, default)}"
	@if [ -n "$$DEVBENCH_DEVBENCH_REPO" ] && [ ! -f "$$DEVBENCH_DEVBENCH_REPO/infra/terragrunt/root.hcl" ]; then \
	  echo "ERROR: DEVBENCH_DEVBENCH_REPO=$$DEVBENCH_DEVBENCH_REPO does not contain infra/terragrunt/root.hcl" >&2; \
	  exit 1; \
	fi
	@echo "Config is healthy."

## ec2-init: terragrunt init for the operator's leaf (uses DEVBENCH_OWNER_EMAIL).
## Removes the local .terragrunt-cache first so a stale backend hash from a
## previous devcontainer or clone does not block re-discovery of the S3 remote
## state. State itself lives in S3 and is untouched.
ec2-init:
	$(call _require_owner)
	@rm -rf "$(EC2_LEAF_DIR)/.terragrunt-cache"
	cd $(EC2_LEAF_DIR) && terragrunt init

## ec2-plan: terragrunt plan for the operator's leaf
ec2-plan:
	$(call _require_owner)
	cd $(EC2_LEAF_DIR) && terragrunt plan

## ec2-apply: provision the EC2 + minimal cloud-init. Run `make ec2-bootstrap` afterward to push the ansible playbook.
ec2-apply:
	$(call _require_owner)
	cd $(EC2_LEAF_DIR) && terragrunt apply $(TG_APPLY_ARGS)

## ec2-list: list every devbench-tagged EC2 in the account, with owner + state
ec2-list:
	@aws ec2 describe-instances \
	  --filters "Name=tag:Project,Values=devbench-remote" \
	  --query 'Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key==`Owner`]|[0].Value,InstanceType]' \
	  --output table

## ec2-start: start the operator's stopped instance
ec2-start:
	$(call _require_owner)
	@INSTANCE_ID=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw instance_id) && \
	  aws ec2 start-instances --instance-ids "$$INSTANCE_ID"

## ec2-stop: stop the operator's running instance
ec2-stop:
	$(call _require_owner)
	@INSTANCE_ID=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw instance_id) && \
	  aws ec2 stop-instances --instance-ids "$$INSTANCE_ID"

## ec2-ssh: SSH into the operator's instance via SSM tunnel
ec2-ssh:
	$(call _require_owner)
	@INSTANCE_ID=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw instance_id) && \
	  LINUX_USER=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw linux_user) && \
	  ssh $(EC2_SSH_KEY_OPT) -F infra/ssh/config.template "$${LINUX_USER}@$${INSTANCE_ID}"

## ec2-status: print instance state + last cloud-init / ansible bootstrap markers
ec2-status:
	$(call _require_owner)
	@INSTANCE_ID=$$(cd $(EC2_LEAF_DIR) && terragrunt output -raw instance_id) && \
	  aws ec2 describe-instances --instance-ids "$$INSTANCE_ID" \
	    --query 'Reservations[].Instances[].[InstanceId,State.Name,LaunchTime,InstanceType]' \
	    --output table

## ec2-refresh: re-run the bootstrap playbook against an already-provisioned instance.
## Picks up rotated secrets and any local edits to ansible roles.
ec2-refresh: ec2-bootstrap

## ec2-destroy: tear down the operator's instance (does NOT touch the network singleton)
ec2-destroy:
	$(call _require_owner)
	cd $(EC2_LEAF_DIR) && terragrunt destroy
