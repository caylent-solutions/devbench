# ADR-32: materialise_proposal re-homes a suggested_id that collides with an unrelated unit

**Status:** Accepted
**Date:** 2026-06-16

---

## Context

[ADR-09](09-idempotent-materialise-proposal.md) made `materialise_proposal`
classify-aware: it consults `classify_proposed_task` for every
`proposed_tasks[]` entry and creates a draft only when the task is
`UNMATERIALISED`. Every other state (`PROPOSED` / `PROMOTED` / `DONE` /
`DECLINED` / `REJECTED`) triggers a skip with an INFO log. This correctly stops
a rejected draft from resurrecting and makes a re-run of the same proposal
idempotent.

Live traffic surfaced a gap that the by-id skip created. A blocker-resolver /
executor proposal carries a `suggested_id` chosen by scanning the target Story.
When that `suggested_id` happens to equal a **pre-existing UNRELATED unit's id**
(an id-allocation race, a backlog edited between the proposal's authoring and its
materialisation, or a manually authored unit), `classify_proposed_task` finds the
unrelated unit's draft and reports `PROMOTED` / `DONE` / etc. `materialise_proposal`
then skips by-id with only a soft INFO log. The orchestrator-proposed fix unit is
never created, and the blocked unit that depends on it can never auto-clear: it
waits forever on a fix unit that does not exist. The only signal is an audit line
on the blocked unit; nothing fails loudly and no fix unit appears in the backlog.

Observed: `E10-F1-S7-T12` stayed blocked because its BUG-1 fix unit's proposal
`suggested_id` (`E10-F1-S6-T26`) collided with a pre-existing unrelated unit;
materialise skipped it by-id, and the fix had to be hand-authored at a free id
after manual triage.

## Decision

`materialise_proposal` distinguishes the two situations that reach the skip
branch and re-homes a genuine collision instead of dropping it:

1. **Own already-materialised work (skip, unchanged).** The existing draft was
   authored by THIS proposal -- its provenance comment cites this source task
   (`from proposal for <source_task_id>.`, written by `generate_draft_md`) -- or
   the task was rejected (no live draft; `REJECTED` via the archive glob). This
   is the idempotent / rejected case ADR-09 protects. Skip.

2. **Collision with an unrelated unit (re-home, new).** A draft exists at the
   `suggested_id` but its provenance does NOT cite this source task. Allocate the
   next free id in the task's Story via `allocate_next_ids`, materialise the fix
   unit there, and re-point the proposal (`proposed_tasks[i].suggested_id` is
   rebound to the free id) so the downstream `promote-proposal` wiring -- which
   matches the proposal's `suggested_id` against the live draft id -- targets the
   real fix unit. The colliding pre-existing unit is never overwritten. When the
   proposal JSON exists on disk it is re-persisted so the wiring survives a
   restart.

The provenance comment is the discriminator: it is present in every draft
`generate_draft_md` writes and absent from any unrelated unit. An unreadable
draft is treated as "not ours" so a collision is never silently skipped on a read
error (fail-safe toward re-homing, never toward dropping).

`materialise_proposal` never silently no-ops on a collision (the AC the tracked
issue requires). The CLI envelope gains a `remapped` map (original id -> allocated
free id) so the operator sees the re-home.

## Consequences

- **An orchestrator-proposed fix unit is never dropped on an id collision.** The
  auto-decomposition / auto-resolve value proposition holds: the fix the
  orchestrator diagnosed is materialised under a free id rather than lost.
- **The dependent blocked unit can auto-clear.** The re-pointed proposal wires the
  blocked source to the real fix unit's id; the ADR-07 cascade fires when it
  completes.
- **ADR-09 idempotency is preserved.** Re-materialising this proposal's own drafts
  (double-call, sweep replays, concurrent manual + sweep) still skips, because
  those drafts carry this proposal's provenance.
- **The colliding unit is untouched.** Re-home allocates a new id; it never
  overwrites or mutates the pre-existing unrelated unit.

## Alternatives considered and rejected

**Fail fast on a collision (the issue's option b).** Acceptable per the tracked
issue, but it leaves the fix unit uncreated and the blocked unit stuck until an
operator intervenes -- the same operational cost the issue reports, only louder.
Re-homing recovers automatically and is the stronger AC option (a). The collision
is still surfaced (WARNING log + `remapped` CLI field), so observability is not
lost.

**Pick the collision by status (only re-home DONE/DECLINED, skip PROPOSED).**
Rejected: status does not tell us ownership. A `PROPOSED` unrelated unit is just
as much a collision as a `DONE` one. Provenance is the correct discriminator.

**Prune/rewrite the colliding unit.** Rejected: the colliding unit is unrelated
work; mutating it to free the id is destructive and out of scope for materialise.

## Related files

### Python
- `src/devbench/backlog/proposal.py` -- `materialise_proposal` collision re-home;
  `_draft_authored_by_source` provenance check; `_persist_proposal_if_present`.
- `src/devbench/cli.py` -- `cmd_materialise_proposal` emits a `remapped` map and
  computes `skipped` excluding re-homed tasks.

### Tests
- `tests/test_backlog/test_proposal_collision.py` -- collision re-home, proposal
  re-point, and own-draft idempotency preservation.
- `tests/test_backlog/test_proposal.py::TestMaterialiseProposal::test_rehomes_when_id_collides_with_unrelated_unit`
  and `TestMaterialiseProposalIdempotent::test_rehomes_when_unrelated_unit_in_any_state_collides`
  -- migrated from the prior by-id skip tests.

### Docs
- `docs/adr/32-materialise-collision-rehome.md` (this file).
- `docs/task-factory.md` -- `materialise-proposal` idempotency subsection updated
  with the collision re-home row + `remapped` CLI field.

## Related ADRs

- [ADR-09](09-idempotent-materialise-proposal.md) -- the classify-aware skip this
  ADR refines (own work still skips; unrelated collisions re-home).
- [ADR-07](07-auto-requeue-on-proposal-completion.md) -- the cascade the
  re-pointed wiring feeds.
