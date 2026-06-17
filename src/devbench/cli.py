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
    validate-backlog [--fix] Check backlog integrity; --fix auto-corrects rule-10/11 violations
    ensure-branch <id>      Create or switch to work unit branch before executor runs
    git-ops <id>            Run git operations for a work unit (commit-only when defer_pr is set)
    git-ops-finalize <repo> Push single branch and create PR (after all deferred commits)
    report [since]          Print progress report with velocity stats
    log <message>           Append a message to the orchestrator log file
    start                   Run the orchestrate skill via the Claude Agent SDK (non-interactive)
    scope set/clear/show    Persistent scope management without starting the orchestrator
    watch [--watch N]       Show a live dashboard of the active orchestration

Plugin agent bridge commands (used by devbench plugin agents)::

    read-unit <id>                          Return work unit content and repo path as JSON
    get-diff <id>                           Return combined git diff for the work unit's repo
    run-tests <id>                          Run test suite for the work unit's repo
    log-verdict <judge> <id> <v> [msg]      Log a judge verdict (pass|fail) to work unit Comments
    log-comment <agent> <id> <message>      Log a non-verdict agent comment to work unit Comments
    log-tdd <id> <phase> <message>          Log a TDD phase entry (RED|GREEN|REFACTOR) to TDD Cycle Log
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
import functools
import getpass
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, NamedTuple

import pexpect

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

