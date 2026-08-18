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
- No em-dash (U+2014) introduced anywhere in the updated work-unit file
  (absolute -- rolls back independent of baseline; spec AC-22).
- ``BacklogManager.validate`` is baseline-relative (FR-10, db-312): only
  errors the amendment itself introduces trigger a rollback; pre-existing
  errors the amendment did not cause are logged as a warning and never
  block the apply (this still catches BACKLOG.md / file status drift,
  orphan references, status-summary counts, and every other existing
  integrity rule the amendment is responsible for).

Two amendment reasons are sanctioned (``ALLOWED_AMENDMENT_REASONS``):
``tdd_green_production_fix`` (unrestricted paths) and
``doc_sync_review_fix`` (FR-11, db-327 -- restricted to documentation or
documentation-pinning test paths via ``_check_reason_path_guard``).

Pre-filter checks (schema, task state, file-in-diff, etc.) live in
``cmd_request_amendment`` in the CLI layer and in ``apply_amendment`` /
``reject_amendment`` themselves. Additional comprehensive pre-filter
checks are added by later slices of this feature.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
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
    remove_rows,
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
# db-327 Leg A1: a second, default-enabled amendment reason for a
# doc_review-mandated out-of-Manifest documentation fix. Unlike
# ``tdd_green_production_fix`` (unrestricted), this reason is path-guarded --
# see ``_REASON_PATH_CLASSIFIERS`` / ``_check_reason_path_guard`` below.
DOC_SYNC_REVIEW_FIX_REASON = "doc_sync_review_fix"
ALLOWED_AMENDMENT_REASONS: frozenset[str] = frozenset({"tdd_green_production_fix", DOC_SYNC_REVIEW_FIX_REASON})
AMENDMENT_APPLIED_ACTION = "AMENDMENT_APPLIED"
AMENDMENT_REJECTED_ACTION = "AMENDMENT_REJECTED"
AMENDER_AGENT_ID = "agent/manifest-amender"
COMMENTS_SECTION_HEADER = "## Comments"

# db-327 Leg A1: reuse the db-300 classifiers on ``BacklogManager`` as the
# single source of truth for "is this path documentation" / "is this path a
# test source file" -- introducing an independent classifier here would let
# the production/test/doc path boundary drift out of sync with the
# task-type-taxonomy validate() rule that also depends on them.
_is_documentation_path = BacklogManager._is_documentation_path
_is_test_source_path = BacklogManager._is_test_source_path

# Amendment reasons restricted to a subset of paths. Every classifier in the
# tuple is tried; a path is accepted if ANY classifier returns True (OR
# semantics -- documentation OR a documentation-pinning test). Reasons absent
# from this mapping (``tdd_green_production_fix``) carry no path restriction.
_REASON_PATH_CLASSIFIERS: dict[str, tuple[Callable[[str], bool], ...]] = {
    DOC_SYNC_REVIEW_FIX_REASON: (_is_documentation_path, _is_test_source_path),
}

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
    """Serialised form of an amendment request emitted by the executor.

    ``files_to_remove`` holds repo-relative paths whose Manifest rows should be
    dropped. It exists because ``AC-FINAL-015`` requires the Changes Manifest to
    match the files git actually changed EXACTLY -- "no extra, no missing" --
    so a declared row whose file ends up with a zero-line diff (its work having
    landed under a sibling unit, say) is a real violation. The
    ``changes_manifest`` judge fails the unit for it and prescribes an
    amendment, which was previously impossible: the request could only ADD.
    Defaults to empty so every request written before this field existed still
    parses.
    """

    task_id: str
    requested_at: str
    reason: str
    justification: str
    files_to_add: list[AmendmentFileEntry]
    linked_acs: list[str]
    files_to_remove: list[str] = dataclass_field(default_factory=list)

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

        files_to_remove = _parse_files_to_remove(data)

        if not files and not files_to_remove:
            raise ValueError(
                "amendment request must change the Manifest: files_to_add and files_to_remove are both empty"
            )

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
            files_to_remove=files_to_remove,
        )


def _parse_files_to_remove(data: dict[str, Any]) -> list[str]:
    """Validate and return the request's ``files_to_remove`` list.

    Optional and defaulting to empty so amendment requests written before the
    field existed still parse. Entries must be non-blank strings (bare paths,
    not the ``{path, change}`` objects ``files_to_add`` uses -- a removal has no
    change to describe). Extracted from ``AmendmentRequest.from_dict`` to keep
    that method within its branch budget.
    """
    remove_raw = data.get("files_to_remove", [])
    if not isinstance(remove_raw, list):
        raise ValueError(f"files_to_remove must be a list, got {type(remove_raw).__name__}")
    files_to_remove: list[str] = []
    for entry in remove_raw:
        if not isinstance(entry, str):
            raise ValueError(f"files_to_remove entries must be strings, got {type(entry).__name__}")
        path = entry.strip()
        if not path:
            raise ValueError("files_to_remove entries must be non-empty strings")
        files_to_remove.append(path)
    return files_to_remove


