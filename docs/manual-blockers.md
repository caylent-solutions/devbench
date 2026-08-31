# Manual External Blockers

## What this is

A "manual external blocker" is a Backlog work unit (Story + Task) whose status is intentionally `blocked` and whose description tells devbench `DO NOT CLAIM`. It exists ONLY as a dependency anchor: other in-queue Tasks have it in their `## Dependencies` table so they cannot be claimed until a human operator manually flips the blocker to `done` via `devbench set-status <id> done`.

Manual blockers are the canonical pattern for representing dependencies that devbench cannot satisfy itself, including:

- Work in flight in a different repository or by a different agent (e.g., a teammate fixing a flaky test elsewhere).
- A different backlog whose outputs the current backlog consumes (e.g., Backlog A consumes Backlog B's released terraform-modules tags).
- External-team handoffs (DNS delegations, vendor onboarding, MDM rollouts).
- Human approval gates (compliance review, security sign-off) that produce no devbench-trackable artifact.

This document covers the manual-blocker (`DO NOT CLAIM`) operator pattern. For the full taxonomy of every blocked-task class, see [block-types.md](block-types.md).

## When to use a manual blocker vs a regular dependency

| Situation | Pattern |
|---|---|
| Task A produces a file Task B reads, both in this backlog | Regular `## Dependencies` entry: `B` lists `A` as a dependency. `devbench next` skips B until A is done. |
| Task A is in this backlog; Task B is in a different backlog driven by a separate orchestrator | Manual blocker. Anchor the dependency in this backlog; another agent / manual flip clears it. |
| Task A is in this backlog; the prerequisite is another **devbench work group's branch** merging into a shared target branch | **Ancestry gate**, not a manual blocker -- this is git-verifiable, so devbench can check it itself via `devbench check-ancestry` instead of waiting on an operator. See [`cross-backlog-dependencies.md`](cross-backlog-dependencies.md#special-case-the-producer-is-another-devbench-work-groups-branch). `check-ancestry` runs both a strict ancestry probe and a squash-merged-PR probe, so a squash-merged, rebased, or fix-pack-landed dependency still passes; fall back to a manual blocker only when the producer is not a devbench-tracked branch merge at all. |
| External team owns the deliverable; devbench cannot detect completion | Manual blocker. Operator flips to `done` once they verify externally. |
| Code change in a third-party repo we can't auto-modify | Manual blocker. |

## File structure

A manual blocker comprises two files: a Story file and a Task file. Both have `## Status: blocked` and prominent `DO NOT CLAIM` text in their description. The blocker lives in `E0` (workspace bootstrap) by convention because it gates many downstream Tasks across later epics.

Suggested location: `backlog/E0/E0-F<N>/E0-F<N>-S1/E0-F<N>-S1-T1.md` plus the parent `E0-F<N>-S1/E0-F<N>-S1.md` and `E0-F<N>/E0-F<N>.md`. The Feature describes the gate; the Story restates the gate; the Task is the dep anchor that other Tasks reference.

## Canonical Story template

```markdown
# E0-F<N>-S1: <one-line description of the manual gate>

## Status: blocked

## Description

**MANUAL EXTERNAL GATE -- NOT FOR DEVBENCH.**

<one paragraph explaining what the external work is, who is doing it,
 and why devbench cannot do it itself>

This Story exists only as a dependency anchor: every leaf Task wired to
depend on `E0-F<N>-S1-T1` will skip in `devbench next` until the operator
manually clears this gate.

**To unblock**: once the external work is verified complete, the operator runs:

`​`​`bash
DEVBENCH_WORKSPACE_ROOT=/path/to/spec \
DEVBENCH_CLAUDE_MODEL=<model> \
uv run --project /path/to/devbench \
  devbench set-status E0-F<N>-S1-T1 done
uv run --project /path/to/devbench \
  devbench set-status E0-F<N>-S1 done
`​`​`

Once both are `done`, the dependent Tasks unblock automatically.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |
```

## Canonical Task template

```markdown
# E0-F<N>-S1-T1: <one-line description> -- DO NOT CLAIM

## Status: blocked

## Target Repository

- **Repo:** `<owner>/<repo>` (informational only -- devbench will NOT touch this repo via this Task)
- **Branch:** N/A (no devbench commit; the work is being done outside this backlog's scope)

## Description

**STOP. DO NOT CLAIM THIS TASK.**

This Task is intentionally `blocked` and exists ONLY as a dependency anchor for
the leaf Tasks in this backlog that depend on the external work it represents.
While the external work is in flight (or pending), this Task remains `blocked`
so devbench's `next` command will not surface any of those dependent Tasks as
claimable.

**What devbench should do**: nothing. Skip this Task. Skip every Task that has
`E0-F<N>-S1-T1` in its Dependencies list.

**What the human operator does** (after the external work is verified):

1. Verify the external deliverable exists (commands specific to the gate).
2. Manually clear this blocker:
   `​`​`bash
   DEVBENCH_WORKSPACE_ROOT=... DEVBENCH_CLAUDE_MODEL=... \
     uv run --project ... devbench set-status E0-F<N>-S1-T1 done
   uv run --project ... devbench set-status E0-F<N>-S1 done
   `​`​`
3. Re-launch devbench (or it will pick up the newly-unblocked Tasks on its
   next iteration).

### Definition of Ready

This Task is intentionally never ready. It is a manual gate.

### Approach

Not applicable. devbench should not execute this Task.

### Code Standards

Not applicable. No code is authored under this Task.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-MANUAL-001 <verifiable external state, e.g., "All 14 Backlog B
  modules are tagged on terraform-modules main">
- [ ] AC-MANUAL-002 The operator has manually flipped this Task's status from
  `blocked` to `done` via `devbench set-status`.

## Changes Manifest

| File | Change |
|------|--------|
| (none) | (no devbench-driven file changes; this Task is a manual gate only) |

## Definition of Done

- [ ] AC-MANUAL-001 .. AC-MANUAL-002 are met.
- [ ] Operator has manually flipped status to `done`.

## TDD Cycle Log

(N/A -- not a TDD task)

## Comments
```

## Wiring dependents

After the manual blocker exists in BACKLOG.md, wire each dependent Task with:

```bash
DEVBENCH_WORKSPACE_ROOT=... DEVBENCH_CLAUDE_MODEL=... \
  uv run --project ... devbench add-dep <dependent-task-id> E0-F<N>-S1-T1
```

`add-dep` writes the manual blocker into the dependent's `## Dependencies` table carrying the manual blocker's real, current title and status (not a `(auto)` / `proposed` placeholder), alongside a `[BLOCKED_PENDING_PROPOSAL]` audit marker (the marker that auto-clearing-via-proposal looks for). When `devbench report` runs, the dependent appears under "Blocked tasks (auto-clearing via proposal)" rather than "needs operator attention", because the cascade-classifier keys off the `[BLOCKED_PENDING_PROPOSAL]` marker -- not the Dependencies row's status cell -- as the signal that the blocker is resolvable.

Once the operator flips the manual blocker to `done`, every dependent that has only this blocker in its Dependencies table becomes claimable; dependents with additional in-flight blockers continue to wait on those.

## What `devbench report` shows

A manual blocker appears in panel 3 of the report ("Blocked tasks (needs operator attention)"):

1. The blocker work unit itself appears as a row.
2. Every dependent Task wired via `devbench add-dep` appears under "Blocked tasks (auto-clearing via proposal)" (not panel 3 / "needs operator attention"), because `add-dep` writes a `[BLOCKED_PENDING_PROPOSAL]` marker that the cascade-classifier treats as auto-resolvable (`src/devbench/backlog/proposal.py`, `_append_manual_dep_comment`). The annotation in that panel reads `[waiting on <blocker-id>]`.

Panel 3 is sorted deterministically by sub-case so the operator's eye lands on related items together: HOLD work units lead, then BLOCKED tasks waiting on each HOLD unit cluster directly underneath, then residual no-marker / unknown-target / all-marker-targets-terminal cases follow.

Inline annotation vocabulary:

- `[HOLD]` -- the row itself is a HOLD unit (operator put it there with `devbench hold`; cleared via `devbench unhold`).
- `[HOLD: <id>]` -- the row is a BLOCKED task whose `[BLOCKED_PENDING_PROPOSAL]` marker target is in HOLD; it cannot clear until the operator unholds the target.
- `[no marker]` -- BLOCKED with no marker and no recovery signal; operator must investigate.
- `[marker target unknown: <id>]` -- the marker points at an ID with no backlog row; cascade cannot resolve.
- `[marker targets all terminal]` -- every marker target reached `done` / `declined` but the cascade did not fire.
- `[needs review]` -- fallback annotation when no other sub-case matches; operator must inspect the task manually (`src/devbench/backlog/proposal.py`, `_panel3_annotation_impl`).

`HOLD` (status, not `blocked`) is the modern preferred mechanism for an intentional gate: it makes the operator intent explicit (set via `devbench hold`, cleared via `devbench unhold`), and the report surfaces it with `[HOLD]` rather than the diagnostic `[no marker]` / `[needs review]` annotation that BLOCKED-with-no-marker carries. The legacy "blocked + DO NOT CLAIM" pattern still works and is still surfaced under panel 3 (with `[no marker]` annotation) for backwards compatibility with existing manual blockers.

The 3-bucket split (auto-clearing via proposal / auto-recovery in flight / needs operator attention) lets operators quickly see which blocks they need to act on (panel 3) versus which ones the orchestrator's task-factory will clear on its own (panels 1 and 2).

## Anti-patterns

- **Don't put work in a manual blocker**: the Task body must NOT contain executable instructions, ACs that test code, or a Changes Manifest with file paths. If devbench accidentally claims a malformed manual blocker, the executor wastes cycles trying to satisfy ACs that have no implementation.
- **Don't reuse a manual blocker for unrelated dependents**: each gate should represent a single coherent external deliverable. If two unrelated external dependencies exist, create two manual blockers (e.g., `E0-F3-S2-T1: kanon int test fix` and `E0-F4-S1-T1: Backlog B terraform-modules gate`), wire each dependent to the right one.
- **Don't auto-flip a manual blocker from another agent**: the whole point is that a HUMAN verifies the external work and flips it. If an automated agent could verify the external state, the dependency could be expressed as a regular `## Dependencies` entry on a real (non-manual) Task.

## Authority

This document is the source of truth for the manual-blocker pattern. The pattern is observable in production at `caylent-telemetry-spec/backlog/E0/E0-F3/E0-F3-S2/` (kanon int test gate) and `caylent-telemetry-spec/backlog/E0/E0-F4/E0-F4-S1/` (Backlog B terraform-modules gate); both follow the templates above verbatim.
