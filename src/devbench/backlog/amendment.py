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
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.backlog.manager import BacklogManager
from devbench.backlog.manifest import (
    EM_DASH,
    ManifestParseError,
    ManifestRow,
    ManifestRowNotFoundError,
    append_rows,
    parse_manifest,
    remove_rows,
)
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus
from devbench.utils.io import atomic_write_text
from devbench.verification import VerificationItem, parse_verification_item

if TYPE_CHECKING:
    from devbench.config_loader import AmendmentConfig

logger = logging.getLogger(__name__)

AMENDMENT_DIR_NAME = ".devbench/amendments"
REJECTED_REQUESTS_DIR_NAME = ".devbench/rejected-requests"
AMENDER_REJECTIONS_DIR_NAME = ".devbench/amender-rejections"
REVIEW_FAILURES_DIR_NAME = ".devbench/review-failures"
REASON_VERIFICATION_DIRECTIVE_DEFECT: str = "verification_directive_defect"

REASON_MANIFEST_ROW_SUPERSEDED: str = "manifest_row_superseded"

ALLOWED_AMENDMENT_REASONS: frozenset[str] = frozenset(
    {
        "tdd_green_production_fix",
        REASON_VERIFICATION_DIRECTIVE_DEFECT,
        REASON_MANIFEST_ROW_SUPERSEDED,
    }
)

VERIFICATION_AMENDMENT_ACTION: str = "VERIFICATION_AMENDMENT"
AMENDMENT_APPLIED_ACTION = "AMENDMENT_APPLIED"
AMENDMENT_REJECTED_ACTION = "AMENDMENT_REJECTED"
OPERATOR_AMENDMENT_ACTION = "OPERATOR_AMENDMENT"
MANIFEST_ROW_REMOVED_ACTION = "MANIFEST_ROW_REMOVED"
AMENDER_AGENT_ID = "agent/manifest-amender"
OPERATOR_AGENT_ID = "operator"
COMMENTS_SECTION_HEADER = "## Comments"

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
class VerificationPatch:
    """One ``## Verification`` directive rewrite requested in a
    ``verification_directive_defect`` amendment.

    ``before`` is the EXACT defective directive line as it appears in the
    work unit; ``after`` is the corrected line. ``cited_done_units`` names the
    DONE unit(s) whose landed change justifies the edit (required for
    stale-assertion removals and landed-rename alignments; may be empty for
    purely syntactic fixes). ``evidence`` is the tool-captured proof of the
    defect and is always mandatory.
    """

    before: str
    after: str
    cited_done_units: list[str] = dataclass_field(default_factory=list)
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.before or not self.before.strip():
            raise ValueError("VerificationPatch.before must be non-empty")
        if not self.after or not self.after.strip():
            raise ValueError("VerificationPatch.after must be non-empty")
        if not self.evidence or not self.evidence.strip():
            raise ValueError("VerificationPatch.evidence must be non-empty")
        if self.before.strip() == self.after.strip():
            raise ValueError("VerificationPatch.after must differ from before (no-op patch)")
        for unit_id in self.cited_done_units:
            if not unit_id or not str(unit_id).strip():
                raise ValueError("VerificationPatch.cited_done_units entries must be non-empty")


@dataclass(frozen=True)
class ManifestRowSupersededClaim:
    """One Changes Manifest row removal requested in a
    ``manifest_row_superseded`` amendment.

    ``row_path`` is the exact ``ManifestRow.file`` value to remove (the
    bare repo-relative path -- not the repo-prefixed display form).
    ``cited_done_units`` names the DONE unit(s) whose landed rename/delete
    explains why the file is absent on disk (always required: only landed
    sibling work justifies a manifest row removal). ``evidence`` is the
    tool-captured proof (e.g. the ``git log`` line for the rename) and is
    always mandatory.
    """

    row_path: str
    cited_done_units: list[str] = dataclass_field(default_factory=list)
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.row_path or not self.row_path.strip():
            raise ValueError("ManifestRowSupersededClaim.row_path must be non-empty")
        if self.row_path != self.row_path.strip():
            raise ValueError(
                f"ManifestRowSupersededClaim.row_path must not have leading/trailing whitespace: {self.row_path!r}"
            )
        if not self.evidence or not self.evidence.strip():
            raise ValueError("ManifestRowSupersededClaim.evidence must be non-empty")
        if not self.cited_done_units:
            raise ValueError(
                "ManifestRowSupersededClaim.cited_done_units must name at least one DONE unit "
                "(only landed sibling work justifies removing a manifest row)."
            )
        for unit_id in self.cited_done_units:
            if not unit_id or not str(unit_id).strip():
                raise ValueError("ManifestRowSupersededClaim.cited_done_units entries must be non-empty")


