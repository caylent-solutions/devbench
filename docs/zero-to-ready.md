# Zero to Ready: DevBench End-to-End Onboarding Guide

By the end of this guide, your workspace will have a passing `devbench validate-backlog`
and be one command away from launching the orchestrator for the first time.

## Table of contents

- [Prerequisites](#prerequisites)
- [Step 1: Clone devbench](#step-1-clone-devbench)
- [Step 2: Install dependencies](#step-2-install-dependencies)
- [Step 3: Install the Claude Code plugin](#step-3-install-the-claude-code-plugin)
- [Step 4: Authenticate Claude / Bedrock](#step-4-authenticate-claude--bedrock)
- [Step 5: Set up the workspace root](#step-5-set-up-the-workspace-root)
- [Step 6: Clone the target repo(s)](#step-6-clone-the-target-repos)
- [Step 7: Author backlog/config/devbench.yaml](#step-7-author-backlogconfigdevbenchyaml)
- [Step 8: Author or import a backlog](#step-8-author-or-import-a-backlog)
- [Step 9: Validate](#step-9-validate)
- [Step 10: Launch](#step-10-launch)
- [Decision points](#decision-points)
- [Troubleshooting](#troubleshooting)
- [Cross-references](#cross-references)

---

## Prerequisites

Verify each tool is present before cloning.

| Tool | Minimum version | Verify |
|------|----------------|--------|
| git | 2.30+ | `git --version` |
| uv | 0.5+ | `uv --version` |
| Claude Code CLI | any | `claude --version` |

Install missing tools using your OS package manager for git, `curl -LsSf https://astral.sh/uv/install.sh | sh` for uv, and `npm install -g @anthropic-ai/claude-code` for Claude Code.

Also confirm you have a Claude Code subscription (Claude Pro or Enterprise) **or** AWS
credentials with Bedrock model access enabled. See [Step 4](#step-4-authenticate-claude--bedrock)
for the trade-offs.

---

## Step 1: Clone devbench

Clone the repository and export `DEVBENCH_DIR` so all subsequent steps can reference the
clone location without a placeholder:

```bash
git clone https://github.com/caylent-solutions/devbench.git ~/devbench
export DEVBENCH_DIR=~/devbench
cd $DEVBENCH_DIR
git log --oneline -1
```

`DEVBENCH_DIR` is used by every step that invokes `uv run --project $DEVBENCH_DIR` or
`make -C $DEVBENCH_DIR`. Add the export to your shell profile (`~/.bashrc`, `~/.zshrc`)
if you want it to persist across sessions.

**SHA pinning (reproducible installs).** If you need a known-good revision, pin with
`git checkout <sha>` after cloning. Expected output of `git log --oneline -1`: one line
with the short SHA and commit message.

---

## Step 2: Install dependencies

```bash
make -C $DEVBENCH_DIR install
```

This runs `uv sync --all-extras` inside the devbench clone, creating a `.venv/` and
installing every runtime and dev dependency declared in `pyproject.toml`.

**Without make:** run `uv sync --all-extras` from inside `$DEVBENCH_DIR`.

Expected exit code: 0. You will see uv resolving and installing packages; the final line
will be something like `Installed N packages`.

---

## Step 3: Install the Claude Code plugin

```bash
make -C $DEVBENCH_DIR plugin-install && claude plugin list
```

This registers the devbench marketplace directory and installs the plugin at user scope so
every `claude` session on this machine can see DevBench's orchestrate skill.

**What is registered:** the `plugin/devbench/` directory in your devbench clone is added to
Claude Code's marketplace, and the `devbench` plugin entry is installed under your user
profile (`~/.claude/`).

**Without make:**

    claude plugin marketplace add $DEVBENCH_DIR/plugin --scope user
    claude plugin install devbench --scope user

**Verify the plugin is installed:** expected output of `claude plugin list`: a line
containing `devbench` in the installed list.

---

## Step 4: Authenticate Claude / Bedrock

DevBench supports two LLM backends. Choose one.

### Option A: Anthropic API (default, via Claude Code OAuth)

Requires a Claude Pro or Enterprise subscription.

Run `claude` to open the browser OAuth flow and complete the login. Claude Code writes
`~/.claude/.credentials.json` with an OAuth access token. DevBench reads this file at
runtime -- no separate API key is required.

Verify the credentials file exists:

```bash
test -f ~/.claude/.credentials.json && echo "ok" || echo "missing"
```

For full details on the OAuth flow and token refresh, see
[`docs/llm-authentication.md`](llm-authentication.md) (ref).

### Option B: AWS Bedrock

Requires AWS credentials with Bedrock model access enabled in your account.

Set the required environment variables in your shell profile or before each invocation:

- `export JUDGE_USE_BEDROCK=1`
- `export JUDGE_BEDROCK_REGION=us-east-1`
- `export JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1`

Verify AWS auth with `aws sts get-caller-identity`. Expected output: a JSON object with
`UserId`, `Account`, and `Arn`. If this command fails, resolve AWS credentials before
proceeding. AWS credentials can be provided via env var, `~/.aws/credentials`, or IAM
role -- no extra step needed.

For the full credential-chain resolution order, see
[`docs/llm-authentication.md`](llm-authentication.md) (ref).

---

## Step 5: Set up the workspace root

The workspace root (`JUDGE_WORKSPACE_ROOT`) is the parent directory that contains your
backlog, your YAML config, and your cloned target repo(s) as siblings. It is **not** the
devbench clone itself.

Create and initialize the workspace root:

```bash
mkdir -p ~/my-workspace/backlog/config
cd ~/my-workspace
git init
printf '# Target repo siblings -- cloned separately; not part of the backlog history\n/my-target-repo/\n' > .gitignore
cat > BACKLOG.md <<'EOF'
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |
|------|-------|------|-------------|----------|---------|----------|

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
EOF
```

The canonical workspace layout at this point (from
[`docs/backlog-contract.md`](backlog-contract.md) (ref)):

```
~/my-workspace/               <- JUDGE_WORKSPACE_ROOT
  .gitignore
  BACKLOG.md
  backlog/
    config/
      devbench.yaml           <- authored in Step 7
  my-target-repo/             <- cloned in Step 6
```

---

## Step 6: Clone the target repo(s)

Clone each repository you want DevBench to operate on **into the workspace root as a
sibling of `backlog/`**:

```bash
cd ~/my-workspace && git clone https://github.com/your-org/your-repo.git my-target-repo
```

The directory name you use here (`my-target-repo`) must match the `checkout_directory`
value you set in `devbench.yaml` in the next step.

**Naming convention:** use the short repo name without the org prefix (e.g.,
`my-target-repo` not `your-org/my-target-repo`). Slashes are not valid in directory
names.

---

## Step 7: Author backlog/config/devbench.yaml

Copy the reference config from your devbench clone as a starting point:

```bash
cp $DEVBENCH_DIR/sample-config.yaml ~/my-workspace/backlog/config/devbench.yaml
```

Open the file and edit the required keys:

### Required keys

```yaml
repos:
  your-org/your-repo:
    default_branch: main
    checkout_directory: my-target-repo   # must match the directory name from Step 6

merge_strategy: squash   # or "merge" or "rebase"
```

`checkout_directory` is **relative to `JUDGE_WORKSPACE_ROOT`** (do not use an absolute
path or `..` traversal). Validation fails with a clear error if it is wrong.

### Optional toggles to consider

```yaml
git_ops:
  single_branch: feat/my-batch-branch   # single-PR mode: all tasks commit to this branch
  defer_pr: false                        # when true, PR is deferred until git-ops-finalize
  pause_before_merge: false              # when true, waits for CI green before merging

manifest_amendment:
  enabled: false   # set true to allow executor to request Manifest changes mid-task

task_factory:
  enabled: false   # set true to let the orchestrator auto-generate follow-up tasks

validate:
  check_orphan_path_tokens: false   # opt-in path-coherence check on AC / DoD prose
```

The full annotated reference with every possible key and its default value is
[`sample-config.yaml`](../sample-config.yaml) (ref).

---

## Step 8: Author or import a backlog

### Minimum-viable backlog (new project)

Use `devbench new-task` to scaffold a work-unit file from the canonical template. Create
the directory tree first, then run the command with `--project` pointing at your devbench
clone:

```bash
cd ~/my-workspace
mkdir -p backlog/E1-my-first-epic/E1-F1-initial-feature/E1-F1-S1-first-story
uv run --project $DEVBENCH_DIR devbench new-task \
  --id "E1-F1-S1-T1" \
  --title "First task" \
  --target backlog/E1-my-first-epic/E1-F1-initial-feature/E1-F1-S1-first-story/E1-F1-S1-T1.md \
  --repo "your-org/your-repo"
```

This writes a task file at
`backlog/E1-my-first-epic/E1-F1-initial-feature/E1-F1-S1-first-story/E1-F1-S1-T1.md`
with stub sections. Open the file and fill in:

- `## Description` -- what the task does and why
- `## Acceptance Criteria` -- at least one `AC-` prefixed, testable item
- `## Changes Manifest` -- the exact files the task will create or modify
- `## Definition of Done` -- done when all ACs pass and tests are green

Also update `BACKLOG.md` to add an index row for the new task:

```markdown
## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |
|------|-------|------|-------------|----------|---------|----------|
| E1   | My first epic | 0 | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E1-F1-S1-T1 | First task | Task | in-queue | None | org/my-target-repo | `backlog/E1-my-first-epic/E1-F1-initial-feature/E1-F1-S1-first-story/E1-F1-S1-T1.md` |
```

### Importing an existing backlog

If you already have a structured backlog directory, copy the `backlog/` tree and
`BACKLOG.md` into the workspace root and proceed to Step 9.

For full backlog-authoring guidance including specs, TDD annotations, lifecycle tests, and
the Git strategy section, see
[`docs/creating-specs-and-backlogs.md`](creating-specs-and-backlogs.md) (ref).

---

## Step 9: Validate

Run `devbench validate-backlog` from any directory; provide the workspace root and model
via environment variables:

```bash
JUDGE_WORKSPACE_ROOT=~/my-workspace \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench validate-backlog
```

**With Bedrock:** add `JUDGE_USE_BEDROCK=1` and `JUDGE_BEDROCK_REGION=us-east-1` to the
same invocation alongside the other variables.

Expected output on success:

```
Backlog integrity check passed.
```

Exit code: 0.

### Reading the error messages

When validation fails, each error is one line identifying the rule number and the offending
file. Common patterns:

| Error | Fix |
|-------|-----|
| `E1-F1-S1-T1: work unit file missing -- expected backlog/.../E1-F1-S1-T1.md` | Add the missing work-unit file at the expected path |
| `E1-F1-S1-T1: status mismatch -- index has 'in-queue', file has 'in-progress'` | Align the `## Status:` in the work-unit file with `BACKLOG.md` |
| `E1-my-first-epic/E1-F1-initial-feature/E1-F1-S1-first-story/E1-F1-S1-T1.md: orphaned work unit file not in BACKLOG.md` | Add a row to `BACKLOG.md` for the file, or delete the file |
| `E1-F1-S1-T1: dependency 'E1-F1-S1-T2' not found in backlog index` | A dependency row references an ID that does not exist; fix the ID or create the missing task |
| `E1-F1-S1-T1: contains em-dash character (U+2014) -- use double hyphen instead` | Remove the U+2014 character and replace with a plain hyphen or double-hyphen; see rule 10 in the backlog contract |
| `E1-F1-S1-T1: Changes Manifest path 'my-target-repo/src/foo.py' begins with checkout_directory prefix 'my-target-repo/'. Paths must be repo-relative (drop the prefix)` | Remove the `my-target-repo/` prefix from paths in `## Changes Manifest` |

Iterate: fix the reported error, re-run `validate-backlog`, repeat until exit 0.

For the complete rule list (20 rules as of v-next), see
[`docs/backlog-contract.md`](backlog-contract.md) (ref).

---

## Step 10: Launch

Choose interactive or non-interactive mode.

### Interactive mode (recommended for first-time operators)

Interactive mode opens a Claude Code session with the devbench plugin loaded. You can
observe every tool call the orchestrator makes in real time and intervene if something
looks wrong.

**With the default `--dangerously-skip-permissions` flag** (the orchestrator needs to read
and write files without per-tool confirmation):

```bash
JUDGE_WORKSPACE_ROOT=~/my-workspace \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start-interactive
```

**With `JUDGE_SAFE_PERMISSIONS=1`** (sandboxed: Claude Code asks for confirmation before
each file operation -- slower but safer for a first run):

```bash
JUDGE_WORKSPACE_ROOT=~/my-workspace \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
JUDGE_SAFE_PERMISSIONS=1 \
make -C $DEVBENCH_DIR start-interactive
```

When `JUDGE_SAFE_PERMISSIONS=1` is set, the Makefile omits
`--dangerously-skip-permissions`, so Claude Code prompts before every sensitive tool use.

### Non-interactive mode

Non-interactive mode runs the orchestrator as a headless Claude Agent SDK process. Suitable
for CI or overnight runs.

```bash
JUDGE_WORKSPACE_ROOT=~/my-workspace \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start
```

**Without make:** run `uv run --project $DEVBENCH_DIR python -m devbench.cli start` with
the same environment variables set.

**Note on end-to-end validation of Step 10:** the launch commands open a live orchestrator
session and cannot safely be invoked in an automated validation pass. During execution
validation, these commands were verified with `--dry-run` (`make -C $DEVBENCH_DIR --dry-run start-interactive`
and `make -C $DEVBENCH_DIR --dry-run start`) to confirm the Makefile expands to the
correct `claude` and `uv run python -m devbench.cli start` invocations. The dry-run exit
code 0 confirms the Makefile targets are well-formed; the actual live invocations are
operator-initiated.

---

## Decision points

These decisions appear at specific steps. Each is a one-time choice per workspace.

### Bedrock vs Anthropic API (Step 4)

| | Anthropic API | AWS Bedrock |
|--|--------------|------------|
| Credential type | Claude Code OAuth (`~/.claude/.credentials.json`) | AWS IAM role / access keys |
| Subscription needed | Claude Pro or Enterprise | AWS account with Bedrock access enabled |
| Extra env var | none | `JUDGE_USE_BEDROCK=1` |

Default is Anthropic API. Set `JUDGE_USE_BEDROCK=1` (and `JUDGE_BEDROCK_REGION`) to switch.

### Single-PR vs multi-PR (Step 7)

| Mode | `devbench.yaml` setting | Effect |
|------|------------------------|--------|
| Multi-PR (default) | `git_ops.single_branch` unset | One branch and PR per task |
| Single-PR | `git_ops.single_branch: feat/batch` | All tasks commit to one shared branch |

Single-PR mode requires `git_ops.defer_pr: false` (default) or pairing with
`git_ops.defer_pr: true` and running `devbench git-ops-finalize` when the batch is ready.

### manifest_amendment.enabled (Step 7)

When enabled, the executor can request to add files to its Manifest mid-task (for TDD
scenarios where a production fix is discovered after the spec was written). Disabled by
default. Enable only when your backlog's TDD discipline requires it.

### task_factory.enabled (Step 7)

When enabled, the orchestrator can auto-generate new backlog tasks from proposals emitted
by blocked executors. Requires `manifest_amendment.enabled: true`. Disabled by default.

### Manual blockers vs regular deps (Step 8)

Use `## Dependencies` rows (regular deps) when the ordering constraint is purely sequencing
between two tasks in your backlog. Use a manual blocker (`DO NOT CLAIM` in the task
description) when a task must wait for a human action (external API access, secret
provisioning, sign-off) that has no corresponding task ID. See
[`docs/manual-blockers.md`](manual-blockers.md) (ref) for the format.

### With-make vs without-make (Steps 2, 3, 10)

`make install`, `make plugin-install`, and `make start-interactive` are convenience
wrappers. Every step in this guide includes the equivalent bare `uv run` / `claude` form
under "Without make" so operators on environments without GNU make can proceed.

---

## Troubleshooting

### `JUDGE_WORKSPACE_ROOT not set`

```
RuntimeError: JUDGE_WORKSPACE_ROOT environment variable is not set. Set it to the absolute path of your workspace root.
```

Export the variable before running any devbench command:

    export JUDGE_WORKSPACE_ROOT=~/my-workspace

Or prefix it inline:

    JUDGE_WORKSPACE_ROOT=~/my-workspace uv run --project $DEVBENCH_DIR devbench validate-backlog

### `DevBench config file not found`

```
FileNotFoundError: DevBench config file not found at '<JUDGE_WORKSPACE_ROOT>/backlog/config/devbench.yaml'
```

The config file was not created (Step 7) or `JUDGE_WORKSPACE_ROOT` is pointing at the
wrong directory. Verify:

    ls $JUDGE_WORKSPACE_ROOT/backlog/config/devbench.yaml

### `Manifest path begins with checkout_directory prefix`

```
E1-F1-S1-T1: Changes Manifest path 'my-target-repo/src/foo.py' begins with checkout_directory prefix 'my-target-repo/'. Paths must be repo-relative (drop the prefix); see docs/backlog-contract.md.
```

Paths in `## Changes Manifest` must be relative to the target repo root, not to the
workspace root. Remove the `my-target-repo/` prefix from the offending row:

```markdown
# Wrong:
| `my-target-repo/src/foo.py` | new file |

# Correct:
| `src/foo.py` | new file |
```

### Plugin not found after `make plugin-install`

If `claude plugin list` does not show `devbench`, try running the install commands
manually from the devbench clone root:

    claude plugin marketplace add $DEVBENCH_DIR/plugin --scope user
    claude plugin install devbench --scope user

If the `plugin/` directory is missing, verify you cloned the full devbench repo (not a
shallow clone with `--depth 1`) and that the clone is at a commit that includes the plugin
directory.

---

## Cross-references

- [`README.md`](../README.md) (ref) -- project overview and quick-start
- [`docs/creating-specs-and-backlogs.md`](creating-specs-and-backlogs.md) (ref) -- full backlog-authoring guide
- [`docs/backlog-contract.md`](backlog-contract.md) (ref) -- validation rule set and workspace layout
- [`docs/llm-authentication.md`](llm-authentication.md) (ref) -- full Claude / Bedrock auth options
- [`docs/manual-blockers.md`](manual-blockers.md) (ref) -- manual-blocker format
- [`docs/upgrade-guide.md`](upgrade-guide.md) (ref) -- migrating an existing workspace to a newer devbench version

---

*Last execution-validated end-to-end at SHA `0f91c8ac3e138a7003985bb1a766518929f30154`.*
