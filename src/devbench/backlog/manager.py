"""Backlog manager that updates work-unit statuses and traceability.

Owns the backlog lifecycle: status writes, done-gate checks, rollups,
comments, backlog validation, and traceability logging.

Public API
----------
``force_status``              -- write any status to both files with no gate
                                checks.  Use for automated lifecycle transitions
                                (in-progress, in-review) and manual recovery.
``mark_done``                 -- gated completion: verifies all required review
                                judges passed before writing ``done``.
``mark_blocked``              -- writes ``blocked`` and appends a reason comment.
``bulk_set_status``           -- set one status on many WUs under a single
                                flock(BACKLOG.lock); writes a workspace-level
                                ``[BULK_STATUS_UPDATE]`` audit row per call.
``validate``                  -- returns integrity errors (missing files, status
                                drift, orphans, broken deps, summary mismatch).
``log_to_traceability_matrix``-- appends a spec/test mapping entry to the
                                traceability matrix file.
``_append_tdd_entry``         -- appends a timestamped TDD phase entry to the
                                ``## TDD Cycle Log`` section of a work-unit file.

Constructor
-----------
``BacklogManager(logger=None)``
    Accepts an optional ``logging.Logger`` instance.  Defaults to
    ``logging.getLogger("devbench.backlog_manager")`` when omitted.

All writes go through the private ``_set_status`` workhorse which updates
both the work-unit file and BACKLOG.md atomically.  After each write,
``_set_status`` calls the private ``_update_status_summary`` helper to keep
the ``## Status Summary`` table in sync.
"""

import itertools
import logging
import re
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from devbench.backlog.work_unit import WorkUnitType
from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_INDEX_CELL_COUNT,
    BACKLOG_STATUS_RE,
    BACKLOG_SUBDIR,
    COMMENT_AGENT_TEMPLATE,
    COMMENT_ENTRY_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    DEFAULT_TASK_TYPE,
    DEPENDENCY_NONE_VALUES,
    EM_DASH,
    EPIC_ID_RE,
    FAILURE_DIGEST_RE,
    GATED_TASK_TYPES,
    RED_OBSERVED_ENTRY_LINE_RE,
    RED_OBSERVED_MESSAGE_FIELDS_RE,
    STATUS_BLOCKED,
    STATUS_DECLINED,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_HOLD,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_LINE_RE,
    STATUS_PROPOSED,
    STATUS_SUMMARY_SECTION_HEADER,
    STATUS_SUMMARY_TABLE_HEADER,
    STRIP_SUMMARY_RE,
    TABLE_STATUS_VALUES,
    TASK_TYPE_CHORE,
    TASK_TYPE_DOCS,
    TASK_TYPE_LINE_RE,
    TASK_TYPE_REFACTOR,
    TASK_TYPE_TEST_ONLY,
    TDD_CYCLE_LOG_SECTION_BODY_RE,
    TDD_CYCLE_LOG_SECTION_HEADER,
    TDD_ENTRY_TEMPLATE,
    TRACEABILITY_MATRIX_HEADER,
    VALID_STATUSES,
    VALID_TASK_TYPES,
)
from devbench.session import flock_backlog
from devbench.utils.io import atomic_write_text

# Terminal statuses for parent-rollup purposes: a child in either state is
# "finalised" and does not block its parent from rolling to done. Kept at
# module level so tests can import and assert the exact set.
_TERMINAL_CHILD_STATUSES: frozenset[str] = frozenset({STATUS_DONE, STATUS_DECLINED})

# Marker written by ``promote-proposal`` to the source task's Comments section
# whenever a proposed draft is wired as a dependency. The auto-requeue scan
# (``_auto_requeue_marker_dependents``) reads these to discriminate blocks
# caused by a promoted proposal chain (auto-recoverable) from blocks caused by
# review failures, git-ops errors, or operator decisions (stay manual). The
# regex captures the target task ID in group 1; the scan is scoped to the
# Comments section so markers quoted in Description/Approach text cannot
# trigger the cascade.
#
# Issue #200 / AC-200-3: the original ``\S+`` capture group was too broad --
# it matched any non-whitespace word, including prose words like "Amendment"
# in lines such as ``[BLOCKED_PENDING_PROPOSAL] Amendment rejected``. This
# caused the auto-requeue cascade to treat "Amendment" as an unknown task ID
# (non-terminal), preventing the cascade from firing even when the real marker
# target (e.g. E5-F3-S1-T4) was terminal. The fix narrows the pattern to only
# capture canonical task IDs matching ``E\d+(-F\d+)?(-S\d+)?(-T\d+)?``.
# Issue #304: the marker must be anchored to the end of its audit row.
# Both writers -- ``_append_promote_comment`` (task-factory) and
# ``_append_wired_comment`` (add-dep) -- emit the marker as the final token
# of the row, so end-anchoring admits every marker devbench writes.
#
# Matching it anywhere in the Comments body meant prose that merely QUOTED a
# marker created one. An operator audit comment recording that a marker had
# been removed, quoting the removed line verbatim, silently re-blocked the
# unit on the quoted ID with no diagnostic: the file read as correct to a
# human because the only occurrence sat inside quotation marks. Agents write
# such narratives routinely, so this was reachable without an operator.
_BLOCKED_PENDING_PROPOSAL_RE: re.Pattern[str] = re.compile(
    r"\[BLOCKED_PENDING_PROPOSAL\]\s+(E\d+(?:-F\d+)?(?:-S\d+)?(?:-T\d+)?)[ \t]*$",
    re.MULTILINE,
)


# ``# <id>: <title>`` heading regex used by the operator-notification helper
# below.  Single source of truth so the manager does not re-parse work-unit
# files just to surface a Slack-friendly title.
_WU_TITLE_RE: re.Pattern[str] = re.compile(r"^#\s+\S+:\s*(.+?)\s*$", re.MULTILINE)


def _extract_wu_title(work_unit_path: Path, fallback: str) -> str:
    """Return the human-readable title from a work-unit MD file.

    Reads the first ``# E0-F1-S1-T1: Title here`` heading; returns the
    title portion stripped of trailing whitespace.  Falls back to
    *fallback* (typically the unit id) when the file is unreadable or
    has no heading.  Best-effort: never raises -- consumed by the
    notifications dispatcher which is itself best-effort.
    """
    try:
        content = work_unit_path.read_text(encoding="utf-8")
    except OSError:
        return fallback
    match = _WU_TITLE_RE.search(content)
    if match is None:
        return fallback
    return match.group(1)


# ---------------------------------------------------------------------------
# FR-4.5/FR-4.6 honest-completion-path invariants (AC-59 / AC-E4-F4-S1-T2-1..5,
# issue #257). These were originally implemented only in devbench.cli's
# cmd_mark_done wrapper (E4-F4-S1-T2 rounds 1-2). code_review (round 3) found
# a second, unguarded caller of BacklogManager.mark_done --
# _check_merge_handle_merged (devbench check-merge), reached whenever a PR
# merges externally -- through which a refactor task with no
# GREEN_GREEN_OBSERVED record, or a gated behavior-fix/feature task with no
# RED_OBSERVED record, could still reach done: empirically reproduced by the
# reviewer against this working tree. Defining the predicates and the
# rejection-message builder here, and enforcing them directly inside
# ``BacklogManager.mark_done`` (below), closes that bypass for every current
# and future caller, not just ``cmd_mark_done``. ``devbench.cli`` re-imports
# these names (mirroring its existing cross-module private-import pattern for
# e.g. ``devbench.drain._current_user``, ``devbench.scope._tokenise``) so
# every pre-existing call site and test keeps working unchanged.
# ---------------------------------------------------------------------------


def _build_remedies_rejection_message(headline: str, detail: str) -> str:
    """Build a fail-closed rejection message naming all three FR-4.5 remedies.

    Mirrors the shape of ``devbench.tdd_gate._build_rejection_message`` so
    every rejection surface in the system (the RED gate itself, the
    mark-done gated-task/refactor block, the decline-citation check)
    presents an identical, three-remedy structure (AC-59 /
    AC-E4-F4-S1-T2-1): produce a genuine RED, re-type the task, or decline
    as already-satisfied. Imports ``REMEDY_1``/``REMEDY_2``/``REMEDY_3``
    locally from ``devbench.tdd_gate`` -- deferred so the existing
    module-level ``devbench.tdd_gate -> devbench.backlog.manager`` import
    never becomes circular -- rather than re-defining the remedy text a
    second time, which would let the two copies drift.

    Args:
        headline: The one-line summary of what was rejected (no ``ERROR:``
            prefix; a caller that raises this as a ``RuntimeError`` lets
            its own caller's ``print(f"ERROR: {exc}")`` supply it, and a
            caller that prints directly adds its own prefix).
        detail: A rejection-specific explanation of why.

    Returns:
        The full multi-line rejection message, headline first, then the
        detail, then all three remedies enumerated.
    """
    from devbench.tdd_gate import REMEDY_1, REMEDY_2, REMEDY_3

    lines = [
        headline,
        f"  Detail: {detail}",
        "  Remedies:",
        f"    1. {REMEDY_1}",
        f"    2. {REMEDY_2}",
        f"    3. {REMEDY_3}",
    ]
    return "\n".join(lines)


def _red_observed_message_has_all_required_fields(message: str) -> bool:
    """Return True iff *message* parses into a well-formed RED_OBSERVED record.

    Re-validates all three fields independently of whatever validation ran
    at write time (MEDIUM/LOW findings inherited on E4-F3-S1-T1): the parsed
    ``exit_code`` must not be ``"0"`` and the parsed ``failure_digest`` must
    match ``FAILURE_DIGEST_RE``.

    Args:
        message: The message body captured from a RED_OBSERVED entry line.

    Returns:
        ``True`` only when all three fields are present, ``exit_code`` is
        nonzero, and ``failure_digest`` is hash-shaped.
    """
    fields_match = RED_OBSERVED_MESSAGE_FIELDS_RE.search(message)
    if fields_match is None:
        return False
    if fields_match.group("exit_code") == "0":
        return False
    return bool(FAILURE_DIGEST_RE.match(fields_match.group("failure_digest")))


def red_gate_satisfied(content: str) -> bool:
    """Return True iff the work unit's TDD Cycle Log contains a RED_OBSERVED entry.

    Security-critical predicate (E4-F3-S1-T1 inherited findings): an
    agent-written ``[RED]`` entry must never be able to satisfy this gate on
    its own. Three defenses combine to close the forgery vectors identified
    in review:

    1. Section-scoping: only text inside the ``## TDD Cycle Log`` section is
       considered (``TDD_CYCLE_LOG_SECTION_BODY_RE``) -- a RED_OBSERVED-shaped
       line anywhere else (e.g. an agent's ``## Comments`` entry) never counts.
       When the section header is absent, this returns ``False`` outright --
       no fallback scan of the whole document.
    2. Anchored line matching: ``RED_OBSERVED_ENTRY_LINE_RE`` only matches a
       ``[RED_OBSERVED]`` tag at an entry line's structural start position, so
       an agent cannot forge the tag by embedding it mid-message inside a
       legitimate ``[RED]`` entry.
    3. Full record re-validation: the matched entry's message must parse via
       ``RED_OBSERVED_MESSAGE_FIELDS_RE`` into all three required fields, with
       a nonzero ``exit_code`` and a hash-shaped ``failure_digest``.

    Args:
        content: The full text of a work-unit markdown file.

    Returns:
        ``True`` only when a structurally well-formed, fully-populated
        RED_OBSERVED record is present inside the TDD Cycle Log section;
        ``False`` in every other case.
    """
    section_match = TDD_CYCLE_LOG_SECTION_BODY_RE.search(content)
    if section_match is None:
        return False
    section_body = section_match.group(1)
    return any(
        _red_observed_message_has_all_required_fields(line_match.group("message"))
        for line_match in RED_OBSERVED_ENTRY_LINE_RE.finditer(section_body)
    )


# The machine-observed green-green record's phase tag (FR-4.6 /
# AC-E4-F4-S1-T2-4, code_review FAIL round 2: "cmd_green_green_check is a
# standalone command that writes no record to the work unit and is consumed
# by no gate") is ``devbench.constants.TDD_PHASE_GREEN_GREEN_OBSERVED``,
# imported above -- registered in ``VALID_TDD_PHASES`` (code_review FAIL
# round 4, SOLID/OCP: a private local literal here left it invisible to
# ``cli._reject_bracketed_phase_tag``'s bracketed-phase-tag security control,
# which is built from ``VALID_TDD_PHASES``, so ``[GREEN_GREEN_OBSERVED]``
# went unrejected in agent free text -- unlike ``[RED_OBSERVED]``). Only
# ``devbench.cli.cmd_green_green_check`` (an orchestrator-facing command,
# mirroring ``write_red_observed_entry``'s RED_OBSERVED discipline) ever
# writes it: ``cmd_log_tdd`` (the only agent-facing writer of TDD Cycle Log
# entries) rejects any phase not in ``AGENT_WRITABLE_TDD_PHASES`` before
# ever reaching ``BacklogManager._append_tdd_entry``.

# Anchored (line-start) match for a GREEN_GREEN_OBSERVED entry, mirroring
# RED_OBSERVED_ENTRY_LINE_RE: a bare substring/"in" check would let a
# REFACTOR entry's agent-written free-text message body forge the tag (the
# same HIGH finding class E4-F3-S1-T1 closed for RED_OBSERVED); anchoring to
# the entry's structural start position closes it here too.
_GREEN_GREEN_OBSERVED_ENTRY_LINE_RE = re.compile(
    r"^-\s+\[GREEN_GREEN_OBSERVED\]\s+\S+\s+--\s+(?P<message>.+)$",
    re.MULTILINE,
)

_GREEN_GREEN_OBSERVED_MESSAGE_TEMPLATE: str = "test_node_ids={test_node_ids}"

# Re-validates the matched entry's message body independently of whatever
# validation ran at write time (mirroring RED_OBSERVED_MESSAGE_FIELDS_RE):
# requires the single ``test_node_ids=`` field, a comma-joined run of
# non-whitespace node ids, with no partial-record fallback.
_GREEN_GREEN_OBSERVED_MESSAGE_FIELDS_RE = re.compile(r"^test_node_ids=(?P<test_node_ids>\S+)$")


def green_green_observed_satisfied(content: str) -> bool:
    """Return True iff the work unit's TDD Cycle Log contains a well-formed
    ``GREEN_GREEN_OBSERVED`` entry (FR-4.6 / AC-E4-F4-S1-T2-4).

    Mirrors ``red_gate_satisfied``'s three defenses so a ``refactor`` task's
    own invariant is verifiable end to end, not merely runnable:

    1. Section-scoping: only text inside the ``## TDD Cycle Log`` section is
       considered (``TDD_CYCLE_LOG_SECTION_BODY_RE``) -- a
       GREEN_GREEN_OBSERVED-shaped line anywhere else never counts.
    2. Anchored line matching: ``_GREEN_GREEN_OBSERVED_ENTRY_LINE_RE`` only
       matches a ``[GREEN_GREEN_OBSERVED]`` tag at an entry line's
       structural start position, so an agent cannot forge the tag by
       embedding it mid-message inside a legitimate ``[REFACTOR]`` entry.
    3. Full record re-validation: the matched entry's message must parse
       via ``_GREEN_GREEN_OBSERVED_MESSAGE_FIELDS_RE`` into the required
       ``test_node_ids`` field.

    Args:
        content: The full text of a work-unit markdown file.

    Returns:
        ``True`` only when a structurally well-formed GREEN_GREEN_OBSERVED
        record is present inside the TDD Cycle Log section; ``False`` in
        every other case.
    """
    section_match = TDD_CYCLE_LOG_SECTION_BODY_RE.search(content)
    if section_match is None:
        return False
    section_body = section_match.group(1)
    return any(
        _GREEN_GREEN_OBSERVED_MESSAGE_FIELDS_RE.match(line_match.group("message")) is not None
        for line_match in _GREEN_GREEN_OBSERVED_ENTRY_LINE_RE.finditer(section_body)
    )


