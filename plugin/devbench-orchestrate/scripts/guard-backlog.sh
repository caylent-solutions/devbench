#!/usr/bin/env bash
# guard-backlog.sh -- PreToolUse hook: block Write/Edit to backlog/ tracking artifacts.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Write"|"Edit", "tool_input": { "file_path": "..." } }
#
# Exit 0  → operation is allowed (Claude proceeds)
# Exit 2  → operation is blocked (stderr becomes Claude's feedback)

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

# Block writes to backlog/ directory and BACKLOG.md -- managed by orchestrate skill only
if [[ "$FILE_PATH" == */backlog/* ]] || [[ "$FILE_PATH" == */BACKLOG.md ]]; then
  echo "guard-backlog: blocked write to backlog tracking artifact: ${FILE_PATH}" >&2
  echo "Fix: backlog/ files and BACKLOG.md are managed exclusively by the orchestrate skill." >&2
  echo "Executors must not modify backlog artifacts directly." >&2
  exit 2
fi

exit 0
