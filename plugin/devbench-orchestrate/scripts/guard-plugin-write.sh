#!/usr/bin/env bash
# guard-plugin-write.sh -- PreToolUse hook: "guard the guards".
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Write"|"Edit", "tool_input": { "file_path": "...", ... } }
#
# An autonomous orchestrator session was able to edit a security guard
# script (guard-verdict-format.sh) because NO hook blocked Write/Edit to
# the plugin's own scripts/hooks. This hook closes that gap.
#
# It HARD-DENIES (exit 2) any Write/Edit whose target file_path matches a
# protected, self-modification path. The denial is GENERIC -- it carries no
# hardcoded workspace, backlog, or plugin name -- and there is NO role
# bypass: even DEVBENCH_AGENT_ROLE=orchestrator is rejected. The guard layer
# must never be editable by the very agents it constrains.
#
# Protected categories (target matches ANY -> exit 2):
#   1. A plugin's guard scripts or hook config:
#        path contains "/plugin/" AND ("/scripts/" OR "/hooks/").
#   2. The workspace shadow plugin:
#        path contains "/.devbench/plugin-shadow/".
#   3. A Claude settings file:
#        basename matches "settings*.json" under a "/.claude/" dir.
#   4. The generic env-injection vector:
#        absolute path equals the file named by $BASH_ENV (when BASH_ENV set).
#
# Everything else -> exit 0 (allow).
#
# Matching works for both absolute and workspace-relative file_path values.
#
# Exit 0  -> operation is allowed (Claude proceeds)
# Exit 2  -> operation is blocked (stderr becomes Claude's feedback)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
FILE_PATH=$(extract_file_path "$INPUT")
decode_json_escapes FILE_PATH

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Normalise the basename for the settings*.json category. ${var##*/} is a
# pure-shell basename that works regardless of whether FILE_PATH is absolute
# or relative.
BASENAME="${FILE_PATH##*/}"

deny() {
  # $1 = short rule label, $2 = human-readable category description.
  echo "guard-plugin-write: BLOCKED ${1}: ${FILE_PATH}" >&2
  echo "Reason: ${2}" >&2
  echo "Fix: the plugin's own guard scripts, hook config, shadow plugin, and Claude" >&2
  echo "settings files are protected from Write/Edit -- the guard layer must not be" >&2
  echo "self-modifiable. There is NO role bypass (not even DEVBENCH_AGENT_ROLE=orchestrator)." >&2
  exit 2
}

# Category 1: any plugin's guard scripts or hook config.
# Requires a "plugin/" path segment AND a "scripts/" or "hooks/" segment later
# in the same path. The leading-slash forms cover absolute paths and paths
# where "plugin/" is nested; the bare "plugin/" prefix forms cover a
# workspace-relative file_path (e.g. "plugin/.../scripts/x.sh").
if [[ "$FILE_PATH" == *"/plugin/"*"/scripts/"* ]] \
  || [[ "$FILE_PATH" == *"/plugin/"*"/hooks/"* ]] \
  || [[ "$FILE_PATH" == "plugin/"*"/scripts/"* ]] \
  || [[ "$FILE_PATH" == "plugin/"*"/hooks/"* ]]; then
  deny "plugin-script-or-hook" "target is under a plugin's scripts/ or hooks/ directory"
fi

# Category 2: the workspace shadow plugin. The bare ".devbench/plugin-shadow/"
# prefix form also catches a workspace-relative file_path.
if [[ "$FILE_PATH" == *"/.devbench/plugin-shadow/"* ]] \
  || [[ "$FILE_PATH" == ".devbench/plugin-shadow/"* ]]; then
  deny "plugin-shadow" "target is inside the workspace shadow plugin (.devbench/plugin-shadow/)"
fi

# Category 3: a Claude settings file -- basename settings*.json under a
# /.claude/ directory (covers settings.json + settings.local.json). The bare
# ".claude/" prefix form also catches a workspace-relative file_path.
if { [[ "$FILE_PATH" == *"/.claude/"* ]] || [[ "$FILE_PATH" == ".claude/"* ]]; } \
  && [[ "$BASENAME" == settings*.json ]]; then
  deny "claude-settings" "target is a Claude settings file under a .claude/ directory"
fi

# Category 4: the generic env-injection vector. When BASH_ENV is set, the
# file it names is sourced by every non-interactive bash invocation -- editing
# it is an arbitrary-code-injection path into the guard layer itself. Compare
# absolute paths so a relative file_path that resolves to the BASH_ENV target
# is still caught.
if [[ -n "${BASH_ENV:-}" ]]; then
  # Resolve both sides to absolute form without requiring the files to exist.
  abspath() {
    case "$1" in
      /*) printf '%s' "$1" ;;
      *)  printf '%s/%s' "$PWD" "$1" ;;
    esac
  }
  FILE_PATH_ABS="$(abspath "$FILE_PATH")"
  BASH_ENV_ABS="$(abspath "$BASH_ENV")"
  if [[ "$FILE_PATH_ABS" == "$BASH_ENV_ABS" ]] || [[ "$FILE_PATH" == "$BASH_ENV" ]]; then
    deny "bash-env-target" "target is the file named by \$BASH_ENV (env-injection vector)"
  fi
fi

exit 0
