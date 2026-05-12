# ADR-05: `declined` work-unit status

**Status:** Accepted
**Date:** 2026-04-18

---

## Context

Work units move through a small lifecycle: `in-queue` → `in-progress` → `in-review` → `done`, with `blocked` available when progress is waiting on external resolution and `proposed` (introduced by the task-factory feature, [ADR-03](03-task-factory.md)) for drafts not yet promoted into the active queue.

A live operational gap has surfaced repeatedly: a backlog author, reviewer, or operator sometimes concludes that a specific work unit **will never be done** -- the scope was rewritten and the task is now out of scope, a different task delivered the same outcome, or the risk / cost / ROI no longer justifies the work. None of the existing statuses express this cleanly:

- `done` means completed -- misleading, because the work did not happen.
- `blocked` means waiting on something -- misleading, because nothing is going to unblock it.
- `proposed` means draft awaiting promotion -- misleading, because the unit was already in the actionable queue before the decision.

Without a dedicated status, operators were abusing `blocked` (pollutes the blocker panel with items that shouldn't appear there), setting `done` on work that never ran (pollutes the completion rate), or deleting work-unit files outright (loses the audit trail).

## Decision

Add a new `declined` status to `WorkUnitStatus`. Semantics:

- **`declined`** = the operator has decided this work will never be done. Terminal state; no further automation runs against the unit.
- **Rollup behaviour:** children in `declined` count as "complete" for parent (Story/Feature/Epic) auto-rollup. A parent rolls to `done` once every child is either `done` or `declined`. The rationale is symmetric: both statuses represent resolved-and-closed work; neither will progress further.
- **Orchestrator behaviour:** `declined` tasks are never in the actionable set. `BacklogParser.get_parallel_candidates` already filters to `in-queue` / `in-progress`, so this is automatic.
- **Reporting:** the consolidated progress table in `devbench report` gains a `Tasks declined` row in the BACKLOG STATE section, populated only in the All-time column (it's an instantaneous snapshot, not a windowed metric). A dedicated `Declined (N):` panel appears near the bottom of the report, mirroring the existing `Proposed (N):` panel, so the audit list is glanceable. `Tasks remaining (total)` and projection ETAs exclude declined tasks so the orchestrator pace isn't distorted by work that will never execute.
- **BACKLOG.md Status Summary:** a new `Declined` column joins Done / In Progress / In Queue / Blocked. `_parse_summary_table` accepts both the legacy 4-count and the new 5-count row shapes so existing backlogs validate cleanly until regenerated.

A dedicated CLI command is added:

```bash
uv run devbench decline <id> --reason "<message>"
```

`--reason` is required -- the decision must leave an audit trail. The command calls `BacklogManager.mark_declined`, which flips the status in both `BACKLOG.md` and the work-unit `.md`, then appends a `[DECLINED] <reason>` comment. Em-dashes are rejected at the input boundary for backlog hygiene (same gate `reject-amendment` / `reject-proposal` use).

`devbench set-status <id> declined` continues to work as a general-purpose recovery escape hatch, but the dedicated `decline` command is the preferred path because it enforces the reason.

## Consequences

**Positive.**

- A precise, auditable status for "not doing this." No more abuse of `blocked` or `done`; audit trail is preserved via the `[DECLINED]` comment rather than file deletion.
- Parent rollup works correctly without operator intervention: a Story with one declined child + every other child done rolls to `done` cleanly.
- Report signal stays clean: declined tasks are visible but never inflate completion percentages or delay ETAs.
- Universal: the status is not opt-in. Every backlog can use it immediately; no config flag.

**Negative.**

- The BACKLOG.md Status Summary table grew one column. Consumers that hand-wrote parsers against the old 4-count shape need to accept 5 counts. The code-path parser already handles both.
- `WorkUnitStatus` now has 8 values. Every touch site (`_RAW_STATUS_TO_ENUM`, `VALID_STATUSES`, `DISPLAY_STATUS_VALUES`, `TABLE_STATUS_VALUES`, tests that assert on the full set) had to be updated -- mechanical change, but non-zero surface area.

## Alternatives considered

- **Reuse `blocked`.** Rejected: `blocked` implies waiting-on-something, and the stop-hook circuit breaker has specific behaviour around blocked tasks that shouldn't apply to permanently-declined ones.
- **`wontfix`.** Rejected: carries GitHub-issue-tracker baggage (usually means "this is a bug we won't fix," narrower than the spec-decision case here).
- **`cancelled`.** Rejected: implies work was interrupted; the decision here is usually proactive scope-management.
- **`obsolete`.** Rejected: narrower than `declined` -- fits the "no longer relevant" subcase but not the "risk/ROI not worth it" subcase.
- **No dedicated command; rely on `set-status <id> declined`.** Rejected: the decision must leave a reason in the audit trail. A bare `set-status` doesn't capture that. `decline` mirrors `mark-blocked`'s existing reason-captured pattern.

## Related files

- Source: `src/devbench/backlog/work_unit.py` (enum), `src/devbench/backlog/parser.py` (`_RAW_STATUS_TO_ENUM`), `src/devbench/backlog/manager.py` (`mark_declined`, `_all_children_done`, `_compute_epic_counts`, `_check_status_summary`, `_parse_summary_table`), `src/devbench/constants.py` (`STATUS_DECLINED`, `STATUS_SUMMARY_TABLE_HEADER`, `VALID_STATUSES`, `DISPLAY_STATUS_VALUES`, `TABLE_STATUS_VALUES`), `src/devbench/cli.py` (`cmd_decline`), `src/devbench/reporting/report.py` (`_BacklogTotals.tasks_declined`, `_declined_listing`).
- Tests: `tests/test_constants.py`, `tests/test_backlog/test_work_unit.py`, `tests/test_backlog/test_manager.py::test_rollup_treats_declined_children_as_complete`, `tests/test_cli.py::TestCmdDecline`, `tests/test_reporting/test_report.py` (declined listing + totals tests).
- Docs: `docs/architecture.md`, `docs/faq.md`, `docs/example-work-unit-template.md`, `docs/creating-specs-and-backlogs.md`.
