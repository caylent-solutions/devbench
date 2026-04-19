# ADR-06: Validation-gate bug escalation (direct proposal emission from the executor)

**Status:** Accepted
**Date:** 2026-04-18

---

## Context

ADR-03 (task-factory) handles one shape of "the executor found bugs outside the source task's scope": the executor attempted an in-scope production fix, the amender rejected the amendment because the change belonged in a separate work unit, and blocker-resolver decomposed the rejection into a proposal JSON that task-factory materialised into drafts.

Live operation surfaced a second shape that ADR-03 does not cover: **validation-gate tasks**. These are work units whose Approach is explicitly "run verifications and report" -- the Changes Manifest is empty (`| none | | |`) and the Approach forbids production code changes. Their purpose is to exercise existing behaviour (manual integration scenarios, lint, coverage gates, bug-reproduction scenarios) and record what passed and what did not.

When a validation gate surfaces a confirmed production bug, the existing amendment pipeline does NOT fire:

- The executor self-polices: it sees the empty Changes Manifest + "no code changes" Approach and correctly declines to stage a production fix.
- Because nothing is staged, no `request-amendment` call happens.
- Because no amendment request exists, the amender never runs and never rejects.
- Because the amender never rejects, blocker-resolver + task-factory never fire.
- The bug is captured ONLY in a `NEEDS_ESCALATION` work-unit comment, which is a write-only log: no BACKLOG.md row, no draft `.md`, no actionable queue entry.

The pattern played out concretely in one 2026-04-18 orchestrator session on a downstream backlog: a validation-gate task for ~50 agent integration scenarios closed at 43 passing and 3 failing, with the 3 failures rooted in confirmed production bugs outside the gate's scope. The operator had to hand-author 3 follow-up work units to capture them -- the same "operator-authors-N-units" pain ADR-03 was supposed to eliminate, except the amendment-reject pipeline had no hook to fire from because the gate never staged a fix.

## Decision

Extend the executor to emit a proposal JSON directly via `uv run devbench write-proposal <source-id>` when it detects a validation-gate task with out-of-scope bugs. The orchestrate skill detects the proposal file's presence at a new step 4a and invokes `devbench:task-factory` immediately -- bypassing blocker-resolver entirely, because the executor's proposal already captures the per-bug decomposition the resolver would otherwise derive from a rejection archive.

No new CLI commands, no new agents, no new data files, no new status values. The two existing primitives (`write-proposal` and `materialise-proposal`) are flow-agnostic and work identically regardless of whether the upstream caller is blocker-resolver or the executor itself.

Configuration gate: the new path requires `task_factory.enabled: true` in `backlog/config/devbench.yaml`. When disabled, the executor still emits NEEDS_ESCALATION but the SKILL logs an audit comment rather than materialising drafts, preserving parity with the existing `task_factory: false` behaviour on the amendment-reject path.

Scope discipline: the executor prompt makes clear that bug-escalation fires ONLY when the task itself is a validation gate. If the Approach authorises production fixes and the executor simply failed to implement them, the correct recovery remains the existing amendment flow (stage the fix, request the amendment, let the amender review).

## Consequences

**Positive.**

- Closes the "validation-gate surfaces bugs" gap identified by the 2026-04-18 rescue. No future operator-authored hand-off like E0-F9-S2-T6/T7/T8 should be required.
- Reuses every primitive ADR-03 already introduced: `write-proposal`, `materialise-proposal`, the `proposed` status, task-factory's ID allocation, the SKILL's file-existence trigger. Zero new surface area in the CLI or data model.
- Deterministic trigger. Step 4a branches on `test -f .devbench/proposals/<id>.json`, the same file-existence idiom already used at step 4c. The executor's NEEDS_ESCALATION prose is audit-only; the file on disk is the sole control point.
- Preserves independence. The source validation-gate task's own review pipeline still runs at step 5. If its ACs passed (for example: "43/46 scenarios pass"), the source may complete normally. The proposal drafts are independent follow-ups the operator reviews and promotes.
- Keeps the amendment path un-perturbed. Step 4a short-circuits when `.devbench/amendments/<id>.json` also exists, so amendment-reject continues to own proposal handling at step 4c without double-firing.

**Negative.**

- Two triggers for the same materialisation path is a small cognitive overhead; the SKILL's step 4a / step 4c parallelism must be read together to understand when task-factory fires.
- The executor prompt is now longer. The BUG ESCALATION FOR VALIDATION GATES section is ~40 lines; a less-careful executor could skip past the "does this trigger apply?" check. Mitigation: the procedure opens with the trigger conditions and makes the "do NOT stage" instruction explicit.
- A misbehaving executor could in principle emit a proposal JSON for a non-validation-gate task. The proposal JSON schema validation (`write-proposal` validates on stdin) and the SKILL's gating on `task_factory.enabled` plus `.devbench/amendments/<id>.json` absence reduce the blast radius, but a malformed escalation is still possible.

## Alternatives considered

- **Let operators author drafts by hand whenever validation gates find bugs.** Rejected: this is exactly what ADR-03 set out to eliminate. The amendment-reject case got the treatment; the validation-gate case deserves it for the same reason.
- **Extend blocker-resolver to also scan for validation-gate executor comments and synthesise proposals.** Rejected: blocker-resolver is an LLM; the executor already has the full context (the failing scenarios, the file paths, the bug nature) at the moment it decides not to fix. Asking a second LLM to re-derive what the first one already had is wasteful and less reliable.
- **Make the amender handle empty-manifest tasks by auto-rejecting every diff.** Rejected: validation-gate tasks produce no diff, so there is nothing for the amender to reject. The amendment pipeline structurally cannot fire.
- **Add a new `devbench escalate-bugs` CLI command that wraps `write-proposal` with validation-gate-aware defaults.** Rejected: `write-proposal` already validates the JSON schema and persists the file. A wrapper adds a layer without adding capability.

## Related files

- Agents: `plugin/devbench/agents/executor.md` (new BUG ESCALATION FOR VALIDATION GATES section).
- Orchestrator: `plugin/devbench/skills/orchestrate/SKILL.md` (new step 4a).
- Source (unchanged, reused): `src/devbench/backlog/proposal.py`, `src/devbench/cli.py` (`cmd_write_proposal`, `cmd_materialise_proposal`).
- Docs: [task-factory.md](../task-factory.md), [manifest-amendments.md](../manifest-amendments.md), [faq.md](../faq.md).
- Tests: `tests/test_integration/test_validation_gate_escalation_lifecycle.py`, `tests/test_plugin/test_agent_structure.py` (asserts the new executor heading).
- Related ADRs: [ADR-02: Manifest amendment workflow](02-manifest-amendment-workflow.md), [ADR-03: Task factory](03-task-factory.md).
