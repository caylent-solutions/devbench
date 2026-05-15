"""Manifest amendment lifecycle: request, apply, reject with Layer 3 post-check.

When an executor stages a file outside the declared Changes Manifest, it
emits an amendment request JSON. A judge reviews the request and either
approves it (invoking ``apply_amendment``) or rejects it (invoking
``reject_amendment``).

``apply_amendment`` is atomic with rollback: it snapshots the work-unit
Markdown file, appends the requested rows plus an audit comment, writes
the result atomically via temp-file-plus-rename, runs the Layer 3
deterministic post-check, and on any post-check failure restores the
original content. Structural damage cannot survive an apply even if the
judge approved.

Layer 3 post-checks (all deterministic):

- Updated Changes Manifest re-parses cleanly.
- No em-dash (U+2014) introduced anywhere in the updated work-unit file.
- ``BacklogManager.validate`` still returns zero errors against the full
  backlog (this catches BACKLOG.md / file status drift, orphan references,
  status-summary counts, and every other existing integrity rule).

Pre-filter checks (schema, task state, file-in-diff, etc.) live in
``cmd_request_amendment`` in the CLI layer and in ``apply_amendment`` /
``reject_amendment`` themselves. Additional comprehensive pre-filter
checks are added by later slices of this feature.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.backlog.manager import BacklogManager
from devbench.backlog.manifest import (
    EM_DASH,
    ManifestParseError,
    ManifestRow,
    append_rows,
    parse_manifest,
)
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus
from devbench.utils.io import atomic_write_text

if TYPE_CHECKING:
    from devbench.config_loader import AmendmentConfig

logger = logging.getLogger(__name__)

AMENDMENT_DIR_NAME = ".devbench/amendments"
REJECTED_REQUESTS_DIR_NAME = ".devbench/rejected-requests"
# Issue #154 (v1, deprecated): per-task feedback log written on every
# manifest-amender rejection at ``.devbench/amender-rejections/<task-id>-<n>.json``.
# Issue #156 (v2, current): unified path for every review judge AND the
# amender at ``.devbench/review-failures/<task-id>-<judge>-<n>.json``. The
# legacy directory name is preserved as a forward-compat read path -- the
# executor-feedback collector still reads it -- but new writes always go to
# REVIEW_FAILURES_DIR_NAME.
AMENDER_REJECTIONS_DIR_NAME = ".devbench/amender-rejections"
REVIEW_FAILURES_DIR_NAME = ".devbench/review-failures"
ALLOWED_AMENDMENT_REASONS: frozenset[str] = frozenset({"tdd_green_production_fix"})
AMENDMENT_APPLIED_ACTION = "AMENDMENT_APPLIED"
AMENDMENT_REJECTED_ACTION = "AMENDMENT_REJECTED"
AMENDER_AGENT_ID = "agent/manifest-amender"
COMMENTS_SECTION_HEADER = "## Comments"

# Issue #154: canonical taxonomy of amender-rejection categories. The
# blocker-resolver / executor-feedback consumer keys retry decisions off
# these values, so the literal strings are part of the contract.
AMENDER_REJECTION_CATEGORIES: frozenset[str] = frozenset(
    {
        "SCOPE",
        "APPROACH_AUTH",
        "JUSTIFICATION_COHERENCE",
        "PRE_FILTER",
        "OTHER",
    }
)


class AmendmentError(RuntimeError):
    """Raised when an amendment request cannot be processed."""


@dataclass(frozen=True)
class AmendmentFileEntry:
    """One file change requested in an amendment."""

    path: str
    change: str

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("AmendmentFileEntry.path must be non-empty")
        if self.path != self.path.strip():
            raise ValueError(f"AmendmentFileEntry.path must not have leading/trailing whitespace: {self.path!r}")
        if not self.change or not self.change.strip():
            raise ValueError("AmendmentFileEntry.change must be non-empty")


@dataclass(frozen=True)
class AmendmentRequest:
    """Serialised form of an amendment request emitted by the executor."""

    task_id: str
    requested_at: str
    reason: str
    justification: str
    files_to_add: list[AmendmentFileEntry]
    linked_acs: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return the request as a JSON-serialisable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AmendmentRequest:
        """Build an ``AmendmentRequest`` from a parsed JSON dict.

        Raises ``ValueError`` on missing keys, wrong types, or invalid
        field values.
        """
        if not isinstance(data, dict):
            raise ValueError(f"amendment request must be a JSON object, got {type(data).__name__}")

        _require_keys(data, ["task_id", "requested_at", "reason", "justification", "files_to_add", "linked_acs"])

        files_raw = data["files_to_add"]
        if not isinstance(files_raw, list):
            raise ValueError(f"files_to_add must be a list, got {type(files_raw).__name__}")
        files: list[AmendmentFileEntry] = []
        for entry in files_raw:
            if not isinstance(entry, dict):
                raise ValueError(f"files_to_add entries must be objects, got {type(entry).__name__}")
            _require_keys(entry, ["path", "change"])
            files.append(AmendmentFileEntry(path=str(entry["path"]), change=str(entry["change"])))

        linked_acs_raw = data["linked_acs"]
        if not isinstance(linked_acs_raw, list):
            raise ValueError(f"linked_acs must be a list, got {type(linked_acs_raw).__name__}")
        linked_acs = [str(x) for x in linked_acs_raw]

        task_id = str(data["task_id"]).strip()
        if not task_id:
            raise ValueError("task_id must be a non-empty string")

        reason = str(data["reason"]).strip()
        if not reason:
            raise ValueError("reason must be a non-empty string")

        justification = str(data["justification"]).strip()
        if not justification:
            raise ValueError("justification must be a non-empty string")

        return cls(
            task_id=task_id,
            requested_at=str(data["requested_at"]),
            reason=reason,
            justification=justification,
            files_to_add=files,
            linked_acs=linked_acs,
        )


def _require_keys(data: dict[str, Any], keys: list[str]) -> None:
    """Raise ``ValueError`` if any key in ``keys`` is missing from ``data``."""
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


def request_path(workspace_root: Path, task_id: str) -> Path:
    """Return the filesystem path for a pending amendment request JSON file."""
    return workspace_root / AMENDMENT_DIR_NAME / f"{task_id}.json"


def write_request(workspace_root: Path, request: AmendmentRequest) -> Path:
    """Persist ``request`` to the pending-amendments directory.

    Raises ``AmendmentError`` if a pending request already exists for this task.
    """
    target = request_path(workspace_root, request.task_id)
    if target.exists():
        raise AmendmentError(
            f"Amendment request already exists for task {request.task_id} at {target}. "
            "Resolve it with apply-amendment or reject-amendment first."
        )
    if request.reason not in ALLOWED_AMENDMENT_REASONS:
        raise AmendmentError(
            f"Amendment reason {request.reason!r} is not in allowed reasons: {sorted(ALLOWED_AMENDMENT_REASONS)}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(request.to_dict(), indent=2) + "\n")
    return target


def read_request(workspace_root: Path, task_id: str) -> AmendmentRequest:
    """Load a pending amendment request from disk.

    Raises ``AmendmentError`` if the request file is missing, is not valid
    JSON, or does not satisfy the schema.
    """
    target = request_path(workspace_root, task_id)
    if not target.exists():
        raise AmendmentError(f"No pending amendment request for task {task_id} at {target}")
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AmendmentError(f"Amendment request for {task_id} is not valid JSON: {exc}") from exc
    try:
        return AmendmentRequest.from_dict(data)
    except (ValueError, TypeError) as exc:
        raise AmendmentError(f"Amendment request for {task_id} is not valid: {exc}") from exc


def delete_request(workspace_root: Path, task_id: str) -> None:
    """Delete the pending amendment request file for ``task_id`` if it exists."""
    target = request_path(workspace_root, task_id)
    if target.exists():
        target.unlink()


def archive_rejected_request(workspace_root: Path, task_id: str) -> Path | None:
    """Move the pending request JSON to ``rejected-requests/<id>-<timestamp>.json``.

    Returns the archive path when the move succeeded, or ``None`` when no
    pending request existed. Used by ``reject_amendment`` so the blocker-resolver
    feature (task-factory input) keeps a record of the rejected request after
    the pending-requests directory is cleaned.
    """
    pending = request_path(workspace_root, task_id)
    if not pending.exists():
        return None
    archive_dir = workspace_root / REJECTED_REQUESTS_DIR_NAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    target = archive_dir / f"{task_id}-{timestamp}.json"
    pending.rename(target)
    return target


def apply_amendment(workspace_root: Path, backlog_index: Path, task_id: str) -> None:
    """Apply an approved amendment atomically with Layer 3 post-check.

    Reads the pending amendment request, appends its rows to the work-unit's
    Changes Manifest, writes an audit comment, performs Layer 3
    post-checks, and deletes the request on success. On any post-check
    failure the work-unit file is restored to its pre-amendment content and
    ``AmendmentError`` is raised -- the caller is expected to log a
    REVIEW_FAIL verdict.
    """
    request = read_request(workspace_root, task_id)
    if request.task_id != task_id:
        raise AmendmentError(f"Amendment request task_id ({request.task_id!r}) does not match argument ({task_id!r})")
    if request.reason not in ALLOWED_AMENDMENT_REASONS:
        raise AmendmentError(
            f"Amendment reason {request.reason!r} is not in allowed reasons: {sorted(ALLOWED_AMENDMENT_REASONS)}"
        )

    wu_file = _resolve_task_file(backlog_index, task_id)

    original_content = wu_file.read_text(encoding="utf-8")

    try:
        manifest_rows = [ManifestRow(file=f.path, change=f.change) for f in request.files_to_add]
    except ValueError as exc:
        raise AmendmentError(f"Amendment contains invalid manifest row: {exc}") from exc

    try:
        content_with_rows = append_rows(original_content, manifest_rows)
    except ManifestParseError as exc:
        raise AmendmentError(f"Cannot apply amendment: {exc}") from exc

    audit_entry = _build_audit_entry(request, AMENDMENT_APPLIED_ACTION)
    final_content = _append_audit_comment(content_with_rows, audit_entry)

    atomic_write_text(wu_file, final_content)

    try:
        _post_check(final_content, backlog_index)
    except AmendmentError:
        atomic_write_text(wu_file, original_content)
        raise

    delete_request(workspace_root, task_id)
    logger.info("Amendment applied for %s: %d file(s), reason=%s", task_id, len(manifest_rows), request.reason)


def reject_amendment(
    workspace_root: Path,
    backlog_index: Path,
    task_id: str,
    rejection_reason: str,
) -> None:
    """Reject an amendment: write audit comment, block the task, delete request.

    Raises ``AmendmentError`` if the request is missing or if the rejection
    reason is empty.
    """
    if not rejection_reason or not rejection_reason.strip():
        raise AmendmentError("reject_amendment requires a non-empty rejection_reason")

    request = read_request(workspace_root, task_id)
    if request.task_id != task_id:
        raise AmendmentError(f"Amendment request task_id ({request.task_id!r}) does not match argument ({task_id!r})")

    wu_file = _resolve_task_file(backlog_index, task_id)

    audit_entry = _build_audit_entry(request, AMENDMENT_REJECTED_ACTION, rejection_reason=rejection_reason)
    content = wu_file.read_text(encoding="utf-8")
    updated = _append_audit_comment(content, audit_entry)
    atomic_write_text(wu_file, updated)

    mgr = BacklogManager()
    mgr.mark_blocked(wu_file, backlog_index, task_id, rejection_reason)

    archive_rejected_request(workspace_root, task_id)
    # Issue #154: persist the rejection feedback so the executor-feedback
    # collector / blocker-resolver can ingest it on the next retry. The
    # JSON is bounded to ``MAX_RETRY_ATTEMPTS`` files per task -- once the
    # budget is exhausted the executor will not be re-invoked anyway, so
    # the cap is an upper bound, never a silently dropped record.
    persist_rejection_feedback(
        workspace_root=workspace_root,
        task_id=task_id,
        rejection_reason=rejection_reason,
        request=request,
    )
    logger.info("Amendment rejected for %s: %s", task_id, rejection_reason)


def persist_rejection_feedback(
    *,
    workspace_root: Path,
    task_id: str,
    rejection_reason: str,
    request: AmendmentRequest,
) -> Path:
    """Write a structured rejection-feedback JSON.

    Issue #154 (original) wrote to ``.devbench/amender-rejections/<task-id>-<n>.json``
    with an ad-hoc payload shape. Issue #156 unifies the path with every
    other review-judge rejection: the JSON now lands at
    ``.devbench/review-failures/<task-id>-manifest_amender-<n>.json`` and
    follows the schema-v1 ``review-feedback-schema.json`` shape.

    The legacy ``amender-rejections`` directory is preserved on read by
    ``read_review_failure_files`` (forward compatibility for archived
    runs) but new writes always go to the unified path.

    Capped at ``MAX_RETRY_ATTEMPTS`` to mirror the CI-failure feedback
    cap. Returns the path to the JSON written.
    """
    from devbench.config import MAX_RETRY_ATTEMPTS

    archive_dir = workspace_root / REVIEW_FAILURES_DIR_NAME
    archive_dir.mkdir(parents=True, exist_ok=True)

    judge = "manifest_amender"
    existing = sorted(archive_dir.glob(f"{task_id}-{judge}-*.json"))
    attempt = len(existing) + 1
    capped = attempt > MAX_RETRY_ATTEMPTS

    category_code = _categorise_rejection_reason(rejection_reason)
    rejected_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "judge": judge,
        "attempt": attempt,
        "rejected_at": rejected_at,
        "categories": [
            {
                "code": category_code,
                "severity": "fail",
                "summary": rejection_reason,
                "remediation": (
                    "Address the manifest-amender finding and re-stage; or surface a"
                    " dependent task via [NEEDS_DEP] when the fix belongs upstream."
                ),
                "files": [f.path for f in request.files_to_add],
            }
        ],
        "raw_verdict_text": rejection_reason,
        "capped": capped,
        # Preserve original-shape fields that downstream consumers (and tests
        # written for issue #154) still inspect alongside the new schema.
        "reason_category": category_code,
        "reason_text": rejection_reason,
        "request": request.to_dict(),
        "recorded_at": rejected_at,
    }
    target = archive_dir / f"{task_id}-{judge}-{attempt}.json"
    atomic_write_text(target, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def read_review_failure_files(workspace_root: Path, task_id: str) -> list[Path]:
    """Return every review-failure JSON file for ``task_id`` (new + legacy paths).

    Issue #156: the executor-feedback collector and the done-gate both
    walk this list. New writes go to ``.devbench/review-failures/`` as
    ``<task-id>-<judge>-<n>.json``. Legacy manifest-amender writes
    archived under ``.devbench/amender-rejections/<task-id>-<n>.json``
    are still read so prior runs are not orphaned.

    Returns a deduplicated list sorted by mtime (oldest first); duplicate
    matches between the two paths are unlikely in practice but the
    deduplication guards against file replication during migration.
    """
    seen: set[Path] = set()
    paths: list[Path] = []
    new_dir = workspace_root / REVIEW_FAILURES_DIR_NAME
    if new_dir.is_dir():
        for path in sorted(new_dir.glob(f"{task_id}-*.json")):
            if path not in seen:
                paths.append(path)
                seen.add(path)
    legacy_dir = workspace_root / AMENDER_REJECTIONS_DIR_NAME
    if legacy_dir.is_dir():
        for path in sorted(legacy_dir.glob(f"{task_id}-*.json")):
            if path not in seen:
                paths.append(path)
                seen.add(path)
    return paths


def _categorise_rejection_reason(rejection_reason: str) -> str:
    """Classify a rejection reason string into one of ``AMENDER_REJECTION_CATEGORIES``.

    Heuristic substring matching: the manifest-amender prompt instructs
    the LLM judge to surface canonical category tokens (``SCOPE`` /
    ``APPROACH_AUTH`` / ``JUSTIFICATION_COHERENCE`` / ``PRE_FILTER``)
    inline in the rejection reason. Unmatched reasons fall back to
    ``OTHER`` so consumers always see a known token.
    """
    haystack = rejection_reason.upper()
    for category in ("SCOPE", "APPROACH_AUTH", "JUSTIFICATION_COHERENCE", "PRE_FILTER"):
        if category in haystack:
            return category
    return "OTHER"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_task_file(backlog_index: Path, task_id: str) -> Path:
    """Resolve a task ID to its work-unit Markdown file path.

    Raises ``AmendmentError`` if the task is not found or the file is missing.
    """
    parser = BacklogParser(
        backlog_root=backlog_index.parent / "backlog",
        backlog_index=backlog_index,
    )
    try:
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        raise AmendmentError(f"Cannot read backlog index {backlog_index}: {exc}") from exc

    for unit in units:
        if unit.id == task_id:
            # BacklogParser.parse_work_unit_file has already verified the file exists,
            # so unit.file_path is guaranteed to be on disk when we reach this point.
            return unit.file_path

    raise AmendmentError(f"Task {task_id} not found in backlog index {backlog_index}")


def _build_audit_entry(
    request: AmendmentRequest,
    action: str,
    rejection_reason: str = "",
) -> str:
    """Render a one-line audit entry for the Comments section."""
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    if action == AMENDMENT_APPLIED_ACTION:
        file_count = len(request.files_to_add)
        message = f"{request.reason}; added {file_count} file(s); justification: {request.justification}"
    elif action == AMENDMENT_REJECTED_ACTION:
        message = f"{request.reason}; rejected: {rejection_reason}"
    else:
        raise ValueError(f"Unknown audit action: {action}")
    return f"[{timestamp}] [{AMENDER_AGENT_ID}] [{action}] {message}\n"


def _append_audit_comment(content: str, entry: str) -> str:
    """Append an audit entry to the ``## Comments`` section, creating it if absent."""
    if COMMENTS_SECTION_HEADER in content:
        return content.rstrip("\n") + "\n\n" + entry
    return content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry


def _post_check(content: str, backlog_index: Path) -> None:
    """Run Layer 3 deterministic post-checks. Raise ``AmendmentError`` on failure.

    ``content`` is the post-amendment work-unit Markdown (already written to
    disk). Checks the rendered content for em-dash introduction and runs the
    full ``validate-backlog`` to detect any integrity regression caused by
    the amendment.
    """
    if EM_DASH in content:
        raise AmendmentError("Post-check: em-dash (U+2014) found in updated work-unit file")

    mgr = BacklogManager()
    errors = mgr.validate(backlog_index, backlog_index.parent)
    if errors:
        raise AmendmentError(
            f"Post-check: backlog integrity violated after amendment ({len(errors)} error(s)): " + "; ".join(errors)
        )


# ---------------------------------------------------------------------------
# Layer 1 deterministic pre-filter
# ---------------------------------------------------------------------------


class PreFilter:
    """Layer 1 deterministic pre-filter for amendment requests.

    Every check is pure in the sense that a given (request, context)
    pair produces the same outcome every time. The LLM amender is only
    invoked after every pre-filter check passes. On any failure the caller
    receives ``AmendmentError`` with a specific, actionable message.

    Orchestration entry point: :meth:`run_all`. Individual checks are
    public and tested one-to-one so coverage can demonstrate each rule
    is exercised independently.
    """

    def __init__(self, backlog_index: Path, config: AmendmentConfig) -> None:
        self._backlog_index = backlog_index
        self._config = config

    def run_all(
        self,
        request: AmendmentRequest,
        *,
        staged_files: frozenset[str] | None = None,
        prior_applied_count: int = 0,
    ) -> None:
        """Run every check in a fixed order. Raise ``AmendmentError`` on the first failure.

        ``staged_files`` is the set of file paths the executor has staged in
        git against the base branch; pass ``None`` to skip the in-diff check
        (for contexts where git access is unavailable, such as unit tests of
        earlier checks). ``prior_applied_count`` is the number of amendments
        already applied to this task in the current executor run.
        """
        self.check_enabled()
        self.check_reason_allowed(request)
        self.check_rate_limit(prior_applied_count)
        unit = self.check_task_exists_and_in_progress(request)
        self.check_linked_acs_exist(request, unit)
        self.check_files_not_already_in_manifest(request, unit)
        if staged_files is not None:
            self.check_files_in_staged_diff(request, staged_files)

    def check_enabled(self) -> None:
        """The backlog config must have ``manifest_amendment.enabled: true``."""
        if not self._config.enabled:
            raise AmendmentError(
                "Amendment workflow is disabled for this backlog. "
                "Set manifest_amendment.enabled: true in backlog/config/devbench.yaml to enable."
            )

    def check_reason_allowed(self, request: AmendmentRequest) -> None:
        """The request's reason must be in the backlog's ``allowed_reasons`` list."""
        if request.reason not in self._config.allowed_reasons:
            raise AmendmentError(
                f"Amendment reason {request.reason!r} is not in allowed reasons for this backlog: "
                f"{sorted(self._config.allowed_reasons)}"
            )

    def check_rate_limit(self, prior_applied_count: int) -> None:
        """Applying this amendment must not exceed ``max_requests_per_execution``."""
        if prior_applied_count >= self._config.max_requests_per_execution:
            raise AmendmentError(
                f"Amendment rate limit exceeded: {prior_applied_count} amendment(s) already applied to this task "
                f"this execution (max {self._config.max_requests_per_execution})."
            )

    def check_task_exists_and_in_progress(self, request: AmendmentRequest) -> WorkUnit:
        """The task must exist in the backlog index AND be in status ``in-progress``.

        Returns the resolved ``WorkUnit`` so later checks can reuse it
        without re-parsing the backlog.
        """
        parser = BacklogParser(
            backlog_root=self._backlog_index.parent / "backlog",
            backlog_index=self._backlog_index,
        )
        try:
            units = parser.parse_index()
        except (FileNotFoundError, ValueError) as exc:
            raise AmendmentError(f"Cannot read backlog index {self._backlog_index}: {exc}") from exc

        for unit in units:
            if unit.id == request.task_id:
                if unit.status is not WorkUnitStatus.IN_PROGRESS:
                    raise AmendmentError(
                        f"Task {request.task_id} is not in-progress (current status: "
                        f"{unit.status.value}). Amendments only apply to in-progress tasks."
                    )
                return unit

        raise AmendmentError(f"Task {request.task_id} not found in backlog index {self._backlog_index}")

    def check_linked_acs_exist(self, request: AmendmentRequest, unit: WorkUnit) -> None:
        """Every ``linked_acs`` entry must match an AC ID in the task's Acceptance Criteria."""
        ac_ids = {_extract_ac_id(line) for line in unit.acceptance_criteria}
        missing = [ac for ac in request.linked_acs if ac not in ac_ids]
        if missing:
            raise AmendmentError(
                f"Amendment references AC IDs not found in task {request.task_id}'s "
                f"Acceptance Criteria: {missing}. Known AC IDs: {sorted(ac_ids)}"
            )

    def check_files_not_already_in_manifest(self, request: AmendmentRequest, unit: WorkUnit) -> None:
        """No file path in ``files_to_add`` may already appear in the Changes Manifest."""
        content = unit.file_path.read_text(encoding="utf-8")
        try:
            existing_files = {row.file for row in parse_manifest(content)}
        except ManifestParseError as exc:
            raise AmendmentError(f"Cannot read current Changes Manifest for task {request.task_id}: {exc}") from exc
        duplicates = [f.path for f in request.files_to_add if f.path in existing_files]
        if duplicates:
            raise AmendmentError(f"Amendment lists file(s) already declared in Changes Manifest: {duplicates}")

    def check_files_in_staged_diff(self, request: AmendmentRequest, staged_files: frozenset[str]) -> None:
        """Every path in ``files_to_add`` must appear in the provided staged-diff file set."""
        missing = [f.path for f in request.files_to_add if f.path not in staged_files]
        if missing:
            raise AmendmentError(
                f"Amendment lists file(s) not in the staged diff: {missing}. "
                "Executor cannot request amendment for files it hasn't staged."
            )


def _extract_ac_id(ac_line: str) -> str:
    """Extract the AC identifier from an AC text line.

    Input format: ``"AC-TEST-003 description text"``.
    Output: ``"AC-TEST-003"``.

    Returns an empty string for blank input.
    """
    stripped = ac_line.strip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0]
