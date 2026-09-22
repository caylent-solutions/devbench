"""Tests for devbench.backlog.amendment module."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from devbench.backlog.amendment import (
    ALLOWED_AMENDMENT_REASONS,
    AMENDER_AGENT_ID,
    AMENDER_REJECTION_CATEGORIES,
    AMENDMENT_APPLIED_ACTION,
    AMENDMENT_DIR_NAME,
    AMENDMENT_REJECTED_ACTION,
    DOC_SYNC_REVIEW_FIX_REASON,
    AmendmentError,
    AmendmentFileEntry,
    AmendmentRequest,
    _append_audit_comment,
    _build_audit_entry,
    _categorise_rejection_reason,
    apply_amendment,
    delete_request,
    read_request,
    reject_amendment,
    request_path,
    write_request,
)
from devbench.backlog.manifest import EM_DASH, parse_manifest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


WORK_UNIT_TEMPLATE = """\
# {task_id}: {title}

## Status: {status}

## Description

Test task description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 new test asserts something
- [ ] AC-FUNC-001 something works end-to-end

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | add new tests |

## Definition of Done

- [ ] All AC checked
"""


BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Build a minimal workspace with BACKLOG.md + one in-progress task file."""
    task_id = "EX-F1-S1-T1"
    (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / f"{task_id}.md").write_text(
        WORK_UNIT_TEMPLATE.format(task_id=task_id, title="Sample Task", status="in-progress"),
        encoding="utf-8",
    )
    return tmp_path


def _sample_request_dict(task_id: str = "EX-F1-S1-T1") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "requested_at": "2026-04-18T00:00:00+00:00",
        "reason": "tdd_green_production_fix",
        "justification": "Test required production fix to handle BOM.",
        "files_to_add": [
            {"path": "src/example/example.py", "change": "use utf-8-sig codec"},
        ],
        "linked_acs": ["AC-TEST-001"],
    }


def _sample_request(task_id: str = "EX-F1-S1-T1") -> AmendmentRequest:
    return AmendmentRequest.from_dict(_sample_request_dict(task_id))


# ---------------------------------------------------------------------------
# AmendmentFileEntry
# ---------------------------------------------------------------------------


class TestAmendmentFileEntry:
    def test_valid(self) -> None:
        e = AmendmentFileEntry(path="src/a.py", change="fix thing")
        assert e.path == "src/a.py"

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AmendmentFileEntry(path="", change="x")

    def test_whitespace_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="leading/trailing whitespace"):
            AmendmentFileEntry(path=" src/a.py ", change="x")

    def test_empty_change_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AmendmentFileEntry(path="src/a.py", change="")

    def test_whitespace_change_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AmendmentFileEntry(path="src/a.py", change="   ")


# ---------------------------------------------------------------------------
# AmendmentRequest.from_dict / to_dict
# ---------------------------------------------------------------------------


class TestAmendmentRequestRoundTrip:
    def test_from_dict_valid(self) -> None:
        req = AmendmentRequest.from_dict(_sample_request_dict())
        assert req.task_id == "EX-F1-S1-T1"
        assert req.reason == "tdd_green_production_fix"
        assert len(req.files_to_add) == 1
        assert req.files_to_add[0].path == "src/example/example.py"
        assert req.linked_acs == ["AC-TEST-001"]

    def test_round_trip_via_dict(self) -> None:
        original = _sample_request()
        restored = AmendmentRequest.from_dict(original.to_dict())
        assert restored == original

    def test_non_dict_input_rejected(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            AmendmentRequest.from_dict(cast(Any, "a string"))

    def test_missing_field_rejected(self) -> None:
        bad = _sample_request_dict()
        del bad["reason"]
        with pytest.raises(ValueError, match="missing required field"):
            AmendmentRequest.from_dict(bad)

    def test_files_to_add_not_list_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["files_to_add"] = "oops"
        with pytest.raises(ValueError, match="files_to_add must be a list"):
            AmendmentRequest.from_dict(bad)

    def test_file_entry_not_dict_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["files_to_add"] = ["just a string"]
        with pytest.raises(ValueError, match="files_to_add entries must be objects"):
            AmendmentRequest.from_dict(bad)

    def test_file_entry_missing_field_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["files_to_add"] = [{"path": "src/a.py"}]  # missing change
        with pytest.raises(ValueError, match="missing required field"):
            AmendmentRequest.from_dict(bad)

    def test_linked_acs_not_list_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["linked_acs"] = "AC-001"
        with pytest.raises(ValueError, match="linked_acs must be a list"):
            AmendmentRequest.from_dict(bad)

    def test_empty_task_id_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["task_id"] = "   "
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            AmendmentRequest.from_dict(bad)

    def test_empty_reason_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["reason"] = ""
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            AmendmentRequest.from_dict(bad)

    def test_empty_justification_rejected(self) -> None:
        bad = _sample_request_dict()
        bad["justification"] = ""
        with pytest.raises(ValueError, match="justification must be a non-empty string"):
            AmendmentRequest.from_dict(bad)


# ---------------------------------------------------------------------------
# request_path / write_request / read_request / delete_request
# ---------------------------------------------------------------------------


class TestRequestFileLifecycle:
    def test_request_path(self, tmp_path: Path) -> None:
        p = request_path(tmp_path, "EX-F1-S1-T1")
        assert p == tmp_path / AMENDMENT_DIR_NAME / "EX-F1-S1-T1.json"

    def test_write_read_round_trip(self, tmp_path: Path) -> None:
        req = _sample_request()
        written = write_request(tmp_path, req)
        assert written.exists()
        loaded = read_request(tmp_path, req.task_id)
        assert loaded == req

    def test_write_creates_parent_directory(self, tmp_path: Path) -> None:
        req = _sample_request()
        assert not (tmp_path / AMENDMENT_DIR_NAME).exists()
        write_request(tmp_path, req)
        assert (tmp_path / AMENDMENT_DIR_NAME).is_dir()

    def test_write_rejects_duplicate(self, tmp_path: Path) -> None:
        req = _sample_request()
        write_request(tmp_path, req)
        with pytest.raises(AmendmentError, match="already exists"):
            write_request(tmp_path, req)

    def test_write_rejects_unknown_reason(self, tmp_path: Path) -> None:
        data = _sample_request_dict()
        data["reason"] = "unknown_reason_type"
        req = AmendmentRequest.from_dict(data)
        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            write_request(tmp_path, req)

    def test_read_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(AmendmentError, match="No pending amendment request"):
            read_request(tmp_path, "EX-F1-S1-T1")

    def test_read_invalid_json(self, tmp_path: Path) -> None:
        path = request_path(tmp_path, "EX-F1-S1-T1")
        path.parent.mkdir(parents=True)
        path.write_text("{not valid json")
        with pytest.raises(AmendmentError, match="not valid JSON"):
            read_request(tmp_path, "EX-F1-S1-T1")

    def test_read_schema_violation(self, tmp_path: Path) -> None:
        path = request_path(tmp_path, "EX-F1-S1-T1")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"task_id": "EX-F1-S1-T1"}))  # missing fields
        with pytest.raises(AmendmentError, match="not valid"):
            read_request(tmp_path, "EX-F1-S1-T1")

    def test_delete_noop_when_absent(self, tmp_path: Path) -> None:
        delete_request(tmp_path, "EX-F1-S1-T1")  # should not raise

    def test_delete_removes_existing(self, tmp_path: Path) -> None:
        req = _sample_request()
        written = write_request(tmp_path, req)
        delete_request(tmp_path, req.task_id)
        assert not written.exists()


