# ADR-10: Multi-target proposal wiring (`affected_task_ids`) + operator `add-dep` CLI

**Status:** Accepted
**Date:** 2026-04-20

---

## Context

Before this ADR, `devbench promote-proposal <draft-id>` wired a `[BLOCKED_PENDING_PROPOSAL] <draft-id>` marker and a Dependencies-table row on exactly one task: the proposal's `source_task_id`. The [ADR-07](07-auto-requeue-on-proposal-completion.md) auto-requeue cascade only fires on tasks that carry a marker, so a task that should unblock when the fix completes cannot participate in the cascade unless it has a marker wired.

The real-world failure mode that prompted this ADR (2026-04-20):

1. `E1-F1-S16-T1` blocked on 14 pre-existing consumer-test failures that broke after `S11-T7` moved `sys.exit` from library code to the CLI boundary.
2. `E1-F1-S15-T1` was blocked on the SAME 14 test failures for the same reason.
3. The blocker-resolver surfaced a single fix proposal `E1-F1-S16-T2` from `S16-T1`; the operator promoted it.
4. `promote-proposal` wired the marker on `S16-T1` (source) automatically. It did not -- could not -- wire `S15-T1` because the proposal schema carried only one task-ID field.
5. The operator hand-edited `S15-T1`'s work-unit file to add an identical marker + Dependencies row so the cascade would reach that task too.

The hand-edit worked, but it is a manual step the tooling should not require: the operator's reasoning ("the same fix unblocks both") is a piece of structured state that the JSON schema can carry. Without a schema field, every 1:N blocker-fix pair requires a human in the loop.

A second, narrower failure mode also surfaced in the same session: an operator wanted to wire a marker AFTER a promote had already landed (proposal already accepted, draft already `in-queue`, operator now realising a peer task should have been in the list). There was no CLI path for that short of editing the work-unit file.

## Decision

Two load-bearing changes:

1. **Extend the proposal JSON schema with `affected_task_ids: list[str]`.** Optional; defaults to `[]`. `promote-proposal` wires the marker + Dependencies row on `[source_task_id] + affected_task_ids` (deduplicated, order-preserved). Fail-fast: if any affected target is missing from the backlog index, the promote raises `ProposalError` BEFORE any file write, so a missing peer never leaves the source half-wired.

2. **Add a `devbench add-dep <blocked-id> <blocker-id> [--reason <msg>]` CLI command.** Covers the scenarios that the proposal flow cannot reach: post-promote corrections, hand-authored work units that unblock peer tasks, or retroactive wiring on proposals that landed without the field populated. Idempotent: repeating the call is a no-op when both the Dependencies row and the marker are already present. Fail-fast on a terminal blocker, on IDs missing from the index, or on malformed ID format.

Together these eliminate every scenario that previously required an operator to hand-edit a work-unit file to wire a dependency edge.

The ADR-07 cascade itself is unchanged: it already iterates per-marker correctly and treats a task with N markers the same as a task with one. ADR-10 only changes how markers get written; cascade semantics are identical.

## Behaviour summary

### When `promote-proposal <task-id>` runs

1. Flip the draft from `proposed` to `in-queue`.
2. Load the originating `Proposal`; compute `targets = [source_task_id] + affected_task_ids` (dedup, order-preserved).
3. For every `target` in `targets`, confirm the task exists in the backlog index. If any is missing -> `ProposalError`, no writes, return non-zero.
4. For every target, append the Dependencies-table row and the `[BLOCKED_PENDING_PROPOSAL] <task-id>` marker on its Comments section.
5. Return a `PromoteResult` carrying the draft path AND the `wired_targets` list; CLI surfaces both in the JSON output and the INFO log.

`--no-dep-on-source` still narrows behaviour: it skips the Dependencies row on the source task only. Every entry in `affected_task_ids` is still wired (the flag addresses the "promoted draft is independent of its source" use case, not "I explicitly listed peer tasks").

### When `devbench add-dep <blocked> <blocker>` runs

