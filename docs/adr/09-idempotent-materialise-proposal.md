# ADR-09: Idempotent classify-aware materialise_proposal

**Status:** Accepted
**Date:** 2026-04-19

---

## Context

ADR-08 shipped the proposal-lifecycle observability work: `ProposalTaskState`, `classify_proposed_task`, per-task state labels, un-materialised reject, sweep-proposals, and cascade-on-reject. Every lifecycle read in the CLI now routes through the classifier, which is authoritative for the six states (`UNMATERIALISED`, `PROPOSED`, `PROMOTED`, `DONE`, `DECLINED`, `REJECTED`).

On 2026-04-19, live traffic surfaced a gap: an operator rejected proposed draft `E1-F1-S11-T3` via `reject-proposal <task-id> --reason ...`. Per-draft reject ran correctly -- the `.md` was archived to `.devbench/rejected-proposals/E1-F1-S11-T3-20260419T225953Z.md`, the BACKLOG.md row was removed, the source task got a `[PROPOSAL_REJECTED]` audit comment, and the ADR-07 marker-strip cascade fired. Seventeen minutes later, a new `E1-F1-S11-T3.md` appeared at the original backlog path with the same auto-generated content.

Something called `materialise_proposal` on the source JSON `.devbench/proposals/E1-F1-S11-T1.json` (which still lists T3 in `proposed_tasks[]`), and `materialise_proposal` re-created the rejected draft. `materialise_proposal` was checking only whether a draft `.md` existed; it was not asking the classifier. The classifier already knew T3 was `REJECTED` (via its `rejected-proposals/<id>-*.md` glob), but the materialiser never asked.

The gap is structural: the observability layer (status, report, list-proposals) uses the classifier, but the write layer (materialise) does not. Any caller that invokes materialise -- including the sweep-proposals step 0 we added in ADR-08, manual `devbench materialise-proposal`, or task-factory replays -- can resurrect rejected drafts.

## Decision

Make `materialise_proposal` consult the classifier on every task before deciding to create a draft. Only tasks in `UNMATERIALISED` state are materialised; every other state triggers a skip with an INFO log. Side-effects:

1. **Rejected drafts do not resurrect.** Classifier returns `REJECTED` because the archive glob matches; materialise skips.
2. **Materialise becomes idempotent.** Calling it twice on the same JSON is a no-op after the first success. Partial materialisation from a prior failed call replays cleanly (tasks already created are classified as `PROPOSED`, skipped; tasks pending are created).
3. **Concurrent manual + sweep calls are safe.** Whichever write lands first wins; the second call sees `PROPOSED` and skips.
4. **The pre-existing `_has_unresolved_proposals` guard is refined** to exclude this proposal's own task IDs, so a partial re-materialise on the same JSON is not falsely blocked by its own pending rows.

Thin-approach refusal and unresolved-prior-proposals guard still run before any per-task loop iteration -- a thin JSON is refused atomically, not per-task. The only loop-body change is the classify + skip branch.

## Consequences

- **No more resurrection of rejected drafts** regardless of how many sweep cycles, manual materialise calls, or task-factory replays happen.
- **Operator can safely retry** a failed or partial materialisation; it's no longer a trap that leaves half-created drafts.
- **`cmd_materialise_proposal` and `cmd_sweep_proposals` output gains a `skipped` surface** so the operator sees why a call was a no-op (not materialised because the task is REJECTED / DONE / DECLINED / PROMOTED).
- **The classifier is now authoritative everywhere state is read** -- the observability layer (ADR-08) and the write layer (this ADR) both route through it. Future state changes only need to touch `classify_proposed_task`.
- **Test migration**: one pre-existing test pinned the raise-on-duplicate contract and is rewritten to the new skip contract; no other tests break.

## Alternatives considered and rejected

**Prune the source JSON on per-draft reject.** Would remove the rejected task from `proposed_tasks[]` in `.devbench/proposals/<source-id>.json`. Rejected because:
- Two concurrent rejects on the same JSON race.
- Breaks the JSON-is-write-once authoring contract.
- Adds an error path (JSON-not-found, JSON-already-pruned) without adding safety beyond what the classifier already provides via archives.
- Audit trail already lives in `rejected-proposals/`; mutating the JSON duplicates that record.

**Teach sweep-proposals to check the REJECTED state before calling materialise.** Rejected because it fixes one caller but not manual `devbench materialise-proposal`, task-factory replays, or any future caller. The defense must live in `materialise_proposal` itself.

**Introduce a new lifecycle state or a rejected-tasks ledger.** Rejected as over-engineering. The classifier + archive combination already has the information needed; we just needed materialise to ask.

## Related files

### Python
- `src/devbench/backlog/proposal.py` -- `materialise_proposal` classify-aware skip; `_has_unresolved_proposals` gains `exclude_task_ids`.
- `src/devbench/cli.py` -- `cmd_materialise_proposal` emits `skipped` map; `cmd_sweep_proposals` uses pre-classify to report "N new, M skipped" and fast-path no-op when nothing unmaterialised remains.

### Tests
- `tests/test_backlog/test_proposal.py::TestMaterialiseProposalIdempotent` -- five idempotency tests (rejected-archive skip, promoted/done/declined skip parametrised, partial-materialise happy path, double-call no-op, reject-then-remateralise).
- `tests/test_backlog/test_proposal.py::TestMaterialiseProposal::test_skips_task_when_draft_file_already_exists` -- migrated from the prior raise-on-duplicate test.
- `tests/test_cli.py::TestCmdSweepAutoAccept::test_rejected_draft_not_recreated_by_sweep` -- end-to-end guard.

### Docs
- `docs/adr/09-idempotent-materialise-proposal.md` (this file).
- `docs/task-factory.md` -- new "materialise-proposal is idempotent" subsection.
- `docs/faq.md` -- Q&A on the resurrection defect.
