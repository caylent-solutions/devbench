"""CLI entry point for the devbench system.

Provides shell-callable commands so Claude Code (or any external process)
can query backlog status and bridge plugin agents to repo context.

Usage::

    python3 -m devbench.cli [--config <path>] <command> [args]

Options::

    --config <path>         Path to devbench YAML config (sets DEVBENCH_CONFIG_PATH).
                            Overrides the DEVBENCH_CONFIG_PATH environment variable.

Commands::

    status                  Show backlog summary (counts by status)
    next                    Print the next actionable work unit ID and title
    claim <id>              Claim a work unit (transition to in-progress)
    set-status <id> <s>     Force any status; bulk: --include "<toks>" [--exclude "<toks>"] [--dry-run] [--yes] <s>

    mark-done <id>          Mark unit as Done (enforces done-gate: all judges must have passed)
    decline <id> --reason M Mark unit Declined (won't ever be done); captures the rationale
    hold <id> --reason M    Mark unit Hold (deferred / under debate); orchestrator skips it
    unhold <id> --reason M  Return a held unit to in-queue and capture why it was released
    validate-backlog [--fix] [--strict] Check backlog integrity; --fix auto-corrects rule-10/11
                            violations; --strict also flags draft/hold Manifest conflicts
    ensure-branch <id>      Create or switch to work unit branch before executor runs
    git-ops <id>            Run git operations for a work unit (commit-only when defer_pr is set)
    git-ops-finalize <repo> [--provenance <path>]
                            Push single branch and create PR (after all deferred commits)
    report [since]          Print progress report with velocity stats
    log <message>           Append a message to the orchestrator log file
    start                   Run the orchestrate skill via the Claude Agent SDK (non-interactive);
                            --daemon, -d detaches to the background and returns immediately (#209)
    scope set/clear/show    Persistent scope management without starting the orchestrator
    watch [--watch N]       Show a live dashboard of the active orchestration

Plugin agent bridge commands (used by devbench plugin agents)::

    read-unit <id>                          Return work unit content and repo path as JSON
    get-diff <id>                           Return combined git diff for the work unit's repo
    check-manifest-scope <id>               Print out-of-Manifest staged paths; exit non-zero
                                             on mismatch (read-only, no LLM judgement)
    check-reachability <id>                 Word-boundary, source-classified reachability gate over the unit's
                                             Changes Manifest scope; blocks (exit 1) on any finding (spec 4.4)
    run-tests <id>                          Run test suite for the work unit's repo
    tdd-gate <id>                           Run the machine-observed RED gate for a gated task;
                                             on a genuine RED, records the orchestrator-only
                                             RED_OBSERVED entry (FR-4.2, exits 1 on any rejection)
    check-fixture-consistency <id>          Cross-reference fixtures against the canonical dataset (opt-in)
    log-verdict <judge> <id> <v> [msg]      Log a judge verdict (pass|fail) to work unit Comments
    log-comment <agent> <id> <message>      Log a non-verdict agent comment to work unit Comments
    log-tdd <id> <phase> <message>          Log a TDD phase entry (RED|GREEN|REFACTOR) to TDD Cycle Log;
                                             RED_OBSERVED is orchestrator-only and always rejected here

    log-verdict/log-comment/log-tdd free-text fields (feedback/message) must be a single
    line with no control characters (e.g. no embedded newline) and no bracketed TDD phase
    tag such as '[RED_OBSERVED]'; a violation exits 1 and writes nothing to the work unit.

    request-amendment <id>                  Register amendment request (JSON payload on stdin)
    apply-amendment <id>                    Apply approved amendment with Layer 3 post-check
    reject-amendment <id> <reason>          Reject amendment and block the task
    list-proposals                          List every pending task-factory proposal
    promote-proposal <id>                   Promote a proposed task to in-queue (with dependency wiring)
    promote-proposal --all-from <src>       Promote every proposed task originating from a source task
    reject-proposal <id> --reason <msg>     Archive a proposed task's draft and remove its BACKLOG row
    materialise-proposal <src>              Materialise a pending proposal into draft files (agent-called)
    write-proposal <src>                    Persist a blocker-resolver proposal JSON read from stdin

All commands exit 0 on success, non-zero on failure. Output is structured
for easy parsing by Claude Code or other automation.
"""

import asyncio
import contextlib
import fnmatch
import functools
import getpass
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import AsyncGenerator, Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, NamedTuple, cast

# Resolved once at import time so each watch tick doesn't re-PATH-search.
# Used by `cmd_report --watch` to clear both the viewport AND the scrollback
# between frames. The fallback escape sequence ``\033c`` is the VT100 RIS
# (Reset to Initial State) -- works on every modern terminal but is more
# disruptive (resets colors). Prefer the OS clear binary when available.
_TERMINAL_CLEAR_CMD: str | None = shutil.which("clear") or shutil.which("cls")


# Pre-parse --config before any devbench imports so that config.py loads the
# correct YAML at module import time (config.py reads DEVBENCH_CONFIG_PATH on import).
def _pre_parse_config(argv: list[str]) -> None:
    """Extract --config <path> from argv and set DEVBENCH_CONFIG_PATH env var."""
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            os.environ["DEVBENCH_CONFIG_PATH"] = argv[i + 1]
            # Remove --config and its value so downstream parsing is unaffected.
            argv.pop(i + 1)
            argv.pop(i)
            return


_pre_parse_config(sys.argv)

from devbench.backlog.amendment import (
    REVIEW_FAILURES_DIR_NAME,
    AmendmentError,
    AmendmentRequest,
    PreFilter,
    apply_amendment,
    read_review_failure_files,
    reject_amendment,
    write_request,
)

# Re-export from manager.py so existing ``cli.red_gate_satisfied`` /
# ``cli.green_green_observed_satisfied`` callers and tests resolve unchanged
# after the predicates moved to manager.py (code_review FAIL round 3 --
# check-merge's ``_check_merge_handle_merged`` -> ``BacklogManager.mark_done``
# path previously bypassed the done-gate entirely because the checks lived
# only in cli.py's ``cmd_mark_done``-only wrapper). Single source of truth
# now lives in manager.py because every ``mark_done()`` caller must inherit
# it, and manager.py cannot depend on cli.py without a circular import.
#
# code_review FAIL round 4 (non-blocking) observed that the module-level
# ``__all__`` below -- which marks these as intentional re-exports so
# ruff does not flag them F401 -- reads as if it declared cli.py's
# entire public surface, misleading for a CLI module with dozens of
# ``cmd_*`` entry points. The self-aliased ``import x as x`` idiom (PEP
# 484's usual alternative for exactly this situation) was evaluated and
# rejected: this repo's ruff configuration enables ``PLC0414`` ("import
# alias does not rename original package"), which flags that idiom as an
# error, so ``__all__`` remains the only lint-clean mechanism available
# here. Read it as "these names are deliberately re-exported", not as
# "this is the whole module".
from devbench.backlog.index_errors import exit_with_index_error
from devbench.backlog.manager import (
    _GREEN_GREEN_OBSERVED_MESSAGE_TEMPLATE,
    BacklogManager,
    _build_remedies_rejection_message,
    _extract_wu_title,
    compose_gate_waiver_record,
    count_review_fails_for_judge,
    green_green_observed_satisfied,
    red_gate_satisfied,
    resolve_judge_retry_budget,
)
from devbench.backlog.parser import BacklogParser
from devbench.backlog.proposal import (
    BlockedTaskState,
    CascadeDepthError,
    Proposal,
    ProposalError,
    ProposalMatch,
    ProposalTaskState,
    _compute_fix_signature,
    _dep_row_has_task,
    _extract_intent_phrase,
    _placeholder_dep_row,
    add_dep,
    classify_blocked_task,
    classify_proposed_task,
    delete_proposal_if_consumed,
    detect_placeholder_descriptions,
    enforce_cascade_depth,
    find_matching_pending_proposal,
    list_proposals,
    materialise_proposal,
    promote_all_from_source,
    promote_proposal,
    read_proposal,
    reject_proposal,
    write_proposal,
)
from devbench.backlog.review_feedback_vocabulary import (
    JUDGE_CATEGORIES,
    JUDGE_SEVERITY_ORDER,
)
from devbench.backlog.work_unit import (
    WorkUnit,
    WorkUnitStatus,
    WorkUnitType,
)
from devbench.comment_time import audit_timestamp_to_utc, comment_timestamp
from devbench.config import (
    AGENT_MODELS,
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    BLOCKED_RECOVERY_WINDOW_SECONDS,
    MAX_CASCADE_DEPTH,
    MAX_TRANSPORT_RESTARTS,
    ORCHESTRATE_EFFORT,
    ORCHESTRATE_MAX_THINKING_TOKENS,
    REPO_LOCAL_PATHS,
    RUNTIME_CONFIG,
    TRANSPORT_RESTART_BACKOFF_BASE_SECONDS,
    TRANSPORT_RESTART_BACKOFF_MAX_SECONDS,
    UPDATE_SUBMODULE,
    WORKSPACE_ROOT,
    _read_env,
    resolve_repo,
    validate_repo,
)
from devbench.config import (
    ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS as _ORCH_INACTIVITY_TIMEOUT,
)
from devbench.config_loader import (
    QuotaHandlingConfig,
    RepoConfig,
    format_branch_name,
    format_single_branch_name,
    get_configured_default_branch,
    get_effective_branch_prefix,
)
from devbench.constants import (
    AGENT_WRITABLE_TDD_PHASES,
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_LOCAL_PATH_RE,
    BACKLOG_REPO_RE,
    BACKLOG_STATUS_RE,
    COMMENT_AGENT_TEMPLATE,
    COMMENT_ENTRY_TEMPLATE,
    COMMENTS_SECTION_HEADER,
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_SUBDIR,
    DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS,
    DEFAULT_MAX_QUOTA_RESUMES,
    DEFAULT_PLUGIN_SUBPATH,
    DISPLAY_STATUS_VALUES,
    EM_DASH,
    EXPECTED_OUTPUT_NONE,
    FAILURE_DIGEST_MAX_LENGTH,
    FAILURE_DIGEST_MIN_LENGTH,
    FAILURE_DIGEST_RE,
    FINALIZE_COMMIT_TEMPLATE,
    FINALIZE_PR_TITLE_TEMPLATE,
    GATE_PROVENANCE_BUILTIN,
    GATE_TIER_MACHINE_BLOCKING,
    GATE_TIERS,
    GATE_WAIVER_ATTRIBUTION_EXECUTOR,
    GATE_WAIVER_ATTRIBUTION_OPERATOR,
    KNOWN_JUDGE_NAMES,
    ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX,
    ORCHESTRATOR_BLOCK_QUARANTINE_AUDIT_PREFIX,
    ORCHESTRATOR_ONLY_TDD_PHASES,
    ORCHESTRATOR_QUOTA_RESUME_AUDIT_PREFIX,
    ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX,
    ORCHESTRATOR_RESTART_EXIT_CODE,
    ORCHESTRATOR_RETRY_BUDGET_EXHAUSTED_AUDIT_TAG,
    RECOVERY_PROBE_REQUEST_SIZE_TOKENS,
    RECOVERY_PROBE_TIMEOUT_SECONDS,
    RED_OBSERVED_FIELD_EXIT_CODE,
    RED_OBSERVED_FIELD_FAILURE_DIGEST,
    RED_OBSERVED_FIELD_TEST_NODE_ID,
    RED_OBSERVED_MESSAGE_TEMPLATE,
    RED_OBSERVED_RECORD_MALFORMED_DIGEST_TEMPLATE,
    RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE,
    RED_OBSERVED_RECORD_WHITESPACE_TEST_NODE_ID_TEMPLATE,
    RED_OBSERVED_RECORD_ZERO_EXIT_CODE_MESSAGE,
    SESSION_DEFAULT_NAME,
    SESSION_DRAIN_SIGNAL_FILENAME,
    SESSION_PID_FILENAME,
    SESSION_SESSIONS_BASE_DIR,
    SESSION_STARTED_AT_FILENAME,
    SESSION_STARTED_BY_FILENAME,
    STATUS_BLOCKED,
    STATUS_DECLINED,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_IN_REVIEW,
    STATUS_SEPARATOR_WIDTH,
    STATUS_SUMMARY_LABEL_WIDTH,
    TDD_PHASE_GREEN_GREEN_OBSERVED,
    TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE,
    TDD_PHASE_RED_OBSERVED,
    VALID_TDD_PHASES,
)
from devbench.drain import DrainState, _current_user, cancel_drain, consume_drain, read_drain_state, request_drain
from devbench.git_orphans import OrphanReport
from devbench.git_quarantine import UNATTRIBUTED_OWNER, QuarantineRecord, RestoreRecord

if TYPE_CHECKING:
    from claude_agent_sdk.types import EffortLevel
from devbench.github.git_ops import GitOpsService
from devbench.log_setup import setup_logging
from devbench.plugin_shadow import (
    materialise_shadow_plugin,
    shadow_plugin_path,
    write_pid_sentinel,
)
from devbench.quota import (
    BackoffConfig,
    QuotaCheckpoint,
    QuotaExhaustedError,
    RecoveryProbeUnavailableError,
    _apply_resume_strategy,
    detect_quota_error,
    load_checkpoint,
    recovery_probe,
    save_checkpoint,
    wait_for_reset,
)

# Re-export from reporting so existing ``cli._format_duration`` callers and tests
# resolve unchanged after the function moved to report.py for the issue #161
# orchestrator-alive banner. Single source of truth lives in report.py because
# the banner is implemented there and reporting must not depend on cli.py. See
# the ``__all__``-vs-self-aliased-``as`` rationale above the
# ``devbench.backlog.manager`` re-export block.
from devbench.reporting.report import _format_duration, read_all_drain_states
from devbench.scope import (
    InvalidScopeError,
    ScopeFilter,
    _expand_prefix,
    _read_and_migrate_scope_payload,
    _scope_file_path,
    _tokenise,
)
from devbench.session import ClaimRaceError, Session, SessionRegistry, detect_scope_overlap, flock_backlog
from devbench.source_classification import SOURCE_EXTENSIONS, is_entry_point_stem, is_source_extension, is_test_path
from devbench.utils.io import atomic_write_text
from devbench.utils.process import run_command
from devbench.work_unit_scope import MODE_DEFER_PR, MODE_PER_TASK_BRANCH, ScopeResult, resolve_changed_files

if TYPE_CHECKING:
    from devbench.config_loader import ResolvedGateConfig, RuntimeConfig

__all__ = ["_format_duration", "green_green_observed_satisfied", "red_gate_satisfied"]

logger = logging.getLogger("devbench.cli")


@dataclass
class _StatusArgs:
    """Parsed arguments for ``cmd_status``.

    Attributes:
        detail: Whether ``--detail`` was supplied.
        include: Raw ``--include`` token string (empty = not supplied).
        exclude: Raw ``--exclude`` token string (empty = not supplied).
        session: Named-session filter from ``--session <name>`` (empty = not supplied).
        exit_code: Non-zero when argument parsing fails.
    """

    detail: bool = False
    include: str = ""
    exclude: str = ""
    session: str = ""
    exit_code: int = 0


def _parse_status_argv(argv: tuple[str, ...]) -> _StatusArgs:
    """Parse ``cmd_status`` arguments.

    Accepts ``--detail``, ``--include <tokens>``, ``--exclude <tokens>``,
    and ``--session <name>`` flags (spec sections 4.2.2, 4.4.6,
    AC-190-10, AC-190-11, AC-192-12).  Any unrecognised positional argument
    causes a non-zero ``exit_code`` in the returned :class:`_StatusArgs`.

    Args:
        argv: The positional argument tuple passed to ``cmd_status``.

    Returns:
        A :class:`_StatusArgs` instance with parsed values and an
        ``exit_code`` of ``0`` on success or ``1`` on parse failure.
    """
    result = _StatusArgs()
    args = list(argv)
    i = 0
    extra_positional: list[str] = []
    while i < len(args):
        arg = args[i]
        if arg == "--detail":
            result.detail = True
        elif arg in ("--include", "--exclude", "--session"):
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                print(
                    f"ERROR: {arg} requires a value",
                    file=sys.stderr,
                )
                result.exit_code = 1
                return result
            i += 1
            if arg == "--include":
                result.include = args[i]
            elif arg == "--exclude":
                result.exclude = args[i]
            else:
                result.session = args[i]
        elif arg:
            extra_positional.append(arg)
        i += 1
    if extra_positional:
        print(
            f"ERROR: cmd_status takes no positional args (got {extra_positional!r})",
            file=sys.stderr,
        )
        result.exit_code = 1
    return result


def _read_scope_banner_data(workspace_root: Path) -> dict[str, object] | None:
    """Return the raw scope.json payload when scope.json exists, else ``None``.

    Reads scope.json to surface ``started_at`` / ``started_by`` metadata that
    :meth:`ScopeFilter.from_file` omits from the dataclass. Delegates to
    :func:`devbench.scope._read_and_migrate_scope_payload` so a legacy
    list-shaped scope.json (issue #270) self-heals on this path exactly as it
    does for ``ScopeFilter.from_file`` -- both ``devbench next`` and
    ``devbench status`` reach this function, so neither crashes on a stale
    array file. ``None`` is returned only when the file is absent.

    Args:
        workspace_root: Path to the workspace root directory.

    Returns:
        The decoded (and, for the legacy list shape, migrated) JSON payload
        dict, or ``None`` if scope.json does not exist.

    Raises:
        json.JSONDecodeError: If the file exists but contains invalid JSON.
        KeyError: If required keys are missing from the JSON payload.
        TypeError: If the top-level payload is neither an object nor the
            legacy list shape, or if a legacy list element is invalid.
        OSError: If migrating a legacy list payload fails to write.
    """
    scope_path = _scope_file_path(workspace_root)
    return _read_and_migrate_scope_payload(scope_path)


def _render_scope_banner(include: list[str], exclude: list[str], started_at: str) -> None:
    """Print the ``SCOPE: include=[...] exclude=[...] (started ...)`` banner.

    Output goes to stdout immediately before the Status Summary header.

    Args:
        include: List of raw include token strings.
        exclude: List of raw exclude token strings.
        started_at: ISO-8601 UTC timestamp string from scope.json (or empty string
            when the scope was built from one-off CLI flags with no persistence).
    """
    include_part = f"include=[{', '.join(include)}]"
    exclude_part = f"exclude=[{', '.join(exclude)}]"
    started_part = f"(started {started_at})" if started_at else "(one-off)"
    print(f"SCOPE: {include_part} {exclude_part} {started_part}")


def _format_drain_status_line(session_name: str | None, state: DrainState) -> str:
    """Format one ``DRAIN REQUESTED`` line for the banner and ``drain --status``.

    db-306 (spec Section 4 FR-19, AC-46): the root-scope form omits the
    session qualifier; the per-session form inserts ``[session=<name>]``
    immediately after ``DRAIN REQUESTED`` so an operator scanning ``report``,
    ``status``, or ``drain --status`` output can tell at a glance which
    signal(s) are pending and where each one came from.

    Args:
        session_name: ``None`` for the workspace-root signal; the session
            directory name for a per-session signal.
        state: The parsed drain state to render.

    Returns:
        A single formatted line with no trailing newline.
    """
    reason_part = state.reason if state.reason else "(none)"
    scope_part = f" [session={session_name}]" if session_name is not None else ""
    return (
        f"DRAIN REQUESTED{scope_part}: at {state.requested_at.isoformat()} "
        f"by {state.requested_by} (reason: {reason_part})"
    )


def _render_drain_banner(workspace_root: Path, file: IO[str] | None = None) -> None:
    """Print one ``DRAIN REQUESTED`` line per pending drain signal.

    db-306 (spec Section 0 item 7, Section 4 FR-19, R4 RC-2): reads every
    drain signal in *workspace_root* non-destructively via
    :func:`~devbench.reporting.report.read_all_drain_states` -- the
    workspace-root signal AND every per-session signal, regardless of
    ``DEVBENCH_SESSION_NAME`` -- so a per-session drain is visible to an
    operator whose shell never exported that variable. When no signal is
    present this function is a no-op. Output goes to *file* (default
    ``sys.stdout``) immediately before the Status Summary header (spec
    section 4.3.5, AC-188-7).

    Args:
        workspace_root: Workspace directory from which drain signal paths are
            resolved.
        file: Output stream to write the banner to. Defaults to ``sys.stdout``
            when ``None``. Callers may pass an ``io.StringIO`` instance to
            capture banner text without capturing the full process stdout.
    """
    states = read_all_drain_states(workspace_root)
    if not states:
        return
    out = file if file is not None else sys.stdout
    for session_name, state in states:
        print(_format_drain_status_line(session_name, state), file=out)


def _print_active_units(active: list[WorkUnit]) -> None:
    """Render the ``Active work units:`` panel for ``cmd_status``.

    Prints each IN_PROGRESS / IN_REVIEW unit with an optional duration suffix
    for IN_PROGRESS tasks.

    Args:
        active: Work units whose status is IN_PROGRESS or IN_REVIEW.
    """
    if not active:
        return
    print("\nActive work units:")
    for u in active:
        line = f"  [{u.status.value}] {u.id} -- {u.title}"
        if u.status is WorkUnitStatus.IN_PROGRESS:
            duration = _in_progress_attempt_duration(u.id)
            line += f" (in-progress for {duration})" if duration is not None else " (in-progress, timer unavailable)"
        print(line)


def _print_actionable_summary(
    parser: BacklogParser,
    units: list[WorkUnit],
    active: list[WorkUnit],
) -> None:
    """Print ``Next actionable``, ``All DONE``, or ``No actionable`` line.

    Issue #185: ``get_parallel_candidates`` includes IN_PROGRESS tasks so
    an interrupted run can resume, but the ``Next actionable`` line should
    point at the next DIFFERENT task.  The ``active`` list is used to
    exclude already-running IDs.

    Issue #251: the line itself is produced by
    :func:`devbench.backlog.actionability.actionability_line`, shared with
    ``devbench report`` so the two commands cannot disagree about whether
    the run can proceed.

    Args:
        parser: The :class:`BacklogParser` instance.
        units: Full list of parsed work units.
        active: Work units currently IN_PROGRESS or IN_REVIEW.
    """
    from devbench.backlog.actionability import actionability_line

    print(f"\n{actionability_line(parser, units, active_ids=[u.id for u in active])}")


@dataclass
class _ScopeResolution:
    """Result of resolving the active scope for a status/report command.

    Attributes:
        has_scope: ``True`` when a scope is active (either per-command flags
            or an active scope.json).
        include: Parsed include token list.
        exclude: Parsed exclude token list.
        started_at: ISO-8601 timestamp from scope.json (empty for one-off flags).
        error: Non-empty string means scope.json was corrupt; value is the
            actionable error message to print to stderr.
    """

    has_scope: bool = False
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    started_at: str = ""
    error: str = ""


def _resolve_scope_for_status(parsed: _StatusArgs) -> _ScopeResolution:
    """Build a ``_ScopeResolution`` from parsed flags or active scope.json.

    Per-command ``--include``/``--exclude`` flags take precedence over any
    active scope.json (AC-190-11).  When neither flag is supplied, the
    workspace-root scope.json is consulted (AC-190-10).  Corrupt scope.json
    returns a non-empty ``error`` field; callers must check this field and
    short-circuit with rc=1 when it is set.

    Args:
        parsed: The parsed status argv struct.

    Returns:
        A :class:`_ScopeResolution` instance.  Check ``error`` first.
    """
    if parsed.include or parsed.exclude:
        # One-off per-command override -- no persistence (AC-190-11).
        return _ScopeResolution(
            has_scope=True,
            include=_tokenise(parsed.include) if parsed.include else [],
            exclude=_tokenise(parsed.exclude) if parsed.exclude else [],
        )
    # Consult active scope.json when no flags supplied (AC-190-10).
    try:
        raw = _read_scope_banner_data(WORKSPACE_ROOT)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return _ScopeResolution(
            error=f"ERROR: scope.json is corrupt and cannot be read: {exc}",
        )
    if raw is None:
        return _ScopeResolution()
    raw_include = raw.get("include", [])
    raw_exclude = raw.get("exclude", [])
    if not isinstance(raw_include, list):
        return _ScopeResolution(
            error=(f"ERROR: scope.json field 'include' must be a list, got {type(raw_include).__name__}"),
        )
    if not isinstance(raw_exclude, list):
        return _ScopeResolution(
            error=(f"ERROR: scope.json field 'exclude' must be a list, got {type(raw_exclude).__name__}"),
        )
    return _ScopeResolution(
        has_scope=True,
        include=list(raw_include),
        exclude=list(raw_exclude),
        started_at=str(raw.get("started_at", "")),
    )


def _extract_session_from_wu(wu: WorkUnit) -> str | None:
    """Return the session name from the most recent ``[WU_CLAIMED]`` audit in a WU file.

    Reads the ``## Comments`` section of *wu*'s backing Markdown file and
    searches for lines containing ``[WU_CLAIMED]`` with a ``session=<name>``
    token.  Returns the name from the last such line (most recent claim wins).
    Returns ``None`` when the file does not exist, has no Comments section,
    has no ``[WU_CLAIMED]`` line, or the line carries no ``session=`` token
    (legacy single-session behaviour).

    Args:
        wu: The :class:`WorkUnit` whose backing file to inspect.

    Returns:
        The session name string, or ``None`` when absent.

    Raises:
        OSError: If the file exists but cannot be read (permissions, I/O error).
    """
    if not wu.file_path.exists():
        return None
    content = wu.file_path.read_text(encoding="utf-8")

    # Extract only the Comments section to avoid false matches in other sections.
    comments_start = content.find(COMMENTS_SECTION_HEADER)
    if comments_start == -1:
        return None
    comments_body = content[comments_start + len(COMMENTS_SECTION_HEADER) :]

    session_name: str | None = None
    session_marker = "session="
    for line in comments_body.splitlines():
        if "[WU_CLAIMED]" not in line:
            continue
        # Look for session=<token> in the line; token ends at next whitespace or EOL.
        idx = line.find(session_marker)
        if idx == -1:
            continue
        value_start = idx + len(session_marker)
        value_end = line.find(" ", value_start)
        session_name = line[value_start:].strip() if value_end == -1 else line[value_start:value_end].strip()

    return session_name if session_name else None


def cmd_status(*argv: str) -> int:
    """Print backlog summary grouped by status.

    Accepts scope-filter flags (spec section 4.2.2, AC-190-10, AC-190-11)
    and a named-session filter flag (spec section 4.4.6, AC-192-12,
    AC-192-13):

    - ``--include "<tokens>"`` -- one-off include selector; overrides active
      scope.json when present.
    - ``--exclude "<tokens>"`` -- one-off exclude selector.
    - ``--session <name>`` -- filter rendered output to only work units
      claimed by the named session.  Without this flag, all work units are
      shown (aggregated view across sessions).

    When neither ``--include`` nor ``--exclude`` is supplied, the active
    ``scope.json`` (if any) is consulted instead.  In either case a
    ``SCOPE: include=[...] exclude=[...] (started ...)`` banner is printed
    above the Status Summary.

    With ``--detail`` (E220), additionally render per-state sections: in-queue
    (every actionable Task with the IDs of its still-open dependencies), six
    blocked-task sections (one per :class:`~devbench.backlog.proposal.BlockedTaskState`
    in canonical order: auto-clearing, amendment-recovery, dependency, held,
    blocked-on-held, operator-required), and held (every Hold Task with
    the most recent ``[HOLD]`` reason from its Comments).
    """
    parsed = _parse_status_argv(argv)
    if parsed.exit_code != 0:
        return parsed.exit_code

    scope = _resolve_scope_for_status(parsed)
    if scope.error:
        print(scope.error, file=sys.stderr)
        return 1
    if scope.has_scope:
        _render_scope_banner(scope.include, scope.exclude, scope.started_at)

    _render_drain_banner(WORKSPACE_ROOT)

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    try:
        all_units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        # Issue #305: this used to escape as a raw traceback while
        # ``devbench report`` produced an actionable diagnostic for the same
        # condition. Both now route through one handler.
        exit_with_index_error("status", BACKLOG_INDEX, exc)

    # AC-192-12: when --session <name> is given, filter to only WUs whose most
    # recent [WU_CLAIMED] audit names that session.  Without --session, the full
    # aggregated list is used (AC-192-13).
    units = [u for u in all_units if _extract_session_from_wu(u) == parsed.session] if parsed.session else all_units

    counts: dict[str, int] = {}
    for unit in units:
        key = unit.status.value.lower()
        counts[key] = counts.get(key, 0) + 1

    total = len(units)
    unmaterialised_count = _count_unmaterialised_proposed_tasks()
    blocked_counts = _count_blocked_split(units)
    draft_count = counts.get("draft", 0)

    print("Backlog Status Summary")
    print("=" * STATUS_SEPARATOR_WIDTH)
    print(f"  {'TOTAL':<{STATUS_SUMMARY_LABEL_WIDTH}} {total:>4}")
    print(f"  {'Draft':<{STATUS_SUMMARY_LABEL_WIDTH}} {draft_count:>4}")
    blocked_rows: list[tuple[str, BlockedTaskState]] = [
        ("Blocked (auto-clearing)", BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL),
        ("Blocked (amendment-recovery)", BlockedTaskState.AWAITING_AMENDMENT_RECOVERY),
        ("Blocked (dependency)", BlockedTaskState.AWAITING_DEPENDENCY),
        ("Blocked (held)", BlockedTaskState.HELD),
        ("Blocked (blocked-on-held)", BlockedTaskState.BLOCKED_ON_HELD),
        ("Blocked (runtime-degradation)", BlockedTaskState.RUNTIME_DEGRADATION),
        ("Blocked (operator-required)", BlockedTaskState.OPERATOR_ACTION_REQUIRED),
    ]
    for status_val in DISPLAY_STATUS_VALUES:
        if status_val == "Blocked":
            for label, state in blocked_rows:
                print(f"  {label:<{STATUS_SUMMARY_LABEL_WIDTH}} {blocked_counts[state]:>4}")
            continue
        count = counts.get(status_val.lower(), 0)
        print(f"  {status_val:<{STATUS_SUMMARY_LABEL_WIDTH}} {count:>4}")
    print(f"  {'Un-materialised':<{STATUS_SUMMARY_LABEL_WIDTH}} {unmaterialised_count:>4}")

    active = [u for u in units if u.status in (WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.IN_REVIEW)]
    _print_active_units(active)
    _print_actionable_summary(parser, units, active)

    if parsed.detail:
        _print_status_detail(units)

    return 0


def _print_blocked_panel_by_bucket(
    blocked_tasks: list[WorkUnit],
    units_by_id: dict[str, WorkUnit],
) -> None:
    """Split the ``--detail`` blocked panel by classifier bucket.

    Up to six separate panel headers are rendered, one per non-empty
    ``BlockedTaskState`` bucket.  Empty buckets are silently omitted.
    """
    buckets: dict[BlockedTaskState, list[WorkUnit]] = {s: [] for s in BlockedTaskState}
    for u in blocked_tasks:
        state = classify_blocked_task(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            task_id=u.id,
            workspace_root=WORKSPACE_ROOT,
        )
        buckets[state].append(u)
    bucket_headers = [
        (BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL, "Blocked tasks (auto-clearing via proposal)"),
        (BlockedTaskState.AWAITING_AMENDMENT_RECOVERY, "Blocked tasks (awaiting amendment recovery)"),
        (BlockedTaskState.AWAITING_DEPENDENCY, "Blocked tasks (awaiting dependency)"),
        (BlockedTaskState.HELD, "Held tasks"),
        (BlockedTaskState.BLOCKED_ON_HELD, "Blocked tasks (blocked on held)"),
        (
            BlockedTaskState.RUNTIME_DEGRADATION,
            "Blocked tasks (runtime-degradation -- restart `make start` to recover)",
        ),
        (BlockedTaskState.OPERATOR_ACTION_REQUIRED, "Blocked tasks (operator action required)"),
    ]
    for state, header in bucket_headers:
        tasks = buckets[state]
        if not tasks:
            continue
        print(f"\n{header} ({len(tasks)}):")
        for u in tasks:
            _print_blocked_row(u, units_by_id)


def _print_blocked_row(u: WorkUnit, units_by_id: dict[str, WorkUnit]) -> None:
    """Render a single row in the blocked-tasks panel: ``id -- title (note)``
    plus any unsuperseded ``[BLOCKED]`` audit lines beneath it."""
    wu_file = _resolve_unit_file(u)
    content = wu_file.read_text(encoding="utf-8") if wu_file is not None else ""
    # Issue #148 / FR-6 (db-253 Gap 2a): walk the markers via the strict,
    # Comments-scoped, end-anchored helper -- prose that merely quotes the
    # marker syntax elsewhere in the file cannot mint a phantom target --
    # and surface only non-terminal targets.
    marker_ids = BacklogManager()._extract_pending_proposal_markers(wu_file) if wu_file is not None else set()
    markers = sorted(
        marker
        for marker in marker_ids
        if (target := units_by_id.get(marker)) is None
        or target.status not in {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    )
    unsatisfied = _first_unsatisfied_dep(u, units_by_id)
    note_parts: list[str] = []
    if markers:
        note_parts.append(f"pending proposal {markers[0]}")
    if unsatisfied:
        note_parts.append(f"blocker {unsatisfied}")
    note = ", ".join(note_parts) if note_parts else "no open marker / blocker found"
    print(f"  {u.id} -- {u.title}  ({note})")
    # Issue #153: surface most-recent unsuperseded ``[BLOCKED]`` audit so
    # the operator sees WHY the task is blocked. Audits superseded by a
    # later ``[UNBLOCKED]`` / ``[CASCADE_RESOLVED]`` are filtered out
    # (the file stays append-only; only the rendered panel hides stale rows).
    for audit in _unsuperseded_blocked_audits(content):
        print(f"      {audit}")


def _print_status_detail(units: list[WorkUnit]) -> None:
    """Render the ``--detail`` panels: in-queue, blocked, held.

    Pulled out of ``cmd_status`` so the command body stays under
    PLR0912's branch ceiling and so the panel rendering can be
    unit-tested directly without spinning up a real backlog.
    """
    units_by_id = {u.id: u for u in units}
    in_queue_tasks = sorted(
        (u for u in units if u.unit_type is WorkUnitType.TASK and u.status is WorkUnitStatus.IN_QUEUE),
        key=lambda u: u.id,
    )
    if in_queue_tasks:
        print("\nIn-queue tasks (with dep status):")
        for u in in_queue_tasks:
            unsatisfied = _first_unsatisfied_dep(u, units_by_id)
            if unsatisfied:
                print(f"  [waiting] {u.id} -- {u.title}  (blocker: {unsatisfied})")
            else:
                print(f"  [ready]   {u.id} -- {u.title}")

    blocked_tasks = sorted(
        (u for u in units if u.unit_type is WorkUnitType.TASK and u.status is WorkUnitStatus.BLOCKED),
        key=lambda u: u.id,
    )
    if blocked_tasks:
        _print_blocked_panel_by_bucket(blocked_tasks, units_by_id)
        _print_blocked_rejection_categories(blocked_tasks)

    held_tasks = sorted(
        (u for u in units if u.unit_type is WorkUnitType.TASK and u.status is WorkUnitStatus.HOLD),
        key=lambda u: u.id,
    )
    if held_tasks:
        print("\nHeld tasks (with most recent [HOLD] reason):")
        for u in held_tasks:
            wu_file = _resolve_unit_file(u)
            reason = _latest_hold_reason(wu_file.read_text(encoding="utf-8")) if wu_file is not None else ""
            reason_note = f"reason: {reason}" if reason else "reason not found in Comments"
            print(f"  {u.id} -- {u.title}  ({reason_note})")


def _print_blocked_rejection_categories(blocked_tasks: list[WorkUnit]) -> None:
    """Issue #156: surface pending review-judge category counts under blocked panels.

    For every blocked task with at least one outstanding rejection
    category, emit a row of the form::

        E0-F1-S1-T3 (3 unresolved categories: code_review:HARDCODED_URL, ...)

    Tasks with no outstanding categories are omitted entirely.
    """
    rows: list[tuple[str, list[tuple[str, str]]]] = []
    for u in blocked_tasks:
        wu_file = _resolve_unit_file(u)
        outstanding = _outstanding_rejection_categories(u.id, wu_file)
        if outstanding:
            rows.append((u.id, outstanding))
    if not rows:
        return
    print("\nReview-judge rejections (unresolved categories):")
    for unit_id, cats in rows:
        joined = ", ".join(f"{judge}:{code}" for judge, code in cats)
        print(f"  {unit_id} ({len(cats)} unresolved categories: {joined})")


_HOLD_COMMENT_RE: re.Pattern[str] = re.compile(r"\[HOLD\]\s+(.+?)(?:\n|$)")

# Issue #158: regex matching the structured-log line written by
# ``BacklogManager.force_status`` when a work unit is claimed:
#   2026-05-02T12:34:56Z [devbench.backlog_manager] INFO Set <id> to 'in-progress' in both ...
#
# Issue #293: this must match the transition RECORD, never a line that merely
# quotes it. The orchestrator logs whole SDK messages, and a tool result that
# read the work unit's ``[WU_CLAIMED]`` audit comment reproduces the phrase
# "Set <id> to 'in-progress'" inside a line stamped with the time of the DUMP.
# The previous pattern allowed ``.*`` between the timestamp and the phrase, so
# those echoes matched and, being later, won the max(). A unit claimed at
# 12:11 reported as claimed at 12:38, under-reporting its age by 27 minutes,
# and the error grew with every further echo. Anchoring to the emitting
# logger and level admits only the real record.
_LOG_PROGRESS_RE: re.Pattern[str] = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z \[devbench\.backlog_manager\] INFO Set (\S+) to 'in-progress'",
    re.MULTILINE,
)
# Fallback: agent-comment audit row of the form
#   [2026-05-02 12:34 EDT] [agent/orchestrator] Set <id> to 'in-progress'
# (the zone token is whatever display_timezone resolved to; UTC when unset)
# The zone token is captured rather than pinned to "UTC": comments are stamped
# in the workspace's ``display_timezone`` when one is set, so a workspace that
# configures it would otherwise stop matching here and lose the duration
# readout entirely. Files written before that setting existed still carry
# "UTC" and keep matching unchanged.
_AUDIT_PROGRESS_RE: re.Pattern[str] = re.compile(
    r"\[(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(?P<zone>[A-Za-z0-9_+\-]+)\]"
    r"[^\n]*?Set\s+(?P<id>\S+)\s+to\s+'in-progress'",
)


def _try_resolve_log_file_path() -> Path | None:
    """Best-effort log-file resolution that never raises.

    Wraps ``_resolve_log_file_path`` so callers that only need a *hint*
    of the log location (the status timer; the in-progress duration
    helper) degrade gracefully to ``None`` instead of propagating
    ``SystemExit`` when none of ``DEVBENCH_LOG_FILE`` / YAML ``log_file`` /
    ``DEVBENCH_WORKSPACE_ROOT`` is set. Issue #185: ``devbench status``
    previously printed ``timer unavailable`` whenever ``DEVBENCH_LOG_FILE``
    was unset even when the YAML config carried a usable ``log_file``;
    this wrapper closes that gap.
    """
    try:
        return _resolve_log_file_path()
    except SystemExit:
        return None


def _latest_log_in_progress_ts(task_id: str, log_path: Path | None) -> datetime | None:
    """Return the most recent ``Set <task_id> to 'in-progress'`` timestamp from the log."""
    candidate = log_path
    if candidate is None:
        candidate = _try_resolve_log_file_path()
    if candidate is None or not candidate.is_file():
        return None
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    latest: datetime | None = None
    for match in _LOG_PROGRESS_RE.finditer(content):
        if match.group(2) != task_id:
            continue
        try:
            ts = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def _latest_audit_in_progress_ts(task_id: str) -> datetime | None:
    """Return the most recent in-progress audit-comment timestamp for the task."""
    wu_file = _resolve_unit_file_by_id(task_id)
    if wu_file is None or not wu_file.is_file():
        return None
    try:
        wu_content = wu_file.read_text(encoding="utf-8")
    except OSError:
        return None
    latest: datetime | None = None
    for match in _AUDIT_PROGRESS_RE.finditer(wu_content):
        if match.group("id") != task_id:
            continue
        try:
            ts = audit_timestamp_to_utc(match.group("ts"), match.group("zone"))
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def _in_progress_attempt_duration(task_id: str, log_path: Path | None = None) -> str | None:
    """Return the humanized in-progress duration for ``task_id`` or ``None``.

    Issue #158. Reads the most recent ``Set <task_id> to 'in-progress'``
    transition timestamp from the structured log first; falls back to
    the work-unit file's audit-comment timestamp; returns ``None``
    when neither yields a parseable timestamp. Multiple in-progress
    transitions (blocked-then-resumed) resolve to the most recent one.
    """
    transition = _latest_log_in_progress_ts(task_id, log_path) or _latest_audit_in_progress_ts(task_id)
    if transition is None:
        return None
    elapsed = (datetime.now(UTC) - transition).total_seconds()
    return _format_duration(elapsed)


def _resolve_unit_file_by_id(task_id: str) -> Path | None:
    """Look up a work-unit file path by ID.

    Best-effort -- swallows parse errors and returns ``None`` when the
    backlog cannot be read. Used by the in-progress duration helper so
    a transient parse failure surfaces as ``timer unavailable`` rather
    than crashing ``status`` / ``report``.
    """
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError, OSError):
        return None
    for u in units:
        if u.id == task_id:
            return _resolve_unit_file(u)
    return None


def _latest_hold_reason(content: str) -> str:
    """Return the most recent ``[HOLD] <reason>`` text from a work-unit file.

    Used by ``status --detail`` to render the held-tasks panel. Walks
    Comments-style audit lines and returns the last match, since the
    audit log appends in chronological order.
    """
    matches = _HOLD_COMMENT_RE.findall(content)
    return matches[-1].strip() if matches else ""


def _build_scope_for_next(
    include_str: str,
    exclude_str: str,
    backlog_ids: list[str],
) -> tuple[ScopeFilter | None, str]:
    """Resolve the active scope filter for ``cmd_next``.

    Two resolution paths:

    1. **Per-command flags** (``include_str`` or ``exclude_str`` non-empty):
       expands tokens against ``backlog_ids`` via :meth:`ScopeFilter.parse`
       so ``expanded_ids`` is populated for candidate filtering (AC-190-11).

    2. **Active scope.json** (both strings empty): reads
       ``<WORKSPACE_ROOT>/.devbench/scope.json`` via
       :meth:`ScopeFilter.from_file`; returns ``None`` when the file is
       absent.

    Args:
        include_str: Raw ``--include`` token string (empty = not supplied).
        exclude_str: Raw ``--exclude`` token string (empty = not supplied).
        backlog_ids: All work-unit IDs from the parsed backlog; used only
            for the flag-based path to expand tokens.

    Returns:
        A two-tuple ``(scope_filter, error_message)``.  When
        ``error_message`` is non-empty the caller must print it to stderr
        and return rc=1.  When ``scope_filter`` is ``None`` and
        ``error_message`` is empty, no scope is active.

    Raises:
        None -- all exceptions are caught and returned as error messages.
    """
    if include_str or exclude_str:
        try:
            return (
                ScopeFilter.parse(
                    include_str=include_str,
                    exclude_str=exclude_str,
                    backlog_ids=backlog_ids,
                ),
                "",
            )
        except Exception as exc:
            return None, f"ERROR: invalid scope token: {exc}"

    # No flags -- consult scope.json.
    try:
        raw = _read_scope_banner_data(WORKSPACE_ROOT)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return None, f"ERROR: scope.json is corrupt and cannot be read: {exc}"
    if raw is None:
        return None, ""
    try:
        return ScopeFilter.from_file(WORKSPACE_ROOT), ""
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return None, f"ERROR: scope.json is corrupt and cannot be read: {exc}"


def _parse_next_argv(argv: tuple[str, ...]) -> tuple[str, str, int]:
    """Parse ``--include`` / ``--exclude`` flags for ``cmd_next``.

    Args:
        argv: Raw argument tuple from the CLI dispatcher.

    Returns:
        A three-tuple ``(include_str, exclude_str, exit_code)``.
        ``exit_code`` is non-zero when a flag is missing its value.
    """
    include_str = ""
    exclude_str = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--include", "--exclude"):
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                print(f"ERROR: {arg} requires a value", file=sys.stderr)
                return "", "", 1
            i += 1
            if arg == "--include":
                include_str = args[i]
            else:
                exclude_str = args[i]
        i += 1
    return include_str, exclude_str, 0


def _detect_units_dependency_cycle(units: list[WorkUnit]) -> str:
    """Return an ``A -> B -> C -> A`` cycle chain among ``units``' declared
    dependencies, or ``""`` when no cycle exists.

    FR-8 (db-253 Gap 2c): a cheap, in-memory companion to
    :meth:`BacklogManager._check_dep_cycles` -- the authoritative,
    file-scanning cycle check that ``validate-backlog`` runs. This helper
    walks ONLY the already-parsed ``unit.dependencies`` edges (no extra file
    I/O) via DFS-with-recursion-stack, so it stays cheap enough to run on
    every ``devbench next`` invocation. It is a diagnostic pointer at
    ``validate-backlog`` for the authoritative answer, not a replacement
    for it.
    """
    graph = {u.id: u.dependencies for u in units}
    color: dict[str, int] = dict.fromkeys(graph, 0)
    stack: list[str] = []
    chain = ""

    def visit(node: str) -> None:
        nonlocal chain
        color[node] = 1
        stack.append(node)
        for nxt in graph.get(node, ()):
            if chain:
                break
            if nxt not in color:
                continue
            if color[nxt] == 1:
                cycle_start = stack.index(nxt)
                cycle = stack[cycle_start:]
                chain = " -> ".join([*cycle, cycle[0]])
                break
            if color[nxt] == 0:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for node in sorted(graph):
        if chain:
            break
        if color.get(node) == 0:
            visit(node)
    return chain


def _no_actionable_diagnostic(units: list[WorkUnit]) -> str:
    """Build the FR-8 (db-253 Gap 2c) diagnostic line ``cmd_next`` appends
    after the terminal ``NO_ACTIONABLE`` token when nothing is actionable.

    Computed entirely from the already-parsed ``units`` list -- no extra
    file reads -- so the diagnostic is cheap on every invocation (D-R1-2).
    Names how many TASK units are still non-terminal, splits out the
    blocked and on-hold counts, and appends a dependency-cycle chain among
    them when one is detected, pointing the operator at
    ``validate-backlog`` and ``reconcile-cascade`` for the next step.
    """
    terminal = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    remaining = [u for u in units if u.unit_type is WorkUnitType.TASK and u.status not in terminal]
    blocked_count = sum(1 for u in remaining if u.status is WorkUnitStatus.BLOCKED)
    hold_count = sum(1 for u in remaining if u.status is WorkUnitStatus.HOLD)
    chain = _detect_units_dependency_cycle(remaining)
    cycle_clause = f", dependency cycle: {chain}" if chain else ""
    return (
        f"# {len(remaining)} unit(s) remain and none are actionable: "
        f"{blocked_count} blocked, {hold_count} on hold{cycle_clause}. "
        "Run 'devbench validate-backlog' and 'devbench reconcile-cascade' to diagnose."
    )


def cmd_next(*argv: str) -> int:
    """Print the next actionable work unit.

    Accepts scope-filter flags (spec section 4.2.2, AC-190-10, AC-190-11):

    - ``--include "<tokens>"`` -- one-off include selector; overrides active
      scope.json when present.
    - ``--exclude "<tokens>"`` -- one-off exclude selector.

    When neither flag is supplied, the active ``scope.json`` (if any) is
    consulted instead.  When a scope is active and no candidates match, prints
    ``NO_ACTIONABLE_IN_SCOPE`` and returns 0 (AC-190-15).

    FR-8 (db-253 Gap 2c, AC-16): when no scope is active and nothing is
    actionable, line 1 stays the literal ``NO_ACTIONABLE`` token (existing
    consumers, including ``_is_terminal_orchestrate_result``, match on this
    substring) and a second diagnostic line names the cause -- how many
    units remain, the blocked/hold split, and any detected dependency-cycle
    chain -- via :func:`_no_actionable_diagnostic`.

    Args:
        *argv: Optional flag arguments (``--include``, ``--exclude``).

    Returns:
        0 on success or scope-exhausted; 1 on scope-resolution error.
    """
    include_str, exclude_str, flag_rc = _parse_next_argv(argv)
    if flag_rc != 0:
        return flag_rc

    backlog_parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = backlog_parser.parse_index()
    all_ids = [u.id for u in units]

    scope_filter, error = _build_scope_for_next(include_str, exclude_str, all_ids)
    if error:
        print(error, file=sys.stderr)
        return 1

    candidates = backlog_parser.get_parallel_candidates(units, scope=scope_filter)

    if not candidates:
        if scope_filter is not None:
            print("NO_ACTIONABLE_IN_SCOPE")
        elif backlog_parser.all_done(units):
            print("ALL_DONE")
        else:
            print("NO_ACTIONABLE")
            print(_no_actionable_diagnostic(units))
        return 0

    unit = candidates[0]

    print(
        json.dumps(
            {
                "id": unit.id,
                "title": unit.title,
                "repo": unit.repo,
                "file_path": str(unit.file_path),
                "dependencies": unit.dependencies,
            }
        )
    )
    return 0


def cmd_claim(unit_id: str) -> int:
    """Transition a work unit to in-progress (claim it for execution).

    Use after ``devbench next`` to explicitly mark the unit as owned before
    the executor begins work.

    Issue #117: refuses to claim a work unit whose Changes Manifest still
    contains a ``TBD`` placeholder row. The orchestrator catches this at
    claim time and either auto-amends (when ``manifest_amendment.enabled:
    true``) or surfaces the failure to the operator -- either way the
    placeholder never reaches the executor.

    Also refuses to claim when the unit's target checkout holds uncommitted
    changes outside this unit's Changes Manifest. In the single-branch modes
    every work unit shares one checkout, so a unit that blocked before
    committing leaves its work in the tree for the next unit to inherit --
    which is how a sibling's files end up committed under the wrong unit's
    message, and how judges come to reject a unit over code it does not own.
    Re-claiming an ``in-progress`` unit is unaffected: the unit's own
    manifest files are allowed to be dirty.

    Spec 4.4.2 / AC-192-5: wraps the status write in an exclusive
    ``flock(BACKLOG.lock)`` so that two concurrent sessions cannot claim the
    same work unit.  Under the lock the current on-disk status is re-read; if
    the status is no longer ``in-queue`` or ``in-progress`` (i.e. another
    session won the race) a ``ClaimRaceError`` is raised and the function
    returns 1 without writing anything.  When ``DEVBENCH_SESSION_NAME`` is
    set the named session is stamped in the ``[WU_CLAIMED]`` audit comment.

    Raises:
        SystemExit: Never -- all errors are reported via stderr + return code.
    """
    units = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX).parse_index()
    unit = next((u for u in units if u.id == unit_id), None)
    if unit is None:
        print(f"ERROR: unit '{unit_id}' not found in backlog index", file=sys.stderr)
        return 1
    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        print(f"ERROR: work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1
    # Use a fresh import path so the unit-test layer's
    # `patch("devbench.cli.BacklogManager", ...)` does not stub out the
    # placeholder-detection classmethod via attribute lookup on the mock.
    from devbench.backlog.manager import BacklogManager as _BacklogManager

    placeholder = _BacklogManager._first_placeholder_manifest_cell(wu_file.read_text(encoding="utf-8"))
    if placeholder:
        print(
            f"ERROR: cannot claim {unit_id!r}: Changes Manifest still has placeholder row "
            f"{placeholder!r}. Replace with real file entries before claim.",
            file=sys.stderr,
        )
        return 1

    scope_error = _prepare_worktree_for_claim(unit, wu_file, unit_id)
    if scope_error is not None:
        print(scope_error, file=sys.stderr)
        return 1

    session_name: str | None = os.environ.get("DEVBENCH_SESSION_NAME", "").strip() or None
    error_message = _claim_under_lock(wu_file, unit_id, session_name)
    if error_message is not None:
        print(error_message, file=sys.stderr)
        return 1

    logger.info("Claimed %s (set to in-progress)", unit_id)
    print(f"Claimed {unit_id}")
    return 0


def _prepare_worktree_for_claim(unit: WorkUnit, wu_file: Path, unit_id: str) -> str | None:
    """Clear another unit's uncommitted work out of the shared checkout, then allow the claim.

    devbench's single-branch modes run every work unit in one shared
    checkout. A unit that blocks, or a run that is interrupted, leaves its
    uncommitted changes in the tree, and the next unit to claim inherits
    them: its commit absorbs a sibling's files under the wrong unit's
    message, and the review judges reject it over code it does not own and
    cannot fix.

    devbench runs unattended, so the residue is quarantined rather than
    reported: each foreign path is stashed under the ID of the unit whose
    Changes Manifest declares it, and the claim proceeds against a checkout
    holding only the claiming unit's scope. Halting here would convert one
    blocked unit into a stopped run.

    Quarantine is non-destructive. Each stash entry carries a discoverable
    ``devbench-quarantine:<owner-id>`` message and stays recoverable via
    ``git stash list``.

    Displaced work is handed back before the scan runs: when the claiming unit
    is itself a previously-displaced owner, ``restore_quarantine`` returns its
    entry to the tree first, so the unit resumes from the attempt it already
    paid for instead of re-executing from an empty checkout. The restore is
    bounded by the unit's own Changes Manifest and refuses to overwrite a
    newer attempt, so it cannot reintroduce the contamination the quarantine
    removes; a refusal leaves the entry intact and blocks the claim rather
    than silently discarding an expensive attempt.

    Re-claiming an ``in-progress`` unit is unaffected, because the unit's own
    manifest files are in scope and are never quarantined.

    The check needs a checkout to inspect. When the unit's repo has no
    configured local path, or that path is not a git work tree, there is no
    shared checkout for this unit and the check does not apply; that is
    logged rather than passed over silently, and ``cmd_git_ops`` still fails
    fast on the same missing configuration at commit time.

    Args:
        unit: The work unit being claimed, used to resolve its target repo.
        wu_file: Absolute path to the work-unit ``.md`` file.
        unit_id: Work-unit identifier, recorded in each stash message.

    Returns:
        ``None`` when the checkout is ready for the claim, whether it was
        already clean or was cleared. A human-readable error string only when
        the quarantine itself failed, which is a genuine fault: proceeding
        would hand the claiming unit the contaminated tree the quarantine was
        supposed to clear.
    """
    from devbench.backlog.manifest import list_changed_files, parse_manifest
    from devbench.git_quarantine import quarantine_paths, restore_quarantine

    canonical_repo = resolve_repo(unit.repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None or not (repo_path / ".git").exists():
        logger.info(
            "claim scope check skipped for %s: no git checkout resolved for repo %r",
            unit_id,
            canonical_repo,
        )
        return None

    manifest_files = {row.file for row in parse_manifest(wu_file.read_text(encoding="utf-8"))}
    try:
        restored = restore_quarantine(repo_path, unit_id, manifest_files)
    except RuntimeError as exc:
        return f"ERROR: cannot prepare the checkout for {unit_id!r}: {exc}"
    if restored is not None:
        _log_quarantine_restore(restored)

    try:
        foreign = [path for path in list_changed_files(repo_path) if path not in manifest_files]
        if not foreign:
            return None
        _checkpoint_owners_before_quarantine(repo_path, foreign, canonical_repo)
        records = quarantine_paths(repo_path, foreign, _non_terminal_manifests(canonical_repo), unit_id)
        remaining = [path for path in list_changed_files(repo_path) if path not in manifest_files]
    except RuntimeError as exc:
        return f"ERROR: cannot prepare the checkout for {unit_id!r}: {exc}"

    if remaining:
        return (
            f"ERROR: cannot prepare the checkout for {unit_id!r}: {len(remaining)} path(s) are still "
            f"outside its Changes Manifest after quarantine: {remaining}. Refusing to claim rather than "
            "commit or review another work unit's changes under this one."
        )

    for record in records:
        _log_quarantine(record, unit_id)
    return None


def _checkpoint_owners_before_quarantine(repo_path: Path, foreign: list[str], canonical_repo: str) -> None:
    """Snapshot the checkout before foreign work is stashed out of it.

    One snapshot covers every displaced owner, because ``git stash create``
    captures the whole tree rather than a path subset. That is deliberately
    coarser than the per-owner stash entries: the checkpoint is a safety net
    for the case where the stash stack is lost, not the primary recovery path,
    and a single reachable commit holding everything is both cheaper and
    harder to lose than one ref per owner.

    Best-effort: a failed snapshot is logged and the quarantine proceeds. The
    stash is still written, so refusing to continue here would stop an
    unattended run to protect a backup of a backup.
    """
    from devbench.git_quarantine import checkpoint_work, group_paths_by_owner

    owners = [
        owner
        for owner in group_paths_by_owner(foreign, _non_terminal_manifests(canonical_repo))
        if owner != UNATTRIBUTED_OWNER
    ]
    if not owners:
        return
    try:
        sha = checkpoint_work(repo_path, sorted(owners)[0])
    except RuntimeError as exc:
        logger.warning("[CHECKPOINT_SKIPPED] pre-quarantine snapshot failed: %s", exc)
        return
    if sha:
        logger.info("[WORK_CHECKPOINTED] pre-quarantine snapshot %s covers %s", sha, sorted(owners))


def _non_terminal_manifests(canonical_repo: str) -> dict[str, list[str]]:
    """Return every non-terminal work unit's Changes Manifest for ``canonical_repo``.

    Used to attribute quarantined paths to the unit that declared them.
    Terminal units (``done`` / ``declined``) are excluded: their work is
    already committed, so they cannot be the source of uncommitted residue.

    Best-effort by design. A unit whose file is unreadable or whose Manifest
    is malformed contributes nothing rather than aborting the claim; the
    consequence is that its paths quarantine as ``unattributed``, which still
    clears the checkout. Manifest validity is validate-backlog's job, not the
    claim path's.
    """
    from devbench.backlog.manifest import ManifestParseError, parse_manifest

    manifests: dict[str, list[str]] = {}
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError, OSError):
        return manifests

    terminal = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    for candidate in units:
        if candidate.unit_type is not WorkUnitType.TASK or candidate.status in terminal:
            continue
        if resolve_repo(candidate.repo) != canonical_repo:
            continue
        candidate_file = _resolve_unit_file(candidate)
        if candidate_file is None or not candidate_file.is_file():
            continue
        try:
            rows = parse_manifest(candidate_file.read_text(encoding="utf-8"))
        except (ManifestParseError, OSError):
            continue
        manifests[candidate.id] = [row.file for row in rows]
    return manifests


def _log_quarantine(record: QuarantineRecord, claiming_unit_id: str) -> None:
    """Record one quarantine in the log and in the owning unit's audit trail.

    The audit comment is what tells the owning unit's next executor that its
    previous attempt was displaced and where to find it. Comment-write
    failures are logged and not raised: the checkout is already clear at this
    point, and losing an audit line must not stop an unattended run.
    """
    owner_id = record.owner_id
    paths = list(record.paths)
    stash_message = record.stash_message
    logger.info(
        "[WORK_QUARANTINED] %d path(s) owned by %s displaced by claim of %s; recover with git stash list | grep %r",
        len(paths),
        owner_id,
        claiming_unit_id,
        stash_message,
    )
    if owner_id == UNATTRIBUTED_OWNER:
        return
    try:
        owner_file = _resolve_unit_file_by_id(owner_id)
        if owner_file is None:
            return
        BacklogManager()._append_agent_comment(
            owner_file,
            "orchestrator",
            f"[WORK_QUARANTINED] {len(paths)} uncommitted path(s) from this unit were displaced from the "
            f"shared checkout when {claiming_unit_id} claimed: {paths}. They are preserved in a git stash "
            f"titled {stash_message!r} and are recoverable with 'git stash list'. They are restored "
            "automatically the next time this unit claims the checkout, so resume from them rather than "
            "re-executing this unit's Changes Manifest from scratch.",
        )
    except (OSError, ValueError) as exc:
        logger.warning("quarantine audit comment failed for %s: %s", owner_id, exc)


def _log_quarantine_restore(record: RestoreRecord) -> None:
    """Record that a unit's displaced work was handed back on its re-claim.

    The counterpart to :func:`_log_quarantine`. The owning unit's next
    executor reads this line to learn that its previous attempt is already in
    the tree, which is the difference between resuming a finished attempt and
    redoing every review round that produced it.

    Comment-write failures are logged and not raised, matching the quarantine
    side: the restore itself has already succeeded by this point, and losing
    an audit line must not stop an unattended run.
    """
    owner_id = record.owner_id
    paths = list(record.paths)
    logger.info(
        "[WORK_RESTORED] %d path(s) returned to %s from quarantine %r",
        len(paths),
        owner_id,
        record.stash_message,
    )
    try:
        owner_file = _resolve_unit_file_by_id(owner_id)
        if owner_file is None:
            return
        BacklogManager()._append_agent_comment(
            owner_file,
            "orchestrator",
            f"[WORK_RESTORED] {len(paths)} previously displaced path(s) were restored into the shared "
            f"checkout for this claim: {paths}. They come from the quarantine stash titled "
            f"{record.stash_message!r}, which has now been consumed. This is the attempt this unit had "
            "already produced, so verify and continue from it rather than starting the Changes Manifest "
            "over.",
        )
    except (OSError, ValueError) as exc:
        logger.warning("quarantine restore audit comment failed for %s: %s", owner_id, exc)


def _active_work_unit_marker_path(session_name: str | None) -> Path:
    """Return the active-work-unit marker path for the given session.

    Issue #336: ``guard-git-stage.sh`` rule 2 resolves the claimed work unit
    from this marker because hook processes inherit the long-lived
    orchestrator environment and cannot receive a per-work-unit environment
    variable.  Named sessions get a suffixed marker so concurrent sessions
    in one workspace never read each other's claim.

    Args:
        session_name: Optional named-session name from ``DEVBENCH_SESSION_NAME``.

    Returns:
        Absolute marker path under ``<workspace>/.devbench/``.
    """
    from devbench.constants import ACTIVE_WORK_UNIT_MARKER_PATH

    marker = WORKSPACE_ROOT / ACTIVE_WORK_UNIT_MARKER_PATH
    if session_name:
        marker = marker.with_name(f"{marker.name}-{session_name}")
    return marker


def _claim_under_lock(wu_file: Path, unit_id: str, session_name: str | None) -> str | None:
    """Acquire BACKLOG.lock, re-read status, and write ``in-progress`` under the lock.

    Returns an error message string when the claim fails (race, timeout, or missing
    status line), or ``None`` on success.  Keeps ``cmd_claim`` within the
    PLR0911 return-statement budget.

    On success, also records the claimed unit's file path in the
    active-work-unit marker (issue #336) so ``guard-git-stage.sh`` rule 2
    can enforce manifest scope on ``git add`` for the duration of the claim.
    The marker is written under the same ``BACKLOG.lock`` as the status
    transition, so the hook can never observe a claim without its marker.

    Args:
        wu_file: Absolute path to the work-unit ``.md`` file.
        unit_id: Work-unit identifier used in error messages and audit comments.
        session_name: Optional named-session name from ``DEVBENCH_SESSION_NAME``.

    Returns:
        ``None`` on success; a human-readable error string on failure.

    Raises:
        OSError: Unexpected OS error from ``fcntl.flock`` or from writing the
            active-work-unit marker (propagated to caller -- a claim whose
            marker cannot be recorded must fail loudly, not degrade the
            guard silently).
    """
    try:
        with flock_backlog(WORKSPACE_ROOT):
            # Re-read the on-disk status under the lock to detect concurrent claims.
            current_content = wu_file.read_text(encoding="utf-8")
            status_match = BACKLOG_STATUS_RE.search(current_content)
            if status_match is None:
                return f"ERROR: cannot claim {unit_id!r}: no '## Status:' line found in {wu_file}"
            current_status = status_match.group(1).strip().lower()
            # Only in-queue or in-progress (resume) are valid claim targets.
            if current_status not in (STATUS_IN_QUEUE, STATUS_IN_PROGRESS):
                raise ClaimRaceError(
                    unit_id=unit_id,
                    expected_status=STATUS_IN_QUEUE,
                    actual_status=current_status,
                )
            mgr = BacklogManager()
            mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_IN_PROGRESS, session_name=session_name)
            marker = _active_work_unit_marker_path(session_name)
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(marker, f"{wu_file.resolve()}\n")
    except ClaimRaceError as exc:
        return (
            f"ERROR: claim race on {unit_id!r} -- another session already changed the status. "
            f"Expected '{exc.expected_status}' but found '{exc.actual_status}' under lock. "
            f"Skip this unit and pick the next candidate."
        )
    except TimeoutError as exc:
        return f"ERROR: could not acquire BACKLOG.lock to claim {unit_id!r}: {exc}"
    return None


def cmd_set_status(*argv: str) -> int:
    """Set the status of one or more work units.

    Supports two invocation forms:

    **Single-ID form** (unchanged behaviour, AC-194-2)::

        devbench set-status <id> <new_status>

    **Bulk form** using printer-pages scope selectors (AC-194-1, AC-194-10)::

        devbench set-status --include "<tokens>" [--exclude "<tokens>"] [--dry-run] [--yes] <new_status>

    The ``--include`` / ``--exclude`` tokens are parsed via
    :meth:`~devbench.scope.ScopeFilter.parse` (no parser duplication).
    Every matching work-unit file is updated with
    :meth:`~devbench.backlog.manager.BacklogManager.force_status` so
    per-WU audit logic continues to fire.  A workspace-level
    ``[BULK_STATUS_UPDATE]`` info log records each bulk invocation.

    When the matched count exceeds
    ``RUNTIME_CONFIG.backlog.bulk_update_confirm_threshold`` (default 10),
    the operator is prompted for confirmation unless ``--yes`` is supplied
    (AC-194-4).  ``--yes`` is intended for non-interactive scripts.

    When ``--dry-run`` is present, no files are written; instead one line
    per affected work unit is printed as ``{id}\\t{current_status}\\t{new_status}``
    (AC-194-3).

    Args:
        *argv: Parsed CLI tokens.  Either ``(<id>, <status>)`` for the
            single-ID form, or ``("--include", "<tokens>", <status>)`` /
            ``("--include", "<tokens>", "--exclude", "<tokens>", <status>)`` /
            ``("--dry-run", "--include", "<tokens>", <status>)``
            for the bulk form.

    Returns:
        0 on success.  1 on any error (invalid status, unknown unit,
        missing file, invalid scope token, no matching units, missing
        positional status argument).

    Raises:
        Nothing -- all errors are reported to stderr and return rc=1.
    """
    args = list(argv)

    # Detect bulk mode: --include flag present anywhere in args.
    if "--include" in args:
        return _cmd_set_status_bulk(args)

    # Single-ID form: exactly 2 positional args.
    if len(args) != 2:
        print(
            "ERROR: set-status usage: set-status <id> <status>  OR  "
            "set-status --include '<tokens>' [--exclude '<tokens>'] [--dry-run] [--yes] <status>",
            file=sys.stderr,
        )
        return 1

    return _cmd_set_status_single(args[0], args[1])


def _cmd_set_status_single(unit_id: str, new_status: str) -> int:
    """Set the status of a single work unit by ID.

    Args:
        unit_id: The work-unit identifier.
        new_status: Target status string (CLI form or title-case).

    Returns:
        0 on success, 1 on any error.

    Raises:
        Nothing -- all errors reported to stderr and return rc=1.
    """
    from devbench.backlog.manager import VALID_STATUSES

    if new_status.lower() not in VALID_STATUSES:
        print(
            f"ERROR: Invalid status '{new_status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return 1

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found", file=sys.stderr)
        return 1

    if new_status.lower() == STATUS_DRAFT and target.unit_type is not WorkUnitType.TASK:
        print(
            f"ERROR: Status 'draft' is only valid for Task work units; "
            f"'{unit_id}' is type {target.unit_type.value}. "
            f"Epics, Features, and Stories cannot be set to 'draft'.",
            file=sys.stderr,
        )
        return 1

    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1

    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, new_status)

    logger.info("Set %s to %s", unit_id, new_status)

    if new_status.lower() == "blocked":
        rc = _clean_target_repo_on_block(wu_file, unit_id)
        if rc != 0:
            print(f"WARNING: target repo cleanup failed for '{unit_id}' (exit {rc})", file=sys.stderr)

    print(f"Set {unit_id} to {new_status}")
    return 0


def _parse_bulk_set_status_args(
    args: list[str],
) -> tuple[str, str, bool, bool, list[str]] | int:
    """Parse ``--include`` / ``--exclude`` / ``--dry-run`` / ``--yes`` from a bulk set-status arg list.

    Args:
        args: Raw CLI token list for the bulk-update path.

    Returns:
        A 5-tuple ``(include_str, exclude_str, dry_run, yes_flag, remaining_positionals)``
        on success, or ``1`` (int) if a parse error occurred (error message
        already printed to stderr).

    Raises:
        Nothing -- all errors reported to stderr and return the integer ``1``.
    """
    include_str = ""
    exclude_str = ""
    dry_run = False
    yes_flag = False
    remaining: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--include", "--exclude"):
            if i + 1 >= len(args):
                print(
                    f"ERROR: '{arg}' requires a value (e.g. --include 'E1,E2')",
                    file=sys.stderr,
                )
                return 1
            if arg == "--include":
                include_str = args[i + 1]
            else:
                exclude_str = args[i + 1]
            i += 2
            continue
        if arg == "--dry-run":
            dry_run = True
            i += 1
            continue
        if arg == "--yes":
            yes_flag = True
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return include_str, exclude_str, dry_run, yes_flag, remaining


def _print_dry_run_bulk(matched: list, new_status: str) -> None:
    """Print one tab-separated line per matched unit for ``--dry-run`` mode (AC-194-3).

    Each line has the form ``{id}\\t{current_status}\\t{new_status}`` where
    ``current_status`` is the CLI form (lower-case, hyphen-separated).

    Args:
        matched: Work units that would be updated.
        new_status: The target status CLI string.

    Raises:
        Nothing.
    """
    for unit in matched:
        current_status_cli = unit.status.value.lower().replace(" ", "-")
        print(f"{unit.id}\t{current_status_cli}\t{new_status}")


def _resolve_bulk_matched_units(
    include_str: str,
    exclude_str: str,
    new_status: str,
) -> list | None:
    """Validate *new_status* and resolve the matched work-unit list for a bulk update.

    Enforces that ``draft`` may only be applied to Task-level work units (AC-194-9,
    AC-189-10): if any matched unit has type Epic, Feature, or Story and the target
    status is ``draft``, the entire batch is rejected before any file is written.

    The ``InvalidScopeError`` format mirrors ``cmd_start``/``cmd_next``'s
    ``--include`` error path verbatim (AC-194-8): ``"ERROR: invalid scope token: ..."``.

    Args:
        include_str: Raw ``--include`` token string.
        exclude_str: Raw ``--exclude`` token string.
        new_status: Target status CLI string.

    Returns:
        Non-empty list of :class:`~devbench.backlog.work_unit.WorkUnit` objects
        selected by the scope tokens on success.  ``None`` when validation fails
        (error message already printed to stderr).

    Raises:
        Nothing -- all errors reported to stderr and return ``None``.
    """
    from devbench.backlog.manager import VALID_STATUSES

    if new_status.lower() not in VALID_STATUSES:
        print(
            f"ERROR: Invalid status '{new_status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return None

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    all_ids = [u.id for u in units]

    try:
        scope = ScopeFilter.parse(include_str, exclude_str, all_ids)
    except InvalidScopeError as exc:
        print(f"ERROR: invalid scope token: {exc}", file=sys.stderr)
        return None

    matched = [u for u in units if scope.allows(u.id)]
    if not matched:
        print(
            f"ERROR: no work units matched --include={include_str!r} (exclude={exclude_str!r})",
            file=sys.stderr,
        )
        return None

    if new_status.lower() == STATUS_DRAFT:
        non_task = [u for u in matched if u.unit_type is not WorkUnitType.TASK]
        if non_task:
            offenders = ", ".join(f"'{u.id}' ({u.unit_type.value})" for u in non_task)
            print(
                f"ERROR: Status 'draft' is only valid for Task work units; "
                f"matched non-Task unit(s): {offenders}. "
                f"Epics, Features, and Stories cannot be set to 'draft'.",
                file=sys.stderr,
            )
            return None

    return matched


def _cmd_set_status_bulk(args: list[str]) -> int:
    """Bulk-update work-unit statuses using printer-pages scope selectors.

    Parses ``--include`` / ``--exclude`` / ``--dry-run`` / ``--yes`` flags
    from ``args`` and resolves matching work-unit IDs via
    :meth:`~devbench.scope.ScopeFilter.parse`.

    When ``--dry-run`` is present, prints one line per matched work unit in
    the format ``{id}\\t{current_status}\\t{new_status}`` and returns 0
    without writing any files (AC-194-3).

    When the matched count exceeds
    ``RUNTIME_CONFIG.backlog.bulk_update_confirm_threshold``, the operator is
    prompted for confirmation unless ``--yes`` is provided (AC-194-4).
    Declining the prompt returns 0 without writing any files.

    Without ``--dry-run``, every matched unit is updated through
    :meth:`~devbench.backlog.manager.BacklogManager.force_status`.
    Units whose on-disk file cannot be resolved are skipped with a
    per-unit warning; the batch continues.

    A workspace-level ``[BULK_STATUS_UPDATE]`` info log entry records the
    invocation count, target status, and the raw include/exclude tokens.

    Args:
        args: Full argument list (includes the ``--include`` flag and its
            value, optional ``--exclude`` flag and value, optional
            ``--dry-run`` boolean flag, optional ``--yes`` boolean flag,
            and the trailing positional ``<new_status>``).

    Returns:
        0 on success (at least one unit matched; no writes performed in
        dry-run mode or when the operator declines the prompt; or at least
        one unit updated otherwise).  1 on any error (invalid status,
        invalid scope token, no matching work units).

    Raises:
        Nothing -- all errors reported to stderr and return rc=1.
    """
    parse_result = _parse_bulk_set_status_args(args)
    if isinstance(parse_result, int):
        return parse_result
    include_str, exclude_str, dry_run, yes_flag, remaining = parse_result

    if not remaining:
        print(
            "ERROR: set-status --include requires a trailing <status> positional argument",
            file=sys.stderr,
        )
        return 1

    new_status = remaining[0]
    matched = _resolve_bulk_matched_units(include_str, exclude_str, new_status)
    if matched is None:
        return 1

    if dry_run:
        _print_dry_run_bulk(matched, new_status)
        return 0

    threshold = RUNTIME_CONFIG.backlog.bulk_update_confirm_threshold
    count = len(matched)
    if count > threshold and not yes_flag:
        answer = input(f"About to update {count} work units. Continue? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Bulk update aborted.")
            return 0

    _apply_bulk_set_status(matched, new_status, include_str, exclude_str)
    return 0


def _apply_bulk_set_status(
    matched: list,
    new_status: str,
    include_str: str,
    exclude_str: str,
) -> None:
    """Delegate the bulk status update to :meth:`~devbench.backlog.manager.BacklogManager.bulk_set_status`.

    Resolves each work-unit's on-disk file path, warns and skips any whose
    file cannot be found, then calls :meth:`bulk_set_status` exactly once so
    that the entire batch is serialised under a single ``flock(BACKLOG.lock)``
    (AC-194-5).  The ``[BULK_STATUS_UPDATE]`` audit row is written by
    ``bulk_set_status`` to the path derived from
    ``RUNTIME_CONFIG.backlog.bulk_update_audit_path`` resolved relative to
    ``WORKSPACE_ROOT`` (AC-194-6).

    Args:
        matched: Work units selected for update.
        new_status: Target status CLI string.
        include_str: Raw ``--include`` token (for audit log).
        exclude_str: Raw ``--exclude`` token (for audit log).

    Raises:
        ValueError: ``new_status`` is not a recognised status value (propagated
            from :meth:`bulk_set_status`).
        FileNotFoundError: A work-unit file or ``BACKLOG_INDEX`` does not exist
            (propagated from :meth:`bulk_set_status`).
        TimeoutError: The BACKLOG.lock could not be acquired within the default
            timeout (propagated from :meth:`bulk_set_status`).
        OSError: An unexpected OS error from ``fcntl.flock`` or file I/O
            (propagated from :meth:`bulk_set_status`).
    """
    unit_ids: list[tuple[str, Path]] = []
    for unit in matched:
        wu_file = _resolve_unit_file(unit)
        if wu_file is None:
            logger.warning("set-status bulk: file not found for '%s'; skipping", unit.id)
            print(
                f"WARNING: work unit file not found for '{unit.id}'; skipping",
                file=sys.stderr,
            )
            continue
        unit_ids.append((unit.id, wu_file))

    audit_log_path = WORKSPACE_ROOT / RUNTIME_CONFIG.backlog.bulk_update_audit_path
    audit_meta = f"--include={include_str!r} --exclude={exclude_str!r}"

    mgr = BacklogManager()
    updated = mgr.bulk_set_status(unit_ids, new_status, BACKLOG_INDEX, audit_log_path, audit_meta=audit_meta)

    print(
        f"Bulk set-status: updated {updated} work unit(s) to '{new_status}' "
        f"(--include={include_str!r} --exclude={exclude_str!r})"
    )


def _clean_target_repo_on_block(wu_file: Path, unit_id: str) -> int:
    """Quarantine the target repo's working tree when a task transitions to blocked.

    A shared single-branch checkout must not carry a blocked task's residue
    into the next unit's claim, but clearing it MUST NOT destroy it. This
    previously ran ``git reset --hard HEAD`` plus ``git clean -fd``, which
    annihilated every uncommitted change in the target repo -- including work
    that was complete and verified but not yet committed, since the executor
    stages and leaves committing to ``devbench git-ops``. The destruction was
    unconditional, irreversible, and silent, and it made ``blocked`` a status
    the orchestrator had to actively avoid: an observed run chose ``hold`` over
    ``blocked`` specifically to keep a finished task's work alive.

    Now delegates to :func:`~devbench.git_quarantine.quarantine_paths` -- the
    same non-destructive primitive claim-time quarantine already uses
    (:func:`_prepare_worktree_for_claim`) -- so the tree is cleared into
    recoverable ``git stash`` entries, one per owning unit, discoverable via
    ``git stash list``. Blocking a task can no longer lose work, and the two
    "clear this shared checkout" paths now share one implementation instead of
    disagreeing about whether the work survives.

    If ``Local path:`` is absent from the file (e.g. validation gates with no
    local path), logs a warning and returns 0 as a defensive skip -- this is
    NOT a fallback; the task is already blocked, so failing here would obscure
    the real status transition.

    Args:
        wu_file: Path to the work-unit ``.md`` file.
        unit_id: The blocking unit's ID, recorded in each stash message so the
            audit trail shows which block triggered the quarantine.

    Returns:
        0 on success, when the tree is already clean, or when ``Local path:``
        is absent; 1 when the work-unit file is unreadable or the quarantine
        could not clear the tree.
    """
    try:
        content = wu_file.read_text()
    except OSError as exc:
        logger.warning("_clean_target_repo_on_block: cannot read '%s': %s", wu_file, exc)
        return 1

    match = BACKLOG_LOCAL_PATH_RE.search(content)
    if not match:
        logger.warning(
            "_clean_target_repo_on_block: 'Local path:' not found in '%s'; skipping repo cleanup",
            wu_file,
        )
        return 0

    local_path = match.group(1).strip()
    if not Path(local_path).exists():
        logger.warning(
            "_clean_target_repo_on_block: local path '%s' does not exist; skipping repo cleanup",
            local_path,
        )
        return 0

    from devbench.backlog.manifest import list_changed_files
    from devbench.git_quarantine import quarantine_paths

    repo_path = Path(local_path)
    changed = list_changed_files(repo_path)
    if not changed:
        logger.info("_clean_target_repo_on_block: target repo at '%s' is already clean", local_path)
        return 0

    repo_match = BACKLOG_REPO_RE.search(content)
    canonical_repo = resolve_repo(repo_match.group(1).strip()) if repo_match else ""
    try:
        records = quarantine_paths(
            repo_path,
            changed,
            _non_terminal_manifests(canonical_repo) if canonical_repo else {},
            unit_id,
        )
    except RuntimeError as exc:
        logger.warning("_clean_target_repo_on_block: quarantine failed for '%s': %s", local_path, exc)
        return 1

    for record in records:
        logger.info(
            "%s%s owner=%s paths=%d stash=%s",
            ORCHESTRATOR_BLOCK_QUARANTINE_AUDIT_PREFIX,
            unit_id,
            record.owner_id,
            len(record.paths),
            record.stash_message,
        )
    logger.info(
        "_clean_target_repo_on_block: quarantined %d path(s) out of '%s' into %d recoverable stash entry(ies)",
        len(changed),
        local_path,
        len(records),
    )
    return 0


def cmd_mark_done(unit_id: str) -> int:
    """Mark a work unit as Done, enforcing the done-gate check.

    Calls :meth:`BacklogManager.mark_done`, which itself enforces two
    invariants before writing ``STATUS_DONE`` anywhere (E4-F4-S1-T2 round
    3 moved both out of this CLI-layer wrapper and into ``mark_done``
    directly, via ``_check_task_type_done_invariant``, so every caller --
    this command and ``devbench check-merge``'s merged-PR path alike --
    inherits them identically; this docstring described the pre-round-3
    architecture, where the checks ran here before delegating, until
    doc_review FAIL round 4 flagged the staleness):

    - **Done-gate** (FR-4.4): all required review judges must have passed
      in the most recent review round.
    - **FR-4.5/FR-4.6 task-type completion invariant** (AC-60 /
      AC-E4-F4-S1-T2-4): a gated task (``## Task Type:`` absent --
      defaults to ``DEFAULT_TASK_TYPE``, the strictest type, per the same
      fail-closed precedent as ``BacklogManager._check_task_type_taxonomy``
      -- or explicitly ``behavior-fix``/``feature``) must carry a
      machine-observed ``RED_OBSERVED`` record (``red_gate_satisfied``);
      a ``refactor`` task is exempt from the RED gate but not from its
      own invariant and must instead carry a machine-observed
      ``GREEN_GREEN_OBSERVED`` record (``green_green_observed_satisfied``),
      written only by a passing ``devbench green-green-check`` run.
      Without the applicable record, ``mark_done`` raises ``RuntimeError``
      rather than claiming a fix or a behavior-preservation guarantee
      never actually proved: honest exits are a genuine RED, a re-type,
      an already-satisfied decline, or (for ``refactor``) a passing
      ``green-green-check`` run -- never a silent done.

    This function catches that ``RuntimeError`` and reports it on stderr
    with exit code 1; it performs no invariant checking of its own.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found", file=sys.stderr)
        return 1

    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1

    # Issue #156: prior review-judge rejections must be either resolved
    # ([REJECTION_FEEDBACK_RESOLVED] audit logged) or escalated via
    # [NEEDS_DEP] before the task can transition to done. Otherwise the
    # done-gate refuses with a clear actionable error and emits a
    # [REJECTION_FEEDBACK_OUTSTANDING] audit so subsequent runs can see why.
    outstanding = _outstanding_rejection_categories(unit_id, wu_file)
    if outstanding:
        mgr_audit = BacklogManager()
        joined = ", ".join(f"{j}:{c}" for j, c in outstanding)
        mgr_audit._append_agent_comment(
            wu_file,
            "orchestrator",
            f"[REJECTION_FEEDBACK_OUTSTANDING] {joined}",
        )
        print(
            f"ERROR: Cannot mark {unit_id} done: unresolved review-judge categories: {joined}. "
            "Address each in the diff and log [REJECTION_FEEDBACK_RESOLVED] <judge>:<code>, "
            "or escalate via [NEEDS_DEP] <judge>:<code>.",
            file=sys.stderr,
        )
        return 1

    mgr = BacklogManager()
    try:
        mgr.mark_done(wu_file, BACKLOG_INDEX, unit_id)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mgr._append_agent_comment(wu_file, "orchestrator", f"[DONE] Work unit {unit_id} completed")

    logger.info("Marked %s as done", unit_id)
    print(f"Marked {unit_id} as done")
    return 0


def cmd_decline(*argv: str) -> int:
    """Mark a work unit as Declined (won't ever be done) with a captured reason.

    Usage::

        decline <id> --reason "<message>" [--citation <commit-hash-or-task-id>]

    Declined is a deliberate final-decision status, distinct from Blocked
    (waiting on something) and Done (completed). Declined children count
    as terminal-complete for parent rollup. The ``--reason`` is REQUIRED
    because the decision must leave an audit trail; em-dashes are
    rejected at the input boundary for backlog hygiene.

    FR-4.5 (AC-E4-F4-S1-T2-2/3): when *reason* names remedy 3
    (``already-satisfied``), the decline is an unfalsifiable claim without
    proof it was checked, so ``--citation`` (the closing commit hash or
    task id, validated by ``BacklogManager.is_valid_citation``) is
    REQUIRED too. On success the citation is folded into the persisted
    reason (``"... (citing <value>)"``) so ``BacklogManager``'s
    comment-format check (which re-reads the persisted ``[DECLINED]``
    comment, not this command's argv) can verify it independently.
    """
    parsed = _parse_decline_argv(argv)
    if isinstance(parsed, int):
        return parsed
    task_id, reason, citation = parsed

    rc = _reject_em_dash("reason", reason)
    if rc is not None:
        return rc

    persisted_reason = _resolve_decline_persisted_reason(task_id, reason, citation)
    if persisted_reason is None:
        return 1

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    target = _find_unit(units, task_id)
    if target is None:
        print(f"ERROR: Work unit '{task_id}' not found", file=sys.stderr)
        return 1
    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{task_id}'", file=sys.stderr)
        return 1

    BacklogManager().mark_declined(wu_file, BACKLOG_INDEX, task_id, persisted_reason)
    logger.info("Declined %s: %s", task_id, persisted_reason)
    print(json.dumps({"task_id": task_id, "status": "declined", "reason": persisted_reason}))
    return 0


def _parse_decline_argv(argv: tuple[str, ...] | list[str]) -> tuple[str, str, str] | int:
    """Parse ``decline`` argv into ``(task_id, reason, citation)``.

    Returns the parsed triple when ``<id>`` and ``--reason`` are both
    present, or an integer non-zero exit code when parsing failed (the
    error message is already on stderr). Mirrors the ``_parse_new_task_argv``
    idiom so the many single-purpose validation branches live in a
    dedicated parser rather than inflating ``cmd_decline``'s own
    return/branch count.
    """
    task_id = ""
    reason = ""
    citation = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--reason":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --reason requires a value", file=sys.stderr)
                return 1
            reason = args[i + 1]
            i += 2
            continue
        if arg == "--citation":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --citation requires a value", file=sys.stderr)
                return 1
            citation = args[i + 1]
            i += 2
            continue
        if not task_id:
            task_id = arg
        i += 1
    if not task_id or not reason:
        print("ERROR: decline requires <id> --reason <message>", file=sys.stderr)
        return 1
    return task_id, reason, citation


def _resolve_decline_persisted_reason(task_id: str, reason: str, citation: str) -> str | None:
    """Return the reason text to persist, or ``None`` when an already-satisfied decline lacks a citation.

    FR-4.5 (AC-E4-F4-S1-T2-2/3): when *reason* names remedy 3
    (``already-satisfied``), the decline is an unfalsifiable claim without
    proof it was checked, so a valid ``citation`` (the closing commit hash
    or task id, validated by ``BacklogManager.is_valid_citation``) is
    REQUIRED too. On success the citation is folded into the returned
    reason (``"... (citing <value>)"``) so ``BacklogManager``'s
    comment-format check (which re-reads the persisted ``[DECLINED]``
    comment, not this command's argv) can verify it independently. On
    failure the three-remedy rejection message is already printed to
    stderr before ``None`` is returned.
    """
    if BacklogManager._ALREADY_SATISFIED_TOKEN not in reason.lower():
        return reason
    if not citation or not BacklogManager.is_valid_citation(citation):
        message = _build_remedies_rejection_message(
            f"ERROR: Cannot decline {task_id} as already-satisfied without a valid citation.",
            "FR-4.5 requires an already-satisfied decline to cite the closing commit hash "
            "(7-40 lowercase hex characters) or task id via --citation <value>; an uncited "
            "already-satisfied decline is as unfalsifiable as a fabricated RED.",
        )
        print(message, file=sys.stderr)
        return None
    return f"{reason} (citing {citation})"


_TEMPLATE_KIND_BY_ID_SHAPE: dict[str, str] = {
    "T": "task",
    "S": "story",
    "F": "feature",
    "E": "epic",
}


def cmd_new_task(*argv: str) -> int:
    """Scaffold a new work-unit ``.md`` file from a canonical template.

    Usage::

        new-task --id <ID> --title "<TITLE>" --target <PATH>
                 [--repo <ORG/REPO>] [--description <TEXT>]
                 [--source-file <PATH>] [--test-file <PATH>]
                 [--ac-func <TEXT>]

    Required flags:
      ``--id``: canonical work-unit ID (e.g. ``E0-F1-S1-T1``). The ID's
        last segment determines which template is rendered: ``T`` -> task,
        ``S`` -> story, ``F`` -> feature, ``E`` -> epic.
      ``--title``: human-readable title.
      ``--target``: absolute path where the new ``.md`` file will be
        written. Fails fast if the file already exists or the parent
        directory is missing.

    Optional flags substitute the matching ``{{TOKEN}}`` placeholders
    in the template; tokens with no matching flag are filled with a
    sensible default (e.g. ``--ac-func`` -> ``"TBD"``,
    ``--source-file`` -> ``src/<repo-name>/<id-lower>.py``).
    """
    parsed = _parse_new_task_argv(argv)
    if isinstance(parsed, int):
        return parsed
    fields = parsed

    target = Path(fields["target"])
    if target.exists():
        print(f"ERROR: target {str(target)!r} already exists; refusing to overwrite", file=sys.stderr)
        return 1
    if not target.parent.is_dir():
        print(
            f"ERROR: parent directory {str(target.parent)!r} does not exist; create it first",
            file=sys.stderr,
        )
        return 1

    kind = _template_kind_for_id(fields["id"])
    if kind is None:
        print(
            f"ERROR: cannot derive template kind from id {fields['id']!r}; "
            f"expected last segment to start with T/S/F/E.",
            file=sys.stderr,
        )
        return 1
    template_path = _devbench_root() / "backlog" / "templates" / f"{kind}.md"
    if not template_path.is_file():
        print(f"ERROR: template not found at {str(template_path)!r}", file=sys.stderr)
        return 1
    template = template_path.read_text(encoding="utf-8")
    rendered = _render_new_task_template(template, fields)
    atomic_write_text(target, rendered)
    print(json.dumps({"id": fields["id"], "kind": kind, "target": str(target)}))
    logger.info("Scaffolded %s %s at %s", kind, fields["id"], target)
    return 0


def _parse_new_task_argv(argv: tuple[str, ...] | list[str]) -> dict[str, str] | int:
    """Parse ``new-task`` argv into a flat ``{flag: value}`` dict.

    Returns the populated dict when every required flag is set, or an
    integer non-zero exit code when parsing failed (the error message
    is already on stderr).
    """
    flag_map = {
        "--id": "id",
        "--title": "title",
        "--target": "target",
        "--repo": "repo",
        "--description": "description",
        "--source-file": "source_file",
        "--test-file": "test_file",
        "--ac-func": "ac_func",
    }
    fields: dict[str, str] = {}
    args = list(argv)
    i = 0
    while i < len(args):
        flag = args[i]
        key = flag_map.get(flag)
        if key is None:
            print(f"ERROR: unknown flag {flag!r}", file=sys.stderr)
            return 1
        if i + 1 >= len(args) or not args[i + 1]:
            print(f"ERROR: {flag} requires a value", file=sys.stderr)
            return 1
        fields[key] = args[i + 1]
        i += 2
    for required in ("id", "title", "target"):
        if required not in fields:
            print(f"ERROR: --{required.replace('_', '-')} is required", file=sys.stderr)
            return 1
    return fields


def _render_new_task_template(template: str, fields: dict[str, str]) -> str:
    """Substitute ``{{TOKEN}}`` placeholders in ``template`` from ``fields``.

    Tokens with no explicit value get a deterministic default so the
    rendered file is immediately validate-backlog-clean (no empty
    sections, no TBD-without-context).
    """
    unit_id = fields["id"]
    repo = fields.get("repo", "org/repo")
    short_repo = repo.split("/", maxsplit=1)[-1]
    source_default = f"src/{short_repo}/{unit_id.lower()}.py"
    source_file = fields.get("source_file", source_default)
    base = Path(source_file).stem
    test_default = f"tests/unit/test_{base}.py"
    substitutions = {
        "{{ID}}": unit_id,
        "{{ID_LOWER}}": unit_id.lower(),
        "{{TITLE}}": fields["title"],
        "{{REPO}}": repo,
        "{{DESCRIPTION}}": fields.get("description", "TBD: describe the work this unit captures."),
        "{{SOURCE_FILE}}": source_file,
        "{{TEST_FILE}}": fields.get("test_file", test_default),
        "{{AC_FUNC}}": fields.get("ac_func", "TBD: describe the functional outcome."),
    }
    rendered = template
    for token, value in substitutions.items():
        rendered = rendered.replace(token, value)
    return rendered


def _template_kind_for_id(unit_id: str) -> str | None:
    """Return the template kind ('task'/'story'/'feature'/'epic') for ``unit_id``."""
    parts = unit_id.split("-")
    if not parts:
        return None
    last = parts[-1]
    if not last:
        return None
    return _TEMPLATE_KIND_BY_ID_SHAPE.get(last[0].upper())


def _devbench_root() -> Path:
    """Return the absolute path to the devbench package root.

    The templates ship inside the package at
    ``<devbench>/backlog/templates/``; resolving via ``__file__`` makes
    the helper portable across editable installs and copies.
    """
    return Path(__file__).resolve().parent.parent.parent


def cmd_sync_blocked() -> int:
    """Reconcile every task's status against current dependency satisfaction.

    Walks the parsed index and for every TASK whose dependencies are
    NOT satisfied (per :meth:`BacklogParser._deps_satisfied` -- includes
    epic / feature / story-level deps after E215, recursing into
    descendants):

    - ``in-queue`` -> ``blocked`` with a ``[BLOCKED] dep <id> not yet terminal``
      audit entry.

    Conversely, for every ``blocked`` TASK whose dependencies are now
    satisfied AND that has NO ``[BLOCKED_PENDING_PROPOSAL]`` marker
    pointing at a still-open proposal:

    - ``blocked`` -> ``in-queue`` with a ``[UNBLOCKED] deps satisfied``
      audit entry.

    Tasks whose status is anything else (``in-progress``, ``in-review``,
    ``done``, ``declined``, ``hold``, ``proposed``) are left untouched.
    The ADR-07 cascade still owns the proposal-marker pathway -- this
    command exists for the orchestrator's pre-flight sweep and for
    operator triage when a backlog has drifted out of sync after manual
    edits.

    Returns 0 always; output is a JSON envelope listing every flip.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    units_by_id = {u.id: u for u in units}
    manager = BacklogManager()
    flipped_to_blocked: list[str] = []
    flipped_to_in_queue: list[str] = []

    for unit in units:
        if unit.unit_type is not WorkUnitType.TASK:
            continue
        deps_ok = parser._deps_satisfied(unit, units_by_id)
        if unit.status is WorkUnitStatus.IN_QUEUE and not deps_ok:
            wu_file = _resolve_unit_file(unit)
            if wu_file is None:
                continue
            unsatisfied = _first_unsatisfied_dep(unit, units_by_id)
            reason = (
                f"sync-blocked: dependency {unsatisfied!r} not yet terminal"
                if unsatisfied
                else "sync-blocked: dependency not yet terminal"
            )
            manager.mark_blocked(wu_file, BACKLOG_INDEX, unit.id, reason)
            flipped_to_blocked.append(unit.id)
            continue
        if unit.status is WorkUnitStatus.BLOCKED and deps_ok:
            wu_file = _resolve_unit_file(unit)
            if wu_file is None:
                continue
            if _has_open_proposal_marker(wu_file, units_by_id):
                # Issue #148: only skip when at least one marker target is
                # still non-terminal. Stale markers pointing at finished
                # tasks no longer represent active cascade work and must
                # not pin this task in the blocked panel forever.
                continue
            manager.force_status(wu_file, BACKLOG_INDEX, unit.id, STATUS_IN_QUEUE)
            # Issue #153: ``[UNBLOCKED] deps satisfied`` is the canonical
            # supersession marker for the panel renderer.
            manager._append_comment(wu_file, "UNBLOCKED", "deps satisfied; sync-blocked dependencies now terminal")
            flipped_to_in_queue.append(unit.id)

    # Issues #207, #209: classification-transition pass.  Re-classify every
    # task still ``blocked`` after the sweep and route through the
    # transition-aware notifier so a stale ``[BLOCKED]`` audit that has
    # drifted into ANY of the seven blocked classes since the last
    # write-site run produces exactly one Slack ping (gated by the
    # per-class toggle in ``devbench.yaml``).  Cache-backed and
    # idempotent: repeated ``sync-blocked`` calls do not duplicate pings.
    _notify_blocked_classification_transitions(units)

    output = {
        "flipped_to_blocked": flipped_to_blocked,
        "flipped_to_in_queue": flipped_to_in_queue,
    }
    print(json.dumps(output))
    logger.info(
        "sync-blocked: %d -> blocked, %d -> in-queue",
        len(flipped_to_blocked),
        len(flipped_to_in_queue),
    )
    return 0


def _notify_blocked_classification_transitions(units: list) -> None:
    """Re-classify still-blocked tasks and route into the transition-aware notifier.

    Called from ``cmd_sync_blocked`` and ``cmd_reconcile_cascade`` as a final
    pass after their main reconciliation work completes (issues #207, #209).
    For every task whose status is still ``blocked``, run
    :func:`classify_blocked_task` and call
    :func:`notify_blocked_classification_transition` so a stale ``[BLOCKED]``
    audit later reclassified into ANY blocked class produces exactly one
    Slack ping per transition (gated by the per-class toggle in
    ``devbench.yaml``).

    The pass also prunes cache entries for tasks that exited ``blocked``
    via the just-run sweep (see :func:`prune_notification_state_for_unblocked`),
    so a task that re-enters ``blocked`` later -- same or different class --
    fires a fresh ping rather than being silently suppressed by a stale
    cache entry.

    Best-effort: any I/O or classifier exception logs ``[WARN]`` to stderr
    and continues to the next task -- the orchestrator must never abort
    because notification bookkeeping failed.
    """
    from devbench.backlog.proposal import classify_blocked_task
    from devbench.notifications import (
        notify_blocked_classification_transition,
        prune_notification_state_for_unblocked,
    )

    workspace_root = BACKLOG_INDEX.parent
    blocked_task_ids: set[str] = set()
    for unit in units:
        if unit.unit_type is not WorkUnitType.TASK:
            continue
        if unit.status is not WorkUnitStatus.BLOCKED:
            continue
        blocked_task_ids.add(unit.id)
        try:
            state = classify_blocked_task(
                BACKLOG_INDEX.parent / "backlog",
                BACKLOG_INDEX,
                unit.id,
                workspace_root=workspace_root,
            )
        except (OSError, ValueError) as exc:
            print(
                f"[WARN] classify_blocked_task failed for {unit.id}: {exc}",
                file=sys.stderr,
            )
            continue
        title = (unit.title or unit.id).strip()
        reason = f"sync-blocked classification: {state.name}"
        notify_blocked_classification_transition(unit.id, title, reason, state.name, workspace_root)
    # Prune any cache entries that no longer correspond to a blocked task --
    # tasks that left ``blocked`` (to ``in-queue`` / ``done`` / etc.) since
    # the last sweep.  Without this, re-entering ``blocked`` later with the
    # same class would be silently suppressed by the stale cached value.
    prune_notification_state_for_unblocked(workspace_root, blocked_task_ids)


def cmd_reconcile_cascade() -> int:
    """Reconcile every blocked task against marker target + regular dep state.

    Issue #150: the recovery cascade can lose track of blocked tasks when a
    promoted proposal completes and the auto-requeue trigger never fires
    (process crash mid-write, missing dependency declaration, etc.). This
    command walks every blocked task and:

    - Evaluates each ``[BLOCKED_PENDING_PROPOSAL]`` marker target's status
      via the loaded backlog index.
    - Evaluates the task's regular dependencies via
      ``BacklogParser._deps_satisfied``.
    - Flips a candidate to ``in-queue`` ONLY when every marker target is
      terminal AND every regular dep is satisfied.

    Each flip writes a ``[CASCADE_RECONCILED]`` audit comment naming the
    closed marker IDs so the operator can trace why the task moved.
    Tasks left blocked are reported with the reason (open marker, unknown
    marker target, unsatisfied dep) so the operator can decide what to do.

    Issue #332 FR-2: a second pass repairs containers (Story/Feature/Epic)
    that were stranded BEFORE the FR-1 live-rollup fix existed. The
    triggering terminal transition that would have promoted such a
    container has already happened, so no live event remains to promote
    it. This pass walks every non-terminal container, evaluates
    ``BacklogManager._all_children_done`` fresh, and promotes qualifying
    containers -- cascading upward exactly as a live rollup would. See
    :func:`_repair_stranded_containers`.

    Returns 0 always; output is a JSON envelope listing flips + skips +
    rolled-up containers.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    units_by_id = {u.id: u for u in units}
    manager = BacklogManager()

    flipped: list[dict[str, str | list[str]]] = []
    skipped: list[dict[str, str]] = []

    terminal_statuses = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}

    for unit in units:
        if unit.unit_type is not WorkUnitType.TASK:
            continue
        if unit.status is not WorkUnitStatus.BLOCKED:
            continue

        wu_file = _resolve_unit_file(unit)
        if wu_file is None:
            skipped.append({"unit_id": unit.id, "reason": "work-unit file missing"})
            continue

        # FR-6 (db-253 Gap 2a): route through the strict, Comments-scoped,
        # end-anchored helper so prose that merely quotes the marker syntax
        # elsewhere in the file cannot mint a phantom marker target.
        marker_ids = sorted(BacklogManager()._extract_pending_proposal_markers(wu_file))

        # Marker evaluation: every target must be terminal AND known.
        unresolved_marker = ""
        for marker in marker_ids:
            target = units_by_id.get(marker)
            if target is None:
                unresolved_marker = f"unknown marker target {marker}"
                break
            if target.status not in terminal_statuses:
                unresolved_marker = f"open marker {marker} ({target.status.value})"
                break
        if unresolved_marker:
            skipped.append({"unit_id": unit.id, "reason": unresolved_marker})
            continue

        # Regular-dep evaluation.
        if not BacklogParser._deps_satisfied(unit, units_by_id):
            unsatisfied = _first_unsatisfied_dep(unit, units_by_id)
            reason = f"regular dep not yet terminal: {unsatisfied}" if unsatisfied else "regular deps unsatisfied"
            skipped.append({"unit_id": unit.id, "reason": reason})
            continue

        manager.force_status(wu_file, BACKLOG_INDEX, unit.id, STATUS_IN_QUEUE)
        message = (
            f"[CASCADE_RECONCILED] markers {marker_ids} terminal and regular deps satisfied; re-queuing"
            if marker_ids
            else "[CASCADE_RECONCILED] regular deps satisfied; re-queuing"
        )
        manager._append_agent_comment(wu_file, "backlog_manager", message)
        flipped.append({"unit_id": unit.id, "closed_markers": marker_ids})

    # Issue #332 FR-2: repair containers stranded before the FR-1 rollup fix
    # existed -- walk every non-terminal Story/Feature/Epic, promote the
    # ones whose children are all terminal, cascading upward exactly as a
    # live rollup would.
    rolled_up = _repair_stranded_containers(manager, BACKLOG_INDEX, skipped)

    # Issues #207, #209: surface classification transitions for tasks that remain
    # blocked after the reconcile sweep -- a stale ``[BLOCKED]`` audit that
    # has drifted into ``OPERATOR_ACTION_REQUIRED`` produces exactly one
    # Slack ping.  Cache-backed, idempotent across repeated invocations.
    _notify_blocked_classification_transitions(units)

    output = {"flipped": flipped, "skipped": skipped, "rolled_up": rolled_up}
    print(json.dumps(output))
    logger.info(
        "reconcile-cascade: %d flipped, %d skipped, %d parent(s) rolled up",
        len(flipped),
        len(skipped),
        len(rolled_up),
    )
    return 0


# Matches Epic / Feature / Story compound IDs (0, 1, or 2 hyphens) while
# explicitly excluding Task IDs (which always carry a trailing ``-T<n>``
# segment) and non-ID table noise (header cells, separator rows, the
# Status Summary table's numeric rows -- none of which match ``E\d+...``).
_CONTAINER_ID_RE = re.compile(r"^E\d+(-F\d+(-S\d+)?)?$")


def _repair_stranded_containers(
    manager: BacklogManager,
    backlog_index: Path,
    skipped: list[dict[str, str]],
) -> list[str]:
    """Promote already-stranded Story/Feature/Epic containers (#332 FR-2).

    The live auto-rollup cascade (``BacklogManager._set_status`` ->
    ``_rollup_parent_status``) only fires from a fresh terminal transition.
    A container stranded BEFORE that trigger existed -- or missed by a
    crashed/partial write -- has no event left that could promote it. This
    walks every non-terminal container directly from ``BACKLOG.md`` (via
    ``BacklogManager._parse_backlog_rows``, which -- unlike
    ``BacklogParser.parse_index`` -- does not require the container's own
    ``.md`` file to exist, so a file-less container row is still visible
    here rather than crashing the whole command), evaluates
    ``BacklogManager._all_children_done`` fresh for each, and promotes
    qualifying containers via ``BacklogManager._set_status`` -- the exact
    private call ``_rollup_parent_status`` itself uses, so a promotion's
    own terminal transition re-triggers ``_rollup_parent_status`` for its
    own parent, cascading upward exactly as a live rollup would.

    Runs the whole evaluate-and-promote sequence under a single
    ``flock_backlog`` acquisition so a concurrent orchestrator process
    cannot interleave a write mid-sweep.

    Args:
        manager: Shared ``BacklogManager`` instance.
        backlog_index: Path to ``BACKLOG.md``.
        skipped: The reconcile-cascade skip list; a container whose
            work-unit file cannot be resolved is appended here (never
            silently dropped).

    Returns:
        Every container ID that was non-terminal when the sweep started
        and is ``done`` when it finished -- credits both containers
        promoted directly by this sweep and ones promoted purely as a
        cascade side-effect of promoting a descendant.
    """
    terminal_statuses = {STATUS_DONE, STATUS_DECLINED}

    with flock_backlog(WORKSPACE_ROOT):
        rows = manager._parse_backlog_rows(backlog_index)
        # Rows with an empty status (e.g. the Status Summary table's
        # numeric-count rows, which share Epic IDs with the Full Work Unit
        # Index but carry no recognised status cell) never contribute a
        # status here, so the dict naturally prefers the real Index row.
        containers_by_id: dict[str, str] = {
            row_id: status for row_id, status, _file_path in rows if status and _CONTAINER_ID_RE.match(row_id)
        }
        candidates = [cid for cid, status in containers_by_id.items() if status not in terminal_statuses]
        # Deepest (Story) first: a promoted Story's own terminal transition
        # cascades upward automatically, so later Feature/Epic entries in
        # this same pass are usually already ``done`` by the time they are
        # reached directly below.
        candidates.sort(key=lambda cid: cid.count("-"), reverse=True)

        for container_id in candidates:
            fresh_rows = manager._parse_backlog_rows(backlog_index)
            if not manager._all_children_done(fresh_rows, container_id):
                continue
            container_file = manager._find_work_unit_file(fresh_rows, container_id, backlog_index.parent)
            if container_file is None:
                skipped.append({"unit_id": container_id, "reason": "container work-unit file missing"})
                logger.warning(
                    "reconcile-cascade: cannot resolve work-unit file for container %s -- counted as skipped",
                    container_id,
                )
                continue
            manager._set_status(container_file, backlog_index, container_id, STATUS_DONE)
            manager._append_agent_comment(
                container_file,
                "backlog_manager",
                "[CASCADE_RECONCILED] all children terminal; rolling up to done via repair sweep",
            )

        final_rows = manager._parse_backlog_rows(backlog_index)
        final_statuses = {row_id: status for row_id, status, _file_path in final_rows if status}
        return [cid for cid in candidates if final_statuses.get(cid) == STATUS_DONE]


def _first_unsatisfied_dep(unit: WorkUnit, units_by_id: dict[str, WorkUnit]) -> str:
    """Return the first dep ID in ``unit.dependencies`` that is NOT terminal.

    ``BacklogParser._deps_satisfied`` returns a single boolean over the
    whole dep list; this helper exposes which dep failed first so the
    blocked-comment audit message names the offending ID. Returns ``""``
    when every dep is satisfied.
    """
    terminal = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    for dep_id in unit.dependencies:
        dep = units_by_id.get(dep_id)
        if dep is None:
            # Unknown dep -- treat as satisfied (validate-backlog reports it).
            continue
        if dep.unit_type is WorkUnitType.TASK:
            if dep.status not in terminal:
                return dep_id
            continue
        # Non-task dep: scan descendant tasks.
        for descendant in units_by_id.values():
            if (
                descendant.id != dep_id
                and descendant.id.startswith(dep_id + "-")
                and descendant.unit_type is WorkUnitType.TASK
                and descendant.status not in terminal
            ):
                return dep_id
    return ""


# Captures the audit-comment classifier tag (``[BLOCKED]`` / ``[UNBLOCKED]`` /
# ``[CASCADE_RESOLVED]``). Used by ``_unsuperseded_blocked_audits`` to walk
# the Comments section in chronological order and drop ``[BLOCKED]`` rows
# that have been superseded by a later positive transition.
# ``CASCADE_RECONCILED`` belongs here for the same reason as the other two:
# ``reconcile-cascade`` writes it when it re-queues a unit, so it means the
# block is over. Its absence made devbench write a tag its own reader
# ignored -- the unit reached ``in-queue`` while every blocked-audit
# consumer kept reporting it as operator-blocked, so the report
# contradicted the lifecycle for the rest of that unit's life.
_BLOCKED_AUDIT_LINE_RE: re.Pattern[str] = re.compile(
    r"\[(?P<tag>BLOCKED|UNBLOCKED|CASCADE_RESOLVED|CASCADE_RECONCILED)\](?P<rest>[^\n]*)"
)


def _unsuperseded_blocked_audits(content: str) -> list[str]:
    """Return ``[BLOCKED]`` audit text lines that have NOT been superseded
    by a later ``[UNBLOCKED]`` or ``[CASCADE_RESOLVED]`` row.

    Issue #153: the audit history in the file stays append-only -- only the
    rendered status panel hides stale ``[BLOCKED]`` rows when a positive
    transition has subsequently fired. Walks the file linearly so the
    chronological ordering of the Comments section drives supersession
    (a ``[BLOCKED]`` followed by ``[UNBLOCKED]`` is stale; a ``[BLOCKED]``
    followed by another ``[BLOCKED]`` is the live cause).

    Excludes ``[BLOCKED_PENDING_PROPOSAL]`` marker rows so cascade markers
    are NOT mistaken for plain ``[BLOCKED]`` audit lines.
    """
    pending_audits: list[str] = []
    for line in content.splitlines():
        if "[BLOCKED_PENDING_PROPOSAL]" in line:
            continue
        match = _BLOCKED_AUDIT_LINE_RE.search(line)
        if match is None:
            continue
        tag = match.group("tag")
        if tag == "BLOCKED":
            pending_audits.append(line.strip())
        else:
            # UNBLOCKED / CASCADE_RESOLVED supersedes every prior BLOCKED.
            pending_audits = []
    return pending_audits


def _has_open_proposal_marker(wu_file: Path, units_by_id: dict[str, WorkUnit]) -> bool:
    """Return ``True`` iff ``wu_file`` carries at least one ``[BLOCKED_PENDING_PROPOSAL]``
    marker whose target task is still non-terminal.

    Issue #148: the prior ``_BLOCKED_PENDING_PROPOSAL_OPEN_RE`` regex flagged
    every marker as "open" -- including markers whose target had already
    completed. That left blocked tasks marooned because the sync-blocked
    sweep treated stale markers as live cascade activity and refused to
    re-queue. The walker resolves each marker target via ``units_by_id`` and
    returns ``True`` only when at least one target is in a non-terminal
    status (anything other than ``done`` / ``declined``). Unknown target
    IDs (rejected drafts whose backlog row was removed) count as
    non-terminal so the cascade owner stays in charge of clearing them.

    FR-6 (db-253 Gap 2a): markers are read via the strict, Comments-scoped,
    end-anchored ``BacklogManager._extract_pending_proposal_markers`` helper
    so prose that merely quotes the marker syntax elsewhere in the file can
    never be mistaken for a live target.
    """
    terminal = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    for marker in BacklogManager()._extract_pending_proposal_markers(wu_file):
        target = units_by_id.get(marker)
        if target is None:
            return True
        if target.status not in terminal:
            return True
    return False


def _parse_id_and_reason(argv: tuple[str, ...] | list[str], command_name: str) -> tuple[str, str] | int:
    """Parse a ``<id> --reason <message>`` argument tuple shared by hold/unhold/decline.

    Returns a ``(task_id, reason)`` tuple on success or an integer
    non-zero exit code on parse error (with the error message already
    written to stderr). Em-dashes in the reason text are rejected at
    the boundary (CLAUDE.md backlog hygiene rule).
    """
    task_id = ""
    reason = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--reason":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --reason requires a value", file=sys.stderr)
                return 1
            reason = args[i + 1]
            i += 2
            continue
        if not task_id:
            task_id = arg
        i += 1
    if not task_id or not reason:
        print(f"ERROR: {command_name} requires <id> --reason <message>", file=sys.stderr)
        return 1
    em_dash_rc = _reject_em_dash("reason", reason)
    if em_dash_rc is not None:
        return em_dash_rc
    return task_id, reason


def cmd_hold(*argv: str) -> int:
    """Mark a work unit as ``hold`` (deferred / under debate).

    Usage::

        hold <id> --reason "<message>"

    ``hold`` is a deferred-decision lifecycle status: the unit stops
    being considered actionable by the orchestrator's ``next`` query
    until an operator runs ``unhold`` to return it to ``in-queue``.
    Unlike ``declined``, ``hold`` is **not** terminal -- a held child
    does NOT count toward a parent's auto-rollup to ``done``. The
    ``--reason`` is REQUIRED so the deferral leaves an audit trail;
    em-dashes are rejected at the input boundary for backlog hygiene.
    """
    parsed = _parse_id_and_reason(argv, "hold")
    if isinstance(parsed, int):
        return parsed
    task_id, reason = parsed

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    target = _find_unit(units, task_id)
    if target is None:
        print(f"ERROR: Work unit '{task_id}' not found", file=sys.stderr)
        return 1
    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{task_id}'", file=sys.stderr)
        return 1

    BacklogManager().mark_held(wu_file, BACKLOG_INDEX, task_id, reason)
    logger.info("Held %s: %s", task_id, reason)
    print(json.dumps({"task_id": task_id, "status": "hold", "reason": reason}))
    return 0


def cmd_unhold(*argv: str) -> int:
    """Return a held work unit to ``in-queue`` with a captured rationale.

    Usage::

        unhold <id> --reason "<message>"

    The unit's status flips from ``hold`` back to ``in-queue`` so the
    orchestrator's ``next``/parallel-candidate scan picks it up again.
    The ``--reason`` is REQUIRED so the release leaves an audit trail;
    em-dashes are rejected at the input boundary. ``unhold`` refuses
    units whose current status is anything other than ``hold`` --
    fail-fast keeps the lifecycle linear.
    """
    parsed = _parse_id_and_reason(argv, "unhold")
    if isinstance(parsed, int):
        return parsed
    task_id, reason = parsed

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    target = _find_unit(units, task_id)
    if target is None:
        print(f"ERROR: Work unit '{task_id}' not found", file=sys.stderr)
        return 1
    if target.status is not WorkUnitStatus.HOLD:
        print(
            f"ERROR: cannot unhold {task_id!r}: current status is {target.status.value!r}, expected 'Hold'",
            file=sys.stderr,
        )
        return 1
    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{task_id}'", file=sys.stderr)
        return 1

    BacklogManager().unmark_held(wu_file, BACKLOG_INDEX, task_id, reason)
    logger.info("Unheld %s: %s", task_id, reason)
    print(json.dumps({"task_id": task_id, "status": "in-queue", "reason": reason}))
    return 0


def cmd_remove(*argv: str) -> int:
    """Remove a work unit through the managed path (db-303, spec 4.A, FR-16).

    Usage::

        remove <id> --reason "<message>"

    Deletes the work-unit ``.md`` file and its BACKLOG.md index row under a
    single ``flock(BACKLOG.lock)``, re-rolls the Status Summary, and appends
    a ``[WU_REMOVED] <id> -- <reason>`` line to the workspace audit log
    (``BacklogManager.remove_unit``). ``BACKLOG.md`` is otherwise protected
    by ``guard-work-unit-write.sh``: a raw Edit/Write is blocked unless the
    operator sets ``DEVBENCH_ALLOW_BACKLOG_EDIT=1``, so this managed verb --
    which writes through Python I/O, not the Edit/Write tools -- is the
    normal path to drop a superseded unit. The ``--reason`` is REQUIRED so
    the removal leaves an audit trail; em-dashes are rejected at the input
    boundary. An unknown ``<id>`` fails fast before any file is touched.
    """
    parsed = _parse_id_and_reason(argv, "remove")
    if isinstance(parsed, int):
        return parsed
    task_id, reason = parsed

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    target = _find_unit(units, task_id)
    if target is None:
        print(f"ERROR: Work unit '{task_id}' not found", file=sys.stderr)
        return 1
    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{task_id}'", file=sys.stderr)
        return 1

    audit_log_path = WORKSPACE_ROOT / RUNTIME_CONFIG.backlog.bulk_update_audit_path
    try:
        BacklogManager().remove_unit(wu_file, BACKLOG_INDEX, task_id, reason, audit_log_path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    logger.info("Removed %s: %s", task_id, reason)
    print(json.dumps({"task_id": task_id, "status": "removed", "reason": reason}))
    return 0


def cmd_promote(*argv: str) -> int:
    """Transition one or more work units from ``draft`` to ``in-queue``.

    Supported invocations::

        devbench promote <id>
            Promote a single work unit identified by ``<id>``.

        devbench promote --epic <id>
            Promote every ``draft``-status descendant of the given epic in one
            atomic transaction.  Aborts with rc=1 if any descendant is not in
            ``draft`` status -- no partial writes occur.

        devbench promote --feature <id>
            Promote every ``draft``-status descendant of the given feature in
            one atomic transaction.

        devbench promote --story <id>
            Promote every ``draft``-status descendant of the given story in one
            atomic transaction.

        devbench promote --all
            Promote every ``draft``-status WU in the workspace.  Prints a
            confirmation prompt listing the count before writing.  Aborts with
            rc=1 if the operator does not confirm.

        devbench promote --all --yes
            Same as ``--all`` but skips the confirmation prompt.

    For every promoted unit an audit comment ``[PROMOTED] draft -> in-queue``
    is appended via ``BacklogManager._append_agent_comment``.

    Args:
        *argv: Parsed CLI tokens -- either ``(<id>,)`` or
            ``("--epic"|"--feature"|"--story", <scope_id>)`` or
            ``("--all",)`` or ``("--all", "--yes")``.

    Returns:
        0 on success, 1 on any error (unit not found, file missing,
        status is not draft, unknown flag, operator declined confirmation).

    Raises:
        Nothing -- all errors are reported to stderr and return rc=1.
    """
    bulk_flags = frozenset({"--epic", "--feature", "--story"})

    if len(argv) == 1 and argv[0] not in bulk_flags and argv[0] != "--all":
        return _promote_single(argv[0])

    if len(argv) == 2 and argv[0] in bulk_flags:
        return _promote_bulk(scope_id=argv[1])

    if len(argv) == 1 and argv[0] == "--all":
        return _promote_all(skip_confirmation=False)

    if len(argv) == 2 and argv[0] == "--all" and argv[1] == "--yes":
        return _promote_all(skip_confirmation=True)

    if len(argv) == 1 and argv[0] in bulk_flags:
        print(
            f"ERROR: '{argv[0]}' requires a scope ID argument (e.g. promote {argv[0]} E1)",
            file=sys.stderr,
        )
        return 1

    print(
        "ERROR: promote usage: promote <id>  OR  promote --epic|--feature|--story <id>  OR  promote --all [--yes]",
        file=sys.stderr,
    )
    return 1


def _promote_single(unit_id: str) -> int:
    """Promote a single work unit from draft to in-queue.

    Args:
        unit_id: The work-unit identifier to promote.

    Returns:
        0 on success, 1 on any error.

    Raises:
        Nothing -- all errors reported to stderr and return rc=1.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found", file=sys.stderr)
        return 1

    if target.status is not WorkUnitStatus.DRAFT:
        print(
            f"ERROR: cannot promote {unit_id!r}: not in 'draft' status (current: {target.status.value!r})",
            file=sys.stderr,
        )
        return 1

    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1

    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_IN_QUEUE)
    mgr._append_agent_comment(wu_file, "orchestrator", "[PROMOTED] draft -> in-queue")

    logger.info("Promoted %s from draft to in-queue", unit_id)
    print(f"Promoted {unit_id} from draft to in-queue")
    return 0


def _promote_bulk(scope_id: str) -> int:
    """Promote every draft descendant of ``scope_id`` in one atomic transaction.

    Enumerates all work units whose IDs are equal to or descended from
    ``scope_id``.  The transaction aborts with rc=1 (no writes performed) if any
    discovered descendant is not in ``draft`` status.

    Args:
        scope_id: The ancestor scope ID (epic, feature, or story prefix).

    Returns:
        0 when all draft descendants were promoted successfully, 1 on any error.

    Raises:
        Nothing -- all errors reported to stderr and return rc=1.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    all_ids = [u.id for u in units]
    descendant_ids = _expand_prefix(scope_id, all_ids)
    descendants = [u for u in units if u.id in descendant_ids]

    draft_units = [u for u in descendants if u.status is WorkUnitStatus.DRAFT]
    non_draft = [u for u in descendants if u.status is not WorkUnitStatus.DRAFT]

    if non_draft:
        for u in non_draft:
            print(
                f"ERROR: cannot promote {u.id!r}: not in 'draft' status (current: {u.status.value!r})",
                file=sys.stderr,
            )
        print(
            "ERROR: bulk promote aborted -- all descendants must be in 'draft' status",
            file=sys.stderr,
        )
        return 1

    if not draft_units:
        print(
            f"ERROR: no draft descendants found under scope '{scope_id}'",
            file=sys.stderr,
        )
        return 1

    # Validate all files exist before writing anything (fail-fast, no partial writes)
    resolved: list[tuple[WorkUnit, Path]] = []
    for u in draft_units:
        wu_file = _resolve_unit_file(u)
        if wu_file is None:
            print(f"ERROR: Work unit file not found for '{u.id}'", file=sys.stderr)
            return 1
        resolved.append((u, wu_file))

    mgr = BacklogManager()
    for u, wu_file in resolved:
        mgr.force_status(wu_file, BACKLOG_INDEX, u.id, STATUS_IN_QUEUE)
        mgr._append_agent_comment(wu_file, "orchestrator", "[PROMOTED] draft -> in-queue")
        logger.info("Promoted %s from draft to in-queue", u.id)

    count = len(resolved)
    print(f"Promoted {count} unit(s) from draft to in-queue under scope '{scope_id}'")
    return 0


def _promote_all(*, skip_confirmation: bool) -> int:
    """Promote every ``draft`` work unit in the workspace to ``in-queue``.

    Discovers all draft work units via ``BacklogParser.parse_index``, then
    optionally asks the operator to confirm before writing.  If the operator
    declines, no files are modified and the function returns 1.

    The function is fail-fast: all file paths are resolved before any write
    occurs so that a missing file (TOCTOU race) never causes partial promotion.

    Args:
        skip_confirmation: When ``True``, no interactive prompt is shown and
            promotion proceeds immediately.  When ``False``, the operator is
            shown the count of draft units and must type ``y`` / ``yes``
            (case-insensitive) to continue; any other input aborts with rc=1.

    Returns:
        0 when all draft units were promoted successfully.
        1 when there are no draft units, the operator declined the prompt, any
        file is missing, or any write fails.

    Raises:
        Nothing -- all errors are reported to stderr and return rc=1.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    draft_units = [u for u in units if u.status is WorkUnitStatus.DRAFT]

    if not draft_units:
        print(
            "ERROR: no draft work units found in the workspace",
            file=sys.stderr,
        )
        return 1

    count = len(draft_units)

    if not skip_confirmation:
        answer = input(f"Promote {count} draft unit(s) to in-queue? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("Promotion aborted.", file=sys.stderr)
            return 1

    # Validate all files exist before writing anything (fail-fast, no partial writes)
    resolved: list[tuple[WorkUnit, Path]] = []
    for u in draft_units:
        wu_file = _resolve_unit_file(u)
        if wu_file is None:
            print(f"ERROR: Work unit file not found for '{u.id}'", file=sys.stderr)
            return 1
        resolved.append((u, wu_file))

    mgr = BacklogManager()
    for u, wu_file in resolved:
        mgr.force_status(wu_file, BACKLOG_INDEX, u.id, STATUS_IN_QUEUE)
        mgr._append_agent_comment(wu_file, "orchestrator", "[PROMOTED] draft -> in-queue")
        logger.info("Promoted %s from draft to in-queue", u.id)

    print(f"Promoted {count} unit(s) from draft to in-queue")
    return 0


def cmd_validate_backlog(*argv: str) -> int:
    """Validate backlog integrity and print any inconsistencies.

    Checks:
    - Every index row has a corresponding work unit file.
    - Every work unit file's status matches the index.
    - No orphaned work unit files.
    - All dependency IDs reference real work unit IDs.
    - Status Summary table exists and counts match the Full Work Unit Index.

    Optional flags:
    - ``--fix``: Auto-correct rule-10 (em-dash) and rule-11 (checkout_directory
      prefix) violations in place and append an audit comment to each corrected
      file's ``## Comments`` section. Prints a summary of corrections made.
    - ``--strict``: Additionally report draft/hold Manifest conflicts (FR-4,
      db-267). Default runs report only in-queue/proposed/blocked/in-progress
      conflicts (FR-3, db-313); ``spec-to-backlog`` runs this flag as its
      authoring-time exit gate.

    Exits 0 if the backlog is consistent (or all violations were fixed); 1 with
    actionable error messages if any inconsistencies remain.
    """
    fix = "--fix" in argv
    strict = "--strict" in argv
    mgr = BacklogManager()
    errors = mgr.validate(BACKLOG_INDEX, BACKLOG_INDEX.parent, fix=fix, strict=strict)
    if fix:
        fix_count, files_fixed = mgr._fix_summary
        print(f"Fixed {fix_count} violation(s) across {files_fixed} file(s).")
    if not errors:
        print("Backlog integrity check passed.")
        return 0
    print(f"Backlog integrity check FAILED ({len(errors)} error(s)):")
    for error in errors:
        print(f"  ERROR: {error}")
    return 1


def _check_repo_symlink(repo_name: str, symlink_path: Path) -> tuple[bool, str | None]:
    """Return ``(symlink_ok, error_or_None)`` for the configured symlink path."""
    if symlink_path.exists():
        return True, None
    return False, (
        f"{repo_name}: symlink missing at {symlink_path} -- run "
        "06-multi-repo-symlinks.md procedure or create it manually"
    )


def _check_repo_origin(repo_name: str, target: Path, timeout: int, local_only: bool = False) -> tuple[bool, str | None]:
    """Return ``(origin_ok, error_or_None)`` after 'git remote get-url origin'.

    Default mode (``local_only=False``): the repo MUST have an ``origin``
    remote configured; absence is an error. Behavior unchanged from the
    pre-local-only world.

    Local-only mode (``local_only=True``): the repo MUST NOT have an
    ``origin`` remote; presence is an error. Catches misconfiguration
    where the operator declared ``git_ops.local_only: true`` but the
    target checkout still tracks a remote.

    The boolean component of the tuple uses "ok" semantics in both modes:
    ``True`` means the check passed and the caller may proceed; ``False``
    means the check failed and the error string in the second slot
    describes the fix.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{repo_name}: 'git remote get-url origin' timed out after {timeout}s"
    has_origin = result.returncode == 0
    if local_only:
        if has_origin:
            return False, (
                f"{repo_name}: git_ops.local_only is true but clone at {target} has an "
                f"'origin' remote configured ({result.stdout.strip()}). Either unset the "
                "remote (git remote remove origin) or set git_ops.local_only: false."
            )
        return True, None
    if not has_origin:
        return False, (
            f"{repo_name}: clone at {target} has no 'origin' remote configured (stderr: {result.stderr.strip()})"
        )
    return True, None


def _check_repo_default_branch(repo_name: str, configured_default: str | None, timeout: int) -> str | None:
    """Return an error string when the remote default_branch disagrees with config, else None."""
    if not configured_default:
        return None
    try:
        api_result = subprocess.run(
            ["gh", "api", f"repos/{repo_name}", "--jq", ".default_branch"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{repo_name}: 'gh api repos/{repo_name}' timed out after {timeout}s"
    if api_result.returncode != 0:
        return (
            f"{repo_name}: 'gh api repos/{repo_name}' failed -- check gh auth status "
            f"(stderr: {api_result.stderr.strip()})"
        )
    remote_default = api_result.stdout.strip()
    if remote_default == configured_default:
        return None
    return (
        f"{repo_name}: default_branch mismatch -- devbench.yaml says "
        f"{configured_default!r} but remote default is {remote_default!r}. "
        f"Run 'gh repo edit {repo_name} --default-branch {configured_default}' "
        "or update devbench.yaml to match the remote."
    )


def _check_repo_open_prs(repo_name: str, single_branch: str, timeout: int) -> str | None:
    """Return an error string when an open PR already targets *single_branch*, else None."""
    try:
        pr_result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo_name,
                "--head",
                single_branch,
                "--state",
                "open",
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"{repo_name}: 'gh pr list' for {single_branch} timed out after {timeout}s"
    if pr_result.returncode != 0 or pr_result.stdout.strip() in ("[]", ""):
        return None
    return (
        f"{repo_name}: open PR(s) already exist on branch {single_branch!r}: "
        f"{pr_result.stdout.strip()} -- close or merge before launching to "
        "avoid conflicting feat-branch creation"
    )


def _check_repo_preflight(
    repo_name: str,
    repo_cfg: RepoConfig,
    single_branch: str | None,
    timeout: int,
    local_only: bool = False,
) -> list[str]:
    """Run all pre-flight checks for a single repo and return its error list.

    Extracted from :func:`cmd_check` to keep the dispatcher under the
    project's branch-count budget. Each helper above owns one rail of
    the gate and returns its own error string (or ``None``) so the
    aggregation here is a flat list-extend.

    When ``local_only`` is true, the origin check is inverted (a present
    remote is the error) and the GitHub-API default-branch + open-PR
    checks are skipped because no remote exists to talk to GitHub through.
    """
    checkout_subdir = repo_cfg.checkout_directory or repo_name.split("/", 1)[-1]
    symlink_path = WORKSPACE_ROOT / checkout_subdir
    symlink_ok, symlink_err = _check_repo_symlink(repo_name, symlink_path)
    if not symlink_ok:
        return [symlink_err] if symlink_err is not None else []
    target = symlink_path.resolve()
    origin_ok, origin_err = _check_repo_origin(repo_name, target, timeout, local_only=local_only)
    if not origin_ok:
        return [origin_err] if origin_err is not None else []
    if local_only:
        return []
    errors: list[str] = []
    branch_err = _check_repo_default_branch(repo_name, repo_cfg.default_branch, timeout)
    if branch_err:
        errors.append(branch_err)
    if single_branch:
        pr_err = _check_repo_open_prs(repo_name, single_branch, timeout)
        if pr_err:
            errors.append(pr_err)
    return errors


def cmd_config_resolve(*argv: str) -> int:
    """Print resolved runtime-config values as one-line JSON: ``config-resolve <field>...``.

    Exposes the fully-resolved configuration (env > YAML > built-in default)
    so an agent can read a setting deterministically instead of re-deriving
    the precedence chain itself, or worse, assuming a default.

    Added because orchestrate SKILL.md already instructed the orchestrator to
    read its retry budget via this verb while the verb did not exist: the call
    failed, so ``max_executor_retries_per_judge`` was unreadable at runtime
    and the review-rejection loop it was meant to bound ran unbounded.

    Field names are ``RuntimeConfig`` attribute names, e.g.
    ``max_executor_retries``, ``max_executor_retries_per_judge``. Nested
    dataclass sections are returned as JSON objects. An unknown field name is
    a hard error naming the valid choices -- never a silent ``null``, which
    would read as "configured empty" and hide a typo.

    Returns 0 on success; 1 when no field is given or any field is unknown.
    """
    from dataclasses import asdict, fields, is_dataclass

    from devbench.config import RUNTIME_CONFIG

    names = [a for a in argv if a]
    if not names:
        valid = ", ".join(sorted(f.name for f in fields(RUNTIME_CONFIG)))
        print(f"ERROR: config-resolve requires at least one field name. Valid fields: {valid}", file=sys.stderr)
        return 1

    known = {f.name for f in fields(RUNTIME_CONFIG)}
    unknown = [n for n in names if n not in known]
    if unknown:
        print(
            f"ERROR: unknown config field(s): {', '.join(unknown)}. Valid fields: {', '.join(sorted(known))}",
            file=sys.stderr,
        )
        return 1

    resolved: dict[str, Any] = {}
    for name in names:
        value = getattr(RUNTIME_CONFIG, name)
        # ``is_dataclass`` narrows to the type, not the instance, so the
        # non-type guard keeps mypy satisfied without a cast.
        resolved[name] = asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
    print(json.dumps(resolved, default=str))
    return 0


def cmd_check() -> int:
    """Pre-flight verifier for orchestrator launch readiness.

    For every repo in ``backlog/config/devbench.yaml``'s ``repos:`` map, verify:

    1. Symlink exists at ``$DEVBENCH_WORKSPACE_ROOT/<checkout_directory>``.
    2. Origin-remote check (mode-dependent):

       - Default mode: the symlink target (the local clone) MUST have an
         ``origin`` remote configured.
       - ``git_ops.local_only: true``: the symlink target MUST NOT have an
         ``origin`` remote configured (presence is misconfiguration).

    3. The remote's ``default_branch`` matches ``devbench.yaml``'s
       ``default_branch`` (or both fall back to ``origin/HEAD``).
       Skipped under ``git_ops.local_only: true`` (no remote to query).
    4. No open PR already targets the configured ``single_branch``
       (when ``git_ops.single_branch`` is set). Skipped under
       ``git_ops.local_only: true``.

    Exits 0 if every check passes; 1 with actionable per-repo error messages
    otherwise. ``DEVBENCH_CHECK_GH_API_TIMEOUT`` (seconds, default 30) bounds
    each ``gh api`` call.
    """
    import os

    from devbench.config_loader import load_runtime_config, resolve_config_path

    timeout = int(os.environ.get("DEVBENCH_CHECK_GH_API_TIMEOUT", "30"))
    cfg_path = resolve_config_path(None, os.environ, WORKSPACE_ROOT)
    if cfg_path is None or not cfg_path.exists():
        print(
            "ERROR: devbench.yaml not found; set DEVBENCH_CONFIG_PATH or place at backlog/config/devbench.yaml",
            file=sys.stderr,
        )
        return 1
    cfg = load_runtime_config(cfg_path, os.environ)

    single_branch = cfg.git_ops.single_branch if cfg.git_ops else None
    local_only = cfg.git_ops.local_only if cfg.git_ops else False
    errors: list[str] = []
    for repo_name, repo_cfg in cfg.repos.items():
        errors.extend(_check_repo_preflight(repo_name, repo_cfg, single_branch, timeout, local_only=local_only))

    if not errors:
        print(f"Pre-flight check passed for {len(cfg.repos)} target repo(s).")
        return 0
    print(f"Pre-flight check FAILED ({len(errors)} error(s)):")
    for error in errors:
        print(f"  ERROR: {error}")
    return 1


def _resolve_log_file_path() -> Path:
    """Resolve the orchestrator log file path.

    Resolution precedence (first match wins; no implicit fallbacks):

    1. ``DEVBENCH_LOG_FILE`` environment variable set to an explicit path.
       Per-invocation override; used in tests and ad-hoc overrides.
       Setting the legacy ``DEVBENCH_LOG_FILE`` raises ``RuntimeError``
       (AC-197-2: hard rejection, no fallback).
    2. ``RUNTIME_CONFIG.log_file`` from ``backlog/config/devbench.yaml``.
       Single source of truth: when the operator sets it once in YAML,
       every devbench invocation against this workspace -- the
       orchestrator's ``setup_logging`` writer and ``cmd_report``'s
       reader alike -- picks up the same path. The value is treated as
       workspace-root-relative when not absolute.
    3. ``<DEVBENCH_WORKSPACE_ROOT>/<DEFAULT_LOG_SUBDIR>/<DEFAULT_LOG_FILENAME>``
       convention (the shared aggregate log). ``WORKSPACE_ROOT`` is resolved from
       ``DEVBENCH_WORKSPACE_ROOT`` by config.py at import time (raises
       ``RuntimeError`` if unset), so this path is always deterministic. It
       mirrors ``log_setup._resolve_log_file`` exactly so the writer and reader
       never disagree. (Per-session logs live at
       ``.devbench/sessions/<name>/orchestrator.log`` and are read via
       ``report --session <name>``.)

    Raises:
        RuntimeError: when the legacy ``DEVBENCH_LOG_FILE`` env var is set
            (AC-197-2).
    """
    explicit = (_read_env("DEVBENCH_LOG_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    configured = (RUNTIME_CONFIG.log_file or "").strip()
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            return configured_path
        return WORKSPACE_ROOT / configured_path
    return WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME


def _resolve_session_report_log(session_name: str) -> Path | None:
    """Resolve the per-session orchestrator log path for ``cmd_report --session``.

    Returns the path ``<WORKSPACE_ROOT>/.devbench/sessions/<name>/orchestrator.log``
    when the session state directory exists.  Returns ``None`` and prints an
    actionable error to stderr when the session directory is absent (the session
    was never started or has been cleaned up).

    Args:
        session_name: Named-session filter from ``--session <name>``.

    Returns:
        The per-session log :class:`Path`, or ``None`` on error.
    """
    session_log = WORKSPACE_ROOT / SESSION_SESSIONS_BASE_DIR / session_name / DEFAULT_LOG_FILENAME
    if not session_log.parent.exists():
        sys.stderr.write(
            f"devbench report: session '{session_name}' not found at "
            f"'{session_log.parent}'.\n"
            "  Start the session first with 'devbench start --name "
            f"{session_name} ...' or check the session name.\n"
        )
        return None
    return session_log


class _CostCalibrateArgs:
    """Parsed args for ``cmd_cost_calibrate``.  Lifted into a small
    container so the dispatcher splits cleanly between "parse" and
    "act" responsibilities (SRP) and the dispatcher's branch count
    stays under the ruff PLR0912 ceiling.
    """

    __slots__ = ("actual_usd", "window_start")

    def __init__(self, actual_usd: float, window_start: datetime) -> None:
        self.actual_usd = actual_usd
        self.window_start = window_start


def _parse_cost_calibrate_argv(argv: tuple[str, ...]) -> _CostCalibrateArgs | int:
    """Parse ``cost-calibrate`` argv into a ``_CostCalibrateArgs`` or
    return an exit code on validation failure.

    Operators invoke as ``cost-calibrate <actual-usd> [--window <ISO-8601>]``;
    this helper handles the variadic dispatch path so the main command
    body only has to handle the success case.
    """
    actual_usd: float | None = None
    window_start: datetime = datetime(1970, 1, 1, tzinfo=UTC)
    err: str | None = None
    it = iter(argv)
    for token in it:
        if token == "--window":
            window_iso = next(it, None)
            if window_iso is None or not window_iso.strip():
                err = "cost-calibrate: --window requires an ISO-8601 timestamp value"
                break
            try:
                parsed = datetime.fromisoformat(window_iso.replace("Z", "+00:00"))
            except ValueError as exc:
                err = f"cost-calibrate: invalid --window value {window_iso!r}: {exc}"
                break
            window_start = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            continue
        if actual_usd is not None:
            err = f"cost-calibrate: unexpected extra argument {token!r}"
            break
        try:
            actual_usd = float(token)
        except ValueError:
            err = f"cost-calibrate: actual USD must be a numeric value; got {token!r}"
            break
    if err is None and actual_usd is None:
        err = (
            "cost-calibrate: missing required <actual-usd> argument. "
            "Usage: devbench cost-calibrate <actual-usd> [--window <ISO-8601>]"
        )
    elif err is None and actual_usd is not None and actual_usd <= 0:
        err = (
            f"cost-calibrate: actual USD must be > 0; got {actual_usd}. "
            "Use a positive billing figure from your most recent Anthropic invoice."
        )
    if err is not None:
        print(err, file=sys.stderr)
        return 2
    return _CostCalibrateArgs(actual_usd=actual_usd or 0.0, window_start=window_start)


def cmd_cost_calibrate(*argv: str) -> int:
    """``devbench cost-calibrate <actual-usd> [--window <ISO-8601>]`` (issue #223).

    Reads the most-recent reported per-model spend over the window
    starting at ``--window`` (default: log start, which folds in every
    recorded event), derives a per-model correction factor from the
    ratio ``actual_usd / reported_total``, apportions the resulting
    correction across each observed model by its share of reported spend,
    and writes the result back to ``<workspace>/backlog/config/devbench.yaml``
    under ``report.models.<id>.correction_factor``.

    Verifies AC-6: round-trip a workspace whose actual billing is $X and
    reported $Y by writing per-model correction factors so the next
    ``devbench report`` reflects the corrected total.

    Args parsing is delegated to ``_parse_cost_calibrate_argv``.
    """
    parsed = _parse_cost_calibrate_argv(argv)
    if isinstance(parsed, int):
        return parsed
    actual_usd = parsed.actual_usd
    window_start = parsed.window_start

    from devbench.reporting.event_index import EventIndex
    from devbench.reporting.report import _per_model_totals_from_aggregator, _resolve_rates_for_model

    config_yaml = WORKSPACE_ROOT / "backlog" / "config" / "devbench.yaml"
    if not config_yaml.is_file():
        print(
            f"cost-calibrate: cannot find {config_yaml}. Provide a workspace "
            "with backlog/config/devbench.yaml or set DEVBENCH_WORKSPACE_ROOT correctly.",
            file=sys.stderr,
        )
        return 2

    event_index = EventIndex.open(WORKSPACE_ROOT)
    try:
        hook_log_path = WORKSPACE_ROOT / "hook-logs.jsonl"
        # Refresh the hook-log cache FIRST so ``first_hook_transcript_path``
        # has rows to read on the lookup that follows; without this order
        # the lookup on a cold cache returns None even when the live hook
        # log carries ``transcript_path`` entries.
        event_index.refresh_hook_log(hook_log_path)
        transcript_path_raw = event_index.first_hook_transcript_path(hook_log_path)
        transcript_dir = Path(transcript_path_raw).parent if transcript_path_raw else None
        if transcript_dir is not None:
            event_index.refresh_transcripts(transcript_dir)
        hook_by_model = _per_model_totals_from_aggregator(
            event_index.aggregate_hook_window_by_model, hook_log_path, window_start
        )
        transcript_by_model = _per_model_totals_from_aggregator(
            event_index.aggregate_transcript_window_by_model, transcript_dir, window_start
        )
    finally:
        event_index.close()

    # Sum per-model spend at the CURRENT rate table to derive each
    # model's share of total reported spend.  We do NOT compose the
    # existing correction_factor here -- the new factor replaces (not
    # multiplies) whatever was there before so successive calibrations
    # don't compound.
    per_model_spend: dict[str, float] = {}
    from devbench.reporting.report import _compute_cost

    for source in (hook_by_model, transcript_by_model):
        for model_id, totals in source.items():
            input_rate, output_rate, cache_read, cache_5m, cache_1h, _existing_correction = _resolve_rates_for_model(
                model_id
            )
            bucket = _compute_cost(totals, input_rate, output_rate, cache_read, cache_5m, cache_1h)
            per_model_spend[model_id] = per_model_spend.get(model_id, 0.0) + bucket.total_cost

    reported_total = sum(per_model_spend.values())
    if reported_total <= 0:
        print(
            "cost-calibrate: reported cost in the selected window is $0.00. "
            "Nothing to calibrate against. Widen the window or run after a session with billable activity.",
            file=sys.stderr,
        )
        return 1

    global_correction = actual_usd / reported_total
    print(f"cost-calibrate: reported total = ${reported_total:.4f}, actual = ${actual_usd:.4f}")
    print(f"cost-calibrate: derived global correction factor = {global_correction:.6f}")

    # Same correction is applied to every observed model id because the
    # operator only supplies one aggregate USD figure; per-model
    # differentiation would require per-model invoice data, which
    # Anthropic does not break out today.  Operators with a per-model
    # invoice can edit the resulting yaml manually for finer control.
    write_per_model_correction_factors(config_yaml, per_model_spend.keys(), global_correction)
    print(f"cost-calibrate: wrote correction_factor={global_correction:.6f} for {len(per_model_spend)} model(s)")
    return 0


def write_per_model_correction_factors(config_yaml: Path, model_ids: Iterable[str], correction_factor: float) -> None:
    """Update ``<config_yaml>::report.models.<id>.correction_factor`` for every
    model id in ``model_ids`` (issue #223 AC-6).

    Uses a minimal YAML round-trip via ``yaml.safe_load`` + ``yaml.safe_dump``
    -- this loses operator comments but preserves data.  Operators
    typically run ``cost-calibrate`` infrequently (after an Anthropic
    invoice arrives) so this trade is acceptable; if comment-preservation
    becomes a requirement the helper can swap to ``ruamel.yaml``.
    """
    import yaml as _yaml

    raw = _yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"cost-calibrate: {config_yaml} top-level YAML must be a mapping; got {type(raw).__name__}.")
    report_section = raw.setdefault("report", {})
    if not isinstance(report_section, dict):
        raise ValueError(
            f"cost-calibrate: {config_yaml} has report: that is not a mapping; cannot inject correction factors."
        )
    models_section = report_section.setdefault("models", {})
    if not isinstance(models_section, dict):
        raise ValueError(
            f"cost-calibrate: {config_yaml} has report.models: that is not a mapping; cannot inject correction factors."
        )
    for model_id in model_ids:
        entry = models_section.setdefault(model_id, {})
        if not isinstance(entry, dict):
            raise ValueError(
                f"cost-calibrate: report.models.{model_id} is not a mapping in {config_yaml}; "
                "cannot inject correction factor."
            )
        # If the operator has NOT listed this model's input/output yet,
        # seed them from the canonical defaults so the resulting yaml is
        # immediately valid (the schema requires both fields).
        if "input" not in entry or "output" not in entry:
            from devbench.constants import DEFAULT_FALLBACK_MODEL_RATES, DEFAULT_MODEL_RATES

            seed = DEFAULT_MODEL_RATES.get(model_id, DEFAULT_FALLBACK_MODEL_RATES)
            entry.setdefault("input", seed.input)
            entry.setdefault("output", seed.output)
        entry["correction_factor"] = float(correction_factor)
    # Atomic write to avoid partial reads if cost-calibrate is interrupted.
    tmp = config_yaml.with_suffix(config_yaml.suffix + ".tmp")
    tmp.write_text(_yaml.safe_dump(raw, sort_keys=False, default_flow_style=False), encoding="utf-8")
    tmp.replace(config_yaml)


def cmd_report(
    since: str = "",
    watch_interval: int = 0,
    once: bool = False,
    include: str = "",
    exclude: str = "",
    session: str = "",
    by_role: bool = False,
) -> int:
    """Print a formatted progress report with velocity and completion stats.

    Accepts scope-filter flags (spec section 4.2.2, AC-190-10, AC-190-11)
    and a named-session filter flag (spec section 4.4.6, AC-192-12, AC-192-13):

    - ``include`` -- raw ``--include`` token string; one-off include selector
      that overrides any active scope.json when non-empty.
    - ``exclude`` -- raw ``--exclude`` token string; one-off exclude selector.
    - ``session`` -- named-session filter; restricts the report to WUs claimed
      by that session and reads from the per-session orchestrator log.  Without
      this flag, all sessions are aggregated.

    When neither ``--include`` nor ``--exclude`` is supplied, the active
    ``scope.json`` (if any) is consulted instead.  In either case, a
    ``SCOPE: include=[...] exclude=[...] (started ...)`` banner is printed
    above the report body, and the WU lists shown in the report are filtered
    to the resolved scope.

    db-306 (spec Section 0 item 7, Section 4 FR-19, AC-46): a ``DRAIN
    REQUESTED`` line is printed above the report body for every pending
    drain signal (workspace-root and per-session).  The banner is rendered
    LIVE on all three emit paths -- the cached-snapshot fast-path, the
    one-shot live path, and every streamed frame -- and is never baked into
    ``generate_report``'s cached snapshot string, so a stale snapshot never
    hides a drain requested after it was written.

    Issue #163: streams continuously by default when stdout is a TTY.
    Pass ``once=True`` (or pipe / redirect stdout) to get the legacy
    one-shot behaviour suitable for scripts and CI consumers.

    The legacy ``--watch N`` flag is preserved for backward compatibility
    but emits a deprecation notice and falls through to the streaming
    loop (the integer interval is ignored; cadence is data-driven).

    Args:
        since: ISO-8601 UTC timestamp string; restricts the report window.
        watch_interval: Deprecated ``--watch N`` interval (ignored; streaming
            mode is always cadence-driven).  Pass ``> 0`` to trigger the
            deprecation notice and streaming fallback.
        once: When ``True``, forces one-shot rendering regardless of TTY.
        include: Raw ``--include`` token string (empty = not supplied).
        exclude: Raw ``--exclude`` token string (empty = not supplied).
        session: Named-session name from ``--session <name>`` (empty = not
            supplied, AC-192-12).  When non-empty, the report is filtered to
            that session's WUs and event-index queries read from the
            per-session log.

    Returns:
        ``0`` on success; ``1`` on scope-resolution error or missing session.
    """
    import warnings as _warnings
    from datetime import datetime

    from devbench.reporting.report import generate_report

    # AC-192-12: resolve per-session log when --session is provided.
    session_name: str | None = session if session else None
    if session_name is not None:
        session_log = _resolve_session_report_log(session_name)
        if session_log is None:
            return 1
        log_file: Path = session_log
    else:
        log_file = _resolve_log_file_path()

    # Resolve scope (AC-190-10, AC-190-11): per-command flags take
    # precedence over an active scope.json; when neither is present,
    # scope_filter is None and generate_report uses the full backlog.
    scope_args = _StatusArgs(include=include, exclude=exclude)
    scope = _resolve_scope_for_status(scope_args)
    if scope.error:
        print(scope.error, file=sys.stderr)
        return 1
    scope_filter: ScopeFilter | None = None
    if scope.has_scope:
        _render_scope_banner(scope.include, scope.exclude, scope.started_at)
        scope_filter = ScopeFilter(
            include=scope.include,
            exclude=scope.exclude,
        )

    since_dt = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    # Issue #163 streaming-default contract:
    # - --since is always one-shot (a frozen-window snapshot doesn't
    #   benefit from continuous refresh).
    # - --once forces one-shot (script / CI consumer escape hatch).
    # - stdout-not-a-TTY forces one-shot (piping / redirecting).
    # - --watch N is deprecated but still works; falls through to
    #   streaming mode with a warning.
    # - default on TTY -> streaming.
    is_tty = sys.stdout.isatty()
    force_once = once or (since_dt is not None) or not is_tty

    if force_once:
        # Issue #162 Phase 6: serve from the materialised snapshot when
        # fresh (orchestrator log unchanged since the snapshot was
        # written). Skips log parsing + per-window aggregation; falls
        # back to live ``generate_report`` when the snapshot is stale,
        # missing, or invalidated by a schema-version mismatch. The
        # ``--since`` path always recomputes because a frozen-window
        # snapshot is never the right answer for a custom-window query.
        # When a scope filter or session filter is active, bypass the
        # snapshot (the snapshot was rendered against the full backlog
        # and the aggregate log, not a session-specific view).
        if since_dt is None and scope_filter is None and session_name is None:
            from devbench.reporting.snapshot import read_snapshot

            cached = read_snapshot(WORKSPACE_ROOT, log_file)
            if cached is not None:
                # db-306 (spec Section 4 FR-19, AC-46): rendered LIVE, kept OUT
                # of the cached snapshot string, so a drain requested after the
                # snapshot was written is never hidden behind a stale cache.
                _render_drain_banner(WORKSPACE_ROOT)
                print(cached.report_text)
                return 0
        report = generate_report(
            log_path=log_file,
            since=since_dt,
            scope_filter=scope_filter,
            session_name=session_name,
            by_role=by_role,
        )
        # db-306 (spec Section 4 FR-19, AC-46): rendered LIVE, immediately
        # before the report body, not baked into generate_report's return value.
        _render_drain_banner(WORKSPACE_ROOT)
        print(report)
        return 0

    if watch_interval > 0:
        _warnings.warn(
            "--watch is deprecated; streaming is now the default; the interval value is ignored",
            DeprecationWarning,
            stacklevel=2,
        )

    # Streaming mode. Build a closure over the constant inputs so the
    # streaming loop's render_fn signature matches what
    # devbench.reporting.streaming.stream_report expects (log_path
    # keyword argument; everything else closed over).
    from devbench.reporting.streaming import stream_report

    report_started_at = datetime.now(UTC)

    def _render(*, log_path: Path) -> str:
        # db-306 (spec Section 4 FR-19, AC-46): the drain banner is rendered
        # to a StringIO and prepended to every frame so it stays LIVE across
        # streaming redraws, rather than being baked into generate_report's
        # cached return value.
        banner_buf = io.StringIO()
        _render_drain_banner(WORKSPACE_ROOT, file=banner_buf)
        report_text = generate_report(
            log_path=log_path,
            since=since_dt,
            report_started_at=report_started_at,
            scope_filter=scope_filter,
            session_name=session_name,
            by_role=by_role,
        )
        return banner_buf.getvalue() + report_text

    return stream_report(log_file, _render)


def cmd_archive_session(*argv: str) -> int:
    """Convert an ended session's JSONL log to a Parquet cold archive.

    Issue #162 Phase 7 (ADR-21). Opt-in via ``pip install devbench[archive]``;
    raises a structured error if ``pyarrow`` isn't installed.

    Usage: ``devbench archive-session <session-id> [--log-path <path>]``.
    Default ``--log-path`` is the workspace's standard orchestrator log;
    pass an explicit path when archiving a sibling log file (rare).
    """
    from devbench.reporting.archive import (
        ArchiveDependencyMissingError,
        archive_session,
    )

    args = list(argv)
    log_path_override: Path | None = None
    if "--log-path" in args:
        idx = args.index("--log-path")
        if idx + 1 >= len(args):
            print("ERROR: --log-path requires a value", file=sys.stderr)
            return 1
        log_path_override = Path(args[idx + 1])
        del args[idx : idx + 2]
    if len(args) != 1:
        print(
            "ERROR: archive-session takes exactly one positional argument: <session-id>",
            file=sys.stderr,
        )
        return 1
    session_id = args[0]
    log_path = log_path_override if log_path_override is not None else _resolve_log_file_path()

    try:
        out_path = archive_session(WORKSPACE_ROOT, session_id, log_path)
    except ArchiveDependencyMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"archive-session: wrote {out_path}")
    return 0


def cmd_rebuild_window_stats(*argv: str) -> int:
    """Walk the orchestrator log and rebuild every per-task aggregate JSON.

    Issue #162 Phase 2 (ADR-17). Idempotent; safe to run at any cadence.
    Used by operators after manually deleting ``.devbench/window-stats/``,
    and as a correctness guarantee when an in-flight orchestrator restarts
    on a workspace that didn't have the per-transition hook installed when
    the first transitions happened.
    """
    if argv:
        print(
            f"ERROR: cmd_rebuild_window_stats takes no arguments; got {argv!r}",
            file=sys.stderr,
        )
        return 1
    from devbench.reporting.window_stats import rebuild_from_log

    log_file = _resolve_log_file_path()
    count = rebuild_from_log(WORKSPACE_ROOT, log_file)
    print(f"rebuild-window-stats: wrote {count} per-task aggregate file(s) under .devbench/window-stats/")
    return 0


def cmd_write_snapshot(*argv: str) -> int:
    """Render the report once and persist it to ``<workspace>/.devbench/report-snapshot.json``.

    Issue #162 Phase 6 (ADR-20). Invoked by the orchestrate skill at the
    end of every loop iteration so subsequent ``devbench report --once``
    calls can serve from the snapshot in single-digit milliseconds
    instead of re-parsing the orchestrator log + recomputing every
    per-window aggregate. Idempotent; safe to invoke at any cadence.

    Pure write -- never mutates the backlog or the orchestrator log.
    Snapshot deletion is always safe; the next ``devbench report`` call
    rebuilds via the live aggregation path.
    """
    if argv:
        # No flags currently. Reject extras early so a future ``--force``
        # flag etc. doesn't silently absorb garbage from an upgraded
        # caller that uses a flag this version doesn't know.
        print(
            f"ERROR: cmd_write_snapshot takes no arguments; got {argv!r}",
            file=sys.stderr,
        )
        return 1
    from devbench.reporting.report import generate_report
    from devbench.reporting.snapshot import write_snapshot

    log_file = _resolve_log_file_path()
    report = generate_report(log_path=log_file)
    write_snapshot(WORKSPACE_ROOT, report, log_file)
    return 0


def cmd_log(message: str) -> int:
    """Append a message to the orchestrator log."""
    logger.info("[USER] %s", message)
    print(f"Logged: {message}")
    return 0


def cmd_read_unit(first_arg: str, second_arg: str = "") -> int:
    """Return work unit content and resolved repo path as JSON.

    Usage::

        read-unit <id>
        read-unit --strip-comments <id>

    Output::

        {
          "unit_id": "E0-F1-S1-T1",
          "work_unit_path": "/abs/path/to/unit.md",
          "repo_path": "/abs/path/to/repo",
          "repo": "org/repo",
          "content": "<full work unit markdown>"
        }

    When ``--strip-comments`` is passed, the ``content`` field contains the
    work unit text up to (but not including) the ``\\n## Comments`` marker.
    This prevents reviewer agents from seeing prior verdict entries.

    Used by plugin agents to get repo context without knowing devbench.yaml.
    """
    if first_arg == "--strip-comments":
        strip_comments = True
        unit_id = second_arg
        if not unit_id:
            print("ERROR: unit_id required after --strip-comments", file=sys.stderr)
            return 1
    else:
        strip_comments = False
        unit_id = first_arg

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    content = wu_file.read_text(encoding="utf-8")
    if strip_comments:
        marker = f"\n{COMMENTS_SECTION_HEADER}"
        idx = content.find(marker)
        if idx != -1:
            content = content[:idx]

    print(
        json.dumps(
            {
                "unit_id": unit.id,
                "work_unit_path": str(wu_file),
                "repo_path": str(repo_path),
                "repo": canonical_repo,
                "content": content,
            }
        )
    )
    return 0


def _resolve_default_branch(canonical_repo: str, repo_path: Path) -> str | None:
    """Return the configured default branch name or fall back to `origin/HEAD`.

    Returns None when neither source yields a branch name; in that case
    the caller should exit non-zero and surface an error to stderr.
    """
    configured = get_configured_default_branch(canonical_repo, RUNTIME_CONFIG)
    if configured:
        return configured

    rc, stdout, _ = run_command(
        ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
        cwd=repo_path,
    )
    if rc != 0 or not stdout.strip():
        return None
    return stdout.strip().removeprefix("origin/")


def _render_untracked_hunks(repo_path: Path, allowed: set[str]) -> list[str]:
    """Return synthetic diff hunks for every untracked file that is IN ``allowed``.

    ``allowed`` is the calling work unit's real Changes Manifest path set (db-296,
    FR-12). Without this filter, ``git ls-files --others`` reports every untracked
    file in the shared checkout, including a sibling task's dirty residue -- which
    then leaks into this unit's diff and misleads the review judges. An untracked
    file outside ``allowed`` is silently skipped, exactly like a Manifest-scoped
    ``git diff`` pathspec skips a tracked file outside the Manifest.
    """
    hunks: list[str] = []
    rc, stdout, _ = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo_path,
    )
    if rc != 0 or not stdout.strip():
        return hunks

    for raw_filepath in stdout.splitlines():
        filepath = raw_filepath.strip()
        if not filepath or filepath not in allowed:
            continue
        abs_path = repo_path / filepath
        try:
            file_content = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = file_content.splitlines(keepends=True)
        added = "".join(f"+{line}" for line in lines)
        hunks.append(
            f"diff --git a/{filepath} b/{filepath}\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/{filepath}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n"
            f"{added}"
        )
    return hunks


def _no_task_commit_found_message(unit_id: str, repo_path: Path) -> str:
    """Build the fail-fast diagnostic for defer_pr post-commit with zero matching commits.

    Shared by :func:`cmd_get_diff`'s defer_pr branch: the working tree is
    clean but :func:`devbench.work_unit_scope.resolve_changed_files` found no
    commit whose subject matches ``^<unit_id>:`` -- there is no HEAD
    fallback (db-247).
    """
    _, branch_out, _ = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    branch = branch_out.strip()
    return "\n".join(
        [
            f"ERROR: get-diff (defer_pr, post-commit): no commit found for work unit '{unit_id}' on branch '{branch}'.",
            f"The working tree is clean but no commit subject matches '^{unit_id}:'. This task's changes were not",
            "committed under its own name (possibly bundled into a sibling's commit, or the commit is missing).",
            f"Inspect with: git log --grep '^{unit_id}:' --format='%H %s' in {repo_path}.",
        ]
    )


def _append_defer_pr_task_commit_hunks(
    unit_id: str, repo_path: Path, scope: ScopeResult, pathspec: list[str], parts: list[str]
) -> int | None:
    """Append this unit's own commit-sha hunk(s) to ``parts`` in defer_pr mode.

    Only called by :func:`cmd_get_diff` when ``parts`` (staged + unstaged)
    is already empty. Returns ``None`` on success (``parts`` mutated in
    place); returns ``1`` (having already printed the fail-fast diagnostic)
    when ``scope`` carries no commit sha to substitute -- there is no HEAD
    fallback (db-247).
    """
    if not scope.commit_shas:
        print(_no_task_commit_found_message(unit_id, repo_path), file=sys.stderr)
        return 1
    for sha in scope.commit_shas:
        rc, stdout, _ = run_command(["git", "show", "--format=", sha, *pathspec], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)
    return None


def _append_branch_vs_default_hunk(
    canonical_repo: str, repo_path: Path, pathspec: list[str], parts: list[str]
) -> int | None:
    """Append the branch-vs-default hunk to ``parts`` in per_task_branch mode.

    Returns ``None`` on success (``parts`` mutated in place); returns ``1``
    (having already printed the fail-fast diagnostic) when the default
    branch cannot be resolved.
    """
    default_branch = _resolve_default_branch(canonical_repo, repo_path)
    if default_branch is None:
        print(
            f"ERROR: Cannot determine default branch for '{canonical_repo}'. "
            "Run 'git remote set-head origin --auto' to configure it.",
            file=sys.stderr,
        )
        return 1
    rc, stdout, _ = run_command(["git", "diff", f"origin/{default_branch}", *pathspec], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)
    return None


def _resolve_scope_or_report(unit_id: str, repo_path: Path, mode: str) -> ScopeResult | None:
    """Resolve ``unit_id``'s scope via :func:`resolve_changed_files`, or ``None`` on failure.

    Shared by :func:`cmd_get_diff` and :func:`cmd_check_manifest_scope` (spec
    4.3, AC-9) so both verbs resolve scope through the single ADR-12
    mode-aware implementation and report a resolution failure identically.
    On failure, the verbatim ERROR is already printed to stderr; the caller
    must return 1 without printing anything further.
    """
    from devbench.backlog.manifest import ManifestParseError

    try:
        return resolve_changed_files(unit_id, repo_path, mode)
    except ManifestParseError as exc:
        print(f"ERROR: Cannot scope diff for '{unit_id}': Changes Manifest is malformed: {exc}", file=sys.stderr)
        return None
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: Cannot scope diff for '{unit_id}': {exc}", file=sys.stderr)
        return None


def _resolve_unit_repo_and_path(unit_id: str) -> tuple[WorkUnit, str, Path] | None:
    """Return ``(unit, canonical_repo, repo_path)`` for ``unit_id``, or ``None`` on failure.

    Shared by :func:`cmd_get_diff` and :func:`cmd_check_manifest_scope` so both
    verbs report "unit not found" / "no local path configured" identically.
    Prints the ERROR itself; the caller's job is only to propagate a non-zero
    exit code.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return None

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return None
    return unit, canonical_repo, repo_path


def cmd_get_diff(unit_id: str) -> int:
    """Return the combined git diff for the work unit's target repo.

    Mode-aware per ADR-12, and Manifest-scoped per db-296/db-247 (spec FR-12,
    FR-13, 4.3). Scope (which files, which ADR-12 mode, and -- in defer_pr
    mode -- this unit's own commit sha(s)) is resolved through the single
    shared implementation, :func:`devbench.work_unit_scope.resolve_changed_files`
    (spec 4.3, AC-9), so a sibling task's dirty residue in the shared
    checkout can never leak into this unit's diff and every scope consumer
    agrees on the same answer.

    In the default per-task-branch mode, emits staged + unstaged +
    branch-vs-default + untracked hunks, all pathspec-scoped. In defer_pr mode
    (single_branch + defer_pr: true), the branch-vs-default hunk is omitted
    because it accumulates every prior task's commits on the shared branch;
    instead the function emits staged + unstaged + untracked, and when both
    staged and unstaged are empty (a post-commit judge invocation) emits
    ``git show`` hunks for the scope's resolved commit sha(s) instead of the
    old unconditional ``git show HEAD``.

    A missing work-unit file or a malformed Changes Manifest fails fast. An
    empty (verification-only) Manifest returns ``(no changes)`` -- never an
    unscoped whole-tree diff.

    Used by plugin agents instead of running raw git commands so they do not
    need to know the repo path or the mode.
    """
    from devbench.config import DEFER_PR

    resolved = _resolve_unit_repo_and_path(unit_id)
    if resolved is None:
        return 1
    unit, canonical_repo, repo_path = resolved

    mode = MODE_DEFER_PR if DEFER_PR else MODE_PER_TASK_BRANCH
    scope = _resolve_scope_or_report(unit_id, repo_path, mode)
    if scope is None:
        return 1
    if not scope.files:
        print("(no changes)")
        return 0
    pathspec = ["--", *scope.files]

    parts: list[str] = []

    rc, stdout, _ = run_command(["git", "diff", "--cached", *pathspec], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    rc, stdout, _ = run_command(["git", "diff", *pathspec], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    if scope.mode == MODE_DEFER_PR:
        if not parts:
            error = _append_defer_pr_task_commit_hunks(unit_id, repo_path, scope, pathspec, parts)
            if error is not None:
                return error
    else:
        error = _append_branch_vs_default_hunk(canonical_repo, repo_path, pathspec, parts)
        if error is not None:
            return error

    parts.extend(_render_untracked_hunks(repo_path, allowed=set(scope.files)))

    print("\n".join(parts) if parts else "(no changes)")
    return 0


def cmd_check_manifest_scope(unit_id: str) -> int:
    """Read-only check: is the staged file set within ``unit_id``'s Changes Manifest?

    Exposes :func:`devbench.backlog.manifest.assert_staged_matches_manifest`'s
    check without mutating anything (spec 4.C, db-296 x db-327). The
    changes-manifest judge shells out to this verb to get a deterministic
    staged-vs-Manifest signal that a judged read of the diff cannot drift from,
    now that ``get-diff`` is Manifest-scoped and a staged-but-unmanifested file
    no longer appears in the diff it reads (FR-11-A2). Scope is resolved
    through the same shared implementation ``get-diff`` uses (spec 4.3, AC-9).

    Prints the out-of-Manifest staged paths (embedded in the underlying
    ``RuntimeError``) and exits non-zero when the staged set is not within the
    Manifest; exits zero when it is. A missing work-unit file or malformed
    Manifest fails fast with the same verbatim ERROR as ``get-diff``.
    """
    from devbench.backlog.manifest import assert_staged_matches_manifest
    from devbench.config import DEFER_PR

    resolved = _resolve_unit_repo_and_path(unit_id)
    if resolved is None:
        return 1
    unit, _canonical_repo, repo_path = resolved

    mode = MODE_DEFER_PR if DEFER_PR else MODE_PER_TASK_BRANCH
    scope = _resolve_scope_or_report(unit_id, repo_path, mode)
    if scope is None:
        return 1

    try:
        assert_staged_matches_manifest(repo_path, scope.files)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"check-manifest-scope: staged set for '{unit_id}' is within the Changes Manifest.")
    return 0


def _resolve_ancestry_repo_context(unit_id: str) -> tuple[str, Path] | None:
    """Resolve *unit_id* to its canonical repo + local checkout path for check-ancestry.

    Returns ``None`` (after printing its own ``ERROR:`` to stderr) when the
    unit is unknown or the repo has no configured local path. Split out of
    :func:`cmd_check_ancestry` purely to keep that function's return-count
    within the project's complexity lint budget.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return None

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return None
    return canonical_repo, repo_path


def cmd_check_ancestry(unit_id: str, dependency_ref: str, target_ref: str = "") -> int:
    """Verify a declared work-group dependency has actually merged, via real git ancestry.

    This is the **canonical, single-source-of-truth check** for "is a declared
    prerequisite deliverable" across the pipeline (see
    ``docs/cross-backlog-dependencies.md``). It exists so that
    ``spec-to-backlog``-generated ancestry-gate tasks, and any other tooling
    that needs the same answer, all shell out to one well-tested command
    instead of inventing a weaker proxy (e.g. checking for a local
    snapshot/report file, which can go stale or never existed).

    Runs ``git merge-base --is-ancestor <dependency_ref> <target_ref>`` in
    the work unit's target repo:

    - *dependency_ref* should be a fully qualified, fetchable ref (e.g.
      ``origin/<dependency-branch>`` or a commit SHA). This function does
      not invent a remote-tracking prefix for a bare branch name.
    - *target_ref* defaults to ``origin/<default-branch>`` (resolved the
      same way as :func:`cmd_get_diff`) when omitted -- i.e. "has the
      dependency merged into the branch this work group's tasks are based
      against."

    Best-effort ``git fetch origin`` runs first so a stale local view of
    ``origin`` does not produce a false "not merged" result; a fetch
    failure (offline, renamed remote) is logged to stderr but does not
    abort the check -- the merge-base call still runs against whatever is
    locally known.

    Exit contract (unlike most devbench commands, which return 0 and encode
    failure only in JSON): returns 0 only when *dependency_ref* IS an
    ancestor of *target_ref* (the gate should pass). Returns 1 for
    "not yet an ancestor" (the gate should block) as well as for any error
    that prevents a decision (unknown work unit, empty dependency ref,
    unresolvable repo/default-branch, invalid git refs). A JSON status line
    is always printed to stdout so callers get a machine-readable record
    either way.

    Known limitation: ``git merge-base --is-ancestor`` is a strict commit
    -graph ancestry check. It can report "not an ancestor" for a
    logically-satisfied dependency when the producer branch was squash
    -merged, rebased, or landed via a fix-pack branch that does not carry
    the original branch's commit hashes. Callers hitting this should
    target the actual merge commit / tag on the shared trunk (e.g.
    ``origin/main`` after the squash-merge lands) rather than the
    original feature branch ref, or fall back to the manual-blocker idiom
    (``docs/manual-blockers.md``) with an operator-verified AC when no
    ancestry-preserving ref exists.

    Usage: check-ancestry <unit_id> <dependency-ref> [<target-ref>]
    """
    if not dependency_ref.strip():
        print("ERROR: check-ancestry requires a non-empty dependency ref", file=sys.stderr)
        return 1

    resolved = _resolve_ancestry_repo_context(unit_id)
    if resolved is None:
        return 1
    canonical_repo, repo_path = resolved

    resolved_target_ref = target_ref.strip()
    if not resolved_target_ref:
        default_branch = _resolve_default_branch(canonical_repo, repo_path)
        if default_branch is None:
            print(
                f"ERROR: Cannot determine default branch for '{canonical_repo}' to use as the "
                "ancestry target. Run 'git remote set-head origin --auto' to configure it, or "
                "pass an explicit target-ref.",
                file=sys.stderr,
            )
            return 1
        resolved_target_ref = f"origin/{default_branch}"

    # Best-effort refresh so a stale local `origin` doesn't produce a false
    # "not merged" result. Non-fatal: offline runs / renamed remotes still
    # fall through to the merge-base check below against whatever refs are
    # already known locally.
    fetch_rc, _fetch_stdout, fetch_stderr = run_command(["git", "fetch", "origin"], cwd=repo_path)
    if fetch_rc != 0:
        print(
            f"WARNING: 'git fetch origin' failed, checking against local refs as-is: {fetch_stderr.strip()}",
            file=sys.stderr,
        )

    rc, _stdout, stderr = run_command(
        ["git", "merge-base", "--is-ancestor", dependency_ref, resolved_target_ref],
        cwd=repo_path,
    )

    if rc == 0:
        print(
            json.dumps(
                {
                    "unit_id": unit_id,
                    "status": "ancestor",
                    "dependency_ref": dependency_ref,
                    "target_ref": resolved_target_ref,
                }
            )
        )
        return 0

    if rc == 1:
        print(
            json.dumps(
                {
                    "unit_id": unit_id,
                    "status": "not_ancestor",
                    "dependency_ref": dependency_ref,
                    "target_ref": resolved_target_ref,
                }
            )
        )
        print(
            f"BLOCKED: '{dependency_ref}' is not yet an ancestor of '{resolved_target_ref}'. "
            "The declared dependency has not merged. Do not proceed with any other task in "
            "this backlog until this check passes.",
            file=sys.stderr,
        )
        return 1

    # rc > 1 (or the run_command sentinel 127 for a missing/timed-out git)
    # means git itself could not answer the question -- unknown ref, not a
    # commit-ish, or the executable is missing. Treat as a hard failure
    # rather than silently reporting "not merged".
    print(
        f"ERROR: 'git merge-base --is-ancestor {dependency_ref} {resolved_target_ref}' could not "
        f"be evaluated (rc={rc}): {stderr.strip()}",
        file=sys.stderr,
    )
    return 1


def _select_test_command(repo_path: Path) -> list[str]:
    """Return the test-suite command to run in *repo_path*.

    Uses ``make test`` when the repo has a Makefile with a ``test`` target,
    otherwise falls back to a bare ``pytest`` invocation. Shared by
    :func:`cmd_run_tests` (task-scoped evidence for the test-reviewer judge)
    and :func:`cmd_check_shared_file_impact` (full-suite regression gate),
    which always run the same command -- the two commands differ in how the
    result is interpreted, not in what gets invoked.
    """
    rc, _stdout, _stderr = run_command(["make", "-n", "test"], cwd=repo_path)
    if rc == 0:
        return ["make", "test"]
    return ["pytest", "--no-header", "-q", "-p", "no:cacheprovider"]


def cmd_run_tests(unit_id: str) -> int:
    """Run the test suite for the work unit's target repo and return the output.

    Uses ``make test`` when the repo has a Makefile with a ``test`` target,
    otherwise falls back to ``pytest``.  Exits non-zero if the test run fails.

    Used by the test-reviewer agent to obtain test execution evidence.

    Note: this always invokes the full suite command (`_select_test_command`);
    it is not scoped to the work unit's Changes Manifest. What IS scoped by
    convention is which parts of the *output* an executor/reviewer treats as
    this task's responsibility -- nothing here enforces that at the tooling
    level. When a task's diff touches a shared/high-fan-in file (per
    `gates.repos.<repo>.shared_file_impact.patterns` in devbench.yaml),
    `check-shared-file-impact` should be used instead: it runs this same
    command but diffs the failure set against a stored baseline and blocks
    on newly-introduced failures.
    """
    from devbench.config import TEST_TIMEOUT

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    cmd = _select_test_command(repo_path)

    rc, stdout, stderr = run_command(cmd, cwd=repo_path, timeout=TEST_TIMEOUT)
    combined = "\n".join(part for part in (stdout, stderr) if part.strip())
    print(combined if combined else "(no output)")
    return rc


# Reachability check (caylent-solutions/devbench-internal-backlog#10, spec
# `integration-reality-gates-hardening.md` 4.4; machine-blocking,
# `constants.GATE_TIERS`).
#
# A grep-based heuristic is deliberately language-agnostic: devbench's target
# repos span many stacks, so a hardcoded JS/TS import-graph walker would not
# generalise. The tool's job is only to surface cheap candidates; the LLM
# code-reviewer makes the final judgment call (dynamic imports, barrel
# re-exports, and lazy route splits are known false-positive shapes it can
# rule out that a grep cannot).
#
# Which extensions are source, which paths are tests, and which filenames
# are entry points is answered once, by `devbench.source_classification`
# (spec 4.3, D-3) -- this module no longer declares its own copies.
#
# The matcher is word-boundary and source-classified (register 315-D01,
# 315-D02): `git grep --word-regexp --fixed-strings` so a symbol named
# `Card` is never satisfied by `Cardinal` or `discardCards`, restricted to
# pathspecs derived from `source_classification.SOURCE_EXTENSIONS` so a
# mention in `CHANGELOG.md` or a design doc can never clear an orphan. The
# old source-comment escape-hatch marker (register finding 5, AC-FUNC-006)
# is gone; `uv run devbench log-waiver ... --gate reachability` (spec 4.9,
# PM-5) is the only way to clear a finding without fixing the wiring, and it
# always leaves an audited record.
#
# Transitive reachability (issue #10 AC2, spec 4.4 bullet 2): a referrer
# clears the candidate only when the referrer is itself reachable from the
# configured `gates.reachability.entry_points` set
# (`_is_reachable_from_entry_points`, cycle-safe via a visited set); a
# candidate whose every referrer is itself unreachable is reported
# `[POTENTIALLY UNREACHABLE via orphan-chain]`, distinct from the
# no-referrer-at-all `[POTENTIALLY UNREACHABLE]` shape. `entry_points`
# defaults, when unconfigured, to `source_classification`'s entry-point
# stem convention rather than an empty walk (D-17, AC-FUNC-006).

_REACHABILITY_GATE_NAME: str = "reachability"
_REACHABILITY_STATUS_DISABLED: str = "disabled"
_REACHABILITY_STATUS_PASS: str = "pass"
_REACHABILITY_STATUS_FAIL: str = "fail"

# Reachability is machine-blocking (`constants.GATE_TIERS`, spec Section
# 3.6/D-6): the operator is the only waiver authority. `_reachability_prepare_run`
# filters `gate_records.gate_waiver_targets`'s full records down to this
# attribution ONLY before a target can be reported `[WAIVED]`, excluded from
# the blocking `findings` count, or contribute to a clean run that persists a
# `[GATE_PASS reachability]` record -- an executor-attributed waiver alone
# must never launder into a record `mark-done`'s generic gate-record
# invariant (`BacklogManager._check_gate_pass_done_invariant`) would then
# accept. Derived from `constants.GATE_WAIVER_ATTRIBUTION_OPERATOR`, the
# single-sourced attribution vocabulary `devbench.backlog.manager` (both
# `compose_gate_waiver_record` and this same done-gate invariant) also
# consumes, rather than a second hand-copied literal.
_REACHABILITY_WAIVER_REQUIRED_ATTRIBUTION: str = GATE_WAIVER_ATTRIBUTION_OPERATOR

# `git grep` exit codes at or above this value are a genuine plumbing
# failure (spec 4.4 bullet 3, Section 7); rc=1 is "no match" data, never an
# error, and is never swallowed by a `continue`.
_REACHABILITY_GIT_GREP_FAILURE_THRESHOLD: int = 2

# How many importer paths `_reachability_ok_block` lists by name before
# collapsing the remainder into a count -- a named constant so the limit is
# declared once rather than repeated as an inline literal at each of its
# three use sites.
_REACHABILITY_IMPORTER_DISPLAY_LIMIT: int = 10

_REACHABILITY_EXPORT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"export\s+default\s+(?:function|class)\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"export\s+(?:const|function|class|let|var)\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*def\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*func\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?class\s+([A-Za-z_]\w*)", re.MULTILINE),
)

_REACHABILITY_EXPORT_BRACE_RE = re.compile(r"export\s*\{([^}]+)\}")


def _is_reachability_test_path(rel_path: str) -> bool:
    """Return True when *rel_path* is a test, spec, story, or fixture file.

    Used both to exclude such files from the candidate set and to exclude
    them when counting importers -- a file referenced only by its own
    test/story file is exactly the orphan pattern this check exists to
    catch. Delegates to :func:`devbench.source_classification.is_test_path`
    (spec 4.3, D-3).
    """
    return is_test_path(rel_path)


def _is_reachability_candidate(rel_path: str) -> bool:
    """Return True when *rel_path* is a source file this check should examine."""
    normalized = rel_path.replace("\\", "/")
    suffix = "." + normalized.rsplit(".", 1)[-1].lower() if "." in normalized.rsplit("/", 1)[-1] else ""
    if not is_source_extension(suffix):
        return False
    if _is_reachability_test_path(normalized):
        return False
    filename = normalized.rsplit("/", 1)[-1]
    stem = filename[: -len(suffix)] if suffix else filename
    return not is_entry_point_stem(stem)


def _derive_reachability_basename_symbol(rel_path: str) -> str:
    """Return the artifact name implied by *rel_path*'s basename.

    ``Foo/index.tsx`` derives ``Foo`` (the directory name) rather than
    ``index``, since barrel-file components are conventionally imported by
    their containing folder's name.
    """
    normalized = rel_path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    filename = parts[-1] if parts else normalized
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    if stem.lower() == "index" and len(parts) > 1:
        return parts[-2]
    return stem


def _extract_reachability_symbols(content: str, rel_path: str) -> list[str]:
    """Return candidate exported symbol names for a file: basename + regex-extracted exports."""
    symbols = {_derive_reachability_basename_symbol(rel_path)}

    for pattern in _REACHABILITY_EXPORT_PATTERNS:
        for match in pattern.finditer(content):
            name = match.group(1)
            if name:
                symbols.add(name)

    brace_match = _REACHABILITY_EXPORT_BRACE_RE.search(content)
    if brace_match:
        for token in brace_match.group(1).split(","):
            name = token.strip().split(" as ")[0].strip()
            if name and re.fullmatch(r"[A-Za-z_$][\w$]*", name):
                symbols.add(name)

    return sorted(s for s in symbols if s)


def _reachability_search_pathspecs() -> list[str]:
    """Return the git pathspec globs restricting the importer search to classified source files.

    One ``*.<ext>`` glob per extension in
    :data:`devbench.source_classification.SOURCE_EXTENSIONS` (spec 4.4
    bullet 1, register 315-D02): a mention of the symbol in prose
    (``CHANGELOG.md``, a design doc, a work-unit markdown file) can never
    clear an orphan, because those extensions are never in the search's
    pathspec at all.
    """
    return sorted(f"*.{ext.lstrip('.')}" for ext in SOURCE_EXTENSIONS)


def _search_reachability_importers(repo_path: Path, rel_path: str, symbols: list[str]) -> list[str]:
    """Return non-test, source-classified files (excluding *rel_path*) that reference any of *symbols*.

    Searches tracked and untracked files via a word-boundary, fixed-string
    ``git grep`` (``--word-regexp --fixed-strings``; spec 4.4 bullet 1,
    register 315-D01): a symbol named ``Card`` is never satisfied by
    ``Cardinal`` or ``discardCards``, because ``--word-regexp`` requires no
    identifier character on either side of the match. The search is
    restricted to :func:`_reachability_search_pathspecs`'s source-classified
    globs (register 315-D02), so a hit inside a non-source file can never
    occur. A hit inside a test/spec/story/fixture file (per
    :func:`_is_reachability_test_path`) is dropped -- a reference from a
    file's own test suite does not make it reachable from the app.

    ``git grep`` rc semantics (spec 4.4 bullet 3, Section 7): rc=0 is a
    match, rc=1 is "no match" (data, not an error, and never swallowed by a
    bare ``continue``), and rc>=2 is a genuine plumbing failure raised here
    as :class:`RuntimeError` with the raw stderr attached so the caller can
    fail loud instead of silently treating a broken search as "no
    importers".

    Raises:
        RuntimeError: ``git grep`` exited rc>=2 for any symbol.
    """
    pathspecs = _reachability_search_pathspecs()
    importers: set[str] = set()
    for symbol in symbols:
        cmd = [
            "git",
            "grep",
            "--word-regexp",
            "--fixed-strings",
            "--files-with-matches",
            "--untracked",
            "-e",
            symbol,
            "--",
            *pathspecs,
        ]
        rc, stdout, stderr = run_command(cmd, cwd=repo_path)
        if rc >= _REACHABILITY_GIT_GREP_FAILURE_THRESHOLD:
            raise RuntimeError(stderr.strip() or f"'{' '.join(cmd)}' exited {rc} with no stderr output")
        if rc == 1:
            continue
        for line in stdout.splitlines():
            hit = line.strip()
            if not hit or hit == rel_path:
                continue
            if _is_reachability_test_path(hit):
                continue
            importers.add(hit)
    return sorted(importers)


def _matches_reachability_entry_point(rel_path: str, entry_points: tuple[str, ...]) -> bool:
    """Return True when *rel_path* is one of the resolved ``entry_points`` roots.

    Two independent match shapes share one predicate (issue #10 AC2, spec
    4.4 bullet 2): an explicit, operator-configured ``entry_points`` value
    (``gates.reachability.entry_points``, spec 4.1's "a list of
    repo-relative paths" contract) matches *rel_path* literally, case-
    sensitively, exactly like a real filesystem path. The
    ``source_classification``-derived built-in default
    (``config_loader.resolve_gate_config``'s ``entry_points`` field when
    absent from config, AC-FUNC-006) instead carries bare filename-stem
    conventions (``main``, ``app``, ``index``, ...) and matches against
    *rel_path*'s own basename stem, lower-cased to match
    :func:`devbench.source_classification.is_entry_point_stem`'s existing
    case-insensitive convention (a component named ``App.tsx`` is a
    recognised composition root regardless of case). The caller never needs
    to know which of the two shapes applies -- both resolve through the
    same ``entry_points`` tuple returned by ``resolve_gate_config``.
    """
    normalized = rel_path.replace("\\", "/")
    if normalized in entry_points:
        return True
    filename = normalized.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem.lower() in entry_points


class _ReachabilityReferrerReadError(Exception):
    """Raised when a referrer file cannot be read during the entry-point
    reachability walk (:func:`_is_reachable_from_entry_points`).

    Carries the referrer's own repo-relative path and the original read
    failure so :func:`_reachability_scan_candidates` can render a
    ``[LOAD_ERROR]`` finding naming the ACTUAL unreadable file (code_review
    round-2 FAIL_FAST finding: cli.py:4888-4891 used to catch the read
    failure unbound and return the default value ``False``, silently
    converting "this referrer could not be read" into "this referrer is
    unreachable"). This mirrors the only other ``except (OSError,
    UnicodeDecodeError))`` site in this module -- the top-level candidate
    read at the head of :func:`_reachability_scan_candidates`, which
    already renders a counted ``[LOAD_ERROR]`` block instead of guessing a
    verdict -- so a referrer that cannot be read gets the identical
    fail-loud contract instead of a silent, wrong ``False``.
    """

    def __init__(self, rel_path: str, cause: OSError | UnicodeDecodeError) -> None:
        super().__init__(f"{rel_path}: {cause}")
        self.rel_path = rel_path
        self.cause = cause


def _is_reachable_from_entry_points(
    repo_path: Path, rel_path: str, entry_points: tuple[str, ...], visited: set[str] | None = None
) -> bool:
    """Return True when *rel_path* is transitively reachable from *entry_points*.

    Issue #10 AC2 / spec 4.4 bullet 2: a referencing file counts toward
    clearing an orphan candidate only when the referencing file is ITSELF
    reachable from the configured entry-point set. Reachability is defined
    recursively over the same import-edge relation
    :func:`_search_reachability_importers` already computes for the
    top-level candidate: *rel_path* is reachable when it IS a configured
    entry point (:func:`_matches_reachability_entry_point`), or when at
    least one of ITS OWN non-test importers is itself reachable. Walking
    "importers of X" back toward the entry-point set is equivalent to
    walking "imports" forward from the entry-point set (spec 4.4 bullet 2's
    "follow import and require edges") without a second, forward
    graph-construction implementation.

    *visited* bounds the walk against a mutual-import cycle (AC-FUNC-004):
    every path visited in the current walk is recorded before recursing, so
    a cycle terminates as "not reachable via this branch" rather than
    recursing forever. Callers never pass *visited* explicitly -- it exists
    so the function can thread the same set through its own recursive
    calls; a fresh, empty set is created per top-level call.

    Args:
        repo_path: Absolute path to the target repo checkout.
        rel_path: Repo-relative path being tested for reachability.
        entry_points: The resolved ``gates.reachability.entry_points``
            value (``resolve_gate_config("reachability", repo).values
            ["entry_points"]``).
        visited: Internal recursion state; leave at the default.

    Returns:
        True when *rel_path* is an entry point, or is (transitively)
        imported by one. False when the walk exhausts every referrer
        without reaching an entry point, or when *rel_path* does not exist
        on disk.

    Raises:
        _ReachabilityReferrerReadError: *rel_path* exists on disk but
            cannot be read (permission failure or non-UTF-8 decode
            failure). The caller must not treat this as "unreachable" --
            see the exception's own docstring.
        RuntimeError: ``git grep`` exited rc>=2 while searching for one of
            *rel_path*'s own importers (propagated from
            :func:`_search_reachability_importers`, same fail-loud contract
            as the top-level candidate search).
    """
    if visited is None:
        visited = set()
    if rel_path in visited:
        return False
    visited.add(rel_path)

    if _matches_reachability_entry_point(rel_path, entry_points):
        return True

    abs_path = repo_path / rel_path
    if not abs_path.is_file():
        return False
    try:
        content = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _ReachabilityReferrerReadError(rel_path, exc) from exc

    symbols = _extract_reachability_symbols(content, rel_path)
    importers = _search_reachability_importers(repo_path, rel_path, symbols)
    return any(_is_reachable_from_entry_points(repo_path, importer, entry_points, visited) for importer in importers)


def _reachability_ok_block(rel_path: str, symbols: list[str], importers: list[str]) -> str:
    """Render the ``[OK]`` report block for a candidate with at least one importer."""
    lines = [
        f"[OK] {rel_path}",
        f"  Symbols checked: {', '.join(symbols)}",
        f"  Non-test importers found: {len(importers)}",
    ]
    for importer in importers[:_REACHABILITY_IMPORTER_DISPLAY_LIMIT]:
        lines.append(f"    - {importer}")
    if len(importers) > _REACHABILITY_IMPORTER_DISPLAY_LIMIT:
        lines.append(f"    ... and {len(importers) - _REACHABILITY_IMPORTER_DISPLAY_LIMIT} more")
    return "\n".join(lines)


def _reachability_unreachable_block(rel_path: str, symbols: list[str], canonical_repo: str) -> str:
    """Render the ``[POTENTIALLY UNREACHABLE]`` report block for an orphan candidate.

    Points the remediation at ``log-waiver`` (spec 4.9, PM-5) instead of the
    deleted source-comment escape-hatch marker (register finding 5,
    AC-FUNC-006).
    """
    return "\n".join(
        [
            f"[POTENTIALLY UNREACHABLE] {rel_path}",
            f"  Symbols checked: {', '.join(symbols)}",
            "  Non-test importers found: 0",
            "  No reference to these symbols was found outside test/story files in "
            f"'{canonical_repo}'. Confirm this artifact is wired into the app's real "
            "composition root (route table, parent container, shell), or record a legitimate "
            "deferral with 'uv run devbench log-waiver <judge> <unit-id> --gate reachability "
            "--target <path> --reason <reason> --operator'.",
        ]
    )


def _reachability_orphan_chain_block(
    rel_path: str, symbols: list[str], importers: list[str], canonical_repo: str
) -> str:
    """Render the ``[POTENTIALLY UNREACHABLE via orphan-chain]`` report block (issue #10 AC2, spec 4.4 bullet 2).

    Distinct from :func:`_reachability_unreachable_block`: *rel_path* DOES
    have at least one non-test importer -- unlike the no-referrer-at-all
    shape -- but every one of those importers is itself unreachable from
    ``gates.reachability.entry_points``
    (:func:`_is_reachable_from_entry_points`), so the reference chain never
    actually terminates at a real composition root.
    """
    lines = [
        f"[POTENTIALLY UNREACHABLE via orphan-chain] {rel_path}",
        f"  Symbols checked: {', '.join(symbols)}",
        f"  Non-test importers found: {len(importers)} (none reachable from a configured entry point)",
    ]
    for importer in importers[:_REACHABILITY_IMPORTER_DISPLAY_LIMIT]:
        lines.append(f"    - {importer}")
    if len(importers) > _REACHABILITY_IMPORTER_DISPLAY_LIMIT:
        lines.append(f"    ... and {len(importers) - _REACHABILITY_IMPORTER_DISPLAY_LIMIT} more")
    lines.append(
        "  Every referrer above is itself unreachable from a configured entry point "
        f"('gates.reachability.entry_points') in '{canonical_repo}'. Wire one of the referrers into "
        "the app's real composition root (route table, parent container, shell), or record a "
        "legitimate deferral with 'uv run devbench log-waiver <judge> <unit-id> --gate reachability "
        "--target <path> --reason <reason> --operator'."
    )
    return "\n".join(lines)


def _reachability_missing_entry_point(repo_path: Path, entry_points: tuple[str, ...], provenance: str) -> str | None:
    """Return the first configured ``entry_points`` path missing from *repo_path*, or None.

    Extracted out of :func:`cmd_check_reachability` to keep that function's
    branch/return count within ruff's thresholds. Always returns ``None``
    for the built-in (stem-based) default (*provenance* ==
    :data:`GATE_PROVENANCE_BUILTIN`): those entries are matching
    conventions, not literal paths, so no existence check applies to them
    (spec Section 7 fail-fast rule, AC-FUNC-005 error path).

    Defense-in-depth containment check (code_review round-2
    MISSING_AC_EVIDENCE finding), independent of
    :func:`devbench.config_loader._parse_reachability_entry_points`'s own
    absolute/``..`` rejection at the config-parse boundary: a bare
    ``(repo_path / entry_point).is_file()`` check is not by itself a safe
    existence guard, because pathlib's ``/`` operator DISCARDS
    *repo_path* whenever *entry_point* is absolute
    (``Path('/tmp/x') / '/etc/hostname' == Path('/etc/hostname')``), so an
    absolute or ``..``-escaping path could otherwise satisfy ``.is_file()``
    against a file OUTSIDE the checkout and defeat the very guard this
    function exists to provide. Every candidate is therefore resolved and
    required to stay inside *repo_path* before the existence check runs.
    """
    if provenance == GATE_PROVENANCE_BUILTIN:
        return None
    resolved_repo_path = repo_path.resolve()
    for entry_point in entry_points:
        candidate = (repo_path / entry_point).resolve()
        if not candidate.is_relative_to(resolved_repo_path):
            return entry_point
        if not candidate.is_file():
            return entry_point
    return None


def _reachability_load_error_block(rel_path: str, exc: OSError | UnicodeDecodeError) -> str:
    """Render the ``[LOAD_ERROR]`` report block for a candidate that could not be read.

    Replaces the old silent ``[SKIPPED]`` branch (spec 4.4 bullet 4): an
    unreadable candidate is now a counted finding that drives exit 1, not a
    silent pass. *exc* is either a permission/IO failure (``OSError``) or a
    non-UTF-8 decode failure (``UnicodeDecodeError``, which is a
    ``ValueError`` subclass, not an ``OSError`` -- both are "candidate could
    not be read" per AC-FUNC-005, and neither may crash the gate uncaught).
    """
    return f"[LOAD_ERROR] {rel_path}\n  Could not read file: {exc}"


def _gate_override_repos(gate: str, runtime_config: "RuntimeConfig") -> list[str]:
    """Return the repos carrying an explicit override object for *gate*, sorted.

    "Carrying an override" means ``gates.repos.<repo>.<gate>`` is present at
    all in the parsed config (a non-``None`` override object) -- even one
    that only sets a structural field like ``shared_file_impact.patterns``
    and leaves ``enabled`` inheriting the project level. This is the set
    rendered in the ``devbench gates`` "repos" column (AC-E2-F1-S2-T1-2),
    distinct from ``_gate_resolution_repo`` below (which repo's ``enabled``
    override actually drives the row's resolved status).

    Args:
        gate: Gate name; one of ``constants.GATE_NAMES``.
        runtime_config: Loaded runtime configuration.

    Returns:
        Sorted list of ``org/repo`` names with an override for *gate*; empty
        when none carry one.
    """
    return sorted(
        repo for repo, overrides in runtime_config.gates.repos.items() if getattr(overrides, gate) is not None
    )


def _gate_resolution_repo(gate: str, override_repos: list[str], runtime_config: "RuntimeConfig") -> str:
    """Pick the repo whose override should drive *gate*'s resolved status.

    ``devbench gates`` renders one row per gate, not one row per repo, so
    when multiple repos carry an override for the same gate this picks the
    first (sorted, so deterministic) repo whose override actually sets
    ``enabled`` -- the only field ``resolve_gate_config`` uses to compute
    the row's ``status``/``provenance`` columns. Falls back to the first
    override repo (still "carrying an override", just not one that changes
    ``enabled``) when none of them set it, or to ``""`` (a no-op repo key
    that matches no entry in ``runtime_config.gates.repos``) when *gate* has
    no override at all -- ``resolve_gate_config`` then resolves purely from
    the project/built-in/env layers.

    Args:
        gate: Gate name; one of ``constants.GATE_NAMES``.
        override_repos: Result of ``_gate_override_repos(gate, runtime_config)``.
        runtime_config: Loaded runtime configuration.

    Returns:
        The repo name to pass as ``resolve_gate_config``'s ``repo`` argument.
    """
    for repo in override_repos:
        override = getattr(runtime_config.gates.repos[repo], gate)
        if getattr(override, "enabled", None) is not None:
            return repo
    return override_repos[0] if override_repos else ""


def _format_gates_table(records: Sequence[tuple[str, "ResolvedGateConfig", list[str]]]) -> list[str]:
    """Render the ``devbench gates`` table from resolved gate records.

    Column widths are computed from the actual header/cell content on every
    call (``str.ljust`` never truncates), so a future column addition needs
    no re-layout of this function.

    The ``tier`` column (spec G2 worked example; E2-F2-S1-T2) is looked up
    from ``constants.GATE_TIERS`` by gate name rather than threaded through
    *records* as a fourth tuple element -- the tier is a static fact about
    the gate name, not something ``resolve_gate_config`` resolves, so it
    needs no extra plumbing through the caller's row-collection loop.

    Args:
        records: ``(gate, resolved, override_repos)`` triples in row order --
            ``resolved`` is the ``ResolvedGateConfig`` returned by
            ``resolve_gate_config`` for that gate (only the ``enabled``
            field/provenance are rendered from it), and ``override_repos``
            is ``_gate_override_repos``'s result for that gate.

    Returns:
        Rendered lines: the header row followed by one row per record.
    """
    from devbench.constants import GATE_TIERS

    header = ("gate", "tier", "status", "repos", "provenance")
    rows = [
        (
            gate,
            GATE_TIERS[gate],
            "enabled" if resolved.values["enabled"] else "disabled",
            ", ".join(override_repos) if override_repos else "-",
            resolved.provenance["enabled"],
        )
        for gate, resolved, override_repos in records
    ]
    all_rows = [header, *rows]
    widths = [max(len(row[i]) for row in all_rows) for i in range(len(header))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) for row in all_rows]


def cmd_gates() -> int:
    """Render the read-only ``devbench gates`` overview table (spec G2, 4.1; AC-4, AC-27).

    Iterates the eight declared gates (``constants.GATE_NAMES``, in
    declaration order) and resolves each one's ``enabled`` status and
    provenance exclusively through ``config_loader.resolve_gate_config`` --
    the ONLY sanctioned read path for gate configuration (AC-27); this
    command never reads ``RuntimeConfig.gates`` fields directly. The
    rendered ``tier`` column (``machine-blocking`` or ``judge-evidence``) is
    looked up from ``constants.GATE_TIERS`` (spec 4.2, D-6), completing the
    G2 worked-example table shape. Total and read-only: renders all eight
    rows even when the workspace has no ``gates:`` key at all, since an
    absent block loads into the all-disabled built-in tree (D-17).

    Reloads the config file fresh from disk (mirrors ``cmd_check``) instead
    of trusting the process-wide ``RUNTIME_CONFIG`` singleton, so a config
    load failure is caught HERE with the loader's own clean, single-line
    message on stderr rather than letting the raw exception escape
    uncaught (spec Section 7: errors on stderr, no stack traces for
    expected failures).

    Returns:
        0 with the rendered table on stdout. 1 with the loader's own
        fail-fast message on stderr and nothing on stdout when the config
        file is missing or fails YAML/schema validation.
    """
    from devbench.config import resolve_gate_env_override
    from devbench.config_loader import load_runtime_config, resolve_config_path, resolve_gate_config
    from devbench.constants import GATE_NAMES as _GATE_ROW_ORDER

    cfg_path = resolve_config_path(None, os.environ, WORKSPACE_ROOT)
    try:
        runtime_config = load_runtime_config(cfg_path, os.environ)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    records: list[tuple[str, ResolvedGateConfig, list[str]]] = []
    for gate in _GATE_ROW_ORDER:
        override_repos = _gate_override_repos(gate, runtime_config)
        resolution_repo = _gate_resolution_repo(gate, override_repos, runtime_config)
        resolved = resolve_gate_config(gate, resolution_repo, runtime_config, resolve_gate_env_override(gate))
        records.append((gate, resolved, override_repos))

    for line in _format_gates_table(records):
        print(line)
    return 0


def _load_reachability_gate_config_or_report(canonical_repo: str) -> "ResolvedGateConfig | int":
    """Load config and resolve the reachability gate's config for *canonical_repo*.

    Extracted out of :func:`cmd_check_reachability` to keep that function's
    return/branch count within ruff's thresholds. Folds two of that
    function's early-exit branches (config load failure, disabled gate)
    into one caller-side check via the union return type: an ``int``
    result means "already handled -- return this exit code as-is" (the
    loader's own fail-fast ``ERROR:`` message, or the spec 5.2
    ``{"gate":"reachability","status":"disabled"}`` line, is already
    printed); a ``ResolvedGateConfig`` result means the gate is enabled and
    the caller should proceed.
    """
    from devbench.config import resolve_gate_env_override
    from devbench.config_loader import load_runtime_config, resolve_config_path, resolve_gate_config

    cfg_path = resolve_config_path(None, os.environ, WORKSPACE_ROOT)
    try:
        runtime_config = load_runtime_config(cfg_path, os.environ)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    gate_config = resolve_gate_config(
        _REACHABILITY_GATE_NAME,
        canonical_repo,
        runtime_config,
        resolve_gate_env_override(_REACHABILITY_GATE_NAME),
    )
    if not gate_config.values["enabled"]:
        print(json.dumps({"gate": _REACHABILITY_GATE_NAME, "status": _REACHABILITY_STATUS_DISABLED}))
        return 0
    return gate_config


def _reachability_waived_block(target: str, reason: str) -> str:
    """Render the ``[WAIVED]`` report block for a candidate an operator has waived (spec 4.9, Section 2 G7)."""
    return f"[WAIVED] {target} -- {reason}"


def _reachability_scan_candidates(
    repo_path: Path,
    candidates: list[str],
    entry_points: tuple[str, ...],
    canonical_repo: str,
    waived: Mapping[str, str],
) -> tuple[list[str], int, int, int] | None:
    """Scan *candidates* for reachability, rendering one report block per file.

    Extracted out of :func:`cmd_check_reachability` to keep that function's
    return/branch count within ruff's thresholds.

    A candidate named in *waived* (``{target: reason}``, already filtered by
    the caller -- :func:`_reachability_prepare_run` -- down to
    ``_REACHABILITY_WAIVER_REQUIRED_ATTRIBUTION``-attributed records from
    :func:`devbench.gate_records.gate_waiver_targets`; this function trusts
    that filtering and performs none of its own) is rendered as a
    ``[WAIVED] <target> -- <reason>`` block and never counted towards
    ``unreachable_count``/``load_error_count`` -- an operator waiver clears
    the finding regardless of what the underlying reachability search would
    otherwise have concluded (spec 4.9, Section 2 G7), so no reachability
    work is performed for a waived candidate at all. A target with only an
    executor-attributed waiver on file is NOT in *waived* (reachability is
    machine-blocking, spec Section 3.6/D-6) and is scanned normally below,
    exactly as if no waiver existed.

    Returns:
        ``(report_lines, unreachable_count, load_error_count, waived_count)``
        on success. ``None`` after printing the loud ``git grep`` failure
        message (spec Section 7) when :func:`_search_reachability_importers`
        raises ``RuntimeError`` for any candidate -- the caller exits 1 in
        that case with no further output.

    A referrer that cannot be read during the entry-point walk
    (:class:`_ReachabilityReferrerReadError`, raised by
    :func:`_is_reachable_from_entry_points`) is rendered as a counted
    ``[LOAD_ERROR]`` block naming the REFERRER that failed to read -- not
    folded into a false ``[OK]``/orphan-chain verdict for *rel_path* --
    matching the identical fail-loud contract already used for the
    top-level candidate read a few lines above.
    """
    report_lines: list[str] = []
    unreachable_count = 0
    load_error_count = 0
    waived_count = 0
    for rel_path in candidates:
        if rel_path in waived:
            waived_count += 1
            report_lines.append(_reachability_waived_block(rel_path, waived[rel_path]))
            continue

        abs_path = repo_path / rel_path
        try:
            content = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            load_error_count += 1
            report_lines.append(_reachability_load_error_block(rel_path, exc))
            continue

        symbols = _extract_reachability_symbols(content, rel_path)
        try:
            importers = _search_reachability_importers(repo_path, rel_path, symbols)
            reachable_importers = [
                importer for importer in importers if _is_reachable_from_entry_points(repo_path, importer, entry_points)
            ]
        except _ReachabilityReferrerReadError as exc:
            load_error_count += 1
            report_lines.append(_reachability_load_error_block(exc.rel_path, exc.cause))
            continue
        except RuntimeError as exc:
            print(f"ERROR: git grep failed: {exc}", file=sys.stderr)
            return None

        if not importers:
            unreachable_count += 1
            report_lines.append(_reachability_unreachable_block(rel_path, symbols, canonical_repo))
        elif reachable_importers:
            report_lines.append(_reachability_ok_block(rel_path, symbols, importers))
        else:
            unreachable_count += 1
            report_lines.append(_reachability_orphan_chain_block(rel_path, symbols, importers, canonical_repo))

    return report_lines, unreachable_count, load_error_count, waived_count


def _reachability_prepare_run(
    unit_id: str,
    repo_path: Path,
    unit: WorkUnit,
    gate_config: "ResolvedGateConfig",
) -> tuple[Path, tuple[str, ...], dict[str, str], ScopeResult] | int:
    """Resolve everything :func:`cmd_check_reachability` needs before it can scan a
    single candidate: the entry-point containment check, the waived-target
    mapping (spec 4.9, Section 2 G7) and the resolved Manifest scope.

    Extracted out of :func:`cmd_check_reachability` to keep that function's
    return/branch count within ruff's thresholds -- every early-exit branch
    below that used to live inline in the caller is now internal to this
    helper and no longer inflates the caller's own return-statement count.

    Returns:
        ``(wu_file, entry_points, waived, scope)`` on success, with *waived*
        already filtered to ``_REACHABILITY_WAIVER_REQUIRED_ATTRIBUTION``-
        attributed records only (spec Section 3.6/D-6: reachability is
        machine-blocking, so an executor cannot self-certify a waiver). An
        already fully-handled exit code (``1``; the failure has already
        printed its own ``ERROR:`` message, and -- per the fail-fast
        contract -- no status line) when the entry-point containment check,
        the waiver read or the scope resolution fails.
    """
    from devbench.config import DEFER_PR
    from devbench.gate_records import gate_waiver_targets

    entry_points = cast("tuple[str, ...]", gate_config.values["entry_points"])
    missing_entry_point = _reachability_missing_entry_point(
        repo_path, entry_points, gate_config.provenance["entry_points"]
    )
    if missing_entry_point is not None:
        print(
            f"ERROR: gates.reachability.entry_points names a path that is not present in the repo: "
            f"{missing_entry_point}",
            file=sys.stderr,
        )
        return 1

    # Waiver adoption (spec 4.9, Section 2 G7): resolved before scope so a
    # malformed `[GATE_WAIVER reachability]` marker fails loud before any
    # further work is done, and before any status line is printed (spec
    # Section 7 fail-fast rule). `gate_waiver_targets` is the sole reader for
    # this marker family (`devbench.gate_records`); a malformed marker is
    # never silently treated as "no waiver". Reachability is machine-blocking
    # (spec Section 3.6/D-6), so an executor-attributed record is read (never
    # silently dropped as "malformed") but excluded here from *waived* --
    # only an operator-attributed record can clear a candidate. Filtering
    # here, once, means every downstream consumer of *waived*
    # (`_reachability_scan_candidates`, the clean-run `[GATE_PASS]` write
    # below) sees only records that are actually allowed to clear a finding.
    wu_file = _resolve_work_unit_file(unit)
    try:
        wu_content = wu_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: Cannot read work unit file for {unit_id}: {exc}", file=sys.stderr)
        return 1
    try:
        waiver_records = gate_waiver_targets(wu_content, _REACHABILITY_GATE_NAME)
    except ValueError as exc:
        print(f"ERROR: malformed [GATE_WAIVER {_REACHABILITY_GATE_NAME}] marker in {unit_id}: {exc}", file=sys.stderr)
        return 1
    waived = {
        target: record.reason
        for target, record in waiver_records.items()
        if record.attribution == _REACHABILITY_WAIVER_REQUIRED_ATTRIBUTION
    }

    mode = MODE_DEFER_PR if DEFER_PR else MODE_PER_TASK_BRANCH
    scope = _resolve_scope_or_report(unit_id, repo_path, mode)
    if scope is None:
        return 1

    return wu_file, entry_points, waived, scope


def cmd_check_reachability(unit_id: str) -> int:
    """Reachability gate: does *unit_id*'s Changes Manifest carry an orphaned source file?

    Command: ``check-reachability <unit-id>`` (spec 4.4; machine-blocking
    per ``constants.GATE_TIERS``). Heuristic, language-agnostic evidence for
    the code-reviewer's ``UNREACHABLE_ARTIFACT`` check
    (caylent-solutions/devbench-internal-backlog#10): for every file in the
    unit's own Changes Manifest -- resolved through the single shared
    :func:`devbench.work_unit_scope.resolve_changed_files` (spec 4.3,
    AC-9), never a raw diff scan -- that :func:`_is_reachability_candidate`
    accepts AND that still exists on disk (a Manifest path a prior stage of
    this same unit deleted is not a candidate at all -- a deleted artifact
    cannot be an orphan), derives candidate exported-symbol names (file
    basename plus regex-extracted exports) and searches the rest of the
    target repo -- tracked and untracked, restricted to
    :func:`_reachability_search_pathspecs`'s source-classified pathspecs --
    for a word-boundary reference (register 315-D01/315-D02). A file
    outside the unit's Changes Manifest is never itself a *candidate*
    (candidates come solely from :func:`resolve_changed_files`), but such
    a file IS named as a referrer inside an ``[OK]``, orphan-chain or
    ``[LOAD_ERROR]`` finding (AC-FUNC-009 governs candidate selection
    only, not referrer naming).

    Transitive reachability (issue #10 AC2, spec 4.4 bullet 2): a referrer
    found by the search above clears the candidate only when the referrer
    is ITSELF reachable from the resolved ``gates.reachability.entry_points``
    set (:func:`_is_reachable_from_entry_points`), walked with a
    cycle-safe visited set. A candidate with a referrer, but where every
    referrer is unreachable, is reported ``[POTENTIALLY UNREACHABLE via
    orphan-chain]`` rather than ``[OK]``. ``entry_points`` is read
    exclusively through ``resolve_gate_config("reachability", repo)``
    (AC-FUNC-007); an explicit, project-configured entry point that does
    not exist in the repo checkout fails the whole run loudly before any
    candidate is examined (spec Section 7 fail-fast rule) rather than
    silently walking an empty graph.

    This is deliberately a heuristic, not a final verdict: a grep miss can
    be a false positive (dynamic ``import()``, barrel re-export the regex
    missed, lazy route split). The tool's job is only to surface candidates
    cheaply; the reviewing LLM makes the final call and can rule a
    candidate a false positive, and the operator can record a legitimate
    deferral with ``uv run devbench log-waiver <judge> <unit-id> --gate
    reachability --target <t> --reason <r> --operator`` (spec 4.9, PM-5).
    The old source-comment escape-hatch marker this command used to honour
    is gone (register finding 5, AC-FUNC-006): no path can clear an
    artifact without an audited waiver record.

    Prints the spec 5.2 gate status line as the FIRST stdout line:
    ``{"gate":"reachability","status":"disabled"}`` and exits 0 when the
    gate is disabled (or unconfigured) for the unit's repo (spec 4.1 final
    bullet, AC-4); otherwise
    ``{"gate":"reachability","tier":"machine-blocking","status":"pass"|"fail",
    "findings":<int>,"scope_hash":"<sha256>"}`` (AC-FUNC-008) followed by
    the human-readable findings. ``findings`` counts both
    ``[POTENTIALLY UNREACHABLE]`` orphans (including the orphan-chain
    shape) and ``[LOAD_ERROR]`` unreadable candidates -- a permission
    failure or a non-UTF-8 decode failure alike (spec 4.4 bullet 4,
    AC-FUNC-005) -- the old silent ``[SKIPPED]`` branch is gone.

    Returns:
        0 when the gate is disabled, or an enabled run finds zero findings.
        1 when the work unit or repo cannot be resolved, when the config
        file fails to load, when a configured (non-built-in)
        ``gates.reachability.entry_points`` path does not exist in the repo
        checkout, when scope resolution fails (no status line printed in
        that case), when ``git grep`` fails loudly (rc>=2, no status line
        printed), or when an enabled run has at least one finding.
    """
    from devbench.gate_records import compose_gate_pass_record

    resolved = _resolve_unit_repo_and_path(unit_id)
    if resolved is None:
        return 1
    unit, canonical_repo, repo_path = resolved

    gate_config = _load_reachability_gate_config_or_report(canonical_repo)
    if isinstance(gate_config, int):
        return gate_config

    prepared = _reachability_prepare_run(unit_id, repo_path, unit, gate_config)
    if isinstance(prepared, int):
        return prepared
    wu_file, entry_points, waived, scope = prepared

    # `scope.files` legitimately carries a Manifest path with no on-disk file
    # -- e.g. a file a prior stage of this same unit deleted, which the
    # complete-replacement standard mandates (see
    # `work_unit_scope._compute_files_scope_hash`). A path that does not
    # exist in the work tree is not a reachability candidate at all: a
    # deleted artifact cannot be an orphan, so it is filtered out here,
    # before any read is attempted, rather than surfaced as a `[LOAD_ERROR]`
    # finding. `[LOAD_ERROR]` stays reserved for a candidate that IS present
    # but cannot be read (permission or decode failure).
    candidates = [
        rel_path
        for rel_path in scope.files
        if _is_reachability_candidate(rel_path) and (repo_path / rel_path).is_file()
    ]

    if not candidates:
        report_lines = ["No classified source files found in this work unit's Changes Manifest."]
        unreachable_count = 0
        load_error_count = 0
    else:
        scanned = _reachability_scan_candidates(repo_path, candidates, entry_points, canonical_repo, waived)
        if scanned is None:
            return 1
        candidate_lines, unreachable_count, load_error_count, waived_count = scanned
        report_lines = [f"Candidate artifacts examined: {len(candidates)}", *candidate_lines]
        report_lines.append(
            f"Summary: {len(candidates)} candidate(s) examined, {unreachable_count} potentially "
            f"unreachable, {load_error_count} load error(s), {waived_count} waived."
        )

    total_findings = unreachable_count + load_error_count
    status = _REACHABILITY_STATUS_FAIL if total_findings else _REACHABILITY_STATUS_PASS

    if total_findings == 0 and scope.files:
        # Persisted machine record (spec 4.2, 4.4 final bullet): a clean
        # enabled run writes `[GATE_PASS reachability]` so `mark-done`'s
        # generic gate hook (`BacklogManager._check_gate_pass_done_invariant`)
        # can later require it. `compose_gate_pass_record` is the sole
        # authorized builder of the marker text (AC-E2-F2-S1-T1-6); no other
        # path in this command formats that text by hand. An empty Changes
        # Manifest (`scope.files` empty, `scope.scope_hash == ""`) has no
        # scope to persist a hash for, so no record is written for it --
        # mirroring `compute_scope_hash`'s own refusal to hash an empty
        # change set.
        if not wu_file.is_file():
            print(
                f"ERROR: Cannot write [GATE_PASS {_REACHABILITY_GATE_NAME}] record for {unit_id}: "
                f"work unit file not found at {wu_file}",
                file=sys.stderr,
            )
            return 1
        from devbench.backlog.manager import BacklogManager

        marker = compose_gate_pass_record(_REACHABILITY_GATE_NAME, scope.scope_hash)
        BacklogManager()._append_audit_marker_before_comments(wu_file, marker)

    print(
        json.dumps(
            {
                "gate": _REACHABILITY_GATE_NAME,
                "tier": GATE_TIERS[_REACHABILITY_GATE_NAME],
                "status": status,
                "findings": total_findings,
                "scope_hash": scope.scope_hash,
            }
        )
    )
    print(f"Reachability check for {unit_id} (repo: {canonical_repo})")
    for line in report_lines:
        print(line)
        print()

    return 1 if total_findings else 0


# Best-effort per-test failure extraction for the shared-file regression gate.
# Covers pytest's short summary line, `go test`'s `--- FAIL:` line, and the
# jest/mocha-style spec-runner glyph. This is intentionally NOT a general
# solution for every test runner devbench's target repos might use -- see
# `_parse_failing_tests` docstring for the documented fallback behaviour.
_FAILING_TEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^FAILED\s+(\S+)", re.MULTILINE),
    re.compile(r"^---\s+FAIL:\s+(\S+)", re.MULTILINE),
    re.compile(r"^\s*(?:✕|✗)\s+(.+?)\s*$", re.MULTILINE),
)

_SHARED_FILE_BASELINE_DEGRADED_MARKER = "<suite-failed-no-per-test-detail-parsed>"


def _matched_shared_files(changed_files: list[str], patterns: tuple[str, ...]) -> list[str]:
    """Return the subset of *changed_files* matching any glob in *patterns*.

    Patterns are ``fnmatch``-style and matched against POSIX-relative paths
    (the same shape :func:`devbench.backlog.manifest.list_changed_files`
    returns). Sorted + de-duplicated for stable output.
    """
    return sorted({f for f in changed_files for pattern in patterns if fnmatch.fnmatch(f, pattern)})


def _parse_failing_tests(output: str, rc: int) -> tuple[set[str], bool]:
    """Best-effort extraction of individual failing-test identifiers from *output*.

    Returns ``(failing_tests, degraded)``. ``degraded`` is ``True`` when the
    suite failed (``rc != 0``) but none of the recognised formats (pytest,
    ``go test``, jest/mocha-style) matched anything -- in that case
    ``failing_tests`` is a single synthetic marker so the baseline-diff
    logic still has something to compare, but callers surface ``degraded``
    so it is visible that per-test attribution could not be computed for
    this repo's test runner rather than silently treating it as precise.
    """
    found: set[str] = set()
    for pattern in _FAILING_TEST_PATTERNS:
        found.update(match.group(1).strip() for match in pattern.finditer(output))
    if rc == 0 or found:
        return found, False
    return {_SHARED_FILE_BASELINE_DEGRADED_MARKER}, True


def _shared_file_baseline_path(canonical_repo: str) -> Path:
    """Return the per-repo baseline file path under the workspace's ``.devbench`` state dir."""
    safe_name = canonical_repo.replace("/", "__")
    return WORKSPACE_ROOT / ".devbench" / "test-baselines" / f"{safe_name}.json"


def _load_shared_file_baseline(path: Path) -> dict[str, Any] | None:
    """Return the parsed baseline JSON at *path*, or ``None`` when absent/unreadable.

    A corrupt or unreadable baseline is treated the same as "no baseline yet"
    (bootstrap path) rather than raising -- a hand-edited or partially
    written baseline file must never be able to turn into a hard crash that
    blocks every subsequent task touching a shared file.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_shared_file_baseline(path: Path, *, canonical_repo: str, failing_tests: set[str], unit_id: str) -> None:
    """Persist *failing_tests* as the new baseline for *canonical_repo*, attributed to *unit_id*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo": canonical_repo,
        "failing_tests": sorted(failing_tests),
        "updated_by_unit": unit_id,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _evaluate_shared_file_gate(
    *, unit_id: str, canonical_repo: str, repo_path: Path, matched_files: list[str]
) -> tuple[dict[str, Any], int]:
    """Run the full suite and baseline-diff for a matched shared-file impact.

    Isolates the "a shared file WAS touched" branch of
    :func:`cmd_check_shared_file_impact` into its own function purely to
    keep the caller's cyclomatic/return-statement complexity low; the
    bootstrap / block / pass decision described in that function's
    docstring lives here. Returns ``(json_payload, exit_code)``.
    """
    from devbench.config import TEST_TIMEOUT

    cmd = _select_test_command(repo_path)
    rc, stdout, stderr = run_command(cmd, cwd=repo_path, timeout=TEST_TIMEOUT)
    combined_output = "\n".join(part for part in (stdout, stderr) if part.strip())
    current_failing, degraded = _parse_failing_tests(combined_output, rc)

    baseline_path = _shared_file_baseline_path(canonical_repo)
    baseline = _load_shared_file_baseline(baseline_path)

    base_payload: dict[str, Any] = {
        "unit_id": unit_id,
        "repo": canonical_repo,
        "shared_file_impact": True,
        "matched_files": matched_files,
        "full_suite_command": cmd,
        "full_suite_exit_code": rc,
        "degraded": degraded,
        "baseline_path": str(baseline_path),
    }

    if baseline is None:
        _write_shared_file_baseline(
            baseline_path, canonical_repo=canonical_repo, failing_tests=current_failing, unit_id=unit_id
        )
        payload = {
            **base_payload,
            "verdict": "bootstrap",
            "failing_tests": sorted(current_failing),
            "note": (
                "No prior baseline existed for this repo; the current failing-test set has "
                "been recorded as the baseline. This run cannot distinguish pre-existing "
                "failures from ones this task introduced -- the next task that touches a "
                "shared file will be checked against this baseline."
            ),
        }
        return payload, 0

    baseline_failing: set[str] = set(baseline.get("failing_tests") or [])
    new_failures = sorted(current_failing - baseline_failing)

    if new_failures:
        payload = {
            **base_payload,
            "verdict": "block",
            "new_failures": new_failures,
            "pre_existing_failures": sorted(current_failing & baseline_failing),
        }
        return payload, 1

    _write_shared_file_baseline(
        baseline_path, canonical_repo=canonical_repo, failing_tests=current_failing, unit_id=unit_id
    )
    payload = {**base_payload, "verdict": "pass", "failing_tests": sorted(current_failing)}
    return payload, 0


def cmd_check_shared_file_impact(unit_id: str) -> int:
    """Gate a work unit's diff against the shared-file full-suite regression policy.

    A task's regression verification (`run-tests`) is not scoped by the
    Changes Manifest at the tooling level -- it always runs the full suite
    command (`_select_test_command`) -- but nothing forces an executor to
    actually invoke a full run, or to notice that a shared/high-fan-in file
    (an app shell, a shared hook, a widely-consumed component) was touched.
    A task can pass its own narrow verification while silently breaking
    hundreds of already-passing tests elsewhere, discovered only when some
    later, unrelated task happens to run the full suite. This command closes
    that gap for repos with `gates.repos.<repo>.shared_file_impact.patterns`
    configured in devbench.yaml:

    1. Computes the work unit's changed-file set via
       `list_changed_files` (staged + unstaged + untracked, relative to the
       repo root -- the same read-only query the claim-scope guard uses).
    2. Cross-references it against the repo's `shared_file_impact.patterns`
       glob list. No match: no-op (exit 0, `shared_file_impact: false`); the
       task's normal `run-tests` evidence stands.
    3. On a match (`_evaluate_shared_file_gate`): runs the full-suite
       command, parses individual failing-test identifiers out of the
       output (`_parse_failing_tests`), and diffs them against a stored
       baseline at `<workspace>/.devbench/test-baselines/<repo>.json`.
       Blocks (exit 1) only on tests failing now that were NOT in the
       baseline -- so this does not stall on pre-existing/flaky failures.
       The blocking output names the offending tests AND `unit_id`,
       attributing the regression to the task that introduced it.
    4. On a pass (no new failures), the baseline is refreshed to the
       current failing set -- a ratchet, so a task that fixes a pre-existing
       failure isn't later blamed by an unrelated task for "un-fixing" it.
    5. No prior baseline: bootstraps (records current failures as the
       baseline, exit 0) since there is nothing yet to compare against.

    Known limitation (v1, documented rather than hidden): this is a
    hand-maintained glob registry, not an auto-derived import/mount-graph
    of actual fan-in (`gates.shared_file_impact.auto_derive_registry` is
    the reserved config surface for the eventual auto-derivation successor
    -- see `docs/devbench-yaml-reference.md` for the tradeoff). Per-test
    failure attribution is parsed from common textual formats (pytest,
    `go test`, jest/mocha-style); other runners still get suite-level
    bootstrap/ratchet behaviour but degrade to a single synthetic marker
    instead of per-test identifiers, surfaced via the `degraded` field in
    the JSON output.
    """
    from devbench.backlog.manifest import list_changed_files

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    gate_repo_override = RUNTIME_CONFIG.gates.repos.get(canonical_repo)
    shared_file_impact_override = gate_repo_override.shared_file_impact if gate_repo_override is not None else None
    patterns = shared_file_impact_override.patterns if shared_file_impact_override is not None else ()

    try:
        changed_files = list_changed_files(repo_path)
    except RuntimeError as exc:
        print(f"ERROR: could not compute changed files for '{canonical_repo}': {exc}", file=sys.stderr)
        return 1

    matched_files = _matched_shared_files(changed_files, patterns) if patterns else []
    if not matched_files:
        payload: dict[str, Any] = {
            "unit_id": unit_id,
            "repo": canonical_repo,
            "shared_file_impact": False,
            "changed_files": changed_files,
        }
        if not patterns:
            payload["reason"] = "no gates.repos.<repo>.shared_file_impact.patterns configured for this repo"
        print(json.dumps(payload, indent=2))
        return 0

    result_payload, rc = _evaluate_shared_file_gate(
        unit_id=unit_id, canonical_repo=canonical_repo, repo_path=repo_path, matched_files=matched_files
    )
    print(json.dumps(result_payload, indent=2))
    if rc != 0:
        print(
            f"ERROR: {unit_id} touches shared file(s) {matched_files} and introduces "
            f"{len(result_payload.get('new_failures', []))} new full-suite failure(s) not present "
            f"in the baseline: {result_payload.get('new_failures')}. "
            "Fix these before this task can be marked done.",
            file=sys.stderr,
        )
    return rc


def cmd_check_fixture_consistency(unit_id: str) -> int:
    """Cross-reference the work unit's target repo's mock/fixture files against its canonical dataset.

    caylent-solutions/devbench-internal-backlog#17 (fixture-catalog cross-reference lint): a
    feature's data-fetch logic is frequently correct but reads from a mock/fixture lookup table
    whose keys were fabricated, keyed in the wrong namespace, or left
    incomplete relative to the project's canonical shared fixture dataset --
    functionally dead or crash-on-save for real records even though the
    underlying logic is sound.

    Opt-in and project-specific: devbench cannot infer a target repo's
    fixture-file layout, so this is a deliberate no-op (prints a note,
    exits 0) unless the workspace configures
    ``gates.fixture_consistency.canonical_sources`` in
    ``backlog/config/devbench.yaml``. When configured, scans every
    ``gates.fixture_consistency.scan`` target for identifier literals
    absent from its designated canonical source, and checks each canonical
    source's distinct-identifier count against an optional
    ``expected_count`` (backfill coverage).

    Used as review evidence by the test-reviewer agent.
    """
    from devbench.fixture_consistency import check_fixture_consistency

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    fixture_config = RUNTIME_CONFIG.gates.fixture_consistency
    if not fixture_config.canonical_sources:
        print(
            "(fixture-consistency check skipped: no gates.fixture_consistency.canonical_sources "
            "configured in backlog/config/devbench.yaml)"
        )
        return 0

    findings = check_fixture_consistency(Path(repo_path), fixture_config)
    if not findings:
        sources = ", ".join(source.path for source in fixture_config.canonical_sources)
        print(f"OK: fixture-catalog cross-reference check passed against canonical source(s): {sources}")
        return 0

    print("FAIL: fixture-catalog cross-reference check found issue(s):")
    for finding in findings:
        print(f"  [{finding.kind}] {finding.message}")
    return 1


def _reject_em_dash(field_name: str, text: str) -> int | None:
    """Reject any agent-supplied text containing U+2014 before it reaches the backlog.

    The validate-backlog Check 10 rejects work-unit files containing em-dash,
    so any CLI writer that accepts free-form agent text must reject em-dash at
    the input boundary -- otherwise LLM-written verdict feedback (which naturally
    uses em-dashes) poisons the file and blocks the next validate-backlog run.

    Returns:
        ``1`` (non-zero exit code) with stderr populated when em-dash is found;
        ``None`` when the text is clean. Callers return the int on non-None.
    """
    if EM_DASH in text:
        print(
            f"ERROR: {field_name} contains em-dash character (U+2014); use '--' (double hyphen) instead.",
            file=sys.stderr,
        )
        return 1
    return None


def _reject_control_characters(field_name: str, text: str) -> int | None:
    """Reject any ASCII control character (including newlines) in agent-supplied text.

    A newline in an agent-controlled free-text field can inject an
    attacker-chosen extra bulleted line into the work-unit markdown file --
    including a forged ``- [RED_OBSERVED] ...`` entry that would otherwise
    resemble a legitimate structural line (HIGH finding, E4-F3-S1-T1 security
    review). Rejecting every control character at the input boundary (not
    just newline) closes the same injection class via carriage return, tab,
    or other C0 control codes.

    Returns:
        ``1`` (non-zero exit code) with stderr populated when a control
        character is found; ``None`` when the text is clean.
    """
    if any(ord(ch) < 0x20 for ch in text):
        print(
            f"ERROR: {field_name} contains a control character (e.g. a newline); "
            "agent-supplied text must be a single line with no control characters.",
            file=sys.stderr,
        )
        return 1
    return None


# Matches a bracketed TDD phase tag anywhere in a string -- used to stop an
# agent from embedding a structural-looking ``[RED_OBSERVED]``/``[RED]``/etc.
# tag inside a message body (HIGH finding, E4-F3-S1-T1 security review).
_BRACKETED_TDD_PHASE_TAG_RE = re.compile(
    r"\[(?:" + "|".join(re.escape(phase) for phase in sorted(VALID_TDD_PHASES)) + r")\]"
)


def _reject_bracketed_phase_tag(field_name: str, text: str) -> int | None:
    """Reject agent-supplied text embedding a bracketed TDD phase tag.

    Prevents an agent from writing a message such as
    ``"observed failure [RED_OBSERVED] exit_code=1"`` to a legitimate
    ``RED`` entry -- the tag has no structural meaning inside a message body,
    but leaving it unrejected would let free text visually mimic a
    RED_OBSERVED entry line (HIGH finding, E4-F3-S1-T1 security review).

    Returns:
        ``1`` (non-zero exit code) with stderr populated when a bracketed
        phase tag is found; ``None`` when the text is clean.
    """
    if _BRACKETED_TDD_PHASE_TAG_RE.search(text):
        print(
            f"ERROR: {field_name} contains a bracketed phase tag (e.g. '[RED_OBSERVED]'); "
            "agent-supplied text cannot embed TDD phase markers.",
            file=sys.stderr,
        )
        return 1
    return None


def _validate_agent_free_text(field_name: str, text: str) -> int | None:
    """Validate an agent-supplied free-text field before it reaches the backlog.

    Composes, in order: em-dash rejection (validate-backlog Check 10),
    control-character rejection (blocks newline-injection forgery of a
    structural line), and bracketed-TDD-phase-tag rejection (blocks a
    message body visually mimicking a phase-tagged entry). This is the
    single validation point shared by the three agent-facing verbs whose
    free-text field feeds directly into a structured, judge-consulted
    record: ``cmd_log_tdd``, ``cmd_log_comment`` and ``cmd_log_verdict``
    (DRY; HIGH finding, E4-F3-S1-T1 security review: "validate ALL agent
    free-text fields, not just log-tdd's"). Other reason-bearing verbs
    (``cmd_decline``, ``_parse_id_and_reason`` feeding the hold/block
    verbs, ``cmd_reject_amendment``, ``cmd_add_dep``, ``cmd_reject_proposal``)
    are NOT wired into this function and still apply em-dash rejection
    only via ``_reject_em_dash``; widening their input boundary is tracked
    as separate follow-up work, not implied by this docstring.

    Returns:
        The first non-``None`` rejection code, or ``None`` when the text
        passes every check.
    """
    return (
        _reject_em_dash(field_name, text)
        or _reject_control_characters(field_name, text)
        or _reject_bracketed_phase_tag(field_name, text)
    )


def _enforce_judge_retry_budget(judge_name: str, unit_id: str, wu_file: Path, content: str) -> bool:
    """Block *unit_id* when *judge_name* has spent its retry budget. Return whether it blocked.

    Called only for canonical review judges after a ``REVIEW_FAIL`` row has
    been written, so the row just appended is included in the count and the
    audit trail stays the single source of truth.

    On exhaustion this appends the ``[BLOCKED]``
    ``[RETRY_BUDGET_EXHAUSTED]`` audit row (the verbatim tag
    ``backlog.proposal`` matches to classify the unit
    ``OPERATOR_ACTION_REQUIRED``), forces the unit to ``blocked``, and
    notifies the operator -- the same escalation shape
    ``_handle_ci_failure`` uses when the CI retry budget is spent.

    Args:
        judge_name: Canonical judge that just failed the unit.
        unit_id: The work-unit identifier.
        wu_file: Path to the work-unit ``.md`` file.
        content: The work-unit text INCLUDING the verdict row just written.

    Returns:
        ``True`` when the budget was exhausted and the unit was blocked;
        ``False`` when rounds remain and nothing was changed.
    """
    budget = resolve_judge_retry_budget(judge_name)
    fails = count_review_fails_for_judge(content, judge_name)
    if fails < budget:
        return False

    mgr = BacklogManager()
    reason = (
        f"{judge_name} rejected this unit {fails} time(s), spending its executor retry budget "
        f"of {budget}; no further executor round is coming and an operator must review"
    )
    mgr._append_agent_comment(
        wu_file,
        "orchestrator",
        f"[BLOCKED] {ORCHESTRATOR_RETRY_BUDGET_EXHAUSTED_AUDIT_TAG} {reason}",
    )
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_BLOCKED)
    logger.info(
        "log-verdict: %s exhausted its retry budget (%d of %d) for %s; unit set to '%s'",
        judge_name,
        fails,
        budget,
        unit_id,
        STATUS_BLOCKED,
    )
    from devbench.notifications import notify_work_unit_blocked_operator

    notify_work_unit_blocked_operator(unit_id, _extract_wu_title(wu_file, unit_id), reason)
    return True


def cmd_log_verdict(judge_name: str, unit_id: str, verdict: str, feedback: str = "") -> int:
    """Append a judge verdict to the work unit's Comments section and log feedback.

    Arguments:
        judge_name:  Judge identifier, e.g. ``code_review`` (matches REVIEW_JUDGE_NAMES).
        unit_id:     Work unit ID, e.g. ``E0-F1-S1-T1``.
        verdict:     ``pass`` or ``fail``.
        feedback:    One-line summary of the verdict (required for ``fail``). Validated by
                     ``_validate_agent_free_text``: must contain no em-dash, no control
                     character (including newline), and no bracketed TDD phase tag such as
                     ``[RED_OBSERVED]``; a violation exits 1 and writes nothing (HIGH
                     finding, E4-F3-S1-T1 security review).

    The entry written to the work unit uses the same format as the orchestrator:
    ``[judge/<name>] [REVIEW_PASS|REVIEW_FAIL] <feedback>``

    Feedback is also written to the orchestrator log for audit purposes.

    Issue #122: a ``REVIEW_FAIL`` from one of the canonical
    ``ALL_REQUIRED_JUDGE_NAMES`` also enforces that judge's executor retry
    budget. When the budget is spent, this command appends the
    ``[RETRY_BUDGET_EXHAUSTED]`` audit row, forces the unit to ``blocked``,
    and notifies the operator -- see ``_enforce_judge_retry_budget``. The
    emitted JSON carries ``retry_budget_exhausted`` so the caller can tell a
    bounded rejection from a terminal one. Enforcement lives here because
    this is the single choke point every judge verdict passes through; the
    previous contract lived only in orchestrate SKILL.md prose and was
    unenforceable.
    """
    verdict_lower = verdict.strip().lower()
    if verdict_lower not in ("pass", "fail"):
        print(f"ERROR: verdict must be 'pass' or 'fail', got '{verdict}'", file=sys.stderr)
        return 1

    # Judge-name allowlist (single source of truth in
    # ``devbench.constants.KNOWN_JUDGE_NAMES``). The set is intentionally
    # broader than ``ALL_REQUIRED_JUDGE_NAMES``: only the canonical 5
    # reviewer names satisfy the done-gate's
    # ``_last_round_all_passed`` check, but workflow agents
    # (``task_factory``, ``blocker_resolver``, ``manifest_amender``,
    # ``executor``) legitimately write audit-only verdicts that are
    # visible in the Comments section but do not count toward the gate.
    # Refusing typos here prevents the malformed
    # ``log-verdict <agent> <id> pass <message>`` shape from landing in
    # the audit trail with a junk judge field that confuses reviewers
    # and the done-gate's bookkeeping.
    judge_clean = judge_name.strip()
    if judge_clean not in KNOWN_JUDGE_NAMES:
        valid = ", ".join(sorted(KNOWN_JUDGE_NAMES))
        canonical_list = ", ".join(sorted(ALL_REQUIRED_JUDGE_NAMES))
        print(
            f"ERROR: judge name {judge_name!r} is not on the allowlist; "
            f"valid choices are: {valid}. Use the underscored form "
            "(e.g. 'code_review', not 'code-reviewer'). Only the 5 "
            f"canonical reviewers satisfy the done-gate ({canonical_list}); "
            "the remaining names write audit-only verdicts. Non-canonical "
            "agents writing free-form narration must use 'log-comment'.",
            file=sys.stderr,
        )
        return 1

    rc = _validate_agent_free_text("feedback", feedback)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_work_unit_file(unit)

    action = "REVIEW_PASS" if verdict_lower == "pass" else "REVIEW_FAIL"
    agent_id = f"judge/{judge_name}"
    timestamp = comment_timestamp()
    entry = COMMENT_ENTRY_TEMPLATE.format(
        timestamp=timestamp,
        agent_id=agent_id,
        action=action,
        message=feedback,
    )

    content = wu_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    atomic_write_text(wu_file, content)

    # Log feedback for audit trail.
    if feedback:
        logger.info("%s judge feedback for %s: %s", judge_name, unit_id, feedback)

    # Issue #122: bound the review-rejection loop. Only the canonical
    # reviewers charge the budget -- workflow agents on the broader
    # ``KNOWN_JUDGE_NAMES`` allowlist (``executor``, ``task_factory``,
    # ``blocker_resolver``, ``manifest_amender``) write audit-only verdicts
    # that must not count against a review budget they do not own.
    budget_exhausted = False
    if action == "REVIEW_FAIL" and judge_clean in ALL_REQUIRED_JUDGE_NAMES:
        budget_exhausted = _enforce_judge_retry_budget(judge_clean, unit_id, wu_file, content)

    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "judge": judge_name,
                "verdict": verdict_lower,
                "retry_budget_exhausted": budget_exhausted,
            }
        )
    )
    return 0


def cmd_log_comment(agent_name: str, unit_id: str, message: str) -> int:
    """Append a non-verdict agent comment to the work unit's Comments section.

    Writes: ``[YYYY-MM-DD HH:MM ZONE] [agent/<name>] <message>``, where ZONE
    is the workspace's ``display_timezone`` abbreviation, or ``UTC`` when unset.

    Use for non-judge actors (executor, blocker-resolver, review-supervisor summary)
    that need to log progress without emitting a REVIEW_PASS/REVIEW_FAIL token.

    Arguments:
        message: Validated by ``_validate_agent_free_text``: must contain no
            em-dash, no control character (including newline), and no
            bracketed TDD phase tag such as ``[RED_OBSERVED]``; a violation
            exits 1 and writes nothing (HIGH finding, E4-F3-S1-T1 security
            review).
    """
    rc = _validate_agent_free_text("message", message)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_work_unit_file(unit)

    timestamp = comment_timestamp()
    entry = COMMENT_AGENT_TEMPLATE.format(
        timestamp=timestamp,
        name=agent_name,
        message=message,
    )

    content = wu_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    atomic_write_text(wu_file, content)

    logger.info("agent/%s comment for %s: %s", agent_name, unit_id, message)
    print(json.dumps({"unit_id": unit_id, "agent": agent_name}))
    return 0


def cmd_log_tdd(unit_id: str, phase: str, message: str) -> int:
    """Append a TDD phase entry to the work unit's TDD Cycle Log section.

    Writes: ``- [<PHASE>] <ISO-8601 timestamp> -- <message>``

    Arguments:
        unit_id:  Work unit ID, e.g. ``E0-F1-S1-T1``.
        phase:    TDD phase, one of ``RED``, ``GREEN``, ``REFACTOR`` (case-insensitive).
                  ``RED_OBSERVED`` is a valid phase overall but is
                  orchestrator-only (see ``write_red_observed_entry``); this
                  agent-facing command always rejects it.
        message:  Description of the TDD phase outcome. Validated by
                  ``_validate_agent_free_text``: must contain no em-dash, no
                  control character (including newline), and no bracketed
                  TDD phase tag such as ``[RED_OBSERVED]``; a violation
                  exits 1 and writes nothing.

    Exits 0 on success, non-zero on any error.  The ``## TDD Cycle Log``
    section must already exist in the work unit file; this command fails fast
    if the section is absent rather than silently creating it.
    """
    phase_upper = phase.upper()
    if phase_upper not in VALID_TDD_PHASES:
        print(
            f"ERROR: Invalid TDD phase '{phase}'. Valid phases: {', '.join(sorted(VALID_TDD_PHASES))}",
            file=sys.stderr,
        )
        return 1

    if phase_upper in ORCHESTRATOR_ONLY_TDD_PHASES:
        print(
            TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE.format(
                phase=phase_upper,
                agent_phases=", ".join(sorted(AGENT_WRITABLE_TDD_PHASES)),
            ),
            file=sys.stderr,
        )
        return 1

    rc = _validate_agent_free_text("message", message)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_work_unit_file(unit)

    mgr = BacklogManager()
    try:
        mgr._append_tdd_entry(wu_file, phase_upper, message)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    logger.info("TDD %s entry logged for %s", phase_upper, unit_id)
    print(json.dumps({"unit_id": unit_id, "phase": phase_upper}))
    return 0


def _resolve_work_unit_file(unit: WorkUnit) -> Path:
    """Resolve the work-unit markdown file path for the log-* CLI commands.

    Tries ``BACKLOG_ROOT / unit.file_path`` first, then falls back to
    ``WORKSPACE_ROOT / unit.file_path`` when the first candidate does not
    exist. Shared by ``cmd_log_verdict``, ``cmd_log_comment``, ``cmd_log_tdd``,
    and ``write_red_observed_entry`` so the two-location resolution logic has
    one definition instead of being duplicated per command (DRY).

    Args:
        unit: WorkUnit whose file_path must be resolved.

    Returns:
        The resolved :class:`pathlib.Path`. Tries the ``BACKLOG_ROOT``
        candidate first and falls back to the ``WORKSPACE_ROOT`` candidate
        when the first does not exist; neither candidate's existence is
        re-verified after this fallback, so the returned path may still not
        exist. Callers that read or write the file must surface a clear
        error on a missing path themselves.
    """
    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path
    return wu_file


def _gate_verb_usage_error(message: str) -> int:
    """Print ``ERROR: <message>`` to stderr and return exit code 2.

    The exit-2 usage-error shape spec `integration-reality-gates-hardening.md`
    section 4.9 defines for the structured gate-marker verbs (``log-waiver``;
    mirrored by ``log-newly-reachable``, E2-F4-S1-T2: "log-newly-reachable
    mirrors these semantics for its fields"): every usage failure -- an
    unknown judge/gate name, an empty required field, or a machine-blocking
    gate waived without ``--operator`` -- exits 2 naming the offending
    argument. Centralising the shape here means the two verbs' usage errors
    can never drift (their argument sets differ, but the exit code and
    stderr shape must not).

    Args:
        message: Already names the offending argument, e.g. ``"--reason is
            required and must be non-empty"``.

    Returns:
        ``2``.
    """
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


def _consume_gate_verb_flag_value(args: list[str], i: int, flag: str) -> tuple[str, int] | int:
    """Return ``(value, next_index)`` for *flag*'s value at ``args[i + 1]``.

    Shared by every flag-scanning function behind a structured gate-marker
    verb -- :func:`_scan_log_waiver_flags` (``--gate``/``--target``/``--reason``)
    and :func:`_scan_log_newly_reachable_flags` (``--path``/``--method``/``--result``)
    -- so the "flag requires a value" usage error has exactly one definition
    across both verbs instead of being duplicated per verb (E2-F4-S1-T2
    REFACTOR: was ``_consume_log_waiver_flag_value``, generalised once
    ``log-newly-reachable`` needed the identical behaviour).

    Returns:
        ``(value, i + 2)`` on success, or the ``2`` usage-error exit code
        (already printed to stderr via :func:`_gate_verb_usage_error`) when
        *flag* has no following token or the following token is empty.
    """
    if i + 1 >= len(args) or not args[i + 1]:
        return _gate_verb_usage_error(f"{flag} requires a value")
    return args[i + 1], i + 2


def _scan_log_waiver_flags(argv: tuple[str, ...]) -> tuple[list[str], str, str, str, bool] | int:
    """Scan ``log-waiver``'s argv into positionals plus its four flags.

    Returns:
        ``(positional, gate, target, reason, operator)`` on success (empty
        string for any flag not supplied). On a missing flag value, returns
        the ``2`` usage-error exit code already printed to stderr.
    """
    positional: list[str] = []
    gate = ""
    target = ""
    reason = ""
    operator = False
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg in ("--gate", "--target", "--reason"):
            consumed = _consume_gate_verb_flag_value(args, i, arg)
            if isinstance(consumed, int):
                return consumed
            value, i = consumed
            if arg == "--gate":
                gate = value
            elif arg == "--target":
                target = value
            else:
                reason = value
            continue
        if arg == "--operator":
            operator = True
            i += 1
            continue
        positional.append(arg)
        i += 1
    return positional, gate, target, reason, operator


def _parse_log_waiver_argv(
    argv: tuple[str, ...],
) -> tuple[str, str, str, str, str, bool] | int:
    """Parse ``log-waiver <judge> <unit-id> --gate <g> --target <t> --reason <r> [--operator]``.

    Returns:
        ``(judge, unit_id, gate, target, reason, operator)`` on success.
        On a usage error (missing positional, missing/empty flag value),
        prints an ``ERROR: ...`` naming the offending argument via
        :func:`_gate_verb_usage_error` and returns ``2`` for the caller to
        return directly.
    """
    scanned = _scan_log_waiver_flags(argv)
    if isinstance(scanned, int):
        return scanned
    positional, gate, target, reason, operator = scanned

    if len(positional) < 2:
        return _gate_verb_usage_error(
            "log-waiver requires <judge> <unit-id> --gate <g> --target <t> --reason <r> [--operator]"
        )
    judge, unit_id = positional[0], positional[1]

    if not gate:
        return _gate_verb_usage_error("--gate is required")
    if not target or not target.strip():
        return _gate_verb_usage_error("--target is required and must be non-empty")
    if not reason or not reason.strip():
        return _gate_verb_usage_error("--reason is required and must be non-empty")

    return judge, unit_id, gate, target, reason, operator


def _validate_log_waiver_semantics(judge: str, gate: str, operator: bool) -> int | None:
    """Validate ``<judge>``/``--gate`` against their vocabularies and the machine-blocking/``--operator`` rule.

    Args:
        judge: The ``<judge>`` positional argument.
        gate: The ``--gate`` flag value.
        operator: Whether ``--operator`` was supplied.

    Returns:
        The ``2`` usage-error exit code (already printed to stderr) on the
        first violation, or ``None`` when the combination is valid.
    """
    if judge not in ALL_REQUIRED_JUDGE_NAMES:
        valid = ", ".join(sorted(ALL_REQUIRED_JUDGE_NAMES))
        return _gate_verb_usage_error(f"unknown judge {judge!r}; valid choices are: {valid}.")

    if gate not in GATE_TIERS:
        valid = ", ".join(sorted(GATE_TIERS))
        return _gate_verb_usage_error(f"--gate names an unknown gate {gate!r}; declared gates are: {valid}.")

    if GATE_TIERS[gate] == GATE_TIER_MACHINE_BLOCKING and not operator:
        return _gate_verb_usage_error(
            f"--operator is required to waive machine-blocking gate {gate!r} "
            "(spec Section 3.6: the operator is the only waiver authority for a machine-blocking gate)."
        )

    return None


def cmd_log_waiver(*argv: str) -> int:
    """Record a structured ``[GATE_WAIVER <gate>]`` waiver marker (spec 4.9, 5.3).

    Usage::

        log-waiver <judge> <unit-id> --gate <g> --target <t> --reason <r> [--operator]

    Writes ``[GATE_WAIVER <gate>] <iso-utc> <target> <operator|executor>
    <reason>`` (spec 5.3 field order, composed by
    ``devbench.backlog.manager.compose_gate_waiver_record`` -- the sole
    authorized builder, mirroring ``devbench.gate_records.compose_gate_pass_record``'s
    role for ``[GATE_PASS]``) into the unit's ``## TDD Cycle Log`` section
    (via ``BacklogManager._append_audit_marker_before_comments``), the audit
    surface that survives every review judge's ``read-unit --strip-comments``
    Evidence fetch (PM-6 evidence-horizon rule, E2-F3-S1-T2). ``## Comments``
    itself is stripped by that fetch and would make the marker invisible to
    the very judges spec 3.6 says must weigh it.

    Trust model (spec Section 3.6): the operator is the only waiver
    authority for a machine-blocking gate
    (``constants.GATE_TIER_MACHINE_BLOCKING``); a machine-blocking gate
    waived without ``--operator`` is a usage error. A judge-evidence gate
    accepts either attribution.

    Args:
        argv: ``<judge> <unit-id> --gate <g> --target <t> --reason <r>
            [--operator]``. ``<judge>`` must be one of the five canonical
            review judges (``constants.ALL_REQUIRED_JUDGE_NAMES`` -- the
            same vocabulary ``log-verdict`` validates against, per spec
            4.9's "single source of truth" requirement). ``--reason`` is
            validated by ``_validate_agent_free_text`` (em-dash, control
            characters, bracketed TDD-phase tags all rejected).

    Returns:
        ``0`` on success (the marker was written; stdout carries a JSON
        summary). ``1`` when the unit does not exist, or when ``--reason``
        fails the free-text boundary validation. ``2`` (usage error, naming
        the offending argument) when ``<judge>`` or ``--gate`` names an
        unknown value, a required field is missing or empty, or a
        machine-blocking gate is waived without ``--operator``.
    """
    parsed = _parse_log_waiver_argv(argv)
    if isinstance(parsed, int):
        return parsed
    judge, unit_id, gate, target, reason, operator = parsed

    rc = _validate_log_waiver_semantics(judge, gate, operator)
    if rc is not None:
        return rc

    rc = _validate_agent_free_text("reason", reason)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_work_unit_file(unit)
    attribution = GATE_WAIVER_ATTRIBUTION_OPERATOR if operator else GATE_WAIVER_ATTRIBUTION_EXECUTOR
    marker = compose_gate_waiver_record(gate, target, attribution, reason)
    BacklogManager()._append_audit_marker_before_comments(wu_file, marker)

    logger.info("GATE_WAIVER %s recorded for %s (judge=%s, attribution=%s)", gate, unit_id, judge, attribution)
    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "judge": judge,
                "gate": gate,
                "target": target,
                "attribution": attribution,
            }
        )
    )
    return 0


# Accepted `--method` values for `log-newly-reachable` (spec 4.9(a), 5.3; PR #320's
# proposed schema; AC-E2-F4-S1-T2-5). Named constants (not inline literals) so the CLI
# and its docs (`docs/cli-reference.md`) can never drift, mirroring how `GATE_TIERS` /
# `ALL_REQUIRED_JUDGE_NAMES` back `log-waiver`'s vocabularies. The four values mirror
# `docs/newly-reachable-paths.md`'s "What counts as live verification" categories:
# exercising the path by hand, or via one of this repo's three test tiers.
NEWLY_REACHABLE_METHOD_MANUAL: str = "manual"
NEWLY_REACHABLE_METHOD_UNIT_TEST: str = "unit_test"
NEWLY_REACHABLE_METHOD_INTEGRATION_TEST: str = "integration_test"
NEWLY_REACHABLE_METHOD_FUNCTIONAL_TEST: str = "functional_test"
NEWLY_REACHABLE_METHODS: frozenset[str] = frozenset(
    {
        NEWLY_REACHABLE_METHOD_MANUAL,
        NEWLY_REACHABLE_METHOD_UNIT_TEST,
        NEWLY_REACHABLE_METHOD_INTEGRATION_TEST,
        NEWLY_REACHABLE_METHOD_FUNCTIONAL_TEST,
    }
)

# Accepted `--result` values for `log-newly-reachable` (spec 4.9(a), 5.3;
# AC-E2-F4-S1-T2-5): the path either behaves as expected once reached, or the live
# verification surfaced a new, independent defect (`docs/newly-reachable-paths.md`:
# "If verification surfaces a new, independent defect in a newly-reachable path, the
# executor does not silently mark the task done").
NEWLY_REACHABLE_RESULT_VERIFIED: str = "verified"
NEWLY_REACHABLE_RESULT_BROKEN: str = "broken"
NEWLY_REACHABLE_RESULTS: frozenset[str] = frozenset({NEWLY_REACHABLE_RESULT_VERIFIED, NEWLY_REACHABLE_RESULT_BROKEN})


def _scan_log_newly_reachable_flags(argv: tuple[str, ...]) -> tuple[list[str], str, str, str] | int:
    """Scan ``log-newly-reachable``'s argv into positionals plus its three flags.

    Returns:
        ``(positional, path, method, result)`` on success (empty string for any flag
        not supplied). On a missing flag value, returns the ``2`` usage-error exit
        code already printed to stderr.
    """
    positional: list[str] = []
    path = ""
    method = ""
    result = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg in ("--path", "--method", "--result"):
            consumed = _consume_gate_verb_flag_value(args, i, arg)
            if isinstance(consumed, int):
                return consumed
            value, i = consumed
            if arg == "--path":
                path = value
            elif arg == "--method":
                method = value
            else:
                result = value
            continue
        positional.append(arg)
        i += 1
    return positional, path, method, result


def _parse_log_newly_reachable_argv(
    argv: tuple[str, ...],
) -> tuple[str, str, str, str] | int:
    """Parse ``log-newly-reachable <unit-id> --path <p> --method <m> --result <r>``.

    Returns:
        ``(unit_id, path, method, result)`` on success. On a usage error (missing
        positional, missing/empty flag value), prints an ``ERROR: ...`` naming the
        offending argument via :func:`_gate_verb_usage_error` and returns ``2`` for
        the caller to return directly.
    """
    scanned = _scan_log_newly_reachable_flags(argv)
    if isinstance(scanned, int):
        return scanned
    positional, path, method, result = scanned

    if len(positional) < 1:
        return _gate_verb_usage_error("log-newly-reachable requires <unit-id> --path <p> --method <m> --result <r>")
    unit_id = positional[0]

    if not path or not path.strip():
        return _gate_verb_usage_error("--path is required and must be non-empty")
    if not method:
        return _gate_verb_usage_error("--method is required")
    if not result:
        return _gate_verb_usage_error("--result is required")

    return unit_id, path, method, result


def _validate_log_newly_reachable_semantics(method: str, result: str) -> int | None:
    """Validate ``--method``/``--result`` against their declared vocabularies.

    Args:
        method: The ``--method`` flag value.
        result: The ``--result`` flag value.

    Returns:
        The ``2`` usage-error exit code (already printed to stderr) on the first
        violation, or ``None`` when both are valid.
    """
    if method not in NEWLY_REACHABLE_METHODS:
        valid = ", ".join(sorted(NEWLY_REACHABLE_METHODS))
        return _gate_verb_usage_error(f"--method names an unknown method {method!r}; valid choices are: {valid}.")

    if result not in NEWLY_REACHABLE_RESULTS:
        valid = ", ".join(sorted(NEWLY_REACHABLE_RESULTS))
        return _gate_verb_usage_error(f"--result names an unknown result {result!r}; valid choices are: {valid}.")

    return None


def compose_newly_reachable_record(path: str, method: str, result: str) -> str:
    """Compose the single-line ``[NEWLY_REACHABLE] <path> <method> <result>`` marker (spec 5.3).

    The sole authorized builder of the ``[NEWLY_REACHABLE]`` marker text: ``cli.cmd_log_newly_reachable``
    calls this function rather than formatting the tag itself, mirroring
    ``devbench.backlog.manager.compose_gate_waiver_record``'s role for ``[GATE_WAIVER]``.

    Args:
        path: The specific code path made newly reachable. Must be a single
            non-empty token with no whitespace -- the marker grammar is
            space-delimited positional fields, so a whitespace-bearing path would
            corrupt the field boundary on read-back.
        method: One of :data:`NEWLY_REACHABLE_METHODS`.
        result: One of :data:`NEWLY_REACHABLE_RESULTS`.

    Returns:
        The exact one-line marker text (no trailing newline).

    Raises:
        ValueError: If ``path`` is empty or contains whitespace, ``method`` is not
            declared, or ``result`` is not declared. The CLI boundary
            (:func:`_parse_log_newly_reachable_argv`,
            :func:`_validate_log_newly_reachable_semantics`) already rejects these
            cases before this function is ever called from :func:`cmd_log_newly_reachable`;
            these checks are defense in depth for any other caller.
    """
    if not path or any(ch.isspace() for ch in path):
        raise ValueError(f"path must be a single non-empty token with no whitespace; got {path!r}.")
    if method not in NEWLY_REACHABLE_METHODS:
        valid_methods = ", ".join(sorted(NEWLY_REACHABLE_METHODS))
        raise ValueError(f"Unknown method {method!r}; declared methods are: {valid_methods}.")
    if result not in NEWLY_REACHABLE_RESULTS:
        valid_results = ", ".join(sorted(NEWLY_REACHABLE_RESULTS))
        raise ValueError(f"Unknown result {result!r}; declared results are: {valid_results}.")

    return f"[NEWLY_REACHABLE] {path} {method} {result}"


def cmd_log_newly_reachable(*argv: str) -> int:
    """Record a structured ``[NEWLY_REACHABLE]`` marker (spec 4.9(a), 5.3; AC-21).

    Usage::

        log-newly-reachable <unit-id> --path <p> --method <m> --result <r>

    Writes ``[NEWLY_REACHABLE] <path> <method> <result>`` (spec 5.3 field order,
    composed by :func:`compose_newly_reachable_record` -- the sole authorized
    builder) into the unit's ``## TDD Cycle Log`` section (via
    ``BacklogManager._append_audit_marker_before_comments``, the same insertion
    point ``log-waiver`` uses), the audit surface that survives every review
    judge's ``read-unit --strip-comments`` Evidence fetch (PM-6 evidence-horizon
    rule, E2-F3-S1-T2). ``## Comments`` itself is stripped by that fetch, so the
    prose ``[NEWLY_REACHABLE]`` convention ``docs/newly-reachable-paths.md``
    previously documented (written via ``log-comment`` into ``## Comments``) was
    invisible to the very judges spec 4.3 requires to weigh it; this verb replaces
    that convention with a validated, judge-visible structured marker.

    Args:
        argv: ``<unit-id> --path <p> --method <m> --result <r>``. ``--method``
            must be one of :data:`NEWLY_REACHABLE_METHODS`; ``--result`` must be
            one of :data:`NEWLY_REACHABLE_RESULTS`.

    Returns:
        ``0`` on success (the marker was written; stdout carries a JSON summary).
        ``1`` when the unit does not exist. ``2`` (usage error, naming the
        offending argument) when a required field is missing or empty, or
        ``--method``/``--result`` names an unknown value.
    """
    parsed = _parse_log_newly_reachable_argv(argv)
    if isinstance(parsed, int):
        return parsed
    unit_id, path, method, result = parsed

    rc = _validate_log_newly_reachable_semantics(method, result)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_work_unit_file(unit)
    marker = compose_newly_reachable_record(path, method, result)
    BacklogManager()._append_audit_marker_before_comments(wu_file, marker)

    logger.info("NEWLY_REACHABLE recorded for %s (path=%s, method=%s, result=%s)", unit_id, path, method, result)
    print(json.dumps({"unit_id": unit_id, "path": path, "method": method, "result": result}))
    return 0


def build_red_observed_message(exit_code: int | None, test_node_id: str, failure_digest: str) -> str:
    """Build and validate the three-field RED_OBSERVED record message.

    Enforces the field-level constraints ``red_gate_satisfied`` re-validates
    on read, so a record that passes this builder always parses on read too:
    a present, non-whitespace ``test_node_id`` (the read-side
    ``RED_OBSERVED_MESSAGE_FIELDS_RE`` captures it as a single non-whitespace
    token, so a space, tab or newline would build a record the gate can never
    match), a nonzero ``exit_code``, and a hash-shaped ``failure_digest``
    (MEDIUM/LOW findings inherited on E4-F3-S1-T1). Raising ``ValueError``
    naming the offending field lets the orchestrator fail fast on a malformed
    record instead of writing one that silently never satisfies the gate.

    Args:
        exit_code: The observed test-run exit code. Must be present and nonzero
            -- a RED phase is, by definition, an observed failure.
        test_node_id: The pytest node ID of the failing test. Must be
            non-empty and contain no whitespace character.
        failure_digest: A lowercase hex digest of the failure output. Must
            match ``FAILURE_DIGEST_RE`` (8-64 lowercase hex characters) --
            never raw free text, which could otherwise leak paths or secrets
            into git history.

    Returns:
        The space-joined ``exit_code=<n> test_node_id=<id> failure_digest=<digest>``
        message body (without the ``[RED_OBSERVED]`` tag or timestamp).

    Raises:
        ValueError: If any field is missing/empty, ``test_node_id`` contains
            whitespace, ``exit_code`` is zero, or ``failure_digest`` is not
            hash-shaped.
    """
    if exit_code is None:
        raise ValueError(RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE.format(field=RED_OBSERVED_FIELD_EXIT_CODE))
    if not test_node_id:
        raise ValueError(RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE.format(field=RED_OBSERVED_FIELD_TEST_NODE_ID))
    if any(character.isspace() for character in test_node_id):
        raise ValueError(RED_OBSERVED_RECORD_WHITESPACE_TEST_NODE_ID_TEMPLATE.format(test_node_id=test_node_id))
    if not failure_digest:
        raise ValueError(RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE.format(field=RED_OBSERVED_FIELD_FAILURE_DIGEST))
    if exit_code == 0:
        raise ValueError(RED_OBSERVED_RECORD_ZERO_EXIT_CODE_MESSAGE)
    if not FAILURE_DIGEST_RE.match(failure_digest):
        raise ValueError(
            RED_OBSERVED_RECORD_MALFORMED_DIGEST_TEMPLATE.format(
                min=FAILURE_DIGEST_MIN_LENGTH,
                max=FAILURE_DIGEST_MAX_LENGTH,
                digest=failure_digest,
            )
        )
    return RED_OBSERVED_MESSAGE_TEMPLATE.format(
        exit_code=exit_code,
        test_node_id=test_node_id,
        failure_digest=failure_digest,
    )


def write_red_observed_entry(unit_id: str, exit_code: int | None, test_node_id: str, failure_digest: str) -> None:
    """Write the orchestrator-only RED_OBSERVED TDD Cycle Log entry (FR-4.3).

    Unlike ``cmd_log_tdd`` -- the agent-facing CLI verb, which rejects
    ``RED_OBSERVED`` outright -- this function is called directly by the
    orchestrator after it has independently run the test suite and observed
    a nonzero exit code. It is deliberately not registered in ``_COMMANDS``:
    there is no ``log-tdd-red-observed`` subcommand an agent could invoke.

    Args:
        unit_id: Work unit ID, e.g. ``E0-F1-S1-T1``.
        exit_code: The observed nonzero exit code from the orchestrator's own
            test run.
        test_node_id: The pytest node ID of the observed failing test.
        failure_digest: A hash-shaped digest of the failure output.

    Raises:
        ValueError: If the unit is not found in the backlog, or if any
            RED_OBSERVED field is invalid (propagated from
            ``build_red_observed_message``).
    """
    message = build_red_observed_message(exit_code, test_node_id, failure_digest)

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        raise ValueError(f"Work unit '{unit_id}' not found in backlog")

    wu_file = _resolve_work_unit_file(unit)

    mgr = BacklogManager()
    mgr._append_tdd_entry(wu_file, TDD_PHASE_RED_OBSERVED, message)

    logger.info("TDD %s entry logged for %s", TDD_PHASE_RED_OBSERVED, unit_id)


def cmd_tdd_gate(unit_id: str) -> int:
    """Run the machine-observed RED gate for a gated task (FR-4.2, issue #257).

    Orchestrator-facing entry point for :func:`devbench.tdd_gate.observe_red`:
    resolves the work unit and its repo, parses the Changes Manifest, runs the
    gate, and on a genuine RED writes the orchestrator-only ``RED_OBSERVED``
    entry via :func:`write_red_observed_entry` -- the same machinery
    ``red_gate_satisfied`` re-validates on read (E4-F3-S1-T1). This function
    performs no observation logic itself (single responsibility); all
    stash/pytest/classification behavior lives in ``devbench.tdd_gate``.

    Usage::

        tdd-gate <id>

    Exits 0 and prints a one-line success message (including the observed
    test node id and exit code) when a genuine RED is observed and recorded.
    Exits 1 and prints the gate's standardized rejection message to stderr on
    every fail-closed rejection path (dirty tree outside the manifest, no
    production-source rows, no named test, a stash failure, or a false/no
    RED) -- see ``devbench.tdd_gate.observe_red`` for the full rejection
    taxonomy.
    """
    from devbench.backlog.manifest import ManifestParseError, parse_manifest
    from devbench.tdd_gate import TddGateRejectionError, observe_red

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        print(f"ERROR: Work unit file for '{unit_id}' not found on disk", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    content = wu_file.read_text(encoding="utf-8")
    try:
        manifest_paths = [row.file for row in parse_manifest(content)]
    except ManifestParseError as exc:
        print(f"ERROR: Changes Manifest could not be parsed for '{unit_id}': {exc}", file=sys.stderr)
        return 1

    try:
        observation = observe_red(unit_id, repo_path, manifest_paths, content)
    except TddGateRejectionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    write_red_observed_entry(
        unit_id,
        observation.exit_code,
        observation.test_node_id,
        observation.failure_digest,
    )
    print(
        f"RED gate satisfied for {unit_id}: test_node_id={observation.test_node_id} "
        f"exit_code={observation.exit_code} failure_digest={observation.failure_digest}"
    )
    return 0


# DRY (E4-F4-S1-T2, code_review FAIL round 4): the scoped stash push/pop
# green-green-check needs for its "before"/"after" reconstruction is
# identical to `devbench.tdd_gate`'s own RED-gate stash helpers. Those
# helpers (`stash_push_scoped`, `stash_pop`, `STASH_NO_LOCAL_CHANGES_MARKER`)
# were promoted from module-private to public in tdd_gate.py specifically so
# this module can import and reuse them instead of duplicating the git
# invocations a second time; see the module-level note above
# `STASH_NO_LOCAL_CHANGES_MARKER` in tdd_gate.py for the full history.


def _gg_clear_pycache(repo_path: Path) -> None:
    """Remove every ``__pycache__`` directory under *repo_path*.

    A green-green check runs the named tests twice in the same repository
    checkout, once per side, with a ``git stash`` round-trip of the
    production source in between. On filesystems with coarse mtime
    resolution, that round-trip can leave a compiled ``.pyc`` that Python's
    import machinery still considers valid for the different on-disk source
    now in its place, which would make the before-state run silently
    execute stale after-state bytecode instead of the actual before-state
    content -- reporting a pass that never happened. Clearing the cache
    before every run removes that risk at negligible cost.

    Skips any ``__pycache__`` found under a ``.venv`` directory
    (code_review FAIL round 4, non-blocking note): a target repository's
    virtualenv can contain thousands of installed packages' compiled
    bytecode, none of which the stash round-trip above touches or
    invalidates, so walking and deleting it on every one of the two
    per-side runs is pure wasted work with no correctness benefit.
    """
    for cache_dir in repo_path.rglob("__pycache__"):
        if ".venv" in cache_dir.relative_to(repo_path).parts:
            continue
        shutil.rmtree(cache_dir, ignore_errors=False)


def _gg_run_named_tests(unit_id: str, side: str, test_node_ids: Sequence[str], repo_path: Path) -> str | None:
    """Run every node id in *test_node_ids* on one *side* of a green-green check.

    Reuses :func:`devbench.tdd_gate.default_pytest_runner` -- the identical
    runner the RED gate itself uses -- so the pytest-invocation behavior
    (file-scoped, ``-rA``) is defined exactly once (DRY), never duplicated.
    Clears the bytecode cache first (see ``_gg_clear_pycache``).

    Args:
        unit_id: The work unit id, named in every rejection message.
        side: ``"before"`` or ``"after"``, named in every rejection message
            so an operator can tell which state failed.
        test_node_ids: The pytest node ids to run.
        repo_path: The target repository's working tree.

    Returns:
        ``None`` when every named test PASSED with exit code
        ``PYTEST_EXIT_OK``. Otherwise a fully-formed rejection message
        (fail-closed): a node whose outcome could not be determined at all
        (``node_outcome is None``) is reported as a collection failure --
        never silently as a pass -- mirroring FR-4.2's exit-2 semantics.
    """
    from devbench.tdd_gate import PYTEST_EXIT_OK, default_pytest_runner

    _gg_clear_pycache(repo_path)
    for test_node_id in test_node_ids:
        observation = default_pytest_runner(test_node_id, repo_path)
        if observation.node_outcome is None:
            return (
                f"ERROR: green-green check rejected task '{unit_id}': the {side}-state run could "
                f"not collect test '{test_node_id}' (exit code {observation.exit_code}); a "
                "collection failure is reported as a failure, never as a pass, per FR-4.6's "
                "fail-closed semantics."
            )
        if observation.exit_code != PYTEST_EXIT_OK or observation.node_outcome != "PASSED":
            return (
                f"ERROR: green-green check rejected task '{unit_id}': the {side}-state run of "
                f"'{test_node_id}' did not pass (exit code {observation.exit_code}, outcome "
                f"{observation.node_outcome})."
            )
    return None


def cmd_green_green_check(*argv: str) -> int:
    """Run the refactor green-green check: named tests pass before and after (FR-4.6).

    Usage::

        green-green-check <id> <test_node_id> [<test_node_id> ...]

    A ``refactor`` task is exempt from the RED gate but not from its own
    invariant: the change must be behavior-preserving, proven by the same
    named tests passing both before and after it. The change is already
    applied in the working tree when this command runs (the "after"
    state), so this command:

    1. Confirms every named test passes in the current ("after") tree.
    2. Path-scoped stashes the Changes Manifest's production-source rows
       (the same stash discipline as ``devbench.tdd_gate.observe_red``) to
       reconstruct the pre-change ("before") state, and confirms the same
       tests pass there too.
    3. Restores the stash unconditionally, even when the before-state test
       run itself raises, mirroring ``observe_red``'s fail-closed restore
       guarantee.

    A collection failure (a named test whose outcome cannot be determined
    at all) on either side fails closed, naming the side and the collection
    error -- "could not run" is never reported as "passed" (FR-4.6).

    On success, appends a machine-observed ``GREEN_GREEN_OBSERVED`` entry to
    the work unit's TDD Cycle Log (``green_green_observed_satisfied``
    re-validates it on read) so ``cmd_mark_done`` can refuse a ``refactor``
    task that never actually ran this check (AC-E4-F4-S1-T2-4). A rejection
    writes nothing -- the record is proof-of-success only, never
    proof-of-attempt.

    Exits 0 only when every named test PASSED on both sides. Exits 1 on
    every rejection path: a dirty tree outside the Manifest, no
    production-source rows to reconstruct a "before" state from, no
    uncommitted production-source change to stash (nothing to reconstruct
    the "before" state from), a stash push/pop failure, or any test
    failing/not-collecting on either side.
    """
    args = list(argv)
    if len(args) < 2:
        print("ERROR: green-green-check requires <id> <test_node_id> [<test_node_id> ...]", file=sys.stderr)
        return 1
    unit_id, test_node_ids = args[0], args[1:]

    resolved = _gg_resolve_target(unit_id)
    if isinstance(resolved, int):
        return resolved
    repo_path, manifest_paths, wu_file = resolved

    prod_paths = _gg_preflight(unit_id, repo_path, manifest_paths)
    if isinstance(prod_paths, int):
        return prod_paths

    result = _gg_run_before_after(unit_id, repo_path, prod_paths, test_node_ids)
    if result != 0:
        return result

    mgr = BacklogManager()
    mgr._append_tdd_entry(
        wu_file,
        TDD_PHASE_GREEN_GREEN_OBSERVED,
        _GREEN_GREEN_OBSERVED_MESSAGE_TEMPLATE.format(test_node_ids=",".join(test_node_ids)),
    )

    print(f"green-green check passed for {unit_id}: {', '.join(test_node_ids)} PASSED before and after.")
    return 0


def _gg_resolve_target(unit_id: str) -> tuple[Path, list[str], Path] | int:
    """Resolve the target repo path, Changes Manifest paths, and work-unit file
    for green-green-check.

    Returns ``(repo_path, manifest_paths, wu_file)`` on success, or an
    integer non-zero exit code (error already printed) on any resolution
    failure. Consolidates the work-unit/repo/manifest lookups that would
    otherwise be four separate early-return branches directly in
    ``cmd_green_green_check``. ``wu_file`` is returned (not just consumed
    here) so the caller can append the GREEN_GREEN_OBSERVED record to the
    same file on success without re-resolving it.
    """
    from devbench.backlog.manifest import ManifestParseError, parse_manifest

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        print(f"ERROR: Work unit file for '{unit_id}' not found on disk", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    content = wu_file.read_text(encoding="utf-8")
    try:
        manifest_paths = [row.file for row in parse_manifest(content)]
    except ManifestParseError as exc:
        print(f"ERROR: Changes Manifest could not be parsed for '{unit_id}': {exc}", file=sys.stderr)
        return 1

    return repo_path, manifest_paths, wu_file


def _gg_preflight(unit_id: str, repo_path: Path, manifest_paths: Sequence[str]) -> list[str] | int:
    """Return the classified production-source paths, or an int error code.

    Rejects a working tree carrying changes outside the Changes Manifest
    (the check never stashes work it does not own) and a Manifest with no
    production-source rows (Rule 14 classifier) to reconstruct a "before"
    state from.
    """
    from devbench.tdd_gate import classify_production_paths, find_paths_outside_manifest

    outside_paths = find_paths_outside_manifest(repo_path, manifest_paths)
    if outside_paths:
        print(
            f"ERROR: green-green check rejected task '{unit_id}': the working tree carries "
            f"changes outside the Changes Manifest: {', '.join(outside_paths)}. The check never "
            "stashes work it does not own; clear or commit these paths before retrying.",
            file=sys.stderr,
        )
        return 1

    prod_paths = classify_production_paths(manifest_paths)
    if not prod_paths:
        print(
            f"ERROR: green-green check rejected task '{unit_id}': the Changes Manifest contains "
            "no production-source rows (Rule 14 classifier); a refactor task must ship at least "
            "one production file to demonstrate green-green.",
            file=sys.stderr,
        )
        return 1

    return prod_paths


def _gg_run_before_after(unit_id: str, repo_path: Path, prod_paths: Sequence[str], test_node_ids: Sequence[str]) -> int:
    """Run the after/stash/before/pop sequence for green-green-check.

    Prints its own rejection messages and returns 0 when every named test
    PASSED on both sides, 1 on any rejection. Re-raises whatever the
    before-state test run itself raises, after the stash has been popped
    (fail-closed restore, mirroring ``observe_red``) -- the pop-failure
    message is printed before the re-raise so it is never silently lost.

    A ``git stash push`` that reports "no local changes to save"
    (``devbench.tdd_gate.stash_push_scoped`` returning ``pushed=False`` with
    no error) is itself a rejection, not a pass: it means the working tree
    has no uncommitted production-source change to reconstruct a "before"
    state from, so running the before-state check would silently re-run the
    same ("after") tree twice and report a guaranteed, meaningless pass --
    the exact "could not run reported as passed" hole FR-4.6 forbids
    (code_review FAIL, round 2).
    """
    from devbench.tdd_gate import stash_pop, stash_push_scoped

    after_rejection = _gg_run_named_tests(unit_id, "after", test_node_ids, repo_path)
    if after_rejection is not None:
        print(after_rejection, file=sys.stderr)
        return 1

    pushed, push_error = stash_push_scoped(repo_path, prod_paths)
    if push_error is not None:
        print(
            f"ERROR: green-green check rejected task '{unit_id}': "
            f"'git stash push -u -- {' '.join(prod_paths)}' failed: {push_error}",
            file=sys.stderr,
        )
        return 1
    if not pushed:
        print(
            f"ERROR: green-green check rejected task '{unit_id}': "
            f"'git stash push -u -- {' '.join(prod_paths)}' found no uncommitted production-source "
            f"change from the Changes Manifest ({', '.join(prod_paths)}); the pre-change ('before') "
            "state could not be reconstructed. A refactor's change must still be uncommitted in the "
            "working tree when this check runs.",
            file=sys.stderr,
        )
        return 1

    check_exception: BaseException | None = None
    before_rejection: str | None = None
    pop_error: str | None = None
    try:
        before_rejection = _gg_run_named_tests(unit_id, "before", test_node_ids, repo_path)
    except BaseException as caught:
        # Broad and intentional, mirroring observe_red: the pop in the
        # finally block below MUST still run when the before-state test
        # step raises -- including KeyboardInterrupt and SystemExit -- so
        # the exception is captured here and re-raised only after the
        # stash has been popped (fail-closed restore).
        check_exception = caught
    finally:
        pop_error = stash_pop(repo_path)

    if pop_error is not None:
        detail = f"'git stash pop' failed after the before-state run: {pop_error}."
        if check_exception is not None:
            detail += f" The test step also raised {check_exception!r}; both failures are reported together."
        print(f"ERROR: green-green check rejected task '{unit_id}': {detail}", file=sys.stderr)
        if check_exception is not None:
            raise check_exception
        return 1

    if check_exception is not None:
        raise check_exception

    if before_rejection is not None:
        print(before_rejection, file=sys.stderr)
        return 1

    return 0


def cmd_ensure_branch(unit_id: str) -> int:
    """Create or switch to the feature branch for a work unit before executor runs.

    Derives the branch name from the work unit ID (``backlog/<id-lower>``) and
    calls :meth:`~devbench.github.git_ops.GitOpsService.ensure_branch` to switch
    to it, stashing and popping if the working tree is dirty.

    Used by the orchestrate skill immediately after ``devbench next`` and before
    invoking the executor agent.
    """
    from devbench.github.git_ops import GitOpsService

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    from devbench.config import SINGLE_BRANCH

    branch_prefix = get_effective_branch_prefix(canonical_repo, RUNTIME_CONFIG)
    branch = (
        format_single_branch_name(SINGLE_BRANCH, branch_prefix)
        if SINGLE_BRANCH
        else format_branch_name(unit_id, branch_prefix)
    )
    ops = GitOpsService()
    ops.ensure_branch(canonical_repo, repo_path, branch)
    logger.info("Branch ready: %s on %s", branch, canonical_repo)
    return 0


def _resolve_git_ops_context(unit_id: str) -> tuple[WorkUnit, str, Path]:
    """Resolve unit, canonical repo, and local path for git-ops commands.

    Returns:
        Tuple of (unit, canonical_repo, repo_path).

    Raises:
        SystemExit: If the unit is not found or the repo path is not configured.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        sys.exit(1)

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        sys.exit(1)

    return unit, canonical_repo, repo_path


def _git_ops_deferred(unit_id: str, unit: WorkUnit, canonical_repo: str, repo_path: Path, branch: str) -> int:
    """Commit locally only (no push/PR/merge) for single-branch deferred mode."""
    from devbench.backlog.manifest import assert_staged_matches_manifest, parse_manifest
    from devbench.github.git_ops import GitOpsService

    ops = GitOpsService()
    commit_message = f"{unit_id}: {unit.title}"

    wu_file = _resolve_unit_file(unit)
    mgr = BacklogManager()

    # Re-affirm the working tree is on the configured branch before
    # committing. ensure_branch is a no-op when HEAD is already correct
    # but corrects drift (detached HEAD, switched branch from a previous
    # task, etc.) without an operator round-trip. commit_local then runs
    # its own assert_on_branch as a final fail-fast guard.
    ops.ensure_branch(canonical_repo, repo_path, branch)

    # Orphan-pattern check: refuse to commit when any staged path matches
    # a build/state pattern (terraform state, terragrunt cache, Python
    # pycache, etc.). This guards against the class of pollution that
    # bypasses the manifest-scope assertion when an agent's Manifest
    # accidentally lists such a path or when files slip through unstaged
    # paths. On detection, auto-emit a cleanup proposal so the cascade
    # self-heals; the original task moves to blocked-pending-proposal.
    if _emit_orphan_cleanup_proposal_if_needed(unit_id, unit, repo_path):
        return 1

    # Manifest-scope check: every staged path must be in the work unit's
    # Changes Manifest. Catches the TRACE_FILE / dst/ / fixture-pollution
    # class of bug deterministically before commit. Skipped only when the
    # work-unit file isn't resolvable (orchestrator runs without a backlog
    # context never reach this path in practice).
    if _refuse_unscoped_commit(unit_id, wu_file):
        return 1
    assert wu_file is not None  # narrowed by _refuse_unscoped_commit above
    manifest_rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
    manifest_files = [r.file for r in manifest_rows]

    # A unit declaring '## Expected Output: none' produces no commit; skip the
    # whole commit/PR/CI/merge sequence rather than failing to stage nothing.
    if _unit_expects_no_output(wu_file, manifest_files):
        return _complete_no_output_unit(unit_id, wu_file, repo_path, manifest_files)

    assert_staged_matches_manifest(repo_path, manifest_files)

    ops.commit_local(canonical_repo, repo_path, branch, commit_message, manifest_files=manifest_files)
    logger.info("Committed locally (deferred PR): %s on %s", unit_id, branch)
    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[COMMIT_DEFERRED] {commit_message}")
    print(json.dumps({"unit_id": unit_id, "mode": "deferred", "branch": branch}))
    return 0


def _orphan_paths_for_repo(unit_id: str, repo_path: Path) -> list[str] | None:
    """Return the union of staged + tracked orphan paths for *repo_path*.

    Returns ``None`` when the gate is skipped (non-git checkout or git
    subprocess failure); the caller treats that as "no detection, no
    refusal". Returns ``[]`` when the repo is clean. Returns the sorted
    list of orphan paths otherwise. Extracted from
    :func:`_emit_orphan_cleanup_proposal_if_needed` to keep that
    function under the project's branch / return budget.
    """
    from devbench.git_orphans import detect_staged_orphans, detect_tracked_orphans

    if not (repo_path / ".git").exists():
        return None
    try:
        staged = detect_staged_orphans(repo_path)
        tracked = detect_tracked_orphans(repo_path)
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "git-ops orphan-detect skipped for %s -- subprocess failed: %s",
            unit_id,
            exc,
        )
        return None
    return sorted(set(staged) | set(tracked))


def _emit_orphan_cleanup_proposal_if_needed(unit_id: str, unit: WorkUnit, repo_path: Path) -> bool:
    """Self-defending git-ops gate: refuse pollution, recover the cleanup inline.

    Two modes (Phase 1 of the orphan-cascade fix):

    1. **Inline-cleanup mode (default)**: when ``INLINE_ORPHAN_CLEANUP_ENABLED``
       is True, run ``cleanup_tracked_orphans`` programmatically and commit the
       result as a devbench-authored chore commit on the task's branch, then
       return ``False`` so the caller continues with the original task's
       commit. No backlog proposal is emitted; the cleanup is not a work unit.
       This collapses the cascade where multiple parents emitted duplicate
       cleanup proposals and those proposals themselves got blocked by the
       manifest amender on predecessor staging.

    2. **Legacy proposal mode**: when the operator sets
       ``DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`` (audit-driven backlogs that
       require a work unit per cleanup), the original behaviour applies:
       allocate a cleanup task ID, materialise the proposal, auto-wire the
       parent as ``BLOCKED_PENDING_PROPOSAL``. Cross-task de-duplication is
       added so two parents detecting the same orphan set wire to the SAME
       cleanup task instead of emitting duplicates.

    Returns ``False`` (continue with commit) when no orphans are detected
    OR when inline cleanup succeeds. Returns ``True`` (refuse) only on
    legacy-mode emit or hard failure of the inline path.
    """
    from devbench.config import INLINE_ORPHAN_CLEANUP_ENABLED

    detected = _orphan_paths_for_repo(unit_id, repo_path)
    if detected is None or not detected:
        return False

    if INLINE_ORPHAN_CLEANUP_ENABLED:
        return _inline_orphan_cleanup_or_refuse(unit_id, repo_path, detected)

    return _legacy_emit_orphan_cleanup_proposal(unit_id, unit, repo_path, detected)


class _InlineCleanupError(RuntimeError):
    """Raised when an inline orphan-cleanup step fails; caught at the
    function boundary so :func:`_inline_orphan_cleanup_or_refuse` has a
    single failure-return path (PLR0911 conformance)."""


def _run_inline_cleanup_steps(
    repo_path: Path,
    detected: list[str],
) -> tuple[bool, OrphanReport]:
    """Inner steps for inline orphan cleanup. Returns (cleanup_committed, report).

    Raises :class:`_InlineCleanupError` on any subprocess failure so the
    caller can convert to a single refuse-return.

    Resolves *repo_path* once at the function head so every subsequent
    operation runs in the same path-space as ``cleanup_tracked_orphans``
    does internally (it calls ``repo_path.resolve()`` at its own
    function head). Without this, a symlinked checkout (the documented
    workspace layout for repos that cannot live next to
    ``DEVBENCH_WORKSPACE_ROOT``) produces an ``OrphanReport`` whose
    ``gitignore_path`` lives in resolved-path space while this caller's
    ``repo_path`` lives in symlink-path space, and the
    ``gitignore_path.relative_to(repo_path)`` call below raises
    ``ValueError``. Issue #125.
    """
    from devbench.constants import DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE
    from devbench.git_orphans import cleanup_tracked_orphans
    from devbench.github.git_ops import GitOpsService

    repo_path = repo_path.resolve()
    ops = GitOpsService()
    try:
        _, staged_out, _ = ops._git(["diff", "--cached", "--name-only"], repo_path)
    except RuntimeError as exc:
        raise _InlineCleanupError(f"could not read pre-cleanup staged paths: {exc}") from exc
    pre_cleanup_staged = [line.strip() for line in staged_out.splitlines() if line.strip()]
    orphan_set = set(detected)
    paths_to_restore = [p for p in pre_cleanup_staged if p not in orphan_set]

    try:
        ops._git(["reset", "HEAD", "--"], repo_path)
    except RuntimeError as exc:
        raise _InlineCleanupError(f"git reset HEAD failed: {exc}") from exc

    try:
        report = cleanup_tracked_orphans(repo_path)
    except (FileNotFoundError, subprocess.CalledProcessError, RuntimeError) as exc:
        raise _InlineCleanupError(f"cleanup_tracked_orphans failed: {exc}") from exc

    cleanup_committed = False
    if report.gitignore_updated:
        try:
            ops._git(
                ["add", "--", str(report.gitignore_path.relative_to(repo_path))],
                repo_path,
            )
        except RuntimeError as exc:
            raise _InlineCleanupError(f"could not stage .gitignore: {exc}") from exc

    if report.removed or report.gitignore_updated:
        try:
            ops._git(
                ["commit", "-m", DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE],
                repo_path,
            )
            cleanup_committed = True
        except RuntimeError as exc:
            raise _InlineCleanupError(f"cleanup commit failed: {exc}") from exc

    if paths_to_restore:
        try:
            ops._git(["add", "-A", "--", *paths_to_restore], repo_path)
        except RuntimeError as exc:
            raise _InlineCleanupError(f"could not re-stage executor paths after cleanup: {exc}") from exc

    return cleanup_committed, report


def _inline_orphan_cleanup_or_refuse(
    unit_id: str,
    repo_path: Path,
    detected: list[str],
) -> bool:
    """Run ``cleanup_tracked_orphans`` inline, commit it, preserve executor staging.

    Procedure (Phase 1, approach 7):

    1. Capture the executor's pre-cleanup staged paths via
       ``git diff --cached --name-only``.
    2. Filter out detected orphans (so we don't re-stage them after cleanup).
    3. Reset the index to HEAD via ``git reset HEAD --``.
    4. Run ``cleanup_tracked_orphans(repo_path)`` -- ``git rm --cached`` each
       tracked orphan and write/extend the devbench-managed ``.gitignore``.
    5. Stage ``.gitignore`` (the rm operations already staged the deletions).
    6. Commit with the canonical chore message.
    7. Re-stage the executor's filtered staging set so the downstream
       ``assert_staged_matches_manifest`` runs against the executor's intent
       (minus orphans).

    Returns ``False`` on success (caller continues with the task commit) or
    ``True`` on hard failure (caller refuses the task commit).
    """
    try:
        cleanup_committed, report = _run_inline_cleanup_steps(repo_path, detected)
    except _InlineCleanupError as exc:
        logger.warning("inline orphan cleanup refused commit for %s: %s", unit_id, exc)
        sample = ", ".join(detected[:5])
        if len(detected) > 5:
            sample += f", and {len(detected) - 5} more"
        print(
            f"ERROR: git-ops refused -- inline orphan cleanup failed for {unit_id} "
            f"({len(detected)} detected, sample: {sample}). Reason: {exc}. "
            "Run 'devbench cleanup-tracked-orphans <repo>' manually, commit "
            "the result, then re-invoke git-ops to resume.",
            file=sys.stderr,
        )
        return True

    if cleanup_committed:
        logger.info(
            "git-ops: inline-cleaned %d orphan(s) for %s (%d removed, gitignore %s)",
            len(detected),
            unit_id,
            len(report.removed),
            "updated" if report.gitignore_updated else "unchanged",
        )
        sample = ", ".join(detected[:5])
        if len(detected) > 5:
            sample += f", and {len(detected) - 5} more"
        print(
            f"git-ops: inline-cleaned {len(detected)} orphan path(s) "
            f"(sample: {sample}); cleanup chore commit landed; "
            "continuing with task commit.",
            file=sys.stderr,
        )
    else:
        logger.info(
            "git-ops: orphan-cleanup no-op for %s (detected %d but cleanup yielded "
            "no commit -- staged-only orphans now ignored via existing .gitignore); "
            "continuing with task commit",
            unit_id,
            len(detected),
        )
    return False


def _legacy_emit_orphan_cleanup_proposal(
    unit_id: str,
    unit: WorkUnit,
    repo_path: Path,
    detected: list[str],
) -> bool:
    """Legacy proposal-emit path (gated behind ``DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1``).

    Adds cross-task de-duplication that the original implementation lacked:
    if any pending proposal under ``<workspace>/.devbench/proposals/*.json``
    already targets ``.gitignore`` with an orphan-cleanup rejection_reason
    overlapping the current detection set, this function reuses that proposal
    (auto-wires the current parent as ``BLOCKED_PENDING_PROPOSAL`` against
    the existing cleanup task) instead of allocating a new one.
    """
    from devbench.backlog.proposal import (
        Proposal,
        ProposalError,
        ProposedTask,
        allocate_next_ids,
        materialise_proposal,
        promote_proposal,
        proposal_path,
        write_proposal,
    )

    proposal_file = proposal_path(WORKSPACE_ROOT, unit_id)
    if proposal_file.exists():
        logger.info(
            "git-ops: orphans detected for %s; proposal already exists at %s -- refusing commit",
            unit_id,
            proposal_file,
        )
        print(
            f"ERROR: git-ops refused -- {len(detected)} build/state artifact(s) "
            f"would pollute the commit (e.g. {detected[0]!r}). "
            f"Cleanup proposal already pending at {proposal_file}; "
            f"the cascade will handle it on the next iteration.",
            file=sys.stderr,
        )
        return True

    existing_cleanup = _find_existing_cleanup_proposal(detected)
    if existing_cleanup is not None:
        wired = _wire_orphan_cleanup_dep_chain(
            new_id=existing_cleanup,
            files_to_own=[".gitignore"],
            unit_repo=unit.repo or "",
        )
        logger.info(
            "git-ops: orphan detection for %s reuses existing cleanup task %s "
            "(de-duplication; wired %d peer claimants)",
            unit_id,
            existing_cleanup,
            len(wired),
        )
        sample = ", ".join(detected[:5])
        print(
            f"ERROR: git-ops refused -- {len(detected)} build/state artifact path(s) "
            f"would pollute the commit (sample: {sample}). "
            f"Existing cleanup task {existing_cleanup} already covers this orphan set; "
            f"{unit_id} wired as dependent (no duplicate emit).",
            file=sys.stderr,
        )
        return True

    story_id = "-".join(unit_id.split("-")[:3])
    try:
        new_id = allocate_next_ids(WORKSPACE_ROOT, BACKLOG_ROOT, story_id, 1)[0]
    except ProposalError as exc:
        print(f"ERROR: git-ops refused (cannot allocate cleanup-task ID): {exc}", file=sys.stderr)
        return True

    repo_label = unit.repo or str(repo_path.name)
    sample = ", ".join(detected[:5])
    if len(detected) > 5:
        sample += f", and {len(detected) - 5} more"
    proposal = Proposal(
        source_task_id=unit_id,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        rejection_reason=(
            f"git-ops refused: {len(detected)} build/state artifact path(s) "
            f"would be committed ({sample}). These match standard "
            "ignore-worthy patterns (terraform state, terragrunt cache, "
            "Python pycache, coverage, etc.) and must be untracked + "
            "added to .gitignore before any work-unit commit can land."
        ),
        proposed_tasks=[
            ProposedTask(
                suggested_id=new_id,
                title=f"Untrack build/state orphans in {repo_label} and write managed .gitignore",
                files_to_own=[".gitignore"],
                linked_scenarios=["git-ops orphan-pattern guard"],
                suggested_acs=[
                    f"AC-FUNC-001: 'devbench cleanup-tracked-orphans {repo_label}' exits 0 "
                    "and removes every detected orphan from the index "
                    "(verified by re-running with --dry-run; detected_count is 0).",
                    "AC-FUNC-002: The repo's root .gitignore contains the "
                    "devbench-managed block "
                    f"({DEVBENCH_GITIGNORE_HEADER_LITERAL!r}) covering "
                    "terraform state, terragrunt cache, Python pycache, "
                    "coverage, node_modules, and .DS_Store.",
                    "AC-FUNC-003: 'git ls-files' in the repo returns zero "
                    "matches for the standard orphan patterns after cleanup.",
                    "AC-FINAL-009: 'devbench validate-backlog' exits 0.",
                    "AC-FINAL-011: No bypass annotations introduced.",
                    "AC-FINAL-012: No em-dash characters introduced.",
                    "AC-FINAL-015: Changes Manifest matches git diff exactly "
                    "(.gitignore + the unstaged --cached file removals).",
                ],
                suggested_approach=(
                    f"Context: {len(detected)} build/state artifact paths "
                    f"are tracked or staged in {repo_label} and would pollute "
                    f"any commit through devbench's git-ops. Detected sample: {sample}. "
                    "Scope: edit .gitignore at the repo root and run "
                    f"'devbench cleanup-tracked-orphans {repo_label}'. "
                    "Out of scope: any file outside the repo's tracked-orphan set; "
                    "no spec changes; no history rewrite (the cleanup is "
                    "forward-only via 'git rm --cached', preserving files on disk). "
                    "Approach: 1. RED -- author tests/integration/"
                    "test_orphan_cleanup.py asserting "
                    "'devbench cleanup-tracked-orphans <repo> --dry-run' "
                    "returns detected_count == 0 (the test fails initially "
                    "because orphans are present). 2. GREEN -- run "
                    f"'devbench cleanup-tracked-orphans {repo_label}', "
                    "which untracks every match and writes the .gitignore "
                    "block; rerun the dry-run; assertion passes. "
                    "3. REFACTOR -- verify .gitignore lines are deduped and "
                    "the devbench-managed header appears once."
                ),
            )
        ],
    )

    # AC-189-8: read the configured default status once so all writes below
    # are consistent. When the config says ``draft``, the cleanup task is
    # left in draft state -- the operator must promote it explicitly before
    # the orchestrator can claim it. When the config says ``in-queue``
    # (the backwards-compatible default) the draft is promoted immediately,
    # matching the pre-AC-189-8 behaviour.
    new_wu_default_status: str = RUNTIME_CONFIG.backlog.default_status_for_new_work_units

    try:
        write_proposal(WORKSPACE_ROOT, proposal)
        materialised_files = materialise_proposal(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            proposal=proposal,
            repo=unit.repo,
        )
        if new_wu_default_status != STATUS_DRAFT:
            promote_proposal(
                workspace_root=WORKSPACE_ROOT,
                backlog_root=BACKLOG_ROOT,
                backlog_index=BACKLOG_INDEX,
                task_id=new_id,
                dep_on_source=True,
            )
        wired_claimants = _wire_orphan_cleanup_dep_chain(
            new_id=new_id,
            files_to_own=[".gitignore"],
            unit_repo=unit.repo,
        )
    except ProposalError as exc:
        print(
            f"ERROR: git-ops refused (orphans detected) and proposal emission failed: {exc}",
            file=sys.stderr,
        )
        return True

    logger.info(
        "git-ops: refused commit for %s due to %d orphan path(s); auto-emitted cleanup proposal %s",
        unit_id,
        len(detected),
        new_id,
    )
    extra = (
        f" Auto-wired dep-chain: {sorted(wired_claimants)} now depend on {new_id} "
        "to land first (resolves Manifest Conflict on '.gitignore')."
        if wired_claimants
        else ""
    )
    print(
        f"ERROR: git-ops refused -- {len(detected)} build/state artifact path(s) "
        f"would pollute the commit (sample: {sample}). "
        f"Auto-emitted cleanup proposal {new_id} ({new_wu_default_status}); "
        f"{unit_id} is now blocked-pending-proposal and will auto-clear when the cleanup commits.{extra} "
        f"Materialised: {[str(p) for p in materialised_files]}.",
        file=sys.stderr,
    )
    return True


def _find_existing_cleanup_proposal(detected: list[str]) -> str | None:
    """Return the suggested_id of any pending orphan-cleanup proposal, else None.

    Cross-task de-duplication for the legacy proposal flow (Phase 1 secondary
    fix). Scans ``<workspace>/.devbench/proposals/*.json`` and returns the
    first proposal whose ``proposed_tasks[].files_to_own`` includes
    ``.gitignore`` -- the canonical signature of an orphan-cleanup proposal
    emitted by :func:`_legacy_emit_orphan_cleanup_proposal`. Since
    ``cleanup_tracked_orphans`` cleans by pattern (not by specific path list),
    any in-flight cleanup proposal resolves any orphan that matches the same
    pattern set, so wiring the current parent to that existing task is
    sufficient -- no need to compare detected-path sets.

    The ``detected`` argument is reserved for future overlap-set narrowing
    when the pattern list is operator-overridable per task; today every
    cleanup proposal targets the same global pattern set so any match is a
    valid de-duplication target.
    """
    del detected  # reserved for future per-task pattern subsetting
    proposals_dir = WORKSPACE_ROOT / ".devbench" / "proposals"
    if not proposals_dir.is_dir():
        return None
    for proposal_json in sorted(proposals_dir.glob("*.json")):
        try:
            payload = json.loads(proposal_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for proposed in payload.get("proposed_tasks") or []:
            files_to_own = proposed.get("files_to_own") or []
            if ".gitignore" in files_to_own:
                suggested_id = proposed.get("suggested_id")
                if isinstance(suggested_id, str) and suggested_id:
                    return suggested_id
    return None


def _count_ci_fail_attempts(wu_file: Path | None) -> int:
    """Return the number of ``[CI_FAIL]`` audit entries in *wu_file*.

    Used by :func:`_handle_ci_failure` to decide whether to keep retrying
    (rc=2) or transition to BLOCKED (rc=1) based on the shared retry budget
    (``MAX_RETRY_ATTEMPTS``). Returns 0 when *wu_file* is None or unreadable.
    """
    if wu_file is None or not wu_file.is_file():
        return 0
    try:
        content = wu_file.read_text(encoding="utf-8")
    except OSError:
        return 0
    return content.count("[CI_FAIL]")


def _emit_ci_failure_feedback(
    ops: GitOpsService,
    unit_id: str,
    canonical_repo: str,
    pr_number: int,
    repo_path: Path,
    attempt: int,
) -> tuple[Path | None, str]:
    """Fetch the failing run log, write it to disk, return (path, summary).

    Returns ``(None, generic_summary)`` when the run-id or log fetch fails;
    the caller still emits a feedback comment so the executor knows a CI
    failure occurred even when the log details were unavailable.
    """
    from devbench.config import CI_FAILURE_LOG_BYTES

    summary_default = f"CI checks failed for PR #{pr_number} on {canonical_repo}; attempt {attempt}; log unavailable."

    run_id = ops.get_latest_failing_run_id(canonical_repo, pr_number, repo_path=repo_path)
    if run_id is None:
        return None, summary_default

    log_text = ops.fetch_run_log(canonical_repo, run_id, CI_FAILURE_LOG_BYTES, repo_path=repo_path)
    if not log_text.strip():
        return None, summary_default

    log_dir = WORKSPACE_ROOT / ".devbench" / "ci-failures"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{unit_id}-{attempt}.log"
    atomic_write_text(log_path, log_text)
    summary = (
        f"CI checks failed for PR #{pr_number} on {canonical_repo} "
        f"(run #{run_id}, attempt {attempt}); trimmed log saved to {log_path}."
    )
    return log_path, summary


def _handle_ci_failure(
    *,
    ops: GitOpsService,
    unit_id: str,
    canonical_repo: str,
    pr_number: int,
    repo_path: Path,
    wu_file: Path | None,
    mgr: BacklogManager,
) -> int:
    """Issue #115 dispatch: CI failure -> rc=2 retry signal or rc=1 BLOCKED.

    When ``CI_FAILURE_RETRY_ENABLED`` is False, returns rc=1 with the
    legacy error message (backward compatible). When True, fetches the
    failing run log, writes it under ``.devbench/ci-failures/``, appends a
    ``[CI_FAIL]`` audit comment, and returns rc=2 if the per-task attempt
    count is below ``MAX_RETRY_ATTEMPTS`` -- otherwise rc=1 with the
    exhaustion error.
    """
    from devbench.config import CI_FAILURE_RETRY_ENABLED, MAX_RETRY_ATTEMPTS

    if not CI_FAILURE_RETRY_ENABLED:
        print(f"ERROR: CI checks failed for PR #{pr_number} on {canonical_repo}", file=sys.stderr)
        return 1

    attempts_so_far = _count_ci_fail_attempts(wu_file)
    next_attempt = attempts_so_far + 1
    log_path, summary = _emit_ci_failure_feedback(
        ops=ops,
        unit_id=unit_id,
        canonical_repo=canonical_repo,
        pr_number=pr_number,
        repo_path=repo_path,
        attempt=next_attempt,
    )

    if wu_file is not None:
        marker = "[CI_FAIL_BLOCKED]" if next_attempt >= MAX_RETRY_ATTEMPTS else "[CI_FAIL]"
        message = f"{marker} {summary}"
        mgr._append_agent_comment(wu_file, "git_ops", message)
    from devbench.notifications import notify_ci_failure

    pr_url_for_notify = f"https://github.com/{canonical_repo}/pull/{pr_number}"
    notify_ci_failure(unit_id, canonical_repo, pr_url_for_notify, next_attempt)

    if next_attempt >= MAX_RETRY_ATTEMPTS:
        print(
            f"ERROR: CI checks failed for PR #{pr_number} on {canonical_repo} "
            f"after {next_attempt} executor retry/retries (budget exhausted at "
            f"MAX_RETRY_ATTEMPTS={MAX_RETRY_ATTEMPTS}). {summary}. "
            "Task BLOCKED; operator review required.",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "ci_retry": True,
                "attempt": next_attempt,
                "max_attempts": MAX_RETRY_ATTEMPTS,
                "log_path": str(log_path) if log_path is not None else None,
                "pr_number": pr_number,
            }
        )
    )
    return 2


def _handle_pr_review_resolution(
    *,
    ops: GitOpsService,
    unit_id: str,
    canonical_repo: str,
    pr_number: int,
    repo_path: Path,
    wu_file: Path | None,
    mgr: BacklogManager,
) -> int:
    """Issue #116 dispatch: poll PR review state before merge.

    Returns 0 when the merge may proceed (no unresolved bot comments / review
    decisions, or the phase is disabled). Returns 3 when the executor must
    be re-invoked to address the unresolved review feedback. Returns 1 when
    the retry budget is exhausted -> BLOCKED.

    No-op fast path: when ``PR_REVIEW_RESOLUTION_ENABLED`` is False (default)
    OR both ``PR_REVIEW_AGENTS`` is empty and ``PR_REVIEW_DECISION_BLOCKS`` is
    False, the phase is disabled and returns 0 immediately.
    """
    from devbench.config import (
        MAX_RETRY_ATTEMPTS,
        PR_REVIEW_AGENTS,
        PR_REVIEW_DECISION_BLOCKS,
        PR_REVIEW_POLL_INTERVAL,
        PR_REVIEW_RESOLUTION_ENABLED,
        PR_REVIEW_SETTLE_SECONDS,
    )

    if not PR_REVIEW_RESOLUTION_ENABLED:
        return 0
    if not PR_REVIEW_AGENTS and not PR_REVIEW_DECISION_BLOCKS:
        return 0

    resolution = ops.poll_pr_review_resolution(
        canonical_repo,
        pr_number,
        repo_path=repo_path,
        agents=PR_REVIEW_AGENTS,
        decision_blocks=PR_REVIEW_DECISION_BLOCKS,
        settle_seconds=PR_REVIEW_SETTLE_SECONDS,
        poll_interval=PR_REVIEW_POLL_INTERVAL,
    )
    if resolution.resolved:
        return 0

    attempts_so_far = _count_pr_bot_retry_attempts(wu_file)
    next_attempt = attempts_so_far + 1
    feedback_path = _emit_pr_bot_feedback(unit_id, pr_number, resolution, next_attempt)

    if wu_file is not None:
        marker = "[PR_BOT_FAIL_BLOCKED]" if next_attempt >= MAX_RETRY_ATTEMPTS else "[PR_BOT_FAIL]"
        summary = (
            f"PR #{pr_number} has unresolved review feedback (attempt {next_attempt}); "
            f"reviewDecision={resolution.review_decision!r}, "
            f"unresolved_bot_comments={len(resolution.unresolved_comments)}, "
            f"unresolved_reviews={len(resolution.unresolved_reviews)}; "
            f"feedback payload at {feedback_path}."
        )
        mgr._append_agent_comment(wu_file, "git_ops", f"{marker} {summary}")

    if next_attempt >= MAX_RETRY_ATTEMPTS:
        print(
            f"ERROR: PR #{pr_number} on {canonical_repo} has unresolved review "
            f"feedback after {next_attempt} executor retry/retries "
            f"(budget exhausted at MAX_RETRY_ATTEMPTS={MAX_RETRY_ATTEMPTS}). "
            f"Feedback payload at {feedback_path}. Task BLOCKED; operator review required.",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "pr_bot_retry": True,
                "attempt": next_attempt,
                "max_attempts": MAX_RETRY_ATTEMPTS,
                "feedback_path": str(feedback_path),
                "pr_number": pr_number,
                "review_decision": resolution.review_decision,
            }
        )
    )
    return 3


def _count_pr_bot_retry_attempts(wu_file: Path | None) -> int:
    """Return the number of ``[PR_BOT_FAIL]`` audit entries in *wu_file*."""
    if wu_file is None or not wu_file.is_file():
        return 0
    try:
        content = wu_file.read_text(encoding="utf-8")
    except OSError:
        return 0
    return content.count("[PR_BOT_FAIL]")


def _emit_pr_bot_feedback(
    unit_id: str,
    pr_number: int,
    resolution: object,
    attempt: int,
) -> Path:
    """Write the ``pr-bot`` feedback payload to ``.devbench/pr-bot-feedback/``.

    The payload is a JSON document the executor can read to understand which
    review threads need addressing. Format mirrors the ``review-fail`` shape
    used elsewhere in the orchestrator.
    """
    feedback_dir = WORKSPACE_ROOT / ".devbench" / "pr-bot-feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    feedback_path = feedback_dir / f"{unit_id}-{attempt}.json"
    payload = {
        "unit_id": unit_id,
        "pr_number": pr_number,
        "attempt": attempt,
        "review_decision": getattr(resolution, "review_decision", None),
        "unresolved_reviews": [
            {
                "reviewer": r.get("reviewer", ""),
                "state": r.get("state", ""),
                "body": r.get("body", ""),
                "submitted_at": r.get("submitted_at", ""),
            }
            for r in getattr(resolution, "unresolved_reviews", [])
        ],
        "unresolved_comments": [
            {
                "author": c.get("author", ""),
                "path": c.get("path", ""),
                "line": c.get("line", 0),
                "body": c.get("body", ""),
                "created_at": c.get("created_at", ""),
            }
            for c in getattr(resolution, "unresolved_comments", [])
        ],
    }
    atomic_write_text(feedback_path, json.dumps(payload, indent=2))
    return feedback_path


def _wire_orphan_cleanup_dep_chain(
    new_id: str,
    files_to_own: list[str],
    unit_repo: str,
) -> list[str]:
    """Auto-wire ``add-dep`` rows so the new cleanup task does not collide on Manifest paths.

    Phase 10 fix: when ``_emit_orphan_cleanup_proposal_if_needed``
    auto-emits a cleanup proposal claiming ``.gitignore`` (or any other
    files declared in ``files_to_own``), an in-queue / blocked /
    proposed peer task that already claims the same path triggers the
    E224 Manifest-Conflict rule and halts the orchestrator. Resolve
    the collision by adding a ``Dependencies`` row on each pre-existing
    claimant pointing at the new cleanup task -- that forms a dep-chain
    which the Manifest-Conflict rule already accepts as a valid
    resolution.

    Returns the list of claimant IDs that received an auto-wired dep
    (sorted by ID for stable output). Empty list when no collision was
    detected. ``ProposalError`` from any underlying call propagates so
    the caller can surface it via the existing error path.
    """
    from devbench.backlog import manifest as manifest_mod
    from devbench.backlog.parser import BacklogParser

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    open_statuses = {WorkUnitStatus.IN_QUEUE, WorkUnitStatus.BLOCKED, WorkUnitStatus.PROPOSED}
    target_paths = set(files_to_own)
    wired: set[str] = set()
    for peer in units:
        if peer.id == new_id:
            continue
        if peer.status not in open_statuses:
            continue
        if peer.repo and unit_repo and peer.repo != unit_repo:
            continue
        wu_path = _resolve_unit_file(peer)
        if wu_path is None or not wu_path.is_file():
            continue
        try:
            entries = manifest_mod.parse_manifest(wu_path.read_text(encoding="utf-8"))
        except manifest_mod.ManifestParseError:
            continue
        peer_paths = {entry.file for entry in entries}
        if not peer_paths & target_paths:
            continue
        # Add the dep on the EXISTING claimant -- it depends on the
        # cleanup landing first, so the cleanup commits its
        # devbench-managed block before the peer rebases on top.
        from devbench.backlog.proposal import add_dep

        add_dep(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            blocked_task_id=peer.id,
            blocker_task_id=new_id,
            reason="auto-wired by orphan-cleanup auto-emit; both manifests claim a shared path",
        )
        wired.add(peer.id)
    return sorted(wired)


# Mirror of git_orphans.DEVBENCH_GITIGNORE_HEADER for lazy-imported docstring contexts.
DEVBENCH_GITIGNORE_HEADER_LITERAL: str = "# devbench-managed: tracked-orphan cleanup defaults"


def cmd_cleanup_tracked_orphans(repo_or_path: str, dry_run_flag: str = "") -> int:
    """Untrack build/state artifacts that should be gitignored.

    Usage::

        cleanup-tracked-orphans <org/repo|repo-path> [--dry-run]

    Walks ``git ls-files`` in the target repo, identifies entries
    matching standard ignore patterns (terraform state, terragrunt
    cache, Python pycache, coverage, node_modules, .DS_Store), runs
    ``git rm --cached`` on each (preserving them on disk), and writes a
    devbench-managed block to the repo's root ``.gitignore`` so future
    commits cannot reintroduce the pattern.

    Pure Python implementation: invokes git via subprocess directly so
    the PreToolUse ``guard-destructive-git`` hook (which scopes only to
    Bash agent-tool calls) does not interfere.

    Override the default pattern list via
    ``DEVBENCH_ORPHAN_IGNORE_PATTERNS`` (comma-separated fnmatch globs)
    when a backlog needs different shapes.

    The repo argument accepts either an ``org/repo`` form (resolved
    against ``devbench.yaml``'s ``repos:`` map for its
    ``checkout_directory``) or a direct filesystem path to a git repo.
    """
    from devbench.git_orphans import cleanup_tracked_orphans

    dry_run = dry_run_flag == "--dry-run"
    repo_path = _resolve_orphan_repo_path(repo_or_path)
    if repo_path is None:
        print(
            f"ERROR: cannot resolve repo path for {repo_or_path!r}; "
            "expected either an 'org/repo' key from devbench.yaml or a "
            "filesystem path to a git repo.",
            file=sys.stderr,
        )
        return 1
    try:
        report = cleanup_tracked_orphans(repo_path, dry_run=dry_run)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "repo": str(report.repo_path),
                "detected_count": len(report.detected),
                "removed_count": len(report.removed),
                "detected": report.detected,
                "removed": report.removed,
                "gitignore_path": str(report.gitignore_path),
                "gitignore_updated": report.gitignore_updated,
                "dry_run": report.dry_run,
            },
            indent=2,
        )
    )
    return 0


def _resolve_orphan_repo_path(arg: str) -> Path | None:
    """Resolve an ``org/repo`` key or filesystem path to a git repo root.

    Returns ``None`` when the argument resolves to neither.
    """
    direct = Path(arg)
    if direct.is_dir() and (direct / ".git").exists():
        return direct
    if "/" in arg:
        repo_path = REPO_LOCAL_PATHS.get(arg)
        if repo_path is not None and (repo_path / ".git").exists():
            return repo_path
    return None


def _refuse_unscoped_commit(unit_id: str, wu_file: Path | None) -> bool:
    """Return True (and explain) when the commit cannot be scoped to a Manifest.

    The Changes Manifest is the only thing that bounds what a work unit's commit
    may contain. Without it, staging would absorb any other in-flight unit's
    changes under this unit's message, which leaves the victim task permanently
    unable to pass ``changes_manifest``.
    Refusing is recoverable; committing an unknown scope is not.
    """
    if wu_file is not None:
        return False
    print(
        f"ERROR: cannot resolve the work-unit file for {unit_id}; refusing to commit "
        "because its Changes Manifest is the only thing that scopes the commit.",
        file=sys.stderr,
    )
    return True


def _unit_expects_no_output(wu_file: Path | None, manifest_files: list[str]) -> bool:
    """Return ``True`` when this unit declares it produces no commit.

    Requires BOTH the explicit ``## Expected Output: none`` declaration and a
    Manifest of only no-output sentinels. Requiring both means a Manifest that
    happens to be sentinel-only never silently changes a legacy unit's
    lifecycle: a backlog authored before this section existed declares nothing,
    resolves to the ``commit`` default, and keeps its current behaviour exactly.
    validate-backlog rule 28 rejects any disagreement between the two at
    authoring time.
    """
    if wu_file is None:
        return False
    from devbench.backlog.sentinels import is_no_output_manifest

    declared = BacklogManager._extract_expected_output(wu_file.read_text(encoding="utf-8"))
    return declared == EXPECTED_OUTPUT_NONE and is_no_output_manifest(manifest_files)


def _git_ops_pre_commit_outcome(unit_id: str, unit: WorkUnit, wu_file: Path | None, repo_path: Path) -> int | None:
    """Return an exit code when git-ops must stop before committing, else ``None``.

    Collapses the three independent pre-commit outcomes into one decision so the
    caller has a single early exit:

    - orphan-pattern pollution in the target repo (emits a cleanup proposal)
    - an unscopeable commit (no resolvable Changes Manifest)
    - a unit declaring ``## Expected Output: none``, which completes with no
      commit, push, PR, CI wait, or merge (rule 28, ADR-35)
    """
    from devbench.backlog.manifest import parse_manifest

    if _emit_orphan_cleanup_proposal_if_needed(unit_id, unit, repo_path) or _refuse_unscoped_commit(unit_id, wu_file):
        return 1
    assert wu_file is not None  # narrowed by _refuse_unscoped_commit above
    manifest_files = [r.file for r in parse_manifest(wu_file.read_text(encoding="utf-8"))]
    if _unit_expects_no_output(wu_file, manifest_files):
        return _complete_no_output_unit(unit_id, wu_file, repo_path, manifest_files)
    return None


def _complete_no_output_unit(unit_id: str, wu_file: Path | None, repo_path: Path, manifest_files: list[str]) -> int:
    """Complete a ``## Expected Output: none`` unit without a commit (ADR-35).

    A verification / decision / no-op unit declares that it modifies no source
    file and records its evidence in ``## Comments``. There is nothing to stage,
    so commit, push, PR, CI and merge are all skipped and the unit completes.

    One refusal guards the dangerous direction: if anything is already STAGED,
    the unit contradicts its own declaration and completing would silently
    discard real work, so this returns 1 loudly instead. The check is on the
    staged set rather than a clean working tree on purpose -- tooling that
    rewrites a lockfile on any invocation (``uv run`` rewriting ``uv.lock``, for
    example) leaves unstaged drift that is a pre-existing repository condition,
    not this unit's output, and must not block it.

    The working-tree state is recorded in the audit comment either way, so the
    path taken is always observable rather than silent.
    """
    _, staged_out, _ = run_command(["git", "diff", "--cached", "--name-only"], cwd=repo_path)
    if staged_out.strip():
        staged_list = ", ".join(staged_out.split())
        print(
            f"ERROR: {unit_id} declares '## Expected Output: none' but has staged changes "
            f"({staged_list}). Completing it without a commit would discard them. Either unstage "
            f"them, or declare '## Expected Output: commit' and list the paths in the Changes "
            f"Manifest.",
            file=sys.stderr,
        )
        return 1

    _, status_out, _ = run_command(["git", "status", "--porcelain"], cwd=repo_path)
    tree_state = "dirty" if status_out.strip() else "clean"
    declared = ", ".join(manifest_files) or "(empty)"
    logger.info(
        "[GIT_OPS_NO_OUTPUT] %s completed without a commit; Manifest declares %s; working tree %s",
        unit_id,
        declared,
        tree_state,
    )
    if wu_file is not None:
        BacklogManager()._append_agent_comment(
            wu_file,
            "git_ops",
            f"[GIT_OPS_NO_OUTPUT] completed without a commit, push, PR, or merge; "
            f"Manifest declares {declared}; working tree {tree_state}; "
            f"evidence recorded in ## Comments.",
        )
    return 0


def cmd_git_ops(unit_id: str) -> int:
    """Run the full git operations sequence for a completed work unit.

    Sequence:
    1. Resolve repo and local path from the work unit.
    2. Determine branch name from work unit ID (``backlog/<id-lower>``).
    3. Commit and push staged changes.
    4. Create a pull request.
    5. Wait for CI checks to pass.
    6. Merge the pull request.
    7. Update the parent submodule reference (only when ``UPDATE_SUBMODULE`` is ``True``).

    Used by the orchestrate skill after all review judges have passed.
    """
    from devbench.github.git_ops import GitOpsService

    unit, canonical_repo, repo_path = _resolve_git_ops_context(unit_id)

    from devbench.config import DEFER_PR, SINGLE_BRANCH

    branch_prefix = get_effective_branch_prefix(canonical_repo, RUNTIME_CONFIG)
    branch = (
        format_single_branch_name(SINGLE_BRANCH, branch_prefix)
        if SINGLE_BRANCH
        else format_branch_name(unit_id, branch_prefix)
    )

    if DEFER_PR:
        return _git_ops_deferred(unit_id, unit, canonical_repo, repo_path, branch)

    # Standard mode: commit, push, PR, CI, merge.
    commit_message = f"{unit_id}: {unit.title}"
    pr_title = f"{unit_id}: {unit.title}"
    pr_body = f"Automated PR for work unit {unit_id}.\n\n{unit.title}"

    ops = GitOpsService()

    wu_file = _resolve_unit_file(unit)
    mgr = BacklogManager()

    from devbench.backlog.manifest import assert_staged_matches_manifest, parse_manifest

    # Every reason git-ops stops before committing, resolved in one place: orphan-pattern
    # pollution (see _git_ops_deferred for rationale, auto-emits a cleanup proposal), an
    # unscopeable commit, and a unit that declares it produces no commit at all.
    pre_commit_rc = _git_ops_pre_commit_outcome(unit_id, unit, wu_file, repo_path)
    if pre_commit_rc is not None:
        return pre_commit_rc

    # Manifest-scope check: every staged path must be in the work unit's Changes
    # Manifest. Catches scope-violation pollution deterministically before the
    # commit, and supplies the pathspec that scopes the commit itself.
    assert wu_file is not None  # narrowed by _git_ops_pre_commit_outcome above
    manifest_files = [r.file for r in parse_manifest(wu_file.read_text(encoding="utf-8"))]
    assert_staged_matches_manifest(repo_path, manifest_files)

    ops.commit_and_push(canonical_repo, repo_path, branch, commit_message, manifest_files=manifest_files)
    logger.info("Committed and pushed %s", unit_id)

    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[PR_CREATED] {pr_url}")
    from devbench.notifications import notify_pr_opened

    notify_pr_opened(unit_id, canonical_repo, pr_url)

    # Extract PR number from URL (e.g. https://github.com/org/repo/pull/42)
    pr_number_str = pr_url.rstrip("/").split("/")[-1]
    if not pr_number_str.isdigit():
        print(f"ERROR: Could not parse PR number from URL: {pr_url}", file=sys.stderr)
        return 1
    pr_number = int(pr_number_str)

    from devbench.github.git_ops import CIResult

    ci_result = ops.wait_for_checks_and_classify(pr_url, repo_path)
    if ci_result is not CIResult.GREEN:
        return _handle_ci_failure(
            ops=ops,
            unit_id=unit_id,
            canonical_repo=canonical_repo,
            pr_number=pr_number,
            repo_path=repo_path,
            wu_file=wu_file,
            mgr=mgr,
        )

    # Issue #116: PR review-comment polling phase. Returns non-zero rc=3 when
    # an agent in the configured allowlist requests changes; the executor retry
    # loop handles re-invocation. No-op (immediate merge) when the phase is
    # disabled OR no signals are configured.
    review_rc = _handle_pr_review_resolution(
        ops=ops,
        unit_id=unit_id,
        canonical_repo=canonical_repo,
        pr_number=pr_number,
        repo_path=repo_path,
        wu_file=wu_file,
        mgr=mgr,
    )
    if review_rc != 0:
        return review_rc

    return _dispatch_post_ci_pass(
        ops=ops,
        unit_id=unit_id,
        canonical_repo=canonical_repo,
        repo_path=repo_path,
        branch=branch,
        pr_number=pr_number,
        pr_url=pr_url,
        wu_file=wu_file,
        mgr=mgr,
    )


def _dispatch_post_ci_pass(
    *,
    ops: GitOpsService,
    unit_id: str,
    canonical_repo: str,
    repo_path: Path,
    branch: str,
    pr_number: int,
    pr_url: str,
    wu_file: Path | None,
    mgr: BacklogManager,
) -> int:
    """Issue #101 dispatch: pause-before-merge vs merge-now.

    Extracted so :func:`cmd_git_ops` stays under the per-function
    return-statement budget (PLR0911).
    """
    from devbench.config import PAUSE_BEFORE_MERGE

    if PAUSE_BEFORE_MERGE:
        return _pause_before_merge(
            unit_id=unit_id,
            pr_number=pr_number,
            pr_url=pr_url,
            wu_file=wu_file,
            mgr=mgr,
        )

    return _finalize_merge_and_submodule(
        ops=ops,
        unit_id=unit_id,
        canonical_repo=canonical_repo,
        repo_path=repo_path,
        branch=branch,
        pr_number=pr_number,
        pr_url=pr_url,
        wu_file=wu_file,
        mgr=mgr,
    )


def _pause_before_merge(
    *,
    unit_id: str,
    pr_number: int,
    pr_url: str,
    wu_file: Path | None,
    mgr: BacklogManager,
) -> int:
    """Issue #101: transition to in-review, do NOT merge.

    The orchestrator's loop reconciles in-review tasks via
    :func:`cmd_check_merge` on the next iteration. The PR remains open
    on GitHub awaiting human review + merge; the orchestrator continues
    with other actionable work units.

    Returns 0 -- the work unit moved successfully to in-review.
    """
    if wu_file is not None:
        mgr.force_status(
            wu_file,
            BACKLOG_INDEX,
            unit_id,
            STATUS_IN_REVIEW,
        )
        mgr._append_agent_comment(
            wu_file,
            "git_ops",
            f"[PR_AWAITING_MERGE] PR #{pr_number} open and awaiting human review + merge: {pr_url}",
        )
    logger.info(
        "Pause-before-merge: %s transitioned to in-review (PR #%d %s)",
        unit_id,
        pr_number,
        pr_url,
    )
    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "status": STATUS_IN_REVIEW,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "mode": "pause-before-merge",
            }
        )
    )
    return 0


def _check_merge_fetch_pr_state(
    ops: GitOpsService,
    canonical_repo: str,
    unit_id: str,
    branch: str,
) -> tuple[int, list[dict[str, object]]]:
    """Query gh for a PR matching *branch*; return (rc, pr_records).

    rc=1 + empty list means the gh call failed; the caller short-circuits
    with rc=1. rc=0 + empty list means no PR found for the branch (caller
    treats as still-in-review with `pr_state=no-pr-found`).
    """
    rc, stdout, stderr = ops._gh(
        ["pr", "list", "--head", branch, "--state", "all", "--json", "number,state,mergedAt,url"],
        repo=canonical_repo,
    )
    if rc != 0:
        print(
            f"ERROR: gh pr list failed for {unit_id} (head={branch}): {stderr.strip()}",
            file=sys.stderr,
        )
        return 1, []
    try:
        pr_records = json.loads(stdout) if stdout.strip() else []
    except json.JSONDecodeError as exc:
        print(f"ERROR: gh pr list returned invalid JSON for {unit_id}: {exc}", file=sys.stderr)
        return 1, []
    if not isinstance(pr_records, list):
        return 0, []
    return 0, [r for r in pr_records if isinstance(r, dict)]


def _check_merge_handle_merged(
    *,
    unit_id: str,
    pr_number: object,
    pr_url: str,
    wu_file: Path | None,
    mgr: BacklogManager,
) -> int:
    """Promote a merged-PR work unit to done via the done-gate."""
    if wu_file is not None:
        try:
            mgr.mark_done(wu_file, BACKLOG_INDEX, unit_id)
            mgr._append_agent_comment(
                wu_file,
                "git_ops",
                f"[PR_MERGED] PR #{pr_number} merged externally; transitioned to done: {pr_url}",
            )
        except RuntimeError as exc:
            print(
                f"ERROR: cannot mark {unit_id} done after merge -- done-gate refused: {exc}",
                file=sys.stderr,
            )
            return 1
    print(json.dumps({"unit_id": unit_id, "status": STATUS_DONE, "pr_number": pr_number, "pr_url": pr_url}))
    return 0


def cmd_check_merge(unit_id: str) -> int:
    """Reconcile a pause-before-merge work unit's PR state.

    Issue #101 reconciliation step. Queries the PR for *unit_id* via
    ``gh pr list --head <branch> --json number,state,mergedAt``:

    - **merged** (``mergedAt`` non-null) -> transition to ``done`` via
      :meth:`BacklogManager.mark_done`, which enforces two things before
      any status write happens: the done-gate (every required judge must
      have passed in the most recent review round) *and* the task's own
      FR-4.5/FR-4.6 task-type completion invariant
      (:meth:`BacklogManager._check_task_type_done_invariant` -- a
      machine-observed ``RED_OBSERVED`` record for gated task types, or a
      machine-observed ``GREEN_GREEN_OBSERVED`` record for ``refactor``).
      This surface is not a thinner variant of ``devbench mark-done``'s
      checks: both refuse identically because both call the same
      ``mark_done`` method (doc_review FAIL, E4-F4-S1-T2 round 4 -- prior
      wording here named only the judge gate, which undersold the FR-4.5/
      FR-4.6 refusal ``check-merge`` also performs).
    - **closed without merge** -> transition to ``blocked`` with an audit
      comment naming the PR.
    - **still open** -> log + return 0; orchestrator picks it up next loop.

    Returns 0 in every case (the orchestrator's loop reads the JSON
    output, not rc, to decide what to do next). Returns 1 only on hard
    failure (cannot resolve work unit, gh API failure that prevents a
    decision, done-gate refusal).
    """
    from devbench.github.git_ops import GitOpsService

    unit, canonical_repo, _repo_path = _resolve_git_ops_context(unit_id)
    wu_file = _resolve_unit_file(unit)
    mgr = BacklogManager()
    ops = GitOpsService()

    branch = unit.branch or format_branch_name(unit_id, get_effective_branch_prefix(canonical_repo, RUNTIME_CONFIG))
    fetch_rc, pr_records = _check_merge_fetch_pr_state(ops, canonical_repo, unit_id, branch)
    if fetch_rc != 0:
        return fetch_rc

    if not pr_records:
        print(json.dumps({"unit_id": unit_id, "status": STATUS_IN_REVIEW, "pr_state": "no-pr-found"}))
        return 0

    pr = pr_records[0]
    pr_number = pr.get("number")
    pr_state = str(pr.get("state") or "").upper()
    pr_merged = pr.get("mergedAt") is not None
    pr_url = str(pr.get("url") or "")

    if pr_merged:
        return _check_merge_handle_merged(unit_id=unit_id, pr_number=pr_number, pr_url=pr_url, wu_file=wu_file, mgr=mgr)

    if pr_state == "CLOSED":
        if wu_file is not None:
            mgr.mark_blocked(
                wu_file,
                BACKLOG_INDEX,
                unit_id,
                f"PR #{pr_number} closed without merge: {pr_url}",
            )
        print(json.dumps({"unit_id": unit_id, "status": STATUS_BLOCKED, "pr_number": pr_number, "pr_url": pr_url}))
        return 0

    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "status": STATUS_IN_REVIEW,
                "pr_number": pr_number,
                "pr_state": pr_state,
                "pr_url": pr_url,
            }
        )
    )
    return 0


def _finalize_merge_and_submodule(
    *,
    ops: GitOpsService,
    unit_id: str,
    canonical_repo: str,
    repo_path: Path,
    branch: str,
    pr_number: int,
    pr_url: str,
    wu_file: Path | None,
    mgr: BacklogManager,
) -> int:
    """Merge the PR (with one CONFLICTING-state retry) and update the parent submodule.

    Extracted from :func:`cmd_git_ops` so the parent function stays under
    the project's per-function return-statement budget (PLR0911).
    Returns 0 on success or 1 when the merge retry path itself fails.
    """
    from devbench.github.git_ops import ConflictingPRError

    try:
        ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)
    except ConflictingPRError:
        logger.warning(
            "PR #%d on %s is CONFLICTING -- rebasing and retrying merge once",
            pr_number,
            canonical_repo,
        )
        ops.rebase_and_force_push(canonical_repo, repo_path, branch)
        try:
            ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)
        except Exception as retry_exc:
            print(
                f"ERROR: Merge retry failed for PR #{pr_number} on {canonical_repo}: {retry_exc}",
                file=sys.stderr,
            )
            return 1

    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[PR_MERGED] {pr_url}")
    from devbench.notifications import notify_pr_merged

    notify_pr_merged(unit_id, canonical_repo, pr_url)

    logger.info("Merged PR #%d for %s", pr_number, unit_id)

    ops.checkout_default_branch(canonical_repo, repo_path)
    logger.info("Checked out default branch after merge for %s", unit_id)

    if UPDATE_SUBMODULE:
        ops.update_parent_submodule_ref(
            canonical_repo,
            repo_path,
            f"chore: update {repo_path.name} submodule after {unit_id}",
        )

    print(json.dumps({"unit_id": unit_id, "pr_url": pr_url, "pr_number": pr_number}))
    return 0


def _find_most_recent_active_task(units: list[WorkUnit]) -> WorkUnit | None:
    """Return the last in-review or done task in *units* (iteration order).

    Used by :func:`_handle_finalize_ci_result` for FAILED_UNKNOWN attribution
    (blame the most recently promoted task when the failure cannot be pinned to
    a specific task marker in the CI log).  Returns ``None`` when no
    in-review / done tasks are present.
    """
    candidate = None
    for unit in units:
        if unit.status in (WorkUnitStatus.IN_REVIEW, WorkUnitStatus.DONE):
            candidate = unit
    return candidate


def _finalize_audit_and_block(
    unit: WorkUnit,
    task_id: str,
    marker: str,
    mgr: BacklogManager,
) -> None:
    """Append *marker* as an audit comment and transition *task_id* to blocked.

    No-op when the work-unit file cannot be resolved.  Extracted to reduce
    branch count in :func:`_handle_finalize_ci_result` (PLR0912 compliance).
    """
    wu_file = _resolve_unit_file(unit)
    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", marker)
        mgr.force_status(wu_file, BACKLOG_INDEX, task_id, STATUS_BLOCKED)


def _finalize_build_recovery_proposal(task_id: str, pr_url: str) -> Proposal:
    """Construct a recovery :class:`Proposal` for *task_id* blamed on *pr_url*.

    Separated from :func:`_handle_finalize_ci_result` so the branch count
    stays within PLR0912's limit.
    """
    import datetime

    from devbench.backlog.proposal import ProposedTask

    generated_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Proposal(
        source_task_id=task_id,
        generated_at=generated_at,
        rejection_reason=f"CI failure on single-branch PR {pr_url}; attributed to task {task_id}",
        proposed_tasks=[
            ProposedTask(
                suggested_id=f"{task_id}-recovery",
                title=f"Investigate CI failure attributed to {task_id}",
                files_to_own=[],
                linked_scenarios=[],
                suggested_acs=[
                    f"AC-001 Identify and fix the CI failure introduced by {task_id}",
                ],
                suggested_approach=(
                    f"RED: reproduce the CI failure logged from PR {pr_url}. "
                    f"GREEN: apply the minimal fix. REFACTOR: verify the suite stays green."
                ),
            )
        ],
    )


def _handle_finalize_known_task_failure(
    *,
    named_task_id: str,
    named_unit: WorkUnit | None,
    pr_url: str,
    mgr: BacklogManager,
) -> int:
    """Resolve FAILED_KNOWN_TASK: cascade-cap check, proposal write, and block.

    Returns 2 in all cases (cascade-capped or not).  Separated from
    :func:`_handle_finalize_ci_result` to reduce its branch count.
    """
    # Cascade-depth cap: check if the first-level recovery (depth=1) would
    # exceed MAX_CASCADE_DEPTH.  With MAX_CASCADE_DEPTH=1, even depth=1
    # is capped (1 >= 1); with MAX_CASCADE_DEPTH=N, up to N-1 recovery
    # levels are allowed.
    try:
        enforce_cascade_depth({"cascade_depth": 1}, MAX_CASCADE_DEPTH)
    except CascadeDepthError:
        if named_unit is not None:
            _finalize_audit_and_block(
                named_unit,
                named_task_id,
                f"[CI_FAILED_CASCADE_CAPPED] cascade_depth limit reached; {pr_url}",
                mgr,
            )
        logger.error(
            "cmd_git_ops_finalize: cascade cap reached for %s; no proposal written",
            named_task_id,
        )
        return 2

    proposal = _finalize_build_recovery_proposal(named_task_id, pr_url)
    try:
        write_proposal(WORKSPACE_ROOT, proposal)
    except ProposalError as exc:
        logger.warning("cmd_git_ops_finalize: could not write proposal for %s: %s", named_task_id, exc)

    if named_unit is not None:
        _finalize_audit_and_block(named_unit, named_task_id, f"[CI_FAILED_BATCH_PR] {pr_url}", mgr)

    logger.error(
        "cmd_git_ops_finalize: CI failure attributed to %s; proposal written, task blocked",
        named_task_id,
    )
    return 2


def _handle_finalize_ci_result(
    *,
    ci_result: object,
    pr_url: str,
    mgr: BacklogManager,
    repo: str,
) -> int:
    """Resolve a CIResult from :func:`cmd_git_ops_finalize` into an exit code.

    Implements the four-branch dispatch described in E7-F2-S1-T1:

    - ``CIResult.GREEN``: log ``[CI_GREEN]`` audit on the most-recent active
      task; return 0.  The PR remains open for human merge.
    - ``CIResult.FAILED_KNOWN_TASK(task_id)``: write a recovery-proposal JSON
      blamed on *task_id*; transition that task to ``blocked`` with a
      ``[CI_FAILED_BATCH_PR]`` audit comment; return 2.  Respects
      ``orchestrate.max_cascade_depth`` -- when the depth cap is reached,
      skip the proposal and log ``[CI_FAILED_CASCADE_CAPPED]`` instead.
    - ``CIResult.FAILED_UNKNOWN``: transition the most-recent in-review /
      done task to ``blocked`` with ``[CI_FAILED_BATCH_PR]``; return 2.
    - ``CIResult.TIMEOUT``: log ``[CI_WATCH_TIMEOUT]`` on the most-recent
      active task; return 2 without changing any task statuses.
    """
    from devbench.github.git_ops import CIResult

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    most_recent = _find_most_recent_active_task(units)

    if ci_result is CIResult.GREEN:
        if most_recent is not None:
            wu_file = _resolve_unit_file(most_recent)
            if wu_file is not None:
                mgr._append_agent_comment(wu_file, "git_ops", f"[CI_GREEN] {pr_url}")
        # Issue #219: fire `ci_pass` Slack ping so operators running
        # ``auto_merge: false`` get an explicit "PR ready for manual merge"
        # signal.  Default-off toggle (off in the schema) keeps existing
        # workspaces silent on upgrade.
        from devbench.notifications import notify_ci_pass

        notify_unit_id = most_recent.id if most_recent is not None else "finalize"
        notify_ci_pass(notify_unit_id, repo, pr_url)
        logger.info("cmd_git_ops_finalize: CI GREEN for %s", pr_url)
        return 0

    if ci_result is CIResult.TIMEOUT:
        if most_recent is not None:
            wu_file = _resolve_unit_file(most_recent)
            if wu_file is not None:
                mgr._append_agent_comment(wu_file, "git_ops", f"[CI_WATCH_TIMEOUT] {pr_url}")
        logger.warning("cmd_git_ops_finalize: CI watch timed out for %s", pr_url)
        return 2

    if isinstance(ci_result, CIResult.FAILED_KNOWN_TASK):
        # Issue #219: fire `ci_failure` Slack ping at the dispatch point so
        # the call happens regardless of the cascade-cap branch inside the
        # known-task helper.  Attempt sentinel = 1 (the finalize path has no
        # retry counter today; documented for future enhancement).
        from devbench.notifications import notify_ci_failure

        notify_ci_failure(ci_result.task_id, repo, pr_url, 1)
        return _handle_finalize_known_task_failure(
            named_task_id=ci_result.task_id,
            named_unit=_find_unit(units, ci_result.task_id),
            pr_url=pr_url,
            mgr=mgr,
        )

    # FAILED_UNKNOWN: block the most-recent active task.
    if most_recent is not None:
        _finalize_audit_and_block(
            most_recent,
            most_recent.id,
            f"[CI_FAILED_BATCH_PR] {pr_url} (unknown attribution)",
            mgr,
        )
    # Issue #219: fire `ci_failure` Slack ping even on unknown-attribution
    # failures so the operator knows the batch PR's CI failed.  Use the
    # most-recent active task as the representative unit; fall back to the
    # symbolic "finalize" sentinel when no WU is in flight.
    from devbench.notifications import notify_ci_failure

    notify_unit_id = most_recent.id if most_recent is not None else "finalize"
    notify_ci_failure(notify_unit_id, repo, pr_url, 1)

    logger.error(
        "cmd_git_ops_finalize: CI failure with unknown attribution; blocked %s",
        most_recent.id if most_recent else "no task",
    )
    return 2


def _parse_git_ops_finalize_argv(argv: tuple[str, ...] | list[str]) -> tuple[str, str] | int:
    """Parse ``git-ops-finalize <repo> [--provenance <path>]`` argv.

    Returns a ``(repo_name, provenance_flag)`` tuple on success, where
    ``provenance_flag`` is the empty string when ``--provenance`` was not
    passed (D-17: the flag is optional -- ``git_ops.provenance_path`` alone
    suffices for unattended ``auto_finalize`` runs). Returns an integer
    non-zero exit code on parse error, with the error message already
    written to stderr.
    """
    repo_name = ""
    provenance_flag = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--provenance":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --provenance requires a value", file=sys.stderr)
                return 1
            provenance_flag = args[i + 1]
            i += 2
            continue
        if not repo_name:
            repo_name = arg
            i += 1
            continue
        print(f"ERROR: unexpected argument {arg!r}", file=sys.stderr)
        return 1
    if not repo_name:
        print("ERROR: git-ops-finalize requires <repo> [--provenance <path>]", file=sys.stderr)
        return 1
    return repo_name, provenance_flag


def cmd_git_ops_finalize(*argv: str) -> int:
    """Push the single branch and create a PR after all deferred commits.

    Usage::

        git-ops-finalize <repo> [--provenance <path>]

    Used after all work units are complete in single-branch / defer-PR mode.
    Pushes the accumulated commits to the remote and creates a pull request.
    After the PR is created, waits for CI checks via
    :meth:`~devbench.github.git_ops.GitOpsService.wait_for_checks_and_classify`
    and resolves the four CIResult branches:

    - GREEN: logs ``[CI_GREEN]`` and returns 0.  The PR stays open.
    - FAILED_KNOWN_TASK: writes a recovery proposal, blocks the named task,
      returns 2.
    - FAILED_UNKNOWN: blocks the most-recent in-review / done task, returns 2.
    - TIMEOUT: logs ``[CI_WATCH_TIMEOUT]`` and returns 2 without status changes.

    The PR body is composed by
    :meth:`~devbench.github.git_ops.GitOpsService.compose_finalize_pr_body`
    (spec 4.13; D-17). ``--provenance <path>`` overrides
    ``git_ops.provenance_path`` for this single invocation; when neither is
    set, the body is the plain body ``git-ops-finalize`` has always
    produced. An unresolvable provenance map (missing, unreadable, invalid
    JSON, or zero mapped issues) fails loudly (exit 1, naming the path)
    BEFORE any push happens -- it never silently falls back to the plain
    body.

    Arguments:
        argv: ``<repo>`` (required; short or fully-qualified) followed by
            an optional ``--provenance <path>`` flag.
    """
    parsed = _parse_git_ops_finalize_argv(argv)
    if isinstance(parsed, int):
        return parsed
    repo_name, provenance_flag = parsed

    from devbench.config import DEFER_PR, SINGLE_BRANCH

    if not SINGLE_BRANCH:
        print(
            "ERROR: git-ops-finalize requires git_ops.single_branch to be set in devbench.yaml",
            file=sys.stderr,
        )
        return 1
    if not DEFER_PR:
        print(
            "ERROR: git-ops-finalize requires git_ops.defer_pr to be true in devbench.yaml",
            file=sys.stderr,
        )
        return 1

    from devbench.github.git_ops import GitOpsService

    canonical_repo = resolve_repo(repo_name)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    branch = format_single_branch_name(SINGLE_BRANCH, get_effective_branch_prefix(canonical_repo, RUNTIME_CONFIG))
    pr_title = FINALIZE_PR_TITLE_TEMPLATE.format(branch=branch)

    # D-17: --provenance beats git_ops.provenance_path beats the plain-body
    # default (None). No DEVBENCH_* env override exists for this key.
    # GitOpsConfig.provenance_path is documented as "relative to the repo
    # working tree, or absolute" -- anchor a relative value to repo_path
    # (already resolved above), matching the sibling repo-scoped calls below
    # (commit_and_push, find_open_pr, create_pr) rather than resolving
    # against the devbench process CWD, which is the workspace root under an
    # unattended auto_finalize run and would silently point at the wrong
    # file in a multi-repo workspace.
    effective_provenance_raw = provenance_flag or RUNTIME_CONFIG.git_ops.provenance_path
    provenance_path: Path | None = None
    if effective_provenance_raw:
        raw_provenance_path = Path(effective_provenance_raw)
        provenance_path = raw_provenance_path if raw_provenance_path.is_absolute() else repo_path / raw_provenance_path

    ops = GitOpsService()

    try:
        pr_body = ops.compose_finalize_pr_body(
            repo=canonical_repo,
            branch=branch,
            title=pr_title,
            provenance_path=provenance_path,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mgr = BacklogManager()

    ops.commit_and_push(
        canonical_repo,
        repo_path,
        branch,
        FINALIZE_COMMIT_TEMPLATE.format(branch=branch),
        # The finalize commit batches every work unit already committed on
        # this branch; it has no single Changes Manifest to scope by, so the
        # whole tree is staged deliberately rather than by omission.
        stage_all=True,
    )
    logger.info("Pushed branch %s to %s", branch, canonical_repo)

    # Issue #220: probe for an existing open PR BEFORE calling create_pr so
    # we can distinguish fresh creation from re-encountering an
    # already-open PR.  Without this check, every restart of a finalize
    # cycle that hits an already-pushed branch fires a misleading
    # ":git: PR opened" Slack ping even though nothing was opened.
    pre_existing_pr = ops.find_open_pr(canonical_repo, branch, repo_path=repo_path)
    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

    # Issue #219 + #220: fire ``pr_opened`` Slack ping ONLY when this run
    # actually created the PR (pre_existing_pr was None).  The batch PR
    # carries every WU in the single-branch run, so there is no single
    # ``unit_id`` -- use the most-recent active task as the
    # representative, falling back to the symbolic "finalize" sentinel
    # when no WU is in flight.
    if pre_existing_pr is None:
        parser_for_notify = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units_for_notify = parser_for_notify.parse_index()
        representative = _find_most_recent_active_task(units_for_notify)
        notify_unit_id = representative.id if representative is not None else "finalize"
        from devbench.notifications import notify_pr_opened

        notify_pr_opened(notify_unit_id, canonical_repo, pr_url)

    ci_result = ops.wait_for_checks_and_classify(pr_url, repo_path)

    return _handle_finalize_ci_result(
        ci_result=ci_result,
        pr_url=pr_url,
        mgr=mgr,
        repo=canonical_repo,
    )


def cmd_watch(watch_interval: int = 0) -> int:
    """Print a live dashboard of the currently-active orchestration.

    Runs once and exits (snapshot mode) when ``watch_interval`` is ``0``.
    Otherwise enters a refresh loop that prints a fresh snapshot every
    ``watch_interval`` seconds and clears the terminal between frames, the
    same pattern ``cmd_report`` uses. ``KeyboardInterrupt`` cleanly exits 0.
    """
    from devbench.activity import collect_snapshot, render_snapshot

    # Canonical resolver (env -> yaml -> logs/orchestrator.log default) so
    # `watch`, the orchestrator writer, and `report` all read/write the same
    # file (previously this hand-rolled the path and ignored yaml log_file).
    log_file = _resolve_log_file_path()
    hook_log = WORKSPACE_ROOT / "hook-logs.jsonl"

    def _resolver(repo_name: str) -> Path | None:
        try:
            canonical = resolve_repo(repo_name)
        except ValueError:
            return None
        return REPO_LOCAL_PATHS.get(canonical)

    def _render_once() -> None:
        snapshot = collect_snapshot(
            workspace_root=WORKSPACE_ROOT,
            backlog_index=BACKLOG_INDEX,
            runtime_config=RUNTIME_CONFIG,
            orchestrator_log=log_file,
            hook_log=hook_log,
            repo_path_resolver=_resolver,
        )
        print(render_snapshot(snapshot))

    if watch_interval == 0:
        _render_once()
        return 0

    import time

    try:
        while True:
            if _TERMINAL_CLEAR_CMD:
                subprocess.run([_TERMINAL_CLEAR_CMD], check=False)
            else:
                print("\033c", end="", flush=True)
            _render_once()
            time.sleep(watch_interval)
    except KeyboardInterrupt:
        return 0


@dataclass(frozen=True)
class _HookTailArgs:
    tz_name: str | None = None
    no_follow: bool = False
    from_start: bool = False
    path_override: str = ""
    orchestrator_session_id: str | None = None
    orchestrator_only: bool = False


def _parse_hook_tail_argv(argv: tuple[str, ...] | list[str]) -> _HookTailArgs | int:
    """Parse ``hook-tail`` argv. Returns the args bundle or a non-zero exit code."""
    tz_name: str | None = None
    no_follow = False
    from_start = False
    path_override = ""
    orchestrator_session_id: str | None = None
    orchestrator_only = False
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--tz":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --tz requires a value", file=sys.stderr)
                return 2
            tz_name = args[i + 1]
            i += 2
            continue
        if arg == "--no-follow":
            no_follow = True
            i += 1
            continue
        if arg == "--from-start":
            from_start = True
            i += 1
            continue
        if arg == "--orchestrator-only":
            orchestrator_only = True
            i += 1
            continue
        if arg == "--orchestrator-session":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --orchestrator-session requires a value", file=sys.stderr)
                return 2
            orchestrator_session_id = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 2
        if not path_override:
            path_override = arg
            i += 1
            continue
        print(f"ERROR: unexpected positional argument: {arg}", file=sys.stderr)
        return 2
    return _HookTailArgs(
        tz_name=tz_name,
        no_follow=no_follow,
        from_start=from_start,
        path_override=path_override,
        orchestrator_session_id=orchestrator_session_id,
        orchestrator_only=orchestrator_only,
    )


def cmd_hook_tail(*argv: str) -> int:
    """Pretty-tail the plugin's hook-logs.jsonl stream.

    Usage::

        hook-tail [<path>] [--tz <zoneinfo-name>] [--no-follow] [--from-start]
                  [--orchestrator-only | --orchestrator-session <id>]

    Defaults ``<path>`` to ``$DEVBENCH_WORKSPACE_ROOT/hook-logs.jsonl`` (the same
    location ``devbench watch`` reads from). Renders timestamps in the OS
    local timezone; ``--tz`` overrides with any IANA zoneinfo name. Disables
    ANSI color when ``NO_COLOR`` is set or stdout is not a TTY.

    Phase 11 (E230) session filter:
      ``--orchestrator-only`` filters the stream to events whose
      ``orchestrator_session`` field equals
      ``$DEVBENCH_ORCHESTRATOR_SESSION_ID`` (set by the launch command on
      the orchestrator's pane). Events from side-pane Claude sessions
      are silently suppressed. ``--orchestrator-session <id>`` provides
      the same filter with an explicit value (useful for ad-hoc
      audits). Older log entries that pre-date the session field are
      passed through unfiltered.
    """
    from devbench.hook_tail import (
        FollowOptions,
        InvalidTimezoneError,
        follow,
        render_header,
        resolve_timezone,
        should_use_color,
    )

    parsed = _parse_hook_tail_argv(argv)
    if isinstance(parsed, int):
        return parsed
    tz_name = parsed.tz_name
    no_follow = parsed.no_follow
    from_start = parsed.from_start
    path_override = parsed.path_override
    orchestrator_session_id = parsed.orchestrator_session_id

    if parsed.orchestrator_only and orchestrator_session_id is None:
        env_session = (_read_env("DEVBENCH_ORCHESTRATOR_SESSION_ID") or "").strip()
        if not env_session:
            print(
                "ERROR: --orchestrator-only requires DEVBENCH_ORCHESTRATOR_SESSION_ID env "
                "to be set, OR pass --orchestrator-session <id> explicitly.",
                file=sys.stderr,
            )
            return 2
        orchestrator_session_id = env_session

    # Precedence: CLI --tz > DEVBENCH_DISPLAY_TIMEZONE env > yaml display_timezone
    # > OS local. DISPLAY_TIMEZONE encodes (env > yaml); resolve_timezone
    # itself falls back to the OS zone when its argument is None/empty.
    from devbench.config import DISPLAY_TIMEZONE

    try:
        tz = resolve_timezone(tz_name or DISPLAY_TIMEZONE)
    except InvalidTimezoneError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Provide an IANA zoneinfo name (e.g. America/New_York, UTC).", file=sys.stderr)
        return 2

    path = Path(path_override) if path_override else WORKSPACE_ROOT / "hook-logs.jsonl"
    color = should_use_color(sys.stdout)

    print(render_header(path, tz, color=color))
    sys.stdout.flush()

    return follow(
        path,
        FollowOptions(
            tz=tz,
            from_start=from_start,
            no_follow=no_follow,
            color=color,
            orchestrator_session_id=orchestrator_session_id,
        ),
        sys.stdout,
    )


@dataclass(frozen=True)
class _WatchdogArgs:
    idle_minutes: int = 5
    flag_file: str = ""
    log_file: str = ""
    print_if_stuck: bool = False


def _parse_watchdog_args(argv: tuple[str, ...]) -> _WatchdogArgs | int:
    """Parse watchdog flags; return a populated args struct, or exit code on error."""
    idle_minutes = 5
    flag_file = ""
    log_file = ""
    print_if_stuck = False
    args = [a for a in argv if a]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--idle-minutes":
            if i + 1 >= len(args) or not args[i + 1]:
                print("ERROR: --idle-minutes requires a value", file=sys.stderr)
                return 2
            try:
                idle_minutes = int(args[i + 1])
            except ValueError:
                print(f"ERROR: --idle-minutes must be an integer, got: {args[i + 1]}", file=sys.stderr)
                return 2
            if idle_minutes < 1:
                print("ERROR: --idle-minutes must be >= 1", file=sys.stderr)
                return 2
            i += 2
        elif arg in ("--flag-file", "--log-file"):
            if i + 1 >= len(args) or not args[i + 1]:
                print(f"ERROR: {arg} requires a value", file=sys.stderr)
                return 2
            if arg == "--flag-file":
                flag_file = args[i + 1]
            else:
                log_file = args[i + 1]
            i += 2
        elif arg == "--print-if-stuck":
            print_if_stuck = True
            i += 1
        else:
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return 2
    return _WatchdogArgs(
        idle_minutes=idle_minutes,
        flag_file=flag_file,
        log_file=log_file,
        print_if_stuck=print_if_stuck,
    )


def cmd_watchdog(*argv: str) -> int:
    """Poll for a stuck orchestrate loop and write a marker file when detected.

    Usage::

        watchdog [--idle-minutes N] [--flag-file PATH] [--log-file PATH]
                 [--print-if-stuck]

    Detects a hang when BACKLOG.md has an in-progress task AND the
    orchestrator log has been silent longer than ``--idle-minutes``
    (default 5). Writes ``<workspace>/.devbench/needs-restart.flag`` with
    the stuck task ID, idle duration, and threshold metadata.

    Exits 0 always. With ``--print-if-stuck`` prints a one-line status to
    stdout when stuck (suitable for PROMPT_COMMAND integration); silent
    when healthy so it pipes cleanly in shell prompts.
    """
    from datetime import UTC, datetime

    from devbench.config import STOP_HOOK_STALE_TASK_MINUTES
    from devbench.watchdog import detect_stuck, write_flag_file

    parsed = _parse_watchdog_args(argv)
    if isinstance(parsed, int):
        return parsed

    log_file = Path(parsed.log_file) if parsed.log_file else Path(__file__).parent / "logs" / "orchestrator.log"
    flag_file = Path(parsed.flag_file) if parsed.flag_file else WORKSPACE_ROOT / ".devbench" / "needs-restart.flag"

    result = detect_stuck(
        backlog_index=WORKSPACE_ROOT / "BACKLOG.md",
        log_file=log_file,
        now=datetime.now(UTC),
        idle_threshold_seconds=parsed.idle_minutes * 60,
        stale_task_minutes=STOP_HOOK_STALE_TASK_MINUTES,
    )

    if result.stuck is None:
        return 0

    write_flag_file(flag_file, result.stuck, datetime.now(UTC))
    if parsed.print_if_stuck:
        print(
            f"[devbench watchdog] STUCK: {result.stuck.task_id} "
            f"(idle {result.stuck.idle_seconds}s, threshold {parsed.idle_minutes}m). "
            f"Flag: {flag_file}"
        )
    return 0


def cmd_notify_test(*argv: str) -> int:
    """Send one sample notification for ``--event <name>`` (smoke-test setup).

    Usage::

        devbench notify-test --event <event_name>

    Fires the named event's canonical payload through the unified
    dispatcher.  Honors ``notifications.enabled`` and
    ``notifications.slack.enabled`` (master + endpoint switches) but
    temporarily forces the per-event toggle on, so the operator can
    verify any of the eleven events regardless of their yaml state.
    Returns rc=2 on bad usage, rc=1 when dispatch raises (best-effort
    is bypassed for smoke-test diagnostics so the failure is visible).
    """
    from devbench.notifications import ALL_EVENTS, send_test_notification

    event_name = ""
    args = [a for a in argv if a]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--event":
            if i + 1 >= len(args):
                print("ERROR: --event requires a value", file=sys.stderr)
                return 2
            event_name = args[i + 1]
            i += 2
            continue
        print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
        return 2
    if not event_name:
        print(
            "ERROR: --event <name> is required; one of:\n  " + "\n  ".join(ALL_EVENTS),
            file=sys.stderr,
        )
        return 2
    if event_name not in ALL_EVENTS:
        print(
            f"ERROR: unknown event {event_name!r}; expected one of:\n  " + "\n  ".join(ALL_EVENTS),
            file=sys.stderr,
        )
        return 2
    send_test_notification(event_name)
    print(f"[OK] notify-test fired {event_name!r}; check the configured channel(s).")
    return 0


def _resolve_plugin_path() -> Path:
    """Return the plugin path to load: shadow when overrides configured, else canonical.

    Materialises a workspace-local shadow plugin (ADR-25) when the operator
    has set any ``agents.*`` field in ``devbench.yaml`` (or the corresponding
    ``JUDGE_AGENT_MODEL_*`` env var). When no overrides are configured this
    is a no-op and the canonical plugin path is returned.
    """
    canonical = Path(__file__).parent.parent.parent / DEFAULT_PLUGIN_SUBPATH
    shadow = materialise_shadow_plugin(canonical, WORKSPACE_ROOT, AGENT_MODELS)
    return shadow if shadow is not None else canonical


def _should_auto_restart_after_no_actionable() -> tuple[bool, list[str]]:
    """Post-mortem inspection: should the wrapping launcher auto-restart?

    Returns ``(True, [<task_id>, ...])`` when **all three** preconditions hold:

    1. At least one BLOCKED task currently classifies as
       :class:`~devbench.backlog.proposal.BlockedTaskState.RUNTIME_DEGRADATION`
       (the SDK subprocess lost Agent-tool access mid-session, recoverable
       by a fresh subprocess).
    2. Zero tasks are :class:`WorkUnitStatus.IN_PROGRESS` or
       :class:`WorkUnitStatus.IN_REVIEW` (the orchestrator is not mid-claim;
       a restart will not interrupt active work).
    3. Zero BLOCKED tasks classify as
       :class:`~devbench.backlog.proposal.BlockedTaskState.OPERATOR_ACTION_REQUIRED`
       (no genuine human-attention block is also pending; restarting would
       not unblock those anyway, so we defer to the operator).

    Returns ``(False, [])`` otherwise. Inspected post-mortem after the SDK
    subprocess has exited so the function never blocks the running
    orchestrator.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    if any(u.status in (WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.IN_REVIEW) for u in units):
        return False, []
    runtime_degraded: list[str] = []
    for unit in units:
        if unit.status is not WorkUnitStatus.BLOCKED:
            continue
        state = classify_blocked_task(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            task_id=unit.id,
            workspace_root=WORKSPACE_ROOT,
        )
        if state is BlockedTaskState.OPERATOR_ACTION_REQUIRED:
            return False, []
        if state is BlockedTaskState.RUNTIME_DEGRADATION:
            runtime_degraded.append(unit.id)
    if not runtime_degraded:
        return False, []
    return True, runtime_degraded


# ---------------------------------------------------------------------------
# Drain-enforcement sentinel (spec section 4.3.3, AC-188-4/5/8)
# ---------------------------------------------------------------------------

#: Audit-log prefix written to the orchestrator log when cmd_start intercepts
#: a cmd_claim attempt while a drain signal is pending.  Format:
#: ``[ORCHESTRATOR_DRAIN_ENFORCED] reason=<text-or-none>``.
_ORCHESTRATOR_DRAIN_ENFORCED_AUDIT_PREFIX: str = "[ORCHESTRATOR_DRAIN_ENFORCED] reason="


class _DrainRequested(BaseException):
    """Sentinel raised inside ``cmd_start._run`` when a drain is pending at claim time.

    Raised as a :class:`BaseException` subclass (not :class:`Exception`) so that
    ``asyncio.run`` propagates it through the event loop without it being caught
    by broad ``except Exception`` handlers.  ``cmd_start`` catches it outside
    ``asyncio.run`` to:

    1. Consume the drain signal via :func:`~devbench.drain.consume_drain`.
    2. Write a ``[ORCHESTRATOR_DRAIN_ENFORCED]`` audit entry to the orchestrator log.
    3. Return ``rc=0`` to the caller.

    Args:
        reason: The ``reason`` field from the :class:`~devbench.drain.DrainState`
            that triggered the drain.

    Raises:
        Nothing -- this class is only ever raised, never caught internally.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


class _QuotaDetected(BaseException):
    """Sentinel raised inside ``cmd_start._run`` when a quota error is detected per-message.

    Raised as a :class:`BaseException` subclass (not :class:`Exception`) so that
    ``asyncio.run`` propagates it through the event loop without it being caught
    by broad ``except Exception`` handlers between the message loop and
    ``asyncio.run`` (spec AC-20, decision D-4). This lets the SDK session tear
    down BEFORE any quota wait begins, running the wait in a fresh event loop
    rather than shielding a task inside the SDK's own loop -- sidestepping the
    anyio cancel-scope defect (issue #235) architecturally instead of guarding
    against it.

    Args:
        quota_exc: The :class:`~devbench.quota.QuotaExhaustedError` produced by
            :func:`~devbench.quota.detect_quota_error`.

    Raises:
        Nothing -- this class is only ever raised, never caught internally.
    """

    def __init__(self, quota_exc: QuotaExhaustedError) -> None:
        super().__init__(str(quota_exc))
        self.quota_exc = quota_exc


#: Verbatim inactivity diagnostic (spec FR-17, db-262). ``{timeout}`` is the
#: configured ``DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT`` value (seconds)
#: that was exceeded. Single source of truth for both the logged ERROR and
#: :class:`_OrchestrateInactivityTimeout`'s message so the two can never
#: drift apart.
_INACTIVITY_TIMEOUT_ERROR_TEMPLATE: str = (
    "ERROR: orchestrator inactivity timeout: no SDK message for {timeout}s "
    "(DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT).\n"
    "The SDK ended a turn without a terminal sentinel and produced no follow-up. "
    "Investigate the last\n"
    "'[ORCHESTRATOR ...]' audit line; the backlog on disk is intact and a fresh "
    "'devbench start' resumes it."
)


class _OrchestrateInactivityTimeout(BaseException):
    """Sentinel raised inside ``cmd_start._run`` when no SDK message arrives in time.

    Sibling of :class:`_QuotaDetected`: a :class:`BaseException` subclass (not
    :class:`Exception`) so that ``asyncio.run`` propagates it through the
    event loop without being caught by any broad ``except Exception`` handler
    between the message loop and ``asyncio.run`` (spec FR-17, db-262).
    Raised when ``asyncio.wait_for(agen.__anext__(), timeout=_ORCH_INACTIVITY_TIMEOUT)``
    times out -- the SDK ended a turn without a terminal sentinel and produced
    no follow-up message, which previously left the orchestrator idling
    forever (observed 2h24m). Unwinds through ``_run``'s
    ``finally: await agen.aclose()`` exactly like a quota sentinel (db-325)
    before :func:`_drive_orchestrate_with_quota_resume` disposes it as a
    bounded fresh-session restart.

    Args:
        timeout_seconds: The configured inactivity timeout (seconds) that was
            exceeded without a follow-up SDK message.

    Raises:
        Nothing -- this class is only ever raised, never caught internally.
    """

    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(_INACTIVITY_TIMEOUT_ERROR_TEMPLATE.format(timeout=timeout_seconds))
        self.timeout_seconds = timeout_seconds


class _OrchestrateTransportError(BaseException):
    """Sentinel raised inside ``cmd_start._run`` when the SDK generator boundary raises (#331).

    Sibling of :class:`_QuotaDetected` and :class:`_OrchestrateInactivityTimeout`: a
    :class:`BaseException` subclass (not :class:`Exception`) so that ``asyncio.run``
    propagates it through the event loop without being caught by any broad
    ``except Exception`` handler between the SDK generator boundary and
    ``asyncio.run`` (spec FR-1).

    Wraps ONLY an exception raised by ``await
    asyncio.wait_for(agen.__anext__(), ...)`` -- the SDK generator boundary itself
    (spec section 1.1, decision D-5). ``StopAsyncIteration`` and ``TimeoutError``
    keep their existing handling and are never wrapped. ``SystemExit``,
    ``KeyboardInterrupt``, and :class:`asyncio.CancelledError` are
    :class:`BaseException` subclasses that are not :class:`Exception`, so the
    ``except Exception`` clause that raises this sentinel never matches them
    either -- they are never wrapped (spec AC-3). A devbench-originated exception
    raised elsewhere in the loop body (e.g. by :func:`_check_quota_and_drain`) is
    outside this narrow boundary and is never wrapped (decision D-5): wrapping the
    whole loop body would turn a genuine devbench defect into a silent restart
    loop.

    Classification is structural (which call raised, not what the message says,
    decision D-4): upstream may raise a bare ``Exception`` with arbitrary text --
    as observed, ``Exception: Claude Code returned an error result: success`` --
    so branching on the message string is explicitly forbidden.

    Args:
        original: The exception raised from the SDK generator boundary. Callers
            preserve it verbatim as ``__cause__`` via ``raise ... from original``
            at the raise site; its ``str()`` becomes this sentinel's own message
            so the ERROR log line :func:`_drive_orchestrate_with_quota_resume`
            emits on each restart carries the upstream text unmodified (spec
            AC-6, "verbatim").

    Raises:
        Nothing -- this class is only ever raised, never caught internally.
    """

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


class _OrchestratePrematureTurnEnd(BaseException):
    """Sentinel raised inside ``cmd_start._run`` when the SDK session ends with work left.

    Sibling of :class:`_QuotaDetected`, :class:`_OrchestrateInactivityTimeout`
    and :class:`_OrchestrateTransportError`: a :class:`BaseException` subclass
    (not :class:`Exception`) so ``asyncio.run`` propagates it without any broad
    ``except Exception`` handler swallowing it.

    Raised when ``agen.__anext__()`` reports ``StopAsyncIteration`` -- the SDK
    generator is exhausted -- WITHOUT the loop having already returned on a
    terminal sentinel. A genuine end-of-run returns early from
    :func:`_log_terminal_exit_if_applicable` the moment ``ALL_DONE`` /
    ``NO_ACTIONABLE`` is observed, so reaching the end of the generator instead
    means the model ended its own turn while backlog work remained.

    Before this sentinel, that path was a bare ``break``: the orchestrator
    exited permanently, rc=0, and the ``orchestrator_stop`` notification
    labelled it a clean exit. That left the fastest-firing failure mode as the
    only one with no recovery at all, while a model going *silent* for the
    inactivity window (a slower form of the same failure) already earned a
    bounded fresh-session restart. The observed trigger was a model ending its
    turn on the claim that it had scheduled a background notification to wake
    itself, a capability devbench does not have.

    Args:
        result_text: The last ``ResultMessage.result`` text observed before the
            generator ended, or ``None`` when the SDK emitted none. Carried into
            this sentinel's message so the restart log line records what the
            model actually said instead of discarding the only diagnostic.

    Raises:
        Nothing -- this class is only ever raised, never caught internally.
    """

    def __init__(self, result_text: str | None) -> None:
        detail = f": {result_text}" if result_text else " (no SDK result text)"
        super().__init__(f"SDK session ended with no terminal sentinel{detail}")
        self.result_text = result_text


def _is_claim_tool_use(message: object) -> bool:
    """Return ``True`` when *message* is an SDK message containing a Bash claim tool use.

    Specifically, returns ``True`` when *message* has a ``content`` attribute
    that is a list containing at least one object with ``name == "Bash"`` and
    an ``input`` dict whose ``command`` value contains ``"devbench claim"``.

    This is the SDK-layer heuristic used by ``cmd_start._run`` to detect when
    the orchestrator agent is about to attempt a work-unit claim.  Combined with
    a pending drain signal, this triggers the ``_DrainRequested`` sentinel to
    cancel the asyncio loop cleanly (spec section 4.3.3).

    Uses duck-typed attribute access so that both real
    :class:`~claude_agent_sdk.types.AssistantMessage` instances and test doubles
    are supported without a hard import of ``claude_agent_sdk.types``.

    Args:
        message: Any object emitted by the :func:`claude_agent_sdk.query` async
            generator.  Objects without a ``content`` list attribute always
            return ``False``.

    Returns:
        ``True`` if *message* is a Bash claim tool use; ``False`` otherwise.

    Raises:
        Nothing -- all attribute/type mismatches are handled gracefully.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return False
    for block in content:
        if getattr(block, "name", None) != "Bash":
            continue
        block_input = getattr(block, "input", None)
        if not isinstance(block_input, dict):
            continue
        command = block_input.get("command", "")
        if isinstance(command, str) and "devbench claim" in command:
            return True
    return False


def _check_quota_and_drain(message: object) -> None:
    """Per-message quota + drain-on-claim short-circuit for the ``_run`` message loop.

    Raises :class:`_QuotaDetected` when *message* carries a quota / rate-limit
    signal recognized by :func:`~devbench.quota.detect_quota_error` (issue
    #236), or raises :class:`_DrainRequested` when *message* is a claim
    tool-use observed while a drain signal is pending (issues #188/#212).
    Returns ``None`` (no-op) in every other case.

    Extracted out of ``_run`` so the per-message branching lives in exactly
    one place (DRY) and ``_run`` itself stays under ruff's PLR0912 12-branch
    cap. :func:`~devbench.quota.detect_quota_error` guarantees it never
    raises, so a malformed or unrelated *message* falls through both checks
    and this function raises nothing (spec Section 7.1 sanctioned swallow 1).

    Args:
        message: Any object emitted by the :func:`claude_agent_sdk.query`
            async generator.

    Raises:
        _QuotaDetected: *message* carries a quota / rate-limit signal.
        _DrainRequested: *message* is a claim tool-use and a drain signal is
            currently pending for this session.
    """
    quota_exc = detect_quota_error(message)
    if quota_exc is not None:
        raise _QuotaDetected(quota_exc)
    if _is_claim_tool_use(message) and (drain_state := read_drain_state(WORKSPACE_ROOT)) is not None:
        raise _DrainRequested(drain_state.reason)


_QUOTA_WAITING_AUDIT_PREFIX: str = "[QUOTA_WAITING]"
_QUOTA_RESUMED_AUDIT_PREFIX: str = "[QUOTA_RESUMED]"
_QUOTA_PROBE_UNAVAILABLE_AUDIT_PREFIX: str = "[QUOTA_PROBE_UNAVAILABLE]"
_QUOTA_FAIL_FAST_AUDIT_PREFIX: str = "[QUOTA_FAIL_FAST]"
_QUOTA_DRAIN_REQUESTED_AUDIT_PREFIX: str = "[QUOTA_DRAIN_REQUESTED]"
_QUOTA_TIMEOUT_KEEP_WAITING_AUDIT_PREFIX: str = "[QUOTA_TIMEOUT_KEEP_WAITING]"


def _quota_structured_events_enabled() -> bool:
    """Return whether structured ``[QUOTA_*]`` log markers should be emitted (FR-2, spec AC-7/AC-8).

    Reads ``RUNTIME_CONFIG.quota_handling.log_structured_events`` directly.
    Performs no I/O and catches nothing: the config loader has already
    validated the field as a boolean, so a try/except-default wrapper here
    would be fallback logic (BLOCKED by this unit's error-handling contract).

    Gates every ``[QUOTA_*]`` structured marker emission in this module
    (``[QUOTA_WAITING]``, ``[QUOTA_RESUMED]``, ``[QUOTA_PROBE_UNAVAILABLE]``,
    ``[QUOTA_FAIL_FAST]``, ``[QUOTA_DRAIN_REQUESTED]``,
    ``[QUOTA_TIMEOUT_KEEP_WAITING]``) plus ``[QUOTA_POLLING]`` in
    ``devbench.quota`` (threaded in as a parameter, since that module
    imports no config). Explicitly NOT gated (decision D-10): Slack
    notifications (``notifications.events.*``), audit comments
    (``audit_comment_on_wait`` / ``audit_comment_on_resume``), ordinary
    non-marker log lines, and checkpoint writes -- each has its own,
    independently-tested toggle.

    Returns:
        ``True`` (the default) when structured quota markers should be
        logged; ``False`` when the operator has disabled them.
    """
    return RUNTIME_CONFIG.quota_handling.log_structured_events


_QUOTA_STOP_REASON_DRAIN_DETECTION: str = "quota-drain-requested"
_QUOTA_STOP_REASON_DRAIN_TIMEOUT: str = "quota-wait-timeout-drain"
#: Non-recovering quota stop reasons whose disposition is a drain request
#: (as opposed to a fail-fast re-raise or a keep-waiting terminal stop).
#: :func:`_drive_orchestrate_with_quota_resume` consults this set to decide
#: whether the returned :class:`_OrchestrateLoopResult.quota_drain_requested`
#: must survive ``cmd_start``'s exit-path drain cleanup (spec AC-25).
_QUOTA_DRAIN_STOP_REASONS: frozenset[str] = frozenset(
    {_QUOTA_STOP_REASON_DRAIN_DETECTION, _QUOTA_STOP_REASON_DRAIN_TIMEOUT}
)
_QUOTA_STOP_REASON_WAIT_RECOVERED: str = "quota-wait-recovered"
_QUOTA_STOP_REASON_TIMEOUT_KEEP_WAITING: str = "quota-wait-timeout-keep-waiting"

#: Allowed ``on_exhaustion_timeout`` values (FR-2.9). Single source of truth
#: for both the ``_dispatch_quota_timeout`` membership guard and its
#: ``ValueError`` message, mirroring the ``_RESUME_STRATEGIES`` idiom in
#: ``devbench.quota``.
_QUOTA_TIMEOUT_ACTIONS: frozenset[str] = frozenset({"drain", "fail", "keep_waiting"})


def _run_quota_side_effect_best_effort(operation: str, fn: Callable[[], None]) -> None:
    """Run a best-effort quota side effect and never let it break or delay a wait.

    Section 7.1 sanctioned swallows 2 and 3: a Slack notification failure and
    a work-unit audit-comment append failure must NEVER break or delay a
    quota wait. Catches ``Exception`` (not ``BaseException``, so a genuine
    interrupt such as ``KeyboardInterrupt`` still propagates), logs a single
    WARNING naming the failed *operation* and the exception, and returns.
    Shared by both notification wrappers and the audit-comment appender so
    the catch-log-continue shape exists in exactly one place (DRY).

    Args:
        operation: Short human-readable operation name used in the WARNING.
        fn: Zero-argument callable performing the side effect.
    """
    try:
        fn()
    except Exception as exc:
        logger.warning("[WARN] %s failed (ignored): %r", operation, exc)


def _fire_quota_waiting_notification(reason: str, reset_at: str) -> None:
    """Best-effort ``quota_waiting`` Slack ping at the start of a quota wait.

    Wraps :func:`devbench.notifications.notify_quota_waiting` via
    :func:`_run_quota_side_effect_best_effort` so a notify/IO failure can
    NEVER break or delay the quota wait (spec AC-27, Section 7.1 sanctioned
    swallow 3).

    Args:
        reason: The quota source/reason (``QuotaExhaustedError.source``).
        reset_at: The provider-stated reset time as ISO 8601, or ``"unknown"``.
    """

    def _notify() -> None:
        from devbench.notifications import notify_quota_waiting

        notify_quota_waiting(reason, reset_at)

    _run_quota_side_effect_best_effort("notify_quota_waiting", _notify)


def _fire_quota_resumed_notification(waited_seconds: int) -> None:
    """Best-effort ``quota_resumed`` Slack ping on the quota-recovered path.

    Wraps :func:`devbench.notifications.notify_quota_resumed` via
    :func:`_run_quota_side_effect_best_effort` so a notify/IO failure can
    NEVER break or delay the resume (spec AC-27, Section 7.1 sanctioned
    swallow 3).

    Args:
        waited_seconds: Total seconds spent waiting before recovery.
    """

    def _notify() -> None:
        from devbench.notifications import notify_quota_resumed

        notify_quota_resumed(waited_seconds)

    _run_quota_side_effect_best_effort("notify_quota_resumed", _notify)


def _append_quota_audit_comment(message: str) -> None:
    """Best-effort append of *message* to the in-flight work unit's Comments section.

    FR-2.12 / decision D-10: ``audit_comment_on_wait`` and
    ``audit_comment_on_resume`` are parsed-but-DEAD on the source branch;
    this function is the fresh implementation this task ships. Resolves the
    single in-progress work unit via :func:`_find_in_flight_wu` and appends
    *message* through :meth:`~devbench.backlog.manager.BacklogManager._append_agent_comment`.
    A no-op (nothing appended, nothing logged) when no work unit is
    in-progress. Wrapped via :func:`_run_quota_side_effect_best_effort` so a
    comment-append failure logs a WARNING and never breaks the wait (spec
    AC-29, Section 7.1 sanctioned swallow 3).

    Args:
        message: The full audit-comment text to append (e.g.
            ``"[QUOTA_WAITING] reason=anthropic-api reset_at=unknown"``).
    """

    def _append() -> None:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
        wu = _find_in_flight_wu(units)
        if wu is None:
            return
        BacklogManager()._append_agent_comment(wu.file_path, "orchestrator", message)

    _run_quota_side_effect_best_effort("quota audit comment append", _append)


def _format_checkpoint_reset_at(reset_at: datetime | None) -> str:
    """Format a checkpoint's ``reset_at`` as ISO 8601, or the literal ``"unknown"`` when absent.

    Shared by :func:`_handle_quota_pause` (the ``[QUOTA_WAITING]`` marker
    emitted at pause time) and :func:`cmd_quota_watcher` (the same marker
    read back from the on-disk checkpoint) so the ISO-or-unknown format
    exists in exactly one place (DRY).

    Args:
        reset_at: The checkpoint's expected reset time, or ``None`` when
            the provider did not supply one.

    Returns:
        ``reset_at.isoformat()`` when set, otherwise ``"unknown"``.
    """
    return reset_at.isoformat() if reset_at is not None else "unknown"


async def _handle_quota_pause(
    *,
    exc: QuotaExhaustedError,
    qh_cfg: QuotaHandlingConfig,
    workspace_root: Path,
    session_name: str,
) -> bool:
    """Handle a quota exhaustion signal with wait-and-resume (FR-2.9/FR-2.10, spec AC-26).

    Fixed sequence:

    1. Saves a checkpoint so SIGTERM does not lose pause state.
    2. When :func:`_quota_structured_events_enabled` is true (FR-2, spec
       AC-7/AC-8), emits ``[QUOTA_WAITING] reason=<r> reset_at=<ISO|unknown>``.
       Fires the wrapped Slack notification, then (when ``audit_comment_on_wait``
       is true) appends the same marker text to the in-flight work unit's
       Comments section -- Slack and the audit comment are UNCONDITIONAL on
       their own toggles regardless of the structured-events flag (FR-2.12,
       D-10).
    3. Awaits ``wait_for_reset`` with no cancellation-shielding primitive
       (D-9): a SIGTERM must propagate naturally so ``devbench stop`` stays
       responsive. Threads the same structured-events decision through as
       ``emit_structured_events`` so the ``[QUOTA_POLLING]`` heartbeat
       (quota.py:526) obeys the same flag.
    4. On recovery: when structured events are enabled, emits
       ``[QUOTA_RESUMED] waited_seconds=<N>``; fires the resumed
       notification, appends the audit comment when
       ``audit_comment_on_resume`` is true, applies the configured resume
       strategy, and returns ``True``.
    5. On timeout: returns ``False`` (caller applies ``on_exhaustion_timeout``
       via :func:`_dispatch_quota_timeout`).
    6. When the recovery probe is permanently unavailable: when structured
       events are enabled, emits ``[QUOTA_PROBE_UNAVAILABLE] reason=<r>
       detail=<msg>``, then returns ``False`` immediately instead of polling
       out the full window.

    Args:
        exc: The detected ``QuotaExhaustedError``.
        qh_cfg: Quota handling configuration.
        workspace_root: Workspace root for checkpoint storage.
        session_name: Current session name (stored in checkpoint).

    Returns:
        ``True`` when recovery was confirmed; ``False`` when timed out or the
        recovery probe was permanently unavailable.
    """
    now = datetime.now(tz=UTC)
    checkpoint = QuotaCheckpoint(
        reason=exc.source,
        reset_at=exc.reset_at,
        saved_at=now,
        session_name=session_name,
    )
    save_checkpoint(checkpoint, workspace_root)

    emit_structured_events = _quota_structured_events_enabled()
    reset_at_str = _format_checkpoint_reset_at(exc.reset_at)
    if emit_structured_events:
        logger.info(
            "%s reason=%s reset_at=%s",
            _QUOTA_WAITING_AUDIT_PREFIX,
            exc.source,
            reset_at_str,
        )
    _fire_quota_waiting_notification(exc.source, reset_at_str)
    if qh_cfg.audit_comment_on_wait:
        _append_quota_audit_comment(f"{_QUOTA_WAITING_AUDIT_PREFIX} reason={exc.source} reset_at={reset_at_str}")

    wait_start = datetime.now(tz=UTC)
    backoff = BackoffConfig(initial_seconds=qh_cfg.poll_interval_seconds)

    try:
        recovered = await wait_for_reset(
            reset_at=exc.reset_at,
            poll_interval_seconds=qh_cfg.poll_interval_seconds,
            max_wait_seconds=qh_cfg.max_wait_seconds,
            probe_fn=functools.partial(
                recovery_probe,
                timeout_seconds=RECOVERY_PROBE_TIMEOUT_SECONDS,
                request_size_tokens=RECOVERY_PROBE_REQUEST_SIZE_TOKENS,
                source=exc.source,
            ),
            backoff_config=backoff,
            emit_structured_events=emit_structured_events,
        )
    except RecoveryProbeUnavailableError as probe_exc:
        if emit_structured_events:
            logger.info(
                "%s reason=%s detail=%s",
                _QUOTA_PROBE_UNAVAILABLE_AUDIT_PREFIX,
                exc.source,
                probe_exc,
            )
        return False

    if not recovered:
        return False

    waited_seconds = int((datetime.now(tz=UTC) - wait_start).total_seconds())
    if emit_structured_events:
        logger.info(
            "%s waited_seconds=%d",
            _QUOTA_RESUMED_AUDIT_PREFIX,
            waited_seconds,
        )
    _fire_quota_resumed_notification(waited_seconds)
    if qh_cfg.audit_comment_on_resume:
        _append_quota_audit_comment(f"{_QUOTA_RESUMED_AUDIT_PREFIX} waited_seconds={waited_seconds}")
    _apply_resume_strategy(qh_cfg.resume_strategy, workspace_root)
    return True


def cmd_quota_watcher() -> int:
    """Inspect the quota pause checkpoint (FR-2.11, spec AC-28, Section 14).

    Usage::

        devbench quota-watcher

    No flags -- the plain invocation is the entire command surface. The
    ``--daemon`` background-monitor mode from earlier design drafts was
    removed in commit ``9883d13``; this is a fresh port, not a copy of the
    source branch's leftover ``--once``-required guard.

    Reads ``<workspace>/.devbench/quota_pause.json`` via
    :func:`devbench.quota.load_checkpoint` and prints
    ``[QUOTA_WAITING] reason=<source> reset_at=<ISO|unknown>`` to stdout
    when a checkpoint is present. The watcher is advisory: when a running
    orchestrator owns the session, its in-loop wait (:func:`_handle_quota_pause`)
    is authoritative -- this command only surfaces the same on-disk
    checkpoint for operator visibility (S10.4 journey J-3).

    Error handling is fail-fast without leaking a traceback: an unreadable
    workspace path (verified via :func:`os.access` before any read attempt)
    and a checkpoint file that raises ``OSError`` mid-read both print a
    one-line ``ERROR:`` message naming the path and return 1; a corrupt
    checkpoint surfaces ``load_checkpoint``'s ``ValueError`` message
    (which already names the path) and returns 1.

    Returns:
        0 when a checkpoint exists (the orchestrator is paused); the pause
        details are printed to stdout.
        1 when no checkpoint exists (not paused), when the checkpoint is
        corrupt, when reading it fails with an ``OSError``, or when the
        workspace path itself is not readable.
    """
    workspace_root = WORKSPACE_ROOT
    if not os.access(workspace_root, os.R_OK | os.X_OK):
        print(f"ERROR: workspace path is not readable: {workspace_root}", file=sys.stderr)
        return 1

    try:
        checkpoint = load_checkpoint(workspace_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: cannot read quota checkpoint: {exc}", file=sys.stderr)
        return 1

    if checkpoint is None:
        return 1

    reset_at_str = _format_checkpoint_reset_at(checkpoint.reset_at)
    print(f"{_QUOTA_WAITING_AUDIT_PREFIX} reason={checkpoint.reason} reset_at={reset_at_str}")
    return 0


def _cancel_drain_unless_requested(workspace_root: Path, quota_drain_requested: bool) -> None:
    """Best-effort cancel of a pending drain signal on ``cmd_start`` exit (spec AC-25).

    Skips the cancel when the quota dispatch deliberately requested a drain
    (``on_exhaustion``/``on_exhaustion_timeout`` == ``"drain"``): that signal
    MUST survive process exit so the Makefile restart loop / a peer session
    acts on it. Otherwise cancels any stale drain so the next start does not
    inherit it. Idempotent; suppresses filesystem errors the same way the
    existing unconditional ``cancel_drain`` call sites in ``cmd_start`` do.

    Args:
        workspace_root: Root directory of the devbench workspace.
        quota_drain_requested: ``True`` when the quota dispatch requested a
            drain.
    """
    if quota_drain_requested:
        return
    with contextlib.suppress(OSError):
        cancel_drain(workspace_root)


def _dispatch_quota_detection(detected: "_QuotaDetected", session_name: str) -> str:
    """Handle a detected quota signal from ``cmd_start``'s ``_run`` loop (FR-2.9, spec AC-24/AC-25).

    Applies the configured ``quota_handling`` policy:

    - ``enabled: false`` -- re-raise the wrapped
      :class:`~devbench.quota.QuotaExhaustedError` so the legacy non-zero
      exit of issue #193 AC-4 is reproduced byte-for-byte (spec AC-24).
    - ``on_exhaustion`` (detection-time): ``"fail"`` logs
      ``[QUOTA_FAIL_FAST]`` and re-raises immediately; ``"drain"`` logs
      ``[QUOTA_DRAIN_REQUESTED] phase=detection``, requests a drain, and
      stops without waiting; ``"wait"`` (default) enters
      :func:`_handle_quota_pause`. Both marker logs are gated on
      :func:`_quota_structured_events_enabled` (FR-2, spec AC-7); the drain
      request itself is never gated.
    - On a wait timeout (or an unrecoverable probe), ``on_exhaustion_timeout``
      is applied via :func:`_dispatch_quota_timeout`.

    Extracted from ``cmd_start`` to keep that function under ruff's PLR0912
    12-branch limit.

    Args:
        detected: The :class:`_QuotaDetected` sentinel raised by ``_run``.
        session_name: Current session name (passed to
            :func:`_handle_quota_pause`).

    Returns:
        A descriptive stop-reason string for the ``cmd_start`` audit trail.

    Raises:
        ~devbench.quota.QuotaExhaustedError: When ``enabled`` is false, or
            when ``on_exhaustion``/``on_exhaustion_timeout`` is ``"fail"``.
    """
    qh_cfg = RUNTIME_CONFIG.quota_handling
    if not qh_cfg.enabled:
        raise detected.quota_exc from detected

    if qh_cfg.on_exhaustion == "fail":
        if _quota_structured_events_enabled():
            logger.info("%s reason=%s", _QUOTA_FAIL_FAST_AUDIT_PREFIX, detected.quota_exc.source)
        raise detected.quota_exc from detected
    if qh_cfg.on_exhaustion == "drain":
        if _quota_structured_events_enabled():
            logger.info(
                "%s reason=%s phase=detection",
                _QUOTA_DRAIN_REQUESTED_AUDIT_PREFIX,
                detected.quota_exc.source,
            )
        request_drain(WORKSPACE_ROOT, reason=f"quota-exhaustion:{detected.quota_exc.source}")
        return _QUOTA_STOP_REASON_DRAIN_DETECTION

    recovered = asyncio.run(
        _handle_quota_pause(
            exc=detected.quota_exc,
            qh_cfg=qh_cfg,
            workspace_root=WORKSPACE_ROOT,
            session_name=session_name,
        )
    )
    if recovered:
        return _QUOTA_STOP_REASON_WAIT_RECOVERED

    return _dispatch_quota_timeout(detected, qh_cfg.on_exhaustion_timeout)


def _dispatch_quota_timeout(detected: "_QuotaDetected", action: str) -> str:
    """Apply ``on_exhaustion_timeout`` after the wait cap elapses or the probe is unavailable.

    Every ``[QUOTA_FAIL_FAST]`` / ``[QUOTA_TIMEOUT_KEEP_WAITING]`` /
    ``[QUOTA_DRAIN_REQUESTED] phase=timeout`` marker log is gated on
    :func:`_quota_structured_events_enabled` (FR-2, spec AC-7); the
    re-raise, the returned stop-reason, and the drain request itself are
    never gated.

    Args:
        detected: The original quota sentinel (holds ``quota_exc`` for
            re-raise).
        action: ``on_exhaustion_timeout`` value -- one of
            :data:`_QUOTA_TIMEOUT_ACTIONS` (``"drain"`` default, ``"fail"``,
            ``"keep_waiting"``).

    Returns:
        A descriptive stop-reason string for ``"drain"`` / ``"keep_waiting"``.

    Raises:
        ValueError: When *action* is not a member of
            :data:`_QUOTA_TIMEOUT_ACTIONS` (defense in depth against
            config-schema drift; the loader already validates the enum).
        ~devbench.quota.QuotaExhaustedError: When ``action == "fail"``
            (legacy non-zero exit).
    """
    if action not in _QUOTA_TIMEOUT_ACTIONS:
        raise ValueError(
            f"unknown on_exhaustion_timeout action {action!r}. Allowed values: {sorted(_QUOTA_TIMEOUT_ACTIONS)}."
        )
    if action == "fail":
        if _quota_structured_events_enabled():
            logger.info("%s reason=%s", _QUOTA_FAIL_FAST_AUDIT_PREFIX, detected.quota_exc.source)
        raise detected.quota_exc from detected
    if action == "keep_waiting":
        if _quota_structured_events_enabled():
            logger.info(
                "%s reason=%s",
                _QUOTA_TIMEOUT_KEEP_WAITING_AUDIT_PREFIX,
                detected.quota_exc.source,
            )
        return _QUOTA_STOP_REASON_TIMEOUT_KEEP_WAITING
    if _quota_structured_events_enabled():
        logger.info(
            "%s reason=%s phase=timeout",
            _QUOTA_DRAIN_REQUESTED_AUDIT_PREFIX,
            detected.quota_exc.source,
        )
    request_drain(WORKSPACE_ROOT, reason=f"quota-timeout:{detected.quota_exc.source}")
    return _QUOTA_STOP_REASON_DRAIN_TIMEOUT


def _resolve_max_quota_resumes() -> int:
    """Return the effective in-process quota-resume cap (env > default).

    Reads ``DEVBENCH_MAX_QUOTA_RESUMES`` from the process environment. When the
    variable is absent, empty, or not a parseable positive integer, the constant
    :data:`~devbench.constants.DEFAULT_MAX_QUOTA_RESUMES` is returned so the
    caller is never left without a bound (unset-safe).

    A value <= 0 is treated as invalid and falls back to the default rather than
    disabling the resume loop, so a typo can never silently turn a single quota
    window back into a run-ending event (fail-safe, not fail-open; spec AC-22).

    Returns:
        The maximum number of consecutive in-process quota resumes ``cmd_start``
        will perform before stopping with
        :data:`~devbench.constants.ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX`.
    """
    raw = os.environ.get("DEVBENCH_MAX_QUOTA_RESUMES", "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            return DEFAULT_MAX_QUOTA_RESUMES
        if parsed > 0:
            return parsed
    return DEFAULT_MAX_QUOTA_RESUMES


def _resolve_max_premature_turn_end_restarts() -> int:
    """Return the effective premature-turn-end restart cap (env > default).

    Mirrors :func:`_resolve_max_quota_resumes`'s fail-safe parse for
    ``DEVBENCH_MAX_PREMATURE_TURN_END_RESTARTS``: a missing, empty,
    non-integer, or non-positive value falls back to
    :data:`~devbench.constants.DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS` so a
    typo can neither remove the bound nor disable the restart loop.

    This cap is deliberately separate from (and much lower than) the shared
    quota / inactivity / transport ceiling -- see the constant's own comment for
    why a fast-repeating turn end needs a tighter cost guard than the three
    self-throttling failure modes.

    Returns:
        The maximum number of consecutive in-process premature-turn-end
        restarts ``cmd_start`` performs before failing fast.
    """
    raw = os.environ.get("DEVBENCH_MAX_PREMATURE_TURN_END_RESTARTS", "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            return DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS
        if parsed > 0:
            return parsed
    return DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS


def _should_resume_after_quota_recovery(resumes_used: int, max_resumes: int) -> bool:
    """Decide whether ``cmd_start`` may resume the orchestrate skill in-process.

    Called after :func:`_dispatch_quota_detection` reports a recovered quota
    wait (``_stop_reason == _QUOTA_STOP_REASON_WAIT_RECOVERED``). A recovered
    wait is NOT terminal: the orchestrator opens a FRESH SDK session and
    re-runs ``_run`` on the remaining backlog so a single quota window cannot
    permanently end an unattended ``--daemon`` run (which has no external
    ``make start`` restart wrapper).

    Bounds the resume count by *max_resumes* so a pathological quota loop can
    never spin forever:

    - When *resumes_used* (resumes already performed, BEFORE this one) is below
      *max_resumes*, emits ``[ORCHESTRATOR_QUOTA_RESUME] resume=<n> max=<cap>``
      and returns ``True`` (caller re-runs ``_run``).
    - When the cap is reached, emits
      ``[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=<cap>`` and returns ``False``
      so the caller falls through to the normal terminal classification.

    Args:
        resumes_used: Number of in-process resumes already performed during this
            ``cmd_start`` invocation (0 on the first recovery).
        max_resumes: The cap from :func:`_resolve_max_quota_resumes`.

    Returns:
        ``True`` when another in-process resume is permitted; ``False`` when the
        cap is exhausted and the run must stop.
    """
    if resumes_used >= max_resumes:
        logger.info("%s max=%d", ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX, max_resumes)
        return False
    logger.info(
        "%s resume=%d max=%d",
        ORCHESTRATOR_QUOTA_RESUME_AUDIT_PREFIX,
        resumes_used + 1,
        max_resumes,
    )
    return True


#: Audit markers for the inactivity-timeout bounded-restart disposition
#: (spec FR-17, db-262). Distinct from the ``[ORCHESTRATOR_QUOTA_RESUME*]``
#: markers so operators can tell a hung-turn restart apart from a
#: quota-driven resume in the log.
_ORCHESTRATOR_INACTIVITY_RESTART_AUDIT_PREFIX: str = "[ORCHESTRATOR_INACTIVITY_RESTART]"
_ORCHESTRATOR_INACTIVITY_RESTARTS_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_INACTIVITY_RESTARTS_EXHAUSTED]"


def _should_restart_after_inactivity_timeout(restarts_used: int, max_resumes: int) -> bool:
    """Decide whether ``cmd_start`` may reopen a fresh SDK session after an inactivity timeout.

    Mirrors :func:`_should_resume_after_quota_recovery`'s bounded-restart
    shape but for :class:`_OrchestrateInactivityTimeout` instead of a quota
    signal (spec FR-17, db-262): reuses the SAME :func:`_resolve_max_quota_resumes`
    cap so a hung-turn recovery loop is bounded by the same operator-tunable
    ceiling, while counting its own restarts independently of quota resumes
    (a quota resume never consumes inactivity-restart budget and vice versa).

    - When *restarts_used* (restarts already performed, BEFORE this one) is
      below *max_resumes*, emits
      ``[ORCHESTRATOR_INACTIVITY_RESTART] attempt=<n> max=<cap>`` and returns
      ``True`` (caller re-runs ``_run`` in a fresh session).
    - When the cap is reached, emits
      ``[ORCHESTRATOR_INACTIVITY_RESTARTS_EXHAUSTED] max=<cap>`` and returns
      ``False`` so the caller fails fast (re-raises the sentinel).

    Args:
        restarts_used: Number of in-process inactivity restarts already
            performed during this ``cmd_start`` invocation (0 on the first
            timeout).
        max_resumes: The cap from :func:`_resolve_max_quota_resumes`.

    Returns:
        ``True`` when another in-process restart is permitted; ``False`` when
        the cap is exhausted and the run must fail fast.
    """
    if restarts_used >= max_resumes:
        logger.info("%s max=%d", _ORCHESTRATOR_INACTIVITY_RESTARTS_EXHAUSTED_AUDIT_PREFIX, max_resumes)
        return False
    logger.info(
        "%s attempt=%d max=%d",
        _ORCHESTRATOR_INACTIVITY_RESTART_AUDIT_PREFIX,
        restarts_used + 1,
        max_resumes,
    )
    return True


#: Audit markers for the transport-error bounded-restart disposition (#331 spec
#: FR-2). Distinct from the ``[ORCHESTRATOR_QUOTA_RESUME*]`` and
#: ``[ORCHESTRATOR_INACTIVITY_RESTART*]`` markers so operators can tell a
#: transport-boundary restart apart from a quota-driven resume or an
#: inactivity-timeout restart in the log. ``_ORCHESTRATOR_TRANSPORT_ERROR_AUDIT_PREFIX``
#: tags the single combined ERROR line (verbatim exception + ordinal + cap,
#: spec AC-6) that :func:`_drive_orchestrate_with_quota_resume` emits BEFORE
#: consulting :func:`_should_restart_after_transport_error`, which in turn
#: emits its own INFO-level restart/exhausted markers below.
_ORCHESTRATOR_TRANSPORT_ERROR_AUDIT_PREFIX: str = "[ORCHESTRATOR_TRANSPORT_ERROR]"
_ORCHESTRATOR_TRANSPORT_RESTART_AUDIT_PREFIX: str = "[ORCHESTRATOR_TRANSPORT_RESTART]"
_ORCHESTRATOR_TRANSPORT_RESTARTS_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_TRANSPORT_RESTARTS_EXHAUSTED]"


# Arithmetic overflow guard for the doubling below -- NOT an operator tunable.
# ``base * 2 ** restarts_used`` with an operator-set cap in the thousands would
# build an integer wide enough to raise OverflowError on the float multiply
# before ``min()`` ever clamps it. Any exponent past this point is already
# astronomically beyond every reachable ``max_seconds``, so clamping the
# exponent changes no observable delay.
_TRANSPORT_RESTART_BACKOFF_EXPONENT_CAP: int = 32


def _sleep_between_transport_restarts(seconds: float) -> None:
    """Pause *seconds* before reopening the SDK session after a transport error.

    Isolated as its own module-level function so the pacing is a patchable
    seam. Driving the restart loop in a test must not burn real wall-clock
    time, and patching this one call is far narrower -- and far less likely to
    mask an unrelated regression -- than patching the global ``time.sleep``,
    which the report watch loop and the process-readiness poller also use.

    Args:
        seconds: Delay from :func:`_transport_restart_backoff_seconds`.
    """
    import time

    time.sleep(seconds)


def _transport_restart_backoff_seconds(restarts_used: int, base_seconds: float, max_seconds: float) -> float:
    """Return how long to wait before the next SDK-transport restart.

    Exponential with a ceiling: ``base_seconds * 2 ** restarts_used``, clamped
    to ``max_seconds``. ``restarts_used`` counts restarts already performed, so
    the first restart waits exactly ``base_seconds``.

    Why this exists: a quota window must elapse and an inactivity restart costs
    a full timeout window, so both self-throttle. A transport fault does not --
    it recurs as fast as the SDK can reject a session. Retrying with no delay
    therefore spends the entire restart budget in seconds (observed in the
    field: ~1000 restarts in 39 minutes) and converts a transient fault into a
    dead daemon. Spacing the attempts lets a genuinely transient fault recover
    while keeping a persistent one inside its bound.

    Args:
        restarts_used: Transport restarts already performed this run (0 before
            the first).
        base_seconds: Delay before the first restart. Must be > 0.
        max_seconds: Ceiling on the delay. Must be > 0.

    Returns:
        The delay in seconds: ``min(base_seconds * 2 ** restarts_used, max_seconds)``.

    Raises:
        ValueError: If ``restarts_used`` is negative, or either bound is not
            positive. The YAML schema already enforces positivity, but the
            environment-variable path bypasses the schema, so the invariant is
            enforced here and fails fast rather than silently degrading into a
            busy loop (a zero or negative delay is exactly the defect this
            function exists to prevent).
    """
    if restarts_used < 0:
        raise ValueError(f"restarts_used must be >= 0, got {restarts_used}")
    if base_seconds <= 0:
        raise ValueError(
            "transport restart backoff base must be > 0 seconds, got "
            f"{base_seconds} -- check DEVBENCH_TRANSPORT_RESTART_BACKOFF_BASE_SECONDS "
            "or orchestrate.transport_restart_backoff_base_seconds"
        )
    if max_seconds <= 0:
        raise ValueError(
            "transport restart backoff ceiling must be > 0 seconds, got "
            f"{max_seconds} -- check DEVBENCH_TRANSPORT_RESTART_BACKOFF_MAX_SECONDS "
            "or orchestrate.transport_restart_backoff_max_seconds"
        )
    exponent = min(restarts_used, _TRANSPORT_RESTART_BACKOFF_EXPONENT_CAP)
    return min(base_seconds * (2**exponent), max_seconds)


def _should_restart_after_transport_error(restarts_used: int, max_restarts: int, backoff_seconds: float) -> bool:
    """Decide whether ``cmd_start`` may reopen a fresh SDK session after a transport error.

    Mirrors :func:`_should_restart_after_inactivity_timeout`'s bounded-restart
    shape but for :class:`_OrchestrateTransportError` instead of an inactivity
    timeout (spec FR-2, decision D-3), counting its own restarts independently
    of quota resumes and inactivity restarts (a transport restart never
    consumes quota-resume or inactivity-restart budget and vice versa).

    The cap is :data:`~devbench.config.MAX_TRANSPORT_RESTARTS`, which is
    deliberately its own setting rather than the shared
    :func:`_resolve_max_quota_resumes` ceiling: a transport fault self-throttles
    no more than a busy loop does, so pairing a 1000-restart budget with an
    immediate retry let one persistent fault burn the whole budget in minutes
    and end the run. *backoff_seconds* (from
    :func:`_transport_restart_backoff_seconds`) is recorded in the audit line so
    the pacing is visible in the log and in ``devbench report``.

    - When *restarts_used* (restarts already performed, BEFORE this one) is
      below *max_resumes*, emits
      ``[ORCHESTRATOR_TRANSPORT_RESTART] attempt=<n> max=<cap>`` and returns
      ``True`` (caller re-runs ``_run`` in a fresh session).
    - When the cap is reached, emits
      ``[ORCHESTRATOR_TRANSPORT_RESTARTS_EXHAUSTED] max=<cap>`` and returns
      ``False`` so the caller fails fast (re-raises the sentinel), preserving
      the verbatim final exception as ``__cause__`` (spec AC-7).

    Args:
        restarts_used: Number of in-process transport restarts already
            performed during this ``cmd_start`` invocation (0 on the first
            transport error).
        max_restarts: The transport-specific cap
            (:data:`~devbench.config.MAX_TRANSPORT_RESTARTS`; env > YAML >
            :data:`~devbench.constants.DEFAULT_MAX_TRANSPORT_RESTARTS`).
        backoff_seconds: The delay the caller will observe before the restart,
            recorded in the audit line for operator visibility.

    Returns:
        ``True`` when another in-process restart is permitted; ``False`` when
        the cap is exhausted and the run must fail fast.
    """
    if restarts_used >= max_restarts:
        logger.info("%s max=%d", _ORCHESTRATOR_TRANSPORT_RESTARTS_EXHAUSTED_AUDIT_PREFIX, max_restarts)
        return False
    logger.info(
        "%s attempt=%d max=%d backoff=%.1fs",
        _ORCHESTRATOR_TRANSPORT_RESTART_AUDIT_PREFIX,
        restarts_used + 1,
        max_restarts,
        backoff_seconds,
    )
    return True


#: Audit markers for the premature-turn-end bounded-restart path. Siblings of
#: the transport-error markers above; the orchestrate loop is designed to stop
#: only on ``ALL_DONE`` / ``NO_ACTIONABLE`` / operator drain, so a turn end with
#: work remaining is a recoverable fault that earns a fresh session, not an exit.
_ORCHESTRATOR_PREMATURE_TURN_END_AUDIT_PREFIX: str = "[ORCHESTRATOR_PREMATURE_TURN_END]"
_ORCHESTRATOR_PREMATURE_TURN_END_RESTART_AUDIT_PREFIX: str = "[ORCHESTRATOR_PREMATURE_TURN_END_RESTART]"
_ORCHESTRATOR_PREMATURE_TURN_END_RESTARTS_EXHAUSTED_AUDIT_PREFIX: str = (
    "[ORCHESTRATOR_PREMATURE_TURN_END_RESTARTS_EXHAUSTED]"
)


def _has_actionable_work_remaining() -> bool:
    """Return ``True`` when the backlog still holds a claimable task.

    Gates the premature-turn-end escalation in ``cmd_start._run``: the loop must
    never end while work remains, but with nothing actionable left, the SDK
    session ending IS the ``NO_ACTIONABLE`` condition -- just discovered here
    rather than announced by the model -- so a restart would spend another
    session re-deriving the same answer.

    Delegates to :meth:`~devbench.backlog.parser.BacklogParser.find_next_actionable`,
    the same selector ``devbench next`` uses, so this check can never disagree
    with the orchestrate loop about what counts as actionable (it resumes
    ``in-progress`` tasks before ``in-queue`` ones, and treats a task with
    unsatisfied dependencies as not actionable).

    A backlog that cannot be read is reported as "no actionable work": the SDK
    session has already ended by the time this runs, so the alternative would be
    to restart into a workspace whose index is unreadable and fail again there.
    The parse failure is logged rather than swallowed, and the run still stops
    with the honest premature-turn-end label.

    Returns:
        ``True`` when at least one task is actionable; ``False`` when none is or
        the backlog index cannot be parsed.
    """
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        return parser.find_next_actionable(parser.parse_index()) is not None
    except (OSError, ValueError) as exc:
        logger.error(
            "premature-turn-end actionability check could not read the backlog index: %r; "
            "treating as no actionable work",
            exc,
        )
        return False


def _should_restart_after_premature_turn_end(restarts_used: int, max_restarts: int) -> bool:
    """Decide whether ``cmd_start`` may reopen a fresh SDK session after a premature turn end.

    Mirrors :func:`_should_restart_after_transport_error`'s bounded-restart
    shape, but bounded by :func:`_resolve_max_premature_turn_end_restarts`
    rather than the shared quota ceiling, because this fault can repeat
    immediately whereas quota / inactivity / transport faults each self-throttle.

    - Below the cap: emits ``[ORCHESTRATOR_PREMATURE_TURN_END_RESTART]
      attempt=<n> max=<cap>`` and returns ``True`` (caller re-runs ``_run`` in a
      fresh session on the remaining backlog).
    - At the cap: emits ``[ORCHESTRATOR_PREMATURE_TURN_END_RESTARTS_EXHAUSTED]
      max=<cap>`` and returns ``False`` so the caller fails fast rather than
      looping without progress.

    Args:
        restarts_used: Premature-turn-end restarts already performed during this
            ``cmd_start`` invocation (0 on the first occurrence).
        max_restarts: The cap from :func:`_resolve_max_premature_turn_end_restarts`.

    Returns:
        ``True`` when another in-process restart is permitted; ``False`` when the
        cap is exhausted and the run must fail fast.
    """
    if restarts_used >= max_restarts:
        logger.info("%s max=%d", _ORCHESTRATOR_PREMATURE_TURN_END_RESTARTS_EXHAUSTED_AUDIT_PREFIX, max_restarts)
        return False
    logger.info(
        "%s attempt=%d max=%d",
        _ORCHESTRATOR_PREMATURE_TURN_END_RESTART_AUDIT_PREFIX,
        restarts_used + 1,
        max_restarts,
    )
    return True


#: Audit markers for the premature-turn-end bounded-restart path. Siblings of


class _OrchestrateLoopResult(NamedTuple):
    """Outcome of :func:`_drive_orchestrate_with_quota_resume`.

    Attributes:
        terminal_rc: The exit code when the loop ended via an early-return
            terminal path (drain enforced, a non-recovering quota disposition,
            or the resume cap exhausted) -- always ``0`` for those paths.
            ``None`` means ``_run`` returned normally and ``cmd_start`` must run
            its usual post-loop terminal classification (continuation-exhausted /
            normal-exit) instead of returning ``terminal_rc``.
        stop_reason: The audit/Slack stop-reason label for the always-fire
            notification. Meaningful only when ``terminal_rc`` is not ``None``;
            on the fall-through path ``cmd_start`` overwrites it.
        quota_drain_requested: ``True`` when the quota disposition requested a
            graceful drain that MUST survive process exit, so ``cmd_start``'s
            exit-path drain cleanups skip their otherwise-unconditional
            ``cancel_drain`` call (spec AC-25).
    """

    terminal_rc: int | None
    stop_reason: str
    quota_drain_requested: bool


def _drive_orchestrate_with_quota_resume(
    run: Callable[[], Coroutine[Any, Any, None]],
    session_name: str,
) -> _OrchestrateLoopResult:
    """Drive the orchestrate session loop with in-process quota resume.

    Each iteration runs ONE SDK session via ``asyncio.run(run())`` (*run* opens
    a fresh session on every call -- decision D-6: the SDK conversation is
    never resumed; context is preserved entirely through the backlog on disk).
    The loop re-enters ONLY when a quota wait recovered: the orchestrator
    resumes the orchestrate skill on the remaining backlog with a brand-new
    session rather than exiting, so a single quota window cannot permanently
    end an unattended ``--daemon`` run that has no external ``make start``
    restart wrapper. The number of consecutive resumes is bounded by
    :func:`_resolve_max_quota_resumes`.

    Terminal dispositions (each leaves the loop and is returned to ``cmd_start``):

    - ``run`` returns normally -> ``terminal_rc=None`` (caller runs its normal
      continuation-exhausted / clean-exit classification).
    - :class:`_DrainRequested` -> consume the marker, audit, ``terminal_rc=0``.
    - :class:`_QuotaDetected` with a non-recovering disposition (fail re-raises;
      drain/keep-waiting return a terminal stop reason) -> ``terminal_rc=0``.
    - A recovered wait whose resume cap is exhausted -> ``terminal_rc=0`` with the
      ``"quota-resume-cap-exhausted"`` stop reason (the exhausted audit line was
      emitted by :func:`_should_resume_after_quota_recovery`).
    - :class:`_OrchestrateInactivityTimeout` (spec FR-17, db-262) -> logs the
      verbatim inactivity ERROR, then either restarts a fresh session (bounded
      by the SAME :func:`_resolve_max_quota_resumes` cap, tracked
      independently of quota resumes via :func:`_should_restart_after_inactivity_timeout`)
      or re-raises to fail fast once that cap is exhausted.
    - :class:`_OrchestrateTransportError` (#331 spec FR-1/FR-2) -> logs the
      verbatim SDK-boundary exception at ERROR with its restart ordinal and
      the cap, then either restarts a fresh session (bounded by the SAME
      :func:`_resolve_max_quota_resumes` cap, tracked independently of quota
      resumes and inactivity restarts via
      :func:`_should_restart_after_transport_error`) or re-raises -- preserving
      the original exception as ``__cause__`` -- to fail fast once that cap is
      exhausted.

    Extracted from ``cmd_start`` so the added resume loop does not push that
    function over ruff PLR0912's 12-branch ceiling.

    Args:
        run: The ``cmd_start._run`` closure; a no-arg coroutine factory awaited
            fresh on every iteration. No conversation handle, transcript, or
            other session state is threaded between iterations (D-6, D-2).
        session_name: Current session name, forwarded to
            :func:`_dispatch_quota_detection` for the quota checkpoint.

    Returns:
        An :class:`_OrchestrateLoopResult` describing how the loop ended.

    Raises:
        :class:`~devbench.quota.QuotaExhaustedError`: Propagated from
            :func:`_dispatch_quota_detection` when ``quota_handling`` is disabled
            or the configured disposition is ``fail`` (legacy non-zero exit).
        ValueError: Propagated from :func:`~devbench.quota._apply_resume_strategy`
            (via :func:`_handle_quota_pause`) when a recovered wait's configured
            ``resume_strategy`` is not one of the three recognised values.
        :class:`_OrchestrateInactivityTimeout`: Re-raised (legacy non-zero
            exit, mirroring the quota ``fail`` disposition above) once the
            bounded-restart cap is exhausted.
        :class:`_OrchestrateTransportError`: Re-raised (legacy non-zero exit,
            mirroring the inactivity-timeout cap-exhaustion above), carrying
            the original SDK-boundary exception as ``__cause__``, once the
            bounded transport-restart cap is exhausted (#331 spec FR-2, AC-7).
    """
    resumes_used = 0
    inactivity_restarts_used = 0
    transport_restarts_used = 0
    premature_restarts_used = 0
    max_resumes = _resolve_max_quota_resumes()
    max_premature_restarts = _resolve_max_premature_turn_end_restarts()
    # Bound once at loop entry. The name resolves through this module's
    # globals at call time, so a test (or an operator override applied before
    # the loop starts) that rebinds ``cli.MAX_TRANSPORT_RESTARTS`` is honoured.
    max_transport_restarts = MAX_TRANSPORT_RESTARTS
    while True:
        try:
            asyncio.run(run())
        except _DrainRequested as exc:
            drained = consume_drain(WORKSPACE_ROOT)
            reason_text = drained.reason if drained is not None else exc.reason
            logger.info("%s%s", _ORCHESTRATOR_DRAIN_ENFORCED_AUDIT_PREFIX, reason_text)
            return _OrchestrateLoopResult(0, f"drain enforced: {reason_text}", False)
        except _QuotaDetected as exc:
            stop_reason = _dispatch_quota_detection(exc, session_name)
            if stop_reason == _QUOTA_STOP_REASON_WAIT_RECOVERED:
                if _should_resume_after_quota_recovery(resumes_used, max_resumes):
                    resumes_used += 1
                    continue
                return _OrchestrateLoopResult(0, "quota-resume-cap-exhausted", False)
            return _OrchestrateLoopResult(0, stop_reason, stop_reason in _QUOTA_DRAIN_STOP_REASONS)
        except _OrchestrateInactivityTimeout as exc:
            logger.error(str(exc))
            if _should_restart_after_inactivity_timeout(inactivity_restarts_used, max_resumes):
                inactivity_restarts_used += 1
                continue
            raise
        except _OrchestrateTransportError as exc:
            logger.error(
                "%s restart=%d max=%d: %s",
                _ORCHESTRATOR_TRANSPORT_ERROR_AUDIT_PREFIX,
                transport_restarts_used + 1,
                max_transport_restarts,
                exc,
            )
            backoff_seconds = _transport_restart_backoff_seconds(
                transport_restarts_used,
                TRANSPORT_RESTART_BACKOFF_BASE_SECONDS,
                TRANSPORT_RESTART_BACKOFF_MAX_SECONDS,
            )
            if _should_restart_after_transport_error(transport_restarts_used, max_transport_restarts, backoff_seconds):
                transport_restarts_used += 1
                # Space the retries. A transport fault imposes no delay of its
                # own, so without this the bounded budget is spent as fast as
                # the SDK can reject a session -- the failure mode this arm
                # exists to survive. SIGTERM during the wait is delivered
                # normally; the ceiling bounds how long a stop can be delayed.
                _sleep_between_transport_restarts(backoff_seconds)
                continue
            raise
        except _OrchestratePrematureTurnEnd as exc:
            logger.error(
                "%s restart=%d max=%d: %s",
                _ORCHESTRATOR_PREMATURE_TURN_END_AUDIT_PREFIX,
                premature_restarts_used + 1,
                max_premature_restarts,
                exc,
            )
            if _should_restart_after_premature_turn_end(premature_restarts_used, max_premature_restarts):
                premature_restarts_used += 1
                continue
            raise
        return _OrchestrateLoopResult(None, "clean", False)


@dataclass(frozen=True)
class _CmdStartArgs:
    """Parsed arguments for ``cmd_start``.

    Attributes:
        include: Raw ``--include`` token string (empty = include everything).
        exclude: Raw ``--exclude`` token string (empty = exclude nothing).
        name: Named-session identifier (defaults to ``SESSION_DEFAULT_NAME``).
        allow_overlap: When ``True``, scope overlap with active sessions emits
            a warning but does not abort the start.  When ``False`` (default),
            any overlap causes an immediate rc=1 failure.
    """

    include: str = ""
    exclude: str = ""
    name: str = SESSION_DEFAULT_NAME
    allow_overlap: bool = False
    daemon: bool = False


def _assert_running_package_matches_its_venv() -> None:
    """Fail fast when the venv in use is serving a DIFFERENT checkout's package.

    Self-hosting means two checkouts publish a package named ``devbench``: the
    harness that RUNS the orchestration and the target that it EDITS. A venv
    records which one is importable in a single-line editable ``.pth``, so
    installing one into the other's environment silently swaps which codebase
    executes. :func:`_agent_environment` removes the usual cause; it cannot
    remove the collision, because an operator, a Makefile or a future tool can
    still re-point the install.

    The invariant checked is "a venv serves the checkout it lives inside":
    ``<checkout>/.venv`` should import ``<checkout>/src/devbench``. Comparing
    the imported package against ``__file__`` instead would be circular -- this
    module IS the package, so a re-pointed install relocates both and the
    comparison always passes. ``sys.prefix`` is the one anchor that does not
    move with the swap.

    Silent when the layout does not apply (no ``src/devbench`` beside the venv,
    e.g. a non-editable install or a packaged deployment): this exists to catch
    one specific, well-understood corruption, not to police every install shape.
    A false positive here would block startup for a workspace doing nothing
    wrong, which is worse than the miss it guards against.

    Raises:
        RuntimeError: When both paths resolve and disagree, naming each one and
            the repair, because the symptoms are otherwise nearly
            undiagnosable: the harness begins running the target's code, so
            config keys, CLI verbs and schemas silently disagree with the files
            on disk.
    """
    checkout = Path(sys.prefix).resolve().parent
    expected = checkout / "src" / "devbench"
    if not expected.is_dir():
        return
    actual = Path(__file__).resolve().parent
    if actual != expected:
        raise RuntimeError(
            f"devbench package re-point detected: the active environment lives in {checkout}, "
            f"whose package is {expected}, but the code now running is {actual}. "
            "Both checkouts in this self-hosted workspace publish a package named 'devbench', "
            "so an install into the wrong environment rewrites the editable pointer and swaps "
            f"which codebase executes. Repair: uv pip install -e {checkout} against that venv, "
            "then restart. Prevention: see _agent_environment."
        )


#: Environment variables stripped from the environment handed to spawned agents.
#: ``VIRTUAL_ENV`` is the load-bearing one -- see
#: :func:`_agent_environment` for why it cannot be inherited.
_AGENT_ENV_STRIPPED_VARS: tuple[str, ...] = ("VIRTUAL_ENV",)


def _agent_environment(base_env: Mapping[str, str]) -> dict[str, str]:
    """Return the environment for spawned agents, with unsafe vars removed.

    The orchestrator is normally launched as ``uv run --project
    harness/devbench ...``, and ``uv run`` EXPORTS ``VIRTUAL_ENV`` pointing at
    the harness venv to its child. The daemon then hands its own environment to
    the SDK unchanged, so every agent shell inherits it -- including agents
    whose working directory is the TARGET repo.

    That combination is destructive because this project is developed with
    itself: the harness checkout and the target checkout both publish a package
    named ``devbench``, and the harness venv records which one is importable in
    a single-line ``_editable_impl_devbench.pth``. Any command that installs
    into the ACTIVE environment (``uv pip install``, ``uv pip sync``, the
    ``--active`` variants) therefore rewrites that pointer to the target's
    ``src`` while an agent is merely doing ordinary work in the target repo.
    The harness CLI then fails in a way that looks nothing like its cause: the
    observed symptom was an ``orchestrate`` schema mismatch on
    ``max_transport_restarts``, a key only the harness checkout defines. It was
    hit and hand-repaired twice inside a single run before anyone connected it
    to ``uv``.

    Stripping ``VIRTUAL_ENV`` does not restrict agents. Project commands --
    ``uv run pytest``, ``uv run ruff``, ``make validate`` -- resolve the
    project's own ``.venv`` from the working directory, which is what an agent
    in the target repo should be using anyway. Only the install-into-the-active
    -environment commands change behaviour, and those now land in the target's
    own venv instead of the harness's.

    Preferred over a ``PreToolUse`` hook that blocks the dangerous verbs: this
    removes the cause rather than policing the symptom, cannot block legitimate
    work, and cannot be sidestepped by a command shape nobody enumerated
    (``pip install``, ``python -m pip``, ``uv pip --python``, and so on).

    Args:
        base_env: The environment to derive from, normally ``os.environ``.

    Returns:
        A plain ``dict`` copy with :data:`_AGENT_ENV_STRIPPED_VARS` removed.
        Absent names are not an error -- a directly-invoked interpreter never
        sets ``VIRTUAL_ENV`` in the first place.
    """
    return {key: value for key, value in base_env.items() if key not in _AGENT_ENV_STRIPPED_VARS}


def _drop_console_log_handlers_after_redirect() -> None:
    """Detach console log handlers once std streams point at the log file.

    Called by :func:`_daemonize_to_background` immediately after its ``dup2``
    redirect. A ``StreamHandler`` on ``sys.stderr`` and the ``FileHandler`` on
    the aggregate log are two handlers writing one file once fd 2 has been
    pointed at that file, so every record is emitted twice.

    ``logging.FileHandler`` subclasses ``logging.StreamHandler``, so the
    isinstance test must exclude it explicitly -- removing file handlers here
    would silence the daemon's log entirely rather than merely de-duplicate it.

    Nothing stops being captured: genuine writes to fd 2 (uncaught tracebacks,
    subprocess stderr) still reach the log through the redirect itself. Only
    the logging module's second copy is dropped.
    """
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)
            handler.close()


def _daemonize_to_background(workspace_root: Path) -> None:
    """Double-fork the current process into the background (#209 ``--daemon``).

    The grandchild process detaches from the controlling terminal, becomes a
    session leader, and redirects stdin / stdout / stderr to
    ``<workspace_root>/logs/orchestrator.log`` (append).  The original
    invoking shell sees the parent exit immediately and the terminal is
    freed.  The grandchild proceeds with the normal ``cmd_start`` body --
    PID file write, plugin shadow materialise, SDK init, orchestrate skill.

    Only called from ``cmd_start`` when ``--daemon`` is set.  No-op on
    Windows (POSIX-only); the operator will see an actionable error if
    they try it.

    Args:
        workspace_root: Workspace root whose ``logs/orchestrator.log`` the
            grandchild redirects stdout / stderr to.
    """
    if os.name != "posix":
        raise RuntimeError("--daemon requires POSIX (fork() not available on this platform)")

    log_dir = workspace_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "orchestrator.log"

    # First fork: parent exits, child continues.
    pid = os.fork()
    if pid > 0:
        # Original parent: tell the operator the daemon PID + exit cleanly so
        # the invoking shell returns to the prompt.  We can only print the
        # FIRST-fork PID here; the grandchild will write the authoritative
        # PID file once it starts up.
        print(
            f"started devbench orchestrator in daemon mode (parent pid {pid}); "
            f"follow logs with: devbench tail <instance_id> --follow",
            flush=True,
        )
        os._exit(0)

    # Become session leader so we detach from any controlling terminal.
    os.setsid()

    # Second fork: prevent re-acquiring a controlling terminal.
    pid = os.fork()
    if pid > 0:
        # First-fork child: exit so the grandchild's parent becomes init.
        os._exit(0)

    # Grandchild: redirect std streams to the log file.  Append-mode so the
    # existing orchestrator.log (read by ``devbench report``, ``devbench tail``)
    # keeps accumulating across daemon restarts.
    sys.stdout.flush()
    sys.stderr.flush()
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    null_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(null_fd)
    os.close(log_fd)

    # fd 2 now points AT the log file, so the stderr StreamHandler that
    # ``setup_logging`` attached and the FileHandler writing ``log_path`` have
    # become two handlers on one file -- and every record is emitted twice.
    # ``setup_logging`` runs at CLI entry, before this redirect, so it cannot
    # see the collision coming; it already refuses to attach the per-session
    # handler when that path equals the aggregate log, for exactly this reason.
    # This is the same guard, applied to the case the redirect creates.
    #
    # Measured before the fix: 152,276 lines in one workspace's log against
    # 78,191 distinct, i.e. ~1.93x, every day since the log began. One-shot CLI
    # invocations were never affected (their stderr is the terminal), which is
    # why the doubling only ever appeared under ``--daemon``.
    #
    # Only the logging module's duplicate copy goes away. Genuine writes to
    # fd 2 -- uncaught tracebacks, subprocess stderr -- still land in the log
    # through the dup2 above, so nothing stops being captured.
    _drop_console_log_handlers_after_redirect()


def _parse_start_args(argv: tuple[str, ...]) -> _CmdStartArgs | int:
    """Parse ``cmd_start`` flags from ``argv``.

    Accepted flags::

        --include "<tokens>"   Comma-separated scope-filter include tokens.
        --exclude "<tokens>"   Comma-separated scope-filter exclude tokens.
        --name "<name>"        Named-session identifier (default: ``SESSION_DEFAULT_NAME``).
        --allow-overlap        Boolean flag; when present, scope overlap with active
                               sessions emits a warning but does not abort.

    Args:
        argv: Trailing arguments after the ``start`` command name (may be empty).

    Returns:
        A populated ``_CmdStartArgs`` on success, or an integer exit code on error.

    Raises:
        Nothing -- all errors are printed to stderr and returned as rc=1.
    """
    include = ""
    exclude = ""
    name = SESSION_DEFAULT_NAME
    allow_overlap = False
    daemon = False
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--include":
            if i + 1 >= len(args):
                print("ERROR: --include requires a value", file=sys.stderr)
                return 1
            include = args[i + 1]
            i += 2
        elif arg == "--exclude":
            if i + 1 >= len(args):
                print("ERROR: --exclude requires a value", file=sys.stderr)
                return 1
            exclude = args[i + 1]
            i += 2
        elif arg == "--name":
            if i + 1 >= len(args):
                print("ERROR: --name requires a value", file=sys.stderr)
                return 1
            name = args[i + 1]
            i += 2
        elif arg == "--allow-overlap":
            allow_overlap = True
            i += 1
        elif arg in ("--daemon", "-d"):
            daemon = True
            i += 1
        else:
            print(f"ERROR: unknown flag for 'start': {arg!r}", file=sys.stderr)
            return 1
    return _CmdStartArgs(include=include, exclude=exclude, name=name, allow_overlap=allow_overlap, daemon=daemon)


def _write_session_state_files(
    workspace_root: Path,
    session_name: str,
    pid: int,
    scope_ids: list[str],
) -> None:
    """Create the per-session state directory and write all required files.

    Creates ``<workspace_root>/.devbench/sessions/<session_name>/`` and writes:

    - ``pid`` -- the process ID (plain integer text).
    - ``started_at`` -- UTC ISO-8601 timestamp of when the session was created.
    - ``started_by`` -- OS username of the process owner.
    - ``scope.json`` -- canonical ``ScopeFilter`` payload, written only when
      the session is scoped. An unscoped session writes no file, because
      absent is the unscoped signal every reader already honours (issue #270).

    Also registers the session in the
    ``<workspace_root>/.devbench/sessions/registry.json`` via
    :class:`~devbench.session.SessionRegistry`.

    Args:
        workspace_root: Root directory of the devbench workspace.
        session_name: Short identifier for this session (e.g. ``"default"``).
        pid: OS process ID to record.
        scope_ids: Expanded list of work-unit IDs for this session's scope.

    Raises:
        OSError: A file or directory write fails.
        ValueError: *session_name* contains ``..`` path segments.
    """
    if ".." in Path(session_name).parts:
        raise ValueError(
            f"session_name contains invalid path segment '..': {session_name!r}. "
            "Use a simple alphanumeric name without directory traversal."
        )

    state_dir = workspace_root / ".devbench" / "sessions" / session_name
    state_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(UTC)
    started_by = getpass.getuser()

    # pid
    (state_dir / "pid").write_text(str(pid), encoding="utf-8")

    # started_at
    (state_dir / SESSION_STARTED_AT_FILENAME).write_text(started_at.isoformat(), encoding="utf-8")

    # started_by
    (state_dir / SESSION_STARTED_BY_FILENAME).write_text(started_by, encoding="utf-8")

    # scope.json -- issue #270. This wrote a bare JSON array of IDs while
    # every reader (``ScopeFilter.from_file``, ``_read_scope_payload``)
    # requires the canonical object with include / exclude / expanded_ids /
    # started_at / started_by. The two shapes land on the SAME path, because
    # ``resolve_scope_file_path`` routes to this per-session file whenever
    # DEVBENCH_SESSION_NAME is set, so the array overwrote the object and
    # every subsequent read raised
    # "scope.json top-level payload must be an object, got 'list'".
    # Unscoped runs wrote "[]" and hit it on the next status call.
    #
    # An unscoped session writes no file at all: absent is the documented
    # unscoped signal (``_read_scope_payload`` returns None, and
    # ``ScopeFilter.from_file`` raises FileNotFoundError for callers that
    # require one). Writing an empty scope would instead assert a scope that
    # matches nothing, which is a different and much worse claim.
    scope_path = state_dir / "scope.json"
    if scope_ids:
        ScopeFilter(include=[], exclude=[], expanded_ids=set(scope_ids)).to_file(workspace_root, path=scope_path)
    elif scope_path.exists():
        scope_path.unlink()

    # registry.json -- add or update this session
    registry = SessionRegistry(workspace_root)
    sessions = registry.load()
    # Replace any existing entry with the same name (re-start scenario)
    sessions = [s for s in sessions if s.name != session_name]
    sessions.append(
        Session(
            name=session_name,
            pid=pid,
            scope=scope_ids,
            started_at=started_at,
            started_by=started_by,
            state_dir=state_dir,
        )
    )
    registry.save(sessions)


def _check_scope_overlap(
    workspace_root: Path,
    scope_ids: list[str],
    allow_overlap: bool,
) -> int | None:
    """Check whether *scope_ids* overlaps with any active session's scope.

    Loads the session registry from *workspace_root* and calls
    :func:`~devbench.session.detect_scope_overlap`.  When overlap is found:

    - If *allow_overlap* is ``True``: prints a ``WARNING`` to stderr and
      returns ``None`` (caller should proceed).
    - If *allow_overlap* is ``False``: prints an ``ERROR`` to stderr and
      returns ``1`` (caller should abort with rc=1).

    When no overlap is found (or *scope_ids* is empty), returns ``None`` to
    signal that the caller should proceed normally.

    Args:
        workspace_root: Root directory of the devbench workspace.
        scope_ids: Expanded list of work-unit IDs for the new session.
        allow_overlap: When ``True``, overlap emits a warning; when ``False``,
            overlap causes an error return.

    Returns:
        ``None`` when the caller should proceed; ``1`` when the caller should
        abort immediately with exit code 1.

    Raises:
        ValueError: ``SessionRegistry.load()`` found invalid JSON in the registry file.
        TypeError: ``detect_scope_overlap()`` received a ``None`` input instead of a list.
    """
    registry = SessionRegistry(workspace_root)
    existing_sessions = registry.load()
    overlapping_ids = detect_scope_overlap(existing_sessions, scope_ids)
    if not overlapping_ids:
        return None

    # Build a map of conflicting ID -> owning session name for the message.
    id_to_sessions: dict[str, list[str]] = {}
    for session in existing_sessions:
        for wu_id in session.scope:
            if wu_id in overlapping_ids:
                id_to_sessions.setdefault(wu_id, []).append(session.name)
    conflict_lines = ", ".join(
        f"{wu_id} (owned by: {', '.join(sorted(names))})" for wu_id, names in sorted(id_to_sessions.items())
    )
    if allow_overlap:
        print(
            f"WARNING: scope overlap detected with active sessions -- "
            f"conflicting IDs: {conflict_lines}. Proceeding because --allow-overlap was passed.",
            file=sys.stderr,
        )
        return None
    print(
        f"ERROR: scope overlap detected -- the following work-unit IDs are already "
        f"claimed by an active session: {conflict_lines}. "
        f"Pass --allow-overlap to start anyway.",
        file=sys.stderr,
    )
    return 1


def _check_auto_restart_and_notify(current_reason: str) -> tuple[int, str]:
    """Decide the post-clean-exit return code and update the stop reason.

    Returns ``(rc, updated_reason)``.  Extracted from ``cmd_start`` so the
    function's branch count stays under the ruff PLR0912 ceiling.  Fires
    the auto-restart notification when ``_should_auto_restart_after_no_actionable``
    says we should restart.
    """
    should_restart, degraded_ids = _should_auto_restart_after_no_actionable()
    if not should_restart:
        return 0, current_reason
    logger.info("%s%s", ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX, ",".join(degraded_ids))
    from devbench.notifications import notify_orchestrator_auto_restart

    notify_orchestrator_auto_restart(list(degraded_ids))
    return ORCHESTRATOR_RESTART_EXIT_CODE, "auto-restart (RUNTIME_DEGRADATION-only NO_ACTIONABLE)"


def _extract_sdk_result_text(message: object) -> str | None:
    """Return the SDK ``ResultMessage.result`` text from ``message`` if any.

    Issue #217: ``cmd_start`` uses this to capture the orchestrate skill's
    end-of-run summary (``NO_ACTIONABLE -- 190/212 done, 11 blocked``,
    ``ALL_DONE``, etc.) and surface it in the ``orchestrator_stop`` Slack
    ping so the operator can tell at a glance whether the backlog finished
    or stalled.

    Returns the ``result`` string when ``message`` is a SDK ResultMessage
    carrying a non-empty string ``result``; ``None`` otherwise.  Duck-typed
    so unit tests can yield bare ``object`` instances with a ``result``
    attribute without importing the SDK.
    """
    candidate = getattr(message, "result", None)
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


#: Issue #218: the three terminal sentinels the orchestrate skill emits
#: at end-of-run (per ``plugin/devbench-orchestrate/skills/orchestrate/SKILL.md``
#: lines 8, 32, 35-36).  ``NO_ACTIONABLE_IN_SCOPE`` is caught by the
#: substring check on ``NO_ACTIONABLE``, so only two distinct tokens
#: live in the tuple.
_TERMINAL_ORCHESTRATE_MARKERS: tuple[str, ...] = ("ALL_DONE", "NO_ACTIONABLE")

#: Audit-log prefix written to the orchestrator log when the SDK loop
#: detects a terminal-marker ResultMessage and breaks early.  Mirrors the
#: shape of ``_ORCHESTRATOR_DRAIN_ENFORCED_AUDIT_PREFIX``.  Issue #218.
_ORCHESTRATOR_TERMINAL_EXIT_AUDIT_PREFIX: str = "[ORCHESTRATOR_TERMINAL_EXIT] reason="


def _is_terminal_orchestrate_result(text: str | None) -> bool:
    """Issue #218: True iff ``text`` carries one of the orchestrate
    skill's three terminal sentinels (``ALL_DONE`` / ``NO_ACTIONABLE`` /
    ``NO_ACTIONABLE_IN_SCOPE``).  Used by ``_run`` to break the SDK
    iterator early so the orchestrator does not burn ~$0.07/turn
    re-invoking the model after the skill has signalled end-of-run.
    """
    if not text:
        return False
    return any(marker in text for marker in _TERMINAL_ORCHESTRATE_MARKERS)


def _log_terminal_exit_if_applicable(text: str | None) -> bool:
    """Issue #218: log the ``[ORCHESTRATOR_TERMINAL_EXIT]`` audit line
    when ``text`` matches a terminal sentinel and return True.  Returns
    False otherwise.  Factored out of ``_run`` so the SDK-iterator loop
    stays under ruff PLR0912's 12-branch cap while still tearing down
    the iterator on terminal markers.
    """
    if not _is_terminal_orchestrate_result(text):
        return False
    logger.info("%s%s", _ORCHESTRATOR_TERMINAL_EXIT_AUDIT_PREFIX, text)
    return True


#: db-271 (spec FR-18 Part A): the distinct label for a premature turn end
#: -- a clean SDK loop exit (``StopAsyncIteration``, no drain/quota/inactivity
#: exhaustion) that captured no terminal-sentinel ``ResultMessage``
#: (``_sdk_result_text`` stayed empty).  Previously this bucket kept the bare
#: ``"clean"`` seed all the way to the Slack ping, indistinguishable from a
#: finished run.  Coordinate wording changes with the FR-17
#: inactivity-timeout owner: this string is assigned to ``_stop_reason``
#: only after the SDK loop has already exited, so it is never fed back
#: through :func:`_is_terminal_orchestrate_result` as if it were a second
#: SDK turn -- keep it that way.
_PREMATURE_TURN_END_REASON: str = (
    "premature turn end -- SDK loop exhausted with no terminal sentinel (ALL_DONE / NO_ACTIONABLE)"
)


def _label_stop_reason(exc: BaseException) -> str:
    """Return a human-readable label for the orchestrator's exit (#213).

    Distinguishes clean exits from real crashes so the ``orchestrator_stop``
    Slack ping carries an accurate label:

    - ``SystemExit(0)`` (or ``SystemExit()`` with no code) -> clean exit.
      Raised by ``sys.exit(0)`` or the SIGTERM handler when the orchestrator
      finishes naturally (NO_ACTIONABLE / ALL_DONE) or the operator drains
      cleanly.
    - ``KeyboardInterrupt`` -> operator interrupt (Ctrl+C / SIGINT).  Not a
      crash; the operator chose to stop the run.
    - :class:`_OrchestrateTransportError` -> ``"transport-error-restart-cap-exhausted"``
      (#331 spec FR-3, AC-10). Reaches this helper only after
      :func:`_drive_orchestrate_with_quota_resume` has exhausted the bounded
      transport-restart cap and re-raised, so this label is unambiguous.
    - Anything else (including ``SystemExit`` with non-zero code, unhandled
      exceptions) -> crash with the exception type + message.

    Before this helper, ``cmd_start``'s outer except-clause labeled every
    BaseException as ``"crash: <type>: <msg>"``, so a clean exit fired
    Slack pings with the wrong wording (``crash: SystemExit: 0``).
    """
    if isinstance(exc, SystemExit) and (exc.code is None or exc.code == 0):
        return "clean exit (SystemExit 0)"
    if isinstance(exc, KeyboardInterrupt):
        return "interrupted by operator (Ctrl+C / SIGINT)"
    if isinstance(exc, _OrchestrateTransportError):
        return "transport-error-restart-cap-exhausted"
    return f"crash: {type(exc).__name__}: {exc}"


def _resolve_clean_stop_reason(current_reason: str, sdk_result_text: str | None) -> str:
    """Resolve the final stop-reason label after a clean SDK loop exit.

    Three-way resolution (issue #217 / db-271 spec FR-18 Part A), extracted
    from ``cmd_start`` so its branch count stays under ruff's PLR0912
    ceiling:

    1. ``sdk_result_text`` actually CARRIES a terminal sentinel
       (:func:`_is_terminal_orchestrate_result`) -> ``"clean exit: <text>"``
       (issue #217: surfaces the orchestrate skill's ``ALL_DONE`` /
       ``NO_ACTIONABLE -- 190/212 done, 11 blocked`` end-of-run summary).
    2. Otherwise, when ``current_reason`` is still the ``"clean"`` seed ->
       the distinct :data:`_PREMATURE_TURN_END_REASON` (db-271), with any
       non-terminal result text appended verbatim for diagnosis.
    3. Otherwise, ``current_reason`` already carries the loop-provided
       drain / quota disposition -- returned unchanged.

    Rule 1 tests the text for a terminal sentinel rather than merely for
    being non-empty. The original truthy check (PR #202) predates the
    premature-turn-end bucket and made rule 2 unreachable whenever the model
    said ANYTHING before stopping: an observed run ended on the model's own
    narration ("the executor agent is running in the background... I'll
    continue automatically", a capability devbench does not have) and that
    narration was reported to the operator as ``clean exit``, indistinguishable
    from a finished backlog. Only the two sentinels the orchestrate skill
    actually emits at end-of-run may claim a clean exit; the text is retained
    on the premature path so the operator sees what the model claimed instead
    of losing the only diagnostic.

    Args:
        current_reason: The ``_stop_reason`` value accumulated so far.
        sdk_result_text: The last captured ``ResultMessage.result`` text, or
            ``None`` / empty when the SDK never emitted one.

    Returns:
        The resolved stop-reason label.
    """
    if _is_terminal_orchestrate_result(sdk_result_text):
        return f"clean exit: {sdk_result_text}"
    if current_reason == "clean":
        if sdk_result_text:
            return f"{_PREMATURE_TURN_END_REASON}; last SDK result text: {sdk_result_text}"
        return _PREMATURE_TURN_END_REASON
    return current_reason


def _fire_orchestrator_stop_notification(reason: str) -> None:
    """Best-effort always-fire of the ``orchestrator_stop`` notification.

    Wraps the lookup + dispatch in a broad try/except so a buggy
    notification import or a transient backlog-parser failure during
    cmd_start's outer try/finally cannot mask the real exit reason.
    Extracted from ``cmd_start`` body so the branch-count of that
    function stays under the project's ruff PLR0912 ceiling (12).

    db-271 (spec FR-18 Part C): also computes a ``(done, total)`` progress
    tuple over every work unit in the backlog index and passes it through so
    the Slack ping's ``Progress`` field reads ``X/Y done``.  On a backlog
    parse failure the progress computation degrades to ``None`` (the
    ``Progress`` field is omitted from the payload) and the failure is
    logged to stderr -- never silently swallowed -- while the in-flight
    lookup keeps its existing best-effort fallback so a parse failure never
    masks the real exit reason.
    """
    try:
        from devbench.notifications import notify_orchestrator_stop

        in_flight_id: str | None = None
        progress: tuple[int, int] | None = None
        try:
            stop_parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
            stop_units = stop_parser.parse_index()
            stop_wu = _find_in_flight_wu(stop_units)
            in_flight_id = stop_wu.id if stop_wu is not None else None
            progress = (
                sum(unit.status is WorkUnitStatus.DONE for unit in stop_units),
                len(stop_units),
            )
        except (OSError, ValueError) as exc:
            in_flight_id = None
            progress = None
            print(
                f"[WARN] orchestrator-stop progress lookup failed: {exc!r}",
                file=sys.stderr,
            )
        notify_orchestrator_stop(reason, in_flight_id, progress=progress)
    except Exception as exc:  # broad guard: notification must never mask real exit
        print(
            f"[WARN] orchestrator-stop notification failed: {exc!r}",
            file=sys.stderr,
        )


def _setup_daemon_and_pid_file(parsed: _CmdStartArgs) -> None:
    """Handle daemonisation (when ``--daemon``) and PID-file write (#209).

    Extracted from ``cmd_start`` to keep its branch count under the ruff
    PLR0912 threshold.  Best-effort PID-file write: a failure logs ``[WARN]``
    on stderr and continues (the orchestrator still runs, just won't be
    enumerable via ``devbench instances`` until next start).
    """
    from devbench.instances import write_pid_file

    # Checked BEFORE daemonising, so a re-pointed install fails on the
    # operator's terminal rather than disappearing into the log of a run that
    # is already executing the wrong codebase.
    _assert_running_package_matches_its_venv()

    if parsed.daemon:
        _daemonize_to_background(WORKSPACE_ROOT)
    try:
        write_pid_file(
            WORKSPACE_ROOT,
            os.getpid(),
            session=parsed.name,
            mode="daemon" if parsed.daemon else "foreground",
            model=os.environ.get("DEVBENCH_CLAUDE_MODEL", ""),
        )
    except OSError as exc:
        print(f"[WARN] failed to write orchestrator PID file: {exc}", file=sys.stderr)


def _write_last_restart_marker(workspace_root: Path) -> None:
    """Write the orchestrator restart marker (#215).

    Records the current UTC timestamp at
    ``<workspace>/.devbench/last-restart`` so that
    :func:`devbench.backlog.proposal._has_runtime_degradation_signal`
    only counts agent-tool-unavailable audit rows newer than this
    timestamp.  Without the marker, RUNTIME_DEGRADATION classification
    would persist across restarts for up to 24 hours, defeating the
    auto-restart recovery loop wired by
    :func:`_should_auto_restart_after_no_actionable`.

    Best-effort: directory creation / write failures are logged and the
    orchestrator continues.  Worst case is fallback to the existing 24h
    sliding-window classifier behaviour.
    """
    from datetime import UTC, datetime

    from devbench.constants import LAST_RESTART_MARKER_PATH

    marker = workspace_root / LAST_RESTART_MARKER_PATH
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] failed to write orchestrator restart marker: {exc}", file=sys.stderr)


def cmd_start(*argv: str) -> int:
    """Run the devbench orchestrate skill non-interactively via the Claude Agent SDK.

    Loads the devbench plugin from the plugin directory adjacent to this package
    and invokes the orchestrate skill, which processes the backlog until all
    work units are complete or blocked.

    Accepts optional scope-filter flags (spec section 4.2.2, AC-190-8, AC-190-9):

    - ``--include "<tokens>"`` -- comma-separated printer-pages-style tokens.
      When supplied, ``ScopeFilter`` is built from the current backlog's WU IDs,
      persisted to ``<workspace>/.devbench/scope.json``, and the
      ``DEVBENCH_SCOPE_FILE`` env var is set to that path before the SDK run.
    - ``--exclude "<tokens>"`` -- comma-separated tokens subtracted from the
      include set.  Only meaningful together with ``--include``.

    When ``--include`` is absent or empty, all work units are eligible (current
    behavior preserved, no scope.json written).

    On clean SDK return, ``ScopeFilter.clear(workspace_root)`` is called to
    remove any active scope.json (AC-190-13).  When the SDK raises (crash
    path), scope.json is intentionally left on disk for operator inspection.

    When ``agents.*`` overrides are configured in ``devbench.yaml`` (or via
    ``JUDGE_AGENT_MODEL_*`` env vars), a workspace-local shadow plugin tree
    is materialised at ``<workspace>/.devbench/plugin-shadow/devbench/`` and
    passed to the SDK in place of the canonical plugin (ADR-25).

    On clean SDK return, inspects the backlog post-mortem: when the only
    reason the orchestrator stopped is one or more
    ``BlockedTaskState.RUNTIME_DEGRADATION`` tasks (SDK lost Agent-tool
    access mid-session) and no IN_PROGRESS / IN_REVIEW / OPERATOR_ACTION_REQUIRED
    work is pending, writes an audit line to the orchestrator log and
    returns :data:`ORCHESTRATOR_RESTART_EXIT_CODE` (42). The wrapping
    ``make start`` loop interprets that code as "auto-restart" up to its
    ``DEVBENCH_MAX_AUTO_RESTARTS`` cap. Any other exit returns 0.

    **Drain enforcement (spec section 4.3.3, AC-188-4/5/8):** Between SDK
    messages, the inner ``_run`` coroutine calls :func:`_check_quota_and_drain`
    once per message, which checks whether a ``devbench claim`` Bash tool-use
    is being requested.  When one is detected AND the drain signal file is
    present, it raises :class:`_DrainRequested`. ``cmd_start`` catches this
    sentinel, consumes the drain marker, logs a
    ``[ORCHESTRATOR_DRAIN_ENFORCED]`` audit entry, and returns 0.

    **Quota detection and in-process resume (spec AC-20, AC-21, AC-22,
    FR-2.7-2.10, decisions D-4/D-6, issue #236):** The same per-message call
    also raises :class:`_QuotaDetected` -- a :class:`BaseException`
    subclass, not :class:`Exception` -- the moment a quota / rate-limit
    signal is recognized in the SDK message stream. ``cmd_start`` drives the
    whole SDK run through :func:`_drive_orchestrate_with_quota_resume`,
    which wraps ``asyncio.run(_run())`` in a loop: on ``_QuotaDetected`` it
    dispatches via :func:`_dispatch_quota_detection`, runs
    :func:`_handle_quota_pause` while waiting, and -- when the quota window
    recovers before ``on_exhaustion_timeout`` -- re-enters a **fresh** SDK
    session (decision D-6: no stateful-client conversation resume; context
    flows only through the on-disk backlog, never through SDK session
    state) up to ``DEVBENCH_MAX_QUOTA_RESUMES`` (default
    :data:`DEFAULT_MAX_QUOTA_RESUMES`) consecutive resumes before stopping.
    Non-recovering dispositions (timeout, drain-requested) and drain
    enforcement (:class:`_DrainRequested`) both terminate the loop; when the
    disposition requested a drain, that intent survives this function's
    exit-path cleanup via :func:`_cancel_drain_unless_requested` so the next
    ``devbench start`` invocation still honours it.

    **Inactivity net and cooperative teardown (spec FR-17, db-262 + db-325):**
    ``_run`` awaits ``asyncio.wait_for(agen.__anext__(),
    timeout=_ORCH_INACTIVITY_TIMEOUT)`` per message instead of a bare
    ``async for`` -- when a turn ends without a terminal sentinel and
    produces no follow-up message, the wait times out and ``_run`` raises
    :class:`_OrchestrateInactivityTimeout` (previously this idled the
    orchestrator forever). ``_run``'s ``finally: await agen.aclose()`` always
    runs first, so both this sentinel and :class:`_QuotaDetected` unwind
    through cooperative teardown -- driving the SDK's own subprocess
    teardown -- before ``_drive_orchestrate_with_quota_resume`` disposes
    them. On inactivity, that disposition is a bounded fresh-session restart
    reusing the SAME ``DEVBENCH_MAX_QUOTA_RESUMES`` cap (tracked
    independently of quota resumes via
    :func:`_should_restart_after_inactivity_timeout`); fail-fast (legacy
    non-zero exit) once that cap is exhausted.

    **Transport-error boundary and bounded restart (#331 spec FR-1/FR-2/FR-3):**
    Any OTHER exception raised by ``agen.__anext__()`` -- one that is neither
    ``StopAsyncIteration`` nor ``TimeoutError`` -- is re-raised by ``_run`` as
    :class:`_OrchestrateTransportError`, carrying the original exception as
    ``__cause__``. Classification is structural, never message-based (decision
    D-4): an upstream frame that is simultaneously ``is_error=True`` and
    ``subtype="success"`` with an empty ``errors`` list previously surfaced as
    the literal string ``"success"``, which no sensible pattern would match.
    Only the ``agen.__anext__()`` boundary itself is wrapped -- never the rest
    of the loop body -- so a genuine devbench defect still fails loudly
    instead of being silently retried (decision D-5).
    ``_drive_orchestrate_with_quota_resume`` disposes this sentinel exactly
    like the inactivity timeout above: it logs the verbatim exception at
    ERROR with its restart ordinal and the cap, then either restarts a fresh
    session (bounded by the SAME ``DEVBENCH_MAX_QUOTA_RESUMES`` cap, tracked
    independently of quota resumes and inactivity restarts via
    :func:`_should_restart_after_transport_error`) or re-raises -- preserving
    the original exception as ``__cause__`` -- once that cap is exhausted.
    On exhaustion, :func:`_label_stop_reason` labels the exit
    ``"transport-error-restart-cap-exhausted"`` (spec FR-3) and the
    always-fire ``orchestrator_stop`` notification below still fires.

    Equivalent to running ``claude --plugin-dir <plugin>`` and invoking
    the orchestrate skill interactively, but suitable for CI/unattended runs.

    Args:
        *argv: Optional flags (``--include``, ``--exclude``, ``--name``,
            ``--allow-overlap``).

    Returns:
        0 on success (including drain-enforced stop and quota-driven stops),
        1 on argument-parse error, invalid scope token, or scope overlap
        without ``--allow-overlap``, :data:`ORCHESTRATOR_RESTART_EXIT_CODE`
        (42) when auto-restart is triggered.

    Raises:
        :class:`~devbench.quota.QuotaExhaustedError`: Propagates from
            :func:`_drive_orchestrate_with_quota_resume` (via
            :func:`_dispatch_quota_detection`) when ``quota_handling`` is
            disabled or the configured ``on_exhaustion`` /
            ``on_exhaustion_timeout`` disposition is ``fail`` -- the
            documented operator escape hatch back to the legacy non-zero
            exit.
        ValueError: Propagates from :func:`_drive_orchestrate_with_quota_resume`
            (via :func:`~devbench.quota._apply_resume_strategy`) when a
            recovered wait's configured ``resume_strategy`` is not one of the
            three recognised values.
        :class:`_OrchestrateInactivityTimeout`: Propagates from
            :func:`_drive_orchestrate_with_quota_resume` once the bounded
            inactivity-restart cap is exhausted (legacy non-zero exit,
            mirroring the quota ``fail`` disposition above).
        :class:`_OrchestrateTransportError`: Propagates from
            :func:`_drive_orchestrate_with_quota_resume` once the bounded
            transport-restart cap is exhausted (#331 spec FR-1/FR-2, legacy
            non-zero exit, carrying the original SDK-boundary exception as
            ``__cause__``).
        Nothing else from this function's own scope for quota / drain /
        inactivity / transport signals -- all four dispositions are otherwise
        fully handled by :func:`_drive_orchestrate_with_quota_resume`.
        ``SystemExit``, ``KeyboardInterrupt``, and :class:`asyncio.CancelledError`
        propagate as-is (spec AC-3).
    """
    from claude_agent_sdk import ClaudeAgentOptions, query

    from devbench.instances import remove_pid_file

    parsed = _parse_start_args(argv)
    if isinstance(parsed, int):
        return parsed

    # Issue #209: daemon mode + PID file management.  Daemonisation must
    # happen BEFORE any heavy work so the parent's exit feels instant; the
    # PID file is written by the grandchild (or foreground process) and
    # cleaned up in the try/finally below.
    _orchestrator_pid_workspace = WORKSPACE_ROOT
    _setup_daemon_and_pid_file(parsed)
    # Issue #215: write the last-restart marker so
    # ``classify_blocked_task`` can bound the agent-tool-unavailable audit
    # scan to rows emitted by this fresh orchestrator instance only.
    # RUNTIME_DEGRADATION audit rows from the previous (now-stopped)
    # instance MUST not keep the new instance's tasks bucketed there.
    _write_last_restart_marker(WORKSPACE_ROOT)

    # Determine the scope IDs for this session (empty when no --include).
    scope_ids: list[str] = []

    # When --include is supplied and non-empty, build + persist a ScopeFilter.
    if parsed.include:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
        backlog_ids = [u.id for u in units]
        try:
            scope = ScopeFilter.parse(parsed.include, parsed.exclude, backlog_ids)
        except InvalidScopeError as exc:
            print(f"ERROR: invalid scope token: {exc}", file=sys.stderr)
            return 1
        scope_file = scope.to_file(WORKSPACE_ROOT)
        os.environ["DEVBENCH_SCOPE_FILE"] = str(scope_file)
        scope_ids = sorted(scope.expanded_ids)

    # AC-192-4: Scope-overlap detection -- before claiming the registry slot,
    # consult active sessions.  When scope_ids is empty (no --include) there
    # is nothing to overlap, so skip the check entirely.
    if scope_ids:
        overlap_rc = _check_scope_overlap(WORKSPACE_ROOT, scope_ids, parsed.allow_overlap)
        if overlap_rc is not None:
            return overlap_rc

    # AC-192-1/2: Create the per-session state directory and register the session.
    # This must happen before the SDK run so that concurrent sessions can detect
    # each other via the registry.
    _write_session_state_files(WORKSPACE_ROOT, parsed.name, os.getpid(), scope_ids)

    plugin_path = _resolve_plugin_path()

    # When the resolver returned the shadow path (overrides configured),
    # record this orchestrator's PID inside the shadow tree. The sentinel
    # makes clear_shadow_plugin refuse to delete the tree while this
    # process is alive -- preventing a stray prepare-plugin-shadow
    # invocation from clearing the shadow out from under the running SDK
    # subprocess and silently breaking hook telemetry mid-run (ADR-25
    # sentinel-protected lifecycle).
    if plugin_path == shadow_plugin_path(WORKSPACE_ROOT):
        write_pid_sentinel(WORKSPACE_ROOT, os.getpid())

    # Set DEVBENCH_SESSION_NAME for the duration of the SDK run.  Use a
    # try/finally to restore the previous value so test isolation is maintained
    # (the orchestrator process is long-lived in tests).
    _prev_session_name = os.environ.get("DEVBENCH_SESSION_NAME")
    os.environ["DEVBENCH_SESSION_NAME"] = parsed.name

    # AC-192-9: Register a SIGTERM handler so that ``devbench stop --session``
    # releases the in-flight work unit back to the queue before this process exits.
    # The handler reads the current backlog, finds the in-progress WU, sets it to
    # ``in-queue``, appends an ``[INTERRUPTED_ON_STOP]`` audit comment, then
    # exits rc=0.  The previous handler is restored in the finally block.
    _session_name_for_sigterm = parsed.name

    def _sigterm_handler(_signum: int, _frame: object) -> None:
        """SIGTERM handler: release the in-flight WU to the queue then exit.

        Reads the backlog, locates the single in-progress work unit (if any),
        transitions it to ``in-queue``, appends an
        ``[INTERRUPTED_ON_STOP] session=<name>`` audit entry, then calls
        ``raise SystemExit(0)`` so the ``finally`` block in ``cmd_start`` runs
        to restore ``DEVBENCH_SESSION_NAME``.

        Args:
            _signum: Signal number (SIGTERM) -- unused but required by the
                signal handler protocol.
            _frame: Current stack frame -- unused but required by the
                signal handler protocol.

        Raises:
            SystemExit: Always -- exits the process with rc=0.
        """
        try:
            parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
            units = parser.parse_index()
            wu = _find_in_flight_wu(units)
            _requeue_in_flight_wu(wu, session_name=_session_name_for_sigterm)
        except (OSError, ValueError) as exc:
            logger.error("[SIGTERM_HANDLER_ERROR] could not requeue in-flight WU: %s", exc)
        raise SystemExit(0)

    _prev_sigterm_handler = signal.signal(signal.SIGTERM, _sigterm_handler)

    # Issue #217: capture the SDK's final ResultMessage `result` text so the
    # orchestrator_stop Slack ping can carry the actual exit reason
    # (e.g., ``NO_ACTIONABLE -- 190/212 done, 11 blocked``) instead of the
    # legacy bare ``"clean"`` that hid whether the backlog was finished.
    _sdk_result_text: str | None = None

    async def _run() -> None:
        """Iterate SDK messages with an inactivity net and cooperative teardown.

        FR-17 (db-262 + db-325): rewritten as a ``try/finally`` around
        ``agen = query(...)``, awaiting
        ``asyncio.wait_for(agen.__anext__(), timeout=_ORCH_INACTIVITY_TIMEOUT)``
        per message instead of a bare ``async for``. Breaks the loop cleanly
        on ``StopAsyncIteration``; a timed-out wait raises
        :class:`_OrchestrateInactivityTimeout` (db-262 -- the SDK ended a turn
        without a terminal sentinel and produced no follow-up, which
        previously left the orchestrator idling forever). The ``finally``
        ALWAYS awaits ``agen.aclose()`` (suppressing aclose's own exceptions)
        so a quota or inactivity sentinel unwinds through cooperative teardown
        while the generator is suspended -- not running -- before it escapes
        this coroutine (db-325): this drives the SDK's own subprocess
        teardown before ``asyncio.run``'s ``shutdown_asyncgens()`` would
        otherwise hit the generator mid-flight. The sentinel re-raises after
        teardown, preserving the :class:`BaseException` contract.

        Any OTHER exception raised by ``agen.__anext__()`` -- an
        :class:`Exception` that is neither ``StopAsyncIteration`` nor
        ``TimeoutError`` -- is re-raised as :class:`_OrchestrateTransportError`
        with the original preserved as ``__cause__`` (#331 spec FR-1). This is
        deliberately narrow: ONLY the ``agen.__anext__()`` boundary is wrapped,
        never the rest of the loop body, so a genuine devbench defect (e.g. a
        bug in :func:`_check_quota_and_drain`) still propagates unwrapped and
        fails loudly instead of being silently retried (decision D-5).
        ``SystemExit``, ``KeyboardInterrupt``, and :class:`asyncio.CancelledError`
        are :class:`BaseException` subclasses that are not :class:`Exception`,
        so the ``except Exception`` clause that raises this sentinel never
        matches them -- they are never wrapped (spec AC-3).

        Per-message, calls :func:`_check_quota_and_drain` once, which raises
        :class:`_QuotaDetected` when a quota / rate-limit signal is observed
        (issue #236) or :class:`_DrainRequested` when a ``devbench claim``
        tool-use is detected while a drain is pending (issues #188/#212).
        Both sentinels -- and :class:`_OrchestrateInactivityTimeout` and
        :class:`_OrchestrateTransportError` -- are :class:`BaseException`
        subclasses (spec AC-20, decision D-4) so they propagate through
        ``asyncio.run`` without being caught by any broad ``except Exception``
        handler in between.

        Does NOT break on any ``ResultMessage``: the orchestrate skill emits
        one per turn across a single long ``query()`` (num_turns ~185); only
        the two ``_TERMINAL_ORCHESTRATE_MARKERS`` end the loop early, and the
        inactivity timeout -- not result-classification -- is the liveness
        lever (spec AC-40).

        Args: (none -- captures local variables from ``cmd_start`` closure)

        Raises:
            _QuotaDetected: A quota / rate-limit signal is observed in an SDK
                message.
            _DrainRequested: A drain signal is present when a ``cmd_claim``
                tool-use is observed.
            _OrchestrateInactivityTimeout: No SDK message arrived within
                ``_ORCH_INACTIVITY_TIMEOUT`` seconds of the previous one.
            _OrchestrateTransportError: ``agen.__anext__()`` raised any other
                exception (#331 spec FR-1).
        """
        nonlocal _sdk_result_text
        # The SDK's `query()` return-type annotation is the narrower
        # `AsyncIterator[...]` (no `aclose()`), but its actual runtime type is
        # always an async generator (the SDK implements it with `yield`).
        # Cast to `AsyncGenerator` so `agen.aclose()` below type-checks
        # without a bypass annotation.
        agen = cast(
            "AsyncGenerator[object, None]",
            query(
                prompt="Run the devbench-orchestrate:orchestrate skill to process the backlog until complete",
                options=ClaudeAgentOptions(
                    plugins=[{"type": "local", "path": str(plugin_path)}],
                    permission_mode="bypassPermissions",
                    env=_agent_environment(os.environ),
                    # Pinned rather than inherited. Without these the session
                    # picks up the ambient Claude Code effort, so an unattended
                    # run's cost profile depends on whatever the operator's
                    # last interactive session happened to be set to, and a
                    # turn that reasons past the prompt-cache lifetime returns
                    # to a cold cache and re-uploads the whole prompt.
                    effort=cast("EffortLevel", ORCHESTRATE_EFFORT),
                    max_thinking_tokens=ORCHESTRATE_MAX_THINKING_TOKENS,
                ),
            ),
        )
        try:
            while True:
                try:
                    message = await asyncio.wait_for(agen.__anext__(), timeout=_ORCH_INACTIVITY_TIMEOUT)
                except StopAsyncIteration:
                    # The generator is exhausted WITHOUT the loop having already
                    # returned on a terminal sentinel: a genuine end-of-run
                    # returns from _log_terminal_exit_if_applicable the moment
                    # ALL_DONE / NO_ACTIONABLE is observed. Reaching here means
                    # the model stopped talking without announcing why.
                    #
                    # Escalate to the bounded-restart path ONLY when the backlog
                    # still holds actionable work: that is the case the loop must
                    # never end on (it stops only on a terminal sentinel or an
                    # operator drain), and it was previously a bare ``break`` --
                    # the one failure mode with no recovery, while a model going
                    # silent for the inactivity window already earned a restart.
                    # With nothing actionable left, ending here IS the
                    # NO_ACTIONABLE condition, just discovered by us rather than
                    # announced by the model, so restarting would only re-derive
                    # the same answer at the cost of another session.
                    if _has_actionable_work_remaining():
                        raise _OrchestratePrematureTurnEnd(_sdk_result_text) from None
                    break
                except TimeoutError:
                    raise _OrchestrateInactivityTimeout(_ORCH_INACTIVITY_TIMEOUT) from None
                except Exception as exc:
                    # #331 FR-1: only the SDK generator boundary itself is
                    # classified as a transport error (decision D-5). Any
                    # BaseException that is not an Exception -- SystemExit,
                    # KeyboardInterrupt, asyncio.CancelledError -- does not
                    # match this clause and propagates unchanged (spec AC-3).
                    raise _OrchestrateTransportError(exc) from exc
                logger.info("sdk message: %s", message)
                _sdk_result_text = _extract_sdk_result_text(message) or _sdk_result_text
                if _log_terminal_exit_if_applicable(_sdk_result_text):
                    return
                _check_quota_and_drain(message)
        finally:
            # db-325: cooperative teardown -- always close the generator, but
            # never let aclose's OWN failure (e.g. a mid-flight SDK teardown
            # error) mask the sentinel that is unwinding through this frame.
            # Local aliased import (mirrors the outer `finally` blocks below):
            # `cmd_start` binds an unaliased `contextlib` local later in its
            # own body, which would otherwise shadow the module-level import
            # for this nested closure's free-variable lookup.
            import contextlib as _run_contextlib

            with _run_contextlib.suppress(Exception):
                await agen.aclose()

    # Always-fire on exit (PR #202): wrap the SDK loop + state-restoration
    # finally in an outer try/finally that calls notify_orchestrator_stop
    # regardless of how the function exits (clean, drain, SystemExit from
    # the SIGTERM handler, or an uncaught SDK exception).  The notify
    # helper is best-effort so a failure here cannot mask the original
    # exit reason.
    _stop_reason: str = "clean"
    _quota_drain_requested: bool = False
    try:
        try:
            _loop_result = _drive_orchestrate_with_quota_resume(_run, parsed.name)
            _quota_drain_requested = _loop_result.quota_drain_requested
            if _loop_result.terminal_rc is not None:
                _stop_reason = _loop_result.stop_reason
                return _loop_result.terminal_rc
        finally:
            # Issue #212: drop the drain signal on any exit from the SDK run so
            # the next start does not inherit a stale request.  Run while
            # DEVBENCH_SESSION_NAME is still set (before the restore below) so
            # cancel_drain scans both the per-session and workspace-root
            # candidate paths.  Idempotent on already-clean state.  When the
            # loop's disposition asked for a drain (spec AC-25), the request
            # must survive so the next ``devbench start`` still honours it --
            # see :func:`_cancel_drain_unless_requested`.
            import contextlib as _contextlib

            with _contextlib.suppress(OSError):
                _cancel_drain_unless_requested(WORKSPACE_ROOT, _quota_drain_requested)
            # Restore the previous DEVBENCH_SESSION_NAME value so test isolation is
            # maintained when cmd_start is invoked multiple times in the same process.
            if _prev_session_name is None:
                os.environ.pop("DEVBENCH_SESSION_NAME", None)
            else:
                os.environ["DEVBENCH_SESSION_NAME"] = _prev_session_name
            # Restore the previous SIGTERM handler so test isolation is maintained.
            signal.signal(signal.SIGTERM, _prev_sigterm_handler)

        # AC-190-13: delete scope.json on clean SDK exit so the next run starts
        # without a stale scope.  On crash (SDK raises), the exception propagates
        # before this line runs, intentionally leaving scope.json in place for
        # operator inspection.
        ScopeFilter.clear(WORKSPACE_ROOT)

        # Issue #217 / db-271: resolve the final stop-reason label after a
        # clean SDK loop exit.  Delegated to ``_resolve_clean_stop_reason``
        # (rather than an inline ``if``/``elif`` chain) so ``cmd_start``'s
        # branch count stays under ruff's PLR0912 cap.
        _stop_reason = _resolve_clean_stop_reason(_stop_reason, _sdk_result_text)

        restart_rc, _stop_reason = _check_auto_restart_and_notify(_stop_reason)
        return restart_rc
    except BaseException as exc:
        # Capture the exit reason for the always-fire notification before
        # re-raising.  ``BaseException`` covers SystemExit (SIGTERM) and
        # KeyboardInterrupt in addition to standard exceptions; see
        # _label_stop_reason for the bucketing rules (#213).
        _stop_reason = _label_stop_reason(exc)
        raise
    finally:
        _fire_orchestrator_stop_notification(_stop_reason)
        # Issue #209: drop the PID file on clean exit so a fresh start does
        # not trip the alive-check on a stale entry.  Best-effort: missing /
        # permission-denied is a no-op.
        import contextlib

        with contextlib.suppress(OSError, NameError):
            remove_pid_file(_orchestrator_pid_workspace)
        # Issue #212: drop the drain signal on any exit so the next start
        # does not inherit a stale request -- unless the quota-resume loop's
        # disposition asked for a drain (spec AC-25), in which case that
        # request must survive so the next ``devbench start`` still honours
        # it.  cancel_drain scans both the per-session and workspace-root
        # candidate paths and is idempotent on already-clean state.
        # Best-effort: NameError guards a very early exit before
        # WORKSPACE_ROOT (or ``_quota_drain_requested``) was set; OSError
        # covers permission-denied during unlink.
        with contextlib.suppress(OSError, NameError):
            _cancel_drain_unless_requested(WORKSPACE_ROOT, _quota_drain_requested)


def cmd_prepare_plugin_shadow() -> int:
    """Materialise the per-agent shadow plugin and print its path.

    Used by interactive launchers (``claude --plugin-dir $(devbench
    prepare-plugin-shadow)``) so the same per-agent model overrides apply
    whether the operator runs ``devbench start`` non-interactively or hand-
    drives the orchestrate skill in a Claude Code session. The function
    shares its implementation with ``cmd_start``'s pre-flight, so the two
    modes always produce identical plugin trees (ADR-25).

    Prints the resolved plugin path (shadow when overrides configured, else
    the canonical path) to stdout and exits 0.
    """
    plugin_path = _resolve_plugin_path()
    print(str(plugin_path))
    return 0


def _session_scope_file_path(workspace_root: Path) -> Path:
    """Return the scope.json path, honouring ``DEVBENCH_SESSION_NAME`` when set.

    When a session name is active, scope.json lives at:
    ``<workspace>/.devbench/sessions/<name>/scope.json``

    When no session is active, scope.json lives at:
    ``<workspace>/.devbench/scope.json``

    Args:
        workspace_root: The workspace root (typically ``WORKSPACE_ROOT``).

    Returns:
        The canonical scope.json ``Path`` for the current session context.

    Raises:
        ValueError: If ``DEVBENCH_SESSION_NAME`` contains ``..`` path segments.
    """
    session_name = os.environ.get("DEVBENCH_SESSION_NAME", "").strip()
    if not session_name:
        return _scope_file_path(workspace_root)
    if ".." in Path(session_name).parts:
        raise ValueError(f"DEVBENCH_SESSION_NAME contains invalid path segment '..': {session_name!r}")
    return workspace_root / ".devbench" / "sessions" / session_name / "scope.json"


def _scope_set(include: str, exclude: str, workspace_root: Path) -> int:
    """Persist a ``ScopeFilter`` to scope.json for the current session context.

    Parses ``include``/``exclude`` tokens via ``ScopeFilter.parse()``, then
    writes the result atomically to the session-scoped (or workspace-root)
    scope.json via :meth:`ScopeFilter.to_file`.

    Args:
        include: Raw ``--include`` token string (must be non-empty).
        exclude: Raw ``--exclude`` token string (may be empty).
        workspace_root: The workspace root path.

    Returns:
        0 on success, 1 on parse error or write error.

    Raises:
        Nothing -- all errors are caught and written to stderr.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    backlog_ids = [u.id for u in units]
    try:
        scope = ScopeFilter.parse(include, exclude, backlog_ids)
    except InvalidScopeError as exc:
        print(f"ERROR: invalid scope token: {exc}", file=sys.stderr)
        return 1
    # Determine the target path honouring DEVBENCH_SESSION_NAME
    try:
        target_path = _session_scope_file_path(workspace_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        scope.to_file(workspace_root, path=target_path)
    except OSError as exc:
        print(
            f"ERROR: cannot write scope.json to {target_path}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(f"scope set: {target_path}")
    return 0


def _scope_clear(workspace_root: Path) -> int:
    """Remove the active scope.json (idempotent).

    Exits 0 even when no scope.json is present (outputs ``no scope pending``).
    Deletion is delegated to :meth:`ScopeFilter.clear` to avoid reimplementing
    the idempotent-unlink logic.

    Args:
        workspace_root: The workspace root path.

    Returns:
        Always 0.

    Raises:
        Nothing -- all errors are caught and written to stderr.
    """
    try:
        target_path = _session_scope_file_path(workspace_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not target_path.exists():
        print("no scope pending")
        return 0
    try:
        ScopeFilter.clear(workspace_root, path=target_path)
    except OSError as exc:
        print(f"ERROR: cannot delete scope.json at {target_path}: {exc}", file=sys.stderr)
        return 1
    print(f"scope cleared: {target_path}")
    return 0


def _scope_show(workspace_root: Path) -> int:
    """Print the active scope state or ``no scope pending``.

    Displays the include list, exclude list, expanded ID count, started_at,
    and started_by metadata from scope.json.  Exits 0 when no scope.json is
    present (prints ``no scope pending``); see the Returns section below for
    the failure contract when scope.json exists but cannot be read.

    Delegates to :func:`devbench.scope._read_and_migrate_scope_payload` so a
    legacy list-shaped scope.json (issue #270) self-heals on this path
    exactly as it does for :meth:`devbench.scope.ScopeFilter.from_file` and
    :func:`_read_scope_banner_data` -- ``devbench scope show`` no longer
    raises a raw ``TypeError`` on a stale array file.

    Uses ``[]`` key access (not ``.get()`` with defaults) so a corrupt
    scope.json with missing required fields raises ``KeyError`` immediately
    (fail-fast) instead of silently masking the corruption with empty values.

    Args:
        workspace_root: The workspace root path.

    Returns:
        0 on success or when no scope is pending; 1 when scope.json cannot
        be resolved, read, migrated, or parsed (session name resolution
        failure, OSError, JSON decode error, missing key, or a legacy
        list-shaped payload whose elements fail migration validation).

    Raises:
        Nothing -- ``ValueError`` from session-name resolution and
        ``OSError``, ``json.JSONDecodeError``, ``KeyError``, and
        ``TypeError`` from the read/migrate path are all caught here and
        reported to stderr with a non-zero return instead of propagating.
    """
    try:
        target_path = _session_scope_file_path(workspace_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        data = _read_and_migrate_scope_payload(target_path)
        if data is None:
            print("no scope pending")
            return 0
        include = data["include"]
        exclude = data["exclude"]
        expanded_ids = data["expanded_ids"]
        started_at = data["started_at"]
        started_by = data["started_by"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"ERROR: cannot read scope.json at {target_path}: {exc}", file=sys.stderr)
        return 1
    print(f"include:      {include}")
    print(f"exclude:      {exclude}")
    print(f"expanded IDs: {len(expanded_ids)}")
    print(f"started_at:   {started_at}")
    print(f"started_by:   {started_by}")
    return 0


def _parse_scope_set_argv(argv: tuple[str, ...]) -> tuple[str, str, int]:
    """Parse arguments for ``scope set``.

    Accepts ``--include <tokens>`` (required) and ``--exclude <tokens>``
    (optional).  Returns ``(include, exclude, exit_code)`` where
    ``exit_code`` is 1 on parse error and 0 on success.

    Args:
        argv: Argument tuple after the ``"set"`` token.

    Returns:
        Tuple of ``(include_str, exclude_str, exit_code)``.  When
        ``exit_code`` is non-zero the include/exclude values should be
        ignored.
    """
    include: str = ""
    exclude: str = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--include", "--exclude"):
            if i + 1 >= len(args) or args[i + 1].startswith("--"):
                print(
                    f"ERROR: {arg} requires a value",
                    file=sys.stderr,
                )
                return "", "", 1
            i += 1
            if arg == "--include":
                include = args[i]
            else:
                exclude = args[i]
        else:
            print(f"ERROR: unrecognised argument for 'scope set': {arg!r}", file=sys.stderr)
            return "", "", 1
        i += 1
    if not include:
        print(
            "ERROR: 'scope set' requires --include <tokens>",
            file=sys.stderr,
        )
        return "", "", 1
    return include, exclude, 0


def cmd_scope(*argv: str) -> int:
    """Persistent scope management: set / clear / show.

    CLI surface (spec 4.2.6.1, issue #196)::

        devbench scope set --include "<tokens>" [--exclude "<tokens>"]
        devbench scope clear
        devbench scope show

    Dispatches to internal helpers ``_scope_set``, ``_scope_clear``, and
    ``_scope_show``.  All helpers reuse :class:`~devbench.scope.ScopeFilter`
    from :mod:`devbench.scope` (built in E2-F1) for token parsing and
    persistence -- no duplication of parsing or file-write logic.

    When ``DEVBENCH_SESSION_NAME`` env var is set, scope.json is written to /
    read from ``<workspace>/.devbench/sessions/<name>/scope.json`` (per-session
    isolation, spec 4.2.6.4, issue #192 integration).

    Error behaviour:

    - Invalid tokens in ``scope set`` -> rc=1, message to stderr identical to
      ``cmd_start --include``'s parser error (no message drift).
    - ``scope set`` cannot write scope.json -> rc=1, stderr
      ``ERROR: cannot write scope.json to <path>: <reason>``.
    - ``scope clear`` on missing file -> rc=0, stdout ``no scope pending``
      (idempotent).
    - ``scope show`` on missing file -> rc=0, stdout ``no scope pending``.
    - Unknown action verb -> rc=2, stderr
      ``ERROR: unknown action '<verb>'; valid: set | clear | show``.

    Args:
        *argv: Positional action verb and optional flags.

    Returns:
        0 on success, 1 on scope error, 2 on unknown action verb.
    """
    if not argv:
        print(
            "ERROR: 'scope' requires an action: set | clear | show",
            file=sys.stderr,
        )
        return 2
    action = argv[0]
    rest = argv[1:]
    if action == "set":
        include, exclude, parse_rc = _parse_scope_set_argv(rest)
        if parse_rc != 0:
            return parse_rc
        return _scope_set(include, exclude, WORKSPACE_ROOT)
    if action == "clear":
        return _scope_clear(WORKSPACE_ROOT)
    if action == "show":
        return _scope_show(WORKSPACE_ROOT)
    print(
        f"ERROR: unknown action {action!r}; valid: set | clear | show",
        file=sys.stderr,
    )
    return 2


def _parse_drain_argv(argv: tuple[str, ...]) -> tuple[str | None, str, str | None, int, str]:
    """Parse ``cmd_drain`` arguments into (mode, reason, session_target, error_rc, error_msg).

    Returns a 5-tuple:

    - ``mode``: one of ``"request"``, ``"cancel"``, ``"status"``, or ``None`` on error.
    - ``reason``: the value of ``--reason`` (empty string when not given).
    - ``session_target``: one of:
        - ``None`` -- workspace-level drain (no ``--session`` / ``--all`` flag).
        - A session name string -- drain only that session (``--session <name>``).
        - ``"__all__"`` -- drain every active session (``--all``).
    - ``error_rc``: 0 on success, 2 on error.
    - ``error_msg``: human-readable error description (empty string on success).

    ``--session`` and ``--all`` are only valid for the ``request`` mode; combining
    them with ``--cancel``, ``--status``, or ``--reason`` yields rc=2.  ``--session``
    and ``--all`` are mutually exclusive with each other.

    Raises:
        SystemExit: never -- all errors are returned with error_rc=2.
    """
    has_cancel = "--cancel" in argv
    has_status = "--status" in argv
    has_reason = "--reason" in argv
    has_session = "--session" in argv
    has_all = "--all" in argv

    error_msg = _drain_argv_validate_flags(
        has_cancel=has_cancel,
        has_status=has_status,
        has_reason=has_reason,
        has_session=has_session,
        has_all=has_all,
    )
    if error_msg:
        return None, "", None, 2, error_msg

    if has_cancel:
        return "cancel", "", None, 0, ""
    if has_status:
        return "status", "", None, 0, ""

    reason, reason_err = _drain_argv_parse_reason(argv, has_reason)
    if reason_err:
        return None, "", None, 2, reason_err

    session_target, session_err = _drain_argv_parse_session_target(argv, has_all, has_session)
    if session_err:
        return None, "", None, 2, session_err

    return "request", reason, session_target, 0, ""


def _drain_argv_validate_flags(
    *,
    has_cancel: bool,
    has_status: bool,
    has_reason: bool,
    has_session: bool,
    has_all: bool,
) -> str:
    """Return a non-empty error message string when flag combinations are invalid, else empty string.

    Raises:
        Nothing -- all results are returned as strings.
    """
    if has_session and has_all:
        return "ERROR: --session and --all are mutually exclusive"
    if (has_session or has_all) and (has_cancel or has_status or has_reason):
        return "ERROR: --session and --all cannot be combined with --cancel, --status, or --reason"
    if sum([has_cancel, has_status, has_reason]) > 1:
        return "ERROR: --cancel, --status, and --reason are mutually exclusive"
    return ""


def _drain_argv_parse_reason(argv: tuple[str, ...], has_reason: bool) -> tuple[str, str]:
    """Return ``(reason, error_msg)`` from *argv*.

    When *has_reason* is ``True``, extracts the value following ``--reason``.
    Returns ``("", error_msg)`` if the value is missing.

    Raises:
        Nothing -- all results are returned as tuples.
    """
    if not has_reason:
        return "", ""
    idx = argv.index("--reason")
    if idx + 1 >= len(argv):
        return "", "ERROR: --reason requires a value"
    return argv[idx + 1], ""


def _drain_argv_parse_session_target(argv: tuple[str, ...], has_all: bool, has_session: bool) -> tuple[str | None, str]:
    """Return ``(session_target, error_msg)`` from *argv*.

    ``session_target`` is ``"__all__"``, a session name, or ``None`` for the
    workspace-root path.  Returns ``(None, error_msg)`` when ``--session`` is
    missing its value.

    Raises:
        Nothing -- all results are returned as tuples.
    """
    if has_all:
        return "__all__", ""
    if has_session:
        idx = argv.index("--session")
        if idx + 1 >= len(argv) or not argv[idx + 1]:
            return None, "ERROR: --session requires a non-empty session name"
        return argv[idx + 1], ""
    return None, ""


def _request_drain_for_session(workspace: Path, session_name: str, reason: str) -> int:
    """Write the drain signal into a specific named session's state directory.

    Validates that the session state directory exists before writing.  Does not
    rely on ``DEVBENCH_SESSION_NAME`` -- the path is constructed directly from
    *session_name* so the caller's environment does not affect which file is
    written (spec 4.4.4, AC-192-7).

    Args:
        workspace: Root directory of the devbench workspace.
        session_name: Name of the target session (must correspond to an existing
            state directory under ``<workspace>/.devbench/sessions/<name>/``).
        reason: Optional free-form reason string (may be empty).

    Returns:
        0 on success; 1 when the session state directory does not exist.

    Raises:
        OSError: The write or rename step fails for a reason other than a missing
            directory (e.g. disk full, permission denied).
    """
    session_state_dir = workspace / SESSION_SESSIONS_BASE_DIR / session_name
    if not session_state_dir.is_dir():
        print(
            f"ERROR: session {session_name!r} does not exist (no state directory at {session_state_dir})",
            file=sys.stderr,
        )
        return 1

    signal_path = session_state_dir / SESSION_DRAIN_SIGNAL_FILENAME
    # Construct and write the drain signal directly to the session path.
    # request_drain(workspace) cannot be used here because it resolves the path
    # via DEVBENCH_SESSION_NAME -- we need an explicit path regardless of the
    # caller's environment (spec 4.4.4, AC-192-7).
    state = DrainState(
        requested_at=datetime.now(tz=UTC),
        requested_by=_current_user(),
        reason=reason,
    )
    payload = json.dumps(state.to_dict(), indent=2)
    tmp = signal_path.parent / "drain.tmp"
    try:
        tmp.write_text(payload, encoding="utf-8")
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    tmp.rename(signal_path)
    return 0


def _request_drain_for_all_sessions(workspace: Path, reason: str) -> int:
    """Write the drain signal for every session in the active session registry.

    Reads the session registry via :class:`~devbench.session.SessionRegistry` and
    writes ``drain.signal`` into each session's state directory.  Does not touch
    the workspace-root drain signal (spec 4.4.4, AC-192-8).

    If no active sessions are registered, prints an informational message and
    returns rc=0 (idempotent; not an error).

    Args:
        workspace: Root directory of the devbench workspace.
        reason: Optional free-form reason string (may be empty).

    Returns:
        0 on success (including the no-sessions case).

    Raises:
        OSError: A filesystem operation fails when writing a session drain signal.
        ValueError: The session registry file contains invalid JSON.
    """
    registry = SessionRegistry(workspace)
    sessions = registry.load()
    if not sessions:
        print("No active sessions -- nothing to drain.")
        return 0

    for session in sessions:
        _request_drain_for_session(workspace, session.name, reason)

    names = ", ".join(s.name for s in sessions)
    print(f"Drain signal written for {len(sessions)} session(s): {names}")
    return 0


def cmd_instances(*argv: str) -> int:
    """List every live devbench orchestrator instance on this host (#209).

    Walks every ``<root>/**/.devbench/orchestrator.pid`` under the operator's
    reachable search roots: ``DEVBENCH_INSTANCE_SEARCH_ROOTS`` (colon-separated)
    when set; otherwise ``$HOME`` plus the current ``DEVBENCH_WORKSPACE_ROOT``
    (deduplicated when the workspace already lives under ``$HOME``). Filters
    to live PIDs, and prints either a human-readable table (default) or a
    JSON array (``--json``).

    Args:
        *argv: Optional ``--json`` flag.

    Returns:
        0 on success.  Always succeeds; an empty list is not an error.
    """
    from devbench.instances import discover_instances

    as_json = False
    for arg in argv:
        if arg == "--json":
            as_json = True
        else:
            print(f"ERROR: unknown flag for 'instances': {arg!r}", file=sys.stderr)
            return 2

    instances = discover_instances()
    if as_json:
        payload = [
            {
                "instance_id": i.instance_id,
                "pid": i.pid,
                "workspace": i.workspace,
                "workspace_name": i.workspace_name,
                "session": i.session,
                "mode": i.mode,
                "started_at": i.started_at,
                "model": i.model,
            }
            for i in instances
        ]
        print(json.dumps(payload, indent=2))
        return 0

    if not instances:
        print("no devbench orchestrator instances running")
        return 0

    header = f"{'INSTANCE_ID':<32} {'PID':>7}  {'MODE':<11} {'SESSION':<15} {'WORKSPACE':<24} STARTED"
    print(header)
    print("-" * len(header))
    for i in instances:
        print(f"{i.instance_id:<32} {i.pid:>7}  {i.mode:<11} {i.session:<15} {i.workspace_name:<24} {i.started_at}")
    return 0


def _parse_stop_instance_args(argv: tuple[str, ...]) -> tuple[str | None, int, bool, int]:
    """Parse ``cmd_stop_instance`` argv into (target, timeout, force, rc).

    Returns ``(target, timeout, force, 0)`` on success or ``(None, 0, False, rc)``
    on parse error (rc=2 for unknown flag / missing value / duplicate target).
    """
    target: str | None = None
    timeout = 30
    force = False
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--timeout":
            if i + 1 >= len(args):
                print("ERROR: --timeout requires a value", file=sys.stderr)
                return None, 0, False, 2
            try:
                timeout = int(args[i + 1])
            except ValueError:
                print(f"ERROR: --timeout must be an integer, got {args[i + 1]!r}", file=sys.stderr)
                return None, 0, False, 2
            i += 2
        elif arg == "--force":
            force = True
            i += 1
        elif arg.startswith("--"):
            print(f"ERROR: unknown flag for 'stop-instance': {arg!r}", file=sys.stderr)
            return None, 0, False, 2
        else:
            if target is not None:
                print("ERROR: 'stop-instance' accepts a single instance id or PID", file=sys.stderr)
                return None, 0, False, 2
            target = arg
            i += 1
    return target, timeout, force, 0


def _wait_for_pid_exit(pid: int, timeout: int) -> bool:
    """Poll ``os.kill(pid, 0)`` until the pid is gone or *timeout* elapses."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.5)
    return False


def _send_signal_and_wait(inst: Any, timeout: int, force: bool) -> int:
    """Send SIGTERM, wait, optionally escalate to SIGKILL.  Returns CLI rc.

    Extracted from ``cmd_stop_instance`` to keep PLR0911 in check.
    """
    import signal

    try:
        os.kill(inst.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        print(f"ERROR: SIGTERM to pid {inst.pid} failed: {exc}", file=sys.stderr)
        return 1
    if _wait_for_pid_exit(inst.pid, timeout):
        print(f"stopped instance {inst.instance_id} (pid {inst.pid})")
        return 0
    if not force:
        print(
            f"ERROR: instance {inst.instance_id} (pid {inst.pid}) did not exit within {timeout}s; "
            f"re-run with --force to escalate to SIGKILL",
            file=sys.stderr,
        )
        return 1
    try:
        os.kill(inst.pid, signal.SIGKILL)
    except OSError as exc:
        print(f"ERROR: SIGKILL to pid {inst.pid} failed: {exc}", file=sys.stderr)
        return 1
    print(f"force-killed instance {inst.instance_id} (pid {inst.pid})")
    return 0


def cmd_stop_instance(*argv: str) -> int:
    """Stop a devbench orchestrator instance by id or PID (#209).

    Sends SIGTERM, waits up to ``--timeout`` seconds (default 30), and
    optionally escalates to SIGKILL with ``--force``.  The orchestrator's
    ``try/finally`` cleanup runs on SIGTERM: the ``orchestrator_stop``
    Slack ping fires (if enabled), atomic writes complete-or-rollback,
    and the PID file is removed.  Current WU in-flight work is lost.

    Args:
        *argv: ``<instance_id_or_pid>`` and optional ``--timeout N`` /
            ``--force``.

    Returns:
        0 on confirmed exit; 1 on unknown instance or signal failure;
        2 on argument errors.
    """
    from devbench.instances import resolve_instance

    target, timeout, force, parse_rc = _parse_stop_instance_args(argv)
    if parse_rc != 0:
        return parse_rc
    if target is None:
        print("ERROR: 'stop-instance' requires an instance id (run 'devbench instances')", file=sys.stderr)
        return 2

    inst = resolve_instance(target)
    if inst is None:
        print(f"ERROR: instance {target!r} not found; run 'devbench instances' to list", file=sys.stderr)
        return 1

    return _send_signal_and_wait(inst, timeout, force)


def _parse_tail_args(argv: tuple[str, ...]) -> tuple[str | None, bool, int, int]:
    """Parse ``cmd_tail`` argv into (target, follow, lines, rc)."""
    target: str | None = None
    follow = False
    lines = 50
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--follow", "-f"):
            follow = True
            i += 1
        elif arg in ("--lines", "-n"):
            if i + 1 >= len(args):
                print("ERROR: --lines requires a value", file=sys.stderr)
                return None, False, 0, 2
            try:
                lines = int(args[i + 1])
            except ValueError:
                print(f"ERROR: --lines must be an integer, got {args[i + 1]!r}", file=sys.stderr)
                return None, False, 0, 2
            i += 2
        elif arg.startswith("--"):
            print(f"ERROR: unknown flag for 'tail': {arg!r}", file=sys.stderr)
            return None, False, 0, 2
        else:
            if target is not None:
                print("ERROR: 'tail' accepts a single instance id or PID", file=sys.stderr)
                return None, False, 0, 2
            target = arg
            i += 1
    return target, follow, lines, 0


def cmd_tail(*argv: str) -> int:
    """Tail an orchestrator instance's log file (#209).

    Resolves the instance's workspace via its PID file, then prints
    ``<workspace>/logs/orchestrator.log`` -- last ``--lines N`` lines by
    default, or live-tail when ``--follow`` is set.

    Args:
        *argv: ``<instance_id_or_pid>`` and optional ``--follow`` / ``--lines N``.

    Returns:
        0 on clean exit; 1 on unknown instance or missing log; 2 on argument errors.
    """
    import subprocess

    from devbench.instances import resolve_instance

    target, follow, lines, parse_rc = _parse_tail_args(argv)
    if parse_rc != 0:
        return parse_rc
    if target is None:
        print("ERROR: 'tail' requires an instance id (run 'devbench instances')", file=sys.stderr)
        return 2

    inst = resolve_instance(target)
    if inst is None:
        print(f"ERROR: instance {target!r} not found; run 'devbench instances' to list", file=sys.stderr)
        return 1

    log_path = Path(inst.workspace) / "logs" / "orchestrator.log"
    if not log_path.is_file():
        print(f"ERROR: log file not found at {log_path}", file=sys.stderr)
        return 1

    cmd = ["tail", "-n", str(lines)]
    if follow:
        cmd.append("-F")
    cmd.append(str(log_path))
    # subprocess.run with shell=False; resolves via PATH.  Returns the
    # child's exit code so an operator can chain in a pipeline.
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def cmd_restart(*argv: str) -> int:
    """Restart a devbench orchestrator instance (#209).

    Composite of stop + start.  Resolves the instance, captures its mode +
    session + workspace, sends SIGTERM, waits for exit, then re-launches in
    the SAME mode (daemon vs foreground) and same workspace + session
    via subprocess.

    Args:
        *argv: ``<instance_id_or_pid>`` and optional ``--timeout N`` /
            ``--force`` (passed to the stop phase).

    Returns:
        0 on confirmed restart; non-zero on resolution / stop / launch failure.
    """
    import subprocess

    from devbench.instances import resolve_instance

    if not argv:
        print("ERROR: 'restart' requires an instance id", file=sys.stderr)
        return 2

    target = argv[0]
    inst = resolve_instance(target)
    if inst is None:
        print(f"ERROR: instance {target!r} not found; run 'devbench instances' to list", file=sys.stderr)
        return 1

    workspace = inst.workspace
    session = inst.session
    mode = inst.mode

    stop_rc = cmd_stop_instance(*argv)
    if stop_rc != 0:
        print(f"ERROR: restart aborted -- stop phase exited {stop_rc}", file=sys.stderr)
        return stop_rc

    start_args = ["uv", "run", "--project", os.environ.get("DEVBENCH_PROJECT_ROOT", "."), "devbench", "start"]
    if mode == "daemon":
        start_args.append("--daemon")
    if session and session != "default":
        start_args.extend(["--name", session])

    env = os.environ.copy()
    env["DEVBENCH_WORKSPACE_ROOT"] = workspace
    print(f"relaunching in {mode} mode at {workspace}")
    try:
        subprocess.run(start_args, env=env, check=True, cwd=workspace)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"ERROR: relaunch failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_drain(*argv: str) -> int:
    """Manage the drain signal for graceful orchestrator shutdown (spec section 4.3.2).

    Invocation forms::

        devbench drain                        -- request workspace-root drain (empty reason)
        devbench drain --reason "<text>"      -- request workspace-root drain with reason
        devbench drain --cancel               -- withdraw workspace-root drain; idempotent
        devbench drain --status               -- print every pending drain (root + per-session); rc=0
        devbench drain --session <name>       -- drain only the named session (AC-192-7)
        devbench drain --all                  -- drain every active session (AC-192-8)

    ``--session`` and ``--all`` are only valid for plain drain requests; combining
    them with ``--cancel``, ``--status``, or ``--reason`` is an error (rc=2).

    All success paths return rc=0.  Mutually exclusive flag combinations and
    missing required values return rc=2 with a distinct diagnostic on stderr
    for each error condition.

    Args:
        *argv: Zero or more CLI flags as individual strings.

    Returns:
        0 on success; 1 when the named session does not exist (``--session`` path);
        2 on invalid argument combination or missing ``--reason`` / ``--session`` value.

    Raises:
        OSError: Propagated from :func:`~devbench.drain.request_drain`,
            :func:`~devbench.drain.cancel_drain`, or
            :func:`~devbench.cli._request_drain_for_session` when a filesystem
            operation fails for a reason other than a missing file.
        ValueError: Propagated from :class:`~devbench.session.SessionRegistry` when
            the registry file contains invalid JSON (``--all`` path only).
    """
    mode, reason, session_target, error_rc, error_msg = _parse_drain_argv(argv)
    if error_rc != 0:
        print(error_msg, file=sys.stderr)
        return error_rc

    if mode == "cancel":
        cancel_drain(WORKSPACE_ROOT)
        return 0

    if mode == "status":
        states = read_all_drain_states(WORKSPACE_ROOT)
        if not states:
            print("no drain pending")
        else:
            for session_name, state in states:
                print(_format_drain_status_line(session_name, state))
        return 0

    if session_target == "__all__":
        return _request_drain_for_all_sessions(WORKSPACE_ROOT, reason)

    if session_target is not None:
        return _request_drain_for_session(WORKSPACE_ROOT, session_target, reason)

    request_drain(WORKSPACE_ROOT, reason=reason)
    return 0


# ---------------------------------------------------------------------------
# cmd_sessions helpers (E4-F5-S1-T1, issue #192)
# ---------------------------------------------------------------------------


def _parse_sessions_argv(argv: tuple[str, ...]) -> tuple[str | None, int, str]:
    """Parse ``cmd_sessions`` arguments into ``(mode, error_rc, error_msg)``.

    Returns a 3-tuple:

    - ``mode``: one of ``"list"`` or ``"cleanup"``, or ``None`` on error.
    - ``error_rc``: 0 on success, 2 on invalid argument.
    - ``error_msg``: human-readable error description (empty string on success).

    Only ``--cleanup`` is a recognised flag.  Any other flag-like token
    (starting with ``-``) is rejected as unknown.

    Raises:
        SystemExit: never -- all errors are returned via error_rc.
    """
    has_cleanup = "--cleanup" in argv
    unknown = [arg for arg in argv if arg.startswith("-") and arg != "--cleanup"]

    if unknown:
        return None, 2, f"ERROR: unknown flag(s) for sessions: {', '.join(unknown)}"

    if has_cleanup:
        return "cleanup", 0, ""

    return "list", 0, ""


def _session_drain_state_str(session: Session) -> str:
    """Return a human-readable drain state string for *session*.

    Reads the drain signal file directly from the session's ``state_dir``
    (``<state_dir>/drain.signal``).  Does not use ``DEVBENCH_SESSION_NAME``
    so the check is accurate regardless of the caller's environment.

    Args:
        session: The :class:`~devbench.session.Session` whose drain state to read.

    Returns:
        ``"pending"`` when a drain signal file exists in the session state dir;
        ``"none"`` otherwise.

    Raises:
        OSError: Reading the drain signal file fails for an unexpected reason.
    """
    drain_path = session.state_dir / SESSION_DRAIN_SIGNAL_FILENAME
    return "pending" if drain_path.exists() else "none"


def cmd_sessions(*argv: str) -> int:
    """List active sessions or remove stale ones (spec section 4.4.5, issue #192).

    Invocation forms::

        devbench sessions              -- list all sessions with name, PID, scope,
                                          started_at, drain state, and liveness
        devbench sessions --cleanup    -- remove session dirs whose PID references
                                          a non-running process

    Exit codes:

    - 0 on success (including when no sessions exist or no stale sessions were found).
    - 2 on invalid arguments (unknown flags).

    Args:
        *argv: Zero or more CLI flags as individual strings.

    Returns:
        0 on success; 2 on invalid argument.

    Raises:
        OSError: Propagated from :class:`~devbench.session.SessionRegistry`
            when a filesystem operation fails unexpectedly.
        ValueError: Propagated from :class:`~devbench.session.SessionRegistry`
            when the registry file contains invalid JSON.
    """
    mode, error_rc, error_msg = _parse_sessions_argv(argv)
    if error_rc != 0:
        print(error_msg, file=sys.stderr)
        return error_rc

    registry = SessionRegistry(WORKSPACE_ROOT)

    if mode == "cleanup":
        removed = registry.cleanup_stale_sessions()
        if removed:
            print(f"Removed {len(removed)} stale session(s): {', '.join(removed)}")
        else:
            print("No stale sessions found -- nothing to clean up.")
        return 0

    sessions = registry.load()
    if not sessions:
        print("No active sessions.")
        return 0

    liveness_map = registry.liveness_of_sessions(sessions)

    # Header row
    header = f"{'NAME':<20} {'PID':>8}  {'LIVENESS':<8}  {'DRAIN':<8}  {'STARTED_AT':<25}  SCOPE"
    print(header)
    print("-" * len(header))

    for session in sessions:
        liveness = liveness_map[session.name]
        drain_str = _session_drain_state_str(session)
        scope_str = ", ".join(session.scope) if session.scope else "(all)"
        started_str = session.started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"{session.name:<20} {session.pid:>8}  {liveness:<8}  {drain_str:<8}  {started_str:<25}  {scope_str}")

    return 0


# ---------------------------------------------------------------------------
# cmd_stop helpers (E4-F5-S1-T2, issue #192)
# ---------------------------------------------------------------------------

#: Audit-log prefix written when cmd_start intercepts SIGTERM and forces the
#: in-flight work unit to ``blocked``.  Format:
#: ``[INTERRUPTED_ON_STOP] session=<name>``.
_INTERRUPTED_ON_STOP_AUDIT_PREFIX: str = "[INTERRUPTED_ON_STOP] session="


def _parse_stop_argv(argv: tuple[str, ...]) -> tuple[str | None, int, str]:
    """Parse ``cmd_stop`` arguments into ``(session_name, error_rc, error_msg)``.

    Returns a 3-tuple:

    - ``session_name``: the value of ``--session <name>``, or ``None`` on error.
    - ``error_rc``: 0 on success, 2 on invalid/missing argument.
    - ``error_msg``: human-readable error description (empty string on success).

    Only ``--session <name>`` is a recognised flag.  Any other flag-like token
    (starting with ``-``) is rejected as unknown.  An empty session name is
    rejected as invalid.  A session name containing ``..`` path segments is
    rejected to prevent directory traversal.

    Args:
        argv: Trailing arguments after the ``stop`` command name (may be empty).

    Returns:
        ``(name, 0, "")`` on success; ``(None, 2, message)`` on any parse error.

    Raises:
        SystemExit: never -- all errors are returned via error_rc.
    """
    args = list(argv)

    # Collect unknown flags
    unknown = [a for a in args if a.startswith("-") and a != "--session"]
    if unknown:
        return None, 2, f"ERROR: unknown flag(s) for stop: {', '.join(unknown)}"

    # Require --session
    if "--session" not in args:
        return None, 2, "ERROR: --session <name> is required for devbench stop"

    idx = args.index("--session")
    if idx + 1 >= len(args):
        return None, 2, "ERROR: --session requires a non-empty session name"

    session_name = args[idx + 1]
    if not session_name:
        return None, 2, "ERROR: --session requires a non-empty session name"

    if ".." in Path(session_name).parts:
        return (
            None,
            2,
            (
                f"ERROR: session name contains invalid path segment '..': {session_name!r}. "
                "Use a simple alphanumeric name without directory traversal."
            ),
        )

    return session_name, 0, ""


def _find_in_flight_wu(units: list[WorkUnit]) -> WorkUnit | None:
    """Return the first in-progress work unit from *units*, or ``None``.

    Scans *units* linearly and returns the first element whose
    ``status`` is :data:`~devbench.backlog.work_unit.WorkUnitStatus.IN_PROGRESS`.

    Args:
        units: List of :class:`~devbench.backlog.work_unit.WorkUnit` objects.

    Returns:
        The first in-progress :class:`~devbench.backlog.work_unit.WorkUnit`, or
        ``None`` when none is found.

    Raises:
        Nothing.
    """
    for unit in units:
        if unit.status is WorkUnitStatus.IN_PROGRESS:
            return unit
    return None


def _requeue_in_flight_wu(wu: WorkUnit | None, session_name: str) -> None:
    """Return *wu* to ``in-queue`` and append an ``[INTERRUPTED_ON_STOP]`` audit comment.

    Called by the SIGTERM handler in ``cmd_start`` to release the in-flight
    work unit when the run stops, so the next run picks it up where it left
    off (spec section 4.4.5, AC-192-9).

    The unit goes to ``in-queue`` rather than ``blocked`` because a stop is
    not a dependency problem, and ``blocked`` is how devbench encodes exactly
    that. A unit parked in ``blocked`` waits for a dependency to go terminal;
    when the stop happened after every dependency was already terminal, no
    such event is ever coming and only a ``reconcile-cascade`` sweep can
    release it. Interrupted work is immediately actionable, so it belongs in
    the queue it was claimed from -- and pairing that with the quarantine
    restore on the claim path means the unit resumes on the attempt it had
    already produced.

    When *wu* is ``None`` (no in-flight unit found), this function is a no-op.

    Args:
        wu: The in-flight :class:`~devbench.backlog.work_unit.WorkUnit` to
            release, or ``None`` for a no-op.
        session_name: The session name to embed in the audit comment
            (e.g. ``"default"``).

    Raises:
        OSError: Reading or writing the work-unit file fails.
        ValueError: The BacklogManager validation rejects the status transition.
    """
    if wu is None:
        return

    checkpoint_sha = _checkpoint_in_flight_work(wu)

    mgr = BacklogManager()
    mgr.force_status(wu.file_path, BACKLOG_INDEX, wu.id, STATUS_IN_QUEUE)
    detail = f" checkpoint={checkpoint_sha}" if checkpoint_sha else ""
    mgr._append_agent_comment(
        wu.file_path,
        "orchestrator",
        f"{_INTERRUPTED_ON_STOP_AUDIT_PREFIX}{session_name}{detail}",
    )


def _checkpoint_in_flight_work(wu: WorkUnit) -> str | None:
    """Snapshot ``wu``'s in-flight work to its checkpoint ref before the run exits.

    The stop path is the last moment devbench controls before an interrupted
    unit's uncommitted work depends on something else keeping it safe. The
    snapshot costs one git command, touches neither the worktree nor the
    index, and gives the work a reachable ref that a later ``git stash clear``
    cannot discard.

    Best-effort by design: this runs inside a SIGTERM handler, so a repo that
    cannot be resolved or a git call that fails is logged and passed over. The
    unit is still released to the queue, and its work is still in the tree --
    a missing checkpoint costs a safety net, whereas raising here would leave
    the unit stuck ``in-progress`` with no run to advance it.

    Returns:
        The checkpoint commit SHA, or ``None`` when there was nothing in
        flight or the snapshot could not be taken.
    """
    from devbench.git_quarantine import checkpoint_work

    try:
        repo_path = REPO_LOCAL_PATHS.get(resolve_repo(wu.repo))
        if repo_path is None or not (repo_path / ".git").exists():
            return None
        sha = checkpoint_work(repo_path, wu.id)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("[CHECKPOINT_SKIPPED] could not snapshot %s: %s", wu.id, exc)
        return None
    if sha:
        logger.info("[WORK_CHECKPOINTED] %s snapshotted to %s", wu.id, sha)
    return sha


def _send_sigterm_to_session(session_name: str) -> tuple[int, str, str]:
    """Read the PID file for *session_name* and send SIGTERM.

    Returns a 3-tuple ``(rc, success_msg, error_msg)``:

    - ``rc=0``: SIGTERM was delivered; ``success_msg`` contains the confirmation
      text; ``error_msg`` is empty.
    - ``rc=1``: a runtime error prevented delivery; ``success_msg`` is empty;
      ``error_msg`` contains the actionable diagnostics.

    Args:
        session_name: Short session identifier (e.g. ``"default"``).

    Returns:
        ``(0, confirmation, "")`` on success;
        ``(1, "", error_message)`` on failure.

    Raises:
        Nothing -- all OS errors are captured and returned as rc=1.
    """
    state_dir = WORKSPACE_ROOT / ".devbench" / "sessions" / session_name
    pid_path = state_dir / SESSION_PID_FILENAME

    if not pid_path.exists():
        return (
            1,
            "",
            f"ERROR: PID file not found for session '{session_name}' at {pid_path}. "
            "The session may not be running or may have already exited.",
        )

    raw = pid_path.read_text(encoding="utf-8").strip()
    try:
        pid = int(raw)
    except ValueError:
        return (
            1,
            "",
            f"ERROR: PID file for session '{session_name}' contains non-integer content: {raw!r}. "
            "The pid file may be corrupt.",
        )

    kill_error = _kill_sigterm(pid, session_name)
    if kill_error:
        return 1, "", kill_error
    return 0, f"Sent SIGTERM to session '{session_name}' (pid={pid}). stop in progress.", ""


def _kill_sigterm(pid: int, session_name: str) -> str:
    """Send ``SIGTERM`` to *pid* and return an error string on failure.

    Returns an empty string on success; a non-empty error message when the
    signal cannot be delivered.

    Args:
        pid: OS process ID to signal.
        session_name: Session name (used only for error messages).

    Returns:
        ``""`` on success; an actionable ``"ERROR: ..."`` string on failure.

    Raises:
        Nothing -- all OS errors are captured and returned as strings.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return (
            f"ERROR: process {pid} for session '{session_name}' is not running (ESRCH). "
            "The session may have already exited."
        )
    except PermissionError:
        return (
            f"ERROR: permission denied sending SIGTERM to process {pid} for session "
            f"'{session_name}' (EPERM). The process is owned by a different user."
        )
    except OSError as exc:
        return f"ERROR: cannot send SIGTERM to process {pid} for session '{session_name}': {exc}"
    return ""


def cmd_stop(*argv: str) -> int:
    """Send SIGTERM to a named session's orchestrator process (spec section 4.4.5, issue #192).

    Invocation form::

        devbench stop --session <name>

    Reads the PID from ``<workspace>/.devbench/sessions/<name>/pid`` and sends
    ``SIGTERM`` to that process.  The SIGTERM handler registered by ``cmd_start``
    catches the signal, forces any in-flight work unit to ``blocked`` with a
    ``[INTERRUPTED_ON_STOP] session=<name>`` audit comment, then exits rc=0.

    Exit codes:

    - 0 on success (SIGTERM delivered).
    - 1 when the session does not exist, the PID file is absent or malformed, or
      the signal cannot be delivered (ESRCH, EPERM, or other OS error).
    - 2 on invalid arguments (missing or unknown flags).

    Args:
        *argv: CLI flags as individual strings (``--session <name>``).

    Returns:
        0 on success; 1 on runtime error; 2 on argument parse error.

    Raises:
        Nothing -- all errors are reported to stderr and returned as exit codes.
    """
    session_name, error_rc, error_msg = _parse_stop_argv(argv)
    if error_rc != 0:
        print(error_msg, file=sys.stderr)
        return error_rc

    # At this point session_name is guaranteed non-None by _parse_stop_argv.
    assert session_name is not None
    rc, success_msg, err_msg = _send_sigterm_to_session(session_name)
    if rc != 0:
        print(err_msg, file=sys.stderr)
    else:
        print(success_msg)
    return rc


def cmd_request_amendment(unit_id: str) -> int:
    """Register an amendment request for ``unit_id``.

    Reads the request payload as JSON on stdin. Expected fields:
    ``reason``, ``justification``, ``files_to_add`` (list of ``{path, change}``),
    ``linked_acs`` (list of AC IDs), and the optional ``files_to_remove`` (list
    of repo-relative paths whose Manifest rows should be dropped). The
    ``task_id`` and ``requested_at`` fields are filled in by this command --
    the caller does not provide them. At least one of ``files_to_add`` /
    ``files_to_remove`` must be non-empty.

    A row may only be removed when its file has no staged, unstaged, or
    untracked changes, so a removal can never carry real work out of the unit's
    reviewed scope.

    Every Layer 1 pre-filter check runs BEFORE the request is written, so a
    request that cannot be approved never reaches disk and never occupies the
    single pending-request slot. The checks are deterministic: the reason must
    be in this backlog's configured ``allowed_reasons``, the task must be
    in-progress, linked ACs must exist, added files must not already be in the
    Manifest and must be present in the staged diff.

    Previously ``PreFilter`` was wired to no CLI path at all, so a backlog that
    narrowed ``manifest_amendment.allowed_reasons`` had that narrowing silently
    ignored and every request was accepted regardless.

    On success, writes the request to
    ``<DEVBENCH_WORKSPACE_ROOT>/.devbench/amendments/<unit_id>.json`` and prints
    a one-line JSON summary. Returns 1 on any pre-filter or schema failure.
    """
    from devbench.backlog.manifest import list_changed_files, list_staged_files

    # Parse stdin BEFORE resolving the repo so a malformed payload reports the
    # schema error the caller can act on, rather than a repo-configuration error
    # that says nothing about what was wrong with the request.
    try:
        request = _build_amendment_request_from_stdin(unit_id)
    except _AmendmentRequestInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    resolved = _resolve_unit_repo_and_path(unit_id)
    if resolved is None:
        return 1
    _, _, repo_path = resolved

    try:
        PreFilter(BACKLOG_INDEX, RUNTIME_CONFIG.manifest_amendment).run_all(
            request,
            staged_files=frozenset(list_staged_files(repo_path)),
            changed_files=frozenset(list_changed_files(repo_path)),
        )
        written_path = write_request(
            WORKSPACE_ROOT,
            request,
            allowed_reasons=RUNTIME_CONFIG.manifest_amendment.allowed_reasons,
        )
    except AmendmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "task_id": unit_id,
                "request_path": str(written_path),
                "files_to_add": [f.path for f in request.files_to_add],
                "files_to_remove": list(request.files_to_remove),
                "reason": request.reason,
            }
        )
    )
    return 0


class _AmendmentRequestInputError(ValueError):
    """Raised by _build_amendment_request_from_stdin on any stdin/schema failure."""


def _build_amendment_request_from_stdin(unit_id: str) -> AmendmentRequest:
    """Read stdin, parse JSON, and construct the ``AmendmentRequest``.

    Raises ``_AmendmentRequestInputError`` on any invalid input.
    """
    try:
        raw = sys.stdin.read()
    except OSError as exc:
        raise _AmendmentRequestInputError(f"cannot read stdin: {exc}") from exc
    if not raw.strip():
        raise _AmendmentRequestInputError("amendment request JSON must be provided on stdin")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _AmendmentRequestInputError(f"amendment request is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _AmendmentRequestInputError("amendment request must be a JSON object")
    payload["task_id"] = unit_id
    payload.setdefault("requested_at", datetime.now(tz=UTC).isoformat())
    try:
        return AmendmentRequest.from_dict(payload)
    except ValueError as exc:
        raise _AmendmentRequestInputError(f"amendment request invalid: {exc}") from exc


def cmd_apply_amendment(unit_id: str) -> int:
    """Apply an approved amendment with Layer 3 post-check and atomic rollback.

    Reads the pending amendment request for ``unit_id``, appends its rows to
    the work unit's Changes Manifest, runs the Layer 3 post-check, and
    deletes the request on success. On any post-check failure the work unit
    is restored to its pre-amendment content and this command exits non-zero.
    """
    try:
        apply_amendment(
            WORKSPACE_ROOT,
            BACKLOG_INDEX,
            unit_id,
            allowed_reasons=RUNTIME_CONFIG.manifest_amendment.allowed_reasons,
        )
    except AmendmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"task_id": unit_id, "status": "applied"}))
    return 0


def cmd_reject_amendment(unit_id: str, rejection_reason: str) -> int:
    """Reject a pending amendment: write audit comment, block task, delete request."""
    rc = _reject_em_dash("rejection_reason", rejection_reason)
    if rc is not None:
        return rc

    try:
        reject_amendment(WORKSPACE_ROOT, BACKLOG_INDEX, unit_id, rejection_reason)
    except AmendmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"task_id": unit_id, "status": "rejected"}))
    return 0


# ---------------------------------------------------------------------------
# Issue #156: review-judge structured rejection-feedback persistence
# ---------------------------------------------------------------------------


def _validate_rejection_feedback_payload(judge: str, payload: object) -> dict[str, Any]:
    """Layer-1 schema check for the ``log-rejection-feedback`` JSON body.

    Returns the validated dict. Raises ``ValueError`` with an actionable
    message on any violation: bad type, missing field, unknown category
    code for *judge*, severity outside ``{fail, warn}``, etc.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"payload must be a JSON object, got {type(payload).__name__}")
    required = {"categories", "raw_verdict_text"}
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(sorted(missing))}")
    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ValueError("categories must be a non-empty list")
    allowed_codes = JUDGE_CATEGORIES.get(judge, frozenset())
    for idx, entry in enumerate(raw_categories):
        if not isinstance(entry, dict):
            raise ValueError(f"categories[{idx}] must be an object")
        for key in ("code", "severity", "summary", "remediation", "files"):
            if key not in entry:
                raise ValueError(f"categories[{idx}] missing field {key!r}")
        code = entry["code"]
        if not isinstance(code, str) or code not in allowed_codes:
            raise ValueError(f"categories[{idx}].code={code!r} is not in {judge}'s vocabulary {sorted(allowed_codes)}")
        severity = entry["severity"]
        if severity not in ("fail", "warn"):
            raise ValueError(f"categories[{idx}].severity={severity!r} must be 'fail' or 'warn'")
        files = entry["files"]
        if not isinstance(files, list) or any(not isinstance(f, str) for f in files):
            raise ValueError(f"categories[{idx}].files must be a list of strings")
    if not isinstance(payload["raw_verdict_text"], str):
        raise ValueError("raw_verdict_text must be a string")
    return payload


def _parse_log_rejection_feedback_argv(argv: tuple[str, ...]) -> tuple[str, str, str]:
    """Return ``(judge, task_id, raw_json)``. Raises ``ValueError`` on bad usage."""
    judge = ""
    task_id = ""
    raw_json = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--json":
            if i + 1 >= len(args):
                raise ValueError("--json requires a value")
            raw_json = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            raise ValueError(f"unknown flag: {arg}")
        if not judge:
            judge = arg
        elif not task_id:
            task_id = arg
        i += 1
    if not judge or not task_id or not raw_json:
        raise ValueError("usage: log-rejection-feedback <judge> <task-id> --json '<payload>'")
    return judge, task_id, raw_json


def cmd_log_rejection_feedback(*argv: str) -> int:
    """Persist a structured review-judge rejection JSON.

    Issue #156. Usage::

        log-rejection-feedback <judge> <task-id> --json '<payload>'

    Validates the payload against the controlled vocabulary for the
    judge and the field-level schema, then writes to
    ``<workspace>/.devbench/review-failures/<task-id>-<judge>-<n>.json``.
    Records over the ``MAX_RETRY_ATTEMPTS`` cap are still written but
    stamped ``capped: true`` for visibility.
    """
    from devbench.config import MAX_RETRY_ATTEMPTS

    try:
        judge, task_id, raw_json = _parse_log_rejection_feedback_argv(argv)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if judge not in JUDGE_CATEGORIES:
        print(
            f"ERROR: unknown judge {judge!r}; expected one of {sorted(JUDGE_CATEGORIES)}",
            file=sys.stderr,
        )
        return 1

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        print(f"ERROR: --json value is not valid JSON: {exc}", file=sys.stderr)
        return 1

    try:
        validated = _validate_rejection_feedback_payload(judge, payload)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    archive_dir = WORKSPACE_ROOT / REVIEW_FAILURES_DIR_NAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(archive_dir.glob(f"{task_id}-{judge}-*.json"))
    attempt = len(existing) + 1
    capped = attempt > MAX_RETRY_ATTEMPTS
    rejected_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    record: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "judge": judge,
        "attempt": attempt,
        "rejected_at": rejected_at,
        "categories": list(validated["categories"]),
        "raw_verdict_text": validated["raw_verdict_text"],
        "capped": capped,
    }
    target = archive_dir / f"{task_id}-{judge}-{attempt}.json"
    atomic_write_text(target, json.dumps(record, indent=2, sort_keys=True) + "\n")

    print(json.dumps({"task_id": task_id, "judge": judge, "attempt": attempt, "path": str(target)}))
    return 0


# ---------------------------------------------------------------------------
# Issue #156: executor-feedback collector for review-judge rejections
# ---------------------------------------------------------------------------


def _collect_review_judge_feedback(task_id: str) -> list[dict[str, Any]]:
    """Return the executor-injected ``review-judge-fail`` payload list for ``task_id``.

    Walks ``.devbench/review-failures/`` (and the legacy
    ``amender-rejections/`` directory) for every JSON matching the task,
    parses it, and returns one dict per rejection ordered by judge
    severity (security > code > test > changes_manifest > doc >
    manifest_amender) and then by attempt number descending. The list is
    truncated to ``MAX_RETRY_ATTEMPTS`` rounds so the executor never
    receives more context than the retry budget can act on.
    """
    from devbench.config import MAX_RETRY_ATTEMPTS

    payloads: list[dict[str, Any]] = []
    for path in read_review_failure_files(WORKSPACE_ROOT, task_id):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        # Synthesize a v1-shaped record for legacy amender-rejections
        # entries that pre-date schema_version: 1.
        if "schema_version" not in data and "reason_category" in data:
            data = {
                "schema_version": 1,
                "task_id": data.get("task_id", task_id),
                "judge": "manifest_amender",
                "attempt": data.get("attempt", 0),
                "rejected_at": data.get("recorded_at", ""),
                "categories": [
                    {
                        "code": data.get("reason_category", "OTHER"),
                        "severity": "fail",
                        "summary": data.get("reason_text", ""),
                        "remediation": "",
                        "files": [],
                    }
                ],
                "raw_verdict_text": data.get("reason_text", ""),
                "capped": data.get("capped", False),
                "_source_path": str(path),
            }
        else:
            data["_source_path"] = str(path)
        payloads.append(data)

    payloads.sort(
        key=lambda p: (
            -JUDGE_SEVERITY_ORDER.get(str(p.get("judge", "")), 0),
            -int(p.get("attempt", 0) or 0),
        )
    )
    return payloads[:MAX_RETRY_ATTEMPTS]


# ---------------------------------------------------------------------------
# Issue #156: done-gate hook for review-judge rejection resolution
# ---------------------------------------------------------------------------


def _outstanding_rejection_categories(task_id: str, wu_file: Path | None) -> list[tuple[str, str]]:
    """Return ``[(judge, code), ...]`` for unresolved review-judge rejections.

    A rejection is considered resolved when the work-unit file shows
    EITHER a matching ``[REJECTION_FEEDBACK_RESOLVED] <judge>:<code>``
    audit row OR a matching ``[NEEDS_DEP] <judge>:<code>`` audit row.
    Otherwise the (judge, code) pair is returned for the done-gate to
    refuse the transition.
    """
    rejections = _collect_review_judge_feedback(task_id)
    if not rejections or wu_file is None or not wu_file.is_file():
        return [
            (str(r.get("judge", "")), str(cat.get("code", "")))
            for r in rejections
            for cat in (r.get("categories") or [])
            if isinstance(cat, dict)
        ]
    content = wu_file.read_text(encoding="utf-8")
    outstanding: list[tuple[str, str]] = []
    for r in rejections:
        judge = str(r.get("judge", ""))
        for cat in r.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            code = str(cat.get("code", ""))
            resolved_marker = f"[REJECTION_FEEDBACK_RESOLVED] {judge}:{code}"
            needs_dep_marker = f"[NEEDS_DEP] {judge}:{code}"
            if resolved_marker in content or needs_dep_marker in content:
                continue
            outstanding.append((judge, code))
    return outstanding


def cmd_sweep_proposals() -> int:
    """Best-effort materialisation of every un-materialised proposal JSON.

    Walks ``<workspace>/.devbench/proposals/*.json`` and for each JSON whose
    ``proposed_tasks`` include at least one id in ``UNMATERIALISED`` state,
    attempts ``materialise_proposal``. The per-proposal call can still be
    refused by task-factory's "skip when prior unresolved proposed tasks
    exist" safety guard (which raises ``ProposalError``) -- the sweep logs
    that outcome and continues to the next proposal. The sweep is invoked
    at orchestrate loop start so stale JSONs don't remain invisible.

    Prints one line per proposal with the outcome:

    - ``materialised <source-id>: N tasks``  -- drafts successfully created.
    - ``skipped <source-id>: <reason>``      -- safety guard or other
                                                ProposalError fired.
    - ``no-op <source-id>``                  -- every task in this proposal
                                                is already materialised.

    Prints ``nothing to do`` and returns 0 when no proposal JSONs exist or
    nothing needed materialising. Never returns non-zero for per-proposal
    refusals; a refused proposal is a soft state the operator resolves via
    promote-proposal / reject-proposal.
    """
    proposals = list_proposals(WORKSPACE_ROOT)
    auto_accept = RUNTIME_CONFIG.task_factory.auto_accept_proposals

    # Issue #155: when no proposal JSONs AND auto-accept is off, fast-exit
    # before parsing the backlog so the legacy "nothing to do" message is
    # preserved. When auto-accept is on we still need to load the index so
    # the orphan auto-promote pass below can run.
    if not proposals and not auto_accept:
        print("sweep-proposals: nothing to do (no proposal JSONs on disk)")
        return 0

    # Resolve source-task repo once for the whole sweep -- read index here
    # rather than per-proposal so the sweep runs with O(1) index parses.
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: cannot read backlog index: {exc}", file=sys.stderr)
        return 1
    unit_by_id = {u.id: u for u in units}
    # ADR-11 audit suffix written on every auto-promoted draft's Comments
    # so a reviewer can tell at a glance that no human pressed the button.
    auto_audit_suffix = "(auto-accepted via task_factory.auto_accept_proposals=true)"

    touched = 0
    for proposal in proposals:
        touched += _sweep_one_proposal(
            proposal=proposal,
            unit_by_id=unit_by_id,
            auto_accept=auto_accept,
            auto_audit_suffix=auto_audit_suffix,
        )

    # Issue #155: extend the auto-promote pass to also pick up pre-existing
    # ``proposed`` drafts whose proposal JSON no longer exists (operator
    # deleted it, or sweep skipped it earlier). The first pass above only
    # iterates over JSONs on disk; this second pass walks the full backlog
    # index and promotes any ``proposed`` task whose source carries the
    # auto-accept toggle.
    if auto_accept:
        orphan_promoted = _auto_promote_orphan_proposed_drafts(units, auto_audit_suffix)
        if orphan_promoted:
            print(f"sweep-proposals: orphan auto-promoted {orphan_promoted} pre-existing proposed draft(s)")

    logger.info("sweep-proposals touched %d draft(s) across %d proposal(s)", touched, len(proposals))
    return 0


def _sweep_one_proposal(
    *,
    proposal: Proposal,
    unit_by_id: dict[str, WorkUnit],
    auto_accept: bool,
    auto_audit_suffix: str,
) -> int:
    """Materialise + (optionally) auto-promote a single proposal JSON.

    Extracted from ``cmd_sweep_proposals`` so the dispatcher body stays
    under ruff's PLR0912 branch ceiling. Returns the number of new drafts
    created on this call (used to drive the ``touched`` counter).
    """
    pre_states = {
        task.suggested_id: classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, task.suggested_id)
        for task in proposal.proposed_tasks
    }
    unmaterialised_before = sum(1 for state in pre_states.values() if state is ProposalTaskState.UNMATERIALISED)
    proposed_before = sum(1 for state in pre_states.values() if state is ProposalTaskState.PROPOSED)
    needs_materialise = unmaterialised_before > 0
    needs_auto_promote = auto_accept and (unmaterialised_before > 0 or proposed_before > 0)

    if not needs_materialise and not needs_auto_promote:
        # Issue #302: every task in this proposal is resolved, so the JSON
        # has done its job. Leaving it on disk keeps the source task pinned
        # to AWAITING_AMENDMENT_RECOVERY (``_has_pending_proposal_json`` is
        # pure file presence) and keeps the sweep re-reading it on every
        # orchestrate loop start. Delete it here, at the one point that
        # knows the whole proposal is finished.
        delete_proposal_if_consumed(WORKSPACE_ROOT, BACKLOG_ROOT, proposal)
        print(f"sweep-proposals: no-op {proposal.source_task_id}")
        return 0

    source_unit = unit_by_id.get(proposal.source_task_id)
    if source_unit is None:
        print(
            f"sweep-proposals: skipped {proposal.source_task_id}: source not found in backlog index",
            file=sys.stderr,
        )
        return 0

    drafts: list[Path] = []
    if needs_materialise:
        try:
            drafts = materialise_proposal(
                workspace_root=WORKSPACE_ROOT,
                backlog_root=BACKLOG_ROOT,
                backlog_index=BACKLOG_INDEX,
                proposal=proposal,
                repo=source_unit.repo,
            )
        except ProposalError as exc:
            print(f"sweep-proposals: skipped {proposal.source_task_id}: {exc}")
            return 0

    auto_promoted = 0
    if auto_accept:
        for task in proposal.proposed_tasks:
            state = classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, task.suggested_id)
            if state is not ProposalTaskState.PROPOSED:
                continue
            try:
                promote_proposal(
                    workspace_root=WORKSPACE_ROOT,
                    backlog_root=BACKLOG_ROOT,
                    backlog_index=BACKLOG_INDEX,
                    task_id=task.suggested_id,
                    audit_suffix=auto_audit_suffix,
                )
                auto_promoted += 1
            except ProposalError as exc:
                print(
                    f"sweep-proposals: auto-promote failed for {task.suggested_id}: {exc}",
                    file=sys.stderr,
                )

    # Issue #302: a proposal whose tasks all resolved during this call is
    # finished too, not only one that arrived resolved.
    delete_proposal_if_consumed(WORKSPACE_ROOT, BACKLOG_ROOT, proposal)

    skipped_count = len(proposal.proposed_tasks) - len(drafts)
    line = f"sweep-proposals: materialised {proposal.source_task_id}: {len(drafts)} new, {skipped_count} skipped"
    if auto_accept:
        line += f" (auto-promoted: {auto_promoted})"
    print(line)
    return len(drafts)


def _auto_promote_orphan_proposed_drafts(units: list[WorkUnit], audit_suffix: str) -> int:
    """Promote every ``proposed`` task whose source has ``auto_accept_proposals``.

    Issue #155: ``cmd_sweep_proposals`` historically only promoted drafts
    referenced by a live proposal JSON. Drafts whose JSON has been deleted
    (e.g. archived after a partial sweep) were marooned in the ``proposed``
    state forever. This helper closes the gap by walking the full backlog
    index and promoting every ``proposed`` task. Idempotent: per-draft
    classify guard inside ``promote_proposal`` is the source of truth, so
    re-runs cause no duplicate marker writes.

    Returns the count of drafts successfully promoted.
    """
    promoted = 0
    for unit in units:
        if unit.unit_type is not WorkUnitType.TASK:
            continue
        if unit.status is not WorkUnitStatus.PROPOSED:
            continue
        state = classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, unit.id)
        if state is not ProposalTaskState.PROPOSED:
            continue
        try:
            promote_proposal(
                workspace_root=WORKSPACE_ROOT,
                backlog_root=BACKLOG_ROOT,
                backlog_index=BACKLOG_INDEX,
                task_id=unit.id,
                audit_suffix=audit_suffix,
            )
            promoted += 1
        except ProposalError as exc:
            print(
                f"sweep-proposals: orphan auto-promote failed for {unit.id}: {exc}",
                file=sys.stderr,
            )
    return promoted


def _enforce_materialise_lifecycle_gates(source_task_id: str, proposal: Proposal) -> int:
    """Issues #143 + #144: gate ``cmd_materialise_proposal`` on placeholder
    rows + cascade-depth cap. Returns 0 to proceed, 1 to abort with the
    error already printed to stderr."""
    placeholder_issues = detect_placeholder_descriptions(proposal)
    if placeholder_issues:
        print(
            "ERROR: proposal carries placeholder description(s); fill in concrete "
            "approach text before materialising:\n  - " + "\n  - ".join(placeholder_issues),
            file=sys.stderr,
        )
        return 1
    try:
        enforce_cascade_depth({"cascade_depth": proposal.cascade_depth}, MAX_CASCADE_DEPTH)
    except CascadeDepthError as exc:
        print(
            f"ERROR: cascade-depth limit reached for {source_task_id}: {exc}\n"
            f"Source task escalated to OPERATOR_ACTION_REQUIRED (no draft materialised).",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_materialise_proposal(source_task_id: str) -> int:
    """Materialise a pending proposal into draft work-unit files.

    Reads ``<workspace>/.devbench/proposals/<source-task-id>.json`` and
    writes one proposed draft ``.md`` per ``proposed_tasks`` entry, then
    inserts a row in ``BACKLOG.md`` for each. Used by the task-factory
    agent as the atomic "generate drafts" step.
    """
    try:
        proposal = read_proposal(WORKSPACE_ROOT, source_task_id)
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    gate_rc = _enforce_materialise_lifecycle_gates(source_task_id, proposal)
    if gate_rc != 0:
        return gate_rc

    # Resolve the target repo from the source task so every draft row uses
    # the same repo string that the orchestrator will execute against.
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: cannot read backlog index: {exc}", file=sys.stderr)
        return 1
    source_unit = next((u for u in units if u.id == source_task_id), None)
    if source_unit is None:
        print(f"ERROR: source task {source_task_id} not found in backlog", file=sys.stderr)
        return 1

    # Pre-classify each proposed task so the CLI output can distinguish
    # "skipped because already resolved" from "materialised just now".
    # materialise_proposal itself logs the same skip rationale at INFO.
    pre_states = {
        task.suggested_id: classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, task.suggested_id)
        for task in proposal.proposed_tasks
    }

    try:
        drafts = materialise_proposal(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            proposal=proposal,
            repo=source_unit.repo,
        )
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    skipped = {tid: state.value for tid, state in pre_states.items() if state is not ProposalTaskState.UNMATERIALISED}
    logger.info(
        "Materialised %d proposed task(s) from %s (skipped %d)",
        len(drafts),
        source_task_id,
        len(skipped),
    )
    print(
        json.dumps(
            {
                "source_task_id": source_task_id,
                "materialised": [str(p) for p in drafts],
                "skipped": skipped,
            }
        )
    )
    return 0


class _ProposalInputError(ValueError):
    """Raised when stdin-provided proposal JSON is unusable."""


def _read_proposal_from_stdin(source_task_id: str) -> Proposal:
    """Read stdin and build a :class:`Proposal`, raising ``_ProposalInputError`` on failures."""

    try:
        raw = sys.stdin.read()
    except OSError as exc:
        raise _ProposalInputError(f"cannot read stdin: {exc}") from exc
    if not raw.strip():
        raise _ProposalInputError("proposal JSON required on stdin")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ProposalInputError(f"proposal is not valid JSON: {exc}") from exc
    try:
        proposal = Proposal.from_dict(data)
    except ValueError as exc:
        raise _ProposalInputError(f"proposal invalid: {exc}") from exc
    if proposal.source_task_id != source_task_id:
        raise _ProposalInputError(
            f"proposal.source_task_id ({proposal.source_task_id!r}) does not match argument ({source_task_id!r})"
        )
    return proposal


def cmd_write_proposal(source_task_id: str) -> int:
    """Persist a blocker-resolver proposal JSON read from stdin.

    Dedup contract (issue #141): before writing, computes a stable
    ``fix_signature`` over (target_repo, sorted(files_to_own),
    normalised intent_phrase). If a pending proposal on disk already
    carries the same signature, this command WIRES THE NEW SOURCE TASK
    AS AN ADDITIONAL DEP on the existing recovery task instead of
    emitting a duplicate proposal. The audit comment ``[RECOVERY_REUSED]``
    on the new source task names the existing recovery task ID. The
    return-code envelope's ``recovery_reused`` field tells the caller
    which path fired.

    When ``task_factory.auto_accept_proposals`` is ``true`` in the active
    config and the dedup path did NOT fire, this command also
    materialises every proposed task and promotes it -- so the cascade
    is actionable in the same call.

    When the auto-accept flag is ``false`` the behaviour is unchanged:
    the JSON is written and the operator promotes manually.
    """
    try:
        proposal = _read_proposal_from_stdin(source_task_id)
        # Issue #146: drop proposed-task entries whose files all live in
        # the backlog repo (not in any configured target repo). The
        # recovery cascade has no valid endpoint for backlog-repo edits;
        # they're operator bookkeeping commits, not work-unit deliverables.
        # NOTE: this filter runs BEFORE the prefix strip below because the
        # filter classifies paths by their first segment matching a
        # configured `checkout_directory`; stripping the prefix first
        # would erase that signal and misclassify target-repo paths as
        # backlog-repo paths.
        proposal, skipped_entries = _filter_backlog_repo_proposed_tasks(proposal)
        for skipped_id, skipped_files in skipped_entries:
            audit = (
                f"[RECOVERY_SKIPPED_BACKLOG_REPO_FILES] source_task={source_task_id} "
                f"proposed_task={skipped_id} files={','.join(skipped_files)}: "
                f"all files live in the backlog repo (not in any configured target repo); "
                f"commit as backlog-repo bookkeeping by hand; no work-unit recovery created."
            )
            logger.info("write-proposal: %s", audit)
        if not proposal.proposed_tasks:
            # Every proposed task was backlog-repo only -- nothing to write.
            # Source task escalates: operator must commit the backlog-repo
            # bookkeeping by hand and decide whether the source task can
            # otherwise proceed.
            print(
                json.dumps(
                    {
                        "source_task_id": source_task_id,
                        "proposal_path": None,
                        "recovery_skipped": True,
                        "reason": "all proposed tasks owned only backlog-repo files",
                    }
                )
            )
            return 0
        # Issue #159: blocker-resolver agents sometimes prefix paths with
        # the target repo's `checkout_directory` (e.g. `kanon/src/foo.py`
        # when `kanon` is configured as the checkout directory). Strip
        # the prefix so the persisted JSON carries repo-relative paths
        # only, matching the manifest-path convention enforced by
        # validate-backlog rule 11 + guard-work-unit-write.sh. Paths that
        # match multiple configured `checkout_directories` are ambiguous
        # and reject the whole proposal. Strip runs AFTER the backlog-
        # repo filter so the filter can still classify by first-segment
        # match with the unstripped paths.
        proposal = _strip_checkout_directory_prefix(proposal)
        # Compute the dedup signature even when the agent did not stamp it.
        proposal = _stamp_fix_signature(proposal)
        # Issue #141: scan for an existing recovery task whose fix
        # signature matches. If found, wire the new source task as an
        # additional dep edge instead of writing a duplicate proposal.
        match = find_matching_pending_proposal(WORKSPACE_ROOT, proposal.fix_signature)
        if match is not None and match.source_task_id != source_task_id:
            return _wire_recovery_reuse(source_task_id, proposal, match)
        written = write_proposal(WORKSPACE_ROOT, proposal)
    except (_ProposalInputError, ProposalError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    auto_cascade = _maybe_auto_cascade_proposal(source_task_id, proposal)
    output: dict[str, object] = {
        "source_task_id": source_task_id,
        "proposal_path": str(written),
        "fix_signature": proposal.fix_signature,
        "cascade_depth": proposal.cascade_depth,
        "recovery_reused": False,
    }
    output.update(auto_cascade)
    print(json.dumps(output))
    return 0


def _stamp_fix_signature(proposal: Proposal) -> Proposal:
    """Return a copy of ``proposal`` with ``fix_signature`` populated.

    No-op when the proposal already carries a non-empty signature
    (operator-edited proposals keep their original signature so dedup
    matching stays stable across hand edits).

    Signature inputs:
      - ``target_repo``: read from the source task's BACKLOG row when
        available; falls back to "" when the source isn't in the index
        yet (sufficient for the early-emission case).
      - ``files_to_own``: flattened sorted across every proposed task.
      - ``intent_phrase``: derived from the FIRST proposed task's
        ``suggested_approach`` via the regex normaliser.
    """
    if proposal.fix_signature:
        return proposal
    target_repo = _resolve_source_repo(proposal.source_task_id)
    files: list[str] = []
    for task in proposal.proposed_tasks:
        files.extend(task.files_to_own)
    intent = ""
    if proposal.proposed_tasks:
        intent = _extract_intent_phrase(proposal.proposed_tasks[0].suggested_approach)
    signature = _compute_fix_signature(target_repo, files, intent)
    from dataclasses import replace as _replace

    return _replace(proposal, fix_signature=signature)


def _resolve_source_repo(source_task_id: str) -> str:
    """Best-effort repo lookup for a source task id; returns "" when not found."""
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError):
        return ""
    for unit in units:
        if unit.id == source_task_id:
            return unit.repo or ""
    return ""


def _file_lives_in_a_target_repo(file_path: str, source_task_id: str | None = None) -> bool:
    """Return True iff ``file_path`` (workspace-relative) lives inside one
    of the configured target repos (issue #146).

    A workspace-relative path is "in a target repo" when its first path
    segment matches the ``checkout_directory`` of any
    ``RUNTIME_CONFIG.repos[*]`` entry. Files outside every configured
    target repo (e.g. ``spec/observability.md``, ``BACKLOG.md``,
    ``backlog/**/*.md``, ``docs/*.md``) are backlog-repo bookkeeping
    edits, not work-unit deliverables, and the recovery cascade has no
    valid completion path for them.

    Issue #180: when ``source_task_id`` is provided AND that task resolves
    to a configured target repo, ``file_path`` is treated as
    repo-relative INSIDE the source's target repo when its first segment
    does not match any other configured ``checkout_directory``. This
    closes the misclassification gap where blocker-resolver agents
    emit repo-relative paths (e.g. ``src/foo.py``) from inside the
    source's checkout -- previously such paths were classified as
    backlog-repo bookkeeping and the recovery cascade was silently
    skipped. The source's own ``checkout_directory`` is still consulted
    first so explicitly-prefixed paths (``kanon/src/foo.py``) classify
    identically.
    """
    if not file_path:
        return False
    if not RUNTIME_CONFIG.repos:
        # No target repos configured -- the filter has no basis for
        # classification; treat every file as in-scope (conservative).
        return True
    first_segment = file_path.split("/", 1)[0]
    all_checkouts: set[str] = set()
    for repo_cfg in RUNTIME_CONFIG.repos.values():
        checkout = repo_cfg.checkout_directory or (
            repo_cfg.validated_repo.split("/", 1)[1] if repo_cfg.validated_repo else None
        )
        if checkout:
            all_checkouts.add(checkout)
    if first_segment in all_checkouts:
        return True
    if source_task_id:
        target_repo = _resolve_source_repo(source_task_id)
        if target_repo and target_repo in RUNTIME_CONFIG.repos:
            # The source task targets a known repo and ``file_path``
            # carries no target-repo prefix -- assume it is a repo-
            # relative path inside the source's target repo (blocker-
            # resolver agents that run from inside the checkout emit
            # paths in this form). Issue #180.
            return True
    return False


def _strip_checkout_directory_prefix(proposal: Proposal) -> Proposal:
    """Issue #159: strip ``<checkout_directory>/`` prefixes from every
    ``proposed_tasks[*].files_to_own`` entry.

    blocker-resolver agents occasionally emit paths prefixed with the
    target repo's ``checkout_directory`` (e.g. ``kanon/src/foo.py`` when
    ``kanon`` is configured as the kanon repo's ``checkout_directory``).
    Persisted as-is, those paths fail validate-backlog rule 11 on every
    iteration and require operator hand-fix via ``sed``. The strip here
    makes the agent's output canonical regardless of whether it
    remembered to stay repo-relative.

    Raises ``ProposalError`` when a single path matches multiple
    configured ``checkout_directories`` (rare; only when checkout
    directories share a common ancestor segment). Operator must fix the
    ambiguous path by hand rather than have devbench silently pick one
    interpretation.
    """
    from dataclasses import replace as _replace

    if not RUNTIME_CONFIG.repos:
        return proposal

    # Enumerate every configured checkout_directory (deterministic order
    # so the multi-match error message is reproducible).
    checkout_dirs: list[str] = sorted(
        {
            (
                repo_cfg.checkout_directory
                or (repo_cfg.validated_repo.split("/", 1)[1] if repo_cfg.validated_repo else "")
            )
            for repo_cfg in RUNTIME_CONFIG.repos.values()
        }
    )
    checkout_dirs = [d for d in checkout_dirs if d]
    if not checkout_dirs:
        return proposal

    new_tasks: list = []
    for task in proposal.proposed_tasks:
        new_files: list[str] = []
        for raw in task.files_to_own:
            matches = [d for d in checkout_dirs if raw == d or raw.startswith(d + "/")]
            if len(matches) > 1:
                raise ProposalError(
                    f"proposed_tasks[{task.suggested_id}].files_to_own contains an ambiguous path "
                    f"{raw!r}: matches multiple configured checkout_directories "
                    f"({', '.join(matches)}); fix the proposal so the path is unambiguous."
                )
            if len(matches) == 1:
                prefix = matches[0]
                if raw == prefix:
                    # Path IS the checkout directory itself (no file
                    # selected). Skip; the agent must name a real file.
                    continue
                new_files.append(raw[len(prefix) + 1 :])
            else:
                new_files.append(raw)
        new_tasks.append(_replace(task, files_to_own=new_files))
    return _replace(proposal, proposed_tasks=new_tasks)


def _filter_backlog_repo_proposed_tasks(proposal: Proposal) -> tuple[Proposal, list[tuple[str, list[str]]]]:
    """Issue #146: drop proposed-task entries whose every file lives in
    the backlog repo (i.e., NOT in any configured target repo).

    Returns a (filtered_proposal, skipped) tuple. ``skipped`` is a list
    of (suggested_id, files) for the dropped entries; the caller logs
    ``[RECOVERY_SKIPPED_BACKLOG_REPO_FILES]`` audits naming each set.
    For mixed entries (some files in target repos, some in backlog
    repo), the entry is RETAINED with only the target-repo files (the
    backlog-repo files are pruned) and a ``[RECOVERY_PARTIAL_BACKLOG_REPO_SKIP]``
    audit is logged separately by the caller.
    """
    from dataclasses import replace as _replace

    kept: list = []
    skipped: list[tuple[str, list[str]]] = []
    mutated = False
    source_task_id = proposal.source_task_id
    for task in proposal.proposed_tasks:
        if not task.files_to_own:
            # Empty files_to_own = research / validation-gate task; not
            # backlog-only by intent. Preserve as-is.
            kept.append(task)
            continue
        target_repo_files = [f for f in task.files_to_own if _file_lives_in_a_target_repo(f, source_task_id)]
        backlog_files = [f for f in task.files_to_own if not _file_lives_in_a_target_repo(f, source_task_id)]
        if not target_repo_files:
            # Entirely backlog-repo work -- drop the entry.
            skipped.append((task.suggested_id, backlog_files))
            mutated = True
            continue
        if backlog_files:
            # Mixed -- prune the backlog files; keep target-repo files.
            kept.append(_replace(task, files_to_own=target_repo_files))
            mutated = True
        else:
            kept.append(task)
    if not mutated:
        return proposal, skipped
    return _replace(proposal, proposed_tasks=kept), skipped


def _wire_recovery_reuse(
    source_task_id: str,
    proposal: Proposal,
    match: ProposalMatch,
) -> int:
    """Issue #141: instead of writing a duplicate proposal, wire the
    new source task as an additional dep on the existing recovery task,
    log a ``[RECOVERY_REUSED]`` audit, and emit the JSON envelope.
    """
    audit_msg = (
        f"[RECOVERY_REUSED] reusing existing recovery task {match.source_task_id} for "
        f"fix_signature {match.fix_signature[:16]} (full proposal at {match.proposal_path})"
    )
    try:
        add_dep(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            blocked_task_id=source_task_id,
            blocker_task_id=match.source_task_id,
            reason=audit_msg,
        )
    except (FileNotFoundError, ProposalError) as exc:
        # Failure to wire dep falls back to the emit path so the
        # operator is not silently left in a half-state. Surface as
        # error; caller (the orchestrator) can choose to retry.
        print(f"ERROR: recovery-reuse dep wiring failed: {exc}", file=sys.stderr)
        return 1
    output = {
        "source_task_id": source_task_id,
        "recovery_reused": True,
        "reused_from_task_id": match.source_task_id,
        "reused_proposal_path": str(match.proposal_path),
        "fix_signature": proposal.fix_signature,
    }
    print(json.dumps(output))
    return 0


def _maybe_auto_cascade_proposal(source_task_id: str, proposal: Proposal) -> dict[str, object]:
    """Materialise + promote every proposed task when auto-accept is on.

    Returns a result dict suitable for embedding in the
    ``write-proposal`` JSON output. Entries:

    - ``auto_cascade``: ``"applied"`` | ``"disabled"`` | ``"failed"``
    - ``materialised``: list of draft Path strings (when applied)
    - ``promoted``: list of task ids successfully promoted (when applied)
    - ``error``: present only when ``auto_cascade == "failed"``

    Soft failure: any error during the cascade is logged + reported in
    the returned dict but does NOT propagate as a non-zero exit. The
    JSON is already on disk; the next ``sweep-proposals`` cycle will
    retry the cascade.
    """
    if not RUNTIME_CONFIG.task_factory.auto_accept_proposals:
        return {"auto_cascade": "disabled"}

    audit_suffix = "(auto-accepted via task_factory.auto_accept_proposals=true at write-proposal time)"

    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            "write-proposal auto-cascade aborted for %s -- cannot read backlog index: %s",
            source_task_id,
            exc,
        )
        return {"auto_cascade": "failed", "error": str(exc)}
    source_unit = next((u for u in units if u.id == source_task_id), None)
    if source_unit is None:
        logger.warning(
            "write-proposal auto-cascade aborted for %s -- source not in backlog index",
            source_task_id,
        )
        return {"auto_cascade": "failed", "error": "source not found in backlog index"}

    try:
        materialised = materialise_proposal(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            proposal=proposal,
            repo=source_unit.repo,
        )
    except ProposalError as exc:
        logger.warning(
            "write-proposal auto-cascade materialise failed for %s: %s",
            source_task_id,
            exc,
        )
        return {"auto_cascade": "failed", "error": str(exc)}

    # Drafts materialised by THIS call must always be wired, whatever status
    # they were born with. Under `backlog.default_status_for_new_work_units:
    # in-queue` a fresh draft classifies as PROMOTED rather than PROPOSED, so
    # a PROPOSED-only guard skips promote_proposal entirely -- and with it the
    # dependency row and the `[BLOCKED_PENDING_PROPOSAL]` marker that the
    # ADR-07 cascade needs to auto-unblock the source once the recovery task
    # completes. The source would stay blocked forever with no marker naming
    # what it is waiting for. The status check still guards drafts left over
    # from earlier runs, which must not be re-promoted.
    just_materialised = {path.stem for path in materialised}
    promoted: list[str] = []
    for task in proposal.proposed_tasks:
        state = classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, task.suggested_id)
        if state is not ProposalTaskState.PROPOSED and task.suggested_id not in just_materialised:
            continue
        try:
            promote_proposal(
                workspace_root=WORKSPACE_ROOT,
                backlog_root=BACKLOG_ROOT,
                backlog_index=BACKLOG_INDEX,
                task_id=task.suggested_id,
                audit_suffix=audit_suffix,
            )
            promoted.append(task.suggested_id)
        except ProposalError as exc:
            logger.warning(
                "write-proposal auto-cascade promote failed for %s: %s",
                task.suggested_id,
                exc,
            )
    delete_proposal_if_consumed(WORKSPACE_ROOT, BACKLOG_ROOT, proposal)

    logger.info(
        "write-proposal auto-cascade applied for %s: materialised=%d promoted=%d",
        source_task_id,
        len(materialised),
        len(promoted),
    )
    return {
        "auto_cascade": "applied",
        "materialised": [str(p) for p in materialised],
        "promoted": promoted,
    }


def cmd_list_proposals() -> int:
    """Print every pending task-factory proposal with per-task lifecycle labels.

    Output is a short human-readable listing with one line per proposed
    task. Each line is prefixed with a state label so the operator can
    distinguish un-materialised entries (JSON-only, no draft yet) from
    proposed drafts, promoted / done / declined drafts, and archived
    rejections. For machine parsing, read
    ``<workspace>/.devbench/proposals/<source-id>.json`` directly.
    """
    proposals = list_proposals(WORKSPACE_ROOT)
    if not proposals:
        print("No pending proposals.")
        return 0
    total = sum(len(p.proposed_tasks) for p in proposals)
    print(f"Pending proposals ({total}):")
    for proposal in proposals:
        for task in proposal.proposed_tasks:
            state = classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, task.suggested_id)
            label = f"[{state.value}]".ljust(_PROPOSAL_STATE_LABEL_WIDTH)
            print(
                f"  {label} {task.suggested_id}  {task.title}  "
                f"(from {proposal.source_task_id}, generated {proposal.generated_at})"
            )
    return 0


# Width pre-computed from the longest ProposalTaskState value
# ("unmaterialised" -> 14 chars + surrounding brackets). Updating the enum
# triggers the test in ``tests/test_cli.py::TestCmdListProposals`` that pins
# alignment.
_PROPOSAL_STATE_LABEL_WIDTH = max(len(s.value) for s in ProposalTaskState) + 2


def _count_unmaterialised_proposed_tasks() -> int:
    """Return the count of proposal JSON entries that have no draft .md yet.

    A single JSON may contribute multiple un-materialised tasks; each
    ``proposed_tasks[].suggested_id`` is classified independently. Used
    by ``cmd_status`` to surface the count next to the other lifecycle
    totals.
    """
    count = 0
    for proposal in list_proposals(WORKSPACE_ROOT):
        for task in proposal.proposed_tasks:
            state = classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, task.suggested_id)
            if state is ProposalTaskState.UNMATERIALISED:
                count += 1
    return count


def _count_blocked_split(units: list[WorkUnit]) -> dict[BlockedTaskState, int]:
    """Return a per-bucket count for each of the six :class:`BlockedTaskState` values.

    Iterates every blocked work unit and classifies it via
    :func:`classify_blocked_task`. The six counts always sum to the total
    number of blocked tasks in ``units`` so the aggregate is recoverable by
    addition.

    Only BLOCKED-status tasks are iterated; HOLD-status tasks are excluded by
    the ``u.status != WorkUnitStatus.BLOCKED`` guard.  The HELD bucket therefore
    remains zero unless the classifier promotes a blocked task to that state.
    """
    result: dict[BlockedTaskState, int] = dict.fromkeys(BlockedTaskState, 0)
    for u in units:
        if u.status != WorkUnitStatus.BLOCKED:
            continue
        state = classify_blocked_task(
            BACKLOG_ROOT,
            BACKLOG_INDEX,
            u.id,
            workspace_root=WORKSPACE_ROOT,
            recovery_window_seconds=BLOCKED_RECOVERY_WINDOW_SECONDS,
        )
        result[state] += 1
    return result


def cmd_promote_proposal(first_arg: str, second_arg: str = "") -> int:
    """Flip a proposed task to ``in-queue`` and wire dependencies.

    Usage::

        promote-proposal <id>
        promote-proposal --no-dep-on-source <id>
        promote-proposal --all-from <source-task-id>

    ``--no-dep-on-source`` skips the auto-dep wiring (the default is to add
    the promoted task as a dependency of the source task that originated the
    proposal).
    """
    dep_on_source = True
    if first_arg == "--no-dep-on-source":
        dep_on_source = False
        task_id = second_arg
    elif first_arg == "--all-from":
        if not second_arg:
            print("ERROR: --all-from requires a source task id", file=sys.stderr)
            return 1
        return _run_promote_all(second_arg)
    else:
        task_id = first_arg

    if not task_id:
        print("ERROR: promote-proposal requires a task id", file=sys.stderr)
        return 1

    # Heuristic: warn (or honor proposal flag) when the new task looks like a
    # test that validates the source task's output. In that pattern, the
    # default dep direction (source.depends_on(new)) creates a circular
    # cycle. See docs/task-factory.md "When to use --no-dep-on-source".
    if dep_on_source:
        proposal_hint = _detect_test_validates_source(task_id)
        if proposal_hint == "flag":
            print(
                f"NOTE: proposal source_dep_direction='test_validates_source' on "
                f"{task_id}; auto-applying --no-dep-on-source.",
                file=sys.stderr,
            )
            dep_on_source = False
        elif proposal_hint == "heuristic":
            print(
                f"WARNING: {task_id} looks like a test-validates-source task "
                f"(title or files_to_own match the heuristic). The default dep "
                f"direction (source.depends_on(new)) may create a cycle. Re-run "
                f"with --no-dep-on-source if appropriate; see "
                f"docs/task-factory.md 'When to use --no-dep-on-source'.",
                file=sys.stderr,
            )

    try:
        result = promote_proposal(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            task_id=task_id,
            dep_on_source=dep_on_source,
        )
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    logger.info(
        "Promoted proposal %s -> in-queue; wired marker on %d task(s)",
        task_id,
        len(result.wired_targets),
    )
    print(
        json.dumps(
            {
                "task_id": task_id,
                "status": "in-queue",
                "file_path": str(result.draft_path),
                "wired_targets": list(result.wired_targets),
            }
        )
    )
    return 0


def _run_promote_all(source_task_id: str) -> int:
    try:
        promoted = promote_all_from_source(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            source_task_id=source_task_id,
        )
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for path in promoted:
        logger.info("Promoted %s", path.stem)
    print(json.dumps({"source_task_id": source_task_id, "promoted_count": len(promoted)}))
    return 0


_TEST_VALIDATES_SOURCE_TITLE_PREFIXES: tuple[str, ...] = (
    "Add tests/",
    "Add unit tests",
    "Add integration tests",
    "Verify ",
    "Validate ",
    "Assert ",
)


def _detect_test_validates_source(task_id: str) -> str:
    """Classify a proposed task's relationship to its source.

    Returns ``"flag"`` when the proposal JSON declares
    ``source_dep_direction == "test_validates_source"``; ``"heuristic"`` when
    the proposed task's title or files_to_own match the test-validates-source
    heuristic but the flag is not set; ``""`` otherwise.

    Reads the proposal JSON files under ``$WORKSPACE_ROOT/.devbench/proposals/``
    and matches by ``ProposedTask.suggested_id``. Returns ``""`` on any I/O or
    parse error so the warning is best-effort and never blocks promotion.
    """
    proposals_dir = WORKSPACE_ROOT / ".devbench" / "proposals"
    if not proposals_dir.is_dir():
        return ""
    try:
        for path in proposals_dir.glob("*.json"):
            try:
                with path.open() as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            for entry in data.get("proposed_tasks", []):
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("suggested_id", "")).strip() != task_id:
                    continue
                if str(data.get("source_dep_direction", "")).strip() == "test_validates_source":
                    return "flag"
                title = str(entry.get("title", "")).strip()
                files = entry.get("files_to_own") or []
                if any(title.startswith(p) for p in _TEST_VALIDATES_SOURCE_TITLE_PREFIXES):
                    return "heuristic"
                if files and all(str(f).startswith("tests/") or "/tests/" in str(f) for f in files):
                    return "heuristic"
    except OSError:
        return ""
    return ""


def cmd_add_dep(*argv: str) -> int:
    """Wire a cross-task dependency + marker on an existing work unit.

    Usage::

        add-dep <blocked-task-id> <blocker-task-id> [--reason "<audit message>"]

    Writes a canonical ``## Dependencies`` row -- the form
    ``validate-backlog``'s Manifest Conflict Rule reads -- alongside the
    existing ``[WU_WIRED] ... [BLOCKED_PENDING_PROPOSAL] <blocker>`` audit
    marker on the blocked task's file (#330 FR-1). The row's Title and
    Status cells carry the blocker's real, current values as of this call,
    not a placeholder. The ADR-07 auto-requeue cascade still only
    auto-unblocks the blocked task when the blocker reaches ``done`` /
    ``declined`` AND the blocked task's own status is ``blocked``; the
    Dependencies row has no such restriction, so it is what satisfies the
    validator regardless of the blocked task's current status.

    Used for three scenarios the ``promote-proposal`` flow does not cover:

    1. Operator realises AFTER a promote that an additional task should have
       been listed in the proposal's ``affected_task_ids``.
    2. Operator hand-authored a work unit (not via task-factory) that
       unblocks another task and wants to wire the marker without touching
       the file by hand.
    3. Operator corrects a proposal authored without ``affected_task_ids``
       retroactively.

    Fail-fast (#330 FR-1 error handling): every path below exits non-zero,
    prints a message naming the file (when one is implicated) and the
    reason, and leaves no partial write behind -- validation runs to
    completion before anything is written.

      - Both IDs must match the task-ID regex.
      - Blocked must exist in the backlog index.
      - Blocker must exist in the backlog index.
      - Blocker must not be in a terminal state (``done`` / ``declined``).
      - Blocked and blocker cannot be the same.
      - The blocked task's file must be readable and contain a
        ``## Dependencies`` section.

    Warns (but does not refuse) when the blocked task is not currently in
    ``blocked`` status: the ADR-07 cascade will not fire until it is, but
    (#330 FR-2) the ``## Dependencies`` row this call writes satisfies the
    validator now regardless of that status.

    Idempotent: calling ``add-dep`` twice for the same pair leaves exactly
    one Dependencies row and one marker. ``wired: true`` in the output JSON
    means the blocked task's ``## Dependencies`` table carries a
    validator-visible row for the blocker as of THIS call -- true whether
    the row was newly written or already present. ``wired: false`` means no
    such row could be produced; the exit code is non-zero in that case and
    ``reason`` explains why (#330 FR-2).
    """
    blocked_task_id, blocker_task_id, reason = _parse_add_dep_argv(argv)
    if blocked_task_id is None:
        return 1

    rc = _reject_em_dash("reason", reason) if reason else None
    if rc is not None:
        return rc

    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        return _fail_add_dep(blocked_task_id, blocker_task_id, f"cannot read backlog index: {exc}")

    blocked_unit = next((u for u in units if u.id == blocked_task_id), None)
    blocker_unit = next((u for u in units if u.id == blocker_task_id), None)
    if blocked_unit is None or blocker_unit is None:
        role, missing_id = ("blocked", blocked_task_id) if blocked_unit is None else ("blocker", blocker_task_id)
        return _fail_add_dep(
            blocked_task_id, blocker_task_id, f"add-dep: {role} task '{missing_id}' not found in backlog index"
        )

    # Warn when blocked is not in `blocked` status (ADR-10 soft guidance).
    if blocked_unit.status != WorkUnitStatus.BLOCKED:
        print(
            f"WARNING: add-dep: {blocked_task_id} is currently '{blocked_unit.status.value}', "
            "not 'blocked'. The ADR-07 cascade will not fire until the task is blocked -- "
            "but the '## Dependencies' row this call writes to the blocked unit's file "
            "satisfies the Manifest Conflict Rule now, independent of that status.",
            file=sys.stderr,
        )

    try:
        wired = _write_add_dep_edge(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            blocked_task_id=blocked_task_id,
            blocked_file=blocked_unit.file_path,
            blocker_task_id=blocker_task_id,
            blocker_unit=blocker_unit,
            reason=reason,
        )
    except (ProposalError, OSError, UnicodeDecodeError) as exc:
        return _fail_add_dep(blocked_task_id, blocker_task_id, str(exc))

    logger.info(
        "add-dep: %s blocked on %s (wired=%s)",
        blocked_task_id,
        blocker_task_id,
        wired,
    )
    print(
        json.dumps(
            {
                "blocked": blocked_task_id,
                "blocker": blocker_task_id,
                "wired": wired,
                "reason": reason,
            }
        )
    )
    return 0 if wired else 1


def _fail_add_dep(blocked_task_id: str, blocker_task_id: str, message: str) -> int:
    """Print an ``add-dep`` failure, emit ``wired: false`` JSON, and return 1.

    Centralises the AC-4 / AC-5 failure contract (#330 FR-1, FR-2): every
    path that cannot produce a validator-visible Dependencies row exits
    non-zero, names the reason, and reports ``"wired": false`` on the same
    JSON payload the success path uses (same keys, per AC-6) so a caller
    parsing stdout never observes a stale ``"wired": true"``.
    """
    print(f"ERROR: {message}", file=sys.stderr)
    print(
        json.dumps(
            {
                "blocked": blocked_task_id,
                "blocker": blocker_task_id,
                "wired": False,
                "reason": message,
            }
        )
    )
    return 1


def _write_add_dep_edge(
    *,
    backlog_root: Path,
    backlog_index: Path,
    blocked_task_id: str,
    blocked_file: Path,
    blocker_task_id: str,
    blocker_unit: WorkUnit,
    reason: str,
) -> bool:
    """Write the ``add-dep`` edge and report whether it is validator-visible (#330 FR-1, FR-2).

    Delegates the row + marker + BACKLOG.md index-cell writes to
    :func:`devbench.backlog.proposal.add_dep`, which already validates
    self-wire, blocker existence, blocker terminal status, and a readable
    ``## Dependencies`` section on the blocked file -- raising
    :class:`~devbench.backlog.proposal.ProposalError` (or propagating an
    ``OSError`` / ``UnicodeDecodeError`` from a malformed file) before any
    write happens, so a rejected call leaves no partial write. ``add_dep``
    writes the shared placeholder row (:func:`~devbench.backlog.proposal._placeholder_dep_row`),
    which is correct for its other caller (``promote-proposal``, wiring a
    just-materialised draft with no better text available yet); this then
    rewrites that placeholder to the blocker's real title and current status
    (AC-2), already resolved by the caller from the parsed backlog index,
    under its own ``flock_backlog`` acquisition (FR-1) so the rewrite cannot
    lose a concurrent flocked update to the same file.

    Returns ``True`` iff, after this call, the blocked file's
    ``## Dependencies`` table carries a row for the blocker -- true whether
    the row was newly written this call or already present from a prior
    call, so idempotent repeats stay validator-visible / ``wired: true``.
    """
    add_dep(
        backlog_root=backlog_root,
        backlog_index=backlog_index,
        blocked_task_id=blocked_task_id,
        blocker_task_id=blocker_task_id,
        reason=reason,
    )
    _canonicalize_add_dep_row(
        blocked_file,
        blocker_task_id,
        blocker_unit.title,
        _add_dep_raw_status_text(blocker_unit.status),
        workspace_root=backlog_index.parent,
    )
    return _dep_row_has_task(blocked_file, blocker_task_id)


def _add_dep_raw_status_text(status: WorkUnitStatus) -> str:
    """Return the lowercase-hyphenated markdown form of a ``WorkUnitStatus``.

    E.g. ``WorkUnitStatus.IN_QUEUE`` (``value == "In Queue"``) becomes
    ``"in-queue"``, matching the ``STATUS_*`` string constants
    (:mod:`devbench.constants`) used in ``## Status:`` lines and
    Dependencies-table rows across the backlog -- distinct from
    ``WorkUnitStatus.value``'s title-case display form used in BACKLOG.md's
    Status Summary and in CLI warning text.
    """
    return status.value.lower().replace(" ", "-")


def _canonicalize_add_dep_row(
    blocked_file: Path, blocker_task_id: str, title: str, status: str, *, workspace_root: Path
) -> None:
    """Upgrade the placeholder Dependencies row ``add_dep()`` writes to real title/status (#330 AC-2).

    :func:`devbench.backlog.proposal.add_dep` writes the shared placeholder
    row text (:func:`devbench.backlog.proposal._placeholder_dep_row`) via its
    ``_append_dependency_to_source`` helper -- correct for its other caller,
    ``promote-proposal``, where the blocker is a freshly materialised draft
    and no better text exists yet. ``add-dep``'s caller already knows the
    blocker's real, current title and status from the parsed backlog index,
    so this rewrites the placeholder cells to that real text. Matching the
    placeholder text against the SAME helper ``add_dep()`` used to write it
    (rather than a second, independently hardcoded copy) means the two can
    never drift apart and silently defeat the match.

    Idempotent: only replaces the exact placeholder row text for
    ``blocker_task_id``. A row already carrying real text -- from a prior
    corrected call, or authored directly -- has no placeholder to match and
    is left untouched, so repeat calls never re-write it.

    Runs the read-modify-write under ``flock_backlog(workspace_root)`` (#330
    FR-1, DoD): ``add_dep()`` releases the backlog flock before returning, so
    without its own lock this full-file rewrite could race a concurrent
    flocked write to the same file and lose it. Acquiring the lock here
    guarantees the content read at the start of this call is read fresh
    under the same lock the write is performed under.
    """
    with flock_backlog(workspace_root):
        content = blocked_file.read_text(encoding="utf-8")
        placeholder = _placeholder_dep_row(blocker_task_id)
        if placeholder not in content:
            return
        real_row = f"| {blocker_task_id} | {title} | {status} |"
        atomic_write_text(blocked_file, content.replace(placeholder, real_row, 1))


def _parse_add_dep_argv(argv: tuple[str, ...]) -> tuple[str | None, str, str]:
    """Parse the add-dep flag grammar.

    Returns ``(blocked_id, blocker_id, reason)``. Returns ``(None, "", "")``
    after printing a usage error to stderr so the caller can ``return 1``.
    """
    task_id_re = re.compile(r"^E\d+-F\d+-S\d+-T\d+$")
    positional: list[str] = []
    reason = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if not arg:
            i += 1
            continue
        if arg == "--reason":
            if i + 1 >= len(argv) or not argv[i + 1]:
                print("ERROR: --reason requires a value", file=sys.stderr)
                return None, "", ""
            reason = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            print(f"ERROR: unknown flag: {arg}", file=sys.stderr)
            return None, "", ""
        positional.append(arg)
        i += 1

    if len(positional) != 2:
        print(
            "ERROR: add-dep requires exactly two task ids: <blocked-task-id> <blocker-task-id>",
            file=sys.stderr,
        )
        return None, "", ""
    blocked_id, blocker_id = positional
    for label, tid in (("blocked", blocked_id), ("blocker", blocker_id)):
        if not task_id_re.match(tid):
            print(
                f"ERROR: add-dep: {label} task id '{tid}' does not match E<N>-F<N>-S<N>-T<N> format",
                file=sys.stderr,
            )
            return None, "", ""
    return blocked_id, blocker_id, reason


def cmd_reject_proposal(*argv: str) -> int:
    """Archive a proposed task's draft or a whole un-materialised proposal JSON.

    Usage::

        reject-proposal <task-id> --reason "<message>"
        reject-proposal --unmaterialised <source-task-id> --reason "<message>"

    See ``_parse_reject_proposal_argv`` for the full form / flag contract.
    """
    try:
        task_id, unmaterialised_source_id, reason = _parse_reject_proposal_argv(argv)
    except _RejectProposalArgError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rc = _reject_em_dash("reason", reason)
    if rc is not None:
        return rc

    try:
        archive = reject_proposal(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            task_id=task_id,
            unmaterialised_source_id=unmaterialised_source_id,
            reason=reason,
        )
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _print_reject_proposal_outcome(task_id, unmaterialised_source_id, archive)
    return 0


class _RejectProposalArgError(ValueError):
    """Raised by ``_parse_reject_proposal_argv`` on invalid / missing / conflicting flags."""


def _parse_reject_proposal_argv(argv: tuple[str, ...]) -> tuple[str, str, str]:
    """Parse the reject-proposal flag grammar.

    Returns ``(task_id, unmaterialised_source_id, reason)``. Exactly one of
    the first two is non-empty; ``reason`` is always non-empty. Raises
    ``_RejectProposalArgError`` on:

    - unknown flags
    - ``--reason`` or ``--unmaterialised`` without a value
    - both ``<task-id>`` and ``--unmaterialised`` supplied
    - neither supplied
    - missing ``--reason``
    """
    task_id = ""
    unmaterialised_source_id = ""
    reason = ""
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--reason":
            if i + 1 >= len(args) or not args[i + 1]:
                raise _RejectProposalArgError("--reason requires a value")
            reason = args[i + 1]
            i += 2
            continue
        if arg == "--unmaterialised":
            if i + 1 >= len(args) or not args[i + 1] or args[i + 1].startswith("--"):
                raise _RejectProposalArgError("--unmaterialised requires a source-task-id")
            unmaterialised_source_id = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            raise _RejectProposalArgError(f"unknown flag: {arg}")
        if not task_id:
            task_id = arg
        i += 1

    if task_id and unmaterialised_source_id:
        raise _RejectProposalArgError(
            "reject-proposal: supply exactly one of <task-id> or --unmaterialised <source-id>, not both"
        )
    if not task_id and not unmaterialised_source_id:
        raise _RejectProposalArgError("reject-proposal requires either <task-id> or --unmaterialised <source-id>")
    if not reason:
        raise _RejectProposalArgError("reject-proposal requires --reason <message>")
    return task_id, unmaterialised_source_id, reason


def _print_reject_proposal_outcome(task_id: str, unmaterialised_source_id: str, archive: Path | None) -> None:
    """Print the reject-proposal JSON result + log INFO line."""
    if unmaterialised_source_id:
        logger.info("Rejected un-materialised proposal %s", unmaterialised_source_id)
        payload: dict[str, str | None] = {
            "source_task_id": unmaterialised_source_id,
            "status": "rejected-unmaterialised",
            "archive": str(archive) if archive else None,
        }
    else:
        logger.info("Rejected proposal %s", task_id)
        payload = {
            "task_id": task_id,
            "status": "rejected",
            "archive": str(archive) if archive else None,
        }
    print(json.dumps(payload))


def _find_unit(units: list[WorkUnit], unit_id: str) -> WorkUnit | None:
    """Find a work unit by ID (case-insensitive)."""
    for unit in units:
        if unit.id.lower() == unit_id.lower():
            return unit
    return None


def _resolve_unit_file(unit: WorkUnit) -> Path | None:
    """Return the absolute path to the work unit file, or None if not found.

    Tries BACKLOG_ROOT / unit.file_path first, then WORKSPACE_ROOT / unit.file_path.

    Args:
        unit: WorkUnit whose file_path must be resolved.

    Returns:
        The resolved :class:`pathlib.Path` if the file exists, ``None`` otherwise.
    """
    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if wu_file.exists():
        return wu_file
    wu_file = WORKSPACE_ROOT / unit.file_path
    return wu_file if wu_file.exists() else None


# Command registry: name -> (handler, min_args, description)
_COMMANDS: dict[str, tuple[Callable[..., int], int, str]] = {
    "status": (cmd_status, 0, "Show backlog summary"),
    "next": (cmd_next, 0, "Print next actionable work unit"),
    "claim": (cmd_claim, 1, "Claim a work unit: claim <id>"),
    "set-status": (
        cmd_set_status,
        0,
        (
            "Set status: set-status <id> <status>  OR  "
            "set-status --include '<tokens>' [--exclude '<tokens>'] [--dry-run] [--yes] <status> "
            "(bulk update via printer-pages scope selectors, spec 4.7.1; "
            "--dry-run prints affected WUs without writing; "
            "--yes skips prompt when matched count > bulk_update_confirm_threshold)"
        ),
    ),
    "mark-done": (cmd_mark_done, 1, "Mark done: mark-done <id>"),
    "decline": (
        cmd_decline,
        2,
        "Mark a work unit Declined (won't ever be done) with a reason: decline <id> --reason <message> "
        "[--citation <commit-hash-or-task-id>] (--citation required when reason names already-satisfied)",
    ),
    "hold": (
        cmd_hold,
        2,
        (
            "Mark a work unit Hold (deferred / under debate) with a reason: "
            "hold <id> --reason <message>. Orchestrator skips held units."
        ),
    ),
    "unhold": (
        cmd_unhold,
        2,
        (
            "Return a held work unit to in-queue with a reason: "
            "unhold <id> --reason <message>. Refuses units not currently on hold."
        ),
    ),
    "remove": (
        cmd_remove,
        2,
        (
            "Remove a work unit through the managed path: remove <id> --reason <message> "
            "(deletes the WU file + BACKLOG.md index row under flock, re-rolls the Status "
            "Summary, and audits [WU_REMOVED]; db-303)"
        ),
    ),
    "promote": (
        cmd_promote,
        0,
        (
            "Promote draft work unit(s) to in-queue: promote <id>  OR  "
            "promote --epic|--feature|--story <scope-id>  (bulk, atomic transaction)"
        ),
    ),
    "sync-blocked": (
        cmd_sync_blocked,
        0,
        (
            "Reconcile every task's status against current dep satisfaction: "
            "in-queue with unsatisfied deps -> blocked; blocked with all deps "
            "now terminal -> in-queue (skipping units with open "
            "[BLOCKED_PENDING_PROPOSAL] markers, which the ADR-07 cascade owns)."
        ),
    ),
    "reconcile-cascade": (
        cmd_reconcile_cascade,
        0,
        (
            "Walk every blocked task; flip eligible ones (markers all terminal "
            "AND regular deps satisfied) to in-queue with a [CASCADE_RECONCILED] "
            "audit. Also repairs already-stranded Story/Feature/Epic containers "
            "(#332 FR-2), promoting any whose children are all terminal and "
            "cascading upward. Returns JSON envelope of flips + skips + rolled_up."
        ),
    ),
    "new-task": (
        cmd_new_task,
        0,
        (
            "Scaffold a new work-unit .md file from the canonical template: "
            'new-task --id <ID> --title "<TITLE>" --target <PATH> '
            "[--repo <ORG/REPO>] [--description <TEXT>] [--source-file <PATH>] "
            "[--test-file <PATH>] [--ac-func <TEXT>]. Template kind is "
            "derived from the ID's last segment (T/S/F/E)."
        ),
    ),
    "validate-backlog": (
        cmd_validate_backlog,
        0,
        "Validate backlog integrity [--fix: auto-correct rule-10/11] "
        "[--strict: also flag draft/hold Manifest conflicts]",
    ),
    "check": (
        cmd_check,
        0,
        (
            "Pre-flight: verify symlinks, origin remotes, default_branch "
            "parity, and no conflicting open PRs across all target repos "
            "in devbench.yaml"
        ),
    ),
    "ensure-branch": (cmd_ensure_branch, 1, "Create or switch to work unit branch: ensure-branch <id>"),
    "git-ops": (cmd_git_ops, 1, "Run git operations for a work unit: git-ops <id>"),
    "git-ops-finalize": (
        cmd_git_ops_finalize,
        1,
        "Push single branch and create PR: git-ops-finalize <repo> [--provenance <path>]",
    ),
    "check-merge": (
        cmd_check_merge,
        1,
        (
            "Reconcile a pause-before-merge work unit's PR state (issue #101). "
            "Promotes to done on merged, blocks on closed-without-merge, "
            "no-ops on still-open: check-merge <id>"
        ),
    ),
    "check-ancestry": (
        cmd_check_ancestry,
        2,
        (
            "Canonical git-ancestry check for a declared work-group dependency. "
            "Runs 'git merge-base --is-ancestor <dependency-ref> <target-ref>' in the work "
            "unit's repo (target-ref defaults to origin/<default-branch>). Exit 0 only when "
            "the dependency is merged: check-ancestry <id> <dependency-ref> [<target-ref>]"
        ),
    ),
    "cleanup-tracked-orphans": (
        cmd_cleanup_tracked_orphans,
        1,
        (
            "Untrack build/state artifacts (terraform state, pycache, "
            "coverage, etc.) and write managed .gitignore: "
            "cleanup-tracked-orphans <org/repo|path> [--dry-run]"
        ),
    ),
    "log": (cmd_log, 1, "Log a message: log <message>"),
    "report": (
        cmd_report,
        0,
        ("Progress report; streams on TTY by default: report [--once|--no-stream] [--since <ISO-8601>] [--watch N]"),
    ),
    "write-snapshot": (
        cmd_write_snapshot,
        0,
        "Issue #162 Phase 6: render the report once and persist it to .devbench/report-snapshot.json",
    ),
    "rebuild-window-stats": (
        cmd_rebuild_window_stats,
        0,
        (
            "Issue #162 Phase 2: rebuild every .devbench/window-stats/"
            "<task>.json aggregate from the orchestrator log (idempotent)"
        ),
    ),
    "archive-session": (
        cmd_archive_session,
        1,
        (
            "Issue #162 Phase 7: archive a session's JSONL log to "
            "logs/legacy/<session>.parquet (requires `pip install devbench[archive]`)"
        ),
    ),
    "drain": (
        cmd_drain,
        0,
        (
            "Graceful orchestrator stop request (spec 4.3.2, issue #188): "
            "drain [--reason '<text>'] | drain --cancel | drain --status"
        ),
    ),
    "cost-calibrate": (
        cmd_cost_calibrate,
        0,
        (
            "Calibrate per-model correction factors against an actual Anthropic invoice (issue #223): "
            "cost-calibrate <actual-usd> [--window <ISO-8601>]"
        ),
    ),
    "sessions": (
        cmd_sessions,
        0,
        ("List active sessions or remove stale ones (spec 4.4.5, issue #192): sessions  |  sessions --cleanup"),
    ),
    "stop": (
        cmd_stop,
        0,
        ("Send SIGTERM to a named session (spec 4.4.5, issue #192): stop --session <name>"),
    ),
    "start": (
        cmd_start,
        0,
        (
            "Run orchestrate skill via Agent SDK (non-interactive).\n"
            "\n"
            "Usage: devbench start [--daemon] [--name <session>] "
            '[--include "<tokens>"] [--exclude "<tokens>"] [--allow-overlap]\n'
            "\n"
            "  --daemon, -d        Detach to the background and return immediately (#209).\n"
            "  --name <session>    Named session to run under. Default: 'default'.\n"
            '  --include "<t>"     Restrict the run to matching work units. Comma-separated\n'
            "                      printer-pages tokens: IDs (E1-F2-S3-T4), last-segment\n"
            "                      ranges (E2-F1-S1-T3-T7), and epic/feature/story\n"
            "                      shorthands (E1, E2-F1, E2-F1-S1). Absent or empty means\n"
            "                      every work unit is eligible.\n"
            '  --exclude "<t>"     Subtract matching work units from the include set.\n'
            "                      Applied after include expansion; only meaningful with\n"
            "                      --include.\n"
            "  --allow-overlap     Permit this scope to overlap another live session's\n"
            "                      scope. Refused by default so two sessions cannot claim\n"
            "                      the same work unit."
        ),
    ),
    "quota-watcher": (
        cmd_quota_watcher,
        0,
        "Print the current quota-pause checkpoint, if any: quota-watcher (spec FR-2.11, Section 14; no flags)",
    ),
    "instances": (cmd_instances, 0, "List every live devbench orchestrator on this host (#209). Flag: --json"),
    "stop-instance": (
        cmd_stop_instance,
        0,
        (
            "Stop an orchestrator instance by id or PID (SIGTERM, then SIGKILL if --force) (#209): "
            "stop-instance <id> [--timeout N] [--force]"
        ),
    ),
    "tail": (
        cmd_tail,
        0,
        "Tail an orchestrator instance's log by id (#209): tail <id> [--follow|-f] [--lines|-n N]",
    ),
    "restart": (
        cmd_restart,
        0,
        "Restart an orchestrator instance (stop + start in same mode) by id (#209): restart <id>",
    ),
    "scope": (
        cmd_scope,
        0,
        (
            "Persistent scope management: scope set --include '<tokens>' "
            "[--exclude '<tokens>']  |  scope clear  |  scope show"
        ),
    ),
    "prepare-plugin-shadow": (
        cmd_prepare_plugin_shadow,
        0,
        (
            "Materialise the per-agent shadow plugin (ADR-25) and print its path; "
            'use for interactive launchers (claude --plugin-dir "$(devbench prepare-plugin-shadow)")'
        ),
    ),
    "watch": (
        cmd_watch,
        0,
        "Dashboard view of the currently-active orchestration: watch [--watch N]",
    ),
    "hook-tail": (
        cmd_hook_tail,
        0,
        "Pretty-tail $WORKSPACE_ROOT/hook-logs.jsonl: hook-tail [<path>] [--tz <zone>] [--no-follow] [--from-start]",
    ),
    "watchdog": (
        cmd_watchdog,
        0,
        (
            "Detect stuck orchestrate loops and write a restart marker: "
            "watchdog [--idle-minutes N] [--flag-file PATH] [--log-file PATH] [--print-if-stuck]"
        ),
    ),
    "notify-test": (
        cmd_notify_test,
        0,
        "Send one sample Slack notification for an event (smoke-test setup): notify-test --event <name>",
    ),
    # Plugin agent bridge commands -- used by devbench plugin agents
    "read-unit": (cmd_read_unit, 1, "Work unit content + repo path as JSON: read-unit [--strip-comments] <id>"),
    "get-diff": (cmd_get_diff, 1, "Return combined git diff for work unit's repo: get-diff <id>"),
    "check-manifest-scope": (
        cmd_check_manifest_scope,
        1,
        "Print out-of-Manifest staged paths and exit non-zero on mismatch (read-only, "
        "deterministic; spec 4.C): check-manifest-scope <id>",
    ),
    "gates": (
        cmd_gates,
        0,
        "Show every gate's tier, status and repo overrides",
    ),
    "check-reachability": (
        cmd_check_reachability,
        1,
        (
            "Word-boundary, source-classified reachability gate over the unit's Changes "
            "Manifest scope: check-reachability <id>. Blocks (exit 1) when a classified "
            "candidate has no non-test importer or can't be read."
        ),
    ),
    "run-tests": (cmd_run_tests, 1, "Run test suite for work unit's repo: run-tests <id>"),
    "check-shared-file-impact": (
        cmd_check_shared_file_impact,
        1,
        (
            "Full-suite regression gate for shared/high-fan-in files: "
            "check-shared-file-impact <id>. No-op unless the diff touches a "
            "gates.repos.<repo>.shared_file_impact.patterns match; blocks (exit 1) on new "
            "failures vs. the stored baseline."
        ),
    ),
    "check-fixture-consistency": (
        cmd_check_fixture_consistency,
        1,
        (
            "Cross-reference mock/fixture files against the configured canonical dataset "
            "(no-op unless gates.fixture_consistency.canonical_sources is set): "
            "check-fixture-consistency <id>"
        ),
    ),
    "log-waiver": (
        cmd_log_waiver,
        2,
        "Record a structured gate waiver: log-waiver <judge> <id> --gate <g> --target <t> --reason <r> [--operator]",
    ),
    "log-newly-reachable": (
        cmd_log_newly_reachable,
        1,
        "Record a newly-reachable-path verification: log-newly-reachable <id> --path <p> --method <m> --result <r>",
    ),
    "tdd-gate": (
        cmd_tdd_gate,
        1,
        "Run the machine-observed RED gate for a gated task and record RED_OBSERVED on success: tdd-gate <id>",
    ),
    "green-green-check": (
        cmd_green_green_check,
        2,
        "Run the refactor green-green check, named tests pass before and after the change: "
        "green-green-check <id> <test_node_id> [<test_node_id> ...]",
    ),
    "log-verdict": (
        cmd_log_verdict,
        3,
        "Log judge verdict: log-verdict <judge> <id> <pass|fail> [feedback] "
        "(feedback: single-line, no control chars, no bracketed phase tags)",
    ),
    "log-comment": (
        cmd_log_comment,
        3,
        "Log agent comment: log-comment <agent> <id> <message> "
        "(message: single-line, no control chars, no bracketed phase tags)",
    ),
    "log-tdd": (
        cmd_log_tdd,
        3,
        "Log TDD phase: log-tdd <id> <RED|GREEN|REFACTOR> <message> (RED_OBSERVED is orchestrator-only; "
        "rejected here; message: single-line, no control chars, no bracketed phase tags)",
    ),
    "request-amendment": (
        cmd_request_amendment,
        1,
        "Register an amendment request (JSON on stdin): request-amendment <id>",
    ),
    "apply-amendment": (
        cmd_apply_amendment,
        1,
        "Apply an approved amendment with Layer 3 post-check: apply-amendment <id>",
    ),
    "reject-amendment": (
        cmd_reject_amendment,
        2,
        "Reject amendment and block the task: reject-amendment <id> <reason>",
    ),
    "log-rejection-feedback": (
        cmd_log_rejection_feedback,
        3,
        "Persist review-judge rejection JSON: log-rejection-feedback <judge> <id> --json '<payload>'",
    ),
    "config-resolve": (
        cmd_config_resolve,
        1,
        "Print resolved config values as JSON: config-resolve <field> [<field>...]",
    ),
    "list-proposals": (
        cmd_list_proposals,
        0,
        "List every pending task-factory proposal: list-proposals",
    ),
    "promote-proposal": (
        cmd_promote_proposal,
        1,
        "Promote a proposed task to in-queue: promote-proposal [--no-dep-on-source] <id> | --all-from <src>",
    ),
    "reject-proposal": (
        cmd_reject_proposal,
        2,
        "Archive a proposed task's draft: reject-proposal <id> --reason <message>",
    ),
    "add-dep": (
        cmd_add_dep,
        2,
        "Wire a cross-task BLOCKED_PENDING_PROPOSAL marker: add-dep <blocked-id> <blocker-id> [--reason <msg>]",
    ),
    "materialise-proposal": (
        cmd_materialise_proposal,
        1,
        "Materialise a pending proposal into draft files: materialise-proposal <source-task-id>",
    ),
    "sweep-proposals": (
        cmd_sweep_proposals,
        0,
        "Best-effort materialise every pending proposal JSON (fires at orchestrate loop start)",
    ),
    "write-proposal": (
        cmd_write_proposal,
        1,
        "Persist a blocker-resolver proposal JSON (stdin): write-proposal <source-task-id>",
    ),
}


_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})

# Commands that parse their own flag grammar and need the full trailing-arg
# list instead of the dispatcher's fixed-arity slice. Additions here are
# deliberate -- the slice is a guardrail against typos for fixed-arity
# commands, so variadic opt-in should be narrow.
#
# add-dep is variadic because its `--reason "<multi token message>"` flag
# value is dropped by the fixed-arity slice (slice keeps only positional
# count + 1 trailing arg, so `--reason` survives but the value after it
# does not). _parse_add_dep_argv handles flags itself; opting into
# variadic dispatch lets the value through.
_VARIADIC_COMMANDS: frozenset[str] = frozenset(
    {
        "cost-calibrate",
        "hook-tail",
        "watchdog",
        "notify-test",
        "add-dep",
        "decline",
        # FR-4.6 (E4-F4-S1-T2): variadic trailing test node ids.
        "green-green-check",
        "hold",
        "unhold",
        # db-303 (E12-F1-S2-T1): --reason <message> is multi-token like hold/unhold/decline.
        "remove",
        "status",
        "new-task",
        "reject-proposal",
        "validate-backlog",
        "log-rejection-feedback",
        # Issue #122: variadic list of RuntimeConfig field names to resolve.
        "config-resolve",
        # Issue #162 Phase 6: pre-render the report into a snapshot file.
        "write-snapshot",
        # Issue #162 Phase 2: rebuild per-task window-stats aggregates from the log.
        "rebuild-window-stats",
        # Issue #162 Phase 7: archive an ended session's log to Parquet (opt-in dep).
        "archive-session",
        # Issue #194 E7-F1-S1-T1: --include / --exclude scope selectors for bulk status update
        "set-status",
        # Issue #189 E1-F4-S1-T2: bulk selectors --epic/--feature/--story
        "promote",
        # Issue #190 E2-F2-S1-T1: --include / --exclude scope selectors
        "start",
        # Issue #209: lifecycle CLI -- flag-bearing subcommands need raw argv.
        # "stop" is already registered above for #192 (--session targeting);
        # #209's "stop-instance" handles instance-id / PID targeting.
        "instances",
        "stop-instance",
        "tail",
        "restart",
        # Issue #190 E2-F2-S2-T3: cmd_next respects scope filter
        "next",
        # Issue #196 E2-F7: scope set / clear / show subcommand
        "scope",
        # Issue #188 E3-F2: drain --reason / --cancel / --status flags
        "drain",
        # Issue #192 E4-F5-S1-T1: sessions --cleanup flag
        "sessions",
        # Issue #192 E4-F5-S1-T2: stop --session <name> flag
        "stop",
        # E2-F4-S1-T1: --gate/--target/--reason/--operator flags.
        "log-waiver",
        # E2-F4-S1-T2: --path/--method/--result flags.
        "log-newly-reachable",
        # E2-F9-S1-T1: --provenance <path> flag (spec 4.13; D-17).
        "git-ops-finalize",
    }
)


def _print_usage() -> None:
    """Print top-level usage and command list. Shared by the `-h`/`--help` path and the no-args path.

    A registry description may span several lines so that
    ``devbench <command> --help`` can document flags and usage in full. Only
    its first line is shown here, which keeps the command column aligned;
    the full text is printed by the per-command help path.
    """
    print("Usage: devbench <command> [args]")
    print("       devbench <command> --help    (per-command usage)")
    print("       devbench --help              (this message)")
    print("\nCommands:")
    for name, (_, _, desc) in sorted(_COMMANDS.items()):
        print(f"  {name:<20} {desc.splitlines()[0]}")


def _extract_watch_flag(raw_args: list[str]) -> tuple[int, list[str]]:
    """Return ``(interval, remaining_args)`` after stripping ``--watch N`` / ``-w N``."""
    filtered: list[str] = []
    interval = 0
    i = 0
    while i < len(raw_args):
        if raw_args[i] in ("--watch", "-w") and i + 1 < len(raw_args):
            interval = int(raw_args[i + 1])
            i += 2
            continue
        filtered.append(raw_args[i])
        i += 1
    return interval, filtered


def _extract_once_flag(raw_args: list[str]) -> tuple[bool, list[str]]:
    """Return ``(once, remaining_args)`` after stripping ``--once`` / ``--no-stream``.

    Issue #163: ``--once`` (or its alias ``--no-stream``) forces the legacy
    one-shot snapshot behaviour, suitable for CI / scripted consumers
    that expect the report to print and exit. The default on a TTY is
    streaming.
    """
    filtered: list[str] = []
    once = False
    for arg in raw_args:
        if arg in ("--once", "--no-stream"):
            once = True
            continue
        filtered.append(arg)
    return once, filtered


def _extract_by_role_flag(raw_args: list[str]) -> tuple[bool, list[str]]:
    """Return ``(by_role, remaining_args)`` after stripping ``--by-role``.

    Issue #206: opt-in per-role token/cost breakdown panel rendered
    beneath the existing aggregate Cost section.  Default OFF
    (omitting the flag preserves the pre-#206 output verbatim).
    """
    filtered: list[str] = []
    by_role = False
    for arg in raw_args:
        if arg == "--by-role":
            by_role = True
            continue
        filtered.append(arg)
    return by_role, filtered


def _extract_scope_flags_for_report(
    raw_args: list[str],
) -> tuple[str, str, str, list[str]]:
    """Strip ``--include``, ``--exclude``, and ``--session`` from report args.

    Returns ``(include, exclude, session, remaining_args)`` after consuming the
    ``--include <tokens>``, ``--exclude <tokens>``, and ``--session <name>``
    flag pairs.  Any positional argument that is not a recognised flag (i.e.
    the ``since`` timestamp) is preserved in ``remaining_args``.

    Spec sections 4.2.2 and 4.4.6 (AC-190-10, AC-190-11, AC-192-12):
    ``cmd_report`` accepts ``--include`` / ``--exclude`` as one-off scope
    overrides and ``--session <name>`` to filter to a named session.

    Args:
        raw_args: Argument list after ``--watch`` and ``--once`` have
            already been stripped.

    Returns:
        A four-tuple ``(include, exclude, session, remaining_args)`` where
        ``include``, ``exclude``, and ``session`` are raw token strings (empty
        when not supplied) and ``remaining_args`` contains every arg not
        consumed by the flags.
    """
    include = ""
    exclude = ""
    session = ""
    filtered: list[str] = []
    i = 0
    while i < len(raw_args):
        arg = raw_args[i]
        if arg in ("--include", "--exclude", "--session") and i + 1 < len(raw_args):
            next_val = raw_args[i + 1]
            if next_val.startswith("--"):
                # Next token is another flag -- do not consume it as a value.
                filtered.append(arg)
                i += 1
                continue
            if arg == "--include":
                include = next_val
            elif arg == "--exclude":
                exclude = next_val
            else:
                session = next_val
            i += 2
            continue
        filtered.append(arg)
        i += 1
    return include, exclude, session, filtered


def _dispatch_watch_commands(
    command: str,
    watch_interval: int,
    args: list[str],
    once: bool = False,
    include: str = "",
    exclude: str = "",
    session: str = "",
    by_role: bool = False,
) -> int | None:
    """Dispatch the ``report`` and ``watch`` commands. Return ``None`` if not handled.

    The ``report`` command is always dispatched here (not via the generic
    ``func(*sliced_args)`` path) so that ``include`` / ``exclude`` scope flags
    and ``session`` are forwarded as keyword arguments to :func:`cmd_report`.

    Args:
        command: The devbench subcommand name.
        watch_interval: The ``--watch N`` interval extracted by
            :func:`_extract_watch_flag` (``0`` when not supplied).
        args: Remaining positional arguments after flag extraction.
        once: Whether ``--once`` / ``--no-stream`` was supplied.
        include: Raw ``--include`` token string forwarded to ``cmd_report``.
        exclude: Raw ``--exclude`` token string forwarded to ``cmd_report``.
        session: Named-session filter string forwarded to ``cmd_report``
            (spec section 4.4.6, AC-192-12).  Empty string means no filter.
        by_role: Issue #206; whether ``--by-role`` was supplied so the
            per-role token/cost panel is rendered beneath the aggregate
            Cost section.

    Returns:
        The command's integer exit code, or ``None`` when the command is
        not handled by this dispatcher.
    """
    if command == "report":
        since_arg = args[0] if args else ""
        return cmd_report(
            since=since_arg,
            watch_interval=watch_interval,
            once=once,
            include=include,
            exclude=exclude,
            session=session,
            by_role=by_role,
        )
    if command == "watch" and watch_interval > 0:
        return cmd_watch(watch_interval=watch_interval)
    return None


def main() -> int:
    """Parse arguments and dispatch to the appropriate command."""
    setup_logging()

    # Top-level: `devbench`, `devbench --help`, `devbench -h` all print usage and exit 0.
    # Only a typo'd command (e.g. `devbench foo`) returns 1.
    if len(sys.argv) < 2 or sys.argv[1] in _HELP_FLAGS:
        _print_usage()
        return 0

    command = sys.argv[1]
    if command not in _COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(_COMMANDS))}", file=sys.stderr)
        return 1

    # Per-command help: `devbench <cmd> --help` / `-h` prints the registry
    # description (single source of truth) and exits 0.
    if any(arg in _HELP_FLAGS for arg in sys.argv[2:]):
        _, _, desc = _COMMANDS[command]
        print(desc)
        return 0

    func, min_args, _ = _COMMANDS[command]

    if command in ("report", "watch"):
        watch_interval, args = _extract_watch_flag(sys.argv[2:])
    else:
        watch_interval, args = 0, list(sys.argv[2:])

    # Issue #163: ``devbench report`` accepts ``--once`` / ``--no-stream``
    # to force one-shot snapshot rendering even on a TTY. Strip the flag
    # before the args reach the command function so the positional
    # ``since`` slot stays well-defined.
    once = False
    by_role = False
    include = ""
    exclude = ""
    session = ""
    if command == "report":
        once, args = _extract_once_flag(args)
        # Issue #206: opt-in per-role breakdown.
        by_role, args = _extract_by_role_flag(args)
        # Issue #190 (AC-190-10, AC-190-11): strip ``--include`` / ``--exclude``
        # scope-filter flags before the remaining positional ``since`` arg is
        # resolved. The extracted strings are forwarded to ``cmd_report``.
        # AC-192-12: also strip ``--session <name>`` for per-session filtering.
        include, exclude, session, args = _extract_scope_flags_for_report(args)

    if len(args) < min_args:
        print(f"Command '{command}' requires at least {min_args} argument(s)", file=sys.stderr)
        return 1

    watch_rc = _dispatch_watch_commands(
        command,
        watch_interval,
        args,
        once=once,
        include=include,
        exclude=exclude,
        session=session,
        by_role=by_role,
    )
    if watch_rc is not None:
        return watch_rc

    # Variadic commands receive the full trailing-arg list -- they own their
    # own flag parsing. Fixed-arity commands get sliced to ``min_args + 1``
    # so a stray extra positional is reported instead of silently absorbed.
    if command in _VARIADIC_COMMANDS:
        sliced_args: list[str] = list(args)
    else:
        if len(args) > min_args + 1:
            print(f"Warning: ignoring {len(args) - min_args - 1} extra argument(s)", file=sys.stderr)
        sliced_args = list(args[: min_args + 1]) if len(args) > min_args else list(args[:min_args])
    return func(*sliced_args)


if __name__ == "__main__":
    sys.exit(main())
