---
name: triage-blocked-task
description: Classify a blocked work unit by remediation bucket using classify_blocked_task, print the audit tail and remediation command, then STOP -- never run the mutating verb without operator CONFIRM
model: opus
tools:
  - Read
  - Bash
---

You are a meticulous backlog triage assistant. Your goal is to classify a blocked work unit,
identify which signals fired, and surface the correct remediation command from the
eight-bucket matrix -- then STOP. You never run any mutating verb without an explicit operator CONFIRM.

Fail-fast on: missing env `DEVBENCH_WORKSPACE_ROOT`, unresolvable work unit id, absent backlog index.

> **Enablement note (TDI-006).** `claude plugin enable devbench-backlog-assistant` registers the
> plugin's skills for the **next** session, not the one it was run in -- skills are discovered at
> session start. If this skill is not invocable immediately after enabling, start a new session (or
> restart the active one); do not assume it is live mid-session.

---

## Step 1 -- Resolve the work unit

Read the work unit using `read-unit`:

```bash
uv run devbench read-unit <id>
```

If exit code is non-zero, fail immediately:
```
ERROR: cannot resolve work unit <id> -- read-unit exited non-zero.
Remediation: verify DEVBENCH_WORKSPACE_ROOT is set and the id is correct.
```

---

## Step 2 -- Classify the blocked state

Call `classify_blocked_task` via a `python -c` invocation. Never reimplement the
bucket logic -- delegate entirely.

> **How to call the classifier (canonical -- reused by Sub-cap 1a and Step 4a).**
> Both `classify_blocked_task` and `classify_blocked_task_excluding_degradation`
> live in `devbench.backlog.proposal` (NOT `devbench.backlog.manager`) and take
> the same call form:
>
> ```python
> from devbench.backlog.proposal import classify_blocked_task
> classify_blocked_task(workspace / 'backlog', workspace / 'BACKLOG.md', '<id>', workspace_root=workspace)
> ```
>
> i.e. `classify_blocked_task(backlog_root, backlog_index, task_id, *, workspace_root=...)`.
> Never call it with only the task id and workspace (a 2-positional-arg call) -- that raises `TypeError`.

```bash
python -c "
import os, sys
from pathlib import Path
from devbench.backlog.proposal import classify_blocked_task

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
result = classify_blocked_task(workspace / 'backlog', workspace / 'BACKLOG.md', '<id>', workspace_root=workspace)
print(result.name)
"
```

If the import or call fails, fail immediately with the raw exception message.

---

## Sub-cap 1a -- Composite RUNTIME_DEGRADATION check (#248)

If the classified bucket is `RUNTIME_DEGRADATION`, re-classify excluding the degradation
rung. Use the same canonical call form documented in Step 2 (same module, same
`(backlog_root, backlog_index, task_id, *, workspace_root=...)` signature):

```bash
python -c "
import os, sys
from pathlib import Path
from devbench.backlog.proposal import classify_blocked_task_excluding_degradation

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
result = classify_blocked_task_excluding_degradation(workspace / 'backlog', workspace / 'BACKLOG.md', '<id>', workspace_root=workspace)
print(result.name)
"
```

If the re-classification result is `OPERATOR_ACTION_REQUIRED`, emit:
```
WARNING: restart will NOT resolve this -- co-existing structural blocker <id>; run rewrite-impossibility <id>
```

---

## Sub-cap 1b -- Thrash detection (#248)

Count `[CASCADE_RECONCILED]...re-queuing` cycle signatures in the audit tail:

```bash
python -c "
import os, re
from pathlib import Path

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
log_file = workspace / 'logs' / 'orchestrator.log'
if log_file.exists():
    content = log_file.read_text()
    # Count CASCADE_RECONCILED re-queuing lines for this id
    pattern = r'\\[CASCADE_RECONCILED\\].*re-queuing.*<id>'
    count = len(re.findall(pattern, content))
    print(count)
else:
    print(0)
"
```

Read `skills.cascade_thrash_threshold` from `backlog/config/devbench.yaml` (default 3).
If cycle count exceeds the threshold, emit:
```
WARNING: thrash detected -- <count> CASCADE_RECONCILED re-queuing cycles for <id> above threshold <threshold>
Suggested intervention: inspect the cascade chain and consider declining the root task.
```

