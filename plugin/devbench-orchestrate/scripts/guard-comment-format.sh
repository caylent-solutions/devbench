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
#   - plugin/devbench-orchestrate/agents/executor.md (Comment language discipline section)
#   - plugin/devbench-orchestrate/skills/orchestrate/SKILL.md (Subagent text is diagnostic section)
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

# No command to inspect -- allow.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Only intercept 'uv run devbench log-comment' calls.
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])uv[[:space:]]+run[[:space:]]+devbench[[:space:]]+log-comment([[:space:]]|$)'; then
  exit 0
fi

# Parse arguments using pure-bash word splitting. The previous
# implementation used ``python3 -c shlex.split`` which silently
# returned no tokens whenever asdf shims could not resolve python3 in
# the hook's cwd; the legitimate log-comment calls then tripped the
# "missing required argument" branch and got blocked. Pure bash keeps
# the splitting in-shell and works everywhere.
META_TOKENS=(\| \|\| \&\& \; \& \< \<\< \> \>\> 2\> 1\> 2\>\&1 \>\& \&\>)
HELP_FLAGS=(--help -h)
read -ra TOKENS <<< "$COMMAND"

LV_IDX=-1
for i in "${!TOKENS[@]}"; do
  if [[ "${TOKENS[$i]}" == "log-comment" ]]; then
    LV_IDX=$i
    break
  fi
done

if (( LV_IDX < 0 )); then
  exit 0
fi

ARGS=()
HELP_SEEN=""
for (( i = LV_IDX + 1; i < ${#TOKENS[@]}; i++ )); do
  tok="${TOKENS[$i]}"
  is_meta=0
  for m in "${META_TOKENS[@]}"; do
    if [[ "$tok" == "$m" ]]; then is_meta=1; break; fi
  done
  if (( is_meta == 1 )); then break; fi
  is_help=0
  for h in "${HELP_FLAGS[@]}"; do
    if [[ "$tok" == "$h" ]]; then is_help=1; break; fi
  done
  if (( is_help == 1 )); then HELP_SEEN="HELP"; fi
  ARGS+=("$tok")
done

ARG_COUNT="${#ARGS[@]}"
AGENT="${ARGS[0]:-}"
UNIT_ID="${ARGS[1]:-}"
if (( ARG_COUNT > 2 )); then
  MESSAGE="${ARGS[*]:2}"
else
  MESSAGE=""
fi
# Strip a single surrounding pair of quotes if the message was authored
# as one quoted shell word; bash's IFS-split keeps the quote characters.
MESSAGE="${MESSAGE#\'}"
MESSAGE="${MESSAGE%\'}"
MESSAGE="${MESSAGE#\"}"
MESSAGE="${MESSAGE%\"}"

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
    echo "plugin/devbench-orchestrate/agents/executor.md (COMMENT LANGUAGE DISCIPLINE section) and" >&2
    echo "plugin/devbench-orchestrate/skills/orchestrate/SKILL.md (Subagent text is diagnostic section)." >&2
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