class TestArchiveRejectedRequest:
    """archive_rejected_request moves the pending JSON to rejected-requests/."""

    def test_no_pending_returns_none(self, tmp_path: Path) -> None:
        from devbench.backlog.amendment import archive_rejected_request

        assert archive_rejected_request(tmp_path, "EX-F1-S1-T1") is None

    def test_moves_file_to_archive(self, tmp_path: Path) -> None:
        from devbench.backlog.amendment import archive_rejected_request

        req = _sample_request()
        written = write_request(tmp_path, req)
        archive = archive_rejected_request(tmp_path, req.task_id)
        assert archive is not None
        assert archive.is_file()
        assert archive.parent == tmp_path / ".devbench" / "rejected-requests"
        assert archive.name.startswith(req.task_id + "-")
        assert archive.suffix == ".json"
        assert not written.exists()


# ---------------------------------------------------------------------------
# apply_amendment
# ---------------------------------------------------------------------------


class TestApplyAmendment:
    def test_happy_path(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))

        apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        updated = wu_file.read_text(encoding="utf-8")

        # Manifest now has 2 rows
        rows = parse_manifest(updated)
        assert len(rows) == 2
        assert rows[0].file == "tests/test_example.py"
        assert rows[1].file == "src/example/example.py"

        # Audit comment written
        assert AMENDMENT_APPLIED_ACTION in updated
        assert AMENDER_AGENT_ID in updated
        assert "tdd_green_production_fix" in updated

        # Request file deleted
        assert not request_path(tmp_workspace, task_id).exists()

    def test_missing_request_raises(self, tmp_workspace: Path) -> None:
        with pytest.raises(AmendmentError, match="No pending amendment"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", "EX-F1-S1-T1")

    def test_task_id_mismatch_raises(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        # Write request with correct task_id, but invoke apply with a different task_id argument.
        write_request(tmp_workspace, _sample_request(task_id))
        other_id = "OTHER-ID"
        # apply_amendment reads the request at request_path(workspace, other_id), so it won't find it.
        with pytest.raises(AmendmentError, match="No pending amendment"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", other_id)

    def test_disallowed_reason_raises(self, tmp_workspace: Path) -> None:
        # Write a request with an allowed reason so it gets stored,
        # then mutate the stored JSON to a disallowed reason and attempt to apply.
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))
        rp = request_path(tmp_workspace, task_id)
        data = json.loads(rp.read_text())
        data["reason"] = "unauthorized_reason"
        rp.write_text(json.dumps(data))
        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

    def test_preexisting_unrelated_error_does_not_roll_back_amendment(
        self, tmp_workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """FR-10 / AC-20: the whole-backlog validate() check is baseline-relative.

        A backlog error that predates the amendment (here: BACKLOG.md's
        dependency cell references a nonexistent ID) is captured in
        ``baseline_errors`` before the write; since the amendment does not
        introduce that error, it must not roll back. The surviving error is
        logged as a WARNING, never silently dropped.
        """
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))

        # Damage BACKLOG.md BEFORE the amendment is applied so the error
        # already exists in the baseline (dep references nonexistent ID).
        backlog_md = tmp_workspace / "BACKLOG.md"
        current = backlog_md.read_text()
        damaged = current.replace(
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | None |",
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | NONEXISTENT-ID |",
        )
        backlog_md.write_text(damaged)

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"

        with caplog.at_level(logging.WARNING, logger="devbench.backlog.amendment"):
            apply_amendment(tmp_workspace, backlog_md, task_id)

        # No rollback: the amendment applied, manifest now has 2 rows.
        updated = wu_file.read_text(encoding="utf-8")
        rows = parse_manifest(updated)
        assert len(rows) == 2
        assert rows[1].file == "src/example/example.py"

        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("NONEXISTENT-ID" in msg for msg in warning_messages), (
            f"expected a WARNING naming the surviving pre-existing error; got {warning_messages}"
        )

    def test_amendment_introduced_error_rolls_back(self, tmp_workspace: Path) -> None:
        """FR-10 / AC-21: an amendment that introduces a NEW validate error rolls back.

        The requested Manifest row (a glob pattern) trips the deterministic
        no-glob-in-manifest rule, which did not fire on the pre-amendment
        backlog. The rollback must restore the work-unit file byte-for-byte.
        """
        task_id = "EX-F1-S1-T1"
        data = _sample_request_dict(task_id)
        data["files_to_add"] = [
            {"path": "docs/glob*.md", "change": "glob path introduces a new backlog integrity error"}
        ]
        write_request(tmp_workspace, AmendmentRequest.from_dict(data))

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        before = wu_file.read_text(encoding="utf-8")

        with pytest.raises(AmendmentError, match=r"introduced \d+ new backlog integrity error"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

        # Rollback: work-unit file restored byte-for-byte to pre-amendment content
        after = wu_file.read_text(encoding="utf-8")
        assert after == before

    def test_clean_amendment_over_clean_backlog_applies(
        self, tmp_workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A clean amendment over an unmodified backlog applies with no warnings logged."""
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))

        with caplog.at_level(logging.WARNING, logger="devbench.backlog.amendment"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        updated = wu_file.read_text(encoding="utf-8")
        rows = parse_manifest(updated)
        assert len(rows) == 2

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records == [], (
            f"expected no WARNING logs for a clean amendment; got {[r.getMessage() for r in warning_records]}"
        )


# ---------------------------------------------------------------------------
# reject_amendment
# ---------------------------------------------------------------------------


class TestRejectAmendment:
    def test_happy_path(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))

        reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id, "unrelated files")

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        updated = wu_file.read_text(encoding="utf-8")

        # Status flipped to blocked
        assert "## Status: blocked" in updated

        # Audit comment written
        assert AMENDMENT_REJECTED_ACTION in updated
        assert "unrelated files" in updated

        # Manifest unchanged (still one row)
        rows = parse_manifest(updated)
        assert len(rows) == 1
        assert rows[0].file == "tests/test_example.py"

        # Pending request file moved into the rejected-requests archive
        assert not request_path(tmp_workspace, task_id).exists()
        archive_dir = tmp_workspace / ".devbench" / "rejected-requests"
        assert archive_dir.is_dir()
        assert any(p.name.startswith(task_id + "-") and p.suffix == ".json" for p in archive_dir.iterdir())

    def test_empty_reason_rejected(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))
        with pytest.raises(AmendmentError, match="non-empty rejection_reason"):
            reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id, "   ")

    def test_missing_request_raises(self, tmp_workspace: Path) -> None:
        with pytest.raises(AmendmentError, match="No pending amendment"):
            reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", "EX-F1-S1-T1", "because")

    def test_rejected_archive_exists_before_mark_blocked_runs(
        self, tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for #210: ``reject_amendment`` must write the rejected-requests
        archive BEFORE calling ``BacklogManager.mark_blocked`` so the
        ``classify_blocked_task`` recovery-signal check inside ``mark_blocked``
        sees the archive and returns ``AWAITING_AMENDMENT_RECOVERY``, not
        ``OPERATOR_ACTION_REQUIRED``.  Pre-fix order had mark_blocked first
        and the archive write last; the classifier mislabeled the bucket and
        the wrong per-class Slack toggle fired.
        """
        from devbench.backlog import manager as manager_mod

        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))

        archive_dir = tmp_workspace / ".devbench" / "rejected-requests"

        original_set_status = manager_mod.BacklogManager._set_status
        observed: dict[str, bool] = {"archive_present_at_mark_blocked": False}

        def _spy(
            self: manager_mod.BacklogManager,
            work_unit_path: Path,
            backlog_index: Path,
            unit_id: str,
            status: str,
        ) -> None:
            # At the moment mark_blocked transitions state (and triggers
            # classify_blocked_task), the archive must already be on disk.
            if status == "blocked" and unit_id == task_id:
                observed["archive_present_at_mark_blocked"] = archive_dir.is_dir() and any(
                    p.name.startswith(task_id + "-") and p.suffix == ".json" for p in archive_dir.iterdir()
                )
            return original_set_status(self, work_unit_path, backlog_index, unit_id, status)

        monkeypatch.setattr(manager_mod.BacklogManager, "_set_status", _spy)

        reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id, "approach forbids prod-code edits")

        assert observed["archive_present_at_mark_blocked"], (
            "rejected-requests archive must be on disk BEFORE mark_blocked runs so "
            "classify_blocked_task sees the AWAITING_AMENDMENT_RECOVERY signal"
        )