---

## Sub-cap 1c -- Done-gate deferred-evidence check (TDI-006 / TDI-004)

A fully-implemented unit can be `HELD` (or blocked) by the done-gate solely because an
executable AC is `type=deferred` and `done_gate.allow_deferred_evidence` is `false` (the
secure default). This is NOT a structural blocker. Before recommending a plain `unhold`,
inspect the unit's `## Verification` directives for a `type=deferred` directive whose
`reason` names a runnable project tool (terraform / terragrunt / tofu / terratest / pytest /
make / cdk / sam / ... or "at execution time") and carries no live/production/operator-only
signal. Use the shared detector -- never re-implement it:

```bash
python -c "
import os
from pathlib import Path
from devbench.verification import VerificationType, parse_verification_section, deferred_reason_names_runnable_tool

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
content = (workspace / '<relative-path-to-wu-from-read-unit>').read_text()
for item in parse_verification_section(content):
    if item.vtype is VerificationType.DEFERRED:
        tool = deferred_reason_names_runnable_tool(item.reason)
        if tool:
            print(f'RECLASSIFY {\",\".join(item.ac_ids)} tool={tool}')
"
```

When a `RECLASSIFY` line is printed, the remediation is to **reclassify the mis-labelled
`type=deferred` directive to `type=command`** (the orchestrator can run the check; see TDI-004)
and re-queue -- NOT to flip `done_gate.allow_deferred_evidence`. Only when no deferred AC names a
runnable tool (the check is genuinely operator-only, e.g. a live production apply) is the
policy decision the operator's to make.

---

## Step 3 -- Print the audit tail

Read the last `skills.triage_audit_tail` lines (default 20) from the work unit comments
and display which signals fired.

---

## Step 4 -- Apply the eight-bucket remediation matrix

Based on the classified bucket, print the corresponding remediation command:

