#!/usr/bin/env bash
# guard-destructive-git.sh -- PreToolUse hook: block destructive git
# commands per CLAUDE.md "Never Bypass Hooks" + "Git Safety Protocol".
#
# Receives JSON on stdin:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Blocks the following patterns (case-insensitive, anchored to a `git ` token
# so substrings inside other words and quoted strings do not false-positive):
#
#   - git rm --cached            (queues deletion on tracked files; misused as unstage)
#   - git reset --hard           (discards uncommitted work)
#   - git checkout -- <path>     (destructive worktree overwrite; superseded by git restore)
#   - git checkout .             (same, bulk form)
#   - git clean -f / -fd / -fdx  (deletes untracked files unconditionally)
#   - git push --force / -f      (rewrites remote history)
#   - git branch -D              (force-deletes a branch with unmerged commits)
#   - git filter-branch          (rewrites history)
#   - git update-ref -d          (force-removes a ref)
#   - git rebase -i              (interactive; requires terminal not available to agents)
#   - git commit --amend         (rewrites the last commit; CLAUDE.md prefers a new commit)
#   - --no-verify / --no-gpg-sign on any git command (bypasses hooks/signing)
#
# Override mechanism (single source of truth, deterministic):
#
#   Set DEVBENCH_ALLOW_DESTRUCTIVE_GIT=1 in the environment of the agent
#   process to permit a single intentional destructive call. The override
#   is logged with the matched pattern and the full command so the trail is
#   auditable. Operators (not agents) set this for recovery operations.
#
# Exit 0  -> allowed (Claude proceeds)
# Exit 2  -> blocked (stderr becomes Claude's feedback; the agent sees
#            the actionable message and must reroute)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Only consider lines that contain a `git` token. The matcher anchors on a
# leading word boundary so strings like 'gitlab' or 'digit' never trigger.
if ! printf '%s' "$COMMAND" | grep -qE '(^|[^[:alnum:]_-])git([[:space:]]|$)'; then
  exit 0
fi

# Each rule is a (pattern, name, fix) tuple. The pattern is a POSIX ERE
# fragment that must appear after a `git ` token within the command.
# Parallel arrays so regex patterns can contain `|` without colliding with
# the field delimiter that an IFS-based split would impose.
PATTERNS=(
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+rm[[:space:]]+([^;&]*[[:space:]])?--cached'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?reset[[:space:]]+([^;&]*[[:space:]])?--hard'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?checkout[[:space:]]+--[[:space:]]+'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?checkout[[:space:]]+\.([[:space:]]|$)'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?clean[[:space:]]+-[a-zA-Z]*f'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?push[[:space:]]+([^;&]*[[:space:]])?(--force([[:space:]]|=|$)|-f([[:space:]]|$))'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?branch[[:space:]]+([^;&]*[[:space:]])?-D([[:space:]]|$)'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?filter-branch'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?update-ref[[:space:]]+([^;&]*[[:space:]])?-d'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?rebase[[:space:]]+([^;&]*[[:space:]])?-i([[:space:]]|$)'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?commit[[:space:]]+([^;&]*[[:space:]])?--amend'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?--no-verify'
  '(^|[;&|`(]|&&|\|\|)[[:space:]]*git[[:space:]]+([^;&]*[[:space:]])?--no-gpg-sign'
)
NAMES=(
  'git rm --cached'
  'git reset --hard'
  'git checkout -- <path>'
  'git checkout .'
  'git clean -f'
  'git push --force'
  'git branch -D'
  'git filter-branch'
  'git update-ref -d'
  'git rebase -i'
  'git commit --amend'
  '--no-verify on git command'
  '--no-gpg-sign on git command'
)
FIXES=(
  "use 'git restore --staged <path>' to unstage; --cached on tracked files queues a deletion in the next commit."
  "use 'git restore --staged --worktree <path>' for path-scoped reverts; --hard discards every uncommitted change in the worktree."
  "use 'git restore <path>' instead; checkout -- overwrites the worktree without confirmation."
  "use 'git restore .' (per-path enumeration preferred); checkout . is a bulk worktree overwrite."
  "enumerate untracked files and 'rm <path>' the ones you authored; never bulk-clean -- it can delete other agents' staged work."
  "never force-push from an agent; if you really need to overwrite remote history, escalate to the operator via log-comment."
  "use 'git branch -d <name>' (which only succeeds when merged); -D force-deletes branches with unmerged commits."
  "history rewriting is forbidden in the agent context."
  "deleting refs from an agent is forbidden; if a ref needs cleanup, escalate to the operator."
  "interactive rebase requires a terminal; if a rebase is genuinely needed, use the non-interactive form with explicit args."
  "create a NEW commit instead -- amend rewrites the previous commit, which is forbidden under the Git Safety Protocol unless the user explicitly requested it."
  "hook-skipping is forbidden by CLAUDE.md (Engineering Standards)."
  "signing-skipping is forbidden by CLAUDE.md (Engineering Standards)."
)

for i in "${!PATTERNS[@]}"; do
  pattern=${PATTERNS[$i]}
  name=${NAMES[$i]}
  fix=${FIXES[$i]}
  if printf '%s' "$COMMAND" | grep -qE "$pattern"; then
    if [[ "${DEVBENCH_ALLOW_DESTRUCTIVE_GIT:-0}" == "1" ]]; then
      printf 'guard-destructive-git: ALLOWED via DEVBENCH_ALLOW_DESTRUCTIVE_GIT=1 (matched %s): %s\n' "$name" "$COMMAND" >&2
      exit 0
    fi
    {
      printf 'guard-destructive-git: BLOCKED -- %s is forbidden.\n' "$name"
      printf 'Reason: %s\n' "$fix"
      printf 'Command: %s\n' "$COMMAND"
      printf 'Override: only an operator may set DEVBENCH_ALLOW_DESTRUCTIVE_GIT=1 in the env to permit a single recovery call. Agents must escalate via log-comment.\n'
    } >&2
    exit 2
  fi
done

exit 0
