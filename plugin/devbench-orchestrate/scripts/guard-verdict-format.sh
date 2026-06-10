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
#   - canonical reviewer judges require agent_type to be one of the allowed
#     reviewer agent types: the four review_team reviewers, which Claude Code
#     namespaces by subdirectory as devbench-orchestrate:review_team:code-reviewer,
#     :review_team:test-reviewer, :review_team:doc-reviewer,
#     :review_team:changes-manifest (the REGISTERED form; ADR-28 postmortem),
#     plus devbench-orchestrate:security-reviewer,
#     devbench-orchestrate:iac-deploy-reviewer, and the deprecated
#     devbench-orchestrate:review-supervisor. The flat (no review_team: infix)
#     forms are kept as defensive cross-version coverage. (default-deny H3)
#   - canonical reviewer judges also require a per-round token FILE at
#     <workspace>/.devbench/review-round-token (written by
#     `devbench review-token new <unit-id>`, removed by `... clear`; ADR-29) that
#     exists, is non-empty, AND is scoped to the unit under review (begins with
#     "<unit-id>-") so a stale leftover token cannot satisfy a verdict for a
#     different unit (H3 round-awareness). The workspace root is resolved from
#     DEVBENCH_WORKSPACE_ROOT. This file replaces the former
#     DEVBENCH_REVIEW_ROUND_TOKEN env var entirely.
#
# Passthroughs (exit 0 without validating):
#   - any '--help' / '-h' anywhere after 'log-verdict' -- let the CLI print help
#   - shell meta-tokens (|, >, 2>&1, etc.) end the positional-arg window so
#     redirections and pipes are not mistaken for judge/unit_id/verdict args
#
# Exit 0  -- allowed (Claude proceeds)
# Exit 2  -- blocked (stderr becomes Claude's feedback)

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
  "iac_review"
)

