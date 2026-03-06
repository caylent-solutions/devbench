"""Git operations judge that handles git and GitHub interactions.

All methods validate the target repository against the allow-list before
performing any operation.
"""

import os
import subprocess
from pathlib import Path

from judges.config import (
    GH_API_TIMEOUT,
    GITHUB_CHECK_TIMEOUT_SECONDS,
    WORKSPACE_ROOT,
    get_gh_token,
    validate_repo,
)
from judges.judges.base import BaseJudge, JudgeResult, Verdict


class GitOpsJudge(BaseJudge):
    """Handles git commit, push, PR creation, merging, tagging, and CI checks."""

    def __init__(self) -> None:
        super().__init__("git_ops")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Not used directly; GitOpsJudge exposes individual operation methods.

        Returns a PASS result as a no-op when called via the evaluate interface.
        """
        return JudgeResult(
            judge_name=self.name,
            verdict=Verdict.PASS,
            reasoning="GitOpsJudge.evaluate is a no-op; use specific operation methods.",
            feedback="",
            evidence=[],
        )

    def commit_and_push(self, repo: str, repo_path: Path, branch: str, message: str) -> None:
        """Stage all changes, commit, and push to the remote branch.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Branch name to commit and push to.
            message: Commit message.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If any git command fails.
        """
        validate_repo(repo)

        self._git(["add", "-A"], repo_path)
        self._git(["commit", "-m", message], repo_path)
        self._git(["push", "origin", branch], repo_path)
        self.logger.info("Committed and pushed to %s on %s", branch, repo)

    def create_pr(self, repo: str, branch: str, title: str, body: str, *, repo_path: Path | None = None) -> str:
        """Create a pull request and return its URL.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            branch: Source branch for the PR.
            title: PR title.
            body: PR body/description.
            repo_path: Local filesystem path to the repository.

        Returns:
            The URL of the newly created pull request.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If the ``gh`` command fails.
        """
        validate_repo(repo)

        cmd: list[str] = [
            "pr",
            "create",
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ]

        rc, stdout, stderr = self._gh(cmd, cwd=repo_path, repo=repo)
        if rc != 0:
            raise RuntimeError(f"Failed to create PR on {repo}: {stderr.strip()}")

        pr_url = stdout.strip()
        self.logger.info("Created PR: %s", pr_url)
        return pr_url

    def merge_pr(self, repo: str, pr_number: int, *, repo_path: Path | None = None) -> None:
        """Merge a pull request using the merge commit strategy.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            pr_number: The PR number to merge.
            repo_path: Local filesystem path to the repository.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If the ``gh`` command fails.
        """
        validate_repo(repo)

        rc, _, stderr = self._gh(
            ["pr", "merge", str(pr_number), "--merge"],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0:
            raise RuntimeError(f"Failed to merge PR #{pr_number} on {repo}: {stderr.strip()}")
        self.logger.info("Merged PR #%d on %s", pr_number, repo)

    def create_tag(self, repo: str, repo_path: Path, tag: str, message: str) -> None:
        """Create an annotated git tag and push it to the remote.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            tag: Tag name (e.g. ``v1.2.3``).
            message: Tag annotation message.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If any git command fails.
        """
        validate_repo(repo)

        self._git(["tag", "-a", tag, "-m", message], repo_path)
        self._git(["push", "origin", tag], repo_path)
        self.logger.info("Created and pushed tag %s on %s", tag, repo)

    def wait_for_checks(
        self, repo: str, pr_number: int, timeout: int | None = None, *, repo_path: Path | None = None,
    ) -> bool:
        """Wait for all CI checks on a PR to complete.

        Uses ``gh pr checks --watch`` with a timeout.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            pr_number: The PR number to watch.
            timeout: Maximum seconds to wait. Defaults to config value.
            repo_path: Local filesystem path to the repository.

        Returns:
            ``True`` if all checks passed, ``False`` otherwise.

        Raises:
            ValueError: If the repo is not in the allow-list.
        """
        validate_repo(repo)

        effective_timeout = timeout if timeout is not None else GITHUB_CHECK_TIMEOUT_SECONDS

        rc, stdout, stderr = self._gh(
            ["pr", "checks", str(pr_number), "--watch"],
            timeout=effective_timeout,
            cwd=repo_path,
            repo=repo,
        )

        if rc != 0:
            self.logger.warning(
                "Checks did not pass for PR #%d on %s: %s",
                pr_number,
                repo,
                stderr.strip(),
            )
            return False

        self.logger.info("All checks passed for PR #%d on %s", pr_number, repo)
        return True

    def update_parent_submodule_ref(self, repo: str, repo_path: Path, message: str) -> None:
        """Update the parent repo's submodule reference after a merge.

        All 4 target repos are git submodules of the workspace root.
        After committing and merging inside a submodule, the parent repo
        must stage the updated submodule pointer and commit it.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the submodule.
            message: Commit message for the parent repo update.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If any git command fails.
        """
        validate_repo(repo)

        parent_path = WORKSPACE_ROOT
        submodule_name = repo_path.name

        # Pull latest default branch into the submodule so parent sees the merged commit
        default_branch = self._get_default_branch(repo_path)
        self._git(["checkout", default_branch], repo_path)
        self._git(["pull", "origin", default_branch], repo_path)

        # Stage the submodule reference update in the parent
        self._git(["add", submodule_name], parent_path)
        self._git(["commit", "-m", message], parent_path)
        self.logger.info(
            "Updated parent submodule ref for %s in %s",
            submodule_name,
            parent_path,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _git(self, args: list[str], cwd: Path) -> tuple[int, str, str]:
        """Run a ``git`` command, raising ``RuntimeError`` on failure."""
        cmd = ["git"] + args
        rc, stdout, stderr = self._run_command(cmd, cwd=cwd)
        if rc != 0:
            raise RuntimeError(f"git {' '.join(args)} failed (exit {rc}): {stderr.strip()}")
        return rc, stdout, stderr

    def _gh(
        self,
        args: list[str],
        timeout: int | None = None,
        cwd: Path | None = None,
        repo: str | None = None,
    ) -> tuple[int, str, str]:
        """Run a ``gh`` CLI command with the configured token.

        Args:
            args: Arguments to pass to ``gh``.
            timeout: Maximum seconds to wait.
            cwd: Working directory.
            repo: GitHub repository in ``owner/name`` format.  When provided,
                ``--repo`` is prepended to *args* so ``gh`` targets the correct
                repository instead of inferring it from fork metadata.
        """
        token = get_gh_token()
        env = {**os.environ, "GH_TOKEN": token}
        effective_timeout = timeout if timeout is not None else GH_API_TIMEOUT
        repo_args = ["--repo", repo] if repo else []
        cmd = ["gh"] + args + repo_args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            env=env,
            cwd=cwd,
        )
        return result.returncode, result.stdout, result.stderr
