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
#   - Quota-error detection (AC-193-14): scans transcript for rate-limit
#     patterns and writes quota_pause.json when a match is found.

WORKSPACE_ROOT="${JUDGE_WORKSPACE_ROOT:-}"
BACKLOG_INDEX="${WORKSPACE_ROOT}/BACKLOG.md"
CONFIG_FILE="${WORKSPACE_ROOT}/backlog/config/devbench.yaml"

# ---------------------------------------------------------------------------
# Quota-error detection (AC-193-14)
# ---------------------------------------------------------------------------
# Read the Stop event payload from stdin.  The payload contains
# ``transcript_path`` which points to the current session transcript JSONL.
# Scan recent transcript content for quota-exhaustion text patterns.
# On a match, write quota_pause.json atomically to the workspace .devbench/
# directory (or the session-scoped path when DEVBENCH_SESSION_NAME is set).
#
# Scan order: we read all of stdin once and cache it; the transcript scan
# happens before the main in-progress check so a quota event is always
# recorded even when the circuit breaker later allows the stop.

_HOOK_STDIN=""
if [ -t 0 ]; then
    # stdin is a terminal (interactive shell, e.g. test runner without input) --
    # do not block waiting for input.
    true
else
    _HOOK_STDIN=$(cat 2>/dev/null || { echo >&2 "[ERROR] continue-orchestration: failed to read Stop event payload from stdin"; true; })
fi

_detect_and_write_quota_checkpoint() {
    local workspace_root="$1"
    local session_name="$2"
    local stdin_payload="$3"

    # Extract transcript_path from the Stop event payload.
    if [ -z "$stdin_payload" ]; then
        return 0
    fi

    local transcript_path
    transcript_path=$(printf '%s' "$stdin_payload" | jq -r '.transcript_path // ""' 2>/dev/null || { echo >&2 "[ERROR] continue-orchestration: jq failed to extract transcript_path from Stop event payload"; return 0; })

    if [ -z "$transcript_path" ] || [ ! -f "$transcript_path" ]; then
        return 0
    fi

    # Resolve the checkpoint path (session-scoped or workspace-root).
    local checkpoint_path
    if [ -n "$session_name" ]; then
        checkpoint_path="${workspace_root}/.devbench/sessions/${session_name}/.devbench/quota_pause.json"
    else
        checkpoint_path="${workspace_root}/.devbench/quota_pause.json"
    fi

    # If a checkpoint already exists, do not overwrite it.
    # The quota-watcher daemon is responsible for clearing the checkpoint.
    if [ -f "$checkpoint_path" ]; then
        return 0
    fi

    # Slurp the transcript file (JSONL) and scan for quota-error patterns.
    # Each line is a JSON object; we inspect the full text of each line for
    # any known quota-error signal substring.  Using grep for speed; jq would
    # be more precise but the text scan is safe here because all patterns are
    # distinctive enough to avoid false positives in normal transcript content.
    local transcript_text
    transcript_text=$(cat "$transcript_path" 2>/dev/null || { echo >&2 "[ERROR] continue-orchestration: failed to read transcript file: ${transcript_path}"; return 0; })

    if [ -z "$transcript_text" ]; then
        return 0
    fi

    # Determine which quota reason applies (first match wins, priority order
    # matches the detect_modes list in constants.py).
    local quota_reason=""

    # subscription_rate_limit: HTTP 429 / rate_limit / overloaded_error
    if printf '%s\n' "$transcript_text" | grep -qiE \
        "(rate_limit|Too Many Requests|overloaded_error|anthropic-ratelimit|HTTP 429|error 429|status.*429|429.*status)" \
        2>/dev/null; then
        quota_reason="subscription_rate_limit"
    # sdk_credit_exhausted: insufficient_quota
    elif printf '%s\n' "$transcript_text" | grep -qiE \
        "(insufficient_quota)" \
        2>/dev/null; then
        quota_reason="sdk_credit_exhausted"
    # bedrock_throttle: ThrottlingException / ServiceQuotaExceededException
    elif printf '%s\n' "$transcript_text" | grep -qE \
        "(ThrottlingException|ServiceQuotaExceededException)" \
        2>/dev/null; then
        quota_reason="bedrock_throttle"
    # api_billing_error: HTTP 402 (billing, without insufficient_quota already matched above)
    elif printf '%s\n' "$transcript_text" | grep -qiE \
        "(HTTP 402|402 Payment Required|payment required|billing)" \
        2>/dev/null; then
        quota_reason="api_billing_error"
    fi

    if [ -z "$quota_reason" ]; then
        return 0
    fi

    # Write the checkpoint atomically (temp-then-rename).
    local paused_at
    paused_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    local checkpoint_dir
    checkpoint_dir=$(dirname "$checkpoint_path")
    mkdir -p "$checkpoint_dir" 2>/dev/null || { echo >&2 "[ERROR] continue-orchestration: failed to create checkpoint directory: ${checkpoint_dir}"; return 0; }

    local tmp_file
    tmp_file="${checkpoint_path}.tmp.$$"

    if ! jq -n \
        --arg paused_at "$paused_at" \
        --arg reason "$quota_reason" \
        '{
            paused_at: $paused_at,
            reset_at: null,
            reason: $reason,
            raw_error: ("quota-pattern detected in transcript: " + $reason),
            in_flight_wu: null,
            in_flight_phase: null,
            completed_judges: [],
            pending_judges: [],
            stage_artefacts: {}
        }' > "$tmp_file" 2>/dev/null; then
        echo >&2 "[ERROR] continue-orchestration: jq failed to write quota checkpoint to ${tmp_file}"
        rm -f "$tmp_file" 2>/dev/null || true
        return 0
    fi
    if ! mv -f "$tmp_file" "$checkpoint_path" 2>/dev/null; then
        echo >&2 "[ERROR] continue-orchestration: failed to atomically rename ${tmp_file} to ${checkpoint_path}"
        rm -f "$tmp_file" 2>/dev/null || true
    fi
}

SESSION_NAME="${DEVBENCH_SESSION_NAME:-}"

# Run quota detection before the main in-progress check.
if [ -n "${WORKSPACE_ROOT:-}" ]; then
    _detect_and_write_quota_checkpoint "$WORKSPACE_ROOT" "$SESSION_NAME" "$_HOOK_STDIN"
fi

# Per-session circuit-breaker state file (AC-192-15):
# When DEVBENCH_SESSION_NAME is set, scope the state file to the named session
# so concurrent orchestrator sessions maintain independent block counters.
# When unset, fall back to the shared path for single-session (legacy) behaviour.
# SESSION_NAME was already assigned above for quota-checkpoint routing.
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

MAX_BLOCKS=$(_read_yaml_int "max_blocks" "5" "JUDGE_STOP_MAX_BLOCKS")
WINDOW_SECONDS=$(_read_yaml_int "window_seconds" "180" "JUDGE_STOP_WINDOW_SECONDS")
STALE_MINUTES=$(_read_yaml_int "stale_task_minutes" "120" "JUDGE_STOP_STALE_MINUTES")

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

EXTRA_IDS_SUFFIX=""
if [ -n "$OTHER_IN_PROGRESS" ]; then
    EXTRA_IDS_SUFFIX=" (also in-progress: ${OTHER_IN_PROGRESS})"
fi
if command -v uv >/dev/null 2>&1; then
    uv run devbench log "Stop hook blocked (${NEW_COUNT}/${MAX_BLOCKS}): ${TASK_ID} in-progress, last action: ${LAST_ACTION}${EXTRA_IDS_SUFFIX}" 2>/dev/null || true
fi

# --- Build block response JSON ---

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