@dataclass(frozen=True)
class AmendmentRequest:
    """Serialised form of an amendment request emitted by the executor.

    Operator-mode fields (issue #242 / Appendix D-7):

    ``operator_mode`` -- when ``True`` the request bypasses the in-progress status
    gate and the LLM judge step; the amendment is applied synchronously in the
    CLI call with Layer-3 post-check.

    ``files_to_remove`` -- file paths to remove from the Changes Manifest table.

    ``target_repository`` -- when non-empty, the new target-repository value to
    write into the work-unit's Target Repository section.

    ``description_patch`` -- when non-empty, replacement text for the Description
    section (the whole section body, not a line-level diff).

    ``approach_patch`` -- when non-empty, replacement text for the Approach section.

    ``title_patch`` -- when non-empty, the new task title (the H1 heading text).

    ``dod_patch`` -- when non-empty, replacement text for the Definition of Done
    section.

    ``section_patches`` -- mapping of section header (e.g. ``"## Related Specs"``)
    to replacement body text; applied after the named single-section patch fields.

    ``verification_patches`` -- list of :class:`VerificationPatch` directive
    rewrites; only valid (and required) when ``reason`` is
    ``verification_directive_defect``, in which case ``files_to_add`` must be
    empty (a directive amendment never also touches the Manifest).
    """

    task_id: str
    requested_at: str
    reason: str
    justification: str
    files_to_add: list[AmendmentFileEntry]
    linked_acs: list[str]
    operator_mode: bool = False
    files_to_remove: list[str] = dataclass_field(default_factory=list)
    target_repository: str = ""
    description_patch: str = ""
    approach_patch: str = ""
    title_patch: str = ""
    dod_patch: str = ""
    section_patches: dict[str, str] = dataclass_field(default_factory=dict)
    verification_patches: list[VerificationPatch] = dataclass_field(default_factory=list)
    manifest_row_superseded_claims: list[ManifestRowSupersededClaim] = dataclass_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the request as a JSON-serialisable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AmendmentRequest:
        """Build an ``AmendmentRequest`` from a parsed JSON dict.

        Raises ``ValueError`` on missing keys, wrong types, or invalid
        field values. Validates all operator-mode fields (Appendix D-7).
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

        operator_mode = _parse_operator_mode(data)
        files_to_remove = _parse_files_to_remove(data)
        target_repository = _parse_string_field(data, "target_repository")
        description_patch = _parse_string_field(data, "description_patch")
        approach_patch = _parse_string_field(data, "approach_patch")
        title_patch = _parse_title_patch(data)
        dod_patch = _parse_string_field(data, "dod_patch")
        section_patches = _parse_section_patches(data)
        verification_patches = _parse_verification_patches(data)
        manifest_row_superseded_claims = _parse_manifest_row_superseded_claims(data)

        return cls(
            task_id=task_id,
            requested_at=str(data["requested_at"]),
            reason=reason,
            justification=justification,
            files_to_add=files,
            linked_acs=linked_acs,
            operator_mode=operator_mode,
            files_to_remove=files_to_remove,
            target_repository=target_repository,
            description_patch=description_patch,
            approach_patch=approach_patch,
            title_patch=title_patch,
            dod_patch=dod_patch,
            section_patches=section_patches,
            verification_patches=verification_patches,
            manifest_row_superseded_claims=manifest_row_superseded_claims,
        )


def _require_keys(data: dict[str, Any], keys: list[str]) -> None:
    """Raise ``ValueError`` if any key in ``keys`` is missing from ``data``."""
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")


def _parse_operator_mode(data: dict[str, Any]) -> bool:
    """Parse and validate the ``operator_mode`` field (Appendix D-7).

    Raises ``ValueError`` with the canonical per-field error string when the
    value is present but is not a JSON boolean.  Absent means False.
    """
    raw = data.get("operator_mode", False)
    if not isinstance(raw, bool):
        raise ValueError(f"operator_mode must be a bool, got {type(raw).__name__}")
    return raw


def _parse_files_to_remove(data: dict[str, Any]) -> list[str]:
    """Parse and validate the ``files_to_remove`` field (Appendix D-7).

    Each entry must be a non-empty string file path.
    """
    raw = data.get("files_to_remove", [])
    if not isinstance(raw, list):
        raise ValueError(f"files_to_remove must be a list, got {type(raw).__name__}")
    result: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError(f"files_to_remove entries must be strings, got {type(entry).__name__}")
        if not entry.strip():
            raise ValueError("files_to_remove entries must be non-empty strings")
        result.append(entry)
    return result


def _parse_string_field(data: dict[str, Any], field: str) -> str:
    """Parse and validate a string-typed operator-mode patch field (Appendix D-7).

    Accepts absent (defaults to ``""``) or a JSON string.  Raises ``ValueError``
    with the canonical per-field error when the value is present but not a string.
    """
    raw = data.get(field, "")
    if not isinstance(raw, str):
        raise ValueError(f"{field} must be a string, got {type(raw).__name__}")
    return raw


def _parse_title_patch(data: dict[str, Any]) -> str:
    """Parse and validate the ``title_patch`` field (Appendix D-7).

    Must be a string when present; booleans are explicitly rejected because
    JSON ``true``/``false`` are a common mistake when the field was intended
    to carry a flag rather than a title string.
    """
    raw = data.get("title_patch", "")
    if isinstance(raw, bool):
        raise ValueError(f"title_patch must be a string, got {type(raw).__name__}")
    if not isinstance(raw, str):
        raise ValueError(f"title_patch must be a string, got {type(raw).__name__}")
    return raw


def _parse_section_patches(data: dict[str, Any]) -> dict[str, str]:
    """Parse and validate the ``section_patches`` field (Appendix D-7).

    Must be a JSON object whose keys and values are all strings.
    """
    raw = data.get("section_patches", {})
    if not isinstance(raw, dict):
        raise ValueError(f"section_patches must be a dict, got {type(raw).__name__}")
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"section_patches keys must be strings, got {type(key).__name__}")
        if not isinstance(value, str):
            raise ValueError(f"section_patches values must be strings, got {type(value).__name__}")
    return dict(raw)


def _parse_verification_patches(data: dict[str, Any]) -> list[VerificationPatch]:
    """Parse the optional ``verification_patches`` list from a request dict.

    Raises ``ValueError`` on a non-list value or malformed entries.
    """
    raw = data.get("verification_patches", [])
    if not isinstance(raw, list):
        raise ValueError(f"verification_patches must be a list, got {type(raw).__name__}")
    patches: list[VerificationPatch] = []
    for entry in raw:
        if isinstance(entry, VerificationPatch):
            patches.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"verification_patches entries must be objects, got {type(entry).__name__}")
        _require_keys(entry, ["before", "after", "evidence"])
        cited_raw = entry.get("cited_done_units", [])
        if not isinstance(cited_raw, list):
            raise ValueError(f"cited_done_units must be a list, got {type(cited_raw).__name__}")
        patches.append(
            VerificationPatch(
                before=str(entry["before"]),
                after=str(entry["after"]),
                cited_done_units=[str(x) for x in cited_raw],
                evidence=str(entry["evidence"]),
            )
        )
    return patches


def _parse_manifest_row_superseded_claims(data: dict[str, Any]) -> list[ManifestRowSupersededClaim]:
    """Parse the optional ``manifest_row_superseded_claims`` list from a request dict.

    Raises ``ValueError`` on a non-list value or malformed entries.
    """
    raw = data.get("manifest_row_superseded_claims", [])
    if not isinstance(raw, list):
        raise ValueError(f"manifest_row_superseded_claims must be a list, got {type(raw).__name__}")
    claims: list[ManifestRowSupersededClaim] = []
    for entry in raw:
        if isinstance(entry, ManifestRowSupersededClaim):
            claims.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"manifest_row_superseded_claims entries must be objects, got {type(entry).__name__}")
        _require_keys(entry, ["row_path", "evidence"])
        cited_raw = entry.get("cited_done_units", [])
        if not isinstance(cited_raw, list):
            raise ValueError(f"cited_done_units must be a list, got {type(cited_raw).__name__}")
        claims.append(
            ManifestRowSupersededClaim(
                row_path=str(entry["row_path"]),
                cited_done_units=[str(x) for x in cited_raw],
                evidence=str(entry["evidence"]),
            )
        )
    return claims


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


def apply_amendment(
    workspace_root: Path,
    backlog_index: Path,
    task_id: str,
    *,
    repo_path: Path | None = None,
    staged_files: frozenset[str] | None = None,
) -> None:
    """Apply an approved amendment atomically with Layer 3 post-check.

    Reads the pending amendment request, appends its rows to the work-unit's
    Changes Manifest, writes an audit comment, performs Layer 3
    post-checks, and deletes the request on success. On any post-check
    failure the work-unit file is restored to its pre-amendment content and
    ``AmendmentError`` is raised -- the caller is expected to log a
    REVIEW_FAIL verdict.

    ``repo_path`` and ``staged_files`` are required only for the
    ``manifest_row_superseded`` reason, whose deterministic guards inspect the
    target repo's working tree (the row's file must be absent on disk) and the
    staged diff (it must not touch the removed path). The CLI resolves both
    generically from the unit's target repo. Other reasons ignore them.
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

    if request.reason == REASON_VERIFICATION_DIRECTIVE_DEFECT:
        patched_content = _apply_verification_patches(original_content, request, backlog_index)
        audit_entry = _build_verification_audit_entry(request)
        final_content = _append_audit_comment(patched_content, audit_entry)
        atomic_write_text(wu_file, final_content)
        try:
            _post_check(final_content, backlog_index)
        except AmendmentError:
            atomic_write_text(wu_file, original_content)
            raise
        delete_request(workspace_root, task_id)
        logger.info(
            "Verification amendment applied for %s: %d directive(s), reason=%s",
            task_id,
            len(request.verification_patches),
            request.reason,
        )
        return

    if request.reason == REASON_MANIFEST_ROW_SUPERSEDED:
        patched_content = _apply_manifest_row_superseded(
            original_content, request, backlog_index, repo_path=repo_path, staged_files=staged_files
        )
        audit_entry = _build_manifest_row_superseded_audit_entry(request)
        final_content = _append_audit_comment(patched_content, audit_entry)
        atomic_write_text(wu_file, final_content)
        try:
            _post_check(final_content, backlog_index)
        except AmendmentError:
            atomic_write_text(wu_file, original_content)
            raise
        delete_request(workspace_root, task_id)
        logger.info(
            "Manifest-row-superseded amendment applied for %s: %d row(s) removed, reason=%s",
            task_id,
            len(request.manifest_row_superseded_claims),
            request.reason,
        )
        return

    try:
        manifest_rows = [ManifestRow(file=f.path, change=f.change) for f in request.files_to_add]
    except ValueError as exc:
        raise AmendmentError(f"Amendment contains invalid manifest row: {exc}") from exc

    working_content = _apply_files_to_remove(original_content, request)

    try:
        content_with_rows = append_rows(working_content, manifest_rows)
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


