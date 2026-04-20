# ADR-07: Marker-based auto-requeue when promoted proposals complete

**Status:** Accepted
**Date:** 2026-04-19

---

## Context

ADR-03 (task-factory) shipped the amendment-reject -> proposal -> promotion flow: when a manifest amendment is rejected because the fix belongs in its own work unit, `blocker-resolver` decomposes the rejection and `task-factory` materialises draft work units the operator promotes. `devbench promote-proposal <draft>` wires each promoted draft as a dependency of the source task and writes a `[PROPOSAL_PROMOTED]` audit comment.

One loop step was missing: when every promoted draft eventually transitions to `done`, nothing unwinds the wiring. The source task stays `blocked` forever. The operator has to remember they promoted drafts, watch for them to complete, and manually run `devbench set-status <source> in-queue` to put the source back on the actionable queue.

Two independent incidents in the 48 hours before this ADR bumped into the gap:

- The 2026-04-17 `ba12957` pollution incident left `E1-F1-S11-T1` blocked pending promoted drafts `T3` / `T4`. The docs (`docs/task-factory.md`) already described "Source task auto-unblocks when every promoted dependency completes" as an existing behaviour. It wasn't.
- The 2026-04-19 halt-language incident surfaced a parallel mental model: the orchestration loop should be a pure function of on-disk state; state that the operator has to remember is state that will eventually go wrong.

A cleanly-scoped reactive cascade that fires from the same `_set_status` seam that drives parent-rollup closes the gap without touching any of the existing semantics.

## Decision

When `promote-proposal` wires a draft as a dependency of the source (the default path; `--no-dep-on-source` opts out), the source's Comments section gets a structured marker:

```
[BLOCKED_PENDING_PROPOSAL] <draft-task-id>
```

One marker comment per promoted draft -- embedded on the same audit line as the existing `[PROPOSAL_PROMOTED]` entry to keep the comment history compact.

`BacklogManager._set_status` -- the workhorse every status write goes through -- fires a new sideways cascade called `_auto_requeue_marker_dependents` immediately before the existing `_rollup_parent_status` call when a task transitions to `done`. The sideways scan:

1. Iterates every backlog row (not just task-factory drafts).
2. For each `blocked` candidate whose declared dependencies include the just-completed task, extracts all `[BLOCKED_PENDING_PROPOSAL]` markers from its Comments section.
3. If every marker ID is terminal (`done` or `declined`), flips the candidate from `blocked` to `in-queue` via `force_status` and writes an `[AUTO_UNBLOCKED]` audit comment naming every marker ID.

Blocks without markers are untouched. Blocks whose markers include at least one non-terminal (or unknown / rejected) ID are untouched. Candidates that are not `blocked` are untouched. Parent rollup runs AFTER the sideways cascade so newly-unblocked children are visible as non-terminal when the parent is evaluated, preserving the rollup invariant (a parent only rolls to `done` when every child is terminal).

## Consequences

**Positive.**

- The gap the operator previously filled manually is now closed. Source tasks re-enter the actionable queue automatically when every promoted dep finishes; no `set-status` intervention required.
- Narrow trigger. Only blocks caused by a promoted proposal chain are eligible. Review-fail blocks, git-ops-failure blocks, executor-retry-exhausted blocks, operator-set blocks, and every other non-promotion cause stay `blocked` because they carry no marker.
- Symmetric to parent rollup. Same reactive shape, same `_set_status` seam, same file-driven discrimination. A maintainer who understands the upward cascade can reason about the sideways cascade without learning new concepts.
- No new status. `blocked` -> `in-queue` is an existing transition; the cascade just schedules it automatically.
- No CLI change. `promote-proposal` keeps its existing surface; the marker write is additive. `mark-done` keeps its existing signature; the cascade fires from inside `_set_status` transparently.
- Full audit trail. `[PROPOSAL_PROMOTED]`, `[BLOCKED_PENDING_PROPOSAL]`, and `[AUTO_UNBLOCKED]` are all written to the source task's Comments section and visible in `devbench report` output.
- Conservative on edge cases. Unknown IDs in markers (e.g. drafts later rejected and removed from the index) count as non-terminal, so a stray rejected-proposal marker can never trigger a spurious auto-requeue.

**Negative.**

- The cascade adds one extra backlog-parse per `mark_done` call. Scan is O(rows × blocked × markers) -- at realistic backlog sizes (hundreds of rows, single-digit blocked, single-digit markers per blocked) this is bounded well under a second, but it's not free.
- One more marker convention for contributors to remember. The `[BLOCKED_PENDING_PROPOSAL]` format joins `[PROPOSAL_PROMOTED]`, `[PROPOSAL_REJECTED]`, `[COMMIT_DEFERRED]`, and `[REVIEW_PASS]` in the structured-comment vocabulary.
- The cascade is invisible when it doesn't fire. A contributor debugging a still-blocked task has to know that the absence of a marker is the reason the scan skipped it -- the debugging narrative lives in the FAQ and task-factory doc, not in the code.

## Alternatives considered

- **Generic "all deps done -> auto-unblock" (no marker).** Rejected. Conflates two different reasons for a task to be blocked. A task blocked by "reviewer said this will never ship" has no missing deps to wait on; auto-flipping it to `in-queue` would be wrong. Without a marker you cannot discriminate. The false-positive risk outweighs the ergonomic win.
- **A new `blocked-pending-deps` status value.** Rejected. Introduces surface area across every status-aware code path (parser enum, manager transitions, constants, report columns, agent prompts, SKILL logic). The declined-status landing (ADR-05) is already evidence of how much a new status touches. Not worth it for what is fundamentally a scoped marker on an existing status.
- **Extend task-factory to fire on generic blocks.** Rejected (at least in this ADR). Generic blocks have extremely varied root causes: CI flakes, merge conflicts, operator disputes, spec rewrites. Asking an LLM to decompose every kind of block into proposal drafts is ambitious and error-prone. The current narrow triggers (amendment-reject and validation-gate escalation) are what make task-factory's output trustworthy.
- **Store the wiring as an out-of-band ledger file rather than as comments.** Rejected. The backlog is already the source of truth for task state; adding a parallel `.devbench/auto-requeue-ledger.json` duplicates persistence, needs lockfile discipline, and complicates recovery. Comments already work.

## Related files

- **Source.** `src/devbench/backlog/manager.py` (`_auto_requeue_marker_dependents`, `_extract_pending_proposal_markers`, `_parse_candidate_dependencies`, `_BLOCKED_PENDING_PROPOSAL_RE`). `src/devbench/backlog/proposal.py` (`_append_promote_comment` -- extended to embed the marker).
- **Tests.** `tests/test_backlog/test_manager.py::TestExtractPendingProposalMarkers` + `TestAutoRequeueMarkerDependents` + `TestParseCandidateDependencies`. `tests/test_backlog/test_proposal.py::TestPromoteProposal` (new marker-write cases). `tests/test_integration/test_task_factory_lifecycle.py::TestTaskFactoryLifecycleHappyPath::test_source_auto_requeues_when_all_promoted_deps_complete`.
- **Docs.** `docs/task-factory.md` (new "Auto-requeue on proposal completion" section), `docs/faq.md` (new entry), `docs/architecture.md` (capabilities bullet).
- **Coverage gate.** `Makefile::test-coverage-new` now includes `devbench.backlog.manager` to pin the cascade at 100%.
- **Related ADRs.** [ADR-03: Task factory](03-task-factory.md), [ADR-05: Declined status](05-declined-status.md), [ADR-06: Validation-gate bug escalation](06-validation-gate-bug-escalation.md).
