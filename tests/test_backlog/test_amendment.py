"""Tests for devbench.backlog.amendment module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from devbench.backlog.amendment import (
    ALLOWED_AMENDMENT_REASONS,
    AMENDER_AGENT_ID,
    AMENDMENT_APPLIED_ACTION,
    AMENDMENT_DIR_NAME,
    AMENDMENT_REJECTED_ACTION,
    AmendmentError,
    AmendmentFileEntry,
    AmendmentRequest,
    _append_audit_comment,
    _build_audit_entry,
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
            AmendmentRequest.from_dict("a string")  # type: ignore[arg-type]

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

    def test_rollback_when_post_check_fails_via_em_dash(self, tmp_workspace: Path) -> None:
        # Craft a request whose "change" text contains an em-dash substring encoded
        # *post* parse_manifest's em-dash guard. ManifestRow rejects em-dash at
        # construction, so we bypass that by injecting directly into the .md.
        # Instead, simulate a corrupted workspace: the BACKLOG.md is deliberately
        # out of sync, so validate-backlog will fail after the write.
        task_id = "EX-F1-S1-T1"
        write_request(tmp_workspace, _sample_request(task_id))

        # Damage BACKLOG.md so validate-backlog fails (dep references nonexistent ID)
        backlog_md = tmp_workspace / "BACKLOG.md"
        current = backlog_md.read_text()
        damaged = current.replace(
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | None |",
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | NONEXISTENT-ID |",
        )
        backlog_md.write_text(damaged)

        wu_file = tmp_workspace / "backlog" / f"{task_id}.md"
        before = wu_file.read_text(encoding="utf-8")

        with pytest.raises(AmendmentError, match="Post-check"):
            apply_amendment(tmp_workspace, backlog_md, task_id)

        # Rollback: work-unit file restored to pre-amendment content
        after = wu_file.read_text(encoding="utf-8")
        assert after == before


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


# ---------------------------------------------------------------------------
# ALLOWED_AMENDMENT_REASONS is not empty (regression guard)
# ---------------------------------------------------------------------------


class TestAllowedReasonsConstant:
    def test_has_at_least_one_reason(self) -> None:
        assert len(ALLOWED_AMENDMENT_REASONS) >= 1
        assert "tdd_green_production_fix" in ALLOWED_AMENDMENT_REASONS


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
