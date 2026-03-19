"""Tests for judges.git_ops module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.config_loader import RepoConfig
from devbench.github.git_ops import GitOpsJudge
from devbench.judges.base import Verdict


def _make_repo_config(
    name: str = "caylent-solutions/git-repo",
    *,
    local_path: Path | None = None,
    default_branch: str | None = "main2",
) -> RepoConfig:
    """Factory for RepoConfig test fixtures with all required fields populated."""
    short_name = name.split("/", maxsplit=1)[1] if "/" in name else name
    path = local_path or Path("/tmp") / short_name
    return RepoConfig(
        name=name,
        short_name=short_name,
        local_path=path,
        default_branch=default_branch,
    )


class TestGitOpsInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = GitOpsJudge()
        assert judge.name == "git_ops"


class TestEvaluate:
    """Test the no-op evaluate method."""

    def test_returns_pass(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        result = judge.evaluate(tmp_path / "wu.md", _make_repo_config(local_path=tmp_path))
        assert result.verdict is Verdict.PASS
        assert "no-op" in result.reasoning



class TestEnsureBranch:
    """Test ensure_branch method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_git_ops_accepts_repo_config(self, tmp_path: Path) -> None:
        """AC-6: ensure_branch accepts a RepoConfig and uses repo_config.local_path."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            # Calling with a RepoConfig must not raise — this is AC-6
            judge.ensure_branch(repo_config, "feature/x")

        assert ["checkout", "feature/x"] not in git_calls  # already on branch

    def test_rejects_invalid_branch_name(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with pytest.raises(ValueError, match="Invalid branch name"):
            judge.ensure_branch(repo_config, "bad branch!")

    def test_noop_when_already_on_target_branch(self, tmp_path: Path) -> None:
        """No checkout or stash call is made when HEAD is already on the target branch."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.ensure_branch(repo_config, "feature/x")

        assert ["checkout", "feature/x"] not in git_calls
        assert ["checkout", "-b", "feature/x"] not in git_calls
        assert not any(a[0] == "stash" for a in git_calls)

    def test_checks_out_existing_branch_when_clean(self, tmp_path: Path) -> None:
        """``git checkout <branch>`` (no -b) is used when tree is clean and branch exists locally."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        # show-ref rc=0 → branch exists
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),
        ):
            judge.ensure_branch(repo_config, "feature/x")

        assert ["checkout", "feature/x"] in git_calls
        assert ["checkout", "-b", "feature/x"] not in git_calls
        assert not any(a[0] == "stash" for a in git_calls)

    def test_stashes_before_checkout_when_staged_changes(self, tmp_path: Path) -> None:
        """stash → checkout → stash pop when tree has staged changes."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, "M foo.py", "")  # staged changes
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),  # show-ref → exists
        ):
            judge.ensure_branch(repo_config, "feature/x")

        assert git_calls.index(["stash"]) < git_calls.index(["checkout", "feature/x"])
        assert git_calls.index(["checkout", "feature/x"]) < git_calls.index(["stash", "pop"])

    def test_stashes_before_checkout_when_unstaged_changes(self, tmp_path: Path) -> None:
        """stash → checkout → stash pop when tree has unstaged changes."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, " M foo.py", "")  # unstaged changes
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),  # show-ref → exists
        ):
            judge.ensure_branch(repo_config, "feature/x")

        assert ["stash"] in git_calls
        assert ["stash", "pop"] in git_calls

    def test_creates_branch_when_absent(self, tmp_path: Path) -> None:
        """``git checkout -b <branch> origin/<default>`` is used when branch does not exist locally."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(1, "", "")),  # show-ref → absent
        ):
            judge.ensure_branch(repo_config, "feature/x")

        assert ["checkout", "-b", "feature/x", "origin/main"] in git_calls
        assert ["checkout", "feature/x"] not in git_calls

    def test_ensure_branch_new_branch_uses_default_branch_as_base(self, tmp_path: Path) -> None:
        """AC-1: New branch is created from origin/<default_branch>, not current HEAD.

        Given: Branch does not exist locally and current HEAD is a feature branch
        When: ensure_branch is called for a new branch
        Then: git checkout -b <branch> origin/<default_branch> is called (not just -b <branch>)
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/prior-task", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(1, "", "")),  # show-ref → absent
        ):
            judge.ensure_branch(repo_config, "feature/new-task")

        assert ["checkout", "-b", "feature/new-task", "origin/main2"] in git_calls
        assert ["checkout", "-b", "feature/new-task"] not in git_calls

    def test_ensure_branch_new_branch_fetches_origin_first(self, tmp_path: Path) -> None:
        """AC-1: origin is fetched before creating a new branch to ensure origin/<default_branch> is current.

        Given: Branch does not exist locally
        When: ensure_branch is called
        Then: git fetch origin is called before git checkout -b
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main2", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(1, "", "")),  # show-ref → absent
        ):
            judge.ensure_branch(repo_config, "feature/new-task")

        fetch_idx = git_calls.index(["fetch", "origin"])
        checkout_idx = git_calls.index(["checkout", "-b", "feature/new-task", "origin/main2"])
        assert fetch_idx < checkout_idx

    def test_ensure_branch_existing_branch_no_fetch(self, tmp_path: Path) -> None:
        """AC-2: Existing branch path does not trigger a fetch.

        Given: Branch already exists locally
        When: ensure_branch is called
        Then: git fetch origin is NOT called (existing branch path unchanged)
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),  # show-ref → exists
        ):
            judge.ensure_branch(repo_config, "feature/x")

        assert ["fetch", "origin"] not in git_calls

    def test_ensure_branch_uses_configured_default_branch(self, tmp_path: Path) -> None:
        """AC-3: default_branch from RepoConfig is used to determine the base branch name.

        Given: repo_config.default_branch is 'develop'
        When: ensure_branch creates a new branch
        Then: origin/develop is used as the base, not origin/main
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="develop")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(1, "", "")),  # show-ref → absent
        ):
            judge.ensure_branch(repo_config, "feature/x")

        assert ["checkout", "-b", "feature/x", "origin/develop"] in git_calls

    def test_ensure_branch_raises_when_no_default_branch_configured(self, tmp_path: Path) -> None:
        """AC-3: Raises ValueError (fail-fast) when no default_branch is configured.

        Given: repo_config.default_branch is None
        When: ensure_branch attempts to create a new branch
        Then: ValueError is raised with a clear actionable message
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch=None)

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(1, "", "")),  # show-ref → absent
            pytest.raises(ValueError, match="No default_branch configured for repo"),
        ):
            judge.ensure_branch(repo_config, "feature/x")


class TestCommitAndPush:
    """Test commit_and_push method."""

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_git", side_effect=RuntimeError("git failed")):
            with pytest.raises(RuntimeError, match="git failed"):
                judge.commit_and_push(repo_config, "b", "m")

    def test_does_not_call_git_checkout(self, tmp_path: Path) -> None:
        """commit_and_push never calls git checkout — branch setup is ensure_branch's job."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            if args == ["status", "--porcelain"]:
                return (0, "M file.py\n", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.commit_and_push(repo_config, "feature/x", "msg")

        checkout_calls = [c for c in git_calls if c[0] == "checkout"]
        assert checkout_calls == [], "commit_and_push must not call git checkout"

    # ------------------------------------------------------------------
    # Happy path: changes present → commit and push
    # ------------------------------------------------------------------

    def test_commits_and_pushes_when_changes_present(self, tmp_path: Path) -> None:
        """Full commit + push sequence runs when the working tree has changes."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            if args == ["status", "--porcelain"]:
                return (0, "M src/foo.py\n", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.commit_and_push(repo_config, "feature/x", "commit msg")

        assert ["add", "-A"] in git_calls
        assert ["commit", "-m", "commit msg"] in git_calls
        assert ["push", "origin", "feature/x"] in git_calls

    # ------------------------------------------------------------------
    # Restart scenarios: nothing to commit
    # ------------------------------------------------------------------

    def test_nothing_to_commit_skips_commit_and_push_when_remote_up_to_date(
        self, tmp_path: Path
    ) -> None:
        """When working tree is clean and remote matches local HEAD, both commit and push are skipped."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "HEAD"]:
                return (0, "abc123\n", "")
            if args == ["rev-parse", "origin/feature/x"]:
                return (0, "abc123\n", "")  # same SHA → up to date
            return (0, "", "")

        # show-ref for origin/feature/x → rc=0 (remote exists)
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),
        ):
            judge.commit_and_push(repo_config, "feature/x", "msg")

        assert ["commit", "-m", "msg"] not in git_calls
        assert ["push", "origin", "feature/x"] not in git_calls

    def test_nothing_to_commit_pushes_when_remote_branch_absent(self, tmp_path: Path) -> None:
        """When working tree is clean and the remote branch does not exist, push runs."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        # rev-parse --verify origin/feature/x → rc=1 (absent)
        run_command_responses = iter([(1, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", side_effect=run_command_responses),
        ):
            judge.commit_and_push(repo_config, "feature/x", "msg")

        assert ["commit", "-m", "msg"] not in git_calls
        assert ["push", "origin", "feature/x"] in git_calls

    def test_nothing_to_commit_pushes_when_local_ahead_of_remote(self, tmp_path: Path) -> None:
        """When working tree is clean but local SHA differs from remote SHA, push runs."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "HEAD"]:
                return (0, "newsha\n", "")
            if args == ["rev-parse", "origin/feature/x"]:
                return (0, "oldsha\n", "")  # different → local is ahead
            return (0, "", "")

        # _run_command for rev-parse --verify origin/feature/x → rc=0 (remote exists)
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),
        ):
            judge.commit_and_push(repo_config, "feature/x", "msg")

        assert ["commit", "-m", "msg"] not in git_calls
        assert ["push", "origin", "feature/x"] in git_calls


