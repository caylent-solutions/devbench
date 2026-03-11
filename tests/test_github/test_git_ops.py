"""Tests for judges.git_ops module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.github.git_ops import GitOpsJudge
from devbench.judges.base import Verdict


class TestGitOpsInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = GitOpsJudge()
        assert judge.name == "git_ops"


class TestEvaluate:
    """Test the no-op evaluate method."""

    def test_returns_pass(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        result = judge.evaluate(tmp_path / "wu.md", tmp_path)
        assert result.verdict is Verdict.PASS
        assert "no-op" in result.reasoning


class TestCommitAndPush:
    """Test commit_and_push method."""

    def test_validates_repo(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="not allowed"):
            judge.commit_and_push("evil/repo", tmp_path, "branch", "msg")

    def test_calls_git_commands(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_git") as mock_git:
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "commit msg")

        assert mock_git.call_count == 4
        calls = [c.args[0] for c in mock_git.call_args_list]
        assert calls[0] == ["checkout", "-B", "feature/x"]
        assert calls[1] == ["add", "-A"]
        assert calls[2] == ["commit", "-m", "commit msg"]
        assert calls[3] == ["push", "origin", "feature/x"]

    def test_rejects_invalid_branch_name(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="Invalid branch name"):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "bad branch!", "msg")

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_git", side_effect=RuntimeError("git failed")):
            with pytest.raises(RuntimeError, match="git failed"):
                judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "b", "m")


class TestCreatePr:
    """Test create_pr method."""

    def test_validates_repo(self) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="not allowed"):
            judge.create_pr("evil/repo", "branch", "title", "body")

    def test_returns_pr_url(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(
            judge,
            "_gh",
            return_value=(0, "https://github.com/org/repo/pull/42\n", ""),
        ):
            url = judge.create_pr("caylent-solutions/git-repo", "branch", "title", "body", repo_path=tmp_path)
        assert url == "https://github.com/org/repo/pull/42"

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(1, "", "error msg")):
            with pytest.raises(RuntimeError, match="Failed to create PR"):
                judge.create_pr("caylent-solutions/git-repo", "branch", "title", "body", repo_path=tmp_path)

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/1\n", "")) as mock_gh:
            judge.create_pr("caylent-solutions/git-repo", "branch", "title", "body", repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("cwd") == tmp_path
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_uses_base_branch_from_yaml_config(self, tmp_path: Path) -> None:
        """create_pr passes --base <branch> when YAML config has a default_branch."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsJudge()
        runtime_config = RuntimeConfig(
            repos={"caylent-solutions/git-repo": RepoConfig(default_branch="main2")}
        )
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config),
            patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/1\n", "")) as mock_gh,
        ):
            judge.create_pr("caylent-solutions/git-repo", "feature-branch", "title", "body", repo_path=tmp_path)

        cmd_args, _ = mock_gh.call_args
        cmd = cmd_args[0]
        base_idx = cmd.index("--base")
        assert cmd[base_idx + 1] == "main2"

    def test_omits_base_branch_when_not_configured(self, tmp_path: Path) -> None:
        """create_pr omits --base when repo has no default_branch in YAML config."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsJudge()
        runtime_config = RuntimeConfig(
            repos={"caylent-solutions/git-repo": RepoConfig(default_branch=None)}
        )
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config),
            patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/1\n", "")) as mock_gh,
        ):
            judge.create_pr("caylent-solutions/git-repo", "feature-branch", "title", "body", repo_path=tmp_path)

        cmd_args, _ = mock_gh.call_args
        cmd = cmd_args[0]
        assert "--base" not in cmd


class TestMergePr:
    """Test merge_pr method."""

    def test_validates_repo(self) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="not allowed"):
            judge.merge_pr("evil/repo", 42)

    def test_merges_successfully(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "", "")):
            judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(1, "", "merge failed")):
            with pytest.raises(RuntimeError, match="Failed to merge PR"):
                judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("cwd") == tmp_path
        assert kwargs.get("repo") == "caylent-solutions/git-repo"


class TestCreateTag:
    """Test create_tag method."""

    def test_validates_repo(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="not allowed"):
            judge.create_tag("evil/repo", tmp_path, "v1.0", "Release")

    def test_creates_and_pushes_tag(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_git") as mock_git:
            judge.create_tag("caylent-solutions/git-repo", tmp_path, "v1.0.0", "Release 1.0")

        assert mock_git.call_count == 2
        calls = [c.args[0] for c in mock_git.call_args_list]
        assert calls[0] == ["tag", "-a", "v1.0.0", "-m", "Release 1.0"]
        assert calls[1] == ["push", "origin", "v1.0.0"]


class TestWaitForChecks:
    """Test wait_for_checks method."""

    def test_validates_repo(self) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="not allowed"):
            judge.wait_for_checks("evil/repo", 1)

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "All checks passed", "")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is True

    def test_returns_false_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(1, "", "checks failed")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is False

    def test_uses_custom_timeout(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.wait_for_checks("caylent-solutions/git-repo", 42, timeout=999, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("timeout") == 999

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("repo") == "caylent-solutions/git-repo"


class TestUpdateParentSubmoduleRef:
    """Test update_parent_submodule_ref method."""

    def test_validates_repo(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with pytest.raises(ValueError, match="not allowed"):
            judge.update_parent_submodule_ref("evil/repo", tmp_path, "msg")

    def test_calls_correct_git_commands(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_path = tmp_path / "git-repo"
        repo_path.mkdir()

        git_calls: list[tuple[list[str], Path]] = []

        def capture_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            git_calls.append((args, cwd))
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=capture_git),
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch("devbench.github.git_ops.WORKSPACE_ROOT", tmp_path),
        ):
            judge.update_parent_submodule_ref(
                "caylent-solutions/git-repo", repo_path, "update submodule"
            )

        assert len(git_calls) == 4
        assert git_calls[0] == (["checkout", "main"], repo_path)
        assert git_calls[1] == (["pull", "origin", "main"], repo_path)
        assert git_calls[2] == (["add", "git-repo"], tmp_path)
        assert git_calls[3] == (["commit", "-m", "update submodule"], tmp_path)

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_path = tmp_path / "git-repo"
        repo_path.mkdir()

        with (
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch.object(judge, "_git", side_effect=RuntimeError("checkout failed")),
            patch("devbench.github.git_ops.WORKSPACE_ROOT", tmp_path),
        ):
            with pytest.raises(RuntimeError, match="checkout failed"):
                judge.update_parent_submodule_ref(
                    "caylent-solutions/git-repo", repo_path, "msg"
                )


class TestGitHelper:
    """Test _git helper method."""

    def test_raises_runtime_error_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_run_command", return_value=(1, "", "fatal: error")):
            with pytest.raises(RuntimeError, match=r"git .* failed"):
                judge._git(["status"], tmp_path)

    def test_returns_tuple_on_success(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_run_command", return_value=(0, "output", "")):
            rc, stdout, stderr = judge._git(["status"], tmp_path)
        assert rc == 0
        assert stdout == "output"


class TestGhHelper:
    """Test _gh helper method."""

    def test_calls_subprocess_with_token(self) -> None:
        judge = GitOpsJudge()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch("devbench.github.git_ops.get_gh_token", return_value="test-token"):
            with patch("devbench.github.git_ops.subprocess.run", return_value=mock_result) as mock_run:
                rc, stdout, stderr = judge._gh(["pr", "list"])

        assert rc == 0
        assert stdout == "ok"
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["env"]["GH_TOKEN"] == "test-token"

    def test_appends_repo_flag_when_provided(self) -> None:
        judge = GitOpsJudge()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.github.git_ops.get_gh_token", return_value="tok"):
            with patch("devbench.github.git_ops.subprocess.run", return_value=mock_result) as mock_run:
                judge._gh(["pr", "create", "--title", "T"], repo="caylent-solutions/git-repo")

        cmd = mock_run.call_args.args[0]
        assert "--repo" in cmd
        assert "caylent-solutions/git-repo" in cmd

    def test_no_repo_flag_when_not_provided(self) -> None:
        judge = GitOpsJudge()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.github.git_ops.get_gh_token", return_value="tok"):
            with patch("devbench.github.git_ops.subprocess.run", return_value=mock_result) as mock_run:
                judge._gh(["pr", "list"])

        cmd = mock_run.call_args.args[0]
        assert "--repo" not in cmd
