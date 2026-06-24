"""CLI-layer tests for operator-mode in cmd_request_amendment (issue #242).

AC-242-1: request-amendment <id> --operator-mode parses, bypasses the in-progress
gate and the LLM judge, runs Layer-3, applies synchronously, and writes the audit
marker [OPERATOR_AMENDMENT] applied; layer3=validate-backlog rc=<n>.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.backlog.amendment import request_path

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | {ip} | 0 | {bl} |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | {status} | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""


WORK_UNIT_TEMPLATE = """\
# EX-F1-S1-T1: Sample Task

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

## Comments
"""


def _build_workspace(tmp_path: Path, status: str = "in-progress") -> Path:
    ip_count = "1" if status == "in-progress" else "0"
    bl_count = "1" if status == "blocked" else "0"
    (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE.format(status=status, ip=ip_count, bl=bl_count))
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "EX-F1-S1-T1.md").write_text(
        WORK_UNIT_TEMPLATE.format(status=status),
        encoding="utf-8",
    )
    return tmp_path


def _operator_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "reason": "tdd_green_production_fix",
        "justification": "Operator fix: task was blocked and needs manifest update.",
        "files_to_add": [],
        "linked_acs": [],
        "operator_mode": True,
    }
    base.update(overrides)
    return base


def _normal_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "reason": "tdd_green_production_fix",
        "justification": "AC-TEST-001 needs a production fix.",
        "files_to_add": [
            {"path": "src/example/new_parser.py", "change": "new production parser"},
        ],
        "linked_acs": ["AC-TEST-001"],
    }
    base.update(overrides)
    return base


class TestRequestAmendmentIsVariadic:
    """request-amendment must appear in _VARIADIC_COMMANDS so --operator-mode is passed through."""

    def test_variadic_commands_includes_request_amendment(self) -> None:
        assert "request-amendment" in cli._VARIADIC_COMMANDS


class TestRequestAmendmentNormalMode:
    """Without --operator-mode the old behaviour is preserved: write pending request."""

    def test_normal_mode_writes_pending_request(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_normal_payload())))
        with patch("devbench.cli.WORKSPACE_ROOT", workspace):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 0, capsys.readouterr().err
        assert request_path(workspace, "EX-F1-S1-T1").exists()

    def test_normal_mode_does_not_write_audit_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = _build_workspace(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_normal_payload())))
        with patch("devbench.cli.WORKSPACE_ROOT", workspace):
            cli.cmd_request_amendment("EX-F1-S1-T1")
        wu_content = (workspace / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "[OPERATOR_AMENDMENT]" not in wu_content

    def test_normal_mode_does_not_apply_synchronously(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """In normal mode the manifest must not be modified by request-amendment."""
        workspace = _build_workspace(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_normal_payload())))
        with patch("devbench.cli.WORKSPACE_ROOT", workspace):
            cli.cmd_request_amendment("EX-F1-S1-T1")
        wu_content = (workspace / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "new_parser.py" not in wu_content


class TestRequestAmendmentOperatorModeBypassesInProgressGate:
    """--operator-mode skips the in-progress check so blocked tasks can be amended.

    The in-progress gate in the standard (non-operator) flow is enforced by
    PreFilter.check_task_exists_and_in_progress, which the orchestrator's
    manifest-amender agent invokes -- not by cmd_request_amendment itself.
    In operator mode the gate is skipped entirely (amendment applied synchronously).
    """

    def test_operator_mode_on_blocked_task_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload(
            files_to_add=[{"path": "src/example/new.py", "change": "add new module"}],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err

    def test_operator_mode_applies_directly_without_writing_pending_request(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Operator mode applies inline -- no pending request file is written."""
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload(
            files_to_add=[{"path": "src/example/new.py", "change": "add new module"}],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err
        from devbench.backlog.amendment import request_path as _rp

        assert not _rp(workspace, "EX-F1-S1-T1").exists()


class TestRequestAmendmentOperatorModeAppliesSynchronously:
    """--operator-mode applies the amendment immediately without writing a pending file."""

    def test_operator_mode_does_not_write_pending_request(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload(
            files_to_add=[{"path": "src/example/new.py", "change": "add new module"}],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err
        assert not request_path(workspace, "EX-F1-S1-T1").exists()

    def test_operator_mode_adds_file_to_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload(
            files_to_add=[{"path": "src/example/new.py", "change": "add new module"}],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err
        wu_content = (workspace / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "src/example/new.py" in wu_content

    def test_operator_mode_on_in_progress_task_also_works(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="in-progress")
        payload = _operator_payload(
            files_to_add=[{"path": "src/example/new2.py", "change": "add helper"}],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err


class TestRequestAmendmentOperatorModeAuditMarker:
    """Operator mode writes [OPERATOR_AMENDMENT] applied; layer3=validate-backlog rc=<n>."""

    def test_audit_marker_written_to_wu_comments(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload(
            files_to_add=[{"path": "src/example/new.py", "change": "add new module"}],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err
        wu_content = (workspace / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "[OPERATOR_AMENDMENT]" in wu_content
        assert "layer3=validate-backlog" in wu_content

    def test_audit_marker_contains_rc(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload()
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0, capsys.readouterr().err
        wu_content = (workspace / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "rc=0" in wu_content


class TestRequestAmendmentOperatorModeLayer3:
    """Layer-3 post-check runs; if it fails the WU is restored and rc=1."""

    def test_em_dash_in_files_to_add_path_causes_layer3_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The em-dash post-check fires if the patch content contains U+2014."""
        workspace = _build_workspace(tmp_path, status="blocked")
        wu_path = workspace / "backlog" / "EX-F1-S1-T1.md"
        original = wu_path.read_text(encoding="utf-8")
        wu_path.write_text(original + "\n\u2014 em-dash line\n", encoding="utf-8")
        payload = _operator_payload()
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err

    def test_layer3_failure_restores_original_content(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """On Layer-3 failure the WU file must be restored byte-for-byte."""
        workspace = _build_workspace(tmp_path, status="blocked")
        wu_path = workspace / "backlog" / "EX-F1-S1-T1.md"
        original_content = wu_path.read_text(encoding="utf-8")
        wu_path.write_text(original_content + "\n\u2014 em-dash line\n", encoding="utf-8")
        content_before = wu_path.read_text(encoding="utf-8")
        payload = _operator_payload()
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        content_after = wu_path.read_text(encoding="utf-8")
        assert content_after == content_before


class TestRequestAmendmentOperatorModeOutput:
    """cmd_request_amendment in operator mode prints a JSON summary to stdout."""

    def test_operator_mode_prints_json_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        payload = _operator_payload()
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["task_id"] == "EX-F1-S1-T1"
        assert out["status"] == "applied"
        assert out["operator_mode"] is True


class TestRequestAmendmentOperatorModeInvalidPayload:
    """Operator mode still validates the JSON schema -- bad payload returns rc=1."""

    def test_missing_reason_field_returns_rc1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        bad_payload = {
            "justification": "some fix",
            "files_to_add": [],
            "linked_acs": [],
            "operator_mode": True,
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bad_payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err

    def test_bad_operator_mode_type_returns_rc1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path, status="blocked")
        bad_payload = _operator_payload(operator_mode="yes")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bad_payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR:" in captured.err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("files_to_remove", ["tests/test_example.py"]),
        ("target_repository", "new-org/new-repo"),
        ("description_patch", "Updated description text."),
        ("approach_patch", "Updated approach text."),
        ("title_patch", "New Task Title"),
        ("dod_patch", "- [ ] Updated DoD."),
        ("section_patches", {"## Related Specifications": "- AC-242-1."}),
    ],
)
def test_operator_patch_fields_accepted_by_cli(
    field: str,
    value: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each of the seven new patch fields must be accepted without error in operator mode."""
    workspace = _build_workspace(tmp_path, status="blocked")
    payload = _operator_payload(**{field: value})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with (
        patch("devbench.cli.WORKSPACE_ROOT", workspace),
        patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
    ):
        rc = cli.cmd_request_amendment("EX-F1-S1-T1", "--operator-mode")
    assert rc == 0, capsys.readouterr().err
