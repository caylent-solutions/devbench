#!/usr/bin/env bash
# guard-verdict-format.sh — PreToolUse hook: validate 'uv run devbench log-verdict' calls.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Only intercepts commands matching: uv run devbench log-verdict <judge> <id> <verdict> [feedback]
#
# Validation rules:
#   - verdict must be 'pass' or 'fail'
#   - judge name must be a known identifier
#   - feedback must be non-empty when verdict is 'fail'
#
# Exit 0  → allowed (Claude proceeds)
# Exit 2  → blocked (stderr becomes Claude's feedback)

set -euo pipefail

KNOWN_JUDGES=(
  "code_review"
  "test_review"
  "doc_review"
  "changes_manifest"
  "executor"
  "security_review"
  "blocker_resolver"
)

INPUT=$(cat)

COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || true)

# No command to inspect — allow.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Only intercept 'uv run devbench log-verdict' calls.
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])uv[[:space:]]+run[[:space:]]+devbench[[:space:]]+log-verdict([[:space:]]|$)'; then
  exit 0
fi

# Parse arguments from the command string using Python's shlex for safe
# shell-word splitting — no eval, no user-controlled string execution.
# Expected form: ... log-verdict <judge> <unit-id> <verdict> [feedback...]
#
# Python prints one field per line (judge, unit_id, verdict, feedback).
# Sequential 'read' calls consume each line without any shell evaluation.
JUDGE=""
UNIT_ID=""
VERDICT=""
FEEDBACK=""
{ read -r JUDGE; read -r UNIT_ID; read -r VERDICT; read -r FEEDBACK; } < <(
  printf '%s' "$COMMAND" | python3 -c "
import sys, shlex
cmd = sys.stdin.read()
try:
    tokens = shlex.split(cmd)
except ValueError:
    print(''); print(''); print(''); print('')
    sys.exit(0)
try:
    idx = next(i for i, t in enumerate(tokens) if t == 'log-verdict')
except StopIteration:
    print(''); print(''); print(''); print('')
    sys.exit(0)
args = tokens[idx + 1:]
print(args[0] if len(args) > 0 else '')
print(args[1] if len(args) > 1 else '')
print(args[2] if len(args) > 2 else '')
print(' '.join(args[3:]) if len(args) > 3 else '')
" 2>/dev/null || printf '\n\n\n\n'
)

# --- Validate judge name ---
JUDGE_KNOWN=0
for known in "${KNOWN_JUDGES[@]}"; do
  if [[ "$JUDGE" == "$known" ]]; then
    JUDGE_KNOWN=1
    break
  fi
done

if [[ "$JUDGE_KNOWN" -eq 0 ]]; then
  echo "guard-verdict-format: unknown judge name '${JUDGE}'." >&2
  echo "Known judges: ${KNOWN_JUDGES[*]}" >&2
  echo "Fix: use a valid judge identifier in your log-verdict call." >&2
  exit 2
fi

# --- Validate verdict value ---
VERDICT_LOWER=$(printf '%s' "$VERDICT" | tr '[:upper:]' '[:lower:]')
if [[ "$VERDICT_LOWER" != "pass" && "$VERDICT_LOWER" != "fail" ]]; then
  echo "guard-verdict-format: invalid verdict '${VERDICT}' — must be 'pass' or 'fail'." >&2
  echo "Command: ${COMMAND}" >&2
  echo "Fix: use 'pass' or 'fail' as the verdict argument." >&2
  exit 2
fi

# --- Validate feedback is non-empty when verdict is 'fail' ---
if [[ "$VERDICT_LOWER" == "fail" && -z "${FEEDBACK// /}" ]]; then
  echo "guard-verdict-format: feedback is required when verdict is 'fail' but was empty." >&2
  echo "Command: ${COMMAND}" >&2
  echo "Fix: provide a non-empty feedback message as the final argument to log-verdict." >&2
  exit 2
fi

exit 0
