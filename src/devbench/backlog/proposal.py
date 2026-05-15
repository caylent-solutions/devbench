"""Task-factory proposal lifecycle.

After the manifest-amender rejects an amendment whose changes are legitimate
production fixes outside the task's scope, the orchestrator invokes
``devbench:blocker-resolver`` which writes a proposal JSON file describing
one or more new work units the factory should generate. ``devbench:task-factory``
then materialises each proposed task as a draft ``.md`` file with a status
determined by ``backlog.default_status_for_new_work_units`` in
``backlog/config/devbench.yaml`` (default: ``in-queue``; ``draft`` when opted in
via AC-189-8) and inserts a row in ``BACKLOG.md``. The human reviews, edits,
and promotes (``devbench promote-proposal``) or rejects
(``devbench reject-proposal``) each draft.

All mutations live in this module so the CLI layer stays thin. Concurrency
is protected by a POSIX file lock on ``.devbench/task-factory.lock``:
``allocate_next_ids`` acquires the lock before scanning the story directory
and returning a contiguous sequence of free task IDs. Two factory runs
firing in parallel cannot produce colliding IDs.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devbench.config_loader import RuntimeConfig

from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.constants import (
    COMMENT_AGENT_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS,
    STATUS_DECLINED,
    STATUS_DONE,
    STATUS_DRAFT,
    STATUS_HOLD,
    STATUS_IN_QUEUE,
    STATUS_PROPOSED,
)

logger = logging.getLogger(__name__)


def _get_runtime_config() -> RuntimeConfig:
    """Return the live ``RUNTIME_CONFIG`` singleton.

    Isolated into a module-level function so tests can monkeypatch it without
    importing ``devbench.config`` at module load time (which would trigger the
    full config-load cycle and potentially fail in environments without a
    ``devbench.yaml``).

    Returns:
        The ``RuntimeConfig`` instance from ``devbench.config``.
    """
    from devbench.config import RUNTIME_CONFIG

    return RUNTIME_CONFIG


PROPOSAL_DIR_NAME = ".devbench/proposals"
REJECTED_PROPOSAL_DIR_NAME = ".devbench/rejected-proposals"
LOCK_FILE_NAME = ".devbench/task-factory.lock"

# Allowed values for ``backlog.default_status_for_new_work_units`` (AC-189-8).
# Only these two statuses are valid initial states for materialised work units.
_ALLOWED_NEW_WU_STATUSES: frozenset[str] = frozenset({STATUS_IN_QUEUE, STATUS_DRAFT})

# Minimum character length for a ``suggested_approach`` field. Enforced by
# ``materialise_proposal`` as a fail-fast contract against thin auto-generated
# drafts that previously required operator hand-editing on every promotion.
# Threshold calibrated to the four-section minimum (Context + Scope + TDD
# approach + Verify) that ``blocker-resolver.md`` now requires: a genuinely
# complete approach narrative cannot fit under 160 characters.
_SUGGESTED_APPROACH_MIN_CHARS: int = 160


class ProposalTaskState(Enum):
    """Lifecycle state of a single ``proposed_tasks[].suggested_id``.

    Used by observability surfaces (``devbench status`` un-materialised count,
    ``devbench report`` panel, ``devbench list-proposals`` state labels) and
    by ``reject_proposal``'s un-materialised-rejection guard to discriminate
    "can drop the JSON" from "drafts already exist, use per-task reject".

    - ``UNMATERIALISED`` -- the proposal JSON names this id but no .md draft
      has been created anywhere (task-factory has not run for this source, or
      its "skip when prior unresolved" safety guard fired).
    - ``PROPOSED`` -- draft .md exists with ``## Status: proposed``; operator
      has not yet promoted or rejected.
    - ``PROMOTED`` -- draft .md has been promoted to an active lifecycle
      status (``in-queue`` / ``in-progress`` / ``in-review`` / ``blocked``).
      ``blocked`` is lumped here because proposal-lifecycle tracking cares
      about "past promotion", not current runability.
    - ``DONE`` -- draft completed its lifecycle.
    - ``DECLINED`` -- draft terminated via ``devbench decline``.
    - ``REJECTED`` -- draft was archived by ``reject-proposal`` into
      ``.devbench/rejected-proposals/``; no live .md remains in the tree.
    """

    UNMATERIALISED = "unmaterialised"
    PROPOSED = "proposed"
    PROMOTED = "promoted"
    DONE = "done"
    DECLINED = "declined"
    REJECTED = "rejected"


def classify_proposed_task(backlog_root: Path, workspace_root: Path, suggested_id: str) -> ProposalTaskState:
    """Return the lifecycle state of a proposed-task id.

    Looks first for a live draft .md under ``backlog_root`` via
    ``_find_draft_file``. If present, reads the ``## Status:`` line and
    maps to the appropriate state. If absent, checks the
    ``.devbench/rejected-proposals/`` archive for a matching file to
    distinguish ``REJECTED`` from ``UNMATERIALISED``.

    Args:
        backlog_root: Root of the backlog tree (``<workspace>/backlog``).
        workspace_root: Workspace root (parent of ``backlog/`` and
            ``.devbench/``). Needed because the rejected-proposals archive
            lives under the workspace, not the backlog.
        suggested_id: The ``proposed_tasks[].suggested_id`` from a proposal
            JSON.

    Returns:
        A ``ProposalTaskState`` value. Always returns; never raises for
        missing files (missing means ``UNMATERIALISED`` unless an archive
        entry exists).
    """
    draft = _find_draft_file(backlog_root, suggested_id)
    if draft is None:
        archive_dir = workspace_root / REJECTED_PROPOSAL_DIR_NAME
        if archive_dir.is_dir():
            for archived in archive_dir.glob(f"{suggested_id}-*.md"):
                if archived.is_file():
                    return ProposalTaskState.REJECTED
        return ProposalTaskState.UNMATERIALISED

    status_value = _read_draft_status(draft).lower()
    if status_value == STATUS_PROPOSED:
        return ProposalTaskState.PROPOSED
    if status_value == STATUS_DONE:
        return ProposalTaskState.DONE
    if status_value == STATUS_DECLINED:
        return ProposalTaskState.DECLINED
    # in-queue / in-progress / in-review / blocked all count as PROMOTED:
    # proposal-lifecycle tracking only cares whether the draft has advanced
    # past operator review, not what activity the draft is currently in.
    return ProposalTaskState.PROMOTED


class BlockedTaskState(Enum):
    """Lifecycle classifier for work units that require operator triage (6-state).

    Six buckets ordered by the classifier's decision priority:

    - ``HELD`` -- the unit's own status is ``hold``; a deliberate operator
      pause. No automation will clear it. Operator must resume manually.
    - ``BLOCKED_ON_HELD`` -- the unit is ``blocked`` and carries a
      ``[BLOCKED_PENDING_PROPOSAL]`` marker whose target is in ``hold``.
      The ADR-07 cascade cannot fire while the target is non-terminal and
      ``hold`` is non-terminal. Operator must resume the held target.
    - ``AUTO_CLEARING_VIA_PROPOSAL`` -- carries at least one
      ``[BLOCKED_PENDING_PROPOSAL]`` marker whose targets all exist in the
      backlog AND at least one of which is non-terminal (and not ``hold``).
      The ADR-07 cascade fires the moment every marker target reaches
      ``done`` / ``declined``. Operator does nothing.
    - ``AWAITING_DEPENDENCY`` -- no marker present, but a regular
      Dependencies-table row points at a non-terminal task. The orchestrator
      will unblock this task automatically once the dependency completes.
      Operator does nothing.
    - ``AWAITING_AMENDMENT_RECOVERY`` -- no marker, no regular-dep blocker,
      but at least one recovery signal is present on disk: a pending proposal
      JSON, a rejected-amendment archive, or a recent ``[BLOCKED]`` audit
      comment from a recovery agent (``agent/orchestrator``,
      ``agent/blocker_resolver``, ``agent/manifest_amender``,
      ``agent/backlog_manager``). The orchestrator's next sweep cycle will
      advance the task. Operator does nothing for now.
    - ``OPERATOR_ACTION_REQUIRED`` -- none of the above conditions match:
      no marker, no pending-dep, no recovery signal. Includes manual gates
      (``DO NOT CLAIM``), unknown marker targets, and cascade-stuck states.
      Operator must act.
    """

    AUTO_CLEARING_VIA_PROPOSAL = "auto-clearing"
    AWAITING_AMENDMENT_RECOVERY = "awaiting-amendment-recovery"
    AWAITING_DEPENDENCY = "awaiting-dependency"
    HELD = "held"
    BLOCKED_ON_HELD = "blocked-on-held"
    OPERATOR_ACTION_REQUIRED = "operator-action-required"
    # Issue #183(d): orchestrator runtime degraded (review-supervisor
    # lost Agent-tool access). Distinct from OPERATOR_ACTION_REQUIRED
    # so the operator sees that a ``make start`` restart -- not a code
    # fix -- is what resolves the task.
    RUNTIME_DEGRADATION = "runtime-degradation"


# Recovery audit-comment heuristics. Used by ``classify_blocked_task``
# when no [BLOCKED_PENDING_PROPOSAL] marker is present yet but the
# orchestrator's loop has logged a recent block from one of the recovery
# agents -- that is, devbench WILL run blocker-resolver / task-factory
# on the next iteration. The agent-tag and body-pattern allowlists keep
# the heuristic narrow so unrelated [BLOCKED] comments do not trigger.
_RECOVERY_AGENT_TAGS: frozenset[str] = frozenset(
    {"agent/orchestrator", "agent/blocker_resolver", "agent/manifest_amender", "agent/backlog_manager"}
)
_RECOVERY_BODY_RE: re.Pattern[str] = re.compile(
    r"amendment[- ]reject(?:ed)?"
    r"|out-of-scope"
    r"|ALL_REVIEWS_FAILED|REVIEW_REJECTED"
    r"|dependency .* not yet terminal|dep .* not yet terminal"
    r"|will auto-requeue when",
    re.IGNORECASE,
)
_BLOCKED_AUDIT_RE: re.Pattern[str] = re.compile(
    r"\[(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC)\]\s+\[(?P<agent>[^\]]+)\]\s+\[BLOCKED\]\s+(?P<body>.+)",
)
# Issue #183(d): structured payloads emitted by review-supervisor's
# Step 0 self-check when the Agent tool drops out of the session. The
# orchestrator's runtime is degraded; only an operator-driven
# ``make start`` restart can recover. Matching the exact phrasing the
# agent emits keeps the classifier honest -- unrelated [BLOCKED] rows
# from other agents must not trigger this bucket.
_RUNTIME_DEGRADATION_BODY_RE: re.Pattern[str] = re.compile(
    r"agent-tool-unavailable|review-supervisor[^\n]*only\s+Bash",
    re.IGNORECASE,
)
# Window after which a degradation comment is considered stale. 24h
# matches the default review cycle; a longer-lived degradation marker
# implies the operator already saw the alert and (perhaps) is debugging.
_RUNTIME_DEGRADATION_WINDOW_SECONDS: int = 24 * 60 * 60


def _has_pending_proposal_json(workspace_root: Path, task_id: str) -> bool:
    """Recovery signal #1: blocker-resolver has already written a proposal.

    True iff ``<workspace>/.devbench/proposals/<task_id>.json`` exists.
    Task-factory will materialise + promote it on the next sweep cycle.
    """
    return proposal_path(workspace_root, task_id).is_file()


def _has_rejected_amendment_archive(workspace_root: Path, task_id: str) -> bool:
    """Recovery signal #2: manifest-amender has archived a rejected amendment.

    True iff any ``<workspace>/.devbench/rejected-requests/<task_id>-*.json``
    exists. Blocker-resolver reads the archive on its next iteration to
    decide what fix proposal to emit (see ``REJECTED_REQUESTS_DIR_NAME``
    in ``amendment.py`` -- the canonical constant kept here as a lazy
    import to avoid a circular dependency through the manager module).
    """
    from devbench.backlog.amendment import REJECTED_REQUESTS_DIR_NAME

    archive_dir = workspace_root / REJECTED_REQUESTS_DIR_NAME
    if not archive_dir.is_dir():
        return False
    pattern = f"{task_id}-*.json"
    return any(archive_dir.glob(pattern))


def _has_runtime_degradation_signal(source_file: Path, now: datetime) -> bool:
    """Issue #183(d): True iff the work-unit's Comments section carries a
    recent ``[BLOCKED]`` audit naming the agent-tool degradation
    payload that review-supervisor's Step 0 self-check emits.

    "Recent" means within ``_RUNTIME_DEGRADATION_WINDOW_SECONDS`` (24h)
    of ``now``. A stale payload past the window is treated as already-
    acknowledged by the operator and does NOT trigger this bucket.
    """
    if not source_file.is_file():
        return False
    try:
        content = source_file.read_text(encoding="utf-8")
    except OSError:
        return False
    most_recent_ts: datetime | None = None
    for line in content.splitlines():
        match = _BLOCKED_AUDIT_RE.search(line)
        if match is None:
            continue
        if not _RUNTIME_DEGRADATION_BODY_RE.search(match.group("body")):
            continue
        try:
            ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M UTC").replace(tzinfo=UTC)
        except ValueError:
            continue
        if most_recent_ts is None or ts > most_recent_ts:
            most_recent_ts = ts
    if most_recent_ts is None:
        return False
    return (now - most_recent_ts).total_seconds() <= _RUNTIME_DEGRADATION_WINDOW_SECONDS


def _recent_recovery_audit_comment(source_file: Path, now: datetime, window_seconds: int) -> bool:
    """Recovery signal #3: a recent ``[BLOCKED]`` audit row from a recovery agent.

    Walks the work-unit's Comments section, finds the most recent
    ``[<ts>] [<agent>] [BLOCKED] <body>`` line, and returns True iff the
    timestamp is within ``window_seconds`` of ``now`` AND the agent tag
    is one of the canonical recovery agents AND the body matches the
    recovery-cause regex (``amendment reject`` / ``amendment rejected`` /
    ``amendment-reject`` / ``out-of-scope`` / ``ALL_REVIEWS_FAILED`` /
    ``REVIEW_REJECTED`` / ``will auto-requeue when``). Excludes the
    ``[BLOCKED_PENDING_PROPOSAL]`` marker rows since those represent
    cascade state, not pending recovery.

    Returns False on any failure (file missing, malformed timestamp,
    no Comments section) -- the caller treats that as "no recovery
    signal" rather than masking it with a synthetic state.
    """
    if not source_file.is_file():
        return False
    content = source_file.read_text(encoding="utf-8")
    most_recent: tuple[datetime, str, str] | None = None
    for line in content.splitlines():
        if "[BLOCKED_PENDING_PROPOSAL]" in line:
            continue
        match = _BLOCKED_AUDIT_RE.search(line)
        if match is None:
            continue
        try:
            ts = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M UTC").replace(tzinfo=UTC)
        except ValueError:
            continue
        if most_recent is None or ts > most_recent[0]:
            most_recent = (ts, match.group("agent").strip(), match.group("body").strip())
    if most_recent is None:
        return False
    ts, agent, body = most_recent
    if (now - ts).total_seconds() > window_seconds:
        return False
    if agent not in _RECOVERY_AGENT_TAGS:
        return False
    return bool(_RECOVERY_BODY_RE.search(body))


def classify_blocked_task(
    backlog_root: Path,
    backlog_index: Path,
    task_id: str,
    *,
    workspace_root: Path | None = None,
    now: datetime | None = None,
    recovery_window_seconds: int | None = None,
) -> BlockedTaskState:
    """Classify ``task_id`` into one of the seven blocked-task states.

    Decision priority (first match wins):

    0. ``RUNTIME_DEGRADATION`` -- a recent ``[BLOCKED]`` audit comment
       (within 24h) names ``agent-tool-unavailable`` /
       ``review-supervisor ... only Bash``. The orchestrator runtime is
       degraded; only ``make start`` restart recovers (issue #183).
    1. ``HELD`` -- the task's own status in the backlog index is ``hold``.
    2. ``BLOCKED_ON_HELD`` -- the task carries a ``[BLOCKED_PENDING_PROPOSAL]``
       marker whose target is in ``hold``.
    3. ``AUTO_CLEARING_VIA_PROPOSAL`` -- at least one non-terminal, non-HOLD
       marker target exists; the ADR-07 cascade is in flight.
    4. ``AWAITING_DEPENDENCY`` -- no marker present, but a regular
       Dependencies-table row points at a non-terminal task.
    5. ``AWAITING_AMENDMENT_RECOVERY`` -- no marker, no pending dep, but at
       least one recovery signal is on disk (pending proposal JSON,
       rejected-amendment archive, or a recent ``[BLOCKED]`` audit comment
       from a recovery agent).
    6. ``OPERATOR_ACTION_REQUIRED`` -- none of the above; operator must act.

    The optional ``workspace_root`` enables recovery checks (all three recovery
    signals under ``AWAITING_AMENDMENT_RECOVERY``). Callers that pass only
    ``backlog_root`` + ``backlog_index`` skip the recovery-signal checks and
    fall through directly to ``OPERATOR_ACTION_REQUIRED`` when no marker /
    dep is present.
    """
    source_file = _find_source_task_file(backlog_root, backlog_index, task_id)

    # Priority 0: RUNTIME_DEGRADATION -- the orchestrator runtime is
    # degraded. Checked BEFORE every other bucket so a degraded session
    # is surfaced even if the task also looks held / blocked-on-held;
    # only restarting ``make start`` can let the work resume in any case.
    if source_file is not None and _has_runtime_degradation_signal(
        source_file,
        now if now is not None else datetime.now(UTC),
    ):
        return BlockedTaskState.RUNTIME_DEGRADATION

    # Priority 1: HELD -- the task itself is in hold status.
    if _task_status_is_hold(backlog_root, backlog_index, task_id):
        return BlockedTaskState.HELD

    if source_file is None:
        return BlockedTaskState.OPERATOR_ACTION_REQUIRED

    mgr = BacklogManager()
    marker_ids = mgr._extract_pending_proposal_markers(source_file)

    if marker_ids:
        marker_result = _classify_with_markers(mgr, backlog_index, marker_ids)
        if marker_result is not None:
            return marker_result
        # Fall through: every marker target is terminal (the cascade
        # should have fired and did not), so the marker rows are stale.
        # Consult regular deps + recovery signals before defaulting to
        # operator attention. Without this fall-through, a task with an
        # unrelated unsatisfied regular dep gets misclassified as
        # OPERATOR_ACTION_REQUIRED when it is plainly an
        # AWAITING_DEPENDENCY situation (issue #186).

    # Priority 4: AWAITING_DEPENDENCY -- regular deps still in flight.
    if _regular_deps_unsatisfied(backlog_root, backlog_index, task_id):
        return BlockedTaskState.AWAITING_DEPENDENCY

    # Priority 5 / 6: recovery signals or operator attention.
    return _classify_recovery_or_attention(
        source_file=source_file,
        task_id=task_id,
        workspace_root=workspace_root,
        now=now,
        recovery_window_seconds=recovery_window_seconds,
    )


def _task_status_is_hold(backlog_root: Path, backlog_index: Path, task_id: str) -> bool:
    """Return ``True`` iff ``task_id``'s status in the backlog index is ``hold``.

    Used by ``classify_blocked_task`` as the first-priority check for ``HELD``.
    Falls back to ``False`` on any parse error so a broken index does not mask
    a genuine blocked state.
    """
    try:
        parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError):
        return False
    for unit in units:
        if unit.id == task_id:
            return unit.status.value.lower() == STATUS_HOLD
    return False


def _regular_deps_unsatisfied(backlog_root: Path, backlog_index: Path, task_id: str) -> bool:
    """Return ``True`` iff ``task_id``'s declared dependencies are NOT all terminal.

    ``classify_blocked_task`` consults this after the marker check to keep
    tasks whose regular task-level deps are still running in
    ``AWAITING_DEPENDENCY`` rather than incorrectly promoting them to the
    operator-attention pile.
    Falls back to ``False`` (deps satisfied) when the parser cannot load
    the index -- the broken-index case is reported by validate-backlog and
    must not generate spurious operator alerts here.
    """
    try:
        parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError):
        return False
    units_by_id = {u.id: u for u in units}
    target = units_by_id.get(task_id)
    if target is None:
        return False
    return not BacklogParser._deps_satisfied(target, units_by_id)


def _classify_with_markers(
    mgr: BacklogManager,
    backlog_index: Path,
    marker_ids: set[str],
) -> BlockedTaskState | None:
    """Resolve the marker-present branch.

    Decisive returns (first match wins):

    - ``BLOCKED_ON_HELD`` -- any marker target is in ``hold`` status.
      The cascade cannot fire while the target is non-terminal and HOLD
      is non-terminal; operator must resume the held target.
    - ``OPERATOR_ACTION_REQUIRED`` -- backlog index missing or any
      marker target unknown to the index; the operator must clean up
      the stray reference before any automation can proceed.
    - ``AUTO_CLEARING_VIA_PROPOSAL`` -- at least one non-terminal,
      non-HOLD marker target exists; the ADR-07 cascade is in flight.

    Returns ``None`` when every marker target is terminal -- the marker
    rows are stale and the caller should fall through to the regular-dep
    and recovery-signal checks instead of bouncing the task into the
    operator-attention bucket (issue #186).
    """
    try:
        rows = mgr._parse_backlog_rows(backlog_index)
    except FileNotFoundError:
        return BlockedTaskState.OPERATOR_ACTION_REQUIRED
    status_by_id = {row_id: status for row_id, status, _ in rows if row_id}

    terminal = {STATUS_DONE, STATUS_DECLINED}
    non_terminal_marker_found = False
    for marker in marker_ids:
        if marker not in status_by_id:
            return BlockedTaskState.OPERATOR_ACTION_REQUIRED
        if status_by_id[marker] == STATUS_HOLD:
            return BlockedTaskState.BLOCKED_ON_HELD
        if status_by_id[marker] not in terminal:
            non_terminal_marker_found = True

    if not non_terminal_marker_found:
        # All markers terminal -- stale cascade signal. Let the caller
        # consult regular deps + recovery signals before defaulting to
        # operator attention.
        return None
    return BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL


def _classify_recovery_or_attention(
    *,
    source_file: Path,
    task_id: str,
    workspace_root: Path | None,
    now: datetime | None,
    recovery_window_seconds: int | None,
) -> BlockedTaskState:
    """Resolve AWAITING_AMENDMENT_RECOVERY vs OPERATOR_ACTION_REQUIRED when no marker or dep exists.

    Older callers that pass no ``workspace_root`` get
    ``OPERATOR_ACTION_REQUIRED`` immediately (legacy two-state behaviour).
    The three recovery signals are checked cheapest-first: file-presence
    > glob-match > timestamp-window read of the source file.
    """
    if workspace_root is None:
        return BlockedTaskState.OPERATOR_ACTION_REQUIRED
    if _has_pending_proposal_json(workspace_root, task_id):
        return BlockedTaskState.AWAITING_AMENDMENT_RECOVERY
    if _has_rejected_amendment_archive(workspace_root, task_id):
        return BlockedTaskState.AWAITING_AMENDMENT_RECOVERY
    effective_now = now if now is not None else datetime.now(UTC)
    effective_window = (
        recovery_window_seconds if recovery_window_seconds is not None else DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS
    )
    if _recent_recovery_audit_comment(source_file, effective_now, effective_window):
        return BlockedTaskState.AWAITING_AMENDMENT_RECOVERY
    return BlockedTaskState.OPERATOR_ACTION_REQUIRED


def recovery_signal_for_task(workspace_root: Path, task_id: str) -> str:
    """Return a one-line annotation naming the AWAITING_AMENDMENT_RECOVERY signal source.

    Used by the report renderer to annotate ``AWAITING_AMENDMENT_RECOVERY``
    rows with a ``[recovery: ...]`` suffix so operators see WHY devbench
    thinks the task will recover. Falls back to the audit-comment label
    when neither the pending-proposal JSON nor the rejected-amendment
    archive is present (the classifier already decided this task
    qualifies; this helper only labels the source).
    """
    if _has_pending_proposal_json(workspace_root, task_id):
        return f"pending proposal at .devbench/proposals/{task_id}.json"
    if _has_rejected_amendment_archive(workspace_root, task_id):
        return f"rejected-requests archive at .devbench/rejected-requests/{task_id}-*.json"
    return "recent [BLOCKED] audit comment from a recovery agent"


def _read_draft_status(draft_path: Path) -> str:
    """Read the ``## Status:`` value from a draft .md file.

    Returns the empty string when the file has no ``## Status:`` line
    (malformed draft); callers treat that as ``PROMOTED`` conservatively.
    """
    for line in draft_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## Status:"):
            return stripped[len("## Status:") :].strip()
    return ""


# Matches valid Story IDs such as ``E0-F1-S1``. Used by ``allocate_next_ids``
# to derive the on-disk directory layout (``backlog/E0/E0-F1/E0-F1-S1/``).
_STORY_ID_RE = re.compile(r"^E\d+-F\d+-S\d+$")

# Matches task IDs under a given story (e.g. ``E0-F1-S1-T<N>``).
_TASK_ID_SUFFIX_RE = re.compile(r"^T(\d+)$")

# Matches the ``## Status:`` line and rewrites the value.
_STATUS_LINE_RE = re.compile(r"^(##\s*Status:\s*)(.+)$", re.MULTILINE)

# Captures backlog-index rows: ``| ID | ... |``.
_BACKLOG_ROW_RE = re.compile(r"^\|\s*(\S+)\s*\|", re.MULTILINE)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedTask:
    """One task the blocker-resolver wants the factory to draft."""

    suggested_id: str
    title: str
    files_to_own: list[str]
    linked_scenarios: list[str]
    suggested_acs: list[str]
    suggested_approach: str


@dataclass(frozen=True)
class Proposal:
    """Complete payload emitted by blocker-resolver after an amendment reject.

    ``affected_task_ids`` (ADR-10) lists peer tasks that share the same underlying
    blocker as ``source_task_id``. When ``promote-proposal`` runs, the
    ``[BLOCKED_PENDING_PROPOSAL]`` marker + Dependencies-table row is written on
    the source AND on every id in ``affected_task_ids``, so the ADR-07 cascade
    auto-unblocks each of them when the fix completes. Field is optional; empty
    list preserves pre-ADR-10 1:1 wiring.
    """

    source_task_id: str
    generated_at: str
    rejection_reason: str
    proposed_tasks: list[ProposedTask]
    affected_task_ids: list[str] = field(default_factory=list)
    source_dep_direction: str = ""
    # Issue #141: stable hash over (target_repo, sorted(files_to_own),
    # normalised intent_phrase) -- empty string for proposals authored
    # before the dedup feature shipped. Set by cmd_write_proposal at
    # emission time so the next blocker-resolver invocation finds a
    # match cheaply.
    fix_signature: str = ""
    # Issue #144: depth in the recovery cascade. Depth 0 = first-class
    # recovery (the source task is a "real" backlog task that surfaced a
    # blocker). Depth N+1 = parent's depth + 1. Configurable cap via
    # `orchestrate.max_cascade_depth` YAML field.
    cascade_depth: int = 0

    def to_dict(self) -> dict:
        """JSON-serialisable form used for on-disk storage."""
        return {
            "source_task_id": self.source_task_id,
            "generated_at": self.generated_at,
            "rejection_reason": self.rejection_reason,
            "proposed_tasks": [asdict(t) for t in self.proposed_tasks],
            "affected_task_ids": list(self.affected_task_ids),
            "source_dep_direction": self.source_dep_direction,
            "fix_signature": self.fix_signature,
            "cascade_depth": self.cascade_depth,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Proposal:
        """Build a :class:`Proposal` from a loaded JSON dict. Raises ``ValueError`` on schema errors."""
        if not isinstance(data, dict):
            raise ValueError(f"Proposal JSON must be an object, got {type(data).__name__}")
        for key in ("source_task_id", "generated_at", "rejection_reason", "proposed_tasks"):
            if key not in data:
                raise ValueError(f"Proposal JSON missing required field: {key}")
        tasks_raw = data["proposed_tasks"]
        if not isinstance(tasks_raw, list):
            raise ValueError("Proposal.proposed_tasks must be a list")
        tasks: list[ProposedTask] = []
        for entry in tasks_raw:
            if not isinstance(entry, dict):
                raise ValueError("Each entry in Proposal.proposed_tasks must be an object")
            required = (
                "suggested_id",
                "title",
                "files_to_own",
                "linked_scenarios",
                "suggested_acs",
                "suggested_approach",
            )
            for field_name in required:
                if field_name not in entry:
                    raise ValueError(f"Proposed task missing required field: {field_name}")
            tasks.append(
                ProposedTask(
                    suggested_id=str(entry["suggested_id"]).strip(),
                    title=str(entry["title"]).strip(),
                    files_to_own=[str(x) for x in entry["files_to_own"]],
                    linked_scenarios=[str(x) for x in entry["linked_scenarios"]],
                    suggested_acs=[str(x) for x in entry["suggested_acs"]],
                    suggested_approach=str(entry["suggested_approach"]),
                )
            )

        source_id = str(data["source_task_id"]).strip()
        affected = _parse_affected_task_ids(data.get("affected_task_ids", []), source_id)
        # source_dep_direction is optional; "" preserves default behavior
        # (source.depends_on(new)). "test_validates_source" inverts the
        # auto-wired dep so the test waits on the source. See
        # docs/task-factory.md "When to use --no-dep-on-source".
        source_dep_direction = str(data.get("source_dep_direction", "")).strip()
        if source_dep_direction not in ("", "test_validates_source"):
            raise ValueError(
                f"Proposal.source_dep_direction must be empty or 'test_validates_source'; got {source_dep_direction!r}"
            )

        # Issue #141 / #144: optional fix_signature + cascade_depth. Absent
        # in proposals authored before these features shipped (forward-compat).
        fix_signature = str(data.get("fix_signature", "") or "")
        cascade_depth_raw = data.get("cascade_depth", 0)
        try:
            cascade_depth = int(cascade_depth_raw or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Proposal.cascade_depth must be a non-negative integer; got {cascade_depth_raw!r}"
            ) from exc
        if cascade_depth < 0:
            raise ValueError(f"Proposal.cascade_depth must be >= 0; got {cascade_depth}")

        return cls(
            source_task_id=source_id,
            generated_at=str(data["generated_at"]),
            rejection_reason=str(data["rejection_reason"]),
            proposed_tasks=tasks,
            affected_task_ids=affected,
            source_dep_direction=source_dep_direction,
            fix_signature=fix_signature,
            cascade_depth=cascade_depth,
        )


def _parse_affected_task_ids(raw: object, source_id: str) -> list[str]:
    """Validate + normalise the ``affected_task_ids`` field of a proposal JSON.

    Extracted from ``Proposal.from_dict`` to keep that classmethod's branch
    count under the project's complexity ceiling. Raises ``ValueError`` with
    a message naming the offending entry on any schema violation.
    """
    if not isinstance(raw, list):
        raise ValueError("Proposal.affected_task_ids must be a list of task IDs")
    affected: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"Proposal.affected_task_ids entry must be a string, got {type(item).__name__}")
        normalised = item.strip()
        if not normalised:
            raise ValueError("Proposal.affected_task_ids contains an empty entry")
        if normalised == source_id:
            raise ValueError(
                f"Proposal.affected_task_ids duplicates source_task_id '{source_id}'; "
                "the source is always wired, so it must not appear in affected_task_ids"
            )
        if normalised in seen:
            raise ValueError(f"Proposal.affected_task_ids contains duplicate entry '{normalised}'")
        seen.add(normalised)
        affected.append(normalised)
    return affected


class ProposalError(RuntimeError):
    """Raised when a proposal cannot be processed."""


class CascadeDepthError(RuntimeError):
    """Raised when a proposal would exceed ``orchestrate.max_cascade_depth``.

    Issue #144. Caught by ``cmd_materialise_proposal`` to escalate the
    source task to ``OPERATOR_ACTION_REQUIRED`` instead of materialising
    another recovery layer. Living-on-its-own exception class so callers
    can ``except CascadeDepthError`` without coupling to the broader
    ``ProposalError`` family.
    """


# ---------------------------------------------------------------------------
# Dedup-signature helpers (issue #141)
# ---------------------------------------------------------------------------

# Verbs that signal "fix the spec / Manifest / placeholder" so the intent
# phrase normalises to a stable key. Order matters: longer matches first
# so "drop-row" beats "drop". Each entry maps a regex pattern (matched
# against lower-cased Approach text) -> normalised intent token.
_INTENT_VERB_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bremove\s+the\s+\S+\s+row\b", "remove-row"),
    (r"\bdelete\s+the\s+\S+\s+entry\b", "delete-entry"),
    (r"\bdrop\s+the\s+conflicting\s+manifest\s+row\b", "drop-conflict-row"),
    (r"\bcorrect\s+the\s+manifest\s+table\b", "correct-manifest"),
    (r"\bfix\s+the\s+changes\s+manifest\b", "fix-manifest"),
    (r"\buntrack\s+\S+", "untrack"),
    (r"\badd\s+\S+\s+to\s+\.gitignore\b", "gitignore-add"),
    (r"\bregister\s+\S+\s+marker\b", "register-marker"),
    (r"\bfix\s+\S+\s+placeholder\b", "fix-placeholder"),
    (r"\bremove\s+\S+", "remove"),
    (r"\bdelete\s+\S+", "delete"),
    (r"\bfix\s+\S+", "fix"),
    (r"\badd\s+\S+", "add"),
)


def _extract_intent_phrase(approach_text: str) -> str:
    """Return a normalised verb-noun token for the proposal's Approach text.

    Pure regex; no LLM. Used as part of the dedup ``fix_signature`` so
    two recovery proposals with the same target_repo + files_to_own +
    semantic intent collapse to the same signature.

    Falls back to ``"generic"`` when no verb pattern matches; that keeps
    the signature stable for proposals whose Approach is too vague to
    classify rather than producing an unhashable ``None``.
    """
    if not approach_text:
        return "generic"
    text = approach_text.lower()
    for pattern, token in _INTENT_VERB_PATTERNS:
        if re.search(pattern, text):
            return token
    return "generic"


def _compute_fix_signature(target_repo: str, files_to_own: list[str], intent_phrase: str) -> str:
    """Compute the dedup signature (issue #141).

    SHA-256 over the canonical tuple
    ``(target_repo, sorted(files_to_own), intent_phrase)``. Returns the
    full 64-char hex digest; callers truncate to the first 16 chars when
    rendering for human display, but the full digest is stored on disk
    so collision risk is operationally negligible.

    Pure function: deterministic, no I/O, no clock or random source.
    """
    canonical = json.dumps(
        {
            "target_repo": target_repo,
            "files_to_own": sorted(files_to_own),
            "intent_phrase": intent_phrase,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProposalMatch:
    """Result of ``find_matching_pending_proposal``.

    Attributes:
        proposal_path: Absolute path to the matching proposal JSON on disk.
        source_task_id: The source-task ID associated with the existing
            proposal (the task that already has its dep edge wired by
            promote-proposal). Callers add their new source task as an
            additional dep edge pointing at this ID.
        fix_signature: The matching signature (echo of the query).
    """

    proposal_path: Path
    source_task_id: str
    fix_signature: str


def find_matching_pending_proposal(workspace_root: Path, signature: str) -> ProposalMatch | None:
    """Scan ``.devbench/proposals/*.json`` for a proposal whose
    ``fix_signature`` matches ``signature``. Returns the first match (or
    None when no match exists).

    Issue #141: this is the generalisation of the orphan-cleanup reuse
    pattern at ``cli.py:1918-1957``. The orphan path scans for an
    overlapping detection set; this generalised path scans for a stable
    structural hash. Both share the same operator-visible outcome:
    instead of emitting a duplicate recovery task, the new source task
    gets a dep edge wired pointing at the existing recovery.

    Pure read; never mutates disk. Stale proposals on disk whose source
    task is in a terminal state (``done`` / ``declined``) are skipped --
    determined by re-reading the source task's `## Status:` line. Empty
    or invalid signatures (the empty string) never match.
    """
    if not signature:
        return None
    proposals_dir = workspace_root / ".devbench" / "proposals"
    if not proposals_dir.is_dir():
        return None
    for proposal_file in sorted(proposals_dir.glob("*.json")):
        try:
            payload = json.loads(proposal_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidate_sig = payload.get("fix_signature", "")
        if candidate_sig != signature:
            continue
        source_id = str(payload.get("source_task_id", "")).strip()
        if not source_id:
            continue
        # Skip if the existing source task is in a terminal state -- the
        # proposal is stale (operator declined; or the proposal was
        # never promoted and the source task moved on).
        if _source_task_in_terminal_state(workspace_root, source_id):
            continue
        return ProposalMatch(
            proposal_path=proposal_file.resolve(),
            source_task_id=source_id,
            fix_signature=signature,
        )
    return None


def _source_task_in_terminal_state(workspace_root: Path, source_task_id: str) -> bool:
    """Return True iff ``<workspace>/backlog/.../<source_task_id>.md``
    has ``## Status: done`` or ``## Status: declined``. Best-effort -- on
    any read error, returns False so the dedup scanner does not skip a
    legitimately-pending proposal."""
    backlog_root = workspace_root / "backlog"
    if not backlog_root.is_dir():
        return False
    for candidate in backlog_root.rglob(f"{source_task_id}.md"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("## status:"):
                status = stripped.split(":", 1)[1].strip()
                return status in {"done", "declined"}
        return False
    return False


def enforce_cascade_depth(proposal_payload: dict, max_depth: int) -> None:
    """Issue #144: raise ``CascadeDepthError`` when the proposal's
    ``cascade_depth`` is at or above ``max_depth``.

    Pure validator; called by ``cmd_materialise_proposal`` before writing
    a draft. ``max_depth`` is resolved env > YAML > default
    (``DEFAULT_MAX_CASCADE_DEPTH``) by the caller; this helper assumes
    ``max_depth >= 1`` (schema enforces).
    """
    raw_depth = proposal_payload.get("cascade_depth", 0)
    try:
        depth = int(raw_depth or 0)
    except (TypeError, ValueError) as exc:
        raise CascadeDepthError(f"proposal cascade_depth must be an integer; got {raw_depth!r}") from exc
    if depth >= max_depth:
        raise CascadeDepthError(
            f"proposal cascade_depth={depth} reached or exceeded "
            f"orchestrate.max_cascade_depth={max_depth}; escalating to "
            "operator attention instead of materialising another recovery layer"
        )


# ---------------------------------------------------------------------------
# TODO/TBD placeholder rejection (issue #143)
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERNS: tuple[str, ...] = ("todo", "tbd")


def detect_placeholder_descriptions(proposal: Proposal) -> list[str]:
    """Return a list of human-readable error fragments for every
    proposed-task description that is empty / TODO / TBD / whitespace.

    Issue #143: ``cmd_materialise_proposal`` calls this BEFORE writing a
    draft. Empty list = clean to materialise. Non-empty list = reject
    with the joined messages.

    The check inspects ``ProposedTask.suggested_approach`` (the
    "description" surface in our schema -- the Approach text becomes the
    work-unit's description). It does NOT re-check Manifest rows here;
    those are auto-generated from ``files_to_own`` at materialisation
    time, and the existing ``validate-backlog`` rule (issue #117)
    already catches the "TODO -- describe change" pattern in the
    rendered Manifest table on the next sweep.
    """
    issues: list[str] = []
    for task in proposal.proposed_tasks:
        approach = task.suggested_approach.strip()
        if not approach:
            issues.append(f"task {task.suggested_id}: suggested_approach is empty / whitespace-only")
            continue
        lowered = approach.lower()
        if any(lowered == p or lowered.startswith(p + " ") for p in _PLACEHOLDER_PATTERNS):
            issues.append(
                f"task {task.suggested_id}: suggested_approach is a placeholder ({approach[:60]!r}); "
                "fill in a concrete description before promoting"
            )
    return issues


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def proposal_path(workspace_root: Path, source_task_id: str) -> Path:
    """Return the on-disk path for the blocker-resolver's proposal JSON."""
    return workspace_root / PROPOSAL_DIR_NAME / f"{source_task_id}.json"


# ---------------------------------------------------------------------------
# Concurrency-safe ID allocator
# ---------------------------------------------------------------------------


@contextmanager
def _id_allocation_lock(workspace_root: Path) -> Iterator[None]:
    """Acquire an exclusive POSIX file lock on ``.devbench/task-factory.lock``.

    Released even if the wrapped block raises. POSIX-only (Linux / macOS);
    DevBench does not claim Windows support today.
    """
    lock_path = workspace_root / LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _story_dir(backlog_root: Path, story_id: str) -> Path:
    """Return the filesystem path that holds the given story's task files."""
    if not _STORY_ID_RE.match(story_id):
        raise ProposalError(f"story_id {story_id!r} is not a valid Story ID (expected ``E<N>-F<N>-S<N>``)")
    parts = story_id.split("-")
    return backlog_root / parts[0] / "-".join(parts[:2]) / story_id


def scan_story_for_task_ids(backlog_root: Path, story_id: str) -> set[str]:
    """Return every existing task ID under ``story_id`` as declared by the filesystem."""
    story_dir = _story_dir(backlog_root, story_id)
    if not story_dir.is_dir():
        return set()
    ids: set[str] = set()
    prefix = f"{story_id}-"
    for path in story_dir.iterdir():
        if not path.is_file() or path.suffix != ".md":
            continue
        stem = path.stem
        if stem.startswith(prefix):
            ids.add(stem)
    return ids


def allocate_next_ids(workspace_root: Path, backlog_root: Path, story_id: str, count: int) -> list[str]:
    """Return the next ``count`` free task IDs under ``story_id`` atomically.

    Uses a POSIX file lock to serialise concurrent factory runs. The IDs are
    returned in ascending ``T<N>`` order; the highest existing task number
    (from the filesystem listing) is incremented by one for each new ID.
    """
    if count < 1:
        raise ProposalError(f"count must be >= 1, got {count}")
    with _id_allocation_lock(workspace_root):
        existing = scan_story_for_task_ids(backlog_root, story_id)
        max_task_num = 0
        for tid in existing:
            suffix = tid.rsplit("-", 1)[-1]
            match = _TASK_ID_SUFFIX_RE.match(suffix)
            if match is not None:
                max_task_num = max(max_task_num, int(match.group(1)))
        return [f"{story_id}-T{max_task_num + i + 1}" for i in range(count)]


# ---------------------------------------------------------------------------
# Proposal I/O
# ---------------------------------------------------------------------------


def write_proposal(workspace_root: Path, proposal: Proposal) -> Path:
    """Persist ``proposal`` to the pending-proposals directory."""
    target = proposal_path(workspace_root, proposal.source_task_id)
    if target.exists():
        raise ProposalError(
            f"Proposal already exists for source task {proposal.source_task_id} at {target}. "
            "Resolve or reject its tasks before generating new proposals."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proposal.to_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def read_proposal(workspace_root: Path, source_task_id: str) -> Proposal:
    """Load a pending proposal from disk, raising :class:`ProposalError` on errors."""
    target = proposal_path(workspace_root, source_task_id)
    if not target.exists():
        raise ProposalError(f"No proposal for source task {source_task_id} at {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProposalError(f"Proposal for {source_task_id} is not valid JSON: {exc}") from exc
    try:
        return Proposal.from_dict(raw)
    except ValueError as exc:
        raise ProposalError(f"Proposal for {source_task_id} is invalid: {exc}") from exc


def delete_proposal(workspace_root: Path, source_task_id: str) -> None:
    """Delete the pending proposal JSON, if it exists."""
    target = proposal_path(workspace_root, source_task_id)
    if target.exists():
        target.unlink()


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------


DRAFT_TEMPLATE: str = """\
# {task_id}: {title}

## Status: {status}

## Target Repository

- **Repo:** `{repo}`
- **Branch:** `backlog/{task_id_lower}`

## Description

<!-- auto-generated by task-factory on {generated_at} from proposal for {source_task_id}.
     Review and edit before promoting to in-queue. -->

{approach}

### Related Scenarios

{linked_scenarios}

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

{acceptance_criteria}

## Changes Manifest

| File | Change |
|------|--------|
{changes_manifest}

## Definition of Done

- [ ] All acceptance criteria checked
- [ ] Tests green
- [ ] Lint and format clean
- [ ] Only files in Changes Manifest are staged with `git add`

## TDD Cycle Log

## Comments
"""


def generate_draft_md(
    proposed: ProposedTask,
    *,
    repo: str,
    source_task_id: str,
    generated_at: str,
    status: str = STATUS_PROPOSED,
) -> str:
    """Render the markdown content for one proposed task file."""
    scenarios = ", ".join(proposed.linked_scenarios) if proposed.linked_scenarios else "(none documented)"
    ac_lines = (
        "\n".join(f"- [ ] {line}" for line in proposed.suggested_acs)
        if proposed.suggested_acs
        else "- [ ] AC-TODO-001 human must author AC"
    )
    manifest_lines = (
        "\n".join(f"| `{path}` | TODO -- describe change |" for path in proposed.files_to_own)
        if proposed.files_to_own
        else "| `TODO` | TODO -- describe change |"
    )
    return DRAFT_TEMPLATE.format(
        task_id=proposed.suggested_id,
        task_id_lower=proposed.suggested_id.lower(),
        title=proposed.title,
        repo=repo,
        status=status,
        generated_at=generated_at,
        source_task_id=source_task_id,
        approach=proposed.suggested_approach.strip() or "TODO -- human must author approach",
        linked_scenarios=scenarios,
        acceptance_criteria=ac_lines,
        changes_manifest=manifest_lines,
    )


# ---------------------------------------------------------------------------
# BACKLOG.md row manipulation
# ---------------------------------------------------------------------------


def _render_backlog_row(task_id: str, title: str, status: str, repo: str, rel_path: str) -> str:
    """Return one BACKLOG.md Full Work Unit Index row for the given task."""
    return f"| {task_id} | {title} | Task | {status} | None | {repo} | `{rel_path}` |\n"


def _append_backlog_row(backlog_index: Path, row: str) -> None:
    """Append ``row`` to the Full Work Unit Index table in ``backlog_index``."""
    content = backlog_index.read_text(encoding="utf-8")
    marker = "## Full Work Unit Index"
    if marker not in content:
        raise ProposalError(f"BACKLOG.md at {backlog_index} has no '## Full Work Unit Index' section")
    # Append at EOF (end of index block); after every existing row the
    # Status Summary regeneration will pick this up.
    content = content.rstrip("\n") + "\n" + row
    backlog_index.write_text(content, encoding="utf-8")


def _remove_backlog_row(backlog_index: Path, task_id: str) -> None:
    """Remove any row for ``task_id`` from the Full Work Unit Index."""
    content = backlog_index.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    removed = False
    for line in lines:
        match = _BACKLOG_ROW_RE.match(line)
        if match is not None and match.group(1) == task_id:
            removed = True
            continue
        kept.append(line)
    if not removed:
        raise ProposalError(f"Row for {task_id} not found in {backlog_index}")
    backlog_index.write_text("".join(kept), encoding="utf-8")


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def materialise_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    proposal: Proposal,
    repo: str,
) -> list[Path]:
    """Write every proposed task as a draft ``.md`` and insert a row in ``BACKLOG.md``.

    Returns the list of materialised draft files. The caller is responsible
    for asserting that the proposal's source task is in ``blocked`` state
    beforehand; task-factory runs exclusively from that path.

    Refuses (with ``ProposalError``) when any proposed task's
    ``suggested_approach`` is too terse to produce a useful draft. The
    threshold is a module-level constant; blocker-resolver's prompt
    (``plugin/devbench/agents/blocker-resolver.md``) requires the
    Context / Scope / TDD approach / Verify four-section structure whose
    minimum honest length exceeds the threshold. Drafts below the floor
    always require operator hand-editing before promotion, so stopping
    them at the materialise boundary surfaces the defect upstream in
    blocker-resolver where it can be fixed once, instead of downstream
    every time a thin draft lands.

    The ``## Status:`` line in every new draft file and the corresponding
    BACKLOG.md row are set to
    ``RUNTIME_CONFIG.backlog.default_status_for_new_work_units`` (AC-189-8).
    When the config key is absent the dataclass default (``in-queue``) is
    used, preserving backwards compatibility for workspaces that have not
    opted in to the ``backlog:`` YAML section (AC-189-9).
    """
    # Read the configured default status for new work units once per call.
    # The lazy _get_runtime_config() helper is monkeypatched in tests to avoid
    # loading the real config from disk.
    runtime_cfg = _get_runtime_config()
    new_wu_status: str = runtime_cfg.backlog.default_status_for_new_work_units
    if new_wu_status not in _ALLOWED_NEW_WU_STATUSES:
        raise ProposalError(
            f"backlog.default_status_for_new_work_units has invalid value {new_wu_status!r}; "
            f"allowed values are {sorted(_ALLOWED_NEW_WU_STATUSES)!r}. "
            "Update backlog/config/devbench.yaml to use 'in-queue' or 'draft'."
        )
    # Thin-approach refusal -- fail fast before any file write so a partial
    # materialisation cannot leave the backlog half-written. Applies to the
    # whole proposal, even if some tasks are already resolved, because a
    # JSON with thin content should not be accepted regardless of which
    # specific tasks would be created on this call.
    for proposed in proposal.proposed_tasks:
        approach = (proposed.suggested_approach or "").strip()
        if len(approach) < _SUGGESTED_APPROACH_MIN_CHARS:
            raise ProposalError(
                f"suggested_approach too terse for {proposed.suggested_id} "
                f"({len(approach)} chars, minimum {_SUGGESTED_APPROACH_MIN_CHARS}); "
                "re-run blocker-resolver with the Context / Scope / TDD approach / Verify "
                "four-section structure documented in blocker-resolver.md."
            )

    # Classify every task up front so we know which ones actually need
    # creating. The classifier is the single source of truth for proposal
    # lifecycle state -- it reads the backlog tree AND the
    # rejected-proposals/ archive, so a previously-rejected draft classifies
    # as REJECTED (not UNMATERIALISED) and is skipped here.
    classifications = [
        (proposed, classify_proposed_task(backlog_root, workspace_root, proposed.suggested_id))
        for proposed in proposal.proposed_tasks
    ]
    needs_create = any(state is ProposalTaskState.UNMATERIALISED for _, state in classifications)

    # Unresolved-prior-proposals guard applies only when this call would
    # actually create new `proposed` rows. Exclude this proposal's own
    # task IDs so a partial re-materialise (some tasks already PROPOSED)
    # doesn't see itself as the blocker.
    if needs_create:
        exclude = frozenset(t.suggested_id for t in proposal.proposed_tasks)
        if _has_unresolved_proposals(backlog_index, exclude_task_ids=exclude):
            raise ProposalError(
                "Skipped proposal generation -- unresolved proposed tasks already exist. "
                "Resolve those via promote-proposal or reject-proposal before generating new proposals."
            )

    drafts: list[Path] = []
    mgr = BacklogManager()
    for proposed, state in classifications:
        if state is not ProposalTaskState.UNMATERIALISED:
            # Classify-aware skip. The task is already in one of the
            # terminal-for-materialise states: a draft exists (PROPOSED,
            # PROMOTED, DONE, DECLINED) or a reject archive exists
            # (REJECTED). Recreating the draft would resurrect rejected
            # work or duplicate in-flight work.
            logger.info(
                "materialise_proposal: skipping %s in state %s "
                "(already materialised, rejected, promoted, done, or declined)",
                proposed.suggested_id,
                state.value,
            )
            continue
        story_id = _extract_story_id(proposed.suggested_id)
        story_dir = _story_dir(backlog_root, story_id)
        story_dir.mkdir(parents=True, exist_ok=True)
        target = story_dir / f"{proposed.suggested_id}.md"
        content = generate_draft_md(
            proposed,
            repo=repo,
            source_task_id=proposal.source_task_id,
            generated_at=proposal.generated_at,
            status=new_wu_status,
        )
        target.write_text(content, encoding="utf-8")
        rel_path = target.relative_to(workspace_root).as_posix()
        row = _render_backlog_row(
            task_id=proposed.suggested_id,
            title=proposed.title,
            status=new_wu_status,
            repo=repo,
            rel_path=rel_path,
        )
        _append_backlog_row(backlog_index, row)
        drafts.append(target)
        logger.info("Materialised proposed task %s -> %s", proposed.suggested_id, target)
    if drafts:
        # Rebuild Status Summary counts so the `proposed` rows appear.
        # Skipped entirely when no drafts were created -- the summary is
        # already correct.
        mgr._update_status_summary(backlog_index)
    return drafts


def _has_unresolved_proposals(backlog_index: Path, *, exclude_task_ids: frozenset[str] = frozenset()) -> bool:
    """Return True when any row in BACKLOG.md already carries status ``proposed``.

    ``exclude_task_ids`` skips rows whose ID is in the set, which lets
    ``materialise_proposal`` re-run on an already-partially-materialised
    proposal without the guard misfiring on the proposal's own tasks.
    """
    if not backlog_index.is_file():
        return False
    for line in backlog_index.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5:
            continue
        task_id = cells[1] if len(cells) >= 2 else ""
        if task_id in exclude_task_ids:
            continue
        status_cell = cells[4].lower() if len(cells) >= 5 else ""
        if status_cell == STATUS_PROPOSED:
            return True
    return False


def _extract_story_id(task_id: str) -> str:
    """Return the Story portion of a task ID, e.g. ``E0-F1-S1-T3`` -> ``E0-F1-S1``."""
    parts = task_id.split("-")
    if len(parts) < 4:
        raise ProposalError(f"Cannot derive story_id from task_id {task_id!r}")
    return "-".join(parts[:3])


# ---------------------------------------------------------------------------
# Promote / reject
# ---------------------------------------------------------------------------


def list_proposals(workspace_root: Path) -> list[Proposal]:
    """Return every pending proposal under ``<workspace_root>/.devbench/proposals/``."""
    proposals_dir = workspace_root / PROPOSAL_DIR_NAME
    if not proposals_dir.is_dir():
        return []
    out: list[Proposal] = []
    for path in sorted(proposals_dir.iterdir()):
        if path.suffix != ".json" or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            out.append(Proposal.from_dict(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Skipping unreadable proposal %s: %s", path, exc)
    return out


def _find_draft_file(backlog_root: Path, task_id: str) -> Path | None:
    """Return the draft .md path for a proposed task ID, or ``None`` if missing."""
    story_id = _extract_story_id(task_id)
    story_dir = _story_dir(backlog_root, story_id)
    target = story_dir / f"{task_id}.md"
    return target if target.is_file() else None


def _rewrite_status(md_path: Path, new_status: str) -> None:
    """Replace the ``## Status:`` value in a draft file."""
    content = md_path.read_text(encoding="utf-8")
    if not _STATUS_LINE_RE.search(content):
        raise ProposalError(f"Draft {md_path} has no '## Status:' line")
    content = _STATUS_LINE_RE.sub(rf"\g<1>{new_status}", content, count=1)
    md_path.write_text(content, encoding="utf-8")


def _rewrite_backlog_status(backlog_index: Path, task_id: str, new_status: str) -> None:
    """Replace the status column in the BACKLOG.md row for ``task_id``."""
    content = backlog_index.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated = False
    for i, line in enumerate(lines):
        match = _BACKLOG_ROW_RE.match(line)
        if match is None or match.group(1) != task_id:
            continue
        cells = line.split("|")
        # Cells layout: ['', ' ID ', ' Title ', ' Type ', ' Status ', ' Deps ', ' Repo ', ' Path ', '']
        if len(cells) >= 5:
            cells[4] = f" {new_status} "
            lines[i] = "|".join(cells)
            updated = True
            break
    if not updated:
        raise ProposalError(f"Row for {task_id} not found in {backlog_index}")
    backlog_index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_dependency_to_source(backlog_root: Path, backlog_index: Path, source_task_id: str, new_dep_id: str) -> None:
    """Append ``new_dep_id`` to ``source_task_id``'s Dependencies table."""
    source_unit = _find_source_task_file(backlog_root, backlog_index, source_task_id)
    if source_unit is None:
        raise ProposalError(f"Cannot find source task file for {source_task_id}")
    content = source_unit.read_text(encoding="utf-8")
    marker = "## Dependencies"
    idx = content.find(marker)
    if idx == -1:
        raise ProposalError(f"Source task file {source_unit} has no '## Dependencies' section")
    # Find the end of the Dependencies section and append a row inside it.
    next_section = content.find("\n## ", idx + 1)
    section = content[idx : next_section if next_section != -1 else len(content)]
    remainder = content[next_section:] if next_section != -1 else ""
    # Replace a placeholder ``| none | | |`` row when the dependencies table
    # is currently empty; otherwise append a new row to the end of the table.
    none_row_re = re.compile(r"^\|\s*none\s*\|\s*\|\s*\|\s*$", re.IGNORECASE | re.MULTILINE)
    if none_row_re.search(section):
        section = none_row_re.sub(f"| {new_dep_id} | (auto) | proposed |", section, count=1)
    else:
        section = section.rstrip("\n") + f"\n| {new_dep_id} | (auto) | proposed |\n"
    source_unit.write_text(content[:idx] + section + remainder, encoding="utf-8")


def _find_source_task_file(backlog_root: Path, backlog_index: Path, task_id: str) -> Path | None:
    """Locate the .md file for an arbitrary task ID by walking the backlog tree."""
    parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
    try:
        units = parser.parse_index()
    except (FileNotFoundError, ValueError):
        return None
    for unit in units:
        if unit.id == task_id:
            return unit.file_path
    return None


@dataclass(frozen=True)
class PromoteResult:
    """Return value of :func:`promote_proposal`.

    - ``draft_path`` -- file path of the promoted draft (backward-compatible
      with pre-ADR-10 callers that expected a ``Path``).
    - ``wired_targets`` -- ordered list of task IDs that received the
      ``[BLOCKED_PENDING_PROPOSAL]`` marker. Always starts with the source
      task ID (when ``dep_on_source=True``); every entry from
      :attr:`Proposal.affected_task_ids` follows in declared order.
    """

    draft_path: Path
    wired_targets: list[str]


def _find_originating_proposal(workspace_root: Path, promoted_task_id: str) -> Proposal | None:
    """Return the ``Proposal`` that authored ``promoted_task_id``, or ``None``.

    Companion to :func:`_find_originating_source_task` that surfaces the full
    payload so callers can read :attr:`Proposal.affected_task_ids` without a
    second disk read.
    """
    for proposal in list_proposals(workspace_root):
        for task in proposal.proposed_tasks:
            if task.suggested_id == promoted_task_id:
                return proposal
    return None


def promote_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    task_id: str,
    dep_on_source: bool = True,
    audit_suffix: str = "",
) -> PromoteResult:
    """Flip a proposed task to ``in-queue`` and wire it as a source + affected-task dep.

    Wiring targets (ADR-10): ``[source_task_id] + affected_task_ids`` from the
    originating proposal, deduplicated, order-preserved. Every target receives
    the ``[BLOCKED_PENDING_PROPOSAL] <task_id>`` marker on its Comments section
    so the ADR-07 cascade can auto-unblock each of them when ``task_id``
    reaches a terminal state.

    ``dep_on_source=False`` skips adding the Dependencies-table row on the
    source task only. Every entry in ``affected_task_ids`` still gets its row
    and marker -- the flag addresses the narrow case where the promoted draft
    is independent of its source, not peer tasks that an author explicitly
    listed as affected.

    Fail-fast: if any target in the computed list is not present in the
    backlog index, the function raises :class:`ProposalError` BEFORE writing
    anything so a partial wiring cannot happen.

    Returns :class:`PromoteResult` carrying the draft path and the list of
    wired task IDs (for CLI output + audit).
    """
    draft = _find_draft_file(backlog_root, task_id)
    if draft is None:
        raise ProposalError(f"No draft file for proposed task {task_id}")
    _rewrite_status(draft, STATUS_IN_QUEUE)
    _rewrite_backlog_status(backlog_index, task_id, STATUS_IN_QUEUE)
    # Refresh Status Summary counts after the status flip.
    BacklogManager()._update_status_summary(backlog_index)

    wired_targets: list[str] = []
    proposal = _find_originating_proposal(workspace_root, task_id)
    if proposal is not None:
        # Compute the dedup'd, order-preserved target list.
        targets: list[str] = [proposal.source_task_id]
        for extra in proposal.affected_task_ids:
            if extra not in targets:
                targets.append(extra)

        # Fail-fast: every affected target must exist in the backlog.
        # _find_originating_proposal always returns the actual source so it
        # is guaranteed to be present; we only need to validate the affected
        # entries. Validation happens BEFORE any write so a missing peer does
        # not leave the source half-wired.
        for target_id in targets[1:]:
            if _find_source_task_file(backlog_root, backlog_index, target_id) is None:
                raise ProposalError(
                    f"promote-proposal {task_id}: affected target '{target_id}' "
                    "not found in backlog index; cannot wire dependency."
                )

        # Now wire every target. Writes are idempotent-per-file individually
        # (the helpers append but do not duplicate existing rows/markers).
        for target_id in targets:
            source_file = _find_source_task_file(backlog_root, backlog_index, target_id)
            if source_file is None:
                # Source itself was not in index; back-compat with pre-ADR-10
                # tests that build minimal fixtures without a full backlog.
                continue
            if dep_on_source or target_id != proposal.source_task_id:
                _append_dependency_to_source(backlog_root, backlog_index, target_id, task_id)
                _append_promote_comment(source_file, target_id, task_id, audit_suffix=audit_suffix)
                wired_targets.append(target_id)
                logger.info("promote-proposal: wired marker + dep on %s", target_id)

    return PromoteResult(draft_path=draft, wired_targets=wired_targets)


def _append_promote_comment(
    source_file: Path,
    source_task_id: str,
    promoted_task_id: str,
    audit_suffix: str = "",
) -> None:
    """Append an audit line naming both the promotion and the pending-proposal marker.

    Writes a single comment to ``source_file``'s Comments section containing
    two structured markers:

    - ``[PROPOSAL_PROMOTED]`` -- audit detail describing the wiring event.
    - ``[BLOCKED_PENDING_PROPOSAL] <id>`` -- state marker read by
      ``BacklogManager._auto_requeue_marker_dependents`` when the promoted
      dependency eventually completes, so the source task auto-flips from
      ``blocked`` back to ``in-queue`` without manual intervention.

    Writing both markers on the same comment line keeps the audit trail
    compact while preserving the state marker's scan-scoped position in
    the Comments section.

    ``audit_suffix`` (ADR-11) -- optional short parenthetical inserted
    between the ``[PROPOSAL_PROMOTED]`` description and the
    ``[BLOCKED_PENDING_PROPOSAL]`` marker. Used by the sweep-time auto-
    accept path to record that no human pressed the button. Default empty
    preserves pre-ADR-11 byte-identical audit output.
    """
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    suffix = f" {audit_suffix.strip()}" if audit_suffix.strip() else ""
    entry = COMMENT_AGENT_TEMPLATE.format(
        timestamp=timestamp,
        name="task_factory",
        message=(
            f"[PROPOSAL_PROMOTED] {promoted_task_id} promoted and wired as dependency of {source_task_id}.{suffix} "
            f"[BLOCKED_PENDING_PROPOSAL] {promoted_task_id}"
        ),
    )
    content = source_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    source_file.write_text(content, encoding="utf-8")


def _append_manual_dep_comment(
    source_file: Path,
    blocked_task_id: str,
    blocker_task_id: str,
    reason: str,
) -> None:
    """Append a ``[WU_WIRED] ... [BLOCKED_PENDING_PROPOSAL]`` audit line (ADR-10 operator path).

    Parallels :func:`_append_promote_comment` but signs the comment with
    ``name="operator"`` instead of ``name="task_factory"`` so reviewers can
    distinguish an ad-hoc wire (`devbench add-dep`) from the task-factory
    promote flow. The underlying ``[BLOCKED_PENDING_PROPOSAL] <id>`` marker
    is byte-identical to the promote-written one so the ADR-07 cascade
    treats both the same.

    Idempotent: callers pass a source file already verified to not contain
    the marker; this helper only does the write.
    """
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    reason_body = f": {reason}" if reason else ""
    entry = COMMENT_AGENT_TEMPLATE.format(
        timestamp=timestamp,
        name="operator",
        message=(
            f"[WU_WIRED] {blocked_task_id} manually blocked on {blocker_task_id} "
            f"via `devbench add-dep`{reason_body}. "
            f"[BLOCKED_PENDING_PROPOSAL] {blocker_task_id}"
        ),
    )
    content = source_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    source_file.write_text(content, encoding="utf-8")


def _comments_have_marker(source_file: Path, marker_task_id: str) -> bool:
    """Return True when the source file already carries a ``[BLOCKED_PENDING_PROPOSAL] <id>`` marker."""
    needle = f"[BLOCKED_PENDING_PROPOSAL] {marker_task_id}"
    return needle in source_file.read_text(encoding="utf-8")


def _dep_row_has_task(source_file: Path, dep_task_id: str) -> bool:
    """Return True when the source file's ``## Dependencies`` table already lists ``dep_task_id``."""
    text = source_file.read_text(encoding="utf-8")
    # Match "| <id> |" in a Dependencies row; conservative enough to catch
    # pre-existing rows written by _append_dependency_to_source or hand-edits.
    return f"| {dep_task_id} |" in text


def add_dep(
    *,
    backlog_root: Path,
    backlog_index: Path,
    blocked_task_id: str,
    blocker_task_id: str,
    reason: str = "",
) -> bool:
    """Wire ``blocker_task_id`` as a dependency of ``blocked_task_id``. Idempotent.

    Writes a Dependencies-table row AND a ``[WU_WIRED] ... [BLOCKED_PENDING_PROPOSAL] <blocker>``
    audit comment on the blocked task's file. Used by ``devbench add-dep`` to wire
    cross-task markers post-promote, for hand-authored tasks, or to correct a
    proposal that was authored without ``affected_task_ids``.

    Fail-fast:
      - ``blocker_task_id`` must be present in the backlog index.
      - ``blocker_task_id`` must NOT be in a terminal state (``done`` / ``declined``).
      - ``blocked_task_id`` must be present in the backlog index.

    Returns ``True`` when at least one of (dep row, marker) was newly written,
    ``False`` when the call was a complete no-op (both already present).

    The ADR-07 cascade fires only when the blocked task's status is ``blocked``.
    If ``blocked_task_id`` is currently NOT in ``blocked`` status, this function
    still writes the marker (harmless metadata) but the CLI wrapper surfaces a
    warning so the operator can choose whether to flip the status.
    """
    from devbench.backlog.parser import BacklogParser
    from devbench.backlog.work_unit import WorkUnitStatus

    if blocked_task_id == blocker_task_id:
        raise ProposalError(f"add-dep: blocked and blocker cannot be the same task ({blocked_task_id})")

    blocker_file = _find_source_task_file(backlog_root, backlog_index, blocker_task_id)
    if blocker_file is None:
        raise ProposalError(
            f"add-dep: blocker task '{blocker_task_id}' not found in backlog index; cannot wire dependency."
        )

    blocked_file = _find_source_task_file(backlog_root, backlog_index, blocked_task_id)
    if blocked_file is None:
        raise ProposalError(
            f"add-dep: blocked task '{blocked_task_id}' not found in backlog index; cannot wire dependency."
        )

    parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
    units = parser.parse_index()
    blocker_unit = next((u for u in units if u.id == blocker_task_id), None)
    if blocker_unit is not None and blocker_unit.status in (WorkUnitStatus.DONE, WorkUnitStatus.DECLINED):
        raise ProposalError(
            f"add-dep: blocker task '{blocker_task_id}' is already terminal "
            f"(status={blocker_unit.status.value}); wiring a dep on a terminal task is a no-op."
        )

    wrote_row = False
    if not _dep_row_has_task(blocked_file, blocker_task_id):
        _append_dependency_to_source(backlog_root, backlog_index, blocked_task_id, blocker_task_id)
        wrote_row = True

    wrote_marker = False
    if not _comments_have_marker(blocked_file, blocker_task_id):
        _append_manual_dep_comment(blocked_file, blocked_task_id, blocker_task_id, reason)
        wrote_marker = True

    return wrote_row or wrote_marker


def _find_originating_source_task(workspace_root: Path, promoted_task_id: str) -> str | None:
    """Return the source_task_id that originated ``promoted_task_id``, or ``None``.

    Walks every pending proposal and returns the first source whose
    ``proposed_tasks`` contains ``promoted_task_id``.
    """
    for proposal in list_proposals(workspace_root):
        for task in proposal.proposed_tasks:
            if task.suggested_id == promoted_task_id:
                return proposal.source_task_id
    return None


def promote_all_from_source(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    source_task_id: str,
    dep_on_source: bool = True,
) -> list[Path]:
    """Promote every proposed task originating from ``source_task_id``."""
    try:
        proposal = read_proposal(workspace_root, source_task_id)
    except ProposalError as exc:
        raise ProposalError(f"Cannot resolve proposals for {source_task_id}: {exc}") from exc
    promoted: list[Path] = []
    for entry in proposal.proposed_tasks:
        result = promote_proposal(
            workspace_root=workspace_root,
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            task_id=entry.suggested_id,
            dep_on_source=dep_on_source,
        )
        promoted.append(result.draft_path)
    return promoted


def reject_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    task_id: str = "",
    unmaterialised_source_id: str = "",
    reason: str,
) -> Path | None:
    """Reject a proposed draft (per-task) OR a whole un-materialised JSON (per-source).

    Two mutually-exclusive forms:

    - ``task_id`` set: classic per-draft rejection. Archive the draft .md,
      remove its BACKLOG.md row, audit on source, AND strip the corresponding
      ``[BLOCKED_PENDING_PROPOSAL]`` marker from source Comments. After the
      strip, invoke the auto-requeue cascade so a source whose remaining
      markers are all terminal flips back to ``in-queue`` cleanly.
    - ``unmaterialised_source_id`` set: rejects an entire proposal JSON whose
      drafts have never been materialised. Archive the JSON to
      ``.devbench/rejected-proposals/<source>-unmaterialised-<ts>.json``,
      delete the live JSON, audit a ``[PROPOSAL_JSON_REJECTED]`` comment on
      the source. Refuses when any task in the JSON has already been
      materialised in any form (fail-fast).

    Supplying neither or both raises :class:`ProposalError`.

    Returns the archive path (``.md`` for per-task, ``.json`` for
    un-materialised). Returns ``None`` only when the per-task form runs
    against a draft that was already missing (idempotent no-op).
    """
    if not reason or not reason.strip():
        raise ProposalError("reject_proposal requires a non-empty reason")

    # Fail-fast on the mutual exclusion guard. Either form must be chosen
    # explicitly; the defaults (empty strings) are what ``cmd_reject_proposal``
    # passes when a flag was not supplied.
    has_task_id = bool(task_id)
    has_source_id = bool(unmaterialised_source_id)
    if has_task_id and has_source_id:
        raise ProposalError("reject_proposal: supply exactly one of task_id or unmaterialised_source_id, not both")
    if not has_task_id and not has_source_id:
        raise ProposalError("reject_proposal: supply exactly one of task_id or unmaterialised_source_id")

    if has_source_id:
        return _reject_unmaterialised_proposal(
            workspace_root=workspace_root,
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            source_task_id=unmaterialised_source_id,
            reason=reason,
        )

    return _reject_per_draft_proposal(
        workspace_root=workspace_root,
        backlog_root=backlog_root,
        backlog_index=backlog_index,
        task_id=task_id,
        reason=reason,
    )


def _reject_per_draft_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    task_id: str,
    reason: str,
) -> Path | None:
    """Per-draft rejection path: archive + remove row + strip marker + cascade."""
    draft = _find_draft_file(backlog_root, task_id)
    archive: Path | None = None
    if draft is not None:
        archive_dir = workspace_root / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = archive_dir / f"{task_id}-{timestamp}.md"
        draft.rename(archive)
    with contextlib.suppress(ProposalError):
        _remove_backlog_row(backlog_index, task_id)
    # Suppress is intentional here: rejection is idempotent, so a missing
    # BACKLOG.md row (e.g. already rejected) is a no-op.

    source = _find_originating_source_task(workspace_root, task_id)
    if source is not None:
        source_file = _find_source_task_file(backlog_root, backlog_index, source)
        if source_file is not None:
            _append_reject_audit_comment(source_file, task_id, reason)
            # Strip the BLOCKED_PENDING_PROPOSAL marker for this specific
            # rejected task so the auto-requeue cascade can correctly
            # evaluate the remaining markers. Without this strip, the
            # cascade would see a marker pointing at a now-rejected ID,
            # treat it as non-terminal (unknown), and refuse to requeue
            # even when all other markers are terminal.
            _strip_pending_proposal_marker(source_file, task_id)
            # Invoke the cascade on the just-rejected id as the "newly
            # terminal" signal for the scan's dependent walk. Source gets
            # re-evaluated; remaining markers all terminal -> source flips.
            # Remaining markers include non-terminal OR no markers remain ->
            # scan abstains per the existing ADR-07 contract.
            BacklogManager()._auto_requeue_marker_dependents(backlog_index, task_id)

    # Refresh Status Summary so the deleted row is no longer counted.
    BacklogManager()._update_status_summary(backlog_index)
    return archive


def _reject_unmaterialised_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    source_task_id: str,
    reason: str,
) -> Path:
    """Reject an entire proposal JSON whose drafts have not been materialised.

    Refuses when any task in the JSON has any state other than
    ``UNMATERIALISED`` (a partial-materialise situation the operator should
    resolve with per-task rejection instead).
    """
    json_path = proposal_path(workspace_root, source_task_id)
    if not json_path.is_file():
        raise ProposalError(f"No proposal JSON found at {json_path}")

    proposal = read_proposal(workspace_root, source_task_id)
    for task in proposal.proposed_tasks:
        state = classify_proposed_task(backlog_root, workspace_root, task.suggested_id)
        if state is not ProposalTaskState.UNMATERIALISED:
            raise ProposalError(
                f"Cannot reject {source_task_id} as un-materialised: "
                f"draft {task.suggested_id} is already in state {state.value}. "
                f"Use per-task reject (reject-proposal {task.suggested_id} --reason ...) "
                f"or resolve the draft first."
            )

    # Archive the JSON. Use a dedicated suffix so audit-reviewers can tell
    # un-materialised rejections apart from per-draft rejections at a glance.
    archive_dir = workspace_root / REJECTED_PROPOSAL_DIR_NAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"{source_task_id}-unmaterialised-{timestamp}.json"
    json_path.rename(archive_path)

    source_file = _find_source_task_file(backlog_root, backlog_index, source_task_id)
    if source_file is not None:
        timestamp_human = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
        message = f"[PROPOSAL_JSON_REJECTED] {source_task_id} rejected (un-materialised): {reason}"
        entry = COMMENT_AGENT_TEMPLATE.format(timestamp=timestamp_human, name="task_factory", message=message)
        content = source_file.read_text(encoding="utf-8")
        if COMMENTS_SECTION_HEADER in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
        source_file.write_text(content, encoding="utf-8")

    return archive_path


def _append_reject_audit_comment(source_file: Path, task_id: str, reason: str) -> None:
    """Write a ``[PROPOSAL_REJECTED]`` audit line to the source task."""
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    message = f"[PROPOSAL_REJECTED] {task_id} rejected: {reason}"
    entry = COMMENT_AGENT_TEMPLATE.format(timestamp=timestamp, name="task_factory", message=message)
    content = source_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    source_file.write_text(content, encoding="utf-8")


# Single-marker regex lifted from manager.py. Duplicated here (as a constant,
# not an import) to keep the proposal <-> manager module seam one-way: manager
# depends on nothing in proposal; proposal depends on manager for the cascade
# invocation. If the marker format ever changes, both constants must move in
# lockstep -- the pin in tests/test_plugin/test_agent_structure.py catches
# drift.
_REJECT_MARKER_STRIP_RE = re.compile(r"^.*\[BLOCKED_PENDING_PROPOSAL\]\s+(\S+).*$\n?", re.MULTILINE)


def _strip_pending_proposal_marker(source_file: Path, rejected_task_id: str) -> None:
    """Remove ``[BLOCKED_PENDING_PROPOSAL] <rejected_task_id>`` marker lines.

    Strips every full comment line (not just the marker substring) whose
    marker ID matches ``rejected_task_id`` exactly. Collapses any resulting
    consecutive blank lines down to one so the Comments section stays
    readable.

    No-op when the source file has no matching marker -- safe to call even
    when the rejected task was never promoted.
    """
    content = source_file.read_text(encoding="utf-8")

    def _drop_if_match(match: re.Match[str]) -> str:
        return "" if match.group(1) == rejected_task_id else match.group(0)

    updated = _REJECT_MARKER_STRIP_RE.sub(_drop_if_match, content)
    # Collapse runs of 3+ newlines down to 2 (one blank line between paragraphs).
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    if updated != content:
        source_file.write_text(updated, encoding="utf-8")
