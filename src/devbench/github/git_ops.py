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
    CHECK_QUORUM_POLL_INTERVAL_SECONDS,
    CHECK_QUORUM_STABLE_POLLS,
    CHECK_REGISTRATION_DELAY_SECONDS,
    CHECK_REGISTRATION_RETRIES,
    GH_API_TIMEOUT,
    GITHUB_CHECK_TIMEOUT_SECONDS,
    RUNTIME_CONFIG,
    WORKSPACE_ROOT,
    get_gh_token,
    resolve_merge_strategy,
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

#: db-328 / FR-15: conclusions on a `completed` check-run that count toward a
#: green head-SHA-pinned quorum in :meth:`GitOpsService._confirm_check_quorum`.
#: Any other conclusion on a completed run (failure, cancelled, timed_out,
#: action_required, stale) refuses the merge immediately.
_GOOD_CHECK_CONCLUSIONS: frozenset[str] = frozenset({"success", "neutral", "skipped"})

#: #332 FR-3 / D-5: inline git credential helper, scoped to a single
#: invocation via ``git -c credential.helper=<this>`` (never written to
#: persistent git config). It answers a credential "get" request by reading
#: ``GH_TOKEN`` from the subprocess environment -- the token is never placed
#: in argv, a remote URL, or a log line. "store"/"erase" requests are
#: ignored (no output), so this helper never persists or evicts anything.
_GIT_CREDENTIAL_HELPER = '!f() { test "$1" = get && printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"; }; f'

#: Git subcommands that touch a remote and therefore require GH_TOKEN
#: authentication. :meth:`GitOpsService._git` resolves the token env and
#: registers the inline credential helper only when the subcommand in its
#: ``args`` (the first element) is a member of this set. Every other
#: subcommand -- add, commit, status, diff, stash, rev-parse, checkout,
#: rebase, tag, etc. -- is local-only and runs with ``env=None``, never
#: calling :func:`get_gh_token`, so purely local git operations succeed
#: even when no token is configured.
_REMOTE_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"push", "fetch", "pull", "ls-remote", "clone"})

#: E2-F9-S1-T1 / spec 4.13 / D-17: the plain PR body ``git-ops-finalize`` has
#: always produced. :meth:`GitOpsService.compose_finalize_pr_body` returns
#: this string byte-for-byte when no provenance map is configured (neither
#: ``git_ops.provenance_path`` nor ``--provenance``), so upgrading to this
#: feature never changes existing behaviour (spec Section 6).
_PLAIN_FINALIZE_PR_BODY_TEMPLATE = (
    "Accumulated commits from DevBench single-branch execution.\n\nBranch: `{branch}`\nRepo: `{repo}`"
)


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


def _render_closing_keyword_line(target_repo: str, issue: object, provenance_path: Path) -> str:
    """Render one GitHub closing-keyword line for a mapped provenance-map issue.

    Single code path for both closing-keyword forms required by spec 4.13
    (AC-E2-F9-S1-T1-2): a cross-repo issue (``repo`` present and different
    from *target_repo*) renders ``Fixes <org>/<repo>#<n>``; a same-repo
    issue (no ``repo`` key, or ``repo`` equal to *target_repo*) renders
    ``Fixes #<n>``.

    Args:
        target_repo: The ``owner/name`` repo the composed PR is opened
            against. An issue entry whose ``repo`` matches this value is
            treated as same-repo.
        issue: One entry from a provenance-map epic's ``issues`` list;
            expected to be a mapping with an integer ``number`` and an
            optional ``repo`` string.
        provenance_path: The source map's path, named in the error message
            so a malformed entry points back at the file to fix.

    Returns:
        The rendered ``Fixes ...`` line.

    Raises:
        ValueError: If *issue* is not a mapping, its ``number`` field is
            missing or not an integer, or its ``repo`` field (when present)
            is not a string matching the GitHub ``owner/name`` shape.
    """
    if not isinstance(issue, dict):
        raise ValueError(f"provenance map at '{provenance_path}' has a non-object issue entry: {issue!r}")
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise ValueError(
            f"provenance map at '{provenance_path}' has an issue entry missing an integer 'number': {issue!r}"
        )
    issue_repo = issue.get("repo")
    if issue_repo is None:
        return f"Fixes #{number}"
    if not isinstance(issue_repo, str) or not _OWNER_REPO_RE.match(issue_repo):
        raise ValueError(
            f"provenance map at '{provenance_path}' has an issue entry with an invalid 'repo' value "
            f"(expected an 'owner/name' string): {issue!r}"
        )
    if issue_repo != target_repo:
        return f"Fixes {issue_repo}#{number}"
    return f"Fixes #{number}"


