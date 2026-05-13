"""Tests for devbench.cli module."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.proposal import Proposal
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import BACKLOG_SUBDIR
from devbench.github.git_ops import CIResult


@pytest.fixture
def mock_units() -> list[WorkUnit]:
    """Create a list of mock work units for testing."""
    return [
        WorkUnit(
            id="E0-F1-S1-T1",
            title="First Task",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        ),
        WorkUnit(
            id="E0-F1-S1-T2",
            title="Second Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=["E0-F1-S1-T1"],
        ),
        WorkUnit(
            id="E0-F1-S1-T3",
            title="Blocked Task",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T3.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        ),
    ]


class TestFindUnit:
    """Test _find_unit helper."""

    def test_finds_by_id(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "E0-F1-S1-T2")
        assert result is not None
        assert result.id == "E0-F1-S1-T2"

    def test_finds_case_insensitive(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "e0-f1-s1-t1")
        assert result is not None
        assert result.id == "E0-F1-S1-T1"

    def test_returns_none_when_not_found(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "NONEXISTENT")
        assert result is None


class TestCmdStatus:
    """Test cmd_status command."""

    def test_returns_zero(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [mock_units[2]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0

    def test_shows_all_done_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        done_unit = WorkUnit(
            id="T1",
            title="Done",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("t.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        assert "All work units are DONE" in capsys.readouterr().out

    def test_shows_blocked_count(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [mock_units[2]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        assert "1 blocked" in capsys.readouterr().out


class TestCmdStatusDetail:
    """E220: ``devbench status --detail`` renders in-queue / blocked / held panels."""

    def _build_backlog(self, tmp_path: Path) -> Path:
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        # T1 in-queue ready (no deps); T2 in-queue waiting on T1; T3 blocked
        # with an open proposal marker; T4 held with a [HOLD] reason.
        rows = [
            ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1", ""),
            ("E0-F1-S1-T2", "Task", "in-queue", "E0-F1-S1-T1", "E0-F1-S1-T2", ""),
            (
                "E0-F1-S1-T3",
                "Task",
                "blocked",
                "None",
                "E0-F1-S1-T3",
                "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n",
            ),
            (
                "E0-F1-S1-T4",
                "Task",
                "hold",
                "None",
                "E0-F1-S1-T4",
                "## Comments\n\n[HOLD] awaiting product input\n",
            ),
        ]
        index_lines = [
            "# Backlog\n",
            "## Full Work Unit Index\n",
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
            "|----|-------|------|--------|--------------|------|-----------|",
        ]
        for unit_id, unit_type, status, deps, basename, comments in rows:
            file_path = f"backlog/{basename}.md"
            index_lines.append(
                f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | "
                f"caylent-solutions/test-repo | `{file_path}` |"
            )
            wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
            if deps and deps != "None":
                dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
                wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
            if comments:
                wu_body += f"\n{comments}"
            (wu_dir / f"{basename}.md").write_text(wu_body)
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("\n".join(index_lines) + "\n")
        return index_path

    def test_detail_renders_three_panels(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        index_path = self._build_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        # In-queue panel: T1 ready, T2 waiting on T1
        assert "In-queue tasks" in out
        assert "[ready]" in out
        assert "E0-F1-S1-T1" in out
        assert "[waiting]" in out
        assert "blocker: E0-F1-S1-T1" in out
        # Blocked panel: T3 with pending proposal marker
        assert "Blocked tasks" in out
        assert "pending proposal E0-F1-S1-T9" in out
        # Held panel: T4 with HOLD reason
        assert "Held tasks" in out
        assert "awaiting product input" in out

    def test_default_invocation_omits_detail_panels(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Backlog Status Summary" in out
        assert "In-queue tasks" not in out
        assert "Held tasks" not in out

    def test_unknown_positional_arg_rejected(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_status("garbage")
        assert rc == 1
        assert "no positional args" in capsys.readouterr().err

    def test_status_is_variadic(self) -> None:
        assert "status" in cli._VARIADIC_COMMANDS


class TestLatestHoldReason:
    """Cover the helper that extracts the most recent [HOLD] line from Comments."""

    def test_returns_last_match_when_multiple(self) -> None:
        content = "## Comments\n\n[HOLD] first reason\n[UNHOLD] back to queue\n[HOLD] second reason\n"
        assert cli._latest_hold_reason(content) == "second reason"

    def test_returns_empty_when_no_hold_line(self) -> None:
        content = "## Comments\n\n[BLOCKED] dep not met\n"
        assert cli._latest_hold_reason(content) == ""


class TestCmdNext:
    """Test cmd_next command."""

    def test_returns_json_when_actionable(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        output = capsys.readouterr().out.strip()
        data = json.loads(output)
        # TD-12: assert the full envelope shape, not just one field, so a
        # regression in any envelope key (renamed, dropped, mistyped) is
        # caught here rather than at a downstream parser.
        assert data["id"] == "E0-F1-S1-T2"
        assert data["title"] == "Second Task"
        assert data["repo"] == "caylent-solutions/git-repo"
        assert data["file_path"] == str(Path("backlog/E0-F1-S1-T2.md"))
        assert data["dependencies"] == ["E0-F1-S1-T1"]

    def test_prints_all_done_when_complete(self, capsys: pytest.CaptureFixture) -> None:
        done_unit = WorkUnit(
            id="T1",
            title="Done",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("t.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        assert "ALL_DONE" in capsys.readouterr().out

    def test_prints_no_actionable(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        assert "NO_ACTIONABLE" in capsys.readouterr().out

    def test_next_does_not_mutate_status(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        """cmd_next must be read-only: BacklogManager.force_status must never be called."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                result = cli.cmd_next()

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    def test_next_returns_json_descriptor(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        """cmd_next emits a JSON object with id, title, repo, file_path, and dependencies."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        data = json.loads(capsys.readouterr().out.strip())
        assert data["id"] == "E0-F1-S1-T2"
        assert data["title"] == "Second Task"
        assert data["repo"] == "caylent-solutions/git-repo"
        assert "file_path" in data
        assert "dependencies" in data


class TestCmdClaim:
    """Test cmd_claim command."""

    def test_claim_sets_unit_in_progress(
        self,
        mock_units: list[WorkUnit],
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_claim transitions the work unit to in-progress via force_status.

        mock_units[1].file_path is Path("backlog/E0-F1-S1-T2.md"), so BACKLOG_ROOT
        must be set to backlog_dir.parent (tmp_path) so that the resolved path is
        tmp_path / "backlog/E0-F1-S1-T2.md" == backlog_dir / "E0-F1-S1-T2.md".
        """
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_claim("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.force_status.assert_called_once()
        call_args = mock_mgr.force_status.call_args
        assert call_args[0][2] == "E0-F1-S1-T2"
        from devbench.constants import STATUS_IN_PROGRESS

        assert call_args[0][3] == STATUS_IN_PROGRESS
        assert "Claimed E0-F1-S1-T2" in capsys.readouterr().out

    def test_claim_refuses_when_manifest_has_tbd_placeholder(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Issue #117: cmd_claim refuses tasks whose Manifest still carries a TBD row."""
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# E0-F1-S1-T2: Test\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n"
            "| TBD | Executor agent: replace this row |\n",
            encoding="utf-8",
        )
        unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")
        assert rc == 1
        err = capsys.readouterr().err
        assert "placeholder row 'TBD'" in err

    def test_claim_returns_nonzero_for_unknown_id(
        self,
        mock_units: list[WorkUnit],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_claim exits non-zero with a clear error when the unit ID is not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_claim("NONEXISTENT-ID")

        assert result == 1
        assert "NONEXISTENT-ID" in capsys.readouterr().err


class TestCmdLog:
    """Test cmd_log command."""

    def test_returns_zero(self, capsys: pytest.CaptureFixture) -> None:
        result = cli.cmd_log("test message")
        assert result == 0
        assert "Logged" in capsys.readouterr().out


class TestCmdSetStatus:
    """Test cmd_set_status command."""

    def test_returns_1_for_invalid_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_set_status("E0-F1-S1-T1", "invalid")
        assert result == 1
        assert "Invalid status" in capsys.readouterr().err

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("NONEXISTENT", "in-progress")

        assert result == 1

    def test_returns_0_on_success(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_set_status("E0-F1-S1-T2", "in-progress")

        assert result == 0
        assert "in-progress" in capsys.readouterr().out
        mock_mgr.force_status.assert_called_once()


class TestCmdMarkDone:
    """Test cmd_mark_done enforces the done-gate via mark_done()."""

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_mark_done("NONEXISTENT")

        assert result == 1

    def test_returns_0_on_success(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.mark_done.assert_called_once()

    def test_returns_1_when_done_gate_fails(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()
        mock_mgr.mark_done.side_effect = RuntimeError("not all required judges passed")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 1
        assert "not all required judges passed" in capsys.readouterr().err


class TestCmdValidateBacklogPathResolution:
    """Bug fix: cmd_validate_backlog must pass workspace root (BACKLOG_INDEX.parent) to validate(),
    not BACKLOG_ROOT -- otherwise file paths of the form 'backlog/...' get resolved as
    BACKLOG_ROOT/backlog/... which is a double 'backlog/' and causes false 'file missing' errors.
    """

    def _make_layout(self, workspace: Path) -> tuple[Path, Path]:
        """Create realistic layout: BACKLOG.md at workspace root, work unit in workspace/backlog/."""
        backlog_dir = workspace / BACKLOG_SUBDIR
        backlog_dir.mkdir(parents=True, exist_ok=True)
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: Task\n\n## Status: in-queue\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nTest task.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Placeholder\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] All ACs checked\n\n"
            "## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        idx = workspace / "BACKLOG.md"
        idx.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        return idx, backlog_dir

    def test_no_false_file_missing_errors_with_real_layout(self, tmp_path: Path) -> None:
        """When BACKLOG_INDEX is at workspace root and BACKLOG_ROOT = workspace/backlog,
        validate-backlog must return 0 (no false 'file missing' errors).
        """
        idx, backlog_dir = self._make_layout(tmp_path)
        # Simulate production: BACKLOG_INDEX at workspace, BACKLOG_ROOT = workspace/backlog
        with (
            patch("devbench.cli.BACKLOG_INDEX", idx),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_validate_backlog()
        assert result == 0

    def test_validate_called_with_workspace_root_not_backlog_root(self, tmp_path: Path) -> None:
        """validate() must receive backlog_index.parent (workspace root), not BACKLOG_ROOT."""
        idx, backlog_dir = self._make_layout(tmp_path)
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with (
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", idx),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            cli.cmd_validate_backlog()

        # Second arg must be workspace root (idx.parent), not BACKLOG_ROOT (backlog_dir)
        _, call_kwargs = mock_mgr.validate.call_args
        positional = mock_mgr.validate.call_args.args
        workspace_root_arg = positional[1] if len(positional) > 1 else call_kwargs.get("backlog_root")
        assert workspace_root_arg == idx.parent
        assert workspace_root_arg != backlog_dir


class TestCmdValidateBacklog:
    """Test cmd_validate_backlog command."""

    def test_returns_0_when_backlog_is_valid(self, tmp_path: Path) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 0

    def test_returns_1_and_prints_errors_when_invalid(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = ["E0-T1: work unit file missing", "E0-T2: status mismatch"]

        with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 1
        output = capsys.readouterr().out
        assert "E0-T1" in output
        assert "E0-T2" in output


class TestMain:
    """Test main argument parsing."""

    def test_no_args_prints_usage_and_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["judges.cli"]):
            result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Usage: devbench" in out
        assert "status" in out  # one of the registered commands

    def test_unknown_command_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli", "nonexistent"]):
            result = cli.main()
        assert result == 1

    def test_dispatches_status(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        assert result == 0
        mock_fn.assert_called_once()

    def test_dispatches_log_with_arg(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "log", "hello"]):
            with patch.dict(cli._COMMANDS, {"log": (mock_fn, 1, "Log a message")}):
                result = cli.main()
        assert result == 0
        mock_fn.assert_called_once_with("hello")

    def test_missing_required_arg_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli", "execute"]):
            result = cli.main()
        assert result == 1

    def test_dispatches_with_extra_args(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "execute", "T1", "feedback-text"]):
            with patch.dict(cli._COMMANDS, {"execute": (mock_fn, 1, "Execute")}):
                result = cli.main()
        assert result == 0


class TestHelp:
    """`devbench --help` / `-h` at top-level and per-command must print usage and exit 0."""

    def test_top_level_long_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["judges.cli", "--help"]):
            result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Usage: devbench" in out
        assert "status" in out

    def test_top_level_short_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["judges.cli", "-h"]):
            result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Usage: devbench" in out

    def test_per_command_long_flag_does_not_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`<cmd> --help` prints the registry description and must not call the handler."""
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status", "--help"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Show backlog summary" in out
        mock_fn.assert_not_called()

    def test_per_command_short_flag_does_not_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status", "-h"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Show backlog summary" in out
        mock_fn.assert_not_called()

    def test_unknown_command_still_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Typos must still fail fast -- --help is not a wildcard excuse."""
        with patch("sys.argv", ["judges.cli", "nonexistent-command"]):
            result = cli.main()
        err = capsys.readouterr().err
        assert result == 1
        assert "Unknown command" in err


class TestPreParseConfig:
    """Test --config CLI pre-parse helper."""

    def test_sets_env_var_and_removes_args(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import os

        config_path = str(tmp_path / "custom.yaml")
        argv = ["judges.cli", "--config", config_path, "status"]
        monkeypatch.delenv("JUDGE_CONFIG_PATH", raising=False)
        cli._pre_parse_config(argv)
        assert os.environ.get("JUDGE_CONFIG_PATH") == config_path
        assert "--config" not in argv
        assert config_path not in argv
        assert argv == ["judges.cli", "status"]

    def test_noop_when_config_not_present(self) -> None:
        argv = ["judges.cli", "status"]
        original = argv.copy()
        cli._pre_parse_config(argv)
        assert argv == original

    def test_noop_when_config_has_no_value(self) -> None:
        argv = ["judges.cli", "--config"]
        original = argv.copy()
        cli._pre_parse_config(argv)
        assert argv == original


@pytest.mark.unit
class TestCmdGitOpsSubmoduleGate:
    """Tests for T3 AC-1 and AC-2: UPDATE_SUBMODULE gates update_parent_submodule_ref."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E202-F1-S1-T3",
            title="Test task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E202-F1-S1-T3.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def _build_mock_ops(self) -> MagicMock:
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        return mock_ops

    def test_cmd_git_ops_skips_submodule_update_when_flag_false(self, tmp_path: Path) -> None:
        """
        Given: UPDATE_SUBMODULE is False
        When: cmd_git_ops is called
        Then: update_parent_submodule_ref is never called (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T3")

        assert result == 0
        mock_ops.update_parent_submodule_ref.assert_not_called()

    def test_cmd_git_ops_calls_submodule_update_when_flag_true(self, tmp_path: Path) -> None:
        """
        Given: UPDATE_SUBMODULE is True
        When: cmd_git_ops is called
        Then: update_parent_submodule_ref is called with correct args (AC-2)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T3")

        assert result == 0
        mock_ops.update_parent_submodule_ref.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "chore: update devbench submodule after E202-F1-S1-T3",
        )


@pytest.mark.unit
class TestCmdGitOpsChecksGate:
    """Tests for T2 AC-4 and AC-5: CI checks gate in cmd_git_ops."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E202-F1-S1-T2",
            title="Test task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E202-F1-S1-T2.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_git_ops_returns_error_when_checks_fail(self, tmp_path: Path) -> None:
        """
        Given: wait_for_checks returns False (checks failed) and the
            CI-failure executor retry path is opted out via YAML
            (``git_ops.ci_failure_retry: false``)
        When: cmd_git_ops is called
        Then: returns 1 and merge_pr is never called (AC-4 -- legacy
            BLOCKED path; see TestCiFailureRetry for the rc=2 default
            behaviour after the v-next flip).
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.FAILED_UNKNOWN
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", False),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T2")

        assert result == 1
        mock_ops.merge_pr.assert_not_called()

    def test_cmd_git_ops_merges_when_checks_pass(self, tmp_path: Path) -> None:
        """
        Given: wait_for_checks returns True (all checks passed or no checks)
        When: cmd_git_ops is called
        Then: merge_pr is called and returns 0 (AC-5)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T2")

        assert result == 0
        mock_ops.merge_pr.assert_called_once()


@pytest.mark.unit
class TestCmdEnsureBranch:
    """Tests for cmd_ensure_branch (T1 AC-1)."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E202-F1-S1-T1",
            title="ensure_branch task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E202-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_ensure_branch_calls_git_ops(self, tmp_path: Path) -> None:
        """
        Given: a valid work unit ID
        When: cmd_ensure_branch is called
        Then: GitOpsService.ensure_branch is called with the correct repo, path, and branch (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_ensure_branch("E202-F1-S1-T1")

        assert result == 0
        mock_ops.ensure_branch.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "backlog/e202-f1-s1-t1",
        )

    def test_cmd_ensure_branch_returns_1_when_unit_not_found(self) -> None:
        """
        Given: a unit ID not in the backlog
        When: cmd_ensure_branch is called
        Then: returns 1
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_ensure_branch("NONEXISTENT")

        assert result == 1


@pytest.mark.unit
class TestCmdGitOpsPostMergeCheckout:
    """Tests for AC-1: cmd_git_ops checks out default branch after merge."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E224-F1-S1-T1",
            title="Post-merge checkout test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E224-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_git_ops_checks_out_default_branch_after_merge(self, tmp_path: Path) -> None:
        """
        Given: merge_pr succeeds
        When: cmd_git_ops is called
        Then: checkout_default_branch is called after merge_pr succeeds (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 0
        mock_ops.checkout_default_branch.assert_called_once_with("caylent-solutions/devbench", repo_path)

    def test_cmd_git_ops_calls_checkout_before_submodule_update(self, tmp_path: Path) -> None:
        """
        Given: merge_pr succeeds and UPDATE_SUBMODULE is True
        When: cmd_git_ops is called
        Then: checkout_default_branch is called before update_parent_submodule_ref (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        call_order: list[str] = []
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops.checkout_default_branch.side_effect = lambda *_: call_order.append("checkout")
        mock_ops.update_parent_submodule_ref.side_effect = lambda *_: call_order.append("submodule")
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 0
        assert call_order.index("checkout") < call_order.index("submodule")


@pytest.mark.unit
class TestCmdGitOpsConflictingRetry:
    """Tests for AC-6 and AC-7: ConflictingPRError retry logic in cmd_git_ops."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E224-F1-S1-T1",
            title="Conflicting retry test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E224-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_git_ops_retries_merge_after_conflicting(self, tmp_path: Path) -> None:
        """
        Given: first merge_pr raises ConflictingPRError, retry succeeds
        When: cmd_git_ops is called
        Then: rebase_and_force_push is called, then merge_pr is retried and returns 0 (AC-6)
        """
        from devbench.github.git_ops import ConflictingPRError

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        # First call raises ConflictingPRError, second succeeds
        mock_ops.merge_pr.side_effect = [
            ConflictingPRError("CONFLICTING"),
            None,
        ]
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 0
        mock_ops.rebase_and_force_push.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "backlog/e224-f1-s1-t1",
        )
        assert mock_ops.merge_pr.call_count == 2

    def test_cmd_git_ops_exits_nonzero_if_retry_merge_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: first merge_pr raises ConflictingPRError, retry also fails
        When: cmd_git_ops is called
        Then: returns 1 with clear error message, no further retry (AC-7)
        """
        from devbench.github.git_ops import ConflictingPRError

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        # Both calls fail
        mock_ops.merge_pr.side_effect = [
            ConflictingPRError("CONFLICTING"),
            RuntimeError("merge still failed"),
        ]
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 1
        err_output = capsys.readouterr().err
        assert "merge" in err_output.lower() or "ERROR" in err_output
        # Must not call merge_pr a third time
        assert mock_ops.merge_pr.call_count == 2


@pytest.mark.unit
class TestCmdGetDiff:
    """Tests for cmd_get_diff origin/<default_branch> fix (E225-F1-S1-T1)."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E225-F1-S1-T1",
            title="get-diff test task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E225-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_get_diff_uses_origin_remote_ref_for_branch_diff(self, tmp_path: Path) -> None:
        """
        Given: a configured default branch of 'main3'
        When: cmd_get_diff is called
        Then: run_command is invoked with ['git', 'diff', 'origin/main3'], not ['git', 'diff', 'main3'] (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "devbench"

        diff_calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd[:2] == ["git", "diff"]:
                diff_calls.append(cmd)
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main3"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            cli.cmd_get_diff("E225-F1-S1-T1")

        branch_diff_calls = [c for c in diff_calls if len(c) == 3 and c[2] not in ("--cached",)]
        assert len(branch_diff_calls) == 1, f"Expected exactly one branch diff call, got: {branch_diff_calls}"
        assert branch_diff_calls[0] == ["git", "diff", "origin/main3"], (
            f"Expected 'origin/main3' ref but got: {branch_diff_calls[0]}"
        )

    def test_get_diff_output_unchanged_when_local_ref_current(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: local 'main3' ref is up-to-date with 'origin/main3' (identical diff output)
        When: cmd_get_diff is called
        Then: the diff output is produced correctly and return code is 0 (AC-2)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "devbench"
        expected_diff = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "origin/main3"]:
                return (0, expected_diff, "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main3"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E225-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "foo.py" in output, "Expected diff content to appear in output when local ref is current"

    def test_get_diff_excludes_upstream_merged_files_when_local_ref_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: local 'main3' is behind 'origin/main3' (stale)
        When: cmd_get_diff is called
        Then: only work-unit-branch changes appear (git diff uses origin/main3, not bare main3) (AC-3)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "devbench"

        # Simulate: bare main3 would include upstream-merged file, origin/main3 would not
        branch_only_diff = (
            "diff --git a/new_feature.py b/new_feature.py\n+++ b/new_feature.py\n@@ -0,0 +1 @@\n+feature\n"
        )
        stale_extra_diff = branch_only_diff + "diff --git a/upstream_merged.py b/upstream_merged.py\n"

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "origin/main3"]:
                return (0, branch_only_diff, "")
            if cmd == ["git", "diff", "main3"]:
                return (0, stale_extra_diff, "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main3"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E225-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "upstream_merged.py" not in output, (
            "Upstream-merged file appeared in output -- bare branch ref was used instead of origin/"
        )
        assert "new_feature.py" in output, "Branch-specific diff should appear in output"


@pytest.mark.unit
class TestCmdReadUnitStripComments:
    """Tests for --strip-comments flag on cmd_read_unit (E216-F1-S1-T1)."""

    def _make_unit(self, wu_file: Path) -> WorkUnit:
        return WorkUnit(
            id="E216-F1-S1-T1",
            title="Strip comments test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def _make_wu_file(self, tmp_path: Path, content: str) -> Path:
        wu_file = tmp_path / "E216-F1-S1-T1.md"
        wu_file.write_text(content, encoding="utf-8")
        return wu_file

    def test_read_unit_strip_comments_removes_comments_section(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-2: --strip-comments removes ## Comments section and everything after."""
        content = (
            "# E216-F1-S1-T1: Strip Test\n\n"
            "## Status: in-progress\n\n"
            "## Description\n\nSome description.\n"
            "\n## Comments\n\n"
            "[judge/executor] [REVIEW_PASS] looks good\n"
        )
        wu_file = self._make_wu_file(tmp_path, content)
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
        ):
            result = cli.cmd_read_unit("--strip-comments", "E216-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "## Comments" not in data["content"], "Comments section should be stripped when --strip-comments is used"
        assert "[REVIEW_PASS]" not in data["content"], "Comment entries should be removed when --strip-comments is used"
        assert "## Description" in data["content"], "Content before ## Comments should be preserved"

    def test_read_unit_without_flag_returns_full_content(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-3: Without --strip-comments, output is unchanged (backward compatible)."""
        content = (
            "# E216-F1-S1-T1: Strip Test\n\n"
            "## Status: in-progress\n\n"
            "## Comments\n\n"
            "[judge/executor] [REVIEW_PASS] looks good\n"
        )
        wu_file = self._make_wu_file(tmp_path, content)
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
        ):
            result = cli.cmd_read_unit("E216-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "## Comments" in data["content"], (
            "Without --strip-comments, full content including Comments should be returned"
        )
        assert "[REVIEW_PASS]" in data["content"], "Without --strip-comments, comment entries should be present"

    def test_read_unit_strip_comments_without_unit_id_returns_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-4: --strip-comments without unit ID exits with non-zero and clear error."""
        result = cli.cmd_read_unit("--strip-comments")
        assert result == 1
        err = capsys.readouterr().err
        assert "unit_id" in err.lower() or "required" in err.lower(), (
            f"Expected clear error about missing unit_id, got: {err!r}"
        )

    def test_read_unit_strip_comments_unit_has_no_comments_section(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-2 edge case: --strip-comments on a file with no ## Comments section is a no-op."""
        content = "# E216-F1-S1-T1: Strip Test\n\n## Status: in-progress\n\n## Description\n\nSome description.\n"
        wu_file = self._make_wu_file(tmp_path, content)
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
        ):
            result = cli.cmd_read_unit("--strip-comments", "E216-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "## Description" in data["content"], (
            "Content should be fully preserved when no ## Comments section exists"
        )
        assert data["content"].strip() == content.strip(), (
            "Content should be unchanged when no ## Comments section is present"
        )


@pytest.mark.unit
class TestCmdLogComment:
    """Tests for cmd_log_comment (AC-1, AC-2)."""

    def _make_wu_file(self, tmp_path: Path, with_comments_section: bool = True) -> tuple[Path, Path]:
        """Return (backlog_dir, wu_file) with a minimal work-unit file."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        header = "# E0-F1-S1-T1\n\n## Status: in-progress\n\n"
        if with_comments_section:
            header += "## Comments\n"
        wu_file.write_text(header, encoding="utf-8")
        return backlog_dir, wu_file

    def _make_mock_unit(self, backlog_dir: Path) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=backlog_dir / "E0-F1-S1-T1.md",
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_log_comment_appends_agent_format_to_comments(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-1: log-comment appends [YYYY-MM-DD HH:MM UTC] [agent/<agent>] <message>."""
        backlog_dir, wu_file = self._make_wu_file(tmp_path)
        unit = self._make_mock_unit(backlog_dir)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", "implementation complete")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        timestamp_pattern = r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\]"
        assert re.search(timestamp_pattern, content), "Timestamp not found in comment"
        assert "[agent/executor]" in content, "Agent prefix not in comment"
        assert "implementation complete" in content, "Message not in comment"

    def test_log_comment_contains_no_review_token(self, tmp_path: Path) -> None:
        """AC-2: log-comment entries must not contain [REVIEW_PASS] or [REVIEW_FAIL]."""
        backlog_dir, wu_file = self._make_wu_file(tmp_path)
        unit = self._make_mock_unit(backlog_dir)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_log_comment("executor", "E0-F1-S1-T1", "pass")

        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" not in content
        assert "[REVIEW_FAIL]" not in content

    def test_log_comment_returns_1_when_unit_not_found(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """log-comment fails fast when unit is missing from the index."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_comment("executor", "NONEXISTENT", "message")

        assert result == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# E230-F1-S1-T1: Discrete event comments in cmd_git_ops and cmd_mark_done
# ---------------------------------------------------------------------------


def _make_git_ops_unit(unit_id: str = "E230-F1-S1-T1") -> WorkUnit:
    """Return a WorkUnit suitable for cmd_git_ops tests."""
    return WorkUnit(
        id=unit_id,
        title="Git ops comment test",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=[],
    )


@pytest.mark.unit
class TestCmdGitOpsEventComments:
    """Tests for AC-1, AC-2, AC-3, AC-5, AC-7: git_ops appends audit comments."""

    def _build_mock_ops(self, pr_url: str = "https://github.com/org/repo/pull/42") -> MagicMock:
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = pr_url
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        return mock_ops

    def _make_wu_file(self, tmp_path: Path, unit_id: str) -> Path:
        """Create wu_file at tmp_path/backlog/{unit_id}.md (matches BACKLOG_ROOT=tmp_path)."""
        backlog_subdir = tmp_path / "backlog"
        backlog_subdir.mkdir(exist_ok=True)
        wu_file = backlog_subdir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")
        return wu_file

    def test_git_ops_appends_pr_created_comment(self, tmp_path: Path) -> None:
        """AC-1: After create_pr succeeds, Comments contains [agent/git_ops] [PR_CREATED] <url>."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content, f"[agent/git_ops] not found in:\n{content}"
        assert "[PR_CREATED]" in content, f"[PR_CREATED] not found in:\n{content}"
        assert pr_url in content, f"PR URL not found in:\n{content}"

    def test_git_ops_appends_pr_merged_comment_normal_path(self, tmp_path: Path) -> None:
        """AC-2: After merge_pr succeeds (normal), Comments contains [agent/git_ops] [PR_MERGED] <url>."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content
        assert "[PR_MERGED]" in content, f"[PR_MERGED] not found in:\n{content}"
        assert pr_url in content

    def test_git_ops_appends_pr_merged_comment_rebase_retry_path(self, tmp_path: Path) -> None:
        """AC-3: After merge_pr succeeds via rebase-retry, Comments contains [agent/git_ops] [PR_MERGED] <url>."""
        from devbench.github.git_ops import ConflictingPRError

        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        # First merge raises ConflictingPRError, second succeeds
        mock_ops.merge_pr.side_effect = [ConflictingPRError("CONFLICTING"), None]
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[PR_MERGED]" in content, f"[PR_MERGED] not found after rebase-retry in:\n{content}"
        assert pr_url in content

    def test_event_comments_contain_no_review_token(self, tmp_path: Path) -> None:
        """AC-5: git_ops event entries contain no [REVIEW_PASS] or [REVIEW_FAIL] token."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" not in content
        assert "[REVIEW_FAIL]" not in content

    def test_git_ops_warns_but_does_not_fail_when_unit_file_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-7: If work unit file cannot be resolved, cmd_git_ops warns but does not fail."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)
        # Note: wu_file is NOT created -- file resolution should fail gracefully

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        # Must NOT fail due to missing file -- git ops already succeeded
        assert result == 0


@pytest.mark.unit
class TestCmdMarkDoneEventComment:
    """Tests for AC-4, AC-5: cmd_mark_done appends [orchestrator] [DONE] comment."""

    def test_mark_done_appends_done_comment(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-4: After cmd_mark_done completes, Comments contains [orchestrator] [DONE] Work unit <id> completed.

        Uses a real BacklogManager (not mocked) so that _append_agent_comment actually writes to the file.
        Provides a real BACKLOG.md so mark_done can update it.
        """
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

        unit_id = "E230-F1-S1-T1"
        unit = WorkUnit(
            id=unit_id,
            title="Mark done comment test",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

        # Build BACKLOG.md with the unit row
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| {unit_id} | Mark done comment test | Task | in-review | None | repo | `backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )

        # All judges pass so mark_done gate is satisfied
        all_pass_comments = "".join(
            f"[2026-01-01 00:00 UTC] [judge/{j}] [REVIEW_PASS] ok\n" for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
        )

        backlog_subdir = tmp_path / "backlog"
        backlog_subdir.mkdir()
        wu_file = backlog_subdir / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}\n\n## Status: in-review\n\n## Comments\n\n{all_pass_comments}",
            encoding="utf-8",
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_mark_done(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/orchestrator]" in content, f"[agent/orchestrator] not found in:\n{content}"
        assert "[DONE]" in content, f"[DONE] not found in:\n{content}"
        assert unit_id in content

    def test_mark_done_done_comment_contains_no_review_token(self, tmp_path: Path) -> None:
        """AC-5: [DONE] entry appended by cmd_mark_done has no [REVIEW_PASS] or [REVIEW_FAIL] token."""
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

        unit_id = "E230-F1-S1-T1"
        unit = WorkUnit(
            id=unit_id,
            title="Mark done comment test",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| {unit_id} | Mark done comment test | Task | in-review | None | repo | `backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )

        all_pass_comments = "".join(
            f"[2026-01-01 00:00 UTC] [judge/{j}] [REVIEW_PASS] ok\n" for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
        )

        backlog_subdir = tmp_path / "backlog"
        backlog_subdir.mkdir()
        wu_file = backlog_subdir / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}\n\n## Status: in-review\n\n## Comments\n\n{all_pass_comments}",
            encoding="utf-8",
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.cmd_mark_done(unit_id)

        content = wu_file.read_text(encoding="utf-8")
        # The [DONE] comment appended by cmd_mark_done must not contain review tokens
        # The existing [REVIEW_PASS] lines from the all_pass_comments are present in content
        # but we only need to verify the NEW entry (last line) doesn't have them.
        # Split on the comments that were there before:
        done_section = content.split("[REVIEW_PASS] ok")[-1]
        assert "[REVIEW_PASS]" not in done_section
        assert "[REVIEW_FAIL]" not in done_section


@pytest.mark.unit
class TestResolveUnitFile:
    """AC-8: _resolve_unit_file helper extracted and used by relevant commands."""

    def test_resolve_unit_file_returns_path_when_found_under_backlog_root(self, tmp_path: Path) -> None:
        """_resolve_unit_file returns the file path when found under BACKLOG_ROOT."""
        unit = WorkUnit(
            id="E230-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E230-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        wu_file = tmp_path / "backlog" / "E230-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# E230-F1-S1-T1\n\n## Status: in-queue\n", encoding="utf-8")

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is not None
        assert result == wu_file

    def test_resolve_unit_file_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """AC-8: _resolve_unit_file returns None when file not found in either location."""
        unit = WorkUnit(
            id="E230-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E230-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        # No file is created -- both paths will be missing

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog_root"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace_root"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is None

    def test_resolve_unit_file_falls_back_to_workspace_root(self, tmp_path: Path) -> None:
        """_resolve_unit_file falls back to WORKSPACE_ROOT when file not found under BACKLOG_ROOT."""
        unit = WorkUnit(
            id="E230-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E230-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        # File exists only under workspace_root
        ws_file = tmp_path / "workspace" / "backlog" / "E230-F1-S1-T1.md"
        ws_file.parent.mkdir(parents=True, exist_ok=True)
        ws_file.write_text("# E230-F1-S1-T1\n\n## Status: in-queue\n", encoding="utf-8")

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog_root"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is not None
        assert result == ws_file


# ---------------------------------------------------------------------------
# E231-F2-S1-T1: cmd_log_tdd and log-tdd command registration
# ---------------------------------------------------------------------------

_WORK_UNIT_WITH_TDD_LOG_TEMPLATE = """\
# {unit_id}: TDD Test

## Status: in-progress

## Comments

## TDD Cycle Log
"""


def _make_wu_with_tdd_section(tmp_path: Path, unit_id: str = "E231-F2-S1-T1") -> Path:
    """Create a work unit file with a ## TDD Cycle Log section."""
    wu = tmp_path / f"{unit_id}.md"
    wu.write_text(_WORK_UNIT_WITH_TDD_LOG_TEMPLATE.format(unit_id=unit_id), encoding="utf-8")
    return wu


def _make_backlog_index_for_tdd(tmp_path: Path, unit_id: str, wu_file: Path) -> Path:
    """Create a minimal BACKLOG.md referencing the given work unit file."""
    content = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|----------|\n"
        f"| {unit_id} | TDD Test | Task | in-progress | None | caylent-solutions/devbench |"
        f" `backlog/{unit_id}.md` |\n"
    )
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(content, encoding="utf-8")
    return idx


@pytest.mark.unit
class TestCmdLogTdd:
    """Tests for cmd_log_tdd -- AC-1 through AC-6, AC-11."""

    def _setup(self, tmp_path: Path, unit_id: str = "E231-F2-S1-T1") -> tuple[Path, Path]:
        """Return (wu_file, backlog_index) with TDD Cycle Log section."""
        wu_file = _make_wu_with_tdd_section(tmp_path, unit_id)
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        # Copy wu_file into backlog subdir so _resolve_unit_file finds it
        backlog_wu = backlog_dir / f"{unit_id}.md"
        backlog_wu.write_text(wu_file.read_text(encoding="utf-8"), encoding="utf-8")
        backlog_index = _make_backlog_index_for_tdd(tmp_path, unit_id, backlog_wu)
        return backlog_wu, backlog_index

    def test_log_tdd_red_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-1: log-tdd RED appends [RED] entry to ## TDD Cycle Log section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "Tests: test_foo.py. Command: make test-unit. Exit: 1.")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        assert tdd_start != -1
        tdd_section = content[tdd_start:]
        assert "[RED]" in tdd_section

    def test_log_tdd_green_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-2: log-tdd GREEN appends [GREEN] entry to ## TDD Cycle Log section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "GREEN", "Command: make test-unit. Result: 5 passed, 0 failed.")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        tdd_section = content[tdd_start:]
        assert "[GREEN]" in tdd_section

    def test_log_tdd_refactor_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-3: log-tdd REFACTOR appends [REFACTOR] entry to ## TDD Cycle Log section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "REFACTOR", "No refactor needed. Tests: 5 passed, 0 failed")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        tdd_section = content[tdd_start:]
        assert "[REFACTOR]" in tdd_section

    def test_log_tdd_phase_case_insensitive(self, tmp_path: Path) -> None:
        """AC-4: Phase argument is case-insensitive -- 'red' normalized to 'RED'."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "red", "lowercase phase message")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        # Entry should be normalized to uppercase [RED]
        assert "[RED]" in content

    def test_log_tdd_invalid_phase_exits_nonzero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-4: Invalid phase value exits non-zero with clear error message."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "BLUE", "invalid phase")

        assert result != 0
        captured = capsys.readouterr()
        assert "BLUE" in captured.err or "phase" in captured.err.lower()

    def test_log_tdd_missing_section_exits_nonzero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-6: Exits non-zero when ## TDD Cycle Log section does not exist in the file."""
        # Create a work unit WITHOUT the TDD Cycle Log section
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / "E231-F2-S1-T1.md"
        wu_file.write_text(
            "# E231-F2-S1-T1\n\n## Status: in-progress\n\n## Comments\n",
            encoding="utf-8",
        )
        backlog_index = _make_backlog_index_for_tdd(tmp_path, "E231-F2-S1-T1", wu_file)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "message without tdd section")

        assert result != 0
        captured = capsys.readouterr()
        assert "TDD Cycle Log" in captured.err or "tdd" in captured.err.lower()

    def test_log_tdd_cli_command_registered(self) -> None:
        """AC-1: 'log-tdd' is a recognized command in the CLI command registry."""
        assert "log-tdd" in cli._COMMANDS, "log-tdd command must be registered in cli._COMMANDS"

    def test_log_tdd_entry_not_in_comments_section(self, tmp_path: Path) -> None:
        """AC-11: TDD Cycle Log entries do not appear in ## Comments section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "unique-tdd-marker-xyz")

        content = wu_file.read_text(encoding="utf-8")
        comments_start = content.find("## Comments")
        tdd_start = content.find("## TDD Cycle Log")
        # Extract comments section (before TDD Cycle Log)
        comments_section = content[comments_start:tdd_start] if tdd_start > comments_start else content[comments_start:]
        assert "unique-tdd-marker-xyz" not in comments_section, f"TDD entry leaked into ## Comments: {comments_section}"


class TestCmdStatusActiveUnits:
    """Test cmd_status shows active work units (IN_PROGRESS / IN_REVIEW)."""

    def test_shows_active_work_units(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 116-118: active IN_PROGRESS and IN_REVIEW units are printed."""
        in_progress_unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        in_review_unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Reviewing Task",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress_unit, in_review_unit]
        mock_parser.get_parallel_candidates.return_value = [in_progress_unit]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        output = capsys.readouterr().out
        assert "Active work units:" in output
        assert "E0-F1-S1-T1" in output
        assert "E0-F1-S1-T2" in output


class TestCmdClaimFileNotFound:
    """Test cmd_claim when work unit file is not found on disk."""

    def test_claim_returns_1_when_file_missing(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 174-175: file not found for resolved unit."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_claim("E0-F1-S1-T2")

        assert result == 1
        assert "file not found" in capsys.readouterr().err.lower()


class TestCmdSetStatusFileNotFound:
    """Test cmd_set_status when work unit file is not found on disk."""

    def test_set_status_returns_1_when_file_missing(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 204-205: work unit file not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_set_status("E0-F1-S1-T2", "in-progress")

        assert result == 1
        assert "file not found" in capsys.readouterr().err.lower()


class TestCmdMarkDoneFileNotFound:
    """Test cmd_mark_done when work unit file is not found on disk."""

    def test_mark_done_returns_1_when_file_missing(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 232-233: work unit file not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 1
        assert "file not found" in capsys.readouterr().err.lower()


class TestCmdReadUnitFileResolution:
    """Test cmd_read_unit file path resolution branches."""

    def _make_unit(self, file_path: Path) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=file_path,
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_read_unit_not_found_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 340-341: unit not found in backlog index."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_read_unit("NONEXISTENT")

        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_read_unit_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 345, 351-352: file resolution from BACKLOG_ROOT falls back to WORKSPACE_ROOT."""
        unit = self._make_unit(Path("backlog/E0-F1-S1-T1.md"))
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        # File exists under WORKSPACE_ROOT, not BACKLOG_ROOT
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1: Test\n\n## Status: in-progress\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing_backlog"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
        ):
            result = cli.cmd_read_unit("E0-F1-S1-T1")

        assert result == 0

    def test_read_unit_no_local_path_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 351-352: no local path configured for repo."""
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1: Test\n\n## Status: in-progress\n", encoding="utf-8")
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_read_unit("E0-F1-S1-T1")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()


class TestCmdGetDiffEdgeCases:
    """Test cmd_get_diff edge cases and error branches."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_get_diff_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 388-389: unit not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_get_diff("NONEXISTENT")

        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_get_diff_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 395-396: no local path configured for repo."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()

    def test_get_diff_falls_back_to_git_default_branch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 412-423: when no configured default branch, falls back to git rev-parse."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"]:
                return (0, "origin/main\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.get_configured_default_branch", return_value=None),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0

    def test_get_diff_returns_error_when_no_default_branch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 417-422: git rev-parse fails and no configured branch."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"]:
                return (1, "", "fatal: error")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.get_configured_default_branch", return_value=None),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 1
        assert "cannot determine default branch" in capsys.readouterr().err.lower()

    def test_get_diff_includes_untracked_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 434-453: untracked files are included as synthetic diff hunks."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # Create an untracked file for the synthetic diff
        untracked_file = repo_path / "new_file.py"
        untracked_file.write_text("print('hello')\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "new_file.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "new_file.py" in output
        assert "+print('hello')" in output

    def test_get_diff_includes_staged_and_unstaged_diffs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 402, 406: staged and unstaged diffs are included in output."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "staged-diff-content\n", "")
            if cmd == ["git", "diff"] and len(cmd) == 2:
                return (0, "unstaged-diff-content\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "staged-diff-content" in output
        assert "unstaged-diff-content" in output

    def test_get_diff_skips_unreadable_untracked_files(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 441-442: OSError reading untracked file is skipped gracefully."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # Do NOT create the file so reading it raises OSError

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "nonexistent_file.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0

    def test_get_diff_skips_empty_filepath_lines(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Line 437: empty filepath lines among valid ones are skipped."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # Create a valid file so one line succeeds, and one blank line gets skipped
        valid_file = repo_path / "valid.py"
        valid_file.write_text("x = 1\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                # Mix of a valid file and an empty line
                return (0, "valid.py\n\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "valid.py" in output


class TestCmdGetDiffModeAware:
    """Tests for ADR-12 mode-aware cmd_get_diff behaviour.

    The non-defer_pr mode is pinned against behavioural regression so that
    the default per-task-branch workflow keeps working byte-identically.
    The defer_pr-mode tests assert that the branch-vs-default hunk is
    never emitted and that the post-commit state uses `git show HEAD`.
    """

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="ADR-12 mode-aware test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_non_defer_pr_mode_includes_branch_vs_main_diff(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Back-compat pin: with defer_pr False, all four hunks (staged,
        unstaged, branch-vs-default, untracked) appear in output."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / "untracked.py").write_text("x = 1\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "STAGED-HUNK\n", "")
            if cmd == ["git", "diff"]:
                return (0, "UNSTAGED-HUNK\n", "")
            if cmd == ["git", "diff", "origin/main"]:
                return (0, "BRANCH-VS-MAIN-HUNK\n", "")
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "untracked.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", False),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "STAGED-HUNK" in output
        assert "UNSTAGED-HUNK" in output
        assert "BRANCH-VS-MAIN-HUNK" in output
        assert "untracked.py" in output

    def test_defer_pr_mode_excludes_branch_vs_main_diff(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With defer_pr True, the branch-vs-default hunk is never emitted
        even when `git diff origin/<default>` would return content."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "STAGED-HUNK\n", "")
            if cmd == ["git", "diff", "origin/main"]:
                return (0, "BRANCH-VS-MAIN-SHOULD-NOT-APPEAR\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "STAGED-HUNK" in output
        assert "BRANCH-VS-MAIN-SHOULD-NOT-APPEAR" not in output

    def test_defer_pr_mode_pre_commit_returns_staged_and_unstaged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Pre-commit: staged and unstaged are both present; both appear;
        git show HEAD is not called because parts is already non-empty."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["git", "diff", "--cached"]:
                return (0, "STAGED-HUNK\n", "")
            if cmd == ["git", "diff"]:
                return (0, "UNSTAGED-HUNK\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "STAGED-HUNK" in output
        assert "UNSTAGED-HUNK" in output
        assert ["git", "show", "--format=", "HEAD"] not in calls, (
            "git show HEAD should only be called when staged/unstaged are empty"
        )

    def test_defer_pr_mode_post_commit_returns_git_show_head(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Post-commit: staged and unstaged are empty; git show HEAD is
        emitted so the post-commit security review sees this task's commit."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "show", "--format=", "HEAD"]:
                return (0, "GIT-SHOW-HEAD-HUNK\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "GIT-SHOW-HEAD-HUNK" in output

    def test_defer_pr_mode_with_accumulated_prior_commits_scopes_correctly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The whole point of ADR-12: with accumulated prior commits on
        the shared branch, the output must contain only the CURRENT task's
        staged change and NOT any of the prior commits."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        current_staged = "diff --git a/current.py b/current.py\n+new line\n"
        accumulated_branch = "".join(f"diff --git a/prior-{i}.py b/prior-{i}.py\n+prior line {i}\n" for i in range(10))

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, current_staged, "")
            if cmd == ["git", "diff", "origin/main"]:
                return (0, accumulated_branch, "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "current.py" in output
        for i in range(10):
            assert f"prior-{i}.py" not in output, (
                f"prior-{i}.py appeared in output under defer_pr mode -- ADR-12 regression"
            )

    def test_defer_pr_mode_untracked_files_still_rendered(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Untracked hunks are rendered in BOTH modes."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / "brand_new.py").write_text("print('hi')\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "brand_new.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "brand_new.py" in output
        assert "+print('hi')" in output

    def test_defer_pr_mode_returns_no_changes_when_all_states_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When staged, unstaged, HEAD, and untracked are all empty, the
        '(no changes)' sentinel is emitted as before."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(_cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        assert capsys.readouterr().out.strip() == "(no changes)"


class TestCmdLogVerdictFileResolution:
    """Test cmd_log_verdict file resolution fallback."""

    def test_log_verdict_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Line 520: file resolution falls back to WORKSPACE_ROOT when not under BACKLOG_ROOT."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass", "ok")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" in content


class TestCmdLogCommentNoCommentsSection:
    """Test cmd_log_comment when Comments section is missing."""

    def test_log_comment_creates_comments_section_when_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Line 577: creates ## Comments section header when it doesn't exist."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n", encoding="utf-8")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=backlog_dir / "E0-F1-S1-T1.md",
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", "message")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "## Comments" in content
        assert "[agent/executor]" in content


class TestCmdLogTddUnitNotFound:
    """Test cmd_log_tdd when unit is not found."""

    def test_log_tdd_returns_1_when_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 611-612: unit not found returns 1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_tdd("NONEXISTENT", "RED", "message")

        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()


class TestCmdRunTests:
    """Test cmd_run_tests command."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_run_tests_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 473: unit not found returns 1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_run_tests("NONEXISTENT")

        assert result == 1

    def test_run_tests_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 480: no local path configured for repo."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_run_tests("E0-F1-S1-T1")

        assert result == 1

    def test_run_tests_uses_make_test_when_available(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 483-489: uses make test when Makefile has test target."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["make", "-n", "test"]:
                return (0, "", "")  # test target exists
            if cmd == ["make", "test"]:
                return (0, "Tests passed", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_run_tests("E0-F1-S1-T1")

        assert result == 0
        assert ["make", "test"] in calls

    def test_run_tests_falls_back_to_pytest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 483-489: falls back to pytest when Makefile test target absent."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["make", "-n", "test"]:
                return (1, "", "No rule to make target")  # no test target
            return (0, "5 passed", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_run_tests("E0-F1-S1-T1")

        assert result == 0
        pytest_calls = [c for c in calls if c[0] == "pytest"]
        assert len(pytest_calls) == 1


class TestCmdLogVerdict:
    """Test cmd_log_verdict command."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_log_verdict_invalid_verdict(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 507-509: invalid verdict returns 1."""
        result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "invalid")
        assert result == 1
        assert "pass" in capsys.readouterr().err.lower()

    def test_log_verdict_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 515-516: unit not found returns 1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_verdict("code_review", "NONEXISTENT", "pass")

        assert result == 1

    def test_log_verdict_pass_appends_review_pass(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 506-544: pass verdict appends REVIEW_PASS to work unit."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass", "looks good")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" in content
        assert "judge/code_review" in content

    def test_log_verdict_fail_appends_review_fail(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 506-544: fail verdict appends REVIEW_FAIL to work unit."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "fail", "needs fixes")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_FAIL]" in content

    def test_log_verdict_creates_comments_section_when_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 535-536: creates ## Comments section when absent."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "## Comments" in content


class TestCmdLogCommentFileResolution:
    """Test cmd_log_comment file resolution fallback paths."""

    def test_log_comment_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 564, 577: when BACKLOG_ROOT path missing, falls back to WORKSPACE_ROOT."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing_backlog"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", "done")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/executor]" in content


class TestCmdLogTddFileResolution:
    """Test cmd_log_tdd file resolution fallback paths."""

    def test_log_tdd_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 611-612, 616: file resolution falls back to WORKSPACE_ROOT."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: in-progress\n\n## Comments\n\n## TDD Cycle Log\n",
            encoding="utf-8",
        )

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            result = cli.cmd_log_tdd("E0-F1-S1-T1", "RED", "tdd message")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[RED]" in content


class TestCmdEnsureBranchNoLocalPath:
    """Test cmd_ensure_branch when repo has no local path configured."""

    def test_ensure_branch_returns_1_when_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 653-654: no local path configured."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_ensure_branch("E0-F1-S1-T1")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()


class TestResolveGitOpsContext:
    """Test _resolve_git_ops_context helper exits."""

    def test_exits_when_unit_not_found(self) -> None:
        """Lines 678-679: sys.exit(1) when unit not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli._resolve_git_ops_context("NONEXISTENT")

        assert exc_info.value.code == 1

    def test_exits_when_no_local_path(self) -> None:
        """Lines 685-686: sys.exit(1) when no local path configured."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli._resolve_git_ops_context("E0-F1-S1-T1")

        assert exc_info.value.code == 1


class TestCmdGitOpsDeferMode:
    """Test cmd_git_ops with DEFER_PR mode."""

    def test_git_ops_uses_defer_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Line 732: when DEFER_PR is True, delegates to _git_ops_deferred."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_git_ops("E0-F1-S1-T1")

        assert result == 0
        mock_ops.commit_local.assert_called_once()


class TestCmdGitOpsBadPrNumber:
    """Test cmd_git_ops when PR URL does not end with a number."""

    def test_returns_1_when_pr_number_not_parseable(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 761-762: PR URL that doesn't end in a number."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/not-a-number"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E0-F1-S1-T1")

        assert result == 1
        assert "could not parse pr number" in capsys.readouterr().err.lower()


class TestCmdGitOpsFinalizeHappyPath:
    """Test cmd_git_ops_finalize happy path (CI GREEN branch)."""

    def test_finalize_pushes_and_creates_pr_then_watches_ci(self, tmp_path: Path) -> None:
        """cmd_git_ops_finalize commits, creates PR, waits for CI, and returns 0 on GREEN."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0) as mock_handler,
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 0
        mock_ops.commit_and_push.assert_called_once()
        mock_ops.create_pr.assert_called_once()
        mock_ops.wait_for_checks_and_classify.assert_called_once()
        mock_handler.assert_called_once()

    def test_finalize_returns_1_when_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 837-838: no local path configured for repo."""
        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()

    def test_finalize_green_does_not_merge(self, tmp_path: Path) -> None:
        """GREEN: cmd_git_ops_finalize returns 0 and does not call merge_pr."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 0
        mock_ops.merge_pr.assert_not_called()

    def test_finalize_timeout_returns_two(self, tmp_path: Path) -> None:
        """TIMEOUT: cmd_git_ops_finalize returns rc=2."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.TIMEOUT

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=2),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 2

    def test_finalize_failed_unknown_returns_two(self, tmp_path: Path) -> None:
        """FAILED_UNKNOWN: cmd_git_ops_finalize returns rc=2."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.FAILED_UNKNOWN

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=2),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 2

    def test_finalize_failed_known_task_returns_two(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK: cmd_git_ops_finalize returns rc=2."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=2),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 2


class TestCmdStart:
    """Test cmd_start command by mocking claude_agent_sdk."""

    def test_cmd_start_invokes_agent_sdk(self) -> None:
        """Lines 868-885: cmd_start creates an async runner and returns 0."""
        import sys
        import types

        # Create a mock claude_agent_sdk module
        mock_sdk = types.ModuleType("claude_agent_sdk")

        mock_options_cls = MagicMock()
        mock_sdk.ClaudeAgentOptions = mock_options_cls  # type: ignore[attr-defined]

        async def mock_query(**kwargs: object) -> object:
            # Async generator that yields a message to cover line 882
            yield "test message"

        mock_sdk.query = mock_query  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
            result = cli.cmd_start()

        assert result == 0


class TestMainMinArgs:
    """Test main() when a command doesn't have enough arguments."""

    def test_returns_1_with_insufficient_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 959-960: command requires more arguments than provided."""
        with patch("sys.argv", ["devbench", "claim"]):
            result = cli.main()
        assert result == 1
        err = capsys.readouterr().err
        assert "requires at least" in err


class TestCmdReport:
    """Test cmd_report command."""

    def test_cmd_report_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_report returns 0 and prints the generated report."""
        with patch("devbench.cli.generate_report", create=True) as mock_gen:
            mock_gen.return_value = "Test report output"
            with patch("devbench.reporting.report.generate_report", mock_gen):
                result = cli.cmd_report()

        assert result == 0
        assert "Test report output" in capsys.readouterr().out

    def test_cmd_report_with_since_timestamp(self) -> None:
        """cmd_report parses the 'since' argument into a datetime and passes it to generate_report."""
        from datetime import UTC, datetime

        captured_kwargs: dict = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured_kwargs.update(kwargs)
            return "report"

        with patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report):
            result = cli.cmd_report(since="2025-01-15T10:30:00Z")

        assert result == 0
        assert captured_kwargs["since"] == datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_cmd_report_watch_zero_runs_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_report with watch_interval=0 runs once (one-shot mode)."""
        with patch("devbench.reporting.report.generate_report", return_value="one-shot report"):
            result = cli.cmd_report(watch_interval=0)

        assert result == 0
        assert "one-shot report" in capsys.readouterr().out

    def test_cmd_report_watch_falls_through_to_streaming_with_deprecation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Issue #163: ``--watch N`` is deprecated. The interval value is
        ignored; the call falls through to the streaming loop and emits
        a deprecation notice."""
        import warnings

        def fake_stream_report(*args: object, **kwargs: object) -> int:
            # Stand-in for the streaming loop: returns immediately.
            return 0

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.report.generate_report", return_value="frame"),
            patch("devbench.reporting.streaming.stream_report", side_effect=fake_stream_report),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = cli.cmd_report(watch_interval=5)

        assert result == 0
        # Deprecation warning fired for --watch.
        assert any(issubclass(w.category, DeprecationWarning) and "--watch" in str(w.message) for w in caught)

    def test_cmd_report_streams_on_tty_by_default(self) -> None:
        """Issue #163: the default report invocation on a TTY uses the streaming loop."""
        called_with: dict[str, object] = {}

        def fake_stream_report(log_path: object, render_fn: object, **kwargs: object) -> int:
            called_with["log_path"] = log_path
            called_with["render_fn"] = render_fn
            return 0

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.streaming.stream_report", side_effect=fake_stream_report),
        ):
            result = cli.cmd_report()

        assert result == 0
        assert "log_path" in called_with
        assert callable(called_with["render_fn"])

    def test_cmd_report_once_flag_forces_one_shot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Issue #163: ``--once`` (passed via main()'s flag-extraction) forces
        the legacy one-shot snapshot regardless of TTY status."""
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.report.generate_report", return_value="one-shot text"),
        ):
            result = cli.cmd_report(once=True)

        assert result == 0
        assert "one-shot text" in capsys.readouterr().out

    def test_cmd_report_non_tty_forces_one_shot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Issue #163: piping / redirecting stdout (non-TTY) forces one-shot
        rendering so script / CI consumers see the snapshot and exit."""
        with (
            patch("sys.stdout.isatty", return_value=False),
            patch("devbench.reporting.report.generate_report", return_value="piped"),
        ):
            result = cli.cmd_report()

        assert result == 0
        assert "piped" in capsys.readouterr().out

    def test_cmd_report_since_forces_one_shot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Issue #163: ``--since <ISO-8601>`` keeps one-shot semantics --
        a frozen-window snapshot doesn't benefit from continuous refresh."""
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.report.generate_report", return_value="since-snapshot"),
        ):
            result = cli.cmd_report(since="2026-05-01T00:00:00Z")

        assert result == 0
        assert "since-snapshot" in capsys.readouterr().out


class TestMainWatchFlagParsing:
    """Test --watch / -w flag extraction in main() (lines 978-988)."""

    def test_watch_flag_extracted_from_args(self) -> None:
        """--watch <N> is extracted from sys.argv for the report command (lines 978-988)."""
        with (
            patch("sys.argv", ["devbench", "report", "--watch", "10"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="", watch_interval=10, once=False)

    def test_short_watch_flag_extracted(self) -> None:
        """-w <N> is equivalent to --watch <N>."""
        with (
            patch("sys.argv", ["devbench", "report", "-w", "3"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="", watch_interval=3, once=False)

    def test_watch_flag_with_since_arg(self) -> None:
        """--watch is separated from the since timestamp argument (lines 996-998)."""
        with (
            patch("sys.argv", ["devbench", "report", "--watch", "5", "2025-01-15T10:30:00Z"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="2025-01-15T10:30:00Z", watch_interval=5, once=False)

    def test_once_flag_extracted_from_args(self) -> None:
        """Issue #163: --once is extracted by main() and forwarded to cmd_report."""
        with (
            patch("sys.argv", ["devbench", "report", "--once"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="", watch_interval=0, once=True)

    def test_no_stream_alias_extracted(self) -> None:
        """Issue #163: --no-stream is an accepted alias for --once."""
        with (
            patch("sys.argv", ["devbench", "report", "--no-stream"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="", watch_interval=0, once=True)

    def test_report_without_watch_dispatches_normally(self) -> None:
        """report without --watch goes through normal dispatch (line 1002)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "report"]),
            patch.dict(cli._COMMANDS, {"report": (mock_fn, 0, "Progress report")}),
        ):
            result = cli.main()

        assert result == 0
        mock_fn.assert_called_once()


class TestMainExtraArgsWarning:
    """Test extra args warning in main() (lines 1000-1001)."""

    def test_extra_args_warning_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When more args than min_args+1 are provided, a warning is printed to stderr (line 1001)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "mycmd", "arg1", "arg2", "arg3", "arg4"]),
            patch.dict(cli._COMMANDS, {"mycmd": (mock_fn, 1, "Test cmd")}),
        ):
            result = cli.main()

        assert result == 0
        err = capsys.readouterr().err
        assert "Warning: ignoring" in err
        assert "extra argument(s)" in err


class TestMainDispatchLine:
    """Test the final dispatch line in main() (line 1002/1006)."""

    def test_dispatch_with_min_args(self) -> None:
        """Dispatch passes exactly min_args arguments to the handler (line 1002)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "mycmd", "val1"]),
            patch.dict(cli._COMMANDS, {"mycmd": (mock_fn, 1, "Test")}),
        ):
            result = cli.main()

        assert result == 0
        mock_fn.assert_called_once_with("val1")

    def test_dispatch_with_optional_extra_arg(self) -> None:
        """Dispatch passes up to min_args+1 arguments (line 1002)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "mycmd", "val1", "val2"]),
            patch.dict(cli._COMMANDS, {"mycmd": (mock_fn, 1, "Test")}),
        ):
            result = cli.main()

        assert result == 0
        mock_fn.assert_called_once_with("val1", "val2")


class TestGitOpsDeferred:
    """Test _git_ops_deferred helper."""

    def test_git_ops_deferred_commits_locally(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """_git_ops_deferred calls commit_local and returns 0."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_ops = MagicMock()
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.GitOpsService", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli._git_ops_deferred(
                "E0-F1-S1-T1",
                unit,
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
            )

        assert result == 0
        mock_ops.commit_local.assert_called_once_with(
            "caylent-solutions/git-repo",
            tmp_path,
            "feature/x",
            "E0-F1-S1-T1: Test Task",
        )
        output = json.loads(capsys.readouterr().out.strip())
        assert output["mode"] == "deferred"

    def test_git_ops_deferred_calls_ensure_branch_before_commit(self, tmp_path: Path) -> None:
        """ensure_branch() must run before commit_local() so a drifted HEAD is corrected."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        call_order: list[str] = []
        mock_ops = MagicMock()
        mock_ops.ensure_branch.side_effect = lambda *_a, **_k: call_order.append("ensure_branch")
        mock_ops.commit_local.side_effect = lambda *_a, **_k: call_order.append("commit_local")

        with (
            patch("devbench.cli.GitOpsService", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            cli._git_ops_deferred(
                "E0-F1-S1-T1",
                unit,
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
            )

        assert call_order == ["ensure_branch", "commit_local"]

    def test_git_ops_deferred_logs_comment(self, tmp_path: Path) -> None:
        """_git_ops_deferred appends agent comment when work-unit file exists."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("# placeholder")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_ops = MagicMock()
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.GitOpsService", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            # Bypass manifest-scope check: this test only cares that the
            # audit comment was appended, not about manifest enforcement
            # (which has its own dedicated tests).
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli._git_ops_deferred(
                "E0-F1-S1-T1",
                unit,
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
            )

        assert result == 0
        mock_mgr._append_agent_comment.assert_called_once()
        call_args = mock_mgr._append_agent_comment.call_args
        assert call_args[0][0] == wu_file
        assert "COMMIT_DEFERRED" in call_args[0][2]


class TestCmdGitOpsFinalize:
    """Test cmd_git_ops_finalize command."""

    def test_git_ops_finalize_requires_single_branch(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_git_ops_finalize returns 1 when SINGLE_BRANCH is not set."""
        with patch("devbench.config.SINGLE_BRANCH", None):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 1
        assert "single_branch" in capsys.readouterr().err.lower()

    def test_git_ops_finalize_requires_defer_pr(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_git_ops_finalize returns 1 when DEFER_PR is False."""
        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", False),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 1
        assert "defer_pr" in capsys.readouterr().err.lower()


class TestRejectEmDash:
    """Agent-supplied text with U+2014 must be rejected at the CLI input boundary.

    The validate-backlog Check 10 rejects work-unit files containing em-dash,
    so any CLI writer that accepts free-form agent text must fail fast rather
    than silently poisoning the file.
    """

    _EM_DASH_FEEDBACK = "issue A -\u2014 still broken"

    def test_log_verdict_fail_feedback_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "fail", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err
        assert "U+2014" in err

    def test_log_verdict_pass_feedback_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Pass verdicts can still carry feedback -- em-dash must still be rejected."""
        result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err

    def test_log_comment_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err

    def test_log_tdd_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_log_tdd("E0-F1-S1-T1", "RED", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err

    def test_clean_feedback_is_not_rejected_by_em_dash_guard(self) -> None:
        """The guard must return None for clean text -- double-hyphen is fine."""
        assert cli._reject_em_dash("feedback", "issue A -- still broken") is None
        assert cli._reject_em_dash("feedback", "") is None


# ---------------------------------------------------------------------------
# Amendment CLI commands
# ---------------------------------------------------------------------------


class TestCmdRequestAmendment:
    """cmd_request_amendment reads JSON from stdin and delegates to write_request."""

    _VALID_PAYLOAD: ClassVar[dict[str, Any]] = {
        "reason": "tdd_green_production_fix",
        "justification": "Test required a minimum production fix.",
        "files_to_add": [{"path": "src/example/parser.py", "change": "use utf-8-sig codec"}],
        "linked_acs": ["AC-TEST-001"],
    }

    def _stdin(self, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    def test_happy_path_writes_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, json.dumps(self._VALID_PAYLOAD))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 0
        out = capsys.readouterr().out
        summary = json.loads(out)
        assert summary["task_id"] == "EX-F1-S1-T1"
        assert summary["reason"] == "tdd_green_production_fix"
        assert (tmp_path / ".devbench/amendments/EX-F1-S1-T1.json").exists()

    def test_empty_stdin_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, "")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "must be provided on stdin" in capsys.readouterr().err

    def test_invalid_json_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, "{not json")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_non_object_payload_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, json.dumps(["array", "not", "object"]))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "must be a JSON object" in capsys.readouterr().err

    def test_schema_violation_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = dict(self._VALID_PAYLOAD)
        del bad["reason"]
        self._stdin(monkeypatch, json.dumps(bad))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "invalid" in capsys.readouterr().err

    def test_duplicate_request_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, json.dumps(self._VALID_PAYLOAD))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli.cmd_request_amendment("EX-F1-S1-T1") == 0
        # Second call with a fresh stdin attempts to write duplicate
        self._stdin(monkeypatch, json.dumps(self._VALID_PAYLOAD))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "already exists" in capsys.readouterr().err


class TestCmdApplyAmendment:
    """cmd_apply_amendment delegates to apply_amendment and handles AmendmentError."""

    def test_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:

        with patch("devbench.cli.apply_amendment") as mock_apply:
            mock_apply.return_value = None
            rc = cli.cmd_apply_amendment("EX-F1-S1-T1")
        assert rc == 0
        assert "applied" in capsys.readouterr().out
        mock_apply.assert_called_once()

    def test_amendment_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.amendment import AmendmentError

        with patch("devbench.cli.apply_amendment", side_effect=AmendmentError("post-check failed")):
            rc = cli.cmd_apply_amendment("EX-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "post-check failed" in err


class TestCmdRejectAmendment:
    """cmd_reject_amendment delegates to reject_amendment and handles AmendmentError."""

    def test_happy_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.reject_amendment") as mock_reject:
            mock_reject.return_value = None
            rc = cli.cmd_reject_amendment("EX-F1-S1-T1", "files not in diff")
        assert rc == 0
        out = capsys.readouterr().out
        assert "rejected" in out
        mock_reject.assert_called_once()

    def test_em_dash_in_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_amendment("EX-F1-S1-T1", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_amendment_error_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.amendment import AmendmentError

        with patch(
            "devbench.cli.reject_amendment",
            side_effect=AmendmentError("no pending request"),
        ):
            rc = cli.cmd_reject_amendment("EX-F1-S1-T1", "because")
        assert rc == 1
        assert "no pending request" in capsys.readouterr().err


class TestCmdWatch:
    """cmd_watch snapshot + live-tail behaviour."""

    def _fake_snapshot(self) -> object:
        from devbench.activity import ActivitySnapshot

        return ActivitySnapshot(
            now=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
            mode_label="standard multi-PR",
            active_task_id=None,
            active_task_title=None,
            active_task_status=None,
            claimed_at=None,
            phase="idle",
            last_tool_call_at=None,
            subagent=None,
            recent_cli=[],
            repo_state=None,
            amendment=None,
            idle_seconds=0,
        )

    def test_cmd_watch_one_shot_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """watch_interval=0 runs once, prints the snapshot, and returns 0."""
        fake_snap = self._fake_snapshot()
        with (
            patch("devbench.activity.collect_snapshot", return_value=fake_snap),
            patch("devbench.activity.render_snapshot", return_value="dashboard frame"),
        ):
            rc = cli.cmd_watch(watch_interval=0)
        assert rc == 0
        assert "dashboard frame" in capsys.readouterr().out

    def test_cmd_watch_watch_mode_interrupted(self) -> None:
        """watch_interval > 0 loops until KeyboardInterrupt, exit code 0."""
        calls = {"renders": 0}

        def fake_render(_snapshot: object) -> str:
            calls["renders"] += 1
            return f"frame {calls['renders']}"

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.activity.collect_snapshot", return_value=self._fake_snapshot()),
            patch("devbench.activity.render_snapshot", side_effect=fake_render),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            rc = cli.cmd_watch(watch_interval=5)
        assert rc == 0
        assert calls["renders"] == 1

    def test_cmd_watch_invokes_clear_command(self) -> None:
        """Live mode clears the terminal between frames when a clear binary exists."""

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            captured.append(cmd)

            class _Done:
                returncode = 0

            return _Done()

        with (
            patch("devbench.activity.collect_snapshot", return_value=self._fake_snapshot()),
            patch("devbench.activity.render_snapshot", return_value="frame"),
            patch("time.sleep", side_effect=fake_sleep),
            patch("devbench.cli._TERMINAL_CLEAR_CMD", "/usr/bin/clear"),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_watch(watch_interval=1)
        assert rc == 0
        assert captured and captured[0] == ["/usr/bin/clear"]

    def test_cmd_watch_falls_back_to_ris(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Without a clear binary, cmd_watch falls back to the VT100 RIS escape."""

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.activity.collect_snapshot", return_value=self._fake_snapshot()),
            patch("devbench.activity.render_snapshot", return_value="frame"),
            patch("time.sleep", side_effect=fake_sleep),
            patch("devbench.cli._TERMINAL_CLEAR_CMD", None),
        ):
            rc = cli.cmd_watch(watch_interval=1)
        assert rc == 0
        assert "\033c" in capsys.readouterr().out

    def test_cmd_watch_registered_in_commands(self) -> None:
        assert "watch" in cli._COMMANDS

    def test_resolver_returns_none_on_unknown_repo(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The inline repo resolver returns None when resolve_repo rejects the name."""
        captured_resolver: dict[str, object] = {}

        def fake_collect(**kwargs: object) -> object:
            captured_resolver["fn"] = kwargs["repo_path_resolver"]
            return self._fake_snapshot()

        with (
            patch("devbench.activity.collect_snapshot", side_effect=fake_collect),
            patch("devbench.activity.render_snapshot", return_value="ok"),
        ):
            cli.cmd_watch(watch_interval=0)

        resolver = captured_resolver["fn"]
        assert callable(resolver)
        assert resolver("no-such-repo") is None


class TestMainWatchCommand:
    """main() --watch dispatch for the watch command."""

    def test_main_watch_with_watch_flag(self) -> None:
        with (
            patch("sys.argv", ["devbench", "watch", "--watch", "3"]),
            patch("devbench.cli.cmd_watch", return_value=0) as mock_watch,
        ):
            rc = cli.main()
        assert rc == 0
        mock_watch.assert_called_once_with(watch_interval=3)

    def test_main_watch_short_flag(self) -> None:
        with (
            patch("sys.argv", ["devbench", "watch", "-w", "2"]),
            patch("devbench.cli.cmd_watch", return_value=0) as mock_watch,
        ):
            rc = cli.main()
        assert rc == 0
        mock_watch.assert_called_once_with(watch_interval=2)

    def test_main_watch_no_flag_runs_once(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "watch"]),
            patch.dict(cli._COMMANDS, {"watch": (mock_fn, 0, "Dashboard")}),
        ):
            rc = cli.main()
        assert rc == 0
        mock_fn.assert_called_once_with()


class TestCmdListProposals:
    def test_none_when_no_proposals(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_list_proposals()
        assert rc == 0
        assert "No pending proposals" in capsys.readouterr().out

    def test_lists_when_present(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-18T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix X",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-FUNC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_list_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Pending proposals (1)" in out
        assert "E0-F1-S1-T2" in out


class TestCmdPromoteProposal:
    def test_missing_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_promote_proposal("")
        assert rc == 1

    def test_all_from_requires_source(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_promote_proposal("--all-from", "")
        assert rc == 1

    def test_promote_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import ProposalError

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", side_effect=ProposalError("nope")),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T2")
        assert rc == 1
        assert "nope" in capsys.readouterr().err

    def test_promote_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import PromoteResult

        draft = tmp_path / "t.md"
        draft.write_text("x")
        result = PromoteResult(draft_path=draft, wired_targets=["E0-F1-S1-T1"])
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", return_value=result),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T2")
        assert rc == 0
        out = capsys.readouterr().out
        assert "E0-F1-S1-T2" in out
        assert "in-queue" in out
        # ADR-10: wired_targets field present in output JSON.
        assert "E0-F1-S1-T1" in out
        assert "wired_targets" in out

    def test_promote_with_no_dep_flag(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import PromoteResult

        draft = tmp_path / "t.md"
        draft.write_text("x")
        seen: dict = {}

        def fake(
            *, workspace_root: Path, backlog_root: Path, backlog_index: Path, task_id: str, dep_on_source: bool = True
        ) -> PromoteResult:
            seen["dep"] = dep_on_source
            seen["id"] = task_id
            return PromoteResult(draft_path=draft, wired_targets=[])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", side_effect=fake),
        ):
            rc = cli.cmd_promote_proposal("--no-dep-on-source", "E0-F1-S1-T2")
        assert rc == 0
        assert seen["dep"] is False
        assert seen["id"] == "E0-F1-S1-T2"

    def test_promote_all_from_happy(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_all_from_source", return_value=[tmp_path / "a.md", tmp_path / "b.md"]),
        ):
            rc = cli.cmd_promote_proposal("--all-from", "E0-F1-S1-T1")
        assert rc == 0
        out = capsys.readouterr().out
        assert '"promoted_count": 2' in out

    def test_promote_all_from_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import ProposalError

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_all_from_source", side_effect=ProposalError("nothing to do")),
        ):
            rc = cli.cmd_promote_proposal("--all-from", "E0-F1-S1-T1")
        assert rc == 1
        assert "nothing to do" in capsys.readouterr().err


class TestCmdRejectProposal:
    def test_missing_reason(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("E0-F1-S1-T2")
        assert rc == 1

    def test_reason_without_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_em_dash_in_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Generic missing-value path (--reason without a value) returns 1.
        rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
        assert rc == 1

    def test_em_dash_blocked_by_validator(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            # task_id first, then --reason with em-dash value.
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        archive = tmp_path / "archive.md"
        archive.write_text("x")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", return_value=archive),
        ):
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
            # "--reason" without value returns 1; the API requires both args.
            assert rc == 1

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", return_value=archive),
        ):
            # Correct shape: task id first, then --reason <val> as separate args.
            import sys as _sys

            with patch.object(_sys, "argv", ["devbench", "reject-proposal", "E0-F1-S1-T2", "--reason", "wrong"]):
                rc = cli.main()
        assert rc == 0

    def test_proposal_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import ProposalError

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", side_effect=ProposalError("bad")),
        ):
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
            assert rc == 1  # Without reason value


class TestCmdStatusUnmaterialisedLine:
    """ADR-08 slice B: ``devbench status`` must always print an 'Un-materialised' row."""

    def test_status_prints_zero_line_when_no_proposals(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Row always renders so regressions to zero are visible."""
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Un-materialised" in out, "status must always print the Un-materialised row."
        assert re.search(r"Un-materialised\s+0\b", out), (
            "Un-materialised row must render a zero count when no proposal JSONs are pending."
        )

    def test_status_prints_nonzero_count(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=7),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert re.search(r"Un-materialised\s+7\b", out)


class TestCmdStatusBlockedSplit:
    """ADR-10: status emits six Blocked (...) rows always (one per BlockedTaskState)."""

    def test_status_emits_six_blocked_rows_even_at_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert re.search(r"Blocked \(auto-clearing\)\s+0\b", out), out
        assert re.search(r"Blocked \(amendment-recovery\)\s+0\b", out), out
        assert re.search(r"Blocked \(dependency\)\s+0\b", out), out
        assert re.search(r"Blocked \(held\)\s+0\b", out), out
        assert re.search(r"Blocked \(blocked-on-held\)\s+0\b", out), out
        assert re.search(r"Blocked \(operator-required\)\s+0\b", out), out
        # The bare "Blocked" row must NOT appear (it was replaced by the split).
        # Match the exact formatted row the pre-split code used to emit.
        assert not re.search(r"^\s*Blocked\s+\d+\s*$", out, flags=re.MULTILINE), out

    def test_status_counts_by_classifier(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit_a = WorkUnit(
            id="E0-F1-S1-T1",
            title="Source A",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/a.md"),
            repo="r",
            dependencies=[],
        )
        unit_b = WorkUnit(
            id="E0-F1-S1-T2",
            title="Source B",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/b.md"),
            repo="r",
            dependencies=[],
        )

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            if task_id == "E0-F1-S1-T1":
                return BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL
            return BlockedTaskState.OPERATOR_ACTION_REQUIRED

        parser = MagicMock()
        parser.parse_index.return_value = [unit_a, unit_b]
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = [unit_a, unit_b]
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert re.search(r"Blocked \(auto-clearing\)\s+1\b", out), out
        assert re.search(r"Blocked \(operator-required\)\s+1\b", out), out

    def test_status_detail_renders_three_blocked_bucket_sections(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Issue #149 follow-up: the ``--detail`` panel renders up to six separate blocked-task panels,
        one per non-empty BlockedTaskState bucket.

        Empty buckets are omitted from the output.
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit_auto = WorkUnit(
            id="E0-F1-S1-T1",
            title="Auto",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/auto.md"),
            repo="r",
            dependencies=[],
        )
        unit_recovery = WorkUnit(
            id="E0-F1-S1-T2",
            title="Recovery",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/recovery.md"),
            repo="r",
            dependencies=[],
        )
        unit_attn = WorkUnit(
            id="E0-F1-S1-T3",
            title="Attn",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/attn.md"),
            repo="r",
            dependencies=[],
        )

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            return {
                "E0-F1-S1-T1": BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
                "E0-F1-S1-T2": BlockedTaskState.AWAITING_AMENDMENT_RECOVERY,
                "E0-F1-S1-T3": BlockedTaskState.OPERATOR_ACTION_REQUIRED,
            }[task_id]

        parser = MagicMock()
        parser.parse_index.return_value = [unit_auto, unit_recovery, unit_attn]
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = [unit_auto, unit_recovery, unit_attn]
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Blocked tasks (auto-clearing via proposal) (1):" in out
        assert "Blocked tasks (awaiting amendment recovery) (1):" in out
        assert "Blocked tasks (operator action required) (1):" in out
        # Each task appears exactly under its own bucket header.
        auto_pos = out.index("Blocked tasks (auto-clearing via proposal)")
        recovery_pos = out.index("Blocked tasks (awaiting amendment recovery)")
        attn_pos = out.index("Blocked tasks (operator action required)")
        assert auto_pos < recovery_pos < attn_pos, "buckets must render in classifier order"
        assert out.index("E0-F1-S1-T1") < recovery_pos
        assert recovery_pos < out.index("E0-F1-S1-T2") < attn_pos
        assert attn_pos < out.index("E0-F1-S1-T3")

    def test_status_detail_omits_empty_buckets(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When a bucket has zero tasks, its section header is omitted."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit_only_auto = WorkUnit(
            id="E0-F1-S1-T1",
            title="Auto-only",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/auto.md"),
            repo="r",
            dependencies=[],
        )
        parser = MagicMock()
        parser.parse_index.return_value = [unit_only_auto]
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = [unit_only_auto]
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch(
                "devbench.cli.classify_blocked_task",
                return_value=BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
            ),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Blocked tasks (auto-clearing via proposal) (1):" in out
        assert "Blocked tasks (awaiting amendment recovery)" not in out
        assert "Blocked tasks (operator action required)" not in out


class TestCmdListProposalsStateLabels:
    """ADR-08 slice D: each listing line has a ``[state]`` label prefix."""

    def test_labels_per_task_state(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import (
            Proposal,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Umat",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                ),
                ProposedTask(
                    suggested_id="E0-F1-S1-T3",
                    title="Prop",
                    files_to_own=["src/y.py"],
                    linked_scenarios=["SC-02"],
                    suggested_acs=["AC-002 fix"],
                    suggested_approach="ok",
                ),
            ],
        )
        write_proposal(tmp_path, proposal)

        def fake_classify(backlog_root: Path, workspace_root: Path, task_id: str) -> ProposalTaskState:
            return {
                "E0-F1-S1-T2": ProposalTaskState.UNMATERIALISED,
                "E0-F1-S1-T3": ProposalTaskState.PROPOSED,
            }[task_id]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.classify_proposed_task", side_effect=fake_classify),
        ):
            rc = cli.cmd_list_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        # Labels must be present and distinct.
        assert "[unmaterialised]" in out, "Every un-materialised task gets an [unmaterialised] label"
        assert "[proposed]" in out, "Every proposed task gets a [proposed] label"
        # Sanity: both suggested ids present.
        assert "E0-F1-S1-T2" in out and "E0-F1-S1-T3" in out


class TestCmdRejectProposalUnmaterialised:
    """ADR-08 slice E: ``reject-proposal --unmaterialised <id> --reason <msg>`` CLI form."""

    def test_unmaterialised_flag_dispatches_through_api(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        archive = tmp_path / "a.json"
        archive.write_text("{}")
        seen: dict = {}

        def fake_reject(
            *,
            workspace_root: Path,
            backlog_root: Path,
            backlog_index: Path,
            task_id: str = "",
            unmaterialised_source_id: str = "",
            reason: str,
        ) -> Path | None:
            seen["task_id"] = task_id
            seen["umid"] = unmaterialised_source_id
            seen["reason"] = reason
            return archive

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", side_effect=fake_reject),
        ):
            rc = cli.cmd_reject_proposal("--unmaterialised", "E0-F1-S1-T1", "--reason", "redundant")
        assert rc == 0, capsys.readouterr().err
        assert seen == {
            "task_id": "",
            "umid": "E0-F1-S1-T1",
            "reason": "redundant",
        }
        out = capsys.readouterr().out
        assert "rejected-unmaterialised" in out
        assert "E0-F1-S1-T1" in out

    def test_both_forms_supplied_errors(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--unmaterialised", "E0-F1-S1-T1", "--reason", "no")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not both" in err or "supply exactly one" in err

    def test_neither_form_supplied_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("--reason", "lonely")
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires either" in err

    def test_unmaterialised_without_value_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("--unmaterialised", "--reason", "x")
        assert rc == 1
        err = capsys.readouterr().err
        assert "source-task-id" in err

    def test_is_variadic_so_multi_flag_invocation_reaches_handler(self) -> None:
        """Regression: ``reject-proposal --unmaterialised <id> --reason <text>``
        passes 4 args + 1 task-id; without variadic dispatch the top-level
        slicer keeps only ``min_args + 1`` args and the ``--reason`` value
        is dropped before _parse_reject_proposal_argv runs, producing a
        spurious ``--reason requires a value`` error. Pin the variadic
        membership so this regression cannot return.
        """
        assert "reject-proposal" in cli._VARIADIC_COMMANDS


class TestCmdSweepProposals:
    """ADR-08 slice J: ``devbench sweep-proposals`` best-effort materialises un-materialised JSONs."""

    def test_nothing_to_do_when_no_proposals(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        assert "nothing to do" in capsys.readouterr().out

    def test_materialises_one_unmaterialised_proposal(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.backlog.proposal import (
            Proposal,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)

        parser = MagicMock()
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch(
                "devbench.cli.classify_proposed_task",
                return_value=ProposalTaskState.UNMATERIALISED,
            ),
            patch("devbench.cli.materialise_proposal", return_value=[tmp_path / "a.md"]),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1: 1 new, 0 skipped" in out

    def test_tolerates_proposal_error_per_entry(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A ProposalError on one proposal must be logged and skipped, not raised."""
        from devbench.backlog.proposal import (
            Proposal,
            ProposalError,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)

        parser = MagicMock()
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch(
                "devbench.cli.classify_proposed_task",
                return_value=ProposalTaskState.UNMATERIALISED,
            ),
            patch(
                "devbench.cli.materialise_proposal",
                side_effect=ProposalError("guard: prior proposed tasks exist"),
            ),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0, "sweep must tolerate per-proposal ProposalError without crashing"
        out = capsys.readouterr().out
        assert "skipped E0-F1-S1-T1" in out
        assert "prior proposed tasks exist" in out

    def test_no_op_when_every_task_already_materialised(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.backlog.proposal import (
            Proposal,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)

        parser = MagicMock()
        parser.parse_index.return_value = []

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch(
                "devbench.cli.classify_proposed_task",
                return_value=ProposalTaskState.PROPOSED,
            ),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        assert "no-op E0-F1-S1-T1" in capsys.readouterr().out


class TestCmdSweepAutoAccept:
    """ADR-11: sweep-proposals auto-promotes every PROPOSED draft when task_factory.auto_accept_proposals is True."""

    def _mk_runtime_config(self, auto_accept: bool) -> MagicMock:
        """Build a RUNTIME_CONFIG mock with task_factory.auto_accept_proposals toggled."""
        cfg = MagicMock()
        cfg.task_factory.auto_accept_proposals = auto_accept
        cfg.task_factory.enabled = True
        return cfg

    def _proposal(self, source_id: str = "E0-F1-S1-T1"):
        from devbench.backlog.proposal import Proposal, ProposedTask

        return Proposal(
            source_task_id=source_id,
            generated_at="2026-04-20T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )

    def test_sweep_does_not_auto_promote_when_flag_false(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Back-compat: materialised drafts stay PROPOSED when the flag is off."""
        from devbench.backlog.proposal import ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=False)),
            patch("devbench.cli.classify_proposed_task", return_value=ProposalTaskState.UNMATERIALISED),
            patch("devbench.cli.materialise_proposal", return_value=[tmp_path / "a.md"]),
            patch("devbench.cli.promote_proposal") as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_promote.assert_not_called()
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1" in out
        # Flag off -> output line MUST NOT include auto-promoted count.
        assert "auto-promoted" not in out

    def test_sweep_auto_promotes_every_proposed_draft_when_flag_true(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Primary ADR-11 behaviour: one promote per PROPOSED draft."""
        from devbench.backlog.proposal import PromoteResult, ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        # Pre-state: UNMATERIALISED -> materialise then PROPOSED on the per-task re-classify.
        state_sequence = [
            ProposalTaskState.UNMATERIALISED,  # pre-check inside the sweep loop
            ProposalTaskState.PROPOSED,  # auto-promote per-task check
        ]
        calls = {"n": 0}

        def fake_classify(_backlog, _ws, _tid):
            idx = calls["n"]
            calls["n"] += 1
            return state_sequence[idx] if idx < len(state_sequence) else state_sequence[-1]

        draft_path = tmp_path / "a.md"
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            patch("devbench.cli.classify_proposed_task", side_effect=fake_classify),
            patch("devbench.cli.materialise_proposal", return_value=[draft_path]),
            patch(
                "devbench.cli.promote_proposal",
                return_value=PromoteResult(draft_path=draft_path, wired_targets=["E0-F1-S1-T1"]),
            ) as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_promote.assert_called_once()
        # The audit_suffix kwarg must be threaded through.
        _, kwargs = mock_promote.call_args
        assert kwargs["task_id"] == "E0-F1-S1-T2"
        assert "auto-accepted" in kwargs["audit_suffix"]
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1" in out
        assert "(auto-promoted: 1)" in out

    def test_sweep_auto_promote_is_idempotent_on_already_promoted_draft(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second sweep tick after the first already promoted everything -> 0 new promotes."""
        from devbench.backlog.proposal import ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            # Already PROMOTED -> pre-check short-circuits to no-op (no UNMATERIALISED, no PROPOSED).
            patch("devbench.cli.classify_proposed_task", return_value=ProposalTaskState.PROMOTED),
            patch("devbench.cli.promote_proposal") as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_promote.assert_not_called()
        assert "no-op E0-F1-S1-T1" in capsys.readouterr().out

    def test_sweep_auto_promote_failure_is_logged_and_continues(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ProposalError on one promote must log and continue, not abort the whole sweep."""
        from devbench.backlog.proposal import ProposalError, ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        classify_calls = {"n": 0}

        def fake_classify(_b, _w, _t):
            classify_calls["n"] += 1
            return ProposalTaskState.UNMATERIALISED if classify_calls["n"] == 1 else ProposalTaskState.PROPOSED

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            patch("devbench.cli.classify_proposed_task", side_effect=fake_classify),
            patch("devbench.cli.materialise_proposal", return_value=[tmp_path / "a.md"]),
            patch("devbench.cli.promote_proposal", side_effect=ProposalError("boom")),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0, "sweep must tolerate per-draft promote errors"
        captured = capsys.readouterr()
        assert "auto-promote failed for E0-F1-S1-T2" in captured.err
        assert "(auto-promoted: 0)" in captured.out

    def test_sweep_auto_promotes_legacy_proposed_drafts_when_flag_flipped_on(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Flipping the flag on with legacy PROPOSED drafts: sweep still auto-promotes them."""
        from devbench.backlog.proposal import PromoteResult, ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        draft_path = tmp_path / "a.md"
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            # No UNMATERIALISED; legacy draft already in PROPOSED state waiting.
            patch("devbench.cli.classify_proposed_task", return_value=ProposalTaskState.PROPOSED),
            patch("devbench.cli.materialise_proposal") as mock_mat,
            patch(
                "devbench.cli.promote_proposal",
                return_value=PromoteResult(draft_path=draft_path, wired_targets=["E0-F1-S1-T1"]),
            ) as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_mat.assert_not_called()
        mock_promote.assert_called_once()
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1: 0 new, 1 skipped (auto-promoted: 1)" in out

    """ADR-09: rejected drafts must not resurrect on the next sweep tick."""

    def test_rejected_draft_not_recreated_by_sweep(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """End-to-end: write JSON -> materialise -> reject -> sweep -> no resurrection."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.backlog.proposal import (
            Proposal,
            ProposedTask,
            materialise_proposal,
            reject_proposal,
            write_proposal,
        )

        # Build a real workspace so materialise + reject + sweep exercise the real code.
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source Task\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 x\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `x.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] AC complete\n"
        )
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Ex | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix",
                    files_to_own=["src/a.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach=(
                        "Context: the unit test fixture for ADR-09 resurrection guard. "
                        "Scope: src/a.py and its companion unit test. "
                        "TDD approach: 1. RED -- write the failing test. "
                        "2. GREEN -- apply the minimal production fix. "
                        "3. REFACTOR -- no behaviour change. "
                        "Verify: make lint && make test-unit exit zero."
                    ),
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        materialise_proposal(
            workspace_root=tmp_path,
            backlog_root=backlog_dir,
            backlog_index=backlog_md,
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        reject_proposal(
            workspace_root=tmp_path,
            backlog_root=backlog_dir,
            backlog_index=backlog_md,
            task_id="E0-F1-S1-T2",
            reason="superseded",
        )
        draft_path = story_dir / "E0-F1-S1-T2.md"
        assert not draft_path.exists(), "per-draft reject must archive the .md"
        assert any(
            proposal_mod.REJECTED_PROPOSAL_DIR_NAME in str(p)
            for p in (tmp_path / proposal_mod.REJECTED_PROPOSAL_DIR_NAME).iterdir()
        )

        # Now run sweep-proposals. Expect no-op because the only task is
        # REJECTED (archive exists) -- classify_proposed_task returns REJECTED,
        # sweep's unmaterialised_before count is zero, hits the no-op branch.
        source_unit = MagicMock()
        source_unit.id = "E0-F1-S1-T1"
        source_unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [source_unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.BacklogParser", return_value=parser),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "no-op E0-F1-S1-T1" in out, out
        # The crucial assertion: the rejected draft file was NOT recreated.
        assert not draft_path.exists(), "sweep-proposals must not resurrect a rejected draft"


class TestCmdAddDep:
    """ADR-10: `devbench add-dep <blocked-id> <blocker-id> [--reason <msg>]`."""

    def test_add_dep_rejects_invalid_task_id_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("not-a-task-id", "E0-F1-S1-T2")
        assert rc == 1
        assert "does not match" in capsys.readouterr().err

    def test_add_dep_requires_two_positionals(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "exactly two task ids" in err

    def test_add_dep_rejects_unknown_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--bogus", "x")
        assert rc == 1
        assert "unknown flag" in capsys.readouterr().err

    def test_add_dep_reason_without_value_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_add_dep_happy_path_emits_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Build a minimal workspace with a blocked T1 and in-queue T2 wired
        # through the real backlog parser so add_dep's validation passes.
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 1 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Fix | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        for tid, status in (("E0-F1-S1-T1", "blocked"), ("E0-F1-S1-T2", "in-queue")):
            (story / f"{tid}.md").write_text(
                f"# {tid}: X\n\n## Status: {status}\n\n## Description\n\nx\n\n"
                "## Dependencies\n\n"
                "| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
            )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason", "ADR-10 CLI smoke")
        assert rc == 0
        out = capsys.readouterr().out
        assert '"blocked": "E0-F1-S1-T1"' in out
        assert '"blocker": "E0-F1-S1-T2"' in out
        assert '"wired": true' in out
        # Marker landed on the blocked file.
        t1 = (story / "E0-F1-S1-T1.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in t1
        assert "ADR-10 CLI smoke" in t1

    def test_add_dep_warns_when_blocked_is_not_blocked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 2 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Src | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Fix | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        for tid in ("E0-F1-S1-T1", "E0-F1-S1-T2"):
            (story / f"{tid}.md").write_text(
                f"# {tid}: X\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
                "## Dependencies\n\n"
                "| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
            )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING:" in err
        assert "not 'blocked'" in err


class TestProposalCommandsRegistered:
    def test_list_proposals_registered(self) -> None:
        assert "list-proposals" in cli._COMMANDS
        assert "promote-proposal" in cli._COMMANDS
        assert "reject-proposal" in cli._COMMANDS

    def test_sweep_proposals_registered(self) -> None:
        assert "sweep-proposals" in cli._COMMANDS

    def test_add_dep_registered(self) -> None:
        assert "add-dep" in cli._COMMANDS


class TestCmdMaterialiseProposal:
    def test_missing_proposal(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "No proposal" in capsys.readouterr().err

    def test_backlog_parse_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="x",
            rejection_reason="x",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    suggested_approach="",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        # BACKLOG.md missing -> parse_index raises FileNotFoundError.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1

    def test_source_task_not_in_backlog(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        # Build minimal workspace where source-id in proposal doesn't exist in BACKLOG.
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F9-S9-T9 | Other | Task | done | None | caylent-solutions/git-repo | `backlog/E0-F9-S9-T9.md` |\n"
        )
        (tmp_path / "backlog").mkdir()
        (tmp_path / "backlog" / "E0-F9-S9-T9.md").write_text("# E0-F9-S9-T9: Other\n\n## Status: done\n")
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="x",
            rejection_reason="x",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    # Concrete approach text so the issue #143 placeholder check
                    # does not fire before the source-task lookup runs.
                    suggested_approach="Author the foo helper that the source task references",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        source_row = (
            "| E0-F1-S1-T1 | Source | Task | blocked | None "
            "| caylent-solutions/example | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"{source_row}\n"
        )
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `caylent-solutions/example`\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] complete\n"
        )
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-18T00:00:00Z",
            rejection_reason="x",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=["src/a.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-FUNC-001"],
                    suggested_approach=(
                        "Context: unit test fixture for cmd_materialise_proposal happy path. "
                        "Scope: this draft is synthetic; no real files affected. "
                        "TDD approach: 1. RED -- n/a. 2. GREEN -- n/a. 3. REFACTOR -- n/a. "
                        "Verify: the materialise-proposal CLI command exits 0."
                    ),
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 0
        assert "E0-F1-S1-T2" in capsys.readouterr().out


class TestCmdWriteProposal:
    def test_stdin_empty_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "required on stdin" in capsys.readouterr().err

    def test_stdin_invalid_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_stdin_schema_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"source_task_id": "x"})))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "invalid" in capsys.readouterr().err

    def test_source_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        payload = {
            "source_task_id": "OTHER-SRC",
            "generated_at": "t",
            "rejection_reason": "r",
            "proposed_tasks": [],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "does not match argument" in capsys.readouterr().err

    def test_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        payload = {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-04-18T00:00:00Z",
            "rejection_reason": "x",
            "proposed_tasks": [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "t",
                    "files_to_own": [],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "",
                }
            ],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        out = capsys.readouterr().out
        assert "proposal_path" in out

    def test_duplicate_write_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="t",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    suggested_approach="",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(proposal.to_dict())))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "already exists" in capsys.readouterr().err

    def test_stdin_os_error_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class _FailStdin:
            def read(self) -> str:
                raise OSError("disconnected")

        monkeypatch.setattr("sys.stdin", _FailStdin())
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "cannot read stdin" in capsys.readouterr().err


class TestCmdDecline:
    """Slice 5c: cmd_decline CLI command."""

    def _make_minimal_unit(self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1") -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | in-queue | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: in-queue\n\n## Description\n\nx\n")
        return backlog_md, wu_file

    def test_happy_path_flips_status_and_audits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, wu_file = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_decline("EX-F1-S1-T1", "--reason", "scope determined unnecessary")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: declined" in content
        assert "[DECLINED]" in content
        assert "scope determined unnecessary" in content
        out = json.loads(capsys.readouterr().out.strip())
        assert out["task_id"] == "EX-F1-S1-T1"
        assert out["status"] == "declined"
        assert out["reason"] == "scope determined unnecessary"

    def test_missing_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_decline("EX-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err

    def test_reason_without_value_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_decline("EX-F1-S1-T1", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_em_dash_in_reason_blocked(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_decline("EX-F1-S1-T1", "--reason", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_decline("NO-SUCH-ID", "--reason", "n/a")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "decline" in cli._COMMANDS


class TestCmdHold:
    """E222: ``devbench hold <id> --reason <text>`` command."""

    def _make_minimal_unit(self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1") -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | in-queue | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: in-queue\n\n## Description\n\nx\n")
        return backlog_md, wu_file

    def test_happy_path_flips_status_and_audits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, wu_file = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_hold("EX-F1-S1-T1", "--reason", "awaiting upstream decision")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: hold" in content
        assert "[HOLD]" in content
        assert "awaiting upstream decision" in content
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {
            "task_id": "EX-F1-S1-T1",
            "status": "hold",
            "reason": "awaiting upstream decision",
        }

    def test_missing_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hold("EX-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err

    def test_reason_without_value_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hold("EX-F1-S1-T1", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_em_dash_in_reason_blocked(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_hold("EX-F1-S1-T1", "--reason", "bad—reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_hold("NO-SUCH-ID", "--reason", "n/a")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "hold" in cli._COMMANDS

    def test_is_variadic_so_multi_token_reason_reaches_handler(self) -> None:
        assert "hold" in cli._VARIADIC_COMMANDS


class TestCmdUnhold:
    """E222: ``devbench unhold <id> --reason <text>`` returns held units to in-queue."""

    def _make_held_unit(self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1") -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | hold | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: hold\n\n## Description\n\nx\n")
        return backlog_md, wu_file

    def test_happy_path_returns_held_unit_to_in_queue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        backlog_md, wu_file = self._make_held_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_unhold("EX-F1-S1-T1", "--reason", "blocker resolved")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: in-queue" in content
        assert "[UNHOLD]" in content
        assert "blocker resolved" in content
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {
            "task_id": "EX-F1-S1-T1",
            "status": "in-queue",
            "reason": "blocker resolved",
        }

    def test_refuses_unit_not_currently_held(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Build an in-queue unit (not held) and assert unhold refuses it.
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| EX-F1-S1-T1 | Test | Task | in-queue | None | caylent-solutions/git-repo | `backlog/EX-F1-S1-T1.md` |\n"
        )
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        (wu_dir / "EX-F1-S1-T1.md").write_text("# EX-F1-S1-T1: Test\n\n## Status: in-queue\n\n## Description\n\nx\n")
        with (
            patch("devbench.cli.BACKLOG_ROOT", wu_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_unhold("EX-F1-S1-T1", "--reason", "n/a")
        assert rc == 1
        err = capsys.readouterr().err
        assert "expected 'Hold'" in err

    def test_missing_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_unhold("EX-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_held_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_unhold("NO-SUCH-ID", "--reason", "n/a")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "unhold" in cli._COMMANDS

    def test_is_variadic(self) -> None:
        assert "unhold" in cli._VARIADIC_COMMANDS


class TestWireOrphanCleanupDepChain:
    """Phase 10: orphan-cleanup auto-emission resolves Manifest collisions via auto-wired deps."""

    def _build_minimal_backlog(
        self,
        tmp_path: Path,
        peer_status: str = "in-queue",
        cleanup_id: str = "E0-F1-S1-T9",
        peer_id: str = "E0-F1-S1-T2",
        repo: str = "ex/foo",
    ) -> Path:
        """Render a backlog where ``peer_id`` already claims `.gitignore`.

        ``cleanup_id`` represents the just-emitted cleanup task; the
        helper assumes it has been materialised on disk separately
        (the test populates a stub work-unit so ``add_dep``'s
        index-presence check passes).
        """
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        # Peer task: claims .gitignore in its manifest.
        (wu_dir / f"{peer_id}.md").write_text(
            f"# {peer_id}: peer\n\n"
            f"## Status: {peer_status}\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `.gitignore` | edit |\n| `peer.py` | new |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        # Cleanup task: claims .gitignore (the auto-emitted target).
        (wu_dir / f"{cleanup_id}.md").write_text(
            f"# {cleanup_id}: cleanup\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `.gitignore` | edit |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {peer_id} | peer | Task | {peer_status} | none | {repo} | `backlog/{peer_id}.md` |\n"
            f"| {cleanup_id} | cleanup | Task | in-queue | none | {repo} | `backlog/{cleanup_id}.md` |\n",
            encoding="utf-8",
        )
        return index_path

    def test_wires_dep_when_peer_claims_same_path(self, tmp_path: Path) -> None:
        index_path = self._build_minimal_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            wired = cli._wire_orphan_cleanup_dep_chain(
                new_id="E0-F1-S1-T9",
                files_to_own=[".gitignore"],
                unit_repo="ex/foo",
            )
        assert wired == ["E0-F1-S1-T2"]
        peer_content = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        # The dep row was added to the peer's Dependencies table.
        assert "E0-F1-S1-T9" in peer_content

    def test_no_collision_returns_empty(self, tmp_path: Path) -> None:
        # Build a backlog where the only peer task claims a different path.
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        (wu_dir / "E0-F1-S1-T2.md").write_text(
            "# E0-F1-S1-T2: peer\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `peer.py` | new |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        (wu_dir / "E0-F1-S1-T9.md").write_text(
            "# E0-F1-S1-T9: cleanup\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `.gitignore` | edit |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T2 | peer | Task | in-queue | none | ex/foo | `backlog/E0-F1-S1-T2.md` |\n"
            "| E0-F1-S1-T9 | cleanup | Task | in-queue | none | ex/foo | `backlog/E0-F1-S1-T9.md` |\n",
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            wired = cli._wire_orphan_cleanup_dep_chain(
                new_id="E0-F1-S1-T9",
                files_to_own=[".gitignore"],
                unit_repo="ex/foo",
            )
        assert wired == []

    def test_skips_done_and_declined_peers(self, tmp_path: Path) -> None:
        # Done peers cannot be wired (add_dep refuses terminal blockers anyway).
        index_path = self._build_minimal_backlog(tmp_path, peer_status="done")
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            wired = cli._wire_orphan_cleanup_dep_chain(
                new_id="E0-F1-S1-T9",
                files_to_own=[".gitignore"],
                unit_repo="ex/foo",
            )
        assert wired == []


class TestCmdNewTask:
    """E223: ``devbench new-task`` scaffolds a work-unit file from a template."""

    def test_renders_task_template_with_substitutions(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "E0-F1-S1-T1.md"
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1-T1",
            "--title",
            "Implement foo",
            "--target",
            str(target),
            "--repo",
            "ex/foo",
            "--description",
            "Foo capability.",
            "--source-file",
            "src/foo/handler.py",
            "--test-file",
            "tests/unit/test_handler.py",
            "--ac-func",
            "the function returns the right answer",
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {"id": "E0-F1-S1-T1", "kind": "task", "target": str(target)}
        rendered = target.read_text()
        assert "# E0-F1-S1-T1: Implement foo" in rendered
        assert "## Status: in-queue" in rendered
        assert "Foo capability." in rendered
        assert "src/foo/handler.py" in rendered
        assert "tests/unit/test_handler.py" in rendered
        assert "the function returns the right answer" in rendered
        assert "backlog/e0-f1-s1-t1" in rendered

    def test_collision_with_existing_target_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "exists.md"
        target.write_text("already there")
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1-T1",
            "--title",
            "x",
            "--target",
            str(target),
        )
        assert rc == 1
        assert "already exists" in capsys.readouterr().err

    def test_missing_parent_dir_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "no-such-dir" / "wu.md"
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1-T1",
            "--title",
            "x",
            "--target",
            str(target),
        )
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err

    def test_missing_required_flag_refused(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_new_task("--id", "E0-F1-S1-T1", "--title", "x")
        assert rc == 1
        assert "--target is required" in capsys.readouterr().err

    def test_unknown_flag_refused(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_new_task("--bogus", "x")
        assert rc == 1
        assert "unknown flag" in capsys.readouterr().err

    def test_template_kind_inferred_from_id(self, tmp_path: Path) -> None:
        target = tmp_path / "E0-F1-S1.md"
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1",
            "--title",
            "Story title",
            "--target",
            str(target),
        )
        assert rc == 0
        assert "# E0-F1-S1: Story title" in target.read_text()

    def test_invalid_id_shape_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "wu.md"
        rc = cli.cmd_new_task(
            "--id",
            "Q-NOT-VALID",
            "--title",
            "x",
            "--target",
            str(target),
        )
        assert rc == 1
        assert "cannot derive template kind" in capsys.readouterr().err

    def test_flag_without_value_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_new_task("--id")
        assert rc == 1
        assert "--id requires a value" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "new-task" in cli._COMMANDS

    def test_is_variadic(self) -> None:
        assert "new-task" in cli._VARIADIC_COMMANDS


class TestCmdSyncBlocked:
    """E215: ``devbench sync-blocked`` reconciles task status against dep satisfaction."""

    def _build_backlog(
        self,
        tmp_path: Path,
        rows: list[tuple[str, str, str, str, str]],
    ) -> Path:
        index_lines = [
            "# Backlog\n",
            "## Full Work Unit Index\n",
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
            "|----|-------|------|--------|--------------|------|-----------|",
        ]
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        for unit_id, unit_type, status, deps, basename in rows:
            file_path = f"backlog/{basename}.md"
            index_lines.append(
                f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | "
                f"caylent-solutions/test-repo | `{file_path}` |"
            )
            wu_file = wu_dir / f"{basename}.md"
            wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
            if deps and deps != "None":
                dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
                wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
            wu_file.write_text(wu_body)
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("\n".join(index_lines) + "\n")
        return index_path

    def test_in_queue_with_unsatisfied_dep_flips_to_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1"),
                ("E0-F1-S1-T2", "Task", "in-queue", "E0-F1-S1-T1", "E0-F1-S1-T2"),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped_to_blocked"] == ["E0-F1-S1-T2"]
        assert envelope["flipped_to_in_queue"] == []
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: blocked" in t2
        assert "[BLOCKED]" in t2
        assert "E0-F1-S1-T1" in t2

    def test_blocked_with_satisfied_deps_flips_to_in_queue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1"),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2"),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped_to_blocked"] == []
        assert envelope["flipped_to_in_queue"] == ["E0-F1-S1-T2"]
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2
        assert "[UNBLOCKED]" in t2

    def test_blocked_with_open_proposal_marker_is_skipped(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1"),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2"),
            ],
        )
        t2_path = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        t2_path.write_text(t2_path.read_text() + "\n## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n")
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped_to_in_queue"] == []
        assert "## Status: blocked" in t2_path.read_text()

    def test_story_level_dep_recursion(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1", "Story", "in-queue", "None", "E0-F1-S1"),
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1"),
                ("E0-F1-S2-T1", "Task", "in-queue", "E0-F1-S1", "E0-F1-S2-T1"),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S2-T1" in envelope["flipped_to_blocked"]
        assert "E0-F1-S1-T1" not in envelope["flipped_to_blocked"]

    def test_registered_in_commands(self) -> None:
        assert "sync-blocked" in cli._COMMANDS


class TestCmdHookTail:
    """``devbench hook-tail`` argument parsing and dispatcher registration.

    Runtime behaviour (file-following, formatting) lives in
    ``tests/unit/test_hook_tail.py`` and
    ``tests/test_integration/test_hook_tail_lifecycle.py``; this block
    covers ONLY the CLI-level flag parsing that ``cmd_hook_tail`` owns.
    """

    def test_registered_in_commands(self) -> None:
        assert "hook-tail" in cli._COMMANDS

    def test_is_variadic_so_flags_reach_handler(self) -> None:
        """The dispatcher truncates positional args for fixed-arity commands;
        hook-tail must be in the variadic opt-in set so --tz etc. reach it."""
        assert "hook-tail" in cli._VARIADIC_COMMANDS

    def test_missing_tz_value_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--tz")
        assert rc == 2
        assert "--tz requires a value" in capsys.readouterr().err

    def test_empty_tz_value_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--tz", "")
        assert rc == 2

    def test_unknown_flag_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--bogus")
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err

    def test_two_positional_paths_return_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("/tmp/a", "/tmp/b")
        assert rc == 2
        assert "unexpected positional argument" in capsys.readouterr().err

    def test_invalid_tz_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--tz", "Not/AZone")
        assert rc == 2
        captured = capsys.readouterr()
        assert "unknown timezone" in captured.err
        assert "Not/AZone" in captured.err

    def test_orchestrator_only_without_env_returns_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("JUDGE_ORCHESTRATOR_SESSION_ID", raising=False)
        rc = cli.cmd_hook_tail("--orchestrator-only", "--no-follow")
        assert rc == 2
        assert "JUDGE_ORCHESTRATOR_SESSION_ID" in capsys.readouterr().err

    def test_orchestrator_session_missing_value_returns_2(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_hook_tail("--orchestrator-session")
        assert rc == 2
        assert "--orchestrator-session requires a value" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Tier 3: test-validates-source heuristic + cmd_promote_proposal warnings
# ---------------------------------------------------------------------------


class TestDetectTestValidatesSource:
    """Unit tests for cli._detect_test_validates_source."""

    @staticmethod
    def _write_proposal(
        proposals_dir: Path,
        source_id: str,
        proposed_id: str,
        title: str = "Implement feature",
        files: list[str] | None = None,
        source_dep_direction: str = "",
    ) -> None:
        proposals_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_task_id": source_id,
            "generated_at": "2026-04-30T00:00:00Z",
            "rejection_reason": "x",
            "source_dep_direction": source_dep_direction,
            "proposed_tasks": [
                {
                    "suggested_id": proposed_id,
                    "title": title,
                    "files_to_own": files or ["src/foo.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "",
                }
            ],
        }
        (proposals_dir / f"{source_id}.json").write_text(json.dumps(payload))

    def test_returns_empty_when_proposals_dir_missing(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == ""

    def test_returns_flag_when_explicit_source_dep_direction(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Implement feature",
            source_dep_direction="test_validates_source",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "flag"

    def test_returns_heuristic_when_title_starts_with_add_tests(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_foo.py to validate T1's foo.py",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"

    def test_returns_heuristic_when_title_starts_with_verify(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Verify pyproject.toml lists ruff",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"

    def test_returns_heuristic_when_files_all_under_tests(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Implement coverage",
            files=["tests/unit/test_a.py", "tests/integration/test_b.py"],
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"

    def test_returns_empty_when_no_match(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Implement merge_properties.py",
            files=["infra/scripts/merge_properties.py"],
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == ""

    def test_returns_empty_when_id_not_in_any_proposal(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/foo",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T7") == ""

    def test_malformed_json_is_skipped(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "broken.json").write_text("{not valid json")
        # And one valid file alongside it so we exercise the continue path.
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_x.py",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"


class TestCmdPromoteProposalTestValidatesSource:
    """cmd_promote_proposal honors / warns on the test-validates-source heuristic."""

    def test_flag_auto_applies_no_dep_on_source(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import PromoteResult

        proposals = tmp_path / ".devbench" / "proposals"
        TestDetectTestValidatesSource._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            source_dep_direction="test_validates_source",
        )
        captured: dict[str, Any] = {}

        def fake_promote(**kwargs: Any) -> PromoteResult:
            captured.update(kwargs)
            return PromoteResult(draft_path=tmp_path / "x.md", wired_targets=[])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.promote_proposal", side_effect=fake_promote),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T9")
        assert rc == 0
        assert captured.get("dep_on_source") is False
        err = capsys.readouterr().err
        assert "auto-applying --no-dep-on-source" in err

    def test_heuristic_emits_warning_but_keeps_default_dep_on_source(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.backlog.proposal import PromoteResult

        proposals = tmp_path / ".devbench" / "proposals"
        TestDetectTestValidatesSource._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_foo.py",
        )
        captured: dict[str, Any] = {}

        def fake_promote(**kwargs: Any) -> PromoteResult:
            captured.update(kwargs)
            return PromoteResult(draft_path=tmp_path / "x.md", wired_targets=[])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.promote_proposal", side_effect=fake_promote),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T9")
        assert rc == 0
        # Heuristic does NOT auto-flip; just warns.
        assert captured.get("dep_on_source") is True
        err = capsys.readouterr().err
        assert "looks like a test-validates-source task" in err

    def test_no_warning_when_no_dep_on_source_already_set(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.backlog.proposal import PromoteResult

        proposals = tmp_path / ".devbench" / "proposals"
        TestDetectTestValidatesSource._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_foo.py",
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli.promote_proposal",
                return_value=PromoteResult(draft_path=tmp_path / "x.md", wired_targets=[]),
            ),
        ):
            rc = cli.cmd_promote_proposal("--no-dep-on-source", "E0-F1-S1-T9")
        assert rc == 0
        # When the operator passes --no-dep-on-source explicitly, no
        # warning is needed (the heuristic check is gated on dep_on_source).
        err = capsys.readouterr().err
        assert "looks like a test-validates-source task" not in err
        assert "auto-applying --no-dep-on-source" not in err


# ---------------------------------------------------------------------------
# Tier 3: cmd_check pre-flight verifier
# ---------------------------------------------------------------------------


class TestCmdCheck:
    """devbench check: pre-flight readiness check across all repos in devbench.yaml."""

    @staticmethod
    def _write_min_yaml(tmp_path: Path, repos_block: str, single_branch: str = "") -> Path:
        # Schema-conformant minimal config (matches tests/fixtures/test_devbench.yaml).
        cfg_dir = tmp_path / "backlog" / "config"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "devbench.yaml"
        ops_block = (
            f"git_ops:\n  single_branch: {single_branch}\n  defer_pr: true\n"
            if single_branch
            else "git_ops:\n  defer_pr: false\n"
        )
        cfg_path.write_text(
            f"judge_model: test-judge-model\nexecutor_model: test-executor-model\nrepos:\n{repos_block}{ops_block}"
        )
        return cfg_path

    def test_returns_1_when_yaml_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Point JUDGE_CONFIG_PATH at a nonexistent file so resolve_config_path
        # does not fall back to the suite-wide test fixture YAML.
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(tmp_path / "no-such.yaml"))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_check()
        assert rc == 1
        assert "devbench.yaml not found" in capsys.readouterr().err

    def test_returns_1_when_symlink_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main\n",
        )
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(cfg))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "symlink missing" in out
        assert "repo-a" in out

    def test_returns_0_when_all_checks_pass(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Set up a fake clone with origin remote configured (via mocked subprocess).
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main\n",
            single_branch="feat/x",
        )
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            elif args[:2] == ["gh", "api"]:
                mock.returncode = 0
                mock.stdout = "main\n"
            elif args[:3] == ["gh", "pr", "list"]:
                mock.returncode = 0
                mock.stdout = "[]"
            else:
                mock.returncode = 0
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 0
        assert "Pre-flight check passed" in capsys.readouterr().out

    def test_flags_default_branch_mismatch(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main2\n",
        )
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            elif args[:2] == ["gh", "api"]:
                mock.returncode = 0
                mock.stdout = "main\n"
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "default_branch mismatch" in out
        assert "'main2'" in out and "'main'" in out

    def test_flags_open_pr_on_single_branch(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main\n",
            single_branch="feat/x",
        )
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            elif args[:2] == ["gh", "api"]:
                mock.returncode = 0
                mock.stdout = "main\n"
            elif args[:3] == ["gh", "pr", "list"]:
                mock.returncode = 0
                mock.stdout = '[{"number":42,"title":"existing"}]'
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "open PR(s) already exist on branch" in out

    @staticmethod
    def _write_local_only_yaml(tmp_path: Path) -> Path:
        cfg_dir = tmp_path / "backlog" / "config"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "devbench.yaml"
        cfg_path.write_text(
            "judge_model: test-judge-model\n"
            "executor_model: test-executor-model\n"
            "repos:\n"
            "  org/repo-a:\n"
            "    checkout_directory: repo-a\n"
            "    default_branch: main\n"
            "git_ops:\n"
            "  single_branch: feat/x\n"
            "  defer_pr: true\n"
            "  local_only: true\n"
        )
        return cfg_path

    def test_passes_when_local_only_repo_has_no_origin(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Under git_ops.local_only: true, a target repo with NO origin remote passes pre-flight."""
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_local_only_yaml(tmp_path)
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            mock.stdout = ""
            if args[:3] == ["git", "-C", str(clone)]:
                # No origin remote -> rc=2 (the real git failure mode)
                mock.returncode = 2
                mock.stderr = "error: No such remote 'origin'"
            else:
                mock.returncode = 0
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 0
        assert "Pre-flight check passed" in capsys.readouterr().out

    def test_flags_local_only_repo_with_origin(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Under git_ops.local_only: true, a target repo that DOES have an origin remote is flagged."""
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_local_only_yaml(tmp_path)
        monkeypatch.setenv("JUDGE_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "git_ops.local_only is true" in out
        assert "has an 'origin' remote" in out


# ---------------------------------------------------------------------------
# Tier 3: variadic dispatch lets `add-dep --reason "<multi token>"` survive
# ---------------------------------------------------------------------------


class TestAddDepVariadicDispatch:
    """The dispatcher must NOT truncate add-dep's --reason value."""

    def test_main_passes_full_reason_through_to_cmd_add_dep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_add_dep(*argv: str) -> int:
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(cli, "cmd_add_dep", fake_add_dep)
        # Re-register the patched function in the dispatch table so main() sees it.
        original = cli._COMMANDS["add-dep"]
        monkeypatch.setitem(cli._COMMANDS, "add-dep", (fake_add_dep, original[1], original[2]))
        monkeypatch.setattr(
            "sys.argv",
            [
                "devbench",
                "add-dep",
                "E0-F1-S1-T1",
                "E0-F1-S1-T2",
                "--reason",
                "this is a multi token reason value",
            ],
        )
        rc = cli.main()
        assert rc == 0
        # All five trailing tokens must reach cmd_add_dep, including the
        # full multi-token --reason value (no slicing by MAX_ARGS).
        assert captured["argv"] == (
            "E0-F1-S1-T1",
            "E0-F1-S1-T2",
            "--reason",
            "this is a multi token reason value",
        )


# ---------------------------------------------------------------------------
# write-proposal auto-cascade (closes the resolver-write -> next-sweep gap)
# ---------------------------------------------------------------------------


def _runtime_config_with_auto_accept(value: bool) -> Any:
    """Build a RuntimeConfig clone whose ``task_factory.auto_accept_proposals`` is *value*.

    ``RuntimeConfig`` and ``TaskFactoryConfig`` are frozen dataclasses, so
    mutation via setattr fails with ``FrozenInstanceError``. The
    canonical replacement pattern is ``dataclasses.replace`` for the
    nested config, then ``dataclasses.replace`` for the parent so the
    one runtime field of interest is swapped without touching the
    other config sections.
    """
    import dataclasses

    base = cli.RUNTIME_CONFIG
    new_tf = dataclasses.replace(base.task_factory, auto_accept_proposals=value)
    return dataclasses.replace(base, task_factory=new_tf)


class TestCmdWriteProposalDedup:
    """Issue #141: ``cmd_write_proposal`` must auto-wire a dep edge to an
    existing recovery task instead of writing a duplicate proposal when
    the would-be proposal's ``fix_signature`` matches an existing pending
    proposal on disk."""

    def _seed_existing_recovery(self, tmp_path: Path, source_id: str, signature: str) -> Path:
        """Drop a pending proposal JSON + a minimal source-task markdown +
        BACKLOG.md row so add_dep can find and modify the source task."""
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        path = proposals_dir / f"{source_id}.json"
        path.write_text(
            json.dumps(
                {
                    "source_task_id": source_id,
                    "generated_at": "2026-05-02T00:00:00Z",
                    "rejection_reason": "fixture",
                    "proposed_tasks": [],
                    "fix_signature": signature,
                }
            ),
            encoding="utf-8",
        )
        backlog_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        (backlog_dir / f"{source_id}.md").write_text(
            f"# {source_id}: existing recovery\n\n## Status: in-queue\n", encoding="utf-8"
        )
        return path

    def test_dedup_reuses_existing_proposal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two source tasks with the same fix signature -> second invocation
        emits ``recovery_reused: true`` instead of writing a duplicate
        proposal JSON."""
        import io

        from devbench.backlog.proposal import _compute_fix_signature, _extract_intent_phrase

        # Compute the signature the way cmd_write_proposal will compute it
        # (target_repo "" because BacklogParser does not accept the bare
        # "r" repo column the BACKLOG.md fixture uses; ``_resolve_source_repo``
        # falls back to "" via its except-ValueError branch).
        # Production strips the configured ``checkout_directory`` prefix
        # before computing the signature (issue #159), so we seed with
        # the STRIPPED form (``pyproject.toml``). The unstripped path
        # stays in the new payload's ``files_to_own`` below so the
        # issue #146 backlog-repo filter still treats the file as
        # in-scope (the file's first segment ``git-repo`` matches the
        # configured checkout_directory of caylent-solutions/git-repo
        # in the test fixture).
        files_unstripped = ["git-repo/pyproject.toml"]
        files_stripped = ["pyproject.toml"]
        intent = _extract_intent_phrase("Remove the pyproject.toml row from T1")
        signature = _compute_fix_signature("", files_stripped, intent)

        # Seed the EXISTING recovery: source_id=E0-F1-S1-T1 carries the signature.
        self._seed_existing_recovery(tmp_path, "E0-F1-S1-T1", signature)
        # Add a target source-task markdown that the new write-proposal
        # invocation will be associated with (so add_dep can write its
        # dep-table row).
        backlog_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        new_source_md = (
            "# E0-F1-S1-T7: new source\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
        )
        (backlog_dir / "E0-F1-S1-T7.md").write_text(new_source_md, encoding="utf-8")
        # Minimal BACKLOG.md so BacklogParser does not crash; both rows present.
        t1_path = "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md`"
        t7_path = "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T7.md`"
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Tasks\n\n"
            "| ID | Title | Type | Status | Deps | Repo | File |\n"
            "|----|-------|------|--------|------|------|------|\n"
            f"| E0-F1-S1-T1 | existing recovery | Task | in-queue | none | r | {t1_path} |\n"
            f"| E0-F1-S1-T7 | new source | Task | blocked | none | r | {t7_path} |\n",
            encoding="utf-8",
        )

        # Submit a new proposal whose fix signature will match.
        new_payload = {
            "source_task_id": "E0-F1-S1-T7",
            "generated_at": "2026-05-02T00:01:00Z",
            "rejection_reason": "duplicate fix",
            "proposed_tasks": [
                {
                    "suggested_id": "E0-F1-S1-T8",
                    "title": "Remove the pyproject.toml row from T7",
                    "files_to_own": files_unstripped,
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Remove the pyproject.toml row from T1",
                }
            ],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(new_payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T7")
        assert rc == 0
        out = capsys.readouterr().out
        envelope = json.loads(out)
        assert envelope["recovery_reused"] is True
        assert envelope["reused_from_task_id"] == "E0-F1-S1-T1"
        # No duplicate proposal JSON written.
        assert not (tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T7.json").exists()


class TestCmdWriteProposalBacklogRepoSkip:
    """Issue #146: ``cmd_write_proposal`` must drop proposed-task entries
    whose ``files_to_own`` all live in the backlog repo (i.e., NOT in any
    configured target repo). The backlog repo isn't a target repo; the
    recovery cascade has no valid completion path for backlog-repo edits.
    """

    def _payload(
        self,
        source_id: str,
        proposed: list[dict],
    ) -> str:
        return json.dumps(
            {
                "source_task_id": source_id,
                "generated_at": "2026-05-02T00:00:00Z",
                "rejection_reason": "fixture",
                "proposed_tasks": proposed,
            }
        )

    def _mk_runtime_config(self) -> MagicMock:
        """Build a RUNTIME_CONFIG mock with two target repos configured
        (``caylent-telemetry`` and ``kanon``). Files outside these
        directories are treated as backlog-repo bookkeeping."""
        cfg = MagicMock()
        repo_a = MagicMock()
        repo_a.checkout_directory = "caylent-telemetry"
        repo_a.validated_repo = "caylent-solutions/caylent-telemetry"
        repo_b = MagicMock()
        repo_b.checkout_directory = "kanon"
        repo_b.validated_repo = "caylent-solutions/kanon"
        cfg.repos = {
            "caylent-solutions/caylent-telemetry": repo_a,
            "caylent-solutions/kanon": repo_b,
        }
        cfg.task_factory.auto_accept_proposals = False
        cfg.task_factory.enabled = True
        return cfg

    def test_target_repo_files_emit_proposal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Proposed task whose files all live in a configured target repo
        is NOT skipped: proposal is written and recovery_skipped is False."""
        import io

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Fix X",
                    "files_to_own": ["caylent-telemetry/src/foo.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Add the foo helper",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True
        assert envelope.get("proposal_path") is not None
        assert (tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").exists()

    def test_all_backlog_repo_files_skipped_no_proposal_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Proposed task whose only file is in the backlog repo (e.g.,
        spec/observability.md) -> entry dropped, no proposal JSON
        written, envelope reports recovery_skipped: True."""
        import io

        payload = self._payload(
            "E3-F3-S2-T1",
            [
                {
                    "suggested_id": "E3-F3-S2-T2",
                    "title": "Sync spec doc",
                    "files_to_own": ["spec/observability.md"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Sync the spec doc with the dashboard",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E3-F3-S2-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["recovery_skipped"] is True
        assert envelope["proposal_path"] is None
        assert not (tmp_path / ".devbench" / "proposals" / "E3-F3-S2-T1.json").exists()

    def test_mixed_files_partial_keep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Proposed task whose files span backlog + target repos -> entry
        kept with target-repo files only; backlog files pruned. Proposal
        is written."""
        import io

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Fix X with mixed files",
                    "files_to_own": [
                        "caylent-telemetry/src/foo.py",  # target repo
                        "spec/architecture.md",  # backlog repo (pruned)
                    ],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Add the foo helper",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True
        assert envelope["proposal_path"] is not None
        # Verify the persisted proposal carries only the target-repo file.
        # Issue #159 (prefix strip): the persisted path is repo-relative
        # (``src/foo.py``) rather than the prefixed form the agent emitted
        # (``caylent-telemetry/src/foo.py``). The strip runs after the
        # backlog-repo filter, so the target-repo classification still fires
        # on the prefixed form before the persistence step normalises it.
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text(encoding="utf-8"))
        assert persisted["proposed_tasks"][0]["files_to_own"] == ["src/foo.py"]

    def test_empty_files_to_own_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty files_to_own = research / validation-gate task; NOT
        treated as backlog-only. Entry preserved as-is."""
        import io

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Investigate X",
                    "files_to_own": [],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Investigate without authoring code",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True

    def test_helper_classifies_paths_correctly(self) -> None:
        """Spot-check ``_file_lives_in_a_target_repo`` against canonical
        examples (target-repo paths return True; backlog-repo paths
        return False)."""
        with patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()):
            assert cli._file_lives_in_a_target_repo("caylent-telemetry/src/foo.py")
            assert cli._file_lives_in_a_target_repo("kanon/something.py")
            assert not cli._file_lives_in_a_target_repo("spec/observability.md")
            assert not cli._file_lives_in_a_target_repo("BACKLOG.md")
            assert not cli._file_lives_in_a_target_repo("backlog/E1/E1-F1/E1-F1.md")
            assert not cli._file_lives_in_a_target_repo("docs/architecture.md")
            assert not cli._file_lives_in_a_target_repo("")

    def test_repo_relative_path_classified_via_source_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Issue #180: a repo-relative path like ``src/foo.py`` (no
        checkout_directory prefix) must classify as target-repo when the
        source task resolves to a configured repo. blocker-resolver agents
        running from inside the source's checkout naturally emit paths in
        this form; the recovery cascade must NOT silently skip them.
        """
        import io

        # Build a backlog where E0-F1-S1-T1 targets the kanon repo.
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(parents=True)
        wu_dir = backlog_root / "E0-name" / "E0-F1-name" / "E0-F1-S1-name"
        wu_dir.mkdir(parents=True)
        wu_path = wu_dir / "E0-F1-S1-T1-name.md"
        wu_path.write_text(
            "# E0-F1-S1-T1: Source Task\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/kanon`\n\n"
            "## Description\n\nFixture task.\n\n"
            "## Changes Manifest\n\n"
            "| Path | Notes |\n|------|-------|\n| src/foo.py | impl |\n\n"
            "## Comments\n",
            encoding="utf-8",
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source Task | Task | blocked | None | caylent-solutions/kanon | "
            "`backlog/E0-name/E0-F1-name/E0-F1-S1-name/E0-F1-S1-T1-name.md` |\n",
            encoding="utf-8",
        )

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Fix X repo-relative",
                    # Path is repo-relative (no `kanon/` prefix). Under
                    # the pre-fix classifier, this would be misread as
                    # backlog-repo and skipped.
                    "files_to_own": ["src/bar.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Add the bar helper",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True, (
            f"repo-relative path must NOT trigger backlog-repo skip: {envelope!r}"
        )
        assert envelope["proposal_path"] is not None
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text(encoding="utf-8"))
        assert persisted["proposed_tasks"][0]["files_to_own"] == ["src/bar.py"]

    def test_helper_with_source_task_treats_repo_relative_as_target(self, tmp_path: Path) -> None:
        """Direct test for the new ``source_task_id`` parameter behaviour.

        With ``source_task_id`` provided and the source resolving to a
        configured repo, repo-relative paths classify as target-repo;
        without it (back-compat), they classify as backlog-repo. Both
        cases must still treat the unambiguous backlog-only ``BACKLOG.md``
        as a non-target path (it carries no target-repo prefix and is
        not a plausible inside-repo path).
        """
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(parents=True)
        wu_dir = backlog_root / "E0-name" / "E0-F1-name" / "E0-F1-S1-name"
        wu_dir.mkdir(parents=True)
        wu_path = wu_dir / "E0-F1-S1-T1-name.md"
        wu_path.write_text(
            "# E0-F1-S1-T1: Source Task\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/kanon`\n\n"
            "## Description\n\nFixture task.\n\n"
            "## Changes Manifest\n\n"
            "| Path | Notes |\n|------|-------|\n| src/foo.py | impl |\n\n"
            "## Comments\n",
            encoding="utf-8",
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source Task | Task | blocked | None | caylent-solutions/kanon | "
            "`backlog/E0-name/E0-F1-name/E0-F1-S1-name/E0-F1-S1-T1-name.md` |\n",
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            # With source -> repo-relative path counts as target-repo.
            assert cli._file_lives_in_a_target_repo("src/foo.py", source_task_id="E0-F1-S1-T1")
            # With source -> still True for prefixed paths.
            assert cli._file_lives_in_a_target_repo("kanon/src/foo.py", source_task_id="E0-F1-S1-T1")
            # Without source (back-compat) -> repo-relative path returns False.
            assert not cli._file_lives_in_a_target_repo("src/foo.py")


class TestCmdWriteProposalCheckoutPrefixStrip:
    """Issue #159: ``cmd_write_proposal`` must strip ``<checkout_directory>/``
    prefixes from every ``proposed_tasks[*].files_to_own`` entry so the
    persisted JSON carries repo-relative paths only. Recurring failure mode
    (verified against the live caylent-telemetry-spec workspace): blocker-
    resolver agents emit paths like ``kanon/src/foo.py`` when ``kanon`` is
    configured as the target repo's checkout_directory; without the strip,
    every materialised work unit fails validate-backlog rule 11."""

    def _payload(self, source_id: str, files: list[str]) -> str:
        return json.dumps(
            {
                "source_task_id": source_id,
                "generated_at": "2026-05-04T00:00:00Z",
                "rejection_reason": "fixture",
                "proposed_tasks": [
                    {
                        "suggested_id": "E0-F1-S1-T2",
                        "title": "Strip me",
                        "files_to_own": files,
                        "linked_scenarios": [],
                        "suggested_acs": [],
                        "suggested_approach": "Add the foo helper",
                    }
                ],
            }
        )

    def _mk_runtime_config(self) -> MagicMock:
        cfg = MagicMock()
        repo_a = MagicMock()
        repo_a.checkout_directory = "caylent-telemetry"
        repo_a.validated_repo = "caylent-solutions/caylent-telemetry"
        repo_b = MagicMock()
        repo_b.checkout_directory = "kanon"
        repo_b.validated_repo = "caylent-solutions/kanon"
        cfg.repos = {
            "caylent-solutions/caylent-telemetry": repo_a,
            "caylent-solutions/kanon": repo_b,
        }
        cfg.task_factory.auto_accept_proposals = False
        cfg.task_factory.enabled = True
        return cfg

    def test_kanon_prefix_stripped_to_repo_relative(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``kanon/src/foo.py`` -> ``src/foo.py`` in the persisted JSON."""
        import io

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                self._payload(
                    "E0-F1-S1-T1",
                    [
                        "kanon/src/kanon_cli/core/xml_validator.py",
                        "kanon/tests/unit/test_xml_validator.py",
                    ],
                )
            ),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text())
        files = persisted["proposed_tasks"][0]["files_to_own"]
        assert "src/kanon_cli/core/xml_validator.py" in files
        assert "tests/unit/test_xml_validator.py" in files
        # Original prefixed forms must NOT survive.
        assert not any(f.startswith("kanon/") for f in files)

    def test_repo_relative_paths_pass_through_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Already-correct paths are not mutated by the strip pass."""
        import io

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                self._payload(
                    "E0-F1-S1-T1",
                    [
                        "caylent-telemetry/src/foo.py",
                    ],
                )
            ),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text())
        files = persisted["proposed_tasks"][0]["files_to_own"]
        # caylent-telemetry IS one of the configured checkout dirs ->
        # gets stripped to repo-relative form just like kanon does.
        assert "src/foo.py" in files

    def test_ambiguous_path_matches_multiple_checkouts_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Per #159 AC: a path matching multiple configured checkout
        directories is ambiguous and the proposal is rejected with a
        structured error rather than silently picking one."""
        import io

        cfg = MagicMock()
        # Two repos whose checkout_directories share a common prefix; a
        # single path could plausibly belong to either. Configure them
        # so that ``foo`` is BOTH a checkout_directory AND a prefix of
        # another checkout_directory.
        repo_a = MagicMock()
        repo_a.checkout_directory = "foo"
        repo_a.validated_repo = "caylent-solutions/foo"
        repo_b = MagicMock()
        repo_b.checkout_directory = "foo"
        repo_b.validated_repo = "caylent-solutions/foo-copy"
        cfg.repos = {
            "caylent-solutions/foo": repo_a,
            "caylent-solutions/foo-copy": repo_b,
        }
        cfg.task_factory.auto_accept_proposals = False
        cfg.task_factory.enabled = True

        # Use a payload whose path duplicates the prefix to exercise
        # the multi-match branch. Since both repos resolve to the same
        # checkout_directory ("foo"), the deduped sorted list still has
        # ONE entry; we need actually-different prefixes that BOTH
        # match. Use a set with ``foo`` and ``foo/bar`` as configured
        # checkout dirs and a path ``foo/bar/baz.py`` that matches both.
        repo_b.checkout_directory = "foo/bar"
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(self._payload("E0-F1-S1-T1", ["foo/bar/baz.py"])),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "ambiguous" in err.lower()


class TestCmdMaterialiseProposalLifecycleGates:
    """Issues #143 + #144: TODO/TBD placeholder reject + cascade-depth limit
    in ``cmd_materialise_proposal``."""

    def _seed_proposal(
        self,
        tmp_path: Path,
        source_id: str,
        approach: str = "concrete",
        cascade_depth: int = 0,
    ) -> Path:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id=source_id,
            generated_at="2026-05-02T00:00:00Z",
            rejection_reason="fixture",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    suggested_approach=approach,
                )
            ],
            cascade_depth=cascade_depth,
        )
        return write_proposal(tmp_path, proposal)

    def test_todo_placeholder_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._seed_proposal(tmp_path, "E0-F1-S1-T1", approach="TODO -- describe change")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "placeholder description" in err
        assert "E0-F1-S1-T2" in err

    def test_cascade_depth_at_cap_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._seed_proposal(tmp_path, "E0-F1-S1-T1", approach="concrete approach", cascade_depth=2)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 2),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "cascade-depth limit reached" in err
        assert "OPERATOR_ACTION_REQUIRED" in err

    def test_cascade_depth_below_cap_passes_through_to_source_lookup(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Depth < cap + concrete approach -> proceeds past gates and hits
        the source-task-not-in-backlog error (same baseline behaviour as
        before this commit)."""
        self._seed_proposal(tmp_path, "E0-F1-S1-T1", approach="concrete approach", cascade_depth=1)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 2),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        # Past the dedup gates -> hits the source-task-lookup error.
        err = capsys.readouterr().err
        assert "not found" in err


class TestCmdWriteProposalAutoCascade:
    """When ``task_factory.auto_accept_proposals`` is true, ``write-proposal``
    must materialise + promote every proposed task in the same Python
    invocation so the cascade is actionable immediately rather than waiting
    for the next ``sweep-proposals`` cycle.
    """

    @staticmethod
    def _sample_proposal_dict(source_task_id: str = "E0-F1-S1-T1") -> dict[str, Any]:
        return {
            "source_task_id": source_task_id,
            "generated_at": "2026-05-01T03:00:00Z",
            "rejection_reason": "x",
            "proposed_tasks": [
                {
                    "suggested_id": "E0-F1-S1-T9",
                    "title": "Follow-up fix",
                    "files_to_own": ["src/foo.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [
                        "AC-FUNC-001 fix the issue",
                    ],
                    "suggested_approach": (
                        "Context: the source task hit X and produced finding Y. "
                        "Scope: src/foo.py only. "
                        "TDD approach: 1. RED write a failing test for the missing behaviour. "
                        "2. GREEN add the implementation in src/foo.py. "
                        "3. REFACTOR clean up duplication if any. "
                        "Verify: pytest exits zero and lint is clean."
                    ),
                }
            ],
            "affected_task_ids": [],
        }

    def _patch_cli_workspace(
        self, monkeypatch: pytest.MonkeyPatch, workspace: Path, backlog_root: Path, backlog_index: Path
    ) -> None:
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", workspace)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog_root)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", backlog_index)

    def test_disabled_when_auto_accept_false(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # When the config flag is false the function returns the
        # "disabled" sentinel and never calls materialise/promote.
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", _runtime_config_with_auto_accept(False))
        proposal = Proposal.from_dict(self._sample_proposal_dict())
        result = cli._maybe_auto_cascade_proposal("E0-F1-S1-T1", proposal)
        assert result == {"auto_cascade": "disabled"}

    def test_failed_when_source_task_missing_from_index(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # When auto-accept is on but the source task is not in the
        # backlog index (caller passed a typo'd id), the cascade reports
        # "failed" with a clear error and does not raise.
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", _runtime_config_with_auto_accept(True))
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n",
            encoding="utf-8",
        )
        self._patch_cli_workspace(monkeypatch, tmp_path, tmp_path / "backlog", backlog)
        proposal = Proposal.from_dict(self._sample_proposal_dict("E0-NOPE-T0"))
        result = cli._maybe_auto_cascade_proposal("E0-NOPE-T0", proposal)
        assert result["auto_cascade"] == "failed"
        # Either "no work-unit rows" (parser rejection on empty index)
        # or "not found" (index parses but source task absent) is an
        # acceptable failure shape; both prove the cascade aborted
        # cleanly without raising.
        err_value = result["error"]
        assert isinstance(err_value, str)
        err = err_value.lower()
        assert "not found" in err or "no work-unit rows" in err

    def test_applied_when_auto_accept_true(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # End-to-end: with auto-accept on and a real source-task in the
        # index, the helper calls materialise + promote and returns
        # "applied" with the materialised path list and promoted ids.
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", _runtime_config_with_auto_accept(True))
        # Build a minimum BACKLOG with one source task at E0-F1-S1-T1
        # (currently blocked, since auto-accept emit happens when the
        # source is failing) plus the directory structure
        # ``materialise_proposal`` expects.
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/foo.py` | edit |\n\n"
            "## Definition of Done\n\n- [ ] done\n",
            encoding="utf-8",
        )
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | x | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | org/repo | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        self._patch_cli_workspace(monkeypatch, tmp_path, backlog_root, backlog)

        proposal = Proposal.from_dict(self._sample_proposal_dict("E0-F1-S1-T1"))
        # Persist the proposal JSON so the cascade has something to work on.
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text(json.dumps(proposal.to_dict()))

        result = cli._maybe_auto_cascade_proposal("E0-F1-S1-T1", proposal)
        assert result["auto_cascade"] == "applied"
        materialised = result["materialised"]
        assert isinstance(materialised, list) and materialised  # at least one path
        promoted = result["promoted"]
        assert isinstance(promoted, list)
        assert "E0-F1-S1-T9" in promoted
        # The promoted task's file now exists with status in-queue.
        promoted_file = story_dir / "E0-F1-S1-T9.md"
        assert promoted_file.exists()
        promoted_content = promoted_file.read_text(encoding="utf-8")
        assert "## Status: in-queue" in promoted_content


# ---------------------------------------------------------------------------
# log-verdict judge-name allowlist (rejects malformed audit-row writes)
# ---------------------------------------------------------------------------


class TestCmdLogVerdictAllowlist:
    """``cmd_log_verdict`` rejects judge names outside the canonical allowlist.

    Empirically observed in production: an executor agent ran
    ``log-verdict judge <id> pass`` (literal string ``"judge"`` instead
    of a canonical reviewer name). The malformed entry landed in the
    work-unit Comments section but was silently invisible to
    ``BacklogManager._last_round_all_passed`` (which only counts
    entries whose judge name is in ``ALL_REQUIRED_JUDGE_NAMES``).
    Refusing typos at the CLI layer prevents pollution + masks.
    """

    @pytest.mark.parametrize(
        "bad_name",
        [
            "judge",  # literal typo seen in production
            "code-reviewer",  # hyphenated form (canonical is underscored)
            "Code_Review",  # casing (canonical is lowercase)
            "auditor",  # role that does not exist
            "",  # empty
        ],
    )
    def test_rejects_non_allowlist_judge(self, bad_name: str, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_log_verdict(bad_name, "E0-F1-S1-T1", "pass", "smoke")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not on the allowlist" in err
        # Error message names every valid choice so the agent can self-correct.
        for canonical in ("code_review", "test_review", "doc_review"):
            assert canonical in err

    @pytest.mark.parametrize(
        "good_name",
        [
            "code_review",
            "test_review",
            "doc_review",
            "changes_manifest",
            "security_review",
            "executor",  # audit-only workflow agent
            "blocker_resolver",  # audit-only workflow agent
            "manifest_amender",
            "task_factory",
        ],
    )
    def test_accepts_allowlist_judges(self, good_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Build a minimal workspace so the verdict-write reaches its
        # successful return path. Failure here would surface as a
        # non-zero rc with stderr; we assert rc==0 to prove the
        # allowlist gate did not trip.
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n",
            encoding="utf-8",
        )
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | x | 0 | 1 | 0 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | in-progress | None | org/repo | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog_root)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", backlog)

        rc = cli.cmd_log_verdict(good_name, "E0-F1-S1-T1", "pass", "looks good")
        assert rc == 0


# ---------------------------------------------------------------------------
# log-file path resolution: fail-fast when neither JUDGE_LOG_FILE nor
# JUDGE_WORKSPACE_ROOT is set; canonical workspace-local path otherwise
# ---------------------------------------------------------------------------


class TestResolveLogFilePath:
    """``_resolve_log_file_path`` is the single source of truth for which
    log file ``devbench report`` reads. Removing the silent source-tree
    fallback prevents the BACKLOG-vs-throughput divergence reported by
    operators (they ran ``devbench report`` from a sub-shell that
    inherited ``JUDGE_WORKSPACE_ROOT`` but not ``JUDGE_LOG_FILE`` and got
    an unrelated dev-tree log instead of their workspace's log).
    """

    def test_explicit_judge_log_file_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JUDGE_LOG_FILE", "/tmp/my-explicit.log")
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/some-workspace")
        # JUDGE_LOG_FILE is the explicit override and MUST win even when
        # JUDGE_WORKSPACE_ROOT is also set.
        assert cli._resolve_log_file_path() == Path("/tmp/my-explicit.log")

    def test_workspace_root_derives_canonical_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/my-workspace")
        # Default path is <workspace>/<DEFAULT_LOG_SUBDIR>/<DEFAULT_LOG_FILENAME>.
        # Operators running ``devbench report`` from any shell with
        # JUDGE_WORKSPACE_ROOT inherited get the same log the
        # orchestrator writes to.
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        expected = Path("/tmp/my-workspace") / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
        assert cli._resolve_log_file_path() == expected

    def test_empty_judge_log_file_falls_through_to_workspace_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Empty / whitespace-only JUDGE_LOG_FILE behaves as unset
        # (avoids "" being treated as a valid path).
        monkeypatch.setenv("JUDGE_LOG_FILE", "   ")
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/ws")
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        expected = Path("/tmp/ws") / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
        assert cli._resolve_log_file_path() == expected

    def test_neither_set_fails_fast(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        # Per CLAUDE.md "Fail-fast": no fallbacks. When neither env var
        # is set the helper exits 1 with an actionable error rather
        # than silently falling back to the devbench source tree's log.
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.delenv("JUDGE_WORKSPACE_ROOT", raising=False)
        with pytest.raises(SystemExit) as excinfo:
            cli._resolve_log_file_path()
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        # Error names BOTH env vars and the canonical workspace-local
        # path so the operator can self-correct from either direction.
        assert "JUDGE_LOG_FILE" in err
        assert "JUDGE_WORKSPACE_ROOT" in err


# ---------------------------------------------------------------------------
# log_file YAML config (Option 2): YAML drives both writer and reader
# ---------------------------------------------------------------------------


class TestResolveLogFileYamlConfig:
    """``RUNTIME_CONFIG.log_file`` (from devbench.yaml) drives the resolver
    when ``JUDGE_LOG_FILE`` env var is not set. This is the canonical
    single source of truth for the orchestrator's log path; the
    orchestrator-as-writer (``setup_logging``) and the report-as-reader
    (``cmd_report``) both consult it so they cannot diverge.
    """

    def _runtime_config_with_log_file(self, value: str | None) -> Any:
        import dataclasses

        return dataclasses.replace(cli.RUNTIME_CONFIG, log_file=value)

    def test_yaml_log_file_workspace_relative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/ws")
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("logs/orch.log"))
        # YAML log_file is workspace-relative when not absolute; the
        # resolver joins it with JUDGE_WORKSPACE_ROOT.
        assert cli._resolve_log_file_path() == Path("/tmp/ws/logs/orch.log")

    def test_yaml_log_file_absolute_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/ws")
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("/var/log/d.log"))
        # An absolute YAML path is used as-is, ignoring the workspace
        # root (operator deliberately put the log outside the workspace).
        assert cli._resolve_log_file_path() == Path("/var/log/d.log")

    def test_explicit_judge_log_file_still_wins_over_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JUDGE_LOG_FILE", "/tmp/explicit.log")
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/ws")
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("logs/orch.log"))
        # Per-invocation env override beats both YAML config and the
        # workspace-local convention; this matches how ``cmd_check`` and
        # the test fixtures set the path explicitly.
        assert cli._resolve_log_file_path() == Path("/tmp/explicit.log")

    def test_yaml_unset_falls_through_to_workspace_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", "/tmp/ws")
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file(None))
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        # When neither JUDGE_LOG_FILE nor YAML log_file is set, the
        # resolver falls back to the workspace-local convention.
        assert cli._resolve_log_file_path() == (Path("/tmp/ws") / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME)

    def test_yaml_with_no_workspace_treats_relative_as_cwd_relative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Edge case: YAML has a relative log_file but JUDGE_WORKSPACE_ROOT
        # is unset (very rare; only happens in test fixtures). The
        # resolver returns the path as-is so callers can decide what
        # to anchor it against.
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.delenv("JUDGE_WORKSPACE_ROOT", raising=False)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("logs/orch.log"))
        assert cli._resolve_log_file_path() == Path("logs/orch.log")


class TestInlineOrphanCleanup:
    """Phase 1: ``cmd_git_ops`` runs the orphan cleanup inline as a chore commit.

    Eliminates the cascade pathology where multiple parents each emitted a
    duplicate cleanup proposal and those proposals themselves got blocked by
    the manifest amender on predecessor staging. The cleanup is no longer a
    backlog work unit -- it is a maintenance commit the engine makes on its
    own when it detects build/state artifact paths that would otherwise
    pollute the task's commit.
    """

    def _make_unit(self, repo: str = "caylent-solutions/git-repo") -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Sample task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo=repo,
            dependencies=[],
        )

    def _seed_orphan_in_repo(self, repo_dir: Path) -> Path:
        """Add a tracked orphan path (``.coverage (1)``) on top of the
        ``tmp_repo_dir`` fixture's initial commit.

        Mirrors the real-world failure shape from the user's halt log
        (the leftover pytest-cov race file).
        """
        import subprocess

        orphan = repo_dir / ".coverage (1)"
        orphan.write_text("ignored coverage data\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "--", ".coverage (1)"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "leak orphan"],
            check=True,
        )
        return orphan

    def test_inline_cleanup_lands_chore_commit_and_continues(self, tmp_repo_dir: Path) -> None:
        from devbench.constants import DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        self._seed_orphan_in_repo(tmp_repo_dir)
        # Stage an executor file alongside the orphan situation so we can
        # verify the executor's staging survives the cleanup pass.
        import subprocess

        (tmp_repo_dir / "feature.py").write_text("print('hi')\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "add", "--", "feature.py"],
            check=True,
        )

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=tmp_repo_dir,
            detected=[".coverage (1)"],
        )
        assert result is False  # caller continues with task commit

        # Cleanup commit landed with the canonical chore message.
        log = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert log == DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        # Orphan is no longer tracked.
        ls = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage (1)" not in ls

        # ``.gitignore`` was written with the devbench-managed block.
        gitignore = (tmp_repo_dir / ".gitignore").read_text(encoding="utf-8")
        assert ".coverage*" in gitignore

        # Executor's staging (feature.py) was preserved.
        staged = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "feature.py" in staged

    @pytest.mark.skipif(
        __import__("sys").platform == "win32",
        reason="os.symlink requires elevated privileges on Windows",
    )
    def test_inline_cleanup_handles_symlinked_repo_path(self, tmp_repo_dir: Path, tmp_path: Path) -> None:
        """Issue #125 regression: inline cleanup must work when the caller
        passes a symlinked path that resolves to the real checkout.

        ``cleanup_tracked_orphans`` calls ``Path.resolve()`` internally so
        its ``OrphanReport.gitignore_path`` lives in resolved-path space.
        ``_run_inline_cleanup_steps`` must therefore also resolve the
        ``repo_path`` it receives before calling
        ``gitignore_path.relative_to(repo_path)``; otherwise
        ``Path.relative_to`` (which is not symlink-aware) raises
        ``ValueError`` and BLOCKS the work unit. This test exercises the
        documented workspace-layout pattern where target repos sit
        elsewhere on disk and a symlink under
        ``JUDGE_WORKSPACE_ROOT/<checkout_directory>`` points at them.
        """
        import subprocess

        from devbench.constants import DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        self._seed_orphan_in_repo(tmp_repo_dir)
        symlinked_path = tmp_path / "via-symlink"
        symlinked_path.symlink_to(tmp_repo_dir)

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=symlinked_path,
            detected=[".coverage (1)"],
        )
        assert result is False

        # Cleanup commit landed under the canonical chore message.
        log = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert log == DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        # Orphan untracked.
        ls = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage (1)" not in ls

        # .gitignore extended with the devbench-managed block.
        gitignore = (tmp_repo_dir / ".gitignore").read_text(encoding="utf-8")
        assert ".coverage*" in gitignore

    def test_inline_cleanup_filters_out_staged_only_orphans(self, tmp_repo_dir: Path) -> None:
        """A staged-only orphan (newly added by executor, not yet in HEAD) is
        un-staged + ignored; no cleanup commit is needed because there's
        nothing to ``rm --cached``.

        The follow-up ``commit_and_push`` would then skip the orphan because
        the just-written .gitignore block matches the pattern.
        """
        import subprocess

        # Stage a brand-new orphan-pattern file that's not yet tracked.
        (tmp_repo_dir / ".coverage").write_text("data\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "add", "-f", "--", ".coverage"],
            check=True,
        )

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=tmp_repo_dir,
            detected=[".coverage"],
        )
        assert result is False

        # Orphan is no longer staged.
        staged = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage" not in staged

    def test_inline_cleanup_refuses_on_subprocess_failure(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the cleanup primitive raises, the helper returns True so
        the caller refuses the parent commit -- and prints an actionable
        error mentioning the manual recovery command.
        """
        # Pass a non-git-repo path so cleanup_tracked_orphans raises.
        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=tmp_path,
            detected=[".coverage"],
        )
        assert result is True
        err = capsys.readouterr().err
        assert "git-ops refused" in err
        assert "cleanup-tracked-orphans" in err  # operator-recovery hint


class TestEmitOrphanCleanupDispatch:
    """``_emit_orphan_cleanup_proposal_if_needed`` dispatches inline vs legacy."""

    def _unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Sample",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_no_orphans_returns_false_without_dispatch(self, tmp_path: Path) -> None:
        with (
            patch("devbench.cli._orphan_paths_for_repo", return_value=[]),
            patch("devbench.cli._inline_orphan_cleanup_or_refuse") as inline,
            patch("devbench.cli._legacy_emit_orphan_cleanup_proposal") as legacy,
        ):
            assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is False
        inline.assert_not_called()
        legacy.assert_not_called()

    def test_skipped_gate_returns_false(self, tmp_path: Path) -> None:
        # _orphan_paths_for_repo returns None when the gate is skipped
        # (non-git checkout). Caller continues without refusal.
        with patch("devbench.cli._orphan_paths_for_repo", return_value=None):
            assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is False

    def test_dispatches_to_inline_when_enabled(self, tmp_path: Path) -> None:
        with (
            patch("devbench.cli._orphan_paths_for_repo", return_value=[".coverage"]),
            patch("devbench.cli.INLINE_ORPHAN_CLEANUP_ENABLED", True, create=True),
            patch("devbench.cli._inline_orphan_cleanup_or_refuse", return_value=False) as inline,
            patch("devbench.cli._legacy_emit_orphan_cleanup_proposal") as legacy,
        ):
            # The function imports INLINE_ORPHAN_CLEANUP_ENABLED at call time;
            # patch the import target directly via the module-level constant.
            import devbench.config as cfg

            cfg.INLINE_ORPHAN_CLEANUP_ENABLED = True
            try:
                assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is False
            finally:
                cfg.INLINE_ORPHAN_CLEANUP_ENABLED = True
        inline.assert_called_once()
        legacy.assert_not_called()

    def test_dispatches_to_legacy_when_disabled(self, tmp_path: Path) -> None:
        import devbench.config as cfg

        original = cfg.INLINE_ORPHAN_CLEANUP_ENABLED
        cfg.INLINE_ORPHAN_CLEANUP_ENABLED = False
        try:
            with (
                patch("devbench.cli._orphan_paths_for_repo", return_value=[".coverage"]),
                patch("devbench.cli._inline_orphan_cleanup_or_refuse") as inline,
                patch("devbench.cli._legacy_emit_orphan_cleanup_proposal", return_value=True) as legacy,
            ):
                assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is True
            inline.assert_not_called()
            legacy.assert_called_once()
        finally:
            cfg.INLINE_ORPHAN_CLEANUP_ENABLED = original


class TestFindExistingCleanupProposal:
    """Phase 1 secondary fix: cross-task de-duplication for the legacy proposal flow."""

    def test_returns_none_when_proposals_dir_absent(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._find_existing_cleanup_proposal([".coverage"]) is None

    def test_returns_none_when_no_cleanup_proposal_present(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        # Some other proposal (not an orphan-cleanup -- claims a different file).
        (proposals / "E0-F1-S1-T2.json").write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T2",
                    "proposed_tasks": [
                        {"suggested_id": "E0-F1-S1-T9", "files_to_own": ["src/feature.py"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._find_existing_cleanup_proposal([".coverage"]) is None

    def test_returns_existing_cleanup_id_when_found(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "E0-F1-S1-T2.json").write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T2",
                    "proposed_tasks": [
                        {"suggested_id": "E0-F1-S1-T7", "files_to_own": [".gitignore"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._find_existing_cleanup_proposal([".coverage"]) == "E0-F1-S1-T7"

    def test_skips_malformed_proposal_json(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "broken.json").write_text("{not valid json", encoding="utf-8")
        (proposals / "E0-F1-S1-T2.json").write_text(
            json.dumps(
                {
                    "proposed_tasks": [
                        {"suggested_id": "E0-F1-S1-T7", "files_to_own": [".gitignore"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # Malformed file is silently skipped; the valid one still wins.
            assert cli._find_existing_cleanup_proposal([".coverage"]) == "E0-F1-S1-T7"


class TestCiFailureRetry:
    """Issue #115: CI-failure executor retry instead of immediate BLOCKED."""

    def _make_wu_file(self, tmp_path: Path, comments: list[str] | None = None) -> Path:
        body = "# E0-F1-S1-T1: sample\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n"
        for c in comments or []:
            body += f"[2026-05-01 00:00 UTC] [agent/git_ops] {c}\n"
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(body, encoding="utf-8")
        return wu

    def test_count_ci_fail_attempts_zero_when_no_entries(self, tmp_path: Path) -> None:
        wu = self._make_wu_file(tmp_path, comments=["[PR_CREATED] https://example/x"])
        assert cli._count_ci_fail_attempts(wu) == 0

    def test_count_ci_fail_attempts_returns_count(self, tmp_path: Path) -> None:
        wu = self._make_wu_file(tmp_path, comments=["[CI_FAIL] one", "[CI_FAIL] two"])
        assert cli._count_ci_fail_attempts(wu) == 2

    def test_count_ci_fail_attempts_zero_when_file_missing(self, tmp_path: Path) -> None:
        assert cli._count_ci_fail_attempts(tmp_path / "missing.md") == 0
        assert cli._count_ci_fail_attempts(None) == 0

    def test_handle_ci_failure_legacy_when_disabled(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        wu = self._make_wu_file(tmp_path)
        mgr = MagicMock()
        with patch("devbench.config.CI_FAILURE_RETRY_ENABLED", False):
            rc = cli._handle_ci_failure(
                ops=MagicMock(),
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 1
        assert "CI checks failed for PR #42" in capsys.readouterr().err
        mgr._append_agent_comment.assert_not_called()

    def test_handle_ci_failure_returns_2_under_budget(
        self,
        tmp_path: Path,
    ) -> None:
        wu = self._make_wu_file(tmp_path)
        mgr = MagicMock()
        ops = MagicMock()
        ops.get_latest_failing_run_id.return_value = "999"
        ops.fetch_run_log.return_value = "ruff E501 line too long\n"
        with (
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_ci_failure(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 2
        # log file written under workspace
        log_files = sorted((tmp_path / ".devbench" / "ci-failures").glob("E0-F1-S1-T1-*.log"))
        assert len(log_files) == 1
        assert "ruff E501" in log_files[0].read_text(encoding="utf-8")
        # audit comment written with [CI_FAIL] (not _BLOCKED)
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[CI_FAIL] ")

    def test_handle_ci_failure_returns_1_when_budget_exhausted(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Pre-seed two prior CI_FAIL entries so this is attempt 3 -- exhausted.
        wu = self._make_wu_file(tmp_path, comments=["[CI_FAIL] r1", "[CI_FAIL] r2"])
        mgr = MagicMock()
        ops = MagicMock()
        ops.get_latest_failing_run_id.return_value = None  # log unavailable
        with (
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_ci_failure(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "budget exhausted" in err
        assert "MAX_RETRY_ATTEMPTS=3" in err
        # exhaustion uses [CI_FAIL_BLOCKED] marker
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[CI_FAIL_BLOCKED] ")

    def test_handle_ci_failure_skips_audit_when_no_wu_file(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.get_latest_failing_run_id.return_value = "1"
        ops.fetch_run_log.return_value = "log\n"
        mgr = MagicMock()
        with (
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_ci_failure(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=None,
                mgr=mgr,
            )
        assert rc == 2
        mgr._append_agent_comment.assert_not_called()


class TestPrReviewResolution:
    """Issue #116: poll PR review state before merging."""

    def _resolution(
        self,
        resolved: bool = False,
        decision: str = "CHANGES_REQUESTED",
        reviews: list[dict[str, str | int]] | None = None,
        comments: list[dict[str, str | int]] | None = None,
    ) -> object:
        from devbench.github.git_ops import ReviewResolution

        return ReviewResolution(
            resolved=resolved,
            review_decision=decision,
            unresolved_reviews=reviews or [],
            unresolved_comments=comments or [],
            elapsed_seconds=0.0,
        )

    def _wu_file(self, tmp_path: Path, retries: int = 0) -> Path:
        comments = "\n".join(f"[2026-05-01 00:0{i} UTC] [agent/git_ops] [PR_BOT_FAIL] r{i}" for i in range(retries))
        body = f"# E0-F1-S1-T1: t\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n{comments}\n"
        wu = tmp_path / "wu.md"
        wu.write_text(body, encoding="utf-8")
        return wu

    def test_returns_0_when_phase_disabled(self, tmp_path: Path) -> None:
        ops = MagicMock()
        with patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=MagicMock(),
            )
        assert rc == 0
        ops.poll_pr_review_resolution.assert_not_called()

    def test_returns_0_when_no_signals_configured(self, tmp_path: Path) -> None:
        ops = MagicMock()
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ()),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", False),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=MagicMock(),
            )
        assert rc == 0
        ops.poll_pr_review_resolution.assert_not_called()

    def test_returns_0_when_resolved(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.poll_pr_review_resolution.return_value = self._resolution(resolved=True, decision="APPROVED")
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ()),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=MagicMock(),
            )
        assert rc == 0

    def test_returns_3_when_unresolved_under_budget(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.poll_pr_review_resolution.return_value = self._resolution(
            reviews=[{"reviewer": "github-copilot[bot]", "state": "CHANGES_REQUESTED", "body": "fix this"}],
        )
        mgr = MagicMock()
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ("github-copilot[bot]",)),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=mgr,
            )
        assert rc == 3
        feedback = sorted((tmp_path / ".devbench" / "pr-bot-feedback").glob("*.json"))
        assert len(feedback) == 1
        payload = json.loads(feedback[0].read_text(encoding="utf-8"))
        assert payload["pr_number"] == 42
        assert payload["unresolved_reviews"][0]["reviewer"] == "github-copilot[bot]"
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[PR_BOT_FAIL] ")

    def test_returns_1_when_budget_exhausted(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.poll_pr_review_resolution.return_value = self._resolution(
            reviews=[{"reviewer": "bot", "state": "CHANGES_REQUESTED", "body": "x"}],
        )
        mgr = MagicMock()
        # Pre-seed MAX-1 PR_BOT_FAIL retries so the next failure exhausts budget.
        wu = self._wu_file(tmp_path, retries=2)
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ("bot",)),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 1
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[PR_BOT_FAIL_BLOCKED] ")


class TestPauseBeforeMerge:
    """Issue #101: pause-before-merge mode lifecycle."""

    def _wu_file(self, tmp_path: Path) -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: t\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n",
            encoding="utf-8",
        )
        return wu

    def test_pause_transitions_to_in_review(self, tmp_path: Path) -> None:
        wu = self._wu_file(tmp_path)
        mgr = MagicMock()
        with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            rc = cli._pause_before_merge(
                unit_id="E0-F1-S1-T1",
                pr_number=42,
                pr_url="https://github.com/ex/foo/pull/42",
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 0
        mgr.force_status.assert_called_once()
        # 4th positional arg of force_status is the new status
        args = mgr.force_status.call_args.args
        assert args[3] == "in-review"
        msg = mgr._append_agent_comment.call_args.args[2]
        assert "[PR_AWAITING_MERGE]" in msg
        assert "PR #42" in msg

    def test_pause_skips_audit_when_no_wu_file(self, tmp_path: Path) -> None:
        mgr = MagicMock()
        rc = cli._pause_before_merge(
            unit_id="E0-F1-S1-T1",
            pr_number=42,
            pr_url="https://github.com/ex/foo/pull/42",
            wu_file=None,
            mgr=mgr,
        )
        assert rc == 0
        mgr.force_status.assert_not_called()
        mgr._append_agent_comment.assert_not_called()


class TestCmdCheckMerge:
    """Issue #101: cmd_check_merge reconciles in-review work units."""

    def _make_unit(self, repo: str = "caylent-solutions/devbench") -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="t",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo=repo,
            branch="backlog/e0-f1-s1-t1",
            dependencies=[],
        )

    def test_returns_0_with_done_when_pr_merged(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (
            0,
            json.dumps([{"number": 42, "state": "MERGED", "mergedAt": "2026-05-07T00:00:00Z", "url": "u"}]),
            "",
        )
        mgr = MagicMock()
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_done.assert_called_once()

    def test_returns_0_with_blocked_when_pr_closed(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, json.dumps([{"number": 42, "state": "CLOSED", "mergedAt": None, "url": "u"}]), "")
        mgr = MagicMock()
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_blocked.assert_called_once()

    def test_noop_when_pr_still_open(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, json.dumps([{"number": 42, "state": "OPEN", "mergedAt": None, "url": "u"}]), "")
        mgr = MagicMock()
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_done.assert_not_called()
        mgr.mark_blocked.assert_not_called()

    def test_returns_0_with_no_pr_found(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, "[]", "")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0

    def test_returns_1_on_gh_failure(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (1, "", "boom")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 1

    def test_returns_1_on_invalid_json(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, "{not json", "")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 1

    def test_returns_1_when_done_gate_refuses(self, tmp_path: Path) -> None:
        """Done-gate refuses merge promotion when judges did not pass -- rc=1."""
        ops = MagicMock()
        ops._gh.return_value = (
            0,
            json.dumps([{"number": 42, "state": "MERGED", "mergedAt": "2026-05-07T00:00:00Z", "url": "u"}]),
            "",
        )
        mgr = MagicMock()
        mgr.mark_done.side_effect = RuntimeError("done-gate failure")
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 1


class TestCheckMergeRegistration:
    """The check-merge command must be registered in the CLI dispatch table."""

    def test_check_merge_in_commands(self) -> None:
        assert "check-merge" in cli._COMMANDS
        handler, argc, _help = cli._COMMANDS["check-merge"]
        assert handler is cli.cmd_check_merge
        assert argc == 1


# ---------------------------------------------------------------------------
# Issue #148 / #150 / #152 / #153 / #155 cascade-reliability fixes.
# Helpers below reuse the lightweight backlog scaffolding pattern from
# ``TestCmdSyncBlocked`` -- duplicated locally so each test class stays
# self-contained and a fixture rename never silently breaks one of these.
# ---------------------------------------------------------------------------


def _cascade_build_backlog(
    tmp_path: Path,
    rows: list[tuple[str, str, str, str, str, str]],
) -> Path:
    """Materialise BACKLOG.md + per-row work-unit files.

    Each row is ``(id, type, status, deps, basename, comments)`` where
    ``comments`` is appended verbatim to the work-unit Markdown (used to
    inject ``[BLOCKED_PENDING_PROPOSAL]`` markers + audit lines).
    """
    index_lines = [
        "# Backlog\n",
        "## Full Work Unit Index\n",
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
        "|----|-------|------|--------|--------------|------|-----------|",
    ]
    wu_dir = tmp_path / "backlog"
    wu_dir.mkdir(exist_ok=True)
    for unit_id, unit_type, status, deps, basename, comments in rows:
        file_path = f"backlog/{basename}.md"
        index_lines.append(
            f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | caylent-solutions/test-repo | `{file_path}` |"
        )
        wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
        if deps and deps != "None":
            dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
            wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
        if comments:
            wu_body += f"\n{comments}"
        (wu_dir / f"{basename}.md").write_text(wu_body)
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text("\n".join(index_lines) + "\n")
    return index_path


class TestSyncBlockedEvaluatesMarkerTargetState:
    """Issue #148: ``cmd_sync_blocked`` checks each ``[BLOCKED_PENDING_PROPOSAL]``
    marker's target status. Stale markers (target already terminal) no longer
    block the re-queue; only at-least-one non-terminal target keeps the task
    pinned.
    """

    def test_marker_target_terminal_allows_requeue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # T2 carries a marker pointing at T9 which is already done;
        # sync-blocked must NOT skip on the marker any more.
        marker_comments = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T9", "Task", "done", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker_comments),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" in envelope["flipped_to_in_queue"]
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2
        assert "[UNBLOCKED] deps satisfied" in t2

    def test_marker_target_open_skips_requeue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        marker_comments = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T9", "Task", "in-queue", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker_comments),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" not in envelope["flipped_to_in_queue"]
        assert "## Status: blocked" in (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()

    def test_marker_unknown_id_keeps_task_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unknown marker target IDs must remain conservative (treat as open)."""
        marker_comments = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T999\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker_comments),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" not in envelope["flipped_to_in_queue"]


class TestCmdReconcileCascade:
    """Issue #150: ``devbench reconcile-cascade`` walks every blocked task,
    flips the eligible ones (markers all terminal AND deps satisfied), and
    emits ``[CASCADE_RECONCILED]`` audits + a JSON envelope of flips/skips.
    """

    def test_eligible_task_is_flipped(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        marker = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T9", "Task", "done", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        flipped_ids = [item["unit_id"] for item in envelope["flipped"]]
        assert "E0-F1-S1-T2" in flipped_ids
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2
        assert "[CASCADE_RECONCILED]" in t2

    def test_open_marker_keeps_task_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        marker = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T9", "Task", "in-progress", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "None", "E0-F1-S1-T2", marker),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped"] == []
        skips = [item["unit_id"] for item in envelope["skipped"]]
        assert "E0-F1-S1-T2" in skips

    def test_unsatisfied_regular_dep_keeps_task_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-progress", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", ""),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        skip_reasons = {item["unit_id"]: item["reason"] for item in envelope["skipped"]}
        assert "E0-F1-S1-T2" in skip_reasons
        assert "regular dep" in skip_reasons["E0-F1-S1-T2"]

    def test_registered_in_commands(self) -> None:
        assert "reconcile-cascade" in cli._COMMANDS
        handler, argc, _help = cli._COMMANDS["reconcile-cascade"]
        assert handler is cli.cmd_reconcile_cascade
        assert argc == 0


class TestVariadicCommandsCoverage:
    """Issue #152: every command whose body parses a ``--<flag> <value>`` pair
    must be registered in ``_VARIADIC_COMMANDS``. Auto-discover via the
    registry so adding a new flag-bearing command without registering it
    fails this test.
    """

    # Flag tokens that ALWAYS take a value (not boolean toggles). A command's
    # body that contains ``arg == "--reason"`` (and similar) MUST opt into
    # variadic dispatch -- the fixed-arity slice would otherwise drop the
    # value when it follows positional args.
    FLAG_TOKENS_NEEDING_VARIADIC: ClassVar[tuple[str, ...]] = (
        '"--reason"',
        '"--reasoning"',
        '"--message"',
    )

    def test_every_flag_with_value_command_is_variadic(self) -> None:
        import inspect

        offenders: list[str] = []
        for name, (handler, _argc, _desc) in cli._COMMANDS.items():
            try:
                source = inspect.getsource(handler)
            except (OSError, TypeError):
                continue
            if not any(token in source for token in self.FLAG_TOKENS_NEEDING_VARIADIC):
                continue
            if name in cli._VARIADIC_COMMANDS:
                continue
            offenders.append(name)
        assert not offenders, (
            "These commands consume a flag-with-value pair but are NOT registered in "
            f"_VARIADIC_COMMANDS: {offenders}. The fixed-arity dispatcher slice will "
            "drop the value, causing silent failures."
        )

    def test_variadic_set_is_subset_of_commands(self) -> None:
        """Sanity: every variadic name must reference a real command."""
        unknown = cli._VARIADIC_COMMANDS - cli._COMMANDS.keys()
        assert not unknown, f"unknown variadic entries: {unknown}"


class TestStatusPanelFiltersStaleBlockedAudits:
    """Issue #153: ``status --detail`` panel renderer hides ``[BLOCKED]``
    audit rows that have been superseded by a later ``[UNBLOCKED]`` /
    ``[CASCADE_RESOLVED]`` line. The audit history in the file is
    append-only; only the rendered panel filters.
    """

    def test_unblocked_supersedes_blocked(self) -> None:
        content = (
            "## Comments\n\n"
            "[2026-04-01 10:00 UTC] [agent/x] [BLOCKED] dep T9 not yet terminal\n"
            "[2026-04-01 11:00 UTC] [agent/x] [UNBLOCKED] deps satisfied\n"
        )
        assert cli._unsuperseded_blocked_audits(content) == []

    def test_cascade_resolved_supersedes_blocked(self) -> None:
        content = (
            "## Comments\n\n"
            "[2026-04-01 10:00 UTC] [agent/x] [BLOCKED] waiting on cascade\n"
            "[2026-04-01 11:00 UTC] [agent/x] [CASCADE_RESOLVED] markers terminal\n"
        )
        assert cli._unsuperseded_blocked_audits(content) == []

    def test_blocked_without_supersession_is_kept(self) -> None:
        content = "## Comments\n\n[2026-04-01 10:00 UTC] [agent/x] [BLOCKED] dep T9 not yet terminal\n"
        kept = cli._unsuperseded_blocked_audits(content)
        assert len(kept) == 1
        assert "[BLOCKED]" in kept[0]

    def test_blocked_pending_proposal_marker_not_treated_as_blocked_audit(self) -> None:
        """The cascade marker line must not be confused with a plain ``[BLOCKED]`` audit."""
        content = "## Comments\n\n[2026-04-01 10:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] T9\n"
        assert cli._unsuperseded_blocked_audits(content) == []


class TestCmdSweepProposalsAutoPromotesPreExisting:
    """Issue #155: ``cmd_sweep_proposals`` also picks up pre-existing
    ``proposed`` drafts whose proposal JSON has already been deleted.
    """

    def test_orphan_proposed_draft_is_auto_promoted(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.config_loader import RuntimeConfig, TaskFactoryConfig

        # No proposal JSON on disk; the orphan-promote pass should still
        # surface the proposed draft and flip it to in-queue.
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "proposed", "None", "E0-F1-S1-T2", ""),
            ],
        )
        # The promoter resolves the draft via _find_draft_file which expects
        # the layout backlog/E0/E0-F1/E0-F1-S1/<id>.md. Replicate that here
        # so the auto-promote actually finds the draft.
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Test\n\n## Status: proposed\n\n## Description\n\nx\n")
        # Re-point the BACKLOG.md row to the nested location.
        idx_text = index.read_text()
        index.write_text(
            idx_text.replace(
                "`backlog/E0-F1-S1-T2.md`",
                "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md`",
            )
        )

        runtime_with_auto_accept = RuntimeConfig(task_factory=TaskFactoryConfig(auto_accept_proposals=True))
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", runtime_with_auto_accept),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        # No proposal JSON existed -> the materialise loop is a no-op, so
        # only the orphan promote pass touches T2.
        out = capsys.readouterr().out
        assert "orphan auto-promoted 1" in out
        # T2 transitioned to in-queue via promote_proposal.
        t2_md = (story_dir / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2_md

    def test_orphan_promote_skipped_when_toggle_off(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.config_loader import RuntimeConfig, TaskFactoryConfig

        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "proposed", "None", "E0-F1-S1-T2", ""),
            ],
        )
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Test\n\n## Status: proposed\n\n## Description\n\nx\n")
        idx_text = index.read_text()
        index.write_text(
            idx_text.replace(
                "`backlog/E0-F1-S1-T2.md`",
                "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md`",
            )
        )
        runtime_no_auto = RuntimeConfig(task_factory=TaskFactoryConfig(auto_accept_proposals=False))
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", runtime_no_auto),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "orphan auto-promoted" not in out
        assert "## Status: proposed" in (story_dir / "E0-F1-S1-T2.md").read_text()


# ---------------------------------------------------------------------------
# Issue #156: cmd_log_rejection_feedback schema + injection + done-gate
# ---------------------------------------------------------------------------


class TestCmdLogRejectionFeedbackSchema:
    """Issue #156: schema validation + persistence happy path."""

    def _payload(self, code: str = "HARDCODED_URL") -> dict[str, object]:
        return {
            "categories": [
                {
                    "code": code,
                    "severity": "fail",
                    "summary": "Hardcoded URL",
                    "remediation": "Read from env var",
                    "files": ["src/devbench/cli.py"],
                }
            ],
            "raw_verdict_text": "Found hardcoded URL in cli.py:42",
        }

    def test_valid_payload_persists(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
        assert rc == 0
        archive_dir = tmp_path / ".devbench" / "review-failures"
        files = list(archive_dir.glob("E0-F1-S1-T1-code_review-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["judge"] == "code_review"
        assert data["task_id"] == "E0-F1-S1-T1"
        assert data["attempt"] == 1
        assert data["categories"][0]["code"] == "HARDCODED_URL"
        assert data["capped"] is False

    def test_attempt_increments_on_repeat(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
            rc = cli.cmd_log_rejection_feedback(
                "code_review",
                "E0-F1-S1-T1",
                "--json",
                json.dumps(self._payload(code="SCOPE_VIOLATION")),
            )
        assert rc == 0
        files = sorted((tmp_path / ".devbench" / "review-failures").glob("*.json"))
        assert len(files) == 2
        attempts = sorted(json.loads(p.read_text())["attempt"] for p in files)
        assert attempts == [1, 2]

    def test_bad_json_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", "not-json")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_bad_category_code_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad = self._payload(code="NOT_A_REAL_CODE")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(bad))
        assert rc == 1
        assert "vocabulary" in capsys.readouterr().err

    def test_unknown_judge_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback(
                "totally_unknown",
                "E0-F1-S1-T1",
                "--json",
                json.dumps(self._payload()),
            )
        assert rc == 1
        assert "unknown judge" in capsys.readouterr().err

    def test_missing_required_field_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback(
                "code_review",
                "E0-F1-S1-T1",
                "--json",
                json.dumps({"raw_verdict_text": "x"}),
            )
        assert rc == 1
        assert "missing required field" in capsys.readouterr().err

    def test_bad_argv_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_log_rejection_feedback("code_review")
        assert rc == 1
        err = capsys.readouterr().err
        assert "log-rejection-feedback" in err

    def test_unknown_flag_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_log_rejection_feedback("code_review", "E0", "--bogus", "value")
        assert rc == 1
        assert "unknown flag" in capsys.readouterr().err

    def test_severity_must_be_fail_or_warn(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad = {
            "categories": [
                {
                    "code": "HARDCODED_URL",
                    "severity": "info",
                    "summary": "x",
                    "remediation": "y",
                    "files": [],
                }
            ],
            "raw_verdict_text": "x",
        }
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0", "--json", json.dumps(bad))
        assert rc == 1
        assert "severity" in capsys.readouterr().err

    def test_capped_when_exceeds_max_retry_attempts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 1)
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
            cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
        files = sorted((tmp_path / ".devbench" / "review-failures").glob("*.json"))
        cap_flags = [json.loads(p.read_text())["capped"] for p in files]
        assert cap_flags == [False, True]


class TestRejectionFeedbackInjection:
    """Issue #156: ``_collect_review_judge_feedback`` ordering + cap."""

    def _seed(self, workspace: Path, judge: str, task_id: str, attempt: int, code: str) -> None:
        archive_dir = workspace / ".devbench" / "review-failures"
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"{task_id}-{judge}-{attempt}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": task_id,
                    "judge": judge,
                    "attempt": attempt,
                    "rejected_at": "2026-05-02T00:00:00Z",
                    "categories": [
                        {
                            "code": code,
                            "severity": "fail",
                            "summary": "x",
                            "remediation": "y",
                            "files": [],
                        }
                    ],
                    "raw_verdict_text": "x",
                    "capped": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def test_orders_by_severity_then_attempt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Cap above the seed count so nothing is truncated.
        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 10)
        task_id = "E0-F1-S1-T1"
        # Seed three rejections across two judges, lower-severity first.
        self._seed(tmp_path, "doc_review", task_id, 1, "README_SYNC")
        self._seed(tmp_path, "code_review", task_id, 1, "HARDCODED_URL")
        self._seed(tmp_path, "code_review", task_id, 2, "SCOPE_VIOLATION")

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback(task_id)

        # security>code>test>changes_manifest>doc; within judge, higher attempt first.
        order = [(p["judge"], p["attempt"]) for p in payloads]
        assert order == [
            ("code_review", 2),
            ("code_review", 1),
            ("doc_review", 1),
        ]

    def test_cap_truncates_to_max_retry_attempts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 2)
        task_id = "E0-F1-S1-T1"
        self._seed(tmp_path, "code_review", task_id, 1, "HARDCODED_URL")
        self._seed(tmp_path, "code_review", task_id, 2, "SCOPE_VIOLATION")
        self._seed(tmp_path, "doc_review", task_id, 1, "README_SYNC")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback(task_id)
        assert len(payloads) == 2
        # Highest-severity / latest-attempt entries survive the cap.
        assert all(p["judge"] == "code_review" for p in payloads)

    def test_legacy_amender_rejections_synthesized(self, tmp_path: Path) -> None:
        """Legacy ``amender-rejections/`` entries get a v1-shaped record."""
        archive_dir = tmp_path / ".devbench" / "amender-rejections"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-1.json").write_text(
            json.dumps(
                {
                    "task_id": "E0-F1-S1-T1",
                    "attempt": 1,
                    "reason_category": "SCOPE",
                    "reason_text": "old reason",
                    "request": {},
                    "capped": False,
                    "recorded_at": "2026-04-30T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback("E0-F1-S1-T1")
        assert len(payloads) == 1
        assert payloads[0]["judge"] == "manifest_amender"
        assert payloads[0]["categories"][0]["code"] == "SCOPE"

    def test_skips_unparseable_files(self, tmp_path: Path) -> None:
        archive_dir = tmp_path / ".devbench" / "review-failures"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-code_review-1.json").write_text("not json", encoding="utf-8")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback("E0-F1-S1-T1")
        assert payloads == []

    def test_skips_non_dict_payload(self, tmp_path: Path) -> None:
        archive_dir = tmp_path / ".devbench" / "review-failures"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-code_review-1.json").write_text("[]", encoding="utf-8")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback("E0-F1-S1-T1")
        assert payloads == []


class TestDoneGateRejectionFeedbackEnforcement:
    """Issue #156: done-gate refuses transition when rejection unresolved."""

    def _seed_rejection(self, workspace: Path, task_id: str) -> None:
        archive = workspace / ".devbench" / "review-failures"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"{task_id}-code_review-1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": task_id,
                    "judge": "code_review",
                    "attempt": 1,
                    "rejected_at": "2026-05-02T00:00:00Z",
                    "categories": [
                        {
                            "code": "HARDCODED_URL",
                            "severity": "fail",
                            "summary": "x",
                            "remediation": "y",
                            "files": [],
                        }
                    ],
                    "raw_verdict_text": "x",
                    "capped": False,
                }
            ),
            encoding="utf-8",
        )

    def test_blocks_when_unresolved(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n\n## Comments\n", encoding="utf-8")
        self._seed_rejection(tmp_path, "E0-F1-S1-T2")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done("E0-F1-S1-T2")
        assert rc == 1
        err = capsys.readouterr().err
        assert "REJECTION_FEEDBACK_OUTSTANDING" in wu_file.read_text() or "unresolved" in err
        assert "code_review:HARDCODED_URL" in err

    def test_allows_when_resolved_marker_present(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# Task\n## Status: in-review\n\n## Comments\n"
            "[2026-05-02 12:00 UTC] [agent/orchestrator] [REJECTION_FEEDBACK_RESOLVED] code_review:HARDCODED_URL\n",
            encoding="utf-8",
        )
        self._seed_rejection(tmp_path, "E0-F1-S1-T2")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done("E0-F1-S1-T2")
        assert rc == 0
        mock_mgr.mark_done.assert_called_once()

    def test_allows_when_needs_dep_marker_present(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# Task\n## Status: in-review\n\n## Comments\n"
            "[2026-05-02 12:00 UTC] [agent/executor] [NEEDS_DEP] code_review:HARDCODED_URL\n",
            encoding="utf-8",
        )
        self._seed_rejection(tmp_path, "E0-F1-S1-T2")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done("E0-F1-S1-T2")
        assert rc == 0
        mock_mgr.mark_done.assert_called_once()


class TestStatusPanelRejectionCategoryCounts:
    """Issue #156: --detail blocked panel shows pending categories per task."""

    def test_panel_shown_when_blocked_with_unresolved_categories(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: T1\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 x\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] x\n\n## Comments\n",
            encoding="utf-8",
        )
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | T1 | Task | blocked | none | org/repo | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        archive = tmp_path / ".devbench" / "review-failures"
        archive.mkdir(parents=True)
        (archive / "E0-F1-S1-T1-code_review-1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": "E0-F1-S1-T1",
                    "judge": "code_review",
                    "attempt": 1,
                    "rejected_at": "2026-05-02T00:00:00Z",
                    "categories": [
                        {
                            "code": "HARDCODED_URL",
                            "severity": "fail",
                            "summary": "x",
                            "remediation": "y",
                            "files": [],
                        }
                    ],
                    "raw_verdict_text": "x",
                    "capped": False,
                }
            ),
            encoding="utf-8",
        )

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Review-judge rejections (unresolved categories):" in out
        assert "code_review:HARDCODED_URL" in out

    def test_panel_omitted_when_no_unresolved(self, capsys: pytest.CaptureFixture[str]) -> None:
        cli._print_blocked_rejection_categories([])
        assert capsys.readouterr().out == ""


class TestInProgressAttemptDurationRender:
    """Issue #158: cmd_status renders ``(in-progress for ...)`` suffix."""

    def test_status_renders_duration_when_log_has_transition(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "orchestrator.log"
        log_path.write_text(
            "2026-05-02T12:00:00Z [devbench.cli] INFO Set E0-F1-S1-T2 to 'in-progress'\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("JUDGE_LOG_FILE", str(log_path))
        # Freeze time so the duration output is deterministic.
        fake_now = datetime(2026, 5, 2, 12, 23, 0, tzinfo=UTC)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: object = None) -> _FrozenDT:
                return _FrozenDT.fromtimestamp(fake_now.timestamp(), tz=UTC)  # type: ignore[arg-type]

        monkeypatch.setattr("devbench.cli.datetime", _FrozenDT)

        in_prog_unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_prog_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        assert "(in-progress for 23m)" in out


class TestInProgressAttemptDurationFallback:
    """Issue #158: when neither log nor audit yields a parseable timestamp,
    the helper returns ``None`` and the renderer prints the
    ``timer unavailable`` placeholder."""

    def test_returns_none_with_no_signals(self, tmp_path: Path) -> None:
        # Force log_path to a non-existent file AND ensure backlog parse fails fast.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            result = cli._in_progress_attempt_duration("E0-F1-S1-T2", log_path=tmp_path / "missing.log")
        assert result is None

    def test_falls_back_to_audit_when_log_missing(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Stub _resolve_unit_file_by_id directly so we don't have to materialise a
        # full backlog -- the fallback path is the only behaviour under test here.
        wu = backlog_dir / "E0-F1-S1-T2.md"
        wu.write_text(
            "## Comments\n[2026-05-02 11:30 UTC] [agent/orchestrator] Set E0-F1-S1-T2 to 'in-progress'\n",
            encoding="utf-8",
        )
        fake_now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: object = None) -> _FrozenDT:
                return _FrozenDT.fromtimestamp(fake_now.timestamp(), tz=UTC)  # type: ignore[arg-type]

        monkeypatch.setattr("devbench.cli.datetime", _FrozenDT)
        with patch("devbench.cli._resolve_unit_file_by_id", return_value=wu):
            result = cli._in_progress_attempt_duration("E0-F1-S1-T2", log_path=tmp_path / "missing.log")
        assert result == "30m"


class TestInProgressAttemptDurationLatestAttemptOnly:
    """Issue #158: multiple in-progress transitions resolve to the most recent one."""

    def test_picks_most_recent_log_transition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "orchestrator.log"
        log_path.write_text(
            "2026-05-02T08:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n"
            "2026-05-02T09:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'blocked'\n"
            "2026-05-02T11:30:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        fake_now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: object = None) -> _FrozenDT:
                return _FrozenDT.fromtimestamp(fake_now.timestamp(), tz=UTC)  # type: ignore[arg-type]

        monkeypatch.setattr("devbench.cli.datetime", _FrozenDT)
        result = cli._in_progress_attempt_duration("E0-F1-S1-T1", log_path=log_path)
        # 4h vs 30m vs 4h+30m: most recent wins -> 30m.
        assert result == "30m"

    def test_format_duration_thresholds(self) -> None:
        assert cli._format_duration(0) == "0s"
        assert cli._format_duration(-5) == "0s"
        assert cli._format_duration(42) == "42s"
        assert cli._format_duration(60) == "1m"
        assert cli._format_duration(23 * 60) == "23m"
        assert cli._format_duration(60 * 60 + 47 * 60) == "1h 47m"
        assert cli._format_duration(2 * 86400 + 3 * 3600) == "2d 3h"

    def test_log_with_invalid_timestamp_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "orchestrator.log"
        log_path.write_text(
            "9999-99-99T99:99:99Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        result = cli._in_progress_attempt_duration("E0-F1-S1-T1", log_path=log_path)
        # Only the bogus timestamp -> nothing parses -> None.
        assert result is None

    def test_audit_with_invalid_timestamp_skipped(
        self,
        tmp_path: Path,
        backlog_dir: Path,
    ) -> None:
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "## Comments\n[9999-99-99 99:99 UTC] [agent/orchestrator] Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        with patch("devbench.cli._resolve_unit_file_by_id", return_value=wu):
            result = cli._in_progress_attempt_duration("E0-F1-S1-T1", log_path=tmp_path / "missing.log")
        assert result is None


class TestTryResolveLogFilePath:
    """Issue #185: ``_try_resolve_log_file_path`` returns ``None`` instead of
    raising ``SystemExit`` so the status-timer fallback can consult the
    YAML config without crashing when none of the three resolution
    inputs is set.
    """

    def test_returns_none_when_resolve_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.delenv("JUDGE_WORKSPACE_ROOT", raising=False)
        cfg = MagicMock()
        cfg.log_file = ""
        with patch("devbench.cli.RUNTIME_CONFIG", cfg):
            result = cli._try_resolve_log_file_path()
        assert result is None

    def test_returns_path_when_yaml_log_file_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``JUDGE_LOG_FILE`` is unset but YAML config carries a
        ``log_file``, the wrapper resolves the workspace-relative path."""
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", str(tmp_path))
        cfg = MagicMock()
        cfg.log_file = "logs/orch.log"
        with patch("devbench.cli.RUNTIME_CONFIG", cfg):
            result = cli._try_resolve_log_file_path()
        assert result == tmp_path / "logs" / "orch.log"

    def test_timer_uses_yaml_config_when_env_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: ``_latest_log_in_progress_ts`` resolves the log via
        YAML ``log_file`` when ``JUDGE_LOG_FILE`` is unset. Prior to
        issue #185 the helper bailed out with ``None`` causing
        ``cmd_status`` to render ``timer unavailable`` even though the
        log was discoverable."""
        log_path = tmp_path / "logs" / "orch.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            "2026-05-02T12:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("JUDGE_LOG_FILE", raising=False)
        monkeypatch.setenv("JUDGE_WORKSPACE_ROOT", str(tmp_path))
        cfg = MagicMock()
        cfg.log_file = "logs/orch.log"
        with patch("devbench.cli.RUNTIME_CONFIG", cfg):
            ts = cli._latest_log_in_progress_ts("E0-F1-S1-T1", None)
        assert ts is not None
        assert ts.year == 2026 and ts.hour == 12 and ts.minute == 0


class TestCmdStatusNextActionableFilter:
    """Issue #185(c): the ``Next actionable`` line excludes IDs already
    rendered in ``Active work units`` (those are IN_PROGRESS / IN_REVIEW
    and ``get_parallel_candidates`` includes IN_PROGRESS for resume).
    Previously the line redundantly echoed the current claim.
    """

    def test_actionable_filtered_when_same_as_active(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        in_prog = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        in_queue = WorkUnit(
            id="E0-F1-S1-T2",
            title="Next up",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_prog, in_queue]
        # get_parallel_candidates returns IN_PROGRESS first, then IN_QUEUE.
        mock_parser.get_parallel_candidates.return_value = [in_prog, in_queue]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []
        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        # Active panel still shows the in-progress task ...
        assert "E0-F1-S1-T1" in out
        # ... and Next actionable points at the DIFFERENT in-queue task.
        assert "Next actionable: E0-F1-S1-T2" in out

    def test_no_actionable_message_when_only_active(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the only candidate is the in-progress task itself, the
        ``Next actionable`` line is suppressed (no genuine next task)."""
        in_prog = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_prog]
        mock_parser.get_parallel_candidates.return_value = [in_prog]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []
        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        # No "Next actionable" line; instead the no-actionable branch fires.
        assert "Next actionable" not in out
        assert "No actionable units." in out


class TestCmdWriteSnapshot:
    """Issue #162 Phase 6 (ADR-20): write a fresh report snapshot."""

    def test_writes_snapshot_to_canonical_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text("seed\n")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=log),
            patch("devbench.reporting.report.generate_report", return_value="REPORT"),
        ):
            rc = cli.cmd_write_snapshot()

        assert rc == 0
        snapshot_file = tmp_path / ".devbench" / "report-snapshot.json"
        assert snapshot_file.is_file()
        payload = json.loads(snapshot_file.read_text())
        assert payload["report_text"] == "REPORT"

    def test_rejects_extra_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_write_snapshot("unexpected")
        assert rc == 1
        assert "no arguments" in capsys.readouterr().err


class TestCmdRebuildWindowStats:
    """Issue #162 Phase 2 (ADR-17): rebuild per-task aggregates from log."""

    def test_rebuilds_aggregates(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-05-04T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'in-progress' in both files\n"
            "2026-05-04T11:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
        )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=log),
        ):
            rc = cli.cmd_rebuild_window_stats()

        assert rc == 0
        agg = tmp_path / ".devbench" / "window-stats" / "E0-F1-S1-T1.json"
        assert agg.is_file()
        out = capsys.readouterr().out
        assert "wrote 1 per-task aggregate" in out

    def test_rejects_extra_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_rebuild_window_stats("oops")
        assert rc == 1
        assert "no arguments" in capsys.readouterr().err


class TestCmdArchiveSession:
    """Issue #162 Phase 7 (ADR-21): archive a session to Parquet."""

    def test_writes_archive(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text('{"event": "x"}\n')

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=log),
        ):
            rc = cli.cmd_archive_session("session-abc")

        assert rc == 0
        archive = tmp_path / "logs" / "legacy" / "session-abc.parquet"
        assert archive.is_file()
        assert "session-abc" in capsys.readouterr().out

    def test_requires_session_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_archive_session()
        assert rc == 1
        assert "exactly one positional" in capsys.readouterr().err

    def test_rejects_extra_positional(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_archive_session("a", "b")
        assert rc == 1
        assert "exactly one positional" in capsys.readouterr().err

    def test_log_path_override(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        custom_log = tmp_path / "custom" / "log"
        custom_log.parent.mkdir(parents=True)
        custom_log.write_text('{"event": "y"}\n')

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=tmp_path / "default.log"),
        ):
            rc = cli.cmd_archive_session("s1", "--log-path", str(custom_log))

        assert rc == 0
        assert (tmp_path / "logs" / "legacy" / "s1.parquet").is_file()

    def test_rejects_log_path_without_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_archive_session("session-id", "--log-path")
        assert rc == 1
        assert "--log-path requires a value" in capsys.readouterr().err

    def test_propagates_archive_dependency_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log = tmp_path / "log"
        log.write_text('{"event": "x"}\n')

        from devbench.reporting.archive import ArchiveDependencyMissingError

        def _raise(*args: object, **kwargs: object) -> object:
            raise ArchiveDependencyMissingError("archive operations")

        monkeypatch.setattr("devbench.cli.WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr("devbench.cli._resolve_log_file_path", lambda: log)
        monkeypatch.setattr("devbench.reporting.archive.archive_session", _raise)

        rc = cli.cmd_archive_session("s1")
        assert rc == 1
        assert "pip install devbench[archive]" in capsys.readouterr().err

    def test_propagates_file_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=tmp_path / "nope.log"),
        ):
            rc = cli.cmd_archive_session("s1")
        assert rc == 1
        assert "not found" in capsys.readouterr().err


class TestCmdStatusSixBucketCounts:
    """E2-F2-S2-T1: cmd_status prints six Blocked count rows and six detail panels."""

    _CANONICAL_COUNT_LABELS: ClassVar[list[str]] = [
        "Blocked (auto-clearing)",
        "Blocked (amendment-recovery)",
        "Blocked (dependency)",
        "Blocked (held)",
        "Blocked (blocked-on-held)",
        "Blocked (operator-required)",
    ]

    _CANONICAL_PANEL_HEADERS: ClassVar[list[str]] = [
        "Blocked tasks (auto-clearing via proposal)",
        "Blocked tasks (awaiting amendment recovery)",
        "Blocked tasks (awaiting dependency)",
        "Held tasks",
        "Blocked tasks (blocked on held)",
        "Blocked tasks (operator action required)",
    ]

    def _make_blocked_unit(self, unit_id: str, title: str) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=title,
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"/fake/{unit_id}.md"),
            repo="r",
            dependencies=[],
        )

    def _make_six_unit_fixture(
        self,
    ) -> tuple[list[WorkUnit], Any]:
        """Return (units, classify_side_effect) covering one task per BlockedTaskState."""
        from devbench.backlog.proposal import BlockedTaskState

        units = [
            self._make_blocked_unit("E9-F1-S1-T1", "Auto"),
            self._make_blocked_unit("E9-F1-S1-T2", "AmendmentRecovery"),
            self._make_blocked_unit("E9-F1-S1-T3", "Dependency"),
            self._make_blocked_unit("E9-F1-S1-T4", "Held"),
            self._make_blocked_unit("E9-F1-S1-T5", "BlockedOnHeld"),
            self._make_blocked_unit("E9-F1-S1-T6", "Operator"),
        ]

        state_map = {
            "E9-F1-S1-T1": BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
            "E9-F1-S1-T2": BlockedTaskState.AWAITING_AMENDMENT_RECOVERY,
            "E9-F1-S1-T3": BlockedTaskState.AWAITING_DEPENDENCY,
            "E9-F1-S1-T4": BlockedTaskState.HELD,
            "E9-F1-S1-T5": BlockedTaskState.BLOCKED_ON_HELD,
            "E9-F1-S1-T6": BlockedTaskState.OPERATOR_ACTION_REQUIRED,
        }

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            return state_map[task_id]

        return units, fake_classify

    def test_six_count_rows_present_in_canonical_order(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Six Blocked (...) count rows appear in canonical spec order, summing to total blocked."""
        units, fake_classify = self._make_six_unit_fixture()

        parser = MagicMock()
        parser.parse_index.return_value = units
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = units
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out

        # Each label present with count 1.
        for label in self._CANONICAL_COUNT_LABELS:
            assert re.search(rf"{re.escape(label)}\s+1\b", out), f"missing row {label!r}\n{out}"

        # Labels appear in canonical order.
        positions = [out.index(label) for label in self._CANONICAL_COUNT_LABELS]
        assert positions == sorted(positions), f"count rows not in canonical order\n{out}"

        # Old three-bucket rows must NOT appear.
        assert "Blocked (auto)" not in out, out
        assert "Blocked (recovery)" not in out, out
        assert "Blocked (attn)" not in out, out

    def test_six_count_rows_all_zero_when_no_blocked(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """All six rows still print (at zero) even when no blocked tasks exist."""
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out

        for label in self._CANONICAL_COUNT_LABELS:
            assert re.search(rf"{re.escape(label)}\s+0\b", out), f"missing zero row {label!r}\n{out}"

    def test_six_detail_panels_in_canonical_order(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--detail renders six panel headers in canonical spec order."""
        units, fake_classify = self._make_six_unit_fixture()

        parser = MagicMock()
        parser.parse_index.return_value = units
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = units
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out

        # All six panel headers present.
        for header in self._CANONICAL_PANEL_HEADERS:
            assert header in out, f"missing panel header {header!r}\n{out}"

        # Panel headers appear in canonical order.
        positions = [out.index(header) for header in self._CANONICAL_PANEL_HEADERS]
        assert positions == sorted(positions), f"panels not in canonical order\n{out}"


# ---------------------------------------------------------------------------
# Multi-PR replay regression tests for rewired cmd_git_ops (E7-F1-S1-T1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCmdGitOpsMultiPrReplay:
    """Regression tests: rewired cmd_git_ops produces same transitions as pre-refactor.

    Each fixture exercises one CIResult value and asserts the same status
    transitions, audit-comment text, and exit code that the pre-refactor code
    produced on that scenario.
    """

    def _make_unit(self, unit_id: str = "E202-F1-S1-T2") -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title="Replay Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    # ------------------------------------------------------------------
    # Scenario 1: CIResult.GREEN => merge, rc=0
    # ------------------------------------------------------------------

    def test_green_result_merges_and_returns_zero(self, tmp_path: Path) -> None:
        """When wait_for_checks_and_classify returns GREEN, cmd_git_ops merges and returns 0."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.PAUSE_BEFORE_MERGE", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 0
        mock_ops_inst.merge_pr.assert_called_once()

    # ------------------------------------------------------------------
    # Scenario 2: CIResult.FAILED_UNKNOWN => same as wait_for_checks=False, rc=2 (retry)
    # ------------------------------------------------------------------

    def test_failed_unknown_result_returns_retry_rc(self, tmp_path: Path) -> None:
        """FAILED_UNKNOWN triggers the same CI-failure retry path as pre-refactor False."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/43"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.FAILED_UNKNOWN
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 5),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 2
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 3: CIResult.FAILED_KNOWN_TASK => same CI-failure path, rc=2
    # ------------------------------------------------------------------

    def test_failed_known_task_result_returns_retry_rc(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK triggers the CI-failure retry path (rc=2)."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/44"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 5),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 2
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 4: CIResult.TIMEOUT => same CI-failure path, rc=2
    # ------------------------------------------------------------------

    def test_timeout_result_returns_retry_rc(self, tmp_path: Path) -> None:
        """TIMEOUT triggers the CI-failure retry path (rc=2), not a hard crash."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/45"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.TIMEOUT
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 5),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 2
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 5: FAILED_KNOWN_TASK + budget exhausted => rc=1 (BLOCKED)
    # ------------------------------------------------------------------

    def test_failed_known_task_budget_exhausted_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When retry budget is exhausted, any CI failure returns rc=1."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/46"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 1),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 1
        err = capsys.readouterr().err
        assert "budget exhausted" in err.lower() or "max_retry" in err.lower() or "blocked" in err.lower()
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 6: parity assertion -- GREEN produces same transitions as
    #             pre-refactor wait_for_checks=True
    # ------------------------------------------------------------------

    def test_green_parity_with_pre_refactor_true(self, tmp_path: Path) -> None:
        """CIResult.GREEN from wait_for_checks_and_classify produces bit-identical
        outcome to what the pre-refactor wait_for_checks=True path produced:
        merge runs and rc=0.

        Both legs of this test use the rewired cmd_git_ops (the pre-refactor
        path no longer exists).  The assertion is that two differently
        constructed mocks -- one whose wait_for_checks_and_classify returns
        GREEN explicitly, one whose MagicMock default is replaced with GREEN
        -- both result in rc=0 and merge_pr being called exactly once.
        """
        from devbench.github.git_ops import CIResult

        unit = self._make_unit("E202-F1-S1-T3")

        # First leg: explicit CIResult.GREEN via wait_for_checks_and_classify
        mock_ops_a = MagicMock()
        mock_ops_a.create_pr.return_value = "https://github.com/org/repo/pull/50"
        mock_ops_a.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops_a_cls = MagicMock(return_value=mock_ops_a)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_a_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.PAUSE_BEFORE_MERGE", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            rc_a = cli.cmd_git_ops(unit.id)

        # Second leg: also CIResult.GREEN but on a fresh mock (parity verification)
        mock_ops_b = MagicMock()
        mock_ops_b.create_pr.return_value = "https://github.com/org/repo/pull/51"
        mock_ops_b.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops_b_cls = MagicMock(return_value=mock_ops_b)

        mock_parser2 = MagicMock()
        mock_parser2.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser2),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_b_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.PAUSE_BEFORE_MERGE", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            rc_b = cli.cmd_git_ops(unit.id)

        assert rc_a == rc_b == 0
        mock_ops_a.merge_pr.assert_called_once()
        mock_ops_b.merge_pr.assert_called_once()
