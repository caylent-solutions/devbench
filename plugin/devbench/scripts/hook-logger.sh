#!/bin/bash

# Read the JSON input from stdin
INPUT=$(cat)

# Extract the hook event name
EVENT_NAME=$(echo "$INPUT" | jq -r '.hook_event_name')

# Log to workspace-scoped file
LOG_FILE="${JUDGE_WORKSPACE_ROOT}/hook-logs.jsonl"
echo "{\"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"event\": \"$EVENT_NAME\", \"input\": $INPUT}" >> "$LOG_FILE"

# Always allow the action to proceed
exit 0
