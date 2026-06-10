#!/usr/bin/env bash
set -euo pipefail

# Stop hook: prevent the orchestrator from stopping mid-loop.
#
# When an orchestration run is active (any task is in-progress),
# this hook blocks the Stop event and injects a continuation
# instruction so Claude re-enters the loop without human intervention.
#
# Timing limitation -- "decision: block" cannot resurrect a committed turn:
#   The Claude agent model commits a turn when the assistant response is
#   finalised. The Stop hook fires AFTER the turn has already wound down,
#   so emitting "decision: block" here is a best-effort nudge to the NEXT
#   turn; it cannot un-commit or roll back the turn that just ended.
#   The real in-process recovery is provided by E10-F1 (pre-tool-call
#   resumption guard) and E10-F2 (post-result continuation check), both of
#   which run INSIDE the active turn and can re-enter the loop before the
#   turn commits. This hook is therefore a safety net for cases where the
#   in-process guards did not fire (e.g., the process was interrupted
#   externally), not the primary recovery mechanism.
#
# Features:
#   - Circuit breaker: allows stop after max_blocks within window_seconds
#   - Task ID + file path extraction for context recovery
#   - Last action detection for specific next-step instructions
#   - Stale task detection for zombie sessions
#   - Blocked transitional state detection
#   - Audit trail: logs blocks and circuit breaker trips

WORKSPACE_ROOT="${DEVBENCH_WORKSPACE_ROOT:-}"
BACKLOG_INDEX="${WORKSPACE_ROOT}/BACKLOG.md"
CONFIG_FILE="${WORKSPACE_ROOT}/backlog/config/devbench.yaml"

SESSION_NAME="${DEVBENCH_SESSION_NAME:-}"

# Per-session circuit-breaker state file (AC-192-15):
# When DEVBENCH_SESSION_NAME is set, scope the state file to the named session
# so concurrent orchestrator sessions maintain independent block counters.
# When unset, fall back to the shared path for single-session (legacy) behaviour.
if [ -n "$SESSION_NAME" ]; then
    STATE_FILE="/tmp/devbench-stop-hook-state-${SESSION_NAME}.json"
else
    STATE_FILE="/tmp/devbench-stop-hook-state.json"
fi

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

MAX_BLOCKS=$(_read_yaml_int "max_blocks" "5" "DEVBENCH_STOP_MAX_BLOCKS")
WINDOW_SECONDS=$(_read_yaml_int "window_seconds" "180" "DEVBENCH_STOP_WINDOW_SECONDS")
STALE_MINUTES=$(_read_yaml_int "stale_task_minutes" "120" "DEVBENCH_STOP_STALE_MINUTES")

# --- Check for in-progress task ---

IN_PROGRESS_ROWS=$(grep "| in-progress |" "$BACKLOG_INDEX" 2>/dev/null || true)

if [ -z "$IN_PROGRESS_ROWS" ]; then
    # No in-progress tasks -- allow stop, clean up state file.
    rm -f "$STATE_FILE"
    exit 0
fi