class TestCreatePr:
    """Test create_pr method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_returns_pr_url(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(
            judge,
            "_gh",
            return_value=(0, "https://github.com/org/repo/pull/42\n", ""),
        ):
            url = judge.create_pr(repo_config, "branch", "title", "body")
        assert url == "https://github.com/org/repo/pull/42"

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(1, "", "error msg")):
            with pytest.raises(RuntimeError, match="Failed to create PR"):
                judge.create_pr(repo_config, "branch", "title", "body")

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        """AC-6: create_pr uses repo_config.local_path as cwd and repo_config.name as repo."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/1\n", "")) as mock_gh:
            judge.create_pr(repo_config, "branch", "title", "body")

        _, kwargs = mock_gh.call_args
        assert kwargs.get("cwd") == tmp_path
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_uses_base_branch_from_repo_config(self, tmp_path: Path) -> None:
        """create_pr passes --base <branch> when repo_config has a default_branch."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        with patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/1\n", "")) as mock_gh:
            judge.create_pr(repo_config, "feature-branch", "title", "body")

        cmd_args, _ = mock_gh.call_args
        cmd = cmd_args[0]
        base_idx = cmd.index("--base")
        assert cmd[base_idx + 1] == "main2"

    def test_omits_base_branch_when_not_configured(self, tmp_path: Path) -> None:
        """create_pr omits --base when repo_config has no default_branch."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch=None)
        with patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/1\n", "")) as mock_gh:
            judge.create_pr(repo_config, "feature-branch", "title", "body")

        cmd_args, _ = mock_gh.call_args
        cmd = cmd_args[0]
        assert "--base" not in cmd