def _validate_provenance_epic(epic: object, provenance_path: Path) -> dict:
    """Validate one provenance-map epic entry (spec 4.13; D-17).

    Every malformed epic shape must fail loudly naming *provenance_path*
    and the offending entry, never silently default or reach an
    un-type-checked ``len()``/iteration downstream.

    Args:
        epic: One entry from the provenance map's ``epics`` list.
        provenance_path: The source map's path, named in the error message
            so a malformed entry points back at the file to fix.

    Returns:
        *epic*, typed as ``dict`` once validated.

    Raises:
        ValueError: If *epic* is not a mapping; is missing a non-empty
            string ``name`` or ``summary`` (AC-E2-F9-S1-T1-1 requires a
            per-epic summary in the composed body, so a silent empty-string
            default would violate it); or carries an ``issues`` value that
            is present but not a list (the un-type-checked case previously
            reached ``len()`` and raised an uncaught ``TypeError``).
    """
    if not isinstance(epic, dict):
        raise ValueError(f"provenance map at '{provenance_path}' has a non-object epic entry: {epic!r}")
    name = epic.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"provenance map at '{provenance_path}' has an epic missing a non-empty 'name': {epic!r}")
    summary = epic.get("summary")
    if not isinstance(summary, str) or not summary:
        raise ValueError(f"provenance map at '{provenance_path}' has an epic missing a non-empty 'summary': {epic!r}")
    issues = epic.get("issues")
    if issues is not None and not isinstance(issues, list):
        raise ValueError(f"provenance map at '{provenance_path}' has an epic with a non-list 'issues' value: {epic!r}")
    return epic


def _load_provenance_map(provenance_path: Path) -> dict:
    """Load and parse the JSON provenance map at *provenance_path*.

    Args:
        provenance_path: Path to the provenance map (the resolved
            ``--provenance`` flag or ``git_ops.provenance_path`` config
            value).

    Returns:
        The parsed JSON object.

    Raises:
        ValueError: If the path does not exist, is unreadable, is not
            valid JSON, or does not decode to a JSON object. Every message
            names *provenance_path* (spec Section 7: loud failures name
            the offending path, never a silent fallback to the plain body).
    """
    if not provenance_path.is_file():
        raise ValueError(
            f"provenance map not found at '{provenance_path}'; configure git_ops.provenance_path "
            "or pass --provenance with an existing file, or omit both for the plain PR body"
        )
    try:
        raw_text = provenance_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"provenance map at '{provenance_path}' is unreadable: {exc}") from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"provenance map at '{provenance_path}' is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"provenance map at '{provenance_path}' must decode to a JSON object with an 'epics' "
            f"list, got {type(payload).__name__}"
        )
    return payload


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

