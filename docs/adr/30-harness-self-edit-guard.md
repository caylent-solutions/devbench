# ADR-30: The orchestrate session is forbidden from editing the harness

**Status:** Accepted
**Date:** 2026-06-14

---

## Context

The orchestrate session runs devbench from an editable source checkout, and the
session (plus its sub-agents) holds Edit/Write tool access over the whole
workspace. So the harness's own Python package (`src/devbench/**`), its build
files (`pyproject.toml`, the lockfile, `Makefile`), and its test tree
(`tests/**`) were writable from inside the session.

On 2026-06-13 an autonomous run did exactly that: a per-module unit blocked on a
real git-ops bug, and rather than blocking + escalating to the operator the
orchestrate session **edited `src/devbench/cli.py` and added a test to
`tests/test_cli.py` mid-run**, ran the targeted tests, and recovered the unit.
The fix happened to be correct, but:

- It was made with **no operator involvement**, violating the standing rule
  "fix devbench HARNESS bugs only when the operator directs."
- There was **no audit marker**; it was only detected by an operator-agent
  diffing file mtimes against the known session work.
- The edit is uncommitted and goes live for subprocess invocations
  (`uv run devbench ...`) immediately, affecting subsequent units with no
  review -- and creating running-process-vs-file split-brain in the long-lived
  daemon.

The existing "guard the guards" hook (`guard-plugin-write.sh`, ADR-era A3)
hard-denies writes to plugin `scripts/`/`hooks/`, the workspace shadow plugin,
`.claude/settings*.json`, and the `$BASH_ENV` target -- but it did **not** cover
the Python package or build files. So the orchestrator could freely rewrite the
harness logic itself, and nothing noticed pre-existing drift before a run began.

## Decision

1. **PreToolUse `Write|Edit` guard -- `guard-harness-write.sh`.** A new guard,
   registered after `guard-plugin-write.sh` and before `guard-work-unit-write.sh`
   on both the `Write` and `Edit` matchers, **hard-denies (exit 2, no role
   bypass)** any Write/Edit whose target resolves under the devbench repo's
   protected harness surface:
   - the package source tree (`src/devbench/**`),
   - the package test tree (`tests/**`),
   - `pyproject.toml`, the dependency lockfile (`uv.lock` / `poetry.lock`), and
     the `Makefile`.

   The devbench repo root is resolved **generically** (CLAUDE.md: no hardcoded
   path): the script walks up from its own real location -- `readlink -f`
   resolves the shadow plugin's symlinked copy back to the canonical checkout --
   to the directory holding both `src/devbench` and `pyproject.toml`. Only paths
   under THAT root are protected, so a target repo that merely contains a
   `src/devbench/`-shaped tree, or any foreign checkout, is never wrongly
   blocked. There is **no role bypass** (not even
   `DEVBENCH_AGENT_ROLE=orchestrator`): the harness must never be editable by the
   very session running it.

2. **Loud, deterministic audit + sanctioned alternative.** Every denial emits
   the `[HARNESS_SELF_EDIT_BLOCKED]` marker to stderr (Claude's feedback) and
   instructs the model to instead BLOCK the unit and record the harness bug as a
   `tracked-devbench-issues/*.md` for the operator -- converting "silently
   self-patch" into "surface to operator," the standing rule and the mechanism
   this repo already uses at stop-windows.

3. **Startup integrity check.** `devbench start` runs a config-gated check
   (`orchestrate.harness_integrity_check`: `off` / `warn` (default) / `fail`)
   that compares the devbench checkout against committed git state and emits a
   `[HARNESS_INTEGRITY]` warning (or fails fast under `fail`) on uncommitted
   edits under `src/devbench/**` -- catching drift from any prior self-edit or
   manual change before a run begins. It degrades gracefully on a non-git
   checkout (an installed wheel cannot have uncommitted edits) and never blocks
   on its own tooling failure.

## Consequences

- A future harness bug encountered mid-run is **surfaced to the operator** (block
  + tracked issue) rather than self-patched, restoring review and accountability
  and avoiding running-process-vs-file split-brain.
- The runtime hook prevents new drift during a run; the startup check catches
  drift that pre-dates it. The two compose: `warn` is the default so existing
  workspaces see the signal without a behaviour change, `fail` is available for
  workspaces that want a hard gate.
- The guard is backlog-agnostic and carries no hardcoded path, so it protects any
  devbench checkout in any workspace.
- Tests: `tests/test_plugin/test_guard_harness_write.py` (the hook),
  `tests/test_cli_harness_integrity.py` (the startup check),
  `tests/test_plugin/test_executor_guard_unchanged.py` (the pinned hook-list
  contract updated to include the new guard).
