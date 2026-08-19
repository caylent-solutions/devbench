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
#    The active work unit is resolved from CURRENT_WORK_UNIT_FILE when set
#    (explicit pin: tests, operator overrides), otherwise from the
#    active-work-unit marker `devbench claim` writes under
#    $DEVBENCH_WORKSPACE_ROOT/.devbench/ (issue #336 -- hook processes
#    inherit the long-lived orchestrator environment, so a per-work-unit
#    env var can never be pinned for them; the marker is the production
#    activation path). Named sessions read their own suffixed marker.
#    The check is skipped when:
#      - no work unit resolves (neither env var nor marker present);
#      - the work-unit file can't be read;
#      - the resolved work unit no longer declares `## Status: in-progress`
#        (a stale marker is a designed skip -- claim never clears it);
#      - the command is `git add -A` / `git add .` / `git add -u` -- those
#        are blanket-stage forms and the downstream commit-time assertion
#        (src/devbench/backlog/manifest.py::assert_staged_matches_manifest)
#        catches violations atomically.
#
# Exit 0  -> allowed (Claude proceeds)
# Exit 2  -> blocked (stderr becomes Claude's feedback)

set -euo pipefail

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
# Resolution order for the active work unit (issue #336):
#   1. CURRENT_WORK_UNIT_FILE env var -- explicit pin (tests, operator).
#   2. The active-work-unit marker written by `devbench claim` under
#      $DEVBENCH_WORKSPACE_ROOT/.devbench/ (session-suffixed when
#      DEVBENCH_SESSION_NAME is set).
# Skipped when neither resolves, when the resolved file is unreadable, or
# when the resolved unit is no longer in-progress (stale marker). Skipped
# for blanket forms (-A, -u, .) because the commit-time assertion rejects
# them precisely.
# -----------------------------------------------------------------------------
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])git[[:space:]]+add([[:space:]]|$)'; then
  exit 0
fi

# Resolve the active work unit: explicit env pin wins; otherwise the marker
# `devbench claim` wrote for this session.
WORK_UNIT_FILE="${CURRENT_WORK_UNIT_FILE:-}"
if [[ -z "$WORK_UNIT_FILE" && -n "${DEVBENCH_WORKSPACE_ROOT:-}" ]]; then
  MARKER="${DEVBENCH_WORKSPACE_ROOT}/.devbench/active-work-unit"
  if [[ -n "${DEVBENCH_SESSION_NAME:-}" ]]; then
    MARKER="${MARKER}-${DEVBENCH_SESSION_NAME}"
  fi
  if [[ -r "$MARKER" ]]; then
    WORK_UNIT_FILE="$(head -n 1 "$MARKER")"
  fi
fi

# Skip if no work unit resolved.
if [[ -z "$WORK_UNIT_FILE" ]]; then
  exit 0
fi
if [[ ! -r "$WORK_UNIT_FILE" ]]; then
  exit 0
fi

# Staleness gate: enforce only while the resolved unit is actually
# in-progress. `devbench claim` never clears the marker; a terminal or
# re-queued status here means there is no active claim context.
if ! grep -qE '^##[[:space:]]+Status:[[:space:]]*in-progress[[:space:]]*$' "$WORK_UNIT_FILE"; then
  exit 0
fi

# Skip blanket stage forms: git add -A / git add . / git add -u / git add --all
# (the commit-time check catches pollution coming through those paths).
# Note: `git add -- <file>` is NOT a blanket form -- we fall through to the
# per-file check below.
if printf '%s' "$COMMAND" | grep -qE 'git[[:space:]]+add[[:space:]]+(-A\b|--all\b|-u\b|--update\b|\.[[:space:]]*$|\.[[:space:]])'; then
  exit 0
fi

# Extract the manifest file list from the work unit. The
# ``## Changes Manifest`` table uses ``| path | change |`` rows; paths
# are fenced with backticks. awk is universally available and works
# reliably under any PATH (asdf shims have caused python3 to silently
# return empty in the hook host environment, defeating the guard).
MANIFEST_FILES=$(awk '
  /^##[[:space:]]+Changes Manifest[[:space:]]*$/ { in_section = 1; next }
  in_section && /^##[[:space:]]/ { in_section = 0 }
  !in_section { next }
  # Skip table header and separator rows.
  $0 ~ /^\|[[:space:]]*[Ff]ile[[:space:]]*\|/ { next }
  $0 ~ /^\|[[:space:]]*-+/ { next }
  /^\|/ {
    # First cell is between the leading "|" and the next "|".
    line = $0
    sub(/^\|[[:space:]]*/, "", line)
    sub(/[[:space:]]*\|.*$/, "", line)
    # Strip surrounding backticks.
    gsub(/^`|`$/, "", line)
    if (length(line) > 0) print line
  }
' "$WORK_UNIT_FILE" 2>/dev/null || true)

# If the manifest couldn't be parsed, allow (fail-open on parse failure;
# the commit-time assertion will catch scope violations regardless).
if [[ -z "$MANIFEST_FILES" ]]; then
  exit 0
fi

# Extract the paths the command is trying to add.  Pure-bash word
# splitting -- the previous shlex-based extractor failed silently when
# python3 could not be resolved (asdf shim PATH miss), allowing every
# git-add to skip the manifest-scope check.
read -ra ADD_TOKENS <<< "$COMMAND"
ADD_IDX=-1
for i in "${!ADD_TOKENS[@]}"; do
  if [[ "${ADD_TOKENS[$i]}" == "add" ]]; then
    ADD_IDX=$i
    break
  fi
done
TARGET_PATHS=""
if (( ADD_IDX >= 0 )); then
  for (( i = ADD_IDX + 1; i < ${#ADD_TOKENS[@]}; i++ )); do
    tok="${ADD_TOKENS[$i]}"
    # Skip ``--`` argument terminator and any flags.
    if [[ "$tok" == "--" || "$tok" == -* ]]; then
      continue
    fi
    TARGET_PATHS+="${tok}"$'\n'
  done
fi

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
