#!/usr/bin/env bash
# guard-quota-aware.sh -- PreToolUse hook: defer uv run devbench calls during quota wait.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Only intercepts commands matching "uv run devbench *".
#
# Behaviour:
#   1. Resolve the quota_pause.json path (session-scoped or workspace-root).
#   2. If the file does not exist: exit 0 (allow through).
#   3. If the file is malformed (invalid JSON or missing reset_at): exit 2 (block,
#      fail-fast; malformed checkpoint signals a bug that must not be silently ignored).
#   4. If reset_at is null (unknown): exit 2 (block; cannot determine when quota clears).
#   5. If reset_at is in the past: exit 0 (quota window has elapsed; allow through).
#   6. If max_wait_seconds has elapsed since paused_at: exit 0 (timeout reached; allow
#      through so the orchestrator can apply its on_exhaustion_timeout strategy).
#   7. Otherwise (reset_at is in the future and within max_wait): exit 2 (block).
#
# Exit 0  -> command is allowed (Claude proceeds)
# Exit 2  -> command is deferred/blocked (stderr becomes Claude's feedback)
#
# Configuration (environment variables, all optional):
#   DEVBENCH_WORKSPACE_ROOT     -- workspace root (required for checkpoint lookup)
#   DEVBENCH_SESSION_NAME       -- named session; when set, uses session-scoped path
#   DEVBENCH_QUOTA_MAX_WAIT_SECONDS -- override for max wait seconds (default: 18000)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

# ---------------------------------------------------------------------------
# Constants -- defaults aligned with constants.py QUOTA_HANDLING_DEFAULT_*
# ---------------------------------------------------------------------------
readonly DEFAULT_MAX_WAIT_SECONDS=18000

# ---------------------------------------------------------------------------
# Parse input
# ---------------------------------------------------------------------------
INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

# Exit 0 immediately for empty commands.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Only intercept "uv run devbench *" commands.
if [[ "$COMMAND" != *"uv run devbench"* ]]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# Resolve checkpoint path
# ---------------------------------------------------------------------------
WORKSPACE_ROOT="${DEVBENCH_WORKSPACE_ROOT:-}"
if [[ -z "$WORKSPACE_ROOT" ]]; then
  # Cannot locate workspace -- allow through (no checkpoint to check).
  exit 0
fi

SESSION_NAME="${DEVBENCH_SESSION_NAME:-}"
if [[ -n "$SESSION_NAME" ]]; then
  # Per-session path: <workspace>/.devbench/sessions/<name>/.devbench/quota_pause.json
  CHECKPOINT_PATH="${WORKSPACE_ROOT}/.devbench/sessions/${SESSION_NAME}/.devbench/quota_pause.json"
else
  # Workspace-root path: <workspace>/.devbench/quota_pause.json
  CHECKPOINT_PATH="${WORKSPACE_ROOT}/.devbench/quota_pause.json"
fi

# If checkpoint does not exist, quota is not active -- allow through.
if [[ ! -f "$CHECKPOINT_PATH" ]]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# Parse the checkpoint file
# ---------------------------------------------------------------------------
# Use jq (a hard dependency of devbench) to extract fields safely.
# If jq fails (malformed JSON), block immediately with an actionable message.
if ! RESET_AT=$(jq -r 'if has("reset_at") then (if .reset_at == null then "NULL_VALUE" else .reset_at end) else "MISSING_FIELD" end' "$CHECKPOINT_PATH" 2>/dev/null); then
  echo "guard-quota-aware: malformed quota_pause.json at ${CHECKPOINT_PATH}" >&2
  echo "  The file could not be parsed as valid JSON." >&2
  echo "  Fix: remove or repair ${CHECKPOINT_PATH} then retry." >&2
  exit 2
fi

