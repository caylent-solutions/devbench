"""Tests for devbench.cli module."""

from __future__ import annotations

import json
import re
from pathlib import Path
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

    def test_next_does_not_mutate_status(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture
    ) -> None:
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

    def test_next_returns_json_descriptor(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture
    ) -> None:
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
    not BACKLOG_ROOT — otherwise file paths of the form 'backlog/...' get resolved as
    BACKLOG_ROOT/backlog/... which is a double 'backlog/' and causes false 'file missing' errors.
    """

    def _make_layout(self, workspace: Path) -> tuple[Path, Path]:
        """Create realistic layout: BACKLOG.md at workspace root, work unit in workspace/backlog/."""
        backlog_dir = workspace / BACKLOG_SUBDIR
        backlog_dir.mkdir(parents=True, exist_ok=True)
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text("# E0-F1-S1-T1: Task\n\n## Status: in-queue\n", encoding="utf-8")
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

    def test_returns_1_and_prints_errors_when_invalid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
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

    def test_no_args_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli"]):
            result = cli.main()
        assert result == 1

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
        mock_ops.checkout_default_branch.assert_called_once_with(
            "caylent-solutions/devbench", repo_path
        )

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
            "diff --git a/new_feature.py b/new_feature.py\n"
            "+++ b/new_feature.py\n@@ -0,0 +1 @@\n+feature\n"
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
            "Upstream-merged file appeared in output — bare branch ref was used instead of origin/"
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
        assert "## Comments" not in data["content"], (
            "Comments section should be stripped when --strip-comments is used"
        )
        assert "[REVIEW_PASS]" not in data["content"], (
            "Comment entries should be removed when --strip-comments is used"
        )
        assert "## Description" in data["content"], (
            "Content before ## Comments should be preserved"
        )

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
        assert "[REVIEW_PASS]" in data["content"], (
            "Without --strip-comments, comment entries should be present"
        )

    def test_read_unit_strip_comments_without_unit_id_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
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
        content = (
            "# E216-F1-S1-T1: Strip Test\n\n"
            "## Status: in-progress\n\n"
            "## Description\n\nSome description.\n"
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

    def test_log_comment_contains_no_review_token(
        self, tmp_path: Path
    ) -> None:
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

    def test_git_ops_appends_pr_created_comment(
        self, tmp_path: Path
    ) -> None:
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
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content, f"[agent/git_ops] not found in:\n{content}"
        assert "[PR_CREATED]" in content, f"[PR_CREATED] not found in:\n{content}"
        assert pr_url in content, f"PR URL not found in:\n{content}"

    def test_git_ops_appends_pr_merged_comment_normal_path(
        self, tmp_path: Path
    ) -> None:
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
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content
        assert "[PR_MERGED]" in content, f"[PR_MERGED] not found in:\n{content}"
        assert pr_url in content

    def test_git_ops_appends_pr_merged_comment_rebase_retry_path(
        self, tmp_path: Path
    ) -> None:
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
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[PR_MERGED]" in content, f"[PR_MERGED] not found after rebase-retry in:\n{content}"
        assert pr_url in content

    def test_event_comments_contain_no_review_token(
        self, tmp_path: Path
    ) -> None:
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
        # Note: wu_file is NOT created — file resolution should fail gracefully

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
        ):
            result = cli.cmd_git_ops(unit_id)

        # Must NOT fail due to missing file — git ops already succeeded
        assert result == 0


@pytest.mark.unit
class TestCmdMarkDoneEventComment:
    """Tests for AC-4, AC-5: cmd_mark_done appends [orchestrator] [DONE] comment."""

    def test_mark_done_appends_done_comment(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
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
            f"[2026-01-01 00:00 UTC] [judge/{j}] [REVIEW_PASS] ok\n"
            for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
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

    def test_mark_done_done_comment_contains_no_review_token(
        self, tmp_path: Path
    ) -> None:
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
            f"[2026-01-01 00:00 UTC] [judge/{j}] [REVIEW_PASS] ok\n"
            for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
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

    def test_resolve_unit_file_returns_path_when_found_under_backlog_root(
        self, tmp_path: Path
    ) -> None:
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

    def test_resolve_unit_file_returns_none_when_not_found(
        self, tmp_path: Path
    ) -> None:
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
        # No file is created — both paths will be missing

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog_root"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace_root"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is None

    def test_resolve_unit_file_falls_back_to_workspace_root(
        self, tmp_path: Path
    ) -> None:
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
    """Tests for cmd_log_tdd — AC-1 through AC-6, AC-11."""

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
        """AC-4: Phase argument is case-insensitive — 'red' normalized to 'RED'."""
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
        assert "log-tdd" in cli._COMMANDS, (
            "log-tdd command must be registered in cli._COMMANDS"
        )

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
        assert "unique-tdd-marker-xyz" not in comments_section, (
            f"TDD entry leaked into ## Comments: {comments_section}"
        )
