# Block Types -- `BlockedTaskState` Operator Reference

## Overview

Every work unit that carries status `blocked` is classified into exactly one of
six mutually exclusive states by the `BlockedTaskState` classifier defined in
`src/devbench/backlog/proposal.py`. The classifier runs during the orchestrator's
triage sweep and drives the report panel, the `devbench list-blocked` output, and
the ADR-07 cascade-unblock logic. Understanding which state a task is in tells the
operator whether to act immediately, wait for automation, or resume a held
dependency. The six states are, in decision priority order:
`HELD`, `BLOCKED_ON_HELD`, `AUTO_CLEARING_VIA_PROPOSAL`, `AWAITING_DEPENDENCY`,
`AWAITING_AMENDMENT_RECOVERY`, and `OPERATOR_ACTION_REQUIRED`.


## Six Classes -- Summary Table

| State | Cause | Resolution hint | Operator action |
|---|---|---|---|
| `HELD` | The task's own status is `hold` -- a deliberate operator pause. | Resume via `devbench set-status <id> in-queue`. | **Required** -- must resume manually. |
| `BLOCKED_ON_HELD` | The task has a `[BLOCKED_PENDING_PROPOSAL]` marker pointing at a task whose status is `hold`. | Resume the held target task first; the cascade fires once it completes. | **Required** -- resume the held target. |
| `AUTO_CLEARING_VIA_PROPOSAL` | At least one `[BLOCKED_PENDING_PROPOSAL]` marker target is non-terminal and not `hold`. ADR-07 cascade is in flight. | Wait; the cascade will unblock this task when all marker targets reach `done`/`declined`. | None -- automation handles it. |
| `AWAITING_DEPENDENCY` | No marker present, but a regular Dependencies-table row points at a non-terminal task. | Wait for the declared dependency to complete. | None -- automation handles it. |
| `AWAITING_AMENDMENT_RECOVERY` | No marker or pending dep, but a recovery signal is on disk (pending proposal JSON, rejected-amendment archive, or a recent `[BLOCKED]` audit comment from a recovery agent). | Wait; the orchestrator's next sweep will run blocker-resolver / task-factory. | None -- check back if the state persists beyond two sweep cycles. |
| `OPERATOR_ACTION_REQUIRED` | None of the above match: no marker, no pending dep, no recovery signal. Includes manual gates (`DO NOT CLAIM`), unknown marker targets, and cascade-stuck states. | Inspect the work-unit's Comments section for the most recent `[BLOCKED]` audit row. | **Required** -- operator must investigate and unblock. |


## Per-Class Reference

### HELD

**Cause.**
The task's own status in the backlog index is `hold`. This is a deliberate
operator pause -- either the task was placed on hold via `devbench set-status
<id> hold`, or the orchestrator put it there automatically after an unrecoverable
failure and the operator has not yet acted.

**Detection.**
`classify_blocked_task` checks this first (priority 1) via `_task_status_is_hold`
(`proposal.py` lines 300-301). The backlog index is parsed; if the unit's status
column reads `hold` the function returns `HELD` immediately without examining
markers or dependencies.

**Resolution path.**
The operator resumes the task manually. No automation clears `HELD`.

**Config / env knobs.**
None -- `HELD` is a pure status-field check with no configurable parameters.

**Operator commands.**

```bash
# Inspect what put the task on hold:
devbench show <task-id>

# Release the hold and return the task to the queue:
devbench unhold <task-id> --reason "<reason for releasing the hold>"

# Alternative: resume without the unhold audit trail (less preferred):
devbench set-status <task-id> in-queue
```

**Worked example.**

```
$ devbench list-blocked
E2-F3-S1-T4  HELD  (status: hold)

$ devbench show E2-F3-S1-T4
## Status: hold
...
## Comments
[2026-04-10 14:22 UTC] [agent/orchestrator] [BLOCKED] Manual gate: do not
claim until the audit log export schema is approved.

$ devbench set-status E2-F3-S1-T4 in-queue
```


### BLOCKED_ON_HELD