class TestMergePr:
    """Test merge_pr method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_merges_successfully(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(0, "", "")):
            judge.merge_pr(repo_config, 42)

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(1, "", "merge failed")):
            with pytest.raises(RuntimeError, match="Failed to merge PR"):
                judge.merge_pr(repo_config, 42)

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        """AC-6: merge_pr uses repo_config.local_path as cwd and repo_config.name as repo."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.merge_pr(repo_config, 42)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("cwd") == tmp_path
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_git_ops_uses_per_repo_merge_strategy(self, tmp_path: Path) -> None:
        """
        AC-4: git_ops.merge_pr() uses the per-repo strategy, not the global constant.

        Given: get_repo_merge_strategy returns 'rebase' for the target repo
        When: merge_pr is called
        Then: _gh is called with the '--rebase' flag (per-repo strategy)
        """
        from devbench.config import MergeStrategy

        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with (
            patch("devbench.github.git_ops.get_repo_merge_strategy", return_value="rebase"),
            patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh,
        ):
            judge.merge_pr(repo_config, 42)

        cmd_args, _ = mock_gh.call_args
        cmd = cmd_args[0]
        assert MergeStrategy.REBASE.flag in cmd, (
            f"Expected '{MergeStrategy.REBASE.flag}' in gh command, got: {cmd}"
        )


