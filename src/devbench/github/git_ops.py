"""Git operations module handling git and GitHub interactions.

All methods validate the target repository against the allow-list before
performing any operation.
"""

import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
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

# ---------------------------------------------------------------------------
# CIResult: structured return type for wait_for_checks_and_classify
# ---------------------------------------------------------------------------

#: Regex that matches the task-ID tag embedded in per-task commit messages,
#: e.g. ``[E3-F2-S1-T5]``.  Groups: (task_id,).
_TASK_MARKER_RE = re.compile(r"\[E\d+-F\d+-S\d+-T\d+\]")

#: CI check states that indicate a failing run.
_FAILING_CHECK_STATES: frozenset[str] = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"})


def _first_failing_job_link(checks: object, failing_states: AbstractSet[str]) -> str:
    """Return the link URL of the first failing check entry, or the empty string.

    Iterates *checks* (expected to be a ``list[dict]`` decoded from
    ``gh pr checks --json name,state,link``) and returns the first non-empty
    ``link`` value whose ``state`` is in *failing_states*.
    """
    if not isinstance(checks, list):
        return ""
    for check in checks:
        if not isinstance(check, dict):
            continue
        if check.get("state") in failing_states:
            link = str(check.get("link") or "")
            if link:
                return link
    return ""


def _extract_distinct_task_ids(log_text: str) -> list[str]:
    """Return deduplicated task IDs found in *log_text*, preserving first-seen order.

    Scans for ``[E<n>-F<n>-S<n>-T<n>]`` patterns and strips the brackets
    from each match before deduplication.
    """
    raw_matches = _TASK_MARKER_RE.findall(log_text)
    seen: set[str] = set()
    distinct: list[str] = []
    for m in raw_matches:
        task_id = m[1:-1]  # strip leading '[' and trailing ']'
        if task_id not in seen:
            seen.add(task_id)
            distinct.append(task_id)
    return distinct


@dataclass(frozen=True)
class _FailedKnownTask:
    """Carrier for a single confirmed task-ID attribution.

    Produced when the failing CI job log contains exactly one distinct
    ``[E<n>-F<n>-S<n>-T<n>]`` marker and the log fetch succeeded.
    """

    task_id: str


class CIResult:
    """Sentinel base class and namespace for CI classification outcomes.

    Three class-level singleton instances (``CIResult.GREEN``,
    ``CIResult.FAILED_UNKNOWN``, ``CIResult.TIMEOUT``) are defined as
    class attributes.  :attr:`FAILED_KNOWN_TASK` is the
    :class:`_FailedKnownTask` carrier class, which stores the attributed
    task ID.

    Usage::

        result = svc.wait_for_checks_and_classify(pr_url, repo_path)
        if result is CIResult.GREEN:
            ...
        elif isinstance(result, CIResult.FAILED_KNOWN_TASK):
            print(result.task_id)
        elif result is CIResult.FAILED_UNKNOWN:
            ...
        elif result is CIResult.TIMEOUT:
            ...
    """

    #: The :class:`_FailedKnownTask` carrier class.  Use
    #: ``isinstance(result, CIResult.FAILED_KNOWN_TASK)`` to test membership
    #: and ``result.task_id`` to retrieve the attributed ID.
    FAILED_KNOWN_TASK: type[_FailedKnownTask] = _FailedKnownTask

    #: Singleton sentinel: all CI checks passed (or the repo has no CI).
    GREEN: "CIResult"
    #: Singleton sentinel: checks failed but attribution is ambiguous.
    FAILED_UNKNOWN: "CIResult"
    #: Singleton sentinel: ``gh pr checks --watch`` exceeded the timeout.
    TIMEOUT: "CIResult"


# Sentinel instances assigned after class definition so that the forward
# reference ``"CIResult"`` in the ClassVar annotations resolves.
CIResult.GREEN = CIResult()
CIResult.FAILED_UNKNOWN = CIResult()
CIResult.TIMEOUT = CIResult()