**Cause.**
The task is `blocked` and its work-unit file contains at least one
`[BLOCKED_PENDING_PROPOSAL]` marker whose target task has status `hold`. The
ADR-07 cascade cannot fire while the target is non-terminal and `hold` is
non-terminal, so the blocking task stays stuck until the target is resumed.

**Detection.**
After confirming the task is not itself `HELD`, `classify_blocked_task` extracts
marker IDs via `mgr._extract_pending_proposal_markers` and calls
`_classify_with_markers` (`proposal.py` lines 368-404). Within that helper, if
any marker target's status is `hold`, the function returns `BLOCKED_ON_HELD`
(line 398).

**Resolution path.**
Resume the held target task. Once it reaches `done` or `declined`, the ADR-07
cascade fires and unblocks this task automatically.

**Config / env knobs.**
None -- `BLOCKED_ON_HELD` is a pure marker-target status check with no configurable parameters.

**Operator commands.**

```bash
# Identify the held target:
devbench show <blocked-task-id>
# Look for [BLOCKED_PENDING_PROPOSAL] markers in the Comments section.

# Release the hold on the marker target:
devbench unhold <held-target-id> --reason "<reason for releasing the hold>"

# Wire an explicit dependency if the dependency was not previously declared:
devbench add-dep <blocked-task-id> <dep-task-id> --reason "<rationale>"
```

**Worked example.**

```
$ devbench list-blocked
E2-F3-S1-T5  BLOCKED_ON_HELD  marker -> E2-F3-S1-T2 (hold)

$ devbench show E2-F3-S1-T5
...
## Comments
[BLOCKED_PENDING_PROPOSAL] E2-F3-S1-T2

$ devbench set-status E2-F3-S1-T2 in-queue
# Once E2-F3-S1-T2 reaches done/declined, T5 is unblocked automatically.
```


### AUTO_CLEARING_VIA_PROPOSAL

**Cause.**
The task has at least one `[BLOCKED_PENDING_PROPOSAL]` marker whose target is
non-terminal and not `hold`. The ADR-07 cascade is actively in flight -- the
orchestrator will unblock this task the moment every marker target reaches a
terminal state (`done` or `declined`).

**Detection.**
`_classify_with_markers` (`proposal.py` lines 368-404) iterates the marker
targets. If at least one is non-terminal and none are `hold`, it returns
`AUTO_CLEARING_VIA_PROPOSAL` (line 404).

**Resolution path.**
No operator action. The cascade fires automatically once all `[BLOCKED_PENDING_PROPOSAL]`
targets complete.

**Config / env knobs.**

| Knob | Default | Description |
|---|---|---|
| `orchestrate.max_cascade_depth` env: `DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH` | Set in `constants.py` | Maximum depth the ADR-07 cascade will recurse when unblocking chained tasks. When the cascade depth reaches this cap the sweep stops and further descendants remain blocked. |

**Operator commands.**

```bash
# Monitor progress of the blocking task:
devbench show <marker-target-id>

# Check the full cascade status for a task:
devbench list-blocked --task <blocked-task-id>

# Promote a proposed draft task to in-queue once its source proposal is accepted:
devbench promote-proposal <proposed-task-id>

# Archive a proposed draft and remove it from the backlog if it should not proceed:
devbench reject-proposal <proposed-task-id> --reason "<rationale>"
```

**Worked example.**

```
$ devbench list-blocked
E3-F1-S1-T3  AUTO_CLEARING_VIA_PROPOSAL  markers -> [E3-F1-S1-T1 (in-progress)]

# When E3-F1-S1-T1 is marked done by the orchestrator, T3 transitions to
# in-queue automatically (ADR-07 cascade).
```


### AWAITING_DEPENDENCY

**Cause.**
No `[BLOCKED_PENDING_PROPOSAL]` marker is present, but the task's
`## Dependencies` table lists one or more tasks that have not yet reached a
terminal state. The orchestrator's dependency-satisfaction check holds this task
in place until all declared dependencies complete.

