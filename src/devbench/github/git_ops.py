"""Git operations judge that handles git and GitHub interactions.

All methods validate the target repository against the allow-list before
performing any operation.
"""

import os
import re
import subprocess
from pathlib import Path

from devbench.config import (
    GH_API_TIMEOUT,
    GITHUB_CHECK_TIMEOUT_SECONDS,
    RUNTIME_CONFIG,
    WORKSPACE_ROOT,
    MergeStrategy,
    get_gh_token,
    get_repo_merge_strategy,
    validate_repo,
)
from devbench.config_loader import get_configured_default_branch
from devbench.judges.base import BaseJudge, JudgeResult, Verdict

# Allowlist pattern for git branch names: starts with alphanumeric, allows
# alphanumerics, dots, hyphens, underscores, and single forward slashes.
# Consecutive special chars (e.g. '//', '..', '/-') are rejected to match
# git ref naming rules (git-check-ref-format).
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9_]|[.\-/][a-zA-Z0-9_])*$")

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

    def ensure_branch(self, repo: str, repo_path: Path, branch: str) -> None:
        """Ensure the repository is on *branch* before the executor stages files.

        Must be called from the orchestrator **before** ``claude_executor.execute()``
        so that the working tree is on the correct branch before any files are staged.

        Operation:

        - Already on *branch*: no-op.
        - On a different branch — check whether the tree is dirty (staged or unstaged
          changes).  If dirty: ``git stash``.
        - If *branch* exists locally: ``git checkout <branch>``.
        - If *branch* does not exist locally: ``git checkout -b <branch>``.
        - If the tree was stashed: ``git stash pop``.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Target branch name.  Must match ``_BRANCH_RE``.

        Raises:
            ValueError: If the repo is not in the allow-list, or the branch name
                does not match the allowed format.
            RuntimeError: If any git command fails.
        """
        validate_repo(repo)
        if not _BRANCH_RE.match(branch):
            raise ValueError(
                f"Invalid branch name '{branch}'. "
                "Branch names must start with an alphanumeric character and contain only "
                "alphanumerics, dots, hyphens, underscores, and forward slashes "
                "(no consecutive special characters)."
            )

        _, current_branch, _ = self._git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
        if current_branch.strip() == branch:
            self.logger.debug("Already on branch %s — no-op", branch)
            return

        rc_ref, _, _ = self._run_command(
            ["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=repo_path
        )
        branch_exists = rc_ref == 0

        _, status_out, _ = self._git(["status", "--porcelain"], repo_path)
        is_dirty = bool(status_out.strip())

        if is_dirty:
            self._git(["stash"], repo_path)

        if branch_exists:
            self._git(["checkout", branch], repo_path)
        else:
            self._git(["checkout", "-b", branch], repo_path)

        if is_dirty:
            self._git(["stash", "pop"], repo_path)

        self.logger.info("Switched to branch %s in %s", branch, repo)

    def is_committed_and_pushed(self, repo: str, repo_path: Path, branch: str) -> bool:
        """Return ``True`` when the working tree is clean and local HEAD matches ``origin/<branch>``.

        Used by the orchestrator to skip executor and judge re-runs when a prior
        iteration already committed and pushed all changes.

        Conditions for ``True``:

        1. ``git status --porcelain`` returns empty output (clean working tree).
        2. ``git rev-parse --verify origin/<branch>`` succeeds (remote branch exists).
        3. Local HEAD SHA equals remote HEAD SHA (nothing left to push).

        Returns ``False`` when any condition is not met.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Branch name to check against its remote counterpart.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If any git command fails unexpectedly.
        """
        validate_repo(repo)

        _, status_out, _ = self._git(["status", "--porcelain"], repo_path)
        if status_out.strip():
            self.logger.debug("is_committed_and_pushed: working tree is dirty on %s", branch)
            return False

        rc, _, _ = self._run_command(
            ["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=repo_path
        )
        if rc != 0:
            self.logger.debug(
                "is_committed_and_pushed: origin/%s does not exist", branch
            )
            return False

        _, local_sha, _ = self._git(["rev-parse", "HEAD"], repo_path)
        _, remote_sha, _ = self._git(["rev-parse", f"origin/{branch}"], repo_path)
        if local_sha.strip() != remote_sha.strip():
            self.logger.debug(
                "is_committed_and_pushed: local HEAD differs from origin/%s", branch
            )
            return False

        self.logger.debug(
            "is_committed_and_pushed: branch %s is clean and synced with origin", branch
        )
        return True

    def commit_and_push(self, repo: str, repo_path: Path, branch: str, message: str) -> None:
        """Stage all changes on the current branch, commit, and push.

        Assumes the repository is already on *branch* (call :meth:`ensure_branch`
        from the orchestrator before the executor runs).

        This method is idempotent: it is safe to call on restart after a partial run.

        Full operation sequence:

        1. Validate *repo* against the allow-list (``validate_repo``).
        2. ``git add -A`` — stage all working-tree changes.
        3. ``git status --porcelain`` — check whether anything was staged.

           - If the output is **non-empty**: proceed to commit and push (steps 4-5).
           - If the output is **empty** (nothing to commit): the working tree is
             already clean, meaning a prior run completed the commit.  Skip the
             commit and evaluate whether a push is still needed:

             - Remote branch absent (``origin/<branch>`` does not exist): push.
             - Remote branch present but local HEAD differs from remote HEAD: push
               (prior run committed but push failed).
             - Remote branch present and local HEAD matches remote HEAD: skip push
               and return.  The desired state is fully achieved.

        4. ``git commit -m <message>`` — commit staged changes (skipped when clean).
        5. ``git push origin <branch>`` — push to the remote (skipped when remote
           is already up to date).

        All git commands are executed via :meth:`_git`, which invokes subprocess
        with a list argument (never ``shell=True``), eliminating shell injection risk.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Branch name (must already be checked out).  Branch name
                validation is performed by :meth:`ensure_branch` before execution.
            message: Commit message.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If any git command fails.
        """
        validate_repo(repo)

        self._git(["add", "-A"], repo_path)

        _, status_out, _ = self._git(["status", "--porcelain"], repo_path)
        if not status_out.strip():
            self.logger.info("Nothing to commit on branch %s — checking remote state", branch)
            rc, _, _ = self._run_command(
                ["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=repo_path
            )
            if rc != 0:
                self.logger.info("Remote branch origin/%s does not exist — pushing", branch)
                self._git(["push", "origin", branch], repo_path)
            else:
                _, local_sha, _ = self._git(["rev-parse", "HEAD"], repo_path)
                _, remote_sha, _ = self._git(["rev-parse", f"origin/{branch}"], repo_path)
                if local_sha.strip() != remote_sha.strip():
                    self.logger.info(
                        "Local branch %s is ahead of origin — pushing", branch
                    )
                    self._git(["push", "origin", branch], repo_path)
                else:
                    self.logger.info(
                        "Branch %s already up to date with origin — skipping push", branch
                    )
            return

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

        base_branch = get_configured_default_branch(repo, RUNTIME_CONFIG)
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
        if base_branch:
            cmd += ["--base", base_branch]

        rc, stdout, stderr = self._gh(cmd, cwd=repo_path, repo=repo)
        if rc != 0:
            raise RuntimeError(f"Failed to create PR on {repo}: {stderr.strip()}")

        pr_url = stdout.strip()
        self.logger.info("Created PR: %s", pr_url)
        return pr_url

    def merge_pr(self, repo: str, pr_number: int, *, repo_path: Path | None = None) -> None:
        """Merge a pull request using the per-repo or global merge strategy.

        The strategy is resolved by :func:`~devbench.config.get_repo_merge_strategy`,
        which checks for a per-repo ``merge_strategy`` override in the YAML config
        before falling back to the global default.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            pr_number: The PR number to merge.
            repo_path: Local filesystem path to the repository.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If the ``gh`` command fails.
        """
        validate_repo(repo)

        strategy = MergeStrategy(get_repo_merge_strategy(repo))
        rc, _, stderr = self._gh(
            ["pr", "merge", str(pr_number), strategy.flag],
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
            if "no checks reported" in stderr:
                self.logger.warning(
                    "No CI configured for PR #%d on %s — treating as pass",
                    pr_number,
                    repo,
                )
                return True
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
        default_branch = self._get_default_branch(repo_path, repo=repo)
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
        self.logger.debug("git cmd=%r cwd=%s", cmd, cwd)
        rc, stdout, stderr = self._run_command(cmd, cwd=cwd)
        self.logger.debug(
            "git exit=%d stdout=%r stderr=%r",
            rc,
            stdout[:500] if stdout else "",
            stderr[:500] if stderr else "",
        )
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
        self.logger.debug("gh cmd=%r cwd=%s", cmd, cwd)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            env=env,
            cwd=cwd,
        )
        self.logger.debug(
            "gh exit=%d stdout=%r stderr=%r",
            result.returncode,
            result.stdout[:500] if result.stdout else "",
            result.stderr[:500] if result.stderr else "",
        )
        return result.returncode, result.stdout, result.stderr