1. Validate both IDs match the task-ID regex.
2. Confirm the blocker exists in the backlog index AND is not in a terminal state (`done` / `declined`) -- wiring a dep on already-done work is always a mistake.
3. Confirm the blocked task exists; warn (not refuse) when its status is not currently `blocked` (the ADR-07 cascade only fires on blocked tasks, so wiring a marker on an in-queue task is harmless metadata but the operator almost certainly meant to flip to blocked first).
4. Write the Dependencies row and the `[WU_WIRED] ... [BLOCKED_PENDING_PROPOSAL] <blocker>` marker comment (signed `[agent/operator]`, distinguishable from task-factory-written markers).
5. Idempotent: if either the row or the marker is already present, the corresponding write is skipped; `wired: false` in the output JSON means the call was a complete no-op.

### When `devbench remove-dep <blocked> <blocker>` runs (the inverse of `add-dep`)

The `remove-dep` verb (shipped after this ADR landed; see [Follow-up shipped](#follow-up-shipped)) is the exact inverse of `add-dep` -- it cuts the edge `add-dep` (or `promote-proposal`) wired:

1. Validate both IDs match the task-ID regex and are not the same task; confirm both exist in the backlog index (fail-fast otherwise).
2. Remove the `## Dependencies`-table row for `<blocker>` from `<blocked>`'s file (collapsing the table to the canonical `| none | | |` row when it empties).
3. Strip the open `[BLOCKED_PENDING_PROPOSAL] <blocker>` marker via the same strip mechanism `reject-proposal` uses, so `_has_open_proposal_marker`, `_comments_have_marker`, and the `add-dep` reverse-cycle guard all stop treating the edge as live. (The `[CASCADE_RESOLVED]` close the auto-requeue cascade writes only flips the blocker's *status* and leaves the marker text in place, so it would not clear those substring readers; the physical strip is the correct close here.)
4. Append a `[DEP_REMOVED]` audit comment recording the cut + operator reason to the blocked task's append-only Comments history.
5. Idempotent: removing an edge that does not exist is a clean no-op that writes nothing; `removed: false` in the output JSON means there was no such dependency.

See the full reference in [cli-reference.md `remove-dep`](../cli-reference.md#remove-dep).

## Consequences

- **Cross-task blocker-fixes are a first-class workflow.** One `promote-proposal` call wires every task the fix unblocks; no operator ever has to hand-edit a sibling task's file.
- **Post-hoc wiring has a CLI path.** `add-dep` covers every remaining ad-hoc scenario (corrections, manual authorship, retroactive wiring). Every dependency edge is now CLI-driven, audited, and idempotent.
- **Observability unchanged.** The blocked task-state classifier introduced by [ADR-08](08-proposal-lifecycle-observability.md) does not distinguish source from affected; every marker target is treated identically. `devbench status`, `devbench report`, and `devbench list-proposals` render the same information they did before.
- **Backward-compatible.** Every existing proposal JSON on disk loads cleanly into the new schema with `affected_task_ids=[]`. The pre-ADR-10 1:1 wiring is preserved byte-for-byte for every JSON that does not set the field.

## Downstream observability

The multi-target wiring naturally pairs with a blocked-count split in `devbench status` and `devbench report` (shipped in the same PR): blocked tasks with active markers render under a `(auto-clearing)` header because they are waiting for the cascade; blocked tasks without markers or with stale markers render under a `(needs-operator-attention)` header because the operator has to make a decision. The split lets humans scan only the attention group.

The blocked-split UX is the current state; operator alerting of attention-class blocks and proposed drafts is scoped for a follow-up and tracked in [spec-operator-attention-alerts.md](../spec-operator-attention-alerts.md).

## Alternatives considered and rejected

**Auto-discover shared blockers by comment-substring matching.** When the amender rejects an amendment, scan every currently-blocked task's Comments for the same failing test file / commit hash / error message; auto-populate `affected_task_ids` without agent involvement. Rejected: substring matching on agent-written English is fragile (natural paraphrase produces false negatives; shared vocabulary across unrelated bugs produces false positives); the maintenance cost of regex drift outweighs the benefit of saving one prompt paragraph.

**Operator CLI only, no schema field.** Ship `add-dep` but keep the proposal schema 1:1. Rejected: this puts every multi-target wire behind human attention, which is exactly the failure mode ADR-10 is closing. The schema field is where the structured state belongs.

**Multi-source proposal JSON.** Allow `source_task_id` to be a list. Rejected: `source_task_id` has well-defined semantics in the existing surface (the "task whose amendment was rejected") and changing it to a list creates churn across every downstream reader (task-factory, classify, FAQ wording) for a gain already covered by `affected_task_ids`.

**`remove-dep` command (at the time of this ADR).** This ADR deliberately scoped out the inverse cut, because the recurring failure mode was addition, not removal. That scoping was later revisited and the verb was shipped -- see [Follow-up shipped](#follow-up-shipped).

## Follow-up shipped

The `remove-dep` verb that this ADR scoped out was subsequently implemented as the exact inverse of `add-dep`: it removes the Dependencies-table row and strips the open `[BLOCKED_PENDING_PROPOSAL]` marker (the same strip mechanism `reject-proposal` uses), appends a `[DEP_REMOVED]` audit comment, and is idempotent on a non-existent edge. See [When `devbench remove-dep` runs](#when-devbench-remove-dep-blocked-blocker-runs-the-inverse-of-add-dep) above and the [cli-reference.md `remove-dep`](../cli-reference.md#remove-dep) reference. It does not change any wiring semantics in this ADR; it only provides a CLI path to undo an edge.

## Related files

### Python
- `src/devbench/backlog/proposal.py` -- `Proposal.affected_task_ids` field; `from_dict` validation; `PromoteResult` dataclass; `_find_originating_proposal`; `promote_proposal` wiring loop; `_append_manual_dep_comment`; `add_dep` helper; `remove_dep` helper + `_append_remove_dep_comment` (the inverse cut shipped as a follow-up).
- `src/devbench/cli.py` -- `cmd_promote_proposal` emits `wired_targets`; `cmd_add_dep` + `cmd_remove_dep` sharing `_parse_dep_edge_argv`; `_COMMANDS` registration.

### Plugin prompts
- `plugin/devbench-orchestrate/agents/blocker-resolver.md` -- `affected_task_ids` evidence rubric and example payload.
- `plugin/devbench-orchestrate/agents/executor.md` -- validation-gate section cross-reference.

### Tests
- `tests/test_backlog/test_proposal.py::TestProposalAffectedTaskIds` (schema).
- `tests/test_backlog/test_proposal.py::TestPromoteProposalAffectedWiring` (wiring loop).
- `tests/test_backlog/test_proposal.py::TestAddDepCoreHelper` (add_dep helper).
- `tests/test_cli.py::TestCmdAddDep` (CLI surface).
- `tests/test_backlog/test_proposal_remove_dep.py` (remove_dep helper: row removal, marker close, idempotent no-op, fail-fast, none-row collapse).
- `tests/test_cli_remove_dep.py` (remove-dep CLI surface: happy path, no-op, fail-fast).
- `tests/test_cli.py::TestCmdPromoteProposal` (wired_targets in JSON output).
- `tests/test_integration/test_task_factory_lifecycle.py::TestAffectedTaskIdsLifecycle` (end-to-end cascade across source + 2 peers).
- `tests/test_plugin/test_agent_structure.py::TestBlockerResolverAffectedTaskIdsInstruction` (prompt regression pin).

### Docs
- `docs/adr/10-multi-target-proposal-wiring.md` (this file).
- `docs/task-factory.md` (extended with "When to use `affected_task_ids`" subsection).
- `docs/cli-reference.md` (promote-proposal + new add-dep entries).
- `docs/architecture.md` (task-factory capability bullet).
- `docs/faq.md` (sibling-task-did-not-unblock Q&A).
- [ADR-07](07-auto-requeue-on-proposal-completion.md), [ADR-08](08-proposal-lifecycle-observability.md), [ADR-09](09-idempotent-materialise-proposal.md) -- cross-references note that this ADR does not change their bodies.
- `docs/spec-operator-attention-alerts.md` -- scoped follow-up.
