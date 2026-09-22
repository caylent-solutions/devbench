"""Tests for github.git_ops module."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.github.git_ops import CIResult, ConflictingPRError, GitOpsService


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
            judge.commit_and_push("evil/repo", tmp_path, "branch", "msg", stage_all=True)

    def test_rejects_invalid_branch_name(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="Invalid branch name"):
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "bad branch!", "msg", stage_all=True)

    def test_raises_on_git_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with patch.object(judge, "_git", side_effect=RuntimeError("git failed")):
            with pytest.raises(RuntimeError, match="git failed"):
                judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "b", "m", stage_all=True)

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
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "commit msg", stage_all=True)

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
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)

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
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)

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
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)

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
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)

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
                judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)

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


class TestProvenancePrBody:
    """Test compose_finalize_pr_body (E2-F9-S1-T1; spec 4.13; D-17; AC-24).

    ``git-ops-finalize`` composes the batch PR body through this seam. With
    no provenance map configured (``provenance_path=None``) the output must
    stay byte-identical to the pre-existing plain body (spec Section 6:
    absent config preserves today's behaviour). With a resolved map, the
    body carries the title, a per-epic summary and one closing-keyword line
    per mapped issue, in both the same-repo and cross-repo forms, rendered
    by a single code path (AC-E2-F9-S1-T1-2).
    """

    #: The exact plain body ``git-ops-finalize`` has always produced.
    #: compose_finalize_pr_body(provenance_path=None) must reproduce this
    #: string byte-for-byte (AC-E2-F9-S1-T1-3).
    _PLAIN_BODY = (
        "Accumulated commits from DevBench single-branch execution.\n\n"
        "Branch: `feature/combined`\nRepo: `caylent-solutions/git-repo`"
    )

    def _write_map(self, tmp_path: Path, payload: dict) -> Path:
        provenance_path = tmp_path / "provenance-map.json"
        provenance_path.write_text(json.dumps(payload), encoding="utf-8")
        return provenance_path

    def test_no_provenance_path_matches_plain_body_exactly(self) -> None:
        judge = GitOpsService()
        body = judge.compose_finalize_pr_body(
            repo="caylent-solutions/git-repo",
            branch="feature/combined",
            title="feat: feature/combined",
            provenance_path=None,
        )
        assert body == self._PLAIN_BODY

    def test_composes_title_epic_summary_and_closing_keywords(self, tmp_path: Path) -> None:
        payload = {
            "epics": [
                {
                    "name": "E1: Cherry-pick integration",
                    "summary": "Landed the eight source PRs on the campaign branch.",
                    "issues": [
                        {"repo": "caylent-solutions/devbench-internal-backlog", "number": 10},
                        {"number": 335},
                    ],
                }
            ]
        }
        provenance_path = self._write_map(tmp_path, payload)
        judge = GitOpsService()

        body = judge.compose_finalize_pr_body(
            repo="caylent-solutions/git-repo",
            branch="feature/combined",
            title="feat: feature/combined",
            provenance_path=provenance_path,
        )

        assert "feat: feature/combined" in body
        assert "E1: Cherry-pick integration" in body
        assert "Landed the eight source PRs on the campaign branch." in body
        assert "Fixes caylent-solutions/devbench-internal-backlog#10" in body
        assert "Fixes #335" in body

    @pytest.mark.parametrize(
        ("issue", "expected_line"),
        [
            (
                {"repo": "caylent-solutions/devbench-internal-backlog", "number": 10},
                "Fixes caylent-solutions/devbench-internal-backlog#10",
            ),
            ({"number": 335}, "Fixes #335"),
            ({"repo": "caylent-solutions/git-repo", "number": 42}, "Fixes #42"),
        ],
        ids=["cross_repo", "same_repo_no_repo_key", "same_repo_explicit_repo_key"],
    )
    def test_closing_keyword_forms_render_from_single_code_path(
        self, tmp_path: Path, issue: dict, expected_line: str
    ) -> None:
        """AC-E2-F9-S1-T1-2: cross-repo and same-repo keyword lines both come
        from :func:`devbench.github.git_ops._render_closing_keyword_line` --
        proven here by driving both shapes through the SAME composer call
        rather than two separate rendering code paths."""
        payload = {"epics": [{"name": "E1", "summary": "s", "issues": [issue]}]}
        provenance_path = self._write_map(tmp_path, payload)
        judge = GitOpsService()

        body = judge.compose_finalize_pr_body(
            repo="caylent-solutions/git-repo",
            branch="feature/combined",
            title="t",
            provenance_path=provenance_path,
        )

        assert expected_line in body

    def test_missing_provenance_path_fails_loudly_naming_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.json"
        judge = GitOpsService()
        with pytest.raises(ValueError, match=re.escape("does-not-exist.json")):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=missing,
            )

    def test_unreadable_provenance_path_fails_loudly_naming_path(self, tmp_path: Path) -> None:
        unreadable = tmp_path / "unreadable-map.json"
        unreadable.write_text(json.dumps({"epics": []}), encoding="utf-8")
        original_mode = unreadable.stat().st_mode
        judge = GitOpsService()
        try:
            unreadable.chmod(0o000)
            # Assert on the distinctive "is unreadable" substring (test_review
            # non-blocking hardening note), not just the filename: under a
            # root test runner chmod(0o000) is a no-op, the read would
            # succeed, and this fixture's {"epics": []} payload would
            # instead raise the *no-mapped-issues* ValueError, whose message
            # also contains the filename -- so a filename-only match would
            # pass on that unintended branch too.
            with pytest.raises(ValueError, match="is unreadable"):
                judge.compose_finalize_pr_body(
                    repo="caylent-solutions/git-repo",
                    branch="feature/combined",
                    title="t",
                    provenance_path=unreadable,
                )
        finally:
            unreadable.chmod(original_mode)

    def test_empty_epics_list_fails_loudly_rather_than_empty_keyword_block(self, tmp_path: Path) -> None:
        provenance_path = self._write_map(tmp_path, {"epics": []})
        judge = GitOpsService()
        with pytest.raises(ValueError, match="no mapped issues"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_epic_with_no_issues_fails_loudly_rather_than_empty_keyword_block(self, tmp_path: Path) -> None:
        provenance_path = self._write_map(tmp_path, {"epics": [{"name": "E1", "summary": "s", "issues": []}]})
        judge = GitOpsService()
        with pytest.raises(ValueError, match="no mapped issues"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_malformed_json_fails_loudly_naming_path(self, tmp_path: Path) -> None:
        provenance_path = tmp_path / "bad.json"
        provenance_path.write_text("{not valid json", encoding="utf-8")
        judge = GitOpsService()
        with pytest.raises(ValueError, match=re.escape("bad.json")):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_issue_entry_missing_number_fails_loudly(self, tmp_path: Path) -> None:
        provenance_path = self._write_map(
            tmp_path, {"epics": [{"name": "E1", "summary": "s", "issues": [{"repo": "org/repo"}]}]}
        )
        judge = GitOpsService()
        with pytest.raises(ValueError, match="number"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_non_object_issue_entry_fails_loudly(self, tmp_path: Path) -> None:
        provenance_path = self._write_map(
            tmp_path, {"epics": [{"name": "E1", "summary": "s", "issues": ["not-an-object"]}]}
        )
        judge = GitOpsService()
        with pytest.raises(ValueError, match="non-object issue entry"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_non_object_provenance_map_fails_loudly(self, tmp_path: Path) -> None:
        provenance_path = tmp_path / "list-map.json"
        provenance_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        judge = GitOpsService()
        with pytest.raises(ValueError, match="must decode to a JSON object"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_non_list_epics_fails_loudly(self, tmp_path: Path) -> None:
        provenance_path = self._write_map(tmp_path, {"epics": "not-a-list"})
        judge = GitOpsService()
        with pytest.raises(ValueError, match="must contain an 'epics' list"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    def test_non_object_epic_entry_fails_loudly(self, tmp_path: Path) -> None:
        provenance_path = tmp_path / "bad-epic.json"
        # A dedicated fixture (not via _write_map) so the total-issues guard
        # is bypassed by a sibling well-formed epic, isolating the
        # non-object-epic branch under test.
        provenance_path.write_text(
            json.dumps({"epics": ["not-an-object", {"name": "E1", "summary": "s", "issues": [{"number": 1}]}]}),
            encoding="utf-8",
        )
        judge = GitOpsService()
        with pytest.raises(ValueError, match="non-object epic entry"):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    @pytest.mark.parametrize("issues_value", [5, True], ids=["int", "bool"])
    def test_epic_with_non_list_issues_value_fails_loudly(self, tmp_path: Path, issues_value: object) -> None:
        """Regression test (spec Section 7): a non-sized JSON scalar in
        ``issues`` must raise a loud ``ValueError`` naming the path and
        the offending epic, never an uncaught ``TypeError`` from ``len()``.
        Restricted to the two shapes verified by execution to reach
        ``len()`` and crash (``int``/``bool``) -- a JSON string or object
        value already fails loudly today via a different, coincidental
        validation branch (string iterates into single-char "issue"
        entries, dict has its own ``__len__``), so those shapes would not
        reproduce this specific gap."""
        provenance_path = tmp_path / "non-list-issues.json"
        provenance_path.write_text(
            json.dumps({"epics": [{"name": "E1", "summary": "s", "issues": issues_value}]}),
            encoding="utf-8",
        )
        judge = GitOpsService()
        with pytest.raises(ValueError, match=re.escape(str(provenance_path))):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    @pytest.mark.parametrize("missing_field", ["name", "summary"])
    def test_epic_missing_name_or_summary_fails_loudly(self, tmp_path: Path, missing_field: str) -> None:
        """Regression test (spec 4.13; AC-E2-F9-S1-T1-1): an epic missing
        ``name`` or ``summary`` must raise loudly rather than silently
        defaulting to an empty string, since AC-E2-F9-S1-T1-1 requires a
        non-empty per-epic summary in the composed body."""
        epic = {"name": "E1", "summary": "s", "issues": [{"number": 1}]}
        del epic[missing_field]
        provenance_path = self._write_map(tmp_path, {"epics": [epic]})
        judge = GitOpsService()
        with pytest.raises(ValueError, match=re.escape(str(provenance_path))):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )

    @pytest.mark.parametrize("bad_repo", [42, True, "no-slash-here"], ids=["int", "bool", "missing-slash"])
    def test_issue_entry_with_invalid_repo_value_fails_loudly(self, tmp_path: Path, bad_repo: object) -> None:
        """Regression test (spec 4.13; D-17): an issue's ``repo`` field
        that is not a string in ``owner/name`` shape must raise loudly
        rather than rendering a dead closing keyword like ``Fixes 42#1``
        that GitHub will never honour on merge."""
        provenance_path = self._write_map(
            tmp_path, {"epics": [{"name": "E1", "summary": "s", "issues": [{"number": 1, "repo": bad_repo}]}]}
        )
        judge = GitOpsService()
        with pytest.raises(ValueError, match=re.escape(str(provenance_path))):
            judge.compose_finalize_pr_body(
                repo="caylent-solutions/git-repo",
                branch="feature/combined",
                title="t",
                provenance_path=provenance_path,
            )


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

    def test_merge_pr_uses_resolved_strategy_flag(self, tmp_path: Path) -> None:
        """Regression for #237: merge_pr passes the flag from the per-repo/top-level
        resolved strategy, not the static global (which ignored YAML merge_strategy)."""
        from devbench.config import MergeStrategy

        judge = GitOpsService()
        with (
            patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh,
            patch(
                "devbench.github.git_ops.resolve_merge_strategy",
                return_value=MergeStrategy.REBASE,
            ) as mock_resolve,
        ):
            judge.merge_pr("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        mock_resolve.assert_called_once_with("caylent-solutions/git-repo")
        args, _ = mock_gh.call_args
        assert "--rebase" in args[0]
        assert "--squash" not in args[0]


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
    """Test wait_for_checks method.

    db-328 / FR-15: once `gh pr checks --watch` returns rc==0, the green
    verdict is gated on a head-SHA-pinned, stability-confirmed check-run
    quorum (`_confirm_check_quorum`). The helpers below build a `_gh`
    side_effect that dispatches on the three call shapes involved:
    the watch call, `gh pr view --json headRefOid`, and
    `gh api repos/<repo>/commits/<sha>/check-runs --paginate`.
    """

    @staticmethod
    def _head_sha_json(sha: str) -> str:
        import json as _json

        return _json.dumps({"headRefOid": sha})

    @staticmethod
    def _check_runs_json(runs: list[dict[str, object]]) -> str:
        import json as _json

        return _json.dumps({"check_runs": runs})

    @staticmethod
    def _run(run_id: str, status: str = "completed", conclusion: str | None = "success") -> dict[str, object]:
        return {"id": run_id, "status": status, "conclusion": conclusion, "name": f"job-{run_id}"}

    @classmethod
    def _quorum_gh_stub(
        cls,
        watch_result: tuple[int, str, str],
        head_sha: str,
        check_runs_polls: list[list[dict[str, object]]],
    ):
        """Return a `_gh` side_effect: watch call -> head-SHA call -> N check-runs polls."""
        polls_iter = iter(check_runs_polls)

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:2] == ["pr", "checks"]:
                return watch_result
            if args[:2] == ["pr", "view"]:
                return (0, cls._head_sha_json(head_sha), "")
            if args and args[0] == "api":
                try:
                    runs = next(polls_iter)
                except StopIteration as exc:
                    raise AssertionError("check-runs API polled more times than the test stubbed") from exc
                return (0, cls._check_runs_json(runs), "")
            raise AssertionError(f"unexpected _gh call: {args}")

        return fake_gh

    def test_validates_repo(self) -> None:
        judge = GitOpsService()
        with pytest.raises(ValueError, match="not allowed"):
            judge.wait_for_checks("evil/repo", 1)

    def test_returns_true_on_success(self, tmp_path: Path) -> None:
        """A stable, all-completed, all-good head-SHA check-run set returns True. AC-4"""
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        fake_gh = self._quorum_gh_stub((0, "All checks passed", ""), "sha-success", [[self._run("r1")]])
        with (
            patch.object(judge, "_gh", side_effect=fake_gh),
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 1),
            patch("devbench.github.git_ops.time.sleep"),
        ):
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
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        fake_gh = self._quorum_gh_stub((0, "", ""), "sha-timeout-knob", [[self._run("r1")]])
        with (
            patch.object(judge, "_gh", side_effect=fake_gh) as mock_gh,
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 1),
            patch("devbench.github.git_ops.time.sleep"),
        ):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, timeout=999, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args_list[0]
        assert kwargs.get("timeout") == 999

    def test_gh_called_with_repo_flag(self, tmp_path: Path) -> None:
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        fake_gh = self._quorum_gh_stub((0, "", ""), "sha-repo-flag", [[self._run("r1")]])
        with (
            patch.object(judge, "_gh", side_effect=fake_gh) as mock_gh,
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 1),
            patch("devbench.github.git_ops.time.sleep"),
        ):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args_list[0]
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_no_checks_with_workflow_files_retries_then_succeeds(self, tmp_path: Path) -> None:
        """Issue #114: workflow exists but Actions has not enqueued yet.

        Mock `gh pr checks` to return "no checks reported" the first call,
        then a clean exit on the second. The retry loop should bridge the
        gap and, once the watch call returns rc==0, still confirm the
        head-SHA quorum before declaring green.
        """
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text("on: push\n")
        watch_calls = {"n": 0}

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:3] == ["pr", "checks", "42"]:
                watch_calls["n"] += 1
                if watch_calls["n"] == 1:
                    return (1, "", "no checks reported")
                return (0, "All checks passed", "")
            if args[:2] == ["pr", "view"]:
                return (0, self._head_sha_json("sha-114-retry"), "")
            if args and args[0] == "api":
                return (0, self._check_runs_json([self._run("r1")]), "")
            raise AssertionError(f"unexpected _gh call: {args}")

        with (
            patch.object(judge, "_gh", side_effect=fake_gh),
            patch.object(git_ops_mod, "CHECK_REGISTRATION_DELAY_SECONDS", 0),
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 1),
            patch("devbench.github.git_ops.time.sleep"),
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

    def test_wait_for_checks_pins_head_sha(self, tmp_path: Path) -> None:
        """The green verdict resolves the head SHA and queries check-runs at that SHA. AC-2"""
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        head_sha = "deadbeef1234567890"
        fake_gh = self._quorum_gh_stub((0, "All checks passed", ""), head_sha, [[self._run("r1")]])
        with (
            patch.object(judge, "_gh", side_effect=fake_gh) as mock_gh,
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 1),
            patch("devbench.github.git_ops.time.sleep"),
        ):
            result = judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        assert result is True
        calls = mock_gh.call_args_list
        view_calls = [c for c in calls if c.args[0][:2] == ["pr", "view"]]
        assert len(view_calls) == 1
        assert view_calls[0].args[0] == ["pr", "view", "42", "--json", "headRefOid"]
        api_calls = [c for c in calls if c.args[0][0] == "api"]
        assert len(api_calls) == 1
        expected_endpoint = f"repos/caylent-solutions/git-repo/commits/{head_sha}/check-runs"
        assert api_calls[0].args[0] == ["api", expected_endpoint, "--paginate"]

    def test_wait_for_checks_not_green_on_partial_registration(self, tmp_path: Path) -> None:
        """A check-run that first appears on a later poll resets stability. AC-1"""
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        head_sha = "sha-partial-registration"
        # Poll 1: only r1 registered and already completed. Poll 2: r2 appears
        # (still queued) -- resets stability. Poll 3: r2 has completed and the
        # id set now matches poll 2 -- 2 consecutive matching polls satisfies
        # CHECK_QUORUM_STABLE_POLLS=2, so green is declared only at poll 3.
        polls = [
            [self._run("r1")],
            [self._run("r1"), self._run("r2", status="queued", conclusion=None)],
            [self._run("r1"), self._run("r2")],
        ]
        fake_gh = self._quorum_gh_stub((0, "All checks passed", ""), head_sha, polls)
        with (
            patch.object(judge, "_gh", side_effect=fake_gh) as mock_gh,
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 2),
            patch("devbench.github.git_ops.time.sleep"),
        ):
            result = judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        assert result is True
        api_calls = [c for c in mock_gh.call_args_list if c.args[0][0] == "api"]
        assert len(api_calls) == 3, "verdict must be withheld until the set is stable across 3 polls"

    def test_wait_for_checks_fail_fast_on_unstable_timeout(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The check-run set never stabilizes; the method refuses to merge with the FR-15 string. AC-3"""
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        head_sha = "sha-never-stable"
        # Every poll introduces a differently-shaped, incomplete set -- stability
        # never accrues.
        polls = [
            [self._run("r1", status="in_progress", conclusion=None)],
            [self._run("r2", status="in_progress", conclusion=None)],
        ]
        fake_gh = self._quorum_gh_stub((0, "All checks passed", ""), head_sha, polls)
        monotonic_values = iter([0.0, 0.0, 20.0])
        with (
            patch.object(judge, "_gh", side_effect=fake_gh) as mock_gh,
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 5),
            patch("devbench.github.git_ops.time.sleep"),
            patch("devbench.github.git_ops.time.monotonic", side_effect=lambda: next(monotonic_values)),
            caplog.at_level("WARNING", logger="devbench.git_ops"),
        ):
            result = judge.wait_for_checks("caylent-solutions/git-repo", 42, timeout=15, repo_path=tmp_path)

        assert result is False
        api_calls = [c for c in mock_gh.call_args_list if c.args[0][0] == "api"]
        assert len(api_calls) == 2
        expected = (
            "wait_for_checks: PR #42 on caylent-solutions/git-repo: check set never stabilized "
            f"at head {head_sha} within 20s (1 check-runs, 1 still pending across the last 2 "
            "polls). Refusing to merge. Inspect: gh api repos/caylent-solutions/git-repo/commits/"
            f"{head_sha}/check-runs."
        )
        assert expected in caplog.text

    def test_wait_for_checks_failing_conclusion_returns_false(self, tmp_path: Path) -> None:
        """Any completed check-run with a bad conclusion refuses the merge immediately. AC-4"""
        from devbench.github import git_ops as git_ops_mod

        judge = GitOpsService()
        head_sha = "sha-failing-run"
        polls = [[self._run("r1", conclusion="success"), self._run("r2", conclusion="failure")]]
        fake_gh = self._quorum_gh_stub((0, "All checks passed", ""), head_sha, polls)
        with (
            patch.object(judge, "_gh", side_effect=fake_gh) as mock_gh,
            patch.object(git_ops_mod, "CHECK_QUORUM_STABLE_POLLS", 3),
            patch("devbench.github.git_ops.time.sleep"),
        ):
            result = judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

        assert result is False
        api_calls = [c for c in mock_gh.call_args_list if c.args[0][0] == "api"]
        assert len(api_calls) == 1, "a failing conclusion must short-circuit further polling"

    def test_wait_for_checks_raises_when_head_sha_resolution_fails(self, tmp_path: Path) -> None:
        """A `gh pr view` failure on the head-SHA lookup surfaces loudly -- never swallowed into green."""
        judge = GitOpsService()

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:2] == ["pr", "checks"]:
                return (0, "All checks passed", "")
            if args[:2] == ["pr", "view"]:
                return (1, "", "gh: could not resolve to a PullRequest")
            raise AssertionError(f"unexpected _gh call: {args}")

        with (
            patch.object(judge, "_gh", side_effect=fake_gh),
            pytest.raises(RuntimeError, match="failed to resolve head SHA"),
        ):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_wait_for_checks_raises_when_head_sha_json_malformed(self, tmp_path: Path) -> None:
        judge = GitOpsService()

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:2] == ["pr", "checks"]:
                return (0, "All checks passed", "")
            if args[:2] == ["pr", "view"]:
                return (0, "{not json", "")
            raise AssertionError(f"unexpected _gh call: {args}")

        with (
            patch.object(judge, "_gh", side_effect=fake_gh),
            pytest.raises(RuntimeError, match="malformed 'gh pr view"),
        ):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_wait_for_checks_raises_when_head_sha_empty(self, tmp_path: Path) -> None:
        judge = GitOpsService()

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:2] == ["pr", "checks"]:
                return (0, "All checks passed", "")
            if args[:2] == ["pr", "view"]:
                return (0, self._head_sha_json(""), "")
            raise AssertionError(f"unexpected _gh call: {args}")

        with patch.object(judge, "_gh", side_effect=fake_gh), pytest.raises(RuntimeError, match="empty headRefOid"):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_wait_for_checks_raises_when_check_runs_api_fails(self, tmp_path: Path) -> None:
        judge = GitOpsService()

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:2] == ["pr", "checks"]:
                return (0, "All checks passed", "")
            if args[:2] == ["pr", "view"]:
                return (0, self._head_sha_json("sha-api-fail"), "")
            if args and args[0] == "api":
                return (1, "", "gh: rate limited")
            raise AssertionError(f"unexpected _gh call: {args}")

        with (
            patch.object(judge, "_gh", side_effect=fake_gh),
            pytest.raises(RuntimeError, match="failed to query check-runs"),
        ):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)

    def test_wait_for_checks_raises_when_check_runs_json_malformed(self, tmp_path: Path) -> None:
        judge = GitOpsService()

        def fake_gh(args: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if args[:2] == ["pr", "checks"]:
                return (0, "All checks passed", "")
            if args[:2] == ["pr", "view"]:
                return (0, self._head_sha_json("sha-bad-json"), "")
            if args and args[0] == "api":
                return (0, "{not json", "")
            raise AssertionError(f"unexpected _gh call: {args}")

        with (
            patch.object(judge, "_gh", side_effect=fake_gh),
            pytest.raises(RuntimeError, match="malformed check-runs JSON"),
        ):
            judge.wait_for_checks("caylent-solutions/git-repo", 42, repo_path=tmp_path)


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
    """Test _git helper method.

    #332 FR-3 / AC-9, AC-10, AC-11: ``_git()`` must authenticate on the same
    ``GH_TOKEN`` the ``gh`` helper (:meth:`GitOpsService._gh`) already uses,
    carried only through the subprocess environment -- never in ``argv``, a
    remote URL, or a log line.
    """

    def test_raises_runtime_error_on_failure(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with (
            patch("devbench.github.git_ops.get_gh_token", return_value="tok"),
            patch("devbench.github.git_ops.run_command", return_value=(1, "", "fatal: error")),
        ):
            with pytest.raises(RuntimeError, match=r"git .* failed"):
                judge._git(["status"], tmp_path)

    def test_returns_tuple_on_success(self, tmp_path: Path) -> None:
        judge = GitOpsService()
        with (
            patch("devbench.github.git_ops.get_gh_token", return_value="tok"),
            patch("devbench.github.git_ops.run_command", return_value=(0, "output", "")),
        ):
            rc, stdout, stderr = judge._git(["status"], tmp_path)
        assert rc == 0
        assert stdout == "output"

    def test_git_carries_gh_token_via_env(self, tmp_path: Path) -> None:
        """AC-9: ``_git()`` resolves ``get_gh_token()`` and threads it through env."""
        judge = GitOpsService()
        with (
            patch("devbench.github.git_ops.get_gh_token", return_value="tok-abc-123"),
            patch("devbench.github.git_ops.run_command", return_value=(0, "", "")) as mock_run,
        ):
            judge._git(["push", "origin", "feature/x"], tmp_path)

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["env"]["GH_TOKEN"] == "tok-abc-123"

    def test_git_token_never_in_argv_or_remote_url(self, tmp_path: Path) -> None:
        """AC-9: the token value never appears in any element of argv (incl. a remote URL)."""
        judge = GitOpsService()
        token = "tok-abc-123"
        with (
            patch("devbench.github.git_ops.get_gh_token", return_value=token),
            patch("devbench.github.git_ops.run_command", return_value=(0, "", "")) as mock_run,
        ):
            judge._git(["push", "origin", "feature/x"], tmp_path)

        cmd = mock_run.call_args.args[0]
        assert all(token not in part for part in cmd), f"token leaked into argv: {cmd}"
        assert all("@" not in part or token not in part for part in cmd), f"token leaked into a URL: {cmd}"

    def test_git_debug_log_never_contains_token(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """AC-11: no log line emitted by ``_git()`` renders the token value."""
        judge = GitOpsService()
        token = "tok-super-secret-999"
        caplog.set_level(logging.DEBUG, logger="devbench.git_ops")
        with (
            patch("devbench.github.git_ops.get_gh_token", return_value=token),
            patch("devbench.github.git_ops.run_command", return_value=(0, "ok stdout", "")),
        ):
            judge._git(["push", "origin", "feature/x"], tmp_path)

        assert token not in caplog.text

    def test_git_failing_push_raises_with_stderr_surfaced(self, tmp_path: Path) -> None:
        """AC-10: a failing push exits non-zero (via RuntimeError) with git's stderr surfaced."""
        judge = GitOpsService()
        stderr = "remote: No anonymous write access. fatal: Authentication failed for 'https://github.com/x/y.git/'"
        with (
            patch("devbench.github.git_ops.get_gh_token", return_value="tok"),
            patch("devbench.github.git_ops.run_command", return_value=(1, "", stderr)),
        ):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                judge._git(["push", "origin", "feature/x"], tmp_path)

    def test_git_raises_when_token_unavailable(self, tmp_path: Path) -> None:
        """A missing token fails fast at get_gh_token() before any subprocess runs, for a remote op."""
        judge = GitOpsService()
        with (
            patch(
                "devbench.github.git_ops.get_gh_token",
                side_effect=RuntimeError("GitHub token not found. Provide it via ..."),
            ),
            patch("devbench.github.git_ops.run_command") as mock_run,
        ):
            with pytest.raises(RuntimeError, match="GitHub token not found"):
                judge._git(["push", "origin", "feature/x"], tmp_path)
        mock_run.assert_not_called()

    def test_git_credential_helper_resolves_token_from_env_via_real_git(self, tmp_path: Path) -> None:
        """The real ``credential.helper`` string _git() builds genuinely reads GH_TOKEN.

        Captures the exact argv/env ``_git()`` constructs for a remote
        subcommand (``push``), then re-executes it through git's own
        ``credential fill`` plumbing -- the same mechanism a real ``git
        push`` uses internally to fetch credentials -- so a shell syntax
        bug in the inline helper (invisible to a fully-mocked test) fails
        this test.
        """
        judge = GitOpsService()
        token = "test-token-value-xyz"
        captured_cmd: list[str] = []
        captured_env: dict[str, str] | None = None

        def _capture_run_command(
            cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
        ) -> tuple[int, str, str]:
            nonlocal captured_cmd, captured_env
            captured_cmd = cmd
            captured_env = env
            return 0, "", ""

        with (
            patch("devbench.github.git_ops.get_gh_token", return_value=token),
            patch("devbench.github.git_ops.run_command", side_effect=_capture_run_command),
        ):
            judge._git(["push", "origin", "feature/x"], tmp_path)

        # The first five elements are git plus two -c flags: an empty-valued
        # credential.helper reset (so an ambient system/global helper cannot
        # also answer, and potentially win over, this one) followed by the
        # helper that reads GH_TOKEN. See GitOpsService._git's docstring.
        helper_prefix = captured_cmd[:5]
        assert helper_prefix[0] == "git"
        assert helper_prefix[1] == "-c"
        assert helper_prefix[2] == "credential.helper="
        assert helper_prefix[3] == "-c"
        assert helper_prefix[4].startswith("credential.helper=")

        result = subprocess.run(
            [*helper_prefix, "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            env=captured_env,
            cwd=tmp_path,
            check=True,
        )
        assert f"password={token}" in result.stdout
        assert "username=x-access-token" in result.stdout

    def test_git_local_subcommand_never_calls_get_gh_token(self, tmp_path: Path) -> None:
        """AC-E17-F2-S1-T3-1/2: a local-only subcommand (status) never resolves GH_TOKEN and runs with env=None."""
        judge = GitOpsService()
        with (
            patch("devbench.github.git_ops.get_gh_token") as mock_token,
            patch("devbench.github.git_ops.run_command", return_value=(0, "", "")) as mock_run,
        ):
            judge._git(["status"], tmp_path)

        mock_token.assert_not_called()
        assert mock_run.call_args.kwargs["env"] is None
        cmd = mock_run.call_args.args[0]
        assert cmd == ["git", "status"]

    def test_git_local_add_and_commit_succeed_without_any_token(self, tmp_path: Path) -> None:
        """AC-E17-F2-S1-T3-2: real ``git add``/``git commit`` through _git() succeed with no token resolvable.

        Reproduces the batch PR #334 CI symptom (get_gh_token raising
        'GitHub token not found') and proves local subcommands never
        trigger it: get_gh_token is patched to raise, and the two local
        git operations must still complete against a real subprocess.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        for init_args in (
            ["init", "-q"],
            ["config", "user.email", "x@y.z"],
            ["config", "user.name", "test"],
            ["config", "commit.gpgsign", "false"],
        ):
            subprocess.run(["git", "-C", str(repo), *init_args], check=True)
        (repo / "file.txt").write_text("hello\n")

        judge = GitOpsService()
        with patch(
            "devbench.github.git_ops.get_gh_token",
            side_effect=RuntimeError("GitHub token not found. Provide it via ..."),
        ) as mock_token:
            judge._git(["add", "file.txt"], repo)
            judge._git(["commit", "-m", "add file"], repo)

        mock_token.assert_not_called()
        log = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "add file" in log.stdout


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


def _real_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit, for tests that must observe actual staging."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "x@y.z"],
        ["config", "user.name", "test"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "mine.py").write_text("original\n")
    (repo / "theirs.py").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "feature/x"], check=True)
    return repo


def _committed_files(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in out.stdout.split() if line}


@pytest.mark.unit
class TestCommitScopedToManifest:
    """A commit carries only its own work unit's files, or it refuses.

    ``git add -A`` stages the whole working tree, so a file another work unit
    left modified-but-unstaged is swept into this unit's commit under this
    unit's message. ``assert_staged_matches_manifest`` cannot catch it: that
    guard reads ``git diff --cached``, which by definition does not see unstaged
    changes. The victim task then fails ``changes_manifest`` forever -- the files
    it declared are committed under someone else's name, and the only remedies
    are an operator override or rewriting published history on a shared branch.
    Observed in production as commit b5201cb.

    Every staging path is an explicit caller decision. There is deliberately no
    degraded mode: a caller that cannot name its scope is refused, never given a
    silent whole-tree commit.
    """

    def test_commit_local_ignores_another_units_unstaged_work(self, tmp_path: Path) -> None:
        repo = _real_repo(tmp_path)
        (repo / "mine.py").write_text("mine change\n")
        subprocess.run(["git", "-C", str(repo), "add", "mine.py"], check=True)
        (repo / "theirs.py").write_text("their in-flight change\n")  # NOT staged

        GitOpsService().commit_local(
            "caylent-solutions/git-repo", repo, "feature/x", "E0-F1-S1-T1: mine", manifest_files=["mine.py"]
        )

        committed = _committed_files(repo)
        assert "mine.py" in committed
        assert "theirs.py" not in committed, (
            f"commit swallowed another unit's unstaged file; committed={sorted(committed)}"
        )
        assert (repo / "theirs.py").read_text() == "their in-flight change\n"

    def test_commit_local_stages_manifest_paths_left_unstaged(self, tmp_path: Path) -> None:
        """The unit's OWN Manifest paths are staged even when the executor left them unstaged."""
        repo = _real_repo(tmp_path)
        (repo / "mine.py").write_text("mine change\n")  # never `git add`-ed

        GitOpsService().commit_local(
            "caylent-solutions/git-repo", repo, "feature/x", "E0-F1-S1-T1: mine", manifest_files=["mine.py"]
        )

        assert "mine.py" in _committed_files(repo)

    def test_commit_refuses_when_scope_is_unknown(self, tmp_path: Path) -> None:
        """No Manifest and no explicit stage_all is a refusal, not a whole-tree commit."""
        repo = _real_repo(tmp_path)
        (repo / "theirs.py").write_text("must not be committed\n")

        with pytest.raises(RuntimeError, match="cannot determine"):
            GitOpsService().commit_local("caylent-solutions/git-repo", repo, "feature/x", "msg")

        assert _committed_files(repo) == {"mine.py", "theirs.py"}  # base commit only

    def test_commit_refuses_a_manifest_of_only_sentinels(self, tmp_path: Path) -> None:
        """An all-sentinel Manifest yields no pathspec, so it refuses rather than staging everything."""
        repo = _real_repo(tmp_path)

        with pytest.raises(RuntimeError, match="no concrete file paths"):
            GitOpsService().commit_local(
                "caylent-solutions/git-repo",
                repo,
                "feature/x",
                "msg",
                manifest_files=["<targets-determined-at-execution>"],
            )

    def test_stage_all_is_an_explicit_opt_in(self, tmp_path: Path) -> None:
        """The finalize path has no work-unit Manifest and stages the batch deliberately."""
        repo = _real_repo(tmp_path)
        (repo / "theirs.py").write_text("batch content\n")

        GitOpsService().commit_local("caylent-solutions/git-repo", repo, "feature/x", "finalize", stage_all=True)

        assert "theirs.py" in _committed_files(repo)

    def test_stage_all_and_manifest_together_is_a_contradiction(self, tmp_path: Path) -> None:
        repo = _real_repo(tmp_path)

        with pytest.raises(ValueError, match="mutually exclusive"):
            GitOpsService().commit_local(
                "caylent-solutions/git-repo",
                repo,
                "feature/x",
                "msg",
                manifest_files=["mine.py"],
                stage_all=True,
            )


@pytest.mark.unit
class TestStagedDeletionPathspecExclusion:
    """A Manifest delete row commits cleanly (db-310).

    On git 2.55.0, once the executor ``git rm``s a Manifest delete-row path,
    the file is gone from the worktree, so ``_stage_for_commit`` re-adding it
    via a plain pathspec (``git add -- <path>``) dies with
    ``fatal: pathspec '<path>' did not match any files`` (exit 128) even
    though the deletion is already correctly staged. ``_stage_for_commit``
    must exclude exactly the paths that are BOTH already staged as deletions
    AND absent from the worktree, so the commit succeeds without losing
    fail-fast behavior on a genuinely bogus path. Spec FR-14, AC-33, AC-34.
    """

    def test_commit_local_handles_manifest_delete_row(self, tmp_path: Path) -> None:
        """A delete-row (`git rm`) plus another change commits cleanly. AC-33"""
        repo = _real_repo(tmp_path)
        subprocess.run(["git", "-C", str(repo), "rm", "-q", "theirs.py"], check=True)
        (repo / "mine.py").write_text("edited by the unit\n")  # left unstaged

        GitOpsService().commit_local(
            "caylent-solutions/git-repo",
            repo,
            "feature/x",
            "E0-F1-S1-T1: delete row plus edit",
            manifest_files=["mine.py", "theirs.py"],
        )

        committed = _committed_files(repo)
        assert committed == {"mine.py", "theirs.py"}
        assert not (repo / "theirs.py").exists()
        assert (repo / "mine.py").read_text() == "edited by the unit\n"

    def test_commit_local_manifest_all_deletions_skips_add(self, tmp_path: Path) -> None:
        """An all-deletions Manifest skips `git add` entirely; the deletion still commits. AC-34"""
        judge = GitOpsService()
        repo = _real_repo(tmp_path)
        subprocess.run(["git", "-C", str(repo), "rm", "-q", "theirs.py"], check=True)

        with patch.object(judge, "_git", wraps=judge._git) as spy:
            judge.commit_local(
                "caylent-solutions/git-repo",
                repo,
                "feature/x",
                "E0-F1-S1-T1: deletion only",
                manifest_files=["theirs.py"],
            )

        add_calls = [call.args[0] for call in spy.call_args_list if call.args[0][0] == "add"]
        assert add_calls == [], f"git add must be skipped, all Manifest paths already staged-deleted: {add_calls}"
        assert _committed_files(repo) == {"theirs.py"}
        assert not (repo / "theirs.py").exists()

    def test_commit_local_manifest_bogus_path_still_fails(self, tmp_path: Path) -> None:
        """A bogus, never-existed Manifest path still trips `git add`'s exit-128 fail-fast. AC-34"""
        repo = _real_repo(tmp_path)

        with pytest.raises(RuntimeError, match=r"did not match any files"):
            GitOpsService().commit_local(
                "caylent-solutions/git-repo",
                repo,
                "feature/x",
                "msg",
                manifest_files=["never-existed.py"],
            )

    def test_commit_local_manifest_unstaged_rm_deletion_still_committed(self, tmp_path: Path) -> None:
        """A plain (unstaged) `rm` of a Manifest path is still staged and committed. FR-14 boundary"""
        repo = _real_repo(tmp_path)
        (repo / "theirs.py").unlink()  # plain rm, NOT `git rm` -- deletion is not yet staged

        GitOpsService().commit_local(
            "caylent-solutions/git-repo",
            repo,
            "feature/x",
            "E0-F1-S1-T1: unstaged deletion",
            manifest_files=["theirs.py"],
        )

        assert _committed_files(repo) == {"theirs.py"}
        assert not (repo / "theirs.py").exists()

    def test_commit_local_manifest_git_rm_then_recreated_commits_new_content(self, tmp_path: Path) -> None:
        """A `git rm`'d-then-recreated path (present in the worktree) is re-added, not excluded. FR-14 boundary"""
        repo = _real_repo(tmp_path)
        subprocess.run(["git", "-C", str(repo), "rm", "-q", "theirs.py"], check=True)
        (repo / "theirs.py").write_text("recreated content\n")

        GitOpsService().commit_local(
            "caylent-solutions/git-repo",
            repo,
            "feature/x",
            "E0-F1-S1-T1: recreate after git rm",
            manifest_files=["theirs.py"],
        )

        assert _committed_files(repo) == {"theirs.py"}
        assert (repo / "theirs.py").read_text() == "recreated content\n"

    def test_commit_and_push_excludes_staged_deletions_from_add(self, tmp_path: Path) -> None:
        """commit_and_push inherits the fix via _stage_for_commit with no per-caller edits. AC-33"""
        judge = GitOpsService()
        git_calls: list[list[str]] = []

        def stub(args: list[str], _path: Path) -> tuple[int, str, str]:
            git_calls.append(args)
            if args == ["diff", "--cached", "--name-only", "--diff-filter=D"]:
                return (0, "deleted.py\n", "")
            if args == ["status", "--porcelain"]:
                return (0, "D  deleted.py\nM  mine.py\n", "")
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, "feature/x", "")
            return (0, "", "")

        with (
            patch.object(judge, "_git", side_effect=stub),
            patch("devbench.github.git_ops.Path.exists", return_value=False),
        ):
            judge.commit_and_push(
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
                "msg",
                manifest_files=["mine.py", "deleted.py"],
            )

        add_calls = [c for c in git_calls if c[0] == "add"]
        assert add_calls == [["add", "--", "mine.py"]], f"expected only mine.py in the add pathspec, got {add_calls}"
        assert ["commit", "-m", "msg"] in git_calls
        assert ["push", "origin", "feature/x"] in git_calls


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
                stage_all=True,
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
                stage_all=True,
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
            judge.commit_local("caylent-solutions/git-repo", tmp_path, "bad branch!", "msg", stage_all=True)


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
            judge.commit_local("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)

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
            judge.commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg", stage_all=True)


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


# ---------------------------------------------------------------------------
# CIResult and wait_for_checks_and_classify tests (E7-F1-S1-T1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCIResult:
    """Test that CIResult enum values exist and behave correctly."""

    def test_green_variant_exists(self) -> None:
        assert CIResult.GREEN is CIResult.GREEN

    def test_failed_unknown_variant_exists(self) -> None:
        assert CIResult.FAILED_UNKNOWN is CIResult.FAILED_UNKNOWN

    def test_timeout_variant_exists(self) -> None:
        assert CIResult.TIMEOUT is CIResult.TIMEOUT

    def test_failed_known_task_carries_task_id(self) -> None:
        result = CIResult.FAILED_KNOWN_TASK("E1-F1-S1-T1")
        assert result.task_id == "E1-F1-S1-T1"

    def test_failed_known_task_distinct_ids_are_distinct(self) -> None:
        r1 = CIResult.FAILED_KNOWN_TASK("E1-F1-S1-T1")
        r2 = CIResult.FAILED_KNOWN_TASK("E2-F1-S1-T2")
        assert r1.task_id != r2.task_id

    def test_failed_known_task_equality(self) -> None:
        r1 = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")
        r2 = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")
        assert r1 == r2

    def test_green_is_not_failed_unknown(self) -> None:
        assert CIResult.GREEN is not CIResult.FAILED_UNKNOWN

    def test_failed_known_task_not_equal_to_non_instance(self) -> None:
        r = CIResult.FAILED_KNOWN_TASK("E1-F1-S1-T1")
        assert r.__eq__("not-a-task") is NotImplemented

    def test_failed_known_task_is_hashable(self) -> None:
        r = CIResult.FAILED_KNOWN_TASK("E1-F1-S1-T1")
        s: set[object] = {r}
        assert r in s


@pytest.mark.unit
class TestFirstFailingJobLink:
    """Unit tests for the _first_failing_job_link module-level helper."""

    def test_returns_empty_when_checks_not_a_list(self) -> None:
        from devbench.github.git_ops import _first_failing_job_link

        result = _first_failing_job_link({"not": "a list"}, {"FAILURE"})
        assert result == ""

    def test_skips_non_dict_entries(self) -> None:
        from devbench.github.git_ops import _first_failing_job_link

        result = _first_failing_job_link(
            ["not-a-dict", {"state": "FAILURE", "link": "https://example.com"}],
            {"FAILURE"},
        )
        assert result == "https://example.com"


@pytest.mark.unit
class TestWaitForChecksAndClassify:
    """Golden-fixture tests for GitOpsService.wait_for_checks_and_classify."""

    # ------------------------------------------------------------------
    # Internal fixture builder
    # ------------------------------------------------------------------

    @staticmethod
    def _one_failing_check_json(run_id: str, state: str = "FAILURE") -> str:
        """Return JSON for 'gh pr checks --json name,state,link' with one failing entry."""
        import json as _json

        return _json.dumps(
            [
                {
                    "name": "ci",
                    "state": state,
                    "link": f"https://github.com/caylent-solutions/devbench/actions/runs/{run_id}/job/1",
                }
            ]
        )

    @staticmethod
    def _run_log_with_marker(marker: str) -> str:
        """Return a fake run log containing a task marker."""
        return f"step 1: checkout\nstep 2: test run\nFAIL: test_foo failed\n{marker} commit abc123\n"

    def _classify_with_gh_stub(
        self,
        judge: GitOpsService,
        pr_url: str,
        repo_path: Path,
        gh_responses: dict[str, tuple[int, str, str]],
    ) -> object:
        """Classify pr_url using a stubbed _gh that returns from gh_responses.

        gh_responses maps a discriminating substring of the args string to the
        (rc, stdout, stderr) tuple to return.  Falls through to (1, '', '') when
        no key matches.
        """

        def fake_gh(args: list[str], **kwargs: object) -> tuple[int, str, str]:
            joined = " ".join(args)
            for key, response in gh_responses.items():
                if key in joined:
                    return response
            return (1, "", "")

        with (
            patch.object(judge, "wait_for_checks", return_value=False),
            patch.object(judge, "_gh", side_effect=fake_gh),
        ):
            return judge.wait_for_checks_and_classify(pr_url, repo_path)

    # ------------------------------------------------------------------
    # AC-FUNC-001: GREEN
    # ------------------------------------------------------------------

    def test_returns_green_when_all_checks_pass(self, tmp_path: Path) -> None:
        """wait_for_checks returns True (rc=0) => CIResult.GREEN."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/42"
        with patch.object(judge, "wait_for_checks", return_value=True):
            result = judge.wait_for_checks_and_classify(pr_url, tmp_path)
        assert result is CIResult.GREEN

    def test_returns_green_for_pr_with_no_ci(self, tmp_path: Path) -> None:
        """No-CI repos: wait_for_checks returns True => CIResult.GREEN."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/git-repo/pull/7"
        with patch.object(judge, "wait_for_checks", return_value=True):
            result = judge.wait_for_checks_and_classify(pr_url, tmp_path)
        assert result is CIResult.GREEN

    # ------------------------------------------------------------------
    # AC-FUNC-004: TIMEOUT
    # ------------------------------------------------------------------

    def test_returns_timeout_when_gh_pr_checks_watch_times_out(self, tmp_path: Path) -> None:
        """subprocess.TimeoutExpired during wait_for_checks => CIResult.TIMEOUT."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/99"
        with patch.object(
            judge,
            "wait_for_checks",
            side_effect=subprocess.TimeoutExpired(cmd="gh pr checks", timeout=300),
        ):
            result = judge.wait_for_checks_and_classify(pr_url, tmp_path)
        assert result is CIResult.TIMEOUT

    # ------------------------------------------------------------------
    # AC-FUNC-002: FAILED_KNOWN_TASK
    # ------------------------------------------------------------------

    def test_returns_failed_known_task_single_marker_in_log(self, tmp_path: Path) -> None:
        """Exactly one task marker in the failing job log => FAILED_KNOWN_TASK."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/10"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("9999"), ""),
                "run view": (0, self._run_log_with_marker("[E3-F2-S1-T5]"), ""),
            },
        )
        assert isinstance(result, CIResult.FAILED_KNOWN_TASK)
        assert result.task_id == "E3-F2-S1-T5"

    def test_returns_failed_known_task_marker_at_start_of_line(self, tmp_path: Path) -> None:
        """Marker at line start (position 0) is matched."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/11"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("1001"), ""),
                "run view": (0, "[E5-F1-S1-T2] commit abc\nrest of output", ""),
            },
        )
        assert isinstance(result, CIResult.FAILED_KNOWN_TASK)
        assert result.task_id == "E5-F1-S1-T2"

    def test_returns_failed_known_task_marker_in_middle_of_log(self, tmp_path: Path) -> None:
        """Marker embedded in middle of log is still matched."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/12"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("2002", "TIMED_OUT"), ""),
                "run view": (0, "line1\nline2 prefix [E10-F3-S2-T1] commit xyz suffix\nline3", ""),
            },
        )
        assert isinstance(result, CIResult.FAILED_KNOWN_TASK)
        assert result.task_id == "E10-F3-S2-T1"

    def test_returns_failed_known_task_marker_at_end_of_log(self, tmp_path: Path) -> None:
        """Marker at last line of log is matched."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/13"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("3003"), ""),
                "run view": (0, "line1\nline2\nstep result=fail [E7-F1-S1-T1]", ""),
            },
        )
        assert isinstance(result, CIResult.FAILED_KNOWN_TASK)
        assert result.task_id == "E7-F1-S1-T1"

    # ------------------------------------------------------------------
    # AC-FUNC-003: FAILED_UNKNOWN sub-cases
    # ------------------------------------------------------------------

    def test_returns_failed_unknown_when_no_marker_in_log(self, tmp_path: Path) -> None:
        """No task marker in the log => FAILED_UNKNOWN."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/20"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("5005"), ""),
                "run view": (0, "step 1: checkout\nstep 2: test run\nFAIL: test_foo failed\nno marker", ""),
            },
        )
        assert result is CIResult.FAILED_UNKNOWN

    def test_returns_failed_unknown_when_multiple_distinct_markers_in_log(self, tmp_path: Path) -> None:
        """Multiple distinct task markers => FAILED_UNKNOWN (ambiguous attribution)."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/21"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("6006"), ""),
                "run view": (0, "[E1-F1-S1-T1] commit aaa\n[E2-F1-S1-T2] commit bbb\nFAIL", ""),
            },
        )
        assert result is CIResult.FAILED_UNKNOWN

    def test_returns_failed_unknown_when_log_fetch_fails(self, tmp_path: Path) -> None:
        """gh run view returns non-zero => FAILED_UNKNOWN."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/22"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("7007"), ""),
                "run view": (1, "", "error fetching log"),
            },
        )
        assert result is CIResult.FAILED_UNKNOWN

    def test_returns_failed_unknown_when_no_failing_job_link(self, tmp_path: Path) -> None:
        """No failing check with a link in gh pr checks output => FAILED_UNKNOWN."""
        import json as _json

        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/23"
        checks_json = _json.dumps([{"name": "ci", "state": "SUCCESS", "link": ""}])
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {"--json name,state,link": (0, checks_json, "")},
        )
        assert result is CIResult.FAILED_UNKNOWN

    def test_returns_failed_unknown_when_pr_checks_json_fails(self, tmp_path: Path) -> None:
        """gh pr checks --json exits non-zero => FAILED_UNKNOWN."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/24"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {"--json name,state,link": (1, "", "error")},
        )
        assert result is CIResult.FAILED_UNKNOWN

    def test_returns_failed_unknown_when_pr_checks_json_is_malformed(self, tmp_path: Path) -> None:
        """gh pr checks --json returns malformed JSON => FAILED_UNKNOWN."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/25"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {"--json name,state,link": (0, "not valid json {{{", "")},
        )
        assert result is CIResult.FAILED_UNKNOWN

    def test_extracts_repo_and_pr_number_from_url(self, tmp_path: Path) -> None:
        """Helper correctly parses owner/repo and PR number from the PR URL."""
        judge = GitOpsService()
        pr_url = "https://github.com/my-org/my-repo/pull/55"

        calls: list[tuple[str, int]] = []

        def capturing_wait(
            repo: str,
            pr_number: int,
            timeout: int | None = None,
            *,
            repo_path: Path | None = None,
        ) -> bool:
            calls.append((repo, pr_number))
            return True

        with (
            patch.object(judge, "wait_for_checks", side_effect=capturing_wait),
            patch("devbench.github.git_ops.validate_repo"),
        ):
            result = judge.wait_for_checks_and_classify(pr_url, tmp_path)

        assert result is CIResult.GREEN
        assert calls == [("my-org/my-repo", 55)]

    def test_duplicate_markers_count_as_one(self, tmp_path: Path) -> None:
        """Same task marker repeated multiple times counts as exactly one distinct marker."""
        judge = GitOpsService()
        pr_url = "https://github.com/caylent-solutions/devbench/pull/30"
        result = self._classify_with_gh_stub(
            judge,
            pr_url,
            tmp_path,
            {
                "--json name,state,link": (0, self._one_failing_check_json("8008"), ""),
                "run view": (0, "[E4-F2-S1-T3] commit aaa\n[E4-F2-S1-T3] commit bbb", ""),
            },
        )
        assert isinstance(result, CIResult.FAILED_KNOWN_TASK)
        assert result.task_id == "E4-F2-S1-T3"
