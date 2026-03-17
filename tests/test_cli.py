"""Tests for devbench.cli module."""

from __future__ import annotations

import json
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
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
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
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
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
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
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