# Fail-fast: reset_at field must exist in the JSON.
if [[ "$RESET_AT" == "MISSING_FIELD" ]]; then
  echo "guard-quota-aware: malformed quota_pause.json at ${CHECKPOINT_PATH}" >&2
  echo "  Required field 'reset_at' is missing from the checkpoint." >&2
  echo "  Fix: remove ${CHECKPOINT_PATH} and allow the quota watcher to rewrite it." >&2
  exit 2
fi

# Fail-fast: reset_at is null (unknown reset time) -- block until watcher clears checkpoint.
if [[ "$RESET_AT" == "NULL_VALUE" ]]; then
  REASON=$(jq -r '.reason // "unknown"' "$CHECKPOINT_PATH" 2>/dev/null || echo "unknown")
  echo "guard-quota-aware: quota wait active (reset_at: unknown)" >&2
  echo "  Reason: ${REASON}" >&2
  echo "  Checkpoint: ${CHECKPOINT_PATH}" >&2
  echo "  The reset time is null; waiting for the quota watcher daemon to clear the checkpoint." >&2
  echo "  Fix: run 'uv run devbench quota-watcher --once' to probe for quota recovery." >&2
  exit 2
fi

REASON=$(jq -r '.reason // "unknown"' "$CHECKPOINT_PATH" 2>/dev/null || echo "unknown")
PAUSED_AT=$(jq -r '.paused_at // ""' "$CHECKPOINT_PATH" 2>/dev/null || echo "")

# Capture current epoch once for all time comparisons below.
NOW_EPOCH=$(date +%s)

# ---------------------------------------------------------------------------
# Max-wait check (using paused_at)
# ---------------------------------------------------------------------------
MAX_WAIT_SECONDS="${DEVBENCH_QUOTA_MAX_WAIT_SECONDS:-${DEFAULT_MAX_WAIT_SECONDS}}"

if [[ -n "$PAUSED_AT" ]]; then
  # Convert paused_at ISO-8601 to epoch seconds using GNU date -d (Linux).
  PAUSED_AT_EPOCH=$(date -d "$PAUSED_AT" +%s 2>/dev/null || echo "")
  if [[ -n "$PAUSED_AT_EPOCH" ]]; then
    ELAPSED_SECONDS=$(( NOW_EPOCH - PAUSED_AT_EPOCH ))
    if [[ "$ELAPSED_SECONDS" -ge "$MAX_WAIT_SECONDS" ]]; then
      # Max wait exceeded -- allow through so orchestrator can apply timeout strategy.
      exit 0
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Compare reset_at against current UTC time
# ---------------------------------------------------------------------------
RESET_AT_EPOCH=$(date -d "$RESET_AT" +%s 2>/dev/null || echo "")
if [[ -z "$RESET_AT_EPOCH" ]]; then
  # Could not parse reset_at as a date -- treat as malformed, block.
  echo "guard-quota-aware: malformed quota_pause.json at ${CHECKPOINT_PATH}" >&2
  echo "  Cannot parse reset_at=${RESET_AT} as a date." >&2
  echo "  Fix: remove ${CHECKPOINT_PATH} and allow the quota watcher to rewrite it." >&2
  exit 2
fi

if [[ "$RESET_AT_EPOCH" -le "$NOW_EPOCH" ]]; then
  # reset_at is in the past -- quota window has elapsed; allow through.
  exit 0
fi

# ---------------------------------------------------------------------------
# Quota wait is still active -- block
# ---------------------------------------------------------------------------
SECONDS_REMAINING=$(( RESET_AT_EPOCH - NOW_EPOCH ))

echo "guard-quota-aware: quota wait active -- deferring uv run devbench call" >&2
echo "  Reason: ${REASON}" >&2
echo "  Reset at: ${RESET_AT} (in ~${SECONDS_REMAINING}s)" >&2
echo "  Checkpoint: ${CHECKPOINT_PATH}" >&2
echo "  Fix: wait for 'uv run devbench quota-watcher --once' to clear the checkpoint," >&2
echo "       or remove ${CHECKPOINT_PATH} manually if quota has already recovered." >&2
exit 2