def _require_keys(data: dict[str, Any], keys: list[str]) -> None:
    """Raise ``ValueError`` if any key in ``keys`` is missing from ``data``."""
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


def _check_reason_path_guard(reason: str, files: list[AmendmentFileEntry]) -> None:
    """Reject a request whose files violate ``reason``'s path restriction.

    ``doc_sync_review_fix`` (db-327 Leg A1) is restricted to documentation
    (``.md``) or documentation-pinning test paths -- see
    ``_REASON_PATH_CLASSIFIERS``. Reasons absent from that mapping (e.g.
    ``tdd_green_production_fix``) carry no restriction and are unaffected.
    Called from BOTH ``write_request`` and ``apply_amendment`` so the guard
    cannot be bypassed by hand-editing a pending request file between the
    two calls.

    Deliberately applies to ``files_to_add`` only. This guard bounds what a
    reason may bring INTO the unit's declared scope; a removal only shrinks
    scope, and is separately gated by
    ``PreFilter.check_files_to_remove_have_no_diff``, which proves the file has
    no changes of any kind. Restricting removals by path class would block the
    exact case removal exists for -- a stale non-documentation row under a
    documentation reason -- while adding no safety, since a file with no diff
    carries no work to smuggle out.
    """
    classifiers = _REASON_PATH_CLASSIFIERS.get(reason)
    if classifiers is None:
        return
    bad_paths = [f.path for f in files if not any(classifier(f.path) for classifier in classifiers)]
    if bad_paths:
        raise AmendmentError(
            f"Amendment reason {reason!r} only permits documentation (.md) or "
            f"documentation-pinning test paths, but these are not: {', '.join(bad_paths)}"
        )


def _resolve_allowed_reasons(allowed_reasons: frozenset[str] | None) -> frozenset[str]:
    """Return the reason set to enforce, preferring the per-backlog configuration.

    ``ALLOWED_AMENDMENT_REASONS`` is the set of reasons devbench IMPLEMENTS; a
    backlog may narrow it via ``manifest_amendment.allowed_reasons``. Enforcing
    the module global instead of the configured set made a backlog's narrowing
    a no-op -- the config was loaded, validated against the schema, and then
    ignored at every gate.

    ``None`` means the caller has no configuration to offer (a direct library
    call), in which case the implemented set applies. Configuration is never
    widened here: a configured reason devbench does not implement is a config
    error, so the result is the intersection.
    """
    if allowed_reasons is None:
        return ALLOWED_AMENDMENT_REASONS
    return frozenset(allowed_reasons) & ALLOWED_AMENDMENT_REASONS


def request_path(workspace_root: Path, task_id: str) -> Path:
    """Return the filesystem path for a pending amendment request JSON file."""
    return workspace_root / AMENDMENT_DIR_NAME / f"{task_id}.json"


def write_request(
    workspace_root: Path,
    request: AmendmentRequest,
    allowed_reasons: frozenset[str] | None = None,
) -> Path:
    """Persist ``request`` to the pending-amendments directory.

    Raises ``AmendmentError`` if a pending request already exists for this task,
    or if the reason is outside the effective allowed set. ``allowed_reasons``
    is the backlog's configured narrowing; ``None`` enforces every reason
    devbench implements.
    """
    target = request_path(workspace_root, request.task_id)
    if target.exists():
        raise AmendmentError(
            f"Amendment request already exists for task {request.task_id} at {target}. "
            "Resolve it with apply-amendment or reject-amendment first."
        )
    effective = _resolve_allowed_reasons(allowed_reasons)
    if request.reason not in effective:
        raise AmendmentError(f"Amendment reason {request.reason!r} is not in allowed reasons: {sorted(effective)}")
    _check_reason_path_guard(request.reason, request.files_to_add)
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


