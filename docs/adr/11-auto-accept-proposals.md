# ADR-11: `task_factory.auto_accept_proposals` toggle (auto-promote drafts)

**Status:** Accepted
**Date:** 2026-04-20

---

## Context

Every task-factory proposal today lands at `## Status: proposed` and waits for the operator to run `promote-proposal` or `reject-proposal`. This is the correct default for most backlogs: a human decides whether the drafts the blocker-resolver just generated are in scope, well-titled, and worth wiring as dependencies of their source tasks.

Some backlogs want the opposite. In those workspaces the operator has observed over time that blocker-resolver and task-factory produce sensible drafts consistently, and they do not want to be in the loop on every promote. Their current workaround is to run `promote-proposal --all-from <source>` after every sweep tick, which works but requires the operator to notice a tick happened and remember to run the command.

This ADR adds a single opt-in workspace-level yaml flag -- `task_factory.auto_accept_proposals` -- that makes `devbench sweep-proposals` auto-promote every task-factory-produced draft at the next orchestrate loop tick. Default is `false`, matching today's behaviour byte-for-byte, so every existing backlog keeps working without change.

## Decision

1. Add an optional boolean `auto_accept_proposals` under the existing `task_factory` yaml block. Default `false`.
2. When the flag is `true`, `cmd_sweep_proposals` -- which runs as SKILL step 0 on every orchestrate loop tick -- calls `promote_proposal()` for every task whose state is currently `PROPOSED` in any on-disk proposal JSON. Promotes are idempotent via `classify_proposed_task`: a draft already in a non-`PROPOSED` state is skipped.
3. Every auto-promoted draft's `[PROPOSAL_PROMOTED]` audit comment gains a short parenthetical suffix -- `(auto-accepted via task_factory.auto_accept_proposals=true)` -- between the description and the `[BLOCKED_PENDING_PROPOSAL]` marker so a reviewer of the work-unit file can tell at a glance that no human pressed the button.

Every downstream system -- the ADR-07 auto-requeue cascade, the ADR-10 multi-target wiring on `affected_task_ids`, the reject-flow marker-strip, the done-gate -- continues to work unchanged. Auto-accept just shifts the who (tool instead of human); the what (which drafts get a marker, where the marker lands, when the cascade fires) is identical to the manual-promote path.

## Consequences

- **Zero-touch proposal lifecycle is now a single yaml flag.** Operators who trust their blocker-resolver flip `auto_accept_proposals: true` and stop being paged on every promote.
- **Audit trail is preserved.** Every auto-promoted draft still carries a timestamped `[PROPOSAL_PROMOTED]` line with the auto-accept suffix; reviewers of the work-unit file see exactly what happened.
- **Backward compatible.** Omitting the key yields `false`; every existing `devbench.yaml` parses and runs identically to before.
- **Rejection still works.** An auto-promoted draft the operator later decides is wrong is rejected via the standard `reject-proposal <id> --reason "..."` flow; marker strip + cascade handle it exactly like any other reject.
- **Legacy drafts are picked up too.** Flipping the flag on when PROPOSED drafts already exist causes the next sweep tick to promote them as well, no extra operator action.

## Alternatives considered and rejected

**Per-proposal `auto_accept: true` override.** Add the flag to the proposal JSON schema so an author can tag individual proposals for auto-accept. Rejected: the flag is a workspace-level policy, not a per-proposal attribute. Per-proposal overrides require an agent to make a policy decision on every proposal, which is the opposite of the goal (take the decision away from the runtime).

**SKILL prompt change that teaches the orchestrator to auto-run `promote-proposal --all-from`.** Rejected: puts policy in prompt text that an LLM has to re-read and re-interpret on every loop tick. A yaml flag read once at config load is deterministic, testable, and auditable. Prompts are for agent behaviour; policy lives in config.

**New operator-facing `devbench auto-accept-proposals` CLI command.** Rejected: duplicates the sweep-time integration. Operators who want to trigger a manual auto-accept pass can run `devbench sweep-proposals` directly (which, with the flag on, performs the auto-accept). One author of auto-promotes is cleaner than two.

**Tied to a new SKILL phase rather than existing sweep.** Rejected: sweep is already the integration point for proposal-lifecycle side effects. Adding a separate phase means a second point where config is read, a second log line, a second place to debug. Every existing lifecycle slice landed in sweep for the same reason.

## Related files

### Python
- `src/devbench/config-schema.json` -- `auto_accept_proposals: boolean` under `task_factory.properties`.
- `src/devbench/config_loader.py::TaskFactoryConfig` -- new field; default `False`.
- `src/devbench/cli.py::cmd_sweep_proposals` -- auto-promote loop guarded by the flag; output line augmented with `(auto-promoted: N)`.
- `src/devbench/backlog/proposal.py::_append_promote_comment` -- optional `audit_suffix: str = ""` kwarg; threaded through `promote_proposal`.

### Tests
- `tests/test_config_loader.py::TestTaskFactoryConfig` (6 new cases: defaults, explicit false, explicit true, schema rejects string, schema rejects integer).
- `tests/test_cli.py::TestCmdSweepAutoAccept` (5 new cases: off / on / idempotent on already-promoted / failure-path / legacy-PROPOSED promotes).
- `tests/test_backlog/test_proposal.py::TestPromoteCommentAuditSuffix` (2 cases: no-suffix back-compat pin + with-suffix happy path).
- `tests/test_integration/test_task_factory_lifecycle.py::TestAutoAcceptProposalsLifecycle` (1 end-to-end: two drafts, sweep with flag on, both reach `in-queue` and both audit suffixes land on the source task).

### Docs
- `docs/adr/11-auto-accept-proposals.md` (this file).
- `docs/task-factory.md` -- new "Auto-accepting proposals" subsection.
- `docs/cli-reference.md` -- `sweep-proposals` entry carries the `(auto-promoted: N)` output note.
- `docs/faq.md` -- new Q&A.
- `docs/architecture.md` -- capability bullet extension.
- Consumer `backlog/config/devbench.yaml` files -- flag is optional; operator adds it only when they want auto-accept.
