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
  "git checkout --"
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

# Defense-in-depth atop guard-plugin-write.sh: that hook only fires for the
# Write / Edit tools, so an agent could route around it by mutating a
# protected file via a Bash command (sed -i, tee, or shell output
# redirection). This best-effort regex scan blocks in-place-write Bash
# commands whose target text matches the same protected paths the
# guard-plugin-write.sh "guard the guards" hook protects:
#   - a plugin's guard scripts / hook config (/plugin/.../scripts/ or .../hooks/)
#   - the workspace shadow plugin (/.devbench/plugin-shadow/)
#   - Claude settings files (/.claude/settings...)
# It is best-effort because it pattern-matches the command STRING rather than
# resolving real file targets; the authoritative block is guard-plugin-write.sh.
PROTECTED_PATH_RE='(/plugin/[^[:space:]]*/(scripts|hooks)/|/\.devbench/plugin-shadow/|/\.claude/settings)'

# In-place / redirecting write verbs we care about: `sed -i`, `tee`, and
# `>` / `>>` output redirection.
INPLACE_WRITE_RE='(sed[[:space:]]+(-[^[:space:]]*[[:space:]]+)*-i|[[:space:]]tee[[:space:]]|^tee[[:space:]]|>>?[[:space:]]*)'

if [[ "$COMMAND" =~ $INPLACE_WRITE_RE ]] && [[ "$COMMAND" =~ $PROTECTED_PATH_RE ]]; then
  echo "guard-bash: blocked in-place Bash write to a protected guard-layer path" >&2
  echo "Command: ${COMMAND}" >&2
  echo "Reason: the command appears to mutate a plugin script/hook, the shadow plugin," >&2
  echo "or a Claude settings file (defense-in-depth atop guard-plugin-write.sh)." >&2
  echo "Fix: the guard layer must not be self-modifiable; there is no bypass." >&2
  exit 2
fi

exit 0
