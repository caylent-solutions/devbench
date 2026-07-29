#!/usr/bin/env bash
# guard-review-supervisor-scope.sh -- PreToolUse hook: enforce read-only
# scope on the review-supervisor agent.
#
# Receives JSON on stdin (Claude Code PreToolUse contract):
#   { "tool_name": "Bash", "tool_input": { "command": "..." },
#     "agent_type": "devbench-orchestrate:review-supervisor", ... }
#
# This agent has no pipeline role post-ADR-33: the orchestrate skill
# dispatches the four review_team judges directly and aggregates their
# verdicts itself, so review-supervisor neither delegates nor reviews.
# It MUST NOT spawn subagents and MUST NOT mutate worktree, index, or
# filesystem state. Any Agent-tool invocation, and any state-changing
# Bash command (file deletion, git mutation, redirection to disk), is
# out of scope and is rejected here.
#
# This hook is a no-op for any agent_type other than
# "devbench-orchestrate:review-supervisor"; main-session and other-agent Bash calls
# are unaffected.
#
# Override mechanism (operator-only):
#   Set DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1 in the environment
#   to bypass this guard for one call. Override is logged with the matched
#   pattern + full command for an auditable trail.
#
# Exit 0  -> allowed (Claude proceeds)
# Exit 2  -> blocked (stderr becomes Claude's feedback)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)

# Without a Claude-supplied agent_type we cannot scope the rule, so the
# hook bows out (exit 0).
AGENT_TYPE=$(extract_field "$INPUT" "agent_type")
if [[ "$AGENT_TYPE" != "devbench-orchestrate:review-supervisor" ]]; then
  exit 0
fi

# Issue #118: the prior Bash-only guard let the supervisor escalate by
# spawning subagents (executor / git-ops) via the Agent tool. Block EVERY
# Agent-tool invocation. Issue #118 allow-listed the four review_team
# subagents because the supervisor fanned out to them itself; ADR-33 moved
# that fan-out to the orchestrate skill as a first-level dispatch, so the
# supervisor now dispatches nothing and the allowlist described a contract
# that no longer exists. The override env var still applies operator-wide.
TOOL_NAME=$(extract_field "$INPUT" "tool_name")
if [[ "$TOOL_NAME" == "Agent" ]]; then
  # Claude Code's PreToolUse Agent payload puts subagent_type under
  # tool_input. Some test fixtures pass it at top-level for convenience;
  # support both shapes.
  if command -v jq >/dev/null 2>&1; then
    SUBAGENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // .subagent_type // ""')
  else
    SUBAGENT=$(printf '%s' "$INPUT" | sed -nE 's/.*"subagent_type"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p')
  fi
  if [[ "${DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS:-0}" == "1" ]]; then
    printf 'guard-review-supervisor-scope: ALLOWED via DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1 (Agent subagent_type=%s)\n' "$SUBAGENT" >&2
    exit 0
  fi
  {
    printf 'guard-review-supervisor-scope: BLOCKED -- review-supervisor attempted to spawn subagent_type %s.\n' "$SUBAGENT"
    printf 'Reason: review-supervisor has no pipeline role (ADR-33) and may not spawn any subagent. The four review_team judges are dispatched directly by the orchestrate skill as first-level sub-agents; the documented pipeline is executor -> the 4 review_team judges -> security-reviewer -> git-ops -> mark-done.\n'
    printf 'Override: only an operator may set DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1 in the env to permit a one-off non-review-team subagent.\n'
  } >&2
  exit 2
fi

COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Patterns that mutate worktree, index, or filesystem state. Each pattern
# is anchored to a logical-command boundary (start of line, semicolon,
# pipeline, subshell, &&, ||) so quoted strings inside echo/log-comment
# bodies never trip the guard.
#
# Parallel arrays (pattern, name, fix) keep regex `|` characters from
# colliding with an IFS-based split delimiter.
BOUNDARY='(^|[;&|`(]|&&|\|\|)[[:space:]]*'

