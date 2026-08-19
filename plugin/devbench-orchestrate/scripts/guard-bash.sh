#!/usr/bin/env bash
# guard-bash.sh -- PreToolUse hook: block destructive bash commands.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Exit 0  → command is allowed (Claude proceeds)
# Exit 2  → command is blocked (stderr becomes Claude's feedback)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

BLOCKED_PATTERNS=(
  "rm -rf"
  "rm -fr"
  "git push --force"
  "git push -f"
  "git reset --hard"
  # Trailing space is load-bearing (issue #335): the destructive file-restore
  # form is `git checkout -- <path>`, while `git checkout --theirs <path>` /
  # `git checkout --ours <path>` are non-destructive conflict-side selection
  # during a merge or cherry-pick. A bare "git checkout --" substring matched
  # both. guard-destructive-git.sh already draws this same line.
  "git checkout -- "
  "git clean -f"
  "git clean -fd"
  "git clean -fdx"
  "> /dev/null 2>&1 &"
)

for pattern in "${BLOCKED_PATTERNS[@]}"; do
  if [[ "$COMMAND" == *"$pattern"* ]]; then
    echo "guard-bash: blocked destructive command matching pattern '${pattern}'" >&2
    echo "Command: ${COMMAND}" >&2
    echo "Fix: use a safer alternative or request explicit approval." >&2
    exit 2
  fi
done

exit 0
