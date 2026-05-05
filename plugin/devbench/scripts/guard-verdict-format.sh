#!/usr/bin/env bash
# guard-verdict-format.sh -- PreToolUse hook: validate 'uv run devbench log-verdict' calls.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Bash", "tool_input": { "command": "..." } }
#
# Only intercepts commands matching: uv run devbench log-verdict <judge> <id> <verdict> [feedback]
#
# Validation rules:
#   - verdict must be 'pass' or 'fail'
#   - judge name must be a known identifier
#   - feedback must be non-empty when verdict is 'fail'
#
# Passthroughs (exit 0 without validating):
#   - any '--help' / '-h' anywhere after 'log-verdict' → let the CLI print help
#   - shell meta-tokens (|, >, 2>&1, etc.) end the positional-arg window so
#     redirections and pipes are not mistaken for judge/unit_id/verdict args
#
# Exit 0  → allowed (Claude proceeds)
# Exit 2  → blocked (stderr becomes Claude's feedback)

set -euo pipefail

KNOWN_JUDGES=(
  "code_review"
  "test_review"
  "doc_review"
  "changes_manifest"
  "executor"
  "security_review"
  "blocker_resolver"
  "manifest_amender"
  "task_factory"
)

# Canonical reviewer judges -- only these 5 names satisfy the done-gate
# in BacklogManager._last_round_all_passed. Mirrors REVIEW_JUDGE_NAMES |
# SECURITY_JUDGE_NAMES in src/devbench/constants.py. Used to scope the
# executor restriction below (the executor is an authoring agent, not a
# reviewer; it must not write canonical reviewer verdicts).
CANONICAL_REVIEWER_JUDGES=(
  "code_review"
  "test_review"
  "doc_review"
  "changes_manifest"
  "security_review"
)

EXPECTED_ORDER="log-verdict <judge> <unit-id> <verdict> [feedback]"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
COMMAND=$(extract_command "$INPUT")
decode_json_escapes COMMAND

AGENT_TYPE=$(extract_field "$INPUT" "agent_type")

