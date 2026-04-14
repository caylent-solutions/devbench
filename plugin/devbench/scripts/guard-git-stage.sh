#!/usr/bin/env bash
# guard-git-stage.sh — PreToolUse hook: block git commit when no files are staged.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Only intercepts commands that are a git commit invocation.
# Runs `git diff --cached --quiet` in the current working directory.
# If no staged changes exist, exit 2 with a clear message.
#
# Exit 0  → allowed (Claude proceeds)
# Exit 2  → blocked (stderr becomes Claude's feedback)

set -euo pipefail

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

# Only intercept real git commit invocations.
# Match patterns like:
#   git commit ...
#   git commit -m ...
#   git commit --amend ...
# Exclude cases where "git commit" appears inside quotes or as an argument
# to echo/printf (i.e., must be a leading token or follow only whitespace).
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])git[[:space:]]+commit([[:space:]]|$)'; then
  exit 0
fi

# Verify there are staged changes by running git diff --cached --quiet.
# Exit code 0 = no diff (nothing staged), exit code 1 = diff exists (changes staged).
if git diff --cached --quiet 2>/dev/null; then
  echo "guard-git-stage: no staged changes found — git commit would fail with nothing to commit." >&2
  echo "Fix: stage your changes first with 'git add <files>' before running git commit." >&2
  exit 2
fi

exit 0
