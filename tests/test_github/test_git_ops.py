"""Tests for github.git_ops module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.github.git_ops import ConflictingPRError, GitOpsService


class TestGitOpsInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = GitOpsService()
        assert judge.name == "git_ops"


class TestCommitAndPush:
    """Test commit_and_push method."""

    def test_validates_repo(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.commit_and_push("evil/repo", tmp_path, "branch", "msg")

    def test_rejects_invalid_branch_name(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="Invalid branch name"):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "bad branch!", "msg")

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git", side_effect=RuntimeError("git failed")):
            with pytest.raises(RuntimeError, match="git failed"):
                judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "b", "m")

    # ------------------------------------------------------------------
    # Happy path: changes present → commit and push
    # ------------------------------------------------------------------

    def test_commits_and_pushes_when_changes_present(self, tmp_path: Path) -> None:
        """Full commit + push sequence runs when the working tree has changes."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "M src/foo.py\n", "")
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "commit msg")

        assert ["add", "-A"] in git_calls
        assert ["commit", "-m", "commit msg"] in git_calls
        assert ["push", "origin", "feature/x"] in git_calls

    # ------------------------------------------------------------------
    # Restart scenarios: nothing to commit
    # ------------------------------------------------------------------

    def test_nothing_to_commit_skips_commit_and_push_when_remote_up_to_date(self, tmp_path: Path) -> None:
        """When working tree is clean and remote matches local HEAD, both commit and push are skipped."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "HEAD"]:
                return (0, "abc123\n", "")
            if args == ["rev-parse", "origin/feature/x"]:
                return (0, "abc123\n", "")  # same SHA → up to date
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        # show-ref for origin/feature/x → rc=0 (remote exists)
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", return_value=(0, "", "")),
        ):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")

        assert ["commit", "-m", "msg"] not in git_calls
        assert ["push", "origin", "feature/x"] not in git_calls

    def test_nothing_to_commit_pushes_when_remote_branch_absent(self, tmp_path: Path) -> None:
        """When working tree is clean and the remote branch does not exist, push runs."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        # Only run_command call = rev-parse --verify origin/feature/x → rc=1 (absent).
        run_command_responses = iter([(1, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")

        assert ["commit", "-m", "msg"] not in git_calls
        assert ["push", "origin", "feature/x"] in git_calls

    def test_nothing_to_commit_pushes_when_local_ahead_of_remote(self, tmp_path: Path) -> None:
        """When working tree is clean but local SHA differs from remote SHA, push runs."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "", "")  # clean
            if args == ["rev-parse", "HEAD"]:
                return (0, "newsha\n", "")
            if args == ["rev-parse", "origin/feature/x"]:
                return (0, "oldsha\n", "")  # different → local is ahead
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        # run_command for rev-parse --verify origin/feature/x → rc=0 (remote exists)
        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", return_value=(0, "", "")),
        ):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")

        assert ["commit", "-m", "msg"] not in git_calls
        assert ["push", "origin", "feature/x"] in git_calls

    # ------------------------------------------------------------------
    # AC-2: commit_and_push must not call git checkout
    # ------------------------------------------------------------------

    def test_commit_and_push_does_not_call_git_checkout(self, tmp_path: Path) -> None:
        """commit_and_push never calls git checkout -- branch setup is ensure_branch's job. AC-2"""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "M file.py\n", "")  # has changes → commit path
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")

        checkout_calls = [c for c in git_calls if c[0] == "checkout"]
        assert checkout_calls == [], "commit_and_push must not call git checkout"


@pytest.mark.unit
class TestEnsureBranch:
    """Tests for GitOpsService.ensure_branch method (T1 AC-3 through AC-10)."""

    def test_ensure_branch_noop_when_already_on_branch(self, tmp_path: Path) -> None:
        """
        Given: current branch is the target branch
        When: ensure_branch is called
        Then: no stash, checkout, or pop is performed (AC-3)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")

        assert ["stash"] not in git_calls
        assert not any(c[0] == "checkout" for c in git_calls)
        assert ["stash", "pop"] not in git_calls

    def test_ensure_branch_checks_out_existing_branch_when_clean(self, tmp_path: Path) -> None:
        """
        Given: different branch, clean tree, target branch exists locally
        When: ensure_branch is called
        Then: checkout (no -b) is performed without stash (AC-4)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command calls: status → clean, show-ref → branch exists
        run_command_responses = iter([(0, "", ""), (0, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")

        assert ["checkout", "feature/x"] in git_calls
        assert ["checkout", "-b", "feature/x"] not in git_calls
        assert ["stash"] not in git_calls
        assert ["stash", "pop"] not in git_calls

    def test_ensure_branch_stashes_before_checkout_when_staged_changes(self, tmp_path: Path) -> None:
        """
        Given: different branch, staged changes in tree, target branch exists
        When: ensure_branch is called
        Then: stash → checkout → stash pop (AC-5)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command: status → staged changes, show-ref → branch exists
        run_command_responses = iter([(0, "M  file.py\n", ""), (0, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")

        assert ["stash"] in git_calls
        assert ["checkout", "feature/x"] in git_calls
        assert ["stash", "pop"] in git_calls
        # Verify order: stash before checkout, pop after
        stash_idx = git_calls.index(["stash"])
        checkout_idx = git_calls.index(["checkout", "feature/x"])
        pop_idx = git_calls.index(["stash", "pop"])
        assert stash_idx < checkout_idx < pop_idx

    def test_ensure_branch_stashes_before_checkout_when_unstaged_changes(self, tmp_path: Path) -> None:
        """
        Given: different branch, unstaged changes in tree, target branch exists
        When: ensure_branch is called
        Then: stash → checkout → stash pop (AC-6)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command: status → unstaged changes, show-ref → branch exists
        run_command_responses = iter([(0, " M file.py\n", ""), (0, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")

        assert ["stash"] in git_calls
        assert ["stash", "pop"] in git_calls

    def test_ensure_branch_creates_branch_when_absent(self, tmp_path: Path) -> None:
        """
        Given: different branch, clean tree, target branch does not exist locally
        When: ensure_branch is called
        Then: fetch origin is run, checkout -b uses origin/<default_branch> as base (AC-3/AC-7)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command: status → clean, show-ref → branch absent
        run_command_responses = iter([(0, "", ""), (1, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "new-branch")

        assert ["fetch", "origin"] in git_calls
        assert ["checkout", "-b", "new-branch", "origin/main"] in git_calls
        assert ["checkout", "new-branch"] not in git_calls

    def test_ensure_branch_validates_branch_name(self, tmp_path: Path) -> None:
        """
        Given: an invalid branch name
        When: ensure_branch is called
        Then: ValueError is raised with 'Invalid branch name' (AC-8)
        """
        judge = GitOpsService()
        with pytest.raises(ValueError, match="Invalid branch name"):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "bad branch!")

    def test_ensure_branch_validates_repo(self, tmp_path: Path) -> None:
        """
        Given: a repo not in the allow-list
        When: ensure_branch is called
        Then: ValueError is raised with 'not allowed' (AC-9)
        """
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.ensure_branch("evil/repo", tmp_path, "feature/x")

    def test_ensure_branch_raises_on_dirty_status_error(self, tmp_path: Path) -> None:
        """
        Given: git status --porcelain exits non-zero (genuine git error)
        When: ensure_branch is called
        Then: RuntimeError is raised (not silently treated as clean) (AC-10)
        """
        judge = GitOpsService()

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command: status → non-zero exit (git error)
        run_command_responses = iter([(1, "", "fatal: not a git repository")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            with pytest.raises(RuntimeError, match="git status"):
                judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")


class TestLocalOnlyMode:
    """Tests for git_ops.local_only=true behavior."""

    def test_ensure_branch_skips_fetch_and_uses_local_default_when_local_only(self, tmp_path: Path) -> None:
        """
        Given: git_ops.local_only=true, branch absent, clean tree
        When: ensure_branch is called
        Then: NO 'fetch origin' is run, and 'checkout -b <branch> refs/heads/<default>' is used
              (the local default ref, not origin/<default>).
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        run_command_responses = iter([(0, "", ""), (1, "", "")])

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
            patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.git_ops.local_only = True
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "new-branch")

        assert ["fetch", "origin"] not in git_calls, (
            "ensure_branch must NOT call 'git fetch origin' under local_only=true"
        )
        assert ["checkout", "-b", "new-branch", "refs/heads/main"] in git_calls
        # Sanity: the origin-based form must NOT be used.
        assert ["checkout", "-b", "new-branch", "origin/main"] not in git_calls

    def test_ensure_branch_noop_when_already_on_branch_local_only(self, tmp_path: Path) -> None:
        """ensure_branch is still a no-op when HEAD is already on the target branch under local_only."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.git_ops.local_only = True
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")

        # Only the rev-parse call; no fetch, no checkout.
        assert git_calls == [["rev-parse", "--abbrev-ref", "HEAD"]]

    def test_commit_and_push_raises_when_local_only(self, tmp_path: Path) -> None:
        """commit_and_push must refuse to run under local_only=true (operators should use commit_local)."""
        judge = GitOpsService()
        with patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.git_ops.local_only = True
            with pytest.raises(RuntimeError, match=r"commit_and_push is not available .*local_only"):
                judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")

    def test_create_tag_raises_when_local_only(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.git_ops.local_only = True
            with pytest.raises(RuntimeError, match=r"create_tag is not available .*local_only"):
                judge.create_tag("caylent-solutions/git-repo", tmp_path, "v1.0", "msg")

    def test_checkout_default_branch_raises_when_local_only(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.git_ops.local_only = True
            with pytest.raises(RuntimeError, match=r"checkout_default_branch is not available .*local_only"):
                judge.checkout_default_branch("caylent-solutions/git-repo", tmp_path)

    def test_rebase_and_force_push_raises_when_local_only(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.git_ops.local_only = True
            with pytest.raises(RuntimeError, match=r"rebase_and_force_push is not available .*local_only"):
                judge.rebase_and_force_push("caylent-solutions/git-repo", tmp_path, "feature/x")

    def test_get_default_branch_refuses_origin_head_fallback_when_local_only(self, tmp_path: Path) -> None:
        """When local_only is true and no YAML default_branch is configured, fail fast
        instead of falling back to 'git rev-parse origin/HEAD'."""
        judge = GitOpsService()
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG") as mock_cfg,
            patch("devbench.github.git_ops.get_configured_default_branch", return_value=""),
        ):
            mock_cfg.git_ops.local_only = True
            with pytest.raises(RuntimeError, match=r"local_only is true but repo .* has no default_branch"):
                judge._get_default_branch(tmp_path, repo="caylent-solutions/git-repo")


class TestCreatePr:
    """Test create_pr method."""

    def test_validates_repo(self) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.create_pr("evil/repo", "branch", "title", "body")

    # _gh is invoked twice now: once for `pr list --head` (find_open_pr) and
    # once for `pr create`. The list-call returns "[]" (no existing PR) so the
    # create path runs.
    _LIST_NO_EXISTING = (0, "[]", "")

    def test_returns_pr_url(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(
            judge,
            "_gh",
            side_effect=[
                self._LIST_NO_EXISTING,
                (0, "https://github.com/org/repo/pull/42\n", ""),
            ],
        ):
            url = judge.create_pr("caylent-solutions/git-repo", "branch", "title", "body", repo_path=tmp_path)
        assert url == "https://github.com/org/repo/pull/42"

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", side_effect=[self._LIST_NO_EXISTING, (1, "", "error msg")]):
            with pytest.raises(RuntimeError, match="Failed to create PR"):
                judge.create_pr("caylent-solutions/git-repo", "branch", "title", "body", repo_path=tmp_path)

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(
            judge,
            "_gh",
            side_effect=[self._LIST_NO_EXISTING, (0, "https://github.com/org/repo/pull/1\n", "")],
        ) as mock_gh:
            judge.create_pr("caylent-solutions/git-repo", "branch", "title", "body", repo_path=tmp_path)

        # Inspect the second call (the actual create); list-call kwargs already validated by find_open_pr coverage.
        _, create_kwargs = mock_gh.call_args_list[1]
        assert create_kwargs.get("cwd") == tmp_path
        assert create_kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_uses_base_branch_from_yaml_config(self, tmp_path: Path) -> None:
        """create_pr passes --base <branch> when YAML config has a default_branch."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsService()
        runtime_config = RuntimeConfig(repos={"caylent-solutions/git-repo": RepoConfig(default_branch="main2")})
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config),
            patch.object(
                judge,
                "_gh",
                side_effect=[self._LIST_NO_EXISTING, (0, "https://github.com/org/repo/pull/1\n", "")],
            ) as mock_gh,
        ):
            judge.create_pr("caylent-solutions/git-repo", "feature-branch", "title", "body", repo_path=tmp_path)

        cmd_args, _ = mock_gh.call_args_list[1]
        cmd = cmd_args[0]
        base_idx = cmd.index("--base")
        assert cmd[base_idx + 1] == "main2"

    def test_omits_base_branch_when_not_configured(self, tmp_path: Path) -> None:
        """create_pr omits --base when repo has no default_branch in YAML config."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsService()
        runtime_config = RuntimeConfig(repos={"caylent-solutions/git-repo": RepoConfig(default_branch=None)})
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config),
            patch.object(
                judge,
                "_gh",
                side_effect=[self._LIST_NO_EXISTING, (0, "https://github.com/org/repo/pull/1\n", "")],
            ) as mock_gh,
        ):
            judge.create_pr("caylent-solutions/git-repo", "feature-branch", "title", "body", repo_path=tmp_path)

        cmd_args, _ = mock_gh.call_args_list[1]
        cmd = cmd_args[0]
        assert "--base" not in cmd


class TestCreatePrExistingPrReuse:
    """Issue #129 regression: a second git-ops invocation on the same branch
    must reuse a pre-existing open PR instead of treating the duplicate
    ``gh pr create`` call as a fatal error.

    Bug: cmd_git_ops calls ``gh pr create`` without first checking whether an
    open PR already exists for the branch. When the executor pushed a fix
    commit (REFACTOR after REVIEW_PASS, or a pr_review_resolution bot fix),
    the second create attempt failed with "a pull request already exists for
    this branch" and devbench transitioned the task to BLOCKED -- even though
    the fix commit was already on the PR.
    """

    def test_find_open_pr_returns_url_when_open_pr_exists(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(
            judge,
            "_gh",
            return_value=(0, '[{"url":"https://github.com/org/repo/pull/20"}]', ""),
        ) as mock_gh:
            url = judge.find_open_pr("caylent-solutions/git-repo", "branch", repo_path=tmp_path)
        assert url == "https://github.com/org/repo/pull/20"
        cmd_args, _ = mock_gh.call_args
        assert "pr" in cmd_args[0]
        assert "list" in cmd_args[0]
        assert "--head" in cmd_args[0]
        assert "branch" in cmd_args[0]
        assert "--state" in cmd_args[0]
        assert "open" in cmd_args[0]

    def test_find_open_pr_returns_none_when_no_open_pr(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "[]", "")):
            url = judge.find_open_pr("caylent-solutions/git-repo", "branch", repo_path=tmp_path)
        assert url is None

    def test_find_open_pr_returns_none_on_gh_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "gh: not authenticated")):
            url = judge.find_open_pr("caylent-solutions/git-repo", "branch", repo_path=tmp_path)
        assert url is None

    def test_find_open_pr_returns_none_on_malformed_json(self, tmp_path: Path) -> None:
        """Defensive: future gh release that changes output format must not crash devbench."""
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "<html>", "")):
            url = judge.find_open_pr("caylent-solutions/git-repo", "branch", repo_path=tmp_path)
        assert url is None

    def test_create_pr_reuses_existing_open_pr_url(self, tmp_path: Path) -> None:
        """Core regression: create_pr returns the existing URL and never invokes
        ``gh pr create`` when an open PR is already on the branch."""
        judge = GitOpsService()
        list_response = (0, '[{"url":"https://github.com/org/repo/pull/20"}]', "")
        with patch.object(judge, "_gh", side_effect=[list_response]) as mock_gh:
            url = judge.create_pr("caylent-solutions/git-repo", "feature-branch", "title", "body", repo_path=tmp_path)
        assert url == "https://github.com/org/repo/pull/20"
        # Exactly one _gh call (the list call); the create call must not run.
        assert mock_gh.call_count == 1
        cmd_args, _ = mock_gh.call_args_list[0]
        assert "create" not in cmd_args[0]

    def test_create_pr_falls_through_to_create_when_no_existing(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(
            judge,
            "_gh",
            side_effect=[(0, "[]", ""), (0, "https://github.com/org/repo/pull/99\n", "")],
        ) as mock_gh:
            url = judge.create_pr("caylent-solutions/git-repo", "feature-branch", "title", "body", repo_path=tmp_path)
        assert url == "https://github.com/org/repo/pull/99"
        assert mock_gh.call_count == 2
        # Second call must be the actual create.
        create_cmd_args, _ = mock_gh.call_args_list[1]
        assert create_cmd_args[0][0:2] == ["pr", "create"]


class TestMergePr:
    """Test merge_pr method."""

    def test_validates_repo(self) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.merge_pr("evil/repo", 42)

    def test_merges_successfully(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "", "")):
            judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "merge failed")):
            with pytest.raises(RuntimeError, match="Failed to merge PR"):
                judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("cwd") == tmp_path
        assert kwargs.get("repo") == "caylent-solutions/git-repo"


class TestCreateTag:
    """Test create_tag method."""

    def test_validates_repo(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.create_tag("evil/repo", tmp_path, "v1.0", "Release")

    def test_creates_and_pushes_tag(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git") as mock_git:
            judge.create_tag("caylent-solutions/git-repo", tmp_path, "v1.0.0", "Release 1.0")

        assert mock_git.call_count == 2
        calls = [c.args[0] for c in mock_git.call_args_list]
        assert calls[0] == ["tag", "-a", "v1.0.0", "-m", "Release 1.0"]
        assert calls[1] == ["push", "origin", "v1.0.0"]


class TestWaitForChecks:
    """Test wait_for_checks method."""

    def test_validates_repo(self) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.wait_for_checks("evil/repo", 1)

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "All checks passed", "")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is True

    def test_returns_false_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "checks failed")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is False

    def test_returns_true_when_no_checks_reported(self, tmp_path: Path) -> None:
        """Returns True when gh exits non-zero with 'no checks reported' (no CI configured). AC-1"""
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "no checks reported")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is True

    def test_uses_custom_timeout(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.wait_for_checks("caylent-solutions/git-repo", 42, timeout=999, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("timeout") == 999

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_no_checks_with_workflow_files_retries_then_succeeds(self, tmp_path: Path) -> None:
        """Issue #114: workflow exists but Actions has not enqueued yet.

        Mock `gh pr checks` to return "no checks reported" the first call,
        then a clean exit on the second. The retry loop should bridge the
        gap and return True without merging blind.
        """
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text("on: push\n")
        responses = [(1, "", "no checks reported"), (0, "All checks passed", "")]
        with (
            patch.object(judge, "_gh", side_effect=responses),
            patch.object(git_ops_mod, "CHECK_REGISTRATION_DELAY_SECONDS", 0),
        ):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is True

    def test_no_checks_with_workflow_files_retry_exhausted_returns_false(self, tmp_path: Path) -> None:
        """Issue #114: workflow files exist but every retry returns 'no checks reported'.

        After CHECK_REGISTRATION_RETRIES attempts, refuse the merge --
        no warn-and-pass fallback (CLAUDE.md no-fallback rule).
        """
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text("on: push\n")
        with (
            patch.object(judge, "_gh", return_value=(1, "", "no checks reported")),
            patch.object(git_ops_mod, "CHECK_REGISTRATION_DELAY_SECONDS", 0),
            patch.object(git_ops_mod, "CHECK_REGISTRATION_RETRIES", 2),
        ):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path) is False


class TestListWorkflowFiles:
    """Phase B3 helper: glob `.github/workflows/*.y[a]ml` under repo_path."""

    def test_returns_empty_when_repo_path_is_none(self) -> None:
        from devbench.github.git_ops import _list_workflow_files

        assert _list_workflow_files(None) == []

    def test_returns_empty_when_workflows_dir_absent(self, tmp_path: Path) -> None:
        from devbench.github.git_ops import _list_workflow_files

        assert _list_workflow_files(tmp_path) == []

    def test_returns_yml_and_yaml_files_sorted(self, tmp_path: Path) -> None:
        from devbench.github.git_ops import _list_workflow_files

        wd = tmp_path / ".github" / "workflows"
        wd.mkdir(parents=True)
        (wd / "release.yaml").write_text("")
        (wd / "ci.yml").write_text("")
        (wd / "README.md").write_text("not a workflow")
        result = _list_workflow_files(tmp_path)
        names = [p.name for p in result]
        assert names == ["ci.yml", "release.yaml"]


class TestUpdateParentSubmoduleRef:
    """Test update_parent_submodule_ref method."""

    def test_validates_repo(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.update_parent_submodule_ref("evil/repo", tmp_path, "msg")

    def test_calls_correct_git_commands(self, tmp_path: Path) -> None:
        judge = GitOpsService()
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
            judge.update_parent_submodule_ref("caylent-solutions/git-repo", repo_path, "update submodule")

        assert len(git_calls) == 4
        assert git_calls[0] == (["checkout", "main"], repo_path)
        assert git_calls[1] == (["pull", "origin", "main"], repo_path)
        assert git_calls[2] == (["add", "git-repo"], tmp_path)
        assert git_calls[3] == (["commit", "-m", "update submodule"], tmp_path)

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        repo_path = tmp_path / "git-repo"
        repo_path.mkdir()

        with (
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch.object(judge, "_git", side_effect=RuntimeError("checkout failed")),
            patch("devbench.github.git_ops.WORKSPACE_ROOT", tmp_path),
        ):
            with pytest.raises(RuntimeError, match="checkout failed"):
                judge.update_parent_submodule_ref("caylent-solutions/git-repo", repo_path, "msg")


class TestGitHelper:
    """Test _git helper method."""

    def test_raises_runtime_error_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch("devbench.github.git_ops.run_command", return_value=(1, "", "fatal: error")):
            with pytest.raises(RuntimeError, match=r"git .* failed"):
                judge._git(["status"], tmp_path)

    def test_returns_tuple_on_success(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch("devbench.github.git_ops.run_command", return_value=(0, "output", "")):
            rc, stdout, stderr = judge._git(["status"], tmp_path)
        assert rc == 0
        assert stdout == "output"


class TestGhHelper:
    """Test _gh helper method."""

    def test_calls_subprocess_with_token(self) -> None:
        judge = GitOpsService()
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
        judge = GitOpsService()
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
        judge = GitOpsService()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.github.git_ops.get_gh_token", return_value="tok"):
            with patch("devbench.github.git_ops.subprocess.run", return_value=mock_result) as mock_run:
                judge._gh(["pr", "list"])

        cmd = mock_run.call_args.args[0]
        assert "--repo" not in cmd


@pytest.mark.unit
class TestCheckoutDefaultBranch:
    """Tests for GitOpsService.checkout_default_branch (AC-2)."""

    def test_checkout_default_branch_runs_checkout_and_pull(self, tmp_path: Path) -> None:
        """
        Given: a repo with a default branch of 'main'
        When: checkout_default_branch is called
        Then: git checkout <default_branch> and git pull origin <default_branch> are called
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def capture_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=capture_git),
            patch.object(judge, "_get_default_branch", return_value="main"),
        ):
            judge.checkout_default_branch("caylent-solutions/git-repo", tmp_path)

        assert ["checkout", "main"] in git_calls
        assert ["pull", "origin", "main"] in git_calls
        # Verify order: checkout before pull
        checkout_idx = git_calls.index(["checkout", "main"])
        pull_idx = git_calls.index(["pull", "origin", "main"])
        assert checkout_idx < pull_idx

    def test_update_parent_submodule_ref_calls_checkout_default_branch(self, tmp_path: Path) -> None:
        """
        Given: update_parent_submodule_ref is called
        When: the method executes
        Then: checkout_default_branch is called (not duplicate git commands inline)
        """
        judge = GitOpsService()
        repo_path = tmp_path / "git-repo"
        repo_path.mkdir()

        checkout_default_branch_calls: list[tuple[str, Path]] = []

        def capture_checkout(repo: str, rp: Path) -> None:
            checkout_default_branch_calls.append((repo, rp))

        with (
            patch.object(judge, "checkout_default_branch", side_effect=capture_checkout),
            patch.object(judge, "_git", return_value=(0, "", "")),
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch("devbench.github.git_ops.WORKSPACE_ROOT", tmp_path),
        ):
            judge.update_parent_submodule_ref("caylent-solutions/git-repo", repo_path, "update submodule")

        assert len(checkout_default_branch_calls) == 1
        assert checkout_default_branch_calls[0] == ("caylent-solutions/git-repo", repo_path)


@pytest.mark.unit
class TestEnsureBranchNewBranchBase:
    """Tests for ensure_branch new-branch base on origin/<default_branch> (AC-3, AC-4)."""

    def test_ensure_branch_new_branch_fetches_and_bases_on_origin(self, tmp_path: Path) -> None:
        """
        Given: target branch does not exist locally, clean tree
        When: ensure_branch is called
        Then: fetch origin is run and checkout -b uses origin/<default_branch> as base (AC-3)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def capture_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command: status → clean, show-ref → branch absent
        run_command_responses = iter([(0, "", ""), (1, "", "")])

        with (
            patch.object(judge, "_git", side_effect=capture_git),
            patch.object(judge, "_get_default_branch", return_value="main"),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "new-branch")

        assert ["fetch", "origin"] in git_calls
        assert ["checkout", "-b", "new-branch", "origin/main"] in git_calls
        # Plain checkout -b without the base must NOT appear
        assert ["checkout", "-b", "new-branch"] not in git_calls

    def test_ensure_branch_existing_branch_no_fetch(self, tmp_path: Path) -> None:
        """
        Given: target branch already exists locally, clean tree
        When: ensure_branch is called
        Then: fetch is NOT run, just git checkout <branch> (AC-4)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def capture_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main", "")
            return (0, "", "")

        # run_command: status → clean, show-ref → branch exists
        run_command_responses = iter([(0, "", ""), (0, "", "")])

        with (
            patch.object(judge, "_git", side_effect=capture_git),
            patch("devbench.github.git_ops.run_command", side_effect=run_command_responses),
        ):
            judge.ensure_branch("caylent-solutions/git-repo", tmp_path, "feature/x")

        assert ["fetch", "origin"] not in git_calls
        assert ["checkout", "feature/x"] in git_calls


@pytest.mark.unit
class TestConflictingPRError:
    """Tests for ConflictingPRError exception and merge_pr raising it (AC-5)."""

    def test_merge_pr_raises_conflicting_pr_error_on_conflict(self, tmp_path: Path) -> None:
        """
        Given: gh pr merge returns non-zero with CONFLICTING in stderr
        When: merge_pr is called
        Then: ConflictingPRError is raised (AC-5)
        """
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "GraphQL: CONFLICTING merge state")):
            with pytest.raises(ConflictingPRError):
                judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_merge_pr_raises_runtime_error_on_non_conflict_failure(self, tmp_path: Path) -> None:
        """
        Given: gh pr merge returns non-zero without CONFLICTING in stderr
        When: merge_pr is called
        Then: RuntimeError (not ConflictingPRError) is raised
        """
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "some other error")):
            with pytest.raises(RuntimeError) as exc_info:
                judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)
        # Must not be a ConflictingPRError for generic failures
        assert type(exc_info.value) is RuntimeError


