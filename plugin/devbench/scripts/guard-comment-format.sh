#!/usr/bin/env bash
# guard-comment-format.sh -- PreToolUse hook: validate 'uv run devbench log-comment' calls.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Only intercepts commands matching: uv run devbench log-comment <agent> <id> <message>
#
# Validation rules:
#   - message body MUST NOT contain control-language imperatives directed at the
#     orchestrator's loop (halt, stop the loop, operator action required, etc.).
#     Subagent prose is diagnostic narration; loop control is owned by the
#     orchestrator + `devbench next` per SKILL halt-discipline.
#
# Passthroughs (exit 0 without validating):
#   - any '--help' / '-h' anywhere after 'log-comment' -> let the CLI print help
#   - shell meta-tokens (|, >, 2>&1, etc.) end the positional-arg window so
#     redirections and pipes are not mistaken for agent/unit_id/message args
#
# Exit 0  -> allowed (Claude proceeds)
# Exit 2  -> blocked (stderr becomes Claude's feedback)

set -euo pipefail

# Single source of truth for forbidden phrases. Mirrored in:
#   - plugin/devbench/agents/executor.md (Comment language discipline section)
#   - plugin/devbench/skills/orchestrate/SKILL.md (Subagent text is diagnostic section)
# Match is case-insensitive substring against the message body.
FORBIDDEN_PHRASES=(
  "halt orchestration"
  "halting orchestration"
  "halt the loop"
  "halt loop"
  "stop the loop"
  "stop orchestration"
  "abort orchestration"
  "operator action required"
  "resume orchestration once"
  "emergency halt"
  "do not continue"
)

EXPECTED_ORDER="log-comment <agent> <unit-id> <message>"

INPUT=$(cat)

COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null || true)

# No command to inspect -- allow.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Only intercept 'uv run devbench log-comment' calls.
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])uv[[:space:]]+run[[:space:]]+devbench[[:space:]]+log-comment([[:space:]]|$)'; then
  exit 0
fi

# Parse arguments from the command string using Python's shlex for safe
# shell-word splitting -- no eval, no user-controlled string execution.
#
# The Python block writes 5 lines to stdout:
#   line 1: "HELP" if --help/-h appears after log-comment, else empty
#   line 2: number of positional args (after truncation at shell meta-tokens)
#   line 3..5: agent, unit_id, message (empty if missing; message joins remaining args)
#
# Shell meta-tokens end positional parsing so `log-comment 2>&1 | tail`
# yields 0 args instead of treating '2>&1' as the agent.
PARSE_OUT=$(printf '%s' "$COMMAND" | python3 -c "
import sys, shlex
META = {'|', '||', '&&', ';', '&', '<', '<<', '>', '>>', '2>', '1>', '2>&1', '>&', '&>'}
HELP_FLAGS = {'--help', '-h'}
try:
    tokens = shlex.split(sys.stdin.read())
except ValueError:
    print(''); print('0'); print(''); print(''); print('')
    sys.exit(0)
try:
    idx = next(i for i, t in enumerate(tokens) if t == 'log-comment')
except StopIteration:
    print(''); print('0'); print(''); print(''); print('')
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
# Message: join the remaining tokens with single spaces. log-comment's CLI
# accepts the message as either one quoted arg or multiple words; either way
# the joined string is what the user / agent sees.
print(' '.join(args[2:]) if len(args) > 2 else '')
" 2>/dev/null || printf '\n0\n\n\n\n')

# Use mapfile so individual field reads cannot trip `set -e` when Python's
# trailing empty lines are stripped by command substitution. Pad to 5 entries
# so every later reference is safe.
mapfile -t PARSE_LINES <<< "$PARSE_OUT"
while (( ${#PARSE_LINES[@]} < 5 )); do
  PARSE_LINES+=("")
done
HELP_SEEN="${PARSE_LINES[0]}"
ARG_COUNT="${PARSE_LINES[1]:-0}"
AGENT="${PARSE_LINES[2]}"
UNIT_ID="${PARSE_LINES[3]}"
MESSAGE="${PARSE_LINES[4]}"

# --- Passthrough: --help / -h lets the CLI print usage without hook interference ---
if [[ "$HELP_SEEN" == "HELP" ]]; then
  exit 0
fi

# --- If we cannot identify a message body, defer to the CLI's own validation. ---
# The Python entry point already rejects missing args + em-dashes; duplicating
# that here would diverge over time. This guard owns ONLY the control-language
# rule, not argument-shape validation.
if (( ARG_COUNT < 3 )) || [[ -z "$MESSAGE" ]]; then
  exit 0
fi

# Suppress unused-variable warnings from `set -u` -- AGENT and UNIT_ID are
# parsed for completeness and future rule extensions.
: "$AGENT" "$UNIT_ID"

# --- Reject control-language imperatives in the message body ---
MESSAGE_LOWER=$(printf '%s' "$MESSAGE" | tr '[:upper:]' '[:lower:]')

for phrase in "${FORBIDDEN_PHRASES[@]}"; do
  if [[ "$MESSAGE_LOWER" == *"$phrase"* ]]; then
    echo "guard-comment-format: forbidden control-language phrase '${phrase}' in message body." >&2
    echo "Subagent log-comment text is diagnostic narration, not orchestrator control flow." >&2
    echo "The orchestrator's loop is controlled ONLY by 'uv run devbench next' return values" >&2
    echo "and the stop-hook circuit breaker -- never by subagent prose. See" >&2
    echo "plugin/devbench/agents/executor.md (COMMENT LANGUAGE DISCIPLINE section) and" >&2
    echo "plugin/devbench/skills/orchestrate/SKILL.md (Subagent text is diagnostic section)." >&2
    echo "" >&2
    echo "Fix: rewrite the message describing the condition factually without imperatives" >&2
    echo "directed at the loop. Example replacements:" >&2
    echo "  - 'Halting orchestration: <X>'    -> '<X> detected: ...'" >&2
    echo "  - 'Operator action required: <Y>' -> 'Recommended fix: <Y>'" >&2
    echo "  - 'Resume orchestration once <Z>' -> 'Source task remains blocked until <Z>'" >&2
    exit 2
  fi
done

exit 0