_VERIFY_LINE_PREFIX = "- VERIFY"


def _parse_directive_line(line: str, *, role: str) -> VerificationItem:
    """Parse one ``- VERIFY ...`` directive line into a :class:`VerificationItem`.

    Raises ``AmendmentError`` (naming *role*: ``before`` or ``after``) when the
    line is not a parseable verification directive.
    """
    stripped = line.strip()
    if not stripped.startswith(_VERIFY_LINE_PREFIX):
        raise AmendmentError(f"Verification patch {role!r} line is not a '- VERIFY' directive: {stripped[:80]!r}")
    body = stripped[len(_VERIFY_LINE_PREFIX) :]
    try:
        return parse_verification_item(body)
    except ValueError as exc:
        raise AmendmentError(f"Verification patch {role!r} line does not parse as a directive: {exc}") from exc


def _check_patch_invariants(patch: VerificationPatch) -> None:
    """Deterministic guards: a rewritten directive can never be weaker.

    The AC ids, directive ``type=``, and ``expect-exit`` of ``after`` must be
    identical to ``before``. Raises ``AmendmentError`` on any violation.
    """
    before_item = _parse_directive_line(patch.before, role="before")
    after_item = _parse_directive_line(patch.after, role="after")
    if set(before_item.ac_ids) != set(after_item.ac_ids):
        raise AmendmentError(
            f"Verification patch changes the directive's AC ids "
            f"({sorted(before_item.ac_ids)} -> {sorted(after_item.ac_ids)}); AC id changes are never allowed."
        )
    if before_item.vtype is not after_item.vtype:
        raise AmendmentError(
            f"Verification patch changes the directive's type= "
            f"({before_item.vtype.value} -> {after_item.vtype.value}); type changes are never allowed."
        )
    if before_item.expect_exit != after_item.expect_exit:
        raise AmendmentError(
            f"Verification patch changes the directive's expect-exit "
            f"({before_item.expect_exit} -> {after_item.expect_exit}); expect-exit changes are never allowed."
        )