class TestCreateTag:
    """Test create_tag method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_creates_and_pushes_tag(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_git") as mock_git:
            judge.create_tag(repo_config, "v1.0.0", "Release 1.0")

        assert mock_git.call_count == 2
        calls = [c.args[0] for c in mock_git.call_args_list]
        assert calls[0] == ["tag", "-a", "v1.0.0", "-m", "Release 1.0"]
        assert calls[1] == ["push", "origin", "v1.0.0"]


class TestWaitForChecks:
    """Test wait_for_checks method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_returns_true_when_no_checks_reported(self, tmp_path: Path) -> None:
        """AC-1: rc != 0 but stderr contains 'no checks reported' → True (no CI configured)."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(
            judge, "_gh", return_value=(1, "", "no checks reported on the 'main2' branch")
        ):
            assert judge.wait_for_checks(repo_config, 42) is True

    def test_returns_false_when_checks_failed(self, tmp_path: Path) -> None:
        """AC-2: rc != 0 and stderr does NOT contain 'no checks reported' → False (CI failed)."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(1, "", "checks failed")):
            assert judge.wait_for_checks(repo_config, 42) is False

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        """AC-3: rc == 0 → True (all checks passed)."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(0, "All checks passed", "")):
            assert judge.wait_for_checks(repo_config, 42) is True

    def test_uses_custom_timeout(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.wait_for_checks(repo_config, 42, timeout=999)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("timeout") == 999

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        """AC-6: wait_for_checks uses repo_config.name as repo."""
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.wait_for_checks(repo_config, 42)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("repo") == "caylent-solutions/git-repo"


class TestUpdateParentSubmoduleRef:
    """Test update_parent_submodule_ref method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_calls_correct_git_commands(self, tmp_path: Path) -> None:
        """AC-6: update_parent_submodule_ref uses repo_config.local_path."""
        judge = GitOpsJudge()
        repo_path = tmp_path / "git-repo"
        repo_path.mkdir()
        repo_config = _make_repo_config(local_path=repo_path, default_branch="main")

        git_calls: list[tuple[list[str], Path]] = []

        def capture_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            git_calls.append((args, cwd))
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=capture_git),
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch("devbench.github.git_ops.WORKSPACE_ROOT", tmp_path),
        ):
            judge.update_parent_submodule_ref(repo_config, "update submodule")

        assert len(git_calls) == 4
        assert git_calls[0] == (["checkout", "main"], repo_path)
        assert git_calls[1] == (["pull", "origin", "main"], repo_path)
        assert git_calls[2] == (["add", "git-repo"], tmp_path)
        assert git_calls[3] == (["commit", "-m", "update submodule"], tmp_path)

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        repo_path = tmp_path / "git-repo"
        repo_path.mkdir()
        repo_config = _make_repo_config(local_path=repo_path, default_branch="main")

        with (
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch.object(judge, "_git", side_effect=RuntimeError("checkout failed")),
            patch("devbench.github.git_ops.WORKSPACE_ROOT", tmp_path),
        ):
            with pytest.raises(RuntimeError, match="checkout failed"):
                judge.update_parent_submodule_ref(repo_config, "msg")


