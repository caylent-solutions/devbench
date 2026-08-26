#!/usr/bin/env bash
# assert-shared-file-impact.sh -- PostToolUse hook: block silent progression
# when `devbench check-shared-file-impact` reports a shared/high-fan-in file
# was touched and the full-suite regression gate did not pass, AND fail
# CLOSED (block) when this hook cannot determine that verdict at all (spec
# 3.5, 4.6, finding 318-D13).
#
# Issue caylent-solutions/devbench-internal-backlog#13 (shared-file
# full-suite regression gate). `devbench check-shared-file-impact
# <unit-id>` is a no-op (exit 0) unless the task's diff touches a file
# matching the target repo's `gates.repos.<repo>.shared_file_impact.patterns`
# (devbench.yaml); when it does match, it runs the FULL test suite and
# diffs the failure set against a stored baseline, exiting non-zero on
# NEWLY introduced failures attributable to the unit's own scope
# (pre-existing/flaky failures, and a new failure the unit's own scope
# cannot be attributed to, never block -- spec 4.3) or when the gate could
# not evaluate a baseline at all.
#
# ROUND-5 REDESIGN (spec 4.6 finding 318-D13, code_review round-4
# JUDGEMENT): four prior review rounds each replaced one sed/jq heuristic
# for re-deriving this hook's verdict from the Claude Code PostToolUse
# payload -- first from a nonexistent `tool_response.exit_code` field, then
# from `tool_input.command` (a bare substring test, then a quoted-region
# deletion matcher, then a tokenised match still defeated by `bash -lc`
# style wrapper forms and an apostrophe-sandwich quoting edge case) and
# `tool_response.stdout` (a tiered JSON-document scan defeated by a
# decapitated block fragment coexisting with an unrelated complete
# document). Every one of those defeats shares a root cause: the hook was
# re-parsing an AGENT-AUTHORED shell string or a COMPOSED stdout string it
# did not control, with no way to prove the parsed result was actually this
# gate's own verdict. This rewrite removes that entire re-parsing surface:
# `devbench check-shared-file-impact` (`cmd_check_shared_file_impact` in
# `src/devbench/cli.py`, ref) now persists its own verdict to a plain-text
# record file as the very first thing it does (status `"pending"`,
# overwritten with `"pass"` or `"block"` only on a clean exit path -- see
# `_write_shared_file_impact_verdict`'s docstring), and this hook's ENTIRE
# job is reading that ONE record back. No `tool_input.command` or
# `tool_response` field is read or needed any more; `_hook_lib.sh` (ref) is
# intentionally NOT sourced, since every helper it exposes exists to pull a
# field out of the PostToolUse payload, and this script no longer reads the
# payload at all (spec 4.6 AC-4's "every extracted field goes through
# `_hook_lib.sh`, no inline jq/sed extraction block" is satisfied
# vacuously: zero payload fields are extracted). A defensive
# `hook_event_name`/`tool_name` sanity check was evaluated and deliberately
# REJECTED: gating the record check on any successfully-extracted payload
# field reintroduces exactly the failure mode this redesign exists to
# remove -- an empty or truncated stdin would silently ALLOW past a genuine
# `"block"` record instead of blocking, because the field extraction itself
# comes back empty. hooks.json's own `PostToolUse`/`Bash` matcher (ref)
# already restricts which payloads reach this script at all; reading the
# payload defensively inside the script would only add a second,
# WEAKER-not-stronger layer.
#
# AC-5 interpretive note (round-5 finding E2): AC-5's literal wording ("a
# payload with a missing exit code, a missing command or unparseable JSON
# makes the hook exit 2") and the Definition of Done's "sources
# `_hook_lib.sh`" line both describe the PAYLOAD-PARSING mechanism this
# redesign deliberately removes -- there is no longer an exit code, a
# command, or any other payload field to fail to parse, so read literally
# neither clause is satisfiable by ANY implementation of this redesign.
# `devbench` offers no mechanism to revise shipped acceptance-criteria text
# (checked: `uv run devbench --help` exposes only
# `request-amendment`/`apply-amendment`/`reject-amendment`, which govern the
# Changes Manifest, not `## Acceptance Criteria` wording; work-unit `.md`
# files are otherwise write-blocked for the executor tier). This script
# instead satisfies AC-5's INTENT, stated in the same clause's spec
# citations (3.5, 4.6): the hook must fail closed rather than allow
# whenever it cannot determine a real pass/block verdict. Every path
# through this script that cannot positively establish `"pass"` -- no
# record, an unrecognised status, a `"pending"` record, a record this
# script cannot read -- still exits 2 except the two narrow, explicitly
# reasoned exceptions below (no record at all, and `DEVBENCH_WORKSPACE_ROOT`
# unset); the record-based design changes HOW the verdict is obtained, not
# the fail-closed obligation AC-5 exists to enforce.
#
# Record location and format (pinned by
# `tests/unit/test_assert_shared_file_impact.py`, ref; the LOCATION is
# documented on `cli._shared_file_impact_verdict_path`'s docstring, ref,
# which itself delegates to `_session_state_file_path` for the routing
# rule, while the 4-line FORMAT is documented on
# `_write_shared_file_impact_verdict`'s own docstring, ref, not on
# `_shared_file_impact_verdict_path`): a 4-line plain
# text file at `<DEVBENCH_WORKSPACE_ROOT>/.devbench/shared-file-impact-verdict`,
# or `<DEVBENCH_WORKSPACE_ROOT>/.devbench/sessions/<DEVBENCH_SESSION_NAME>/
# shared-file-impact-verdict` when a named session is active (spec 4.4.4;
# mirrors `_session_scope_file_path`'s `scope.json` routing so two
# concurrent named sessions targeting the same workspace never share one
# verdict record) -- line 1 is the status (`pending`/`pass`/`block`), line
# 2 is the unit id (diagnostics only, never re-parsed for correlation),
# line 3 is an ISO-8601 UTC timestamp (diagnostics only), and line 4 is a
# per-invocation correlator (`cli._shared_file_impact_invocation_id`,
# round-5 finding A1 family, this round's residual-gap fix) THIS SCRIPT
# NEVER READS OR CARES ABOUT -- it exists only so
# `_write_shared_file_impact_verdict`'s own non-clobbering guard can tell
# one invocation's own `"pending"` -> `"pass"`/`"block"` transition apart
# from a DIFFERENT invocation's foreign write; this hook's verdict is
# unaffected by its value or presence either way. Written atomically
# (temp-then-rename, `devbench.utils.io.atomic_write_text`) so this hook
# never observes a partially written record. `DEVBENCH_SESSION_NAME`
# routing below (the `..` path-segment guard, and ASCII whitespace
# stripping) mirrors `cli._session_state_file_path` for every ASCII-space
# case round 5 raised (round-5 findings A2, A3), verified by
# `TestAssertSharedFileImpactHookSessionRoutingCrossLayerParity` (ref),
# which writes the record via the real Python function and reads it
# back via this real script rather than two independent reimplementations.
# Bounded, not universal (round-6 doc_review finding): Python's
# `str.strip()` also strips several non-ASCII whitespace code points
# (measured: U+001C, U+0085, U+00A0 among them) that bash's
# `[[:space:]]` character class (below) does NOT strip, so a
# `DEVBENCH_SESSION_NAME` padded with one of those specific code points
# resolves to a DIFFERENT record path in this script than in
# `cli._session_state_file_path` -- a real, if narrow, cross-layer
# divergence outside the ASCII-whitespace cases this hook's own tests
# cover.
#
# Staleness bound: this hook only ever clears a record it has itself just
# read (see the "consume" step below), so a record can affect Claude at
# most until the next time THIS hook successfully reads and deletes it --
# not a fixed count of Bash calls. Two refinements the pre-round-5 header
# overstated:
#   1. (round-5 finding A1, a regression this round fixes; extended in a
#      later change to close the same finding's residual gap)
#      `_write_shared_file_impact_verdict` (ref) refuses to overwrite an
#      on-disk `"block"` status with anything, so a DIFFERENT, later
#      `check-shared-file-impact` invocation's own `"pending"`/`"pass"`
#      writes can never silently erase an earlier, unconsumed `"block"` --
#      the record stays `"block"` until THIS hook consumes it, however many
#      later invocations write to it in the meantime. The identical
#      protection now also covers an unconsumed `"pending"`: a DIFFERENT,
#      later invocation's own write can never silently erase an earlier
#      invocation's still-open `"pending"` either (the crash-mid-run case
#      spec 3.5 requires to fail closed), while that SAME invocation's own
#      subsequent `"pass"`/`"block"` write still transitions it normally
#      (see the record-format paragraph above and that function's own
#      docstring for the per-invocation correlator this distinction is
#      keyed on).
#   2. (round-5 finding B1) `hooks.json` (ref, a DEFERRED file this unit's
#      Manifest does not touch) registers this script on `PostToolUse` for
#      the `Bash` tool only. Measured against this repo's own
#      `hook-logs.jsonl`: a Bash tool call that exits NON-ZERO emits a
#      `PostToolUseFailure` event, not `PostToolUse` -- this hook is not
#      registered for `PostToolUseFailure` at all, so it never fires on
#      that call. A blocking `check-shared-file-impact` invocation itself
#      exits non-zero, so THAT call's own PostToolUse event is exactly the
#      one this hook misses; combined with the non-clobbering write above,
#      the block is not lost, it is observed on the next Bash tool call
#      whose PostToolUse event actually reaches this hook -- which is not
#      guaranteed to be the very next Bash call if intervening calls also
#      exit non-zero. Registering this hook on `PostToolUseFailure` too is
#      the closable follow-up; out of this unit's Manifest, which does not
#      include `hooks.json`. No tracking work unit has been filed for it yet
#      (a prior draft, E5-F2-S1-T3, was filed via `write-proposal` and then
#      withdrawn via `reject-proposal` after it deadlocked this unit as an
#      auto-wired blocker) -- this gap is knowingly deferred and untracked,
#      not tracked under an id a reader could look up.
#
# Verdict semantics:
#   - No record file present at all -- ALLOW (exit 0). Reachable three ways,
#     only two of which are the intended "nothing unresolved" case: (a) this
#     hook has never seen a `check-shared-file-impact` invocation in this
#     session; (b) the prior record was already consumed by an earlier
#     PostToolUse event this hook received. The third (round-5 finding C2,
#     a genuine fail-open, not closable from inside this hook) is
#     `cmd_check_shared_file_impact` never reaching its own first line at
#     all -- an unrecognised CLI subcommand, an import-time configuration
#     failure, argparse rejecting the invocation, `devbench` not on PATH,
#     or `_write_shared_file_impact_verdict`'s own initial "pending" write
#     raising `OSError` -- none of which produce a record for this hook to
#     find. This case is symmetric with the `DEVBENCH_WORKSPACE_ROOT`-unset
#     exception below: there is nothing on disk to fail closed ON, and nothing
#     in THIS process's own environment or arguments that could distinguish
#     it from "never invoked at all". Reached with NO command-string parsing
#     of any kind, unlike every prior round's guard 2.
#   - Record reads `"pass"` -- ALLOW (exit 0): a no-match no-op, or a
#     full-suite run that introduced no failures attributable to this
#     unit's own scope.
#   - Record reads `"block"` -- BLOCK (exit 2), naming the unit id and
#     pointing at the JSON output the agent already saw as this Bash
#     call's own result.
#   - Record reads anything else (`"pending"`, a value from a future
#     schema this build of the hook does not recognise, or an empty/
#     unreadable record) -- BLOCK (exit 2). `"pending"` covers every path
#     `cmd_check_shared_file_impact` can exit through AFTER its initial
#     write WITHOUT reaching a clean pass/block verdict: an unrecognised
#     unit id, no local repo path configured, a scope-resolution error,
#     `_evaluate_shared_file_gate` raising, or the process being killed or
#     crashing outright before it finishes -- spec 3.5's "a run that
#     started but whose verdict cannot be determined" case, which this hook
#     fails CLOSED on rather than guessing "allow".
#   - The record exists but this hook cannot remove it (round-5 finding A4,
#     e.g. its containing directory is not writable) -- BLOCK (exit 2) with
#     this hook's own controlled message, never a bare `rm` failure
#     propagating through `set -e` with the OS's own stderr text and a
#     non-blocking exit code.
#
# Known limitation (round-5 finding E1, investigated, not fixed by
# redesign): this record is keyed only by (workspace, session), and several
# agent processes -- the executor and every review judge -- can share one
# `DEVBENCH_SESSION_NAME` (commonly `"default"`) within a single
# orchestrator run, each firing Bash PostToolUse events. Checked for a
# usable per-agent correlator visible to BOTH the `check-shared-file-impact`
# subprocess's own environment and this hook's invocation: the PostToolUse
# payload carries an `input.session_id` field, and `CLAUDE_CODE_SESSION_ID`
# is set in the gate subprocess's environment, but measured directly
# against this repo's own `hook-logs.jsonl`, that session id identifies the
# top-level orchestrator session, not the individual agent -- multiple
# concurrently-running agent types (executor, manifest-amender, each review
# judge) share the identical `session_id` value. The payload's `agent_id`
# field IS per-agent, but `CLAUDE_CODE_SESSION_ID` -- the one gate-subprocess
# env var actually checked as an `agent_id` equivalent -- is not; no candidate
# both sides can key on was found AMONG THE FIELDS CHECKED. This bound is
# narrower than "no usable correlator exists": the gate subprocess's own
# environment also carries `CLAUDE_PID` and `CLAUDE_CODE_CHILD_SESSION`
# alongside `CLAUDE_CODE_SESSION_ID`, and neither was evaluated as a
# candidate; separately, this hook is itself a subprocess with its OWN
# environment, which was never examined as a correlator source at all (only
# the PostToolUse payload's fields and the GATE subprocess's environment
# were). Closing that gap would mean keying the verdict record by agent
# rather than by (workspace, session) -- the redesign this round is
# explicitly not reopening. Bounded to the fields actually checked, this is
# a genuine, undocumented-until-now exposure, bounded to
# concurrent agents sharing the SAME workspace and SAME session name: a
# verdict written by one agent's `check-shared-file-impact` call can be
# consumed by a different, concurrently-running agent's own next Bash
# PostToolUse event, either blocking a call that earned no block (false
# positive) or clearing a block for the agent that did earn it (fail-open).
# Distinct `DEVBENCH_SESSION_NAME` values, or agents that do not overlap in
# time, are unaffected.
#
# One narrow, intentional exception to "no fail-open": `DEVBENCH_WORKSPACE_ROOT`
# being entirely UNSET in this hook's own environment is treated as ALLOW
# (exit 0), mirroring the established convention `guard-git-stage.sh` (ref)
# already uses for its own "no context to resolve, skip enforcement" case.
# `DEVBENCH_WORKSPACE_ROOT` is a required, import-time-enforced env var for
# every `devbench` CLI invocation (`src/devbench/config.py::_require_env`,
# ref) -- it being absent here is an environment-misconfiguration failure
# outside a real devbench-managed session, never the "an invocation started
# but its verdict could not be determined" case this hook otherwise fails
# closed on, and this hook cannot resolve ANY record path at all without it
# (there is nothing to fail closed ON).
#
# Exit 0  -> allowed (Claude proceeds)
# Exit 2  -> blocked (stderr becomes Claude's feedback)