**Detection.**
`classify_blocked_task` calls `_regular_deps_unsatisfied` (`proposal.py`
lines 345-365) after the marker check. This parses the backlog index and
delegates to `BacklogParser._deps_satisfied` to determine whether all declared
dep IDs are terminal. If any dep is non-terminal, the function returns
`AWAITING_DEPENDENCY` (lines 313-315).

**Resolution path.**
No operator action. The orchestrator automatically promotes this task to `in-queue`
when all dependencies complete.

**Config / env knobs.**
None -- dependency satisfaction is a deterministic terminal-status check.

**Operator commands.**

```bash
# See which dependencies are outstanding:
devbench show <task-id>
# Inspect the ## Dependencies table.

# Check each dependency's current status:
devbench show <dep-task-id>
```

**Worked example.**

```
$ devbench list-blocked
E3-F1-S2-T2  AWAITING_DEPENDENCY  deps -> [E3-F1-S1-T1 (in-queue)]

$ devbench show E3-F1-S2-T2
## Dependencies
| E3-F1-S1-T1 | Author block-types.md reference | required |

# Once E3-F1-S1-T1 reaches done, E3-F1-S2-T2 transitions to in-queue.
```


### AWAITING_AMENDMENT_RECOVERY

**Cause.**
No marker is present and no regular dependency is outstanding, but at least one
of three recovery signals is present on disk. Recovery signals indicate that the
orchestrator's blocker-resolver / task-factory loop is already working on this
task and will advance it on the next sweep cycle:

- **Signal 1** -- a pending proposal JSON exists at
  `.devbench/proposals/<task-id>.json`.
- **Signal 2** -- a rejected-amendment archive entry exists under
  `.devbench/rejected-requests/<task-id>-*.json`.
- **Signal 3** -- the work-unit file contains a `[BLOCKED]` audit comment from
  one of the canonical recovery agents within the configured recovery window,
  with a body matching the recovery-cause pattern (see the
  Recovery-Signal Heuristic Contract section below).

**Detection.**
`_classify_recovery_or_attention` (`proposal.py` lines 407-434) is called when
no marker and no pending dep exists. The three signals are checked cheapest-first
(file-presence > glob-match > timestamp-window file read). If any signal is
present, the function returns `AWAITING_AMENDMENT_RECOVERY`.

**Resolution path.**
No operator action for normal recovery. If the state persists beyond two or three
sweep cycles (the task is still `AWAITING_AMENDMENT_RECOVERY` after the
orchestrator has run multiple times), inspect `.devbench/proposals/` and
`.devbench/rejected-requests/` to see whether task-factory is stuck.

**Config / env knobs.**

| Knob | Default | Description |
|---|---|---|
| `DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS` env: `DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS` | Set in `constants.py` | Window within which a `[BLOCKED]` audit comment from a recovery agent counts as Signal 3. After this window expires, Signal 3 no longer fires and the task downgrades to `OPERATOR_ACTION_REQUIRED`. |
| `recovery_window_seconds` parameter | `None` (uses `DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS`) | Per-call override for `classify_blocked_task`. Used in tests and the report panel to control the window. |
| `manifest_amendment.enabled` | `false` | When `false`, the manifest-amender is disabled and no amendments are processed; the task cannot enter the rejected-amendment recovery loop (Signal 2 can never be triggered). Set to `true` to activate the amendment workflow. |
| `task_factory.enabled` | `false` | When `false`, the task-factory loop does not run after amendment rejects; proposal JSON (Signal 1) is never written by this path. Requires `manifest_amendment.enabled: true` to activate. |

**Operator commands.**

```bash
# Inspect pending proposals for the task:
ls .devbench/proposals/<task-id>.json

# Inspect rejected amendment archives:
ls .devbench/rejected-requests/<task-id>-*.json

# See the most recent audit comment in the work unit:
devbench show <task-id>
```

**Worked example.**

