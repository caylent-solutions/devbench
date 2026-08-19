"""Per-unit checkout isolation via git worktrees.

The shared-checkout model runs every work unit in one working tree. That is
what makes a claim destructive: when a unit is interrupted its uncommitted
work is still sitting in the tree, so the next unit to claim has to displace
it before it can safely commit. :mod:`devbench.git_quarantine` exists to make
that displacement survivable, but the cheapest defect is the one that cannot
occur, and two units that never share a working tree never collide.

This module gives each unit its own git worktree under a devbench-owned
directory beside the primary checkout. A worktree is a full working directory
backed by the same object store, so it costs a checkout rather than a clone,
and removing one leaves the repository untouched.

Isolation is opt-in (``git_ops.isolate_worktrees``) and mutually exclusive
with ``git_ops.single_branch``. git refuses to check the same branch out in
two worktrees at once, and single-branch mode exists precisely so every unit
lands on one shared branch, so the two models cannot both hold. The
combination is rejected at config load rather than at the first claim, where
it would surface as an opaque git error partway through an unattended run.
Workspaces that need single-branch accumulation get their durability from
checkpointing and quarantine restore instead.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Directory name, beside the primary checkout, holding every per-unit
# worktree for that repo. Named so an operator listing the parent directory
# can tell at a glance that devbench owns it.
WORKTREE_DIR_NAME = ".devbench-worktrees"

# Bounded timeout (seconds) for each git invocation, matching the quarantine
# path: a hung git process must surface rather than stall a claim.
_GIT_TIMEOUT: int = 120


def _git(repo_path: Path, args: list[str]) -> str:
    """Run a git command in ``repo_path`` and return stdout.

    Raises:
        RuntimeError: If git times out or exits non-zero. A failed worktree
            operation is never swallowed: continuing would run the unit
            against a checkout that is not the one it was promised.
    """
    printable = " ".join(args)
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"worktree: git {printable} timed out in {repo_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"worktree: git {printable} failed in {repo_path} (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def worktree_root(repo_path: Path) -> Path:
    """Return the directory holding every per-unit worktree for ``repo_path``.

    Placed beside the checkout rather than inside it so the worktrees are
    never candidates for the repo's own status, ignore rules, or cleanup
    sweeps.
    """
    return repo_path.parent / WORKTREE_DIR_NAME / repo_path.name


def unit_worktree_path(repo_path: Path, unit_id: str) -> Path:
    """Return the worktree path ``unit_id`` uses for ``repo_path``."""
    return worktree_root(repo_path) / unit_id


def unit_branch_name(unit_id: str, branch_prefix: str | None = None) -> str:
    """Return the branch checked out in ``unit_id``'s isolated worktree.

    Each worktree needs a branch of its own, because git allows a given
    branch to be checked out in exactly one worktree at a time. The name is
    derived from the unit ID so the mapping is recoverable from either
    direction without consulting state.
    """
    lowered = unit_id.lower()
    return f"{branch_prefix}/devbench/{lowered}" if branch_prefix else f"devbench/{lowered}"


def list_unit_worktrees(repo_path: Path) -> tuple[str, ...]:
    """Return the unit IDs that currently have an isolated worktree, sorted."""
    root = worktree_root(repo_path)
    if not root.is_dir():
        return ()
    return tuple(sorted(child.name for child in root.iterdir() if child.is_dir()))


def ensure_unit_worktree(repo_path: Path, unit_id: str, base_ref: str, branch_prefix: str | None = None) -> Path:
    """Return ``unit_id``'s isolated worktree, creating it from ``base_ref`` if absent.

    Idempotent: an existing worktree is returned untouched, which is what
    makes this safe to call on every claim. That is also what preserves an
    interrupted unit's work -- the unit re-claims, finds the worktree it left
    behind, and resumes on it, with no displacement step to survive.

    Args:
        repo_path: Primary checkout, whose object store the worktree shares.
        unit_id: Work-unit ID the worktree belongs to.
        base_ref: Ref the branch is created from on first use.
        branch_prefix: Optional namespace for the branch name, so several
            workspaces sharing a downstream repo cannot collide.

    Returns:
        Absolute path to the worktree directory.

    Raises:
        RuntimeError: If a git invocation fails.
    """
    target = unit_worktree_path(repo_path, unit_id)
    if (target / ".git").exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    branch = unit_branch_name(unit_id, branch_prefix)
    existing = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    # A branch left over from an earlier worktree that was removed is reused
    # rather than recreated, so the unit's history is continuous across the
    # removal. A missing branch exits non-zero here, which is the ordinary
    # first-claim case rather than a fault.
    if existing.returncode == 0:
        _git(repo_path, ["worktree", "add", str(target), branch])
    else:
        _git(repo_path, ["worktree", "add", "-b", branch, str(target), base_ref])
    return target


def remove_unit_worktree(repo_path: Path, unit_id: str, *, force: bool = False) -> bool:
    """Remove ``unit_id``'s isolated worktree, leaving its branch in place.

    Called once a unit is terminal and its work is committed. The branch is
    deliberately kept: it is the unit's history, and deleting it here would
    make the removal destructive in exactly the way this module exists to
    avoid.

    Args:
        repo_path: Primary checkout the worktree belongs to.
        unit_id: Work-unit ID whose worktree is being removed.
        force: Remove even when the worktree holds uncommitted changes.
            Defaults to ``False`` so an unfinished unit is never discarded by
            a routine cleanup pass.

    Returns:
        ``True`` when a worktree was removed, ``False`` when there was none.

    Raises:
        RuntimeError: If the worktree exists, holds uncommitted work, and
            ``force`` is not set -- or if a git invocation fails.
    """
    target = unit_worktree_path(repo_path, unit_id)
    if not (target / ".git").exists():
        return False

    args = ["worktree", "remove", str(target)]
    if force:
        args.append("--force")
    _git(repo_path, args)
    # `git worktree remove` leaves the now-empty parent behind; clearing it
    # keeps the worktree root from accumulating dead directories over a long
    # run. Only an empty directory is removed, so nothing can be lost here.
    if target.exists() and not any(target.iterdir()):
        shutil.rmtree(target)
    return True


def prune_worktrees(repo_path: Path) -> None:
    """Drop git's administrative records for worktrees whose directories are gone.

    A worktree directory deleted outside git leaves a stale record behind that
    makes git refuse to recreate a worktree at the same path. Pruning is
    idempotent and safe to run before any create.
    """
    _git(repo_path, ["worktree", "prune"])