# Active-task selection (issue #131): the BACKLOG.md "first in-progress row"
# heuristic returns whichever ID is alphabetically first, not the task the
# orchestrator most recently claimed. Prefer the most recent claim entry from
# the orchestrator log; fall back to head -1 when no log entry is parseable
# (fresh checkout, never-launched workspace).
_active_task_id_from_logs() {
    local logs_dir="${WORKSPACE_ROOT}/logs"
    [ -d "$logs_dir" ] || return 1
    # Most recent "Branch ready: <branch> on <task_id>" or
    # "Set <task_id> to 'in-progress'" entry across any orchestrator log.
    local last_entry
    last_entry=$(grep -hE "Branch ready:.* on [A-Z0-9-]+|Set [A-Z0-9-]+ to 'in-progress'" "$logs_dir"/*.log 2>/dev/null | tail -1 || true)
    [ -n "$last_entry" ] || return 1
    if [[ "$last_entry" =~ Branch\ ready:.*on\ ([A-Z0-9-]+) ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi
    if [[ "$last_entry" =~ Set\ ([A-Z0-9-]+)\ to ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

# Build the alphabetically-ordered list of in-progress task IDs (for use in
# the reason text when more than one task is in-progress).
ALL_IN_PROGRESS_IDS=$(printf '%s\n' "$IN_PROGRESS_ROWS" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); if ($2 != "") print $2}')

ACTIVE_TASK_ID=$(_active_task_id_from_logs || true)
if [ -n "$ACTIVE_TASK_ID" ] && printf '%s\n' "$ALL_IN_PROGRESS_IDS" | grep -qx "$ACTIVE_TASK_ID"; then
    TASK_ID="$ACTIVE_TASK_ID"
else
    # Fallback: first row in BACKLOG.md.
    TASK_ID=$(printf '%s\n' "$ALL_IN_PROGRESS_IDS" | head -1)
fi

IN_PROGRESS_LINE=$(printf '%s\n' "$IN_PROGRESS_ROWS" | awk -F'|' -v tid="$TASK_ID" '{gsub(/^[ \t]+|[ \t]+$/, "", $2); if ($2 == tid) {print; exit}}')

# Extract file path (last backtick-wrapped field).
FILE_PATH=$(echo "$IN_PROGRESS_LINE" | grep -oE '`[^`]+`' | tail -1 | tr -d '`')

# When multiple tasks share in-progress, list them all (active named first).
OTHER_IN_PROGRESS=$(printf '%s\n' "$ALL_IN_PROGRESS_IDS" | grep -vx "$TASK_ID" | paste -sd, - || true)

# --- Detect blocked transitional state ---
# BACKLOG.md says in-progress but the actual work unit file may say blocked.

if [ -n "$FILE_PATH" ] && [ -f "${WORKSPACE_ROOT}/${FILE_PATH}" ]; then
    FILE_STATUS=$(sed -nE 's/^## Status:[[:space:]]*([^[:space:]]+).*/\1/p' "${WORKSPACE_ROOT}/${FILE_PATH}" 2>/dev/null | head -1 || true)
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
    # Use jq (not python3): the hook may run with an asdf-shimmed PATH where
    # python3 is unconfigured; jq is a hard dependency of devbench and the
    # rest of the hook chain so its presence is invariant.
    BLOCK_COUNT=$(jq -r '.count // 0' "$STATE_FILE" 2>/dev/null || echo 0)
    FIRST_BLOCK_TS=$(jq -r --argjson fallback "$NOW" '.first_block_ts // $fallback' "$STATE_FILE" 2>/dev/null || echo "$NOW")
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
        PROGRESS_TS=$(echo "$LAST_PROGRESS_LINE" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}' || true)
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
        NEXT_STEP="Dispatch the 4 review_team reviewers directly for ${TASK_ID} (code_review, test_review, doc_review, changes_manifest), after writing a fresh unit-scoped round token via 'devbench review-token new ${TASK_ID}'."
    elif echo "$LAST_COMMENT" | grep -q "REVIEW_PASS.*code_review\|REVIEW_PASS.*test_review\|REVIEW_PASS.*doc_review\|REVIEW_PASS.*changes_manifest" 2>/dev/null; then
        LAST_ACTION="review pass"
        NEXT_STEP="Check if all 4 reviewers passed. If yes, invoke security-reviewer for ${TASK_ID}. If not, run remaining reviewers."
    elif echo "$LAST_COMMENT" | grep -q "REVIEW_FAIL" 2>/dev/null; then
        LAST_ACTION="review fail"
        NEXT_STEP="Re-run executor for ${TASK_ID} with prior feedback, then re-dispatch the 4 review_team reviewers."
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

EXTRA_IDS_SUFFIX=""
if [ -n "$OTHER_IN_PROGRESS" ]; then
    EXTRA_IDS_SUFFIX=" (also in-progress: ${OTHER_IN_PROGRESS})"
fi
if command -v uv >/dev/null 2>&1; then
    uv run devbench log "Stop hook blocked (${NEW_COUNT}/${MAX_BLOCKS}): ${TASK_ID} in-progress, last action: ${LAST_ACTION}${EXTRA_IDS_SUFFIX}" 2>/dev/null || true
fi

# --- Build block response JSON ---
# Note: this "decision: block" response is delivered to the NEXT turn, not
# the one that just ended. See the timing limitation note at the top of this
# script -- the in-process resume (E10-F1/E10-F2) is the real recovery path.

REASON_TEXT="Orchestration loop active. Task ${TASK_ID} is in-progress (file: ${FILE_PATH}). Last action: ${LAST_ACTION}.${EXTRA_IDS_SUFFIX} ${NEXT_STEP} Circuit breaker: ${NEW_COUNT}/${MAX_BLOCKS} blocks in ${ELAPSED}s window.${STALE_WARNING} Never stop between tasks."

BLOCK_JSON=$(jq -nc --arg reason "$REASON_TEXT" '{
    decision: "block",
    reason: $reason,
    hookSpecificOutput: {
        hookEventName: "Stop",
        additionalContext: $reason
    }
}')

# --- Diagnostic capture ---
# Records exactly what the hook emitted plus surrounding context, so a future
# hang can be post-mortemed from evidence rather than speculation. One file
# per invocation under <workspace>/.devbench/stop-hook-diag/.

DIAG_DIR="${WORKSPACE_ROOT}/.devbench/stop-hook-diag"
DIAG_FILE="${DIAG_DIR}/$(date -u +%Y%m%dT%H%M%SZ)-${TASK_ID:-no-task}.json"
mkdir -p "$DIAG_DIR" 2>/dev/null || true
# jq instead of python3: the hook may run with an asdf-shimmed PATH where
# python3 is unconfigured. Falling back silently to no-diag-file (as the
# previous python3 block did) hid the very root cause this hook is meant
# to surface.
jq -n \
    --arg ts "$(date -u +%FT%TZ)" \
    --arg task_id "${TASK_ID:-}" \
    --arg file_path "${FILE_PATH:-}" \
    --arg last_action "${LAST_ACTION:-}" \
    --argjson block_count "${NEW_COUNT}" \
    --argjson max_blocks "${MAX_BLOCKS}" \
    --argjson window_seconds "${WINDOW_SECONDS}" \
    --argjson elapsed_seconds "${ELAPSED}" \
    --arg stale_warning "${STALE_WARNING}" \
    --arg next_step "${NEXT_STEP}" \
    --argjson emitted_stdout "${BLOCK_JSON}" \
    '{ts:$ts, task_id:$task_id, file_path:$file_path, last_action:$last_action, block_count:$block_count, max_blocks:$max_blocks, window_seconds:$window_seconds, elapsed_seconds:$elapsed_seconds, stale_warning:$stale_warning, next_step:$next_step, emitted_stdout:$emitted_stdout}' \
    > "$DIAG_FILE" 2>/dev/null || true

# --- Emit block response to stdout ---

printf '%s\n' "$BLOCK_JSON"

exit 0
