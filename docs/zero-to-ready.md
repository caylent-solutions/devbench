# Zero to Ready: DevBench End-to-End Onboarding Guide

By the end of this guide, your workspace will have a passing `devbench validate-backlog`
and be one command away from launching the orchestrator for the first time.

## Two setup paths

You have two ways to reach a running DevBench orchestrator:

| Path | When to use |
|------|-------------|
| **Skill-driven** (recommended for new projects) | You want the full setup automated -- Claude Code marketplace skills author the spec, generate the backlog, write `devbench.yaml`, and bootstrap every repo. See [docs/onboarding.md](onboarding.md) for the chained-skill workflow (`create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment -> make start`). |
| **Manual** (this guide) | You already have a backlog, need fine-grained control, or prefer to walk through each step yourself. Continue reading. |

---

## Table of contents

- [Two setup paths](#two-setup-paths)
- [Prerequisites](#prerequisites)
- [Step 1: Clone devbench](#step-1-clone-devbench)
- [Step 2: Install dependencies](#step-2-install-dependencies)
- [Step 3: Install the Claude Code plugin](#step-3-install-the-claude-code-plugin)
- [Step 4: Authenticate Claude / Bedrock](#step-4-authenticate-claude--bedrock)
- [Step 5: Set up the workspace root](#step-5-set-up-the-workspace-root)
- [Step 6: Clone the target repo(s)](#step-6-clone-the-target-repos)
- [Step 7: Author backlog/config/devbench.yaml](#step-7-author-backlogconfigdevbenchyaml)
- [Step 8: Author or import a backlog](#step-8-author-or-import-a-backlog)
- [Working with draft work units](#working-with-draft-work-units)
- [Bulk operations on the backlog](#bulk-operations-on-the-backlog)
- [Scoping a run](#scoping-a-run)
- [Stopping a run cleanly](#stopping-a-run-cleanly)
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

## Step 3: Install the Claude Code plugin (SKIP unless you need interactive mode)

> **Default recommendation: do NOT install the plugin.** The non-interactive launcher
> (`make start` -- the recommended way to run DevBench) loads the plugin ad-hoc from
> the devbench checkout via the Agent SDK. No global install is needed for any normal
> use of DevBench.
>
> **Why skipping matters:** installing the plugin at user scope registers its hooks
> **globally on this machine**. Those hooks fire on every Claude Code session you open
> -- not just orchestrator sessions -- and they intercept Write tool calls to
> `backlog/**` files. The practical effect: **any Claude session you spin up to edit a
> work unit, author a new task, fix a Manifest, or apply an operator recovery will be
> blocked from writing to the backlog.** That breaks the entire two-track operator
> workflow described in Step 10 (where you stop the orchestrator, then use a separate
> Claude session + `devbench` CLI to mutate the backlog between runs).
>
> **The only reason to install the plugin** is if you specifically want to run
> `make start-interactive` (the observation-only mode that lets you watch tool calls
> live). Even then, prefer the per-session `--plugin-dir` approach (see
> [README Interactive Mode](../README.md#interactive-mode)) so the hooks load only for
> that specific session and don't poison other Claude work.
>
> **If you install it, accept the trade-off:** while installed, you will have a hard
> time editing the backlog from other Claude sessions. Plan to uninstall (commands
> below) when you're done observing.

If you've decided you want the global install anyway:

```bash
make -C $DEVBENCH_DIR plugin-install && claude plugin list
```

This registers the devbench marketplace directory and installs the plugin at user scope so
every `claude` session on this machine can see DevBench's orchestrate skill.

**What is registered:** the `plugin/devbench-orchestrate/` directory in your devbench clone is added to
Claude Code's marketplace, and the `devbench` plugin entry is installed under your user
profile (`~/.claude/`).

**Without make:**

    claude plugin marketplace add $DEVBENCH_DIR/plugin --scope user
    claude plugin install devbench --scope user

**Verify the plugin is installed:** expected output of `claude plugin list`: a line
containing `devbench` in the installed list.

**To uninstall later** (e.g., to unblock backlog edits in other Claude sessions):

    claude plugin uninstall devbench --scope user
    claude plugin marketplace remove devbench --scope user

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

- `export DEVBENCH_USE_BEDROCK=1`
- `export DEVBENCH_BEDROCK_REGION=us-east-1`
- `export DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1`

Verify AWS auth with `aws sts get-caller-identity`. Expected output: a JSON object with
`UserId`, `Account`, and `Arn`. If this command fails, resolve AWS credentials before
proceeding. AWS credentials can be provided via env var, `~/.aws/credentials`, or IAM
role -- no extra step needed.

For the full credential-chain resolution order, see
[`docs/llm-authentication.md`](llm-authentication.md) (ref).

---

## Step 5: Set up the workspace root

The workspace root (`DEVBENCH_WORKSPACE_ROOT`) is the parent directory that contains your
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
~/my-workspace/               <- DEVBENCH_WORKSPACE_ROOT
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

`checkout_directory` is **relative to `DEVBENCH_WORKSPACE_ROOT`** (do not use an absolute
path or `..` traversal). Validation fails with a clear error if it is wrong.

### Optional toggles to consider

```yaml
git_ops:
  single_branch: feat/my-batch-branch   # single-PR mode: all tasks commit to this branch
  defer_pr: false                        # when true, PR is deferred until git-ops-finalize
  pause_before_merge: false              # when true, waits for CI green before merging

manifest_amendment:
  enabled: true    # default; set false to stop executors requesting Manifest changes mid-task

task_factory:
  enabled: false   # set true to let the orchestrator auto-generate follow-up tasks

validate:
  check_orphan_path_tokens: true    # Rule 20 (default on); set false to opt out of the AC / DoD path-coherence check

agents:                              # ADR-25: per-agent model overrides
  # Each field below pins the agent to its CURRENT frontmatter default,
  # which are tuned by the role each agent plays:
  #   - executor (writes code under TDD): sonnet -- fast happy path.
  #   - The five judges (code-reviewer, test-reviewer, doc-reviewer,
  #     changes-manifest, security-reviewer): opus -- bad verdicts cost
  #     more than inference; judges only fire after executor finishes.
  #   - blocker-resolver, manifest-amender, task-factory (workflow /
  #     recovery): opus -- judgment-heavy and fire only on unhappy
  #     paths, so cost is bounded.
  #   - review-supervisor: sonnet -- not invoked (ADR-33); retained for config back-compat.
  # Writing the same value as the frontmatter default is a no-op; flip an
  # individual field when your per-model quota is uneven (e.g. sonnet left,
  # opus exhausted). Omit the agents: block entirely (or set a field to
  # null) to use the frontmatter default. Values must match your auth
  # channel: short names (opus / sonnet / haiku) or claude-<name>-<digits>
  # when use_bedrock: false; full Bedrock ARNs when use_bedrock: true.
  # DEVBENCH_AGENT_MODEL_<NAME> env vars override this block per-call
  # (env > yaml > frontmatter). See docs/adr/25-per-agent-model-overrides.md.
  executor: sonnet
  blocker_resolver: opus
  manifest_amender: opus
  security_reviewer: opus
  task_factory: opus
  review_supervisor: sonnet
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
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

## Working with draft work units

When you set `backlog.default_status_for_new_work_units: draft` in
`backlog/config/devbench.yaml`, every newly created work unit (including those generated
by `task-factory` or imported via `spec-to-backlog`) lands in `draft` status rather than
`in-queue`. Draft work units are invisible to the orchestrator: `get_parallel_candidates`
excludes them, so the autonomous run cannot claim them until an operator explicitly
promotes them.

This gives you a review gate between generation and execution: inspect every generated
work unit, tighten scope, verify Manifests, and then release the ones you approve.

### Opting in

Add the following to your `backlog/config/devbench.yaml`:

```yaml
backlog:
  default_status_for_new_work_units: draft   # or "in-queue" (legacy default)
```

**Existing workspaces are unaffected.** Omitting this key (or leaving the default `in-queue`)
preserves the legacy behaviour exactly: new work units go straight to `in-queue` and are
eligible for autonomous claim immediately. There is no migration required.

### Reviewing the generated backlog

After generating a backlog (or after `task-factory` materialises new tasks), check how
many draft work units are waiting for review:

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench status
```

The `devbench status` summary includes a `Draft N` row so you can see the pending count
at a glance. Inspect each draft work unit's `.md` file directly -- the `## Changes
Manifest`, `## Acceptance Criteria`, and `## Approach` sections are the key places to
review before promoting.

### Promoting draft work units

Once you are satisfied with a work unit (or a group of them), transition it from
`draft -> in-queue` with `devbench promote`:

| You want to... | Use |
|---|---|
| Promote a single work unit | `devbench promote <ID>` |
| Promote every draft WU under an epic | `devbench promote --epic <epic-id>` |
| Promote every draft WU under a feature | `devbench promote --feature <feature-id>` |
| Promote every draft WU under a story | `devbench promote --story <story-id>` |
| Promote all draft WUs in the backlog | `devbench promote --all` |
| Promote all without a confirmation prompt | `devbench promote --all --yes` |

`devbench promote` refuses to promote any work unit that is not currently in `draft`
status; if you pass an ID that is already `in-queue` or `done`, it exits with rc=1 and
a clear error. Each promoted work unit receives a `[PROMOTED] draft -> in-queue`
audit-comment line in its `## Comments` section.

### Common patterns

**Review then release selectively** -- generate the full backlog, inspect each epic,
promote the epics you want the orchestrator to tackle first:

```bash
devbench promote --epic E1
devbench promote --epic E3
```

**Release everything at once** -- if you trust the generation output and want to start
the autonomous run immediately:

```bash
devbench promote --all --yes
```

**Hold back risky work** -- promote most of the backlog but place high-risk epics on
hold until you have reviewed them. Note that `draft` is only valid for task-level work
units (`E7-F1-S1-T1` style IDs); to pause an entire epic or feature use `hold` instead:

```bash
devbench promote --all --yes
devbench set-status E7 hold   # pause E7 at epic level for closer review
devbench set-status E7-F1-S1-T1 draft   # or revert a specific task back to draft
```

After promoting, proceed to Step 9 (`devbench validate-backlog`) to confirm the promoted
work units satisfy all backlog-contract rules before launching the orchestrator.

---

## Bulk operations on the backlog

After `spec-to-backlog` generates your backlog (or after `task-factory` materialises new
tasks), you often have tens or hundreds of work units to manage before launching the
orchestrator. The `devbench set-status` command provides a bulk-update surface that
accepts the same printer-pages selector syntax as `devbench start --include / --exclude`,
letting you promote, hold, or decline entire epics or task ranges in a single command.

### The three-phase workflow

1. **Review draft work units.** Inspect the generated `.md` files -- especially
   `## Changes Manifest`, `## Acceptance Criteria`, and `## Approach` -- to confirm each
   task is ready for autonomous work. Use `devbench status` to see the pending draft count.

2. **Bulk-promote or hold.** Promote the epics you want the orchestrator to tackle first;
   hold or decline anything you are not ready to run:

   ```bash
   # Preview what would be promoted -- no state changes yet:
   DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
   uv run --project $DEVBENCH_DIR devbench set-status \
     --include "E1-E3" --dry-run in-queue

   # Promote E1 through E3 to in-queue (confirm interactively if > threshold):
   DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
   uv run --project $DEVBENCH_DIR devbench set-status \
     --include "E1-E3" in-queue

   # Promote everything at once, skipping the confirmation prompt:
   DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
   uv run --project $DEVBENCH_DIR devbench set-status \
     --include "E1-E10" --yes in-queue

   # Hold E5 for closer review while releasing everything else:
   DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
   uv run --project $DEVBENCH_DIR devbench set-status \
     --include "E1-E10" --exclude "E5" in-queue
   DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
   uv run --project $DEVBENCH_DIR devbench set-status \
     --include "E5" hold
   ```

3. **Launch the orchestrator.** Once the promoted work units pass `devbench validate-backlog`,
   start the autonomous run with `make start` (see [Step 10](#step-10-launch)).

### Flag reference

| Flag | Effect |
|------|--------|
| `--include "<tokens>"` | Apply the status change to work units matching the printer-pages selector |
| `--exclude "<tokens>"` | Remove matching units from the update set (combined with --include) |
| `--dry-run` | Print the IDs that would be updated; make no state changes |
| `--yes` | Skip the confirmation prompt even when the expansion exceeds `bulk_update_confirm_threshold` |

When the number of work units that would be updated exceeds `bulk_update_confirm_threshold`
(default 10, configurable in `backlog/config/devbench.yaml`) and `--yes` is not supplied,
`set-status` prints the affected IDs and asks for confirmation before proceeding.

Every bulk invocation appends a `[BULK_STATUS_UPDATE]` audit row to the path configured
in `bulk_update_audit_path` (default `logs/bulk-updates.log`), recording the selector
expression, target status, affected IDs, and timestamp.

### Complement to devbench promote

`devbench set-status` and `devbench promote` serve different purposes:

- **`devbench promote`** -- moves `draft` WUs to `in-queue` only. Use it when you want the
  `draft -> in-queue` lifecycle semantics enforced (refuses non-draft inputs) and the
  `[PROMOTED]` audit comment written per WU.
- **`devbench set-status --include`** -- moves WUs to any valid status (not just
  `in-queue`), and does not enforce a source-status constraint. Use it to
  hold, decline, or re-queue a subtree regardless of its current status.

For the selector syntax reference (single-ID tokens, range tokens, mixed lists), see
[Scoping a run -- Printer-pages token syntax](#printer-pages-token-syntax) and
[`docs/cli-reference.md` -- set-status](cli-reference.md#set-status).

---

## Scoping a run

By default `devbench start` processes every eligible work unit in the backlog. Scope
selectors let you restrict the orchestrator to a subset -- for example, a single epic you
want to land first, or all epics except a risky one you want to review manually.

### Printer-pages token syntax

Tokens are comma-separated values passed to `--include` and `--exclude`. Whitespace around
commas is ignored. Two token types are recognised:

**Single-ID token** -- matches the exact ID and every descendant. Descendants are IDs
whose string starts with `<token>-`.

| Token | What it matches |
|-------|-----------------|
| `E2` | Epic E2 and all features, stories, and tasks under it |
| `E2-F1` | Feature E2-F1 and all stories and tasks under it |
| `E2-F1-S1-T3` | Exactly task E2-F1-S1-T3 (leaf; no descendants) |

**Range token** -- two adjacent same-type segments at the end of the token. Expands
inclusively on the final segment; earlier segments must match exactly.

| Token | What it matches |
|-------|-----------------|
| `E1-E3` | All work units under epics E1, E2, and E3 |
| `E5-F1-F3` | All work units under features E5-F1, E5-F2, and E5-F3 |
| `E5-F1-S1-T2-T5` | Tasks E5-F1-S1-T2, T3, T4, T5 and any descendants |

**Mixed comma-separated list** -- tokens are unioned:

```
--include "E1-E3, E5"
# matches all work units under E1, E2, E3, and E5
```

**Reverse ranges** (`E3-E1`) are rejected immediately with an actionable error message
and exit code 1. **Out-of-range tokens** (no matching work unit in the backlog) emit a
warning but do not abort -- the run continues with the remaining matched IDs.

For the full syntax reference including edge cases and evaluation order, see
[`docs/cli-reference.md` -- Scope selectors](cli-reference.md#scope-selectors-printer-pages-syntax).

### Scoping devbench start

Pass `--include` (and optionally `--exclude`) to `devbench start`:

```bash
# Run only epics E1 through E3 plus E5:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench start --include "E1-E3, E5"

# Run E1 through E10 but skip E5 and everything under E7-F3:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench start \
  --include "E1-E10" --exclude "E5, E7-F3"
```

When `--include` is supplied, the parsed scope is persisted atomically to
`<workspace>/.devbench/scope.json` before the orchestrate skill starts. Subsequent
`devbench status`, `devbench report`, and `devbench next` invocations consult this file
automatically (no extra flags needed) and render a `SCOPE:` banner above their output.
The scope.json file is deleted on clean orchestrator exit; it survives orchestrator
crashes so a follow-up `devbench status` still shows the active scope.

### Scoping a run interactively

When you want to set the scope before launching interactive Claude Code (so the
orchestrate skill respects the filter without you having to launch and kill `devbench
start` first), use `devbench scope set`. The scope.json it writes is byte-identical to the
one `devbench start --include` writes -- the orchestrate skill honours both pathways
identically at Step 1c (consulting scope.json before claiming the next work unit):

```bash
# Step 1: Write scope.json without starting the orchestrator:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench scope set --include "E1-E3, E5"

# Step 2 (optional): Inspect the active scope:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench scope show

# Step 3: Launch interactive Claude Code; the orchestrate skill respects scope.json:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
claude --dangerously-skip-permissions \
  --plugin-dir $DEVBENCH_DIR/plugin/devbench

# Step 4: Clear the scope when done:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench scope clear
```

`devbench scope clear` is idempotent -- it exits 0 with the message `no scope pending`
when no scope file is present.

`devbench validate-backlog` ignores scope.json entirely -- it always validates the
whole backlog regardless of any active scope.

---

## Stopping a run cleanly

When you want to upgrade devbench, patch a work unit, or simply pause the autonomous
run between tasks, use `devbench drain` to request a graceful stop. The orchestrator
finishes the current work unit (reaching `done` or `blocked`), then exits cleanly
with rc=0. It does NOT kill the in-flight executor mid-claim.

### Requesting a drain

```bash
# Request a graceful stop with no reason (the orchestrator exits after the current WU):
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench drain

# With a human-readable reason (recorded in the drain marker and audit log):
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench drain --reason "upgrading devbench to v1.2"
```

Once the drain marker is written, `devbench status` prepends a
`DRAIN REQUESTED: at <ts> by <user> (reason: <text>)` banner so you can confirm the
signal is pending.

### Checking drain state

```bash
# Print marker contents (requested_by, at, reason) if pending; "no drain pending" otherwise.
# Exit code is rc=0 in both states -- safe to use in scripts.
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench drain --status
```

### Cancelling a drain request

If you change your mind before the orchestrator picks up the marker, withdraw the
request. The cancel is idempotent -- it exits rc=0 and prints "no drain pending" if no
marker is present:

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench drain --cancel
```

After cancelling, the orchestrator continues claiming the next work unit as if no drain
was ever requested (AC-188-10).

### What happens when the orchestrator sees the marker

1. The orchestrator completes the current work unit (executor + judges + git-ops).
2. Between work units, the skill runs `devbench drain --status`; if pending it logs an
   `[ORCHESTRATOR_DRAIN]` audit comment on the last WU and exits cleanly.
3. The drain marker is consumed (deleted) on orchestrator exit, so a subsequent
   `devbench start` runs without any drain constraint (AC-188-5).

### Pre-arm pattern: run exactly one WU then exit

Drop the drain marker **before** launching `devbench start`. The orchestrator will
claim one work unit, complete it, detect the pre-armed drain between WUs, and exit.
This is useful when you want to verify a single task end-to-end before committing to a
full autonomous run:

```bash
# Step 1: write the drain marker:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench drain --reason "single-WU test run"

# Step 2: start the orchestrator; it will process one WU then exit:
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start
```

The drain marker is consumed on exit; run `devbench drain --status` to confirm no
drain is pending before starting the next full run (AC-188-6).

For the complete flag reference, exit-code table, and session-scoped drain variants
(`devbench drain --session <name>`, `devbench drain --all`), see
[`docs/cli-reference.md` -- drain](cli-reference.md#drain).

---

## Step 9: Validate

Run `devbench validate-backlog` from any directory; provide the workspace root and model
via environment variables:

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench validate-backlog
```

**With Bedrock:** add `DEVBENCH_USE_BEDROCK=1` and `DEVBENCH_BEDROCK_REGION=us-east-1` to the
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

### Non-interactive mode (recommended)

Non-interactive mode runs the orchestrator as a headless Claude Agent SDK process. This
is the recommended way to run DevBench. The system is stable enough that the backlog
itself is the right place to manage the run, not a live console session.

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start
```

**Without make:** run `uv run --project $DEVBENCH_DIR python -m devbench.cli start` with
the same environment variables set.

**Why non-interactive is the default:** the orchestrator's lifecycle (claim -> implement
-> review -> retry -> git-ops -> mark-done) is deterministic and self-correcting. The
review judges + manifest amender + blocker resolver already catch most issues without
human input. Live operator interjection during a claim usually disturbs the executor
mid-turn and produces worse outcomes than letting the cycle complete and then editing
the backlog afterwards.

**Live observation while non-interactive is running** -- you do NOT need to open
interactive mode to see what the orchestrator is doing. Open side terminals against
the same workspace:

```bash
# Terminal 2: every tool call, judge verdict, status transition streamed live.
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench hook-tail

# Terminal 3: live progress dashboard (epic counts, judges, CI, cost).
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench report

# Terminal 4 (optional): low-frequency status snapshot.
cd ~/my-workspace && watch -n 60 \
  'DEVBENCH_WORKSPACE_ROOT=$PWD DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
   uv run --project $DEVBENCH_DIR devbench status'
```

Between those plus `git log` on your backlog repo (every status promotion, TDD-cycle
entry, audit comment lands as a commit-worthy diff), you see exactly what the
orchestrator is doing. Interactive mode adds almost nothing beyond this except the
ability to type instructions mid-run -- which is the one thing we recommend against.

### What `make start` does on its own (auto-restart on SDK degradation)

`make start` wraps `uv run python -m devbench.cli start` in a bounded while-loop.
Whenever the orchestrator exits with code `42`, the loop re-launches it -- up to
`DEVBENCH_MAX_AUTO_RESTARTS` attempts (default 3, override via env var). Any other
exit code passes through unchanged and the loop stops.

`cmd_start` returns `42` only when the SDK subprocess exited cleanly via
`NO_ACTIONABLE` AND the post-mortem inspection finds: at least one BLOCKED task
classified as `BlockedTaskState.RUNTIME_DEGRADATION` (the Claude Agent SDK lost
Agent-tool access mid-session), zero `IN_PROGRESS` / `IN_REVIEW` tasks, and zero
`OPERATOR_ACTION_REQUIRED` blockers. A fresh SDK subprocess typically clears the
degradation, so the loop will pick the same tasks back up on the next attempt.
If the cap is exhausted, the Makefile fails with rc=1 + a clear error so an
operator can investigate the persistent SDK-side issue. The audit lines
(`[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks=<ids>` and
`INFO: orchestrator auto-restart (attempt N/max)`) live in
`logs/orchestrator.log` and on stderr respectively.

### If you need to change something while the run is in flight

Stop the orchestrator (Ctrl+C on the `make start` process), then **manage the change
through the backlog itself.** Two distinct tools, two distinct responsibilities:

#### `devbench` CLI -- moves state and wires the graph

Use the `devbench` CLI for status transitions, dep wiring, comments, and validation. No
file edits, just state mutations. From a separate Claude session pointed at your
workspace:

| You want to... | Use |
|---|---|
| Release a draft work unit for autonomous claim | `devbench promote <ID>` |
| Release all draft work units in the backlog | `devbench promote --all --yes` |
| Skip a task entirely | `devbench decline <ID> --reason "<message>"` |
| Pause a task pending more context | `devbench hold <ID> --reason "<message>"` |
| Resume a held task | `devbench unhold <ID> --reason "<message>"` |
| Wire an ordering constraint | `devbench add-dep <blocked-id> <blocker-id>` |
| Record an operator audit note | `devbench log-comment operator <ID> "<message>"` |
| Re-queue an `amendment-recovery` block after editing the work unit | `devbench set-status <ID> in-queue` |
| Reconcile dep state after edits | `devbench sync-blocked` |
| Re-check parser integrity | `devbench validate-backlog` |

#### Claude -- edits the work-unit `.md` content

Use Claude (in a separate session, pointed at your workspace) for any change that
touches **the content of a work-unit file** or adds a new work unit. The CLI doesn't
edit prose; Claude does. Typical content edits:

| You want to... | Have Claude do |
|---|---|
| Rewrite an Approach to authorise a production fix | Edit the `### Approach` block in the work-unit `.md`; add an explicit operator-authorisation step with scope guardrails |
| Adjust a Manifest (add / remove files, change scope) | Edit the `## Changes Manifest` table; keep paths repo-relative |
| Tighten or relax an Acceptance Criterion | Edit the `## Acceptance Criteria` list |
| Add a brand-new work unit (recovery task, follow-up) | Author a new `*.md` under the right Epic / Feature / Story dir following `docs/example-work-unit-template.md`; then update `BACKLOG.md` |
| Split a too-large task into two | Edit the original `.md` to narrow scope; author a sibling `.md` for the carved-out work |
| Fix an em-dash / orphan-path / manifest-conflict that `validate-backlog` flagged | Edit the offending file per the rule; re-run `devbench validate-backlog` |

After any content edit, run `devbench validate-backlog` to confirm the file still
satisfies the 20 backlog-contract rules, then move state with the `devbench` CLI table
above. Restart with `make start` once the changes are in place. A worked example of
this two-track workflow is described in
[`examples/backlogs/brownfield/multi-repo_single-pr_no-merge/README.md`](../examples/backlogs/brownfield/multi-repo_single-pr_no-merge/README.md).

### Interactive mode (rarely needed)

Interactive mode opens a Claude Code session with the devbench plugin loaded so you can
watch tool calls inside Claude Code's UI. **It offers almost nothing over non-interactive
+ `devbench hook-tail` + `devbench report`** -- both modes give live tool-call visibility.
The only unique capability of interactive mode is the ability to type natural-language
instructions to the orchestrate skill mid-run, and that is exactly what we recommend
AGAINST (mid-claim interjection disturbs the executor's reasoning; corrections belong in
the backlog via the two-track workflow above). If you still want it (e.g., for a guided
walk-through of how a single task progresses through judges), here is how:

**With the default `--dangerously-skip-permissions` flag** (the orchestrator needs to read
and write files without per-tool confirmation):

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start-interactive
```

**With `DEVBENCH_SAFE_PERMISSIONS=1`** (sandboxed: Claude Code asks for confirmation before
each file operation -- slower but safer if you want to watch each tool call confirm):

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
DEVBENCH_SAFE_PERMISSIONS=1 \
make -C $DEVBENCH_DIR start-interactive
```

When `DEVBENCH_SAFE_PERMISSIONS=1` is set, the Makefile omits
`--dangerously-skip-permissions`, so Claude Code prompts before every sensitive tool use.

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
| Extra env var | none | `DEVBENCH_USE_BEDROCK=1` |

Default is Anthropic API. Set `DEVBENCH_USE_BEDROCK=1` (and `DEVBENCH_BEDROCK_REGION`) to switch.

### Single-PR vs multi-PR (Step 7)

| Mode | `devbench.yaml` setting | Effect |
|------|------------------------|--------|
| Multi-PR (default) | `git_ops.single_branch` unset | One branch and PR per task |
| Single-PR | `git_ops.single_branch: feat/batch` | All tasks commit to one shared branch |

Single-PR mode requires `git_ops.defer_pr: false` (default) or pairing with
`git_ops.defer_pr: true` and running `devbench git-ops-finalize` when the batch is ready.

### manifest_amendment.enabled (Step 7)

When enabled, the executor can request to add files to its Manifest mid-task (for TDD
scenarios where a production fix is discovered after the spec was written). Enabled by
default; set `false` to opt out.

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

`make install`, `make plugin-install`, `make start`, and `make start-interactive` are
convenience wrappers. Every step in this guide includes the equivalent bare `uv run` /
`claude` form under "Without make" so operators on environments without GNU make can
proceed. Note that Step 3 (`make plugin-install`) is skippable for non-interactive
runs -- only needed for the optional `make start-interactive` observation mode.

---

## Troubleshooting

### `DEVBENCH_WORKSPACE_ROOT not set`

```
RuntimeError: DEVBENCH_WORKSPACE_ROOT environment variable is not set. Set it to the absolute path of your workspace root.
```

Export the variable before running any devbench command:

    export DEVBENCH_WORKSPACE_ROOT=~/my-workspace

Or prefix it inline:

    DEVBENCH_WORKSPACE_ROOT=~/my-workspace uv run --project $DEVBENCH_DIR devbench validate-backlog

### `DevBench config file not found`

```
FileNotFoundError: DevBench config file not found at '<DEVBENCH_WORKSPACE_ROOT>/backlog/config/devbench.yaml'
```

The config file was not created (Step 7) or `DEVBENCH_WORKSPACE_ROOT` is pointing at the
wrong directory. Verify:

    ls $DEVBENCH_WORKSPACE_ROOT/backlog/config/devbench.yaml

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
- [`docs/onboarding.md`](onboarding.md) (ref) -- chained-skill operator workflow (create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment -> make start)
- [`docs/creating-specs-and-backlogs.md`](creating-specs-and-backlogs.md) (ref) -- full backlog-authoring guide
- [`docs/backlog-contract.md`](backlog-contract.md) (ref) -- validation rule set and workspace layout
- [`docs/llm-authentication.md`](llm-authentication.md) (ref) -- full Claude / Bedrock auth options
- [`docs/manual-blockers.md`](manual-blockers.md) (ref) -- manual-blocker format

---

*Last execution-validated end-to-end at SHA `0f91c8ac3e138a7003985bb1a766518929f30154`.*
