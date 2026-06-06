# ADR-25: Done-Integrity Hardening

**Status:** Accepted
**Date:** 2026-06-06

---

## Context

Prior to Epic E8, the `done` lifecycle status could be written by multiple code paths:

1. `force_status('done')` called from anywhere in the codebase.
2. `cmd_set_status` accepting `done` as a valid target status.
3. `mark_done` (the judge-gated path).
4. `_rollup_parent_status` (structural parent rollup when all children are done).

Paths 1 and 2 bypassed the done-gate entirely. The done-gate (`_last_round_all_passed`)
was the only mechanism that enforced "all required judges passed before marking done",
but it was only invoked through `mark_done`. Any caller that used `force_status` or
`set-status done` silently skipped the gate, leaving work units in `done` state without
a complete judge record.

A second weakness: `_last_round_all_passed` matched any text in the work-unit comments
that contained `[REVIEW_PASS]` or `[judge/...]` tokens, including free-text comments
that an actor could craft to forge a passing verdict. The gate counted occurrences, not
authenticity.

A third weakness: `guard-verdict-format.sh` used a default-allow policy. Any agent could
write a canonical verdict line as long as the format was correct. There was no enforcement
that only the designated reviewer agents (`review-supervisor`, `security-reviewer`) could
produce canonical verdicts, and no per-round token requirement.

A fourth weakness: `devbench start` proceeded even when the PreToolUse guard hooks were
not loaded. A run without the hooks left the integrity guarantees unenforced at the
tool-call layer.

These four weaknesses together meant the done-integrity guarantee was nominally in place
but practically defeatable by any of: direct `force_status` calls, `set-status done`
from the CLI, forged comment tokens, non-reviewer verdict writes, or hookless startup.

---

## Decision

### H1: Single gated path to `done`

`force_status` raises `ValueError: force_status must not write 'done'; use mark_done
(done-gate enforced)` when the target status resolves to `done`. `cmd_set_status`
rejects `done` as a target before acquiring any lock, with stderr message:
`ERROR: 'set-status done' is not allowed; completion must go through 'mark-done'
(enforces the done-gate: all required judges passed)` and exit code 1.

The only writers of `done` are:

- `mark_done` (leaf tasks) -- gated by `_last_round_all_passed`.
- `_rollup_parent_status` (Epic/Feature/Story) -- structural gate: all children done.

No other code path may write `done`.

### H2: Non-forgeable done-gate

`_last_round_all_passed` counts a pass only on the canonical verdict line shape emitted
by `cmd_log_verdict`:

```
^\[<iso-ts>\] \[judge/<name>\] \[REVIEW_PASS\]
```

with no `[agent/` prefix. Free-text comments that merely contain the tokens `[REVIEW_PASS]`
or `[judge/...]` are not counted. The `[REVIEW_REJECTED]` round boundary must likewise be
a canonical verdict line.

`guard-comment-format.sh` and `guard-work-unit-write.sh` gain rule 12: any non-verdict
write whose body contains `[REVIEW_PASS]`, `[REVIEW_REJECTED]`, or `[judge/<canonical>]`
is rejected with a named token in stderr and exit 2. Verdicts may only be written via
`log-verdict`.

### H3: Verdict authority (default-deny)

`guard-verdict-format.sh` is changed from default-allow to default-deny for the five
canonical reviewer verdicts. A canonical verdict is permitted only when both conditions
hold:

1. `agent_type` is one of `devbench-orchestrate:review-supervisor` or
   `devbench-orchestrate:security-reviewer`.
2. The per-round token `DEVBENCH_REVIEW_ROUND_TOKEN` is present in the environment.

Any other agent type (executor, blocker-resolver, manifest-amender, task-factory,
unknown/absent) attempting to write a canonical verdict is blocked. A missing round token
makes a spoofed `agent_type` alone insufficient to produce a verdict.

The orchestrator injects `DEVBENCH_REVIEW_ROUND_TOKEN` into each reviewer sub-agent's
environment before dispatching.

### H4: Fail-closed hook self-check

`cmd_start` asserts that the guard hooks are registered before proceeding. If they are
absent, devbench fails closed:

```
ERROR: devbench guard hooks not loaded; refusing to run (done-integrity cannot be enforced).
Launch via the devbench-orchestrate plugin.
```