def apply_amendment(
    workspace_root: Path,
    backlog_index: Path,
    task_id: str,
    allowed_reasons: frozenset[str] | None = None,
) -> None:
    """Apply an approved amendment atomically with Layer 3 post-check.

    Reads the pending amendment request, appends its rows to the work-unit's
    Changes Manifest, writes an audit comment, performs Layer 3
    post-checks, and deletes the request on success. On any post-check
    failure the work-unit file is restored to its pre-amendment content and
    ``AmendmentError`` is raised -- the caller is expected to log a
    REVIEW_FAIL verdict.

    The reason is re-checked here against the same effective allowed set
    ``write_request`` used, so hand-editing a pending request on disk between
    the two calls cannot smuggle in a reason the backlog disallows.
    ``allowed_reasons`` is the backlog's configured narrowing; ``None``
    enforces every reason devbench implements.
    """
    request = read_request(workspace_root, task_id)
    if request.task_id != task_id:
        raise AmendmentError(f"Amendment request task_id ({request.task_id!r}) does not match argument ({task_id!r})")
    effective = _resolve_allowed_reasons(allowed_reasons)
    if request.reason not in effective:
        raise AmendmentError(f"Amendment reason {request.reason!r} is not in allowed reasons: {sorted(effective)}")
    _check_reason_path_guard(request.reason, request.files_to_add)

    wu_file = _resolve_task_file(backlog_index, task_id)

    original_content = wu_file.read_text(encoding="utf-8")

    # FR-10 (db-312): capture the backlog's pre-amendment error set BEFORE
    # any write, so the Layer 3 post-check below can distinguish errors this
    # amendment introduced from errors that already existed and are not this
    # amendment's responsibility.
    baseline_errors = frozenset(BacklogManager().validate(backlog_index, backlog_index.parent))

    try:
        manifest_rows = [ManifestRow(file=f.path, change=f.change) for f in request.files_to_add]
    except ValueError as exc:
        raise AmendmentError(f"Amendment contains invalid manifest row: {exc}") from exc

    # Removals apply first so a request that both drops a stale row and adds a
    # new one lands the additions at the end of the surviving table, matching
    # the append-only ordering reviewers already read. Both operations sit
    # inside the same atomic write and rollback envelope below, so a post-check
    # failure restores the manifest whole -- never half-amended.
    try:
        content_with_rows = remove_rows(original_content, request.files_to_remove)
        content_with_rows = append_rows(content_with_rows, manifest_rows)
    except ManifestParseError as exc:
        raise AmendmentError(f"Cannot apply amendment: {exc}") from exc

    audit_entry = _build_audit_entry(request, AMENDMENT_APPLIED_ACTION)
    final_content = _append_audit_comment(content_with_rows, audit_entry)

    atomic_write_text(wu_file, final_content)

    try:
        _post_check(final_content, backlog_index, baseline_errors)
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

    # Issue #210: write the rejected-requests archive + rejection-feedback JSON
    # BEFORE calling mark_blocked.  mark_blocked runs classify_blocked_task
    # inline; the classifier's AWAITING_AMENDMENT_RECOVERY signal is the
    # presence of the archive on disk.  Pre-fix the writes happened AFTER
    # mark_blocked, so the classifier saw no recovery signal and fell through
    # to OPERATOR_ACTION_REQUIRED -- the wrong per-class Slack toggle fired.
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

    mgr = BacklogManager()
    mgr.mark_blocked(wu_file, backlog_index, task_id, rejection_reason)
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
        # Both directions are reported so a removal is never invisible in the
        # audit trail: a dropped Manifest row changes what the unit is allowed
        # to commit, which a reviewer must be able to see happened.
        parts = [f"added {len(request.files_to_add)} file(s)"]
        if request.files_to_remove:
            parts.append(f"removed {len(request.files_to_remove)} row(s): {', '.join(request.files_to_remove)}")
        message = f"{request.reason}; {'; '.join(parts)}; justification: {request.justification}"
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