set -euo pipefail

# Drain stdin (the Claude Code PostToolUse JSON payload) without extracting
# anything from it -- see the header above. This hook's verdict comes
# entirely from its own previously-written record file, never from the
# payload, so there is nothing left to parse out of it; this `cat` only
# avoids leaving the payload unread on the caller's side. Deliberately NOT
# routed through `_hook_lib.sh` `extract_field` even for a defensive
# `hook_event_name`/`tool_name` sanity check: measured directly against
# this script (see the empty-stdin case in
# `tests/unit/test_assert_shared_file_impact.py`, ref), gating the record
# check on ANY successfully-extracted payload field reintroduces exactly
# the failure mode this redesign exists to remove -- an empty or truncated
# stdin would silently ALLOW past a genuine `"block"` record instead of
# blocking, because the field extraction itself would come back empty.
# Fail-closed correctness of the record check below wins over a payload
# sanity check hooks.json's own `Bash`-only PostToolUse matcher already
# provides.
cat >/dev/null

fail_closed() {
  local reason="$1" fix="$2"
  echo "assert-shared-file-impact: ${reason}" >&2
  echo "Fix: ${fix}" >&2
  exit 2
}

# See the header's "one narrow, intentional exception" paragraph.
if [[ -z "${DEVBENCH_WORKSPACE_ROOT:-}" ]]; then
  exit 0