from devbench import verification
from devbench.actionability import check_actionability
from devbench.backlog.amendment import (
    REVIEW_FAILURES_DIR_NAME,
    AmendmentError,
    AmendmentRequest,
    apply_amendment,
    apply_operator_amendment,
    read_request,
    read_review_failure_files,
    reject_amendment,
    write_request,
)
from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.backlog.proposal import (
    ESCALATION_NO_PROPOSAL_MARKER,
    ESCALATION_PROPOSAL_WRITTEN_MARKER,
    BlockedTaskState,
    CascadeDepthError,
    Proposal,
    ProposalError,
    ProposalMatch,
    ProposalTaskState,
    _compute_fix_signature,
    _extract_intent_phrase,
    add_dep,
    allocate_next_ids,
    build_escalation_proposal,
    classify_blocked_task,
    classify_proposed_task,
    detect_placeholder_descriptions,
    enforce_cascade_depth,
    find_matching_pending_proposal,
    list_proposals,
    materialise_proposal,
    promote_all_from_source,
    promote_proposal,
    read_proposal,
    reject_proposal,
    remove_dep,
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
from devbench.config import (
    AGENT_MODELS,
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    BLOCKED_RECOVERY_WINDOW_SECONDS,
    CLAUDE_CREDENTIALS_FILE,
    MAX_CASCADE_DEPTH,
    ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS,
    REPO_LOCAL_PATHS,
    RUNTIME_CONFIG,
    UPDATE_SUBMODULE,
    USE_BEDROCK,
    WORKSPACE_ROOT,
    _read_env,
    resolve_repo,
    validate_repo,
)
from devbench.config_loader import (
    AUTO_FINALIZE_SKIPPED_LOCAL_ONLY,
    QuotaHandlingConfig,
    RepoConfig,
    get_configured_default_branch,
)
from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_STATUS_RE,
    BLOCKED_TARGET_REPO_UNRESOLVED_MARKER,
    CLAIM_BLOCKED_PRECLAIM,
    CLAIM_DEFERRED_SERIALIZED,
    CLAIM_NOT_CONVERGING_MARKER,
    CLAIM_TEARDOWN_MARKER,
    COMMENT_AGENT_TEMPLATE,
    COMMENT_ENTRY_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    DEFAULT_CLAIM_TEARDOWN_CLEANUP_HOOK,
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_SUBDIR,
    DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS,
    DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS,
    DEFAULT_MAX_NON_CONVERGING_CLAIMS,
    DEFAULT_MAX_PARALLEL_IN_PROGRESS,
    DEFAULT_MAX_QUOTA_RESUMES,
    DEFAULT_MAX_WITHIN_CLAIM_ATTEMPTS,
    DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS,
    DEFAULT_PLUGIN_SUBPATH,
    DEFAULT_PRESYNC_COMMAND,
    DEFAULT_PRESYNC_ENVIRONMENT,
    DEFAULT_PRESYNC_TIMEOUT_SECONDS,
    DEFAULT_WITHIN_CLAIM_CONVERGENCE_CHECK,
    DISPLAY_STATUS_VALUES,
    EM_DASH,
    FATAL_SDK_ERROR_CODES,
    FINALIZE_COMMIT_TEMPLATE,
    FINALIZE_PR_TITLE_TEMPLATE,
    KNOWN_JUDGE_NAMES,
    ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX,
    ORCHESTRATOR_FATAL_ERROR_AUDIT_PREFIX,
    ORCHESTRATOR_FATAL_ERROR_EXIT_CODE,
    ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX,
    ORCHESTRATOR_QUOTA_RESUME_AUDIT_PREFIX,
    ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX,
    ORCHESTRATOR_RESTART_EXIT_CODE,
    ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX,
    ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE,
    REQUEUED_AFTER_DEAD_SESSION_AUDIT_PREFIX,
    SESSION_DEFAULT_NAME,
    SESSION_DRAIN_SIGNAL_FILENAME,
    SESSION_PID_FILENAME,
    SESSION_SESSIONS_BASE_DIR,
    SESSION_STARTED_AT_FILENAME,
    SESSION_STARTED_BY_FILENAME,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_IN_REVIEW,
    STATUS_LINE_RE,
    STATUS_PROPOSED,
    STATUS_SEPARATOR_WIDTH,
    STATUS_SUMMARY_LABEL_WIDTH,
    SUPERVISE_BILLING_MODE_ENV_VAR,
    SUPERVISE_DEFAULT_BILLING_MODE,
    SUPERVISE_DEFAULT_NAME,
    SUPERVISE_EXIT_REASON_HARD_STOP,
    SUPERVISE_EXIT_REASON_STALE_RECONCILED,
    SUPERVISE_INTERNAL_RUN_SUBVERB,
    SUPERVISE_PROGRESS_STALL_SECONDS_ENV_VAR,
    SUPERVISE_SESSION_NAME_PATTERN,
    SUPERVISE_STATE_COMPLETED_CLEAN,
    SUPERVISE_STATE_FAULTED,
    SUPERVISE_STATE_QUOTA_WAITING,
    SUPERVISE_STATE_RUNNING,
    SUPERVISE_STATE_STOPPED,
    SUPERVISE_SUBVERBS,
    SUPERVISE_VALID_BILLING_MODES,
    TIMEOUT_RESULT_MARKERS,
    VALID_TDD_PHASES,
)
from devbench.drain import DrainState, _current_user, cancel_drain, consume_drain, read_drain_state, request_drain
from devbench.instances import is_pid_alive, pid_file_path, read_pid_file
from devbench.log_setup import setup_logging
from devbench.plugin_shadow import (
    materialise_shadow_plugin,
    shadow_plugin_path,
    write_pid_sentinel,
)
from devbench.quota import (
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
# the banner is implemented there and reporting must not depend on cli.py.
from devbench.reporting.report import _format_duration
from devbench.scope import (
    InvalidScopeError,
    ScopeFilter,
    _expand_prefix,
    _scope_file_path,
    _tokenise,
    session_scope_file_path,
)
from devbench.session import ClaimRaceError, Session, SessionRegistry, detect_scope_overlap, flock_backlog
from devbench.supervise import (
    AuthVerifier,
    DetectionPatterns,
    EnvSanitizer,
    EventLoopResult,
    LogTailDetector,
    PtyDriver,
    PtyLogWriter,
    SuperviseError,
    SuperviseReadyTimeoutError,
    SuperviseRegistry,
    SuperviseSessionState,
    _block_until_readable,
    build_claude_launch_argv,
    build_quota_waiter,
    build_resume_argv,
    follow_pty_log,
    format_status_line,
    new_session_state,
    normalize_resume_id_for_display,
    parse_screen_ls,
    read_stop_request,
    reconcile_info_rows,
    require_claude,
    resolve_supervise_effort,
    resolve_supervise_model,
    run_supervise_event_loop,
    run_supervised_kickoff,
    sanitize_resume_id,
    screen_session_name,
    supervise_pty_log_path,
    write_session_scope,
    write_stop_request,
)
from devbench.utils.io import atomic_write_text
from devbench.utils.process import run_command, run_command_in_process_group

__all__ = ["_format_duration"]

logger = logging.getLogger("devbench.cli")


@dataclass
class OrchestratorState:
    """Resolved orchestrator status for rendering in ``cmd_status`` (issue #252).

    Attributes:
        status: ``"running"`` or ``"stopped"``.
        mode: Orchestrator mode (``"daemon"`` or ``"foreground"``); ``None`` when stopped.
        pid: OS process ID; ``None`` when stopped.
        instance_id: Human-readable instance identifier; ``None`` when stopped.
        uptime: Formatted uptime string (``"HH:MM:SS"`` or ``"Dd HH:MM:SS"``); ``None`` when stopped.
        detail: Auxiliary status detail (e.g. ``"no pid file"``, ``"stale pid file"``);
            empty string when running.
    """

    status: str
    mode: str | None
    pid: int | None
    instance_id: str | None
    uptime: str | None
    detail: str


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

    Reads scope.json directly to surface ``started_at`` / ``started_by``
    metadata that :meth:`ScopeFilter.from_file` omits from the dataclass.
    The file is read and parsed once; ``None`` is returned only when the
    file is absent.

    Args:
        workspace_root: Path to the workspace root directory.

    Returns:
        The decoded JSON payload dict, or ``None`` if scope.json does not exist.

    Raises:
        json.JSONDecodeError: If the file exists but contains invalid JSON.
        KeyError: If required keys are missing from the JSON payload.
        TypeError: If field types in the payload are invalid.
    """
    scope_path = _scope_file_path(workspace_root)
    if not scope_path.exists():
        return None
    payload = json.loads(scope_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(
            f"scope.json top-level payload must be an object, "
            f"got {type(payload).__name__!r}. "
            f"The file at '{scope_path}' may be corrupt -- "
            f"remove it and re-run 'devbench start --include ...' to recreate it."
        )
    return payload


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


def _render_drain_banner(workspace_root: Path, file: IO[str] | None = None) -> None:
    """Print the ``DRAIN REQUESTED: at <ts> by <user> (reason: <text>)`` banner.

    Reads the drain signal file from *workspace_root* non-destructively via
    :func:`~devbench.drain.read_drain_state`. When no signal is present this
    function is a no-op. Output goes to *file* (default ``sys.stdout``)
    immediately before the Status Summary header (spec section 4.3.5, AC-188-7).

    Args:
        workspace_root: Workspace directory from which the drain signal path is
            resolved.
        file: Output stream to write the banner to. Defaults to ``sys.stdout``
            when ``None``. Callers may pass an ``io.StringIO`` instance to
            capture banner text without capturing the full process stdout.
    """
    state = read_drain_state(workspace_root)
    if state is None:
        return
    reason_part = state.reason if state.reason else "(none)"
    print(
        f"DRAIN REQUESTED: at {state.requested_at.isoformat()} by {state.requested_by} (reason: {reason_part})",
        file=file if file is not None else sys.stdout,
    )


def _format_uptime(started_at: str) -> str:
    """Format uptime from ISO-8601 UTC ``started_at`` to ``HH:MM:SS`` or ``Dd HH:MM:SS``.

    Returns ``"unknown"`` when *started_at* is empty or unparsable.

    Args:
        started_at: ISO-8601 UTC timestamp string (e.g. ``"2026-06-06T12:00:00Z"``).

    Returns:
        Uptime as ``"HH:MM:SS"`` for under 24 hours, ``"Dd HH:MM:SS"`` for 24h or more,
        or ``"unknown"`` when *started_at* is empty or cannot be parsed.
    """
    if not started_at:
        return "unknown"
    try:
        started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return "unknown"
    total_seconds = max(0, int((datetime.now(tz=UTC) - started).total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours >= 24:
        days, rem_hours = divmod(hours, 24)
        return f"{days}d {rem_hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _resolve_orchestrator_state(workspace: Path) -> OrchestratorState:
    """Resolve orchestrator status from the canonical PID file (issue #252, AC-252-1).

    Reads ``<workspace>/.devbench/orchestrator.pid`` via :func:`~devbench.instances.pid_file_path`
    and :func:`~devbench.instances.read_pid_file`, then checks liveness with
    :func:`~devbench.instances.is_pid_alive`.  Never calls ``discover_instances``
    (AC-252a-1).

    Args:
        workspace: Path to the workspace root directory.

    Returns:
        An :class:`OrchestratorState` with ``status="running"``
        when the PID file exists and the process is alive, ``"stopped"`` with
        ``detail="stale pid file"`` when the PID file exists but the process is dead,
        or ``"stopped"`` with ``detail="no pid file"`` when the file is absent.
    """
    pid_path = pid_file_path(workspace)
    inst = read_pid_file(pid_path)
    if inst is None:
        return OrchestratorState(
            status="stopped",
            mode=None,
            pid=None,
            instance_id=None,
            uptime=None,
            detail="no pid file",
        )
    if not is_pid_alive(inst.pid):
        return OrchestratorState(
            status="stopped",
            mode=None,
            pid=None,
            instance_id=None,
            uptime=None,
            detail="stale pid file",
        )
    return OrchestratorState(
        status="running",
        mode=inst.mode,
        pid=inst.pid,
        instance_id=inst.instance_id,
        uptime=_format_uptime(inst.started_at),
        detail="",
    )


def _render_orchestrator_state(state: OrchestratorState) -> None:
    """Print the orchestrator status line based on *state* (spec AC-252-1).

    Renders exactly one of:

    - ``Orchestrator: running (<mode>)  pid <N>  instance <id>  uptime <HH:MM:SS>``
    - ``Orchestrator: stopped (stale pid file)``
    - ``Orchestrator: stopped (no pid file)``

    Double-space separators between the running-line fields are required by spec
    section 2 G5.

    Args:
        state: The resolved :class:`OrchestratorState`.
    """
    if state.status == "running":
        print(
            f"Orchestrator: running ({state.mode})"
            f"  pid {state.pid}"
            f"  instance {state.instance_id}"
            f"  uptime {state.uptime}"
        )
    else:
        print(f"Orchestrator: stopped ({state.detail})")


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

    Args:
        parser: The :class:`BacklogParser` instance.
        units: Full list of parsed work units.
        active: Work units currently IN_PROGRESS or IN_REVIEW.
    """
    active_ids = {u.id for u in active}
    all_candidates, all_done_flag, blocked_count = check_actionability(parser, units)
    actionable = [u for u in all_candidates if u.id not in active_ids]
    if actionable:
        print(f"\nNext actionable: {actionable[0].id} -- {actionable[0].title}")
    elif all_done_flag:
        print("\nAll work units are DONE.")
    else:
        print(f"\nNo actionable units. {blocked_count} blocked.")


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
    _render_orchestrator_state(_resolve_orchestrator_state(WORKSPACE_ROOT))

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    all_units = parser.parse_index()

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
    # Issue #148: walk the markers and surface only non-terminal targets.
    markers = [
        marker
        for marker in _BLOCKED_PENDING_PROPOSAL_MARKER_RE.findall(content)
        if (target := units_by_id.get(marker)) is None
        or target.status not in {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    ]
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

# Issue #158: regex matching the structured-log line:
#   2026-05-02T12:34:56Z [logger] LEVEL ... Set <id> to 'in-progress'
_LOG_PROGRESS_RE: re.Pattern[str] = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (\S+) to 'in-progress'",
    re.MULTILINE,
)
# Fallback: agent-comment audit row of the form
#   [2026-05-02 12:34 UTC] [agent/orchestrator] Set <id> to 'in-progress'
_AUDIT_PROGRESS_RE: re.Pattern[str] = re.compile(
    r"\[(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+UTC\][^\n]*?Set\s+(?P<id>\S+)\s+to\s+'in-progress'",
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
            ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
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


_DEP_TERMINAL_STATUSES_FOR_STALL: frozenset[WorkUnitStatus] = frozenset({WorkUnitStatus.DONE, WorkUnitStatus.DECLINED})


def _unmet_dep_ids(task: WorkUnit, units_by_id: dict[str, WorkUnit]) -> list[str]:
    """Return dependency IDs of ``task`` that are not in a terminal status."""
    return [
        dep_id
        for dep_id in task.dependencies
        if dep_id in units_by_id and units_by_id[dep_id].status not in _DEP_TERMINAL_STATUSES_FOR_STALL
    ]


def _held_blocking_ids(in_queue_tasks: list[WorkUnit], units_by_id: dict[str, WorkUnit]) -> list[str]:
    """Return IDs of HOLD-status units that block in-queue tasks (deduped, ordered)."""
    seen: set[str] = set()
    result: list[str] = []
    for task in in_queue_tasks:
        for dep_id in _unmet_dep_ids(task, units_by_id):
            dep = units_by_id.get(dep_id)
            if dep is not None and dep.status is WorkUnitStatus.HOLD and dep_id not in seen:
                seen.add(dep_id)
                result.append(dep_id)
    return result


def _cyclic_task_ids(in_queue_tasks: list[WorkUnit], units_by_id: dict[str, WorkUnit]) -> list[str]:
    """Return the members of any dependency cycle that stalls an in-queue task (TDI-009).

    Uses the shared :func:`devbench.backlog.dep_cycle.find_cycles` over the
    work-unit ``## Dependencies`` graph (the same source ``validate-backlog``'s
    canonical graph draws on), so ``next`` and ``validate-backlog`` agree on
    cycle membership and the stall diagnostic names the ACTUAL cycle members --
    not an arbitrary detection node, the misleading behaviour the previous
    closed-set heuristic produced. Only cycles among non-terminal units that
    include at least one in-queue task are returned (those are the cycles that
    can stall ``next``).
    """
    from devbench.backlog.dep_cycle import find_cycles

    in_queue_ids = {t.id for t in in_queue_tasks}
    graph: dict[str, list[str]] = {
        uid: list(unit.dependencies)
        for uid, unit in units_by_id.items()
        if unit.status not in (WorkUnitStatus.DONE, WorkUnitStatus.DECLINED)
    }
    seen: set[str] = set()
    result: list[str] = []
    for cycle in find_cycles(graph):
        if not any(member in in_queue_ids for member in cycle):
            continue
        for member in cycle:
            if member not in seen:
                seen.add(member)
                result.append(member)
    return result


def _classify_next_stall(
    units: list[WorkUnit],
) -> tuple[int, str, list[str]]:
    """Classify why no in-queue task is actionable.

    Inspects in-queue TASK units and their unsatisfied dependencies to produce
    a three-tuple describing the stall:

    - ``in_queue_count``: number of TASK units with status ``IN_QUEUE``.
    - ``label``: one of ``"held-blocking"``, ``"cyclic"``, or ``"awaiting-dep"``.
    - ``ids``: the IDs most directly responsible for the stall.

    Priority of labels (highest wins):

    1. ``held-blocking`` -- at least one in-queue task has a dependency whose
       status is ``HOLD``.
    2. ``cyclic`` -- every in-queue task's unmet dependencies are also in-queue
       (closed set with no external progress possible).
    3. ``awaiting-dep`` -- remaining cases where a non-terminal, non-HOLD dep
       is blocking progress.

    Args:
        units: All work units from the parsed backlog.

    Returns:
        ``(in_queue_count, label, ids)`` describing the dominant stall reason.
    """
    units_by_id = {u.id: u for u in units}

    in_queue_tasks = [u for u in units if u.unit_type is WorkUnitType.TASK and u.status is WorkUnitStatus.IN_QUEUE]
    in_queue_count = len(in_queue_tasks)

    held_ids = _held_blocking_ids(in_queue_tasks, units_by_id)
    if held_ids:
        return in_queue_count, "held-blocking", held_ids

    cyclic_ids = _cyclic_task_ids(in_queue_tasks, units_by_id)
    if cyclic_ids:
        return in_queue_count, "cyclic", cyclic_ids

    # Remaining case: awaiting-dep (dep not done, not hold, not cyclic).
    seen: set[str] = set()
    awaiting_ids: list[str] = []
    for task in in_queue_tasks:
        for dep_id in _unmet_dep_ids(task, units_by_id):
            if dep_id not in seen:
                seen.add(dep_id)
                awaiting_ids.append(dep_id)
    return in_queue_count, "awaiting-dep", awaiting_ids


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


def cmd_next(*argv: str) -> int:
    """Print the next actionable work unit.

    Accepts scope-filter flags (spec section 4.2.2, AC-190-10, AC-190-11):

    - ``--include "<tokens>"`` -- one-off include selector; overrides active
      scope.json when present.
    - ``--exclude "<tokens>"`` -- one-off exclude selector.

    When neither flag is supplied, the active ``scope.json`` (if any) is
    consulted instead.  When a scope is active and no candidates match, prints
    ``NO_ACTIONABLE_IN_SCOPE`` and returns 0 (AC-190-15).

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

    # Serialize claims (tracked-issue 002): every claim shares ONE target-repo
    # checkout, so a NEW in-queue unit must not be offered while the
    # concurrently-in-progress cap is already saturated -- otherwise two
    # in-progress units leak each other's uncommitted files into get-diff. Count
    # the units currently IN-PROGRESS; if that meets the cap, drop IN_QUEUE
    # candidates so only resumable IN_PROGRESS candidates remain.
    in_progress_ids = [u.id for u in units if u.status is WorkUnitStatus.IN_PROGRESS]
    serialize_cap = _resolve_max_parallel_in_progress()
    dropped_by_serialize = False
    if len(in_progress_ids) >= serialize_cap:
        kept = [c for c in candidates if c.status is WorkUnitStatus.IN_PROGRESS]
        # The serialized reason is reported ONLY when the cap actually suppressed
        # an otherwise-actionable in-queue candidate -- so a genuine dependency
        # stall (candidates already empty before filtering) still reports its real
        # cause rather than being masked as "serialized, busy".
        dropped_by_serialize = len(kept) < len(candidates)
        candidates = kept

    if not candidates:
        if scope_filter is not None:
            print("NO_ACTIONABLE_IN_SCOPE")
        elif dropped_by_serialize:
            # Distinct from a genuine stall: the loop is serialized and busy, not
            # wedged. Name the in-progress unit(s) so the operator/loop can tell
            # "serialized, retry later" from "nothing actionable left".
            print("NO_ACTIONABLE")
            ids_str = ", ".join(in_progress_ids)
            print(
                f"  reason: IN_PROGRESS_AT_CAPACITY: {len(in_progress_ids)} in-progress at cap "
                f"{serialize_cap}; a new unit is deferred until one completes: {ids_str}"
            )
        elif backlog_parser.all_done(units):
            print("ALL_DONE")
        else:
            in_queue_count, stall_label, stall_ids = _classify_next_stall(units)
            print("NO_ACTIONABLE")
            ids_str = ", ".join(stall_ids)
            print(f"  reason: {in_queue_count} in-queue, 0 actionable; {stall_label}: {ids_str}")
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
    placeholder_error = _claim_placeholder_error(wu_file, unit_id)
    if placeholder_error is not None:
        print(placeholder_error, file=sys.stderr)
        return 1

    # Pre-claim target-repo guard (issue #241) -> CLAIM_BLOCKED_PRECLAIM (44), and
    # serialize-claims backstop (tracked-issue 002) -> CLAIM_DEFERRED_SERIALIZED
    # (47). Both are folded into one pre-lock check that returns the exit code to
    # surface (or None to proceed), keeping cmd_claim within the PLR0911 budget.
    preclaim_rc = _claim_preflight_guard(unit, wu_file, unit_id, units)
    if preclaim_rc is not None:
        return preclaim_rc

    session_name: str | None = os.environ.get("DEVBENCH_SESSION_NAME", "").strip() or None
    error_message = _claim_under_lock(wu_file, unit_id, session_name)
    if error_message is not None:
        print(error_message, file=sys.stderr)
        return 1

    # TDI-006: before the executor starts, evict any foreign (non-manifest)
    # orphaned WIP left in the target checkout by a prior interrupted unit so
    # every executor begins from a known-clean tree. The claimed unit's own
    # manifest work is preserved; evicted WIP is parked in a recoverable stash.
    _clean_foreign_wip_on_claim(unit)

    logger.info("Claimed %s (set to in-progress)", unit_id)
    print(f"Claimed {unit_id}")
    return 0


def _claim_write_unresolved_repo_marker(wu_file: Path, unit_id: str, repo: str) -> None:
    """Idempotently write the [BLOCKED_TARGET_REPO_UNRESOLVED] marker to wu_file.

    Reads wu_file to check whether the marker is already present. If so,
    returns immediately (no duplicate write). Otherwise:

    1. Rewrites the ``## Status:`` line to ``blocked`` directly in wu_file.
    2. Appends a timestamped audit comment embedding the marker tag.
    3. Calls ``BacklogManager().mark_blocked`` to synchronise BACKLOG_INDEX
       (the index row must also reflect ``blocked``). The mark_blocked call
       will find the status line already set to ``blocked`` in wu_file and
       write only the BACKLOG_INDEX row.

    The marker is written directly to wu_file (steps 1-2) before the
    BacklogManager call so that the idempotency guard is file-based:
    a repeat invocation after a partial failure (e.g. BACKLOG_INDEX write
    fails) will not duplicate the WU-file comment.

    Args:
        wu_file: Absolute path to the work-unit ``.md`` file.
        unit_id: Work-unit identifier used in the mark_blocked call.
        repo: The unresolvable repo name embedded in the marker tag.
    """
    from datetime import UTC, datetime

    marker_tag = f"{BLOCKED_TARGET_REPO_UNRESOLVED_MARKER} {repo}"
    content = wu_file.read_text(encoding="utf-8")
    if marker_tag in content:
        return

    # Rewrite the status line to blocked in the WU file.
    updated = STATUS_LINE_RE.sub(r"\g<1>blocked", content)
    # Append the audit comment embedding the marker tag.
    timestamp = datetime.now(UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    entry = f"[{timestamp}] [backlog_manager] [BLOCKED] target repo unresolvable: {marker_tag}\n"
    if COMMENTS_SECTION_HEADER in updated:
        updated = updated.rstrip("\n") + "\n\n" + entry
    else:
        updated = updated.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    wu_file.write_text(updated, encoding="utf-8")

    # Synchronise the BACKLOG_INDEX row to ``blocked``. ``force_status`` updates
    # both the WU file status line and the index row without appending a second
    # audit comment (unlike ``mark_blocked`` which would append a duplicate).
    # The WU file status is already ``blocked`` from the direct write above;
    # ``_set_status`` rewrites it to the same value (idempotent on the WU file)
    # while correctly updating the index row.
    BacklogManager().force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_BLOCKED)


def _claim_placeholder_error(wu_file: Path, unit_id: str) -> str | None:
    """Return an error message when the unit's Changes Manifest still has a placeholder.

    Issue #117: refuses to claim a work unit whose Changes Manifest still contains
    a ``TBD`` placeholder row. Returns the human-readable error message, or ``None``
    when the manifest is clean. Extracted so ``cmd_claim`` stays within the PLR0911
    return-statement budget.
    """
    # Use a fresh import path so the unit-test layer's
    # `patch("devbench.cli.BacklogManager", ...)` does not stub out the
    # placeholder-detection classmethod via attribute lookup on the mock.
    from devbench.backlog.manager import BacklogManager as _BacklogManager

    placeholder = _BacklogManager._first_placeholder_manifest_cell(wu_file.read_text(encoding="utf-8"))
    if not placeholder:
        return None
    return (
        f"ERROR: cannot claim {unit_id!r}: Changes Manifest still has placeholder row "
        f"{placeholder!r}. Replace with real file entries before claim."
    )


def _claim_serialize_deferral(unit: WorkUnit, units: list[WorkUnit]) -> str | None:
    """Return a deferral message when claiming *unit* would breach the serialize cap.

    Serialize-claims hard backstop (tracked-issue 002). Every claim shares ONE
    target-repo checkout, so a SECOND unit must not go in-progress while the cap
    (``orchestrate.max_parallel_in_progress``, default 1) is saturated by OTHER
    in-progress units -- otherwise the two units leak each other's uncommitted
    files into ``get-diff`` / the staged index. Re-claiming a unit that is ALREADY
    in-progress is idempotent and always permitted (it owns the checkout already),
    so this returns ``None`` for it.

    Returns a human-readable DEFERRAL message (a deferral, NOT a unit failure) when
    the cap is breached, else ``None`` to proceed with the claim.
    """
    if unit.status is WorkUnitStatus.IN_PROGRESS:
        return None
    other_in_progress = [u.id for u in units if u.id != unit.id and u.status is WorkUnitStatus.IN_PROGRESS]
    serialize_cap = _resolve_max_parallel_in_progress()
    if len(other_in_progress) < serialize_cap:
        return None
    busy = ", ".join(other_in_progress)
    return (
        f"DEFERRED: cannot claim {unit.id!r}: {len(other_in_progress)} unit(s) already in-progress "
        f"at the serialized cap of {serialize_cap} ({busy}). This is NOT a failure -- the unit stays "
        f"in-queue; retry after the in-progress unit completes."
    )


def _claim_preflight_guard(unit: WorkUnit, wu_file: Path, unit_id: str, units: list[WorkUnit]) -> int | None:
    """Run the lock-free pre-claim guards; return an exit code to surface, or ``None`` to proceed.

    Two guards, evaluated before any lock is acquired:

    1. **Target-repo guard (issue #241).** When ``unit.repo`` cannot be resolved
       by :func:`resolve_repo`, write the idempotent
       ``[BLOCKED_TARGET_REPO_UNRESOLVED]`` marker, set the unit ``blocked``, and
       return :data:`CLAIM_BLOCKED_PRECLAIM` (44).
    2. **Serialize-claims backstop (tracked-issue 002).** When claiming this NEW
       unit would breach ``orchestrate.max_parallel_in_progress`` (the shared
       target-repo checkout would then carry two units' WIP), print the deferral
       message and return :data:`CLAIM_DEFERRED_SERIALIZED` (47) -- a deferral,
       not a unit failure (nothing is written; the unit stays ``in-queue``).

    Extracted from ``cmd_claim`` so it stays within the PLR0911 return budget.
    """
    try:
        resolve_repo(unit.repo)
    except ValueError:
        _claim_write_unresolved_repo_marker(wu_file, unit_id, unit.repo)
        print(
            f"ERROR: cannot claim {unit_id!r}: target repo {unit.repo!r} is not in the allowed "
            f"repos list. {BLOCKED_TARGET_REPO_UNRESOLVED_MARKER} {unit.repo}",
            file=sys.stderr,
        )
        return CLAIM_BLOCKED_PRECLAIM

    deferral = _claim_serialize_deferral(unit, units)
    if deferral is not None:
        print(deferral, file=sys.stderr)
        return CLAIM_DEFERRED_SERIALIZED
    return None


def _claim_under_lock(wu_file: Path, unit_id: str, session_name: str | None) -> str | None:
    """Acquire BACKLOG.lock, re-read status, and write ``in-progress`` under the lock.

    Returns an error message string when the claim fails (race, timeout, or missing
    status line), or ``None`` on success.  Keeps ``cmd_claim`` within the
    PLR0911 return-statement budget.

    Args:
        wu_file: Absolute path to the work-unit ``.md`` file.
        unit_id: Work-unit identifier used in error messages and audit comments.
        session_name: Optional named-session name from ``DEVBENCH_SESSION_NAME``.

    Returns:
        ``None`` on success; a human-readable error string on failure.

    Raises:
        OSError: Unexpected OS error from ``fcntl.flock`` (propagated to caller).
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

    # Cascade mode: <id> <status> --cascade
    if "--cascade" in args:
        cascade_args = [a for a in args if a != "--cascade"]
        if len(cascade_args) != 2:
            print(
                "ERROR: set-status cascade usage: set-status <id> <status> --cascade",
                file=sys.stderr,
            )
            return 1
        unit_id, new_status = cascade_args[0], cascade_args[1]
        from devbench.backlog.manager import VALID_STATUSES

        if new_status.lower() not in VALID_STATUSES:
            print(
                f"ERROR: Invalid status '{new_status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
                file=sys.stderr,
            )
            return 1
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
        return cascade_status_mutation(
            unit_id,
            f"set-status:{new_status.lower()}",
            "",
            units,
            BacklogManager(),
        )

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

    if new_status.lower() == STATUS_DONE:
        print(
            "ERROR: 'set-status done' is not allowed; completion must go through"
            " 'mark-done' (enforces the done-gate: all required judges passed)",
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
        rc = _clean_target_repo_on_block(target)
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

    if new_status.lower() == STATUS_DONE:
        print(
            "ERROR: 'set-status done' is not allowed; completion must go through"
            " 'mark-done' (enforces the done-gate: all required judges passed)",
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


def _clean_target_repo_on_block(unit: WorkUnit) -> int:
    """Reset and clean the target repo's working tree when a task transitions to blocked.

    Resolves the target checkout DETERMINISTICALLY from the unit's configured
    repo (``resolve_repo`` + ``REPO_LOCAL_PATHS`` -- the same resolution the
    claim and git-ops paths use), then runs ``git reset --hard HEAD`` and
    ``git clean -fd`` against it.

    Fails fast (returns 1 with an actionable error) when the repo is
    unrecognised or has no configured local checkout -- it never silently
    skips, because leftover staged/untracked files from the blocked unit would
    otherwise pollute the next claimed unit's working tree and review diff.
    (Previously this scraped a ``Local path:`` line out of the work-unit prose
    and skipped cleanup when absent -- a fragile no-op that leaked the blocked
    unit's partial work into subsequent units.)

    Args:
        unit: The work unit being transitioned to ``blocked``.

    Returns:
        0 when the target working tree was reset + cleaned; 1 when the repo
        cannot be resolved, the checkout is missing, or a git command errors.
    """
    try:
        canonical_repo = resolve_repo(unit.repo)
    except ValueError as exc:
        logger.error(
            "_clean_target_repo_on_block: cannot resolve target repo %r for unit %s: %s",
            unit.repo,
            unit.id,
            exc,
        )
        return 1

    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        logger.error(
            "_clean_target_repo_on_block: no local checkout configured for repo %r (unit %s); "
            "cannot clean the target working tree (configure REPO_LOCAL_PATHS).",
            canonical_repo,
            unit.id,
        )
        return 1

    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        logger.error(
            "_clean_target_repo_on_block: configured checkout %r for repo %r does not exist (unit %s).",
            str(repo_path),
            canonical_repo,
            unit.id,
        )
        return 1

    reset_result = subprocess.run(
        ["git", "-C", str(repo_path), "reset", "--hard", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if reset_result.returncode != 0:
        logger.warning(
            "_clean_target_repo_on_block: git reset failed for '%s': %s",
            repo_path,
            reset_result.stderr.strip(),
        )
        return 1

    clean_result = subprocess.run(
        ["git", "-C", str(repo_path), "clean", "-fd"],
        check=False,
        capture_output=True,
        text=True,
    )
    if clean_result.returncode != 0:
        logger.warning(
            "_clean_target_repo_on_block: git clean failed for '%s': %s",
            repo_path,
            clean_result.stderr.strip(),
        )
        return 1

    logger.info("_clean_target_repo_on_block: cleaned target repo at '%s'", repo_path)
    return 0


def cmd_mark_done(*argv: str) -> int:
    """Mark a work unit as Done, enforcing the done-gate check.

    Usage::

        mark-done <id>                     standard diff-attributed done-gate
        mark-done <id> --already-satisfied verification-only already-landed path

    The default path calls ``BacklogManager.mark_done()`` which verifies that
    all required review judges passed in the most recent round AND that the AC
    evidence ledger is complete before allowing the transition.

    The ``--already-satisfied`` flag routes to
    ``BacklogManager.mark_done_already_satisfied()`` -- the narrow, audited
    recovery for a *verification-only* unit whose deliverable already landed (no
    diff to attribute, so the review pipeline can never run). That path is gated
    on the unit being verification-only AND its ``verify-ac`` evidence being
    complete; it cannot be used to skip work on a unit that authors source.

    Returns ``1`` (with a stderr ERROR) on any gate failure or bad invocation.
    """
    parsed = _parse_mark_done_argv(argv)
    if isinstance(parsed, int):
        return parsed
    unit_id, already_satisfied = parsed

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
    # This guard applies to both paths: an already-satisfied unit must not
    # carry unresolved rejection feedback either.
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
        if already_satisfied:
            mgr.mark_done_already_satisfied(wu_file, BACKLOG_INDEX, unit_id)
        else:
            mgr.mark_done(wu_file, BACKLOG_INDEX, unit_id)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mgr._append_agent_comment(wu_file, "orchestrator", f"[DONE] Work unit {unit_id} completed")

    logger.info("Marked %s as done", unit_id)
    print(f"Marked {unit_id} as done")
    return 0


def _parse_mark_done_argv(argv: "tuple[str, ...]") -> "tuple[str, bool] | int":
    """Parse ``mark-done`` argv into ``(unit_id, already_satisfied)``.

    Accepts exactly one positional work-unit id and an optional
    ``--already-satisfied`` flag, in any order. Returns the integer ``1`` (with
    a stderr ERROR) on a missing id, an unknown flag, or extra positionals --
    fail-fast, no silent absorption.

    Args:
        argv: Raw trailing CLI tokens for ``mark-done``.

    Returns:
        ``(unit_id, already_satisfied)`` on success, or ``1`` on a parse error.
    """
    unit_id: str | None = None
    already_satisfied = False
    for arg in argv:
        if arg == "--already-satisfied":
            already_satisfied = True
            continue
        if arg.startswith("--"):
            print(f"ERROR: mark-done: unknown flag {arg!r}", file=sys.stderr)
            return 1
        if unit_id is not None:
            print(f"ERROR: mark-done accepts exactly one work-unit id; got extra positional {arg!r}", file=sys.stderr)
            return 1
        unit_id = arg
    if unit_id is None:
        print("ERROR: mark-done usage: mark-done <id> [--already-satisfied]", file=sys.stderr)
        return 1
    return unit_id, already_satisfied


def cmd_decline(*argv: str) -> int:
    """Mark a work unit as Declined (won't ever be done) with a captured reason.

    Usage::

        decline <id> --reason "<message>" [--cascade]

    Declined is a deliberate final-decision status, distinct from Blocked
    (waiting on something) and Done (completed). Declined children count
    as terminal-complete for parent rollup. The ``--reason`` is REQUIRED
    because the decision must leave an audit trail; em-dashes are
    rejected at the input boundary for backlog hygiene.

    When ``--cascade`` is supplied, all eligible descendants of ``<id>``
    are also declined in depth-desc order (Tasks first, then Stories,
    then Features, then the root itself).  Descendants already in
    ``done`` or ``declined`` are skipped with a SKIP line on stdout.
    """
    parsed = _parse_id_and_reason_cascade(argv, "decline", reason_required=True)
    if isinstance(parsed, int):
        return parsed
    task_id, reason, cascade = parsed

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    if cascade:
        return cascade_status_mutation(task_id, "decline", reason, units, BacklogManager())

    target = _find_unit(units, task_id)
    if target is None:
        print(f"ERROR: Work unit '{task_id}' not found", file=sys.stderr)
        return 1
    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{task_id}'", file=sys.stderr)
        return 1

    BacklogManager().mark_declined(wu_file, BACKLOG_INDEX, task_id, reason)
    logger.info("Declined %s: %s", task_id, reason)
    print(json.dumps({"task_id": task_id, "status": "declined", "reason": reason}))
    return 0


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
            content = wu_file.read_text(encoding="utf-8")
            if _has_open_proposal_marker(content, units_by_id):
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

    Issue #248b: a per-task signature counter under
    ``<workspace>/.devbench/cascade-cycles/<id>.json`` gates infinite re-queue
    loops. When a task's count exceeds
    ``RUNTIME_CONFIG.backlog.cascade_requeue_max_cycles`` for the same
    signature (markers + unsatisfied deps unchanged), the circuit breaker
    writes a ``[CASCADE_CIRCUIT_BREAKER]`` audit and a ``[BLOCKED]`` row,
    adds the task to the ``escalated`` list in the output envelope, and
    continues (rc stays 0). The counter resets whenever the signature changes
    (i.e. genuine progress was made).

    Returns 0 always; output is a JSON envelope listing flips + skips +
    escalated.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    units_by_id = {u.id: u for u in units}
    manager = BacklogManager()

    flipped: list[dict[str, str | list[str]]] = []
    skipped: list[dict[str, str]] = []
    escalated: list[str] = []

    terminal_statuses = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    max_cycles: int = RUNTIME_CONFIG.backlog.cascade_requeue_max_cycles

    for unit in units:
        if unit.unit_type is not WorkUnitType.TASK:
            continue
        if unit.status is not WorkUnitStatus.BLOCKED:
            continue

        wu_file = _resolve_unit_file(unit)
        if wu_file is None:
            skipped.append({"unit_id": unit.id, "reason": "work-unit file missing"})
            continue

        content = wu_file.read_text(encoding="utf-8")
        marker_ids = sorted(set(_BLOCKED_PENDING_PROPOSAL_MARKER_RE.findall(content)))

        # Marker evaluation: every target must be terminal AND known.
        open_markers: list[str] = []
        unresolved_marker = ""
        for marker in marker_ids:
            target = units_by_id.get(marker)
            if target is None:
                unresolved_marker = f"unknown marker target {marker}"
                open_markers.append(marker)
                break
            if target.status not in terminal_statuses:
                unresolved_marker = f"open marker {marker} ({target.status.value})"
                open_markers.append(marker)
                break
        if unresolved_marker:
            unsatisfied_deps = _all_unsatisfied_deps(unit, units_by_id)
            _cascade_check_and_track(
                WORKSPACE_ROOT,
                manager,
                wu_file,
                unit.id,
                open_markers,
                unsatisfied_deps,
                unresolved_marker,
                max_cycles,
                skipped,
                escalated,
            )
            continue

        # Regular-dep evaluation.
        if not BacklogParser._deps_satisfied(unit, units_by_id):
            unsatisfied_dep_id = _first_unsatisfied_dep(unit, units_by_id)
            reason = (
                f"regular dep not yet terminal: {unsatisfied_dep_id}"
                if unsatisfied_dep_id
                else "regular deps unsatisfied"
            )
            unsatisfied_deps = _all_unsatisfied_deps(unit, units_by_id)
            _cascade_check_and_track(
                WORKSPACE_ROOT,
                manager,
                wu_file,
                unit.id,
                [],
                unsatisfied_deps,
                reason,
                max_cycles,
                skipped,
                escalated,
            )
            continue

        manager.force_status(wu_file, BACKLOG_INDEX, unit.id, STATUS_IN_QUEUE)
        if marker_ids:
            message = f"[CASCADE_RECONCILED] markers {marker_ids} terminal and regular deps satisfied; re-queuing"
        elif _FORCED_BLOCKED_ON_STOP_AUDIT_PREFIX in content:
            # TDI-002: the unit's only blocker was the SIGTERM shutdown safeguard
            # (no marker, deps satisfied). It was merely interrupted; re-queue it
            # with a distinct audit so the operator sees no action was required.
            message = "[REQUEUED_AFTER_STOP] interrupted on orchestrator stop; no structural blocker; re-queuing"
        else:
            message = "[CASCADE_RECONCILED] regular deps satisfied; re-queuing"
        manager._append_agent_comment(wu_file, "backlog_manager", message)
        flipped.append({"unit_id": unit.id, "closed_markers": marker_ids})

    # Issues #207, #209: surface classification transitions for tasks that remain
    # blocked after the reconcile sweep -- a stale ``[BLOCKED]`` audit that
    # has drifted into ``OPERATOR_ACTION_REQUIRED`` produces exactly one
    # Slack ping.  Cache-backed, idempotent across repeated invocations.
    _notify_blocked_classification_transitions(units)

    output: dict[str, object] = {"flipped": flipped, "skipped": skipped, "escalated": escalated}
    print(json.dumps(output))
    logger.info(
        "reconcile-cascade: %d flipped, %d skipped, %d escalated",
        len(flipped),
        len(skipped),
        len(escalated),
    )
    return 0


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


_BLOCKED_PENDING_PROPOSAL_MARKER_RE: re.Pattern[str] = re.compile(
    r"\[BLOCKED_PENDING_PROPOSAL\]\s+(E\d+(?:-F\d+)?(?:-S\d+)?(?:-T\d+)?)"
)


def _cascade_cycle_signature(marker_ids: list[str], unsatisfied_dep_ids: list[str]) -> str:
    """Compute the 12-char circuit-breaker signature for a blocked task.

    The signature is the first 12 hex characters of the SHA-256 digest of
    the pipe-joined sorted marker IDs, a ``#`` separator, and the pipe-joined
    sorted unsatisfied dependency IDs (issue #248b, AC-248b-1).

    Args:
        marker_ids: All ``[BLOCKED_PENDING_PROPOSAL]`` marker targets found in
            the work-unit file (resolved to sorted, deduplicated IDs).
        unsatisfied_dep_ids: Dep IDs that are not yet terminal per
            ``_first_unsatisfied_dep`` (may be empty).

    Returns:
        A 12-character lower-hex string.
    """
    payload = "|".join(sorted(marker_ids)) + "#" + "|".join(sorted(unsatisfied_dep_ids))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _cascade_cycle_file(workspace_root: Path, task_id: str) -> Path:
    """Return the path to the per-task cascade-cycle counter JSON file.

    Creates parent directories on first access.
    """
    cycles_dir = workspace_root / ".devbench" / "cascade-cycles"
    cycles_dir.mkdir(parents=True, exist_ok=True)
    return cycles_dir / f"{task_id}.json"


def _read_cascade_cycle(counter_file: Path) -> tuple[str, int]:
    """Read the persisted signature and count from *counter_file*.

    Returns ``("", 0)`` when the file does not exist or is unreadable.
    """
    if not counter_file.exists():
        return "", 0
    data = json.loads(counter_file.read_text(encoding="utf-8"))
    return str(data.get("signature", "")), int(data.get("count", 0))


def _write_cascade_cycle(counter_file: Path, signature: str, count: int) -> None:
    """Persist the signature and count to *counter_file*."""
    counter_file.write_text(
        json.dumps({"signature": signature, "count": count}),
        encoding="utf-8",
    )


def _all_unsatisfied_deps(unit: WorkUnit, units_by_id: dict[str, WorkUnit]) -> list[str]:
    """Return all dep IDs in ``unit.dependencies`` that are NOT terminal.

    Used by the cascade circuit breaker to build the per-task signature.
    Unlike ``_first_unsatisfied_dep``, this collects every unsatisfied dep
    so that partial progress (one dep going terminal while others stay open)
    changes the signature and resets the counter.
    """
    terminal = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    result: list[str] = []
    for dep_id in unit.dependencies:
        dep = units_by_id.get(dep_id)
        if dep is None:
            continue
        if dep.unit_type is WorkUnitType.TASK:
            if dep.status not in terminal:
                result.append(dep_id)
            continue
        # Non-task dep: scan descendant tasks.
        for descendant in units_by_id.values():
            if (
                descendant.id != dep_id
                and descendant.id.startswith(dep_id + "-")
                and descendant.unit_type is WorkUnitType.TASK
                and descendant.status not in terminal
            ):
                result.append(dep_id)
                break
    return sorted(result)


def _cascade_check_and_track(
    workspace_root: Path,
    manager: BacklogManager,
    wu_file: Path,
    task_id: str,
    open_markers: list[str],
    unsatisfied_deps: list[str],
    reason: str,
    max_cycles: int,
    skipped: list[dict[str, str]],
    escalated: list[str],
) -> None:
    """Update the per-task cycle counter and fire the breaker when over the cap.

    Called for every task that remains blocked (either due to an unresolved
    marker or an unsatisfied dep). Increments the counter when the signature
    is stable; resets it when the signature changes (genuine progress).  When
    ``count > max_cycles`` the circuit breaker fires and the task joins
    *escalated*; otherwise the task joins *skipped*.

    Args:
        workspace_root: Workspace root path for the counter-file directory.
        manager: Active ``BacklogManager`` for writing audit comments.
        wu_file: Work-unit Markdown file path.
        task_id: Work-unit identifier.
        open_markers: Open ``[BLOCKED_PENDING_PROPOSAL]`` marker IDs.
        unsatisfied_deps: Unsatisfied dependency IDs.
        reason: Human-readable skip reason (used when breaker does NOT fire).
        max_cycles: Cap from ``backlog.cascade_requeue_max_cycles``.
        skipped: Mutable list of skip records (appended to when no breaker).
        escalated: Mutable list of escalated task IDs (appended to on trip).
    """
    signature = _cascade_cycle_signature(open_markers, unsatisfied_deps)
    counter_file = _cascade_cycle_file(workspace_root, task_id)
    stored_sig, count = _read_cascade_cycle(counter_file)
    if stored_sig != signature:
        count = 0
    count += 1
    if count > max_cycles:
        _cascade_circuit_breaker_fire(manager, wu_file, task_id, signature, count)
        escalated.append(task_id)
    else:
        _write_cascade_cycle(counter_file, signature, count)
        skipped.append({"unit_id": task_id, "reason": reason})


def _cascade_circuit_breaker_fire(
    manager: BacklogManager,
    wu_file: Path,
    task_id: str,
    signature: str,
    count: int,
) -> None:
    """Write the verbatim circuit-breaker audit marker and a ``[BLOCKED]`` row.

    Called by ``cmd_reconcile_cascade`` when the per-task cycle counter
    exceeds ``cascade_requeue_max_cycles`` for the same signature.

    Args:
        manager: The active ``BacklogManager`` instance.
        wu_file: Work-unit Markdown file path.
        task_id: Work-unit identifier (e.g. ``E2-F1-S2-T1``).
        signature: 12-char hex signature for the current stale cycle.
        count: Current cycle count (already exceeds the cap).
    """
    breaker_msg = (
        f"[CASCADE_CIRCUIT_BREAKER] task={task_id} signature={signature} "
        f"cycles={count} escalated=OPERATOR_ACTION_REQUIRED"
    )
    blocked_msg = f"[BLOCKED] cascade circuit breaker tripped for {task_id} -- operator review required"
    manager._append_agent_comment(wu_file, "backlog_manager", breaker_msg)
    manager._append_agent_comment(wu_file, "backlog_manager", blocked_msg)
    logger.warning(
        "cascade circuit breaker tripped: task=%s signature=%s cycles=%d",
        task_id,
        signature,
        count,
    )


# Captures the audit-comment classifier tag (``[BLOCKED]`` / ``[UNBLOCKED]`` /
# ``[CASCADE_RESOLVED]``). Used by ``_unsuperseded_blocked_audits`` to walk
# the Comments section in chronological order and drop ``[BLOCKED]`` rows
# that have been superseded by a later positive transition.
_BLOCKED_AUDIT_LINE_RE: re.Pattern[str] = re.compile(r"\[(?P<tag>BLOCKED|UNBLOCKED|CASCADE_RESOLVED)\](?P<rest>[^\n]*)")


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


def _has_open_proposal_marker(content: str, units_by_id: dict[str, WorkUnit]) -> bool:
    """Return ``True`` iff ``content`` carries at least one ``[BLOCKED_PENDING_PROPOSAL]``
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
    """
    terminal = {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    for marker in _BLOCKED_PENDING_PROPOSAL_MARKER_RE.findall(content):
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


def _parse_id_and_reason_cascade(
    argv: tuple[str, ...] | list[str],
    command_name: str,
    reason_required: bool,
) -> tuple[str, str, bool] | int:
    """Parse ``<id> [--reason <message>] [--cascade]`` for hold/unhold/decline cascade variants.

    Args:
        argv: Raw CLI tokens.
        command_name: Human-readable command name for error messages.
        reason_required: When ``True``, ``--reason`` is mandatory and its
            absence returns rc=1.  Pass ``False`` for commands like
            ``unhold`` where reason is accepted but not enforced.

    Returns:
        ``(task_id, reason, cascade)`` on success, or an integer exit code
        on parse error (message already written to stderr).
    """
    task_id = ""
    reason = ""
    cascade = False
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        if arg == "--cascade":
            cascade = True
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
    if not task_id:
        print(f"ERROR: {command_name} requires <id> [--reason <message>]", file=sys.stderr)
        return 1
    if reason_required and not reason:
        print(f"ERROR: {command_name} requires <id> --reason <message>", file=sys.stderr)
        return 1
    if reason:
        em_dash_rc = _reject_em_dash("reason", reason)
        if em_dash_rc is not None:
            return em_dash_rc
    return task_id, reason, cascade


# ---------------------------------------------------------------------------
# Shared cascade traversal engine (issue #245)
# ---------------------------------------------------------------------------

#: Statuses ineligible for ``hold``: already held or terminal.
_HOLD_INELIGIBLE: frozenset[WorkUnitStatus] = frozenset(
    {WorkUnitStatus.HOLD, WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
)

#: Statuses ineligible for ``decline``: already terminal.
_DECLINE_INELIGIBLE: frozenset[WorkUnitStatus] = frozenset({WorkUnitStatus.DONE, WorkUnitStatus.DECLINED})

#: Statuses ineligible for ``unhold``: all statuses except hold.
#: (unhold eligibility = IS hold; ineligible = is NOT hold)


def _cascade_depth_key(unit_id: str) -> int:
    """Return the depth of a work-unit ID as a non-negative integer.

    Depth equals the number of hyphens, so Tasks (deepest) have the
    highest depth value.  Used for descending-depth sort so Tasks are
    processed before Stories, Stories before Features, Features before Epics.

    Args:
        unit_id: A canonical work-unit ID such as ``E3-F1-S1-T1``.

    Returns:
        Number of hyphen-separated segments minus one (0 for an Epic with
        no hyphens, 3 for a Task like ``E3-F1-S1-T1``).
    """
    return unit_id.count("-")


def cascade_status_mutation(
    root_id: str,
    op: str,
    reason: str,
    units: list[WorkUnit],
    mgr: BacklogManager,
) -> int:
    """Apply a status mutation to all descendants of ``root_id`` in depth-desc order.

    Traversal visits descendants sorted by depth descending (Tasks first,
    then Stories, then Features, then the root Epic itself) using
    :func:`~devbench.scope._expand_prefix` for discovery.  The root unit
    itself is also mutated.

    For each descendant, eligibility is evaluated:

    - ``hold``: ineligible when current status is ``hold``, ``done``, or
      ``declined``.
    - ``unhold``: ineligible when current status is anything other than
      ``hold``.
    - ``decline``: ineligible when current status is ``done`` or
      ``declined``.
    - ``set-status:<target>``: ineligible when current status is ``done``
      or ``declined``.

    Ineligible descendants receive a SKIP line on stdout:
    ``SKIP <id>: <current-status> not eligible for <op>``.

    Eligible descendants are mutated via the appropriate ``BacklogManager``
    method.  The audit reason passed to each mutator is prefixed with
    ``[CASCADE_FROM <root_id>]`` so the per-WU comment reads:
    ``[<OP>] [CASCADE_FROM <root_id>] <reason>`` (spec Section 2 G6,
    Section 5).

    Args:
        root_id: The ancestor scope to expand (e.g. ``E3``).
        op: One of ``"hold"``, ``"unhold"``, ``"decline"``, or
            ``"set-status:<target_status>"``.
        reason: Human-readable rationale appended to each audit comment.
        units: All work units from the parsed index.
        mgr: :class:`~devbench.backlog.manager.BacklogManager` instance.

    Returns:
        0 on success (even when some descendants are skipped).  1 when
        the root ID is not found in the index or a descendant file cannot
        be resolved.

    Raises:
        Nothing -- all errors are reported to stderr and return rc=1.
    """
    all_ids = [u.id for u in units]
    descendant_ids = _expand_prefix(root_id, all_ids)
    if not descendant_ids:
        print(f"ERROR: Work unit '{root_id}' not found", file=sys.stderr)
        return 1

    units_by_id = {u.id: u for u in units}

    # Sort depth-descending: Tasks (most hyphens) first, Epics last.
    sorted_ids = sorted(descendant_ids, key=_cascade_depth_key, reverse=True)

    cascade_reason = f"[CASCADE_FROM {root_id}] {reason}"

    for unit_id in sorted_ids:
        unit = units_by_id[unit_id]
        current_status = unit.status.value.lower().replace(" ", "-")

        # Evaluate eligibility for the requested operation.
        if op == "hold":
            eligible = unit.status not in _HOLD_INELIGIBLE
        elif op == "unhold":
            eligible = unit.status is WorkUnitStatus.HOLD
        elif op == "decline":
            eligible = unit.status not in _DECLINE_INELIGIBLE
        else:
            # set-status:<target>: skip terminal descendants.
            eligible = unit.status not in _DECLINE_INELIGIBLE

        if not eligible:
            print(f"SKIP {unit_id}: {current_status} not eligible for {op}")
            continue

        wu_file = _resolve_unit_file(unit)
        if wu_file is None:
            print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
            return 1

        if op == "hold":
            mgr.mark_held(wu_file, BACKLOG_INDEX, unit_id, cascade_reason)
            logger.info("Cascade held %s from %s: %s", unit_id, root_id, reason)
        elif op == "unhold":
            mgr.unmark_held(wu_file, BACKLOG_INDEX, unit_id, cascade_reason)
            logger.info("Cascade unheld %s from %s: %s", unit_id, root_id, reason)
        elif op == "decline":
            mgr.mark_declined(wu_file, BACKLOG_INDEX, unit_id, cascade_reason)
            logger.info("Cascade declined %s from %s: %s", unit_id, root_id, reason)
        else:
            # set-status:<target>
            target_status = op[len("set-status:") :]
            mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, target_status)
            mgr._append_agent_comment(
                wu_file,
                "backlog_manager",
                f"[SET-STATUS:{target_status}] [CASCADE_FROM {root_id}] {reason}",
            )
            logger.info("Cascade set-status %s -> %s from %s", unit_id, target_status, root_id)

    return 0


def cmd_hold(*argv: str) -> int:
    """Mark a work unit as ``hold`` (deferred / under debate).

    Usage::

        hold <id> --reason "<message>" [--cascade]

    ``hold`` is a deferred-decision lifecycle status: the unit stops
    being considered actionable by the orchestrator's ``next`` query
    until an operator runs ``unhold`` to return it to ``in-queue``.
    Unlike ``declined``, ``hold`` is **not** terminal -- a held child
    does NOT count toward a parent's auto-rollup to ``done``. The
    ``--reason`` is REQUIRED so the deferral leaves an audit trail;
    em-dashes are rejected at the input boundary for backlog hygiene.

    When ``--cascade`` is supplied, all eligible descendants of ``<id>``
    are also held in depth-desc order (Tasks first, then Stories, then
    Features, then the root itself).  Descendants already in ``hold``,
    ``done``, or ``declined`` are skipped with a SKIP line on stdout.
    """
    parsed = _parse_id_and_reason_cascade(argv, "hold", reason_required=True)
    if isinstance(parsed, int):
        return parsed
    task_id, reason, cascade = parsed

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    if cascade:
        return cascade_status_mutation(task_id, "hold", reason, units, BacklogManager())

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


def _cmd_unhold_single(task_id: str, reason: str) -> int:
    """Unhold a single work unit by ID.

    Args:
        task_id: The work-unit identifier.
        reason: Human-readable rationale for the release.

    Returns:
        0 on success, 1 on any error.

    Raises:
        Nothing -- all errors reported to stderr and return rc=1.
    """
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


def cmd_unhold(*argv: str) -> int:
    """Return a held work unit to ``in-queue`` with a captured rationale.

    Usage::

        unhold <id> --reason "<message>" [--cascade]

    The unit's status flips from ``hold`` back to ``in-queue`` so the
    orchestrator's ``next``/parallel-candidate scan picks it up again.
    The ``--reason`` is REQUIRED so the release leaves an audit trail;
    em-dashes are rejected at the input boundary. ``unhold`` refuses
    units whose current status is anything other than ``hold`` --
    fail-fast keeps the lifecycle linear.

    When ``--cascade`` is supplied, all descendants currently in ``hold``
    are returned to ``in-queue`` in depth-desc order.  Descendants not
    in ``hold`` are skipped with a SKIP line on stdout.
    """
    parsed = _parse_id_and_reason_cascade(argv, "unhold", reason_required=False)
    if isinstance(parsed, int):
        return parsed
    task_id, reason, cascade = parsed

    # Non-cascade path requires --reason (existing behaviour preserved).
    if not cascade and not reason:
        print("ERROR: unhold requires <id> --reason <message>", file=sys.stderr)
        return 1

    if cascade:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
        return cascade_status_mutation(task_id, "unhold", reason, units, BacklogManager())

    return _cmd_unhold_single(task_id, reason)


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


def _fire_promoted_notification(wu_file: Path, unit_id: str) -> None:
    """Fire the ``work_unit_promoted`` event for a CLI-promoted draft unit.

    Operator-block Slack-gap spec F-proposal / AC-3 / G3: the three CLI promote
    entry points (:func:`_promote_single`, :func:`_promote_bulk`,
    :func:`_promote_all`) flip ``draft -> in-queue`` via ``force_status`` -- a
    different code path from ``promote_proposal`` -- so each needs its own hook
    to fire the (previously dead) ``work_unit_promoted`` event once per unit.

    Best-effort and gated by the per-event toggle + master switch + webhook
    presence inside the notifier; every failure is swallowed so a notification
    bug can never break or delay a promote.

    Args:
        wu_file: Path to the promoted unit's ``.md`` file (title source).
        unit_id: The promoted task identifier.
    """
    try:
        from devbench.backlog.manager import _extract_wu_title
        from devbench.notifications import notify_work_unit_promoted

        title = _extract_wu_title(wu_file, unit_id)
        notify_work_unit_promoted(unit_id, title)
    except (OSError, ValueError, ImportError):
        pass


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
    _fire_promoted_notification(wu_file, unit_id)

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
        _fire_promoted_notification(wu_file, u.id)
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
        _fire_promoted_notification(wu_file, u.id)
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
    - ``--strict`` / ``--include-draft``: Escalate draft/hold manifest conflicts
      from WARNING to ERROR so the command returns a non-zero exit code.
      The strict default is ``False``; the current activation path is this
      CLI flag only. The implementation uses ``getattr`` on the config object
      so that a future ``validate.strict_manifest_conflicts`` YAML key can
      be wired in without changing this call site.

    Exits 0 if the backlog is consistent (or all violations were fixed); 1 with
    actionable error messages if any inconsistencies remain.
    """
    fix = "--fix" in argv
    # Resolve strict mode: CLI flag overrides config default.
    config_strict = getattr(RUNTIME_CONFIG.validate, "strict_manifest_conflicts", False)
    strict = config_strict or ("--strict" in argv) or ("--include-draft" in argv)
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(BACKLOG_INDEX, BACKLOG_INDEX.parent, fix=fix, strict=strict)
    if fix:
        fix_count, files_fixed = mgr._fix_summary
        print(f"Fixed {fix_count} violation(s) across {files_fixed} file(s).")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    if not errors:
        print("Backlog integrity check passed.")
        return 0
    print(f"Backlog integrity check FAILED ({len(errors)} error(s)):")
    for error in errors:
        print(f"  ERROR: {error}")
    return 1


def cmd_reconcile_backlog_md(*argv: str) -> int:
    """Reconcile the Status Summary region in BACKLOG.md against the Full Work Unit Index.

    Flag semantics:

    - No flags: print a mismatch report and return 0 (read-only; no writes).
    - ``--check-only``: return 1 on drift, 0 on no drift, no writes.
    - ``--force``: atomically rewrite the Status Summary region on drift; return 0
      on success, 2 on write error.
    - ``--check-only --force``: ERROR -- mutually exclusive; return 2.
    """
    flags = set(argv)
    check_only = "--check-only" in flags
    force = "--force" in flags

    if check_only and force:
        print("ERROR: --check-only and --force are mutually exclusive", file=sys.stderr)
        return 2

    mgr = BacklogManager()

    if not force:
        # No-flag and --check-only both need a drift report without writing.
        # Delegate to reconcile_backlog_md to detect drift and obtain the
        # reconciled content for reporting (avoids re-implementing the derivation).
        rc, reconciled = mgr.reconcile_backlog_md(WORKSPACE_ROOT, force=False, check_only=check_only)
        if rc == 1:
            # check_only mode: drift detected
            return 1
        if not check_only:
            # No-flag mode: print a mismatch report using the reconciled content
            # returned by the manager; no write occurs.
            content = BACKLOG_INDEX.read_text(encoding="utf-8")
            if reconciled != content:
                print("Status Summary drift detected -- run 'devbench reconcile-backlog-md --force' to fix.")
            else:
                print("Status Summary is consistent with the Full Work Unit Index.")
        return 0

    # --force path
    rc, _reconciled = mgr.reconcile_backlog_md(WORKSPACE_ROOT, force=True, check_only=False)
    if rc == 2:
        print("ERROR: failed to rewrite BACKLOG.md -- check file permissions.", file=sys.stderr)
        return 2
    print("Status Summary reconciled successfully.")
    return 0


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

    # A pending drain is operationally load-bearing context (the orchestrator
    # stops after the current WU) -- surface it in the report exactly as
    # cmd_status does (AC-188-7 parity).
    _render_drain_banner(WORKSPACE_ROOT)

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
                print(cached.report_text)
                return 0
        report = generate_report(
            log_path=log_file,
            since=since_dt,
            scope_filter=scope_filter,
            session_name=session_name,
            by_role=by_role,
        )
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
        return generate_report(
            log_path=log_path,
            since=since_dt,
            report_started_at=report_started_at,
            scope_filter=scope_filter,
            session_name=session_name,
            by_role=by_role,
        )

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


def _render_untracked_hunks(repo_path: Path) -> list[str]:
    """Return synthetic diff hunks for every untracked file the repo reports."""
    hunks: list[str] = []
    rc, stdout, _ = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo_path,
    )
    if rc != 0 or not stdout.strip():
        return hunks

    for raw_filepath in stdout.splitlines():
        filepath = raw_filepath.strip()
        if not filepath:
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


def _get_diff_defer_empty(
    unit_id: str,
    repo_path: "Path",
    no_attributable_exit_code: int,
) -> "tuple[list[str], int]":
    """Look up task-attributed commits when staged+unstaged are empty in defer-PR mode.

    Runs ``git log --grep '^<unit_id>:'`` on the current branch to find commits
    attributed to this task (using the standard ``<task-id>: <summary>`` prefix
    convention). For each matching commit SHA, runs ``git show --format=`` to
    emit the diff. Fails fast if ``git show`` fails on a known SHA.

    When no attributable commit is found, returns exit code ``no_attributable_exit_code``
    (GET_DIFF_NO_ATTRIBUTABLE = 45) with a verbatim diagnostic on stderr.

    Non-defer empty path remains unchanged (rc 0, "(no changes)"); this helper
    is only called in defer-PR mode.

    Args:
        unit_id: Work unit ID (e.g. "E4-F1-S1-T1").
        repo_path: Absolute path to the target repository working tree.
        no_attributable_exit_code: Exit code to return when no commit found (45).

    Returns:
        Tuple of (diff_parts, exit_code). When exit_code is non-zero, diff_parts
        is empty and the caller should return exit_code immediately without printing.
    """
    rc_branch, branch_stdout, _ = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if rc_branch != 0:
        print(
            "WARNING: git rev-parse --abbrev-ref HEAD failed; falling back to HEAD as branch ref",
            file=sys.stderr,
        )
        branch = "HEAD"
    else:
        branch = branch_stdout.strip() or "HEAD"

    grep_pattern = f"^{unit_id}:"
    rc_log, log_stdout, _ = run_command(
        ["git", "log", "--grep", grep_pattern, "--format=%H", branch],
        cwd=repo_path,
    )
    shas = [s.strip() for s in log_stdout.splitlines() if s.strip()] if rc_log == 0 else []

    if not shas:
        print(
            f"ERROR: no task-attributable changes for {unit_id} on {branch}; "
            f'staged+unstaged empty and no commit matches "{grep_pattern}"; '
            f'investigate with: git log --grep "{grep_pattern}" {branch}',
            file=sys.stderr,
        )
        return [], no_attributable_exit_code

    parts: list[str] = []
    for sha in shas:
        rc_show, show_stdout, _ = run_command(["git", "show", "--format=", sha], cwd=repo_path)
        if rc_show != 0:
            print(
                f"ERROR: git show failed for commit {sha} (rc={rc_show}); "
                "repository may be corrupt or the object missing",
                file=sys.stderr,
            )
            return [], rc_show
        if show_stdout.strip():
            parts.append(show_stdout)

    return parts, 0


def cmd_get_diff(unit_id: str) -> int:
    """Return the combined git diff for the work unit's target repo.

    Mode-aware per ADR-12. In the default per-task-branch mode, emits
    staged + unstaged + branch-vs-default + untracked hunks. In defer_pr
    mode (single_branch + defer_pr: true), the branch-vs-default hunk is
    omitted because it accumulates every prior task's commits on the
    shared branch; instead the function emits staged + unstaged + untracked.

    When staged, unstaged, and untracked are all empty in defer-PR mode,
    performs a task-attributed commit lookup via ``git log --grep '^<unit_id>:'``
    and emits those diffs. If no attributable commit exists, exits with
    GET_DIFF_NO_ATTRIBUTABLE (45) and a verbatim diagnostic to stderr.

    Non-defer empty still prints "(no changes)" with rc 0 (unchanged).

    Used by plugin agents instead of running raw git commands so they do
    not need to know the repo path or the mode.
    """
    from devbench.config import DEFER_PR
    from devbench.constants import GET_DIFF_NO_ATTRIBUTABLE

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

    parts: list[str] = []

    rc, stdout, _ = run_command(["git", "diff", "--cached"], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    rc, stdout, _ = run_command(["git", "diff"], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    parts.extend(_render_untracked_hunks(repo_path))

    if DEFER_PR:
        if not parts:
            attributed, attr_rc = _get_diff_defer_empty(unit_id, repo_path, GET_DIFF_NO_ATTRIBUTABLE)
            if attr_rc != 0:
                return attr_rc
            parts.extend(attributed)
    else:
        default_branch = _resolve_default_branch(canonical_repo, repo_path)
        if default_branch is None:
            print(
                f"ERROR: Cannot determine default branch for '{canonical_repo}'. "
                "Run 'git remote set-head origin --auto' to configure it.",
                file=sys.stderr,
            )
            return 1
        rc, stdout, _ = run_command(["git", "diff", f"origin/{default_branch}"], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

    print("\n".join(parts) if parts else "(no changes)")
    return 0


def cmd_run_tests(unit_id: str) -> int:
    """Run the test suite for the work unit's target repo and return the output.

    Uses ``make test`` when the repo has a Makefile with a ``test`` target,
    otherwise falls back to ``pytest``.  Exits non-zero if the test run fails.

    Used by the test-reviewer agent to obtain test execution evidence.
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

    rc, stdout, _ = run_command(["make", "-n", "test"], cwd=repo_path)
    cmd = ["make", "test"] if rc == 0 else ["pytest", "--no-header", "-q", "-p", "no:cacheprovider"]

    rc, stdout, stderr = run_command(cmd, cwd=repo_path, timeout=TEST_TIMEOUT)
    combined = "\n".join(part for part in (stdout, stderr) if part.strip())
    print(combined if combined else "(no output)")
    return rc


def _format_long_op_heartbeat(*, verb: str, unit: str, elapsed_seconds: int) -> str:
    """Build the benign long-op heartbeat log line (design point 4, mechanism a).

    The line is prefixed with :data:`SUPERVISE_LONG_OP_HEARTBEAT_MARKER`, which
    matches NO ``supervise.log_tail`` marker family (clean/quota/fault/restart), so
    the :class:`~devbench.supervise.LogTailDetector` never misclassifies it -- it
    only needs to GROW the orchestrator log so the progress watchdog's byte offset
    advances during a genuinely-quiet long op (terraform apply / go test).
    """
    from devbench.constants import SUPERVISE_LONG_OP_HEARTBEAT_MARKER

    return f"{SUPERVISE_LONG_OP_HEARTBEAT_MARKER} verb={verb} unit={unit} elapsed={elapsed_seconds}s"


def run_with_long_op_heartbeat(
    *,
    run: "Callable[[], Any]",
    heartbeat_interval_seconds: int,
    emit_heartbeat: "Callable[..., None]",
) -> Any:
    """Run a BLOCKING op while emitting periodic heartbeats (design point 4).

    The in-session ``verify-ac`` runner blocks in ``subprocess.run`` for the whole
    of a 30-60 min terraform apply / go test with ZERO orchestrator-log output.
    This wrapper runs *run* on the calling thread and, on a separate daemon thread,
    invokes *emit_heartbeat(elapsed=<seconds>)* every *heartbeat_interval_seconds*
    until *run* returns (or raises), so the progress watchdog's log-growth signal
    keeps advancing and a healthy long op is NOT classified as a stall.

    The cadence is driven by an interruptible :class:`threading.Event` wait (NOT a
    fixed ``time.sleep``): the heartbeat thread wakes IMMEDIATELY when the op
    completes (the event is set in the ``finally``), so no heartbeat is emitted
    after the op returns and the thread is always joined (no leaked daemon, even on
    an exception). *heartbeat_interval_seconds* MUST be strictly less than the
    supervisor's ``progress_stall_seconds`` so the log grows before the watchdog
    window elapses (validated in config + documented).

    Args:
        run: The zero-arg blocking callable to execute; its return value is
            passed through unchanged.
        heartbeat_interval_seconds: Seconds between heartbeats (the cadence).
        emit_heartbeat: Invoked as ``emit_heartbeat(elapsed=<int seconds>)`` on
            each beat (production writes the heartbeat line to the orchestrator
            logger; tests capture the calls).

    Returns:
        Whatever *run* returns.
    """
    stop = threading.Event()
    started = time.monotonic()

    def _beat() -> None:
        # Event-driven cadence: wait returns True the instant the op finishes
        # (stop.set in the finally) -> loop exits; False on the interval timeout
        # -> emit one beat. No fixed sleep, no busy-wait (CLAUDE.md Section 7.5).
        while not stop.wait(heartbeat_interval_seconds):
            emit_heartbeat(elapsed=int(time.monotonic() - started))

    thread = threading.Thread(target=_beat, name="devbench-long-op-heartbeat", daemon=True)
    thread.start()
    try:
        return run()
    finally:
        stop.set()
        thread.join()


def _run_verification_item(
    item: "verification.VerificationItem",
    repo_path: Path,
    workspace_root: Path,
    task_id: str,
    attempt: int,
    *,
    log_bytes: int,
    timeout: int,
    pin_randomly: bool,
) -> "verification.EvidenceRecord":
    """Execute one executable VERIFY directive and capture its REAL exit code.

    Runs ``item.command`` via ``bash -c`` in the target repo working dir (the
    command grammar legitimately contains ``$VARS`` and pipes, so a shell is
    required), trims the combined stdout/stderr to *log_bytes*, writes the
    trimmed text to a per-AC artifact, and returns an
    :class:`verification.EvidenceRecord` carrying the tool-captured exit code.

    The command runs with a DETERMINISTIC environment overlay
    (:func:`verification.deterministic_gate_env`): ``PYTHONHASHSEED`` is always
    pinned, and (when *pin_randomly* is True -- i.e. the target repo has
    ``pytest-randomly`` installed) a fixed ``--randomly-seed`` is pinned via
    ``PYTEST_ADDOPTS`` on top of the inherited process environment so the
    per-unit gate's verdict is reproducible run-to-run. Without this, a target
    repo using ``pytest-randomly`` (random order per run) can pass an
    order-dependent sibling test on one wall-clock seed and fail it on the next,
    non-deterministically blocking an otherwise complete, unrelated unit. The
    inherited environment is preserved (PATH etc. still resolve the toolchain);
    only the ordering knobs are overlaid. When the plugin is absent
    (*pin_randomly* False) the ``--randomly-seed`` option is NOT injected so a
    repo without the plugin never errors on an unknown pytest option.

    The exit code is never self-reported: it comes straight from the subprocess.
    An executable directive with no ``cmd=`` is recorded as a hard failure (exit
    code ``SUBPROCESS_ERROR_EXIT_CODE``) rather than silently passing an empty
    shell -- a missing command can never be proof of a passing AC.

    DEFERRED (not implemented): an automatic flaky-vs-real discriminator that,
    on failure, re-runs the failing tests in isolation and reclassifies them as
    a pre-existing order-dependent flake when they pass alone. With the
    deterministic seed (here) and the spec-to-backlog own-test gate scoping in
    place, the per-unit non-determinism is already eliminated; an
    auto-reclassifier was judged too invasive for this critical path (it risks
    masking a genuine failure as non-attributable, contradicting fail-fast). See
    ``docs/acceptance-criteria-canonical.md`` "Per-unit gate vs. epic-capstone".
    """
    from devbench.config import VERIFY_AC_PYTEST_SEED
    from devbench.constants import SUBPROCESS_ERROR_EXIT_CODE

    started_at = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    command = item.command or ""
    if not command.strip():
        rc, stdout, stderr = (
            SUBPROCESS_ERROR_EXIT_CODE,
            "",
            f"VERIFY {', '.join(item.ac_ids)} is executable (type={item.vtype.value}) but declares no cmd=`...`.",
        )
    else:
        gate_env = verification.deterministic_gate_env(
            dict(os.environ), seed=VERIFY_AC_PYTEST_SEED, pin_randomly=pin_randomly
        )
        # The command can be a 30-60 min terraform apply / go test that produces
        # NO orchestrator-log output while it blocks. Wrap it in the long-op
        # heartbeat (design point 4): a benign [LONG_OP_HEARTBEAT] line is written
        # to THIS process's logger (which IS the orchestrator log the supervise
        # progress watchdog tails) on the configured cadence, so a healthy long op
        # keeps the watchdog's log-growth signal advancing and is never false-stalled.
        heartbeat_interval = RUNTIME_CONFIG.supervise.timeouts.long_op_heartbeat_seconds

        def _emit_heartbeat(*, elapsed: int) -> None:
            logger.info(_format_long_op_heartbeat(verb="verify-ac", unit=task_id, elapsed_seconds=elapsed))

        # Launch the live command in its OWN process group and register that
        # pgid for the active session, so a [CLAIM_NOT_CONVERGING] block can tear
        # down exactly this command's subtree (e.g. a live ``terraform apply`` /
        # ``go test``) instead of orphaning it to init (Item B, tracked issue
        # 015). The registration is cleared the instant the command terminates.
        rc, stdout, stderr = run_with_long_op_heartbeat(
            run=lambda: run_command_in_process_group(
                ["bash", "-c", command],
                cwd=repo_path,
                timeout=timeout,
                env=gate_env,
                on_pgid=_register_executor_pgid,
                on_complete=_clear_executor_pgid,
            ),
            heartbeat_interval_seconds=heartbeat_interval,
            emit_heartbeat=_emit_heartbeat,
        )
    finished_at = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)

    combined = "\n".join(part for part in (stdout, stderr) if part.strip())
    trimmed = verification.trim_log(combined, log_bytes)

    attempt_dir = verification.evidence_attempt_dir(workspace_root, task_id, attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = attempt_dir / f"{verification.sanitize_ac_label(item.ac_ids)}.log"
    atomic_write_text(artifact_path, trimmed if trimmed else "(no output)\n")

    summary = (
        f"{', '.join(item.ac_ids)} via `{command}` exited {rc} (expected {item.expect_exit}); log at {artifact_path}."
    )
    return verification.EvidenceRecord(
        ac_ids=list(item.ac_ids),
        vtype=item.vtype.value,
        command=command,
        exit_code=rc,
        tool=item.tool,
        started_at=started_at,
        finished_at=finished_at,
        artifact=str(artifact_path),
        summary=summary,
    )


def _resolve_unit_file_and_repo_path(unit_id: str) -> tuple[Path, Path] | None:
    """Return ``(work_unit_path, repo_path)`` for *unit_id*, or ``None`` on error.

    Looks up the unit in the backlog index, resolves its on-disk ``.md`` file and
    its configured target-repo working directory. Prints an actionable error to
    stderr and returns ``None`` when any step fails, so the caller can ``return 1``.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    unit = _find_unit(parser.parse_index(), unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return None

    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return None

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return None
    return wu_file, repo_path


def cmd_verify_ac(unit_id: str) -> int:
    """Execute the work unit's ``## Verification`` contract and record evidence.

    Resolves the unit's target repo (via ``REPO_LOCAL_PATHS``), parses the
    ``## Verification`` section, and for every *executable* directive (skipping
    ``judge`` and ``deferred`` items) runs the command in the repo working dir,
    capturing the REAL tool exit code -- never a self-reported one. Each result
    is trimmed and written to ``.devbench/evidence/<id>/<attempt>/<ac>.log`` and
    aggregated into ``.devbench/evidence/<id>/<attempt>/evidence.json`` (the
    ledger the done-gate and the ``iac_review`` judge load).

    Also runs the deterministic TDD genuine-RED gate, preferring the
    tool-captured RED exit code from the freshly written evidence ledger over
    the executor's self-reported ``log-tdd RED`` value.

    Exits ``0`` when every executable item met its ``expect-exit`` and the TDD
    gate passed; non-zero otherwise (so the orchestrator blocks before review).
    """
    from devbench.config import CI_FAILURE_LOG_BYTES, TEST_TIMEOUT

    resolved = _resolve_unit_file_and_repo_path(unit_id)
    if resolved is None:
        return 1
    wu_file, repo_path = resolved

    content = wu_file.read_text(encoding="utf-8")
    try:
        items = verification.parse_verification_section(content)
    except ValueError as exc:
        print(f"ERROR: malformed '## Verification' directive in {unit_id}: {exc}", file=sys.stderr)
        return 1

    executable = verification.executable_items(items)
    attempt = verification.next_attempt_number(WORKSPACE_ROOT, unit_id)

    # Probe the target repo ONCE: only pin pytest-randomly's seed flag when the
    # plugin is actually installed there (otherwise pytest would error on the
    # unknown ``--randomly-seed`` option). ``PYTHONHASHSEED`` is pinned either
    # way. Skip the probe entirely when there is nothing executable to run.
    pin_randomly = bool(executable) and verification.pytest_randomly_available(repo_path, run_command)

    records: list[verification.EvidenceRecord] = []
    for item in executable:
        records.append(
            _run_verification_item(
                item,
                repo_path,
                WORKSPACE_ROOT,
                unit_id,
                attempt,
                log_bytes=CI_FAILURE_LOG_BYTES,
                timeout=item.timeout if item.timeout is not None else TEST_TIMEOUT,
                pin_randomly=pin_randomly,
            )
        )

    ledger_path = verification.write_evidence_ledger(WORKSPACE_ROOT, unit_id, attempt, records)

    failures = [r for r, item in zip(records, executable, strict=True) if r.exit_code != item.expect_exit]
    tdd_failed = _run_tdd_gate_with_evidence(content, repo_path, records)

    print(
        json.dumps(
            {
                "unit_id": unit_id,
                "attempt": attempt,
                "ledger": str(ledger_path),
                "executable_items": len(executable),
                "failures": [r.summary for r in failures],
                "records": [r.to_dict() for r in records],
            },
            indent=2,
        )
    )

    if failures:
        print(
            f"ERROR: {len(failures)} executable Acceptance Criterion verification(s) did not "
            f"meet expect-exit for {unit_id}; see {ledger_path}.",
            file=sys.stderr,
        )
        return 1
    if tdd_failed is not None:
        print(f"ERROR: TDD genuine-RED gate rejected {unit_id}: {tdd_failed}", file=sys.stderr)
        return 1
    return 0


def _run_tdd_gate_with_evidence(
    content: str,
    repo_path: Path,
    records: list["verification.EvidenceRecord"],
) -> str | None:
    """Invoke the deterministic TDD genuine-RED gate from a Python code path.

    The gate (``tdd_gate.check_tdd_gate``) was previously dead code with no
    Python caller and read a self-reported RED exit code. This wires it into
    ``verify-ac``: when a ``red`` evidence record carries a tool-captured exit
    code, that code is spliced into the work-unit content (replacing the
    self-reported ``log-tdd RED`` ``Exit:`` token the gate reads) so the gate
    judges genuine RED on tool-captured proof. Falls back to the work unit's
    recorded RED exit code when no tool-captured RED evidence exists.

    Returns the gate's rejection message when it rejects, or ``None`` on pass
    (including when the unit logged no RED entry at all -- the gate is a no-op
    there, preserving back-compat for units without a TDD cycle).
    """
    from devbench import tdd_gate

    if tdd_gate.extract_red_exit_code(content) is None:
        return None  # no RED entry recorded -- gate not applicable

    gate_content = content
    captured = _tool_captured_red_exit(records)
    if captured is not None:
        gate_content = _splice_red_exit_code(content, captured)

    rc, diff_stdout, _ = run_command(["git", "diff", "HEAD"], cwd=repo_path)
    diff_output = diff_stdout if rc == 0 else ""
    result = tdd_gate.check_tdd_gate(gate_content, diff_output)
    return None if result.passed else result.message


def _tool_captured_red_exit(records: list["verification.EvidenceRecord"]) -> int | None:
    """Return the exit code of the last RED-type evidence record, or ``None``.

    A verification item authored as ``type=command`` whose AC ids include a
    ``red`` marker, or a directive explicitly tagged ``tool=red``, is treated as
    the genuine-RED proof. We accept any record whose ``tool`` equals ``red`` so
    authors can mark the RED command without a new VerificationType.
    """
    captured: int | None = None
    for rec in records:
        if rec.tool == "red":
            captured = rec.exit_code
    return captured


_RED_EXIT_TOKEN_RE: re.Pattern[str] = re.compile(r"(\[RED\][^\n]*?\bExit:\s*)(\d+)")


def _splice_red_exit_code(content: str, exit_code: int) -> str:
    """Replace the ``Exit: <n>`` token in the last RED log entry with *exit_code*.

    Keeps the rest of the work-unit content byte-identical so the TDD gate's
    other checks (production-file presence, Task Type exemption) operate on the
    real file. Only the trailing RED entry's exit token is rewritten.
    """
    matches = list(_RED_EXIT_TOKEN_RE.finditer(content))
    if not matches:
        return content
    last = matches[-1]
    return content[: last.start(2)] + str(exit_code) + content[last.end(2) :]


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


def cmd_log_verdict(judge_name: str, unit_id: str, verdict: str, feedback: str = "") -> int:
    """Append a judge verdict to the work unit's Comments section and log feedback.

    Arguments:
        judge_name:  Judge identifier, e.g. ``code_review`` (matches REVIEW_JUDGE_NAMES).
        unit_id:     Work unit ID, e.g. ``E0-F1-S1-T1``.
        verdict:     ``pass`` or ``fail``.
        feedback:    One-line summary of the verdict (required for ``fail``).

    The entry written to the work unit uses the same format as the orchestrator:
    ``[judge/<name>] [REVIEW_PASS|REVIEW_FAIL] <feedback>``

    Feedback is also written to the orchestrator log for audit purposes.
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

    rc = _reject_em_dash("feedback", feedback)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    action = "REVIEW_PASS" if verdict_lower == "pass" else "REVIEW_FAIL"
    agent_id = f"judge/{judge_name}"
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
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

    print(json.dumps({"unit_id": unit_id, "judge": judge_name, "verdict": verdict_lower}))
    return 0


def cmd_log_comment(agent_name: str, unit_id: str, message: str) -> int:
    """Append a non-verdict agent comment to the work unit's Comments section.

    Writes: ``[YYYY-MM-DD HH:MM UTC] [agent/<name>] <message>``

    Use for non-judge actors (executor, blocker-resolver, review-supervisor summary)
    that need to log progress without emitting a REVIEW_PASS/REVIEW_FAIL token.
    """
    rc = _reject_em_dash("message", message)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
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
        message:  Description of the TDD phase outcome.

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

    rc = _reject_em_dash("message", message)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    mgr = BacklogManager()
    try:
        mgr._append_tdd_entry(wu_file, phase_upper, message)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    logger.info("TDD %s entry logged for %s", phase_upper, unit_id)
    print(json.dumps({"unit_id": unit_id, "phase": phase_upper}))
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

    branch = SINGLE_BRANCH if SINGLE_BRANCH else f"backlog/{unit_id.lower()}"
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


def _committable_manifest_paths(manifest_rows: list) -> list[str]:
    """Return real file paths from *manifest_rows*, excluding sentinel rows.

    Sentinel Manifest values (``<verification-only>``, ``<decision-only>``,
    ``<no changes>``, etc.) document a unit's intent and are never real file
    paths. They must be excluded before the paths are handed to ``git add``
    (which fails with exit 128 on a non-existent pathspec) or to
    ``assert_staged_matches_manifest``. This mirrors how the validator already
    exempts sentinel values from path-based Manifest rules
    (``devbench.backlog.sentinels.is_sentinel_manifest_value``), so a
    verification-only unit -- including one amended to add real files -- can
    reach the commit step without the sentinel row poisoning ``git add``.
    """
    from devbench.backlog.sentinels import is_sentinel_manifest_value

    return [row.file for row in manifest_rows if not is_sentinel_manifest_value(row.file)]


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
    manifest_paths: list[str] | None = None
    if wu_file is not None:
        manifest_rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
        manifest_paths = _committable_manifest_paths(manifest_rows)
        assert_staged_matches_manifest(repo_path, manifest_paths)

    ops.commit_local(canonical_repo, repo_path, branch, commit_message, manifest_paths)
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
) -> tuple[bool, object]:
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
            len(report.removed),  # type: ignore[attr-defined]
            "updated" if report.gitignore_updated else "unchanged",  # type: ignore[attr-defined]
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

    # Proposals are materialised at ``proposed`` (see materialise_proposal).
    # Promotion to ``in-queue`` is gated by ``task_factory.auto_accept_proposals``:
    # when true the cleanup task is promoted immediately so the orchestrator can
    # claim it; when false it is left at ``proposed`` for operator review.
    auto_accept_proposals: bool = RUNTIME_CONFIG.task_factory.auto_accept_proposals

    try:
        write_proposal(WORKSPACE_ROOT, proposal)
        materialised_files = materialise_proposal(
            workspace_root=WORKSPACE_ROOT,
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            proposal=proposal,
            repo=unit.repo,
        )
        if auto_accept_proposals:
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
        f"Auto-emitted cleanup proposal {new_id} "
        f"({STATUS_IN_QUEUE if auto_accept_proposals else STATUS_PROPOSED}); "
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
    ops: object,
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

    run_id = ops.get_latest_failing_run_id(  # type: ignore[attr-defined]
        canonical_repo, pr_number, repo_path=repo_path
    )
    if run_id is None:
        return None, summary_default

    log_text = ops.fetch_run_log(  # type: ignore[attr-defined]
        canonical_repo, run_id, CI_FAILURE_LOG_BYTES, repo_path=repo_path
    )
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
    ops: object,
    unit_id: str,
    canonical_repo: str,
    pr_number: int,
    repo_path: Path,
    wu_file: Path | None,
    mgr: object,
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
        mgr._append_agent_comment(wu_file, "git_ops", message)  # type: ignore[attr-defined]
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
    ops: object,
    unit_id: str,
    canonical_repo: str,
    pr_number: int,
    repo_path: Path,
    wu_file: Path | None,
    mgr: object,
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

    resolution = ops.poll_pr_review_resolution(  # type: ignore[attr-defined]
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
        mgr._append_agent_comment(wu_file, "git_ops", f"{marker} {summary}")  # type: ignore[attr-defined]

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

    branch = SINGLE_BRANCH if SINGLE_BRANCH else f"backlog/{unit_id.lower()}"

    if DEFER_PR:
        return _git_ops_deferred(unit_id, unit, canonical_repo, repo_path, branch)

    # Standard mode: commit, push, PR, CI, merge.
    commit_message = f"{unit_id}: {unit.title}"
    pr_title = f"{unit_id}: {unit.title}"
    pr_body = f"Automated PR for work unit {unit_id}.\n\n{unit.title}"

    ops = GitOpsService()

    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        logger.warning("Could not resolve work unit file for %s -- audit comments will be skipped", unit_id)

    mgr = BacklogManager()

    from devbench.backlog.manifest import assert_staged_matches_manifest, parse_manifest

    # Orphan-pattern check (see _git_ops_deferred for rationale): refuse
    # the commit on detection, auto-emit cleanup proposal, return non-zero.
    if _emit_orphan_cleanup_proposal_if_needed(unit_id, unit, repo_path):
        return 1

    # Manifest-scope check: every staged path must be in the work unit's
    # Changes Manifest. Catches scope-violation pollution deterministically
    # before commit. Skipped only when the work-unit file isn't resolvable.
    manifest_paths: list[str] | None = None
    if wu_file is not None:
        manifest_rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
        manifest_paths = _committable_manifest_paths(manifest_rows)
        assert_staged_matches_manifest(repo_path, manifest_paths)

    ops.commit_and_push(canonical_repo, repo_path, branch, commit_message, manifest_paths)
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
    ops: object,
    unit_id: str,
    canonical_repo: str,
    repo_path: Path,
    branch: str,
    pr_number: int,
    pr_url: str,
    wu_file: Path | None,
    mgr: object,
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
    mgr: object,
) -> int:
    """Issue #101: transition to in-review, do NOT merge.

    The orchestrator's loop reconciles in-review tasks via
    :func:`cmd_check_merge` on the next iteration. The PR remains open
    on GitHub awaiting human review + merge; the orchestrator continues
    with other actionable work units.

    Returns 0 -- the work unit moved successfully to in-review.
    """
    if wu_file is not None:
        mgr.force_status(  # type: ignore[attr-defined]
            wu_file,
            BACKLOG_INDEX,
            unit_id,
            STATUS_IN_REVIEW,
        )
        mgr._append_agent_comment(  # type: ignore[attr-defined]
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
    ops: object,
    canonical_repo: str,
    unit_id: str,
    branch: str,
) -> tuple[int, list[dict[str, object]]]:
    """Query gh for a PR matching *branch*; return (rc, pr_records).

    rc=1 + empty list means the gh call failed; the caller short-circuits
    with rc=1. rc=0 + empty list means no PR found for the branch (caller
    treats as still-in-review with `pr_state=no-pr-found`).
    """
    rc, stdout, stderr = ops._gh(  # type: ignore[attr-defined]
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
    mgr: object,
) -> int:
    """Promote a merged-PR work unit to done via the done-gate."""
    if wu_file is not None:
        try:
            mgr.mark_done(wu_file, BACKLOG_INDEX, unit_id)  # type: ignore[attr-defined]
            mgr._append_agent_comment(  # type: ignore[attr-defined]
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

    - **merged** (``mergedAt`` non-null) -> transition to ``done`` via :meth:`BacklogManager.mark_done`
      (enforces the done-gate: every required judge must have passed).
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

    branch = unit.branch or f"backlog/{unit_id.lower()}"
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
    ops: object,
    unit_id: str,
    canonical_repo: str,
    repo_path: Path,
    branch: str,
    pr_number: int,
    pr_url: str,
    wu_file: Path | None,
    mgr: object,
) -> int:
    """Merge the PR (with one CONFLICTING-state retry) and update the parent submodule.

    Extracted from :func:`cmd_git_ops` so the parent function stays under
    the project's per-function return-statement budget (PLR0911).
    Returns 0 on success or 1 when the merge retry path itself fails.
    """
    from devbench.github.git_ops import ConflictingPRError

    try:
        ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)  # type: ignore[attr-defined]
    except ConflictingPRError:
        logger.warning(
            "PR #%d on %s is CONFLICTING -- rebasing and retrying merge once",
            pr_number,
            canonical_repo,
        )
        ops.rebase_and_force_push(canonical_repo, repo_path, branch)  # type: ignore[attr-defined]
        try:
            ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)  # type: ignore[attr-defined]
        except Exception as retry_exc:
            print(
                f"ERROR: Merge retry failed for PR #{pr_number} on {canonical_repo}: {retry_exc}",
                file=sys.stderr,
            )
            return 1

    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[PR_MERGED] {pr_url}")  # type: ignore[attr-defined]
    from devbench.notifications import notify_pr_merged

    notify_pr_merged(unit_id, canonical_repo, pr_url)

    logger.info("Merged PR #%d for %s", pr_number, unit_id)

    ops.checkout_default_branch(canonical_repo, repo_path)  # type: ignore[attr-defined]
    logger.info("Checked out default branch after merge for %s", unit_id)

    if UPDATE_SUBMODULE:
        ops.update_parent_submodule_ref(  # type: ignore[attr-defined]
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


def cmd_git_ops_finalize(repo_name: str) -> int:
    """Push the single branch and create a PR after all deferred commits.

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

    Arguments:
        repo_name: Repository name (short or fully-qualified).
    """
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

    repo_cfg = RUNTIME_CONFIG.repos.get(canonical_repo)
    if repo_cfg is not None and repo_cfg.local_only:
        logger.info(AUTO_FINALIZE_SKIPPED_LOCAL_ONLY)
        return 0

    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    branch = SINGLE_BRANCH
    pr_title = FINALIZE_PR_TITLE_TEMPLATE.format(branch=branch)
    pr_body = (
        f"Accumulated commits from DevBench single-branch execution.\n\nBranch: `{branch}`\nRepo: `{canonical_repo}`"
    )

    ops = GitOpsService()
    mgr = BacklogManager()

    ops.commit_and_push(canonical_repo, repo_path, branch, FINALIZE_COMMIT_TEMPLATE.format(branch=branch))
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


def _devbench_repo_root() -> Path:
    """Return the devbench package checkout root (the dir holding ``src/devbench``).

    Resolved generically from this module's location: ``cli.py`` lives at
    ``<root>/src/devbench/cli.py``, so the root is two parents up from the
    package dir. No hardcoded path. Used by the startup harness-integrity check
    so it inspects the SAME checkout the orchestrator runs from.
    """
    return Path(__file__).resolve().parent.parent.parent


#: Deterministic audit marker emitted by the startup harness-integrity check.
HARNESS_INTEGRITY_MARKER: str = "[HARNESS_INTEGRITY]"


def _check_harness_integrity(mode: str) -> int | None:
    """Detect uncommitted edits under the devbench package source at startup.

    ``mode`` is ``orchestrate.harness_integrity_check`` -- ``"off"`` (skip),
    ``"warn"`` (the default: emit a loud ``[HARNESS_INTEGRITY]`` warning and
    continue), or ``"fail"`` (refuse to start).

    Returns a non-zero exit code only when ``mode == "fail"`` AND uncommitted
    edits are found under ``src/devbench/`` in the devbench checkout. Returns
    ``None`` in every other case (disabled, clean, warn-only, or a non-git
    checkout where the concept does not apply -- the check degrades gracefully
    and never blocks on its own infrastructure failure).
    """
    if mode == "off":
        return None

    root = _devbench_repo_root()
    pkg_rel = "src/devbench"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", pkg_rel],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # Not a git checkout / git unavailable: cannot have tracked uncommitted
        # edits. Degrade gracefully -- never block on our own tooling failure.
        return None
    if result.returncode != 0:
        return None
    dirty = [line for line in result.stdout.splitlines() if line.strip()]
    if not dirty:
        return None

    paths = ", ".join(line[3:].strip() for line in dirty)
    print(
        f"{HARNESS_INTEGRITY_MARKER} uncommitted edits detected under the devbench package "
        f"source ({pkg_rel}/) in the checkout the orchestrator runs from ({root}): {paths}. "
        "This is the signature of a prior orchestrate self-edit or an unreviewed manual change. "
        "The orchestrate session is forbidden from editing the harness (guard-harness-write.sh); "
        "review and commit or revert these edits before running.",
        file=sys.stderr,
    )
    if mode == "fail":
        print(
            f"{HARNESS_INTEGRITY_MARKER} orchestrate.harness_integrity_check=fail; refusing to start.",
            file=sys.stderr,
        )
        return 1
    return None


def _check_orchestrator_startup_gates(plugin_path: Path) -> int | None:
    """Run the pre-SDK startup gates; return a non-zero exit code on the first
    blocking failure, else ``None``.

    Gate order:

    1. Guard hooks registered (H4, spec AC-H4-1): fail closed when the plugin's
       PreToolUse guard hooks are not loaded.
    2. Harness integrity (``orchestrate.harness_integrity_check``, default
       ``warn``): warn or fail fast on uncommitted edits under the devbench
       package source -- the signature of a prior orchestrate self-edit. Pairs
       with the ``guard-harness-write.sh`` hook that hard-denies the session
       from editing the harness in the first place.
    3. Target-env pre-sync (TDI #016, ``orchestrate.presync_environment``,
       default on): warm each configured repo's dependency environment ONCE
       before the orchestrate loop claims any work, so no claim pays the
       cold-dependency-sync cost inside a timed test attempt (which would
       otherwise be misread as a test failure and trip the within-claim
       convergence bound). Fail-fast on a real provisioning failure.
    """
    if (hook_check_rc := _check_guard_hooks_registered(plugin_path)) is not None:
        return hook_check_rc
    if (integrity_rc := _check_harness_integrity(RUNTIME_CONFIG.orchestrate.harness_integrity_check)) is not None:
        return integrity_rc
    if (presync_rc := _run_presync_if_enabled()) is not None:
        return presync_rc
    return None


class _OrchestratorModelUnsetError(RuntimeError):
    """Raised by :func:`_resolve_orchestrator_model` when no model is configured."""


def _resolve_orchestrator_model() -> str:
    """Return the orchestrate-session model from ``orchestrate.model`` in devbench.yaml.

    This is the SINGLE source of truth for the model the SDK-launched
    orchestrator runs on (``devbench start`` / ``--daemon``). The value is
    passed into ``ClaudeAgentOptions(model=...)`` so the session can NEVER
    inherit the interactive Claude Code ``~/.claude/settings.json`` model. Per
    CLAUDE.md there is NO fallback (not to ``DEVBENCH_CLAUDE_MODEL``, not to the
    CLI settings): when ``orchestrate.model`` is unset the orchestrator-launch
    path fails fast.

    Returns:
        The configured non-empty orchestrate model string.

    Raises:
        _OrchestratorModelUnsetError: When ``orchestrate.model`` is absent/empty.
    """
    model = RUNTIME_CONFIG.orchestrate.model
    if not model or not model.strip():
        raise _OrchestratorModelUnsetError(
            "orchestrate.model is not set in backlog/config/devbench.yaml. "
            "The SDK-launched orchestrator requires an explicit model so it never "
            "inherits the interactive Claude Code (~/.claude/settings.json) selection. "
            "Set e.g.:\n\norchestrate:\n  model: claude-opus-4-8\n\n"
            "and restart. (This does not affect the interactive "
            "/devbench-orchestrate:orchestrate slash command, which runs on your "
            "host session's selected model.)"
        )
    return model.strip()


#: Fail-closed error message emitted by ``cmd_start`` when guard hooks are absent.
#: Verbatim string required by spec AC-H4-1 (E8.F4.S1).
_GUARD_HOOKS_ABSENT_ERROR: str = (
    "ERROR: devbench guard hooks not loaded; refusing to run"
    " (done-integrity cannot be enforced)."
    " Launch via the devbench-orchestrate plugin."
)


def _check_guard_hooks_registered(plugin_path: Path) -> int | None:
    """Check that the guard hooks are registered in the resolved plugin path.

    The guard hooks are registered when the plugin's ``hooks/hooks.json`` file
    exists.  This file is the mechanism by which the ``devbench-orchestrate``
    plugin registers its PreToolUse/PostToolUse guard scripts with Claude Code.
    When it is absent, no guard scripts will run during the SDK session --
    done-integrity cannot be enforced at the hook layer (though the library-level
    gates in ``force_status`` and ``mark_done`` still hold regardless).

    This is a startup pre-flight check (spec AC-H4-1, E8.F4.S1).  The check is
    intentionally strict: if the hooks file is missing, refuse to start rather
    than run unguarded.

    Args:
        plugin_path: The resolved plugin directory path (canonical or shadow).

    Returns:
        ``1`` when ``hooks/hooks.json`` is absent (after printing the verbatim
        fail-closed error to stderr), or ``None`` when the hooks are present.
    """
    hooks_json = plugin_path / "hooks" / "hooks.json"
    if not hooks_json.exists():
        print(_GUARD_HOOKS_ABSENT_ERROR, file=sys.stderr)
        return 1
    return None


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

#: Audit marker emitted when quota wait begins (AC-236-1, Appendix A QW-7).
#: Format: ``[QUOTA_WAITING] reason=<r> reset_at=<ISO|unknown>``
_QUOTA_WAITING_AUDIT_PREFIX: str = "[QUOTA_WAITING]"

#: Audit marker emitted when quota wait ends in recovery (AC-236-1, Appendix A QW-7).
#: Format: ``[QUOTA_RESUMED] waited_seconds=<int>``
_QUOTA_RESUMED_AUDIT_PREFIX: str = "[QUOTA_RESUMED]"

#: Audit marker emitted when the recovery probe is permanently unavailable
#: (no/invalid API credential) and no provider-supplied reset time is known, so
#: the orchestrator stops the wait immediately instead of polling a probe that
#: can never succeed.  Format: ``[QUOTA_PROBE_UNAVAILABLE] reason=<r> detail=<msg>``
_QUOTA_PROBE_UNAVAILABLE_AUDIT_PREFIX: str = "[QUOTA_PROBE_UNAVAILABLE]"

#: Audit marker emitted when ``on_exhaustion=fail`` (detection) or
#: ``on_exhaustion_timeout=fail`` (timeout) aborts the run by re-raising the
#: quota error for a non-zero exit.  Format: ``[QUOTA_FAIL_FAST] reason=<source>``
_QUOTA_FAIL_FAST_AUDIT_PREFIX: str = "[QUOTA_FAIL_FAST]"

#: Audit marker emitted when ``on_exhaustion=drain`` (detection) or
#: ``on_exhaustion_timeout=drain`` (timeout) requests a graceful drain instead
#: of waiting / instead of failing.
#: Format: ``[QUOTA_DRAIN_REQUESTED] reason=<source> phase=<detection|timeout>``
_QUOTA_DRAIN_REQUESTED_AUDIT_PREFIX: str = "[QUOTA_DRAIN_REQUESTED]"

#: Audit marker emitted when ``on_exhaustion_timeout=keep_waiting`` elects not
#: to escalate after the wait cap elapsed (the Makefile restart loop re-enters).
#: Format: ``[QUOTA_TIMEOUT_KEEP_WAITING] reason=<source>``
_QUOTA_TIMEOUT_KEEP_WAITING_AUDIT_PREFIX: str = "[QUOTA_TIMEOUT_KEEP_WAITING]"

#: ``_stop_reason`` strings returned by the quota dispatch when a graceful drain
#: was requested.  ``cmd_start`` checks membership to know it must NOT cancel the
#: drain signal in its exit ``finally`` blocks (the signal must survive so the
#: restart loop / a peer session drains).
_QUOTA_STOP_REASON_DRAIN_DETECTION: str = "quota-drain-requested"
_QUOTA_STOP_REASON_DRAIN_TIMEOUT: str = "quota-wait-timeout-drain"
_QUOTA_DRAIN_STOP_REASONS: frozenset[str] = frozenset(
    {_QUOTA_STOP_REASON_DRAIN_DETECTION, _QUOTA_STOP_REASON_DRAIN_TIMEOUT}
)

#: ``_stop_reason`` string returned by :func:`_dispatch_quota_detection` when a
#: quota wait recovered.  This value is NOT terminal: ``cmd_start`` treats it as
#: the signal to re-open a FRESH ``ClaudeSDKClient`` session and resume the
#: orchestrate skill on the remaining backlog (bounded by
#: :func:`_resolve_max_quota_resumes`).  Single-sourced so the dispatch, the
#: cmd_start resume loop, and the notification classifier never drift on the
#: literal.
_QUOTA_STOP_REASON_WAIT_RECOVERED: str = "quota-wait-recovered"

#: HTTP timeout in seconds for the ``recovery_probe`` API call issued during
#: quota wait polling.  A short timeout avoids blocking the orchestrator for
#: more than one poll cycle on a transient network hang.
_RECOVERY_PROBE_TIMEOUT_SECONDS: float = 30.0

#: Minimum token count for the ``recovery_probe`` prompt.  A single token is
#: sufficient to confirm the quota window has cleared without incurring cost.
_RECOVERY_PROBE_REQUEST_SIZE_TOKENS: int = 1


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
    by broad ``except Exception`` handlers.  ``cmd_start`` catches it outside
    ``asyncio.run`` and dispatches to :func:`_handle_quota_pause` when
    ``quota_handling.enabled`` is true, or re-raises the wrapped
    :class:`~devbench.quota.QuotaExhaustedError` for the legacy non-zero exit
    when ``enabled`` is false (AC-236-1).

    Args:
        quota_exc: The :class:`~devbench.quota.QuotaExhaustedError` produced by
            :func:`~devbench.quota.detect_quota_error`.

    Raises:
        Nothing -- this class is only ever raised, never caught internally.
    """

    def __init__(self, quota_exc: QuotaExhaustedError) -> None:
        super().__init__(str(quota_exc))
        self.quota_exc = quota_exc


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
        message: Any object emitted by the
            ``ClaudeSDKClient.receive_response()`` async iterator.
            Objects without a ``content`` list attribute always return
            ``False``.

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


#: Captures the unit-id argument of a ``devbench claim <id>`` Bash command.
_CLAIM_UNIT_ID_RE: re.Pattern[str] = re.compile(r"devbench\s+claim\s+(\S+)")


def _claimed_unit_id(message: object) -> str | None:
    """Return the unit-id of a ``devbench claim <id>`` Bash tool-use, or ``None``.

    Duck-typed; used by ``_run`` to tell the within-claim convergence tracker
    which unit is now in flight. Returns ``None`` when *message* is not a claim.
    """
    for command in _bash_commands(message):
        match = _CLAIM_UNIT_ID_RE.search(command)
        if match is not None:
            return match.group(1)
    return None


def _bash_commands(message: object) -> list[str]:
    """Return every Bash ``tool_input.command`` string carried by *message*.

    Duck-typed; returns an empty list when *message* has no tool-use content.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return []
    commands: list[str] = []
    for block in content:
        if getattr(block, "name", None) != "Bash":
            continue
        block_input = getattr(block, "input", None)
        if not isinstance(block_input, dict):
            continue
        command = block_input.get("command", "")
        if isinstance(command, str) and command.strip():
            commands.append(command.strip())
    return commands


#: Bash command fragments that identify a deterministic acceptance / TDD-RED /
#: live-test re-run -- the observable signal a non-converging claim repeats. A
#: claim that runs the SAME one of these over and over (same target identifier)
#: without ever completing is "busy but not converging." Matched as substrings
#: so the helper is robust to surrounding flags / env prefixes.
_CLAIM_FAILURE_COMMAND_MARKERS: tuple[str, ...] = (
    "devbench verify-ac",
    "tf-test",
    "go test",
    "terratest",
    "pytest",
    "make test",
)

#: Captures a unit-id-shaped or path-shaped target token so two re-runs of the
#: same check against the same target collapse to one signature, while a re-run
#: against a DIFFERENT target (genuine progress) is a distinct signature.
_FAILURE_TARGET_RE: re.Pattern[str] = re.compile(r"(E\d+-F\d+-S\d+-T\d+|[A-Za-z0-9_.-]*/[A-Za-z0-9_./-]+)")

#: Captures a ``KEY=value`` argument (e.g. ``MODULE_PATH=providers/aws/alb``) so
#: a parameterised test command keys on its value -- distinguishing two modules
#: even when the value itself is not path-shaped.
_FAILURE_KV_ARG_RE: re.Pattern[str] = re.compile(r"\b([A-Z_]+)=(\S+)")


def _failure_target_token(command: str) -> str:
    """Return the most specific target identifier in *command* for signature keying.

    Preference order so a parameterised check keys on the thing that varies
    between genuinely-different targets: an explicit ``KEY=value`` value first
    (e.g. ``MODULE_PATH=...``), then a unit-id / path token, else the empty
    string (no distinguishable target).
    """
    kv = _FAILURE_KV_ARG_RE.search(command)
    if kv is not None:
        return kv.group(2)
    target = _FAILURE_TARGET_RE.search(command)
    return target.group(0) if target is not None else ""


def _extract_failure_signature(message: object) -> str | None:
    """Return a stable signature for a repeated AC-verify / TDD-RED / test re-run.

    Pure + deterministic. Scans the Bash commands in *message* for one of
    :data:`_CLAIM_FAILURE_COMMAND_MARKERS`; when found, returns a normalised
    signature combining the matched marker with the command's target identifier
    (a work-unit id, a ``KEY=value`` arg, or a path token). Two re-runs of the
    SAME check against the SAME target share a signature; a re-run against a
    DIFFERENT target is a DISTINCT signature (so genuine progress across targets
    does not accrue toward the bound).

    Returns ``None`` when the message carries no recognisable verification /
    test re-run -- a claim, a status read, an edit, etc. never produce a
    signature, so only repeated *checking* of the same thing counts.
    """
    for command in _bash_commands(message):
        for marker in _CLAIM_FAILURE_COMMAND_MARKERS:
            if marker in command:
                return f"{marker}::{_failure_target_token(command)}"
    return None


#: The AUTHORITATIVE per-unit acceptance gate. A repeated ``devbench verify-ac``
#: failure ALWAYS counts toward the within-claim convergence bound, regardless of
#: its target token: it is scoped to the unit by construction, so it can never be
#: a "whole repo suite" run.
_VERIFY_AC_MARKER: str = "devbench verify-ac"

#: Test-runner markers whose target is a TEST PATH (a file, node id, directory,
#: or the whole checkout). Only these can express a "whole repo suite" target,
#: so only these are subject to the whole-suite exemption. Markers that
#: parameterise by a ``KEY=value`` module (``tf-test`` / ``terratest``) always
#: name a SPECIFIC module -- never a whole suite -- so they are excluded here and
#: always counted toward the bound.
_PATH_SCOPED_TEST_MARKERS: frozenset[str] = frozenset({"pytest", "make test", "go test"})


def _is_whole_suite_target(marker: str, target_token: str, repo_roots: tuple[str, ...]) -> bool:
    """Return ``True`` when a failure is a WHOLE-SUITE / out-of-scope test-runner run.

    Root cause of tracked-issue 004: the executor sometimes runs the FULL repo
    test suite within a claim. A whole-suite failure can be caused by an
    OUT-OF-SCOPE / other-unit defect even when this unit's OWN scoped
    ``verify-ac`` is green, so it must NOT accrue toward the within-claim
    non-converging bound (a leaf unit must never be held hostage to another
    unit's tests).

    Pure + deterministic (no I/O), so it is trivially unit-tested.

    Classification:

    - The authoritative per-unit gate (:data:`_VERIFY_AC_MARKER`) is NEVER
      whole-suite -- it always counts.
    - A raw test-runner failure (``pytest`` / ``make test`` / ``go test`` /
      ``tf-test`` / ``terratest``) is whole-suite when its *target token* is:
        * empty (a bare runner invocation with no target), OR
        * a bare directory (no file component -- e.g. ``tests`` / ``tests/unit``),
          OR
        * an absolute path equal to, or a descendant of, a configured target-repo
          checkout root (the whole checkout, not a specific file).
    - A target naming a SPECIFIC test file (``...test_foo.py`` /
      ``file.py::node``) or a parameterised ``KEY=value`` module value is SCOPED,
      so it still counts.

    Args:
        marker: The matched command marker (the part before ``::`` in a
            signature, e.g. ``pytest`` or ``devbench verify-ac``).
        target_token: The target identifier (the part after ``::``).
        repo_roots: Resolved target-repo checkout root paths to compare absolute
            targets against. May be empty (only the empty/bare-dir rules then
            apply).
    """
    if marker == _VERIFY_AC_MARKER:
        return False
    if marker not in _PATH_SCOPED_TEST_MARKERS:
        # A ``KEY=value``-parameterised runner (``tf-test`` / ``terratest``)
        # always names a SPECIFIC module, never a whole suite -- always counts.
        return False
    token = target_token.strip()
    if not token:
        # A bare runner invocation with no target -> the whole suite.
        return True
    if token.startswith("/"):
        # An absolute path: whole-suite when it IS a checkout root or a
        # descendant of one (the whole checkout, not one file inside it).
        normalized = token.rstrip("/")
        for root in repo_roots:
            root_norm = root.rstrip("/")
            if normalized == root_norm or normalized.startswith(root_norm + "/"):
                # A specific file UNDER the root is still scoped.
                return not _looks_like_test_file(token)
        # An absolute path outside every configured checkout root: treat a bare
        # directory as whole-suite, a specific file as scoped.
        return not _looks_like_test_file(token)
    # A relative target: a specific test file / node id is scoped; a bare
    # directory (no file component) is a whole-suite run.
    return not _looks_like_test_file(token)


def _looks_like_test_file(token: str) -> bool:
    """Return ``True`` when *token* names a SPECIFIC test file or node id.

    A specific target carries a file component -- a ``.py`` file (optionally with
    a ``::node`` id) or a ``::``-qualified node selector. A bare directory token
    (``tests`` / ``tests/unit`` / an absolute dir) has no file component and is
    therefore a whole-suite target, not a scoped one.
    """
    head = token.split("::", 1)[0]
    last = head.rstrip("/").rsplit("/", 1)[-1]
    return "." in last and not head.endswith("/")


def _resolve_timeout_result_markers() -> tuple[str, ...]:
    """Return the kill-by-timeout result markers (env > constants default).

    TDI #016. Reads the comma-separated env
    ``DEVBENCH_ORCHESTRATOR_TIMEOUT_RESULT_MARKERS`` when set (each token lower-cased
    + stripped, empty tokens dropped), else falls through to
    :data:`~devbench.constants.TIMEOUT_RESULT_MARKERS`. Kept config-driven so a
    target stack whose runner phrases timeouts differently can extend the set
    without a code change (CLAUDE.md: no hard-coded literals baked into behaviour).
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_TIMEOUT_RESULT_MARKERS", "").strip()
    if raw:
        tokens = tuple(tok.strip().lower() for tok in raw.split(",") if tok.strip())
        if tokens:
            return tokens
    return TIMEOUT_RESULT_MARKERS


def _tool_result_texts(message: object) -> list[str]:
    """Return the text content of every ToolResultBlock carried by *message*.

    Duck-typed: a ToolResultBlock is any content block exposing a
    ``tool_use_id`` attribute (so it is distinguished from a ToolUseBlock, which
    exposes ``name`` + ``input``). Its ``content`` is either a plain string or a
    list of ``{"type": "text", "text": ...}`` dicts (the SDK's two shapes); both
    are flattened to their text. Returns an empty list when *message* carries no
    tool result.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return []
    texts: list[str] = []
    for block in content:
        if not hasattr(block, "tool_use_id"):
            continue
        block_content = getattr(block, "content", None)
        if isinstance(block_content, str):
            texts.append(block_content)
        elif isinstance(block_content, (list, tuple)):
            for part in block_content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    return texts


def _is_timeout_result(message: object) -> bool:
    """Return ``True`` when *message* is a Bash result KILLED by its timeout.

    TDI #016. A timed-out run is a NON-deterministic provisioning/infra failure
    (e.g. a cold ``uv`` env syncing dependencies on the first invocation), NOT
    the "same deterministic test failure": it carries timeout text and no
    captured assertion/collection output. The within-claim convergence tracker
    uses this to AVOID counting a timed-out run toward CLAIM_NOT_CONVERGING the
    way a real failure does.

    Matches any of :func:`_resolve_timeout_result_markers` (case-insensitive
    substring) in any ToolResultBlock text. Duck-typed; never raises.
    """
    markers = _resolve_timeout_result_markers()
    for text in _tool_result_texts(message):
        lowered = text.lower()
        if any(marker in lowered for marker in markers):
            return True
    return False


class ClaimConvergenceTracker:
    """Bound a single in-progress claim that repeats the SAME failure forever.

    Neither the turn-end continuation budget (no-activity stalls) nor the
    cascade circuit-breaker (claim re-queue cycles) bounds a unit that stays
    "busy" while re-running the SAME unresolvable AC-verify / TDD-RED / live
    test for hours. This tracker does.

    It keys on the REPEATED IDENTICAL failure signature, never on raw duration:
    a genuinely-progressing long run emits a DIFFERENT signature each round (or
    no repeated failure at all), so the per-signature count never reaches the
    bound and the run is left alone. A wall-clock backstop fires only after a
    duration set well above the observed-legit maximum, as a last resort for a
    claim stuck for an implausibly long time without a repeated signature.

    All clock values are injected via the ``now`` parameter (no hardcoded clock)
    so the tracker is fully deterministic under test.
    """

    def __init__(
        self,
        *,
        max_within_claim_attempts: int,
        max_claim_wall_clock_seconds: float,
        max_no_claim_activity_seconds: float = 0.0,
        repo_roots: tuple[str, ...] = (),
    ) -> None:
        self._max_attempts = max_within_claim_attempts
        self._max_wall_clock = max_claim_wall_clock_seconds
        # Resolved target-repo checkout roots (tracked-issue 004). Used to
        # classify a raw test-runner failure whose target is a checkout root /
        # bare directory as a WHOLE-SUITE run that must NOT accrue toward the
        # within-claim non-converging bound (a leaf unit must never be held
        # hostage to another unit's tests). The authoritative per-unit
        # ``verify-ac`` gate is unaffected and always counts.
        self._repo_roots = repo_roots
        # Inter-claim activity backstop: max seconds the orchestrator may stay
        # active while NO unit is claimed. <= 0 disables it. Default 0.0 keeps
        # the bound off unless the caller (production wiring) supplies a value.
        self._max_no_claim_activity = max_no_claim_activity_seconds
        self.current_unit_id: str | None = None
        self._claim_started_at: float | None = None
        self._signature_counts: dict[str, int] = {}
        # When > 0, the timestamp of the first message observed while no unit is
        # claimed; reset to None whenever a claim is active or freshly noted.
        self._no_claim_active_since: float | None = None
        # TDI #016: the signature of the most-recent run whose count was just
        # incremented, pending its result. A subsequent timed-out result
        # (``_is_timeout_result``) rolls that increment back because a timeout is
        # non-deterministic provisioning latency, not a deterministic failure.
        # Cleared once any result is observed so a timeout can never roll back an
        # increment it did not cause.
        self._pending_signature: str | None = None

    def note_claim(self, unit_id: str, *, now: float) -> None:
        """Begin tracking a freshly-claimed unit, resetting all per-claim state."""
        self.current_unit_id = unit_id
        self._claim_started_at = now
        self._signature_counts = {}
        self._pending_signature = None
        # A fresh claim is forward progress: stop any inter-claim stall timer.
        self._no_claim_active_since = None

    def clear_current_claim(self) -> None:
        """Stop tracking the current claim after it has been force-blocked.

        Block-and-continue: once a non-converging unit is BLOCKED the
        orchestrator continues to the next in-queue unit rather than halting.
        Clearing the current claim ensures the just-blocked unit cannot
        re-trip the bound on every subsequent identical-failure message that
        may still arrive before the skill claims the next unit (which would
        otherwise inflate the aggregate-valve count for the SAME unit and
        re-issue redundant force-blocks). ``observe`` returns ``None`` until
        the next :meth:`note_claim`.
        """
        self.current_unit_id = None
        self._claim_started_at = None
        self._signature_counts = {}
        self._pending_signature = None
        # Restart the inter-claim window fresh: the orchestrator should claim
        # the next unit promptly after a block; if it instead keeps emitting
        # messages without claiming, the backstop below catches that wedge.
        self._no_claim_active_since = None

    def observe(self, message: object, *, now: float) -> str | None:
        """Fold *message* into the bound; return the recurring failure when tripped.

        Returns the recurring failure signature (or a wall-clock / inter-claim
        diagnostic) when the bound trips, else ``None``. Safe to call before any
        claim has been noted.
        """
        if self.current_unit_id is None or self._claim_started_at is None:
            # No active claim. Bound the inter-claim "active but not claiming"
            # window: a legitimate long op always runs INSIDE a claim, so a
            # no-claim stall can only be an orphaned/churning loop (e.g. an
            # executor still emitting messages after its unit was force-blocked,
            # or a loop stuck without claiming the next unit).
            if self._max_no_claim_activity > 0:
                if self._no_claim_active_since is None:
                    self._no_claim_active_since = now
                elif (now - self._no_claim_active_since) >= self._max_no_claim_activity:
                    elapsed = int(now - self._no_claim_active_since)
                    return (
                        f"no claim progressed for {elapsed}s while the orchestrator stayed "
                        "active (possible stall: messages flowing but no work claimed)"
                    )
            return None

        # An active claim is forward progress for the inter-claim backstop.
        self._no_claim_active_since = None

        # TDI #016: a Bash result KILLED by its timeout is a non-deterministic
        # provisioning failure (e.g. a cold ``uv`` env syncing dependencies on
        # the first invocation), NOT the "same deterministic test failure". Roll
        # back the increment the just-run command (``_pending_signature``)
        # contributed so repeated timed-out runs never accrue toward the bound.
        # A timeout result carries no failure signature of its own, so this is
        # handled before signature extraction. The pending signature is then
        # cleared either way so a later result cannot double-roll-back it.
        if _is_timeout_result(message):
            if self._pending_signature is not None:
                rolled_back = self._signature_counts.get(self._pending_signature, 0) - 1
                if rolled_back > 0:
                    self._signature_counts[self._pending_signature] = rolled_back
                else:
                    self._signature_counts.pop(self._pending_signature, None)
                self._pending_signature = None
            return self._wall_clock_verdict(now)

        signature = _extract_failure_signature(message)
        if signature is not None and self._is_whole_suite_signature(signature):
            # Tracked-issue 004: a WHOLE-SUITE / out-of-scope test-runner failure
            # (the executor ran the full repo suite, or a bare checkout-root
            # directory) can be tripped by ANOTHER unit's defect even when this
            # unit's own scoped verify-ac is green. It must NOT accrue toward the
            # within-claim bound -- the authoritative per-unit verify-ac gate
            # still does. Skip the increment and clear any pending marker so a
            # following result cannot roll back an unrelated increment.
            logger.info(
                "convergence: not counting whole-suite/out-of-scope failure %r toward the "
                "within-claim bound for %s (scoped verify-ac is the per-unit gate)",
                signature,
                self.current_unit_id,
            )
            self._pending_signature = None
            return self._wall_clock_verdict(now)

        if signature is not None:
            # A NEW test/verify re-run: its result (success / real failure /
            # timeout) arrives in a LATER message. Remember it as pending so a
            # following timeout result can roll this increment back (#016).
            count = self._signature_counts.get(signature, 0) + 1
            self._signature_counts[signature] = count
            self._pending_signature = signature
            if count >= self._max_attempts:
                return signature
        else:
            # A non-run message that is not a timeout result (an edit, a
            # non-timeout test result, narrative text): the pending run's verdict
            # is settled, so its increment stands. Drop the pending marker so a
            # subsequent timeout result cannot roll back an unrelated increment.
            self._pending_signature = None

        return self._wall_clock_verdict(now)

    def _is_whole_suite_signature(self, signature: str) -> bool:
        """Return ``True`` when *signature* is a whole-suite / out-of-scope test run.

        Splits the ``marker::target`` signature (the shape produced by
        :func:`_extract_failure_signature`) and delegates to the pure
        :func:`_is_whole_suite_target` classifier against the tracker's
        configured ``repo_roots`` (tracked-issue 004).
        """
        marker, _, target = signature.partition("::")
        return _is_whole_suite_target(marker, target, self._repo_roots)

    def _wall_clock_verdict(self, now: float) -> str | None:
        """Return the wall-clock backstop diagnostic when exceeded, else ``None``.

        Extracted so every ``observe`` exit path applies the same secondary
        backstop without duplicating the condition (DRY).
        """
        if self._claim_started_at is None:
            return None
        if self._max_wall_clock > 0 and (now - self._claim_started_at) >= self._max_wall_clock:
            return f"wall-clock backstop exceeded for {self.current_unit_id}"
        return None


#: Sub-agent (Task tool) activity message class names. Their presence means the
#: orchestrator is doing real work via a spawned agent (e.g. a long terraform
#: apply / make validate) even when the top-level session is quiet -- so the
#: turn-end stall budget must NOT accrue against them.
_SUBAGENT_ACTIVITY_MESSAGE_TYPES: frozenset[str] = frozenset(
    {"TaskStartedMessage", "TaskProgressMessage", "TaskNotificationMessage"}
)


def _is_genuine_progress(message: object) -> bool:
    """Return ``True`` when *message* shows the orchestrator is doing real work.

    Used to reset the turn-end stall budget so a legitimately long-running unit
    (a quiet sub-agent running a terraform apply / ``make validate`` / a real
    terratest) is NOT killed by accumulating inactivity timeouts. Genuine
    progress is any of: a work-unit claim, a sub-agent Task activity message, or
    any tool-use block in the message content (the model is actively invoking a
    tool). An empty or synthetic message (e.g. the ``model_not_found`` error
    AssistantMessage) is NOT progress -- those still accrue toward the budget so
    a true no-progress loop still trips, and a fatal error exits via
    :func:`detect_fatal_sdk_error` before reaching this check. Duck-typed; never
    raises.
    """
    if type(message).__name__ in _SUBAGENT_ACTIVITY_MESSAGE_TYPES:
        return True
    content = getattr(message, "content", None)
    if not isinstance(content, (list, tuple)):
        return False
    # A tool-use block carries a ``name`` + ``input`` (ToolUseBlock); its presence
    # means the model is actively calling a tool == real work.
    return any(getattr(block, "name", None) and getattr(block, "input", None) is not None for block in content)


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

    The session's scope is persisted to ``scope.json`` separately by
    :meth:`ScopeFilter.to_file` (object schema) when ``--include`` is supplied;
    this function does NOT write ``scope.json`` (it formerly wrote a bare JSON
    array that the object-schema readers reject as corrupt).  The expanded
    ``scope_ids`` are still recorded in the session registry below.

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

    # NOTE: scope.json is intentionally NOT written here.  It is owned solely by
    # ScopeFilter.to_file (object schema), which cmd_start invokes only when
    # --include is supplied.  Writing a bare JSON array here produced the
    # "scope.json corrupt -- got 'list'" failure on no-include starts.

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

    Issue #262: an empty-string ``result`` is a real, explicit turn boundary
    and must be returned as ``''`` (not ``None``) so callers can distinguish
    "the SDK emitted a ResultMessage with an empty result" from "the message
    carried no result attribute at all".

    Returns the ``result`` string (including ``''``) when ``message`` is a
    SDK ResultMessage carrying a string ``result``; ``None`` when the
    attribute is absent or not a string.  Duck-typed so unit tests can yield
    bare ``object`` instances with a ``result`` attribute without importing
    the SDK.
    """
    candidate = getattr(message, "result", None)
    if isinstance(candidate, str):
        return candidate
    return None


def _check_quota_and_drain(message: object) -> None:
    """Per-message quota + drain-on-claim short-circuit for the orchestrate loop.

    Raises :class:`_QuotaDetected` when ``message`` carries a quota / rate-limit
    error (#236), or :class:`_DrainRequested` when a claim tool-use is observed
    while a drain signal is pending (#188/#212). No-op otherwise. Extracted from
    ``_run`` so its branch count stays under ruff PLR0912.
    """
    _qe = detect_quota_error(message)
    if _qe is not None:
        raise _QuotaDetected(_qe)
    _drain = read_drain_state(WORKSPACE_ROOT)
    if _is_claim_tool_use(message) and _drain is not None:
        raise _DrainRequested(_drain.reason)


def detect_fatal_sdk_error(message: object) -> str | None:
    """Return a non-retryable fatal-error code carried by ``message``, else ``None``.

    A hard SDK error such as ``model_not_found`` (the configured model does not
    exist / the account lacks access -- e.g. a withdrawn model) recurs
    identically every turn; it cannot be resolved by retrying, only by operator
    action. ``_run`` checks this BEFORE the turn-end continuation path so such an
    error exits fast on the FIRST occurrence instead of re-prompting forever
    (the runaway this guards against). Quota / rate-limit errors are deliberately
    NOT matched here -- they route to the quota wait-and-resume path via
    :func:`detect_quota_error`.

    Detection is duck-typed: it inspects the message's ``error`` attribute
    (carried by a synthetic ``AssistantMessage`` like
    ``error='model_not_found'``) and matches it case-insensitively against
    :data:`~devbench.constants.FATAL_SDK_ERROR_CODES`. Returns the matched code
    when found, else ``None``.
    """
    err = getattr(message, "error", None)
    if not isinstance(err, str) or not err:
        return None
    err_lower = err.lower()
    for code in FATAL_SDK_ERROR_CODES:
        if code in err_lower:
            return code
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

#: Issue #262 (E10-F1-S2): audit prefix emitted when a non-terminal ResultMessage
#: is detected and the orchestrator issues an in-session continuation query.
_ORCHESTRATOR_TURN_END_NO_SENTINEL_AUDIT: str = "[ORCHESTRATOR_TURN_END_NO_SENTINEL]"

#: Issue #271 (E14-F1-S1-T1): audit prefix emitted at the point the final
#: stop reason is determined, so the reason is visible in the orchestrator log
#: as well as in the Slack notification.  Format:
#: ``[ORCHESTRATOR_STOP_REASON] reason=<token>``
_ORCHESTRATOR_STOP_REASON_AUDIT_PREFIX: str = "[ORCHESTRATOR_STOP_REASON] reason="

#: Issue #271 (E14-F1-S1-T1): distinct stop-reason token emitted when the
#: SDK loop returns normally without producing a terminal sentinel.  Never
#: equals the legacy bare ``"clean"`` token.
_STOP_REASON_PREMATURE_TURN_END: str = "premature-turn-end"

#: Block-and-continue (TDI): stop-reason prefix emitted when the aggregate
#: non-converging-claims safety valve trips -- K distinct units each hit the
#: within-claim convergence bound in ONE session, signalling a systemically
#: broken run that needs operator attention. A single non-converging claim is
#: BLOCKED and the session continues; only this aggregate valve halts it. Routed
#: to STOP_CLASS_CRASH (operator mention) by ``classify_stop_class``.
_STOP_REASON_TOO_MANY_NON_CONVERGING: str = "too many non-converging claims"


def _too_many_non_converging_reason(count: int) -> str:
    """Build the aggregate non-converging-claims stop reason ``too many non-converging claims (K)``.

    Single-sourced so the ``_run`` audit line and the
    :func:`_classify_orchestrator_exit` stop reason never drift.
    """
    return f"{_STOP_REASON_TOO_MANY_NON_CONVERGING} ({count})"


#: Issue #262 (E10-F1-S2): verbatim continuation prompt sent to the same
#: ClaudeSDKClient session when a non-terminal ResultMessage is observed.
#: Must not contain an em-dash (U+2014) per code standards; uses -- (double
#: hyphen) for any separators.  Instructs the agent that its next action
#: must be a tool call -- specifically running devbench next and acting on
#: the dispatch result -- rather than generating a summary.
ORCHESTRATOR_CONTINUATION_PROMPT: str = (
    "Your previous turn ended without a terminal sentinel (ALL_DONE / NO_ACTIONABLE). "
    "Your next action MUST be a tool call -- run `uv run devbench next` and act on its "
    "dispatch. Do not summarise; do not output plain text. Invoke the tool now."
)


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


def _classify_normal_exit_reason(sdk_result_text: str | None) -> str:
    """Issue #271 (E14-F1-S1-T1): classify a normal (non-exception) loop exit.

    Maps the final ``_sdk_result_text`` captured during the SDK run to
    the canonical stop-reason token.  Called from ``cmd_start`` whenever
    the SDK loop returns without raising (i.e., the ``else``-branch of the
    ``if _continuation_exhausted`` check).

    Rules (single-sourced; no duplicated reason literals):

    - ``sdk_result_text`` contains a terminal sentinel (``ALL_DONE`` /
      ``NO_ACTIONABLE`` / ``NO_ACTIONABLE_IN_SCOPE``) -- completion:
      ``f"clean exit: {sdk_result_text}"``.
    - ``sdk_result_text`` is ``None`` or empty-string (no terminal sentinel
      was ever captured) -- premature turn-end:
      ``_STOP_REASON_PREMATURE_TURN_END`` (``"premature-turn-end"``).

    The function NEVER returns the bare literal ``"clean"``.  Best-effort
    guarantee: any unexpected input falls through to the premature-turn-end
    branch rather than silently returning ``"clean"``.

    Args:
        sdk_result_text: The last ``ResultMessage.result`` text captured
            by ``_run``, or ``None`` when no ResultMessage was seen.

    Returns:
        A non-empty, non-``"clean"`` stop-reason token.
    """
    if _is_terminal_orchestrate_result(sdk_result_text):
        return f"clean exit: {sdk_result_text}"
    return _STOP_REASON_PREMATURE_TURN_END


def _classify_orchestrator_exit(
    *,
    fatal_error_code: str | None,
    continuation_exhausted: bool,
    too_many_non_converging: int,
    sdk_result_text: str | None,
) -> tuple[int, str]:
    """Map a normal (non-exception) SDK-loop exit to ``(restart_rc, stop_reason)``.

    Extracted from ``cmd_start`` to keep its branch count under PLR0912. Decision
    order (first match wins):

    1. ``fatal_error_code`` set -- non-retryable SDK error (e.g.
       ``model_not_found``): exit with the distinct fatal-error code so the
       wrapping ``make start`` loop does NOT auto-restart (a restart re-hits the
       same error). Operator must fix the model.
    2. ``continuation_exhausted`` -- the turn-end continuation budget tripped:
       exit with the distinct continuations-exhausted code.
    3. ``too_many_non_converging`` > 0 -- the AGGREGATE non-converging-claims
       safety valve tripped: that many DISTINCT units each hit the within-claim
       convergence bound in this session (block-and-continue did NOT halt on any
       single one). The session stops for operator attention with the
       ``too many non-converging claims (K)`` reason. The blocked units await
       operator review; a restart picks up the next claimable units, so the
       normal auto-restart classification applies.
    4. Otherwise -- classify the normal exit reason and run the auto-restart
       check. A session that blocked one or two non-converging units and then
       drained its scope reaches this branch and exits cleanly (block-and-
       continue): a single non-converging claim never produces a stop reason.
    """
    if fatal_error_code is not None:
        return ORCHESTRATOR_FATAL_ERROR_EXIT_CODE, f"fatal SDK error: {fatal_error_code}"
    if continuation_exhausted:
        return ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE, "continuation budget exhausted"
    if too_many_non_converging > 0:
        return _check_auto_restart_and_notify(_too_many_non_converging_reason(too_many_non_converging))
    return _check_auto_restart_and_notify(_classify_normal_exit_reason(sdk_result_text))


def _is_sdk_result_message(message: object) -> bool:
    """Issue #262: True iff ``message`` looks like an SDK ``ResultMessage``.

    Identification uses duck typing only -- no SDK import -- so the
    orchestrate loop remains decoupled from the SDK class hierarchy.  A
    ``ResultMessage`` carries a ``subtype`` attribute plus at least one of
    ``num_turns`` or ``duration_ms``; ordinary assistant/tool messages lack
    the turn-accounting attributes.

    Returns False (never raises) when ``message`` lacks the expected
    attribute surface so the SDK-iterator loop can keep iterating safely.
    """
    if not hasattr(message, "subtype"):
        return False
    return hasattr(message, "num_turns") or hasattr(message, "duration_ms")


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


async def _handle_result_message(message: object, client: Any) -> bool:
    """Issue #262 (E10-F1-S2): handle a non-terminal ResultMessage turn boundary.

    When ``message`` is a ResultMessage (detected by :func:`_is_sdk_result_message`)
    and its result text is NOT a terminal sentinel, logs
    ``[ORCHESTRATOR_TURN_END_NO_SENTINEL]`` and issues
    ``client.query(ORCHESTRATOR_CONTINUATION_PROMPT)`` into the same session,
    then returns ``True`` so the caller breaks the ``async for`` and continues
    the outer ``while True`` to iterate ``receive_response()`` again.

    Returns ``False`` in all other cases:

    - ``message`` is not a ResultMessage (not detected by ``_is_sdk_result_message``).
    - ``message`` IS a ResultMessage but carries a terminal sentinel -- the caller's
      ``_log_terminal_exit_if_applicable`` check fires first and returns before this
      function is reached, so this case never occurs in practice.

    Extracted from ``_run`` so the receive-loop branch count stays under
    ruff PLR0912's 12-branch cap.

    Args:
        message: The SDK message to inspect.
        client: The live ``ClaudeSDKClient`` instance (typed as ``Any`` for
            duck-typing); its ``query`` coroutine is awaited on a non-terminal
            turn boundary.

    Returns:
        ``True`` when a non-terminal turn boundary was detected and a
        continuation query was issued (caller should break the async for).
        ``False`` otherwise (caller should continue iterating).
    """
    if not _is_sdk_result_message(message):
        return False
    # At this point the message IS a ResultMessage.  The terminal-sentinel
    # check already ran in the caller (via _log_terminal_exit_if_applicable);
    # if we arrive here the result is non-terminal: issue the continuation.
    result_text = _extract_sdk_result_text(message)
    logger.info(
        "%s result=%r",
        _ORCHESTRATOR_TURN_END_NO_SENTINEL_AUDIT,
        result_text,
    )
    await client.query(ORCHESTRATOR_CONTINUATION_PROMPT)
    return True


async def _iter_messages_with_inactivity_timeout(
    agen: Any,
    timeout_seconds: float,
) -> Any:
    """Issue #262 (E10-F2-S1): yield messages from an async generator with per-message timeout.

    When *timeout_seconds* is positive, each message is fetched via
    ``asyncio.wait_for(agen.__anext__(), timeout=timeout_seconds)`` so the
    timer resets on every received message.  When *timeout_seconds* is <= 0
    the wrap is skipped and the generator is iterated directly.

    Raises ``asyncio.TimeoutError`` when the per-message inactivity timeout
    fires.  The caller is responsible for handling that exception.

    Args:
        agen: The async generator returned by ``client.receive_response()``.
        timeout_seconds: Timeout in seconds.  A value <= 0 disables the wrap.

    Yields:
        Each message from *agen* in order.

    Raises:
        asyncio.TimeoutError: When *timeout_seconds* > 0 and no message
            arrives within the timeout window.
        StopAsyncIteration: When the generator is exhausted (normal path).
    """
    if timeout_seconds <= 0:
        async for message in agen:
            yield message
        return
    aiter_obj = agen.__aiter__()
    while True:
        try:
            message = await asyncio.wait_for(aiter_obj.__anext__(), timeout=timeout_seconds)
        except StopAsyncIteration:
            return
        yield message


def _resolve_max_turn_end_continuations() -> int:
    """Return the effective continuation-budget cap (env > default).

    Reads ``DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS`` from the
    process environment.  When the variable is absent or empty the constant
    ``DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS`` is returned so the
    caller is never left without a bound (unset-safe, Issue #262 E10-F1-S3).

    Returns:
        The maximum number of consecutive non-terminal ResultMessage
        continuations ``_run`` will issue before aborting with
        ``ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE``.
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS", "").strip()
    if raw:
        return int(raw)
    return DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS


def _resolve_within_claim_convergence_check() -> bool:
    """Return whether the within-claim convergence bound is active (env > YAML > default)."""
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_WITHIN_CLAIM_CONVERGENCE_CHECK", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    yaml_value = RUNTIME_CONFIG.orchestrate.within_claim_convergence_check
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_WITHIN_CLAIM_CONVERGENCE_CHECK


def _resolve_max_within_claim_attempts() -> int:
    """Return the within-claim repeated-failure attempt cap (env > YAML > default)."""
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_MAX_WITHIN_CLAIM_ATTEMPTS", "").strip()
    if raw:
        return int(raw)
    yaml_value = RUNTIME_CONFIG.orchestrate.max_within_claim_attempts
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_MAX_WITHIN_CLAIM_ATTEMPTS


def _resolve_max_parallel_in_progress() -> int:
    """Return the concurrently-in-progress cap (env > YAML > default).

    Caps how many work units may be IN-PROGRESS at once. Default 1 SERIALIZES
    claims because every claim shares ONE target-repo checkout: two concurrent
    in-progress units would leak each other's uncommitted files into get-diff /
    the staged index (tracked-issue 002). Reads
    ``DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS`` (int), then YAML
    ``orchestrate.max_parallel_in_progress``, falling through to
    :data:`~devbench.constants.DEFAULT_MAX_PARALLEL_IN_PROGRESS`.
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS", "").strip()
    if raw:
        return int(raw)
    yaml_value = RUNTIME_CONFIG.orchestrate.max_parallel_in_progress
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_MAX_PARALLEL_IN_PROGRESS


def _resolve_max_claim_wall_clock_seconds() -> float:
    """Return the within-claim wall-clock backstop in seconds (env > YAML > default)."""
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_MAX_CLAIM_WALL_CLOCK_SECONDS", "").strip()
    if raw:
        return float(raw)
    yaml_value = RUNTIME_CONFIG.orchestrate.max_claim_wall_clock_seconds
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS


def _resolve_max_no_claim_activity_seconds() -> float:
    """Return the inter-claim activity backstop in seconds (env > YAML > default).

    Bounds how long the orchestrator may stay active while NO unit is claimed
    before the loop treats it as a stall (the orphaned-executor / "0 in-progress
    but hook-logs flowing" wedge). A value <= 0 disables the backstop.
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_MAX_NO_CLAIM_ACTIVITY_SECONDS", "").strip()
    if raw:
        return float(raw)
    yaml_value = RUNTIME_CONFIG.orchestrate.max_no_claim_activity_seconds
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS


def _resolve_max_non_converging_claims() -> int:
    """Return the aggregate non-converging-claims valve threshold (env > YAML > default).

    Block-and-continue: a single non-converging claim is BLOCKED and the session
    continues. Once this many DISTINCT units have each tripped the within-claim
    convergence bound in ONE session, the session halts for operator attention.
    Reads ``DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS`` (int), then YAML
    ``orchestrate.max_non_converging_claims``, falling through to
    :data:`~devbench.constants.DEFAULT_MAX_NON_CONVERGING_CLAIMS` so the caller is
    never left without a bound (no hard-coded magic number).
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS", "").strip()
    if raw:
        return int(raw)
    yaml_value = RUNTIME_CONFIG.orchestrate.max_non_converging_claims
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_MAX_NON_CONVERGING_CLAIMS


def _resolve_claim_teardown_cleanup_hook() -> str | None:
    """Return the sanctioned cleanup command run after executor-group teardown.

    Resolves ``DEVBENCH_ORCHESTRATOR_CLAIM_TEARDOWN_CLEANUP_HOOK`` (env), then
    YAML ``orchestrate.claim_teardown_cleanup_hook``, then
    :data:`~devbench.constants.DEFAULT_CLAIM_TEARDOWN_CLEANUP_HOOK`. An empty /
    whitespace-only value means "no hook" and yields ``None`` so no command ever
    runs unless the project explicitly opts in (CLAUDE.md: no implicit
    behaviour, no hard-coded values).
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_CLAIM_TEARDOWN_CLEANUP_HOOK", "").strip()
    if not raw:
        yaml_value = RUNTIME_CONFIG.orchestrate.claim_teardown_cleanup_hook
        candidate = yaml_value if yaml_value is not None else DEFAULT_CLAIM_TEARDOWN_CLEANUP_HOOK
        raw = candidate.strip()
    return raw or None


def _resolve_presync_environment() -> bool:
    """Return whether start-time target-env pre-sync is active (env > YAML > default).

    TDI #016. Reads ``DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT`` (truthy/falsey),
    then YAML ``orchestrate.presync_environment``, then
    :data:`~devbench.constants.DEFAULT_PRESYNC_ENVIRONMENT`.
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    yaml_value = RUNTIME_CONFIG.orchestrate.presync_environment
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_PRESYNC_ENVIRONMENT


def _resolve_presync_command() -> list[str]:
    """Return the per-repo provisioning command argv (env > YAML > default).

    TDI #016. The env override ``DEVBENCH_ORCHESTRATOR_PRESYNC_COMMAND`` is
    whitespace-tokenised; YAML ``orchestrate.presync_command`` is already a list;
    the fallback is :data:`~devbench.constants.DEFAULT_PRESYNC_COMMAND`.
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_PRESYNC_COMMAND", "").strip()
    if raw:
        tokens = raw.split()
        if tokens:
            return tokens
    yaml_value = RUNTIME_CONFIG.orchestrate.presync_command
    if yaml_value:
        return list(yaml_value)
    return list(DEFAULT_PRESYNC_COMMAND)


def _resolve_presync_timeout_seconds() -> int:
    """Return the per-repo pre-sync timeout in seconds (env > YAML > default).

    TDI #016. Reads ``DEVBENCH_ORCHESTRATOR_PRESYNC_TIMEOUT_SECONDS`` (int), then
    YAML ``orchestrate.presync_timeout_seconds``, then
    :data:`~devbench.constants.DEFAULT_PRESYNC_TIMEOUT_SECONDS`.
    """
    raw = os.environ.get("DEVBENCH_ORCHESTRATOR_PRESYNC_TIMEOUT_SECONDS", "").strip()
    if raw:
        return int(raw)
    yaml_value = RUNTIME_CONFIG.orchestrate.presync_timeout_seconds
    if yaml_value is not None:
        return yaml_value
    return DEFAULT_PRESYNC_TIMEOUT_SECONDS


class PresyncError(RuntimeError):
    """Raised when start-time pre-sync of a target repo environment fails (#016).

    Fail-fast: a real provisioning failure (a non-zero ``presync_command`` exit,
    or a repo with no resolved checkout path) surfaces LOUDLY at orchestrator
    start rather than silently inside a timed claim attempt where it would be
    misread as a test failure. The message names the offending repo + the
    provisioning command's stderr so an operator can act deterministically.
    """


def _presync_target_environments(
    repos: dict[str, RepoConfig],
    *,
    command: list[str],
    runner: verification.CommandRunner,
    timeout: int,
) -> None:
    """Provision each configured repo's dependency environment ONCE (#016).

    Runs *command* (e.g. ``["uv", "sync"]``) in every configured repo's resolved
    checkout BEFORE the orchestrate loop claims any work, so no claim pays the
    cold-dependency-sync cost inside a timed test attempt. ``uv sync`` is
    idempotent and fast on a warm env, so this is a no-op-fast warm-up when the
    environment is already synced.

    Args:
        repos: The configured ``org/repo`` -> :class:`RepoConfig` mapping.
        command: The provisioning command argv to run in each repo checkout.
        runner: Injected command runner (``run_command``-shaped) returning
            ``(returncode, stdout, stderr)``; injectable so the helper is
            unit-testable without shelling out.
        timeout: Per-repo timeout in seconds passed to *runner*.

    Raises:
        PresyncError: When a repo has no resolved checkout path, or the
            provisioning command exits non-zero for any repo (fail-fast).
    """
    for repo_name, repo_cfg in repos.items():
        checkout = repo_cfg.resolved_checkout_path
        if checkout is None:
            raise PresyncError(
                f"pre-sync: repo {repo_name!r} has no resolved checkout path; cannot provision its "
                f"environment. Check repos.{repo_name}.checkout_directory in devbench.yaml."
            )
        logger.info("[ORCHESTRATOR_PRESYNC] repo=%s command=%r cwd=%s", repo_name, command, checkout)
        rc, _stdout, stderr = runner(command, cwd=checkout, timeout=timeout)
        if rc != 0:
            raise PresyncError(
                f"pre-sync of {repo_name!r} failed (rc={rc}) running {' '.join(command)} in {checkout}: "
                f"{stderr.strip() or '<no stderr>'}"
            )


def _run_presync_if_enabled() -> int | None:
    """Pre-sync every configured target repo at ``cmd_start``, when enabled (#016).

    Resolves the config-driven enablement / command / timeout (env > YAML >
    default) and provisions each configured repo's environment via
    :func:`_presync_target_environments`. A no-op (returns ``None``) when the
    feature is disabled or no repos are configured.

    Returns:
        ``None`` on success or when disabled (``cmd_start`` proceeds), or rc=1
        when a provisioning failure (:class:`PresyncError`) is raised -- the
        caller returns that rc so the run fails fast at start with an actionable
        message rather than carrying a broken env into the orchestrate loop.
    """
    if not _resolve_presync_environment():
        return None
    repos = RUNTIME_CONFIG.repos
    if not repos:
        return None
    try:
        _presync_target_environments(
            repos,
            command=_resolve_presync_command(),
            runner=run_command,
            timeout=_resolve_presync_timeout_seconds(),
        )
    except PresyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        logger.error("[ORCHESTRATOR_PRESYNC_FAILED] %s", exc)
        return 1
    return None


def _resolve_max_quota_resumes() -> int:
    """Return the effective in-process quota-resume cap (env > default).

    Reads ``DEVBENCH_MAX_QUOTA_RESUMES`` from the process environment.  When the
    variable is absent, empty, or not a parseable positive integer, the constant
    :data:`~devbench.constants.DEFAULT_MAX_QUOTA_RESUMES` is returned so the
    caller is never left without a bound (unset-safe).

    A value <= 0 is treated as invalid and falls back to the default rather than
    disabling the resume loop, so a typo can never silently turn a single quota
    window back into a run-ending event (fail-safe, not fail-open).

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
    return f"crash: {type(exc).__name__}: {exc}"


def _read_stop_progress(
    units: list[WorkUnit],
) -> tuple[int, int]:
    """Return (done_count, total_count) from a parsed backlog unit list.

    Counts units whose status is :data:`~devbench.backlog.work_unit.WorkUnitStatus.DONE`
    and returns the total list length alongside it.  Single-sourced so the
    counting logic is not duplicated across callers.

    Args:
        units: List of :class:`~devbench.backlog.work_unit.WorkUnit` objects.

    Returns:
        ``(done_count, total_count)`` tuple.
    """
    done = sum(1 for u in units if u.status is WorkUnitStatus.DONE)
    return done, len(units)


def _fire_orchestrator_stop_notification(reason: str) -> None:
    """Best-effort always-fire of the ``orchestrator_stop`` notification.

    Issue #271 (E14-F1-S1-T1): writes the stop reason to the audit log via
    ``logger.info`` BEFORE dispatching the Slack notification so the reason
    is always present in the orchestrator log regardless of whether the
    notification itself succeeds.

    Issue #271 (E14-F2-S2-T1): reads done/total work-unit counts and the
    in-flight unit id via the existing backlog parser, guarded best-effort so
    a parser failure never masks the stop notification.  The progress context
    is passed to ``notify_orchestrator_stop``; when the parser fails, both
    counts are ``None`` and the Progress field is omitted from the payload.

    Wraps the lookup + dispatch in a broad try/except so a buggy
    notification import or a transient backlog-parser failure during
    cmd_start's outer try/finally cannot mask the real exit reason.
    Extracted from ``cmd_start`` body so the branch-count of that
    function stays under the project's ruff PLR0912 ceiling (12).
    """
    logger.info("%s%s", _ORCHESTRATOR_STOP_REASON_AUDIT_PREFIX, reason)
    try:
        from devbench.notifications import notify_orchestrator_stop

        in_flight_id: str | None = None
        done_count: int | None = None
        total_count: int | None = None
        try:
            stop_parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
            stop_units = stop_parser.parse_index()
            stop_wu = _find_in_flight_wu(stop_units)
            in_flight_id = stop_wu.id if stop_wu is not None else None
            done_count, total_count = _read_stop_progress(stop_units)
        except (OSError, ValueError):
            in_flight_id = None
            done_count = None
            total_count = None
        notify_orchestrator_stop(reason, in_flight_id, done_count, total_count)
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


def _should_handle_quota(
    _exc: QuotaExhaustedError,
    qh_cfg: QuotaHandlingConfig,
) -> bool:
    """Return True when the quota error should be handled by wait-and-resume.

    Returns False when ``qh_cfg.enabled`` is False, meaning the caller should
    re-raise the exception to restore the legacy non-zero exit behaviour.

    Args:
        _exc: The detected quota exception (reserved for future per-source filtering).
        qh_cfg: Loaded ``QuotaHandlingConfig`` from the YAML config.

    Returns:
        ``True`` when wait-and-resume should proceed; ``False`` for legacy exit.
    """
    return qh_cfg.enabled


async def _handle_quota_pause(
    *,
    exc: QuotaExhaustedError,
    qh_cfg: QuotaHandlingConfig,
    workspace_root: Path,
    session_name: str,
) -> bool:
    """Handle a quota exhaustion signal with wait-and-resume.

    1. Saves a checkpoint so SIGTERM does not lose pause state.
    2. Emits ``[QUOTA_WAITING] reason=<r> reset_at=<ISO|unknown>`` to the log.
    3. Awaits ``wait_for_reset`` (no shield -- SIGTERM propagates naturally).
    4. On recovery: emits ``[QUOTA_RESUMED] waited_seconds=<N>``,
       applies the resume strategy, returns ``True``.
    5. On timeout: returns ``False`` (caller applies ``on_exhaustion_timeout``).
    6. When the recovery probe is permanently unavailable (no/invalid
       credential) and no reset time is known: emits
       ``[QUOTA_PROBE_UNAVAILABLE]`` and returns ``False`` immediately rather
       than polling for the full ``max_wait_seconds``.

    Args:
        exc: The detected ``QuotaExhaustedError``.
        qh_cfg: Quota handling configuration.
        workspace_root: Workspace root for checkpoint storage.
        session_name: Current session name (stored in checkpoint).

    Returns:
        ``True`` when recovery was confirmed; ``False`` when timed out.
    """
    from devbench.quota import BackoffConfig

    now = datetime.now(tz=UTC)
    checkpoint = QuotaCheckpoint(
        reason=exc.source,
        reset_at=exc.reset_at,
        saved_at=now,
        session_name=session_name,
    )
    save_checkpoint(checkpoint, workspace_root)

    reset_at_str = exc.reset_at.isoformat() if exc.reset_at is not None else "unknown"
    logger.info(
        "%s reason=%s reset_at=%s",
        _QUOTA_WAITING_AUDIT_PREFIX,
        exc.source,
        reset_at_str,
    )
    _fire_quota_waiting_notification(exc.source, reset_at_str)

    wait_start = datetime.now(tz=UTC)

    backoff = BackoffConfig(initial_seconds=qh_cfg.poll_interval_seconds)

    try:
        recovered = await wait_for_reset(
            reset_at=exc.reset_at,
            poll_interval_seconds=qh_cfg.poll_interval_seconds,
            max_wait_seconds=qh_cfg.max_wait_seconds,
            probe_fn=functools.partial(
                recovery_probe,
                timeout_seconds=_RECOVERY_PROBE_TIMEOUT_SECONDS,
                request_size_tokens=_RECOVERY_PROBE_REQUEST_SIZE_TOKENS,
            ),
            backoff_config=backoff,
        )
    except RecoveryProbeUnavailableError as probe_exc:
        # The probe cannot confirm recovery (no/invalid credential) and no
        # provider reset time is known. Stop immediately with an actionable
        # audit instead of polling for the full ``max_wait_seconds``.
        logger.info(
            "%s reason=%s detail=%s",
            _QUOTA_PROBE_UNAVAILABLE_AUDIT_PREFIX,
            exc.source,
            probe_exc,
        )
        return False

    if recovered:
        waited_seconds = int((datetime.now(tz=UTC) - wait_start).total_seconds())
        logger.info(
            "%s waited_seconds=%d",
            _QUOTA_RESUMED_AUDIT_PREFIX,
            waited_seconds,
        )
        _fire_quota_resumed_notification(waited_seconds)
        _apply_resume_strategy(qh_cfg.resume_strategy, workspace_root)
        return True
    return False


def _fire_quota_waiting_notification(reason: str, reset_at: str) -> None:
    """Best-effort ``quota_waiting`` Slack ping at the start of a quota wait.

    Wraps :func:`devbench.notifications.notify_quota_waiting` in a catch-all so a
    notify/IO failure (including import or config-read errors that the helper's
    own dispatcher does not already swallow) can NEVER break or delay the quota
    wait.  Mirrors the best-effort guard used by every other notification call
    site in the orchestrator.

    Args:
        reason: The quota source/reason (``QuotaExhaustedError.source``).
        reset_at: The provider-stated reset time as ISO 8601, or ``"unknown"``.
    """
    try:
        from devbench.notifications import notify_quota_waiting

        notify_quota_waiting(reason, reset_at)
    except Exception as exc:  # broad guard: notification must never break/delay the wait
        logger.warning("[WARN] notify_quota_waiting failed (ignored): %r", exc)


def _fire_quota_resumed_notification(waited_seconds: int) -> None:
    """Best-effort ``quota_resumed`` Slack ping on the quota-recovered path.

    Wraps :func:`devbench.notifications.notify_quota_resumed` in a catch-all so a
    notify/IO failure can NEVER break or delay the resume.  Mirrors the
    best-effort guard used by every other notification call site.

    Args:
        waited_seconds: Total seconds spent waiting before recovery.
    """
    try:
        from devbench.notifications import notify_quota_resumed

        notify_quota_resumed(waited_seconds)
    except Exception as exc:  # broad guard: notification must never break/delay the resume
        logger.warning("[WARN] notify_quota_resumed failed (ignored): %r", exc)


def _restore_session_env_name(prev: str | None) -> None:
    """Restore ``DEVBENCH_SESSION_NAME`` to its value before a ``cmd_start`` run.

    Extracted from ``cmd_start``'s ``finally`` block to keep that function under
    ruff's PLR0912 12-branch limit.

    Args:
        prev: The value of ``os.environ.get("DEVBENCH_SESSION_NAME")`` captured
            before the SDK run.  ``None`` means the variable was absent; the
            variable is removed rather than set to ``"None"``.
    """
    if prev is None:
        os.environ.pop("DEVBENCH_SESSION_NAME", None)
    else:
        os.environ["DEVBENCH_SESSION_NAME"] = prev


def _cancel_drain_unless_requested(workspace_root: Path, quota_drain_requested: bool) -> None:
    """Best-effort cancel of a pending drain signal on ``cmd_start`` exit.

    Skips the cancel when the quota dispatch deliberately requested a drain
    (``on_exhaustion``/``on_exhaustion_timeout`` == ``drain``): that signal MUST
    survive process exit so the Makefile restart loop / a peer session acts on
    it (#236 follow-up).  Otherwise cancels any stale drain so the next start
    does not inherit it.  Idempotent; suppresses filesystem errors.

    Args:
        workspace_root: Root directory of the devbench workspace.
        quota_drain_requested: True when the quota dispatch requested a drain.
    """
    if quota_drain_requested:
        return
    with contextlib.suppress(OSError):
        cancel_drain(workspace_root)


def _dispatch_quota_detection(detected: "_QuotaDetected", session_name: str) -> str:
    """Handle a detected quota signal from ``cmd_start._run``.

    Applies the configured ``quota_handling`` policy:

    - ``enabled: false`` -- re-raise the wrapped
      :class:`~devbench.quota.QuotaExhaustedError` for the legacy non-zero exit.
    - ``on_exhaustion`` (detection-time): ``fail`` re-raises immediately;
      ``drain`` requests a graceful drain and stops without waiting; ``wait``
      (default) pauses and polls via :func:`_handle_quota_pause`.
    - On a wait timeout (or an unrecoverable probe), ``on_exhaustion_timeout``
      is applied via :func:`_dispatch_quota_timeout`.

    Extracted from ``cmd_start`` to keep that function under ruff's PLR0912
    12-branch limit.

    Args:
        detected: The :class:`_QuotaDetected` sentinel raised by ``_run``.
        session_name: Current session name (passed to :func:`_handle_quota_pause`).

    Returns:
        A descriptive ``_stop_reason`` string for the ``cmd_start`` audit trail.
        When a graceful drain was requested the string is a member of
        :data:`_QUOTA_DRAIN_STOP_REASONS` so ``cmd_start`` preserves the signal.

    Raises:
        :class:`~devbench.quota.QuotaExhaustedError`: When ``enabled`` is false,
            or when ``on_exhaustion``/``on_exhaustion_timeout`` is ``fail``.
    """
    qh_cfg = RUNTIME_CONFIG.quota_handling
    if not _should_handle_quota(detected.quota_exc, qh_cfg):
        raise detected.quota_exc from detected

    # on_exhaustion is consulted at DETECTION time, before any wait.
    if qh_cfg.on_exhaustion == "fail":
        logger.info("%s reason=%s", _QUOTA_FAIL_FAST_AUDIT_PREFIX, detected.quota_exc.source)
        raise detected.quota_exc from detected
    if qh_cfg.on_exhaustion == "drain":
        logger.info(
            "%s reason=%s phase=detection",
            _QUOTA_DRAIN_REQUESTED_AUDIT_PREFIX,
            detected.quota_exc.source,
        )
        request_drain(WORKSPACE_ROOT, reason=f"quota-exhaustion:{detected.quota_exc.source}")
        return _QUOTA_STOP_REASON_DRAIN_DETECTION

    # on_exhaustion == "wait" (validated default): pause and poll for recovery.
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

    # Timeout (max_wait elapsed) or an unrecoverable probe: apply
    # on_exhaustion_timeout (default "drain").
    return _dispatch_quota_timeout(detected, qh_cfg.on_exhaustion_timeout)


def _dispatch_quota_timeout(detected: "_QuotaDetected", action: str) -> str:
    """Apply ``on_exhaustion_timeout`` after the wait cap elapses or the probe is unavailable.

    Args:
        detected: The original quota sentinel (holds ``quota_exc`` for re-raise).
        action: ``on_exhaustion_timeout`` value -- ``"drain"`` (default),
            ``"fail"``, or ``"keep_waiting"``.

    Returns:
        A descriptive ``_stop_reason`` string for ``"drain"`` / ``"keep_waiting"``.
        ``"drain"`` returns :data:`_QUOTA_STOP_REASON_DRAIN_TIMEOUT` so
        ``cmd_start`` preserves the drain signal.

    Raises:
        ValueError: When *action* is not a recognised value (defends against
            config-schema drift; the loader already validates the enum).
        :class:`~devbench.quota.QuotaExhaustedError`: When ``action == "fail"``
            (legacy non-zero exit).
    """
    if action not in ("drain", "fail", "keep_waiting"):
        raise ValueError(
            f"unknown on_exhaustion_timeout action {action!r}. Allowed values: ['drain', 'fail', 'keep_waiting']."
        )
    if action == "fail":
        logger.info("%s reason=%s", _QUOTA_FAIL_FAST_AUDIT_PREFIX, detected.quota_exc.source)
        raise detected.quota_exc from detected
    if action == "keep_waiting":
        logger.info(
            "%s reason=%s",
            _QUOTA_TIMEOUT_KEEP_WAITING_AUDIT_PREFIX,
            detected.quota_exc.source,
        )
        return "quota-wait-timeout-keep-waiting"
    # Remaining validated action: drain.
    logger.info(
        "%s reason=%s phase=timeout",
        _QUOTA_DRAIN_REQUESTED_AUDIT_PREFIX,
        detected.quota_exc.source,
    )
    request_drain(WORKSPACE_ROOT, reason=f"quota-timeout:{detected.quota_exc.source}")
    return _QUOTA_STOP_REASON_DRAIN_TIMEOUT


def _should_resume_after_quota_recovery(resumes_used: int, max_resumes: int) -> bool:
    """Decide whether ``cmd_start`` may resume the orchestrate skill in-process.

    Called after :func:`_dispatch_quota_detection` reports a recovered quota
    wait (``_stop_reason == _QUOTA_STOP_REASON_WAIT_RECOVERED``).  A recovered
    wait is NOT terminal: the orchestrator opens a FRESH ``ClaudeSDKClient``
    session and re-runs ``_run`` on the remaining backlog so a single quota
    window cannot permanently end an unattended ``--daemon`` run (which has no
    external ``make start`` restart wrapper).

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
            notification.  Meaningful only when ``terminal_rc`` is not ``None``;
            on the fall-through path ``cmd_start`` overwrites it.
        quota_drain_requested: ``True`` when the quota disposition requested a
            graceful drain that MUST survive process exit, so ``cmd_start``'s
            exit ``finally`` blocks skip their otherwise-unconditional
            ``cancel_drain`` (#236 follow-up).
    """

    terminal_rc: int | None
    stop_reason: str
    quota_drain_requested: bool


def _drive_orchestrate_with_quota_resume(
    run: Callable[[], Coroutine[Any, Any, None]],
    session_name: str,
) -> _OrchestrateLoopResult:
    """Drive the orchestrate session loop with in-process quota resume.

    Each iteration runs ONE ``ClaudeSDKClient`` session via ``asyncio.run(run())``
    (*run* opens a fresh client on every call).  The loop re-enters ONLY when a
    quota wait recovered: the orchestrator resumes the orchestrate skill on the
    remaining backlog with a brand-new session rather than exiting, so a single
    quota window cannot permanently end an unattended ``--daemon`` run that has
    no external ``make start`` restart wrapper.  The number of consecutive
    resumes is bounded by :func:`_resolve_max_quota_resumes`.

    Terminal dispositions (each leaves the loop and is returned to ``cmd_start``):

    - ``_run`` returns normally -> ``terminal_rc=None`` (caller runs its normal
      continuation-exhausted / clean-exit classification).
    - :class:`_DrainRequested` -> consume the marker, audit, ``terminal_rc=0``.
    - :class:`_QuotaDetected` with a non-recovering disposition (fail re-raises;
      drain/keep-waiting return a terminal stop reason) -> ``terminal_rc=0``.
    - A recovered wait whose resume cap is exhausted -> ``terminal_rc=0`` with the
      ``"quota-resume-cap-exhausted"`` stop reason (the exhausted audit line was
      emitted by :func:`_should_resume_after_quota_recovery`).

    Extracted from ``cmd_start`` so the added resume loop does not push that
    function over ruff PLR0912's 12-branch ceiling.

    Args:
        run: The ``cmd_start._run`` closure; awaited fresh on every iteration.
        session_name: Current session name, forwarded to
            :func:`_dispatch_quota_detection` for the quota checkpoint.

    Returns:
        An :class:`_OrchestrateLoopResult` describing how the loop ended.

    Raises:
        :class:`~devbench.quota.QuotaExhaustedError`: Propagated from
            :func:`_dispatch_quota_detection` when ``quota_handling`` is disabled
            or the configured disposition is ``fail`` (legacy non-zero exit).
    """
    resumes_used = 0
    max_resumes = _resolve_max_quota_resumes()
    while True:
        try:
            asyncio.run(run())
        except _DrainRequested as exc:
            # Drain-enforcement backstop (spec section 4.3.3, AC-188-4, AC-188-5,
            # AC-188-8): consume the marker so the next run starts unscoped.
            drained = consume_drain(WORKSPACE_ROOT)
            reason_text = drained.reason if drained is not None else exc.reason
            logger.info("%s%s", _ORCHESTRATOR_DRAIN_ENFORCED_AUDIT_PREFIX, reason_text)
            return _OrchestrateLoopResult(0, f"drain enforced: {reason_text}", False)
        except _QuotaDetected as exc:
            # Issue #236 (AC-236-1): quota wait-and-resume dispatch.
            stop_reason = _dispatch_quota_detection(exc, session_name)
            if stop_reason == _QUOTA_STOP_REASON_WAIT_RECOVERED:
                if _should_resume_after_quota_recovery(resumes_used, max_resumes):
                    resumes_used += 1
                    continue
                # Recovered but the resume cap is exhausted: stop terminally
                # (the exhausted audit line was emitted by the helper above).
                return _OrchestrateLoopResult(0, "quota-resume-cap-exhausted", False)
            # A drain requested by on_exhaustion / on_exhaustion_timeout must
            # outlive this process; signal the caller to preserve it.
            return _OrchestrateLoopResult(0, stop_reason, stop_reason in _QUOTA_DRAIN_STOP_REASONS)
        # ``_run`` returned normally (no quota / no drain): the session finished
        # its work -- fall through to cmd_start's terminal classification.
        return _OrchestrateLoopResult(None, "clean", False)


def _resolve_scope_ids_or_error(parsed: _CmdStartArgs, session_scope_path: Path) -> tuple[list[str], int | None]:
    """Build the session scope ID list and check for overlaps.

    Extracted from ``cmd_start`` to keep that function's return-statement count
    under ruff PLR0911's ceiling.  Combines the scope-token parsing step and the
    overlap-detection step so the caller only needs a single early-exit check.

    Scope persistence is single-sourced through :meth:`ScopeFilter.to_file` at
    the explicit per-session ``session_scope_path`` (NOT the env-routed default,
    because ``DEVBENCH_SESSION_NAME`` is not set yet at this point in
    ``cmd_start``).  When ``--include`` is absent the run is UNSCOPED, which is
    represented by the ABSENCE of scope.json; any stale file left by a prior
    ``--include`` run or crash is cleared so this run is not silently re-scoped.

    Args:
        parsed: Parsed ``cmd_start`` arguments produced by ``_parse_start_args``.
        session_scope_path: Explicit per-session scope.json path
            (from :func:`~devbench.scope.session_scope_file_path`).

    Returns:
        ``(scope_ids, None)`` on success.  ``([], error_rc)`` when the scope
        token is invalid (error_rc=1) or when overlap is detected without
        ``--allow-overlap`` (error_rc=1).
    """
    scope_ids: list[str] = []
    if parsed.include:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
        backlog_ids = [u.id for u in units]
        try:
            scope = ScopeFilter.parse(parsed.include, parsed.exclude, backlog_ids)
        except InvalidScopeError as exc:
            print(f"ERROR: invalid scope token: {exc}", file=sys.stderr)
            return [], 1
        # TDI-003c: scope is resolved from the per-session scope.json path (via
        # session_scope_file_path), not from an env var. The former
        # DEVBENCH_SCOPE_FILE write was write-only (no reader) and is removed so
        # the scope flow carries no misleading dead surface.
        scope.to_file(WORKSPACE_ROOT, path=session_scope_path)
        scope_ids = sorted(scope.expanded_ids)
    else:
        # No --include => UNSCOPED (all WUs eligible).  Absence of scope.json is
        # the unscoped sentinel; delete any stale object left by a prior
        # --include run or crash so this run is not silently re-scoped.
        ScopeFilter.clear(WORKSPACE_ROOT, path=session_scope_path)

    if scope_ids:
        overlap_rc = _check_scope_overlap(WORKSPACE_ROOT, scope_ids, parsed.allow_overlap)
        if overlap_rc is not None:
            return [], overlap_rc

    return scope_ids, None


def cmd_start(*argv: str) -> int:
    """Run the devbench orchestrate skill non-interactively via the Claude Agent SDK.

    Loads the devbench plugin from the plugin directory adjacent to this package
    and invokes the orchestrate skill, which processes the backlog until all
    work units are complete or blocked.

    Accepts optional scope-filter flags (spec section 4.2.2, AC-190-8, AC-190-9):

    - ``--include "<tokens>"`` -- comma-separated printer-pages-style tokens.
      When supplied, ``ScopeFilter`` is built from the current backlog's WU IDs
      and persisted to the per-session ``scope.json`` path, which the SDK
      subprocess readers resolve via ``session_scope_file_path``.
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
    messages, the inner ``_run`` coroutine checks whether a ``devbench claim``
    Bash tool-use is being requested.  When one is detected AND the drain
    signal file is present, ``_run`` raises :class:`_DrainRequested`.
    ``cmd_start`` catches this sentinel, consumes the drain marker, logs a
    ``[ORCHESTRATOR_DRAIN_ENFORCED]`` audit entry, and returns 0.

    Equivalent to running ``claude --plugin-dir <plugin>`` and invoking
    the orchestrate skill interactively, but suitable for CI/unattended runs.

    Args:
        *argv: Optional flags (``--include``, ``--exclude``, ``--name``,
            ``--allow-overlap``).

    Returns:
        0 on success (including drain-enforced stop), 1 on argument-parse
        error, invalid scope token, or scope overlap without ``--allow-overlap``,
        :data:`ORCHESTRATOR_RESTART_EXIT_CODE` (42) when auto-restart is triggered.

    Raises:
        Nothing from this function's own scope -- all SDK exceptions propagate
        as-is through the asyncio boundary; :class:`_DrainRequested` is
        caught here and handled as a clean exit.
    """
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

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

    # Resolve the per-session scope.json path explicitly (env-free): cmd_start
    # sets DEVBENCH_SESSION_NAME only later, so the env-routed resolver would
    # otherwise target the workspace-root path while the SDK-subprocess readers
    # use the per-session path.  Computing it once here keeps the writer, the
    # clean-exit clear, and the subprocess readers in agreement (#236 follow-up).
    session_scope_path = session_scope_file_path(WORKSPACE_ROOT, parsed.name)

    # Determine the scope IDs for this session and detect any overlap.
    # AC-192-4: build + persist the ScopeFilter when --include is supplied, then
    # consult active sessions for scope overlap.  Both the invalid-token and
    # overlap-detected error paths are handled inside the helper to keep
    # cmd_start's return-statement count under ruff PLR0911's ceiling.
    scope_ids, scope_rc = _resolve_scope_ids_or_error(parsed, session_scope_path)
    if scope_rc is not None:
        return scope_rc

    # AC-192-1/2: Create the per-session state directory and register the session.
    # This must happen before the SDK run so that concurrent sessions can detect
    # each other via the registry.
    _write_session_state_files(WORKSPACE_ROOT, parsed.name, os.getpid(), scope_ids)

    plugin_path = _resolve_plugin_path()

    # Startup gates run immediately after the plugin path is resolved, before
    # any SDK subprocess is spawned: fail closed when guard hooks are not
    # loaded (H4), then warn / fail on uncommitted harness-source edits.
    if (gate_rc := _check_orchestrator_startup_gates(plugin_path)) is not None:
        return gate_rc

    # Resolve the orchestrate-session model from devbench.yaml (orchestrate.model)
    # and fail fast BEFORE spawning the SDK subprocess when it is unset. This is
    # the single source of truth -- the session is pinned to this model so it can
    # never inherit the interactive Claude Code (~/.claude/settings.json) model.
    # No fallback (CLAUDE.md). Scoped to the orchestrator-launch path so other
    # commands (status / report / validate-backlog) load without requiring it.
    try:
        _orchestrator_model = _resolve_orchestrator_model()
    except _OrchestratorModelUnsetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

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
    # can force the in-flight work unit to ``blocked`` before this process exits.
    # The handler reads the current backlog, finds the in-progress WU, sets it to
    # ``blocked``, appends a ``[FORCED_BLOCKED_ON_STOP]`` audit comment, then
    # exits rc=0.  The previous handler is restored in the finally block.
    _session_name_for_sigterm = parsed.name

    def _sigterm_handler(_signum: int, _frame: object) -> None:
        """SIGTERM handler: force in-flight WU to blocked then exit.

        Reads the backlog, locates the single in-progress work unit (if any),
        transitions it to ``blocked``, appends a
        ``[FORCED_BLOCKED_ON_STOP] session=<name>`` audit entry, then calls
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
            _force_block_in_flight_wu(wu, session_name=_session_name_for_sigterm)
        except (OSError, ValueError) as exc:
            logger.error("[SIGTERM_HANDLER_ERROR] could not force-block in-flight WU: %s", exc)
        raise SystemExit(0)

    _prev_sigterm_handler = signal.signal(signal.SIGTERM, _sigterm_handler)

    # Issue #217: capture the SDK's final ResultMessage `result` text so the
    # orchestrator_stop Slack ping can carry the actual exit reason
    # (e.g., ``NO_ACTIONABLE -- 190/212 done, 11 blocked``) instead of the
    # legacy bare ``"clean"`` that hid whether the backlog was finished.
    _sdk_result_text: str | None = None

    # Issue #262 (E10-F1-S3): set to True by ``_run`` when the per-stall
    # continuation counter exhausts its budget so the outer handler can
    # return the distinct exit code without raising an exception.
    _continuation_exhausted: bool = False

    # Set by ``_run`` to the fatal SDK error code (e.g. ``model_not_found``) when a
    # non-retryable error is detected, so the outer handler returns the distinct
    # fatal-error exit code instead of looping. ``None`` when no fatal error.
    _fatal_error_code: str | None = None

    # Block-and-continue (TDI): the within-claim convergence bound BLOCKS a
    # non-converging unit and the orchestrate session CONTINUES to its next
    # in-queue unit rather than halting -- one bad module no longer abandons the
    # rest of a session's scope. ``_non_converging_unit_ids`` accumulates the
    # DISTINCT units that tripped the bound across the WHOLE session (it survives
    # quota-resume re-invocations of ``_run`` because it lives in this closure).
    # When its size reaches the aggregate valve threshold K
    # (:func:`_resolve_max_non_converging_claims`), ``_run`` stops the session and
    # the outer handler emits the ``too many non-converging claims (K)`` stop
    # reason for operator attention.
    _non_converging_unit_ids: set[str] = set()
    _max_non_converging_claims = _resolve_max_non_converging_claims()

    # Set by ``_run`` when the aggregate non-converging-claims valve trips (K
    # distinct units blocked in one session) so the outer handler records the
    # ``too many non-converging claims (K)`` stop reason. ``False`` when the
    # session drained its scope without tripping the valve (block-and-continue
    # ran to a normal NO_ACTIONABLE / ALL_DONE exit).
    _too_many_non_converging: bool = False

    async def _run() -> None:
        """Drive a stateful ClaudeSDKClient session with drain enforcement.

        Opens a single ``ClaudeSDKClient`` context, sends the orchestrate
        prompt, and iterates ``client.receive_response()``.  On each turn
        boundary (ResultMessage), delegates to :func:`_handle_result_message`
        which either logs the terminal exit (breaking out) or issues an
        in-session continuation query (keeping the same session alive).

        A per-stall counter ``stall_count`` increments on every non-terminal
        ResultMessage (or per-message inactivity timeout) and resets to zero
        on genuine progress (``_is_genuine_progress``: a claim, a tool-use, or
        sub-agent Task activity). It is deliberately NOT reset by every
        non-ResultMessage: an arbitrary empty/synthetic message is not progress,
        and resetting on it made the budget unreachable (every turn emits such a
        message before its ResultMessage), letting a no-sentinel-every-turn
        condition loop forever. Resetting on real activity (not only a claim)
        keeps a legitimately long-running unit -- a quiet sub-agent doing a
        terraform apply / make validate -- alive across its inactivity timeouts.
        When ``stall_count`` reaches the cap resolved by
        :func:`_resolve_max_turn_end_continuations`, ``_run`` logs
        ``ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX`` and
        returns, signalling the outer handler via ``_continuation_exhausted``
        (Issue #262 E10-F1-S3).

        A non-retryable fatal SDK error (``detect_fatal_sdk_error``, e.g.
        ``model_not_found``) is detected per message and exits ``_run`` on the
        FIRST occurrence via ``_fatal_error_code`` (the outer handler returns
        ``ORCHESTRATOR_FATAL_ERROR_EXIT_CODE``), never entering the continuation
        loop -- the error recurs identically every turn and only operator action
        (fixing ``orchestrate.model``) can resolve it.

        Each await for the next SDK message is wrapped in
        ``asyncio.wait_for(..., timeout=ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS)``
        via :func:`_iter_messages_with_inactivity_timeout` so long legitimate
        turns never trip it (Issue #262 E10-F2-S1).  A value <= 0 disables the wrap.

        Args: (none -- captures local variables from ``cmd_start`` closure)

        Raises:
            _DrainRequested: A drain signal is present when a ``cmd_claim``
                tool-use is observed.
            _QuotaDetected: A quota error is detected in the SDK message stream.
        """
        nonlocal _sdk_result_text, _continuation_exhausted, _fatal_error_code, _too_many_non_converging
        # Pin the orchestrate session to the devbench.yaml-configured model
        # (resolved + fail-fast-checked in cmd_start). Passing model= here is
        # what prevents the session from inheriting the interactive Claude Code
        # (~/.claude/settings.json) model -- the root cause of the model_not_found
        # runaway. No fallback (CLAUDE.md).
        options = ClaudeAgentOptions(
            plugins=[{"type": "local", "path": str(plugin_path)}],
            permission_mode="bypassPermissions",
            model=_orchestrator_model,
        )
        _orchestrate_prompt = "Run the devbench-orchestrate:orchestrate skill to process the backlog until complete"
        _continuation_budget = _resolve_max_turn_end_continuations()
        _inactivity_timeout = ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS
        # Within-claim convergence bound (config-gated, default on): block a
        # claim that repeats the SAME unresolvable failure rather than churning.
        _convergence_enabled = _resolve_within_claim_convergence_check()
        _convergence_tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=_resolve_max_within_claim_attempts(),
            max_claim_wall_clock_seconds=_resolve_max_claim_wall_clock_seconds(),
            max_no_claim_activity_seconds=_resolve_max_no_claim_activity_seconds(),
            # Tracked-issue 004: the configured target-repo checkout roots, so a
            # whole-suite test-runner failure against a checkout root / bare dir
            # is not counted toward the within-claim bound.
            repo_roots=tuple(str(p) for p in REPO_LOCAL_PATHS.values()),
        )

        def _check_convergence(msg: object) -> bool:
            """Fold *msg* into the convergence tracker; block-and-continue, halt only at the valve.

            Notes a new claim, then observes the message for a repeated failure
            signature. When the bound trips, force-blocks the in-flight unit with
            the ``[CLAIM_NOT_CONVERGING]`` marker (as before) and records the unit
            in the session-wide ``_non_converging_unit_ids`` set.

            Block-and-continue: the just-blocked claim is then CLEARED from the
            tracker and the function returns ``False`` so ``_run`` keeps driving
            the SAME session on to its next in-queue unit -- one bad module no
            longer abandons the rest of the scope. Only when the number of
            DISTINCT non-converging units reaches the aggregate valve threshold K
            does the function set ``_too_many_non_converging`` and return ``True``
            to stop the session for operator attention. A no-op (returns False)
            when the bound is disabled.
            """
            nonlocal _too_many_non_converging
            if not _convergence_enabled:
                return False
            now = time.monotonic()
            claimed = _claimed_unit_id(msg)
            if claimed is not None:
                _convergence_tracker.note_claim(claimed, now=now)
            recurring = _convergence_tracker.observe(msg, now=now)
            if recurring is None:
                return False
            if _convergence_tracker.current_unit_id is None:
                # Inter-claim stall: the orchestrator is active (messages still
                # arriving, so the per-message inactivity timeout never fires)
                # but has claimed no unit for too long -- e.g. an executor still
                # churning AFTER its unit was force-blocked, or a loop stuck
                # without claiming the next unit (the "0 in-progress but
                # hook-logs flowing" wedge). End the session cleanly so the
                # daemon stops instead of hanging (and ignoring SIGTERM); the
                # operator/supervisor restarts it on the remaining backlog.
                logger.warning("%s%s", _ORCHESTRATOR_STOP_REASON_AUDIT_PREFIX, recurring)
                return True
            unit_id = _convergence_tracker.current_unit_id
            # Positively-attributed teardown: the in-session live-command runner
            # records the pgid of the external command it launched into this
            # session's executor.pgid file; read it (None when no live command is
            # registered) and hand it to the block so the executor's subtree is
            # reaped rather than orphaned to init (Item B, tracked issue 015).
            _block_non_converging_claim(unit_id, recurring, executor_pgid=_read_attributed_executor_pgid())
            _non_converging_unit_ids.add(unit_id)
            # Clear the just-blocked claim so its lingering identical-failure
            # messages cannot re-trip the bound for the SAME unit before the skill
            # claims the next one (which would double-count it / re-block it).
            _convergence_tracker.clear_current_claim()
            if len(_non_converging_unit_ids) >= _max_non_converging_claims:
                _too_many_non_converging = True
                logger.warning(
                    "%s%s -- %d distinct units could not converge in this session: %s",
                    _ORCHESTRATOR_STOP_REASON_AUDIT_PREFIX,
                    _too_many_non_converging_reason(len(_non_converging_unit_ids)),
                    len(_non_converging_unit_ids),
                    ", ".join(sorted(_non_converging_unit_ids)),
                )
                return True
            # Block-and-continue: keep working the session's remaining units.
            return False

        async with ClaudeSDKClient(options=options) as client:
            await client.query(_orchestrate_prompt)
            stall_count: int = 0
            while True:
                _exhausted = False
                try:
                    async for message in _iter_messages_with_inactivity_timeout(
                        client.receive_response(), _inactivity_timeout
                    ):
                        logger.info("sdk message: %s", message)
                        # Fatal, non-retryable SDK error (e.g. model_not_found): exit
                        # fast on the FIRST occurrence. Such an error recurs identically
                        # every turn and must never be fed into the continuation loop
                        # (the model_not_found runaway). Checked before the continuation
                        # path; quota errors are NOT matched here -- they route below.
                        if (_fatal := detect_fatal_sdk_error(message)) is not None:
                            logger.info(
                                "%s%s model=%r detail=%r remediation=%s",
                                ORCHESTRATOR_FATAL_ERROR_AUDIT_PREFIX,
                                _fatal,
                                _orchestrator_model,
                                _extract_sdk_result_text(message) or getattr(message, "error", ""),
                                "set orchestrate.model in devbench.yaml to an accessible model and restart",
                            )
                            _fatal_error_code = _fatal
                            return
                        _sdk_result_text = _extract_sdk_result_text(message) or _sdk_result_text
                        # Issue #218: check terminal sentinel on every message that
                        # carries result text; break immediately to avoid re-invoking.
                        if _log_terminal_exit_if_applicable(_sdk_result_text):
                            return
                        # Issue #262 (E10-F1-S2 + E10-F1-S3): handle ResultMessage
                        # turn boundary.  True means a non-terminal continuation was
                        # issued; increment the stall counter and enforce the budget.
                        if await _handle_result_message(message, client):
                            stall_count += 1
                            if stall_count >= _continuation_budget:
                                logger.info(ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX)
                                _continuation_exhausted = True
                                return
                            break
                        # Reset the stall budget on GENUINE PROGRESS (a claim, a
                        # tool-use, or sub-agent Task activity), NOT on every
                        # non-ResultMessage. The prior unconditional reset made the
                        # budget unreachable (every turn emits a message before its
                        # ResultMessage), so a no-sentinel-every-turn condition looped
                        # forever. Resetting ONLY on a claim was too strict -- a
                        # legitimately long-running unit (a quiet sub-agent doing a
                        # terraform apply / make validate) accrues inactivity timeouts
                        # with no new claim and would be killed. Resetting on any real
                        # activity keeps long runs alive while still bounding a true
                        # no-progress / empty-turn loop (a fatal error exits earlier).
                        if _is_genuine_progress(message):
                            stall_count = 0
                        # Within-claim convergence bound (block-and-continue): a
                        # claim that repeats the SAME unresolvable failure beyond
                        # the configured cap (or exceeds the wall-clock backstop)
                        # is force-blocked with [CLAIM_NOT_CONVERGING] and the
                        # session CONTINUES to its next in-queue unit. Only when
                        # the aggregate valve trips (K distinct non-converging
                        # units in one session) does _check_convergence return
                        # True and the loop exit for operator attention.
                        if _check_convergence(message):
                            return
                        # Per-message quota detection (#236) + drain-on-claim
                        # short-circuit (#188/#212), factored into one helper that
                        # raises _QuotaDetected / _DrainRequested. Keeps _run under
                        # ruff PLR0912's 12-branch cap.
                        _check_quota_and_drain(message)
                    else:
                        # receive_response exhausted without a turn-boundary: loop is done.
                        _exhausted = True
                except TimeoutError:
                    # Issue #262 (E10-F2-S1): per-message inactivity timeout fired.
                    # Log the audit prefix and issue an in-session continuation,
                    # counting against the same stall budget (E10-F1-S3).
                    logger.info(ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX)
                    stall_count += 1
                    if stall_count >= _continuation_budget:
                        logger.info(ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX)
                        _continuation_exhausted = True
                        return
                    await client.query(ORCHESTRATOR_CONTINUATION_PROMPT)
                if _exhausted:
                    return

    # Always-fire on exit (PR #202): wrap the SDK loop + state-restoration
    # finally in an outer try/finally that calls notify_orchestrator_stop
    # regardless of how the function exits (clean, drain, SystemExit from
    # the SIGTERM handler, or an uncaught SDK exception).  The notify
    # helper is best-effort so a failure here cannot mask the original
    # exit reason.
    _stop_reason: str = "clean"
    # Issue #236 follow-up: when the quota dispatch requests a graceful drain,
    # the drain signal MUST survive cmd_start's exit so the restart loop / a
    # peer session acts on it.  This flag tells the exit finally blocks below to
    # skip their otherwise-unconditional cancel_drain.
    _quota_drain_requested: bool = False
    try:
        try:
            # TDI: drive the orchestrate session(s) with in-process quota resume.
            # The helper re-opens a FRESH ClaudeSDKClient and re-runs ``_run`` on
            # the remaining backlog after every recovered quota wait (bounded by
            # DEVBENCH_MAX_QUOTA_RESUMES) so a single quota window cannot
            # permanently end an unattended ``--daemon`` run that lacks the
            # external ``make start`` restart wrapper.  All non-recovered exits
            # (clean return, drain enforced, quota fail/drain/keep-waiting,
            # resume-cap exhausted) are returned terminally.
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
            # candidate paths.  Idempotent on already-clean state.  Preserved when
            # the quota dispatch deliberately requested a drain (#236 follow-up).
            _cancel_drain_unless_requested(WORKSPACE_ROOT, _quota_drain_requested)
            # Restore the previous DEVBENCH_SESSION_NAME value so test isolation is
            # maintained when cmd_start is invoked multiple times in the same process.
            _restore_session_env_name(_prev_session_name)
            # Restore the previous SIGTERM handler so test isolation is maintained.
            signal.signal(signal.SIGTERM, _prev_sigterm_handler)

        # AC-190-13: delete scope.json on clean SDK exit so the next run starts
        # without a stale scope.  On crash (SDK raises), the exception propagates
        # before this line runs, intentionally leaving scope.json in place for
        # operator inspection.  Use the explicit per-session path: the inner
        # finally above has already restored DEVBENCH_SESSION_NAME, so the
        # env-routed default would otherwise target the wrong path (#236 follow-up).
        ScopeFilter.clear(WORKSPACE_ROOT, path=session_scope_path)

        # Issue #262 (E10-F1-S3): fail-fast on continuation-budget exhaustion.
        # Issue #217: bubble the SDK's final ResultMessage text into the Slack reason.
        # Issue #271 (E14-F1-S1-T1): distinguish premature turn-end from genuine
        # completion so the stop reason is never the bare literal "clean".
        # Extracted into a helper so cmd_start stays under PLR0912's branch cap.
        restart_rc, _stop_reason = _classify_orchestrator_exit(
            fatal_error_code=_fatal_error_code,
            continuation_exhausted=_continuation_exhausted,
            too_many_non_converging=(len(_non_converging_unit_ids) if _too_many_non_converging else 0),
            sdk_result_text=_sdk_result_text,
        )
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
        # does not inherit a stale request.  cancel_drain scans both the
        # per-session and workspace-root candidate paths and is idempotent
        # on already-clean state.  Best-effort: NameError guards a very
        # early exit before WORKSPACE_ROOT was set; OSError covers
        # permission-denied during unlink.  Preserved when the quota dispatch
        # deliberately requested a drain (#236 follow-up) so it survives exit.
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


# ---------------------------------------------------------------------------
# supervise: interactive `claude` CLI orchestrator under a detached `screen`
# daemon (devbench-supervise-screen-orchestrator). Phase 1 lands the verb
# surface + arg parsing + dispatch; the sub-verb bodies land in later phases.
# ---------------------------------------------------------------------------

_SUPERVISE_SESSION_NAME_RE = re.compile(SUPERVISE_SESSION_NAME_PATTERN)

_SUPERVISE_USAGE: str = (
    "usage: devbench supervise <start|stop|restart|status|info|attach> [options]\n"
    "Supervise an interactive `claude` CLI orchestrator inside a detached `screen`\n"
    "daemon, driven by a pexpect supervisor. --billing-mode selects the channel:\n"
    "subscription (default; the Claude Code subscription's rolling 5-hour windows)\n"
    "or bedrock (AWS Bedrock; no 5-hour windows). AWS creds pass through in both.\n"
    "\n"
    "sub-verbs:\n"
    "  start     Launch a supervised interactive orchestrator under screen\n"
    "  stop      Stop a supervised session (graceful drain, or --hard)\n"
    "  restart   Stop then relaunch preserving session context (--continue)\n"
    "  status    Show per-session state (running/quota-waiting/draining/...)\n"
    "  info      List all supervise screens and how to attach\n"
    "  attach    Observe a running session read-only (no input injection)"
)


@dataclass(frozen=True)
class _SuperviseArgs:
    """Parsed ``devbench supervise <sub>`` flags (FR-2).

    The same flag set serves every sub-verb; each sub-verb consumes the subset
    it needs (``--hard`` for stop, ``--screen`` for attach, scope/model/effort
    for start). Defaults mirror Section 14's ``--help`` snapshots.
    """

    name: str = SUPERVISE_DEFAULT_NAME
    include: str = ""
    exclude: str = ""
    allow_overlap: bool = False
    model: str | None = None
    effort: str | None = None
    billing_mode: str | None = None
    hard: bool = False
    screen: bool = False


def _validate_supervise_name(name: str) -> None:
    """Validate a supervise ``--name`` against the ADR-23 grammar (FR-2).

    Accepts non-empty names matching ``^[A-Za-z0-9][A-Za-z0-9_-]*$`` and rejects
    path-traversal ``..`` segments. Fail-fast with a clear, actionable message.

    Args:
        name: The candidate session name.

    Raises:
        ValueError: *name* is empty, contains a ``..`` segment, or does not
            match the grammar.
    """
    if ".." in Path(name).parts or not _SUPERVISE_SESSION_NAME_RE.match(name):
        raise ValueError(
            f"invalid session name {name!r}: use alphanumerics, hyphen, underscore "
            "(must start with an alphanumeric; no path separators or '..')."
        )


# Flag tokens that take a value, mapped to the ``_SuperviseArgs`` field they set.
_SUPERVISE_VALUE_FLAGS: dict[str, str] = {
    "--name": "name",
    "--include": "include",
    "--exclude": "exclude",
    "--model": "model",
    "--effort": "effort",
    "--billing-mode": "billing_mode",
}
# Boolean flag tokens, mapped to the ``_SuperviseArgs`` field they set ``True``.
_SUPERVISE_BOOL_FLAGS: dict[str, str] = {
    "--allow-overlap": "allow_overlap",
    "--hard": "hard",
    "--screen": "screen",
}


def _parse_supervise_args(args: list[str]) -> _SuperviseArgs:
    """Parse the shared ``supervise`` sub-verb flags (FR-2).

    Table-driven over ``_SUPERVISE_VALUE_FLAGS`` (flags taking a value) and
    ``_SUPERVISE_BOOL_FLAGS`` (boolean flags) so the parser stays flat and a new
    flag is added by extending a table, not the control flow.

    Args:
        args: The flag tokens following the sub-verb.

    Returns:
        A populated :class:`_SuperviseArgs`.

    Raises:
        ValueError: A flag is missing its value or is unknown (fail-fast).
    """
    values: dict[str, str] = {}
    bools: dict[str, bool] = {}

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _SUPERVISE_VALUE_FLAGS:
            if i + 1 >= len(args):
                raise ValueError(f"{arg} requires a value")
            values[_SUPERVISE_VALUE_FLAGS[arg]] = args[i + 1]
            i += 2
        elif arg in _SUPERVISE_BOOL_FLAGS:
            bools[_SUPERVISE_BOOL_FLAGS[arg]] = True
            i += 1
        else:
            raise ValueError(f"unknown flag for 'supervise': {arg!r}")

    return _SuperviseArgs(
        name=values.get("name", SUPERVISE_DEFAULT_NAME),
        include=values.get("include", ""),
        exclude=values.get("exclude", ""),
        allow_overlap=bools.get("allow_overlap", False),
        model=values.get("model"),
        effort=values.get("effort"),
        billing_mode=values.get("billing_mode"),
        hard=bools.get("hard", False),
        screen=bools.get("screen", False),
    )


# ---------------------------------------------------------------------------
# Phase 2 wiring seams. These thin module-level functions isolate the
# config/credentials/backlog dependencies the supervise bodies need so the tests
# can inject deterministic values without a live config or a real backlog. They
# read the SAME single sources of truth the rest of the CLI uses (no duplication).
# ---------------------------------------------------------------------------

#: The subscription credentials file the AuthVerifier checks (Section 3.6.1). A
#: module-level alias so tests point it at a fixture without touching config.py.
SUPERVISE_CREDENTIALS_FILE: Path = CLAUDE_CREDENTIALS_FILE


def _supervise_runtime_config():
    """Return the ``supervise`` config block (single source of truth)."""
    return RUNTIME_CONFIG.supervise


def _supervise_use_bedrock() -> bool:
    """Return the resolved ``use_bedrock`` flag for model-format validation."""
    return USE_BEDROCK


def _resolve_supervise_billing_mode(*, cli_mode: str | None, config_mode: str) -> str:
    """Resolve the supervise billing mode: flag > env > config > default (fail-fast).

    Mirrors the project's ``_resolve_*`` precedence helpers (Section 5.4): the
    ``--billing-mode`` flag wins, else ``DEVBENCH_SUPERVISE_BILLING_MODE`` env,
    else ``supervise.billing_mode`` config, else the documented default. The
    resolved value MUST be a recognized mode; an invalid value at any tier fails
    fast (no silent fallback).

    Args:
        cli_mode: The ``--billing-mode`` flag value (or ``None`` when unset).
        config_mode: ``supervise.billing_mode`` from the config (already
            validated by the loader).

    Returns:
        The resolved billing mode (one of :data:`SUPERVISE_VALID_BILLING_MODES`).

    Raises:
        ValueError: The flag or env value is not a recognized billing mode.
    """
    env_mode = os.environ.get(SUPERVISE_BILLING_MODE_ENV_VAR, "").strip() or None
    for candidate in (cli_mode, env_mode, config_mode):
        if candidate:
            if candidate not in SUPERVISE_VALID_BILLING_MODES:
                valid = ", ".join(sorted(SUPERVISE_VALID_BILLING_MODES))
                raise ValueError(f"supervise billing_mode {candidate!r} is not one of [{valid}].")
            return candidate
    return SUPERVISE_DEFAULT_BILLING_MODE


def _resolve_supervise_progress_stall_seconds(*, config_value: int) -> int:
    """Resolve the progress-watchdog stall window: env > config (fail-fast).

    Mirrors the project's ``_resolve_*`` precedence helpers (Section 5.4): the
    ``DEVBENCH_SUPERVISE_PROGRESS_STALL_SECONDS`` env var wins, else the
    ``supervise.timeouts.progress_stall_seconds`` config value (already validated
    >= 1 by the loader). An env value that is not a parseable integer >= 1 fails
    fast -- a typo must never silently disable the progress watchdog (design point
    2: this is the primary "is real work happening?" gate, so a fail-open here
    would re-open the exact hang class the feature exists to close).

    Unlike the sibling supervise timeouts (which are parse/validate-only and NOT
    env-resolved despite their docstrings -- the only pre-existing
    ``DEVBENCH_SUPERVISE_*`` env override is the billing mode), this resolver makes
    ``progress_stall_seconds`` explicitly env-overridable so an operator can
    widen/narrow the stall window for a single run without editing YAML.

    Args:
        config_value: ``supervise.timeouts.progress_stall_seconds`` from config.

    Returns:
        The resolved stall window in seconds (>= 1).

    Raises:
        ValueError: The env override is set but not a parseable integer >= 1.
    """
    raw = os.environ.get(SUPERVISE_PROGRESS_STALL_SECONDS_ENV_VAR, "").strip()
    if not raw:
        return config_value
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{SUPERVISE_PROGRESS_STALL_SECONDS_ENV_VAR}={raw!r} is not an integer (expected seconds >= 1)."
        ) from exc
    if parsed < 1:
        raise ValueError(f"{SUPERVISE_PROGRESS_STALL_SECONDS_ENV_VAR}={parsed!r} must be >= 1 (seconds).")
    return parsed


def _supervise_backlog_ids() -> list[str]:
    """Return every work-unit ID from the parsed backlog (for scope expansion)."""
    units = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX).parse_index()
    return [u.id for u in units]


def _supervise_in_progress_id() -> str | None:
    """Return the id of the single in-progress work unit, or ``None`` (FR-9).

    Reads the SAME backlog the SDK ``status`` path reads (no claim-audit
    duplication): ``status`` surfaces the currently-claimed work unit so an
    operator can see what the supervised session is working on. A backlog that
    cannot be parsed (none present yet) yields ``None`` rather than failing the
    read-only status verb (a transient absence is not a fault for an observer).
    """
    try:
        units = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX).parse_index()
    except (OSError, ValueError):
        return None
    for unit in units:
        if unit.status is WorkUnitStatus.IN_PROGRESS:
            return unit.id
    return None


def _supervise_live_screen_names() -> set[str]:
    """Return the set of live ``screen`` session names via ``screen -ls`` (FR-11).

    ``screen -ls`` exits 1 when there are NO sockets, so a non-zero return code is
    NOT treated as a failure -- the combined stdout/stderr is parsed by
    :func:`parse_screen_ls`, yielding an empty set for the legitimate "no screens"
    case. A REAL invocation failure (``screen`` not on ``PATH``, an ``OSError``, or
    a ``subprocess`` error) is distinct from "no screens" and FAILS FAST with a
    :class:`SuperviseError` (CLAUDE.md no-silent-failure): a teardown verb (``stop``)
    must not mistake a broken ``screen -ls`` for "the session is gone" and skip the
    drain/quit. Read-only callers (``info``) catch this and degrade with a clear
    note rather than crashing.

    Raises:
        SuperviseError: ``screen`` is not installed, or ``screen -ls`` could not be
            invoked (the distinct failure signal, NOT "no screens").
    """
    screen_path = shutil.which("screen")
    if screen_path is None:
        raise SuperviseError(
            "cannot list screens: 'screen' is not installed "
            "(devcontainer: 'apt-get install -y screen'; macOS: 'brew install screen')."
        )
    invocation_timeout = _supervise_runtime_config().timeouts.command_invocation_seconds
    try:
        result = subprocess.run([screen_path, "-ls"], capture_output=True, text=True, timeout=invocation_timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SuperviseError(f"failed to invoke 'screen -ls': {exc}") from exc
    return parse_screen_ls((result.stdout or "") + (result.stderr or ""))


def _supervise_screen_quit(*, screen_name: str, screen_path: str) -> None:
    """Tear down a supervise screen via ``screen -S <name> -X quit`` (Section 4.2).

    Used by ``stop --hard`` and the graceful-stop escalation to force the screen
    (and the ``__run`` supervisor + ``claude`` child it hosts) to exit. Quitting a
    screen that is already gone exits non-zero ("No screen session found") -- that
    is a NO-OP success for teardown, not a fault, so a non-zero return is tolerated.
    A failure to INVOKE ``screen`` at all (``OSError``) is likewise non-fatal here:
    the registry transition to ``stopped`` is the authoritative operator outcome.

    Args:
        screen_name: The ``screen`` session name (``<prefix><name>``).
        screen_path: The resolved ``screen`` executable path.
    """
    invocation_timeout = _supervise_runtime_config().timeouts.command_invocation_seconds
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            [screen_path, "-S", screen_name, "-X", "quit"],
            capture_output=True,
            text=True,
            timeout=invocation_timeout,
        )


def _supervise_wait_for_terminal(
    *,
    name: str,
    registry: SuperviseRegistry,
    timeout_seconds: int,
    _gate: Callable[[float], None] | None = None,
    _now: Callable[[], float] | None = None,
) -> bool:
    """Wait (event-driven, bounded) for ``__run`` to drive *name* to a terminal (Section 4.2).

    The operator-facing graceful ``stop`` writes the drain + ``stop.request``
    control files, then DELEGATES the actual shutdown to the in-screen ``__run``
    supervisor (it drains the in-flight WU, sends ``/exit``, and records the
    terminal state). This polls the registry until the session reaches a terminal
    state (``stopped`` / ``completed-clean`` / ``faulted``, reusing
    :data:`_SUPERVISE_VACATED_STATES`) or the *timeout_seconds* budget elapses.

    The wait is event-driven, NOT a ``time.sleep`` busy-loop: between reads it
    parks the process on a bounded ``select`` (``poll_interval_seconds``) via the
    injected *_gate* (defaults to :func:`_block_until_readable`), and bounds total
    time with a monotonic clock (*_now*, defaults to ``time.monotonic``).

    Args:
        name: The supervise session name.
        registry: The :class:`SuperviseRegistry` to read.
        timeout_seconds: The graceful-stop budget; on expiry the caller escalates
            to a hard stop (Section 4.2 step 3).
        _gate: Test seam for the per-iteration bounded park; production uses the
            config-driven :func:`_block_until_readable`.
        _now: Test seam for the monotonic clock.

    Returns:
        ``True`` when the session reached a terminal within the budget; ``False``
        on timeout (the caller escalates to hard).
    """
    now = _now if _now is not None else time.monotonic
    if _gate is None:
        poll_interval = _supervise_runtime_config().timeouts.poll_interval_seconds

        def _gate(_remaining: float) -> None:
            _block_until_readable(poll_interval_seconds=min(float(poll_interval), max(_remaining, 0.0)))

    start = now()
    while True:
        state = registry.read_state(name)
        if state is not None and state.state in _SUPERVISE_VACATED_STATES:
            return True
        elapsed = now() - start
        if elapsed >= timeout_seconds:
            return False
        _gate(timeout_seconds - elapsed)


def _supervise_wait_for_running(
    *,
    name: str,
    registry: SuperviseRegistry,
    launch_began_at: datetime,
    timeout_seconds: int,
    _gate: Callable[[float], None] | None = None,
    _now: Callable[[], float] | None = None,
) -> "SuperviseSessionState | str | None":
    """Wait (event-driven, bounded) for the NEW daemon to reach ``running``.

    ``supervise start`` launches the in-screen ``__run`` daemon asynchronously via
    ``screen -dmS`` and the daemon writes its own registry record only once it
    comes up. Reading the registry immediately after launch therefore returns the
    STALE prior record (a ``stopped`` / ``faulted`` leftover from an earlier run),
    which must NEVER be reported as the launch result (tracked issue:
    supervise-start-returns-early-prints-stale-record).

    This distinguishes the NEW record from the stale one by ``started_at``: only a
    record written at or after *launch_began_at* belongs to this launch. It polls
    until that fresh record reaches:

    - ``running`` -> returns the :class:`SuperviseSessionState` (success), or
    - ``faulted`` -> returns the fault ``exit_reason`` string (fail-fast), or
    - the *timeout_seconds* budget elapses with no fresh record reaching either
      -> returns ``None`` (the caller fails fast with a timeout diagnostic).

    The wait is event-driven (a bounded ``select`` park between reads, the same
    mechanism as :func:`_supervise_wait_for_terminal`), not a ``time.sleep``
    busy-loop, and is bounded by a configurable timeout (``ready_prompt_seconds``).

    Args:
        name: The supervise session name.
        registry: The :class:`SuperviseRegistry` to poll.
        launch_began_at: UTC timestamp captured just before the launch; the
            discriminator between the new record and any stale prior one.
        timeout_seconds: Readiness budget; on expiry returns ``None``.
        _gate: Test seam for the per-iteration bounded park.
        _now: Test seam for the monotonic clock.

    Returns:
        The running :class:`SuperviseSessionState` on success, the fault reason
        string on a startup fault, or ``None`` on timeout.
    """
    now = _now if _now is not None else time.monotonic
    if _gate is None:
        poll_interval = _supervise_runtime_config().timeouts.poll_interval_seconds

        def _gate(_remaining: float) -> None:
            _block_until_readable(poll_interval_seconds=min(float(poll_interval), max(_remaining, 0.0)))

    start = now()
    while True:
        state = registry.read_state(name)
        # Only a record written at/after the launch belongs to THIS daemon; a
        # stale prior record (older started_at) is ignored entirely.
        if state is not None and state.started_at >= launch_began_at:
            if state.state == SUPERVISE_STATE_RUNNING:
                return state
            if state.state == SUPERVISE_STATE_FAULTED:
                return state.exit_reason or "startup fault"
        elapsed = now() - start
        if elapsed >= timeout_seconds:
            return None
        _gate(timeout_seconds - elapsed)


def _supervise_launch_screen(
    *,
    name: str,
    screen_name: str,
    env: dict[str, str],
    run_argv: list[str],
    screen_path: str,
) -> int:
    """Create the detached ``screen`` running the ``__run`` supervisor (FR-6).

    ``screen -dmS <screen_name> <run_argv...>`` starts the in-screen supervisor
    in the foreground of the detached session with the minimized *env*. Returns
    the launched ``screen`` PID. The ``__run`` program writes the registry
    ``running`` record once it reaches the running state (Section 4.1 step 4).

    Args:
        name: The supervise session name (for the error message).
        screen_name: The ``screen`` session name (``<prefix><name>``).
        env: The minimized session environment from :class:`EnvSanitizer`.
        run_argv: The ``devbench supervise __run --name <name> ...`` argv.
        screen_path: The resolved ``screen`` executable path.

    Returns:
        The PID of the spawned ``screen`` process.

    Raises:
        SuperviseError: ``screen -dmS`` exited non-zero (fail-fast, FR-30).
    """
    cmd = [screen_path, "-dmS", screen_name, *run_argv]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise SuperviseError(
            f"failed to create screen for supervise session {name!r}: "
            f"'{' '.join(cmd)}' exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.returncode


def _record_tool_version(path: str, version_flag: str = "--version") -> str | None:
    """Return ``<tool> <version_flag>`` output (first line) for the registry (FR-25).

    Never raises: a tool that does not support the flag yields ``None`` so the
    audit record degrades gracefully rather than blocking launch on a version
    probe (the path itself is the load-bearing audit value).
    """
    invocation_timeout = _supervise_runtime_config().timeouts.command_invocation_seconds
    try:
        result = subprocess.run([path, version_flag], capture_output=True, text=True, timeout=invocation_timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or result.stderr).strip().splitlines()
    return out[0] if out else None


def _supervise_preflight(parsed: _SuperviseArgs) -> tuple[str, str, str, str, str] | int:
    """Run the ``start`` preflight (Section 4.1 step 1); return resolved values or rc.

    Returns ``(claude_path, screen_path, model, effort, billing_mode)`` on
    success, or an int exit code (2) when a fail-fast preflight check tripped (the
    message is already on stderr). The AuthVerifier checks are mode-aware: in
    subscription mode they require subscription auth + reject routing vars; in
    bedrock mode they require the AWS Bedrock prerequisites instead.
    """
    screen_path = shutil.which("screen")
    if screen_path is None:
        print(
            "ERROR: 'screen' is not installed. Install it "
            "(devcontainer: 'apt-get install -y screen'; macOS: 'brew install screen') and retry.",
            file=sys.stderr,
        )
        return 2

    cfg = _supervise_runtime_config()
    try:
        billing_mode = _resolve_supervise_billing_mode(cli_mode=parsed.billing_mode, config_mode=cfg.billing_mode)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    verifier = AuthVerifier(credentials_file=SUPERVISE_CREDENTIALS_FILE)
    try:
        verifier.verify(source_env=dict(os.environ), euid=os.geteuid(), billing_mode=billing_mode)
    except SuperviseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        orchestrate_model = None
        with contextlib.suppress(_OrchestratorModelUnsetError):
            orchestrate_model = _resolve_orchestrator_model()
        model = resolve_supervise_model(
            cli_model=parsed.model,
            supervise_model=cfg.model,
            orchestrate_model=orchestrate_model,
            use_bedrock=_supervise_use_bedrock(),
        )
        effort = resolve_supervise_effort(cli_effort=parsed.effort, supervise_effort=cfg.effort)
    except (SuperviseError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        claude_path = require_claude(which=shutil.which)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return claude_path, screen_path, model, effort, billing_mode


# Registry states that mean a same-named session is no longer occupying the name
# (a fresh ``start`` may reuse it). Anything else means "already running" (FR-2).
_SUPERVISE_VACATED_STATES: frozenset[str] = frozenset(
    {SUPERVISE_STATE_STOPPED, SUPERVISE_STATE_COMPLETED_CLEAN, SUPERVISE_STATE_FAULTED}
)


def _supervise_start_under_flock(
    parsed: _SuperviseArgs,
    *,
    registry: SuperviseRegistry,
    model: str,
    billing_mode: str,
    screen_path: str,
    cfg,
) -> int:
    """Run the flocked portion of ``start`` (Section 4.1 steps 2-5); return rc.

    Holds ``flock_backlog`` while it checks for a same-named running session,
    expands + persists the scope, arbitrates overlap against the SDK registry,
    builds the minimized env, and launches the screen + ``__run``. Returns 0 when
    the launch was issued, or a non-zero exit code on a fail-fast condition (the
    message is already on stderr).
    """
    with flock_backlog(WORKSPACE_ROOT):
        # A same-named session still occupying the name is a fail-fast error.
        existing = registry.read_state(parsed.name)
        if existing is not None and registry.is_alive(existing.pid) and existing.state not in _SUPERVISE_VACATED_STATES:
            print(
                f"ERROR: supervise session {parsed.name!r} already running (pid {existing.pid}); "
                "use 'supervise restart' or a different --name.",
                file=sys.stderr,
            )
            return 2

        # Expand + persist the scope (Section 5.6, step 4a) BEFORE launch.
        try:
            scope_ids = write_session_scope(
                workspace_root=WORKSPACE_ROOT,
                session_name=parsed.name,
                include=parsed.include,
                exclude=parsed.exclude,
                backlog_ids=_supervise_backlog_ids(),
            )
        except InvalidScopeError as exc:
            print(f"ERROR: invalid scope token: {exc}", file=sys.stderr)
            return 2

        # Multi-session arbitration against the SDK SessionRegistry (FR-18).
        if (overlap_rc := _check_scope_overlap(WORKSPACE_ROOT, scope_ids, parsed.allow_overlap)) is not None:
            return overlap_rc

        # Build the minimized screen env (Section 3.6.1, 5.6). The interactive
        # billing model is the --model flag; DEVBENCH_CLAUDE_MODEL is the
        # import-time model the in-session subprocesses need.
        env = EnvSanitizer(extra_deny_vars=cfg.env.deny_vars, billing_mode=billing_mode).build(
            source_env=dict(os.environ),
            workspace_root=str(WORKSPACE_ROOT),
            session_name=parsed.name,
            import_model=model,
        )
        run_argv = [
            "uv",
            "run",
            "devbench",
            "supervise",
            SUPERVISE_INTERNAL_RUN_SUBVERB,
            "--name",
            parsed.name,
            "--model",
            model,
            "--billing-mode",
            billing_mode,
        ]
        if parsed.effort:
            run_argv += ["--effort", parsed.effort]

        _supervise_launch_screen(
            name=parsed.name,
            screen_name=screen_session_name(parsed.name, prefix=cfg.screen_name_prefix),
            env=env,
            run_argv=run_argv,
            screen_path=screen_path,
        )
    return 0


def _cmd_supervise_start(parsed: _SuperviseArgs) -> int:
    """``supervise start`` body: preflight -> scope -> screen+__run -> running (FR-3..8)."""
    preflight = _supervise_preflight(parsed)
    if isinstance(preflight, int):
        return preflight
    _claude_path, screen_path, model, _effort, billing_mode = preflight
    cfg = _supervise_runtime_config()

    registry = SuperviseRegistry(WORKSPACE_ROOT)
    # Capture the launch instant BEFORE the (asynchronous) screen launch so the
    # readiness wait can tell the NEW daemon's record (started_at >= this) apart
    # from any stale prior record. The screen daemon writes its own record only
    # once it comes up, so reading the registry immediately after launch would
    # otherwise surface a stopped/faulted leftover from an earlier run.
    launch_began_at = datetime.now(UTC)
    try:
        launch_rc = _supervise_start_under_flock(
            parsed, registry=registry, model=model, billing_mode=billing_mode, screen_path=screen_path, cfg=cfg
        )
    except (TimeoutError, OSError, SuperviseError, ClaimRaceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if launch_rc != 0:
        return launch_rc

    # Wait (event-driven, bounded by the configurable ready-prompt timeout) for
    # the NEW daemon to reach running (Section 4.1 step 4). Never report the
    # stale prior record (tracked issue: supervise-start-returns-early).
    outcome = _supervise_wait_for_running(
        name=parsed.name,
        registry=registry,
        launch_began_at=launch_began_at,
        timeout_seconds=cfg.timeouts.ready_prompt_seconds,
    )
    if outcome is None:
        print(
            f"ERROR: supervise session {parsed.name!r} did not reach running within "
            f"{cfg.timeouts.ready_prompt_seconds}s; check 'supervise status --name {parsed.name}' "
            "and the session pty.log for the startup fault.",
            file=sys.stderr,
        )
        return 1
    if isinstance(outcome, str):
        print(
            f"ERROR: supervise session {parsed.name!r} faulted during startup ({outcome}).",
            file=sys.stderr,
        )
        return 1
    print(
        f"[supervise] state={outcome.state} pid={outcome.pid} screen={outcome.screen_name} "
        f"claude-session={normalize_resume_id_for_display(outcome.claude_session_id)}",
    )
    return 0


def _supervise_spawn_child(*, launch_argv: list[str], cfg) -> Any:
    """Spawn the interactive ``claude`` child via ``pexpect.spawn`` (Section 4.1 step 5)."""
    return pexpect.spawn(
        launch_argv[0],
        args=launch_argv[1:],
        env=dict(os.environ),
        encoding="utf-8",
        timeout=cfg.timeouts.ready_prompt_seconds,
    )


def _supervise_orchestrator_log_path(cfg) -> Path:
    """Resolve the orchestrator log the hybrid log-tail detector watches (FR-14)."""
    return WORKSPACE_ROOT / cfg.log_tail.orchestrator_log_relpath


def _make_supervise_relaunch(
    *,
    cfg,
    driver: PtyDriver,
    claude_path: str,
    model: str,
    effort: str,
    plugin_dir: str,
    state,
) -> Callable[..., None]:
    """Return the relaunch closure the event loop calls on restart / quota-resume (Section 4.3).

    Re-spawns ``claude`` with the resume flags (``--continue`` / ``--resume <id>``
    per ``resume_mode``, via the REUSED ``build_resume_argv``), re-runs kickoff,
    and rebinds *driver* to the fresh child so the loop continues against it. The
    loop passes ``reason``/``resume`` kwargs (advisory audit context); the
    relaunch always uses the resume flags, so they are accepted and not branched on.
    """

    def _relaunch(**_loop_context: object) -> None:
        resume_argv = build_resume_argv(
            claude_path=claude_path,
            model=model,
            effort=effort,
            plugin_dir=plugin_dir,
            restart_config=cfg.restart,
            claude_session_id=state.claude_session_id,
        )
        driver.child = _supervise_spawn_child(launch_argv=resume_argv, cfg=cfg)
        run_supervised_kickoff(
            driver=driver,
            injectable_commands=cfg.injectable_commands,
            ready_timeout_seconds=cfg.timeouts.ready_prompt_seconds,
            command_ack_seconds=cfg.timeouts.command_ack_seconds,
            command_submit_quiet_seconds=cfg.timeouts.command_submit_quiet_seconds,
            command_submit_settle_seconds=cfg.timeouts.command_submit_settle_seconds,
        )

    return _relaunch


def _make_supervise_quota_wait_persister(
    *, registry: SuperviseRegistry, state: SuperviseSessionState
) -> Callable[[datetime | None, int], None]:
    """Return the callback the event loop invokes when it enters ``quota-waiting``.

    It persists ``state=quota-waiting`` with the parsed ``expected-resume`` and the
    current ``resumes-used`` to the registry BEFORE the (possibly long) wait begins, so
    a concurrent ``supervise status`` from another process surfaces the holding state
    and the provider reset time (FR-10, FR-16, Goal G-3). It mutates the same in-memory
    ``state`` the run body re-stamps with the terminal result afterward.
    """

    def _persist(reset_at: datetime | None, resumes_used: int) -> None:
        state.state = SUPERVISE_STATE_QUOTA_WAITING
        state.expected_resume = reset_at
        state.resumes_used = resumes_used
        state.last_activity = datetime.now(UTC)
        registry.write_state(state)

    return _persist


def _make_supervise_activity_persister(
    *,
    registry: SuperviseRegistry,
    state: SuperviseSessionState,
    min_interval_seconds: int,
    _now: Callable[[], datetime] | None = None,
) -> Callable[[], None]:
    """Return the callback the event loop invokes on observed PTY activity.

    It refreshes ``state.last_activity`` and persists the record so a concurrent
    ``supervise status`` reflects true liveness -- a session actively producing
    PTY output is not mistaken for a hung one (tracked issue:
    supervise-status-last-activity-stale-during-active-work). The registry write
    is THROTTLED to at most once per *min_interval_seconds* so a chatty session
    does not hammer the registry on every read chunk; the in-flight loop observes
    activity far more often than the status surface needs to advance. A
    *min_interval_seconds* of ``0`` writes on every call (no throttle).
    """
    now = _now if _now is not None else (lambda: datetime.now(UTC))
    last_write: list[datetime | None] = [None]

    def _persist() -> None:
        current = now()
        previous = last_write[0]
        if previous is not None and (current - previous).total_seconds() < min_interval_seconds:
            return
        state.last_activity = current
        registry.write_state(state)
        last_write[0] = current

    return _persist


def _cmd_supervise_run(parsed: _SuperviseArgs) -> int:
    """Hidden ``supervise __run`` body: the pexpect supervisor inside the screen (D-10).

    Spawns the interactive ``claude`` child, drives launch -> ready -> kickoff ->
    running (Section 4.1 steps 5-8), then runs the event loop (Section 4.8) until a
    terminal: clean -> exit 0; fault -> classified non-zero; quota -> wait-and-resume
    (NEVER an exit); restart-signal -> bounded relaunch. The final state + exit
    reason are recorded in the registry (FR-13, FR-27).
    """
    cfg = _supervise_runtime_config()
    # Resolve the progress-watchdog stall window (env > yaml > default) and fold the
    # result back into cfg so the event loop reads the env-overridden value (design
    # point 2). A bad env value fails fast -- the watchdog must never be silently
    # disabled (it is the primary "is real work happening?" gate).
    try:
        resolved_stall = _resolve_supervise_progress_stall_seconds(config_value=cfg.timeouts.progress_stall_seconds)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if resolved_stall != cfg.timeouts.progress_stall_seconds:
        cfg = replace(cfg, timeouts=replace(cfg.timeouts, progress_stall_seconds=resolved_stall))
    try:
        claude_path = require_claude(which=shutil.which)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # The billing mode was resolved at ``start`` and forwarded via --billing-mode;
    # re-resolve here (flag > env > config > default) so __run honours the same
    # precedence even if invoked directly, and fail fast on an invalid value.
    try:
        billing_mode = _resolve_supervise_billing_mode(cli_mode=parsed.billing_mode, config_mode=cfg.billing_mode)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    model = parsed.model or ""
    effort = resolve_supervise_effort(cli_effort=parsed.effort, supervise_effort=cfg.effort)
    plugin_dir = str(_resolve_plugin_path())
    launch_argv = build_claude_launch_argv(
        claude_path=claude_path,
        model=model,
        effort=effort,
        plugin_dir=plugin_dir,
    )

    registry = SuperviseRegistry(WORKSPACE_ROOT)
    screen_name = screen_session_name(parsed.name, prefix=cfg.screen_name_prefix)
    log_writer = PtyLogWriter(
        path=supervise_pty_log_path(WORKSPACE_ROOT, parsed.name),
        redact_patterns=cfg.logging.redact_patterns,
    )
    patterns = DetectionPatterns(cfg.detection_patterns)
    child = _supervise_spawn_child(launch_argv=launch_argv, cfg=cfg)
    driver = PtyDriver(child=child, patterns=patterns, log_writer=log_writer)

    state = new_session_state(
        name=parsed.name,
        pid=os.getpid(),
        screen_name=screen_name,
        model=model,
        effort=effort,
        started_by=getpass.getuser(),
        billing_mode=billing_mode,
    )
    state.claude_path = claude_path
    state.claude_version = _record_tool_version(claude_path)
    registry.write_state(state)

    try:
        run_supervised_kickoff(
            driver=driver,
            injectable_commands=cfg.injectable_commands,
            ready_timeout_seconds=cfg.timeouts.ready_prompt_seconds,
            command_ack_seconds=cfg.timeouts.command_ack_seconds,
            command_submit_quiet_seconds=cfg.timeouts.command_submit_quiet_seconds,
            command_submit_settle_seconds=cfg.timeouts.command_submit_settle_seconds,
        )
    except SuperviseReadyTimeoutError as exc:
        state.state = SUPERVISE_STATE_FAULTED
        state.exit_reason = "ready-prompt-timeout"
        registry.write_state(state)
        log_writer.close()
        with contextlib.suppress(Exception):
            child.terminate(force=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    state.state = SUPERVISE_STATE_RUNNING
    state.last_activity = datetime.now(UTC)
    registry.write_state(state)

    # Run the event loop (Section 4.8) until a terminal. Quota/restart are handled
    # inside the loop; quota NEVER exits non-zero (FR-13).
    quota_waiter = build_quota_waiter(
        patterns=patterns,
        config=cfg,
        workspace_root=WORKSPACE_ROOT,
        session_name=parsed.name,
        billing_mode=billing_mode,
    )
    log_tail = LogTailDetector(log_path=_supervise_orchestrator_log_path(cfg), config=cfg.log_tail)
    relaunch = _make_supervise_relaunch(
        cfg=cfg,
        driver=driver,
        claude_path=claude_path,
        model=model,
        effort=effort,
        plugin_dir=plugin_dir,
        state=state,
    )
    on_quota_wait = _make_supervise_quota_wait_persister(registry=registry, state=state)
    # Refresh last_activity on observed PTY activity (throttled to the registry
    # poll cadence) so supervise status distinguishes a busy session from a hung
    # one without an operator having to stat the pty.log (tracked issue:
    # supervise-status-last-activity-stale-during-active-work).
    on_activity = _make_supervise_activity_persister(
        registry=registry, state=state, min_interval_seconds=cfg.timeouts.poll_interval_seconds
    )
    result: EventLoopResult = run_supervise_event_loop(
        driver=driver,
        config=cfg,
        quota_waiter=quota_waiter,
        log_poll=log_tail.poll,
        relaunch=relaunch,
        stop_poll=lambda: read_stop_request(WORKSPACE_ROOT, parsed.name),
        on_quota_wait=on_quota_wait,
        on_activity=on_activity,
        # PROGRESS WATCHDOG (design point 2): the same LogTailDetector that scrapes
        # the orchestrator log for terminal markers also reports whether that log
        # GREW (progressed), which is the watchdog's "is real work happening?"
        # signal. The watched file (_supervise_orchestrator_log_path) is the SAME
        # file the in-session devbench subprocesses' setup_logging writes to.
        progress_poll=log_tail.progressed,
    )

    state.state = result.final_state
    state.exit_reason = result.exit_reason
    state.restart_count = result.restarts_used
    state.resumes_used = result.resumes_used
    state.last_activity = datetime.now(UTC)
    registry.write_state(state)
    log_writer.close()
    with contextlib.suppress(Exception):
        driver.child.terminate(force=True)
    return result.exit_code


def _cmd_supervise_attach(parsed: _SuperviseArgs) -> int:
    """``supervise attach`` body: read-only PTY-log follow (Section 4.7, FR-26).

    The default (no flags) is the ALWAYS-SAFE read-only follow of the redacted
    ``pty.log`` -- a pure read of a file the ``__run`` supervisor writes, with the
    attaching process's stdin NEVER connected to the ``claude`` TTY, so an
    observer cannot inject input or steal the PTY (AC-18). ``Ctrl-C`` stops the
    tail (the supervisor and orchestration are untouched), returning 0.

    The input-capable native ``screen -x`` path (``--screen``) stays
    fail-fast-disabled until DI-4 verifies its ACL blocks all input (AC-33,
    Section 3.6.5); the supervisor never silently upgrades a read-only attach to a
    writable one.

    Errors: unknown ``--name`` -> exit 2; no ``pty.log`` yet -> exit 2 (fail-fast,
    FR-30, rather than hanging on a transcript that does not exist).
    """
    if parsed.screen:
        print(
            "ERROR: --screen attach is not enabled (input-blocking not yet verified; use read-only attach).",
            file=sys.stderr,
        )
        return 2

    registry = SuperviseRegistry(WORKSPACE_ROOT)
    if registry.read_state(parsed.name) is None:
        print(f"ERROR: no supervise session named {parsed.name!r}.", file=sys.stderr)
        return 2

    log_path = supervise_pty_log_path(WORKSPACE_ROOT, parsed.name)
    if not log_path.exists():
        print(
            f"ERROR: no PTY transcript to follow for supervise session {parsed.name!r} yet (expected at '{log_path}').",
            file=sys.stderr,
        )
        return 2

    print(
        f"[supervise] read-only observation of {parsed.name!r}. The pexpect supervisor owns stdin; "
        "you are watching the PTY transcript tail. Press Ctrl-C to stop watching "
        "(this does NOT stop the orchestration).",
    )

    def _write(chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()

    # The follow loop is event-driven and read-only: stdin is never wired to the
    # child. Between reads it PARKS the process on a bounded ``select`` (the
    # config-driven poll interval) via ``_block_until_readable`` rather than
    # spinning -- a true ``tail -F``, not a CPU busy-loop (Section 4.7, CLAUDE.md
    # Section 7.5). Ctrl-C (KeyboardInterrupt) ends the follow with exit 0; it
    # interrupts the blocking ``select`` so the operator never waits a full
    # interval to stop watching.
    poll_interval = _supervise_runtime_config().timeouts.poll_interval_seconds

    def _block() -> None:
        _block_until_readable(poll_interval_seconds=float(poll_interval))

    try:
        follow_pty_log(log_path, write=_write, should_continue=lambda: True, block=_block)
    except KeyboardInterrupt:
        print("\n[supervise] stopped watching (orchestration continues).")
    return 0


def _cmd_supervise_stop(parsed: _SuperviseArgs) -> int:
    """``supervise stop`` body: graceful drain-then-stop / hard / stale reconcile (Section 4.2, FR-5).

    Graceful (default): write the per-session ``drain.signal`` + the
    ``stop.request`` control file the in-screen ``__run`` supervisor polls, then
    WAIT (event-driven, bounded by ``supervise.timeouts.graceful_stop_seconds``)
    for ``__run`` to drain the in-flight WU, send ``/exit``, and record a terminal
    state. The operator stop does NOT stamp ``stopped`` itself -- the in-screen
    supervisor owns the teardown. On timeout the verb ESCALATES to hard (Section
    4.2 step 3).

    Hard (``--hard``): tear the screen down via ``screen -S <screen> -X quit`` (the
    ``__run`` supervisor + ``claude`` child die with it), then record
    ``stopped exit-reason=hard-stop``.

    Stale-screen reconcile: when the registry says the session is running but
    ``screen -ls`` no longer lists its screen, there is no supervisor to drain --
    reconcile to ``stopped`` (``stale-screen-reconciled``) and return 0 with a
    note. A failure to LIST screens (distinct from "no screens") fails fast (FR-30).

    An unknown ``--name`` exits 2 (fail-fast, FR-30).
    """
    registry = SuperviseRegistry(WORKSPACE_ROOT)
    state = registry.read_state(parsed.name)
    if state is None:
        print(f"ERROR: no supervise session named {parsed.name!r}.", file=sys.stderr)
        return 2

    try:
        live_screens = _supervise_live_screen_names()
    except SuperviseError as exc:
        # A broken ``screen -ls`` must NOT be mistaken for "the session is gone"
        # (that would skip the teardown); fail fast (FR-30, CLAUDE.md).
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if parsed.hard:
        return _supervise_stop_hard(parsed.name, state, registry)
    return _supervise_stop_graceful(parsed.name, state, registry, live_screens)


def _supervise_stop_hard(name: str, state: SuperviseSessionState, registry: SuperviseRegistry) -> int:
    """Hard-stop *name*: quit the screen, then record ``stopped`` (Section 4.2)."""
    screen_path = shutil.which("screen")
    if screen_path is not None:
        _supervise_screen_quit(screen_name=state.screen_name, screen_path=screen_path)
    state.state = SUPERVISE_STATE_STOPPED
    state.exit_reason = SUPERVISE_EXIT_REASON_HARD_STOP
    registry.write_state(state)
    print(f"[supervise] state=stopped name={name} mode=hard (screen quit).")
    return 0


def _supervise_stop_graceful(
    name: str,
    state: SuperviseSessionState,
    registry: SuperviseRegistry,
    live_screens: set[str],
) -> int:
    """Graceful-stop *name*: signal ``__run``, wait, escalate to hard on timeout (Section 4.2)."""
    # Stale-screen reconcile: a registry-running session whose screen is gone has
    # no supervisor to drain; reconcile rather than write a pointless drain signal.
    if state.screen_name not in live_screens:
        state.state = SUPERVISE_STATE_STOPPED
        state.exit_reason = SUPERVISE_EXIT_REASON_STALE_RECONCILED
        registry.write_state(state)
        print(f"[supervise] state=stopped name={name} (stale screen reconciled; no live supervisor).")
        return 0

    # Signal __run: write the per-session drain signal + the stop.request the
    # __run event loop polls to enter ``draining`` (Section 4.2 step 1-2).
    prev = os.environ.get("DEVBENCH_SESSION_NAME")
    os.environ["DEVBENCH_SESSION_NAME"] = name
    try:
        request_drain(WORKSPACE_ROOT, reason="supervise stop")
    finally:
        _restore_session_env_name(prev)
    write_stop_request(WORKSPACE_ROOT, name)

    # Delegate the teardown to __run: wait (bounded) for it to reach a terminal.
    cfg = _supervise_runtime_config()
    reached = _supervise_wait_for_terminal(
        name=name, registry=registry, timeout_seconds=cfg.timeouts.graceful_stop_seconds
    )
    if reached:
        final = registry.read_state(name)
        final_state = final.state if final is not None else SUPERVISE_STATE_STOPPED
        print(f"[supervise] state={final_state} name={name} mode=graceful (drained by supervisor).")
        return 0

    # Graceful budget expired without __run winding down: escalate to hard.
    print(f"[supervise] graceful stop of {name!r} exceeded budget; escalating to hard stop.", file=sys.stderr)
    return _supervise_stop_hard(name, state, registry)


def _cmd_supervise_status(parsed: _SuperviseArgs, *, name_given: bool) -> int:
    """``supervise status`` body: per-session or all-session state (Section 4.4, FR-9/FR-10).

    With an explicit ``--name`` (``name_given``), prints exactly that session's
    line and exits 2 on an unknown name (fail-fast). Without ``--name``, lists
    every supervise session (``No supervise sessions.`` when none exist). Each
    line surfaces ``billing-channel: subscription`` and, for ``quota-waiting``,
    the ``expected-resume`` + ``resumes-used=<n>/<cap>`` (FR-10), reusing
    :func:`_resolve_max_quota_resumes` for the cap (DRY).
    """
    registry = SuperviseRegistry(WORKSPACE_ROOT)
    max_resumes = _resolve_max_quota_resumes()

    if name_given:
        state = registry.read_state(parsed.name)
        if state is None:
            print(f"ERROR: no supervise session named {parsed.name!r}.", file=sys.stderr)
            return 2
        print(format_status_line(state, max_resumes=max_resumes, in_progress=_supervise_in_progress_id()))
        return 0

    sessions = sorted(registry.load(), key=lambda s: s.name)
    if not sessions:
        print("No supervise sessions.")
        return 0
    in_progress = _supervise_in_progress_id()
    for state in sessions:
        print(format_status_line(state, max_resumes=max_resumes, in_progress=in_progress))
    return 0


def _cmd_supervise_info(_parsed: _SuperviseArgs) -> int:
    """``supervise info`` body: join ``screen -ls`` with the registry (Section 4.5, FR-11).

    Lists every supervise screen reconciled against the registry: a live screen
    with no registry entry is ``unknown`` (orphan); a registry entry with no live
    screen is ``stale``. The ATTACH column is the exact ``supervise attach
    --name N`` command. Always exit 0 (read-only listing).

    Read-only degradation: ``info`` MUST still list the registry when ``screen
    -ls`` cannot be invoked, but it surfaces a distinct ``screen list unavailable``
    note so "screen unavailable" is NOT silently indistinguishable from "no live
    screens" (every session would otherwise reconcile to ``stale``). The teardown
    verb (``stop``) fails fast on the same condition; ``info`` does not.
    """
    registry = SuperviseRegistry(WORKSPACE_ROOT)
    cfg = _supervise_runtime_config()
    try:
        screen_names = _supervise_live_screen_names()
    except SuperviseError as exc:
        print(f"[supervise] screen list unavailable ({exc}); sessions shown from the registry only.")
        screen_names = set()
    rows = reconcile_info_rows(
        sessions=registry.load(),
        screen_names=screen_names,
        prefix=cfg.screen_name_prefix,
    )
    if not rows:
        print("No supervise screens.")
        return 0

    header = f"{'SCREEN':<32} {'NAME':<16} {'STATE':<14} {'PID':>8}  {'CLAUDE-SESSION':<18} {'BILLING':<13} ATTACH"
    print(header)
    print("-" * len(header))
    for row in rows:
        pid_str = str(row.pid) if row.pid is not None else "-"
        sanitized_session = sanitize_resume_id(row.claude_session)
        claude_session = sanitized_session if sanitized_session is not None else "-"
        print(
            f"{row.screen:<32} {row.name:<16} {row.state:<14} {pid_str:>8}  "
            f"{claude_session:<18} {row.billing:<13} {row.attach}"
        )
    return 0


def _cmd_supervise_restart(parsed: _SuperviseArgs) -> int:
    """``supervise restart`` body: graceful stop then start, preserving context (Section 4.3, FR-12).

    Performs ``stop --name N`` (graceful, capturing the claude session id from the
    registry), then ``start --name N`` -- the start path relaunches via the resume
    flags (``--continue``/``--resume``, REUSED from ``build_resume_argv``) so
    orchestration context is preserved. A failed stop short-circuits (start is not
    attempted). An unknown ``--name`` exits 2.
    """
    registry = SuperviseRegistry(WORKSPACE_ROOT)
    if registry.read_state(parsed.name) is None:
        print(f"ERROR: no supervise session named {parsed.name!r}.", file=sys.stderr)
        return 2

    stop_rc = _cmd_supervise_stop(parsed)
    if stop_rc != 0:
        return stop_rc
    return _cmd_supervise_start(parsed)


def _dispatch_supervise_subverb(sub: str, args: list[str]) -> int:
    """Route a validated supervise sub-verb to its body.

    All six operator sub-verbs are implemented: ``start`` (the launch pipeline),
    ``stop`` (graceful drain-then-stop + stale reconcile), ``restart`` (stop+start
    preserving context, Section 4.3), ``status`` (per/all-session state, FR-9/10),
    ``info`` (screen-ls join + reconcile, FR-11), ``attach`` (read-only PTY-log
    follow, with ``--screen`` fail-fast-gated on DI-4, FR-26/AC-33), plus the
    hidden internal ``__run`` (the program ``screen`` runs, D-10).

    ``status`` distinguishes an explicit ``--name`` (one session, unknown -> exit
    2) from no ``--name`` (list all), so the operator-facing ``--name`` default
    does not mask a missing single session.

    Args:
        sub: The sub-verb token (already confirmed to be a known sub-verb).
        args: The remaining flag tokens.

    Returns:
        The sub-verb's exit code.
    """
    parsed = _parse_supervise_args(args)
    # ``status`` is the only body that needs the "explicit --name" distinction
    # (one session vs list all), so it is handled outside the single-arg table.
    if sub == "status":
        return _cmd_supervise_status(parsed, name_given="--name" in args)
    # A name->handler table keeps the dispatcher flat (one return) and DRY: a new
    # single-arg sub-verb is added by extending the table, not the control flow.
    handlers: dict[str, Callable[[_SuperviseArgs], int]] = {
        "start": _cmd_supervise_start,
        SUPERVISE_INTERNAL_RUN_SUBVERB: _cmd_supervise_run,
        "stop": _cmd_supervise_stop,
        "restart": _cmd_supervise_restart,
        "info": _cmd_supervise_info,
        "attach": _cmd_supervise_attach,
    }
    return handlers[sub](parsed)


def cmd_supervise(*argv: str) -> int:
    """Dispatch the ``devbench supervise`` verb group (FR-1).

    Sub-verbs: ``start``, ``stop``, ``restart``, ``status``, ``info``,
    ``attach`` (plus the hidden internal ``__run`` the screen daemon runs,
    D-10). Dispatches on ``argv[0]``; an unknown sub-verb (or none) exits 2 with
    a usage message listing the six operator sub-verbs. ``--name`` is validated
    against the ADR-23 grammar (FR-2) before any body runs.

    Args:
        *argv: The sub-verb followed by its flags.

    Returns:
        2 on unknown sub-verb / invalid argument; otherwise the sub-verb's exit
        code (later phases). Phase 1 sub-verb bodies raise ``NotImplementedError``.
    """
    if not argv:
        print(_SUPERVISE_USAGE, file=sys.stderr)
        return 2

    sub = argv[0]
    rest = list(argv[1:])

    known = (*SUPERVISE_SUBVERBS, SUPERVISE_INTERNAL_RUN_SUBVERB)
    if sub not in known:
        print(
            f"ERROR: unknown 'supervise' sub-verb {sub!r}. Use: {'|'.join(SUPERVISE_SUBVERBS)}.",
            file=sys.stderr,
        )
        return 2

    # Validate --name (FR-2) before any body runs so a crafted name fails fast.
    try:
        parsed = _parse_supervise_args(rest)
        _validate_supervise_name(parsed.name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return _dispatch_supervise_subverb(sub, rest)


def cmd_quota_watcher(*argv: str) -> int:
    """Inspect the quota pause checkpoint.

    Usage::

        devbench quota-watcher --once    # print current pause status then exit

    Reads the checkpoint file at ``<workspace>/.devbench/quota_pause.json`` and
    prints the current quota pause status to stdout. Returns 0 when a checkpoint
    is found, 1 when absent or the flag is missing/unknown.

    The watcher is advisory: when a running orchestrator owns the session, its
    in-loop wait is authoritative. The watcher simply surfaces the checkpoint
    data for operator visibility. (TDI-003b: the unimplemented ``--daemon``
    background-monitor stub was removed; ``--once`` is the only supported flag.)

    Args:
        *argv: The ``--once`` flag.

    Returns:
        0 when ``--once`` is given and a checkpoint exists.
        1 on parse errors, absent checkpoint, or unsupported flag.
    """
    if not argv or argv[0] != "--once":
        print(
            "ERROR: quota-watcher requires --once.\nUsage: devbench quota-watcher --once",
            file=sys.stderr,
        )
        return 1

    # --once: read the checkpoint and print status.
    checkpoint = load_checkpoint(WORKSPACE_ROOT)
    if checkpoint is None:
        print("No quota pause checkpoint found -- orchestrator is not waiting.", file=sys.stdout)
        return 1

    reset_at_str = checkpoint.reset_at.isoformat() if checkpoint.reset_at is not None else "unknown"
    print(
        f"{_QUOTA_WAITING_AUDIT_PREFIX} reason={checkpoint.reason} reset_at={reset_at_str}",
        file=sys.stdout,
    )
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
    and started_by metadata from scope.json.  Exits 0 in both cases (file
    absent and file present).

    Uses ``[]`` key access (not ``.get()`` with defaults) so a corrupt
    scope.json with missing required fields raises ``KeyError`` immediately
    (fail-fast) instead of silently masking the corruption with empty values.

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
        data = json.loads(target_path.read_text(encoding="utf-8"))
        include = data["include"]
        exclude = data["exclude"]
        expanded_ids = data["expanded_ids"]
        started_at = data["started_at"]
        started_by = data["started_by"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
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
    reachable search roots (override via ``DEVBENCH_INSTANCE_SEARCH_ROOTS``,
    default home directory), filters to live PIDs, and prints either a
    human-readable table (TTY default) or a JSON array (``--json``).

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
        devbench drain --status               -- print workspace-root drain state; rc=0
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
        state = read_drain_state(WORKSPACE_ROOT)
        if state is None:
            print("no drain pending")
        else:
            print(str(state))
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
            # A session that died without a clean SIGTERM stop leaves any unit it
            # had set to ``in-progress`` stuck there forever (tracked issue:
            # dead-session-leaves-claimed-unit-stuck-in-progress). cleanup already
            # knows which sessions are dead; re-queue each orphaned in-progress
            # unit it claimed, cross-checking pid liveness against the surviving
            # registry so a unit a LIVE session is actively working is never
            # touched. ``registry.load()`` now returns only the survivors (cleanup
            # rewrote the registry).
            recovered = _recover_orphaned_units_from_dead_sessions(
                dead_session_names=set(removed),
                surviving_sessions=registry.load(),
            )
            if recovered:
                print(
                    f"Re-queued {len(recovered)} orphaned in-progress unit(s) "
                    f"from dead session(s): {', '.join(recovered)}"
                )
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


def _recover_orphaned_units_from_dead_sessions(
    *,
    dead_session_names: set[str],
    surviving_sessions: list[Session],
) -> list[str]:
    """Re-queue every ``in-progress`` unit orphaned by a now-dead session.

    A session that dies without a clean SIGTERM stop leaves the unit it had set
    to ``in-progress`` ([WU_CLAIMED] session=<name>) stuck there indefinitely.
    This is called from ``sessions --cleanup`` after the dead sessions' registry
    entries are removed. For every Task in ``in-progress`` whose most recent
    [WU_CLAIMED] audit names one of *dead_session_names*, the unit is re-queued
    (``force_status`` -> in-queue) with an explicit
    ``[REQUEUED_AFTER_DEAD_SESSION] session=<name>`` audit comment.

    Liveness cross-check (AC): a unit that appears in ANY surviving (live)
    session's scope is NEVER re-queued, even if its claim audit names a dead
    session -- a live session may legitimately have re-claimed it. The pid
    liveness was already established by ``cleanup_stale_sessions`` (the survivors
    are exactly the sessions whose pid is alive).

    Returns the sorted list of re-queued unit ids (empty when nothing matched).
    Best-effort per unit: a read/parse/write failure on one unit is logged and
    skipped so a single bad file cannot block recovery of the others.
    """
    if not dead_session_names:
        return []

    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("dead-session recovery: cannot read backlog index: %s", exc)
        return []

    # Any unit a surviving (live) session holds in scope is off-limits.
    live_scoped: set[str] = set()
    for session in surviving_sessions:
        live_scoped.update(session.scope)

    mgr = BacklogManager()
    recovered: list[str] = []
    for unit in units:
        if unit.status is not WorkUnitStatus.IN_PROGRESS:
            continue
        if unit.id in live_scoped:
            # A live session may have re-claimed this unit; never re-queue it.
            continue
        claiming = _extract_session_from_wu(unit)
        if claiming is None or claiming not in dead_session_names:
            continue
        try:
            mgr.force_status(unit.file_path, BACKLOG_INDEX, unit.id, STATUS_IN_QUEUE)
            mgr._append_agent_comment(
                unit.file_path,
                "orchestrator",
                f"{REQUEUED_AFTER_DEAD_SESSION_AUDIT_PREFIX}{claiming}",
            )
            _flag_orphaned_staged_wip(unit, f"dead session {claiming}")
            recovered.append(unit.id)
            logger.info(
                "dead-session recovery: re-queued %s orphaned in-progress by dead session %s",
                unit.id,
                claiming,
            )
        except (OSError, ValueError) as exc:
            logger.warning(
                "dead-session recovery: failed to re-queue %s (claimed by %s): %s",
                unit.id,
                claiming,
                exc,
            )
    return sorted(recovered)


def _flag_orphaned_staged_wip(unit: WorkUnit, reason: str) -> None:
    """Coordinate an interrupted unit's recovery with the staged-WIP invariant.

    An interrupted unit (dead session, drain, or SIGTERM stop) can leave staged
    changes in its target checkout's index. Resetting the unit's status is not
    enough: a later commit in the same checkout could sweep those staged files in
    under the wrong unit/message. This unstages the orphaned staged WIP so a
    subsequent commit cannot include it (edits stay in the working tree). Shared
    by the dead-session recovery and the drain/stop force-block path (tracked
    issue ``drain-leaves-interrupted-unit-staged-wip-in-index``). Best-effort:
    no-op when the unit's repo has no configured local checkout.
    """
    if not unit.repo:
        return
    try:
        canonical_repo = resolve_repo(unit.repo)
    except ValueError:
        # Unrecognised repo: nothing to clean up here. Best-effort -- never let a
        # repo-resolution quirk break the unit's status recovery.
        return
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        return
    _unstage_interrupted_wip(Path(repo_path), unit_id=unit.id, reason=reason)


def _unstage_interrupted_wip(repo_path: Path, *, unit_id: str, reason: str) -> bool:
    """Unstage any staged WIP left in *repo_path*'s index by an interrupted unit.

    When a session is drained / stopped / dies after the executor ran
    ``git add`` but BEFORE the commit, the staged changes sit in the checkout's
    index. A later ``git commit`` in the same checkout (the next unit's git-ops
    or a follow-up offline fix) would sweep those orphaned staged files in under
    the wrong unit/message -- cross-unit commit contamination (tracked issue:
    ``drain-leaves-interrupted-unit-staged-wip-in-index``).

    This unstages everything currently staged (``git reset -q`` -- the universal
    unstage that leaves the edits in the working tree, so the WIP is recoverable
    when the unit is re-claimed and is never silently lost). It is a no-op when
    the index is clean. Best-effort: a non-git directory or a git error is logged
    and swallowed so recovery of the unit's status is never blocked by a git
    quirk. Returns ``True`` when staged changes were found and unstaged.

    Args:
        repo_path: Target repo working-directory checkout.
        unit_id: The interrupted unit (for the audit log line).
        reason: Why the unit was interrupted (e.g. ``"drain"``, ``"dead session X"``).
    """
    if not repo_path.is_dir():
        return False
    # `git diff --cached --quiet` exits 0 when nothing is staged, 1 when the
    # index differs from HEAD. Any other exit (e.g. not a git repo) is treated
    # as "nothing to do" so this never raises into the recovery path.
    probe = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--cached", "--quiet"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return False
    if probe.returncode != 1:
        logger.warning(
            "_unstage_interrupted_wip: cannot probe index for '%s' (unit %s, %s): %s",
            repo_path,
            unit_id,
            reason,
            probe.stderr.strip(),
        )
        return False
    reset_result = subprocess.run(
        ["git", "-C", str(repo_path), "reset", "-q"],
        check=False,
        capture_output=True,
        text=True,
    )
    if reset_result.returncode != 0:
        logger.warning(
            "_unstage_interrupted_wip: git reset (unstage) failed for '%s' (unit %s, %s): %s",
            repo_path,
            unit_id,
            reason,
            reset_result.stderr.strip(),
        )
        return False
    logger.info(
        "_unstage_interrupted_wip: unstaged orphaned WIP in '%s' left by interrupted unit %s (%s); "
        "edits remain in the working tree, recoverable when the unit is re-claimed.",
        repo_path,
        unit_id,
        reason,
    )
    return True


def _dirty_paths(repo_path: Path) -> list[str] | None:
    """Return every path with index/working-tree/untracked changes in *repo_path*.

    Uses ``git status --porcelain -z`` so paths with spaces or special
    characters are unambiguous. Returns the repo-relative paths (renames are
    expanded to the destination path). Returns ``None`` when *repo_path* is not
    a git checkout or the status probe fails -- the caller treats that as
    "nothing to clean" (best-effort, never raises into the claim path).
    """
    if not repo_path.is_dir():
        return None
    probe = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain", "-z"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return None
    paths: list[str] = []
    # NUL-delimited records: each is "XY <path>"; a rename ("R") record is
    # followed by a second NUL-delimited field holding the ORIGINAL path,
    # which must be consumed but is not a dirty destination path itself.
    fields = [field for field in probe.stdout.split("\0") if field]
    index = 0
    while index < len(fields):
        record = fields[index]
        status_code = record[:2]
        path = record[3:]
        if path:
            paths.append(path)
        if status_code and status_code[0] in ("R", "C"):
            # Consume the trailing original-path field for rename/copy records.
            index += 1
        index += 1
    return paths


def _own_manifest_paths(unit: WorkUnit) -> set[str]:
    """Return the claimed unit's own legitimate manifest file paths.

    These are the real (non-sentinel) ``Changes Manifest`` paths the executor
    for *unit* is permitted to touch. The on-claim cleanup must NOT evict these
    -- a resumed in-progress unit's own WIP is legitimate. Returns an empty set
    when the work-unit file or its manifest cannot be read (best-effort).
    """
    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        return set()
    from devbench.backlog.manifest import parse_manifest

    try:
        manifest_rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(_committable_manifest_paths(manifest_rows))


def _resolve_claim_checkout(unit: WorkUnit) -> Path | None:
    """Resolve *unit*'s target checkout for on-claim cleanup, or ``None``.

    Returns the existing git-checkout directory configured for the unit's repo,
    or ``None`` when the unit has no repo, the repo is unresolvable, no local
    checkout is configured, or the configured path is not a directory. Keeps
    :func:`_clean_foreign_wip_on_claim` within the PLR0911 return budget.
    """
    if not unit.repo:
        return None
    try:
        canonical_repo = resolve_repo(unit.repo)
    except ValueError:
        return None
    configured = REPO_LOCAL_PATHS.get(canonical_repo)
    if configured is None:
        return None
    repo_path = Path(configured)
    if not repo_path.is_dir():
        return None
    return repo_path


def _clean_foreign_wip_on_claim(unit: WorkUnit) -> bool:
    """Evict foreign (non-manifest) orphaned WIP from the unit's checkout on claim.

    When a prior unit is interrupted (crash, quota exit, SIGTERM) mid-execution
    its staged / working-tree edits can remain in the shared single-branch
    checkout. In single-branch mode ``ensure_branch`` is a no-op (no stash), so
    that orphaned WIP would otherwise reach the NEXT unit's executor, which then
    either bundles foreign files into its commit (manifest-scope violation) or
    takes investigative/destructive action (tracked issue TDI-006).

    On claim this parks every dirty path that does NOT belong to *unit*'s own
    Changes Manifest into a single ``git stash push -u`` entry -- removing the
    foreign WIP from BOTH the index and the working tree while keeping it
    recoverable (the owning unit redoes the work when next claimed, the stash is
    a backup). The claimed unit's own manifest paths are explicitly preserved so
    a resumed in-progress unit's legitimate WIP is never clobbered.

    Best-effort: a no-op (returns ``False``) when the repo is unresolvable, has
    no configured checkout, is not a git repo, or carries no foreign WIP. Never
    raises into the claim path. Returns ``True`` when foreign WIP was evicted.
    """
    repo_path = _resolve_claim_checkout(unit)
    if repo_path is None:
        return False

    dirty = _dirty_paths(repo_path)
    if not dirty:
        return False
    own = _own_manifest_paths(unit)
    foreign = [path for path in dirty if path not in own]
    if not foreign:
        # The only dirty paths belong to the claimed unit itself -- leave them.
        return False

    stash_message = f"devbench: orphaned WIP evicted on claim of {unit.id}"
    stash_result = subprocess.run(
        ["git", "-C", str(repo_path), "stash", "push", "-u", "-m", stash_message, "--", *foreign],
        check=False,
        capture_output=True,
        text=True,
    )
    if stash_result.returncode != 0:
        logger.warning(
            "_clean_foreign_wip_on_claim: git stash push failed for '%s' (unit %s); foreign WIP NOT evicted: %s",
            repo_path,
            unit.id,
            stash_result.stderr.strip(),
        )
        return False
    logger.info(
        "_clean_foreign_wip_on_claim: evicted %d foreign WIP path(s) %s from '%s' on claim of %s "
        "(parked in a recoverable stash); the claimed unit's own manifest work is preserved.",
        len(foreign),
        sorted(foreign),
        repo_path,
        unit.id,
    )
    return True


# ---------------------------------------------------------------------------
# cmd_stop helpers (E4-F5-S1-T2, issue #192)
# ---------------------------------------------------------------------------

#: Audit-log prefix written when cmd_start intercepts SIGTERM and forces the
#: in-flight work unit to ``blocked``.  Format:
#: ``[FORCED_BLOCKED_ON_STOP] session=<name>``.
_FORCED_BLOCKED_ON_STOP_AUDIT_PREFIX: str = "[FORCED_BLOCKED_ON_STOP] session="


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


def _force_block_in_flight_wu(wu: WorkUnit | None, session_name: str) -> None:
    """Set *wu* to ``blocked`` and append a ``[FORCED_BLOCKED_ON_STOP]`` audit comment.

    This is called by the SIGTERM handler in ``cmd_start`` to mark the
    in-flight work unit as ``blocked`` with a session-tagged audit entry,
    satisfying spec section 4.4.5 and AC-192-9.

    When *wu* is ``None`` (no in-flight unit found), this function is a no-op.

    Args:
        wu: The in-flight :class:`~devbench.backlog.work_unit.WorkUnit` to
            force-block, or ``None`` for a no-op.
        session_name: The session name to embed in the audit comment
            (e.g. ``"default"``).

    Raises:
        OSError: Reading or writing the work-unit file fails.
        ValueError: The BacklogManager validation rejects the status transition.
    """
    if wu is None:
        return

    mgr = BacklogManager()
    mgr.force_status(wu.file_path, BACKLOG_INDEX, wu.id, STATUS_BLOCKED)
    mgr._append_agent_comment(
        wu.file_path,
        "orchestrator",
        f"{_FORCED_BLOCKED_ON_STOP_AUDIT_PREFIX}{session_name}",
    )
    # The unit may have been interrupted mid-git-ops -- after the executor ran
    # ``git add`` but before the commit. Force-blocking its status is not enough:
    # the staged changes sit in the target checkout's index and a later commit in
    # the same checkout would sweep them in under the wrong unit/message (tracked
    # issue: drain-leaves-interrupted-unit-staged-wip-in-index). Unstage them
    # (edits stay in the working tree, recoverable when the unit is re-claimed).
    _flag_orphaned_staged_wip(wu, f"interrupted on stop session={session_name}")


def _executor_pgid_file(session_name: str | None) -> Path:
    """Return the session-scoped path that records the live executor pgid.

    Cross-process attribution channel: the in-session live-command runner (a
    separate ``devbench verify-ac`` CLI subprocess) writes the process-group id
    of the external command it launched here; the orchestrator daemon reads it
    at block time to tear down EXACTLY that group. Keyed by the active session
    so two sessions on one workspace never read each other's pgid.
    """
    name = session_name or os.environ.get("DEVBENCH_SESSION_NAME", "").strip() or "default"
    return WORKSPACE_ROOT / SESSION_SESSIONS_BASE_DIR / name / "executor.pgid"


def _register_executor_pgid(pgid: int, *, session_name: str | None = None) -> None:
    """Record *pgid* as the live executor process group for the active session.

    Called by the in-session long-op runner right after it launches the live
    command in its own group. Best-effort: a write failure is logged, never
    fatal (the command still runs; only the block-time teardown loses its
    handle).
    """
    path = _executor_pgid_file(session_name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, f"{pgid}\n")
    except OSError as exc:
        logger.warning("%s could not register executor pgid=%d: %s", CLAIM_TEARDOWN_MARKER, pgid, exc)


def _clear_executor_pgid(*, session_name: str | None = None) -> None:
    """Remove the recorded executor pgid once the live command has terminated.

    A no-longer-live group must never be torn down for a later, unrelated claim,
    so the file is cleared as soon as the command returns. Best-effort.
    """
    path = _executor_pgid_file(session_name)
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _read_attributed_executor_pgid(*, session_name: str | None = None) -> int | None:
    """Return the recorded live executor pgid for the active session, or None.

    Read by the orchestrator at ``[CLAIM_NOT_CONVERGING]`` block time. ``None``
    when no live command is registered (no subprocess to tear down) or the file
    is missing / unreadable / malformed (fail-safe: a teardown is skipped rather
    than risking a wrong pgid).
    """
    path = _executor_pgid_file(session_name)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pgid = int(raw)
    except ValueError:
        logger.warning(
            "%s executor pgid file %s holds non-integer %r; skipping teardown", CLAIM_TEARDOWN_MARKER, path, raw
        )
        return None
    return pgid if pgid > 1 else None


def _terminate_process_group(pgid: int) -> bool:
    """Send ``SIGTERM`` to EXACTLY the one process group *pgid*; never broadly.

    Positively-attributed, single-group teardown (Item B of tracked issue 015):
    a long external subprocess the executor spawned (e.g. a live ``terraform
    apply`` / ``go test`` tree) must be reaped on a ``[CLAIM_NOT_CONVERGING]``
    block so it is not orphaned to init and left applying billable resources
    outside devbench's lifecycle. Signals ONLY *pgid* via :func:`os.killpg` --
    NOT a process-name scan, NOT a parent-tree walk, NOT a machine-wide kill --
    so it can never reach an unrelated session's work.

    Refuses, returning ``False`` without signalling:

    - ``pgid <= 1`` -- pgid 0 means "the caller's own group" and 1 is init; a
      broad-kill guard so a mis-attributed / unset pgid can never fan out.
    - the orchestrator's OWN process group (``os.getpgrp()``) -- the daemon must
      never tear itself (or sibling in-flight work sharing its group) down.

    A group that already exited (``ProcessLookupError``) is a no-op success.

    Args:
        pgid: The process-group id positively attributed to THIS claim's
            executor (the leader pid of a subprocess launched with
            ``start_new_session=True`` / ``setsid``).

    Returns:
        ``True`` when ``SIGTERM`` was delivered to *pgid*; ``False`` when the
        group was refused by the attribution guards above.
    """
    if pgid <= 1:
        logger.warning(
            "%s refused to signal reserved process group pgid=%d (broad-kill guard)", CLAIM_TEARDOWN_MARKER, pgid
        )
        return False
    if pgid == os.getpgrp():
        logger.warning("%s refused to signal the orchestrator's OWN process group pgid=%d", CLAIM_TEARDOWN_MARKER, pgid)
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        # The group already exited between attribution and teardown -- nothing
        # to reap. A no-op success, not a fault (CLAUDE.md no-silent-failure:
        # logged, not swallowed silently).
        logger.info(
            "%s executor process group pgid=%d already exited; nothing to tear down", CLAIM_TEARDOWN_MARKER, pgid
        )
        return True
    except (OSError, PermissionError) as exc:
        logger.warning("%s failed to signal executor process group pgid=%d: %s", CLAIM_TEARDOWN_MARKER, pgid, exc)
        return False
    logger.info("%s sent SIGTERM to attributed executor process group pgid=%d", CLAIM_TEARDOWN_MARKER, pgid)
    return True


def _run_claim_teardown_cleanup_hook(command: str) -> None:
    """Run the sanctioned post-teardown cleanup *command* (best-effort).

    Invoked AFTER the executor group is torn down on a block, only when a hook
    is configured (e.g. a run-id-scoped terratest sweep that reclaims any
    resource a torn-down ``terraform apply`` left half-created). A non-zero exit
    or a launch failure is logged (CLAUDE.md no-silent-failure) but does not
    propagate so the orchestrate loop can still exit cleanly.
    """
    rc, _stdout, stderr = run_command(["bash", "-c", command])
    if rc != 0:
        logger.warning("%s sanctioned cleanup hook exited %d: %s", CLAIM_TEARDOWN_MARKER, rc, stderr.strip())
        return
    logger.info("%s sanctioned cleanup hook completed (rc=0)", CLAIM_TEARDOWN_MARKER)


def _teardown_non_converging_executor(unit_id: str, executor_pgid: int | None) -> None:
    """Tear down the attributed executor group, then run the cleanup hook.

    Separated from the status-block so the resource reclaim runs even if the
    backlog write raised: an orphaned billable ``terraform apply`` is the worse
    harm. No-op when no pgid was attributed (a non-subprocess claim).
    """
    if executor_pgid is None:
        return
    torn_down = _terminate_process_group(executor_pgid)
    if not torn_down:
        return
    logger.info(
        "%s task=%s executor_pgid=%d torn down on non-converging block", CLAIM_TEARDOWN_MARKER, unit_id, executor_pgid
    )
    hook = _resolve_claim_teardown_cleanup_hook()
    if hook:
        _run_claim_teardown_cleanup_hook(hook)


def _block_non_converging_claim(unit_id: str, recurring_failure: str, *, executor_pgid: int | None = None) -> None:
    """Force-block *unit_id* with a ``[CLAIM_NOT_CONVERGING]`` audit comment.

    Called from ``_run`` when the within-claim convergence bound trips: the
    claim has repeated the SAME unresolvable failure beyond the configured
    attempt cap (or exceeded the wall-clock backstop). Routes the unit to the
    normal operator / stop-window path -- the correct outcome for a failure
    that cannot be resolved in scope. Best-effort: a read/parse failure is
    logged and swallowed so the loop can still exit cleanly.

    When *executor_pgid* is provided it is the process group positively
    attributed to THIS claim's executor (a subprocess launched with
    ``start_new_session=True``). After the unit is blocked, that single group is
    torn down via :func:`_terminate_process_group` (SIGTERM to exactly that
    pgid, never a broad kill) and the configured sanctioned cleanup hook (if
    any) is run -- so a long external subprocess the executor spawned (e.g. a
    live ``terraform apply`` / ``go test``) is reaped instead of orphaned to
    init and left leaking billable resources (Item B of tracked issue 015). The
    teardown runs regardless of whether the backlog write succeeded.
    """
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        unit = _find_unit(parser.parse_index(), unit_id)
        if unit is None:
            logger.warning("%s could not locate unit %s to block (not in index)", CLAIM_NOT_CONVERGING_MARKER, unit_id)
            return
        wu_file = _resolve_unit_file(unit)
        if wu_file is None:
            logger.warning("%s could not resolve file for unit %s", CLAIM_NOT_CONVERGING_MARKER, unit_id)
            return
        mgr = BacklogManager()
        mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_BLOCKED)
        mgr._append_agent_comment(
            wu_file,
            "orchestrator",
            f"{CLAIM_NOT_CONVERGING_MARKER} claim repeated the same unresolvable failure "
            f"without converging: {recurring_failure}. Blocked for operator review at the "
            "stop-window (the failure cannot be resolved in scope -- e.g. a vendored/target-repo "
            "defect a verification-only unit cannot fix). Record a tracked-devbench-issues/*.md "
            "if this is a harness/target-repo bug.",
        )
        logger.info("%s task=%s recurring_failure=%r", CLAIM_NOT_CONVERGING_MARKER, unit_id, recurring_failure)
    except (OSError, ValueError) as exc:
        logger.warning("%s failed to block unit %s: %s", CLAIM_NOT_CONVERGING_MARKER, unit_id, exc)
    finally:
        # Reap the executor's spawned subprocess group even if the block raised:
        # an orphaned billable apply is the worse harm. Single, positively
        # attributed group only.
        _teardown_non_converging_executor(unit_id, executor_pgid)


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


#: Exit code for a daemon-control verb refused by the caller-role gate (TDI-004).
#: Distinct from the argument-parse error (2) and the runtime-delivery error (1)
#: so callers and tests can tell an authorisation refusal apart from a bad flag.
DAEMON_CONTROL_REFUSED_RC: int = 3


def _is_daemon_control_authorized() -> bool:
    """Return whether the caller may invoke a devbench daemon-control verb (TDI-004).

    The validated defect: an executor sub-agent (a non-interactive Claude Agent
    SDK subprocess) ran ``devbench stop --session <name>`` and SIGTERMed its OWN
    orchestrator, halting the entire run. The executor carries no
    ``DEVBENCH_AGENT_ROLE=orchestrator`` indicator (the same env mechanism
    ``guard-work-unit-write.sh`` already keys on) and runs with no controlling
    TTY.

    Authorisation rule (defense-in-depth behind ``guard-bash.sh``), evaluated
    in order:

    - ``DEVBENCH_AGENT_ROLE=executor`` -> REFUSED. The caller positively declares
      itself a work-unit worker; a worker is never authorised, even at a TTY.
    - ``DEVBENCH_AGENT_ROLE=orchestrator`` -> AUTHORIZED. The orchestrator's own
      lifecycle management is legitimate.
    - An interactive caller (``stdin`` is a TTY) -> AUTHORIZED. An operator at a
      terminal may stop/drain/restart a session directly.
    - Otherwise (non-interactive AND no orchestrator role -- the executor
      sub-agent case, which inherits no role indicator) -> REFUSED.

    Returns:
        ``True`` when the caller is authorised; ``False`` otherwise.
    """
    role = os.environ.get("DEVBENCH_AGENT_ROLE", "").strip()
    if role == "executor":
        return False
    if role == "orchestrator":
        return True
    try:
        return bool(sys.stdin.isatty())
    except (OSError, ValueError):
        # A closed/detached stdin is the non-interactive (automated) case.
        return False


def _refuse_daemon_control(verb: str) -> int:
    """Print the TDI-004 refusal diagnostic for *verb* and return the refusal rc.

    Args:
        verb: The daemon-control verb being refused (e.g. ``"stop"``).

    Returns:
        :data:`DAEMON_CONTROL_REFUSED_RC` so the caller can ``return`` it directly.
    """
    print(
        (
            f"ERROR: 'devbench {verb}' is a daemon-control verb and is refused from this context "
            "(TDI-004). A work-unit worker must never control the orchestrator's lifecycle -- "
            f"'devbench {verb}' would stop or restart the orchestrator running you, halting ALL "
            "work, not just your unit. If the repo state is confusing, escalate: log a comment "
            "and BLOCK your own unit; never stop the daemon. Authorised callers are the "
            "orchestrator itself (DEVBENCH_AGENT_ROLE=orchestrator) or an interactive operator "
            "at a terminal."
        ),
        file=sys.stderr,
    )
    return DAEMON_CONTROL_REFUSED_RC


def cmd_stop(*argv: str) -> int:
    """Send SIGTERM to a named session's orchestrator process (spec section 4.4.5, issue #192).

    Invocation form::

        devbench stop --session <name>

    Reads the PID from ``<workspace>/.devbench/sessions/<name>/pid`` and sends
    ``SIGTERM`` to that process.  The SIGTERM handler registered by ``cmd_start``
    catches the signal, forces any in-flight work unit to ``blocked`` with a
    ``[FORCED_BLOCKED_ON_STOP] session=<name>`` audit comment, then exits rc=0.

    Caller-role gate (TDI-004): ``stop`` is a daemon-control verb. It is refused
    when invoked by a non-interactive caller that is NOT the orchestrator -- the
    executor-sub-agent case that was observed SIGTERMing its own orchestrator.
    Authorised callers are the orchestrator itself
    (``DEVBENCH_AGENT_ROLE=orchestrator``) or an interactive operator at a TTY.
    See :func:`_is_daemon_control_authorized`. This is defense-in-depth behind
    the deterministic ``guard-bash.sh`` daemon-control block.

    Exit codes:

    - 0 on success (SIGTERM delivered).
    - 1 when the session does not exist, the PID file is absent or malformed, or
      the signal cannot be delivered (ESRCH, EPERM, or other OS error).
    - 2 on invalid arguments (missing or unknown flags).
    - 3 when the caller-role gate refuses (TDI-004): see
      :data:`DAEMON_CONTROL_REFUSED_RC`.

    Args:
        *argv: CLI flags as individual strings (``--session <name>``).

    Returns:
        0 on success; 1 on runtime error; 2 on argument parse error; 3 when the
        daemon-control caller-role gate refuses.

    Raises:
        Nothing -- all errors are reported to stderr and returned as exit codes.
    """
    if not _is_daemon_control_authorized():
        return _refuse_daemon_control("stop")

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


def cmd_request_amendment(*argv: str) -> int:
    """Register an amendment request for a work unit.

    Usage::

        request-amendment <id> [--operator-mode]

    Reads the request payload as JSON on stdin. Expected fields:
    ``reason``, ``justification``, ``files_to_add`` (list of ``{path, change}``),
    ``linked_acs`` (list of AC IDs). The ``task_id`` and ``requested_at``
    fields are filled in by this command -- the caller does not provide them.

    Without ``--operator-mode`` (default): validates the Layer-1 pre-filter, writes
    the request to ``<DEVBENCH_WORKSPACE_ROOT>/.devbench/amendments/<id>.json``, and
    prints a one-line JSON summary. The orchestrator's manifest-amender agent then
    decides whether to apply or reject.

    With ``--operator-mode``: bypasses the in-progress status gate and the LLM
    judge; applies the amendment synchronously in this call with Layer-3 post-check
    (restores on failure); writes the operator-amendment audit entry to the work-unit
    ``## Comments`` section; prints a one-line JSON summary.

    Fails fast on schema errors, duplicate pending requests (without operator mode),
    or unknown reasons.
    """
    unit_id, operator_mode = _parse_request_amendment_argv(argv)
    if unit_id is None:
        return 1

    try:
        request = _build_amendment_request_from_stdin(unit_id)
    except _AmendmentRequestInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if operator_mode:
        try:
            apply_operator_amendment(BACKLOG_INDEX, unit_id, request)
        except AmendmentError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "task_id": unit_id,
                    "status": "applied",
                    "operator_mode": True,
                    "files_to_add": [f.path for f in request.files_to_add],
                    "reason": request.reason,
                }
            )
        )
        return 0

    try:
        written_path = write_request(WORKSPACE_ROOT, request)
    except AmendmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "task_id": unit_id,
                "request_path": str(written_path),
                "files_to_add": [f.path for f in request.files_to_add],
                "reason": request.reason,
            }
        )
    )
    return 0


def _parse_request_amendment_argv(argv: tuple[str, ...]) -> tuple[str | None, bool]:
    """Parse the request-amendment flag grammar.

    Returns ``(unit_id, operator_mode)``.  Returns ``(None, False)`` after
    printing a usage error to stderr so the caller can ``return 1``.

    Grammar::

        request-amendment <id> [--operator-mode]
    """
    positional: list[str] = []
    operator_mode = False
    for arg in argv:
        if not arg:
            continue
        if arg == "--operator-mode":
            operator_mode = True
        elif arg.startswith("-"):
            print(f"ERROR: request-amendment: unknown flag: {arg!r}", file=sys.stderr)
            return None, False
        else:
            positional.append(arg)
    if len(positional) != 1:
        print(
            f"ERROR: request-amendment requires exactly one positional argument (task id), got {positional!r}",
            file=sys.stderr,
        )
        return None, False
    return positional[0], operator_mode


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

    For a ``manifest_row_superseded`` request the deterministic guards need the
    target repo's working tree (the removed row's file must be absent on disk)
    and the staged-file set (it must not touch the removed path). Both are
    resolved generically from the unit's target repo here and threaded into
    ``apply_amendment``; other reasons do not require them, so the repo
    resolution is skipped entirely for them.
    """
    repo_path: Path | None = None
    staged_files: frozenset[str] | None = None
    if _amendment_reason_needs_repo_context(unit_id):
        repo_path, staged_files = _resolve_amendment_repo_context(unit_id)

    try:
        apply_amendment(
            WORKSPACE_ROOT,
            BACKLOG_INDEX,
            unit_id,
            repo_path=repo_path,
            staged_files=staged_files,
        )
    except AmendmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"task_id": unit_id, "status": "applied"}))
    return 0


def _amendment_reason_needs_repo_context(unit_id: str) -> bool:
    """Return ``True`` iff the pending request for *unit_id* needs repo context.

    Only the ``manifest_row_superseded`` reason inspects the target repo's
    working tree and staged diff. Reading the reason here lets every other
    reason skip repo resolution entirely (so a workspace whose repo is not
    locally configured still applies tdd/verification amendments). Best-effort:
    returns ``False`` when the request cannot be read (apply_amendment then
    surfaces the canonical error).
    """
    from devbench.backlog.amendment import REASON_MANIFEST_ROW_SUPERSEDED

    try:
        request = read_request(WORKSPACE_ROOT, unit_id)
    except AmendmentError:
        return False
    return request.reason == REASON_MANIFEST_ROW_SUPERSEDED


def _resolve_amendment_repo_context(unit_id: str) -> tuple[Path | None, frozenset[str] | None]:
    """Return ``(repo_path, staged_files)`` for *unit_id*, or ``(None, None)``.

    Resolves the unit's target repo working directory and its staged-file set
    (``git diff --cached --name-only``). Returns ``(None, None)`` when the repo
    cannot be resolved -- the manifest-row-superseded guards then fail fast with
    an actionable error, and other amendment reasons (which do not need the
    repo) proceed unaffected. Best-effort: never raises.
    """
    from devbench.backlog.manifest import list_staged_files

    resolved = _resolve_unit_file_and_repo_path(unit_id)
    if resolved is None:
        return None, None
    _wu_file, repo_path = resolved
    try:
        staged = frozenset(list_staged_files(repo_path))
    except RuntimeError:
        return repo_path, None
    return repo_path, staged


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
    # Capture the original ids in declaration order so a collision re-home
    # (suggested_id reassigned by materialise_proposal) is observable here.
    original_ids = [task.suggested_id for task in proposal.proposed_tasks]
    pre_states = {tid: classify_proposed_task(BACKLOG_ROOT, WORKSPACE_ROOT, tid) for tid in original_ids}

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

    # A collision re-home reassigns proposed_tasks[i].suggested_id to a free id.
    # Surface every old -> new mapping so the operator can see the fix unit was
    # created under a new id (never silently dropped on an id collision).
    remapped = {
        old: task.suggested_id
        for old, task in zip(original_ids, proposal.proposed_tasks, strict=True)
        if old != task.suggested_id
    }
    # A task that was re-homed is NOT skipped -- it was materialised under a new
    # id. Only report a task as skipped when its pre-state was terminal AND it
    # was not re-homed.
    skipped = {
        tid: state.value
        for tid, state in pre_states.items()
        if state is not ProposalTaskState.UNMATERIALISED and tid not in remapped
    }
    logger.info(
        "Materialised %d proposed task(s) from %s (skipped %d, re-homed %d)",
        len(drafts),
        source_task_id,
        len(skipped),
        len(remapped),
    )
    print(
        json.dumps(
            {
                "source_task_id": source_task_id,
                "materialised": [str(p) for p in drafts],
                "skipped": skipped,
                "remapped": remapped,
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

    promoted: list[str] = []
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
                audit_suffix=audit_suffix,
            )
            promoted.append(task.suggested_id)
        except ProposalError as exc:
            logger.warning(
                "write-proposal auto-cascade promote failed for %s: %s",
                task.suggested_id,
                exc,
            )

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


def cmd_escalate_proposal(source_task_id: str) -> int:
    """Auto-decompose a cross-unit-defect escalation into a fix proposal.

    Reads ``{"attributed_files": [...]}`` JSON on stdin -- the files the executor
    attributed a ``NEEDS_ESCALATION`` live-AC failure to. Resolves the blocked
    unit's own Changes Manifest, computes the OUT-OF-SCOPE subset (attributed
    files not in the unit's manifest), and:

    * When at least one file is out-of-scope: allocates fix-unit ids, builds a
      :class:`Proposal` with one ``proposed_tasks`` entry per out-of-scope file
      (concrete manifest + corrective AC + a re-run of the failing live AC),
      writes ``.devbench/proposals/<id>.json``, and appends the deterministic
      ``[ESCALATION_PROPOSAL_WRITTEN]`` audit marker to the blocked unit. The
      operator/auto-accept pipeline then materialises + dep-wires the fixes.

    * When NO attributed file is out-of-scope (or none was supplied): appends the
      deterministic ``[ESCALATION_NO_PROPOSAL]`` marker so a watching
      operator/loop can detect "blocked with no auto-resolution draft" without
      reading prose, and returns success (there is nothing to decompose).

    Prints a one-line JSON summary. Fails fast on stdin/schema errors or an
    unresolvable unit.
    """
    try:
        attributed_files = _read_attributed_files_from_stdin()
        wu_file, _unit, manifest_files = _resolve_escalation_context(source_task_id)
    except _ProposalInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_of_scope = [f.strip() for f in attributed_files if f.strip() and f.strip() not in set(manifest_files)]
    # The PARENT's own failing executable gate directive, carried onto each fix
    # unit so its done-gate re-runs the parent gate -- a fix that merely relocates
    # the failure cannot reach done (tracked issue:
    # fix-unit-validates-narrow-diagnostic-not-parent-full-gate).
    parent_verify_directive = _parent_failing_verify_directive(wu_file)
    mgr = BacklogManager()

    if not out_of_scope:
        mgr._append_agent_comment(
            wu_file,
            "orchestrator",
            f"{ESCALATION_NO_PROPOSAL_MARKER} {source_task_id} blocked with no out-of-scope attributed "
            "file to decompose; no fix-proposal created. Operator review required.",
        )
        print(json.dumps({"source_task_id": source_task_id, "proposal_written": False, "out_of_scope": []}))
        return 0

    try:
        written, fix_ids = _write_escalation_proposal(
            source_task_id=source_task_id,
            attributed_files=attributed_files,
            manifest_files=manifest_files,
            out_of_scope=out_of_scope,
            parent_verify_directive=parent_verify_directive,
        )
    except _ProposalInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mgr._append_agent_comment(
        wu_file,
        "orchestrator",
        f"{ESCALATION_PROPOSAL_WRITTEN_MARKER} {source_task_id} decomposed cross-unit defect into "
        f"fix unit(s) {', '.join(fix_ids)} for out-of-scope file(s) {', '.join(out_of_scope)}; "
        f"proposal at {written}. Promote to wire {source_task_id} depends-on the fix unit(s).",
    )
    logger.info(
        "%s source=%s fix_units=%s out_of_scope=%s",
        ESCALATION_PROPOSAL_WRITTEN_MARKER,
        source_task_id,
        fix_ids,
        out_of_scope,
    )
    print(
        json.dumps(
            {
                "source_task_id": source_task_id,
                "proposal_written": True,
                "proposal_path": str(written),
                "fix_units": fix_ids,
                "out_of_scope": out_of_scope,
            }
        )
    )
    return 0


def _resolve_escalation_context(source_task_id: str) -> tuple[Path, WorkUnit, list[str]]:
    """Resolve ``(wu_file, unit, manifest_files)`` for an escalation, or raise.

    Raises ``_ProposalInputError`` (with an actionable message) when the index
    is unreadable, the unit is missing, the file is missing, or the manifest is
    malformed -- collapsing four failure returns in ``cmd_escalate_proposal``
    into one handled exception (keeps the command under PLR0911's return cap).
    """
    from devbench.backlog.manifest import ManifestParseError, parse_manifest

    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        unit = _find_unit(parser.parse_index(), source_task_id)
    except (FileNotFoundError, ValueError) as exc:
        raise _ProposalInputError(f"cannot read backlog index: {exc}") from exc
    if unit is None:
        raise _ProposalInputError(f"work unit {source_task_id!r} not found in backlog")
    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        raise _ProposalInputError(f"work-unit file not found for {source_task_id!r}")
    try:
        manifest_files = [row.file for row in parse_manifest(wu_file.read_text(encoding="utf-8"))]
    except ManifestParseError as exc:
        raise _ProposalInputError(f"cannot read Changes Manifest for {source_task_id!r}: {exc}") from exc
    return wu_file, unit, manifest_files


def _parent_failing_verify_directive(wu_file: Path) -> str | None:
    """Return the PARENT unit's failing executable ``VERIFY`` directive line, or ``None``.

    Reads the blocked unit's ``## Verification`` section and returns the directive
    line of the gate the cross-unit failure came from -- preferring an infra/live
    directive (terraform/terragrunt/deploy/apply, the class that surfaces
    cross-unit defects) and otherwise the first executable directive. This exact
    line is carried onto each fix unit so its done-gate re-runs the PARENT's gate
    command, so a fix that merely relocates the failure cannot reach ``done``
    (tracked issue: fix-unit-validates-narrow-diagnostic-not-parent-full-gate).
    Returns ``None`` when the parent declares no executable directive (the fix unit
    then keeps its narrow-only verification -- there is no parent gate to re-run).
    """
    try:
        content = wu_file.read_text(encoding="utf-8")
        items = verification.parse_verification_section(content)
    except (OSError, ValueError):
        return None
    executable = [item for item in items if item.is_executable() and (item.command or "").strip()]
    if not executable:
        return None
    infra = [item for item in executable if item.is_infra()]
    chosen = infra[0] if infra else executable[0]
    return chosen.raw or None


def _write_escalation_proposal(
    *,
    source_task_id: str,
    attributed_files: list[str],
    manifest_files: list[str],
    out_of_scope: list[str],
    parent_verify_directive: str | None = None,
) -> tuple[Path, list[str]]:
    """Allocate ids, build + write the escalation proposal; return ``(path, fix_ids)``.

    Raises ``_ProposalInputError`` on any build / write failure (caught once by
    ``cmd_escalate_proposal``). ``out_of_scope`` is non-empty (the caller has
    already short-circuited the no-out-of-scope case). *parent_verify_directive*
    is the parent's failing executable gate, carried onto the fix units so their
    done-gate re-runs the parent gate.
    """
    story_id = "-".join(source_task_id.split("-")[:3])
    try:
        suggested_ids = allocate_next_ids(WORKSPACE_ROOT, BACKLOG_ROOT, story_id, len(out_of_scope))
        proposal = build_escalation_proposal(
            source_task_id=source_task_id,
            attributed_files=attributed_files,
            manifest_files=manifest_files,
            suggested_ids=suggested_ids,
            generated_at=datetime.now(tz=UTC).isoformat(),
            rejection_reason=(
                f"{source_task_id} live acceptance check failed due to defect(s) in file(s) "
                f"outside its Changes Manifest: {', '.join(out_of_scope)}"
            ),
            parent_verify_directive=parent_verify_directive,
        )
    except (ProposalError, ValueError) as exc:
        raise _ProposalInputError(f"cannot build escalation proposal: {exc}") from exc
    if proposal is None:
        raise _ProposalInputError("escalation produced no proposal despite out-of-scope files")
    try:
        written = write_proposal(WORKSPACE_ROOT, proposal)
    except ProposalError as exc:
        raise _ProposalInputError(str(exc)) from exc
    return written, [t.suggested_id for t in proposal.proposed_tasks]


def _read_attributed_files_from_stdin() -> list[str]:
    """Read stdin and return the ``attributed_files`` list.

    Raises ``_ProposalInputError`` on any stdin / schema failure.
    """
    try:
        raw = sys.stdin.read()
    except OSError as exc:
        raise _ProposalInputError(f"cannot read stdin: {exc}") from exc
    if not raw.strip():
        raise _ProposalInputError('escalation JSON required on stdin (expected {"attributed_files": [...]})')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ProposalInputError(f"escalation input is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _ProposalInputError("escalation input must be a JSON object")
    files_raw = data.get("attributed_files", [])
    if not isinstance(files_raw, list):
        raise _ProposalInputError(f"attributed_files must be a list, got {type(files_raw).__name__}")
    result: list[str] = []
    for entry in files_raw:
        if not isinstance(entry, str):
            raise _ProposalInputError(f"attributed_files entries must be strings, got {type(entry).__name__}")
        result.append(entry)
    return result


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

    Writes a Dependencies-table row and a ``[WU_WIRED] ... [BLOCKED_PENDING_PROPOSAL] <blocker>``
    audit comment on the blocked task's file. The ADR-07 auto-requeue cascade
    then auto-unblocks the task when the blocker reaches ``done`` / ``declined``.

    Used for three scenarios the ``promote-proposal`` flow does not cover:

    1. Operator realises AFTER a promote that an additional task should have
       been listed in the proposal's ``affected_task_ids``.
    2. Operator hand-authored a work unit (not via task-factory) that
       unblocks another task and wants to wire the marker without touching
       the file by hand.
    3. Operator corrects a proposal authored without ``affected_task_ids``
       retroactively.

    Fail-fast:
      - Both IDs must match the task-ID regex.
      - Blocker must exist in the backlog index.
      - Blocker must not be in a terminal state (``done`` / ``declined``).
      - Blocked must exist in the backlog index.
      - Blocked and blocker cannot be the same.
      - Wiring a reverse edge (blocker already depends on blocked via dep row or marker) is rejected as a cycle.

    Warns (but does not refuse) when the blocked task is not currently in
    ``blocked`` status. The cascade only fires on blocked tasks, so wiring a
    marker on an in-queue task is harmless metadata; the operator almost
    certainly meant to flip to blocked first.

    Idempotent: if either the dep row or the marker is already present, the
    corresponding write is skipped. ``wired: true`` in the output JSON means
    at least one of the two was newly written on this call; ``wired: false``
    means the call was a complete no-op.
    """
    blocked_task_id, blocker_task_id, reason = _parse_add_dep_argv(argv)
    if blocked_task_id is None:
        return 1

    rc = _reject_em_dash("reason", reason) if reason else None
    if rc is not None:
        return rc

    # Warn when blocked is not in `blocked` status (ADR-10 soft guidance).
    try:
        parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: cannot read backlog index: {exc}", file=sys.stderr)
        return 1
    blocked_unit = next((u for u in units if u.id == blocked_task_id), None)
    if blocked_unit is None:
        print(
            f"ERROR: add-dep: blocked task '{blocked_task_id}' not found in backlog index",
            file=sys.stderr,
        )
        return 1
    if blocked_unit.status != WorkUnitStatus.BLOCKED:
        print(
            f"WARNING: add-dep: {blocked_task_id} is currently '{blocked_unit.status.value}', "
            "not 'blocked'. The ADR-07 cascade only fires on blocked tasks -- the marker "
            "written by this call will be inert until the task is blocked.",
            file=sys.stderr,
        )

    try:
        wired = add_dep(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            blocked_task_id=blocked_task_id,
            blocker_task_id=blocker_task_id,
            reason=reason,
        )
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

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
    return 0


# A leaf Task id: every hierarchy segment present.
_DEP_EDGE_TASK_ID_RE: re.Pattern[str] = re.compile(r"^E\d+-F\d+-S\d+-T\d+$")
# A canonical work-unit id of any level: Epic, Feature, Story, or Task. Used as
# the blocker shape for ``remove-dep`` so an operator can cut a CONTAINER
# dependency edge (TDI-001). ``add-dep`` keeps the strict Task-only shape so it
# can never wire a new self-ancestor / container edge in the first place.
_DEP_EDGE_CANONICAL_ID_RE: re.Pattern[str] = re.compile(r"^E\d+(-F\d+)?(-S\d+)?(-T\d+)?$")


def _parse_dep_edge_argv(
    argv: tuple[str, ...],
    verb: str,
    *,
    allow_container_blocker: bool = False,
) -> tuple[str | None, str, str]:
    """Parse the shared ``<blocked-id> <blocker-id> [--reason <msg>]`` grammar.

    Used by both ``add-dep`` and ``remove-dep`` (their CLI grammar is identical).
    ``verb`` only feeds the error messages so each command names itself.

    The ``blocked`` operand (the depending unit whose ``## Dependencies`` table
    is edited) must always be a leaf Task. The ``blocker`` operand (the
    dependency being wired or cut) is a leaf Task by default; when
    ``allow_container_blocker`` is ``True`` it may instead be any canonical
    work-unit id (Epic / Feature / Story / Task) so ``remove-dep`` can cut a
    non-Task dependency edge an author wrote by hand (TDI-001). ``add-dep`` never
    enables this: wiring a new container edge would re-introduce the
    self-ancestor self-block the validator now rejects.

    Returns ``(blocked_id, blocker_id, reason)``. Returns ``(None, "", "")``
    after printing a usage error to stderr so the caller can ``return 1``.
    """
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
            f"ERROR: {verb} requires exactly two task ids: <blocked-task-id> <blocker-task-id>",
            file=sys.stderr,
        )
        return None, "", ""
    blocked_id, blocker_id = positional
    blocker_re = _DEP_EDGE_CANONICAL_ID_RE if allow_container_blocker else _DEP_EDGE_TASK_ID_RE
    blocker_shape = "E<N>[-F<N>][-S<N>][-T<N>]" if allow_container_blocker else "E<N>-F<N>-S<N>-T<N>"
    for label, tid, pattern, shape in (
        ("blocked", blocked_id, _DEP_EDGE_TASK_ID_RE, "E<N>-F<N>-S<N>-T<N>"),
        ("blocker", blocker_id, blocker_re, blocker_shape),
    ):
        if not pattern.match(tid):
            print(
                f"ERROR: {verb}: {label} task id '{tid}' does not match {shape} format",
                file=sys.stderr,
            )
            return None, "", ""
    return blocked_id, blocker_id, reason


def _parse_add_dep_argv(argv: tuple[str, ...]) -> tuple[str | None, str, str]:
    """Parse the add-dep flag grammar.

    Returns ``(blocked_id, blocker_id, reason)``. Returns ``(None, "", "")``
    after printing a usage error to stderr so the caller can ``return 1``.
    """
    return _parse_dep_edge_argv(argv, "add-dep")


def cmd_remove_dep(*argv: str) -> int:
    """Cut a cross-task dependency + close its marker on an existing work unit.

    Usage::

        remove-dep <blocked-task-id> <blocker-task-id> [--reason "<audit message>"]

    Exact inverse of ``add-dep``. Removes the Dependencies-table row for
    ``<blocker-task-id>`` from ``<blocked-task-id>``'s file (collapsing to the
    canonical ``| none | | |`` row when the table empties) AND strips the open
    ``[BLOCKED_PENDING_PROPOSAL] <blocker>`` marker so the ADR-07 cascade, the
    ``add-dep`` reverse-cycle guard, and every other marker reader stop treating
    the edge as live. A ``[DEP_REMOVED]`` audit comment records the cut.

    Fail-fast:
      - Both IDs must match the task-ID regex.
      - Both IDs must exist in the backlog index.
      - Blocked and blocker cannot be the same.

    Idempotent: removing an edge that does not exist is a clean no-op.
    ``removed: true`` in the output JSON means the dep row and/or the marker was
    actually removed on this call; ``removed: false`` means there was no such
    dependency and nothing was written.
    """
    blocked_task_id, blocker_task_id, reason = _parse_dep_edge_argv(argv, "remove-dep", allow_container_blocker=True)
    if blocked_task_id is None:
        return 1

    rc = _reject_em_dash("reason", reason) if reason else None
    if rc is not None:
        return rc

    try:
        removed = remove_dep(
            backlog_root=BACKLOG_ROOT,
            backlog_index=BACKLOG_INDEX,
            blocked_task_id=blocked_task_id,
            blocker_task_id=blocker_task_id,
            reason=reason,
        )
    except ProposalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not removed:
        print(
            f"INFO: remove-dep: no such dependency ({blocked_task_id} -> {blocker_task_id}); nothing to remove.",
            file=sys.stderr,
        )

    logger.info(
        "remove-dep: %s no longer blocked on %s (removed=%s)",
        blocked_task_id,
        blocker_task_id,
        removed,
    )
    print(
        json.dumps(
            {
                "blocked": blocked_task_id,
                "blocker": blocker_task_id,
                "removed": removed,
                "reason": reason,
            }
        )
    )
    return 0


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


def cmd_review_token(*argv: str) -> int:
    """Manage the file-based per-round review token (ADR-29).

    Usage:
        review-token new <unit-id>   -- write a fresh ``<unit-id>-r<n>-<rand>``
                                        token to ``<workspace>/.devbench/review-round-token``
                                        (increments the per-unit round counter) and print it.
        review-token clear           -- remove the token file.

    The ``guard-verdict-format.sh`` PreToolUse hook reads this file as the H3
    second factor; the orchestrate skill calls ``new`` before each review round
    and ``clear`` after it. Fails fast (rc=1) on bad usage or a missing unit id.
    """
    from devbench import review_token

    if not argv:
        print("review-token requires a subcommand: 'new <unit-id>' or 'clear'", file=sys.stderr)
        return 1
    sub = argv[0]
    if sub == "new":
        if len(argv) < 2 or not argv[1].strip():
            print("review-token new requires a unit id: review-token new <unit-id>", file=sys.stderr)
            return 1
        try:
            token = review_token.new_token(WORKSPACE_ROOT, argv[1])
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(token)
        return 0
    if sub == "clear":
        removed = review_token.clear_token(WORKSPACE_ROOT)
        print("review-token: cleared" if removed else "review-token: no token file to clear")
        return 0
    print(f"review-token: unknown subcommand '{sub}'; expected 'new <unit-id>' or 'clear'", file=sys.stderr)
    return 1


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
    "mark-done": (
        cmd_mark_done,
        1,
        (
            "Mark done: mark-done <id> [--already-satisfied]. "
            "--already-satisfied completes a verification-only unit whose deliverable already "
            "landed (no attributable diff); gated on verification-only Manifest + complete verify-ac evidence."
        ),
    ),
    "decline": (
        cmd_decline,
        2,
        "Mark a work unit Declined (won't ever be done) with a reason: decline <id> --reason <message>",
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
            "audit. Returns JSON envelope of flips + skips."
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
    "validate-backlog": (cmd_validate_backlog, 0, "Validate backlog integrity [--fix: auto-correct rule-10/11]"),
    "reconcile-backlog-md": (
        cmd_reconcile_backlog_md,
        0,
        (
            "Reconcile Status Summary in BACKLOG.md against the Full Work Unit Index: "
            "no flag prints mismatch report rc 0; --check-only returns rc 1 on drift; "
            "--force atomically rewrites the index region; --check-only --force errors rc 2."
        ),
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
    "git-ops-finalize": (cmd_git_ops_finalize, 1, "Push single branch and create PR: git-ops-finalize <repo>"),
    "check-merge": (
        cmd_check_merge,
        1,
        (
            "Reconcile a pause-before-merge work unit's PR state (issue #101). "
            "Promotes to done on merged, blocks on closed-without-merge, "
            "no-ops on still-open: check-merge <id>"
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
            "Run orchestrate skill via Agent SDK (non-interactive). "
            "Flags: --daemon detaches to background; "
            "--include <tokens> scopes to matching units; "
            "--exclude <tokens> skips matching units; "
            "--name <session> sets session name; "
            "--allow-overlap permits concurrent sessions (#209, #249)."
        ),
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
    "supervise": (
        cmd_supervise,
        0,
        (
            "Supervise an interactive `claude` CLI orchestrator under a detached screen daemon "
            "(--billing-mode subscription [default] | bedrock; AWS creds pass through in both). Sub-verbs: "
            "start | stop | restart | status | info | attach. "
            "start [--name N] [--include '<tokens>'] [--exclude '<tokens>'] [--allow-overlap] "
            "[--model M] [--effort E] [--billing-mode {subscription,bedrock}]; "
            "stop [--name N] [--hard]; "
            "restart [--name N]; "
            "status [--name N]; "
            "info; "
            "attach [--name N] [--screen]."
        ),
    ),
    "quota-watcher": (
        cmd_quota_watcher,
        0,
        "Inspect the quota pause checkpoint: quota-watcher --once",
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
    "run-tests": (cmd_run_tests, 1, "Run test suite for work unit's repo: run-tests <id>"),
    "verify-ac": (
        cmd_verify_ac,
        1,
        "Execute the unit's '## Verification' contract, capture tool exit codes, write evidence: verify-ac <id>",
    ),
    "log-verdict": (cmd_log_verdict, 3, "Log judge verdict: log-verdict <judge> <id> <pass|fail> [feedback]"),
    "log-comment": (cmd_log_comment, 3, "Log agent comment: log-comment <agent> <id> <message>"),
    "log-tdd": (cmd_log_tdd, 3, "Log TDD phase: log-tdd <id> <RED|GREEN|REFACTOR> <message>"),
    "request-amendment": (
        cmd_request_amendment,
        1,
        "Register an amendment request (JSON on stdin): request-amendment <id> [--operator-mode]",
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
    "remove-dep": (
        cmd_remove_dep,
        2,
        "Remove a cross-task dependency/BLOCKED_PENDING_PROPOSAL marker: "
        "remove-dep <blocked-id> <blocker-id> [--reason <msg>]",
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
    "review-token": (
        cmd_review_token,
        1,
        "Manage the per-round review token file: review-token new <unit-id> | review-token clear",
    ),
    "write-proposal": (
        cmd_write_proposal,
        1,
        "Persist a blocker-resolver proposal JSON (stdin): write-proposal <source-task-id>",
    ),
    "escalate-proposal": (
        cmd_escalate_proposal,
        1,
        'Decompose a cross-unit-defect escalation into a fix proposal (stdin {"attributed_files": [...]}): '
        "escalate-proposal <source-task-id>",
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
        "remove-dep",
        "decline",
        "hold",
        "unhold",
        "status",
        "new-task",
        "reject-proposal",
        "review-token",
        "validate-backlog",
        "log-rejection-feedback",
        # Tracked issue 014: mark-done owns its --already-satisfied flag parsing.
        "mark-done",
        # Issue #162 Phase 6: pre-render the report into a snapshot file.
        "write-snapshot",
        # Issue #162 Phase 2: rebuild per-task window-stats aggregates from the log.
        "rebuild-window-stats",
        # Issue #162 Phase 7: archive an ended session's log to Parquet (opt-in dep).
        "archive-session",
        # Issue #242 E7-F1-S1-T1: --operator-mode bypass flag
        "request-amendment",
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
        # Issue #236: quota wait-and-resume -- optional --once/--daemon flag
        "quota-watcher",
        # devbench-supervise-screen-orchestrator (FR-1): the supervise verb group
        # owns its sub-verb + flag parsing, so it needs the raw trailing argv.
        "supervise",
    }
)


def _print_usage() -> None:
    """Print top-level usage and command list. Shared by the `-h`/`--help` path and the no-args path."""
    print("Usage: devbench <command> [args]")
    print("       devbench <command> --help    (per-command usage)")
    print("       devbench --help              (this message)")
    print("\nCommands:")
    for name, (_, _, desc) in sorted(_COMMANDS.items()):
        print(f"  {name:<20} {desc}")


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
