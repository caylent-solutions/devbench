#!/usr/bin/env bash
# _hook_lib.sh -- shared JSON-extraction + bash-arg-parsing helpers.
#
# The PreToolUse hooks read the Claude Code event payload from stdin
# and need to extract specific fields (``tool_input.command``,
# ``tool_input.file_path``, ``agent_type``, ``tool_response.exit_code``,
# etc.) without depending on python3.  ``python3 -c '...'`` was the
# original pattern but it fails silently under asdf-shim PATHs that the
# hook host environment may not pin -- the guards then bow out at
# ``[[ -z "$COMMAND" ]]; exit 0`` and let dangerous tool calls through.
#
# Sourcing convention:
#   # near the top of every guard script:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=_hook_lib.sh
#   . "$SCRIPT_DIR/_hook_lib.sh"
#
# Helpers exposed:
#   extract_field <payload> <field-name>
#       Return the top-level field (e.g. ``agent_type``,
#       ``hook_event_name``).  Empty string when missing.
#   extract_command <payload>
#       Return ``tool_input.command``.  Empty when missing.
#   extract_file_path <payload>
#       Return ``tool_input.file_path``.  Empty when missing.
#   decode_json_escapes <var-name>
#       Decode common JSON backslash escapes in-place on the named
#       variable so the regex matchers below can compare against
#       literal characters.
#
# Resolution order: ``jq`` (universally available since it's in the
# project ``.tool-versions``) takes precedence; sed against the
# documented PreToolUse schema is the fallback.  The schema is
# specified in
# https://docs.anthropic.com/en/docs/claude-code/hooks#hook-input
# and is stable across Claude Code releases.

extract_field() {
  local payload="$1" field="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$payload" | jq -r --arg f "$field" '.[$f] // ""' 2>/dev/null && return 0
  fi
  printf '%s' "$payload" | sed -nE "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"((\\\\.|[^\"\\\\])*)\".*/\1/p"
}

extract_command() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -r '.tool_input.command // ""' 2>/dev/null && return 0
  fi
  printf '%s' "$1" | sed -nE 's/.*"command"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)".*/\1/p'
}

extract_file_path() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -r '.tool_input.file_path // ""' 2>/dev/null && return 0
  fi
  printf '%s' "$1" | sed -nE 's/.*"file_path"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)".*/\1/p'
}

extract_tool_response_field() {
  local payload="$1" field="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$payload" | jq -r --arg f "$field" '.tool_response[$f] // .tool_response[$f]|tostring | select(. != "null")' 2>/dev/null && return 0
  fi
  printf '%s' "$payload" | sed -nE "s/.*\"tool_response\"[^}]*\"$field\"[[:space:]]*:[[:space:]]*\"?([^,\"}]*)\"?.*/\1/p"
}

decode_json_escapes() {
  # Shell-bound in-place decoder.  Pass the variable NAME, not its
  # value; uses bash's nameref so the caller's variable is mutated.
  local -n __ref="$1"
  __ref=${__ref//\\\"/\"}
  __ref=${__ref//\\\\/\\}
  __ref=${__ref//\\n/$'\n'}
  __ref=${__ref//\\t/$'\t'}
}