def _check_cited_units_done(cited: set[str], backlog_index: Path, *, context: str) -> None:
    """Every cited unit must exist in the backlog index with status ``done``.

    ``context`` names the citation source (``"Verification patch"`` /
    ``"Manifest-row-superseded claim"``) so the error message is actionable.
    A no-op when ``cited`` is empty.
    """
    if not cited:
        return
    parser = BacklogParser(
        backlog_root=backlog_index.parent / "backlog",
        backlog_index=backlog_index,
    )
    try:
        units = {unit.id: unit for unit in parser.parse_index()}
    except (FileNotFoundError, ValueError) as exc:
        raise AmendmentError(f"Cannot read backlog index {backlog_index}: {exc}") from exc
    for unit_id in sorted(cited):
        unit = units.get(unit_id)
        if unit is None:
            raise AmendmentError(f"{context} cites unit {unit_id} which does not exist in the backlog index.")
        if unit.status is not WorkUnitStatus.DONE:
            raise AmendmentError(
                f"{context} cites unit {unit_id} with status {unit.status.value!r}; "
                "cited units must be status done (only landed work justifies the edit)."
            )


def _check_citations_done(patches: list[VerificationPatch], backlog_index: Path) -> None:
    """Every cited unit in *patches* must be status ``done`` in the index."""
    cited = {unit_id for patch in patches for unit_id in patch.cited_done_units}
    _check_cited_units_done(cited, backlog_index, context="Verification patch")


