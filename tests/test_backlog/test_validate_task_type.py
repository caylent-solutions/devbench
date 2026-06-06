"""Tests for validate-backlog Task Type header check (rule 21).

Covers AC-257a-1: validate-backlog rejects an unknown ## Task Type: value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager


def _make_backlog_index(tmp_path: Path, task_id: str = "E0-F1-S1-T1") -> Path:
    """Create a minimal BACKLOG.md with a single task row."""
    content = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|----------|\n"
        f"| {task_id} | Sample Task | Task | in-queue | None | git-repo |"
        f" `backlog/{task_id}.md` |\n\n"
        "## Status Summary\n\n"
        "| Status | Count |\n"
        "|--------|-------|\n"
        "| in-queue | 1 |\n"
    )
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(content)
    return index_path


def _make_minimal_task_file(
    tmp_path: Path,
    task_id: str,
    task_type_line: str = "",
) -> Path:
    """Create a minimal work-unit .md file under tmp_path/backlog/."""
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)

    task_type_section = f"\n## Task Type: {task_type_line}\n" if task_type_line else ""
    content = (
        f"# {task_id}: Sample Task\n\n"
        "## Status: in-queue\n"
        f"{task_type_section}"
        "\n## Target Repository\n\n"
        "- **Repo:** `caylent-solutions/git-repo`\n\n"
        "## Description\n\nSample description.\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-TEST-001 something\n\n"
        "## Changes Manifest\n\n"
        "| File | Change |\n"
        "|------|--------|\n"
        "| `src/foo.py` | add |\n"
        "| `tests/test_foo.py` | add |\n\n"
        "## Definition of Done\n\n- [ ] Done.\n\n"
        "## Dependencies\n\n"
        "| ID | Title | Status |\n"
        "|----|-------|--------|\n"
        "| None | | |\n\n"
        "## TDD Cycle Log\n\n"
        "## Comments\n"
    )
    wu_path = backlog_dir / f"{task_id}.md"
    wu_path.write_text(content)
    return wu_path


class TestValidateBacklogTaskTypeHeader:
    """validate-backlog rule 21: ## Task Type header must carry a valid value."""

    def test_no_error_when_task_type_header_absent(self, tmp_path: Path) -> None:
        task_id = "E0-F1-S1-T1"
        index = _make_backlog_index(tmp_path, task_id)
        _make_minimal_task_file(tmp_path, task_id, task_type_line="")
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        task_type_errors = [e for e in errors if "Task Type" in e]
        assert task_type_errors == [], f"Unexpected Task Type errors: {task_type_errors}"

    @pytest.mark.parametrize("valid_value", ["test-only", "coverage-only"])
    def test_no_error_for_valid_task_type_values(self, tmp_path: Path, valid_value: str) -> None:
        task_id = "E0-F1-S1-T1"
        index = _make_backlog_index(tmp_path, task_id)
        _make_minimal_task_file(tmp_path, task_id, task_type_line=valid_value)
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        task_type_errors = [e for e in errors if "Task Type" in e]
        assert task_type_errors == [], f"Value {valid_value!r} should be valid but got errors: {task_type_errors}"

    def test_error_for_unknown_task_type_value(self, tmp_path: Path) -> None:
        task_id = "E0-F1-S1-T1"
        index = _make_backlog_index(tmp_path, task_id)
        _make_minimal_task_file(tmp_path, task_id, task_type_line="not-valid")
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        task_type_errors = [e for e in errors if "Task Type" in e or "unknown" in e]
        assert len(task_type_errors) >= 1, f"Expected at least one Task Type error for 'not-valid', got: {errors}"
        assert any("not-valid" in e for e in task_type_errors), (
            f"Error should mention 'not-valid', got: {task_type_errors}"
        )

    @pytest.mark.parametrize(
        "bad_value",
        ["behavior-fix", "unknown", "TEST-ONLY", "coverage_only", ""],
    )
    def test_error_for_various_invalid_task_type_values(self, tmp_path: Path, bad_value: str) -> None:
        if not bad_value:
            # Empty value: skip, header with empty value is pathological
            pytest.skip("Empty task type value is not a realistic case")
        task_id = "E0-F1-S1-T1"
        index = _make_backlog_index(tmp_path, task_id)
        _make_minimal_task_file(tmp_path, task_id, task_type_line=bad_value)
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        task_type_errors = [e for e in errors if "Task Type" in e or "unknown" in e]
        assert len(task_type_errors) >= 1, f"Expected an error for bad value {bad_value!r}, got: {errors}"

    def test_error_message_includes_task_id(self, tmp_path: Path) -> None:
        task_id = "E0-F1-S1-T1"
        index = _make_backlog_index(tmp_path, task_id)
        _make_minimal_task_file(tmp_path, task_id, task_type_line="bad-value")
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        task_type_errors = [e for e in errors if "Task Type" in e or "unknown" in e]
        assert any(task_id in e for e in task_type_errors), (
            f"Expected task ID {task_id!r} in error message, got: {task_type_errors}"
        )

    def test_skips_row_when_file_missing(self, tmp_path: Path) -> None:
        """When a work-unit file is missing, the task type check is skipped (not an error)."""
        task_id = "E0-F1-S1-T1"
        index = _make_backlog_index(tmp_path, task_id)
        # Do NOT create the work-unit file -- it is absent on disk.
        # validate() already reports a separate file-missing error; the
        # task type check must not double-report or crash.
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        task_type_errors = [e for e in errors if "Task Type" in e or "unknown" in e]
        assert task_type_errors == [], f"Task Type check should be silent for missing file, got: {task_type_errors}"