def _post_check(content: str, backlog_index: Path, baseline_errors: frozenset[str]) -> None:
    """Run Layer 3 deterministic post-checks. Raise ``AmendmentError`` on failure.

    ``content`` is the post-amendment work-unit Markdown (already written to
    disk). ``baseline_errors`` is the ``validate()`` error set captured
    BEFORE the amendment was written.

    The em-dash check is absolute (FR-10, spec AC-22): an amendment-
    introduced U+2014 always rolls back, independent of baseline, because an
    introduced em-dash is inherently the amendment's fault.

    The whole-backlog ``validate()`` check is baseline-relative (FR-10,
    db-312): only errors the amendment ITSELF introduced
    (``errors - baseline_errors``) roll back. Pre-existing errors the
    amendment did not cause (``errors & baseline_errors``) are logged as a
    WARNING -- never silently dropped, and never able to mask a genuinely
    new error, since only the introduced set drives the rollback decision.
    """
    if EM_DASH in content:
        raise AmendmentError("Post-check: em-dash (U+2014) found in updated work-unit file")

    mgr = BacklogManager()
    errors = frozenset(mgr.validate(backlog_index, backlog_index.parent))

    new_errors = errors - baseline_errors
    if new_errors:
        raise AmendmentError(
            f"Post-check: amendment introduced {len(new_errors)} new backlog integrity error(s): "
            + "; ".join(sorted(new_errors))
        )

    surviving_errors = errors & baseline_errors
    if surviving_errors:
        logger.warning(
            "Amendment applied over %d pre-existing backlog integrity error(s) unrelated to this amendment: %s",
            len(surviving_errors),
            "; ".join(sorted(surviving_errors)),
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
        changed_files: frozenset[str] | None = None,
        prior_applied_count: int = 0,
    ) -> None:
        """Run every check in a fixed order. Raise ``AmendmentError`` on the first failure.

        ``changed_files`` is the union of staged, unstaged, and untracked
        paths. It proves both that an added path really was modified and that a
        removal candidate really has no changes; pass ``None`` to skip both
        checks (for contexts where git access is unavailable, such as unit
        tests of earlier checks). ``staged_files`` is accepted for backward
        compatibility and no longer gates additions -- staging is precisely what
        the executor cannot do for a path the Manifest does not yet declare.
        ``prior_applied_count`` is the number of amendments
        already applied to this task in the current executor run.
        """
        self.check_enabled()
        self.check_reason_allowed(request)
        self.check_rate_limit(prior_applied_count)
        unit = self.check_task_exists_and_in_progress(request)
        self.check_linked_acs_exist(request, unit)
        self.check_files_not_already_in_manifest(request, unit)
        self.check_files_to_remove_are_declared(request, unit)
        if changed_files is not None:
            self.check_files_in_changed_set(request, changed_files)
            self.check_files_to_remove_have_no_diff(request, changed_files)

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

    def check_files_in_changed_set(self, request: AmendmentRequest, changed_files: frozenset[str]) -> None:
        """Every path in ``files_to_add`` must have a real change somewhere in the worktree.

        Tests against the CHANGED set (staged + unstaged + untracked) rather
        than the staged set alone, because staging is exactly what the executor
        cannot do for these paths. ``guard-git-stage.sh`` rejects ``git add``
        for any path absent from the Changes Manifest and directs the executor
        to request an amendment; requiring the path to be staged first made the
        two controls mutually blocking, so a file that needed an amendment
        could never be staged and could never be amended. The executor's only
        escapes were bypassing a guard hook, which the no-hook-bypass rule
        forbids, or blocking the work unit.

        The anti-fabrication intent is unchanged: a path with no diff anywhere
        in the worktree is still rejected, so the executor cannot amend in a
        file it has not actually touched.
        """
        missing = [f.path for f in request.files_to_add if f.path not in changed_files]
        if missing:
            raise AmendmentError(
                f"Amendment lists file(s) that the worktree has not been modified to contain: {missing}. "
                "Executor cannot request amendment for files it hasn't changed."
            )

    def check_files_to_remove_are_declared(self, request: AmendmentRequest, unit: WorkUnit) -> None:
        """Every path in ``files_to_remove`` must currently appear in the Changes Manifest.

        Rejecting an undeclared path here (rather than letting
        ``manifest.remove_rows`` raise during apply) keeps the failure in Layer
        1 where the message is actionable and nothing has been written yet.
        A request cannot both add and remove the same path -- that is
        self-contradictory, and permitting it would make the final manifest
        depend on the order the two lists happen to be applied in.
        """
        if not request.files_to_remove:
            return
        content = unit.file_path.read_text(encoding="utf-8")
        try:
            existing_files = {row.file for row in parse_manifest(content)}
        except ManifestParseError as exc:
            raise AmendmentError(f"Cannot read current Changes Manifest for task {request.task_id}: {exc}") from exc

        undeclared = [p for p in request.files_to_remove if p not in existing_files]
        if undeclared:
            raise AmendmentError(
                f"Amendment lists file(s) to remove that are not in the Changes Manifest: {undeclared}. "
                f"Manifest declares: {sorted(existing_files)}"
            )

        added = {f.path for f in request.files_to_add}
        contradictory = sorted(added & set(request.files_to_remove))
        if contradictory:
            raise AmendmentError(
                f"Amendment both adds and removes the same file(s): {contradictory}. Choose one direction per path."
            )

    def check_files_to_remove_have_no_diff(self, request: AmendmentRequest, changed_files: frozenset[str]) -> None:
        """No path in ``files_to_remove`` may have ANY change in the target repo.

        This is the safety property that makes removal non-abusable. A Manifest
        row is the only thing authorising a file to appear in the unit's commit,
        so if removal were permitted for a file with real changes, an executor
        could drop the row and carry the work along unreviewed -- exactly the
        scope violation ``assert_staged_matches_manifest`` exists to stop.

        ``changed_files`` must be the UNION of staged, unstaged, and untracked
        paths (``manifest.list_changed_files``), not just the staged set: a file
        modified but not yet staged still represents real work.
        """
        if not request.files_to_remove:
            return
        dirty = [p for p in request.files_to_remove if p in changed_files]
        if dirty:
            raise AmendmentError(
                f"Amendment cannot remove Manifest row(s) for file(s) with changes: {dirty}. "
                "A row may only be dropped once its file has no staged, unstaged, or untracked "
                "changes; otherwise the work would leave the unit's declared scope. "
                "Commit or revert the changes first, or keep the row."
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
