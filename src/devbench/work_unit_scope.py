"""Single ADR-12 mode-aware scope-resolution implementation (spec 4.3, PM-6).

Before this module existed, "which files does this unit touch" had four
independent answers: the mode-aware branching inline in
``devbench.cli.cmd_get_diff``, a near-copy in the reachability evidence
command, and working-tree scans in the shared-file-impact and
fixture-consistency gates. Four implementations of the same question is four
different answers every gate's attribution rule depends on -- a drift risk,
not a convenience.

:func:`resolve_changed_files` is the one place that answers it now. It
determines, for a given work unit and ADR-12 mode:

- **files** -- the unit's real Changes Manifest path set (the attribution
  boundary: a gate finding may name only files in this set).
- **mode** -- echoes the caller-supplied ADR-12 mode (:data:`MODE_PER_TASK_BRANCH`
  or :data:`MODE_DEFER_PR`), so a consumer never has to re-derive it.
- **commit_shas** -- in :data:`MODE_DEFER_PR`, this unit's own commit(s) on the
  shared branch, resolved by commit-message subject
  (``git log --grep '^<unit_id>:'``) rather than trusting ``HEAD`` (db-247):
  in single-branch + defer_pr mode, HEAD may belong to a sibling task that
  committed later. Always empty in :data:`MODE_PER_TASK_BRANCH`, where the
  branch-vs-default diff is the mode's own attribution mechanism instead.
- **scope_hash** -- the spec-4.2 ``[GATE_PASS]`` scope hash
  (:func:`devbench.gate_records.compute_scope_hash`) computed over ``files``
  and each file's current git blob hash, so a later edit to any in-scope
  file invalidates a previously-recorded gate pass.

This module composes the existing manifest primitives
(:func:`devbench.backlog.manifest.parse_manifest`,
:meth:`devbench.backlog.manager.BacklogManager._is_real_manifest_path`) rather
than re-implementing manifest parsing, and reuses
:func:`devbench.gate_records.compute_scope_hash` rather than re-implementing
the hash definition -- so the value this module returns is byte-identical to
the value the gate-record freshness rule (spec 4.2) later recomputes.

Callers -- ``devbench get-diff``, ``devbench check-manifest-scope``, and
(subsequent hardening tasks) every machine-blocking gate -- resolve scope
ONLY through this module. Complete replacement, not an addition: the
near-copies this module supersedes are deleted in the same change as their
migration, never left dormant.

Error semantics (spec 4.3, non-negotiable):

- An unknown work-unit id raises :class:`ValueError` naming the id.
- A repo path that does not exist, or is not a git work tree, raises
  :class:`ValueError` naming the path and the config key to fix.
- Git plumbing returning an exit code of 2 or greater raises
  :class:`RuntimeError` with stderr attached.
- :func:`resolve_changed_files` never returns a partial result: every error
  path raises before constructing a :class:`ScopeResult` -- a partial scope
  would silently narrow every gate that consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from devbench.backlog.manager import BacklogManager
from devbench.backlog.manifest import parse_manifest
from devbench.backlog.parser import BacklogParser
from devbench.config import BACKLOG_INDEX, BACKLOG_ROOT
from devbench.gate_records import compute_scope_hash
from devbench.utils.process import run_command


class _ManifestOwner(Protocol):
    """Structural subset of ``devbench.backlog.work_unit.WorkUnit`` this module needs.

    Depending on this narrow, module-local ``Protocol`` rather than the full
    ``WorkUnit`` dataclass is a deliberate DIP choice (depend on an
    abstraction limited to what is actually used), not a workaround: this
    module's only use of the parsed work unit is reading ``file_path`` to
    locate the Changes Manifest. It also means this module never imports
    ``WorkUnit`` purely for annotations, which -- under this module's
    ``from __future__ import annotations`` -- a type-checking-only import
    would otherwise have to live behind an ``if TYPE_CHECKING:`` guard. That
    guard block is structurally unreachable at runtime (Python never
    executes it), which would leave this module, held to 100% line coverage
    (AC-FINAL-014), permanently short of it for a branch no test could ever
    legitimately exercise.
    """

    file_path: Path


# ---------------------------------------------------------------------------
# ADR-12 modes. `MODE_PER_TASK_BRANCH` is the devbench-default workflow (each
# unit on its own branch cut from the default branch); `MODE_DEFER_PR` is the
# opt-in `git_ops.single_branch` + `git_ops.defer_pr: true` workflow (commits
# land on a shared branch, pushed only at finalize time). See
# `docs/adr/12-mode-aware-get-diff.md` for the full history.
# ---------------------------------------------------------------------------
MODE_PER_TASK_BRANCH: str = "per_task_branch"
MODE_DEFER_PR: str = "defer_pr"
ALLOWED_MODES: tuple[str, ...] = (MODE_PER_TASK_BRANCH, MODE_DEFER_PR)

# Exit codes at or above this value indicate a genuine git plumbing failure
# (spec 4.3): rc=0 is success, and this module treats rc=1 the same as
# success (an empty, well-formed result -- e.g. `git log --grep` matching
# zero commits) since none of the plumbing this module runs uses rc=1 as a
# "no results" signal the way `grep` does.
_GIT_FAILURE_THRESHOLD: int = 2

# Sentinel blob-hash value recorded for a Changes Manifest path that is not
# present on disk (e.g. a file the Manifest declares but a prior stage
# deleted). Distinguishes "this path resolved to no blob" from a genuine
# hash collision -- `compute_scope_hash` only requires a stable string per
# path, not a real git object id.
_ABSENT_BLOB_MARKER: str = "<absent>"


@dataclass(frozen=True)
class ScopeResult:
    """The resolved ADR-12 scope for one work unit.

    Attributes:
        files: The unit's real Changes Manifest path set (sorted,
            de-duplicated), filtered of placeholder/sentinel entries via
            :meth:`BacklogManager._is_real_manifest_path`. Empty for a
            verification-only unit. This is the attribution boundary: a
            gate finding may name only files in this set.
        mode: The ADR-12 mode this result was resolved under -- one of
            :data:`MODE_PER_TASK_BRANCH` or :data:`MODE_DEFER_PR`.
        commit_shas: This unit's own commit(s), resolved by commit-message
            subject. Always empty in :data:`MODE_PER_TASK_BRANCH`. In
            :data:`MODE_DEFER_PR`, empty either because the unit has not
            committed yet under its own name, or because ``files`` itself
            is empty.
        scope_hash: SHA-256 hex digest over ``files`` and each file's
            current git blob hash (spec 4.2), or the empty string when
            ``files`` is empty (there is nothing to hash).
    """

    files: list[str]
    mode: str
    commit_shas: list[str]
    scope_hash: str


def resolve_changed_files(unit_id: str, repo_path: Path, mode: str) -> ScopeResult:
    """Resolve the ADR-12 mode-aware scope for ``unit_id`` in ``repo_path``.

    Args:
        unit_id: The work-unit id to resolve, e.g. ``"E2-F3-S1-T1"``.
        repo_path: Local checkout path of the unit's target repo.
        mode: One of :data:`MODE_PER_TASK_BRANCH` or :data:`MODE_DEFER_PR`.

    Returns:
        A fully populated :class:`ScopeResult`. Never partial.

    Raises:
        ValueError: If ``mode`` is not one of :data:`ALLOWED_MODES`; if
            ``unit_id`` is not found in the backlog index; or if
            ``repo_path`` does not exist or is not a git work tree.
        devbench.backlog.manifest.ManifestParseError: If the unit's
            ``## Changes Manifest`` section is missing or malformed (a
            :class:`ValueError` subclass).
        RuntimeError: If any git plumbing command this function runs exits
            with a code of 2 or greater; the command's stderr is attached to
            the message.
    """
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unknown scope mode {mode!r} for work unit '{unit_id}'; expected one of {ALLOWED_MODES}.")

    unit = _find_work_unit(unit_id)
    files = _load_manifest_paths(unit)

    if not files:
        return ScopeResult(files=[], mode=mode, commit_shas=[], scope_hash="")

    _require_git_work_tree(repo_path, unit_id)

    commit_shas: list[str] = _resolve_task_commit_shas(unit_id, repo_path) if mode == MODE_DEFER_PR else []
    scope_hash = _compute_files_scope_hash(repo_path, files)

    return ScopeResult(files=files, mode=mode, commit_shas=commit_shas, scope_hash=scope_hash)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_work_unit(unit_id: str) -> _ManifestOwner:
    """Return the parsed work unit for ``unit_id``, case-insensitively.

    Raises:
        ValueError: If no backlog entry matches ``unit_id``.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    for unit in units:
        if unit.id.lower() == unit_id.lower():
            return unit
    raise ValueError(f"Unknown work unit id: '{unit_id}'. No matching entry in the backlog index at '{BACKLOG_INDEX}'.")


