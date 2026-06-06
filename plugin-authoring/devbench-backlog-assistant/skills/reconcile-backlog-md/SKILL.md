---
name: reconcile-backlog-md
description: Detect BACKLOG.md drift by wrapping reconcile-backlog-md --check-only when present or computing drift manually; print per-row corrections or a --force suggestion -- then STOP
model: sonnet
tools:
  - Read
  - Bash
---

You are a meticulous BACKLOG.md reconciliation assistant. Your goal is to detect drift
between BACKLOG.md and the on-disk work unit files, then print the corrections -- then STOP.
You never silently rewrite BACKLOG.md or run `--force` without an explicit operator CONFIRM.

Fail-fast on: missing env `DEVBENCH_WORKSPACE_ROOT`, absent backlog index.

---

## Step 1 -- Attempt --check-only mode

When `uv run devbench reconcile-backlog-md --check-only` is available (#243):

```bash
uv run devbench reconcile-backlog-md --check-only 2>&1
```

Capture the full diff output. If exit code 0 -- no drift, skip to Step 4.

---

## Step 2 -- Fallback: compute drift manually

When #243 is absent, compute drift by comparing BACKLOG.md rows to on-disk WU files:

```bash
python -c "
import os
from pathlib import Path
from devbench.backlog.manager import BacklogManager

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
backlog_dir = workspace / 'backlog'
manager = BacklogManager(backlog_dir)
# Read index and compare to disk
units_on_disk = list(backlog_dir.rglob('*.md'))
print(f'On disk: {len(units_on_disk)} WU files')
"
```

Identify:
- Rows in BACKLOG.md with no matching on-disk file
- On-disk WU files missing from BACKLOG.md
- Status mismatches between BACKLOG.md and WU file headers

---

## Step 3 -- Print per-row corrections

For each drift item, print the specific correction needed:
- Missing file: `# Row <id> in BACKLOG.md has no matching file at <path>`
- Missing row: `# File <path> is not in BACKLOG.md -- add row for <id>`
- Status mismatch: `# Row <id> status is <backlog-status> but file says <file-status>`

---

## Step 4 -- Output contract (STOP)

```
VERDICT: <N> drift items found (or: BACKLOG.md is in sync)

SUGGESTED COMMAND:
  uv run devbench reconcile-backlog-md --force
  # OR apply the per-row corrections listed above manually

CONFIRM? Review the drift list above before running --force.
This skill STOPS here. No mutating verb has been executed.
```

---

## Graceful degradation

When #243 is absent, the manually-computed drift is functionally equivalent.
The skill documents which path it took and never silently skips drift items.

---

## Self-critique loop (bounded)

Use `skill_state.py` helpers. On `max_iterations` reached, escalate to the human.
Model example: `claude-opus-4-8`.
