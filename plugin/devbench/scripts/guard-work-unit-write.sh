#!/usr/bin/env bash
# guard-work-unit-write.sh -- PreToolUse hook: block Write/Edit to work unit .md files.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Write"|"Edit", "tool_input": { "file_path": "..." } }
#
# Blocks writes to files matching backlog/**/*.md (work unit files).
# Allows:
#   - Files outside backlog/
#   - BACKLOG.md (top-level tracking index, managed separately)
#   - backlog/config/AGENT-INSTRUCTIONS.md and other non-.md files in backlog/
#   - Non-.md files anywhere under backlog/
#
# Work unit .md files are managed exclusively by the orchestrate skill.
# Executor agents must not modify them directly.
#
# Exit 0  → operation is allowed (Claude proceeds)
# Exit 2  → operation is blocked (stderr becomes Claude's feedback)

set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('file_path', ''))
" 2>/dev/null || true)

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Allow BACKLOG.md at any level -- this is the top-level tracking index,
# not a work unit file. Only backlog/**/*.md files are work units.
if [[ "$FILE_PATH" == "BACKLOG.md" ]] || [[ "$FILE_PATH" == */BACKLOG.md ]]; then
  exit 0
fi

# Allow writes to files inside backlog/config/ -- these are configuration artifacts,
# not work unit files. Config files are managed by the workspace setup.
if [[ "$FILE_PATH" == */backlog/config/* ]] || [[ "$FILE_PATH" == backlog/config/* ]]; then
  exit 0
fi

# Block writes to .md files under backlog/ -- these are work unit files.
# Work unit files are managed exclusively by the orchestrate skill.
if [[ "$FILE_PATH" == */backlog/*.md ]] || [[ "$FILE_PATH" == backlog/*.md ]]; then
  echo "guard-work-unit-write: blocked write to work unit file: ${FILE_PATH}" >&2
  echo "Fix: work unit .md files under backlog/ are managed exclusively by the orchestrate skill." >&2
  echo "Executors must not modify work unit files directly." >&2
  exit 2
fi

exit 0
