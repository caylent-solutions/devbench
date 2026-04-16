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
# Passthroughs (exit 0 without validating):
#   - any '--help' / '-h' anywhere after 'log-verdict' → let the CLI print help
#   - shell meta-tokens (|, >, 2>&1, etc.) end the positional-arg window so
#     redirections and pipes are not mistaken for judge/unit_id/verdict args
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

EXPECTED_ORDER="log-verdict <judge> <unit-id> <verdict> [feedback]"

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
#
# The Python block writes 5 lines to stdout:
#   line 1: "HELP" if --help/-h appears after log-verdict, else empty
#   line 2: number of positional args (after truncation at shell meta-tokens)
#   line 3..6: judge, unit_id, verdict, feedback (empty if missing)
#
# Shell meta-tokens end positional parsing so `log-verdict 2>&1 | tail`
# yields 0 args instead of treating '2>&1' as the judge.
PARSE_OUT=$(printf '%s' "$COMMAND" | python3 -c "
import sys, shlex
META = {'|', '||', '&&', ';', '&', '<', '<<', '>', '>>', '2>', '1>', '2>&1', '>&', '&>'}
HELP_FLAGS = {'--help', '-h'}
try:
    tokens = shlex.split(sys.stdin.read())
except ValueError:
    print(''); print('0'); print(''); print(''); print(''); print('')
    sys.exit(0)
try:
    idx = next(i for i, t in enumerate(tokens) if t == 'log-verdict')
except StopIteration:
    print(''); print('0'); print(''); print(''); print(''); print('')
    sys.exit(0)
tail = tokens[idx + 1:]
# Truncate at the first shell meta-token (redirection / pipe / chain separator).
args = []
for t in tail:
    if t in META:
        break
    args.append(t)
help_seen = 'HELP' if any(a in HELP_FLAGS for a in args) else ''
print(help_seen)
print(len(args))
print(args[0] if len(args) > 0 else '')
print(args[1] if len(args) > 1 else '')
print(args[2] if len(args) > 2 else '')
print(' '.join(args[3:]) if len(args) > 3 else '')
" 2>/dev/null || printf '\n0\n\n\n\n\n')

# Use mapfile so individual field reads cannot trip `set -e` when Python's
# trailing empty lines are stripped by command substitution. Pad to 6 entries
# so every later reference is safe.
mapfile -t PARSE_LINES <<< "$PARSE_OUT"
while (( ${#PARSE_LINES[@]} < 6 )); do
  PARSE_LINES+=("")
done
HELP_SEEN="${PARSE_LINES[0]}"
ARG_COUNT="${PARSE_LINES[1]:-0}"
JUDGE="${PARSE_LINES[2]}"
UNIT_ID="${PARSE_LINES[3]}"
VERDICT="${PARSE_LINES[4]}"
FEEDBACK="${PARSE_LINES[5]}"

# --- Passthrough: --help / -h lets the CLI print usage without hook interference ---
if [[ "$HELP_SEEN" == "HELP" ]]; then
  exit 0
fi

# --- Require at least 3 positional args (judge, unit-id, verdict) ---
if (( ARG_COUNT < 3 )); then
  # Quote each captured arg so empty ones are visible.
  GOT="(none)"
  if (( ARG_COUNT > 0 )); then
    GOT=""
    for a in "$JUDGE" "$UNIT_ID" "$VERDICT"; do
      [[ -n "$a" ]] && GOT="${GOT:+$GOT }'$a'"
    done
  fi
  echo "guard-verdict-format: missing required argument(s); got ${ARG_COUNT} of 3 required." >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Got: ${GOT}" >&2
  exit 2
fi

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
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Fix: use a valid judge identifier as the FIRST positional argument." >&2
  exit 2
fi

# --- Validate verdict value ---
VERDICT_LOWER=$(printf '%s' "$VERDICT" | tr '[:upper:]' '[:lower:]')
if [[ "$VERDICT_LOWER" != "pass" && "$VERDICT_LOWER" != "fail" ]]; then
  echo "guard-verdict-format: invalid verdict '${VERDICT}' -- must be 'pass' or 'fail'." >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Fix: use 'pass' or 'fail' as the THIRD positional argument." >&2
  exit 2
fi

# --- Validate feedback is non-empty when verdict is 'fail' ---
if [[ "$VERDICT_LOWER" == "fail" && -z "${FEEDBACK// /}" ]]; then
  echo "guard-verdict-format: feedback is required when verdict is 'fail' but was empty." >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Fix: provide a non-empty feedback message as the final argument to log-verdict." >&2
  exit 2
fi

exit 0
