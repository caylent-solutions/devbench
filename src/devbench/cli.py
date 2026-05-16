"""CLI entry point for the devbench system.

Provides shell-callable commands so Claude Code (or any external process)
can query backlog status and bridge plugin agents to repo context.

Usage::

    python3 -m devbench.cli [--config <path>] <command> [args]

Options::

    --config <path>         Path to devbench YAML config (sets JUDGE_CONFIG_PATH).
                            Overrides the JUDGE_CONFIG_PATH environment variable.

Commands::

    status                  Show backlog summary (counts by status)
    next                    Print the next actionable work unit ID and title
    claim <id>              Claim a work unit (transition to in-progress)
    set-status <id> <s>     Force any status (no gate -- use for recovery/lifecycle transitions)
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

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Resolved once at import time so each watch tick doesn't re-PATH-search.
# Used by `cmd_report --watch` to clear both the viewport AND the scrollback
# between frames. The fallback escape sequence ``\033c`` is the VT100 RIS
# (Reset to Initial State) -- works on every modern terminal but is more
# disruptive (resets colors). Prefer the OS clear binary when available.
_TERMINAL_CLEAR_CMD: str | None = shutil.which("clear") or shutil.which("cls")


# Pre-parse --config before any devbench imports so that config.py loads the
# correct YAML at module import time (config.py reads JUDGE_CONFIG_PATH on import).
def _pre_parse_config(argv: list[str]) -> None:
    """Extract --config <path> from argv and set JUDGE_CONFIG_PATH env var."""
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            os.environ["JUDGE_CONFIG_PATH"] = argv[i + 1]
            # Remove --config and its value so downstream parsing is unaffected.
            argv.pop(i + 1)
            argv.pop(i)
            return


_pre_parse_config(sys.argv)

from devbench.backlog.amendment import (
    REVIEW_FAILURES_DIR_NAME,
    AmendmentError,
    AmendmentRequest,
    apply_amendment,
    read_review_failure_files,
    reject_amendment,
    write_request,
)
from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.backlog.proposal import (
    BlockedTaskState,
    CascadeDepthError,
    Proposal,
    ProposalError,
    ProposalMatch,
    ProposalTaskState,
    _compute_fix_signature,
    _extract_intent_phrase,
    add_dep,
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
    MAX_CASCADE_DEPTH,
    REPO_LOCAL_PATHS,
    RUNTIME_CONFIG,
    UPDATE_SUBMODULE,
    WORKSPACE_ROOT,
    resolve_repo,
    validate_repo,
)
from devbench.config_loader import RepoConfig, get_configured_default_branch
from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_LOCAL_PATH_RE,
    COMMENT_AGENT_TEMPLATE,
    COMMENT_ENTRY_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_SUBDIR,
    DEFAULT_PLUGIN_SUBPATH,
    DISPLAY_STATUS_VALUES,
    EM_DASH,
    FINALIZE_COMMIT_TEMPLATE,
    FINALIZE_PR_TITLE_TEMPLATE,
    KNOWN_JUDGE_NAMES,
    ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX,
    ORCHESTRATOR_RESTART_EXIT_CODE,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_IN_REVIEW,
    STATUS_SEPARATOR_WIDTH,
    VALID_TDD_PHASES,
)
from devbench.log_setup import setup_logging
from devbench.plugin_shadow import (
    materialise_shadow_plugin,
    shadow_plugin_path,
    write_pid_sentinel,
)

# Re-export from reporting so existing ``cli._format_duration`` callers and tests
# resolve unchanged after the function moved to report.py for the issue #161
# orchestrator-alive banner. Single source of truth lives in report.py because
# the banner is implemented there and reporting must not depend on cli.py.
from devbench.reporting.report import _format_duration
from devbench.scope import InvalidScopeError, ScopeFilter, _expand_prefix
from devbench.utils.io import atomic_write_text
from devbench.utils.process import run_command

__all__ = ["_format_duration"]

logger = logging.getLogger("devbench.cli")


def _parse_status_argv(argv: tuple[str, ...]) -> tuple[bool, int]:
    """Return ``(detail, exit_code)``.

    ``exit_code`` is ``0`` on success and non-zero when an unknown
    positional argument was supplied (the error message is written to
    stderr inline). Extracted from ``cmd_status`` so the dispatch body
    stays under PLR0912's branch ceiling.
    """
    detail = False
    extra_positional: list[str] = []
    for arg in argv:
        if arg == "--detail":
            detail = True
            continue
        if not arg:
            continue
        extra_positional.append(arg)
    if extra_positional:
        print(
            f"ERROR: cmd_status takes no positional args (got {extra_positional!r})",
            file=sys.stderr,
        )
        return False, 1
    return detail, 0


def cmd_status(*argv: str) -> int:
    """Print backlog summary grouped by status.

    With ``--detail`` (E220), additionally render per-state sections: in-queue
    (every actionable Task with the IDs of its still-open dependencies), six
    blocked-task sections (one per :class:`~devbench.backlog.proposal.BlockedTaskState`
    in canonical order: auto-clearing, amendment-recovery, dependency, held,
    blocked-on-held, operator-required), and held (every Hold Task with
    the most recent ``[HOLD]`` reason from its Comments).
    """
    detail, parse_rc = _parse_status_argv(argv)
    if parse_rc != 0:
        return parse_rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

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
    print(f"  {'TOTAL':<15} {total:>4}")
    print(f"  {'Draft':<15} {draft_count:>4}")
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
                print(f"  {label:<28} {blocked_counts[state]:>4}")
            continue
        count = counts.get(status_val.lower(), 0)
        print(f"  {status_val:<15} {count:>4}")
    print(f"  {'Un-materialised':<15} {unmaterialised_count:>4}")

    active = [u for u in units if u.status in (WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.IN_REVIEW)]
    if active:
        print("\nActive work units:")
        for u in active:
            line = f"  [{u.status.value}] {u.id} -- {u.title}"
            if u.status is WorkUnitStatus.IN_PROGRESS:
                duration = _in_progress_attempt_duration(u.id)
                line += (
                    f" (in-progress for {duration})" if duration is not None else " (in-progress, timer unavailable)"
                )
            print(line)

    # Issue #185: ``get_parallel_candidates`` includes IN_PROGRESS tasks
    # (so an interrupted run can resume), but the "Next actionable" line
    # is meant to point at the next DIFFERENT task to start once the
    # current claim is done. Exclude any ID already rendered above in
    # ``Active work units`` so the line is genuinely actionable, not a
    # redundant echo of the in-progress claim.
    active_ids = {u.id for u in active}
    actionable = [u for u in parser.get_parallel_candidates(units) if u.id not in active_ids]
    if actionable:
        print(f"\nNext actionable: {actionable[0].id} -- {actionable[0].title}")
    elif parser.all_done(units):
        print("\nAll work units are DONE.")
    else:
        blocked = parser.get_blocked_units(units)
        print(f"\nNo actionable units. {len(blocked)} blocked.")

    if detail:
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
    ``SystemExit`` when none of ``JUDGE_LOG_FILE`` / YAML ``log_file`` /
    ``JUDGE_WORKSPACE_ROOT`` is set. Issue #185: ``devbench status``
    previously printed ``timer unavailable`` whenever ``JUDGE_LOG_FILE``
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


def cmd_next() -> int:
    """Print the next actionable work unit."""
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    candidates = parser.get_parallel_candidates(units)

    if not candidates:
        if parser.all_done(units):
            print("ALL_DONE")
        else:
            print("NO_ACTIONABLE")
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
    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_IN_PROGRESS)
    logger.info("Claimed %s (set to in-progress)", unit_id)
    print(f"Claimed {unit_id}")
    return 0


def cmd_set_status(unit_id: str, new_status: str) -> int:
    """Set the status of a work unit in both the work-unit file and BACKLOG.md."""
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

    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1

    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, new_status)

    logger.info("Set %s to %s", unit_id, new_status)

    if new_status.lower() == "blocked":
        rc = _clean_target_repo_on_block(wu_file)
        if rc != 0:
            print(f"WARNING: target repo cleanup failed for '{unit_id}' (exit {rc})", file=sys.stderr)

    print(f"Set {unit_id} to {new_status}")
    return 0


def _clean_target_repo_on_block(wu_file: Path) -> int:
    """Reset and clean the target repo's working tree when a task transitions to blocked.

    Reads the ``Local path:`` field from the work-unit file and runs
    ``git reset --hard HEAD`` and ``git clean -fd`` against that directory.
    Both commands are run with ``check=False``; returns 1 if either fails.

    If ``Local path:`` is absent from the file (e.g. validation gates with no
    local path), logs a warning and returns 0 as a defensive skip -- this is
    NOT a fallback; the task is already blocked, so failing here would obscure
    the real status transition.

    Args:
        wu_file: Path to the work-unit ``.md`` file.

    Returns:
        0 on success or when ``Local path:`` is absent; 1 if a git command
        errors.
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

    reset_result = subprocess.run(
        ["git", "-C", local_path, "reset", "--hard", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if reset_result.returncode != 0:
        logger.warning(
            "_clean_target_repo_on_block: git reset failed for '%s': %s",
            local_path,
            reset_result.stderr.strip(),
        )
        return 1

    clean_result = subprocess.run(
        ["git", "-C", local_path, "clean", "-fd"],
        check=False,
        capture_output=True,
        text=True,
    )
    if clean_result.returncode != 0:
        logger.warning(
            "_clean_target_repo_on_block: git clean failed for '%s': %s",
            local_path,
            clean_result.stderr.strip(),
        )
        return 1

    logger.info("_clean_target_repo_on_block: cleaned target repo at '%s'", local_path)
    return 0


def cmd_mark_done(unit_id: str) -> int:
    """Mark a work unit as Done, enforcing the done-gate check.

    Calls ``BacklogManager.mark_done()`` which verifies that all required
    review judges passed in the most recent round before allowing the transition.
    Raises ``RuntimeError`` if the gate check fails.
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

        decline <id> --reason "<message>"

    Declined is a deliberate final-decision status, distinct from Blocked
    (waiting on something) and Done (completed). Declined children count
    as terminal-complete for parent rollup. The ``--reason`` is REQUIRED
    because the decision must leave an audit trail; em-dashes are
    rejected at the input boundary for backlog hygiene.
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
        print("ERROR: decline requires <id> --reason <message>", file=sys.stderr)
        return 1
    rc = _reject_em_dash("reason", reason)
    if rc is not None:
        return rc

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

    Returns 0 always; output is a JSON envelope listing flips + skips.
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

        content = wu_file.read_text(encoding="utf-8")
        marker_ids = sorted(set(_BLOCKED_PENDING_PROPOSAL_MARKER_RE.findall(content)))

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

    output = {"flipped": flipped, "skipped": skipped}
    print(json.dumps(output))
    logger.info("reconcile-cascade: %d flipped, %d skipped", len(flipped), len(skipped))
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


_BLOCKED_PENDING_PROPOSAL_MARKER_RE: re.Pattern[str] = re.compile(r"\[BLOCKED_PENDING_PROPOSAL\]\s+(\S+)")

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

    Optional flag:
    - ``--fix``: Auto-correct rule-10 (em-dash) and rule-11 (checkout_directory
      prefix) violations in place and append an audit comment to each corrected
      file's ``## Comments`` section. Prints a summary of corrections made.

    Exits 0 if the backlog is consistent (or all violations were fixed); 1 with
    actionable error messages if any inconsistencies remain.
    """
    fix = "--fix" in argv
    mgr = BacklogManager()
    errors = mgr.validate(BACKLOG_INDEX, BACKLOG_INDEX.parent, fix=fix)
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


def cmd_check() -> int:
    """Pre-flight verifier for orchestrator launch readiness.

    For every repo in ``backlog/config/devbench.yaml``'s ``repos:`` map, verify:

    1. Symlink exists at ``$JUDGE_WORKSPACE_ROOT/<checkout_directory>``.
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
            "ERROR: devbench.yaml not found; set JUDGE_CONFIG_PATH or place at backlog/config/devbench.yaml",
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
    """Resolve the orchestrator log file path. Fail-fast on missing inputs.

    Resolution precedence (first match wins; no implicit fallbacks):

    1. ``JUDGE_LOG_FILE`` environment variable set to an explicit path.
       Per-invocation override; used in tests and ad-hoc overrides.
    2. ``RUNTIME_CONFIG.log_file`` from ``backlog/config/devbench.yaml``.
       Single source of truth: when the operator sets it once in YAML,
       every devbench invocation against this workspace -- the
       orchestrator's ``setup_logging`` writer and ``cmd_report``'s
       reader alike -- picks up the same path. The value is treated as
       workspace-root-relative when not absolute.
    3. ``<JUDGE_WORKSPACE_ROOT>/<DEFAULT_LOG_SUBDIR>/<DEFAULT_LOG_FILENAME>``
       convention. Fires when neither (1) nor (2) is set but the
       workspace root is known.

    When NONE of the three resolves, raises :class:`SystemExit` with an
    actionable error naming all three input shapes. The previous
    implementation silently fell back to the devbench source-tree's
    log path -- letting operators read a stale, unrelated log without
    noticing -- which CLAUDE.md "Fail-fast" forbids.
    """
    explicit = os.environ.get("JUDGE_LOG_FILE", "").strip()
    if explicit:
        return Path(explicit)
    workspace = os.environ.get("JUDGE_WORKSPACE_ROOT", "").strip()
    configured = (RUNTIME_CONFIG.log_file or "").strip()
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            return configured_path
        if workspace:
            return Path(workspace) / configured_path
        return configured_path
    if workspace:
        return Path(workspace) / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
    canonical = f"<root>/{DEFAULT_LOG_SUBDIR}/{DEFAULT_LOG_FILENAME}"
    print(
        "ERROR: cannot resolve orchestrator log file. Set one of:\n"
        "  - JUDGE_LOG_FILE=<absolute-path-to-orchestrator.log>\n"
        "  - 'log_file: <workspace-relative-path>' in backlog/config/devbench.yaml\n"
        f"  - JUDGE_WORKSPACE_ROOT=<workspace-root>  (log resolves to {canonical})\n"
        "and re-run.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def cmd_report(since: str = "", watch_interval: int = 0, once: bool = False) -> int:
    """Print a formatted progress report with velocity and completion stats.

    Issue #163: streams continuously by default when stdout is a TTY.
    Pass ``once=True`` (or pipe / redirect stdout) to get the legacy
    one-shot behaviour suitable for scripts and CI consumers.

    The legacy ``--watch N`` flag is preserved for backward compatibility
    but emits a deprecation notice and falls through to the streaming
    loop (the integer interval is ignored; cadence is data-driven).
    """
    import warnings as _warnings
    from datetime import datetime

    from devbench.reporting.report import generate_report

    log_file = _resolve_log_file_path()

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
        if since_dt is None:
            from devbench.reporting.snapshot import read_snapshot

            cached = read_snapshot(WORKSPACE_ROOT, log_file)
            if cached is not None:
                print(cached.report_text)
                return 0
        report = generate_report(log_path=log_file, since=since_dt)
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


def cmd_get_diff(unit_id: str) -> int:
    """Return the combined git diff for the work unit's target repo.

    Mode-aware per ADR-12. In the default per-task-branch mode, emits
    staged + unstaged + branch-vs-default + untracked hunks. In defer_pr
    mode (single_branch + defer_pr: true), the branch-vs-default hunk is
    omitted because it accumulates every prior task's commits on the
    shared branch; instead the function emits staged + unstaged +
    untracked, and substitutes `git show HEAD` when staged/unstaged are
    both empty (a post-commit judge invocation).

    Used by plugin agents instead of running raw git commands so they do
    not need to know the repo path or the mode.
    """
    from devbench.config import DEFER_PR

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

    if DEFER_PR:
        if not parts:
            rc, stdout, _ = run_command(["git", "show", "--format=", "HEAD"], cwd=repo_path)
            if rc == 0 and stdout.strip():
                parts.append(stdout)
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

    parts.extend(_render_untracked_hunks(repo_path))

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
    if wu_file is not None:
        manifest_rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
        assert_staged_matches_manifest(repo_path, [r.file for r in manifest_rows])

    ops.commit_local(canonical_repo, repo_path, branch, commit_message)
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
    ``JUDGE_WORKSPACE_ROOT``) produces an ``OrphanReport`` whose
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
    if wu_file is not None:
        manifest_rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
        assert_staged_matches_manifest(repo_path, [r.file for r in manifest_rows])

    ops.commit_and_push(canonical_repo, repo_path, branch, commit_message)
    logger.info("Committed and pushed %s", unit_id)

    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[PR_CREATED] {pr_url}")

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

    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

    ci_result = ops.wait_for_checks_and_classify(pr_url, repo_path)

    return _handle_finalize_ci_result(
        ci_result=ci_result,
        pr_url=pr_url,
        mgr=mgr,
    )


def cmd_watch(watch_interval: int = 0) -> int:
    """Print a live dashboard of the currently-active orchestration.

    Runs once and exits (snapshot mode) when ``watch_interval`` is ``0``.
    Otherwise enters a refresh loop that prints a fresh snapshot every
    ``watch_interval`` seconds and clears the terminal between frames, the
    same pattern ``cmd_report`` uses. ``KeyboardInterrupt`` cleanly exits 0.
    """
    from devbench.activity import collect_snapshot, render_snapshot

    log_file = Path(
        os.environ.get(
            "JUDGE_LOG_FILE",
            str(Path(__file__).resolve().parent / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME),
        )
    )
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

    Defaults ``<path>`` to ``$JUDGE_WORKSPACE_ROOT/hook-logs.jsonl`` (the same
    location ``devbench watch`` reads from). Renders timestamps in the OS
    local timezone; ``--tz`` overrides with any IANA zoneinfo name. Disables
    ANSI color when ``NO_COLOR`` is set or stdout is not a TTY.

    Phase 11 (E230) session filter:
      ``--orchestrator-only`` filters the stream to events whose
      ``orchestrator_session`` field equals
      ``$JUDGE_ORCHESTRATOR_SESSION_ID`` (set by the launch command on
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
        env_session = os.environ.get("JUDGE_ORCHESTRATOR_SESSION_ID", "").strip()
        if not env_session:
            print(
                "ERROR: --orchestrator-only requires JUDGE_ORCHESTRATOR_SESSION_ID env "
                "to be set, OR pass --orchestrator-session <id> explicitly.",
                file=sys.stderr,
            )
            return 2
        orchestrator_session_id = env_session

    # Precedence: CLI --tz > JUDGE_DISPLAY_TIMEZONE env > yaml display_timezone
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


@dataclass(frozen=True)
class _CmdStartArgs:
    """Parsed arguments for ``cmd_start``.

    Attributes:
        include: Raw ``--include`` token string (empty = include everything).
        exclude: Raw ``--exclude`` token string (empty = exclude nothing).
    """

    include: str = ""
    exclude: str = ""


def _parse_start_args(argv: tuple[str, ...]) -> _CmdStartArgs | int:
    """Parse ``cmd_start`` flags from ``argv``.

    Accepted flags::

        --include "<tokens>"   Comma-separated scope-filter include tokens.
        --exclude "<tokens>"   Comma-separated scope-filter exclude tokens.

    Args:
        argv: Trailing arguments after the ``start`` command name (may be empty).

    Returns:
        A populated ``_CmdStartArgs`` on success, or an integer exit code on error.

    Raises:
        Nothing -- all errors are printed to stderr and returned as rc=1.
    """
    include = ""
    exclude = ""
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
        else:
            print(f"ERROR: unknown flag for 'start': {arg!r}", file=sys.stderr)
            return 1
    return _CmdStartArgs(include=include, exclude=exclude)


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

    Equivalent to running ``claude --plugin-dir <plugin>`` and invoking
    the orchestrate skill interactively, but suitable for CI/unattended runs.

    Args:
        *argv: Optional flags (``--include``, ``--exclude``).

    Returns:
        0 on success, 1 on argument-parse error or invalid scope token,
        :data:`ORCHESTRATOR_RESTART_EXIT_CODE` (42) when auto-restart is triggered.

    Raises:
        Nothing from this function's own scope -- all SDK exceptions propagate
        as-is through the asyncio boundary.
    """
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions, query

    parsed = _parse_start_args(argv)
    if isinstance(parsed, int):
        return parsed

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

    async def _run() -> None:
        async for message in query(
            prompt="Run the devbench:orchestrate skill to process the backlog until complete",
            options=ClaudeAgentOptions(
                plugins=[{"type": "local", "path": str(plugin_path)}],
                permission_mode="bypassPermissions",
            ),
        ):
            logger.info("sdk message: %s", message)

    asyncio.run(_run())

    should_restart, degraded_ids = _should_auto_restart_after_no_actionable()
    if should_restart:
        logger.info("%s%s", ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX, ",".join(degraded_ids))
        return ORCHESTRATOR_RESTART_EXIT_CODE
    return 0


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


def cmd_request_amendment(unit_id: str) -> int:
    """Register an amendment request for ``unit_id``.

    Reads the request payload as JSON on stdin. Expected fields:
    ``reason``, ``justification``, ``files_to_add`` (list of ``{path, change}``),
    ``linked_acs`` (list of AC IDs). The ``task_id`` and ``requested_at``
    fields are filled in by this command -- the caller does not provide them.

    On success, writes the request to
    ``<JUDGE_WORKSPACE_ROOT>/.devbench/amendments/<unit_id>.json`` and prints
    a one-line JSON summary. Fails fast on schema errors, duplicate pending
    requests, or unknown reasons.
    """
    try:
        request = _build_amendment_request_from_stdin(unit_id)
        written_path = write_request(WORKSPACE_ROOT, request)
    except (_AmendmentRequestInputError, AmendmentError) as exc:
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
        apply_amendment(WORKSPACE_ROOT, BACKLOG_INDEX, unit_id)
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
    "set-status": (cmd_set_status, 2, "Set status: set-status <id> <status>"),
    "mark-done": (cmd_mark_done, 1, "Mark done: mark-done <id>"),
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
    "start": (cmd_start, 0, "Run orchestrate skill via Agent SDK (non-interactive)"),
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
    # Plugin agent bridge commands -- used by devbench plugin agents
    "read-unit": (cmd_read_unit, 1, "Work unit content + repo path as JSON: read-unit [--strip-comments] <id>"),
    "get-diff": (cmd_get_diff, 1, "Return combined git diff for work unit's repo: get-diff <id>"),
    "run-tests": (cmd_run_tests, 1, "Run test suite for work unit's repo: run-tests <id>"),
    "log-verdict": (cmd_log_verdict, 3, "Log judge verdict: log-verdict <judge> <id> <pass|fail> [feedback]"),
    "log-comment": (cmd_log_comment, 3, "Log agent comment: log-comment <agent> <id> <message>"),
    "log-tdd": (cmd_log_tdd, 3, "Log TDD phase: log-tdd <id> <RED|GREEN|REFACTOR> <message>"),
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
        "hook-tail",
        "watchdog",
        "add-dep",
        "decline",
        "hold",
        "unhold",
        "status",
        "new-task",
        "reject-proposal",
        "validate-backlog",
        "log-rejection-feedback",
        # Issue #162 Phase 6: pre-render the report into a snapshot file.
        "write-snapshot",
        # Issue #162 Phase 2: rebuild per-task window-stats aggregates from the log.
        "rebuild-window-stats",
        # Issue #162 Phase 7: archive an ended session's log to Parquet (opt-in dep).
        "archive-session",
        # Issue #189 E1-F4-S1-T2: bulk selectors --epic/--feature/--story
        "promote",
        # Issue #190 E2-F2-S1-T1: --include / --exclude scope selectors
        "start",
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


def _dispatch_watch_commands(command: str, watch_interval: int, args: list[str], once: bool = False) -> int | None:
    """Dispatch commands that take ``--watch N``. Return ``None`` if not handled."""
    if command == "report" and (watch_interval > 0 or once):
        since_arg = args[0] if args else ""
        return cmd_report(since=since_arg, watch_interval=watch_interval, once=once)
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
        watch_interval, args = 0, sys.argv[2:]

    # Issue #163: ``devbench report`` accepts ``--once`` / ``--no-stream``
    # to force one-shot snapshot rendering even on a TTY. Strip the flag
    # before the args reach the command function so the positional
    # ``since`` slot stays well-defined.
    once = False
    if command == "report":
        once, args = _extract_once_flag(args)

    if len(args) < min_args:
        print(f"Command '{command}' requires at least {min_args} argument(s)", file=sys.stderr)
        return 1

    watch_rc = _dispatch_watch_commands(command, watch_interval, args, once=once)
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
