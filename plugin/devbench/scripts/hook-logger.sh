#!/bin/bash

# Read the JSON input from stdin
INPUT=$(cat)

# Extract the hook event name
EVENT_NAME=$(echo "$INPUT" | jq -r '.hook_event_name')

# Phase 11 (E230): every event records the orchestrator's session id
# alongside the standard payload. The launch command sets
# JUDGE_ORCHESTRATOR_SESSION_ID; ad-hoc Claude sessions started in side
# panes leave it unset. ``devbench hook-tail`` reads this field to
# scope its output to the orchestrator's session, so a mid-run
# investigation pane no longer pollutes the audit stream.
ORCHESTRATOR_SESSION="${JUDGE_ORCHESTRATOR_SESSION_ID:-}"

# Log to workspace-scoped file
LOG_FILE="${JUDGE_WORKSPACE_ROOT}/hook-logs.jsonl"
echo "{\"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"event\": \"$EVENT_NAME\", \"orchestrator_session\": \"$ORCHESTRATOR_SESSION\", \"input\": $INPUT}" >> "$LOG_FILE"

# Always allow the action to proceed
exit 0
