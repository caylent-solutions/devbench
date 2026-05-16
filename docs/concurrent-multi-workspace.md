# Concurrent Multi-Workspace Runs: The Two-Clone Pattern

This guide explains how to run two devbench instances simultaneously against the
same backlog by giving each instance a disjoint `--include` scope filter. Each
instance works on a separate subset of the backlog and claims only the work units
that belong to its scope, so the two instances never race for the same task.

This pattern uses two separate workspace root directories -- one per devbench
instance -- each with its own clone of the target repositories and its own
`.devbench/` state directory. It is a practical workaround for teams that want
to parallelise work today without the overhead of named sessions (#192). True
intra-workspace concurrency (running two orchestrators against the same workspace
root simultaneously) is provided by named sessions; see the
[segue to named sessions](#segue-to-named-sessions) section.

## Table of contents

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
  - [Step 1 -- Clone the workspace for instance A](#step-1----clone-the-workspace-for-instance-a)
  - [Step 2 -- Clone the workspace for instance B](#step-2----clone-the-workspace-for-instance-b)
  - [Step 3 -- Verify the scopes are disjoint](#step-3----verify-the-scopes-are-disjoint)
  - [Step 4 -- Launch instance A](#step-4----launch-instance-a)
  - [Step 5 -- Launch instance B](#step-5----launch-instance-b)
- [Worked example](#worked-example)
- [Overlap risk and how to avoid it](#overlap-risk-and-how-to-avoid-it)
- [Segue to named sessions](#segue-to-named-sessions)
- [Cross-references](#cross-references)

---

## How it works

DevBench enforces scope at claim time: when `devbench start --include "<tokens>"`
is used, the orchestrator writes a `scope.json` file in
`<workspace>/.devbench/scope.json` and restricts every subsequent `devbench next`
call to the work units in the expanded scope. Because each instance has its own
workspace root and therefore its own `scope.json`, and because the two `--include`
filters are disjoint (no work unit appears in both), the instances never attempt to
claim the same task.

The two instances commit to separate branches in the shared target repository and
the normal git-ops lifecycle (push, PR, merge) is unchanged.

**AC-190-12** -- Two devbench instances with disjoint `--include` filters claim
disjoint WU sets.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Two workspace root directories | Each instance needs its own directory -- they must NOT share a `JUDGE_WORKSPACE_ROOT`. |
| Shared git remote for each target repo | Both clones of a target repo must point to the same remote `origin` so commits from both instances merge via the standard PR workflow. |
| Non-overlapping `--include` tokens | The two filter strings must expand to disjoint sets of work-unit IDs. See [Step 3](#step-3----verify-the-scopes-are-disjoint). |
| DevBench installed | Follow [zero-to-ready.md](zero-to-ready.md) for the baseline single-instance setup first. |

---

## Setup

### Step 1 -- Clone the workspace for instance A

```bash
# Create a dedicated directory for instance A.
mkdir -p ~/devbench-workspace-a
cd ~/devbench-workspace-a

# Copy or re-run the backlog authoring steps from zero-to-ready.md to populate
# the backlog/ directory and backlog/config/devbench.yaml.
# Typically you initialise this from your canonical workspace:
cp -r ~/devbench-workspace/backlog ./backlog
cp -r ~/devbench-workspace/spec ./spec 2>/dev/null || true

# Clone every target repo listed in backlog/config/devbench.yaml.
# (Replace "myorg/myrepo" with the actual repo slug.)
git clone https://github.com/myorg/myrepo.git myrepo

export JUDGE_WORKSPACE_ROOT=~/devbench-workspace-a
```

### Step 2 -- Clone the workspace for instance B

```bash
# Create a separate directory for instance B.
mkdir -p ~/devbench-workspace-b
cd ~/devbench-workspace-b

# Populate backlog/ from the same canonical source.
cp -r ~/devbench-workspace/backlog ./backlog
cp -r ~/devbench-workspace/spec ./spec 2>/dev/null || true

# Clone the same target repo (different working tree, same remote origin).
git clone https://github.com/myorg/myrepo.git myrepo

export JUDGE_WORKSPACE_ROOT=~/devbench-workspace-b
```

### Step 3 -- Verify the scopes are disjoint

Before launching either instance, confirm that the two `--include` tokens you plan
to use produce non-overlapping work-unit sets. A quick check with `devbench scope show`
after a `devbench scope set` call (from instance A's workspace) helps you visualise the
expanded IDs.

```bash
# In workspace A: preview the expanded IDs for the first partition.
cd ~/devbench-workspace-a
JUDGE_WORKSPACE_ROOT=~/devbench-workspace-a \
  uv run --project $DEVBENCH_DIR devbench scope set --include "E1-E3"
JUDGE_WORKSPACE_ROOT=~/devbench-workspace-a \
  uv run --project $DEVBENCH_DIR devbench scope show

# In workspace B: preview the expanded IDs for the second partition.
cd ~/devbench-workspace-b
JUDGE_WORKSPACE_ROOT=~/devbench-workspace-b \
  uv run --project $DEVBENCH_DIR devbench scope set --include "E4-E6"
JUDGE_WORKSPACE_ROOT=~/devbench-workspace-b \
  uv run --project $DEVBENCH_DIR devbench scope show
```

Ensure the two expanded ID sets have no common entries. If any work-unit ID appears
in both, adjust one of the token strings before proceeding.

### Step 4 -- Launch instance A

Open a terminal dedicated to instance A:

```bash
cd ~/devbench-workspace-a

JUDGE_WORKSPACE_ROOT=~/devbench-workspace-a \
JUDGE_CLAUDE_MODEL=claude-sonnet-4-5 \
  uv run --project $DEVBENCH_DIR devbench start --include "E1-E3"
```

Instance A will:
1. Write `~/devbench-workspace-a/.devbench/scope.json` restricting it to work units
   in the `E1-E3` range.
2. Claim and implement only work units whose IDs match that scope.
3. Delete `scope.json` on clean exit.

### Step 5 -- Launch instance B

Open a second terminal dedicated to instance B:

```bash
cd ~/devbench-workspace-b

JUDGE_WORKSPACE_ROOT=~/devbench-workspace-b \
JUDGE_CLAUDE_MODEL=claude-sonnet-4-5 \
  uv run --project $DEVBENCH_DIR devbench start --include "E4-E6"
```

Instance B runs independently, claiming only the `E4-E6` work units.

---

## Worked example

Suppose the backlog has six epics (E1 through E6). To split the work evenly:

```bash
# Instance A handles the first three epics.
devbench start --include "E1-E3"

# Instance B handles the last three epics (separate terminal, separate workspace root).
devbench start --include "E4-E6"
```

For a more selective split using mixed tokens:

```bash
# Instance A: all of E1 and E2, plus a specific feature branch in E5.
devbench start --include "E1-E2, E5-F1"

# Instance B: everything else in E3 through E6, minus the feature branch already claimed.
devbench start --include "E3-E4, E5-F2-F4, E6"
```

See [docs/cli-reference.md](cli-reference.md) for the full scope-selector syntax
(single-ID tokens, range tokens, mixed comma-separated lists, and `--exclude`
subtraction).

---

## Overlap risk and how to avoid it

If the two `--include` filters are not disjoint, both instances can attempt to claim
the same work unit at the same time. DevBench uses atomic file operations for claim
arbitration (spec section 4.4.2), so exactly one instance will win the claim race and
the other will skip to its next candidate. However, a non-disjoint split causes
inefficiency -- the losing instance wastes a claim attempt -- and can leave a work unit
claimed by a different instance than you intended.

To avoid overlap:

1. **Partition on epics or features** whenever possible. Epic-level boundaries
   (`E1-E3` vs `E4-E6`) never overlap because each work unit belongs to exactly one
   epic.
2. **Use `devbench scope show`** (or `devbench scope set ... && devbench scope show`)
   to preview the expanded IDs before launching.
3. **Avoid mixing range and single-ID tokens** across the two filter strings unless you
   have manually verified the union covers every work unit exactly once.
4. **Check for a `collision`** in the `devbench status` output: if a work unit appears
   `in-progress` in both workspaces simultaneously, one instance will be blocked by the
   claim arbitration -- adjust the scopes and restart the affected instance.

---

## Segue to named sessions

The two-clone pattern works today but requires duplicating the workspace directory and
manually coordinating scope tokens across two separate shells. **Named sessions** (#192,
spec section 4.3) provide true intra-workspace concurrency: two orchestrators run
simultaneously within the **same workspace root**, each identified by a unique session
name and carrying its own `scope.json`:

```bash
# Terminal 1 -- session "alpha" handles E1-E3 in the shared workspace.
JUDGE_WORKSPACE_ROOT=~/devbench-workspace \
  devbench start --name alpha --include "E1-E3"

# Terminal 2 -- session "beta" handles E4-E6 in the same workspace.
JUDGE_WORKSPACE_ROOT=~/devbench-workspace \
  devbench start --name beta --include "E4-E6"
```

With named sessions:
- Each session keeps its state under `<workspace>/.devbench/sessions/<name>/` so the
  two sessions never clobber each other's `scope.json` or `drain.signal`.
- DevBench validates at startup that the two scopes are disjoint and rejects a second
  session whose `--include` overlaps an already-running session (unless
  `--allow-overlap` is passed).
- `devbench sessions` lists all active sessions, their scopes, and liveness.

Named sessions supersede the two-clone pattern for same-machine parallel runs. The
two-clone pattern remains appropriate when you want to run instances on separate
machines or separate containers where a single shared workspace root is not practical.

---

## Cross-references

- [docs/cli-reference.md](cli-reference.md) -- full scope-selector syntax, `--include`
  / `--exclude` flags, and the `scope` subcommand (`set`, `clear`, `show`).
- [docs/zero-to-ready.md](zero-to-ready.md) -- baseline single-instance setup that
  must be complete before adding a second instance.
- [docs/execution-modes.md](execution-modes.md) -- automated vs interactive modes and
  the orchestrate skill lifecycle.