def _load_manifest_paths(unit: _ManifestOwner) -> list[str]:
    """Return ``unit``'s real Changes Manifest file paths, sorted and de-duplicated.

    ``unit.file_path`` is read directly (no fallback resolution): every
    ``unit`` this function receives was just constructed by
    :func:`_find_work_unit`'s ``BacklogParser.parse_index()`` call, which
    already resolves ``file_path`` to an absolute path and requires that
    exact path to exist on disk to build the work unit in the first place
    (``BacklogParser.parse_work_unit_file``) -- so a second, independent
    existence-and-fallback check here would be dead fallback logic, not a
    real safeguard.

    Raises:
        devbench.backlog.manifest.ManifestParseError: If the ``## Changes
            Manifest`` section is missing or malformed.
        FileNotFoundError: If ``unit.file_path`` no longer exists (a
            same-process race with something deleting the file between the
            backlog parse above and this read).
    """
    rows = parse_manifest(unit.file_path.read_text(encoding="utf-8"))
    return sorted({row.file for row in rows if BacklogManager._is_real_manifest_path(row.file)})


def _require_git_work_tree(repo_path: Path, unit_id: str) -> None:
    """Raise ``ValueError`` unless ``repo_path`` is a directory containing ``.git``.

    A pure filesystem check (no subprocess): cheaper than shelling out to
    ``git rev-parse``, and lets a missing/misconfigured repo path fail with
    an actionable message instead of a raw ``FileNotFoundError`` from the
    first subprocess call that would otherwise run with a nonexistent `cwd`.

    Raises:
        ValueError: If ``repo_path`` does not exist, is not a directory, or
            has no ``.git`` entry (directory, for an ordinary clone, or file,
            for a linked worktree).
    """
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        raise ValueError(
            f"Repo path '{repo_path}' for work unit '{unit_id}' does not exist or is not a git work "
            "tree. Fix the target repo's local checkout: check REPO_LOCAL_PATHS / the repo's "
            "'checkout_directory' entry in backlog/config/devbench.yaml."
        )