```
$ devbench list-blocked
E2-F1-S1-T7  AWAITING_AMENDMENT_RECOVERY  [recovery: pending proposal at .devbench/proposals/E2-F1-S1-T7.json]

# The blocker-resolver has already written the proposal JSON.
# On the next sweep, task-factory will materialise the draft tasks and
# the orchestrator will promote them. T7 will be unblocked once all
# proposed tasks complete.
```


### OPERATOR_ACTION_REQUIRED

**Cause.**
None of the five earlier states match: no `hold` status, no `[BLOCKED_PENDING_PROPOSAL]`
marker, no outstanding regular dependency, and no recovery signal on disk. Common
triggers include:

- A manual gate (`DO NOT CLAIM` or equivalent wording in Comments).
- A `[BLOCKED_PENDING_PROPOSAL]` marker whose target ID is not in the backlog
  index (unknown task -- the index may be stale or the marker is malformed).
- Every marker target is already terminal but the ADR-07 cascade did not fire
  (cascade stuck -- usually a bug in the orchestrator's sweep or a parallel commit
  that skipped the cascade trigger).
- A `[BLOCKED]` audit comment from a non-recovery agent, or a recovery comment
  that is older than the recovery window.

**Detection.**
`classify_blocked_task` falls through to `OPERATOR_ACTION_REQUIRED` from two
paths (`proposal.py` lines 303-305 and 434): when the task's source file cannot
be found, or when `_classify_recovery_or_attention` finds no recovery signal
(line 434). A third path fires at line 423 inside `_classify_recovery_or_attention`
when `workspace_root` is `None` (legacy callers that do not pass a workspace root
skip all recovery checks and return immediately). `_classify_with_markers` also
returns `OPERATOR_ACTION_REQUIRED` (lines 389, 396, and 402-403) when marker
targets are missing from the index or all markers are already terminal.

**Resolution path.**
The operator must act. Inspect the work-unit's Comments section for the most
recent `[BLOCKED]` audit row. Common fixes:

- Remove a stale `DO NOT CLAIM` gate comment and set the task back to `in-queue`.
- If a marker target is unknown, verify the target ID in `BACKLOG.md` and either
  correct the marker or set status to `in-queue` if the proposal was already merged
  manually.
- If the cascade is stuck (all markers terminal but task still blocked), run
  `devbench set-status <task-id> in-queue` to manually unblock.

**Config / env knobs.**
None -- `OPERATOR_ACTION_REQUIRED` is the classifier's catch-all; no configurable parameters govern whether a task lands here (the knobs in other states govern whether the task is diverted away before reaching this path).

**Operator commands.**

```bash
# Inspect the work-unit audit trail:
devbench show <task-id>

# Decline the task permanently if it will never be executed:
devbench decline <task-id> --reason "<rationale>"

# Manually unblock when the root cause is resolved:
devbench set-status <task-id> in-queue

# Wire a missing dependency if the block is a dependency that was not declared:
devbench add-dep <task-id> <dep-task-id> --reason "<rationale>"

# Re-evaluate all blocked tasks in the backlog and update their status:
devbench sync-blocked

# If the blocker is a cascade failure, also check:
grep -r "BLOCKED_PENDING_PROPOSAL" backlog/ | grep <task-id>
```

**Worked example.**

```
$ devbench list-blocked
E1-F2-S3-T9  OPERATOR_ACTION_REQUIRED

$ devbench show E1-F2-S3-T9
## Comments
[2026-04-01 09:00 UTC] [agent/executor] [BLOCKED] DO NOT CLAIM -- waiting
for legal sign-off on data-export spec. See Jira INFRA-4421.

# After sign-off is received:
$ devbench set-status E1-F2-S3-T9 in-queue
```


## Recovery-Signal Heuristic Contract

When `classify_blocked_task` reaches priority 5
(`AWAITING_AMENDMENT_RECOVERY`) with no marker and no pending dep, it relies on
three on-disk signals. Signal 3 -- the audit-comment heuristic -- is governed by
two allowlists defined at `proposal.py` lines 218-225:

### `_RECOVERY_AGENT_TAGS` (line 218)