@pytest.mark.unit
class TestRebaseAndForcePush:
    """Tests for GitOpsService.rebase_and_force_push (AC-8)."""

    def test_rebase_and_force_push_runs_correct_git_commands(self, tmp_path: Path) -> None:
        """
        Given: a repo with a default branch of 'main'
        When: rebase_and_force_push is called for 'feature/x'
        Then: fetch origin, rebase origin/main, push --force-with-lease origin feature/x (AC-8)
        """
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def capture_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=capture_git),
            patch.object(judge, "_get_default_branch", return_value="main"),
        ):
            judge.rebase_and_force_push("caylent-solutions/git-repo", tmp_path, "feature/x")

        assert ["fetch", "origin"] in git_calls
        assert ["rebase", "origin/main"] in git_calls
        assert ["push", "--force-with-lease", "origin", "feature/x"] in git_calls
        # Verify order
        fetch_idx = git_calls.index(["fetch", "origin"])
        rebase_idx = git_calls.index(["rebase", "origin/main"])
        push_idx = git_calls.index(["push", "--force-with-lease", "origin", "feature/x"])
        assert fetch_idx < rebase_idx < push_idx


class TestCommitLocal:
    """Test commit_local method."""

    def test_commit_local_stages_and_commits(self, tmp_path: Path) -> None:
        """commit_local stages files and commits when there are changes."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "M src/foo.py\n", "")
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.commit_local(
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
                "local commit",
            )

        assert ["add", "-A"] in git_calls
        assert ["status", "--porcelain"] in git_calls
        assert ["commit", "-m", "local commit"] in git_calls
        # Verify push was NOT called (commit_local is local only)
        push_calls = [c for c in git_calls if c[0] == "push"]
        assert push_calls == []

    def test_commit_local_skips_when_nothing_staged(self, tmp_path: Path) -> None:
        """commit_local skips commit when working tree is clean after staging."""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["status", "--porcelain"]:
                return (0, "", "")
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with patch.object(judge, "_git", side_effect=stub):
            judge.commit_local(
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
                "local commit",
            )

        assert ["add", "-A"] in git_calls
        assert ["status", "--porcelain"] in git_calls
        # Commit should NOT have been called
        commit_calls = [c for c in git_calls if c[0] == "commit"]
        assert commit_calls == []

    def test_commit_local_rejects_invalid_branch(self, tmp_path: Path) -> None:
        """commit_local raises ValueError for invalid branch names."""
        judge = GitOpsService()
        with pytest.raises(ValueError, match="Invalid branch name"):
            judge.commit_local("caylent-solutions/git-repo", tmp_path, "bad branch!", "msg")


class TestAssertOnBranch:
    """Branch verification before any commit path runs."""

    def test_passes_when_head_matches(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git", return_value=(0, "feature/x\n", "")):
            judge.assert_on_branch(tmp_path, "feature/x")  # no raise

    def test_strips_trailing_newline(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git", return_value=(0, "feature/x", "")):
            judge.assert_on_branch(tmp_path, "feature/x")

    def test_raises_on_wrong_branch(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git", return_value=(0, "main\n", "")):
            with pytest.raises(
                RuntimeError,
                match=r"Branch assertion failed.*expected 'feature/x'.*HEAD is on 'main'",
            ):
                judge.assert_on_branch(tmp_path, "feature/x")

    def test_raises_on_rev_parse_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git", return_value=(128, "", "fatal: not a git repository")):
            with pytest.raises(RuntimeError, match="git rev-parse --abbrev-ref HEAD failed"):
                judge.assert_on_branch(tmp_path, "feature/x")


class TestCommitMethodsRejectWrongBranch:
    """commit_local + commit_and_push must abort when HEAD is on a different branch."""

    def test_commit_local_rejects_when_head_drifted(self, tmp_path: Path) -> None:
        judge = GitOpsService()

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "backlog/wrong-branch\n", "")
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            pytest.raises(RuntimeError, match="HEAD is on 'backlog/wrong-branch'"),
        ):
            judge.commit_local("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")

    def test_commit_and_push_rejects_when_head_drifted(self, tmp_path: Path) -> None:
        judge = GitOpsService()

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "main\n", "")
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            pytest.raises(RuntimeError, match="HEAD is on 'main'"),
        ):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg")


class TestGetDefaultBranch:
    """Test _get_default_branch fallback logic."""

    def test_returns_configured_branch_when_available(self, tmp_path: Path) -> None:
        """Lines 459-462: returns YAML-configured branch when available."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsService()
        runtime_config = RuntimeConfig(repos={"caylent-solutions/git-repo": RepoConfig(default_branch="main2")})
        with patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config):
            result = judge._get_default_branch(tmp_path, repo="caylent-solutions/git-repo")
        assert result == "main2"

    def test_falls_back_to_git_when_no_config(self, tmp_path: Path) -> None:
        """Lines 464-473: falls back to git rev-parse when no YAML config."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsService()
        runtime_config = RuntimeConfig(repos={"caylent-solutions/git-repo": RepoConfig(default_branch=None)})
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config),
            patch("devbench.github.git_ops.run_command", return_value=(0, "origin/main\n", "")),
        ):
            result = judge._get_default_branch(tmp_path, repo="caylent-solutions/git-repo")
        assert result == "main"

    def test_raises_when_git_fallback_fails(self, tmp_path: Path) -> None:
        """Lines 468-472: raises RuntimeError when git fallback fails."""
        from devbench.config_loader import RepoConfig, RuntimeConfig

        judge = GitOpsService()
        runtime_config = RuntimeConfig(repos={"caylent-solutions/git-repo": RepoConfig(default_branch=None)})
        with (
            patch("devbench.github.git_ops.RUNTIME_CONFIG", runtime_config),
            patch("devbench.github.git_ops.run_command", return_value=(1, "", "fatal: error")),
        ):
            with pytest.raises(RuntimeError, match="Cannot determine default branch"):
                judge._get_default_branch(tmp_path, repo="caylent-solutions/git-repo")

    def test_falls_back_to_git_when_no_repo_provided(self, tmp_path: Path) -> None:
        """Lines 459, 464-473: falls back to git when no repo string is provided."""
        judge = GitOpsService()
        with patch("devbench.github.git_ops.run_command", return_value=(0, "origin/develop\n", "")):
            result = judge._get_default_branch(tmp_path)
        assert result == "develop"

    def test_raises_when_git_returns_empty_stdout(self, tmp_path: Path) -> None:
        """Lines 468-472: raises when git returns empty stdout."""
        judge = GitOpsService()
        with patch("devbench.github.git_ops.run_command", return_value=(0, "", "")):
            with pytest.raises(RuntimeError, match="Cannot determine default branch"):
                judge._get_default_branch(tmp_path)


class TestGetLatestFailingRunId:
    """Issue #115: extract failing-run ID from gh pr checks JSON."""

    def test_returns_run_id_when_failure_present(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        gh_json = (
            '[{"name":"build","state":"SUCCESS","link":""},'
            '{"name":"lint","state":"FAILURE","link":"https://github.com/caylent-solutions/git-repo/actions/runs/12345/job/9"}]'
        )
        with patch.object(judge, "_gh", return_value=(0, gh_json, "")):
            assert judge.get_latest_failing_run_id("caylent-solutions/git-repo", 7, repo_path=tmp_path) == "12345"

    def test_returns_none_when_all_passing(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, '[{"name":"x","state":"SUCCESS","link":""}]', "")):
            assert judge.get_latest_failing_run_id("caylent-solutions/git-repo", 7, repo_path=tmp_path) is None

    def test_returns_none_on_subprocess_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(1, "", "boom")):
            assert judge.get_latest_failing_run_id("caylent-solutions/git-repo", 7, repo_path=tmp_path) is None

    def test_returns_none_on_invalid_json(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "{not json", "")):
            assert judge.get_latest_failing_run_id("caylent-solutions/git-repo", 7, repo_path=tmp_path) is None

    def test_returns_none_when_failure_has_no_run_id(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        gh_json = '[{"name":"x","state":"FAILURE","link":""}]'
        with patch.object(judge, "_gh", return_value=(0, gh_json, "")):
            assert judge.get_latest_failing_run_id("caylent-solutions/git-repo", 7, repo_path=tmp_path) is None

    def test_skips_non_dict_entries_in_array(self, tmp_path: Path) -> None:
        # Defensive: gh API response may include non-dict entries (e.g. nulls).
        judge = GitOpsService()
        gh_json = (
            '[null, "string", {"name":"lint","state":"FAILURE","link":"https://github.com/x/y/actions/runs/77/job/9"}]'
        )
        with patch.object(judge, "_gh", return_value=(0, gh_json, "")):
            assert judge.get_latest_failing_run_id("caylent-solutions/git-repo", 7, repo_path=tmp_path) == "77"


class TestFetchRunLog:
    """Issue #115: fetch and trim a failing run's log."""

    def test_returns_full_log_when_under_cap(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "short log\n", "")):
            assert judge.fetch_run_log("caylent-solutions/git-repo", "1", 1024, repo_path=tmp_path) == "short log\n"

    def test_trims_to_tail_when_over_cap(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        body = "head" + ("X" * 200) + "tailtail"
        with patch.object(judge, "_gh", return_value=(0, body, "")):
            trimmed = judge.fetch_run_log("caylent-solutions/git-repo", "1", 8, repo_path=tmp_path)
        assert trimmed == "tailtail"

    def test_returns_empty_on_subprocess_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(2, "", "boom")):
            assert judge.fetch_run_log("caylent-solutions/git-repo", "1", 1024, repo_path=tmp_path) == ""

    def test_max_bytes_zero_returns_full(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_gh", return_value=(0, "abc", "")):
            assert judge.fetch_run_log("caylent-solutions/git-repo", "1", 0, repo_path=tmp_path) == "abc"


class TestPollPrReviewResolution:
    """Issue #116: poll PR review state for asynchronous bot feedback."""

    @staticmethod
    def _gh_view_pr(decision: str = "", reviews: list | None = None) -> str:
        import json as _json

        return _json.dumps({"reviewDecision": decision, "reviews": reviews or []})

    @staticmethod
    def _gh_comments(comments: list | None = None) -> str:
        import json as _json

        return _json.dumps(comments or [])

    @staticmethod
    def _patch_gh(judge: GitOpsService, view_payload: str, comments_payload: str):
        def fake_gh(args, *_a, **_kw):
            if args and args[0] == "pr":
                return (0, view_payload, "")
            if args and args[0] == "api":
                return (0, comments_payload, "")
            return (1, "", "unexpected")

        return patch.object(judge, "_gh", side_effect=fake_gh)

    def test_resolves_when_no_signals(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with self._patch_gh(judge, self._gh_view_pr(decision="APPROVED"), self._gh_comments()):
            with patch("devbench.github.git_ops.time.sleep"):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=("bot",),
                    decision_blocks=True,
                    settle_seconds=0,
                    poll_interval=0,
                )
        assert resolution.resolved is True
        assert resolution.review_decision == "APPROVED"

    def test_blocks_on_changes_requested_review_decision(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        view = self._gh_view_pr(
            decision="CHANGES_REQUESTED",
            reviews=[{"state": "CHANGES_REQUESTED", "author": {"login": "human"}, "body": "fix x"}],
        )
        with self._patch_gh(judge, view, self._gh_comments()):
            with patch("devbench.github.git_ops.time.sleep"):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=(),
                    decision_blocks=True,
                    settle_seconds=0,
                    poll_interval=0,
                )
        assert resolution.resolved is False
        assert resolution.review_decision == "CHANGES_REQUESTED"
        assert len(resolution.unresolved_reviews) == 1

    def test_blocks_on_bot_comment_in_allowlist(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        comments = [
            {"user": {"login": "github-copilot[bot]"}, "path": "src/x.py", "line": 12, "body": "use snake_case"},
            {"user": {"login": "human-bystander"}, "path": "src/x.py", "line": 13, "body": "nit"},
        ]
        with self._patch_gh(judge, self._gh_view_pr(decision="REVIEW_REQUIRED"), self._gh_comments(comments)):
            with patch("devbench.github.git_ops.time.sleep"):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=("github-copilot[bot]",),
                    decision_blocks=False,
                    settle_seconds=0,
                    poll_interval=0,
                )
        assert resolution.resolved is False
        assert len(resolution.unresolved_comments) == 1
        assert resolution.unresolved_comments[0]["author"] == "github-copilot[bot]"

    def test_ignores_non_changes_requested_reviews(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        view = self._gh_view_pr(
            decision="REVIEW_REQUIRED",
            reviews=[{"state": "COMMENTED", "author": {"login": "h"}, "body": "lgtm-ish"}],
        )
        with self._patch_gh(judge, view, self._gh_comments()):
            with patch("devbench.github.git_ops.time.sleep"):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=(),
                    decision_blocks=True,
                    settle_seconds=0,
                    poll_interval=0,
                )
        assert resolution.resolved is True

    def test_handles_invalid_json_as_empty(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with self._patch_gh(judge, "{not json", "{not json"):
            with patch("devbench.github.git_ops.time.sleep"):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=("bot",),
                    decision_blocks=True,
                    settle_seconds=0,
                    poll_interval=0,
                )
        assert resolution.resolved is True

    def test_polls_multiple_times_within_settle_window(self, tmp_path: Path) -> None:
        """The settle loop sleeps + retries when no signal appears; poll runs at
        least twice when settle_seconds > 0."""
        judge = GitOpsService()
        with self._patch_gh(judge, self._gh_view_pr(decision="REVIEW_REQUIRED"), self._gh_comments()):
            sleep_calls: list[int] = []

            def fake_sleep(secs: int) -> None:
                # Append once, then bump time forward by raising a sentinel
                # via monotonic-shift is awkward; instead patch monotonic to
                # advance past the deadline after the first sleep.
                sleep_calls.append(secs)

            monotonic_calls = iter([0.0, 0.0, 100.0, 100.0, 100.0])
            with (
                patch("devbench.github.git_ops.time.sleep", side_effect=fake_sleep),
                patch("devbench.github.git_ops.time.monotonic", side_effect=lambda: next(monotonic_calls)),
            ):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=(),
                    decision_blocks=True,
                    settle_seconds=10,
                    poll_interval=2,
                )
        # The loop slept at least once before the deadline elapsed.
        assert sleep_calls
        assert resolution.resolved is True

    def test_skips_review_whose_author_not_in_allowlist(self, tmp_path: Path) -> None:
        """When decision_blocks=False and the PR's reviewDecision is not
        CHANGES_REQUESTED, a CHANGES_REQUESTED review by a non-allowlisted
        author is skipped so the merge is not blocked."""
        judge = GitOpsService()
        view = self._gh_view_pr(
            decision="REVIEW_REQUIRED",
            reviews=[{"state": "CHANGES_REQUESTED", "author": {"login": "random-human"}, "body": "drive-by"}],
        )
        with self._patch_gh(judge, view, self._gh_comments()):
            with patch("devbench.github.git_ops.time.sleep"):
                resolution = judge.poll_pr_review_resolution(
                    "caylent-solutions/git-repo",
                    7,
                    repo_path=tmp_path,
                    agents=("github-copilot[bot]",),
                    decision_blocks=False,
                    settle_seconds=0,
                    poll_interval=0,
                )
        assert resolution.resolved is True
        assert resolution.unresolved_reviews == []
