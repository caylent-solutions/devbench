# ADR-03: Task factory (proposed work units from amendment rejects)

**Status:** Accepted
**Date:** 2026-04-18

---

## Context

The manifest-amender workflow ([ADR-02](02-manifest-amendment-workflow.md)) handles TDD GREEN production fixes that belong inside the source task's scope. But during live operation it also surfaces a second, genuinely different shape of failure: the amender correctly rejects an amendment because the changes are legitimate production fixes that fall OUTSIDE the source task's Approach. These are not scope-creep bugs; they are real bugs that need to be fixed, but they belong in new work units of their own.

The handling pattern before this ADR was:

1. Amender rejects on Approach authorisation.
2. Source task blocks with a comprehensive rationale.
3. The orchestrator surfaces the blocker to the operator.
4. The operator manually authors N new work unit `.md` files, wires dependencies, updates `BACKLOG.md`, re-validates.
5. The operator re-runs the orchestrator.

Steps 4--5 were the pain point. In one 2026-04-17 session, five blocked tasks in a single backlog required this manual decomposition: an hour of hand-typing each time, plus the risk of miswiring dependencies or forgetting to update the Status Summary. The orchestrator was idle during the whole window.

## Decision

Add a two-agent, two-CLI-command workflow that generates draft work-unit `.md` files after a legitimate amendment rejection:

- **`devbench:blocker-resolver`** (extended): reads the rejected-requests archive left by `reject-amendment`, diagnoses which out-of-scope fixes need their own work units, and emits a structured proposal JSON via `uv run devbench write-proposal <source-id>`.
- **`devbench:task-factory`** (new): reads the proposal JSON, calls `uv run devbench materialise-proposal <source-id>` which generates one draft `.md` per proposed task with `## Status: proposed` and inserts a matching row in `BACKLOG.md`.

Drafts are inert. `proposed` is a new `WorkUnitStatus` value; `devbench next` only considers `in-queue` and `in-progress` tasks, so the generator cannot silently poison the actionable queue. The operator promotes drafts via `devbench promote-proposal <id>` (flips to `in-queue`, wires the source task's dependency automatically) or rejects them via `devbench reject-proposal <id> --reason "..."` (archives the draft, removes the row, audits on the source task). The source task unblocks when every promoted dependency completes.

The feature is opt-in per backlog via `task_factory.enabled: true`, which requires `manifest_amendment.enabled: true` (because task-factory runs from the amendment-reject path).

## Consequences

**Positive.**

- The "operator must hand-author N new work units" step is substantially reduced. Drafts are generated programmatically; the operator reviews and promotes rather than authors.
- No orchestrator idle time. The amender reject -> blocker-resolver -> task-factory -> orchestrator-continues chain runs without a human in the loop up to the point of promotion. The operator reviews when they choose to.
- Concurrency-safe ID allocation on day one. `allocate_next_ids` acquires an exclusive POSIX file lock before scanning the Story directory; two parallel factory runs cannot collide.
- Deterministic state: proposals are persisted on disk (`<workspace>/.devbench/proposals/<source-id>.json`); losing the terminal or stopping the orchestrator mid-flow leaves the workspace recoverable.
- Separation of concerns: blocker-resolver diagnoses, task-factory generates, CLI validates + writes, operator approves, orchestrator executes. Every component has one job.
- Preserves `AC-FINAL-015`. Generated work units carry pre-declared Changes Manifests populated by blocker-resolver, so promoted tasks start clean with no amendment needed during their own TDD cycle.

**Negative.**

- Two new agents (`blocker-resolver` extended with a proposal-emission path, `task-factory` new) + two new CLI commands (`materialise-proposal`, `write-proposal`) + three existing-command extensions (`list-proposals`, `promote-proposal`, `reject-proposal`). Added surface area.
- A `reject-amendment` call now archives instead of deletes the pending request so blocker-resolver can read it after rejection. Minor disk growth; `reject-amendment` documents the archive path.
- The `PROPOSED` status adds a code path to every status-aware piece of code (parser, manager, constants, display lists). Every touch site has been updated; coverage is enforced at 100% on `devbench.backlog.proposal`.

## Alternatives considered

- **Always regenerate on re-block.** Rejected: produces duplicate proposals when a task blocks multiple times on the same root cause. The chosen "skip generation if unresolved proposals already exist" is simpler and forces the operator to engage with pending drafts before more appear.
- **Merge new proposals into the existing proposal file.** Rejected: the LLM's output varies run-to-run; reliably merging structured JSON from two different LLM invocations is fragile. Skip-if-unresolved is deterministic.
- **Hard-delete rejected proposals.** Rejected: git history doesn't help when the `.md` was never committed. Archiving preserves a recovery path at trivial disk cost.
- **Sub-IDs (`T4.1`, `T4.2`).** Rejected: requires changes across parser.py, `_ID_SEGMENT_TYPE`, `BACKLOG_INDEX_TABLE_ROW_RE`, status-summary counting, and every string that parses compound IDs. Large surface, minimal benefit. The chosen scheme -- next sequential within the Story -- matches existing ID conventions.
- **New-Story approach.** Rejected: adds structural noise and makes dependency wiring awkward. The proposed tasks belong logically in the same Story as the source; a separate Story for "unblockers of X" is noise.

## Related files

- Source: `src/devbench/backlog/proposal.py`, `src/devbench/config_loader.py` (`TaskFactoryConfig`), `src/devbench/constants.py` (`STATUS_PROPOSED`), `src/devbench/backlog/work_unit.py` (`WorkUnitStatus.PROPOSED`), `src/devbench/backlog/parser.py` (`_RAW_STATUS_TO_ENUM`), `src/devbench/cli.py` (`cmd_materialise_proposal`, `cmd_write_proposal`, `cmd_list_proposals`, `cmd_promote_proposal`, `cmd_reject_proposal`).
- Agents: `plugin/devbench/agents/blocker-resolver.md`, `plugin/devbench/agents/task-factory.md`.
- Orchestrator: `plugin/devbench/skills/orchestrate/SKILL.md` (step 4c).
- Docs: [docs/task-factory.md](../task-factory.md), [docs/faq.md](../faq.md).
- Tests: `tests/test_backlog/test_proposal.py`, `tests/test_integration/test_task_factory_lifecycle.py`.