#: Shape validator for a provenance-map issue's optional ``repo`` field
#: (spec 4.13; D-17). GitHub closing keywords only honour the
#: ``owner/name`` cross-repo form; a ``repo`` value that fails
#: this pattern (or is not a string at all) is rejected by
#: :func:`_render_closing_keyword_line` rather than rendered into a dead
#: keyword line like ``Fixes 42#1`` that silently never auto-closes the
#: mapped issue on merge.
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


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

    def _stage_for_commit(self, repo_path: Path, manifest_files: list[str] | None, stage_all: bool) -> None:
        """Stage exactly this work unit's files, or refuse.

        ``git add -A`` stages every working-tree change, so a file another
        in-flight work unit left modified-but-unstaged is swept into this unit's
        commit under this unit's message. ``assert_staged_matches_manifest``
        cannot catch that: it reads ``git diff --cached``, which by definition
        does not see unstaged changes. The victim task then fails
        ``changes_manifest`` permanently -- the files it declared are committed
        under another unit's name, and the only remedies are an operator
        override or rewriting published history on a shared branch.

        Every path here is an explicit caller decision. There is deliberately no
        degraded mode: a caller that cannot name its scope is refused rather than
        silently given a whole-tree commit, because committing everything under
        the wrong unit's name is the unrecoverable failure while refusing to
        commit is the recoverable one.

        Args:
            repo_path: Local repo root.
            manifest_files: The unit's Changes Manifest paths. Pathspec-limited
                staging still picks up deletions and unstaged modifications of
                the unit's OWN files.
            stage_all: Explicit whole-tree staging for callers that legitimately
                have no single work-unit Manifest -- the ``git-ops-finalize``
                batch commit is the only such caller.

        Raises:
            ValueError: when both *manifest_files* and *stage_all* are supplied;
                the intent is contradictory and guessing either way is unsafe.
            RuntimeError: when neither is supplied (scope unknown), or when
                *manifest_files* contains no concrete path because every entry is
                an execution-time sentinel.
        """
        if stage_all:
            if manifest_files is not None:
                raise ValueError(
                    "commit staging: manifest_files and stage_all are mutually exclusive; "
                    "pass the Manifest to scope the commit, or stage_all=True to stage the whole tree."
                )
            self._git(["add", "-A"], repo_path)
            return
        if manifest_files is None:
            raise RuntimeError(
                "commit staging: cannot determine the work unit's scope -- no Changes Manifest was "
                "supplied and stage_all was not requested. Refusing to commit rather than staging the "
                "whole working tree, which would absorb any other in-flight work unit's changes under "
                "this unit's commit message."
            )
        concrete = [f for f in manifest_files if f and not f.startswith("<")]
        if not concrete:
            raise RuntimeError(
                "commit staging: the Changes Manifest contains no concrete file paths "
                f"(got {manifest_files!r}); every entry is an execution-time sentinel, so there is no "
                "pathspec to scope the commit by. Resolve the sentinel to real paths via a manifest "
                "amendment before committing."
            )
        to_add = self._exclude_already_staged_deletions(repo_path, concrete)
        if not to_add:
            return
        self._git(["add", "--", *to_add], repo_path)

    def _exclude_already_staged_deletions(self, repo_path: Path, concrete: list[str]) -> list[str]:
        """Drop Manifest paths the executor already `git rm`'d from the add pathspec (db-310).

        On git 2.55.0, once a Manifest delete-row path is removed from the
        index via ``git rm``, the file no longer exists in the worktree, so
        re-adding it with a plain pathspec (``git add -- <path>``) dies with
        ``fatal: pathspec '<path>' did not match any files`` (exit 128) even
        though the deletion is already correctly staged. A path is safe to
        drop iff it is BOTH already staged as a deletion (``git diff --cached
        --diff-filter=D``) AND absent from the worktree -- a ``git rm``'d-then
        -recreated path stays in the pathspec so its new content is picked
        up, and a bogus, never-existed path stays in the pathspec too, so
        ``git add`` still fails fast on it.
        """
        _, deletions_out, _ = self._git(["diff", "--cached", "--name-only", "--diff-filter=D"], repo_path)
        staged_deletions = {line for line in deletions_out.splitlines() if line}
        return [f for f in concrete if not (f in staged_deletions and not (repo_path / f).exists())]

    def commit_and_push(
        self,
        repo: str,
        repo_path: Path,
        branch: str,
        message: str,
        *,
        manifest_files: list[str] | None = None,
        stage_all: bool = False,
    ) -> None:
        """Validate inputs, stage all changes, commit, and push.

        Assumes the repository is already on the correct branch -- call
        :meth:`ensure_branch` before the executor agent stages files.

        This method is idempotent: it is safe to call on restart after a partial run.

        Full operation sequence:

        1. Validate *repo* against the allow-list (``validate_repo``).
        2. Validate *branch* format against ``_BRANCH_RE`` (allowlist pattern that
           rejects consecutive special characters per git ref naming rules).
        3. Stage the unit's Manifest paths (or the whole tree when ``stage_all``).
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
        self._stage_for_commit(repo_path, manifest_files, stage_all)

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

    def commit_local(
        self,
        repo: str,
        repo_path: Path,
        branch: str,
        message: str,
        *,
        manifest_files: list[str] | None = None,
        stage_all: bool = False,
    ) -> None:
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
        self._stage_for_commit(repo_path, manifest_files, stage_all)

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

    def compose_finalize_pr_body(
        self,
        *,
        repo: str,
        branch: str,
        title: str,
        provenance_path: Path | None,
    ) -> str:
        """Compose the ``git-ops-finalize`` batch PR body (spec 4.13; D-17).

        With no provenance map configured (*provenance_path* is ``None``,
        the case when neither ``git_ops.provenance_path`` nor
        ``--provenance`` is set), returns the plain body ``git-ops-finalize``
        has always produced, byte-for-byte (spec Section 6: no config
        change alters existing behaviour).

        With a resolved *provenance_path*, reads the JSON provenance map
        and composes: *title*, one ``###``-headed per-epic summary section,
        then a closing-keyword block with one ``Fixes ...`` line per mapped
        issue (cross-repo: ``Fixes <org>/<repo>#<n>``; same-repo:
        ``Fixes #<n>`` -- both rendered by :func:`_render_closing_keyword_line`,
        AC-E2-F9-S1-T1-2). This closes issue #334: without it, a combined
        PR carries no closing-keyword block, so mapped issues never
        auto-close on merge.

        Provenance map shape (JSON)::

            {
              "epics": [
                {
                  "name": "E1: Cherry-pick integration",
                  "summary": "One-line summary of what this epic delivered.",
                  "issues": [
                    {"repo": "org/other-repo", "number": 10},
                    {"number": 335}
                  ]
                }
              ]
            }

        Args:
            repo: The ``owner/name`` repo the PR is opened against; issues
                whose ``repo`` matches this value render the same-repo
                keyword form.
            branch: The branch the plain body names (unused when a
                provenance map resolves).
            title: The PR title (also passed as ``gh pr create --title``);
                rendered as the first line of the composed body.
            provenance_path: Resolved effective path (``--provenance`` flag
                if set, else ``git_ops.provenance_path`` config, else
                ``None``).

        Returns:
            The composed PR body.

        Raises:
            ValueError: If *provenance_path* is set but missing, unreadable,
                not valid JSON, resolves to zero mapped issues, or contains
                a malformed epic (non-object, missing a non-empty
                ``name``/``summary``, or a non-list ``issues`` value) or a
                malformed issue entry (non-object, missing an integer
                ``number``, or a ``repo`` that is not a valid
                ``owner/name`` string) -- never silently degrades to the
                plain body (spec Section 7).
        """
        if provenance_path is None:
            return _PLAIN_FINALIZE_PR_BODY_TEMPLATE.format(branch=branch, repo=repo)

        payload = _load_provenance_map(provenance_path)
        epics = payload.get("epics")
        if not isinstance(epics, list):
            raise ValueError(f"provenance map at '{provenance_path}' must contain an 'epics' list")

        validated_epics = [_validate_provenance_epic(epic, provenance_path) for epic in epics]

        total_issues = sum(len(epic.get("issues") or []) for epic in validated_epics)
        if total_issues == 0:
            raise ValueError(
                f"provenance map at '{provenance_path}' contains no mapped issues; refusing to emit "
                "an empty closing-keyword block"
            )

        lines: list[str] = [title, ""]
        for epic in validated_epics:
            lines.append(f"### {epic['name']}")
            lines.append(epic["summary"])
            lines.append("")
        lines.append("### Closes")
        for epic in validated_epics:
            for issue in epic.get("issues") or []:
                lines.append(_render_closing_keyword_line(repo, issue, provenance_path))
        return "\n".join(lines)

    def merge_pr(self, repo: str, pr_number: int, *, repo_path: Path | None = None) -> None:
        """Merge a pull request using the effective merge strategy for *repo*.

        The strategy is resolved by :func:`devbench.config.resolve_merge_strategy`
        with precedence ``DEVBENCH_MERGE_STRATEGY`` env > per-repo
        ``repos.<org/repo>.merge_strategy`` > top-level ``merge_strategy`` >
        ``"squash"``.

        Args:
            repo: GitHub repository in ``owner/name`` format.
            pr_number: The PR number to merge.
            repo_path: Local filesystem path to the repository.

        Raises:
            ValueError: If the repo is not in the allow-list.
            RuntimeError: If the ``gh`` command fails.
        """
        validate_repo(repo)

        strategy = resolve_merge_strategy(repo)
        rc, _, stderr = self._gh(
            ["pr", "merge", str(pr_number), strategy.flag],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0:
            if "CONFLICTING" in stderr:
                raise ConflictingPRError(f"PR #{pr_number} on {repo} is in CONFLICTING state: {stderr.strip()}")
            raise RuntimeError(f"Failed to merge PR #{pr_number} on {repo}: {stderr.strip()}")
        self.logger.info("Merged PR #%d on %s using %s", pr_number, repo, strategy.value)

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

        Uses ``gh pr checks --watch`` with a timeout as the blocking wait.
        ``rc == 0`` from that call is NOT sufficient to declare green
        (db-328): on a many-job repo, ``gh pr checks --watch`` can return
        success after an early subset of check-runs completes while later
        jobs are still queued. Once the watch call returns ``rc == 0``, the
        verdict is gated on a head-SHA-pinned, stability-confirmed quorum
        (:meth:`_confirm_check_quorum`, FR-15): the PR head SHA is resolved
        via ``gh pr view <n> --json headRefOid``, then
        ``gh api repos/<repo>/commits/<sha>/check-runs --paginate`` is polled
        until at least one check-run is present, every check-run is
        ``completed``, every conclusion is in
        ``{success, neutral, skipped}``, and the check-run id set is
        unchanged across :data:`CHECK_QUORUM_STABLE_POLLS` consecutive
        polls (a check-run that first appears mid-poll resets stability).
        Local workflow-file count is explicitly never used as a quorum --
        ``on:``/path filters skip files and files fan out to a variable
        number of jobs.

        When ``gh`` returns ``"no checks reported"``, disambiguates between
        "repo legitimately has no CI" and "GitHub Actions has not yet
        enqueued the workflow" (issue #114) by checking the local
        ``<repo>/.github/workflows/`` directory:

        - Zero workflow files -> repo has no CI -> pass.
        - One or more workflow files -> race condition -> retry up to
          :data:`CHECK_REGISTRATION_RETRIES` times, sleeping
          :data:`CHECK_REGISTRATION_DELAY_SECONDS` between attempts.
          Fail-fast on retry exhaustion (no warn-and-pass: CLAUDE.md
          forbids fallbacks; if CI cannot be confirmed, refuse the merge).

        Args:
            repo: GitHub repository in ``owner/name`` format.
            pr_number: The PR number to watch.
            timeout: Maximum seconds to wait per ``gh pr checks`` call, and
                the budget given to the head-SHA quorum poll. Defaults to
                config value.
            repo_path: Local filesystem path to the repository. Required
                for the workflow-file disambiguation; ``None`` falls
                back to assuming the repo has no CI (legacy behaviour
                for callers that have not been migrated to pass it).

        Returns:
            ``True`` if all checks passed and the head-SHA quorum stabilized
            green, or if the repo legitimately has no CI configured.
            ``False`` if any check-run's conclusion failed, if the
            workflow-registration race exhausted its retries, or if the
            check-run quorum never stabilized within the timeout (refuse
            to merge).

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
                return self._confirm_check_quorum(repo, pr_number, repo_path, effective_timeout)
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

    def _resolve_pr_head_sha(self, repo: str, pr_number: int, repo_path: Path | None) -> str:
        """Resolve the current head commit SHA of *pr_number* via ``gh pr view``.

        Used by :meth:`_confirm_check_quorum` (db-328 / FR-15) to pin the
        check-run quorum to the exact commit ``gh pr checks --watch`` just
        confirmed, rather than trusting an ambient local workflow-file
        count. Raises ``RuntimeError`` on any ``gh`` failure or malformed
        response -- a head SHA that cannot be resolved must never be
        swallowed into a green verdict.
        """
        rc, stdout, stderr = self._gh(
            ["pr", "view", str(pr_number), "--json", "headRefOid"],
            cwd=repo_path,
            repo=repo,
        )
        if rc != 0:
            raise RuntimeError(
                f"wait_for_checks: failed to resolve head SHA for PR #{pr_number} on {repo}: {stderr.strip()}"
            )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"wait_for_checks: malformed 'gh pr view --json headRefOid' response for "
                f"PR #{pr_number} on {repo}: {stdout.strip()}"
            ) from exc
        head_sha = str(payload.get("headRefOid") or "") if isinstance(payload, dict) else ""
        if not head_sha:
            raise RuntimeError(f"wait_for_checks: empty headRefOid for PR #{pr_number} on {repo}")
        return head_sha

    def _confirm_check_quorum(
        self,
        repo: str,
        pr_number: int,
        repo_path: Path | None,
        timeout: int,
    ) -> bool:
        """Gate the ``gh pr checks --watch`` rc==0 verdict on a stable, head-SHA-pinned quorum.

        db-328 / FR-15 (OD-2=a). Resolves the PR head SHA, then polls
        ``gh api repos/<repo>/commits/<sha>/check-runs --paginate`` until:

        - at least one check-run is present,
        - every check-run's ``status`` is ``completed``,
        - every ``conclusion`` is in :data:`_GOOD_CHECK_CONCLUSIONS`, and
        - the set of check-run ids is unchanged across
          :data:`CHECK_QUORUM_STABLE_POLLS` consecutive polls (a check-run
          that first appears mid-poll resets the stability counter).

        A queued/in_progress check-run keeps polling. Any completed
        check-run with a failing conclusion returns ``False`` immediately.
        On timeout, logs the verbatim FR-15 refuse-to-merge string and
        returns ``False``. This is an active readiness poll bounded by
        *timeout*, not a sleep-based synchronization primitive.
        """
        head_sha = self._resolve_pr_head_sha(repo, pr_number, repo_path)

        start = time.monotonic()
        deadline = start + max(0, int(timeout))
        previous_ids: frozenset[str] | None = None
        stable_polls = 0
        poll_count = 0
        last_run_count = 0
        last_pending_count = 0

        while True:
            poll_count += 1
            rc, stdout, stderr = self._gh(
                ["api", f"repos/{repo}/commits/{head_sha}/check-runs", "--paginate"],
                cwd=repo_path,
                repo=None,
            )
            if rc != 0:
                raise RuntimeError(
                    f"wait_for_checks: failed to query check-runs for PR #{pr_number} on {repo} "
                    f"at head {head_sha}: {stderr.strip()}"
                )
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"wait_for_checks: malformed check-runs JSON for PR #{pr_number} on {repo} "
                    f"at head {head_sha}: {stdout.strip()}"
                ) from exc
            raw_runs = payload.get("check_runs") if isinstance(payload, dict) else None
            runs = [r for r in raw_runs if isinstance(r, dict)] if isinstance(raw_runs, list) else []

            last_run_count = len(runs)
            pending = [r for r in runs if r.get("status") != "completed"]
            last_pending_count = len(pending)
            failing = [
                r for r in runs if r.get("status") == "completed" and r.get("conclusion") not in _GOOD_CHECK_CONCLUSIONS
            ]
            if failing:
                self.logger.warning(
                    "wait_for_checks: PR #%d on %s: check-run %r failed at head %s (conclusion=%s)",
                    pr_number,
                    repo,
                    failing[0].get("name"),
                    head_sha,
                    failing[0].get("conclusion"),
                )
                return False

            current_ids = frozenset(str(r.get("id")) for r in runs)
            if runs and current_ids == previous_ids:
                stable_polls += 1
            else:
                stable_polls = 1
            previous_ids = current_ids

            is_complete = bool(runs) and not pending
            if is_complete and stable_polls >= CHECK_QUORUM_STABLE_POLLS:
                self.logger.info(
                    "wait_for_checks: PR #%d on %s: check quorum stable at head %s (%d check-runs, %d polls)",
                    pr_number,
                    repo,
                    head_sha,
                    last_run_count,
                    poll_count,
                )
                return True

            now = time.monotonic()
            if now >= deadline:
                elapsed = now - start
                self.logger.warning(
                    "wait_for_checks: PR #%d on %s: check set never stabilized at head %s within "
                    "%ds (%d check-runs, %d still pending across the last %d polls). Refusing to "
                    "merge. Inspect: gh api repos/%s/commits/%s/check-runs.",
                    pr_number,
                    repo,
                    head_sha,
                    round(elapsed),
                    last_run_count,
                    last_pending_count,
                    poll_count,
                    repo,
                    head_sha,
                )
                return False

            time.sleep(max(0, int(CHECK_QUORUM_POLL_INTERVAL_SECONDS)))

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

    @staticmethod
    def _env_with_gh_token() -> dict[str, str]:
        """Return a copy of the process environment with ``GH_TOKEN`` set.

        Shared by :meth:`_git` and :meth:`_gh` so every git/gh subprocess
        authenticates against the same resolved token
        (:func:`get_gh_token`) without duplicating the merge logic. Raises
        ``RuntimeError`` (via :func:`get_gh_token`) when no token can be
        resolved, before any subprocess runs.
        """
        return {**os.environ, "GH_TOKEN": get_gh_token()}

    def _git(self, args: list[str], cwd: Path) -> tuple[int, str, str]:
        """Run a ``git`` command, raising ``RuntimeError`` on failure.

        Token resolution and the inline credential helper are scoped to
        remote-touching subcommands only -- those listed in
        :data:`_REMOTE_GIT_SUBCOMMANDS` (``push``, ``fetch``, ``pull``,
        ``ls-remote``, ``clone``), determined from ``args[0]``. For those
        subcommands, this method authenticates on the same ``GH_TOKEN``
        :meth:`_gh` uses (:func:`get_gh_token`), carried only through the
        subprocess environment. An inline ``credential.helper``, scoped to
        this invocation via ``-c`` (never a remote URL), reads the token from
        that environment variable at credential-fetch time, so it never
        appears in ``argv``, a remote URL, or a log line (spec Section 13
        D-5). A missing token fails fast here, before any subprocess runs.

        The first ``-c credential.helper=`` (empty value) resets git's
        accumulated helper list before registering ours -- git config
        merges ``credential.helper`` entries across scopes rather than
        replacing them, so without the reset an ambient system/global
        helper would still be consulted alongside (and could still win
        over) the token-backed one, which is exactly the stale-credential
        failure mode this method exists to eliminate.

        Every other (local-only) subcommand -- add, commit, status, diff,
        stash, rev-parse, checkout, rebase, tag, etc. -- never touches a
        remote, so it runs with ``env=None`` (inherits the parent process
        environment unchanged) and never calls :func:`get_gh_token`. This
        keeps local-only git operations working even when no token is
        configured, e.g. a CI unit-test job that exports no ``GH_TOKEN``.
        """
        subcommand = args[0] if args else ""
        if subcommand in _REMOTE_GIT_SUBCOMMANDS:
            env: dict[str, str] | None = self._env_with_gh_token()
            cmd = ["git", "-c", "credential.helper=", "-c", f"credential.helper={_GIT_CREDENTIAL_HELPER}"] + args
        else:
            env = None
            cmd = ["git"] + args
        self.logger.debug("git cmd=%r cwd=%s", cmd, cwd)
        rc, stdout, stderr = run_command(cmd, cwd=cwd, env=env)
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
        env = self._env_with_gh_token()
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
