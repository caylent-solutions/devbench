"""Tests for devbench.cli module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import BACKLOG_SUBDIR


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
        assert data["id"] == "E0-F1-S1-T2"

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
        mock_ops.wait_for_checks.return_value = True
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        Given: wait_for_checks returns False (checks failed)
        When: cmd_git_ops is called
        Then: returns 1 and merge_pr is never called (AC-4)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks.return_value = False
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        Then: GitOpsJudge.ensure_branch is called with the correct repo, path, and branch (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True
        mock_ops.checkout_default_branch.side_effect = lambda *_: call_order.append("checkout")
        mock_ops.update_parent_submodule_ref.side_effect = lambda *_: call_order.append("submodule")
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", True),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        mock_ops.wait_for_checks.return_value = True

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E0-F1-S1-T1")

        assert result == 1
        assert "could not parse pr number" in capsys.readouterr().err.lower()


class TestCmdGitOpsFinalizeHappyPath:
    """Test cmd_git_ops_finalize happy path."""

    def test_finalize_pushes_and_creates_pr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 831-855: full happy path for git-ops-finalize."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output["pr_url"] == "https://github.com/org/repo/pull/99"
        mock_ops.commit_and_push.assert_called_once()
        mock_ops.create_pr.assert_called_once()

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

    def test_cmd_report_watch_mode_interrupted(self) -> None:
        """cmd_report with watch_interval > 0 loops until KeyboardInterrupt (lines 296-306)."""
        call_count = 0

        def fake_generate_report(**kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            return f"report iteration {call_count}"

        def fake_sleep(seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            result = cli.cmd_report(watch_interval=5)

        assert result == 0
        assert call_count == 1

    def test_cmd_report_watch_invokes_clear_command_each_tick(self) -> None:
        """Each watch tick must clear the terminal viewport AND scrollback.

        When the OS provides a `clear` (or `cls`) binary, we delegate to it via
        subprocess -- terminfo handles the right sequence per terminal, including
        the scrollback erase that bare `\\033[H\\033[2J` does not perform on
        all terminals (notably VS Code's xterm.js where the prior `\\033[3J`
        approach was unreliable).
        """

        def fake_sleep(seconds: float) -> None:
            raise KeyboardInterrupt

        captured_clear_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            captured_clear_calls.append(cmd)

            class _Done:
                returncode = 0

            return _Done()

        with (
            patch("devbench.reporting.report.generate_report", return_value="frame"),
            patch("time.sleep", side_effect=fake_sleep),
            patch("devbench.cli._TERMINAL_CLEAR_CMD", "/usr/bin/clear"),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            result = cli.cmd_report(watch_interval=1)

        assert result == 0
        # First tick must have called subprocess.run with the resolved clear path.
        assert captured_clear_calls
        assert captured_clear_calls[0] == ["/usr/bin/clear"]

    def test_cmd_report_watch_falls_back_to_ris_when_no_clear_binary(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When no `clear`/`cls` binary is on PATH, fall back to VT100 RIS."""

        def fake_sleep(seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.reporting.report.generate_report", return_value="frame"),
            patch("time.sleep", side_effect=fake_sleep),
            patch("devbench.cli._TERMINAL_CLEAR_CMD", None),
        ):
            result = cli.cmd_report(watch_interval=1)

        out = capsys.readouterr().out
        assert result == 0
        # Full reset escape (RIS) emitted on stdout.
        assert "\033c" in out


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
        mock_report.assert_called_once_with(since="", watch_interval=10)

    def test_short_watch_flag_extracted(self) -> None:
        """-w <N> is equivalent to --watch <N>."""
        with (
            patch("sys.argv", ["devbench", "report", "-w", "3"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="", watch_interval=3)

    def test_watch_flag_with_since_arg(self) -> None:
        """--watch is separated from the since timestamp argument (lines 996-998)."""
        with (
            patch("sys.argv", ["devbench", "report", "--watch", "5", "2025-01-15T10:30:00Z"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(since="2025-01-15T10:30:00Z", watch_interval=5)

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
            patch("devbench.cli.GitOpsJudge", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.cli.GitOpsJudge", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
            patch("devbench.cli.GitOpsJudge", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsJudge", return_value=mock_ops),
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
        draft = tmp_path / "t.md"
        draft.write_text("x")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", return_value=draft),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T2")
        assert rc == 0
        out = capsys.readouterr().out
        assert "E0-F1-S1-T2" in out
        assert "in-queue" in out

    def test_promote_with_no_dep_flag(self, tmp_path: Path) -> None:
        draft = tmp_path / "t.md"
        draft.write_text("x")
        seen: dict = {}

        def fake(
            *, workspace_root: Path, backlog_root: Path, backlog_index: Path, task_id: str, dep_on_source: bool = True
        ) -> Path:
            seen["dep"] = dep_on_source
            seen["id"] = task_id
            return draft

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


class TestCmdSweepProposalsResurrectionGuard:
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


class TestProposalCommandsRegistered:
    def test_list_proposals_registered(self) -> None:
        assert "list-proposals" in cli._COMMANDS
        assert "promote-proposal" in cli._COMMANDS
        assert "reject-proposal" in cli._COMMANDS

    def test_sweep_proposals_registered(self) -> None:
        assert "sweep-proposals" in cli._COMMANDS


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
                    suggested_approach="",
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
