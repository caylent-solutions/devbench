#!/usr/bin/env bash
set -euo pipefail

# Stop hook: prevent the orchestrator from stopping mid-loop.
#
# When an orchestration run is active (any task is in-progress),
# this hook blocks the Stop event and injects a continuation
# instruction so Claude re-enters the loop without human intervention.
#
# Features:
#   - Circuit breaker: allows stop after max_blocks within window_seconds
#   - Task ID + file path extraction for context recovery
#   - Last action detection for specific next-step instructions
#   - Stale task detection for zombie sessions
#   - Blocked transitional state detection
#   - Audit trail: logs blocks and circuit breaker trips

WORKSPACE_ROOT="${JUDGE_WORKSPACE_ROOT:-}"
BACKLOG_INDEX="${WORKSPACE_ROOT}/BACKLOG.md"
CONFIG_FILE="${WORKSPACE_ROOT}/backlog/config/devbench.yaml"
STATE_FILE="/tmp/devbench-stop-hook-state.json"

# If no workspace root or no backlog, allow stop.
if [ -z "$WORKSPACE_ROOT" ] || [ ! -f "$BACKLOG_INDEX" ]; then
    exit 0
fi

# --- Read config (yaml -> env var -> default) ---

_read_yaml_int() {
    local key="$1" default="$2" env_var="$3"
    # Env var wins if set.
    local env_val="${!env_var:-}"
    if [ -n "$env_val" ]; then
        echo "$env_val"
        return
    fi
    # Try yaml.
    if [ -f "$CONFIG_FILE" ]; then
        local val
        val=$(grep -E "^\s+${key}:" "$CONFIG_FILE" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '[:space:]')
        if [ -n "$val" ]; then
            echo "$val"
            return
        fi
    fi
    echo "$default"
}

MAX_BLOCKS=$(_read_yaml_int "stop_hook_max_blocks" "5" "JUDGE_STOP_MAX_BLOCKS")
WINDOW_SECONDS=$(_read_yaml_int "stop_hook_window_seconds" "180" "JUDGE_STOP_WINDOW_SECONDS")
STALE_MINUTES=$(_read_yaml_int "stop_hook_stale_task_minutes" "120" "JUDGE_STOP_STALE_MINUTES")

# --- Check for in-progress task ---

IN_PROGRESS_LINE=$(grep "| in-progress |" "$BACKLOG_INDEX" 2>/dev/null | head -1 || true)

if [ -z "$IN_PROGRESS_LINE" ]; then
    # No in-progress tasks -- allow stop, clean up state file.
    rm -f "$STATE_FILE"
    exit 0
fi

# Extract task ID (first pipe-delimited field after leading pipe).
TASK_ID=$(echo "$IN_PROGRESS_LINE" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')

# Extract file path (last backtick-wrapped field).
FILE_PATH=$(echo "$IN_PROGRESS_LINE" | grep -oP '`[^`]+`' | tail -1 | tr -d '`')

# --- Detect blocked transitional state ---
# BACKLOG.md says in-progress but the actual work unit file may say blocked.

if [ -n "$FILE_PATH" ] && [ -f "${WORKSPACE_ROOT}/${FILE_PATH}" ]; then
    FILE_STATUS=$(grep -oP '## Status:\s*\K\S+' "${WORKSPACE_ROOT}/${FILE_PATH}" 2>/dev/null || true)
    if [ "$FILE_STATUS" = "blocked" ]; then
        cat <<HOOKEOF
{
    "decision": "block",
    "reason": "Task ${TASK_ID} is transitioning to blocked state. Run: uv run devbench validate-backlog && uv run devbench next to find the next actionable task. Then claim it and continue the orchestration loop."
}
HOOKEOF
        exit 0
    fi
fi

# --- Circuit breaker ---

NOW=$(date +%s)
BLOCK_COUNT=0
FIRST_BLOCK_TS="$NOW"

if [ -f "$STATE_FILE" ]; then
    BLOCK_COUNT=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('count',0))" 2>/dev/null || echo 0)
    FIRST_BLOCK_TS=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('first_block_ts',$NOW))" 2>/dev/null || echo "$NOW")
fi

ELAPSED=$((NOW - FIRST_BLOCK_TS))

# Reset counter if window has expired.
if [ "$ELAPSED" -ge "$WINDOW_SECONDS" ]; then
    BLOCK_COUNT=0
    FIRST_BLOCK_TS="$NOW"
fi

# Check if circuit breaker should trip.
if [ "$BLOCK_COUNT" -ge "$MAX_BLOCKS" ]; then
    # Circuit breaker tripped -- allow stop, log to work unit, clean state.
    if [ -n "$TASK_ID" ] && command -v uv >/dev/null 2>&1; then
        uv run devbench log-comment stop_hook "$TASK_ID" "[CIRCUIT_BREAKER] Allowed stop after ${BLOCK_COUNT} blocks in ${ELAPSED}s. Human intervention may be needed." 2>/dev/null || true
    fi
    rm -f "$STATE_FILE"
    exit 0