def _apply_verification_patches(content: str, request: AmendmentRequest, backlog_index: Path) -> str:
    """Rewrite each patched directive line inside the ``## Verification`` section.

    Enforces the deterministic guards (same AC ids / type / expect-exit, cited
    units done, exact ``before`` line present in the section) and returns the
    patched work-unit content. Raises ``AmendmentError`` on any violation; the
    caller owns atomic write + rollback.
    """
    for patch in request.verification_patches:
        _check_patch_invariants(patch)
    _check_citations_done(request.verification_patches, backlog_index)

    lines = content.splitlines(keepends=True)
    section_start: int | None = None
    section_end = len(lines)
    for idx, line in enumerate(lines):
        if line.strip() == "## Verification":
            section_start = idx
            continue
        if section_start is not None and line.startswith("## ") and idx > section_start:
            section_end = idx
            break
    if section_start is None:
        raise AmendmentError("Work unit has no '## Verification' section; cannot apply a verification patch.")

    for patch in request.verification_patches:
        target = patch.before.strip()
        replaced = False
        for idx in range(section_start + 1, section_end):
            if lines[idx].strip() == target:
                newline = "\n" if lines[idx].endswith("\n") else ""
                lines[idx] = patch.after.strip() + newline
                replaced = True
                break
        if not replaced:
            raise AmendmentError(
                f"Verification patch 'before' line not found in the '## Verification' section: {target[:120]!r}"
            )
    return "".join(lines)