class TestIsCommittedAndPushed:
    """Tests for GitOpsJudge.is_committed_and_pushed.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_is_committed_and_pushed_false_when_dirty(self, tmp_path: Path) -> None:
        """AC-1: Returns False when working tree has staged changes.

        Given: Working tree has staged (dirty) changes
        When: is_committed_and_pushed is called
        Then: Returns False
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["status", "--porcelain"]:
                return (0, "M src/foo.py\n", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            result = judge.is_committed_and_pushed(repo_config=repo_config, branch="feature/x")

        assert result is False

    def test_is_committed_and_pushed_false_when_not_pushed(self, tmp_path: Path) -> None:
        """AC-2: Returns False when working tree is clean but branch not pushed.

        Given: Working tree is clean, remote branch does not exist (rc != 0)
        When: is_committed_and_pushed is called
        Then: Returns False
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            return (0, "", "")

        # rev-parse origin/<branch> → rc=1 means branch not pushed
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(1, "", "fatal: ambiguous argument")),
        ):
            result = judge.is_committed_and_pushed(repo_config=repo_config, branch="feature/x")

        assert result is False

    def test_is_committed_and_pushed_false_when_ahead_of_remote(self, tmp_path: Path) -> None:
        """AC-3: Returns False when local HEAD differs from origin/<branch>.

        Given: Working tree is clean, remote branch exists but SHA differs
        When: is_committed_and_pushed is called
        Then: Returns False
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "HEAD"]:
                return (0, "newsha\n", "")
            if args == ["rev-parse", "origin/feature/x"]:
                return (0, "oldsha\n", "")
            return (0, "", "")

        # rev-parse --verify origin/feature/x → rc=0 (remote exists)
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),
        ):
            result = judge.is_committed_and_pushed(repo_config=repo_config, branch="feature/x")

        assert result is False

    def test_is_committed_and_pushed_true_when_clean_and_synced(self, tmp_path: Path) -> None:
        """AC-4: Returns True when working tree is clean and local HEAD matches origin/<branch>.

        Given: Working tree is clean, remote branch exists, local SHA equals remote SHA
        When: is_committed_and_pushed is called
        Then: Returns True
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path)

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "HEAD"]:
                return (0, "abc123\n", "")
            if args == ["rev-parse", "origin/feature/x"]:
                return (0, "abc123\n", "")  # same SHA
            return (0, "", "")

        # rev-parse --verify origin/feature/x → rc=0 (remote exists)
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_run_command", return_value=(0, "", "")),
        ):
            result = judge.is_committed_and_pushed(repo_config=repo_config, branch="feature/x")

        assert result is True


class TestRebaseOntoDefault:
    """Test rebase_onto_default method.

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_rebase_onto_default_fetches_then_rebases_then_force_pushes(self, tmp_path: Path) -> None:
        """AC-4: rebase_onto_default fetches origin, rebases, then force-pushes with --force-with-lease.

        Given: A feature branch that is behind origin/<default_branch>
        When: rebase_onto_default is called
        Then: git fetch origin, git rebase origin/<default_branch>, git push --force-with-lease are called in order
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.rebase_onto_default(repo_config, "feature/x")

        assert ["fetch", "origin"] in git_calls
        assert ["rebase", "origin/main2"] in git_calls
        assert ["push", "--force-with-lease", "origin", "feature/x"] in git_calls

        fetch_idx = git_calls.index(["fetch", "origin"])
        rebase_idx = git_calls.index(["rebase", "origin/main2"])
        push_idx = git_calls.index(["push", "--force-with-lease", "origin", "feature/x"])
        assert fetch_idx < rebase_idx < push_idx

    def test_rebase_onto_default_raises_on_rebase_failure(self, tmp_path: Path) -> None:
        """AC-3: Rebase failure raises RuntimeError with a clear message.

        Given: git rebase fails (e.g., conflicts)
        When: rebase_onto_default is called
        Then: RuntimeError is raised with actionable message; no loop back to create_pr
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args[0] == "rebase":
                raise RuntimeError("git rebase origin/main2 failed (exit 1): conflict in foo.py")
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            pytest.raises(RuntimeError, match="rebase"),
        ):
            judge.rebase_onto_default(repo_config, "feature/x")

    def test_rebase_onto_default_uses_force_with_lease_not_force(self, tmp_path: Path) -> None:
        """AC-4: --force-with-lease is used, not --force.

        Given: rebase_onto_default runs successfully
        When: the force-push step executes
        Then: the push command uses --force-with-lease, not --force
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.rebase_onto_default(repo_config, "feature/x")

        push_calls = [c for c in git_calls if c[0] == "push"]
        assert len(push_calls) == 1
        assert "--force-with-lease" in push_calls[0]
        # More specifically: --force alone must NOT appear
        assert push_calls[0] != ["push", "--force", "origin", "feature/x"]

    def test_rebase_onto_default_uses_configured_default_branch(self, tmp_path: Path) -> None:
        """rebase_onto_default uses repo_config.default_branch to determine the rebase target.

        Given: repo_config.default_branch is 'develop'
        When: rebase_onto_default is called
        Then: git rebase origin/develop is executed
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="develop")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.rebase_onto_default(repo_config, "feature/x")

        assert ["rebase", "origin/develop"] in git_calls


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