# Canonical reviewer judges -- done-gate-satisfying reviewer verdicts. The
# first 5 (code_review, test_review, doc_review, changes_manifest,
# security_review) mirror REVIEW_JUDGE_NAMES | SECURITY_JUDGE_NAMES (the
# always-on core) in src/devbench/constants.py and satisfy the done-gate
# unconditionally. ``iac_review`` is an optional specialty reviewer
# (OPTIONAL_JUDGE_NAMES) whose verdict satisfies the done-gate only when the
# unit's Verification contract makes it applicable AND the judge is enabled;
# it is still a default-deny canonical verdict, written only by the designated
# reviewer agent types and only when the per-round token file is present and
# unit-scoped (H3 default-deny; see the token-file check below).
CANONICAL_REVIEWER_JUDGES=(
  "code_review"
  "test_review"
  "doc_review"
  "changes_manifest"
  "security_review"
  "iac_review"
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

# --- Default-deny: canonical reviewer judges require an allowed reviewer agent type ---
# H3: The canonical reviewer judges (the always-on core 5 -- code_review,
# test_review, doc_review, changes_manifest, security_review -- plus the
# optional specialty judge iac_review) may ONLY be written by the designated
# reviewer agent types:
#   - devbench-orchestrate:code-reviewer    (review_team, direct-dispatched)
#   - devbench-orchestrate:test-reviewer    (review_team, direct-dispatched)
#   - devbench-orchestrate:doc-reviewer     (review_team, direct-dispatched)
#   - devbench-orchestrate:changes-manifest (review_team, direct-dispatched)
#   - devbench-orchestrate:security-reviewer
#   - devbench-orchestrate:iac-deploy-reviewer
#   - devbench-orchestrate:review-supervisor (deprecated; ADR-28)
# The four review_team reviewers were added when ADR-28 flattened the review
# pipeline: the orchestrate skill now dispatches them directly (first-level)
# instead of via review-supervisor, because the Claude Agent SDK forbids a
# sub-agent from spawning sub-agents. Each reviewer therefore presents its own
# agent_type and must be allowlisted to write its canonical verdict.
# Every other agent type -- including the executor, manifest-amender, and any
# absent or spoofed agent_type -- is blocked from canonical verdicts. The
# audit-only non-canonical judge names (executor, blocker_resolver,
# manifest_amender, task_factory) remain available to any agent type.
#
# H3 also requires the per-round token FILE (see header; ADR-29) to be present,
# non-empty, AND scoped to the unit under review (prefix "<unit-id>-") for a
# canonical verdict. A spoofed agent_type alone is insufficient -- the injected
# token provides a second factor that a rogue subagent cannot forge without the
# orchestrator's cooperation, and the unit-id scoping ensures a stale leftover
# token from a prior unit's round cannot satisfy this unit's verdict.
IS_CANONICAL_JUDGE=0
for canonical in "${CANONICAL_REVIEWER_JUDGES[@]}"; do
  if [[ "$JUDGE" == "$canonical" ]]; then
    IS_CANONICAL_JUDGE=1
    break
  fi
done

if (( IS_CANONICAL_JUDGE == 1 )); then
  # Check agent_type is an allowed reviewer.
  #
  # Claude Code namespaces a plugin sub-agent by its subdirectory, so the four
  # review_team reviewers (agents/review_team/<name>.md) present at runtime as
  # `devbench-orchestrate:review_team:<name>` -- the `review_team:` infix is the
  # REGISTERED, load-bearing form (ADR-28 postmortem). The flat
  # `devbench-orchestrate:<name>` forms are retained as defensive cross-version
  # coverage in case a future Claude Code release stops infixing the subdir;
  # they are harmless when unused (no agent resolves to them today).
  ALLOWED_REVIEWER_AGENT_TYPES=(
    "devbench-orchestrate:review_team:code-reviewer"
    "devbench-orchestrate:review_team:test-reviewer"
    "devbench-orchestrate:review_team:doc-reviewer"
    "devbench-orchestrate:review_team:changes-manifest"
    "devbench-orchestrate:security-reviewer"
    "devbench-orchestrate:iac-deploy-reviewer"
    "devbench-orchestrate:review-supervisor"
    "devbench-orchestrate:code-reviewer"
    "devbench-orchestrate:test-reviewer"
    "devbench-orchestrate:doc-reviewer"
    "devbench-orchestrate:changes-manifest"
  )
  IS_ALLOWED_REVIEWER=0
  for allowed in "${ALLOWED_REVIEWER_AGENT_TYPES[@]}"; do
    if [[ "$AGENT_TYPE" == "$allowed" ]]; then
      IS_ALLOWED_REVIEWER=1
      break
    fi
  done

  if (( IS_ALLOWED_REVIEWER == 0 )); then
    echo "guard-verdict-format: BLOCKED -- canonical reviewer verdict requires an allowed reviewer agent type." >&2
    echo "Judge attempted: '${JUDGE}' (a canonical reviewer judge)." >&2
    echo "Agent type presented: '${AGENT_TYPE:-<absent>}'." >&2
    echo "Allowed agent types for canonical verdicts: the four review_team reviewers (devbench-orchestrate:code-reviewer, :test-reviewer, :doc-reviewer, :changes-manifest), devbench-orchestrate:security-reviewer, devbench-orchestrate:iac-deploy-reviewer, devbench-orchestrate:review-supervisor." >&2
    echo "Fix: only the four review_team reviewers, the security-reviewer, the iac-deploy-reviewer, and the (deprecated) review-supervisor may write canonical reviewer verdicts." >&2
    exit 2
  fi

  # H3 second factor: a per-round, unit-scoped token (ADR-29). The transport is
  # a FILE -- <workspace>/.devbench/review-round-token -- written by
  # `devbench review-token new <unit-id>` before each review round and removed
  # by `devbench review-token clear` after it. The file replaces the former
  # DEVBENCH_REVIEW_ROUND_TOKEN env var entirely (which was never implemented in
  # code and twice failed: a stale shell.env value masked a missing injection,
  # then a later run wrote it where the hook never read it). The workspace root
  # comes from the stable DEVBENCH_WORKSPACE_ROOT (set once by `devbench start`),
  # not from any per-round env -- so there is no per-round env to go stale.
  REVIEW_TOKEN_WS="${DEVBENCH_WORKSPACE_ROOT:-}"
  if [[ -z "$REVIEW_TOKEN_WS" ]]; then
    echo "guard-verdict-format: BLOCKED -- cannot locate the review-round token: DEVBENCH_WORKSPACE_ROOT is unset." >&2
    echo "Judge attempted: '${JUDGE}' (a canonical reviewer judge)." >&2
    echo "Reason: the guard reads <workspace>/.devbench/review-round-token; without the workspace root it cannot verify the H3 second factor, so it fails closed." >&2
    echo "Fix: run reviewers under an orchestrator that exports DEVBENCH_WORKSPACE_ROOT." >&2
    exit 2
  fi
  REVIEW_TOKEN_FILE="${REVIEW_TOKEN_WS%/}/.devbench/review-round-token"
  if [[ ! -f "$REVIEW_TOKEN_FILE" ]]; then
    echo "guard-verdict-format: BLOCKED -- canonical reviewer verdict requires a per-round token file." >&2
    echo "Judge attempted: '${JUDGE}' (a canonical reviewer judge)." >&2
    echo "Agent type: '${AGENT_TYPE}'." >&2
    echo "Reason: ${REVIEW_TOKEN_FILE} does not exist; the orchestrate skill writes it via 'devbench review-token new <unit-id>' before dispatching reviewers each round. Its absence indicates the verdict is not originating from an orchestrated review round." >&2
    echo "Fix: ensure the orchestrate skill runs 'devbench review-token new ${UNIT_ID}' before dispatching this unit's reviewers." >&2
    exit 2
  fi
  REVIEW_TOKEN_VALUE="$(tr -d '\r\n' < "$REVIEW_TOKEN_FILE")"
  if [[ -z "$REVIEW_TOKEN_VALUE" ]]; then
    echo "guard-verdict-format: BLOCKED -- the review-round token file is empty (${REVIEW_TOKEN_FILE})." >&2
    echo "Judge attempted: '${JUDGE}' (a canonical reviewer judge)." >&2
    echo "Fix: re-run 'devbench review-token new ${UNIT_ID}' to write a fresh token." >&2
    exit 2
  fi

  # Round-awareness (H3 hardening): the token MUST be scoped to the unit being
  # reviewed (prefix "<unit-id>-"), so a leftover token from a *different* unit's
  # round can never satisfy this unit's canonical verdict -- the exact staleness
  # that masked the E9-F1-S1-T5 incident.
  if [[ "$REVIEW_TOKEN_VALUE" != "${UNIT_ID}-"* ]]; then
    echo "guard-verdict-format: BLOCKED -- the review-round token is not scoped to unit '${UNIT_ID}'." >&2
    echo "Judge attempted: '${JUDGE}' (a canonical reviewer judge) for unit '${UNIT_ID}'." >&2
    echo "Agent type: '${AGENT_TYPE}'." >&2
    echo "Reason: the token must begin with '${UNIT_ID}-' (format: <unit-id>-r<n>-<rand>); a token scoped to a different unit indicates a stale/leftover token, not a fresh per-round token for this unit." >&2
    echo "Fix: run 'devbench review-token new ${UNIT_ID}' before dispatching this unit's reviewers." >&2
    exit 2
  fi
fi

exit 0