class BacklogManager:
    """Owns backlog lifecycle: status writes, done-gate checks, rollups, comments, and validation."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the manager.

        Args:
            logger: Optional logger instance.  Defaults to
                ``logging.getLogger("devbench.backlog_manager")`` when omitted.
        """
        self.logger = logger or logging.getLogger("devbench.backlog_manager")
        # Idempotency guard for the auto-requeue cascade (issue #147). A
        # ``(backlog_index, unit_id)`` pair is added when the cascade runs;
        # subsequent ``_set_status`` calls for the same terminal target skip
        # the scan. The set is per-instance so independent ``BacklogManager``
        # constructions get their own guard (tests share no state).
        self._cascade_fired_for: set[tuple[str, str]] = set()
        # Populated by ``validate(fix=True)``: ``(fix_count, files_fixed)``.
        self._fix_summary: tuple[int, int] = (0, 0)

    def force_status(
        self,
        work_unit_path: Path,
        backlog_index: Path,
        unit_id: str,
        new_status: str,
        session_name: str | None = None,
    ) -> None:
        """Write any status to both files, bypassing all gate checks.

        Use this for automated lifecycle transitions (``in-progress``,
        ``in-review``) and for manual recovery overrides where the done-gate
        would incorrectly block a legitimate repair.  Prefer ``mark_done``
        for agent-driven completion -- it enforces that all judges have passed.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier (e.g. ``E0-F1-S1-T1``).
            new_status: Status in CLI form (``in-queue``, ``in-progress``,
                ``in-review``, ``done``, ``blocked``) or title-case form.
            session_name: Optional named-session identifier sourced from
                ``DEVBENCH_SESSION_NAME``.  When provided and the target
                status is ``in-progress``, the ``[WU_CLAIMED]`` audit comment
                is extended with ``session=<name>`` per spec section 4.4.7
                and spec section 6 (AC-192-6).

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status is invalid, the ``## Status:`` line
                is missing, or the unit is not found in the backlog index.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, new_status, session_name=session_name)

    def _check_task_type_done_invariant(self, unit_id: str, content: str) -> None:
        """Raise ``RuntimeError`` when *content*'s own FR-4.5/FR-4.6 task-type
        completion invariant is unmet.

        Consolidates the two task-type-specific done-gate checks -- a
        machine-observed ``RED_OBSERVED`` record for ``GATED_TASK_TYPES``
        (behavior-fix/feature) and a machine-observed ``GREEN_GREEN_OBSERVED``
        record for ``refactor`` -- into a single check called directly by
        :meth:`mark_done`, so every caller of ``mark_done`` inherits it
        (code_review FAIL, E4-F4-S1-T2 round 3: this logic previously lived
        only in ``devbench.cli.cmd_mark_done``'s wrapper, so the second
        ``mark_done`` caller, ``devbench.cli._check_merge_handle_merged``
        [reached by ``devbench check-merge``], bypassed both checks
        entirely -- empirically reproduced by the reviewer). A task with no
        ``## Task Type:`` section defaults to ``DEFAULT_TASK_TYPE``
        (``behavior-fix``, the strictest type), matching
        ``_check_task_type_taxonomy``'s fail-closed precedent, so omitting
        the section is never an escape hatch from the RED gate.

        Args:
            unit_id: Work unit ID, named in the rejection message.
            content: The full text of the work-unit markdown file.

        Raises:
            RuntimeError: *task_type*'s own invariant is unmet; the message
                names all three FR-4.5 remedies (AC-59 / AC-E4-F4-S1-T2-1).
        """
        declared = self._extract_task_type(content)
        task_type = declared if declared is not None else DEFAULT_TASK_TYPE

        if task_type in GATED_TASK_TYPES and not red_gate_satisfied(content):
            raise RuntimeError(
                _build_remedies_rejection_message(
                    f"Cannot mark {unit_id} done: no RED_OBSERVED record found.",
                    f"Task type is {task_type!r} (gated); FR-4.6 requires a machine-observed "
                    "RED_OBSERVED entry in the TDD Cycle Log before a gated task can reach done.",
                )
            )

        if task_type == TASK_TYPE_REFACTOR and not green_green_observed_satisfied(content):
            raise RuntimeError(
                _build_remedies_rejection_message(
                    f"Cannot mark {unit_id} done: no GREEN_GREEN_OBSERVED record found.",
                    "Task type is 'refactor'; FR-4.6 requires a machine-observed GREEN_GREEN_OBSERVED "
                    "entry in the TDD Cycle Log -- run 'devbench green-green-check <id> <test_node_id> "
                    "[...]' and let it pass -- before a refactor task can reach done.",
                )
            )

    def mark_done(self, work_unit_path: Path, backlog_index: Path, unit_id: str) -> None:
        """Mark a work unit as Done in both files.

        Raises ``RuntimeError`` if the task's own FR-4.5/FR-4.6 task-type
        completion invariant is unmet (``_check_task_type_done_invariant``),
        or if not all required review judges have passed in the most recent
        review round (done-gate enforcement). Both checks are enforced here
        -- not in a CLI-layer wrapper -- so every caller (``cmd_mark_done``,
        ``_check_merge_handle_merged``, and any future caller) inherits them
        identically; there is no second done-transition path that can skip
        either check (E4-F4-S1-T2 round 3).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.

        Raises:
            RuntimeError: If the task-type invariant is unmet, or if not all
                required judges passed in the last round.
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        content = work_unit_path.read_text(encoding="utf-8")
        self._check_task_type_done_invariant(unit_id, content)
        if not self._last_round_all_passed(work_unit_path):
            raise RuntimeError(
                f"Cannot mark {unit_id} done: not all required judges passed in the most recent review round"
            )
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_DONE)
        # Operator notification (PR #202).  notify_* helpers are best-effort
        # and gated by the per-event toggle in devbench.yaml; safe to call
        # unconditionally.
        from devbench.notifications import notify_work_unit_done

        title = _extract_wu_title(work_unit_path, unit_id)
        notify_work_unit_done(unit_id, title)

    def mark_blocked(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Mark a work unit as Blocked in both files and append a comment.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable reason the work unit is blocked.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_BLOCKED)
        self._append_comment(work_unit_path, "BLOCKED", reason)
        # Operator notification (PR #202).  Fires only when the classifier
        # determines the block is OPERATOR_ACTION_REQUIRED -- the other
        # blocked-buckets (AWAITING_DEPENDENCY, AUTO_CLEARING_VIA_PROPOSAL,
        # etc.) auto-resolve so notifying on them every time would be noisy.
        # All notification helpers are best-effort and gated by per-event
        # toggles in devbench.yaml; safe to call unconditionally.
        try:
            from devbench.backlog.proposal import classify_blocked_task
            from devbench.notifications import notify_blocked_classification_transition

            workspace_root = backlog_index.parent
            state = classify_blocked_task(
                backlog_index.parent / "backlog",
                backlog_index,
                unit_id,
                workspace_root=workspace_root,
            )
            title = _extract_wu_title(work_unit_path, unit_id)
            # Issue #207 + #209: routes through the transition-aware helper
            # so the ping fires on every transition INTO any of the seven
            # blocked classes (initial mark_blocked OR later reclassification
            # via cmd_sync_blocked / cmd_reconcile_cascade).  Each class has
            # its own per-event toggle in devbench.yaml; the dispatcher
            # picks the right notify_* helper based on the classifier's
            # return value.
            notify_blocked_classification_transition(unit_id, title, reason, state.name, workspace_root)
        except (OSError, ValueError, ImportError):
            # Classifier I/O failures should not block the status write.
            pass

    def mark_declined(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Mark a work unit as Declined in both files and append a comment.

        Declined means "this work has been determined to never be done" -- a
        deliberate, final decision distinct from Blocked (waiting) and Done
        (completed). Children marked Declined count as terminal-complete for
        parent rollup purposes (see :meth:`_all_children_done`).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable rationale for the decision. Captured in
                the Comments audit trail alongside a ``[DECLINED]`` marker.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_DECLINED)
        self._append_comment(work_unit_path, "DECLINED", reason)

    def mark_held(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Mark a work unit as ``hold`` in both files and append an audit comment.

        ``hold`` is a deferred-decision status: the unit is intentionally
        skipped by the orchestrator's ``next``/parallel-candidate scan
        (``BacklogParser.get_parallel_candidates`` filters to
        ``IN_QUEUE``/``IN_PROGRESS`` only) until an operator runs
        ``unmark_held`` to return it to ``in-queue``. Unlike ``declined``,
        ``hold`` is **not** terminal -- a held child does NOT count toward
        a parent's auto-rollup to ``done``; the parent stays open while
        any descendant is on hold.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable rationale for the deferral. Captured in
                the Comments audit trail alongside a ``[HOLD]`` marker.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_HOLD)
        self._append_comment(work_unit_path, "HOLD", reason)

    def unmark_held(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Return a held work unit to ``in-queue`` and append an audit comment.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable rationale for the release. Captured in
                the Comments audit trail alongside a ``[UNHOLD]`` marker.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_IN_QUEUE)
        self._append_comment(work_unit_path, "UNHOLD", reason)

    def remove_unit(
        self,
        work_unit_path: Path,
        backlog_index: Path,
        unit_id: str,
        reason: str,
        audit_log_path: Path,
    ) -> None:
        """Remove a work unit through the managed path (db-303, spec 4.A, FR-16).

        Runs under a single ``flock(BACKLOG.lock)`` so a concurrent devbench
        session cannot interleave a partial removal with another write.
        Deletes the ``unit_id`` row from the BACKLOG.md index first --
        :meth:`_remove_backlog_index_row` raises ``ValueError`` before any
        file is touched when no row matches, so a typo can never delete an
        unrelated unit -- then deletes the work-unit ``.md`` file, re-rolls
        the ``## Status Summary`` table via :meth:`_update_status_summary`,
        and appends a ``[WU_REMOVED] <id> -- <reason>`` line to
        *audit_log_path* using the same timestamped-append shape as
        :meth:`bulk_set_status`'s ``[BULK_STATUS_UPDATE]`` row.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file to delete.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier to remove.
            reason: Human-readable rationale for the removal, captured
                verbatim in the ``[WU_REMOVED]`` audit line.
            audit_log_path: Path where the ``[WU_REMOVED]`` audit row is
                appended. The file and its parent directories are created
                when absent.

        Raises:
            ValueError: No BACKLOG.md row matches ``unit_id``. Nothing is
                deleted.
            FileNotFoundError: ``backlog_index`` does not exist, or
                ``work_unit_path`` does not exist on disk.
            TimeoutError: The BACKLOG.lock could not be acquired within the
                default timeout.
            OSError: An unexpected OS error from ``fcntl.flock`` or file I/O.
        """
        workspace_root = backlog_index.parent

        with flock_backlog(workspace_root):
            self._remove_backlog_index_row(backlog_index, unit_id)
            work_unit_path.unlink()
            self._update_status_summary(backlog_index)

            audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            audit_row = f"[{timestamp}] [WU_REMOVED] {unit_id} -- {reason}\n"
            with audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(audit_row)

        self.logger.info("Removed work unit %s (reason: %s)", unit_id, reason)

    def _remove_backlog_index_row(self, backlog_index: Path, unit_id: str) -> None:
        """Delete ``unit_id``'s row from the BACKLOG.md index table.

        Mirrors :meth:`_update_backlog_index`'s row-match algorithm (the
        row's first cell, ``cells[1]``, matched exactly against
        ``unit_id``) but removes the matched line entirely instead of
        rewriting a status cell within it.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier whose row is removed.

        Raises:
            FileNotFoundError: ``backlog_index`` does not exist.
            ValueError: No row in the index matches ``unit_id``.
        """
        if not backlog_index.exists():
            raise FileNotFoundError(f"Backlog index not found: {backlog_index}")

        content = backlog_index.read_text(encoding="utf-8")
        lines = content.splitlines()
        new_lines: list[str] = []
        removed = False

        for line in lines:
            if not removed and line.strip().startswith("|"):
                cells = line.split("|")
                row_id = cells[1].strip() if len(cells) > 1 else ""
                if row_id == unit_id:
                    removed = True
                    continue
            new_lines.append(line)

        if not removed:
            raise ValueError(f"remove: work unit '{unit_id}' not found in BACKLOG.md")

        atomic_write_text(backlog_index, "\n".join(new_lines) + "\n")
        self.logger.info("Removed %s row from %s", unit_id, backlog_index.name)

    def bulk_set_status(
        self,
        unit_ids: list[tuple[str, Path]],
        new_status: str,
        backlog_index: Path,
        audit_log_path: Path,
        *,
        audit_meta: str,
    ) -> int:
        """Set the same status on every work unit in the list under a single flock.

        Acquires ``flock(BACKLOG.lock)`` exactly once for the entire batch so that
        concurrent devbench sessions cannot interleave partial writes.  Every
        per-WU update is routed through :meth:`_set_status` so existing audit
        logic (``[WU_CLAIMED]``, checkbox ticks, parent rollup, cascade requeue)
        continues to fire for each work unit.

        After all per-WU writes complete, a single workspace-level
        ``[BULK_STATUS_UPDATE] <count> WUs set to '<status>' by <audit_meta>``
        row is appended to *audit_log_path* (parent directories are created
        automatically if absent).

        Args:
            unit_ids: Ordered list of ``(unit_id, work_unit_path)`` pairs to
                update.  Pass an empty list to record a zero-count audit row
                without touching any files.
            new_status: Target status string (CLI form or title-case).  Validated
                against :data:`~devbench.constants.VALID_STATUSES` before the
                flock is acquired; raises immediately on invalid input so no
                partial writes occur.
            backlog_index: Path to the ``BACKLOG.md`` index file.  The parent
                directory of this file is used as the workspace root when
                acquiring the flock.
            audit_log_path: Path where the ``[BULK_STATUS_UPDATE]`` audit row is
                appended.  The file and its parent directories are created when
                absent.
            audit_meta: Caller-supplied selector description appended verbatim to
                the audit row (e.g. ``'--include="E7" --exclude="E7-F3"'``).

        Returns:
            The number of work units that were updated (equals ``len(unit_ids)``
            on success).

        Raises:
            ValueError: *new_status* is not a recognised status value.
            FileNotFoundError: A work-unit file or ``backlog_index`` does not
                exist.
            TimeoutError: The BACKLOG.lock could not be acquired within the
                default timeout.
            OSError: An unexpected OS error from ``fcntl.flock`` or file I/O.
        """
        # Validate status early -- fail fast before acquiring the lock.
        canonical = VALID_STATUSES.get(new_status.lower())
        if canonical is None:
            raise ValueError(f"Invalid status '{new_status}'. Valid statuses: {', '.join(sorted(VALID_STATUSES))}")

        workspace_root = backlog_index.parent

        with flock_backlog(workspace_root):
            for unit_id, work_unit_path in unit_ids:
                self._set_status(work_unit_path, backlog_index, unit_id, canonical)

        count = len(unit_ids)
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        audit_row = f"[{timestamp}] [BULK_STATUS_UPDATE] {count} WUs set to '{canonical}' by {audit_meta}\n"
        with audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(audit_row)

        self.logger.info(
            "Bulk status update: %d WUs set to '%s'; audit written to %s",
            count,
            canonical,
            audit_log_path,
        )
        return count

    def validate(
        self,
        backlog_index: Path,
        workspace_root: Path,
        fix: bool = False,
        strict: bool = False,
    ) -> list[str]:
        """Check backlog integrity and return a list of error messages.

        Checks performed:
        1. Every row in BACKLOG.md has a corresponding work unit file.
        2. Every work unit file's status matches the index.
        3. No orphaned work unit files (in workspace_root/backlog/ but not in index).
        4. All dependency IDs reference real work unit IDs in the index.
        5. Status Summary table exists and counts match the Full Work Unit Index.
        6. Task files have non-empty ## Description section.
        7. Task files have ## Acceptance Criteria with at least one AC- item.
        8. Task files have ## Changes Manifest with at least one entry.
        9. Task files have ## Definition of Done section.
        10. No em-dash character (U+2014) in work unit files.
        11. Changes Manifest paths do not start with a ``checkout_directory`` prefix.
        12. Manifest path conflicts: HARD claimants (``in-queue``, ``proposed``,
            ``blocked``, ``in-progress``) of one path with no ordering dependency
            are always an error (FR-3, db-313). Under ``strict=True``, SOFT
            claimants (``draft``, ``hold``) are folded into the count too,
            surfacing authoring-time draft/hold collisions (FR-4, db-267).
        13. Language-AC alignment (non-Python tasks must mark Python ACs N/A).
        14. Source-test atomicity (every prod source has a paired test in the same Manifest).
        15. Required sections (Status, Dependencies, Changes Manifest) on every Task.
        16. Status enum (every parsed ``## Status:`` value matches ``VALID_STATUSES``).
        17. Dependency-ID format (every entry in a ``## Dependencies`` table matches the canonical regex).
        18. Branch uniqueness (no two Tasks derive the same branch name; skipped in single-PR mode).
        19. No placeholder Manifest rows (no active Task carries a ``TBD`` row in its Changes Manifest).
        20. No orphan path tokens in AC / DoD (gated by ``validate.check_orphan_path_tokens``):
            backtick-quoted path-shaped tokens in ``## Acceptance Criteria`` and
            ``## Definition of Done`` must appear in the Task's Changes Manifest after
            normalisation, OR be marked read-only via a trailing ``(ref)`` suffix.
        21. Unique work-unit IDs: no ID appears on more than one index row.
        22. Six-type task taxonomy (FR-4.1): the optional ``## Task Type:`` section
            (defaulting to ``behavior-fix`` when absent) must name one of the six
            allowed types, and each type's Changes Manifest rows must satisfy its
            per-type invariant (production-source presence for gated types;
            test-only / docs / chore row-shape restrictions for exempt types).
            Terminal Tasks (``done`` / ``declined``) are skipped.
        23. Already-satisfied decline citation (FR-4.5): a ``declined`` Task whose
            ``[DECLINED]`` comment reason contains ``already-satisfied`` must cite
            the closing commit hash or task id somewhere in that reason; an
            uncited already-satisfied decline is an unfalsifiable claim and is
            rejected.
        24. Dependency cycle detection (FR-1) unions three edge channels that all
            encode the same directed ``blocked_id -> blocker_id`` edge: the index
            ``Dependencies`` column, each non-terminal Task's ``## Dependencies``
            table, and its ``[BLOCKED_PENDING_PROPOSAL]`` markers. Terminal
            (``done`` / ``declined``) Tasks contribute no table/marker edge, so a
            stale marker on a closed Task cannot resurrect a historical cycle.
        25. Dangling ``[BLOCKED_PENDING_PROPOSAL]`` markers (FR-7): a non-terminal
            Task's marker referencing a WU-ID absent from the index is an error,
            surfaced here instead of silently surviving until ``reconcile-cascade``
            trips on it.
        26. File-path contract for Task rows (FR-20, db-279): a Task row with an
            empty ``File Path`` cell is an error. Epic/Feature/Story rows may be
            file-less; this converges the validator with ``parse_index``, which
            raises on the same file-less Task row and tolerates a file-less
            non-Task row.
        27. Marker/status agreement: a non-terminal Task carrying a
            ``[BLOCKED_PENDING_PROPOSAL]`` marker whose target is itself
            non-terminal MUST have status ``blocked``. The marker and the status
            are written by separate steps, and the ADR-07 cascade
            (:meth:`_auto_requeue_marker_dependents`) skips non-blocked
            candidates, so a mismatch strands the task permanently once its
            blocker completes. Markers whose targets are all terminal are exempt
            (the cascade has legitimately requeued the task).

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            workspace_root: Workspace root containing BACKLOG.md and the backlog/ subdirectory.
            fix: When ``True``, auto-correct rule-10 (em-dash) and rule-11
                (checkout_directory prefix) violations in-place and append an
                audit comment to each corrected file's ``## Comments`` section.
                Violations that were corrected are NOT included in the returned
                error list. Without ``fix``, the method is read-only.
            strict: When ``True``, rule 12 (Manifest path conflicts) also
                reports draft/hold collisions (FR-4, db-267). Defaults to
                ``False``, preserving the all-draft rc=0 authoring gate.

        Returns:
            A list of error strings. Empty list means the backlog is valid (or
            all fixable violations were corrected when ``fix=True``).
        """
        rows = self._parse_backlog_rows(backlog_index)
        known_ids = {row_id for row_id, _, _ in rows if row_id and not row_id.startswith("-")}

        errors: list[str] = []
        self._check_full_index_has_rows(backlog_index, errors)
        self._check_unique_ids(rows, errors)
        indexed_files = self._check_files_and_statuses(rows, workspace_root, errors)
        self._check_task_rows_have_files(rows, errors)
        self._check_orphans(workspace_root, indexed_files, errors)
        self._check_dependencies(backlog_index, known_ids, errors)
        self._check_dep_cycles(backlog_index, rows, workspace_root, errors)
        self._check_dangling_markers(rows, workspace_root, known_ids, errors)
        self._check_status_summary(backlog_index, rows, errors)
        if fix:
            fix_count, fix_files = self._apply_fixes(rows, workspace_root)
            self._fix_summary = (fix_count, fix_files)
            rows = self._parse_backlog_rows(backlog_index)
        self._check_task_content(rows, workspace_root, errors)
        self._check_manifest_path_prefixes(rows, workspace_root, errors)
        self._check_no_glob_in_manifest(rows, workspace_root, errors)
        self._check_manifest_conflicts(rows, workspace_root, errors, strict=strict)
        self._check_language_ac_alignment(rows, workspace_root, errors)
        self._check_source_test_pairs(rows, workspace_root, errors)
        self._check_task_type_taxonomy(rows, workspace_root, errors)
        self._check_required_sections(rows, workspace_root, errors)
        self._check_status_enum(rows, workspace_root, errors)
        self._check_dep_id_format(rows, workspace_root, errors)
        self._check_branch_uniqueness(rows, workspace_root, errors)
        self._check_no_placeholder_manifest_rows(rows, workspace_root, errors)
        self._check_no_orphan_path_tokens(rows, workspace_root, errors)
        self._check_marker_status_agreement(rows, workspace_root, errors)
        self._check_already_satisfied_decline_citation(rows, workspace_root, errors)
        return errors

    _CANONICAL_FULL_INDEX_HEADER_CELLS: tuple[str, ...] = (
        "",
        "ID",
        "Title",
        "Type",
        "Status",
        "Dependencies",
        "Repo",
        "File Path",
        "",
    )

    @staticmethod
    def _scan_full_index_rows(
        backlog_index: Path,
    ) -> tuple[list[str] | None, list[str] | None, int]:
        """Return ``(header_text_cells, separator_cells, real_row_count)``.

        First-pass scanner used by ``_check_full_index_has_rows``. The
        header text row is the first pipe-row inside ``## Full Work Unit
        Index`` whose second cell is ``id`` (case-insensitive); the
        separator row is the next pipe-row whose second cell begins
        with ``-``.
        """
        content = backlog_index.read_text(encoding="utf-8")
        in_full_index = False
        header_text_cells: list[str] | None = None
        separator_cells: list[str] | None = None
        real_row_count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                in_full_index = "Full Work Unit Index" in stripped
                continue
            if not in_full_index or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if header_text_cells is None and len(cells) >= 3 and cells[1].lower() == "id":
                header_text_cells = cells
                continue
            if (
                header_text_cells is not None
                and separator_cells is None
                and len(cells) >= 2
                and cells[1].startswith("-")
            ):
                separator_cells = cells
                continue
            if len(cells) != BACKLOG_INDEX_CELL_COUNT:
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            real_row_count += 1
        return header_text_cells, separator_cells, real_row_count

    @staticmethod
    def _format_header_validation_errors(
        backlog_index: Path,
        header_text_cells: list[str] | None,
        separator_cells: list[str] | None,
        real_row_count: int,
    ) -> list[str]:
        """Convert scan results into the ordered list of prepended errors."""
        prepended: list[str] = []
        if header_text_cells is None:
            prepended.append(
                f"No '## Full Work Unit Index' header text row found in '{backlog_index}'."
                " Expected canonical 7-column header:"
                " | ID | Title | Type | Status | Dependencies | Repo | File Path |"
            )
        elif len(header_text_cells) != BACKLOG_INDEX_CELL_COUNT:
            prepended.append(
                f"'## Full Work Unit Index' header row in '{backlog_index}' has"
                f" {len(header_text_cells)} cells; expected {BACKLOG_INDEX_CELL_COUNT}"
                " (canonical 7-column format:"
                " | ID | Title | Type | Status | Dependencies | Repo | File Path |)."
            )
        elif tuple(header_text_cells) != BacklogManager._CANONICAL_FULL_INDEX_HEADER_CELLS:
            prepended.append(
                f"'## Full Work Unit Index' header row in '{backlog_index}' does not match"
                " the canonical column order/spelling."
                f" Got: | {' | '.join(header_text_cells[1:-1])} |."
                " Expected: | ID | Title | Type | Status | Dependencies | Repo | File Path |."
            )

        if header_text_cells is not None and separator_cells is None:
            prepended.append(f"'## Full Work Unit Index' separator row (|---|---|...) is missing in '{backlog_index}'.")
        elif separator_cells is not None and len(separator_cells) != BACKLOG_INDEX_CELL_COUNT:
            prepended.append(
                f"'## Full Work Unit Index' separator row in '{backlog_index}' has"
                f" {len(separator_cells)} cells; expected {BACKLOG_INDEX_CELL_COUNT}."
            )

        if real_row_count == 0:
            prepended.append(
                f"No work-unit rows parsed from '{backlog_index}'."
                " The '## Full Work Unit Index' section is missing or malformed."
                " Expected 7-column rows:"
                " | ID | Title | Type | Status | Dependencies | Repo | File Path |"
            )
        return prepended

    @staticmethod
    def _check_full_index_has_rows(backlog_index: Path, errors: list[str]) -> None:
        """Rule-0: verify '## Full Work Unit Index' uses the canonical 7-column header and contains at least one row.

        The header text row MUST read exactly:
            ``| ID | Title | Type | Status | Dependencies | Repo | File Path |``
        and the separator row immediately below it MUST have the same
        number of cells. A zero-row result, an unexpected column order /
        spelling, or a malformed separator all surface as distinct errors
        so the operator can fix the root cause directly.

        This check fires BEFORE orphan detection (rule 3) so a malformed
        index does not produce a stale orphan-storm that obscures the root
        cause. New errors are prepended so they appear first regardless of
        call order within ``validate()``.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            errors: Mutable error list; new errors are prepended.
        """
        header_text_cells, separator_cells, real_row_count = BacklogManager._scan_full_index_rows(backlog_index)
        prepended = BacklogManager._format_header_validation_errors(
            backlog_index, header_text_cells, separator_cells, real_row_count
        )
        # Prepend in reverse so the order in ``errors`` matches the order
        # we appended (header issue first, separator issue second, row
        # count third).
        for msg in reversed(prepended):
            errors.insert(0, msg)

    def _apply_fixes(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
    ) -> tuple[int, int]:
        """Auto-correct rule-10 (em-dash) and rule-11 (checkout_directory prefix) violations.

        For each task work unit:
        - Rule 10: replace every U+2014 character with ``--``.
        - Rule 11: strip the ``checkout_directory/`` prefix from Changes Manifest
          paths that carry it.

        After correcting a violation, appends an audit comment to the work
        unit's ``## Comments`` section:
        ``[VALIDATE_FIX] auto-corrected <rule>: <before> -> <after>``

        Args:
            rows: Parsed backlog rows as returned by ``_parse_backlog_rows``.
            workspace_root: Workspace root used to resolve work-unit file paths.

        Returns:
            ``(fix_count, files_fixed)`` -- total individual corrections applied
            and the count of distinct files that were modified.
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        fix_count = 0
        files_fixed: set[Path] = set()

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue

            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue

            content = wu_path.read_text(encoding="utf-8")
            original = content
            audit_lines: list[str] = []

            content, r10_count = self._fix_em_dash(content, audit_lines)
            fix_count += r10_count

            content, r11_count = self._fix_manifest_prefixes(content, audit_lines)
            fix_count += r11_count

            if content != original or audit_lines:
                content = self._append_fix_audit(content, timestamp, audit_lines)
                atomic_write_text(wu_path, content)
                files_fixed.add(wu_path)

        return fix_count, len(files_fixed)

    @staticmethod
    def _fix_em_dash(content: str, audit_lines: list[str]) -> tuple[str, int]:
        """Replace all U+2014 em-dash characters with '--'.

        Returns the (possibly modified) content and the count of replacements.
        Appends an audit line to ``audit_lines`` when at least one replacement is made.
        """
        if EM_DASH not in content:
            return content, 0
        count = content.count(EM_DASH)
        content = content.replace(EM_DASH, "--")
        audit_lines.append(
            f"[VALIDATE_FIX] auto-corrected rule-10: replaced {count} em-dash character(s) (U+2014) with '--'"
        )
        return content, count

    @staticmethod
    def _fix_manifest_prefixes(content: str, audit_lines: list[str]) -> tuple[str, int]:
        """Strip checkout_directory prefix from Changes Manifest path cells.

        Returns the (possibly modified) content and the count of path corrections.
        Appends audit lines to ``audit_lines`` for each corrected path.
        """
        from devbench.backlog.manifest import parse_manifest
        from devbench.config import RUNTIME_CONFIG

        repo = BacklogManager._extract_repo(content)
        if repo is None or repo not in RUNTIME_CONFIG.repos:
            return content, 0
        checkout_dir = RUNTIME_CONFIG.repos[repo].checkout_directory
        if not checkout_dir:
            return content, 0

        prefix = f"{checkout_dir.rstrip('/')}/"
        try:
            manifest_rows = parse_manifest(content)
        except Exception:
            return content, 0

        fix_count = 0
        for mrow in manifest_rows:
            if not mrow.file.startswith(prefix):
                continue
            stripped = mrow.file[len(prefix) :]
            old_cell = f"`{mrow.file}`"
            new_cell = f"`{stripped}`"
            if old_cell in content:
                content = content.replace(old_cell, new_cell, 1)
                audit_lines.append(
                    f"[VALIDATE_FIX] auto-corrected rule-11: "
                    f"stripped checkout_directory prefix from "
                    f"'{mrow.file}' -> '{stripped}'"
                )
                fix_count += 1

        return content, fix_count

    @staticmethod
    def _append_fix_audit(content: str, timestamp: str, audit_lines: list[str]) -> str:
        """Append the VALIDATE_FIX audit block to the work-unit file content.

        If the file already has a ``## Comments`` section, appends after the last
        line; otherwise creates the section first.
        """
        if not audit_lines:
            return content
        audit_block = "\n".join(f"[{timestamp}] [validate_fix] {line}" for line in audit_lines)
        if COMMENTS_SECTION_HEADER in content:
            return content.rstrip("\n") + "\n\n" + audit_block + "\n"
        return content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + audit_block + "\n"

    @staticmethod
    def _check_unique_ids(rows: list[tuple[str, str, str]], errors: list[str]) -> None:
        """Check 21: every work-unit ID appears exactly once in the index.

        A work unit written into two directory trees produces two index rows
        under one ID. Every other check still passes: each file exists, each
        file's status matches its own row, and neither file is orphaned,
        because both are indexed. The backlog is nonetheless incoherent. The
        two rows can disagree about status, and a dependency on that ID
        resolves against whichever row is reached first, so whether the
        dependency counts as satisfied is an ordering accident. Totals also
        double-count the unit.

        Typical shape: a task materialised once into a bare-``<id>`` tree and
        again into the ``<id>-<slug>`` tree, the two rows carrying different
        statuses (for example ``done`` and ``declined``), with the integrity
        check reporting success.

        Args:
            rows: Parsed ``(id, status, file_path)`` index rows.
            errors: Accumulator appended to on violation.
        """
        seen: dict[str, list[tuple[str, str]]] = {}
        for row_id, status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            # ``_parse_backlog_rows`` scans every pipe row in BACKLOG.md,
            # including the Status Summary table, whose rows repeat an ID
            # without a File Path cell. A row that names no file is not a Full
            # Work Unit Index row, so it cannot be a duplicate work unit. This
            # is the same test ``_check_files_and_statuses`` uses to recognise
            # real index rows.
            if not file_path_str:
                continue
            seen.setdefault(row_id, []).append((status, file_path_str))
        for row_id, occurrences in sorted(seen.items()):
            if len(occurrences) < 2:
                continue
            detail = "; ".join(f"status '{status}' at {path}" for status, path in occurrences)
            errors.append(
                f"{row_id}: duplicate work unit ID -- {len(occurrences)} index rows share this ID ({detail}). "
                "One ID must map to exactly one work unit file; remove or re-key the duplicate."
            )

    def _check_files_and_statuses(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> set[Path]:
        """Check file existence and status consistency (checks 1 and 2)."""
        indexed_files: set[Path] = set()
        for row_id, index_status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            indexed_files.add(wu_path.resolve())

            if not wu_path.exists():
                errors.append(f"{row_id}: work unit file missing -- expected {file_path_str}")
                continue

            content = wu_path.read_text(encoding="utf-8")
            m = BACKLOG_STATUS_RE.search(content)
            if m:
                file_status = m.group(1).strip().lower()
                if file_status != index_status:
                    errors.append(f"{row_id}: status mismatch -- index has '{index_status}', file has '{file_status}'")
            else:
                errors.append(f"{row_id}: work unit file missing '## Status:' line")
        return indexed_files

    def _check_task_rows_have_files(
        self,
        rows: list[tuple[str, str, str]],
        errors: list[str],
    ) -> None:
        """Check 26: every Task row names a file path (FR-20, db-279, OD-3=A).

        ``_check_files_and_statuses`` tolerates a file-less row of any type
        (its file-existence/status checks simply have nothing to check). That
        silent tolerance is correct for Epic/Feature/Story rows -- their
        bodies are scaffolding -- but wrong for Task rows: ``parse_index``
        (:mod:`devbench.backlog.parser`) hard-raises on a file-less Task row,
        so a Task row that passes ``validate()`` while missing a file would
        crash the orchestrator's ``set-status``/``start`` path. This rule
        closes that gap by flagging the same file-less Task row here, using
        the same :meth:`_is_task_id` idiom other rules use to scope Task-only
        checks. Status-Summary Epic rows and the ``**TOTAL**`` row are never
        Task IDs, so they are skipped without any special-casing.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not self._is_task_id(row_id):
                continue
            if file_path_str:
                continue
            errors.append(
                f"{row_id}: Task-level work unit has no file path in BACKLOG.md "
                "-- every Task row must name a materialised work-unit file"
            )

    def _check_orphans(self, workspace_root: Path, indexed_files: set[Path], errors: list[str]) -> None:
        """Check 3: no orphaned work unit files."""
        backlog_dir = workspace_root / BACKLOG_SUBDIR
        if backlog_dir.exists():
            for wu_file in backlog_dir.rglob("*.md"):
                if wu_file.resolve() not in indexed_files:
                    rel = wu_file.relative_to(backlog_dir)
                    errors.append(f"{rel}: orphaned work unit file not in BACKLOG.md")

    def _check_dependencies(self, backlog_index: Path, known_ids: set[str], errors: list[str]) -> None:
        """Check 4: all dependency IDs in the Full Work Unit Index reference real IDs.

        The Status Summary table has the same cell count as the Full Work
        Unit Index after the Declined column was added, so a naive pipe-row
        scan would mis-parse Summary rows as index rows. Track the current
        ``##`` section to scope dependency parsing to the index only.
        """
        content = backlog_index.read_text(encoding="utf-8")
        in_full_index = False
        for line in content.splitlines():
            # Section tracking: dependency parsing is valid only inside the
            # Full Work Unit Index section. Any other ## header closes it.
            stripped = line.strip()
            if stripped.startswith("## "):
                in_full_index = "Full Work Unit Index" in stripped
                continue
            if not in_full_index:
                continue
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) != BACKLOG_INDEX_CELL_COUNT:
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            dep_cell = cells[5]
            for raw_dep in dep_cell.split(","):
                dep_id = raw_dep.strip()
                if dep_id and dep_id.lower() not in DEPENDENCY_NONE_VALUES and dep_id not in known_ids:
                    errors.append(f"{row_id}: dependency '{dep_id}' not found in backlog index")

    def _check_dep_cycles(
        self,
        backlog_index: Path,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Issue #151 / FR-1 (db-253 Gap 1, db-299 Defect 2): detect dependency
        cycles via DFS-with-recursion-stack, over the UNION of every edge
        channel the orchestrator honors.

        The dependency/ownership graph is written to three channels: the index
        ``Dependencies`` column, each Task's own ``## Dependencies`` table, and
        its ``[BLOCKED_PENDING_PROPOSAL]`` markers. All three encode the same
        directed edge ``blocked_id -> blocker_id``, so unioning them cannot
        invent a false edge -- it can only surface a cycle the index-only view
        was blind to. See :meth:`_extend_dependency_graph_with_wu_edges` for the
        table/marker union; terminal (``done`` / ``declined``) Tasks contribute
        no table/marker edge there.

        Walks the resulting graph via DFS. A cycle exists when, during DFS, we
        encounter a node that is currently in the recursion stack (the "gray"
        set). Self-edges and chains of any length (4-node, N-node) are
        detected because the recursion-stack membership check is the unique
        cycle witness.

        Reports one error per cycle, naming the participating node IDs in
        traversal order so the operator can spot the offending chain. Cycle
        reporting is normalised: each cycle is rotated to start at its
        lexicographically smallest ID and reported once even when the DFS
        encounters it from multiple roots.
        """
        graph = self._build_dependency_graph(backlog_index)
        self._extend_dependency_graph_with_wu_edges(graph, rows, workspace_root)
        if not graph:
            return

        # DFS color tracking: 0 = white (unvisited), 1 = gray (on the
        # recursion stack -- a back-edge to a gray node is the cycle
        # witness), 2 = black (fully processed). ``stack`` records the
        # current DFS chain so we can extract the cycle nodes when a
        # back-edge fires.
        color: dict[str, int] = dict.fromkeys(graph, 0)
        stack: list[str] = []
        reported: set[tuple[str, ...]] = set()

        def visit(node: str) -> None:
            color[node] = 1
            stack.append(node)
            for nxt in graph.get(node, ()):
                if nxt not in color:
                    # Dependency on a non-indexed ID -- already reported by
                    # _check_dependencies; skip without traversal.
                    continue
                if color[nxt] == 1:
                    # Cycle: rotate so the lexicographically smallest node
                    # starts the chain, then dedupe.
                    cycle_start = stack.index(nxt)
                    cycle = tuple(stack[cycle_start:])
                    rotation = cycle.index(min(cycle))
                    normalised = cycle[rotation:] + cycle[:rotation]
                    if normalised not in reported:
                        reported.add(normalised)
                        chain = " -> ".join([*normalised, normalised[0]])
                        errors.append(f"dependency cycle detected: {chain}")
                    continue
                if color[nxt] == 0:
                    visit(nxt)
            stack.pop()
            color[node] = 2

        for node in sorted(graph):
            if color.get(node) == 0:
                visit(node)

    def _build_dependency_graph(self, backlog_index: Path) -> dict[str, list[str]]:
        """Parse the Full Work Unit Index into a ``{id: [dep_id, ...]}`` map.

        Mirrors the parsing scope used by :meth:`_check_dependencies`. Only
        rows inside the ``## Full Work Unit Index`` section are considered;
        Status Summary rows have the same cell count and would otherwise
        pollute the graph.
        """
        graph: dict[str, list[str]] = {}
        if not backlog_index.exists():
            return graph
        content = backlog_index.read_text(encoding="utf-8")
        in_full_index = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                in_full_index = "Full Work Unit Index" in stripped
                continue
            if not in_full_index or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) != BACKLOG_INDEX_CELL_COUNT:
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            dep_cell = cells[5]
            deps: list[str] = []
            for raw_dep in dep_cell.split(","):
                dep_id = raw_dep.strip()
                if not dep_id or dep_id.lower() in DEPENDENCY_NONE_VALUES:
                    continue
                deps.append(dep_id)
            graph[row_id] = deps
        return graph

    def _iter_non_terminal_task_files(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
    ) -> Iterator[tuple[str, Path]]:
        """Yield ``(row_id, wu_path)`` for every real, non-terminal, on-disk Task row.

        Shared eligibility filter reused by :meth:`_extend_dependency_graph_with_wu_edges`
        (FR-1) and :meth:`_check_dangling_markers` (FR-7): both read a Task's own
        ``## Dependencies`` table / ``[BLOCKED_PENDING_PROPOSAL]`` markers only
        when the row is a genuine Task ID with a resolvable file, and only when
        the Task is non-terminal (``done`` / ``declined`` Tasks are closed and
        must not contribute stale edges or resurrect stale marker complaints).

        Args:
            rows: ``(row_id, status, file_path)`` tuples from
                :meth:`_parse_backlog_rows`.
            workspace_root: Workspace root used to resolve ``file_path``.

        Yields:
            ``(row_id, wu_path)`` pairs for eligible rows, in ``rows`` order.
        """
        for row_id, status, file_path in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not self._is_task_id(row_id) or not file_path:
                continue
            if status in _TERMINAL_CHILD_STATUSES:
                continue
            wu_path = workspace_root / file_path
            if not wu_path.exists():
                continue
            yield row_id, wu_path

    def _extend_dependency_graph_with_wu_edges(
        self,
        graph: dict[str, list[str]],
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
    ) -> None:
        """FR-1: union in each non-terminal Task's ``## Dependencies`` table
        and ``[BLOCKED_PENDING_PROPOSAL]`` marker edges, in place.

        The index ``Dependencies`` column (seeded by :meth:`_build_dependency_graph`)
        is only one of three channels the orchestrator honors as a
        ``blocked_id -> blocker_id`` edge; the other two are each non-terminal
        Task's own ``## Dependencies`` table (:meth:`_extract_dep_ids`) and its
        ``[BLOCKED_PENDING_PROPOSAL]`` markers (:meth:`_extract_pending_proposal_markers`).
        Every channel encodes the same edge direction, so the union cannot
        invent a cycle that none of the three channels individually encodes.

        Terminal (``done`` / ``declined``) Tasks are skipped (see
        :meth:`_iter_non_terminal_task_files`): a closed Task cannot deadlock
        the orchestrator, and reading its table/markers would risk
        resurrecting a historical cycle from a stale marker that was never
        cleaned up after the Task closed.

        Args:
            graph: The ``{id: [dep_id, ...]}`` adjacency map, seeded from the
                index and mutated in place with the additional edges.
            rows: ``(row_id, status, file_path)`` tuples from
                :meth:`_parse_backlog_rows`.
            workspace_root: Workspace root used to resolve ``file_path``.
        """
        for row_id, wu_path in self._iter_non_terminal_task_files(rows, workspace_root):
            content = wu_path.read_text(encoding="utf-8")
            extra_deps = self._extract_dep_ids(content) | self._extract_pending_proposal_markers(wu_path)
            if not extra_deps:
                continue
            existing = graph.setdefault(row_id, [])
            for dep_id in sorted(extra_deps):
                if dep_id not in existing:
                    existing.append(dep_id)

    def _check_dangling_markers(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        known_ids: set[str],
        errors: list[str],
    ) -> None:
        """FR-7 (db-253 Gap 2b): a well-formed marker must reference a known WU-ID.

        A ``[BLOCKED_PENDING_PROPOSAL]`` marker pointing at a WU-ID absent from
        the index previously survived :meth:`validate` silently and only
        tripped ``reconcile-cascade`` later, when the cascade tried (and
        failed) to resolve the reference. This check surfaces the same
        condition at validate time so the operator can fix it before it blocks
        the cascade.

        Terminal (``done`` / ``declined``) Tasks are skipped (see
        :meth:`_iter_non_terminal_task_files`). A missing work-unit file
        contributes no error here -- it is already reported by
        :meth:`_check_files_and_statuses`.

        Args:
            rows: ``(row_id, status, file_path)`` tuples from
                :meth:`_parse_backlog_rows`.
            workspace_root: Workspace root used to resolve ``file_path``.
            known_ids: Every row ID present in the backlog index.
            errors: Error accumulator; a verbatim ERROR string is appended per
                dangling marker.
        """
        for row_id, wu_path in self._iter_non_terminal_task_files(rows, workspace_root):
            for marker_id in sorted(self._extract_pending_proposal_markers(wu_path)):
                if marker_id not in known_ids:
                    errors.append(
                        f"work unit {row_id}: [BLOCKED_PENDING_PROPOSAL] marker references "
                        f"unknown task '{marker_id}' -- the referenced task is not in the "
                        f"index; remove the marker or fix the reference (blocks reconcile-cascade)."
                    )

    def log_to_traceability_matrix(self, matrix_path: Path, spec_ref: str, test_ref: str) -> None:
        """Append an entry to the traceability matrix.

        Creates the file with a header row if it does not exist.

        Args:
            matrix_path: Path to the traceability matrix markdown file.
            spec_ref: Specification reference (e.g. ``AC-FUNC-001``).
            test_ref: Test reference (e.g. ``test_user_creation``).
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

        if not matrix_path.exists():
            header = TRACEABILITY_MATRIX_HEADER
            matrix_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(matrix_path, header)
            self.logger.info("Created traceability matrix at %s", matrix_path)

        row = f"| {spec_ref} | {test_ref} | {timestamp} |\n"
        with matrix_path.open("a", encoding="utf-8") as fh:
            fh.write(row)

        self.logger.info("Logged traceability: %s -> %s", spec_ref, test_ref)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Target section headings whose checkboxes are ticked on done.
    _TICK_SECTIONS: frozenset[str] = frozenset({"Acceptance Criteria", "Definition of Done"})

    def _tick_completion_checkboxes(self, work_unit_path: Path) -> None:
        """Rewrite unchecked / legacy-ticked AC and DoD checkboxes to checked + U+2705.

        Walks the work-unit file line by line, tracking the current ``## ``
        section.  Inside ``## Acceptance Criteria`` and ``## Definition of Done``
        only, rewrites:

        - ``- [ ] <content>``          -> ``- [x] <content> \u2705``
        - ``- [x] <content>`` (no emoji) -> ``- [x] <content> \u2705``
        - Already ``- [x] <content> \u2705`` lines -> no-op.

        Lines outside the two target sections are never modified.  The file is
        written back **only** when at least one line changed (preserving mtime
        when the file is already fully ticked).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
        """
        green_check = "\u2705"
        content = work_unit_path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        in_target_section = False
        new_lines: list[str] = []
        changed = False

        for line in lines:
            stripped = line.rstrip("\n")
            # Track section changes on ``## `` headings
            if stripped.startswith("## "):
                section_title = stripped[3:].strip()
                in_target_section = section_title in self._TICK_SECTIONS
                new_lines.append(line)
                continue

            if in_target_section:
                # Match ``- [ ] ...`` (unchecked)
                if stripped.startswith("- [ ] "):
                    body = stripped[6:]
                    new_line = f"- [x] {body} {green_check}\n"
                    new_lines.append(new_line)
                    changed = True
                    continue
                # Match ``- [x] ...`` without a trailing green-check (legacy ticked)
                if stripped.startswith("- [x] ") and not stripped.endswith(green_check):
                    body = stripped[6:]
                    new_line = f"- [x] {body} {green_check}\n"
                    new_lines.append(new_line)
                    changed = True
                    continue

            new_lines.append(line)

        if changed:
            atomic_write_text(work_unit_path, "".join(new_lines))

    def _set_status(
        self,
        work_unit_path: Path,
        backlog_index: Path,
        unit_id: str,
        new_status: str,
        session_name: str | None = None,
    ) -> None:
        """Private workhorse: write status to both files with no gate checks.

        All public transition methods (``force_status``, ``mark_done``,
        ``mark_blocked``) and internal rollup code call this method so that
        every write goes through a single code path.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            new_status: Status string (CLI form or title-case).
            session_name: Optional named-session name from
                ``DEVBENCH_SESSION_NAME``.  When provided and the target
                status is ``in-progress``, the ``[WU_CLAIMED]`` audit comment
                is extended with ``session=<name>`` per spec 4.4.7 /
                AC-192-6.
        """
        canonical = VALID_STATUSES.get(new_status.lower())
        if canonical is None:
            raise ValueError(f"Invalid status '{new_status}'. Valid statuses: {', '.join(sorted(VALID_STATUSES))}")

        self._update_status(work_unit_path, canonical)
        self._update_backlog_index(backlog_index, unit_id, canonical)
        self._update_status_summary(backlog_index)
        self.logger.info(
            "Set %s to '%s' in both work-unit file and BACKLOG.md",
            unit_id,
            canonical,
        )

        # Issue #185 / spec 4.4.2 / AC-192-5: every transition into
        # ``in-progress`` writes a ``[WU_CLAIMED]`` audit-comment row.
        # Issue #192 / spec 4.4.7 / AC-192-6: when a named session is active
        # (``session_name`` is provided), the comment is extended with
        # ``session=<name>`` so the audit trail records which session
        # performed the claim.
        # Skips Stories / Features / Epics whose status is auto-rolled from
        # children (no human ever claims those directly).
        if canonical == STATUS_IN_PROGRESS and "-T" in unit_id:
            claim_body = f"[WU_CLAIMED] Set {unit_id} to 'in-progress'"
            if session_name:
                claim_body = f"{claim_body} session={session_name}"
            self._append_agent_comment(
                work_unit_path,
                "orchestrator",
                claim_body,
            )

        if canonical == STATUS_DONE:
            self._tick_completion_checkboxes(work_unit_path)

        # Issue #162 Phase 2 (ADR-17): every task-state transition lands
        # in a per-task aggregate JSON at
        # ``<workspace>/.devbench/window-stats/<task-id>.json`` so the
        # reporter can read aggregates instead of re-scanning the log.
        # Single hook point: every public transition method routes
        # through ``_set_status``, so this one call covers all of them.
        # Stories / Features / Epics are skipped (their state is auto-
        # rolled from children; window-stats only tracks tasks).
        if "-T" in unit_id:
            from datetime import UTC, datetime

            from devbench.reporting.window_stats import update_aggregate

            workspace_root = backlog_index.parent
            update_aggregate(workspace_root, unit_id, canonical, datetime.now(UTC))

        if canonical in _TERMINAL_CHILD_STATUSES:
            # Issue #147: every terminal transition (``done`` AND ``declined``)
            # fires the auto-requeue cascade. Previously only ``mark_done``
            # routed through here; ``mark_declined`` / ``force_status declined``
            # silently skipped the scan, leaving downstream blocked tasks
            # marooned. The idempotency guard prevents a redundant scan when
            # the same target is set to the same terminal status twice in
            # one process lifetime (e.g. via repeated ``set-status`` calls).
            cascade_key = (str(backlog_index), unit_id)
            if cascade_key not in self._cascade_fired_for:
                self._cascade_fired_for.add(cascade_key)
                # Auto-requeue reverse-dependents first so the rollup check
                # that follows sees any freshly-unblocked children as
                # non-terminal and correctly declines to promote the parent
                # to done.
                self._auto_requeue_marker_dependents(backlog_index, unit_id)
                # Issue #208 follow-up: the marker cascade only covers tasks
                # carrying a ``[BLOCKED_PENDING_PROPOSAL]`` marker. Tasks that
                # landed in ``blocked`` via ``cmd_sync_blocked`` (regular
                # Dependencies table, no marker) were marooned. The regular-dep
                # cascade closes that gap.
                self._auto_requeue_regular_dep_dependents(backlog_index, unit_id)
            # Issue #332: the rollup must run for every terminal transition
            # (``done`` AND ``declined``), matching the #147 fix applied to
            # the requeue cascade two calls above. A story whose last open
            # child is Declined (not Done) previously never triggered this
            # call, stranding the story -- and its feature/epic ancestors --
            # in a non-terminal status forever. Runs AFTER both requeue
            # calls: a child freshly unblocked by the requeue must be seen
            # as non-terminal by ``_all_children_done`` so the rollup
            # correctly declines to promote the parent.
            self._rollup_parent_status(backlog_index, unit_id)

    def _last_round_all_passed(self, work_unit_path: Path) -> bool:
        """Check whether the most recent review round had all required judges pass.

        Reads the work-unit file's comment history in reverse. Collects
        ``[REVIEW_PASS]`` entries per judge; stops and resets if a
        ``[REVIEW_REJECTED]`` line is encountered (prior round boundary).

        Returns:
            True if every judge in ``ALL_REQUIRED_JUDGE_NAMES`` has a ``[REVIEW_PASS]``
            entry in the most recent round; False otherwise.
        """
        content = work_unit_path.read_text(encoding="utf-8")
        passed: set[str] = set()
        for line in reversed(content.splitlines()):
            if "[REVIEW_REJECTED]" in line:
                break  # everything before this belongs to a prior round
            for judge in ALL_REQUIRED_JUDGE_NAMES:
                if f"[judge/{judge}]" in line and "[REVIEW_PASS]" in line:
                    passed.add(judge)
        return passed >= ALL_REQUIRED_JUDGE_NAMES

    def _rollup_parent_status(self, backlog_index: Path, unit_id: str) -> None:
        """If all children of the parent unit are Done, mark the parent Done too.

        Derives the parent ID by removing the last segment (e.g. E0-F1-S1-T1 → E0-F1-S1).
        Calls ``_set_status`` directly (bypassing the done-gate -- parent units are
        structurally done when all children are done, no judge review required).
        Cascades upward by recursing through ``_set_status`` → ``_rollup_parent_status``.
        """
        parts = unit_id.rsplit("-", 1)
        if len(parts) < 2:
            return

        parent_id = parts[0]
        rows = self._parse_backlog_rows(backlog_index)

        if not self._all_children_done(rows, parent_id):
            return

        parent_file = self._find_work_unit_file(rows, parent_id, backlog_index.parent)
        if parent_file is None:
            self.logger.warning("Could not find file for parent %s -- skipping rollup", parent_id)
            return

        self.logger.info("All children of %s are done -- rolling up status", parent_id)
        # _set_status: atomic write to both files; cascades via _rollup_parent_status.
        self._set_status(parent_file, backlog_index, parent_id, STATUS_DONE)

        # Write audit comment to parent work unit
        timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
        rollup_comment = COMMENT_AGENT_TEMPLATE.format(
            timestamp=timestamp,
            name="orchestrator",
            message="Auto-rolled to done -- all children completed",
        )
        content = parent_file.read_text(encoding="utf-8")
        if COMMENTS_SECTION_HEADER in content:
            content = content.rstrip("\n") + "\n\n" + rollup_comment
        else:
            content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + rollup_comment
        atomic_write_text(parent_file, content)

    def _extract_pending_proposal_markers(self, work_unit_path: Path) -> set[str]:
        """Return the set of task IDs flagged by ``[BLOCKED_PENDING_PROPOSAL]`` markers.

        Scans only the ``## Comments`` body of ``work_unit_path`` so marker
        text quoted in Description, Approach, or other narrative sections
        cannot trigger the auto-requeue cascade. Returns an empty set when
        the file is missing, has no Comments section, or carries no markers.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.

        Returns:
            Set of promoted-dependency task IDs that the source task is
            currently waiting on.
        """
        if not work_unit_path.exists():
            return set()
        content = work_unit_path.read_text(encoding="utf-8")
        sections = self._extract_sections(content)
        comments = sections.get("Comments", "")
        return set(_BLOCKED_PENDING_PROPOSAL_RE.findall(comments))

    def _auto_requeue_marker_dependents(self, backlog_index: Path, newly_done_id: str) -> None:
        """Auto-requeue any blocked task whose promoted proposals are now all terminal.

        Symmetric counterpart to :meth:`_rollup_parent_status`: both fire
        from ``_set_status`` when a task transitions to ``done``; the rollup
        cascades upward (child done -> parent done), this cascade runs
        sideways (dep done -> blocked dependent requeued).

        Narrow trigger. A blocked candidate is auto-requeued only when ALL
        of the following hold:

        1. Its status is ``blocked`` (non-blocked candidates are skipped).
        2. The just-completed task (``newly_done_id``) appears EITHER in the
           candidate's declared Dependencies table OR as a
           ``[BLOCKED_PENDING_PROPOSAL]`` marker ID in the Comments section.
           Issue #200 / AC-200-2: the marker-only path was previously missing,
           causing tasks whose only reference to the promoted dep was via a
           marker (no Dependencies-table row) to stay blocked indefinitely.
        3. The candidate's Comments section carries at least one
           ``[BLOCKED_PENDING_PROPOSAL]`` marker.
        4. Every task ID named by those markers is in a terminal state
           (``done`` or ``declined``). Unknown IDs (e.g. rejected proposals
           no longer in the index) count as non-terminal so the cascade
           stays conservative.

        Tasks that block for other reasons (review failure, git-ops error,
        operator intervention) never carry the marker and stay manual. The
        transition uses :meth:`force_status` and writes an ``[AUTO_UNBLOCKED]``
        audit comment naming every marker ID.

        The scan operates directly on ``_parse_backlog_rows`` output (the
        same lightweight tuple-based view ``_rollup_parent_status`` uses)
        rather than the full ``BacklogParser.parse_index`` object model,
        because the latter validates every file in the index exists and a
        single missing sibling file would otherwise abort the whole scan.

        Args:
            backlog_index: Path to ``BACKLOG.md``.
            newly_done_id: The task that just transitioned to ``done``.
        """
        try:
            rows = self._parse_backlog_rows(backlog_index)
        except FileNotFoundError as exc:
            self.logger.warning("Auto-requeue scan skipped -- %s", exc)
            return

        terminal_ids = {row_id for row_id, status, _ in rows if row_id and status in _TERMINAL_CHILD_STATUSES}
        workspace = backlog_index.parent

        for row_id, status, file_path in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if status != STATUS_BLOCKED:
                continue
            if not file_path:
                continue
            candidate_file = workspace / file_path
            if not candidate_file.exists():
                self.logger.warning(
                    "Auto-requeue scan: candidate file missing for %s at %s -- skipping",
                    row_id,
                    candidate_file,
                )
                continue

            # Issue #200 / AC-200-2: the trigger condition is relaxed to accept
            # ``newly_done_id`` appearing EITHER in the declared Dependencies
            # table OR as a ``[BLOCKED_PENDING_PROPOSAL]`` marker in the
            # Comments section.  Previously only the dep-table path was checked,
            # which silently skipped tasks where task-factory wired the dep via
            # a marker-only reference (no Dependencies-table row), leaving them
            # stuck in ``blocked`` after the marker target reached ``done``.
            content = candidate_file.read_text(encoding="utf-8")
            marker_ids = self._extract_pending_proposal_markers(candidate_file)
            if not marker_ids:
                continue
            referenced_via_dep = newly_done_id in self._parse_candidate_dependencies(content)
            referenced_via_marker = newly_done_id in marker_ids
            if not referenced_via_dep and not referenced_via_marker:
                continue
            if not marker_ids.issubset(terminal_ids):
                continue

            sorted_markers = sorted(marker_ids)
            self.logger.info(
                "Auto-requeuing %s -- promoted proposals %s are terminal",
                row_id,
                sorted_markers,
            )
            self.force_status(candidate_file, backlog_index, row_id, STATUS_IN_QUEUE)
            # Issue #153: emit ``[CASCADE_RESOLVED]`` so the status-detail
            # panel renderer can supersede the earlier ``[BLOCKED]`` audit
            # row. ``[AUTO_UNBLOCKED]`` is retained alongside for backward
            # compatibility with operator tooling that already greps for it.
            self._append_agent_comment(
                candidate_file,
                "backlog_manager",
                f"[AUTO_UNBLOCKED] [CASCADE_RESOLVED] promoted proposals {sorted_markers} are terminal; re-queuing",
            )

    def _auto_requeue_regular_dep_dependents(self, backlog_index: Path, newly_done_id: str) -> None:
        """Auto-requeue blocked tasks whose regular Dependencies-table deps are now all terminal.

        Issue #208 (companion to issue #147). The marker cascade in
        :meth:`_auto_requeue_marker_dependents` only handles blocked tasks
        carrying a ``[BLOCKED_PENDING_PROPOSAL]`` marker. Tasks that landed
        in ``blocked`` via ``cmd_sync_blocked`` (regular Dependencies-table
        deps unsatisfied, no marker) were marooned: even after the dep
        transitioned to ``done``, the task stayed blocked indefinitely. An
        operator had to run ``devbench sync-blocked`` or
        ``devbench reconcile-cascade`` manually.

        Narrow trigger. A blocked candidate is auto-requeued only when ALL
        of the following hold:

        1. Its status is ``blocked``.
        2. It carries NO ``[BLOCKED_PENDING_PROPOSAL]`` marker (those are
           owned by the marker cascade -- double-handling would produce
           conflicting audit comments).
        3. The just-completed task (``newly_done_id``) appears in its
           declared Dependencies table.
        4. Every other entry in its Dependencies table is in a terminal
           state (``done`` or ``declined``).

        The transition uses :meth:`force_status` and writes a single
        ``[UNBLOCKED] [CASCADE_RESOLVED]`` audit comment naming the dep
        whose completion triggered the cascade. The supersession audit
        shape mirrors the marker cascade and ``cmd_sync_blocked`` so the
        status-panel renderer treats all three uniformly (#153).

        Args:
            backlog_index: Path to ``BACKLOG.md``.
            newly_done_id: The task that just transitioned to a terminal state.
        """
        try:
            rows = self._parse_backlog_rows(backlog_index)
        except FileNotFoundError as exc:
            self.logger.warning("Regular-dep auto-requeue scan skipped -- %s", exc)
            return

        terminal_ids = {row_id for row_id, status, _ in rows if row_id and status in _TERMINAL_CHILD_STATUSES}
        workspace = backlog_index.parent

        for row_id, status, file_path in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if status != STATUS_BLOCKED:
                continue
            if not file_path:
                continue
            candidate_file = workspace / file_path
            if not candidate_file.exists():
                self.logger.warning(
                    "Regular-dep auto-requeue scan: candidate file missing for %s at %s -- skipping",
                    row_id,
                    candidate_file,
                )
                continue

            # Marker cascade owns marker-bearing candidates.
            if self._extract_pending_proposal_markers(candidate_file):
                continue

            content = candidate_file.read_text(encoding="utf-8")
            declared_deps = self._parse_candidate_dependencies(content)
            if newly_done_id not in declared_deps:
                continue
            if not set(declared_deps).issubset(terminal_ids):
                continue

            self.logger.info(
                "Auto-requeuing %s -- regular dependency %r now terminal",
                row_id,
                newly_done_id,
            )
            self.force_status(candidate_file, backlog_index, row_id, STATUS_IN_QUEUE)
            self._append_agent_comment(
                candidate_file,
                "backlog_manager",
                f"[UNBLOCKED] [CASCADE_RESOLVED] dependency {newly_done_id!r} now terminal; re-queuing",
            )

    @staticmethod
    def _parse_candidate_dependencies(content: str) -> list[str]:
        """Extract declared dependency task IDs from a work-unit file's content.

        Scoped to the ``## Dependencies`` section body. Header rows
        (``| ID | Title | Status |``), separator rows (``|----|...``), and
        the ``| none | | |`` sentinel are all filtered out. Mirrors the
        parser's ``_parse_dependency_table`` but works on already-loaded
        content and does not require instantiating a full parser.
        """
        deps: list[str] = []
        in_deps_section = False
        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("## "):
                in_deps_section = stripped == "## Dependencies"
                continue
            if not in_deps_section or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            if len(cells) < 4:
                continue
            first = cells[1]
            if not first:
                continue
            lowered = first.lower()
            if lowered == "id" or lowered in DEPENDENCY_NONE_VALUES or first.startswith("-"):
                continue
            deps.append(first)
        return deps

    def _parse_backlog_rows(self, backlog_index: Path) -> list[tuple[str, str, str]]:
        """Parse BACKLOG.md table rows into (id, status, file_path) tuples."""
        recognized = {v.lower() for v in TABLE_STATUS_VALUES} | {v.lower() for v in VALID_STATUSES}
        content = backlog_index.read_text(encoding="utf-8")
        rows: list[tuple[str, str, str]] = []

        for line in content.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 5:
                continue

            row_id = cells[1]
            status = ""
            file_path = ""
            for cell in cells:
                if cell.lower() in recognized:
                    status = cell.lower()
                stripped = cell.strip("`")
                if stripped.startswith("backlog/") and stripped.endswith(".md"):
                    file_path = stripped
            rows.append((row_id, status, file_path))

        return rows

    def _all_children_done(self, rows: list[tuple[str, str, str]], parent_id: str) -> bool:
        """Check if the parent exists, is not already Done, and all direct children are in a terminal state.

        A child is "terminal" when its status is Done OR Declined. Declined
        work has been intentionally taken off the table; it should not block
        the parent from rolling to Done. Done + Declined together mean every
        child has been resolved one way or the other.
        """
        parent_depth = parent_id.count("-")
        parent_found = False
        has_children = False

        for row_id, status, _ in rows:
            if row_id == parent_id:
                parent_found = True
                if status == STATUS_DONE:
                    return False  # Already done
                continue

            if row_id.startswith(parent_id + "-") and row_id.count("-") == parent_depth + 1:
                has_children = True
                if status not in _TERMINAL_CHILD_STATUSES:
                    return False

        return parent_found and has_children

    def _find_work_unit_file(
        self,
        rows: list[tuple[str, str, str]],
        unit_id: str,
        workspace_root: Path,
    ) -> Path | None:
        """Find the work unit file path for a given ID."""
        for row_id, _, file_path in rows:
            if row_id == unit_id and file_path:
                candidate = workspace_root / file_path
                if candidate.exists():
                    return candidate
        return None

    def _update_status(self, work_unit_path: Path, new_status: str) -> None:
        """Replace the ``## Status:`` value in a work-unit file."""
        if not work_unit_path.exists():
            raise FileNotFoundError(f"Work-unit file not found: {work_unit_path}")

        content = work_unit_path.read_text(encoding="utf-8")

        if not STATUS_LINE_RE.search(content):
            raise ValueError(f"Could not find '## Status: ...' line in {work_unit_path}")

        updated = STATUS_LINE_RE.sub(rf"\g<1>{new_status}", content, count=1)
        atomic_write_text(work_unit_path, updated)

    def _update_backlog_index(self, backlog_index: Path, unit_id: str, new_status: str) -> None:
        """Update the status column for a work unit in the BACKLOG.md table."""
        if not backlog_index.exists():
            raise FileNotFoundError(f"Backlog index not found: {backlog_index}")

        # Build a case-insensitive lookup of recognized statuses
        recognized = {v.lower() for v in TABLE_STATUS_VALUES} | {v.lower() for v in VALID_STATUSES}

        content = backlog_index.read_text(encoding="utf-8")
        lines = content.splitlines()
        updated = False

        for i, line in enumerate(lines):
            if not line.strip().startswith("|"):
                continue
            cells = line.split("|")
            # Match the ID cell exactly (cells[0] is empty before first |)
            row_id = cells[1].strip() if len(cells) > 1 else ""
            if row_id != unit_id:
                continue
            for j, cell in enumerate(cells):
                if cell.strip().lower() in recognized:
                    cells[j] = f" {new_status} "
                    lines[i] = "|".join(cells)
                    updated = True
                    break
            if updated:
                break

        if not updated:
            raise ValueError(f"Could not find unit '{unit_id}' with a recognized status in {backlog_index}")

        atomic_write_text(backlog_index, "\n".join(lines) + "\n")
        self.logger.info("Updated %s status to '%s' in %s", unit_id, new_status, backlog_index.name)

    def _append_comment(self, work_unit_path: Path, action: str, message: str) -> None:
        """Append a comment entry to the Comments section of a work-unit file."""
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = COMMENT_ENTRY_TEMPLATE.format(
            timestamp=timestamp,
            agent_id="backlog_manager",
            action=action,
            message=message,
        )

        content = work_unit_path.read_text(encoding="utf-8")

        comments_header = COMMENTS_SECTION_HEADER
        if comments_header in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + comments_header + "\n\n" + entry

        atomic_write_text(work_unit_path, content)

    def _append_agent_comment(self, work_unit_path: Path, agent_name: str, message: str) -> None:
        """Append an agent comment using COMMENT_AGENT_TEMPLATE format.

        Writes: ``[YYYY-MM-DD HH:MM UTC] [agent/<agent_name>] <message>``

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            agent_name: Agent name (e.g. ``git_ops``, ``orchestrator``).
            message: Message to append (may contain token and detail).
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = COMMENT_AGENT_TEMPLATE.format(
            timestamp=timestamp,
            name=agent_name,
            message=message,
        )

        content = work_unit_path.read_text(encoding="utf-8")

        if COMMENTS_SECTION_HEADER in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry

        atomic_write_text(work_unit_path, content)

    @staticmethod
    def _find_next_section_index(lines: list[str], start: int) -> int:
        """Return the line index of the next ``## `` heading after ``start``.

        Scans forward from ``start + 1`` (exclusive of the TDD Cycle Log heading
        itself) and returns the index of the first line whose first three
        characters are ``## `` (case-sensitive).  Returns ``-1`` when no such
        line exists.

        Args:
            lines: File content split into lines (no trailing newline on each).
            start: Index of the ``## TDD Cycle Log`` heading line.

        Returns:
            Index of the next ``## `` heading, or ``-1`` if none found.
        """
        for idx in range(start + 1, len(lines)):
            if lines[idx].startswith("## "):
                return idx
        return -1

    def _append_tdd_entry(self, work_unit_path: Path, phase: str, message: str) -> None:
        """Append a TDD phase entry to the TDD Cycle Log section of a work-unit file.

        Writes: ``- [<PHASE>] <ISO-8601 timestamp> -- <message>``

        The entry is inserted immediately before the next ``## `` heading after
        ``## TDD Cycle Log``.  When ``## TDD Cycle Log`` is the last section in
        the file (no following ``## `` heading exists), the entry is appended at
        EOF -- preserving the original edge-case behaviour.

        Exactly one blank line separates the new entry from the preceding
        content, and exactly one blank line separates the new entry from the
        following ``## `` heading (when one exists).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            phase: TDD phase -- any member of ``devbench.constants.VALID_TDD_PHASES``
                (``RED``, ``GREEN``, ``REFACTOR``, ``RED_OBSERVED``,
                ``GREEN_GREEN_OBSERVED``; caller must pass normalized
                uppercase value). This helper performs no phase validation
                itself: the agent-writable versus orchestrator-only boundary
                (``AGENT_WRITABLE_TDD_PHASES`` vs. ``RED_OBSERVED``/
                ``GREEN_GREEN_OBSERVED``) is enforced by the caller --
                ``cmd_log_tdd`` rejects phases outside
                ``AGENT_WRITABLE_TDD_PHASES`` before this method is ever reached.
            message: Description of the TDD phase outcome.

        Raises:
            ValueError: If the ``## TDD Cycle Log`` section does not exist in the file.
        """
        timestamp = datetime.now(tz=UTC).isoformat()
        entry = TDD_ENTRY_TEMPLATE.format(phase=phase, timestamp=timestamp, message=message)

        content = work_unit_path.read_text(encoding="utf-8")

        if TDD_CYCLE_LOG_SECTION_HEADER not in content:
            raise ValueError(
                f"'## TDD Cycle Log' section not found in {work_unit_path}. "
                "The section must already exist in the work unit file (it is defined in the task spec template)."
            )

        lines = content.splitlines()
        tdd_heading_idx = next(i for i, line in enumerate(lines) if line == TDD_CYCLE_LOG_SECTION_HEADER)
        next_section_idx = self._find_next_section_index(lines, tdd_heading_idx)

        if next_section_idx == -1:
            # ## TDD Cycle Log is the last section -- append to EOF.
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            # Insert before the next ## heading, preserving exactly one blank
            # line above the entry and exactly one blank line below it.
            before_next = lines[:next_section_idx]
            # Strip trailing blank lines from the block before the next heading.
            while before_next and before_next[-1].strip() == "":
                before_next.pop()
            new_lines = before_next + ["", entry.rstrip("\n"), "", lines[next_section_idx]]
            new_lines.extend(lines[next_section_idx + 1 :])
            content = "\n".join(new_lines) + "\n"

        atomic_write_text(work_unit_path, content)

    def _update_status_summary(self, backlog_index: Path) -> None:
        """Rewrite the Status Summary table section in BACKLOG.md.

        Reads all rows from the Full Work Unit Index, groups descendants by epic,
        counts statuses, and either inserts or replaces the ``## Status Summary``
        section immediately before ``## Full Work Unit Index``.

        An epic row is identified as any row whose ID matches the pattern
        ``E<digits>`` (no hyphen suffix). Counts are computed over all descendant
        rows -- rows whose ID starts with ``<epic-id>-``.
        """
        rows = self._parse_backlog_rows(backlog_index)
        epic_titles = self._parse_epic_titles(backlog_index)
        epic_counts = self._compute_epic_counts(rows)
        summary_block = self._build_summary_block(epic_counts, epic_titles)

        content = backlog_index.read_text(encoding="utf-8")

        # Remove any existing Status Summary section
        if STATUS_SUMMARY_SECTION_HEADER in content:
            content = self._strip_summary_section(content)

        # Insert before the Full Work Unit Index heading
        full_index_marker = "## Full Work Unit Index"
        if full_index_marker in content:
            content = content.replace(
                full_index_marker,
                summary_block + full_index_marker,
                1,
            )
        else:
            content = content.rstrip("\n") + "\n\n" + summary_block

        atomic_write_text(backlog_index, content)

    def _parse_epic_titles(self, backlog_index: Path) -> dict[str, str]:
        """Parse epic IDs to their titles from BACKLOG.md table rows.

        Returns a dict mapping epic_id -> title string.
        """
        titles: dict[str, str] = {}
        content = backlog_index.read_text(encoding="utf-8")

        for line in content.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 4:
                continue
            row_id = cells[1]
            if EPIC_ID_RE.match(row_id):
                titles[row_id] = cells[2]

        return titles

    def _compute_epic_counts(self, rows: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
        """Compute per-epic status counts from backlog rows.

        Returns a dict mapping epic_id -> {status: count} where status is one
        of ``done``, ``in-progress``, ``in-queue``, ``blocked``, ``declined``,
        ``draft``. Only descendant rows (those starting with ``<epic-id>-``)
        are counted; the epic row itself is excluded.
        """
        epic_order: list[str] = []
        for row_id, _, _ in rows:
            if row_id and EPIC_ID_RE.match(row_id):
                epic_order.append(row_id)

        counts: dict[str, dict[str, int]] = {
            epic_id: {
                STATUS_DONE: 0,
                STATUS_IN_PROGRESS: 0,
                STATUS_IN_QUEUE: 0,
                STATUS_BLOCKED: 0,
                STATUS_DECLINED: 0,
                STATUS_DRAFT: 0,
            }
            for epic_id in epic_order
        }

        for row_id, status, _ in rows:
            if not row_id or not status:
                continue
            for epic_id in epic_order:
                if row_id.startswith(epic_id + "-"):
                    canonical_status = status.lower()
                    if canonical_status in counts[epic_id]:
                        counts[epic_id][canonical_status] += 1
                    break

        return counts

    def _build_summary_block(
        self,
        epic_counts: dict[str, dict[str, int]],
        epic_titles: dict[str, str],
    ) -> str:
        """Build the full Status Summary section as a markdown string."""
        table_rows = ""
        for epic_id, c in epic_counts.items():
            title = epic_titles.get(epic_id, "")
            table_rows += (
                f"| {epic_id} | {title} | {c[STATUS_DONE]} | "
                f"{c[STATUS_IN_PROGRESS]} | {c[STATUS_IN_QUEUE]} | "
                f"{c[STATUS_BLOCKED]} | {c[STATUS_DECLINED]} | {c[STATUS_DRAFT]} |\n"
            )

        return STATUS_SUMMARY_SECTION_HEADER + "\n\n" + STATUS_SUMMARY_TABLE_HEADER + table_rows + "\n"

    def _strip_summary_section(self, content: str) -> str:
        """Remove the existing Status Summary section from BACKLOG.md content."""
        without_summary = STRIP_SUMMARY_RE.sub("", content)
        # Collapse any resulting triple+ blank lines to double blank lines
        return re.sub(r"\n{3,}", "\n\n", without_summary)

    def _check_status_summary(
        self,
        backlog_index: Path,
        rows: list[tuple[str, str, str]],
        errors: list[str],
    ) -> None:
        """Check 5: Status Summary table exists and counts match the index."""
        content = backlog_index.read_text(encoding="utf-8")

        if STATUS_SUMMARY_SECTION_HEADER not in content:
            errors.append(
                "Status Summary section missing from BACKLOG.md -- run 'devbench validate-backlog' to regenerate it"
            )
            return

        # Compute expected counts and parse the actual table
        epic_counts = self._compute_epic_counts(rows)
        actual_counts = self._parse_summary_table(content)

        for epic_id, expected in epic_counts.items():
            if epic_id not in actual_counts:
                errors.append(f"Status Summary missing epic row '{epic_id}'")
                continue
            actual = actual_counts[epic_id]
            for status_key in (
                STATUS_DONE,
                STATUS_IN_PROGRESS,
                STATUS_IN_QUEUE,
                STATUS_BLOCKED,
                STATUS_DECLINED,
                STATUS_DRAFT,
            ):
                if expected[status_key] != actual.get(status_key, -1):
                    errors.append(
                        f"Status Summary mismatch for {epic_id}: "
                        f"expected {status_key}={expected[status_key]}, "
                        f"got {actual.get(status_key, 'missing')}"
                    )

    def _parse_summary_table(self, content: str) -> dict[str, dict[str, int]]:
        """Parse the Status Summary table from BACKLOG.md content.

        Returns a dict mapping epic_id -> {status: count}.
        """
        result: dict[str, dict[str, int]] = {}

        in_summary = False
        for line in content.splitlines():
            if line.strip() == STATUS_SUMMARY_SECTION_HEADER:
                in_summary = True
                continue
            if in_summary and line.startswith("##"):
                break
            if not in_summary or not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            # Splitting "|" around a pipe-delimited row produces empty flank
            # cells, so a 6-data-column row yields 8 cells total, a
            # 7-data-column row (with the Declined column) yields 9, and an
            # 8-data-column row (with both Declined and Draft columns) yields
            # 10. Both older shapes are accepted for backward compatibility;
            # missing columns default to 0 until the backlog is regenerated.
            if len(cells) < 7:
                continue
            row_id = cells[1]
            if not EPIC_ID_RE.match(row_id):
                continue
            try:
                declined_count = int(cells[7]) if len(cells) >= 9 else 0
                draft_count = int(cells[8]) if len(cells) >= 10 else 0
                result[row_id] = {
                    STATUS_DONE: int(cells[3]),
                    STATUS_IN_PROGRESS: int(cells[4]),
                    STATUS_IN_QUEUE: int(cells[5]),
                    STATUS_BLOCKED: int(cells[6]),
                    STATUS_DECLINED: declined_count,
                    STATUS_DRAFT: draft_count,
                }
            except (ValueError, IndexError):
                continue

        return result

    def _check_task_content(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Checks 6-10: validate content quality for task-level work units.

        Only task files (IDs ending with -T{n}) are checked. Epic, Feature,
        and Story files are skipped because they do not require the same
        level of detail.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            if not self._is_task_id(row_id):
                continue

            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue  # Already reported by _check_files_and_statuses

            content = wu_path.read_text(encoding="utf-8")
            sections = self._extract_sections(content)

            # Check 6: non-empty Description
            if "Description" not in sections:
                errors.append(f"{row_id}: missing required '## Description' section")
            elif not sections["Description"].strip():
                errors.append(f"{row_id}: '## Description' section is empty")

            # Check 7: Acceptance Criteria with AC- items
            if "Acceptance Criteria" not in sections:
                errors.append(f"{row_id}: missing required '## Acceptance Criteria' section")
            elif "AC-" not in sections["Acceptance Criteria"]:
                errors.append(f"{row_id}: '## Acceptance Criteria' has no AC- items")

            # Check 8: Changes Manifest with entries
            if "Changes Manifest" not in sections:
                errors.append(f"{row_id}: missing required '## Changes Manifest' section")

            # Check 9: Definition of Done
            if "Definition of Done" not in sections:
                errors.append(f"{row_id}: missing required '## Definition of Done' section")

            # Check 10: no em-dash (U+2014)
            if EM_DASH in content:
                errors.append(f"{row_id}: contains em-dash character (U+2014) -- use double hyphen instead")

    def _check_manifest_path_prefixes(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 11: Changes Manifest paths must be repo-relative.

        For every task work unit whose target repo has ``checkout_directory``
        configured in ``backlog/config/devbench.yaml``, reject any manifest
        row whose path begins with ``<checkout_directory>/``. Such paths
        block at ``git-ops`` time because ``assert_staged_matches_manifest``
        (``src/devbench/backlog/manifest.py``) compares manifest entries
        against ``git diff --name-only`` output, which is always
        repo-relative.

        This is a state-based discriminator, not a silent-on-failure
        fallback: work units for repos without a configured
        ``checkout_directory`` are skipped because the check genuinely
        does not apply there (no prefix to compare against).
        """
        from devbench.backlog.manifest import parse_manifest
        from devbench.config import RUNTIME_CONFIG

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            if not self._is_task_id(row_id):
                continue

            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue  # already reported by _check_files_and_statuses

            content = wu_path.read_text(encoding="utf-8")
            repo = self._extract_repo(content)
            if repo is None or repo not in RUNTIME_CONFIG.repos:
                continue

            checkout_dir = RUNTIME_CONFIG.repos[repo].checkout_directory
            if not checkout_dir:
                continue

            prefix = f"{checkout_dir.rstrip('/')}/"
            for manifest_row in parse_manifest(content):
                if manifest_row.file.startswith(prefix):
                    errors.append(
                        f"{row_id}: Changes Manifest path {manifest_row.file!r} "
                        f"begins with checkout_directory prefix {prefix!r}. "
                        f"Paths must be repo-relative (drop the prefix); "
                        f"see docs/backlog-contract.md."
                    )

    # Language tier classification used by the source-test pair and
    # language-AC-alignment rules. Mirrors docs/acceptance-criteria-canonical.md
    # tier table. Production-source globs identify paths whose Python files
    # require a sibling test entry in the same Manifest per
    # docs/source-test-atomicity.md; the test-path globs are the matching
    # locations the rule searches for that sibling.
    _PYTHON_EXTS: ClassVar[tuple[str, ...]] = (".py",)
    _NON_PY_EXTS_TO_TIER: ClassVar[dict[str, str]] = {
        ".hcl": "HCL",
        ".tf": "HCL",
        ".tfvars": "HCL",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".json": "JSON",
        ".xml": "XML",
        ".toml": "TOML",
        ".md": "Markdown",
    }
    _PROD_SRC_PATTERNS: ClassVar[tuple[str, ...]] = (
        "src/",
        "infra/scripts/",
    )
    _PROD_SRC_NESTED_PATTERNS: ClassVar[tuple[str, ...]] = ("/src/",)
    # Extensions consumed by the ``chore`` task-type invariant that are not
    # already covered by ``_NON_PY_EXTS_TO_TIER`` (which is reused below for
    # the config/dependency tiers it already tracks -- HCL, YAML, JSON, XML,
    # TOML). Lockfiles and legacy setup/config formats have no dedicated
    # tier entry, so they are listed explicitly here.
    _CHORE_EXTRA_EXTS: ClassVar[tuple[str, ...]] = (".lock", ".cfg", ".ini", ".txt")
    _AC_FINAL_LANGUAGE_TIER_IDS: ClassVar[frozenset[str]] = frozenset(
        {
            "AC-FINAL-002",
            "AC-FINAL-003",
            "AC-FINAL-004",
            "AC-FINAL-005",
            "AC-FINAL-006",
            "AC-FINAL-008",
            "AC-FINAL-014",
        }
    )

    @classmethod
    def _classify_manifest_tier(cls, paths: list[str]) -> str:
        """Return the language tier for a Manifest's file paths.

        Returns ``"Python"`` if any path ends in ``.py``; otherwise the
        single non-Python tier matching all paths; otherwise ``"Mixed"``.
        Returns ``""`` if the path list is empty.
        """
        if not paths:
            return ""
        py_paths = [p for p in paths if any(p.lower().endswith(ext) for ext in cls._PYTHON_EXTS)]
        if py_paths:
            return "Python"
        tiers = set()
        for p in paths:
            lower = p.lower()
            for ext, tier in cls._NON_PY_EXTS_TO_TIER.items():
                if lower.endswith(ext):
                    tiers.add(tier)
                    break
        if len(tiers) == 1:
            return tiers.pop()
        if len(tiers) > 1:
            return "Mixed"
        return ""

    @classmethod
    def _is_test_source_path(cls, path: str) -> bool:
        """Return True if the path is a Python file located under a ``tests/`` dir.

        This is the single shared authority for "is this path a Python test
        file" used both by ``_is_production_source`` (Rule 14, source-test
        atomicity) and by the task-type taxonomy invariants (FR-4.1 /
        E4-F2-S1-T1, AC-47). Introducing a second, independent test-path
        classifier is prohibited -- both call sites must reuse this method
        so the production/test boundary can never drift out of sync.
        """
        if not any(path.lower().endswith(ext) for ext in cls._PYTHON_EXTS):
            return False
        return path.startswith("tests/") or "/tests/" in path

    @classmethod
    def _is_production_source(cls, path: str) -> bool:
        """Return True if the path looks like production Python source.

        Used by the source-test pair rule to distinguish source files (which
        require a matching test entry) from one-off scripts in
        configuration-only directories. Test files themselves (paths under
        any ``tests/`` segment, per ``_is_test_source_path``) are excluded --
        they are not production source even when their extension is ``.py``.
        """
        if not any(path.lower().endswith(ext) for ext in cls._PYTHON_EXTS):
            return False
        # Exclude test files
        if cls._is_test_source_path(path):
            return False
        # Exclude package marker files
        from pathlib import PurePosixPath

        if PurePosixPath(path).name == "__init__.py":
            return False
        return any(path.startswith(p) for p in cls._PROD_SRC_PATTERNS) or any(
            seg in path for seg in cls._PROD_SRC_NESTED_PATTERNS
        )

    @staticmethod
    def _is_documentation_path(path: str) -> bool:
        """Return True if the path is a documentation/markdown file.

        Used by the ``docs`` task-type invariant (FR-4.1). Deliberately
        extension-based only (``.md``) -- documentation-only tasks author
        markdown, never other file types.
        """
        return path.lower().endswith(".md")

    @classmethod
    def _is_chore_path(cls, path: str) -> bool:
        """Return True if the path is a dependency/config/lockfile entry.

        Used by the ``chore`` task-type invariant (FR-4.1). Reuses the
        existing config/dependency tiers already tracked by
        ``_NON_PY_EXTS_TO_TIER`` (HCL, YAML, JSON, XML, TOML) -- excluding
        Markdown, which the ``docs`` task type owns instead -- plus a small
        set of lockfile/legacy-config extensions with no dedicated tier
        entry.
        """
        lower = path.lower()
        if any(lower.endswith(ext) for ext in cls._CHORE_EXTRA_EXTS):
            return True
        return any(lower.endswith(ext) for ext, tier in cls._NON_PY_EXTS_TO_TIER.items() if tier != "Markdown")

    @staticmethod
    def _is_real_manifest_path(path: str) -> bool:
        """Return True if the Manifest entry is a real file path.

        Filters out placeholder strings like ``(none)``, ``(no file changes;
        ...)``, etc. that documentation-only or verification-only tasks use to
        indicate an empty Manifest. Also filters out sentinel values like
        ``<verification-only>``, ``<decision-only>``, and any
        ``<name>``-shaped variant -- see ``devbench.backlog.sentinels`` for
        the canonical allowlist + pattern + rationale.
        """
        from devbench.backlog.sentinels import is_sentinel_manifest_value

        if not path:
            return False
        stripped = path.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            return False
        return not is_sentinel_manifest_value(stripped)

    # HARD claimants collide at git-ops time whether or not the operator has
    # noticed yet -- ``in-progress`` is included (db-313, FR-3) because an
    # actively-executing claimant is just as real a collision risk as a
    # queued one. SOFT claimants are pre-lifecycle authoring states that
    # have not committed to an execution order yet; they are only folded
    # into the conflict count under ``strict=True`` (db-267, FR-4). Any
    # other status (``done``, ``declined``, ``in-review``, or an unrecognised
    # value) belongs to neither set and is excluded from both checks -- it
    # can never be silently bucketed into HARD or SOFT to mask a genuine
    # conflict.
    _HARD_CLAIMANT_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {STATUS_IN_QUEUE, STATUS_PROPOSED, STATUS_BLOCKED, STATUS_IN_PROGRESS}
    )
    _SOFT_CLAIMANT_STATUSES: ClassVar[frozenset[str]] = frozenset({STATUS_DRAFT, STATUS_HOLD})

    def _check_manifest_conflicts(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
        strict: bool = False,
    ) -> None:
        """Check 12: no two in-flight claimants in the SAME REPO own the same Manifest path.

        Per docs/backlog-contract.md "Manifest Conflict Rule": two claimants
        of one path collide at git-ops time. HARD claimants
        (``in-queue``, ``proposed``, ``blocked``, ``in-progress``) are
        checked on every run; two or more with no ordering dependency is an
        ERROR using the unchanged Manifest-conflict wording (FR-3, db-313).

        SOFT claimants (``draft``, ``hold``) are pre-lifecycle authoring
        states. When ``strict`` is ``True``, a collision that exists only
        once SOFT claimants are folded into the HARD set emits a distinct
        draft/hold ERROR (FR-4, db-267), giving ``spec-to-backlog`` an
        authoring-time exit gate. Default runs never evaluate SOFT
        claimants, preserving the all-draft rc=0 authoring gate. A
        collision already reported via the HARD check is not reported a
        second time under the SOFT check.

        Tasks with explicit Dependencies between them are exempt because the
        ordering resolves the conflict. The check is scoped by ``(repo, path)``
        because two tasks targeting different repos can legitimately list the
        same path (e.g., ``.devcontainer/devcontainer.json`` exists in both
        caylent-telemetry and the kanon repo's per-repo edits).
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        # Map: (repo, path) -> list of (task_id, status)
        ownership: dict[tuple[str, str], list[tuple[str, str]]] = {}
        deps_by_task: dict[str, set[str]] = {}

        for row_id, status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            repo = self._extract_repo(content) or ""
            try:
                manifest_rows = parse_manifest(content)
            except ManifestParseError:
                # Other validate rules (TestValidateContent) emit the missing
                # Manifest error; conflict detection treats unparseable
                # Manifests as having zero entries to avoid duplicate noise.
                manifest_rows = []
            for manifest_row in manifest_rows:
                if not self._is_real_manifest_path(manifest_row.file):
                    continue
                ownership.setdefault((repo, manifest_row.file), []).append((row_id, status))
            deps_by_task[row_id] = self._extract_dep_ids(content)

        for (repo, path), owners in ownership.items():
            if len(owners) < 2:
                continue
            hard_ids = [tid for tid, st in owners if st in self._HARD_CLAIMANT_STATUSES]
            hard_error = self._hard_manifest_conflict_error(path, repo, hard_ids, deps_by_task)
            if hard_error:
                errors.append(hard_error)
                continue
            if not strict:
                continue
            soft_ids = [tid for tid, st in owners if st in self._SOFT_CLAIMANT_STATUSES]
            soft_error = self._soft_manifest_conflict_error(path, hard_ids + soft_ids, deps_by_task)
            if soft_error:
                errors.append(soft_error)

    def _hard_manifest_conflict_error(
        self,
        path: str,
        repo: str,
        hard_ids: list[str],
        deps_by_task: dict[str, set[str]],
    ) -> str | None:
        """Return the unchanged Manifest-conflict ERROR text (FR-3), or ``None``.

        Fires when two or more HARD claimants of ``path`` exist with no
        ordering dependency between them (any DAG that totally orders the
        set via transitive reachability is accepted).
        """
        if len(hard_ids) < 2 or self._tasks_form_dep_chain(hard_ids, deps_by_task):
            return None
        sorted_ids = sorted(hard_ids)
        chain_hint = "\n".join(
            f"    uv run devbench add-dep {later} {earlier}" for earlier, later in itertools.pairwise(sorted_ids)
        )
        return (
            f"Manifest conflict on {path!r} in repo {repo or '(unknown)'}: "
            f"claimed by {', '.join(sorted_ids)}. Wire a serial dep chain:\n"
            f"{chain_hint}\n"
            f"  -- or any other DAG that totally orders the set. See "
            f"docs/backlog-contract.md 'Manifest Conflict Rule'."
        )

    def _soft_manifest_conflict_error(
        self,
        path: str,
        combined_ids: list[str],
        deps_by_task: dict[str, set[str]],
    ) -> str | None:
        """Return the strict-mode draft/hold Manifest-conflict ERROR text (FR-4), or ``None``.

        Fires only under ``strict=True``, when the HARD check has not
        already reported a conflict for ``path`` and folding SOFT
        (``draft``/``hold``) claimants into the HARD set produces a
        collision with no ordering dependency between them.
        """
        if len(combined_ids) < 2 or self._tasks_form_dep_chain(combined_ids, deps_by_task):
            return None
        sorted_combined = sorted(combined_ids)
        return (
            f"Manifest conflict (draft/hold) on {path!r}: "
            f"claimed by {', '.join(sorted_combined)}. These units are not yet "
            f"in-queue; wire a serial dep chain before promoting. See "
            f"docs/backlog-contract.md 'Manifest Conflict Rule'."
        )

    @staticmethod
    def _extract_dep_ids(content: str) -> set[str]:
        """Return the set of task IDs in a work-unit's ## Dependencies table."""
        deps: set[str] = set()
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                in_deps = stripped.startswith("## Dependencies")
                continue
            if not in_deps:
                continue
            if not stripped.startswith("|"):
                continue
            # `startswith("|")` guarantees at least 2 cells after split.
            cells = [c.strip() for c in stripped.split("|")]
            cell = cells[1]
            if not cell or cell.lower() == "id" or cell.startswith("-"):
                continue
            if cell in DEPENDENCY_NONE_VALUES:
                continue
            # Cell may contain a comma-separated dep list
            for raw in cell.split(","):
                token = raw.strip()
                # Allow IDs with ":" suffix variants; keep core ID
                if not token:
                    continue
                # Validate against task-id shape
                if re.fullmatch(r"E\d+(-F\d+)?(-S\d+)?(-T\d+)?", token):
                    deps.add(token)
        return deps

    @staticmethod
    def _tasks_form_dep_chain(ids: list[str], deps: dict[str, set[str]]) -> bool:
        """Return True iff every id in ``ids`` is comparable via transitive reachability.

        Two task ids are *comparable* when one is reachable from the other by
        following dep edges. A dep chain that resolves ownership conflicts
        requires the conflict set to be totally ordered: every pair of
        claimants must be comparable in at least one direction. This is the
        canonical contract -- any DAG that totally orders the set (a clean
        N-1 chain, a branching DAG that merges, full N*(N-1)/2 pairwise
        edges) is accepted (issue #145).

        FR-5 (db-311): the BFS traverses through EVERY child, including
        non-claimant intermediates that are not themselves in ``ids`` --
        ``deps`` (``deps_by_task``) already holds every task row's edges, so
        the data is present. ``id_set`` is only applied at the end, via
        ``seen & id_set``, so a correctly-ordered chain through a non-claimant
        (``C -> B(non-claimant) -> A``) is no longer mis-scored as unordered.
        ``seen`` still guards cycles so the traversal always terminates.
        """
        if len(ids) < 2:
            return True
        id_set = set(ids)

        def reachable(start: str) -> set[str]:
            seen: set[str] = set()
            queue: deque[str] = deque([start])
            while queue:
                node = queue.popleft()
                for child in deps.get(node, set()):
                    if child in seen:
                        continue
                    seen.add(child)
                    queue.append(child)
            return seen & id_set

        reach = {tid: reachable(tid) for tid in ids}
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if b not in reach[a] and a not in reach[b]:
                    return False
        return True

    def _check_language_ac_alignment(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 13: AC-FINAL Python-tooling lines must carry the N/A suffix on non-Python tasks.

        Per docs/acceptance-criteria-canonical.md, AC-FINAL-002..005, 008,
        and 014 apply only to Python tasks. Non-Python tasks must append
        ``-- N/A for <tier> Tasks (no <language> source authored)`` so
        reviewers do not enforce inapplicable checks.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            try:
                paths = [m.file for m in parse_manifest(content)]
            except ManifestParseError:
                continue
            tier = self._classify_manifest_tier(paths)
            if tier in ("", "Python", "Mixed"):
                # Mixed tasks have at least one .py file -> Python ACs apply
                # to that subset; do not emit warnings.
                continue

            # Walk AC-FINAL lines; for each Python-tier AC ID, require the
            # N/A suffix unless the line is missing entirely (handled by
            # other rules).
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped.startswith("- ["):
                    continue
                m = re.match(r"^- \[[ x]\] (AC-FINAL-\d{3})\b", stripped)
                if not m:
                    continue
                ac_id = m.group(1)
                if ac_id not in self._AC_FINAL_LANGUAGE_TIER_IDS:
                    continue
                if "-- N/A" in stripped:
                    continue
                errors.append(
                    f"{row_id}: {ac_id} requires the N/A suffix on "
                    f"{tier}-tier task (no Python source in Manifest). "
                    f"Append '-- N/A for {tier} Tasks (no Python source authored)' "
                    f"per docs/acceptance-criteria-canonical.md."
                )

    def _check_no_glob_in_manifest(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Issue #221 B4: reject Manifest entries containing ``*`` or ``**`` globs.

        Globs in a Changes Manifest produce confusing downstream errors -- the
        source-test atomicity rule, the path-prefix rule, and the conflict
        detector all treat the glob as a literal path and emit misleading
        diagnostics. Manifest paths must be concrete; tasks whose actual file
        list is determined at execution time should either (a) use a sentinel
        value like ``<source-drift-fix-targets-determined-at-execution>``
        (see ``devbench.backlog.sentinels``), or (b) declare the canonical
        candidates and rely on ``manifest_amendment`` to amend the Manifest
        at runtime when the surface is known.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest
        from devbench.backlog.sentinels import is_sentinel_manifest_value

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            try:
                manifest_rows = parse_manifest(content)
            except ManifestParseError:
                continue
            for manifest_row in manifest_rows:
                path = manifest_row.file
                if is_sentinel_manifest_value(path):
                    continue
                if "*" in path:
                    errors.append(
                        f"{row_id}: Manifest entry {path!r} contains a glob "
                        f"pattern. Manifest paths must be concrete; for "
                        f"execution-determined file lists, use a sentinel "
                        f"value (e.g., "
                        f"`<source-drift-fix-targets-determined-at-execution>`) "
                        f"and amend the Manifest at runtime via "
                        f"`manifest_amendment`. See "
                        f"docs/backlog-contract.md 'Manifest Glob Rejection'."
                    )

    def _check_source_test_pairs(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 14: every production-source .py file in a Manifest needs a matching test entry.

        Source-test atomicity (Update vs Add): an ``Update`` annotation on
        an EXISTING ``tests/<...>/test_<basename>.py`` file satisfies the
        rule the same way an ``Add`` annotation does. The rule asserts
        only that the test file appears in the Manifest -- whether the
        executor creates it or augments it is a per-task implementation
        detail. See ``docs/source-test-atomicity.md`` for a worked example.

        Per docs/source-test-atomicity.md, splitting source and test across
        sibling tasks causes AC-FINAL-014 (100% coverage) to fail in the
        source-authoring task. The rule ensures the test pair is in the
        same Manifest.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            try:
                paths = [m.file for m in parse_manifest(content)]
            except ManifestParseError:
                continue
            for source_path in paths:
                if not self._is_production_source(source_path):
                    continue
                source_stem = self._source_stem_for_pair_match(source_path)
                if not source_stem:
                    continue
                # A test pair is any `test_*.py` (or `*_test.py`) entry in the
                # SAME Manifest whose basename contains the source stem as a
                # substring. This accepts project naming conventions such as
                # `test_telemetry_event.py` for `event.py`, while still
                # catching the split-Manifest anti-pattern (where the test
                # file lives in a sibling task's Manifest entirely).
                has_pair = any(self._test_filename_pairs_with_stem(p, source_stem) for p in paths)
                if not has_pair:
                    errors.append(
                        f"{row_id}: production source {source_path!r} has no "
                        f"matching test in the same Manifest "
                        f"(expected a test_*.py whose basename contains "
                        f"{source_stem!r}, e.g., tests/unit/test_{source_stem}.py). "
                        f"Add the test entry per docs/source-test-atomicity.md."
                    )

    def _check_task_type_taxonomy(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 21: six-type task taxonomy and per-type Manifest invariants (FR-4.1).

        Parses the optional ``## Task Type:`` section. A task with no
        ``## Task Type:`` section defaults to ``behavior-fix`` -- the
        strictest type -- so that a missing declaration is never an escape
        hatch from the RED gate or the production-source Manifest
        invariant (AC-45). Terminal Tasks (``done`` / ``declined``) are
        skipped entirely: they predate the taxonomy and cannot be
        retroactively failed for a missing section. Non-terminal Tasks
        have the per-type Changes Manifest invariant from the FR-4.1
        taxonomy table enforced:

        - ``behavior-fix`` / ``feature`` (RED-gated, ``GATED_TASK_TYPES``):
          the Manifest must contain at least one production-source row.
        - ``test-only``: every Manifest row must be a test path.
        - ``docs``: every Manifest row must be a documentation/markdown path
          OR a documentation-pinning test path (db-300: a docs task may
          legitimately own a test that pins its own documentation).
        - ``chore``: every Manifest row must be a dependency/config/lockfile
          path OR a documentation/markdown path (db-300: a chore task may
          legitimately own ``CHANGELOG.md``).
        - ``refactor``: exempt from the per-row invariants enforced here --
          its green-green runtime requirement is a TDD-cycle-log concern,
          out of scope for this static Manifest check.

        Every per-row invariant still rejects production Python source under
        ``src/`` for docs/chore/test-only: each named classifier in the
        OR-lists above independently rejects production source, so widening
        a type's OR-list to accept a second classifier never widens it to
        accept production source too.

        An unrecognized ``## Task Type:`` value fails naming the full
        allowed set (AC-45). Every invariant rejection names the offending
        row_id, the declared type, and the violated invariant (AC-E4-F2-S1-T1-5).

        Production/test classification reuses ``_is_production_source`` /
        ``_is_test_source_path`` (Rule 14) exclusively -- no independent
        classifier exists for that boundary (AC-47).

        A Task whose ``## Changes Manifest`` table cannot be parsed (e.g. a
        row with the wrong column count) is skipped by this rule rather
        than crashing validate() or deriving a diagnostic from a partially
        parsed table. This mirrors the ``except ManifestParseError:
        continue`` pattern already used by every other Manifest-consuming
        validate rule; it does NOT imply another rule catches the malformed
        table on the author's behalf -- for a repo with no configured
        ``checkout_directory`` none currently does. That gap predates this
        check and is shared by all six ManifestParseError call sites in
        this module, so closing it is out of scope here.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        for row_id, row_status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            # Skip terminal tasks: they predate the taxonomy and cannot be
            # retroactively failed for a missing ## Task Type: section.
            if row_status in _TERMINAL_CHILD_STATUSES:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            declared = self._extract_task_type(content)
            # A Task with no explicit ## Task Type: section defaults to
            # the strictest type (behavior-fix) rather than being exempt,
            # so omitting the section is never a way to dodge the RED gate
            # or the production-source Manifest invariant (AC-45).
            task_type = declared if declared is not None else DEFAULT_TASK_TYPE

            if task_type not in VALID_TASK_TYPES:
                errors.append(
                    f"{row_id}: unrecognized '## Task Type:' value {task_type!r}. "
                    f"Allowed values: {', '.join(sorted(VALID_TASK_TYPES))}. "
                    f"See docs/backlog-contract.md 'Task-Type Taxonomy'."
                )
                continue

            if task_type == TASK_TYPE_REFACTOR:
                continue

            try:
                manifest_paths = [m.file for m in parse_manifest(content)]
            except ManifestParseError:
                # Rule 21 mirrors the ManifestParseError swallow already used
                # by every other Manifest-consuming validate rule (the
                # manifest-conflict, AC-language-tier, no-glob and Rule 14
                # source-test-pair checks at lines 2115, 2245, 2307 and 2358).
                # That established pattern does NOT currently guarantee any
                # OTHER rule surfaces the malformed Manifest either -- for a
                # repo with no configured checkout_directory, a malformed
                # Manifest row today produces zero validate() errors from
                # any rule, a pre-existing gap that predates this task and
                # spans all six ManifestParseError call sites. Rule 21
                # deliberately follows the established (imperfect) swallow
                # convention rather than being the one rule that either (a)
                # crashes validate() on an already-broken table or (b)
                # derives a task-type-invariant diagnostic from a partially
                # parsed Manifest. Closing the underlying gap requires
                # auditing all six sites together and is out of this task's
                # scope (AC-E4-F2-S1-T1-1..6 concern only the taxonomy).
                continue
            paths = [p for p in manifest_paths if self._is_real_manifest_path(p)]
            self._check_task_type_manifest_invariant(row_id, task_type, paths, errors)

    # Per-type Manifest invariant: type -> (OR-list of row classifier names,
    # human description). A row is accepted if ANY named classifier accepts
    # it (db-300: a type may legitimately own rows shaped like more than one
    # classifier -- e.g. a docs task owning a documentation-pinning test, or
    # a chore task owning CHANGELOG.md). ``behavior-fix`` / ``feature`` are
    # handled separately in ``_check_task_type_manifest_invariant`` (an
    # aggregate "at least one" check, not a per-row classifier).
    # ``refactor`` never reaches the dispatcher -- the caller filters it out
    # before parsing the Manifest. The three classifiers below
    # (``_is_test_source_path``, ``_is_documentation_path``,
    # ``_is_chore_path``) are the single source of truth reused across every
    # OR-list entry -- no fourth classifier exists.
    _TASK_TYPE_ROW_INVARIANTS: ClassVar[dict[str, tuple[tuple[str, ...], str]]] = {
        TASK_TYPE_TEST_ONLY: (("_is_test_source_path",), "test"),
        TASK_TYPE_DOCS: (
            ("_is_documentation_path", "_is_test_source_path"),
            "documentation/markdown or documentation-pinning test",
        ),
        TASK_TYPE_CHORE: (
            ("_is_chore_path", "_is_documentation_path"),
            "dependency/config/lockfile or documentation/markdown",
        ),
    }

    def _check_task_type_manifest_invariant(
        self,
        row_id: str,
        task_type: str,
        paths: list[str],
        errors: list[str],
    ) -> None:
        """Dispatch to the per-type Changes Manifest invariant for ``task_type``.

        ``behavior-fix`` / ``feature`` (``GATED_TASK_TYPES``) require an
        aggregate check -- at least one production-source row anywhere in
        the Manifest. ``test-only`` / ``docs`` / ``chore`` require a
        per-row check via ``_TASK_TYPE_ROW_INVARIANTS`` -- every row must be
        accepted by AT LEAST ONE of that type's named classifiers (an
        OR-list, db-300). Every classifier rejects production Python source
        under ``src/``, so the OR-list widening never lets production
        source through.
        """
        if task_type in GATED_TASK_TYPES:
            if not any(self._is_production_source(p) for p in paths):
                errors.append(
                    f"{row_id}: task type {task_type!r} requires at least "
                    f"one production-source row in the Changes Manifest, "
                    f"but none was found -- task-type invariant violated. "
                    f"See docs/backlog-contract.md 'Task-Type Taxonomy'."
                )
            return

        # Every VALID_TASK_TYPES member partitions into exactly one of
        # GATED_TASK_TYPES (handled above), TASK_TYPE_REFACTOR (filtered
        # by the caller before this method is reached), or a key in
        # _TASK_TYPE_ROW_INVARIANTS -- direct indexing (not .get()) so a
        # future type added to VALID_TASK_TYPES without a matching entry
        # here raises immediately (fail-fast) instead of silently
        # skipping its invariant.
        classifier_names, description = self._TASK_TYPE_ROW_INVARIANTS[task_type]
        classifiers = [getattr(self, classifier_name) for classifier_name in classifier_names]
        for path in paths:
            if not any(classifier(path) for classifier in classifiers):
                errors.append(
                    f"{row_id}: task type {task_type!r} allows only "
                    f"{description} rows in the Changes Manifest, but "
                    f"{path!r} is not a {description} path -- task-type "
                    f"invariant violated. See docs/backlog-contract.md "
                    f"'Task-Type Taxonomy'."
                )

    # ------------------------------------------------------------------
    # E209: Backlog-Contract Alignment hardening rules
    # ------------------------------------------------------------------

    _REQUIRED_TASK_SECTIONS: ClassVar[tuple[str, ...]] = ("Status", "Dependencies", "Changes Manifest")
    # Accepts the canonical Epic shape ``E\d+`` plus the test-harness
    # convention ``EX``. Anything else (a free-text dep, a typo) fails
    # the rule. Mirrors the ``EPIC_ID_RE`` shape in constants.py while
    # adding the optional Feature/Story/Task suffixes.
    _DEP_ID_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^E[A-Z0-9]+(-F\d+)?(-S\d+)?(-T\d+)?$")

    # A lowercase hex commit hash, abbreviated (7 chars, git's default short
    # form) through full-length (40 chars, a SHA-1 object id). Uppercase hex
    # is deliberately excluded -- git's own output is always lowercase, so an
    # uppercase token is a hand-typed guess, not a citation of a real commit.
    _CITATION_COMMIT_RE: ClassVar[re.Pattern[str]] = re.compile(r"^[0-9a-f]{7,40}$")

    @classmethod
    def is_valid_citation(cls, value: str) -> bool:
        """Return True iff *value* is shaped like a commit hash or a task id (FR-4.5).

        An already-satisfied decline must cite the closing commit or task id
        (FR-4.5's error-handling contract: "an uncited decline is rejected").
        Two shapes are accepted: a lowercase hex commit hash (``_CITATION_COMMIT_RE``,
        7-40 characters -- git's abbreviated-to-full range) or a canonical
        work-unit id matching ``_DEP_ID_PATTERN``, the same single-source-of-truth
        pattern the Dependencies-table format check (Rule 17) already uses,
        rather than a second, possibly-divergent "what a task id looks like"
        regex.

        Args:
            value: The candidate citation token. Leading/trailing whitespace
                is stripped before matching.

        Returns:
            ``True`` when the trimmed *value* matches either shape;
            ``False`` for an empty string, a whitespace-only string, or any
            other free text.
        """
        trimmed = value.strip()
        if not trimmed:
            return False
        return bool(cls._CITATION_COMMIT_RE.match(trimmed)) or bool(cls._DEP_ID_PATTERN.match(trimmed))

    def _check_required_sections(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 15: every Task work-unit file declares Status, Dependencies, and Changes Manifest.

        Epic, Feature, and Story rows are exempt (their bodies are
        scaffolding -- a Manifest does not apply). Tasks that miss any
        required section receive an explicit error naming the section,
        so authors know exactly what to add.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not self._is_task_id(row_id):
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            sections = self._extract_sections(content)
            for required in self._REQUIRED_TASK_SECTIONS:
                if required not in sections:
                    errors.append(
                        f"{row_id}: missing required section '## {required}'. "
                        f"Add the section per docs/backlog-contract.md."
                    )

    def _check_status_enum(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 16: every parsed ``## Status:`` value is in ``VALID_STATUSES``.

        The CLI's ``set-status`` and ``mark-*`` helpers reject invalid
        values at write time, but a hand-edited file can drift; this
        check catches the drift at validate-backlog time so the
        orchestrator never sees a bad status mid-run.

        Additionally enforces that ``draft`` is only permitted for Task-level
        work units (i.e. IDs containing a ``-T<digits>`` segment).  Epics,
        Features, and Stories are managed by automatic rollup logic and must
        never carry a ``draft`` status.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            match = STATUS_LINE_RE.search(content)
            if match is None:
                # Missing status line is reported by _check_files_and_statuses;
                # don't double-report here.
                continue
            raw_status = match.group(2).strip().lower()
            if raw_status not in VALID_STATUSES:
                errors.append(
                    f"{row_id}: invalid '## Status:' value {raw_status!r}. Allowed values: {sorted(VALID_STATUSES)}."
                )
                continue
            if raw_status == STATUS_DRAFT and not self._is_task_id(row_id):
                unit_type = self._unit_type_label(row_id)
                errors.append(
                    f'{row_id}: Status "{STATUS_DRAFT}" is only valid for Task work units;'
                    f" {row_id} is type {unit_type}."
                )

    def _check_dep_id_format(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 17: every ``## Dependencies`` row's first cell is a valid task-ID shape.

        ``DEPENDENCY_NONE_VALUES`` cells (``None`` / ``-``) are skipped.
        Header rows (``ID``, separator dashes) are skipped. Anything
        else must match the canonical ID regex
        ``E\\d+(-F\\d+)?(-S\\d+)?(-T\\d+)?``; mismatches produce an
        explicit error with the offending row text so authors can fix
        the typo.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            for dep_id in self._iter_dep_ids(content):
                if not self._DEP_ID_PATTERN.match(dep_id):
                    errors.append(
                        f"{row_id}: dependency ID {dep_id!r} does not match the "
                        f"canonical task-ID regex E<n>[-F<n>][-S<n>][-T<n>]. "
                        f"Fix the row in '## Dependencies' or remove it."
                    )

    def _check_branch_uniqueness(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 18 (E219): no two Tasks derive the same branch name.

        Each Task's branch is either an explicit ``- **Branch:**
        `backlog/<id>``` line in the work-unit file or the canonical
        ``backlog/<id-lowercase>`` derivation. A collision means two
        Tasks would push to the same branch, breaking auto-merge and
        producing false review failures.

        Skipped entirely when single-PR mode is active (the
        ``git_ops.single_branch`` yaml field is set), since every task
        legitimately shares the configured branch.
        """
        from devbench.config import RUNTIME_CONFIG

        single_branch = getattr(RUNTIME_CONFIG.git_ops, "single_branch", None)
        if single_branch:
            return
        branch_prefix = getattr(RUNTIME_CONFIG.git_ops, "branch_prefix", None)
        branches: dict[str, list[str]] = {}
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not self._is_task_id(row_id):
                continue
            branch = self._derive_branch_for_row(row_id, file_path_str, workspace_root, branch_prefix)
            branches.setdefault(branch, []).append(row_id)
        for branch, ids in sorted(branches.items()):
            if len(ids) > 1:
                errors.append(
                    f"Branch collision on {branch!r}: claimed by {sorted(ids)}. "
                    f"Rename one of the work units (or its explicit '**Branch:**' override) "
                    f"so each task pushes to a unique branch; see docs/backlog-contract.md "
                    f"'Branch Uniqueness Rule'."
                )

    # Issue #117: the changes_manifest reviewer was passing work units whose
    # Manifest still contained the placeholder row authors are supposed to
    # replace. Catch the placeholder at validate-backlog time so the orchestrator
    # never even claims the task; the executor cannot build atop a TBD manifest.
    _PLACEHOLDER_MANIFEST_RE: ClassVar[re.Pattern[str]] = re.compile(r"^TBD\b", re.IGNORECASE)
    _ACTIVE_TASK_STATUSES_FOR_PLACEHOLDER_CHECK: ClassVar[frozenset[str]] = frozenset(
        {STATUS_IN_QUEUE, STATUS_IN_PROGRESS, STATUS_BLOCKED}
    )

    def _check_no_placeholder_manifest_rows(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Issue #117: reject work units whose Manifest still contains a TBD row.

        The canonical placeholder reads ``TBD | Executor agent: replace
        this row with the actual files to be created or modified.`` The
        check fires for every active Task (in-queue / in-progress /
        blocked); ``in-review`` and terminal statuses are skipped because
        the executor has either already amended the Manifest or the task
        is closed.

        Each offending row produces an error naming the task ID and the
        first cell text so the operator (or the auto-amender) can fix
        it before any agent claims the unit.
        """
        for row_id, status, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not self._is_task_id(row_id):
                continue
            if not file_path_str:
                continue
            if status.lower() not in self._ACTIVE_TASK_STATUSES_FOR_PLACEHOLDER_CHECK:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            placeholder_cell = self._first_placeholder_manifest_cell(content)
            if placeholder_cell:
                errors.append(
                    f"{row_id}: Changes Manifest still has placeholder row "
                    f"{placeholder_cell!r}. Replace with real file entries before "
                    f"claim; see docs/backlog-contract.md 'No Placeholder Rows Rule'."
                )

    @classmethod
    def _first_placeholder_manifest_cell(cls, content: str) -> str:
        """Return the first ``TBD`` cell in ``## Changes Manifest`` or ``""``.

        Walks the Manifest table once: skips the section heading line,
        the header row (cells like ``File`` / ``Change``), and any
        separator row (cells starting with ``-``). The first data row
        whose cell-1 starts with ``TBD`` (case-insensitive) is returned
        verbatim; any other row terminates the scan.
        """
        in_manifest = False
        seen_header = False
        for raw in content.splitlines():
            stripped = raw.strip()
            if stripped.startswith("## "):
                in_manifest = stripped.startswith("## Changes Manifest")
                seen_header = False
                continue
            if not in_manifest or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            cell = cells[1]
            if not cell or cell.startswith("-"):
                continue
            if not seen_header:
                # First non-separator row is the header; skip it.
                seen_header = True
                continue
            if cls._PLACEHOLDER_MANIFEST_RE.match(cell):
                return cell
        return ""

    # Rule 20: orphan path tokens in AC / DoD. The Manifest is the single
    # source of truth for files a Task produces; AC / DoD that restate
    # paths is redundancy that drifts. The check is gated by
    # ``RUNTIME_CONFIG.validate.check_orphan_path_tokens`` so existing
    # backlogs see no behaviour change until they opt in.
    #
    # Token regex: a single-backtick group whose body has no whitespace.
    # The optional second group captures a trailing `` (ref)`` marker --
    # an inline escape hatch declaring the token a read-only reference
    # (e.g. an external config file the Task reads but does not modify).
    _ORPHAN_TOKEN_RE: ClassVar[re.Pattern[str]] = re.compile(r"`([^`\s]+)`(\s*\(ref\))?")
    _ORPHAN_PATH_EXTS: ClassVar[tuple[str, ...]] = (
        ".md",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".java",
        ".kt",
        ".rb",
        ".rs",
        ".swift",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".xml",
        ".tf",
        ".tfvars",
        ".hcl",
        ".sh",
        ".dockerfile",
        ".html",
        ".css",
        ".scss",
        ".sql",
    )
    _ORPHAN_KNOWN_PREFIXES: ClassVar[tuple[str, ...]] = (
        "src/",
        "tests/",
        "test/",
        "infra/",
        "docs/",
        "backlog/",
        "config/",
    )

    @classmethod
    def _is_path_shaped(cls, token: str, manifest_dir_prefixes: set[str]) -> bool:
        """Return ``True`` if ``token`` looks like a single-file path reference.

        Excludes shell flags, key=value forms, glob patterns, URL-scheme
        tokens, and tokens with no path-like marker. Includes tokens
        ending in a known extension, starting with a built-in directory
        prefix, or starting with a directory prefix observed anywhere in
        the Task's parsed Manifest. Uses no domain knowledge -- the
        prefix and extension lists are static.

        Bare extensions like ``.md`` (3 chars, no filename stem, no
        directory) appear in prose like "only ``.md`` files modified" and
        are NOT path references. Tokens that end in a known extension
        must have either a ``/`` separator OR at least one alphanumeric
        character in the stem to qualify as a path.
        """
        if not token or token.startswith("-") or "=" in token or "*" in token or "://" in token:
            return False
        lower = token.lower()
        for ext in cls._ORPHAN_PATH_EXTS:
            if lower.endswith(ext):
                stem = token[: -len(ext)]
                # Require a directory separator OR a real filename stem.
                # Bare ``.md`` / ``.py`` / etc. (literal extension in prose)
                # have an empty stem and MUST NOT be treated as paths.
                return "/" in stem or bool(stem and any(c.isalnum() for c in stem))
        if any(token.startswith(p) for p in cls._ORPHAN_KNOWN_PREFIXES):
            return True
        return any(token.startswith(prefix) for prefix in manifest_dir_prefixes)

    @staticmethod
    def _normalise_orphan_path(token: str, checkout_dir: str | None) -> str:
        """Strip leading ``./``, optional ``checkout_dir/`` prefix, and trailing ``/``.

        Mirrors the normalisation rule 11 applies to Manifest paths so
        AC / DoD tokens authored with the prefix still match Manifest
        entries that the prefix-rule fixer stripped.
        """
        s = token
        if s.startswith("./"):
            s = s[2:]
        if checkout_dir:
            prefix = checkout_dir.rstrip("/") + "/"
            if s.startswith(prefix):
                s = s[len(prefix) :]
        return s.rstrip("/")

    @classmethod
    def _iter_orphan_candidates(cls, section_body: str) -> "list[tuple[str, bool]]":
        """Yield ``(token, has_ref_marker)`` for every backtick-quoted no-whitespace token.

        Does not filter for path-shape -- the caller decides which tokens
        are path-shaped relative to the Task's Manifest. The boolean is
        ``True`` when the token is immediately followed by ``(ref)``,
        signalling the inline read-only escape hatch.
        """
        out: list[tuple[str, bool]] = []
        for match in cls._ORPHAN_TOKEN_RE.finditer(section_body):
            token = match.group(1)
            has_ref = match.group(2) is not None
            out.append((token, has_ref))
        return out

    def _check_marker_status_agreement(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Rule 27: a task waiting on a live ``[BLOCKED_PENDING_PROPOSAL]`` marker must be ``blocked``.

        The marker and the ``## Status:`` line are written by separate steps, and
        three consumers read them independently, so a disagreement is silently
        load-bearing rather than cosmetic:

        - :meth:`_auto_requeue_marker_dependents` (the ADR-07 cascade) skips any
          candidate whose status is not ``blocked``, so a marked-but-unblocked
          task is never requeued when its promoted dependency completes -- it
          strands with a satisfied dependency, the exact outcome the cascade
          exists to prevent.
        - ``cli._should_auto_restart_after_no_actionable`` refuses to restart
          while any task is ``in-progress``, so a target marked mid-execution
          suppresses auto-restart indefinitely.
        - :meth:`~devbench.backlog.parser.BacklogParser.find_next_actionable`
          PRIORITISES ``in-progress`` over ``in-queue``, so a claim sweep can
          re-claim the target while its blocker is unresolved.

        ``classify_blocked_task`` keys off marker presence, not status, so it
        reports such a task as auto-clearing while the status line disagrees;
        neither view cross-checks the other. This rule is that cross-check, and
        it holds regardless of which code path introduced the mismatch --
        including future ones -- rather than relying on every marker writer
        remembering to write the status too.

        Only markers with a NON-TERMINAL target are checked. A task whose
        markers all point at ``done``/``declined`` work is legitimately back in
        ``in-queue`` (or already running) because the cascade requeued it, so
        requiring ``blocked`` there would flag correct state.

        Terminal statuses are exempt: a ``done`` or ``declined`` task that still
        carries an old marker is finished, and its history is not a defect.

        Args:
            rows: ``(id, status, file_path)`` triples from the backlog index.
            workspace_root: Workspace root that ``file_path`` is relative to.
            errors: Accumulator appended to on violation.
        """
        status_by_id = {
            row_id: (status or "").strip().lower()
            for row_id, status, _ in rows
            if row_id and not row_id.startswith("-") and row_id.lower() != "id"
        }
        terminal = {STATUS_DONE, STATUS_DECLINED}

        for row_id, status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            own_status = (status or "").strip().lower()
            if own_status in (STATUS_BLOCKED, *terminal):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            live_markers = sorted(
                marker_id
                for marker_id in self._extract_pending_proposal_markers(wu_path)
                if status_by_id.get(marker_id, "") not in terminal
            )
            if not live_markers:
                continue
            errors.append(
                f"{row_id}: status is {own_status!r} but the task carries a live "
                f"[BLOCKED_PENDING_PROPOSAL] marker on {', '.join(live_markers)}. A task "
                f"waiting on a promoted proposal MUST be 'blocked': the ADR-07 auto-requeue "
                f"cascade skips non-blocked candidates, so this task would never be requeued "
                f"when its blocker completes, and 'in-progress' additionally suppresses "
                f"auto-restart and can be re-claimed by a sweep. Run "
                f"'uv run devbench sync-blocked' to reconcile status against dependency state."
            )

    def _check_no_orphan_path_tokens(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Rule 20: AC / DoD must not name paths that are absent from the Changes Manifest.

        For every Task work-unit (Stories/Features/Epics short-circuit;
        they have no Manifest), extract the ``## Acceptance Criteria``
        and ``## Definition of Done`` section bodies and walk every
        backtick-quoted no-whitespace token. If a token is path-shaped
        (per ``_is_path_shaped``) and is not present in the Task's
        parsed Manifest after normalisation -- and is not declared
        read-only via the inline ``(ref)`` marker -- emit an integrity
        error.

        Gated by ``RUNTIME_CONFIG.validate.check_orphan_path_tokens``;
        returns immediately when the flag is ``False`` (the default),
        so this rule is invisible to backlogs that have not opted in.
        """
        from devbench.config import RUNTIME_CONFIG

        if not getattr(RUNTIME_CONFIG.validate, "check_orphan_path_tokens", False):
            return

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            self._check_one_task_orphan_paths(row_id, wu_path, errors)

    def _check_one_task_orphan_paths(
        self,
        row_id: str,
        wu_path: Path,
        errors: list[str],
    ) -> None:
        """Per-task body for ``_check_no_orphan_path_tokens``."""
        from devbench.backlog.manifest import ManifestParseError, parse_manifest
        from devbench.config import RUNTIME_CONFIG

        content = wu_path.read_text(encoding="utf-8")
        try:
            manifest_rows = parse_manifest(content)
        except ManifestParseError:
            # Already reported by other rules; skip this task quietly.
            return

        repo = self._extract_repo(content)
        checkout_dir: str | None = None
        if repo is not None and repo in RUNTIME_CONFIG.repos:
            checkout_dir = RUNTIME_CONFIG.repos[repo].checkout_directory

        manifest_paths = {
            self._normalise_orphan_path(r.file, checkout_dir)
            for r in manifest_rows
            if self._is_real_manifest_path(r.file)
        }
        manifest_dir_prefixes = {p.split("/", 1)[0] + "/" for p in manifest_paths if "/" in p}

        sections = self._extract_sections(content)
        for section_name in ("Acceptance Criteria", "Definition of Done"):
            body = sections.get(section_name, "")
            if not body:
                continue
            for token, has_ref in self._iter_orphan_candidates(body):
                if has_ref or not self._is_path_shaped(token, manifest_dir_prefixes):
                    continue
                normalised = self._normalise_orphan_path(token, checkout_dir)
                if normalised in manifest_paths:
                    continue
                errors.append(
                    f"{row_id}: orphan path {token!r} in '## {section_name}' "
                    f"not in Changes Manifest. Either add the path to the "
                    f"Manifest, rewrite the AC/DoD line behaviourally "
                    f"(reference the Manifest symbolically), or suffix the "
                    f"token with ' (ref)' to declare it a read-only "
                    f"reference. See docs/backlog-contract.md "
                    f"'No Orphan Path Tokens Rule'."
                )

    # Matches a ``[DECLINED]`` audit line as written by ``_append_comment``
    # (``COMMENT_ENTRY_TEMPLATE``): ``[<timestamp>] [backlog_manager] [DECLINED] <message>``.
    # Anchored to the exact agent id the mark_declined() write path uses, so a
    # free-text ``## Comments`` line an agent writes elsewhere (e.g. quoting
    # "declined" in prose) can never be mistaken for the structural entry.
    _DECLINED_COMMENT_LINE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"^\[.+?\] \[backlog_manager\] \[DECLINED\] (?P<message>.+)$", re.MULTILINE
    )
    # FR-4.5 remedy 3's routing keyword. Matched case-insensitively against
    # the persisted decline reason so ``already-satisfied``, ``Already-Satisfied``,
    # etc. are all recognised as the same routing decision.
    _ALREADY_SATISFIED_TOKEN: str = "already-satisfied"
    # A citation token embedded in free text is typically wrapped or
    # punctuated, e.g. "already-satisfied (citing abc1234)" or
    # "already-satisfied, see E1-F1-S1-T9.". Stripping these characters
    # before tokenising on whitespace lets ``is_valid_citation`` match the
    # bare hash/id inside such wrapping without a bespoke extraction regex.
    _CITATION_WRAPPING_CHARS: str = "(),.;:\"'"

    def _check_already_satisfied_decline_citation(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 22: an already-satisfied decline must cite a commit or task id (FR-4.5).

        FR-4.5 remedy 3 lets an orchestrator decline a behavior-fix task
        whose test already passes because a prior task genuinely closed the
        behavior -- but only when the decline names the commit or task that
        closed it. An uncited "already-satisfied" claim is exactly as
        unfalsifiable as a fabricated RED (the failure mode FR-4.5 exists to
        close), so this rule scans every ``declined`` Task's ``[DECLINED]``
        comment entries and rejects any whose reason contains the
        ``already-satisfied`` routing keyword but no token that
        :meth:`is_valid_citation` accepts.

        Only ``declined``-status rows are scanned (Rule 22 is a no-op for
        every other status); a Task in any other state cannot yet carry a
        ``[DECLINED]`` comment written by ``mark_declined()``.

        Args:
            rows: Parsed ``(row_id, status, file_path)`` tuples from the
                backlog index.
            workspace_root: Workspace root the row's ``file_path`` is
                relative to.
            errors: Shared error accumulator; violations are appended here.
        """
        for row_id, row_status, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if row_status != STATUS_DECLINED:
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            for match in self._DECLINED_COMMENT_LINE_RE.finditer(content):
                message = match.group("message")
                if self._ALREADY_SATISFIED_TOKEN not in message.lower():
                    continue
                stripped_message = message
                for char in self._CITATION_WRAPPING_CHARS:
                    stripped_message = stripped_message.replace(char, " ")
                tokens = stripped_message.split()
                if any(self.is_valid_citation(token) for token in tokens):
                    continue
                errors.append(
                    f"{row_id}: declined with reason containing "
                    f"'{self._ALREADY_SATISFIED_TOKEN}' but no citation (commit hash or "
                    f"task id) was found in the message {message!r}. FR-4.5 requires an "
                    f"already-satisfied decline to cite the closing commit or task, e.g. "
                    f"'already-satisfied (citing abc1234)'."
                )

    @staticmethod
    def _derive_branch_for_row(
        unit_id: str, file_path_str: str, workspace_root: Path, branch_prefix: str | None = None
    ) -> str:
        """Resolve the branch name a Task row would push to.

        Mirrors ``BacklogParser._parse_branch``: prefer an explicit
        ``- **Branch:** \\`<name>\\``` line in the work-unit file; fall
        back to the canonical lowercase-ID template (namespaced by
        *branch_prefix* when set) when the explicit line is absent or
        unreadable.
        """
        from devbench.config_loader import format_branch_name

        if file_path_str:
            wu_path = workspace_root / file_path_str
            if wu_path.is_file():
                content = wu_path.read_text(encoding="utf-8")
                explicit = re.search(r"-\s+\*?\*?Branch:?\*?\*?\s*`([^`]+)`", content)
                if explicit:
                    return explicit.group(1).strip()
        return format_branch_name(unit_id, branch_prefix)

    @classmethod
    def _iter_dep_ids(cls, content: str) -> list[str]:
        """Yield every dep-ID candidate in a work-unit's ``## Dependencies`` table.

        Returns the list of cell-1 strings (after stripping). Header
        rows, separator rows, and ``DEPENDENCY_NONE_VALUES`` cells are
        excluded. Comma-separated tokens within a single cell are
        split so ``| E1, E2 |`` yields two candidates. Mirrors the
        parsing rules used by the runtime ``_extract_dep_ids`` helper
        but exposed for the format-check rule.
        """
        deps: list[str] = []
        in_deps = False
        for raw in content.splitlines():
            stripped = raw.strip()
            if stripped.startswith("## "):
                in_deps = stripped.startswith("## Dependencies")
                continue
            if not in_deps or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            cell = cells[1]
            if not cell or cell.lower() == "id" or cell.startswith("-"):
                continue
            if cell in DEPENDENCY_NONE_VALUES:
                continue
            for raw_token in cell.split(","):
                token = raw_token.strip()
                if token:
                    deps.append(token)
        return deps

    @staticmethod
    def _source_stem_for_pair_match(source_path: str) -> str:
        """Return the basename stem (without ``.py``) for source-test pairing.

        Returns ``""`` for ``__init__.py`` and non-Python paths so callers
        can skip the pairing check on packaging-only entries.
        """
        base = source_path.rsplit("/", 1)[-1]
        if base == "__init__.py" or not base.endswith(".py"):
            return ""
        return base[:-3]

    @staticmethod
    def _test_filename_pairs_with_stem(path: str, source_stem: str) -> bool:
        """Return True if ``path`` is a test file whose basename references ``source_stem``.

        Accepts both ``test_<...>.py`` and ``<...>_test.py`` patterns and
        requires ``source_stem`` to appear as a substring of the basename
        (minus extension and any ``test_`` / ``_test`` framing).
        """
        base = path.rsplit("/", 1)[-1]
        if not base.endswith(".py") or base == "__init__.py":
            return False
        stem = base[:-3]
        if stem.startswith("test_"):
            inner = stem[len("test_") :]
        elif stem.endswith("_test"):
            inner = stem[: -len("_test")]
        else:
            return False
        return source_stem in inner.split("_") or source_stem in inner

    @staticmethod
    def _extract_repo(content: str) -> str | None:
        """Extract the canonical ``org/repo`` string from a work-unit ``## Target Repository`` section.

        Returns ``None`` if the section is absent or malformed; callers
        treat that case as "check does not apply".
        """
        m = re.search(r"\*\*Repo:\*\*\s*`([^`]+)`", content)
        return m.group(1) if m else None

    @staticmethod
    def _extract_task_type(content: str) -> str | None:
        """Extract the declared ``## Task Type:`` value from a work-unit body.

        Returns the lowercased, whitespace-trimmed value, or ``None`` if the
        section is absent. Callers (``_check_task_type_taxonomy``) resolve a
        ``None`` result to ``DEFAULT_TASK_TYPE`` (``behavior-fix``), never
        to an exemption. Value casing/whitespace is normalized here so
        authors may write ``Test-Only`` or ``  docs  `` without tripping
        the taxonomy check.
        """
        m = TASK_TYPE_LINE_RE.search(content)
        if not m:
            return None
        return m.group(2).strip().lower()

    @staticmethod
    def _is_task_id(unit_id: str) -> bool:
        """Return True if the ID represents a task (contains -T followed by digits)."""
        parts = unit_id.split("-")
        return any(p.startswith("T") and p[1:].isdigit() for p in parts)

    @staticmethod
    def _unit_type_label(unit_id: str) -> str:
        """Return the hierarchy level label for a work-unit ID.

        Derives the label purely from ID structure:
        - ``E<n>``               -> ``"Epic"``
        - ``E<n>-F<n>``          -> ``"Feature"``
        - ``E<n>-F<n>-S<n>``     -> ``"Story"``
        - ``E<n>-F<n>-S<n>-T<n>`` -> ``"Task"``

        Args:
            unit_id: A canonical work-unit identifier such as ``"E1-F2-S3-T4"``.

        Returns:
            One of ``"Epic"``, ``"Feature"``, ``"Story"``, or ``"Task"``
            (the corresponding ``WorkUnitType`` enum value).

        Raises:
            ValueError: If ``unit_id`` does not match any recognised hierarchy
                shape (E<n>, E<n>-F<n>, E<n>-F<n>-S<n>, or E<n>-F<n>-S<n>-T<n>).
        """
        if BacklogManager._is_task_id(unit_id):
            return WorkUnitType.TASK.value
        parts = unit_id.split("-")
        if any(p.startswith("S") and p[1:].isdigit() for p in parts):
            return WorkUnitType.STORY.value
        if any(p.startswith("F") and p[1:].isdigit() for p in parts):
            return WorkUnitType.FEATURE.value
        if len(parts) == 1 and parts[0].startswith("E"):
            return WorkUnitType.EPIC.value
        raise ValueError(
            f"Unrecognized work-unit ID shape: {unit_id!r}. "
            "Expected one of: E<n>, E<n>-F<n>, E<n>-F<n>-S<n>, E<n>-F<n>-S<n>-T<n>."
        )

    @staticmethod
    def _extract_sections(content: str) -> dict[str, str]:
        """Extract ## sections from markdown content into a dict.

        Keys are section names (without '## ' prefix), values are the
        body text between this heading and the next ## heading.
        """
        sections: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in content.splitlines():
            if line.startswith("## "):
                if current_name is not None:
                    sections[current_name] = "\n".join(current_lines)
                heading = line[3:].strip()
                # Handle "## Status: in-queue" -> key "Status"
                current_name = heading.split(":")[0].strip()
                current_lines = []
            elif current_name is not None:
                current_lines.append(line)

        if current_name is not None:
            sections[current_name] = "\n".join(current_lines)

        return sections