Exit code 1. This gate is in addition to the library-level `mark_done`/`force_status`
guards, which hold regardless of hook state.

---

## Alternatives considered

### Alternative A: Deprecation warning instead of hard failure

Print a deprecation warning when `force_status('done')` or `set-status done` is called,
then continue.

**Rejected** because the CLAUDE.md engineering standard requires fail-fast with non-zero
exit codes. A warning that continues is silent failure by another name. The integrity
guarantee is only meaningful if violations are caught at the earliest possible point.

### Alternative B: Audit-only (log the bypass, do not block)

Log a structured `[DONE_INTEGRITY_BYPASS]` marker whenever `done` is written outside
`mark_done` or rollup, but do not block the operation.

**Rejected** because audit-only does not prevent the bypass from propagating. A
production automation run that reaches `mark-done` via a bypassed path would still
produce a `done` work unit whose judge record is incomplete. Blocking is the only
correct response.

### Alternative C: Hash-based verdict tokens

Replace the `DEVBENCH_REVIEW_ROUND_TOKEN` env var with a cryptographic HMAC that the
orchestrator derives from the work-unit ID + timestamp and that the guard validates.

**Considered** but deferred. HMAC would provide stronger replay protection but requires
key distribution across subprocesses. The env-var token provides sufficient protection
against the threat model (an autonomous executor agent attempting to self-issue verdicts
within a single orchestrator run). The HMAC approach can be introduced in a future ADR
if the threat model expands.

---

## Consequences

### Safety guarantees

- **No bypassed done-gate.** `force_status('done')` and `set-status done` now raise /
  exit non-zero before touching any file. The only production paths to `done` are
  `mark_done` and `_rollup_parent_status`, both of which enforce their respective
  prerequisites.
- **No forged verdict counts.** `_last_round_all_passed` counts only canonical verdict
  lines; free-text comment tokens cannot inflate the pass count.
- **No unauthorised verdicts.** Default-deny on `guard-verdict-format.sh` means a
  non-reviewer agent cannot produce a verdict that the done-gate will count.
- **No unguarded starts.** `cmd_start` refuses to proceed without the hooks, ensuring
  the tool-call layer enforcements are always active during a live run.

### Compatibility

No existing operator workflow is broken:

- Operators who used `devbench mark-done` continue as before.
- Operators who used `devbench set-status done` (a minority, as the CLI has always
  preferred `mark-done`) now receive a clear error message pointing at `mark-done`.
- Parent rollup (`_rollup_parent_status`) still works; it uses the gated helper.
- The library-level guards hold even in degraded mode (no hooks).

### Testing

100% new-branch coverage on all H1-H4 changes. Unit tests cover:
- `force_status` done-refusal (positive and negative).
- `cmd_set_status done` rejection (single and bulk).
- `_last_round_all_passed` canonical shape match, forged-comment negative, round boundary.
- Guard rule-12 token rejection (comment and Write/Edit tool calls).
- Guard-verdict default-deny matrix (each agent type including absent and missing token).
- Startup self-check present/absent.

Functional tests: `set-status done` rc 1; `mark-done` happy path.
Integration test: full round (five canonical verdicts then `mark-done` succeeds); forged-comment round (`mark-done` refuses).

---

## References

- `src/devbench/manager.py` -- `force_status`, `_last_round_all_passed`, `mark_done`,
  `_rollup_parent_status`.
- `src/devbench/cli.py` -- `cmd_set_status`, `cmd_start` hook self-check.
- `plugin/devbench-orchestrate/scripts/guard-comment-format.sh` -- rule 12.
- `plugin/devbench-orchestrate/scripts/guard-work-unit-write.sh` -- rule 12.
- `plugin/devbench-orchestrate/scripts/guard-verdict-format.sh` -- default-deny.
- `plugin/devbench-orchestrate/agents/orchestrate/SKILL.md` -- round-token injection.
- Spec Section 4 E8 (H1-H4) -- normative requirements.
- Issue tracking: E8-F1-S1 (H1), E8-F2-S1 (H2a), E8-F2-S2 (H2b), E8-F3-S1 (H3),
  E8-F4-S1 (H4).