# No command to inspect -- allow.
if [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Only intercept 'uv run devbench log-verdict' calls.
if ! printf '%s' "$COMMAND" | grep -qE '(^|[[:space:]])uv[[:space:]]+run[[:space:]]+devbench[[:space:]]+log-verdict([[:space:]]|$)'; then
  exit 0
fi

# Parse arguments from the command string using pure-bash word
# splitting. Earlier versions used ``python3 -c shlex.split`` which
# silently returned no tokens whenever asdf shims could not resolve
# python3 in the hook's cwd; the legitimate review-team verdicts then
# tripped the "missing required argument" branch and got blocked.
# Using ``read -ra`` keeps the splitting in-shell, which is sufficient
# because the hook only needs to find positional words; quote-aware
# parsing was overkill (verdicts are simple identifiers, not free-form
# shell). Single-quote-wrapped feedback is collapsed by the shell's
# IFS split into multiple bash words, so we re-join words 4..N back
# into FEEDBACK after stripping the surrounding quote characters.
META_TOKENS=(\| \|\| \&\& \; \& \< \<\< \> \>\> 2\> 1\> 2\>\&1 \>\& \&\>)
HELP_FLAGS=(--help -h)

# Tokenise COMMAND into an array via the shell's word-splitting on
# whitespace. Single-quoted strings stay together because bash-array
# parsing treats them as a single field when piped through eval-safe
# patterns; we use ``read -ra`` here to avoid eval entirely.
read -ra TOKENS <<< "$COMMAND"

# Find the position of the "log-verdict" subcommand.
LV_IDX=-1
for i in "${!TOKENS[@]}"; do
  if [[ "${TOKENS[$i]}" == "log-verdict" ]]; then
    LV_IDX=$i
    break
  fi
done

if (( LV_IDX < 0 )); then
  # No log-verdict invocation found (handled by the earlier intercept
  # check, but defensive against future hook chain reordering).
  exit 0
fi

# Collect positional args after log-verdict, stopping at shell meta-tokens.
ARGS=()
HELP_SEEN=""
for (( i = LV_IDX + 1; i < ${#TOKENS[@]}; i++ )); do
  tok="${TOKENS[$i]}"
  is_meta=0
  for m in "${META_TOKENS[@]}"; do
    if [[ "$tok" == "$m" ]]; then is_meta=1; break; fi
  done
  if (( is_meta == 1 )); then break; fi
  is_help=0
  for h in "${HELP_FLAGS[@]}"; do
    if [[ "$tok" == "$h" ]]; then is_help=1; break; fi
  done
  if (( is_help == 1 )); then HELP_SEEN="HELP"; fi
  ARGS+=("$tok")
done

ARG_COUNT="${#ARGS[@]}"
JUDGE="${ARGS[0]:-}"
UNIT_ID="${ARGS[1]:-}"
VERDICT="${ARGS[2]:-}"
# Re-join the remaining tokens for FEEDBACK; strip a surrounding pair
# of single or double quotes if the operator wrapped the feedback so
# the bash-word splitter treated the quotes as part of the word.
if (( ARG_COUNT > 3 )); then
  FEEDBACK="${ARGS[*]:3}"
else
  FEEDBACK=""
fi
FEEDBACK="${FEEDBACK#\'}"
FEEDBACK="${FEEDBACK%\'}"
FEEDBACK="${FEEDBACK#\"}"
FEEDBACK="${FEEDBACK%\"}"

# --- Passthrough: --help / -h lets the CLI print usage without hook interference ---
if [[ "$HELP_SEEN" == "HELP" ]]; then
  exit 0
fi

# --- Require at least 3 positional args (judge, unit-id, verdict) ---
if (( ARG_COUNT < 3 )); then
  # Quote each captured arg so empty ones are visible.
  GOT="(none)"
  if (( ARG_COUNT > 0 )); then
    GOT=""
    for a in "$JUDGE" "$UNIT_ID" "$VERDICT"; do
      [[ -n "$a" ]] && GOT="${GOT:+$GOT }'$a'"
    done
  fi
  echo "guard-verdict-format: missing required argument(s); got ${ARG_COUNT} of 3 required." >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Got: ${GOT}" >&2
  exit 2
fi

# --- Validate judge name ---
JUDGE_KNOWN=0
for known in "${KNOWN_JUDGES[@]}"; do
  if [[ "$JUDGE" == "$known" ]]; then
    JUDGE_KNOWN=1
    break
  fi
done

if [[ "$JUDGE_KNOWN" -eq 0 ]]; then
  echo "guard-verdict-format: unknown judge name '${JUDGE}'." >&2
  echo "Known judges: ${KNOWN_JUDGES[*]}" >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Fix: use a valid judge identifier as the FIRST positional argument." >&2
  exit 2
fi

# --- Validate verdict value ---
VERDICT_LOWER=$(printf '%s' "$VERDICT" | tr '[:upper:]' '[:lower:]')
if [[ "$VERDICT_LOWER" != "pass" && "$VERDICT_LOWER" != "fail" ]]; then
  echo "guard-verdict-format: invalid verdict '${VERDICT}' -- must be 'pass' or 'fail'." >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Fix: use 'pass' or 'fail' as the THIRD positional argument." >&2
  exit 2
fi

# --- Validate feedback is non-empty when verdict is 'fail' ---
if [[ "$VERDICT_LOWER" == "fail" && -z "${FEEDBACK// /}" ]]; then
  echo "guard-verdict-format: feedback is required when verdict is 'fail' but was empty." >&2
  echo "Expected positional order: ${EXPECTED_ORDER}" >&2
  echo "Fix: provide a non-empty feedback message as the final argument to log-verdict." >&2
  exit 2
fi

# --- Scope: executor must not write canonical reviewer verdicts ---
# The executor is an authoring agent, not a reviewer. Review-team
# verdicts (code_review, test_review, doc_review, changes_manifest,
# security_review) are written by review-supervisor and
# security-reviewer; the executor self-attesting them confuses the
# audit trail and risks racing the actual reviewers' verdicts in the
# done-gate's "most recent round" bookkeeping. The audit-only
# ``executor`` judge name remains allowed (logs progress + signals
# completion) -- the restriction is specifically about the executor
# claiming to BE a reviewer.
if [[ "$AGENT_TYPE" == "devbench:executor" ]]; then
  for canonical in "${CANONICAL_REVIEWER_JUDGES[@]}"; do
    if [[ "$JUDGE" == "$canonical" ]]; then
      echo "guard-verdict-format: BLOCKED -- the executor agent must not write canonical reviewer verdicts." >&2
      echo "Judge attempted: '${JUDGE}' (one of the 5 canonical reviewer judges)." >&2
      echo "Reason: review-team verdicts belong to review-supervisor / security-reviewer." >&2
      echo "Fix: write a 'log-comment' for narrative status, or a 'log-verdict executor <id> <pass|fail> <feedback>' for audit-only progress (the 'executor' judge is on the allowlist but does NOT satisfy the done-gate)." >&2
      exit 2
    fi
  done
fi

exit 0
