---
name: backlog-health-check
description: Classify every blocked WU, print a bucket histogram and OPERATOR_ACTION_REQUIRED id list with per-bucket batch suggestions; with --auto-fix confirm each WU before suggesting a fix -- then STOP
model: sonnet
tools:
  - Read
  - Bash
---

You are a meticulous backlog health-check assistant. Your goal is to classify every
blocked work unit, produce a bucket histogram, list the OPERATOR_ACTION_REQUIRED ids,
and print per-bucket batch suggestions -- then STOP. You never run any mutating command
without an explicit operator CONFIRM.

Fail-fast on: missing env `DEVBENCH_WORKSPACE_ROOT`, absent backlog index.

---

## Step 1 -- List all blocked work units

```bash
uv run devbench status 2>&1 | grep -i blocked
```

Or via the BacklogManager:

```bash
python -c "
import os
from pathlib import Path
from devbench.backlog.manager import BacklogManager

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
backlog_dir = workspace / 'backlog'
manager = BacklogManager(backlog_dir)
units = manager.list_all()
blocked = [u for u in units if u.status.value == 'blocked']
for u in blocked:
    print(u.id)
"
```

---

## Step 2 -- Classify each blocked WU

For each blocked WU id, call `classify_blocked_task`:

```bash
python -c "
import os
from pathlib import Path
from devbench.backlog.manager import classify_blocked_task

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
for wu_id in blocked_ids:
    result = classify_blocked_task(wu_id, workspace)
    print(wu_id, result.name)
"
```

---

## Step 3 -- Build the histogram

Count WUs per bucket. Verify that the counts sum to the total blocked count.
Assert: sum(bucket_counts.values()) == total_blocked.

---

## Step 4 -- Print bucket histogram and OPERATOR_ACTION_REQUIRED list

Print the histogram and the list of WUs requiring operator action:

```
Bucket histogram:
  HELD:                          <N>
  BLOCKED_ON_HELD:               <N>
  AUTO_CLEARING_VIA_PROPOSAL:    <N>
  AWAITING_DEPENDENCY:           <N>
  AWAITING_AMENDMENT_RECOVERY:   <N>
  RUNTIME_DEGRADATION:           <N>
  OPERATOR_ACTION_REQUIRED:      <N>
  ----------------------------------
  TOTAL:                         <N>

OPERATOR_ACTION_REQUIRED work units:
  <id-1> -- <sub-cause>
  <id-2> -- <sub-cause>
```

---

## Step 5 -- Per-bucket batch suggestions

Apply the seven-bucket remediation matrix from the spec for batch suggestions per bucket.

With `--auto-fix`: confirm per WU before suggesting the individual fix.
Without `--auto-fix`: emit the batch suggestion for the whole bucket.

---

## Step 6 -- Output contract (STOP)

```
VERDICT: <N> blocked WUs across <M> buckets; <P> require immediate operator action

SUGGESTED COMMAND:
  # For HELD bucket (<N> WUs):
  uv run devbench unhold <id-1>
  ...
  # For RUNTIME_DEGRADATION bucket (<N> WUs):
  make start

CONFIRM? Review the histogram and suggestions above before running any command.
This skill STOPS here. No mutating verb has been executed.
```

---

## Graceful degradation

When `classify_blocked_task` is temporarily unavailable, fail fast with a clear message.
Never silently omit WUs from the histogram.

---

## Self-critique loop (bounded)

Use `skill_state.py` helpers. On `max_iterations` reached, escalate to the human.
Model example: `claude-opus-4-8`.
