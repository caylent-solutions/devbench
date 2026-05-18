# ADR-15: Orchestrator-Tier Hook Bypass for `guard-work-unit-write`

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

The `guard-work-unit-write.sh` PreToolUse hook is the runtime gate that
enforces "executor agents must not edit work-unit files directly." It
inspects every Edit / Write tool call and blocks the ones whose
`tool_input.file_path` matches `backlog/**/*.md`.

Issue #160 surfaced a tension in that contract. The hook can't tell the
difference between:

1. An **executor agent** trying to edit a work-unit file (must be
   blocked, the hook's original purpose).
2. The **orchestrator agent itself** doing a legitimate corrective edit
   on a work-unit file when devbench-authored content (e.g., a
   blocker-resolver-emitted proposal carrying bad rule-11 paths -- see
   issue #159) needs in-place repair.

Today both get blocked identically. The orchestrator's only escape is
to fall back to `sed -i` from a Bash tool call, which bypasses the
Tool-system guard entirely. `sed` is a workaround, not a sanctioned
path -- it skips audit logging, skips the rule-10 (em-dash) and rule-11
(checkout_directory prefix) content checks, and skips any future
content checks the hook gains.

## Decision

Add a deterministic role indicator that the hook reads from the
calling environment: `DEVBENCH_AGENT_ROLE`.

- When the role is **orchestrator**, the hook ALLOWS the write but
  STILL applies the existing content rules (rule 10 em-dash,
  rule 11 checkout_directory prefix). The hook becomes content-rules-
  enforcing rather than path-blocking for orchestrator-tier callers.
- When the role is **executor**, the hook BLOCKS as today.
- When the role is **missing** or **unrecognised**, the hook defaults
  to BLOCK. This preserves today's behaviour for legacy callers that
  haven't been updated and is the safe default per CLAUDE.md
  "fail-fast" / "default-deny" principles.

Implementation: a new `_resolve_caller_role` helper in
`plugin/devbench/scripts/_hook_lib.sh` reads `DEVBENCH_AGENT_ROLE` from
the env and returns `orchestrator`, `executor`, or empty string. The
guard script branches on the result at the final block-or-allow gate.

The orchestrator subprocess sets `DEVBENCH_AGENT_ROLE=orchestrator` in
its env before invoking any Claude tool; executor subprocesses
inherit no such env var.

## Alternatives considered

- **Parse role from a structured stdin field** (e.g., a `role` JSON key
  in the hook payload). Equivalently safe but ties the hook to the
  Claude Code payload schema; an env-var indicator is portable across
  any future hook caller. Defer to env var.
- **Whitelist orchestrator process by PID / parent-process inspection.**
  Brittle: relies on process-tree heuristics that break under
  containerised invocations and `nohup` / `setsid` re-parenting.
  Reject.
- **Different hook script for orchestrator-tier writes.** Doubles the
  rule-10 / rule-11 logic into two scripts that drift over time.
  Reject.

## Consequences

- The orchestrator's corrective-edit path no longer requires the
  `sed -i` workaround. Edit-tool calls get the rule-10 + rule-11
  content checks for free.
- `sed -i` is still available as an escape hatch for shell-tier
  scripted edits (chore commits, etc.); the hook does not see those
  by design.
- New `tests/unit/test_guard_work_unit_write.py::TestGuardWorkUnitWriteOrchestratorBypass`
  pins all six scenarios: orchestrator+clean->ALLOW,
  orchestrator+rule-10->BLOCK, orchestrator+rule-11->BLOCK,
  executor->BLOCK, missing-role->BLOCK, unknown-role->BLOCK.
- Documentation under `docs/architecture.md` (Hooks layer, section
  "Caller-role indicator: `DEVBENCH_AGENT_ROLE`") names the env-var
  indicator and the expected calling convention.

## References

- Issue #160 (parent)
- Issue #159 (the recurring rule-11 bug that surfaced this hook gap)
- `plugin/devbench/scripts/_hook_lib.sh` -- where `_resolve_caller_role` lives
- `plugin/devbench/scripts/guard-work-unit-write.sh` -- where the role-aware ALLOW/BLOCK gate lives
- `tests/unit/test_guard_work_unit_write.py::TestGuardWorkUnitWriteOrchestratorBypass`