# ---------------------------------------------------------------------------
# ALLOWED_AMENDMENT_REASONS is not empty (regression guard)
# ---------------------------------------------------------------------------


class TestAllowedReasonsConstant:
    def test_has_at_least_one_reason(self) -> None:
        assert len(ALLOWED_AMENDMENT_REASONS) >= 1
        assert "tdd_green_production_fix" in ALLOWED_AMENDMENT_REASONS


# ---------------------------------------------------------------------------
# FR-11 Leg A1: doc_sync_review_fix -- a second, path-restricted amendment
# reason for doc_review-mandated out-of-Manifest documentation fixes.
# ---------------------------------------------------------------------------


class TestDocSyncReviewFixReason:
    def test_write_request_accepts_doc_sync_review_fix(self, tmp_path: Path) -> None:
        data = _sample_request_dict()
        data["reason"] = "doc_sync_review_fix"
        data["files_to_add"] = [{"path": "docs/manifest-amendments.md", "change": "document the new reason"}]
        req = AmendmentRequest.from_dict(data)

        written = write_request(tmp_path, req)

        assert written.exists()
        loaded = read_request(tmp_path, req.task_id)
        assert loaded.reason == "doc_sync_review_fix"

    def test_apply_amendment_accepts_doc_sync_review_fix(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        data = _sample_request_dict(task_id)
        data["reason"] = "doc_sync_review_fix"
        data["files_to_add"] = [{"path": "docs/example.md", "change": "sync doc with new behavior"}]
        write_request(tmp_workspace, AmendmentRequest.from_dict(data))

        apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        updated = wu_file.read_text(encoding="utf-8")
        rows = parse_manifest(updated)
        assert any(row.file == "docs/example.md" for row in rows)
        assert "doc_sync_review_fix" in updated

    def test_doc_sync_review_fix_rejects_non_doc_path(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        data = _sample_request_dict(task_id)
        data["reason"] = "doc_sync_review_fix"
        data["files_to_add"] = [{"path": "src/foo.py", "change": "unrelated production fix"}]

        with pytest.raises(AmendmentError, match="only permits documentation"):
            write_request(tmp_workspace, AmendmentRequest.from_dict(data))

        # apply_amendment enforces the same guard even when a pending request
        # bypasses write_request's own check (e.g. a hand-edited request file).
        path = request_path(tmp_workspace, task_id)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        with pytest.raises(AmendmentError, match="only permits documentation"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

    def test_doc_sync_review_fix_accepts_md_and_doc_pin_test(self, tmp_path: Path) -> None:
        data = _sample_request_dict()
        data["reason"] = "doc_sync_review_fix"
        data["files_to_add"] = [
            {"path": "docs/example.md", "change": "sync doc wording"},
            {"path": "tests/test_docs/test_example_doc_pin.py", "change": "pin new doc wording"},
        ]
        req = AmendmentRequest.from_dict(data)

        written = write_request(tmp_path, req)

        assert written.exists()


# ---------------------------------------------------------------------------
# Edge cases for apply_amendment task_id / manifest / em-dash
# ---------------------------------------------------------------------------


class TestApplyAmendmentEdgeCases:
    def test_task_id_mismatch_between_file_and_arg(self, tmp_workspace: Path) -> None:
        # Write a request file at path keyed by "EX-F1-S1-T1" but with inner task_id "OTHER".
        task_id_in_file = "EX-F1-S1-T1"
        data = _sample_request_dict(task_id="DIFFERENT-ID")
        path = request_path(tmp_workspace, task_id_in_file)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        with pytest.raises(AmendmentError, match="does not match argument"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id_in_file)

    def test_files_to_add_with_em_dash_raises(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        data = _sample_request_dict(task_id)
        # Inject em-dash into change text. AmendmentFileEntry doesn't check for em-dash,
        # but ManifestRow does when apply_amendment converts.
        data["files_to_add"] = [{"path": "src/a.py", "change": f"fix{EM_DASH}bug"}]
        path = request_path(tmp_workspace, task_id)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        with pytest.raises(AmendmentError, match="invalid manifest row"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

    def test_work_unit_without_manifest_section_raises(self, tmp_path: Path) -> None:
        # Build a workspace where the task's .md file has no Changes Manifest.
        task_id = "EX-F1-S1-T1"
        (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
        (tmp_path / "backlog").mkdir()
        (tmp_path / "backlog" / f"{task_id}.md").write_text(
            f"# {task_id}: Sample Task\n\n## Status: in-progress\n\n## Description\n\nNo manifest here.\n"
        )
        write_request(tmp_path, _sample_request(task_id))

        with pytest.raises(AmendmentError, match="section not found"):
            apply_amendment(tmp_path, tmp_path / "BACKLOG.md", task_id)

    def test_post_check_em_dash_via_justification_triggers_rollback(self, tmp_workspace: Path) -> None:
        task_id = "EX-F1-S1-T1"
        # Write a request whose justification contains an em-dash. AmendmentRequest
        # does not reject em-dash in justification; it flows into the audit comment,
        # which Layer 3 post-check catches and rolls back.
        data = _sample_request_dict(task_id)
        data["justification"] = f"Required because{EM_DASH}BOM"
        path = request_path(tmp_workspace, task_id)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        before = wu_file.read_text(encoding="utf-8")

        with pytest.raises(AmendmentError, match="em-dash"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id)

        after = wu_file.read_text(encoding="utf-8")
        assert after == before


class TestRejectAmendmentEdgeCases:
    def test_task_id_mismatch_between_file_and_arg(self, tmp_workspace: Path) -> None:
        task_id_in_file = "EX-F1-S1-T1"
        data = _sample_request_dict(task_id="DIFFERENT-ID")
        path = request_path(tmp_workspace, task_id_in_file)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        with pytest.raises(AmendmentError, match="does not match argument"):
            reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id_in_file, "bad")


# ---------------------------------------------------------------------------
# _resolve_task_file error paths (exercised via apply_amendment)
# ---------------------------------------------------------------------------


class TestResolveTaskFileErrorPaths:
    def test_missing_backlog_index_raises(self, tmp_path: Path) -> None:
        # Workspace has no BACKLOG.md at all.
        (tmp_path / "backlog").mkdir()
        write_request(tmp_path, _sample_request("EX-F1-S1-T1"))
        with pytest.raises(AmendmentError, match="Cannot read backlog index"):
            apply_amendment(tmp_path, tmp_path / "BACKLOG.md", "EX-F1-S1-T1")

    def test_task_not_in_backlog_raises(self, tmp_workspace: Path) -> None:
        # BACKLOG.md has EX-F1-S1-T1, but we request amendment for a different task.
        other_id = "EX-F1-S1-T99"
        data = _sample_request_dict(task_id=other_id)
        path = request_path(tmp_workspace, other_id)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        with pytest.raises(AmendmentError, match="not found in backlog"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", other_id)

    def test_missing_work_unit_file_raises(self, tmp_path: Path) -> None:
        # BACKLOG.md references a file that doesn't exist on disk.
        (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
        (tmp_path / "backlog").mkdir()
        # Intentionally do NOT create backlog/EX-F1-S1-T1.md
        with pytest.raises((FileNotFoundError, AmendmentError)):
            write_request(tmp_path, _sample_request("EX-F1-S1-T1"))
            apply_amendment(tmp_path, tmp_path / "BACKLOG.md", "EX-F1-S1-T1")


# ---------------------------------------------------------------------------
# Internal helper direct tests
# ---------------------------------------------------------------------------


class TestBuildAuditEntry:
    def test_unknown_action_rejected(self) -> None:
        req = _sample_request()
        with pytest.raises(ValueError, match="Unknown audit action"):
            _build_audit_entry(req, "not_a_real_action")


class TestAppendAuditComment:
    def test_appends_when_comments_section_exists(self) -> None:
        content = "# T\n\n## Description\n\nsome text\n\n## Comments\n\n[prior] entry\n"
        entry = "[new] line\n"
        out = _append_audit_comment(content, entry)
        # The new entry should be after the existing content
        assert "[prior] entry" in out
        assert "[new] line" in out
        assert out.index("[prior] entry") < out.index("[new] line")

    def test_creates_comments_section_when_missing(self) -> None:
        content = "# T\n\n## Description\n\nsome text\n"
        entry = "[new] line\n"
        out = _append_audit_comment(content, entry)
        assert "## Comments" in out
        assert "[new] line" in out


class TestAmenderRejectionPersistsFeedbackJson:
    """Issue #154 + #156: every ``reject_amendment`` call writes a
    structured feedback JSON. As of #156 the path is shared with the
    other review judges at
    ``.devbench/review-failures/<task-id>-manifest_amender-<n>.json``;
    the legacy ``.devbench/amender-rejections/`` directory remains as a
    forward-compat read path tested separately below.
    """

    def test_rejection_writes_structured_json(self, tmp_workspace: Path) -> None:
        from devbench.backlog.amendment import REVIEW_FAILURES_DIR_NAME

        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))
        reject_amendment(
            tmp_workspace,
            tmp_workspace / "BACKLOG.md",
            task_id,
            "SCOPE: amendment is out of scope for this task",
        )

        archive_dir = tmp_workspace / REVIEW_FAILURES_DIR_NAME
        assert archive_dir.is_dir()
        json_files = sorted(archive_dir.glob(f"{task_id}-manifest_amender-*.json"))
        assert len(json_files) == 1
        payload = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert payload["task_id"] == task_id
        assert payload["attempt"] == 1
        assert payload["schema_version"] == 1
        assert payload["judge"] == "manifest_amender"
        assert payload["categories"][0]["code"] == "SCOPE"
        # Legacy fields preserved for downstream consumers still keyed off them.
        assert payload["reason_category"] == "SCOPE"
        assert "out of scope" in payload["reason_text"]
        assert payload["capped"] is False
        assert payload["request"]["task_id"] == task_id

    def test_attempt_counter_increments(self, tmp_workspace: Path) -> None:
        """A second rejection on the same task lands at attempt=2."""
        from devbench.backlog.amendment import REVIEW_FAILURES_DIR_NAME

        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))
        reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id, "SCOPE one")

        write_request(tmp_workspace, _sample_request(task_id))
        reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id, "APPROACH_AUTH two")

        archive_dir = tmp_workspace / REVIEW_FAILURES_DIR_NAME
        json_files = sorted(archive_dir.glob(f"{task_id}-manifest_amender-*.json"))
        assert len(json_files) == 2
        payloads = [json.loads(p.read_text()) for p in json_files]
        attempts = sorted(p["attempt"] for p in payloads)
        assert attempts == [1, 2]
        cats = {p["attempt"]: p["reason_category"] for p in payloads}
        assert cats == {1: "SCOPE", 2: "APPROACH_AUTH"}

    def test_unmatched_reason_falls_back_to_other(self, tmp_workspace: Path) -> None:
        """Reasons missing the canonical tokens default to ``OTHER``."""
        from devbench.backlog.amendment import REVIEW_FAILURES_DIR_NAME

        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))
        reject_amendment(
            tmp_workspace,
            tmp_workspace / "BACKLOG.md",
            task_id,
            "the amendment looks fine but we cannot accept it right now",
        )
        archive_dir = tmp_workspace / REVIEW_FAILURES_DIR_NAME
        payload = json.loads(next(archive_dir.glob(f"{task_id}-manifest_amender-*.json")).read_text())
        assert payload["reason_category"] == "OTHER"
        assert payload["categories"][0]["code"] == "OTHER"

    def test_capped_flag_set_when_attempt_exceeds_max(
        self,
        tmp_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once the per-task attempt count exceeds ``MAX_RETRY_ATTEMPTS`` the
        feedback record is still written but stamped ``capped: True``."""
        from devbench.backlog import amendment as amendment_mod
        from devbench.backlog.amendment import (
            AMENDER_REJECTIONS_DIR_NAME,
            REVIEW_FAILURES_DIR_NAME,
            persist_rejection_feedback,
        )

        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 1)

        task_id = "EX-F1-S1-T1"
        request = _sample_request(task_id)
        persist_rejection_feedback(
            workspace_root=tmp_workspace,
            task_id=task_id,
            rejection_reason="SCOPE first",
            request=request,
        )
        result_path = persist_rejection_feedback(
            workspace_root=tmp_workspace,
            task_id=task_id,
            rejection_reason="SCOPE second",
            request=request,
        )
        payload = json.loads(result_path.read_text())
        assert payload["attempt"] == 2
        assert payload["capped"] is True
        assert isinstance(amendment_mod.REVIEW_FAILURES_DIR_NAME, str)
        # Both directory constants stay exported -- legacy is forward-compat read-only.
        assert REVIEW_FAILURES_DIR_NAME == ".devbench/review-failures"
        assert AMENDER_REJECTIONS_DIR_NAME == ".devbench/amender-rejections"

    def test_review_failures_path_returned_by_reader(self, tmp_workspace: Path) -> None:
        """A JSON written via ``persist_rejection_feedback`` (new path) must
        be returned by ``read_review_failure_files``."""
        from devbench.backlog.amendment import read_review_failure_files

        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))
        reject_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", task_id, "SCOPE")
        paths = read_review_failure_files(tmp_workspace, task_id)
        assert any("review-failures" in str(p) for p in paths)
        assert all(task_id in p.name for p in paths)

    def test_legacy_amender_rejections_path_still_readable(
        self,
        tmp_workspace: Path,
    ) -> None:
        """Forward-compat: a JSON written under the legacy
        ``.devbench/amender-rejections/`` directory must still be returned by
        ``read_review_failure_files`` so prior runs are not orphaned."""
        from devbench.backlog.amendment import (
            AMENDER_REJECTIONS_DIR_NAME,
            read_review_failure_files,
        )

        task_id = "EX-F1-S1-T1"
        legacy_dir = tmp_workspace / AMENDER_REJECTIONS_DIR_NAME
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / f"{task_id}-1.json"
        legacy_file.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "attempt": 1,
                    "reason_category": "SCOPE",
                    "reason_text": "legacy reason",
                    "request": _sample_request_dict(task_id),
                    "capped": False,
                    "recorded_at": "2026-04-30T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        paths = read_review_failure_files(tmp_workspace, task_id)
        assert legacy_file in paths


# ---------------------------------------------------------------------------
# Per-backlog allowed_reasons enforcement
# ---------------------------------------------------------------------------


class TestPerBacklogAllowedReasons:
    """A backlog's configured ``allowed_reasons`` narrowing must actually be enforced.

    ``write_request`` / ``apply_amendment`` previously checked the module-level
    ``ALLOWED_AMENDMENT_REASONS`` (what devbench IMPLEMENTS) rather than the
    per-backlog config (what this backlog PERMITS), so a narrowing was loaded,
    schema-validated, and then ignored at every gate.
    """

    def test_write_request_refuses_reason_outside_configured_narrowing(self, tmp_workspace: Path) -> None:
        request = AmendmentRequest.from_dict(
            {
                **_sample_request_dict(),
                "reason": DOC_SYNC_REVIEW_FIX_REASON,
                "files_to_add": [{"path": "docs/thing.md", "change": "document it"}],
            }
        )
        narrowed = frozenset({"tdd_green_production_fix"})

        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            write_request(tmp_workspace, request, allowed_reasons=narrowed)

        assert not request_path(tmp_workspace, request.task_id).exists(), (
            "a refused request must not occupy the single pending-request slot"
        )

    def test_write_request_accepts_reason_inside_configured_narrowing(self, tmp_workspace: Path) -> None:
        request = _sample_request()
        written = write_request(tmp_workspace, request, allowed_reasons=frozenset({"tdd_green_production_fix"}))
        assert written.exists()

    def test_none_config_enforces_implemented_set(self, tmp_workspace: Path) -> None:
        """A direct library call with no config still refuses an unimplemented reason."""
        request = AmendmentRequest.from_dict({**_sample_request_dict(), "reason": "not_a_real_reason"})
        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            write_request(tmp_workspace, request, allowed_reasons=None)

    def test_config_cannot_widen_beyond_implemented_set(self, tmp_workspace: Path) -> None:
        """A config naming a reason devbench does not implement does not enable it."""
        request = AmendmentRequest.from_dict({**_sample_request_dict(), "reason": "invented_reason"})
        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            write_request(tmp_workspace, request, allowed_reasons=frozenset({"invented_reason"}))

    def test_apply_amendment_rechecks_against_configured_narrowing(self, tmp_workspace: Path) -> None:
        """Hand-editing a pending request's reason on disk cannot bypass the narrowing."""
        request = _sample_request()
        target = write_request(tmp_workspace, request, allowed_reasons=None)
        smuggled = json.loads(target.read_text(encoding="utf-8"))
        smuggled["reason"] = DOC_SYNC_REVIEW_FIX_REASON
        smuggled["files_to_add"] = [{"path": "docs/thing.md", "change": "document it"}]
        target.write_text(json.dumps(smuggled), encoding="utf-8")

        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            apply_amendment(
                tmp_workspace,
                tmp_workspace / "BACKLOG.md",
                request.task_id,
                allowed_reasons=frozenset({"tdd_green_production_fix"}),
            )


# ---------------------------------------------------------------------------
# files_to_remove -- Manifest row removal
# ---------------------------------------------------------------------------


class TestFilesToRemoveParsing:
    """``files_to_remove`` is optional, validated, and round-trips."""

    def test_absent_field_defaults_to_empty(self) -> None:
        """Requests written before this field existed must still parse."""
        request = AmendmentRequest.from_dict(_sample_request_dict())
        assert request.files_to_remove == []

    def test_round_trips_through_to_dict(self) -> None:
        payload = {**_sample_request_dict(), "files_to_remove": ["tests/test_stale.py"]}
        request = AmendmentRequest.from_dict(payload)
        assert request.files_to_remove == ["tests/test_stale.py"]
        assert AmendmentRequest.from_dict(request.to_dict()).files_to_remove == ["tests/test_stale.py"]

    def test_non_list_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="files_to_remove must be a list"):
            AmendmentRequest.from_dict({**_sample_request_dict(), "files_to_remove": "tests/test_stale.py"})

    def test_non_string_entry_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be strings"):
            AmendmentRequest.from_dict({**_sample_request_dict(), "files_to_remove": [{"path": "x"}]})

    def test_blank_entry_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            AmendmentRequest.from_dict({**_sample_request_dict(), "files_to_remove": ["  "]})

    def test_removal_only_request_is_valid(self) -> None:
        """Dropping a stale row is a complete amendment on its own."""
        request = AmendmentRequest.from_dict(
            {**_sample_request_dict(), "files_to_add": [], "files_to_remove": ["tests/test_stale.py"]}
        )
        assert request.files_to_add == []
        assert request.files_to_remove == ["tests/test_stale.py"]

    def test_empty_in_both_directions_is_rejected(self) -> None:
        """A request that changes nothing is a no-op, not a valid amendment."""
        with pytest.raises(ValueError, match="both empty"):
            AmendmentRequest.from_dict({**_sample_request_dict(), "files_to_add": [], "files_to_remove": []})


