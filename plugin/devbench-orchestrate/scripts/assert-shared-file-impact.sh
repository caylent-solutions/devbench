#!/usr/bin/env bash
# assert-shared-file-impact.sh -- PostToolUse hook: block silent progression
# when `devbench check-shared-file-impact` reports a shared/high-fan-in file
# was touched and the full-suite regression gate did not pass.
#
# Issue caylent-solutions/devbench-internal-backlog#13 (shared-file
# full-suite regression gate). `devbench check-shared-file-impact
# <unit-id>` is a no-op (exit 0) unless the task's diff touches a file
# matching the target repo's `gates.repos.<repo>.shared_file_impact.patterns`
# (devbench.yaml); when it does match, it runs the FULL test suite and
# diffs the failure set against a stored baseline, exiting non-zero on
# NEWLY introduced failures (pre-existing/flaky failures never block) or
# when the gate could not evaluate a baseline at all (corrupt/mismatched
# baseline, unresolvable branch point, or a lock-acquisition timeout). This
# hook is the enforcement point: the executor is instructed to run the
# command, but nothing stops an agent from skipping that instruction under
# time pressure -- this hook makes the exit code load-bearing instead of
# advisory, the same mechanism `assert-tests-pass.sh` already uses for
# `run-tests` / `pytest` / `make test`.
#
# Receives JSON on stdin with structure:
#   {
#     "tool_name": "Bash",
#     "tool_input": { "command": "..." },
#     "tool_result": { "exit_code": N, "output": "..." }
#   }
#
# Exit 0  → allowed (Claude proceeds)
# Exit 2  → blocked (stderr becomes Claude's feedback)
#
# Commands matched: any Bash invocation containing
# `devbench check-shared-file-impact` (bare, `uv run devbench ...`, or any
# prefix -- matched as a substring so wrapper scripts and env-var prefixes
# do not evade the gate).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

if command -v jq >/dev/null 2>&1; then
  EXIT_CODE=$(printf '%s' "$INPUT" | jq -r '(.tool_result.exit_code // .tool_response.exit_code) // empty' 2>/dev/null || true)
else
  EXIT_CODE=$(printf '%s' "$INPUT" | sed -nE 's/.*"exit_code"[[:space:]]*:[[:space:]]*([0-9-]+).*/\1/p' | head -1)
fi

# No command to inspect -- allow.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# No exit code parsed -- allow.
if [[ -z "$EXIT_CODE" ]]; then
  exit 0
fi

# Not a check-shared-file-impact invocation -- allow regardless of exit code.
if [[ "$COMMAND" != *"check-shared-file-impact"* ]]; then
  exit 0
fi

# Gate passed (no shared-file match, or full-suite ran clean against the
# pre-change baseline) -- allow.
if [[ "$EXIT_CODE" -eq 0 ]]; then
  exit 0
fi

# Gate blocked: either the diff touched a registered shared file and the
# full-suite run introduced new failures not present in the stored
# baseline (JSON payload with a 'new_failures' list), or the gate could
# not evaluate a baseline at all -- a corrupt/branch-point-mismatched
# baseline file, an unresolvable branch point, or a timed-out baseline
# lock acquisition -- in which case the command prints a single stderr
# 'ERROR: ...' line with no JSON payload instead.
echo "assert-shared-file-impact: check-shared-file-impact exited with code ${EXIT_CODE} -- this task's diff touches a shared/high-fan-in file and the full-suite regression gate did not pass." >&2
echo "Command: ${COMMAND}" >&2
echo "Fix: if JSON output above names a 'new_failures' list, fix every regression it introduced (do not just delete or skip the failing tests); otherwise read the single 'ERROR: ...' line above (corrupt/mismatched baseline, unresolvable branch point, or a stuck baseline lock) and resolve that condition. Then re-run check-shared-file-impact." >&2
exit 2