def _build_verification_audit_entry(request: AmendmentRequest) -> str:
    """Render the ``[VERIFICATION_AMENDMENT]`` audit comment for the work unit."""
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = []
    for patch in request.verification_patches:
        cited = ", ".join(patch.cited_done_units) if patch.cited_done_units else "(none)"
        parts.append(
            f"before: {patch.before.strip()} | after: {patch.after.strip()} | "
            f"cited: {cited} | evidence: {patch.evidence.strip()}"
        )
    body = " || ".join(parts)
    return (
        f"[{timestamp}] [{AMENDER_AGENT_ID}] [{VERIFICATION_AMENDMENT_ACTION}] applied "
        f"{len(request.verification_patches)} directive patch(es); reason={request.reason}; "
        f"justification: {request.justification}; {body}\n"
    )


def _apply_manifest_row_superseded(
    content: str,
    request: AmendmentRequest,
    backlog_index: Path,
    *,
    repo_path: Path | None,
    staged_files: frozenset[str] | None,
) -> str:
    """Remove each claimed stale row after enforcing the deterministic guards.

    For every claim the guards require, BEFORE any removal:

    * ``repo_path`` and ``staged_files`` were supplied (the CLI resolves both
      from the unit's target repo). Missing either is a caller error.
    * The row's file is ABSENT on disk in the target repo (never remove a row
      whose file still exists).
    * The staged diff does not touch the removed path (a path the executor is
      actively staging is not "superseded").
    * Every cited unit is status ``done`` in the backlog index.

    Returns the content with the claimed rows removed (via
    :func:`devbench.backlog.manifest.remove_rows`). Raises ``AmendmentError``
    on any violation or missing row; the caller owns atomic write + rollback.
    """
    if repo_path is None:
        raise AmendmentError(
            "manifest_row_superseded amendment requires the target repo path to verify the row's "
            "file is absent on disk; none was provided."
        )
    if staged_files is None:
        raise AmendmentError(
            "manifest_row_superseded amendment requires the staged-file set to verify the staged diff "
            "does not touch the removed path; none was provided."
        )

    cited: set[str] = set()
    for claim in request.manifest_row_superseded_claims:
        row_path = claim.row_path.strip()
        if (repo_path / row_path).exists():
            raise AmendmentError(
                f"manifest_row_superseded claim names row {row_path!r} but its file still exists on disk "
                f"under {repo_path}; a row whose file is present is not superseded and is never removed."
            )
        if row_path in staged_files:
            raise AmendmentError(
                f"manifest_row_superseded claim names row {row_path!r} which appears in the staged diff; "
                "a path the executor is actively staging cannot be superseded."
            )
        cited.update(claim.cited_done_units)

    _check_cited_units_done(cited, backlog_index, context="Manifest-row-superseded claim")

    paths_to_remove = [claim.row_path.strip() for claim in request.manifest_row_superseded_claims]
    try:
        return remove_rows(content, paths_to_remove)
    except ManifestRowNotFoundError as exc:
        raise AmendmentError(f"Cannot remove manifest row(s): {exc}") from exc
    except ManifestParseError as exc:
        raise AmendmentError(f"Cannot apply manifest_row_superseded amendment: {exc}") from exc


def _build_manifest_row_superseded_audit_entry(request: AmendmentRequest) -> str:
    """Render the ``[MANIFEST_ROW_REMOVED]`` audit comment for the work unit."""
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = []
    for claim in request.manifest_row_superseded_claims:
        cited = ", ".join(claim.cited_done_units) if claim.cited_done_units else "(none)"
        parts.append(f"row: {claim.row_path.strip()} | cited: {cited} | evidence: {claim.evidence.strip()}")
    body = " || ".join(parts)
    return (
        f"[{timestamp}] [{AMENDER_AGENT_ID}] [{MANIFEST_ROW_REMOVED_ACTION}] removed "
        f"{len(request.manifest_row_superseded_claims)} superseded row(s); reason={request.reason}; "
        f"justification: {request.justification}; {body}\n"
    )