```python
_RECOVERY_AGENT_TAGS: frozenset[str] = frozenset(
    {"agent/orchestrator", "agent/blocker_resolver", "agent/manifest_amender", "agent/backlog_manager"}
)
```

Only `[BLOCKED]` audit comments whose agent tag appears in this set are
considered recovery signals. Comments from `agent/executor`, `agent/security`,
`agent/code_review`, or any custom agent tag are explicitly excluded -- those
represent human-readable notes, not orchestrator-loop events.

**Rationale:** the four allowed agents are the only agents in the orchestrator's
automated sweep that write `[BLOCKED]` comments as a direct result of
amendment-reject / dependency / review-failure events. Broadening the allowlist
would generate false positives (e.g., an executor that logs `[BLOCKED] waiting
for external API` would incorrectly suppress operator-attention alerts).

### `_RECOVERY_BODY_RE` (line 221)

```python
_RECOVERY_BODY_RE: re.Pattern[str] = re.compile(
    r"amendment[- ]reject(?:ed)?"
    r"|out-of-scope"
    r"|ALL_REVIEWS_FAILED|REVIEW_REJECTED"
    r"|dependency .* not yet terminal|dep .* not yet terminal"
    r"|will auto-requeue when",
    re.IGNORECASE,
)
```

The comment body must match at least one of these patterns:

| Pattern | Trigger | Meaning |
|---|---|---|
| `amendment-reject` | manifest-amender archived a rejection | blocker-resolver will emit a proposal on next sweep |
| `out-of-scope` | manifest-amender rejected an out-of-scope fix | same as above |
| `ALL_REVIEWS_FAILED` | review gate: all judges returned FAIL | blocker-resolver determines next step |
| `REVIEW_REJECTED` | review judge rejected with structured feedback | blocker-resolver determines next step |
| `dependency .* not yet terminal` | orchestrator dep-check | dep is outstanding; orchestrator will re-check |
| `dep .* not yet terminal` | orchestrator dep-check (short form) | same as above |

The match is case-insensitive (`re.IGNORECASE`). A `[BLOCKED]` comment from a
recovery agent whose body does NOT match this pattern is treated as a non-recovery
block and does not count as Signal 3.

### Recovery window

Signal 3 is time-bounded. The comment's timestamp must be within
`DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS` of the current time (configured in
`constants.py`). Comments older than the window are ignored -- if the
orchestrator's blocker-resolver loop did not advance the task within the window,
the task downgrades to `OPERATOR_ACTION_REQUIRED` so the operator is alerted
rather than the task silently lingering in an assumed-recovery state.

The window can be overridden per-call via the `recovery_window_seconds` keyword
argument to `classify_blocked_task` (`proposal.py` line 274).


## Troubleshooting -- Misclassification Scenarios

The following scenarios were root-caused and fixed in the E2-F1 story. They are
documented here so operators can recognise the symptoms if a regression occurs.

### Scenario 1 -- Task stuck as `OPERATOR_ACTION_REQUIRED` when proposal JSON exists

**Symptom.** `devbench list-blocked` shows `OPERATOR_ACTION_REQUIRED` for a task
that has a valid `.devbench/proposals/<task-id>.json` on disk.

**Root cause (pre-E2-F1).** The classifier did not check Signal 1 (pending
proposal JSON) before falling through to the catch-all. All tasks without a
marker and without a pending dep landed in `OPERATOR_ACTION_REQUIRED`.

**Fix introduced.** `_classify_recovery_or_attention` now checks
`_has_pending_proposal_json` as the first recovery signal (cheapest path). If the
file exists, the classifier returns `AWAITING_AMENDMENT_RECOVERY` immediately
without reading the work-unit file.

**Verification.** If this regression occurs, check:
```bash
ls .devbench/proposals/<task-id>.json
# File should exist. If it does and the task shows OPERATOR_ACTION_REQUIRED,
# the classifier's Signal 1 path is broken.
```

### Scenario 2 -- `AWAITING_AMENDMENT_RECOVERY` persists indefinitely after proposal consumed