PATTERNS=(
  "${BOUNDARY}rm[[:space:]]"
  "${BOUNDARY}rmdir[[:space:]]"
  "${BOUNDARY}mv[[:space:]]"
  "${BOUNDARY}cp[[:space:]]"
  "${BOUNDARY}chmod[[:space:]]"
  "${BOUNDARY}chown[[:space:]]"
  "${BOUNDARY}chgrp[[:space:]]"
  "${BOUNDARY}touch[[:space:]]"
  "${BOUNDARY}mkdir[[:space:]]"
  "${BOUNDARY}truncate[[:space:]]"
  "${BOUNDARY}dd[[:space:]]"
  "${BOUNDARY}tee([[:space:]]|$)"
  "${BOUNDARY}sed[[:space:]]+([^|;&]*[[:space:]])?-i([[:space:]]|=|$)"
  "${BOUNDARY}awk[[:space:]]+([^|;&]*[[:space:]])?-i([[:space:]]|=|$)"
  "${BOUNDARY}git[[:space:]]+(-C[[:space:]]+[^[:space:]]+[[:space:]]+)?(add|commit|push|pull|fetch|merge|rebase|reset|restore|checkout|rm|mv|stash|clean|apply|am|filter-branch|update-ref|tag|notes|cherry-pick|revert)([[:space:]]|$)"
  "${BOUNDARY}git[[:space:]]+(-C[[:space:]]+[^[:space:]]+[[:space:]]+)?branch[[:space:]]+([^|;&]*[[:space:]])?-[dD]([[:space:]]|$)"
  "${BOUNDARY}git[[:space:]]+(-C[[:space:]]+[^[:space:]]+[[:space:]]+)?config[[:space:]]+([^|;&]*[[:space:]])?(--unset|--add|--replace-all|--rename-section|--remove-section)([[:space:]]|$)"
  "${BOUNDARY}find[[:space:]]+([^|;&]*[[:space:]])?-(exec|execdir|delete)([[:space:]]|$)"
  "${BOUNDARY}xargs[[:space:]]+([^|;&]*[[:space:]])?(rm|mv|cp|chmod|chown)([[:space:]]|$)"
)
NAMES=(
  'rm'
  'rmdir'
  'mv'
  'cp'
  'chmod'
  'chown'
  'chgrp'
  'touch'
  'mkdir'
  'truncate'
  'dd'
  'tee'
  'sed -i'
  'awk -i'
  'git mutation'
  'git branch -d/-D'
  'git config write'
  'find -exec / -delete'
  'xargs mutation'
)
FIXES=(
  "review agents are read-only; if a file genuinely needs deletion, escalate via 'devbench log-comment review-supervisor <id> ...' and let the operator clean up."
  "review agents are read-only; do not remove directories during review."
  "review agents are read-only; do not move files during review."
  "review agents are read-only; do not copy files during review."
  "review agents are read-only; permission changes belong to the executor or operator."
  "review agents are read-only; ownership changes are operator territory."
  "review agents are read-only; group changes are operator territory."
  "review agents are read-only; do not create files (timestamp updates included)."
  "review agents are read-only; do not create directories during review."
  "review agents are read-only; do not truncate files."
  "review agents are read-only; do not dd-write."
  "review agents are read-only; tee writes to disk -- pipe to a non-writing reader instead."
  "review agents are read-only; sed -i edits files in place. Use 'sed -n' (no -i) for read-only matching."
  "review agents are read-only; awk -i edits files in place. Drop the -i flag for read-only patterns."
  "review agents are read-only; do not mutate the index or worktree. Use 'git diff' / 'git status' / 'git log' / 'git show' / 'git ls-files' for inspection. Delegate any required mutation to the executor agent through a proposal."
  "review agents are read-only; branch deletion is not a review action."
  "review agents are read-only; git config writes are not a review action."
  "review agents are read-only; find -exec/-delete can mutate the filesystem. Use 'find' without -exec/-delete to enumerate paths, then process them in pure read-only steps."
  "review agents are read-only; xargs into mutating commands is forbidden. Pipe into read-only commands only."
)

block_with() {
  local name="$1" fix="$2"
  if [[ "${DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS:-0}" == "1" ]]; then
    printf 'guard-review-supervisor-scope: ALLOWED via DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1 (matched %s): %s\n' "$name" "$COMMAND" >&2
    exit 0
  fi
  {
    printf 'guard-review-supervisor-scope: BLOCKED -- review-supervisor agent attempted %s.\n' "$name"
    printf 'Reason: %s\n' "$fix"
    printf 'Command: %s\n' "$COMMAND"
    printf 'Override: only an operator may set DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1 in the env to permit one mutation. Reviewers must escalate via log-comment.\n'
  } >&2
  exit 2
}

for i in "${!PATTERNS[@]}"; do
  pattern=${PATTERNS[$i]}
  name=${NAMES[$i]}
  fix=${FIXES[$i]}
  if printf '%s' "$COMMAND" | grep -qE "$pattern"; then
    block_with "$name" "$fix"
  fi
done

# Redirection rules. Match against a quote-stripped form of the command so
# `>` characters inside single- or double-quoted argument strings (e.g.
# `echo 'config: a > b'`) do not false-positive. The strip is two passes
# (single quotes, then double quotes) using sed; this does not handle
# escaped quotes inside same-kind quotes (bash itself disallows that for
# single quotes, and the agent is unlikely to author `\"` inside `"..."`
# in a Bash tool call). After stripping, any surviving `>` followed by a
# non-`&` token is a real file-redirect.
STRIPPED=$(printf '%s' "$COMMAND" | sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g")
if printf '%s' "$STRIPPED" | grep -qE '[[:space:]]>[[:space:]]*[^&[:space:]]'; then
  block_with 'output redirection (>)' \
    "review agents are read-only; redirecting stdout to a file writes to disk. Print to stdout instead so Claude captures it."
fi
if printf '%s' "$STRIPPED" | grep -qE '[[:space:]]>>[[:space:]]'; then
  block_with 'output redirection (>>)' \
    "review agents are read-only; appending to a file writes to disk. Print to stdout instead."
fi

exit 0
