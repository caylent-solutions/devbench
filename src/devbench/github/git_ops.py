"""Git operations module handling git and GitHub interactions.

All methods validate the target repository against the allow-list before
performing any operation.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from devbench.config import (
    CHECK_REGISTRATION_DELAY_SECONDS,
    CHECK_REGISTRATION_RETRIES,
    GH_API_TIMEOUT,
    GITHUB_CHECK_TIMEOUT_SECONDS,
    MERGE_STRATEGY,
    RUNTIME_CONFIG,
    WORKSPACE_ROOT,
    get_gh_token,
    validate_repo,
)
from devbench.config_loader import get_configured_default_branch
from devbench.constants import RAW_RESPONSE_PREVIEW_CHARS
from devbench.utils.process import run_command


def _list_workflow_files(repo_path: Path | None) -> list[Path]:
    """Return every ``.github/workflows/*.y[a]ml`` file under ``repo_path``.

    Used by :meth:`GitOpsService.wait_for_checks` to disambiguate "repo
    has no CI" from "Actions has not yet enqueued the workflow" (issue
    #114). Returns an empty list when ``repo_path`` is ``None`` or the
    workflows directory is absent. The list lives here so the unit-test
    layer can monkey-patch the helper without instantiating
    ``GitOpsService``.
    """
    if repo_path is None:
        return []
    workflows_dir = repo_path / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []
    return sorted(p for p in workflows_dir.iterdir() if p.is_file() and p.suffix in {".yml", ".yaml"})


class ConflictingPRError(RuntimeError):
    """Raised when a pull request is in CONFLICTING merge state."""


# Allowlist pattern for git branch names: starts with alphanumeric, allows
# alphanumerics, dots, hyphens, underscores, and single forward slashes.
# Consecutive special chars (e.g. '//', '..', '/-') are rejected to match
# git ref naming rules (git-check-ref-format).
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9_]|[.\-/][a-zA-Z0-9_])*$")


class GitOpsService:
    """Handles git commit, push, PR creation, merging, tagging, and CI checks."""

    def __init__(self) -> None:
        self.name = "git_ops"
        self.logger = logging.getLogger(f"devbench.{self.name}")

    def assert_on_branch(self, repo_path: Path, expected_branch: str) -> None:
        """Verify the working tree is checked out to ``expected_branch``.

        Fails fast with a precise diagnostic when HEAD is on a different
        branch, in detached-HEAD state, or when ``rev-parse`` itself fails.
        Called from every commit path so a drifted HEAD can never silently
        produce an orphan-branch commit.
        """
        rc, current, err = self._git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
        if rc != 0:
            raise RuntimeError(f"git rev-parse --abbrev-ref HEAD failed in {repo_path} (exit {rc}): {err.strip()}")
        actual = current.strip()
        if actual != expected_branch:
            raise RuntimeError(
                f"Branch assertion failed in {repo_path}: expected '{expected_branch}', "
                f"HEAD is on '{actual}'. Refusing to commit on the wrong branch. "
                "Call ensure_branch() before commit_local()/commit_and_push() to switch first."
            )

    def ensure_branch(self, repo: str, repo_path: Path, branch: str) -> None:
        """Ensure the repository is on *branch*, creating it if necessary.

        Must be called before the executor agent stages any files.  Handles all
        four cases:

        1. Already on *branch*: no-op.
        2. On a different branch, tree is clean, *branch* exists locally:
           ``git checkout <branch>``.
        3. On a different branch, tree is clean, *branch* does not exist:
           ``git checkout -b <branch>``.
        4. On a different branch, tree is dirty (staged or unstaged changes):
           ``git stash`` → checkout (with or without ``-b``) → ``git stash pop``.

        A non-zero exit from ``git status --porcelain`` is a genuine git error
        and raises ``RuntimeError`` immediately (never silently treated as clean).

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Branch name to switch to or create.  Must match ``_BRANCH_RE``.

        Raises:
            ValueError: If the repo is not in the allow-list, or the branch
                name does not match the allowed format.
            RuntimeError: If ``git status --porcelain`` exits non-zero, or any
                git command fails.
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
            self.logger.info("Already on branch %s -- no-op", branch)
            return

        rc_status, status_out, status_err = run_command(["git", "status", "--porcelain"], cwd=repo_path)
        if rc_status != 0:
            raise RuntimeError(f"git status --porcelain failed (exit {rc_status}): {status_err.strip()}")
        dirty = bool(status_out.strip())

        rc_ref, _, _ = run_command(["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=repo_path)
        branch_exists = rc_ref == 0

        if dirty:
            self._git(["stash"], repo_path)

        if branch_exists:
            self._git(["checkout", branch], repo_path)
        else:
            self._git(["fetch", "origin"], repo_path)
            default_branch = self._get_default_branch(repo_path, repo=repo)
            self._git(["checkout", "-b", branch, f"origin/{default_branch}"], repo_path)

        if dirty:
            self._git(["stash", "pop"], repo_path)

        self.logger.info("Switched to branch %s in %s", branch, repo)

    def commit_and_push(self, repo: str, repo_path: Path, branch: str, message: str) -> None:
        """Validate inputs, stage all changes, commit, and push.

        Assumes the repository is already on the correct branch -- call
        :meth:`ensure_branch` before the executor agent stages files.

        This method is idempotent: it is safe to call on restart after a partial run.

        Full operation sequence:

        1. Validate *repo* against the allow-list (``validate_repo``).
        2. Validate *branch* format against ``_BRANCH_RE`` (allowlist pattern that
           rejects consecutive special characters per git ref naming rules).
        3. ``git add -A`` -- stage all working-tree changes.
        4. ``git status --porcelain`` -- check whether anything was staged.

           - If the output is **non-empty**: proceed to commit and push (steps 5-6).
           - If the output is **empty** (nothing to commit): the working tree is
             already clean, meaning a prior run completed the commit.  Skip the
             commit and evaluate whether a push is still needed:

             - Remote branch absent (``origin/<branch>`` does not exist): push.
             - Remote branch present but local HEAD differs from remote HEAD: push
               (prior run committed but push failed).
             - Remote branch present and local HEAD matches remote HEAD: skip push
               and return.  The desired state is fully achieved.

        5. ``git commit -m <message>`` -- commit staged changes (skipped when clean).
        6. ``git push origin <branch>`` -- push to the remote (skipped when remote
           is already up to date).

        All git commands are executed via :meth:`_git`, which invokes subprocess
        with a list argument (never ``shell=True``), eliminating shell injection risk.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: The branch name the repository is already on (set up by
                :meth:`ensure_branch` before the executor runs).  Used for
                validation, remote state detection, and push target.  Must match
                ``_BRANCH_RE``: starts with an alphanumeric character; subsequent
                characters are alphanumerics, underscores, or a single separator
                (``.``, ``-``, or ``/``) followed by an alphanumeric/underscore.
            message: Commit message.

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

        # Refuse to commit if HEAD has drifted off the expected branch (e.g.
        # leftover detached-HEAD state from a previous task). Prevents the
        # orphan-branch class of bug where a commit lands on backlog/<id>
        # instead of the configured single_branch.
        self.assert_on_branch(repo_path, branch)
        self._git(["add", "-A"], repo_path)

        _, status_out, _ = self._git(["status", "--porcelain"], repo_path)
        if not status_out.strip():
            self.logger.info("Nothing to commit on branch %s -- checking remote state", branch)
            rc, _, _ = run_command(["git", "rev-parse", "--verify", f"origin/{branch}"], cwd=repo_path)
            if rc != 0:
                self.logger.info("Remote branch origin/%s does not exist -- pushing", branch)
                self._git(["push", "origin", branch], repo_path)
            else:
                _, local_sha, _ = self._git(["rev-parse", "HEAD"], repo_path)
                _, remote_sha, _ = self._git(["rev-parse", f"origin/{branch}"], repo_path)
                if local_sha.strip() != remote_sha.strip():
                    self.logger.info("Local branch %s is ahead of origin -- pushing", branch)
                    self._git(["push", "origin", branch], repo_path)
                else:
                    self.logger.info("Branch %s already up to date with origin -- skipping push", branch)
            return

        self._git(["commit", "-m", message], repo_path)
        self._git(["push", "origin", branch], repo_path)
        self.logger.info("Committed and pushed to %s on %s", branch, repo)

    def commit_local(self, repo: str, repo_path: Path, branch: str, message: str) -> None:
        """Stage and commit locally without pushing.

        Used in single-branch / defer-PR mode.  Commits are accumulated
        on the branch and pushed later by ``git-ops-finalize``.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Branch name (must already be checked out).
            message: Commit message.

        Raises:
            ValueError: If the repo is not in the allow-list or branch is invalid.
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

        # Same orphan-branch protection as commit_and_push -- refuse to
        # commit when HEAD has drifted off the expected branch.
        self.assert_on_branch(repo_path, branch)
        self._git(["add", "-A"], repo_path)

        _, status_out, _ = self._git(["status", "--porcelain"], repo_path)
        if not status_out.strip():
            self.logger.info("Nothing to commit on branch %s -- skipping", branch)
            return

        self._git(["commit", "-m", message], repo_path)
        self.logger.info("Committed locally to %s on %s (push deferred)", branch, repo)

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
        """Merge a pull request using the strategy set by JUDGE_MERGE_STRATEGY.

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
            ["pr", "merge", str(pr_number), MERGE_STRATEGY.flag],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0:
            if "CONFLICTING" in stderr:
                raise ConflictingPRError(f"PR #{pr_number} on {repo} is in CONFLICTING state: {stderr.strip()}")
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
        self,
        repo: str,
        pr_number: int,
        timeout: int | None = None,
        *,
        repo_path: Path | None = None,
    ) -> bool:
        """Wait for all CI checks on a PR to complete.

        Uses ``gh pr checks --watch`` with a timeout. When ``gh`` returns
        ``"no checks reported"``, disambiguates between "repo legitimately
        has no CI" and "GitHub Actions has not yet enqueued the workflow"
        (issue #114) by checking the local ``<repo>/.github/workflows/``
        directory:

        - Zero workflow files -> repo has no CI -> pass.
        - One or more workflow files -> race condition -> retry up to
          :data:`CHECK_REGISTRATION_RETRIES` times, sleeping
          :data:`CHECK_REGISTRATION_DELAY_SECONDS` between attempts.
          Fail-fast on retry exhaustion (no warn-and-pass: CLAUDE.md
          forbids fallbacks; if CI cannot be confirmed, refuse the merge).

        Args:
            repo: GitHub repository in ``owner/name`` format.
            pr_number: The PR number to watch.
            timeout: Maximum seconds to wait per ``gh pr checks`` call.
                Defaults to config value.
            repo_path: Local filesystem path to the repository. Required
                for the workflow-file disambiguation; ``None`` falls
                back to assuming the repo has no CI (legacy behaviour
                for callers that have not been migrated to pass it).

        Returns:
            ``True`` if all checks passed, or if the repo legitimately has
            no CI configured. ``False`` if checks were reported and one
            or more failed, OR if the workflow-registration race
            exhausted its retries (the failure mode is logged with the
            elapsed wait + workflow files found).

        Raises:
            ValueError: If the repo is not in the allow-list.
        """
        validate_repo(repo)

        effective_timeout = timeout if timeout is not None else GITHUB_CHECK_TIMEOUT_SECONDS

        # Total attempts = retries + 1 (the first call is attempt 0; on
        # "no checks reported" the loop sleeps and retries up to
        # CHECK_REGISTRATION_RETRIES additional times). On exhaustion the
        # post-loop block emits the canonical refuse-to-merge error.
        workflow_files: list[Path] = []
        for attempt in range(CHECK_REGISTRATION_RETRIES + 1):
            rc, stdout, stderr = self._gh(
                ["pr", "checks", str(pr_number), "--watch"],
                timeout=effective_timeout,
                cwd=repo_path,
                repo=repo,
            )
            if rc == 0:
                self.logger.info("All checks passed for PR #%d on %s", pr_number, repo)
                return True
            if "no checks reported" not in stderr:
                self.logger.warning(
                    "Checks did not pass for PR #%d on %s: %s",
                    pr_number,
                    repo,
                    stderr.strip(),
                )
                return False
            workflow_files = _list_workflow_files(repo_path)
            if not workflow_files:
                self.logger.warning(
                    "No CI checks configured for PR #%d on %s -- treating as pass",
                    pr_number,
                    repo,
                )
                return True
            if attempt == CHECK_REGISTRATION_RETRIES:
                # Final attempt: skip the sleep; fall through to the
                # retry-exhausted block below.
                break
            self.logger.info(
                "PR #%d on %s: workflow files exist (%d) but `gh pr checks` "
                "returned 'no checks reported' on attempt %d/%d -- retrying after %ds",
                pr_number,
                repo,
                len(workflow_files),
                attempt + 1,
                CHECK_REGISTRATION_RETRIES + 1,
                CHECK_REGISTRATION_DELAY_SECONDS,
            )
            time.sleep(CHECK_REGISTRATION_DELAY_SECONDS)
        elapsed = CHECK_REGISTRATION_RETRIES * CHECK_REGISTRATION_DELAY_SECONDS
        self.logger.warning(
            "wait_for_checks gave up on PR #%d in %s after %ds: workflow files "
            "%s exist locally but `gh pr checks` keeps reporting 'no checks "
            "reported'. Refusing to merge. Investigate the GitHub Actions queue "
            "or the workflow's `on:` triggers.",
            pr_number,
            repo,
            elapsed,
            [str(p.relative_to(repo_path)) if repo_path else str(p) for p in workflow_files],
        )
        return False

    def checkout_default_branch(self, repo: str, repo_path: Path) -> None:
        """Check out the default branch and pull latest changes.

        Runs ``git checkout <default_branch>`` followed by
        ``git pull origin <default_branch>`` so the working tree is on a
        clean, up-to-date default branch after a merge.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.

        Raises:
            RuntimeError: If any git command fails or the default branch
                cannot be determined.
        """
        default_branch = self._get_default_branch(repo_path, repo=repo)
        self._git(["checkout", default_branch], repo_path)
        self._git(["pull", "origin", default_branch], repo_path)
        self.logger.info("Checked out default branch %s in %s", default_branch, repo_path)

    def rebase_and_force_push(self, repo: str, repo_path: Path, branch: str) -> None:
        """Rebase the current branch onto the remote default branch and force-push.

        Used as a one-time conflict recovery step when ``merge_pr`` raises
        :class:`ConflictingPRError`.  Runs:

        1. ``git fetch origin``
        2. ``git rebase origin/<default_branch>``
        3. ``git push --force-with-lease origin <branch>``

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.
            branch: Branch name to force-push after rebasing.

        Raises:
            RuntimeError: If any git command fails.
        """
        default_branch = self._get_default_branch(repo_path, repo=repo)
        self._git(["fetch", "origin"], repo_path)
        self._git(["rebase", f"origin/{default_branch}"], repo_path)
        self._git(["push", "--force-with-lease", "origin", branch], repo_path)
        self.logger.info("Rebased %s onto origin/%s and force-pushed", branch, default_branch)

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
        self.checkout_default_branch(repo, repo_path)

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

    def _get_default_branch(self, repo_path: Path, repo: str = "") -> str:
        """Return the default branch name for the repo (e.g. ``main``, ``main3``).

        Resolution order:
        1. YAML ``repos.<repo>.default_branch`` when *repo* is provided and configured.
        2. ``git rev-parse --abbrev-ref origin/HEAD`` fallback.

        Raises:
            RuntimeError: If no YAML branch is configured and the git fallback
                cannot determine the default branch.
        """
        if repo:
            configured = get_configured_default_branch(repo, RUNTIME_CONFIG)
            if configured:
                return configured

        rc, stdout, _ = run_command(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            cwd=repo_path,
        )
        if rc != 0 or not stdout.strip():
            raise RuntimeError(
                f"Cannot determine default branch in {repo_path}. "
                "Run 'git remote set-head origin --auto' to configure it."
            )
        return stdout.strip().removeprefix("origin/")

    def _git(self, args: list[str], cwd: Path) -> tuple[int, str, str]:
        """Run a ``git`` command, raising ``RuntimeError`` on failure."""
        cmd = ["git"] + args
        self.logger.debug("git cmd=%r cwd=%s", cmd, cwd)
        rc, stdout, stderr = run_command(cmd, cwd=cwd)
        self.logger.debug(
            "git exit=%d stdout=%r stderr=%r",
            rc,
            stdout[:RAW_RESPONSE_PREVIEW_CHARS] if stdout else "",
            stderr[:RAW_RESPONSE_PREVIEW_CHARS] if stderr else "",
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
            result.stdout[:RAW_RESPONSE_PREVIEW_CHARS] if result.stdout else "",
            result.stderr[:RAW_RESPONSE_PREVIEW_CHARS] if result.stderr else "",
        )
        return result.returncode, result.stdout, result.stderr