class TestCheckoutDefaultBranch:
    """Tests for checkout_default_branch method (AC-1, AC-2, AC-3).

    AC-6: git_ops public methods accept a RepoConfig and no longer call validate_repo internally.
    """

    def test_checkout_default_branch_calls_git_checkout_and_pull(self, tmp_path: Path) -> None:
        """AC-1, AC-2: checkout_default_branch checks out the configured default branch and pulls.

        Given: repo_config has default_branch='main2'
        When: checkout_default_branch is called
        Then: git checkout main2 is called, then git pull origin main2
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.checkout_default_branch(repo_config)

        assert ["checkout", "main2"] in git_calls
        assert ["pull", "origin", "main2"] in git_calls
        checkout_idx = git_calls.index(["checkout", "main2"])
        pull_idx = git_calls.index(["pull", "origin", "main2"])
        assert checkout_idx < pull_idx

    def test_checkout_default_branch_raises_on_checkout_failure(self, tmp_path: Path) -> None:
        """AC-3: RuntimeError is raised when git checkout fails (fail-fast).

        Given: git checkout exits with non-zero code
        When: checkout_default_branch is called
        Then: RuntimeError is raised — no silent fallback
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")

        with (
            patch.object(
                judge,
                "_git",
                side_effect=RuntimeError("git checkout main2 failed (exit 1): error"),
            ),
            pytest.raises(RuntimeError, match="git checkout main2 failed"),
        ):
            judge.checkout_default_branch(repo_config)

    def test_checkout_default_branch_raises_on_pull_failure(self, tmp_path: Path) -> None:
        """AC-3: RuntimeError is raised when git pull fails (fail-fast).

        Given: git checkout succeeds but git pull fails
        When: checkout_default_branch is called
        Then: RuntimeError is raised — no silent fallback
        """
        judge = GitOpsJudge()
        repo_config = _make_repo_config(local_path=tmp_path, default_branch="main2")
        call_count = [0]

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            call_count[0] += 1
            if call_count[0] == 1:
                return (0, "", "")  # checkout succeeds
            raise RuntimeError("git pull origin main2 failed (exit 1): network error")

        with (
            patch.object(judge, "_git", side_effect=stub),
            pytest.raises(RuntimeError, match="git pull origin main2 failed"),
        ):
            judge.checkout_default_branch(repo_config)