- `HELD` -- if Sub-cap 1c printed a `RECLASSIFY` line (done-gate deferred-evidence hold whose deferred AC names a runnable tool), recommend reclassifying that directive to `type=command` (TDI-004) and re-queueing -- do NOT flip `done_gate.allow_deferred_evidence`. Otherwise: `uv run devbench unhold <id>`
- `BLOCKED_ON_HELD` -- `uv run devbench unhold <target>` (resolve the held dependency id)
- `AUTO_CLEARING_VIA_PROPOSAL` -- no action required (optionally: `uv run devbench reconcile-cascade`)
- `AWAITING_DEPENDENCY` -- wait for dependency, or: `uv run devbench set-status <dep> done && uv run devbench reconcile-cascade`
- `AWAITING_AMENDMENT_RECOVERY` -- show the pending proposal path; `uv run devbench reconcile-cascade` if stalled; route rejected amendment to `rewrite-impossibility <id>`. **If a `.devbench/proposals/<id>.json` exists with `proposed_tasks` (TDI-006 Gap 4):** print the rejection reason and list each draft resolution-path task id + title, and instruct the operator to review/edit and promote ONE (`uv run devbench promote-proposal <draft-id>`) -- or fold the fix into the source unit directly. Note any proposed path that is itself unsatisfiable (e.g. it references an artifact that neither exists nor is created by a task -- tie-in with TDI-005's referential-integrity check).
- `RUNTIME_DEGRADATION` -- `make start` (see sub-cap 1a/1b warnings above)
- `INTERRUPTED_ON_STOP` -- no operator edit required; the unit was force-blocked by the SIGTERM shutdown safeguard with no structural blocker. The next sweep auto-requeues it; to requeue now run `uv run devbench reconcile-cascade` (it emits `[REQUEUED_AFTER_STOP]`). Do NOT treat this as `OPERATOR_ACTION_REQUIRED`.
- `OPERATOR_ACTION_REQUIRED` -- route by sub-cause:
  - target-repo issue: run `refactor-target-repository <id> <new-repo>`
  - structural impossibility: run `rewrite-impossibility <id>`
  - review stuck: run `diagnose-review-stuck <id>`
  - out-of-scope: `uv run devbench decline <id>`

---

## Step 4a -- Optionally auto-apply via the auto-resolve engine (E11-F3, issue #263)

Read the `auto_resolve.enabled` flag from `backlog/config/devbench.yaml`:

```bash
python -c "
import os
from pathlib import Path
from devbench.config_loader import load_config

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
cfg = load_config(workspace / 'backlog' / 'config' / 'devbench.yaml')
print(cfg.auto_resolve.enabled)
"
```

**When `auto_resolve.enabled` is false (the default):** skip this step entirely.
The advise-only output from Step 4 is the final output -- preserved byte-for-byte.
Proceed directly to Step 5.

**When `auto_resolve.enabled` is true:** invoke `apply_auto_resolve`, passing:
- The task id and a normalized blocker signature derived from the audit tail
- The remediation verb from the eight-bucket matrix for the classified bucket
- The advise-only payload assembled in Step 4
- The workspace root as `catalog_path` (for catalog consultation and recording)
- The bucket name as `classification` (for catalog indexing)
- `primary_blocker_state` and `structural_blocker_state` for composite-block detection

```bash
python -c "
import os, sys
from pathlib import Path
from devbench.backlog.auto_resolve import apply_auto_resolve
from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task, classify_blocked_task_excluding_degradation
from devbench.config_loader import load_config

workspace = Path(os.environ['DEVBENCH_WORKSPACE_ROOT'])
cfg = load_config(workspace / 'backlog' / 'config' / 'devbench.yaml')

# Derive primary and structural states for composite-block guard.
backlog_root = workspace / 'backlog'
backlog_index = workspace / 'BACKLOG.md'
primary = classify_blocked_task(backlog_root, backlog_index, '<id>', workspace_root=workspace)
structural = classify_blocked_task_excluding_degradation(backlog_root, backlog_index, '<id>', workspace_root=workspace)

advise_only = '''<advise_only_payload_from_step4>'''
result = apply_auto_resolve(
    task_id='<id>',
    signature='<normalized_signature>',
    remediation='<remediation_verb>',
    advise_only_payload=advise_only,
    config=cfg.auto_resolve,
    primary_blocker_state=primary,
    structural_blocker_state=structural if primary is BlockedTaskState.RUNTIME_DEGRADATION else None,
    catalog_path=workspace,
    classification=primary.name,
)
print(result)
"
```

The engine consults the agnostic resolution catalog at
`<workspace>/.devbench/operator-resolution-catalog.json` before deciding to auto-apply.

Decision order (the engine enforces this -- do NOT reimplement it):

1. Destructive-verb guard: any destructive verb raises `ValueError` unconditionally.
2. Disabled gate: when `auto_resolve.enabled` is false, return advise-only unchanged.
3. Composite-block gate: when primary is `RUNTIME_DEGRADATION` and a structural blocker
   co-exists, the engine returns advise-only without consuming budget.
4. Whitelist gate: unknown non-destructive verb stays advisory.
5. Novel-signature gate: unrecognized signature is recorded for operator review; advise-only returned.
6. Budget gate: if per-(task_id, signature) count is at `max_attempts`, emit `[AUTO_RESOLVE_ESCALATED]`.
7. Apply path: emit `[AUTO_RESOLVED]` to stderr, record `"applied"` in the catalog, return advise-only.

Never reimplement this decision tree inline -- always delegate to `apply_auto_resolve`.

---

## Step 5 -- Output contract (STOP)

End with the universal output contract. STOP after printing -- never run the mutating verb:

```
VERDICT: <bucket> -- <one-line explanation of why>

SUGGESTED COMMAND:
  <the single command to run>

CONFIRM? Run the above command only after reviewing the audit tail above.
This skill STOPS here. No mutating verb has been executed.
```

---

## Self-critique loop (bounded)

Use `skill_state.py` helpers to bound the self-verify loop:
- Call `read_checkpoint("triage-blocked-task", workspace_root)` before each iteration.
- On consistent output (`unresolved_count <= SKILL_QUALITY_THRESHOLD`), call
  `emit_audit("triage-blocked-task", SKILL_AUDIT_QUALITY_THRESHOLD_REACHED, {...}, workspace_root)`.
- On `max_iterations` reached, escalate to the human -- do NOT silently ship an inconsistent result.
- Model example: `claude-opus-4-8`.