class TestApplyAmendmentWithRemoval:
    """Removals apply inside the same atomic envelope as additions."""

    def _seed_two_row_manifest(self, tmp_workspace: Path) -> Path:
        wu = tmp_workspace / "backlog" / "EX-F1-S1-T1.md"
        content = wu.read_text(encoding="utf-8").replace(
            "| `tests/test_example.py` | add new tests |",
            "| `tests/test_example.py` | add new tests |\n| `tests/test_stale.py` | modify |",
        )
        wu.write_text(content, encoding="utf-8")
        return wu

    def test_removal_drops_only_the_named_row(self, tmp_workspace: Path) -> None:
        wu = self._seed_two_row_manifest(tmp_workspace)
        request = AmendmentRequest.from_dict(
            {**_sample_request_dict(), "files_to_add": [], "files_to_remove": ["tests/test_stale.py"]}
        )
        write_request(tmp_workspace, request)

        apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", request.task_id)

        files = [row.file for row in parse_manifest(wu.read_text(encoding="utf-8"))]
        assert files == ["tests/test_example.py"]

    def test_removal_is_recorded_in_the_audit_trail(self, tmp_workspace: Path) -> None:
        """A dropped row changes what the unit may commit, so it must be visible."""
        wu = self._seed_two_row_manifest(tmp_workspace)
        request = AmendmentRequest.from_dict(
            {**_sample_request_dict(), "files_to_add": [], "files_to_remove": ["tests/test_stale.py"]}
        )
        write_request(tmp_workspace, request)

        apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", request.task_id)

        content = wu.read_text(encoding="utf-8")
        assert "removed 1 row(s): tests/test_stale.py" in content

    def test_add_and_remove_in_one_request(self, tmp_workspace: Path) -> None:
        """Both directions land together -- the combination AC-FINAL-015 can require.

        The added path is a test file so the source-test atomicity rule is not
        the thing under test here; that rule's interaction with removal is
        covered by ``test_post_check_rollback_restores_both_directions``.
        """
        wu = self._seed_two_row_manifest(tmp_workspace)
        request = AmendmentRequest.from_dict(
            {
                **_sample_request_dict(),
                "files_to_add": [{"path": "tests/test_new_helper.py", "change": "add tests"}],
                "files_to_remove": ["tests/test_stale.py"],
            }
        )
        write_request(tmp_workspace, request)

        apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", request.task_id)

        files = [row.file for row in parse_manifest(wu.read_text(encoding="utf-8"))]
        assert files == ["tests/test_example.py", "tests/test_new_helper.py"]

    def test_post_check_rollback_restores_both_directions(self, tmp_workspace: Path) -> None:
        """A Layer 3 failure rolls back the removal too -- never a half-amended Manifest.

        Adding a production source with no paired test violates the source-test
        atomicity rule, so ``_post_check`` rejects the amendment. The removal
        requested alongside it must be undone as well, proving both operations
        share one atomic envelope and that the existing post-check still guards
        a removal without modification.
        """
        wu = self._seed_two_row_manifest(tmp_workspace)
        before = wu.read_text(encoding="utf-8")
        request = AmendmentRequest.from_dict(
            {
                **_sample_request_dict(),
                "files_to_add": [{"path": "src/example/unpaired.py", "change": "add helper"}],
                "files_to_remove": ["tests/test_stale.py"],
            }
        )
        write_request(tmp_workspace, request)

        with pytest.raises(AmendmentError, match="no matching test"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", request.task_id)

        assert wu.read_text(encoding="utf-8") == before
        files = [row.file for row in parse_manifest(wu.read_text(encoding="utf-8"))]
        assert "tests/test_stale.py" in files, "the removal must be rolled back with the addition"

    def test_undeclared_removal_fails_and_leaves_file_untouched(self, tmp_workspace: Path) -> None:
        wu = tmp_workspace / "backlog" / "EX-F1-S1-T1.md"
        before = wu.read_text(encoding="utf-8")
        request = AmendmentRequest.from_dict(
            {**_sample_request_dict(), "files_to_add": [], "files_to_remove": ["tests/never_declared.py"]}
        )
        write_request(tmp_workspace, request)

        with pytest.raises(AmendmentError, match="not declared"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", request.task_id)

        assert wu.read_text(encoding="utf-8") == before

    def test_removing_every_row_is_refused(self, tmp_workspace: Path) -> None:
        """A unit with an empty Manifest has nothing to verify staged changes against."""
        request = AmendmentRequest.from_dict(
            {**_sample_request_dict(), "files_to_add": [], "files_to_remove": ["tests/test_example.py"]}
        )
        write_request(tmp_workspace, request)

        with pytest.raises(AmendmentError, match="at least one file"):
            apply_amendment(tmp_workspace, tmp_workspace / "BACKLOG.md", request.task_id)


# ---------------------------------------------------------------------------
# ``_categorise_rejection_reason`` -- earliest-named-token classification
# (issue #154 / #156 follow-up: a hardcoded iteration order over the
# taxonomy tuple let an incidentally-mentioned category beat the actually
# -declared one; see E3-F2-S1-T5).
# ---------------------------------------------------------------------------


# Verbatim ``reason_text`` archived at
# ``.devbench/review-failures/E3-F2-S1-T1-manifest_amender-2.json`` for the
# real rejection that surfaced this defect. Stored as a hermetic fixture
# string rather than read from disk at test time.
_E3_F2_S1_T1_REJECTION_REASON = (
    "PRE_FILTER pre-conflict (issue #137): both requested paths are already claimed by "
    "E10-F1-S1-T1 (status: blocked) in the same repo caylent-solutions/devbench -- "
    "'src/devbench/backlog/manager.py' and 'tests/test_backlog/test_manager.py' appear "
    "in that unit's Changes Manifest (lines 304, 308). 'blocked' is a HARD claimant "
    "status (BacklogManager._HARD_CLAIMANT_STATUSES) and is not terminal, so the "
    "done/declined auto-dep exception does not apply, and no ordering dependency exists "
    "in either direction (E3-F2-S1-T1 deps: E3-F1-S1-T2, E3-F2-S1-T3, E3-F2-S1-T4; "
    "E10-F1-S1-T1 deps: E9-F2-S1-T1). Appending these rows would author a new hard "
    "Manifest conflict. Resolve via dep wiring or a spec-correction recovery task before "
    "re-requesting. APPROACH_AUTH, SCOPE and JUSTIFICATION_COHERENCE all PASSED and no "
    "project-standards violations were found, so the underlying manager.py delegation "
    "fix is sound and should be re-requested once the E10-F1-S1-T1 claim is resolved."
)


class TestCategoriseRejectionReasonEarliestToken:
    """AC-FUNC-001 through AC-FUNC-005: earliest-named-token classification."""

    def test_real_pre_filter_rejection_classifies_as_pre_filter(self) -> None:
        """AC-FUNC-001: the archived E3-F2-S1-T1 rejection opens with
        'PRE_FILTER' but its narrative later names 'APPROACH_AUTH, SCOPE and
        JUSTIFICATION_COHERENCE' -- the earliest-declared token must win, not
        whichever tuple entry a hardcoded iteration order happens to hit
        first."""
        assert _categorise_rejection_reason(_E3_F2_S1_T1_REJECTION_REASON) == "PRE_FILTER"

    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            pytest.param(
                "APPROACH_AUTH FAIL: the Approach does not authorise this; SCOPE was fine.",
                "APPROACH_AUTH",
                id="approach_auth_named_first",
            ),
            pytest.param(
                "SCOPE FAIL: unrelated refactor; PRE_FILTER passed.",
                "SCOPE",
                id="scope_named_first",
            ),
        ],
    )
    def test_competing_tokens_resolve_by_earliest_occurrence(self, reason: str, expected: str) -> None:
        """AC-FUNC-002: whichever canonical token is named first in the
        reason string wins, regardless of any fixed iteration order over
        the taxonomy."""
        assert _categorise_rejection_reason(reason) == expected

    def test_no_token_falls_back_to_other(self) -> None:
        """AC-FUNC-004: a reason naming no canonical token falls back to 'OTHER'."""
        assert _categorise_rejection_reason("the amendment looks fine but we cannot accept it right now") == "OTHER"

    def test_matching_is_case_insensitive(self) -> None:
        """AC-FUNC-004: a lowercase token mention still classifies correctly."""
        assert _categorise_rejection_reason("this reason mentions pre_filter in lowercase") == "PRE_FILTER"

    @pytest.mark.parametrize("token", sorted(AMENDER_REJECTION_CATEGORIES - {"OTHER"}))
    @pytest.mark.parametrize(
        "suffix_template",
        [
            "{token} FAIL: reason text",
            "{token}: some narrative text",
        ],
    )
    def test_each_canonical_token_recognised_in_isolation(self, token: str, suffix_template: str) -> None:
        """AC-FUNC-003 + REFACTOR characterization: one parametrized case per
        canonical token (crossed with two independent narrative shapes),
        proving no token in the shared taxonomy constant is silently
        unrecognised by the classifier and that the candidate list is
        derived from ``AMENDER_REJECTION_CATEGORIES`` (minus ``OTHER``)
        rather than a fourth hand-copied tuple literal that could silently
        drop one. Each token reports independently rather than stopping at
        the first failure."""
        assert _categorise_rejection_reason(suffix_template.format(token=token)) == token

    @pytest.mark.parametrize(
        "reason",
        [
            _E3_F2_S1_T1_REJECTION_REASON,
            "APPROACH_AUTH FAIL: the Approach does not authorise this; SCOPE was fine.",
            "SCOPE FAIL: unrelated refactor; PRE_FILTER passed.",
            "the amendment looks fine but we cannot accept it right now",
            "this reason mentions pre_filter in lowercase",
        ],
    )
    def test_result_is_always_a_member_of_the_taxonomy(self, reason: str) -> None:
        """AC-FUNC-005: the returned value is always a member of
        ``AMENDER_REJECTION_CATEGORIES``, asserted directly rather than
        assumed."""
        assert _categorise_rejection_reason(reason) in AMENDER_REJECTION_CATEGORIES
