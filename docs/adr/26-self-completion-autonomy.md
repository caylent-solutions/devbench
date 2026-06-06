# ADR-26: Self-Completion Autonomy Posture

**Status:** Accepted
**Date:** 2026-06-06

---

## Context

Devbench's orchestration loop can, in principle, run an entire backlog from empty
to fully done without human intervention: the orchestrator claims tasks, dispatches
executor sub-agents, receives judge verdicts, and advances task lifecycle states
automatically. Several Epic E5/E9 features (ADR-11: `task_factory.auto_accept_proposals`
defaulting to `true`, manifest-amendment defaulting to `enabled: true`) extend the
scope of what the loop can do autonomously.

Before this ADR, the autonomy posture was implicit: features were added one at a time
without a documented statement of where the system should draw the line between
"proceed autonomously" and "wait for operator confirmation". The absence of a policy
created three practical risks:

1. **Scope creep in autonomy.** Future maintainers might add autonomous capabilities
   without considering the operator-safety implications, because no canonical boundary
   was documented.
2. **Unclear operator expectations.** Operators deploying devbench in high-stakes
   environments (production backlogs, financial-services regulated codebases) needed to
   understand what the system would do without prompting.
3. **Trust model ambiguity.** The done-integrity hardening (ADR-25 / Epic E8) adds
   non-forgeable judge gates, but those gates are only meaningful if operators understand
   which actions remain human-gated. Without a self-completion posture statement, it was
   unclear whether the judge gates were the last line of defence or one layer among many.

---

## Decision

### Autonomy posture

Devbench's self-completion autonomy is bounded by the following invariants. These
invariants are not negotiable and cannot be overridden by configuration:

**1. The done-gate is always human-derived.**

A work unit reaches `done` only after real judge agents (not the executor itself) have
recorded `[REVIEW_PASS]` on canonical verdict lines (H2, ADR-25). The orchestrator
never self-issues verdicts. This means the loop cannot silently declare work done
without at least one external review step, even when running fully unattended.

**2. The PR is always a human merge.**

`auto_merge` defaults to `false` and the release gate (E9.F4) does not change this.
The single batch PR opened by `git-ops` after the release gate is intended for human
review before merge. Devbench can open the PR; it cannot merge it.

**3. Operator-action-required blockers halt autonomous progress.**

When `classify_blocked_task` returns `OPERATOR_ACTION_REQUIRED`, the orchestrator
emits a `NO_ACTIONABLE` exit and (if Slack notifications are configured) pings the
operator. The loop does not attempt to self-resolve `OPERATOR_ACTION_REQUIRED` states.

**4. Autonomy for recoverable conditions is bounded by a restart cap.**

`RUNTIME_DEGRADATION` (SDK subprocess lost Agent-tool access) triggers an auto-restart,
but the cap `DEVBENCH_MAX_AUTO_RESTARTS` (default 3) prevents infinite restart loops.
After the cap, the orchestrator fails fast with a diagnostic message rather than
looping forever.

**5. Manifest amendments require judge approval or explicit operator override.**

The `manifest-amender` agent (not the executor) decides whether to apply or reject an
amendment request. An executor cannot extend its own Changes Manifest without an
external decision. The operator-mode bypass (`--operator-mode`) is available for
human-initiated amendments and is not accessible to the autonomous loop.

### What the loop may do autonomously

The following actions are within the autonomous scope and do not require operator
intervention:

- Claim tasks from the backlog.
- Dispatch executor sub-agents and run TDD cycles.
- Request manifest amendments (the amender agent decides; the executor does not).
- Accept task-factory proposals when `task_factory.auto_accept_proposals` is `true`
  (see ADR-11).
- Mark tasks `done` after all required judges have recorded `REVIEW_PASS`.
- Roll up parent status when all children reach `done`.
- Auto-restart after SDK-level `RUNTIME_DEGRADATION` within the configured cap.
- Open a PR via `git-ops` after the release gate passes.

### What the loop must not do autonomously

- Merge a PR.
- Override a judge verdict.
- Self-issue a `[REVIEW_PASS]` or `[REVIEW_REJECTED]` verdict.
- Resolve an `OPERATOR_ACTION_REQUIRED` blocker without a human action.
- Apply an operator-mode amendment.
- Disable or bypass guard hooks.
- Proceed with `devbench start` when guard hooks are not loaded (H4, ADR-25).

---

## Alternatives considered

### Alternative A: Full human-in-the-loop for every lifecycle transition

Require an explicit operator confirmation before each task transitions to `done`,
before each PR is opened, and before each amendment is accepted.

**Rejected** because it defeats the primary purpose of the system: reducing the operator
loop-time to the minimum required for safety. The judge agents already provide
human-equivalent review at each task boundary; adding a human click on top is redundant
for low-stakes backlogs and can be enabled for high-stakes ones via `auto_accept_proposals: false`.

### Alternative B: Full autonomy (auto-merge enabled by default)

Make `auto_merge: true` the default so the entire backlog-to-merged-PR pipeline is
unattended.

**Rejected** for financial-services regulated environments. A PR that changes production
code must have a human merge as the final gate, both for regulatory compliance (SOX,
FINRA change-management requirements) and to provide a clear audit trail of human
approval. Devbench operates in environments where "no human saw this before it went to
main" is not acceptable.

### Alternative C: Trust-level tiers (read-only, read-write, full-autonomous)

Introduce a `trust_level` configuration key that gates which autonomous actions are
allowed.

**Considered** but deferred. The current invariants cover the full range from "human
at every step" (turn off `auto_accept_proposals`, disable `auto_merge` -- already the
default) to "autonomous up to PR open" (enable all defaults). A formal tier system adds
configuration complexity without enabling any new capability today. It may be introduced
in a future ADR if operational experience reveals tier-shaped demand.

---

## Consequences

### Operator trust model

Operators deploying devbench can reason about the system's autonomy boundary from this
document. The invariants are stable across releases: a future feature that would violate
one of the five invariants above requires a new ADR and explicit operator communication.

### Regulatory compliance

For SOX/FINRA-regulated repositories, the invariants ensure:

- Every `done` work unit has a documented judge audit trail (H2, ADR-25).
- The production merge is a human action (PR human merge).
- `OPERATOR_ACTION_REQUIRED` states surface to a human before the loop continues.

These properties hold even when devbench runs fully unattended between human check-ins.

### Configuration guidance

Operators who want the most conservative posture (maximum human gates) should set:

```yaml
task_factory:
  auto_accept_proposals: false

manifest_amendment:
  enabled: false
```

This returns the system to a mode where every proposal and every amendment requires an
explicit human `devbench accept-proposal` or `devbench request-amendment --operator-mode`
invocation.

Operators who want the most autonomous posture (minimum human gates) may use all
defaults; the loop will proceed autonomously up to and including opening the PR, but the
merge remains a human action.

---

## References

- ADR-11 -- `task_factory.auto_accept_proposals` default true.
- ADR-25 -- Done-integrity hardening (the non-negotiable base layer for this ADR).
- `plugin/devbench-orchestrate/agents/orchestrate/SKILL.md` -- orchestrator loop
  behaviour, round-token injection (H3 from ADR-25), backlog-assistant handoff.
- `docs/task-factory.md` -- `auto_accept_proposals` operator guidance.
- `docs/manifest-amendments.md` -- amendment workflow and operator-mode documentation.
- Spec Section 16 -- self-completion posture requirements.
- Spec Section 6 -- release gate and PR merge posture.
