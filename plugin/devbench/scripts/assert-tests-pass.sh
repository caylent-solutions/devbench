#!/usr/bin/env bash
# assert-tests-pass.sh — PostToolUse hook: block silent progression when a test
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

INPUT=$(cat)

COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || true)

EXIT_CODE=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
result = d.get('tool_result', {})
code = result.get('exit_code', 0)
print(int(code))
" 2>/dev/null || true)

# No command to inspect — allow.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# No exit code parsed — allow.
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

# Not a test command — allow regardless of exit code.
if [[ "$IS_TEST_COMMAND" -eq 0 ]]; then
  exit 0
fi

# Test command passed — allow.
if [[ "$EXIT_CODE" -eq 0 ]]; then
  exit 0
fi

# Test command failed — block with a clear, actionable error message.
echo "assert-tests-pass: test command exited with code ${EXIT_CODE} — fix all failures before proceeding." >&2
echo "Command: ${COMMAND}" >&2
echo "Fix: resolve the failing tests shown above, then re-run the test command." >&2
exit 2