# Public type alias used in method signatures.
CIResultType = CIResult | _FailedKnownTask


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


@dataclass(frozen=True)
class ReviewResolution:
    """Outcome of polling a PR's review state for asynchronous bot feedback.

    Used by :meth:`GitOpsService.poll_pr_review_resolution` (issue #116) to
    decide whether ``cmd_git_ops`` should merge immediately or hand control
    back to the orchestrator's executor-retry loop.

    Attributes:
        resolved: True iff the merge may proceed -- no blocking review
            decision and no unresolved comments authored by an agent in
            the configured allowlist.
        review_decision: The PR's ``reviewDecision`` field as returned by
            ``gh pr view --json``. One of ``APPROVED``,
            ``CHANGES_REQUESTED``, ``REVIEW_REQUIRED``, ``COMMENTED``, or
            empty when no decision has been recorded.
        unresolved_reviews: List of structured review records (one per
            REQUEST_CHANGES review). Each entry is a dict with keys
            ``reviewer``, ``state``, ``body``, ``submitted_at``.
        unresolved_comments: List of structured comment records (one per
            unresolved inline / review comment authored by an allowlisted
            bot). Each entry is a dict with keys ``author``, ``path``,
            ``line``, ``body``, ``created_at``.
        elapsed_seconds: How long the poll loop ran before exit. Useful
            for telemetry / debugging.
    """

    resolved: bool
    review_decision: str = ""
    unresolved_reviews: list[dict[str, str | int]] = field(default_factory=list)
    unresolved_comments: list[dict[str, str | int]] = field(default_factory=list)
    elapsed_seconds: float = 0.0


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

    @staticmethod
    def _refuse_if_local_only(operation: str) -> None:
        """Raise RuntimeError when ``git_ops.local_only`` is true.

        Defense-in-depth guard for methods that touch the git ``origin``
        remote (push, fetch from origin, force-push).  Local-only mode
        should never reach these paths -- ``cli._git_ops_deferred`` routes
        every commit through :meth:`commit_local`, finalize/tag/rebase are
        not called when ``defer_pr: true`` -- so this guard exists to
        protect against future refactors that silently re-enter a remote
        path.
        """
        if RUNTIME_CONFIG.git_ops.local_only:
            raise RuntimeError(
                f"{operation} is not available when git_ops.local_only is true. "
                "Local-only repos have no origin remote; remote-touching git "
                "operations must be skipped via the deferred / commit_local path."
            )

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

        When ``git_ops.local_only`` is true (config flag), case 3 skips the
        ``git fetch origin`` step entirely and creates the branch directly off
        the local default branch (``refs/heads/<default_branch>``). The repo
        has no origin remote in this mode; the YAML-configured
        ``default_branch`` is mandatory.

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
        elif RUNTIME_CONFIG.git_ops.local_only:
            default_branch = self._get_default_branch(repo_path, repo=repo)
            self._git(["checkout", "-b", branch, f"refs/heads/{default_branch}"], repo_path)
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
            RuntimeError: If any git command fails, or if ``git_ops.local_only``
                is true (use :meth:`commit_local` for local-only repos).
        """
        self._refuse_if_local_only("commit_and_push")
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

    def find_open_pr(self, repo: str, branch: str, *, repo_path: Path | None = None) -> str | None:
        """Return the URL of an open PR on ``branch``, or ``None`` if none exists.

        Issue #129: ``create_pr`` is invoked a second time on a branch when the
        executor refactors after REVIEW_PASS or when ``pr_review_resolution``
        pushes a fix commit. Calling ``gh pr create`` twice raises
        ``RuntimeError`` and surfaces as a hard failure. ``cmd_git_ops`` calls
        this helper first so a pre-existing open PR is reused instead.
        """
        validate_repo(repo)
        rc, stdout, _ = self._gh(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "url"],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0:
            return None
        try:
            entries = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            return None
        if not entries:
            return None
        url = entries[0].get("url")
        return url if isinstance(url, str) and url else None

    def create_pr(self, repo: str, branch: str, title: str, body: str, *, repo_path: Path | None = None) -> str:
        """Create a pull request and return its URL.

        If an open PR already exists on ``branch``, returns that PR's URL
        instead of failing -- a second git-ops invocation on a branch (after
        a REFACTOR cycle, a ``pr_review_resolution`` fix push, or a CI-retry
        replay) must reuse the existing PR rather than treating the duplicate
        ``gh pr create`` as a fatal error (issue #129).

        Args:
            repo: GitHub repository in ``owner/name`` format.
            branch: Source branch for the PR.
            title: PR title.
            body: PR body/description.
            repo_path: Local filesystem path to the repository.

        Returns:
            The URL of the (newly-created or pre-existing) pull request.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If the ``gh`` command fails AND no pre-existing PR
                          covers the failure case.
        """
        validate_repo(repo)

        existing_url = self.find_open_pr(repo, branch, repo_path=repo_path)
        if existing_url:
            self.logger.info(
                "Open PR already exists on %s for branch %s; reusing %s",
                repo,
                branch,
                existing_url,
            )
            return existing_url

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
            RuntimeError: If any git command fails, or if ``git_ops.local_only``
                is true (tags require an origin remote to push to).
        """
        self._refuse_if_local_only("create_tag")
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

    def wait_for_checks_and_classify(
        self,
        pr_url: str,
        repo_path: Path,
        timeout: int | None = None,
    ) -> CIResultType:
        """Wait for CI checks and classify the outcome as a :class:`CIResult`.

        Wraps :meth:`wait_for_checks` and, on failure, performs
        failure-attribution by:

        1. Calling ``gh pr checks --json name,state,link`` to locate the
           URL of the first failing job.
        2. Extracting the run ID from the job URL.
        3. Fetching the run log via ``gh run view <run_id> --log-failed``.
        4. Scanning the log for ``[E<n>-F<n>-S<n>-T<n>]`` task-ID markers.

        Returns:
            :attr:`CIResult.GREEN`
                All checks passed (or the repo has no CI configured).
            :class:`CIResult.FAILED_KNOWN_TASK` ``(task_id)``
                The log contains exactly one distinct task-ID marker.
            :attr:`CIResult.FAILED_UNKNOWN`
                Checks failed but attribution is ambiguous (no marker,
                multiple distinct markers, or the log fetch failed).
            :attr:`CIResult.TIMEOUT`
                ``gh pr checks --watch`` raised
                :class:`subprocess.TimeoutExpired`.

        Args:
            pr_url: Full GitHub PR URL, e.g.
                ``https://github.com/owner/repo/pull/42``.
            repo_path: Local filesystem path to the repository.
            timeout: Maximum seconds to wait per ``gh pr checks`` call.
                Defaults to the configured value.
        """
        # Parse owner/repo and PR number from the URL.
        # Expected shape: https://github.com/<owner>/<repo>/pull/<number>
        parts = pr_url.rstrip("/").split("/")
        pr_number = int(parts[-1])
        repo = f"{parts[-4]}/{parts[-3]}"

        # Phase 1: wait for checks.
        try:
            checks_passed = self.wait_for_checks(repo, pr_number, timeout, repo_path=repo_path)
        except subprocess.TimeoutExpired:
            self.logger.warning(
                "wait_for_checks_and_classify: gh pr checks --watch timed out for %s",
                pr_url,
            )
            return CIResult.TIMEOUT

        if checks_passed:
            return CIResult.GREEN

        # Phase 2: attribute the failure.
        return self._classify_ci_failure(repo, pr_number, repo_path)

    def _classify_ci_failure(
        self,
        repo: str,
        pr_number: int,
        repo_path: Path,
    ) -> CIResultType:
        """Perform failure attribution and return the appropriate CIResult.

        Called internally by :meth:`wait_for_checks_and_classify` when
        :meth:`wait_for_checks` returns False.  Returns
        :attr:`CIResult.FAILED_KNOWN_TASK` when exactly one distinct task
        marker is found in the failing job log; :attr:`CIResult.FAILED_UNKNOWN`
        otherwise.
        """
        run_id = self._find_failing_run_id_from_checks(repo, pr_number, repo_path)
        if run_id is None:
            return CIResult.FAILED_UNKNOWN

        log_text = self._fetch_failing_run_log(repo, run_id, repo_path)
        if not log_text:
            return CIResult.FAILED_UNKNOWN

        distinct_ids = _extract_distinct_task_ids(log_text)
        if len(distinct_ids) == 1:
            return CIResult.FAILED_KNOWN_TASK(distinct_ids[0])

        return CIResult.FAILED_UNKNOWN

    def _find_failing_run_id_from_checks(
        self,
        repo: str,
        pr_number: int,
        repo_path: Path,
    ) -> str | None:
        """Return the run ID for the first failing check on *pr_number*.

        Returns ``None`` when no failing check is found, the ``gh`` command
        fails, the JSON is malformed, or the job link does not contain a run ID.
        """
        rc, stdout, _ = self._gh(
            ["pr", "checks", str(pr_number), "--json", "name,state,link"],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0 or not stdout.strip():
            return None
        try:
            checks = json.loads(stdout)
        except json.JSONDecodeError:
            return None

        job_link = _first_failing_job_link(checks, _FAILING_CHECK_STATES)
        if not job_link:
            return None

        run_match = re.search(r"/actions/runs/(\d+)", job_link)
        return run_match.group(1) if run_match else None

    def _fetch_failing_run_log(
        self,
        repo: str,
        run_id: str,
        repo_path: Path,
    ) -> str:
        """Fetch the failing run log for *run_id*.

        Returns the log text, or the empty string when the ``gh`` command
        fails or the response is blank.
        """
        rc_log, log_text, _ = self._gh(
            ["run", "view", run_id, "--log-failed"],
            cwd=repo_path,
            repo=repo,
        )
        if rc_log != 0 or not log_text.strip():
            return ""
        return log_text

    def get_latest_failing_run_id(
        self,
        repo: str,
        pr_number: int,
        *,
        repo_path: Path | None = None,
    ) -> str | None:
        """Return the run ID of the most recent failing CI run for *pr_number*.

        Walks ``gh pr checks <num> --json name,state,link`` looking for the
        first FAILURE / TIMED_OUT / CANCELLED check, then extracts the run
        ID from its check-runs URL. Returns ``None`` when:

        - No failing check is reported (caller should treat as "no run to
          fetch" and skip the log fetch step entirely).
        - ``gh`` exits non-zero or returns a JSON shape we cannot parse.

        Used by :func:`devbench.cli._emit_ci_failure_feedback` (issue #115).
        """
        validate_repo(repo)
        rc, stdout, _ = self._gh(
            ["pr", "checks", str(pr_number), "--json", "name,state,link"],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0 or not stdout.strip():
            return None
        try:
            import json as _json

            checks = _json.loads(stdout)
        except _json.JSONDecodeError:
            return None
        for check in checks if isinstance(checks, list) else []:
            if not isinstance(check, dict):
                continue
            if check.get("state") in _FAILING_CHECK_STATES:
                link = check.get("link") or ""
                # ``link`` shape: https://github.com/<org>/<repo>/actions/runs/<id>/job/<job-id>
                # Extract the run ID -- the segment after ``runs/``.
                match = re.search(r"/actions/runs/(\d+)", str(link))
                if match:
                    return match.group(1)
        return None

    def fetch_run_log(
        self,
        repo: str,
        run_id: str,
        max_bytes: int,
        *,
        repo_path: Path | None = None,
    ) -> str:
        """Return the trimmed log for a failing GitHub Actions run.

        Calls ``gh run view <run_id> --log-failed`` and trims the result to
        the trailing ``max_bytes`` so the feedback payload stays bounded.
        The trimmed-from-tail bias is intentional: failing log lines are
        almost always at the end (test failures, lint findings, build
        errors). Returns the empty string on subprocess failure -- the
        caller falls back to a generic feedback message.
        """
        validate_repo(repo)
        rc, stdout, _ = self._gh(
            ["run", "view", str(run_id), "--log-failed"],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0:
            return ""
        if max_bytes <= 0 or len(stdout) <= max_bytes:
            return stdout
        # Tail-bias trim: keep the last ``max_bytes`` of the log.
        return stdout[-max_bytes:]

    def poll_pr_review_resolution(
        self,
        repo: str,
        pr_number: int,
        *,
        repo_path: Path | None = None,
        agents: tuple[str, ...] | list[str] = (),
        decision_blocks: bool = True,
        settle_seconds: int = 60,
        poll_interval: int = 5,
    ) -> ReviewResolution:
        """Poll for asynchronous PR review feedback before merging (issue #116).

        Calls ``gh pr view`` and ``gh api`` repeatedly within ``settle_seconds``,
        exiting early as soon as a blocking signal appears. A blocking signal
        is either:

        - ``reviewDecision == "CHANGES_REQUESTED"`` (when *decision_blocks* is True), or
        - one or more unresolved comments / reviews authored by a login in *agents*.

        Returns a ``ReviewResolution`` describing what was found. When the
        settle window elapses with no signal, returns ``resolved=True``.
        """
        validate_repo(repo)
        agents_set = {a.strip() for a in agents if a.strip()}
        deadline = time.monotonic() + max(0, int(settle_seconds))
        elapsed = 0.0
        review_decision = ""
        unresolved_reviews: list[dict[str, str | int]] = []
        unresolved_comments: list[dict[str, str | int]] = []
        while True:
            review_decision, reviews, comments = self._fetch_pr_review_state(repo, pr_number, repo_path=repo_path)
            unresolved_reviews = self._select_unresolved_reviews(reviews, agents_set, decision_blocks, review_decision)
            unresolved_comments = self._select_unresolved_bot_comments(comments, agents_set)
            blocking_decision = decision_blocks and review_decision == "CHANGES_REQUESTED"
            if blocking_decision or unresolved_reviews or unresolved_comments:
                elapsed = max(0.0, time.monotonic() - (deadline - max(0, int(settle_seconds))))
                return ReviewResolution(
                    resolved=False,
                    review_decision=review_decision,
                    unresolved_reviews=unresolved_reviews,
                    unresolved_comments=unresolved_comments,
                    elapsed_seconds=elapsed,
                )
            now = time.monotonic()
            if now >= deadline:
                elapsed = max(0.0, now - (deadline - max(0, int(settle_seconds))))
                return ReviewResolution(
                    resolved=True,
                    review_decision=review_decision,
                    unresolved_reviews=[],
                    unresolved_comments=[],
                    elapsed_seconds=elapsed,
                )
            time.sleep(max(1, int(poll_interval)))

    def _fetch_pr_review_state(
        self,
        repo: str,
        pr_number: int,
        *,
        repo_path: Path | None,
    ) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
        """Return ``(review_decision, reviews_list, comments_list)`` for a PR.

        Uses ``gh pr view --json reviewDecision,reviews`` for the decision +
        review list, then ``gh api repos/<repo>/pulls/<n>/comments`` for
        inline comments. Both calls degrade to empty results on subprocess
        failure so the caller can keep polling without raising.
        """
        import json as _json

        rc, stdout, _ = self._gh(
            ["pr", "view", str(pr_number), "--json", "reviewDecision,reviews"],
            cwd=repo_path,
            repo=repo,
        )
        review_decision = ""
        reviews: list[dict[str, object]] = []
        if rc == 0 and stdout.strip():
            try:
                payload = _json.loads(stdout)
                review_decision = str(payload.get("reviewDecision") or "")
                raw_reviews = payload.get("reviews") or []
                if isinstance(raw_reviews, list):
                    reviews = [r for r in raw_reviews if isinstance(r, dict)]
            except _json.JSONDecodeError:
                pass

        rc, stdout, _ = self._gh(
            ["api", f"repos/{repo}/pulls/{pr_number}/comments"],
            cwd=repo_path,
            repo=None,
        )
        comments: list[dict[str, object]] = []
        if rc == 0 and stdout.strip():
            try:
                raw_comments = _json.loads(stdout)
                if isinstance(raw_comments, list):
                    comments = [c for c in raw_comments if isinstance(c, dict)]
            except _json.JSONDecodeError:
                pass

        return review_decision, reviews, comments

    @staticmethod
    def _select_unresolved_reviews(
        reviews: list[dict[str, object]],
        agents: set[str],
        decision_blocks: bool,
        review_decision: str,
    ) -> list[dict[str, str | int]]:
        """Select REQUEST_CHANGES reviews that should block merge.

        When *decision_blocks* is False, only reviews authored by an agent
        in *agents* are returned. When True, every REQUEST_CHANGES review
        is returned (since the decision itself blocks merge).
        """
        result: list[dict[str, str | int]] = []
        for review in reviews:
            state = str(review.get("state") or "")
            if state != "CHANGES_REQUESTED":
                continue
            author = ""
            author_field = review.get("author")
            if isinstance(author_field, dict):
                author = str(author_field.get("login") or "")
            if not decision_blocks and review_decision != "CHANGES_REQUESTED" and author not in agents:
                continue
            result.append(
                {
                    "reviewer": author,
                    "state": state,
                    "body": str(review.get("body") or ""),
                    "submitted_at": str(review.get("submittedAt") or ""),
                }
            )
        return result

    @staticmethod
    def _select_unresolved_bot_comments(
        comments: list[dict[str, object]],
        agents: set[str],
    ) -> list[dict[str, str | int]]:
        """Select inline comments authored by an agent in *agents*.

        Empty *agents* yields the empty list (phase no-op).
        """
        if not agents:
            return []
        result: list[dict[str, str | int]] = []
        for comment in comments:
            user = comment.get("user")
            login = ""
            if isinstance(user, dict):
                login = str(user.get("login") or "")
            if login not in agents:
                continue
            line_value = comment.get("line")
            line_int = int(line_value) if isinstance(line_value, int) else 0
            result.append(
                {
                    "author": login,
                    "path": str(comment.get("path") or ""),
                    "line": line_int,
                    "body": str(comment.get("body") or ""),
                    "created_at": str(comment.get("created_at") or ""),
                }
            )
        return result

    def checkout_default_branch(self, repo: str, repo_path: Path) -> None:
        """Check out the default branch and pull latest changes.

        Runs ``git checkout <default_branch>`` followed by
        ``git pull origin <default_branch>`` so the working tree is on a
        clean, up-to-date default branch after a merge.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            repo_path: Local filesystem path to the repository.

        Raises:
            RuntimeError: If any git command fails, the default branch cannot
                be determined, or ``git_ops.local_only`` is true (no origin
                to pull from).
        """
        self._refuse_if_local_only("checkout_default_branch")
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
            RuntimeError: If any git command fails, or if ``git_ops.local_only``
                is true (no origin to fetch/push).
        """
        self._refuse_if_local_only("rebase_and_force_push")
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

        When ``git_ops.local_only`` is true, only step 1 is honored. The
        ``origin/HEAD`` fallback is unavailable (no remote) and raises with
        a clear "default_branch is required for local-only repos" message.

        Raises:
            RuntimeError: If no YAML branch is configured and the git fallback
                cannot determine the default branch (or the fallback is
                disabled in local-only mode).
        """
        if repo:
            configured = get_configured_default_branch(repo, RUNTIME_CONFIG)
            if configured:
                return configured

        if RUNTIME_CONFIG.git_ops.local_only:
            raise RuntimeError(
                f"Cannot determine default branch in {repo_path}: git_ops.local_only "
                f"is true but repo {repo!r} has no default_branch configured in "
                "devbench.yaml. Set repos.<repo>.default_branch explicitly; the "
                "origin/HEAD fallback is unavailable in local-only mode."
            )

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
