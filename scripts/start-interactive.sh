#!/usr/bin/env bash
# Launch the interactive autonomous backlog execution session.
# Usage: ./scripts/start-interactive.sh
#
# 1. Ensures GitHub auth with all required scopes
# 2. Starts a background token refresher (every 4h)
# 3. Launches Claude Code with the orchestrator prompt (interactive)
#
# Controls:
#   Escape      — pause
#   Type + Enter — give instructions while paused
#   Continue    — resume
#   Ctrl+C      — stop (progress saved in BACKLOG.md)
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
TOKEN_FILE="/tmp/gh_token_env"
PROMPTS_DIR="${JUDGE_PROMPTS_DIR:-$DEVBENCH_ROOT/prompts}"
PROMPT_FILE="$PROMPTS_DIR/orchestrator-prompt.md"

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

# --- Step 1: GitHub Auth ---
if [[ -n "${GH_TOKEN:-}" ]]; then
    echo "=== Using pre-configured GH_TOKEN ==="
    echo "Skipping gh auth (GH_TOKEN already set)."
    echo "export GH_TOKEN=\"${GH_TOKEN}\"" > "$TOKEN_FILE"
    echo "Token written to $TOKEN_FILE"
    echo ""
else
    echo "=== GitHub Authentication ==="
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
    echo ""
fi

# --- Step 3: Token refresher (always runs) ---
echo "=== Starting token refresher (every 4h) ==="
nohup bash -c "
while true; do
    sleep 14400
    unset GH_TOKEN
    gh auth refresh -h github.com ${scope_flags[*]} 2>/dev/null || true
    echo \"export GH_TOKEN=\\\"\$(gh auth token)\\\"\" > $TOKEN_FILE
    echo \"[\$(date -u +%Y-%m-%dT%H:%M:%SZ)] token refreshed\" >> /tmp/gh_token_refresh.log
done
" >/dev/null 2>&1 &
echo "Token refresher PID: $!"
echo ""

# --- Step 4: Verify prompt file ---
if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "ERROR: Orchestrator prompt not found: $PROMPT_FILE"
    exit 1
fi

# --- Step 5: Launch interactive Claude session ---
echo "=== Launching interactive Claude Code session ==="
echo ""
echo "Controls:"
echo "  Escape       — pause at any time"
echo "  Type + Enter — give instructions while paused"
echo "  'Continue'   — resume processing"
echo "  Ctrl+C       — stop (progress saved in BACKLOG.md)"
echo ""
echo "---"
echo ""

cd "$JUDGE_WORKSPACE_ROOT"

echo "Working directory: $(pwd)"
echo "Prompt file: $PROMPT_FILE ($(wc -c < "$PROMPT_FILE") bytes)"
echo "Launching claude..."
echo ""

exec claude \
    --dangerously-skip-permissions \
    --model "$JUDGE_CLAUDE_MODEL" \
    --append-system-prompt "$(cat "$PROMPT_FILE")" \
    "Begin autonomous backlog execution. Read BACKLOG.md and process all work units."