**Symptom.** After task-factory materialises the draft tasks (consuming the
proposal JSON), the blocked task is still shown as `AWAITING_AMENDMENT_RECOVERY`.

**Root cause (pre-E2-F1).** After task-factory consumed the proposal JSON, the
`[BLOCKED_PENDING_PROPOSAL]` marker had not yet been written back to the
work-unit file. The classifier's Signal 1 file-presence check saw a missing JSON
and should have fallen through to `OPERATOR_ACTION_REQUIRED`, but Signal 3
(the recent audit comment) was still within the window and kept the task in
`AWAITING_AMENDMENT_RECOVERY`.

**Fix introduced.** Task-factory now writes the `[BLOCKED_PENDING_PROPOSAL]`
marker immediately after materialising the draft tasks. On the next sweep the
classifier reaches the marker path (priority 2-3) before Signal 3 is evaluated,
so the state transitions correctly to `AUTO_CLEARING_VIA_PROPOSAL`.

**Verification.** If this regression occurs, check:
```bash
grep "BLOCKED_PENDING_PROPOSAL" backlog/<path-to-task>.md
# Marker should be present if task-factory has already run.
```

### Scenario 3 -- `BLOCKED_ON_HELD` when held target is already terminal

**Symptom.** `devbench list-blocked` shows `BLOCKED_ON_HELD` even though the
marker target has status `done`.

**Root cause (pre-E2-F1).** `_classify_with_markers` checked for `hold` status
before checking for terminal status, so a task whose marker target was in `hold`
and then transitioned to `done` without a re-sweep could show stale state.

**Fix introduced.** `_classify_with_markers` now marks `non_terminal_marker_found`
correctly and returns `OPERATOR_ACTION_REQUIRED` (lines 402-403) when all markers
are terminal, preventing the stale-hold classification. The cascade should have
fired if all targets are terminal -- seeing `OPERATOR_ACTION_REQUIRED` in that
case correctly alerts the operator that the cascade is stuck.

**Verification.**
```bash
devbench show <marker-target-id>
# If status shows done/declined and the blocked task is BLOCKED_ON_HELD,
# the backlog index may be stale. Run devbench validate-backlog to force a reparse.
```

### Scenario 4 -- `AWAITING_AMENDMENT_RECOVERY` from unrelated executor `[BLOCKED]` comment

**Symptom.** A task shows `AWAITING_AMENDMENT_RECOVERY` even though no proposal
JSON or rejected-amendment archive exists, and the orchestrator is not in a
recovery loop for this task.

**Root cause (pre-E2-F1).** The `_RECOVERY_AGENT_TAGS` allowlist was not applied
during Signal 3 evaluation. Any `[BLOCKED]` audit comment matching the body regex
triggered the recovery state regardless of which agent wrote it.

**Fix introduced.** `_recent_recovery_audit_comment` (`proposal.py` line 262)
now checks `if agent not in _RECOVERY_AGENT_TAGS: return False` before evaluating
the body regex. Executor and review-judge `[BLOCKED]` comments are excluded.

**Verification.**
```bash
devbench show <task-id>
# Inspect Comments. If the most recent [BLOCKED] row has agent/executor
# or a review-judge tag, it must NOT produce AWAITING_AMENDMENT_RECOVERY.
```


## Cross-References

- **`docs/backlog-contract.md`** -- canonical backlog-index schema, status field
  values (`hold`, `blocked`, `done`, `declined`, `in-queue`, `in-progress`,
  `in-review`), and the ADR-07 cascade-unblock contract.
- **`docs/manual-blockers.md`** -- how operators place tasks on hold, write manual
  gate comments (`DO NOT CLAIM`), and use `devbench set-status` to resume tasks
  stuck in `HELD` or `OPERATOR_ACTION_REQUIRED`.
- **`docs/spec-operator-attention-alerts.md`** -- the operator-attention alert
  pipeline: how `OPERATOR_ACTION_REQUIRED` tasks surface in `devbench report`,
  `devbench list-blocked`, and the watch-activity feed, and how to configure
  alert thresholds.