def apply_operator_amendment(backlog_index: Path, task_id: str, request: AmendmentRequest) -> int:
    """Apply an operator-mode amendment synchronously with Layer-3 post-check.

    Unlike :func:`apply_amendment`, this function does not read from a pending
    request file and does not call the LLM judge. It takes a fully constructed
    :class:`AmendmentRequest` (with ``operator_mode=True``), applies the declared
    manifest rows and patch fields atomically, runs Layer-3 post-check, restores
    the original file on failure, and writes the operator-amendment audit entry
    to the work-unit ``## Comments`` section.

    Returns the Layer-3 validate-backlog exit code (0 on success, non-zero if the
    post-check fires and the file was restored). The audit entry always carries the
    actual return code: ``[OPERATOR_AMENDMENT] applied; layer3=validate-backlog rc=<n>``.

    Raises ``AmendmentError`` on any irrecoverable error (task not found, manifest
    parse error, etc.).
    """
    wu_file = _resolve_task_file(backlog_index, task_id)
    original_content = wu_file.read_text(encoding="utf-8")

    working_content = original_content

    working_content = _apply_files_to_remove(working_content, request)

    if request.files_to_add:
        try:
            manifest_rows = [ManifestRow(file=f.path, change=f.change) for f in request.files_to_add]
        except ValueError as exc:
            raise AmendmentError(f"Operator amendment contains invalid manifest row: {exc}") from exc
        try:
            working_content = append_rows(working_content, manifest_rows)
        except ManifestParseError as exc:
            raise AmendmentError(f"Cannot apply operator amendment: {exc}") from exc

    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    audit_rc = 0
    try:
        _post_check(working_content, backlog_index)
    except AmendmentError:
        audit_rc = 1

    audit_entry = (
        f"[{timestamp}] [{OPERATOR_AGENT_ID}] [{OPERATOR_AMENDMENT_ACTION}] applied; "
        f"layer3=validate-backlog rc={audit_rc}; "
        f"reason={request.reason}; justification: {request.justification}"
        f"{_removed_rows_audit_fragment(request)}\n"
    )
    final_content = _append_audit_comment(working_content, audit_entry)

    atomic_write_text(wu_file, final_content)

    if audit_rc != 0:
        atomic_write_text(wu_file, original_content)
        raise AmendmentError(
            f"Operator amendment Layer-3 post-check failed for {task_id} (rc={audit_rc}); "
            "work-unit file restored to pre-amendment content."
        )

    logger.info(
        "Operator amendment applied for %s: %d file(s) added, %d row(s) removed, reason=%s",
        task_id,
        len(request.files_to_add),
        len(request.files_to_remove),
        request.reason,
    )
    return audit_rc


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

    archive_rejected_request(workspace_root, task_id)
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
            return unit.file_path

    raise AmendmentError(f"Task {task_id} not found in backlog index {backlog_index}")


def _removed_rows_audit_fragment(request: AmendmentRequest) -> str:
    """Render the ``[MANIFEST_ROW_REMOVED]`` audit fragment naming removed rows.

    Returns an empty string when nothing was removed so callers can append it
    unconditionally to their audit line.
    """
    if not request.files_to_remove:
        return ""
    rows = ", ".join(request.files_to_remove)
    return f"; [{MANIFEST_ROW_REMOVED_ACTION}] {rows}"


def _apply_files_to_remove(content: str, request: AmendmentRequest) -> str:
    """Remove ``request.files_to_remove`` rows from the Changes Manifest.

    Returns ``content`` unchanged when nothing is requested for removal.
    Wraps :func:`devbench.backlog.manifest.remove_rows` so the manifest-layer
    errors surface as :class:`AmendmentError` with an actionable message:
    a missing section, a malformed manifest, or a requested path that is not
    present all fail fast. No partial removal is applied.
    """
    if not request.files_to_remove:
        return content
    try:
        return remove_rows(content, request.files_to_remove)
    except ManifestRowNotFoundError as exc:
        raise AmendmentError(f"Cannot remove manifest row(s): {exc}") from exc
    except ManifestParseError as exc:
        raise AmendmentError(f"Cannot apply amendment: {exc}") from exc


