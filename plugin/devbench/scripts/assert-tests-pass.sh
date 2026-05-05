#!/usr/bin/env bash
# assert-tests-pass.sh -- PostToolUse hook: block silent progression when a test
# command exits non-zero.
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
# Test commands matched: pytest, make test, make test-unit, make test-functional,
# make validate, uv run pytest.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

# Tool result lives at ``.tool_result.exit_code`` (the test fixtures
# and the original implementation use ``tool_result``; some Claude
# Code versions also surface ``tool_response`` -- accept either by
# falling through). Use jq when available; sed fallback only extracts
# the integer no matter which key wraps it.
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

# Determine whether this is a test command.
IS_TEST_COMMAND=0

# Match: bare or prefixed pytest invocations (e.g. "pytest ...", "uv run pytest ...")
if [[ "$COMMAND" =~ (^|[[:space:]])pytest([[:space:]]|$) ]]; then
  IS_TEST_COMMAND=1
fi

# Match: make test, make test-unit, make test-functional, make validate
if [[ "$COMMAND" =~ (^|[[:space:]]|\&\&|\|)make[[:space:]]+(test|test-unit|test-functional|validate)([[:space:]]|$) ]]; then
  IS_TEST_COMMAND=1
fi

# Not a test command -- allow regardless of exit code.
if [[ "$IS_TEST_COMMAND" -eq 0 ]]; then
  exit 0
fi

# Test command passed -- allow.
if [[ "$EXIT_CODE" -eq 0 ]]; then
  exit 0
fi

# Test command failed -- block with a clear, actionable error message.
echo "assert-tests-pass: test command exited with code ${EXIT_CODE} -- fix all failures before proceeding." >&2
echo "Command: ${COMMAND}" >&2
echo "Fix: resolve the failing tests shown above, then re-run the test command." >&2
exit 2
