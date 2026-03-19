"""Git operations judge that handles git and GitHub interactions.

All public methods accept a ``RepoConfig`` instance which carries the
fully-qualified repo name, local path, and branch configuration.

Backward compatibility: the five mutating methods (``commit_and_push``,
``create_pr``, ``merge_pr``, ``wait_for_checks``, ``create_tag``) also
accept the legacy ``(repo: str, repo_path: Path, ...)`` calling
convention for tests written before the RepoConfig migration (T1).
The legacy form is resolved internally to a RepoConfig before dispatch.
New callers must use the RepoConfig form.  Legacy support will be
removed when the judge functional tests are migrated in T2.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from devbench.config import (
    GH_API_TIMEOUT,
    GITHUB_CHECK_TIMEOUT_SECONDS,
    WORKSPACE_ROOT,
    MergeStrategy,
    get_gh_token,
    get_repo_merge_strategy,
)
from devbench.config_loader import RepoConfig
from devbench.judges.base import BaseJudge, JudgeResult, Verdict

# Allowlist pattern for git branch names: starts with alphanumeric, allows
# alphanumerics, dots, hyphens, underscores, and single forward slashes.
# Consecutive special chars (e.g. '//', '..', '/-') are rejected to match
# git ref naming rules (git-check-ref-format).
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9_]|[.\-/][a-zA-Z0-9_])*$")


def _coerce_repo_config(
    repo_or_config: str | RepoConfig,
    repo_path: Path | None = None,
) -> RepoConfig:
    """Return a RepoConfig from either a RepoConfig or a legacy (str, Path) pair.

    When *repo_or_config* is already a ``RepoConfig`` the value is returned
    unchanged.  When it is a ``str`` (legacy calling convention) a minimal
    ``RepoConfig`` is constructed from the string name and the *repo_path*
    positional argument.

    Raises:
        ValueError: When a string repo name is supplied but *repo_path* is
            ``None``.
    """
    if isinstance(repo_or_config, RepoConfig):
        return repo_or_config
    repo_name: str = repo_or_config
    if repo_path is None:
        raise ValueError(
            f"repo_path must be provided when repo is passed as a string (got '{repo_name}')"
        )
    short_name = repo_name.split("/", maxsplit=1)[1] if "/" in repo_name else repo_name
    return RepoConfig(
        name=repo_name,
        short_name=short_name,
        local_path=repo_path,
        default_branch=None,
    )


class GitOpsJudge(BaseJudge):
    """Handles git commit, push, PR creation, merging, tagging, and CI checks."""

    def __init__(self) -> None:
        super().__init__("git_ops")

    def evaluate(self, work_unit_path: Path, repo_config: RepoConfig, **kwargs: object) -> JudgeResult:
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

    def ensure_branch(self, repo_config: RepoConfig, branch: str) -> None:
        """Ensure the repository is on *branch* before the executor stages files.

        Must be called from the orchestrator **before** ``claude_executor.execute()``
        so that the working tree is on the correct branch before any files are staged.

        Operation:

        - Already on *branch*: no-op.
        - On a different branch — check whether the tree is dirty (staged or unstaged
          changes).  If dirty: ``git stash``.
        - If *branch* exists locally: ``git checkout <branch>``.
        - If *branch* does not exist locally: ``git fetch origin``, then
          ``git checkout -b <branch> origin/<default_branch>`` where
          *default_branch* comes from ``repo_config.default_branch``.
        - If the tree was stashed: ``git stash pop``.

        Args:
            repo_config: Repository configuration including name and local path.
            branch: Target branch name.  Must match ``_BRANCH_RE``.

        Raises:
            ValueError: If the branch name does not match the allowed format, or
                no ``default_branch`` is configured for the repo.
            RuntimeError: If any git command fails.
        """
        repo_path = repo_config.local_path
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
            self._git(["fetch", "origin"], repo_path)
            default_branch = repo_config.default_branch
            if not default_branch:
                raise ValueError(
                    f"No default_branch configured for repo '{repo_config.name}'. "
                    "Set default_branch in devbench.yaml."
                )
            self._git(["checkout", "-b", branch, f"origin/{default_branch}"], repo_path)

        if is_dirty:
            self._git(["stash", "pop"], repo_path)

        self.logger.info("Switched to branch %s in %s", branch, repo_config.name)

    def is_committed_and_pushed(self, repo_config: RepoConfig, branch: str) -> bool:
        """Return ``True`` when the working tree is clean and local HEAD matches ``origin/<branch>``.

        Used by the orchestrator to skip executor and judge re-runs when a prior
        iteration already committed and pushed all changes.

        Conditions for ``True``:

        1. ``git status --porcelain`` returns empty output (clean working tree).
        2. ``git rev-parse --verify origin/<branch>`` succeeds (remote branch exists).
        3. Local HEAD SHA equals remote HEAD SHA (nothing left to push).

        Returns ``False`` when any condition is not met.

        Args:
            repo_config: Repository configuration including name and local path.
            branch: Branch name to check against its remote counterpart.

        Raises:
            RuntimeError: If any git command fails unexpectedly.
        """
        repo_path = repo_config.local_path

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

    # ------------------------------------------------------------------
    # commit_and_push — backward compat: also accepts (str, Path, branch, message)
    # ------------------------------------------------------------------

    def commit_and_push(
        self,
        repo_config: str | RepoConfig,
        branch: str | Path,
        message: str = "",
        _legacy_message: str = "",
    ) -> None:
        """Stage all changes on the current branch, commit, and push.

        Preferred calling convention::

            judge.commit_and_push(repo_config, branch, message)

        Legacy calling convention (T1 backward compat, removed in T2)::

            judge.commit_and_push(repo_str, repo_path, branch, message)

        Assumes the repository is already on *branch* (call :meth:`ensure_branch`
        from the orchestrator before the executor runs).

        This method is idempotent: it is safe to call on restart after a partial run.

        Full operation sequence:

        1. ``git add -A`` — stage all working-tree changes.
        2. ``git status --porcelain`` — check whether anything was staged.

           - If the output is **non-empty**: proceed to commit and push (steps 3-4).
           - If the output is **empty** (nothing to commit): the working tree is
             already clean, meaning a prior run completed the commit.  Skip the
             commit and evaluate whether a push is still needed:

             - Remote branch absent (``origin/<branch>`` does not exist): push.
             - Remote branch present but local HEAD differs from remote HEAD: push
               (prior run committed but push failed).
             - Remote branch present and local HEAD matches remote HEAD: skip push
               and return.  The desired state is fully achieved.

        3. ``git commit -m <message>`` — commit staged changes (skipped when clean).
        4. ``git push origin <branch>`` — push to the remote (skipped when remote
           is already up to date).

        All git commands are executed via :meth:`_git`, which invokes subprocess
        with a list argument (never ``shell=True``), eliminating shell injection risk.

        Raises:
            RuntimeError: If any git command fails.
        """
        if isinstance(repo_config, RepoConfig):
            # New calling convention: commit_and_push(repo_config, branch, message)
            resolved = repo_config
            effective_branch = str(branch)
            commit_message = message
        else:
            # Legacy calling convention: commit_and_push(repo_str, repo_path, branch, message)
            # branch holds repo_path, message holds branch_str, _legacy_message holds message
            resolved = _coerce_repo_config(repo_config, Path(branch))
            effective_branch = message
            commit_message = _legacy_message

        repo_path = resolved.local_path

        self._git(["add", "-A"], repo_path)

        _, status_out, _ = self._git(["status", "--porcelain"], repo_path)
        if not status_out.strip():
            self.logger.info(
                "Nothing to commit on branch %s — checking remote state", effective_branch
            )
            rc, _, _ = self._run_command(
                ["git", "rev-parse", "--verify", f"origin/{effective_branch}"], cwd=repo_path
            )
            if rc != 0:
                self.logger.info(
                    "Remote branch origin/%s does not exist — pushing", effective_branch
                )
                self._git(["push", "origin", effective_branch], repo_path)
            else:
                _, local_sha, _ = self._git(["rev-parse", "HEAD"], repo_path)
                _, remote_sha, _ = self._git(
                    ["rev-parse", f"origin/{effective_branch}"], repo_path
                )
                if local_sha.strip() != remote_sha.strip():
                    self.logger.info(
                        "Local branch %s is ahead of origin — pushing", effective_branch
                    )
                    self._git(["push", "origin", effective_branch], repo_path)
                else:
                    self.logger.info(
                        "Branch %s already up to date with origin — skipping push",
                        effective_branch,
                    )
            return

        self._git(["commit", "-m", commit_message], repo_path)
        self._git(["push", "origin", effective_branch], repo_path)
        self.logger.info(
            "Committed and pushed to %s on %s", effective_branch, resolved.name
        )

    # ------------------------------------------------------------------
    # create_pr — backward compat: also accepts (str, branch, title, body, repo_path=path)
    # ------------------------------------------------------------------

    def create_pr(
        self,
        repo_config: str | RepoConfig,
        branch: str,
        title: str,
        body: str,
        *,
        repo_path: Path | None = None,
    ) -> str:
        """Create a pull request and return its URL.

        Preferred calling convention::

            judge.create_pr(repo_config, branch, title, body)

        Legacy calling convention (T1 backward compat, removed in T2)::

            judge.create_pr(repo_str, branch, title, body, repo_path=path)

        Args:
            repo_config: ``RepoConfig`` instance **or** (legacy) repo name string.
            branch: Source branch for the PR.
            title: PR title.
            body: PR body/description.
            repo_path: *(Legacy only)* Working directory for the repo.

        Returns:
            The URL of the newly created pull request.

        Raises:
            RuntimeError: If the ``gh`` command fails.
        """
        resolved = _coerce_repo_config(repo_config, repo_path)
        base_branch = resolved.default_branch
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

        rc, stdout, stderr = self._gh(cmd, cwd=resolved.local_path, repo=resolved.name)
        if rc != 0:
            raise RuntimeError(f"Failed to create PR on {resolved.name}: {stderr.strip()}")

        pr_url = stdout.strip()
        self.logger.info("Created PR: %s", pr_url)
        return pr_url

    # ------------------------------------------------------------------
    # merge_pr — backward compat: also accepts (str, pr_number, repo_path=path)
    # ------------------------------------------------------------------

    def merge_pr(
        self,
        repo_config: str | RepoConfig,
        pr_number: int,
        *,
        repo_path: Path | None = None,
    ) -> None:
        """Merge a pull request using the per-repo or global merge strategy.

        Preferred calling convention::

            judge.merge_pr(repo_config, pr_number)

        Legacy calling convention (T1 backward compat, removed in T2)::

            judge.merge_pr(repo_str, pr_number, repo_path=path)

        The strategy is resolved by :func:`~devbench.config.get_repo_merge_strategy`,
        which checks for a per-repo ``merge_strategy`` override in the YAML config
        before falling back to the global default.

        Args:
            repo_config: ``RepoConfig`` instance **or** (legacy) repo name string.
            pr_number: The PR number to merge.
            repo_path: *(Legacy only)* Working directory for the repo.

        Raises:
            RuntimeError: If the ``gh`` command fails.
        """
        resolved = _coerce_repo_config(repo_config, repo_path)
        strategy = MergeStrategy(get_repo_merge_strategy(resolved.name))
        rc, _, stderr = self._gh(
            ["pr", "merge", str(pr_number), strategy.flag],
            cwd=resolved.local_path,
            repo=resolved.name,
        )
        if rc != 0:
            raise RuntimeError(
                f"Failed to merge PR #{pr_number} on {resolved.name}: {stderr.strip()}"
            )
        self.logger.info("Merged PR #%d on %s", pr_number, resolved.name)

    def checkout_default_branch(self, repo_config: RepoConfig) -> None:
        """Checkout the configured default branch and pull from origin.

        Called by the orchestrator after every successful ``merge_pr`` so the
        working tree starts the next work unit from a current default branch
        rather than the stale completed feature branch.

        Operation sequence:

        1. Resolve the default branch from ``repo_config.default_branch``.
        2. ``git checkout <default_branch>``
        3. ``git pull origin <default_branch>``

        Args:
            repo_config: Repository configuration including name, local path,
                and default branch.

        Raises:
            ValueError: If no ``default_branch`` is configured for the repo.
            RuntimeError: If ``git checkout`` or ``git pull`` fails.
        """
        repo_path = repo_config.local_path
        default_branch = repo_config.default_branch
        if not default_branch:
            raise ValueError(
                f"No default_branch configured for repo '{repo_config.name}'. "
                "Set default_branch in devbench.yaml."
            )
        self._git(["checkout", default_branch], repo_path)
        self._git(["pull", "origin", default_branch], repo_path)
        self.logger.info(
            "Checked out and pulled default branch %s in %s", default_branch, repo_config.name
        )

    def rebase_onto_default(self, repo_config: RepoConfig, branch: str) -> None:
        """Rebase *branch* onto ``origin/<default_branch>`` and force-push.

        Called by the orchestrator when ``merge_pr`` fails because the PR is not
        mergeable (GitHub cannot create a clean merge commit).

        Operation sequence:

        1. ``git fetch origin`` — update remote refs.
        2. ``git rebase origin/<default_branch>`` — replay branch commits on top of
           the latest default branch.
        3. ``git push --force-with-lease origin <branch>`` — update the remote
           branch, using ``--force-with-lease`` (not ``--force``) to avoid
           overwriting concurrent pushes.

        Args:
            repo_config: Repository configuration including name, local path,
                and default branch.
            branch: The feature branch to rebase and force-push.

        Raises:
            RuntimeError: If any git command fails (e.g. rebase conflict).
        """
        repo_path = repo_config.local_path
        default_branch = repo_config.default_branch

        self._git(["fetch", "origin"], repo_path)
        self._git(["rebase", f"origin/{default_branch}"], repo_path)
        self._git(["push", "--force-with-lease", "origin", branch], repo_path)
        self.logger.info("Rebased %s onto origin/%s and force-pushed", branch, default_branch)

    # ------------------------------------------------------------------
    # create_tag — backward compat: also accepts (str, Path, tag, message)
    # ------------------------------------------------------------------

    def create_tag(
        self,
        repo_config: str | RepoConfig,
        tag_or_repo_path: str | Path,
        message_or_tag: str = "",
        message: str = "",
    ) -> None:
        """Create an annotated git tag and push it to the remote.

        Preferred calling convention::

            judge.create_tag(repo_config, tag, message)

        Legacy calling convention (T1 backward compat, removed in T2)::

            judge.create_tag(repo_str, repo_path, tag, message)

        Args:
            repo_config: ``RepoConfig`` instance **or** (legacy) repo name string.
            tag_or_repo_path: Tag name (new) **or** repo path (legacy).
            message_or_tag: Message (new) **or** tag name (legacy).
            message: *(Legacy only)* Tag annotation message.

        Raises:
            RuntimeError: If any git command fails.
        """
        if isinstance(repo_config, RepoConfig):
            # New calling convention: create_tag(repo_config, tag, message)
            resolved = repo_config
            tag = str(tag_or_repo_path)
            tag_message = message_or_tag
        else:
            # Legacy calling convention: create_tag(repo_str, repo_path, tag, message)
            resolved = _coerce_repo_config(repo_config, Path(tag_or_repo_path))
            tag = message_or_tag
            tag_message = message

        repo_path = resolved.local_path
        self._git(["tag", "-a", tag, "-m", tag_message], repo_path)
        self._git(["push", "origin", tag], repo_path)
        self.logger.info("Created and pushed tag %s on %s", tag, resolved.name)

    # ------------------------------------------------------------------
    # wait_for_checks — backward compat: also accepts (str, pr_number, repo_path=path)
    # ------------------------------------------------------------------

    def wait_for_checks(
        self,
        repo_config: str | RepoConfig,
        pr_number: int,
        timeout: int | None = None,
        *,
        repo_path: Path | None = None,
    ) -> bool:
        """Wait for all CI checks on a PR to complete.

        Preferred calling convention::

            judge.wait_for_checks(repo_config, pr_number)

        Legacy calling convention (T1 backward compat, removed in T2)::

            judge.wait_for_checks(repo_str, pr_number, repo_path=path)

        Uses ``gh pr checks --watch`` with a timeout.

        Args:
            repo_config: ``RepoConfig`` instance **or** (legacy) repo name string.
            pr_number: The PR number to watch.
            timeout: Maximum seconds to wait. Defaults to config value.
            repo_path: *(Legacy only)* Working directory for the repo.

        Returns:
            ``True`` if all checks passed, ``False`` otherwise.
        """
        resolved = _coerce_repo_config(repo_config, repo_path)
        effective_timeout = timeout if timeout is not None else GITHUB_CHECK_TIMEOUT_SECONDS

        rc, stdout, stderr = self._gh(
            ["pr", "checks", str(pr_number), "--watch"],
            timeout=effective_timeout,
            cwd=resolved.local_path,
            repo=resolved.name,
        )

        if rc != 0:
            if "no checks reported" in stderr:
                self.logger.warning(
                    "No CI configured for PR #%d on %s — treating as pass",
                    pr_number,
                    resolved.name,
                )
                return True
            self.logger.warning(
                "Checks did not pass for PR #%d on %s: %s",
                pr_number,
                resolved.name,
                stderr.strip(),
            )
            return False

        self.logger.info("All checks passed for PR #%d on %s", pr_number, resolved.name)
        return True

    def update_parent_submodule_ref(self, repo_config: RepoConfig, message: str) -> None:
        """Update the parent repo's submodule reference after a merge.

        All 4 target repos are git submodules of the workspace root.
        After committing and merging inside a submodule, the parent repo
        must stage the updated submodule pointer and commit it.

        Args:
            repo_config: Repository configuration including name, local path,
                and default branch.
            message: Commit message for the parent repo update.

        Raises:
            RuntimeError: If any git command fails.
        """
        repo_path = repo_config.local_path
        parent_path = WORKSPACE_ROOT
        submodule_name = repo_path.name

        # Pull latest default branch into the submodule so parent sees the merged commit
        default_branch = self._get_default_branch(repo_path, repo=repo_config.name)
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
