#!/usr/bin/env bash
# guard-git-stage.sh -- PreToolUse hook: enforce Changes Manifest discipline on git staging + commit.
#
# Receives JSON on stdin:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Two kinds of intervention, both deterministic:
#
# 1. `git commit` with nothing staged: exit 2 with a clear message.
#
# 2. `git add <path>` where <path> is NOT listed in the active work unit's
#    Changes Manifest: exit 2 with a manifest-scope violation message.
#    This catches TRACE_FILE / dst/ / fixture-pollution style bugs at the
#    earliest possible boundary -- before a single bad file is staged.
#    The check is skipped when:
#      - CURRENT_WORK_UNIT_FILE env var is unset (no work unit context);
#      - the work-unit file can't be read;
#      - the command is `git add -A` / `git add .` / `git add -u` -- those
#        are blanket-stage forms and the downstream commit-time assertion
#        (src/devbench/backlog/manifest.py::assert_staged_matches_manifest)
#        catches violations atomically.
#
# Exit 0  -> allowed (Claude proceeds)
# Exit 2  -> blocked (stderr becomes Claude's feedback)

set -euo pipefail

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

# -----------------------------------------------------------------------------
# Rule 1: `git commit` with nothing staged.
# -----------------------------------------------------------------------------
if printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])git[[:space:]]+commit([[:space:]]|$)'; then
  if git diff --cached --quiet 2>/dev/null; then
    echo "guard-git-stage: no staged changes found -- git commit would fail with nothing to commit." >&2
    echo "Fix: stage your changes first with 'git add <files>' before running git commit." >&2
    exit 2
  fi
  exit 0
fi

# -----------------------------------------------------------------------------
# Rule 2: `git add <path>` must target paths in the Changes Manifest.
#
# Skipped when no work-unit file is pinned (CURRENT_WORK_UNIT_FILE env var);
# skipped for blanket forms (-A, -u, .) because the commit-time assertion
# rejects them precisely. Honours CURRENT_WORK_UNIT_FILE as the path to the
# active work unit's .md file.
# -----------------------------------------------------------------------------
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])git[[:space:]]+add([[:space:]]|$)'; then
  exit 0
fi

# Skip if no work-unit file pinned in env.
if [[ -z "${CURRENT_WORK_UNIT_FILE:-}" ]]; then
  exit 0
fi
if [[ ! -r "${CURRENT_WORK_UNIT_FILE}" ]]; then
  exit 0
fi

# Skip blanket stage forms: git add -A / git add . / git add -u / git add --all
# (the commit-time check catches pollution coming through those paths).
# Note: `git add -- <file>` is NOT a blanket form -- we fall through to the
# per-file check below.
if printf '%s' "$COMMAND" | grep -qE 'git[[:space:]]+add[[:space:]]+(-A\b|--all\b|-u\b|--update\b|\.[[:space:]]*$|\.[[:space:]])'; then
  exit 0
fi

# Extract the manifest file list from the work unit. The ## Changes Manifest
# table uses `| path | change |` rows; paths are fenced with backticks.
MANIFEST_FILES=$(python3 -c "
import re, sys
from pathlib import Path
try:
    content = Path(sys.argv[1]).read_text(encoding='utf-8')
except OSError:
    sys.exit(0)
m = re.search(r'^##\s+Changes Manifest\s*\n(.*?)(?=^##\s|\Z)', content, flags=re.MULTILINE | re.DOTALL)
if not m:
    sys.exit(0)
for raw in m.group(1).splitlines():
    line = raw.strip()
    if not line.startswith('|'):
        continue
    cells = [c.strip() for c in line.strip('|').split('|')]
    # Skip header + separator rows.
    if len(cells) < 2:
        continue
    if cells[0].lower() == 'file':
        continue
    if all(set(c.strip(':')) <= set('-') and c.strip(':') for c in cells):
        continue
    path = cells[0].strip('\`').strip()
    if path:
        print(path)
" "${CURRENT_WORK_UNIT_FILE}" 2>/dev/null || true)

# If the manifest couldn't be parsed, allow (fail-open on parse failure;
# the commit-time assertion will catch scope violations regardless).
if [[ -z "$MANIFEST_FILES" ]]; then
  exit 0
fi

# Extract the paths the command is trying to add.
TARGET_PATHS=$(printf '%s' "$COMMAND" | python3 -c "
import shlex, sys
try:
    tokens = shlex.split(sys.stdin.read())
except ValueError:
    sys.exit(0)
# Find 'git add' then emit every subsequent non-flag token.
try:
    i = tokens.index('add')
except ValueError:
    sys.exit(0)
for t in tokens[i+1:]:
    if t == '--':
        continue
    if t.startswith('-'):
        continue
    print(t)
" 2>/dev/null || true)

if [[ -z "$TARGET_PATHS" ]]; then
  exit 0
fi

# Intersect: every target path must be in MANIFEST_FILES.
OUT_OF_SCOPE=""
while IFS= read -r target; do
  [[ -z "$target" ]] && continue
  if ! printf '%s\n' "$MANIFEST_FILES" | grep -Fxq -- "$target"; then
    OUT_OF_SCOPE="${OUT_OF_SCOPE}${target}"$'\n'
  fi
done <<< "$TARGET_PATHS"

if [[ -n "$OUT_OF_SCOPE" ]]; then
  echo "guard-git-stage: manifest scope violation -- the following paths are not in the work unit's Changes Manifest:" >&2
  printf '  %s\n' $(printf '%s' "$OUT_OF_SCOPE" | sed '/^$/d') >&2
  echo "" >&2
  echo "Manifest declares:" >&2
  printf '  %s\n' $(printf '%s' "$MANIFEST_FILES") >&2
  echo "" >&2
  echo "Fix: either (a) revert the out-of-scope file, or (b) request a Changes Manifest amendment via 'uv run devbench request-amendment <id>'." >&2
  exit 2
fi

exit 0
