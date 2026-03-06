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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JUDGES_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE_DIR="$(dirname "$JUDGES_DIR")"
TOKEN_FILE="/tmp/gh_token_env"
PROMPT_FILE="$JUDGES_DIR/orchestrator-prompt.md"

# Source cached token if available (persists across terminals)
if [[ -z "${GH_TOKEN:-}" && -f "$TOKEN_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$TOKEN_FILE"
fi

# Restrict GitHub operations to this org. Unset to allow any org in allow-list.
export JUDGE_GH_ORG="${JUDGE_GH_ORG:-caylent-solutions}"
# Scopes are minimized to only what's needed for caylent-solutions repos.
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

cd "$WORKSPACE_DIR"

echo "Working directory: $(pwd)"
echo "Prompt file: $PROMPT_FILE ($(wc -c < "$PROMPT_FILE") bytes)"
echo "Launching claude..."
echo ""

if [[ -z "${JUDGE_CLAUDE_MODEL:-}" ]]; then
    echo "ERROR: JUDGE_CLAUDE_MODEL environment variable is not set."
    echo "Set it to a valid model identifier (e.g. us.anthropic.claude-sonnet-4-6-v1)."
    exit 1
fi

exec claude \
    --dangerously-skip-permissions \
    --model "$JUDGE_CLAUDE_MODEL" \
    --append-system-prompt "$(cat "$PROMPT_FILE")" \
    "Begin autonomous backlog execution. Read BACKLOG.md and process all work units."
