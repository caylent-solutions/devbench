"""Tests for cmd_get_diff defer-mode task-attributed commit lookup (issue #247).

Covers:
- Task-attributed commit found via git log --grep -> diffs emitted
- Defer empty with no attributable commit -> exit 45 + verbatim stderr
- Non-defer empty -> (no changes) rc 0 (unchanged behaviour)
- git show HEAD fallback must NOT be called (AC-247a-1)
- git show failure on known SHA -> fail-fast (stderr + non-zero)
- git rev-parse failure -> WARNING emitted before HEAD fallback
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli


@pytest.mark.unit
class TestGetDiffDeferTaskAttributedLookup:
    """Defer-mode tests: task-attributed commit lookup replaces git show HEAD."""

    def _make_unit(self) -> object:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        return WorkUnit(
            id="E4-F1-S1-T1",
            title="Replace get-diff defer fallback",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E4-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_defer_mode_emits_task_attributed_diff_when_commit_found(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When git log --grep finds a commit for the unit ID, its diff is emitted."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        sha = "abc1234def5678"
        task_diff = "diff --git a/src/f.py b/src/f.py\n+new line\n"

        responses: dict[tuple[str, ...], tuple[int, str, str]] = {
            ("git", "log", "--grep", "^E4-F1-S1-T1:", "--format=%H", "feat/improvements"): (0, sha + "\n", ""),
            ("git", "show", "--format=", sha): (0, task_diff, ""),
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/improvements\n", ""),
        }

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            return responses.get(tuple(cmd), (0, "", ""))

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E4-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert task_diff.strip() in output

    def test_defer_mode_exits_45_when_no_attributable_commit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When git log --grep finds no commit, exit 45 with verbatim stderr."""
        from devbench.constants import GET_DIFF_NO_ATTRIBUTABLE

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        branch = "feat/improvements"

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "", "")
            if cmd == ["git", "diff"]:
                return (0, "", "")
            if cmd[:4] == ["git", "log", "--grep", "^E4-F1-S1-T1:"]:
                return (0, "", "")
            if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, branch + "\n", "")
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E4-F1-S1-T1")

        assert result == GET_DIFF_NO_ATTRIBUTABLE
        assert result == 45
        err = capsys.readouterr().err
        assert "E4-F1-S1-T1" in err
        assert branch in err
        assert "no task-attributable changes" in err

    def test_defer_mode_git_show_head_never_called(self, tmp_path: Path) -> None:
        """AC-247a-1: git show HEAD must never be called in defer mode."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["git", "diff", "--cached"]:
                return (0, "", "")
            if cmd == ["git", "diff"]:
                return (0, "", "")
            if cmd[:4] == ["git", "log", "--grep", "^E4-F1-S1-T1:"]:
                return (0, "", "")
            if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feat/improvements\n", "")
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            cli.cmd_get_diff("E4-F1-S1-T1")

        git_show_head_cmds = [c for c in calls if c[:3] == ["git", "show", "--format="] and "HEAD" in c]
        assert not git_show_head_cmds, (
            f"git show HEAD was called {len(git_show_head_cmds)} time(s) -- AC-247a-1 violated"
        )

    def test_non_defer_empty_returns_no_changes_rc0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Non-defer mode with no changes still emits (no changes) with rc 0."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", False),
        ):
            result = cli.cmd_get_diff("E4-F1-S1-T1")

        assert result == 0
        assert capsys.readouterr().out.strip() == "(no changes)"

    @pytest.mark.parametrize("fail_rc", [128, 1])
    def test_defer_mode_git_show_failure_fails_fast(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], fail_rc: int
    ) -> None:
        """When git show on a known commit SHA fails, emit ERROR to stderr and return non-zero."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        sha = "deadbeef1234"

        responses: dict[tuple[str, ...], tuple[int, str, str]] = {
            ("git", "log", "--grep", "^E4-F1-S1-T1:", "--format=%H", "feat/improvements"): (0, sha + "\n", ""),
            ("git", "show", "--format=", sha): (fail_rc, "", "fatal: bad object"),
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "feat/improvements\n", ""),
        }

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            return responses.get(tuple(cmd), (0, "", ""))

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E4-F1-S1-T1")

        assert result != 0
        err = capsys.readouterr().err
        assert "ERROR" in err
        assert sha in err

    def test_defer_mode_rev_parse_failure_emits_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When git rev-parse fails, a WARNING is emitted to stderr before falling back to HEAD."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "", "")
            if cmd == ["git", "diff"]:
                return (0, "", "")
            if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return (128, "", "fatal: not a git repo")
            if cmd[:4] == ["git", "log", "--grep", "^E4-F1-S1-T1:"]:
                return (0, "", "")
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            cli.cmd_get_diff("E4-F1-S1-T1")

        err = capsys.readouterr().err
        assert "WARNING" in err

    def test_verification_only_already_landed_unit_is_stuck_at_exit_45(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Tracked issue 014 trigger: a verification-only unit whose deliverable already landed.

        The unit produces no staged/unstaged diff and the landing commit is NOT
        prefixed with the unit id, so defer-mode get-diff returns
        GET_DIFF_NO_ATTRIBUTABLE (45). This is the state that strands the unit;
        the sanctioned recovery is ``mark-done --already-satisfied`` (covered in
        the manager/CLI suites), not a change to get-diff.
        """
        from devbench.constants import GET_DIFF_NO_ATTRIBUTABLE

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feat/flatten-review-pipeline\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E4-F1-S1-T1")

        assert result == GET_DIFF_NO_ATTRIBUTABLE
        assert result == 45

    def test_defer_mode_rev_parse_failure_falls_back_to_head(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When git rev-parse fails, branch name defaults to HEAD and lookup still runs."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        sha = "cafebabe9999"
        task_diff = "diff --git a/x.py b/x.py\n+x\n"
        git_log_calls: list[list[str]] = []

        static_responses: dict[tuple[str, ...], tuple[int, str, str]] = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (128, "", "fatal: not a git repo"),
            ("git", "show", "--format=", sha): (0, task_diff, ""),
        }

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd[:4] == ["git", "log", "--grep", "^E4-F1-S1-T1:"]:
                git_log_calls.append(cmd)
                return (0, sha + "\n", "")
            return static_responses.get(tuple(cmd), (0, "", ""))

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E4-F1-S1-T1")

        assert result == 0
        assert git_log_calls, "git log --grep should still be called after rev-parse failure"
        branch_arg = git_log_calls[0][-1]
        assert branch_arg == "HEAD"
        output = capsys.readouterr().out
        assert task_diff.strip() in output