def _build_audit_entry(
    request: AmendmentRequest,
    action: str,
    rejection_reason: str = "",
) -> str:
    """Render a one-line audit entry for the Comments section."""
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    if action == AMENDMENT_APPLIED_ACTION:
        file_count = len(request.files_to_add)
        message = (
            f"{request.reason}; added {file_count} file(s); "
            f"justification: {request.justification}{_removed_rows_audit_fragment(request)}"
        )
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
        operator_mode: bool = False,
    ) -> None:
        """Run every check in a fixed order. Raise ``AmendmentError`` on the first failure.

        ``staged_files`` is the set of file paths the executor has staged in
        git against the base branch; pass ``None`` to skip the in-diff check
        (for contexts where git access is unavailable, such as unit tests of
        earlier checks). ``prior_applied_count`` is the number of amendments
        already applied to this task in the current executor run.

        ``operator_mode`` -- when ``True`` the in-progress status gate
        (:meth:`check_task_exists_and_in_progress`) is skipped so operator-
        initiated amendments can be applied to tasks in any status. All other
        checks (enabled, reason, rate-limit) still run in operator mode.
        """
        self.check_enabled()
        self.check_reason_allowed(request)
        self.check_verification_patch_shape(request)
        self.check_manifest_row_superseded_shape(request)
        self.check_rate_limit(prior_applied_count)
        if not operator_mode:
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
        """The request's reason must be permitted for this backlog.

        ``verification_directive_defect`` is governed by the dedicated
        ``manifest_amendment.allow_verification_directive_amendments`` flag
        (default on) rather than ``allowed_reasons``, so workspaces toggle it
        without re-declaring the standard reason list.
        """
        if request.reason == REASON_VERIFICATION_DIRECTIVE_DEFECT:
            if not self._config.allow_verification_directive_amendments:
                raise AmendmentError(
                    "Verification-directive amendments are disabled for this backlog. "
                    "Set manifest_amendment.allow_verification_directive_amendments: true "
                    "in backlog/config/devbench.yaml to enable."
                )
            return
        if request.reason == REASON_MANIFEST_ROW_SUPERSEDED:
            if not self._config.allow_manifest_row_superseded_amendments:
                raise AmendmentError(
                    "Manifest-row-superseded amendments are disabled for this backlog. "
                    "Set manifest_amendment.allow_manifest_row_superseded_amendments: true "
                    "in backlog/config/devbench.yaml to enable."
                )
            return
        if request.reason not in self._config.allowed_reasons:
            raise AmendmentError(
                f"Amendment reason {request.reason!r} is not in allowed reasons for this backlog: "
                f"{sorted(self._config.allowed_reasons)}"
            )

    def check_verification_patch_shape(self, request: AmendmentRequest) -> None:
        """Verification patches and the directive-defect reason imply each other.

        A ``verification_directive_defect`` request must carry at least one
        patch and must NOT also touch the Manifest (``files_to_add`` empty);
        any other reason must not smuggle in ``verification_patches``.
        """
        if request.reason == REASON_VERIFICATION_DIRECTIVE_DEFECT:
            if not request.verification_patches:
                raise AmendmentError(
                    "verification_directive_defect amendments require at least one entry in verification_patches."
                )
            if request.files_to_add:
                raise AmendmentError(
                    "verification_directive_defect amendments must not carry files_to_add -- "
                    "a directive amendment never also touches the Changes Manifest."
                )
        elif request.verification_patches:
            raise AmendmentError(
                f"verification_patches are only valid with reason "
                f"{REASON_VERIFICATION_DIRECTIVE_DEFECT!r}, got {request.reason!r}."
            )

    def check_manifest_row_superseded_shape(self, request: AmendmentRequest) -> None:
        """Manifest-row-superseded claims and the reason imply each other.

        A ``manifest_row_superseded`` request must carry at least one claim and
        must NOT also touch the Manifest via ``files_to_add`` (the amendment
        only removes rows); any other reason must not smuggle in
        ``manifest_row_superseded_claims``.
        """
        if request.reason == REASON_MANIFEST_ROW_SUPERSEDED:
            if not request.manifest_row_superseded_claims:
                raise AmendmentError(
                    "manifest_row_superseded amendments require at least one entry in manifest_row_superseded_claims."
                )
            if request.files_to_add:
                raise AmendmentError(
                    "manifest_row_superseded amendments must not carry files_to_add -- "
                    "a row-removal amendment never also adds Changes Manifest rows."
                )
        elif request.manifest_row_superseded_claims:
            raise AmendmentError(
                f"manifest_row_superseded_claims are only valid with reason "
                f"{REASON_MANIFEST_ROW_SUPERSEDED!r}, got {request.reason!r}."
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
