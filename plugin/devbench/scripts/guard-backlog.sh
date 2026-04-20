#!/usr/bin/env bash
# guard-backlog.sh -- PreToolUse hook: block Write/Edit to backlog/ tracking artifacts.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Write"|"Edit", "tool_input": { "file_path": "..." } }
#
# Exit 0  → operation is allowed (Claude proceeds)
# Exit 2  → operation is blocked (stderr becomes Claude's feedback)

set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('tool_input', {}).get('file_path', ''))" 2>/dev/null || true)

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