fi

# --- Detect stale in-progress task ---

STALE_WARNING=""
STALE_THRESHOLD_SECONDS=$((STALE_MINUTES * 60))

# Find the most recent in-progress log entry for this task.
LOG_FILE="${WORKSPACE_ROOT}/../devbench/src/devbench/logs/orchestrator.log"
if [ -f "$LOG_FILE" ] && [ -n "$TASK_ID" ]; then
    LAST_PROGRESS_LINE=$(grep "Set ${TASK_ID} to 'in-progress'" "$LOG_FILE" 2>/dev/null | tail -1 || true)
    if [ -n "$LAST_PROGRESS_LINE" ]; then
        PROGRESS_TS=$(echo "$LAST_PROGRESS_LINE" | grep -oP '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}' || true)
        if [ -n "$PROGRESS_TS" ]; then
            PROGRESS_EPOCH=$(date -d "${PROGRESS_TS}" +%s 2>/dev/null || echo 0)
            TASK_AGE=$((NOW - PROGRESS_EPOCH))
            if [ "$TASK_AGE" -ge "$STALE_THRESHOLD_SECONDS" ]; then
                STALE_WARNING=" WARNING: This task has been in-progress for $((TASK_AGE / 60)) minutes (threshold: ${STALE_MINUTES}m). It may be stale from a crashed session. Run 'devbench status' to assess."
            fi
        fi
    fi
fi

# --- Determine last action and next step ---

LAST_ACTION="unknown"
NEXT_STEP="Run uv run devbench read-unit ${TASK_ID} to reload context, then continue the orchestration loop."

if [ -n "$FILE_PATH" ] && [ -f "${WORKSPACE_ROOT}/${FILE_PATH}" ]; then
    # Get last judge/agent comment line.
    LAST_COMMENT=$(grep -E '^\[.*\] \[(judge|agent)/' "${WORKSPACE_ROOT}/${FILE_PATH}" 2>/dev/null | tail -1 || true)

    if echo "$LAST_COMMENT" | grep -q "executor" 2>/dev/null; then
        LAST_ACTION="executor completed"
        NEXT_STEP="Invoke review-supervisor for ${TASK_ID}. Run the 4 review agents (code_review, test_review, doc_review, changes_manifest)."
    elif echo "$LAST_COMMENT" | grep -q "REVIEW_PASS.*code_review\|REVIEW_PASS.*test_review\|REVIEW_PASS.*doc_review\|REVIEW_PASS.*changes_manifest" 2>/dev/null; then
        LAST_ACTION="review pass"
        NEXT_STEP="Check if all 4 reviewers passed. If yes, invoke security-reviewer for ${TASK_ID}. If not, run remaining reviewers."
    elif echo "$LAST_COMMENT" | grep -q "REVIEW_FAIL" 2>/dev/null; then
        LAST_ACTION="review fail"
        NEXT_STEP="Re-run executor for ${TASK_ID} with prior feedback, then re-run review-supervisor."
    elif echo "$LAST_COMMENT" | grep -q "security_review.*REVIEW_PASS" 2>/dev/null; then
        LAST_ACTION="security pass"
        NEXT_STEP="Run uv run devbench git-ops ${TASK_ID} then uv run devbench mark-done ${TASK_ID}."
    elif echo "$LAST_COMMENT" | grep -q "COMMIT_DEFERRED\|PR_MERGED" 2>/dev/null; then
        LAST_ACTION="git-ops completed"
        NEXT_STEP="Run uv run devbench mark-done ${TASK_ID} then loop back: uv run devbench validate-backlog && uv run devbench next."
    elif echo "$LAST_COMMENT" | grep -q "DONE" 2>/dev/null; then
        LAST_ACTION="task done"
        NEXT_STEP="Loop back: uv run devbench validate-backlog && uv run devbench next. Claim the next task and continue."
    fi
fi

# --- Increment counter and save state ---

NEW_COUNT=$((BLOCK_COUNT + 1))
echo "{\"count\": ${NEW_COUNT}, \"first_block_ts\": ${FIRST_BLOCK_TS}}" > "$STATE_FILE"

# --- Log block to orchestrator log ---

if command -v uv >/dev/null 2>&1; then
    uv run devbench log "Stop hook blocked (${NEW_COUNT}/${MAX_BLOCKS}): ${TASK_ID} in-progress, last action: ${LAST_ACTION}" 2>/dev/null || true
fi

# --- Block stop with context ---

cat <<HOOKEOF
{
    "decision": "block",
    "reason": "Orchestration loop active. Task ${TASK_ID} is in-progress (file: ${FILE_PATH}). Last action: ${LAST_ACTION}. ${NEXT_STEP} Circuit breaker: ${NEW_COUNT}/${MAX_BLOCKS} blocks in ${ELAPSED}s window.${STALE_WARNING} Never stop between tasks."
}
HOOKEOF

exit 0
