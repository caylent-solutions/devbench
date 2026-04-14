#!/usr/bin/env bash
# Start the autonomous backlog execution system.
# Usage: ./scripts/start.sh
#
# 1. Authenticates with GitHub (opens browser if needed)
# 2. Writes token to ~/.gh_token_env
# 3. Starts a background token refresher (every 4h)
# 4. Launches the orchestrator in the background
set -euo pipefail

# Required environment variable guard
required_vars=(JUDGE_CLAUDE_MODEL JUDGE_WORKSPACE_ROOT)
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Required environment variable $var is not set." >&2
    exit 1
  fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVBENCH_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_FILE="${JUDGE_LOG_FILE:-/tmp/backlog-run.log}"
TOKEN_FILE="${JUDGE_GH_TOKEN_FILE:-${HOME}/.gh_token_env}"

# Source cached token if available (persists across terminals)
if [[ -z "${GH_TOKEN:-}" && -f "$TOKEN_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$TOKEN_FILE"
fi

# Scopes are minimized to only what's needed.
# Notably admin:org and admin:enterprise are excluded to prevent accidental
# access to other organizations the user's account may belong to.
GH_SCOPES=(
    repo
    workflow
    read:org
    admin:repo_hook
    security_events
)

scope_flags=()
for scope in "${GH_SCOPES[@]}"; do
    scope_flags+=(-s "$scope")
done

if [[ -n "${GH_TOKEN:-}" ]]; then
    echo "=== Step 1: Using pre-configured GH_TOKEN ==="
    echo "Skipping gh auth (GH_TOKEN already set)."
    echo "export GH_TOKEN=\"${GH_TOKEN}\"" > "$TOKEN_FILE"
    echo "Token written to $TOKEN_FILE"
else
    echo "=== Step 1: GitHub Authentication ==="
    unset GH_TOKEN 2>/dev/null || true

    if gh auth status 2>&1 | grep -q "Logged in"; then
        echo "Already authenticated — using existing token."
        GH_TOKEN="$(gh auth token)"
    else
        echo "Not authenticated. Starting login..."
        gh auth login -h github.com "${scope_flags[@]}"
        GH_TOKEN="$(gh auth token)"
    fi

    echo "export GH_TOKEN=\"${GH_TOKEN}\"" > "$TOKEN_FILE"
    export GH_TOKEN
    echo "Token written to $TOKEN_FILE"
fi

echo ""
echo "=== Step 4: Starting token refresher (every 4h) ==="
nohup bash -c "
while true; do
    sleep 14400
    unset GH_TOKEN
    gh auth refresh -h github.com ${scope_flags[*]} 2>/dev/null || true
    echo \"export GH_TOKEN=\\\"\$(gh auth token)\\\"\" > $TOKEN_FILE
    echo \"[\$(date -u +%Y-%m-%dT%H:%M:%SZ)] token refreshed\" >> ${JUDGE_LOG_FILE:-/tmp/backlog-run.log}.token-refresh
done
" >/dev/null 2>&1 &
REFRESHER_PID=$!
echo "Token refresher PID: $REFRESHER_PID"

echo ""
echo "=== Step 5: Launching backlog orchestrator ==="
nohup bash -c "
source $TOKEN_FILE
cd $DEVBENCH_ROOT
uv run python -m devbench.execution.orchestrator
" > "$LOG_FILE" 2>&1 &
ORCHESTRATOR_PID=$!
echo "Orchestrator PID: $ORCHESTRATOR_PID"

# Give it a moment to check for immediate failures
sleep 2
if ! kill -0 "$ORCHESTRATOR_PID" 2>/dev/null; then
    echo ""
    echo "ERROR: Orchestrator exited immediately. Check log:"
    echo "  cat $LOG_FILE"
    exit 1
fi

echo ""
echo "=== Running ==="
echo "Log:    tail -f $LOG_FILE"
echo "Status: cd $DEVBENCH_ROOT && uv run python -m devbench.cli status"
echo "Stop:   kill $ORCHESTRATOR_PID $REFRESHER_PID"