def _raise_on_git_failure(cmd: list[str], repo_path: Path, rc: int, stderr: str) -> None:
    """Raise ``RuntimeError`` naming ``cmd`` and ``stderr`` when ``rc`` signals a genuine failure."""
    if rc >= _GIT_FAILURE_THRESHOLD:
        raise RuntimeError(f"'{' '.join(cmd)}' failed in '{repo_path}' (exit {rc}): {stderr.strip()}")


def _resolve_task_commit_shas(unit_id: str, repo_path: Path) -> list[str]:
    """Return this unit's own commit sha(s) on the current branch, by commit-message subject.

    Resolves via ``git log --grep '^<unit_id>:' --format=%H`` -- the exact
    ``<unit_id>: <title>`` shape ``cmd_git_ops`` writes -- rather than
    trusting ``HEAD``, which in single-branch + defer_pr mode may belong to
    a sibling task that committed later on the shared branch (db-247). A
    task may carry more than one of its own commits (an initial commit plus
    a later ``pr_review_resolution`` fix commit); every matching sha is
    returned, most-recent first (git log's default order).

    Returns an empty list when zero commits match -- this is not an error;
    the caller decides whether an empty result is a problem in its own
    context (e.g. `devbench get-diff` treats it as fatal only when the
    working tree is ALSO clean).

    Raises:
        RuntimeError: If the underlying ``git log`` command exits >= 2.
    """
    cmd = ["git", "log", "--grep", f"^{unit_id}:", "--format=%H"]
    rc, stdout, stderr = run_command(cmd, cwd=repo_path)
    _raise_on_git_failure(cmd, repo_path, rc, stderr)
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _compute_files_scope_hash(repo_path: Path, files: list[str]) -> str:
    """Return the spec-4.2 scope hash over ``files`` at their current on-disk content.

    Blob hashes are computed via a single ``git hash-object`` invocation
    (batched across every path that currently exists on disk), reflecting
    the working-tree content regardless of staged/unstaged status -- the
    same "what would be committed" content ``get-diff`` renders. A
    Manifest path with no on-disk file (e.g. a declared file a prior stage
    deleted) is recorded with :data:`_ABSENT_BLOB_MARKER` instead of being
    silently dropped from the hash input, so its absence still moves the
    hash.

    Raises:
        RuntimeError: If ``git hash-object`` exits >= 2, or returns a
            hash count that does not match the number of paths queried.
    """
    blob_hashes: dict[str, str] = dict.fromkeys(files, _ABSENT_BLOB_MARKER)
    existing = sorted(f for f in files if (repo_path / f).is_file())
    if existing:
        cmd = ["git", "hash-object", "--", *existing]
        rc, stdout, stderr = run_command(cmd, cwd=repo_path)
        _raise_on_git_failure(cmd, repo_path, rc, stderr)
        hashes = [line.strip() for line in stdout.splitlines() if line.strip()]
        if len(hashes) != len(existing):
            raise RuntimeError(
                f"'{' '.join(cmd)}' in '{repo_path}' returned {len(hashes)} hash(es) for {len(existing)} "
                "path(s): expected exactly one hash per path."
            )
        for path, blob_hash in zip(existing, hashes, strict=True):
            blob_hashes[path] = blob_hash
    return compute_scope_hash(blob_hashes)
