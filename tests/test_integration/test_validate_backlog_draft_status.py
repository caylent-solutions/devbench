"""Functional tests for the validate-backlog draft-status restriction.

Exercises the full ``cmd_validate_backlog`` CLI entry point against real
workspaces on disk. Complements the unit-level coverage in
``tests/test_backlog/test_manager.py::TestValidateStatusEnum`` by pinning the
CLI exit code + stdout / stderr contract for the draft-status-is-task-only rule.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.constants import STATUS_DRAFT

BACKLOG_INDEX_EPIC_DRAFT = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 0 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| EX | Example Epic | Epic | {status} | none | example-org/example-repo | `backlog/EX.md` |
"""

BACKLOG_INDEX_TASK_DRAFT = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 0 | 1 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | {status} | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |
"""


def _build_epic_draft_workspace(tmp_path: Path) -> Path:
    """Construct a workspace with an Epic carrying STATUS_DRAFT."""
    (tmp_path / "BACKLOG.md").write_text(
        BACKLOG_INDEX_EPIC_DRAFT.format(status=STATUS_DRAFT),
        encoding="utf-8",
    )
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    epic_file = backlog_dir / "EX.md"
    epic_file.write_text(
        f"# EX\n\n## Status: {STATUS_DRAFT}\n\n## Description\n\nEpic summary.\n",
        encoding="utf-8",
    )
    return tmp_path


def _build_task_draft_workspace(tmp_path: Path) -> Path:
    """Construct a workspace with a Task carrying STATUS_DRAFT."""
    (tmp_path / "BACKLOG.md").write_text(
        BACKLOG_INDEX_TASK_DRAFT.format(status=STATUS_DRAFT),
        encoding="utf-8",
    )
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    task_file = backlog_dir / "EX-F1-S1-T1.md"
    task_file.write_text(
        f"# EX-F1-S1-T1\n\n"
        f"## Status: {STATUS_DRAFT}\n\n"
        "## Target Repository\n\n"
        "- **Repo:** `example-org/example-repo`\n\n"
        "## Description\n\nFunctional-test task.\n\n"
        "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
        "## Changes Manifest\n\n"
        "| File | Change |\n"
        "|------|--------|\n"
        "| `README.md` | update |\n\n"
        "## Definition of Done\n\n- [ ] Done\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.functional
class TestValidateBacklogDraftStatusFunctional:
    """End-to-end: invoke cli.cmd_validate_backlog against workspaces with draft status."""

    def test_cli_validate_backlog_exits_nonzero_on_epic_draft_status(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An Epic with STATUS_DRAFT must cause cmd_validate_backlog to exit non-zero
        with an error message naming the unit and explaining that draft is only valid
        for Task work units."""
        workspace = _build_epic_draft_workspace(tmp_path)
        with patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"):
            rc = cli.cmd_validate_backlog()

        assert rc != 0
        captured = capsys.readouterr()
        assert "FAILED" in captured.out
        assert f'Status "{STATUS_DRAFT}" is only valid for Task work units' in captured.out

    def test_cli_validate_backlog_exits_zero_on_task_draft_status(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A Task with STATUS_DRAFT must NOT cause cmd_validate_backlog to produce a
        draft-restriction error; the command exits 0 and prints the pass message."""
        workspace = _build_task_draft_workspace(tmp_path)
        with patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"):
            rc = cli.cmd_validate_backlog()

        assert rc == 0
        captured = capsys.readouterr()
        assert f'Status "{STATUS_DRAFT}" is only valid for Task work units' not in captured.out
        assert "passed" in captured.out.lower()