fi

# Mirrors `cli._session_state_file_path`'s DEVBENCH_SESSION_NAME routing
# for every ASCII-whitespace and `..`-segment case round 5 raised (spec
# 4.4.4, round-5 findings A2/A3) so this hook and
# `cmd_check_shared_file_impact` resolve the SAME record path for those
# cases; see the module header's "bounded, not universal" paragraph above
# for the narrow non-ASCII-whitespace divergence this does NOT cover.
RECORD_DIR="${DEVBENCH_WORKSPACE_ROOT}/.devbench"
SESSION_NAME="${DEVBENCH_SESSION_NAME:-}"
# A2: strip leading/trailing ASCII whitespace the way Python's `str.strip()`
# also does (`cli._session_state_file_path` does `os.environ.get(...).strip()`,
# which additionally strips several non-ASCII whitespace code points this
# bash character class does not -- see the module header) -- without this,
# a padded value (`' alpha '`) resolves to a DIFFERENT directory here than
# in Python, and an all-whitespace value (`'  '`, which Python treats as
# "no session active") would be treated as a real, non-empty session name
# by this script alone.
SESSION_NAME="${SESSION_NAME#"${SESSION_NAME%%[![:space:]]*}"}"
SESSION_NAME="${SESSION_NAME%"${SESSION_NAME##*[![:space:]]}"}"
if [[ -n "$SESSION_NAME" ]]; then
  # A3: reject only an exact `..` PATH SEGMENT, matching Python's
  # `".." in Path(session_name).parts` -- NOT any `..` substring. Wrapping
  # the (already-stripped) name in `/`-delimiters and glob-matching the
  # literal 4-character sequence `/../` is a segment test: it matches
  # `..`, `../escape` and `x/../y` (each has a `..` segment) but not
  # `a..b` (one segment, `a..b`, containing `..` only as a substring, which
  # devbench itself accepts as a valid session name).
  case "/${SESSION_NAME}/" in
    */../*)
      fail_closed "DEVBENCH_SESSION_NAME ('${SESSION_NAME}') contains an invalid '..' path segment -- cannot safely resolve the shared-file-impact verdict record location." \
        "fix the DEVBENCH_SESSION_NAME value in the orchestrator/session environment; it must not contain a '..' path segment."
      ;;
  esac
  RECORD_DIR="${RECORD_DIR}/sessions/${SESSION_NAME}"
fi
RECORD_PATH="${RECORD_DIR}/shared-file-impact-verdict"

# No record at all -- never invoked in this session, the prior record was
# already consumed by an earlier Bash PostToolUse event this hook received,
# or the invocation never reached its first line at all (header's "no
# record at all" paragraph, round-5 finding C2). Allow, with no
# command-string parsing needed to reach that conclusion (the round-5
# redesign this header describes).
if [[ ! -f "$RECORD_PATH" ]]; then
  exit 0
fi

STATUS=$(sed -n '1p' "$RECORD_PATH" 2>/dev/null || true)
UNIT_ID=$(sed -n '2p' "$RECORD_PATH" 2>/dev/null || true)
UNIT_ID="${UNIT_ID:-<unknown unit>}"

# A4: branch on the record's own status FIRST -- decide what this call
# would report -- then attempt to consume (delete) the record as part of
# reporting that decision, rather than unlinking it unconditionally before
# branching. Consuming (not merely reading) still happens on every path
# below, preserving the staleness bound; the difference is what happens
# when the consume itself FAILS (e.g. a non-writable `.devbench`/session
# directory: unlinking a file requires write permission on its CONTAINING
# directory, not the file itself). A bare `rm -f` failure under
# `set -euo pipefail` used to abort this whole script with the OS's own
# stderr text and a non-blocking exit code (1) -- silently walking past a
# real `"block"` verdict AND leaking a filesystem path via uncontrolled
# `rm` stderr. `consume_or_fail_closed` fails CLOSED (exit 2) with this
# hook's own controlled message instead, regardless of which status was
# read, since an unremovable record means the staleness bound this hook
# promises can no longer be guaranteed either way.
consume_or_fail_closed() {
  if ! rm -f "$RECORD_PATH" 2>/dev/null; then
    fail_closed "could not consume the shared-file-impact verdict record at ${RECORD_PATH} (status read: '${STATUS}', unit: ${UNIT_ID}) -- its containing directory is likely not writable." \
      "ensure ${RECORD_DIR} is writable by this process, then re-run 'devbench check-shared-file-impact ${UNIT_ID}'."
  fi
}

case "$STATUS" in
  pass)
    consume_or_fail_closed
    exit 0
    ;;
  block)
    consume_or_fail_closed
    fail_closed "check-shared-file-impact reported verdict: block for unit ${UNIT_ID} -- this task's diff touches a shared/high-fan-in file and the full-suite regression gate did not pass." \
      "read the JSON output 'devbench check-shared-file-impact ${UNIT_ID}' already printed for the 'new_failures' list and fix every regression it introduced (do not just delete or skip the failing tests). Then re-run check-shared-file-impact."
    ;;
  *)
    consume_or_fail_closed
    fail_closed "check-shared-file-impact started for unit ${UNIT_ID} but never recorded a pass/block verdict (record read: '${STATUS}') -- the run crashed, was killed, or hit an error path before completing." \
      "re-run 'devbench check-shared-file-impact ${UNIT_ID}' directly and resolve whatever error it prints on stderr."
    ;;
esac
