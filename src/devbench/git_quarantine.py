"""Quarantine of foreign uncommitted work from the shared checkout.

The single-branch modes (``git_ops.single_branch`` with ``defer_pr``) run
every work unit in one shared checkout. A unit that blocks, or a run that is
interrupted, leaves its uncommitted changes in the tree. The next unit to
claim then inherits them: its commit absorbs a sibling's files under the
wrong unit's message, and the review judges reject it over code it does not
own and cannot fix.

devbench is meant to run unattended, so the answer cannot be to stop and wait
for an operator. This module moves the foreign work out of the way instead,
into a named git stash keyed to the unit that owns it, so the claiming unit
starts from a checkout containing only its own scope and the run continues.

Quarantine is non-destructive on purpose. The stash entry is a normal commit
object with a discoverable message (``devbench-quarantine:<owner-id>``), so
the work is recoverable with ``git stash list`` / ``git stash apply`` long
after the run has moved on.

Displaced work is handed back to its owner by :func:`restore_quarantine` when
that owner is the unit claiming the checkout, and only then. Re-injecting an
attempt into *another* unit's tree would recreate the contamination this
module exists to remove, so the restore is narrowed three ways: it matches
only entries devbench itself wrote for that exact owner, it refuses any entry
holding a path outside the claiming unit's Changes Manifest, and it refuses to
overwrite a path that already carries uncommitted work. Anything it declines
stays in the stash rather than being dropped -- an executor turn is expensive
enough that losing one to an over-eager restore is the worse outcome.

Ownership is resolved from the Changes Manifests of non-terminal work units,
which is the same declaration the manifest-conflict rule uses, so a path is
attributed to the unit that declared it rather than guessed at. Paths that no
work unit claims are quarantined too, under an ``unattributed`` key: they are
still not the claiming unit's scope, and leaving them in the tree would
corrupt its commit just the same.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Prefix on every stash message this module writes. Operators grep for it,
# and it keeps devbench-created entries distinguishable from stashes a human
# made by hand.
QUARANTINE_STASH_PREFIX = "devbench-quarantine"

# Key used when no work unit's Changes Manifest claims a path.
UNATTRIBUTED_OWNER = "unattributed"

# Bounded timeout (seconds) for each git invocation. Generous enough for a
# large checkout, far below the global command timeout so a hung git process
# surfaces quickly rather than stalling the claim.
_GIT_TIMEOUT: int = 60


@dataclass(frozen=True)
class QuarantineRecord:
    """One stash entry created by :func:`quarantine_paths`.

    Attributes:
        owner_id: Work-unit ID whose Changes Manifest claims these paths, or
            :data:`UNATTRIBUTED_OWNER` when no unit claims them.
        paths: Repo-relative paths moved into the stash.
        stash_message: Full message written to the stash entry, which is how
            an operator locates it in ``git stash list``.
    """

    owner_id: str
    paths: tuple[str, ...]
    stash_message: str


@dataclass(frozen=True)
class RestoreRecord:
    """One stash entry handed back by :func:`restore_quarantine`.

    Attributes:
        owner_id: Work-unit ID whose displaced work was returned to the tree.
        paths: Repo-relative paths restored, sorted.
        stash_message: Message the restored entry carried, retained so the
            audit trail can name exactly which quarantine was consumed.
    """

    owner_id: str
    paths: tuple[str, ...]
    stash_message: str


def _git(repo_path: Path, args: list[str]) -> str:
    """Run a git command in ``repo_path`` and return stdout.

    Raises:
        RuntimeError: If git times out or exits non-zero. Quarantine failures
            are never swallowed: proceeding with a claim after a failed
            quarantine would hand the claiming unit exactly the contaminated
            tree the quarantine was meant to clear.
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
        raise RuntimeError(f"quarantine: git {printable} timed out in {repo_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"quarantine: git {printable} failed in {repo_path} (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def group_paths_by_owner(paths: list[str], manifests_by_unit: dict[str, list[str]]) -> dict[str, list[str]]:
    """Attribute each path to the work unit whose Changes Manifest declares it.

    Args:
        paths: Repo-relative paths needing attribution.
        manifests_by_unit: Map of work-unit ID to that unit's Changes Manifest
            file list. Callers supply only non-terminal units; a ``done`` or
            ``declined`` unit's work is already committed, so it cannot be the
            source of uncommitted residue.

    Returns:
        Map of owner ID to its paths, sorted within each owner for stable
        output. Paths claimed by no unit are grouped under
        :data:`UNATTRIBUTED_OWNER`. When two units declare the same path the
        first owner in sorted ID order wins, which keeps attribution
        deterministic; the manifest-conflict rule is what prevents that
        overlap existing in the first place.
    """
    owner_of: dict[str, str] = {}
    for unit_id in sorted(manifests_by_unit):
        for declared in manifests_by_unit[unit_id]:
            owner_of.setdefault(declared, unit_id)

    grouped: dict[str, list[str]] = {}
    for path in paths:
        grouped.setdefault(owner_of.get(path, UNATTRIBUTED_OWNER), []).append(path)
    return {owner: sorted(owned) for owner, owned in grouped.items()}


def quarantine_paths(
    repo_path: Path,
    paths: list[str],
    manifests_by_unit: dict[str, list[str]],
    claiming_unit_id: str,
) -> list[QuarantineRecord]:
    """Stash ``paths`` out of the checkout, one stash entry per owning unit.

    Each entry is created with ``git stash push --include-untracked`` limited
    to that owner's paths, so untracked residue is captured and nothing
    outside the named paths is touched. Grouping by owner keeps each unit's
    work recoverable as a unit rather than smeared across one combined entry.

    Args:
        repo_path: Local repo root to operate on.
        paths: Repo-relative paths to remove from the working tree.
        manifests_by_unit: Non-terminal units' Changes Manifests, for
            attribution. See :func:`group_paths_by_owner`.
        claiming_unit_id: The unit about to claim, recorded in each stash
            message so the trail shows which claim triggered the quarantine.

    Returns:
        One :class:`QuarantineRecord` per stash entry created, ordered by
        owner ID. Empty when ``paths`` is empty.

    Raises:
        RuntimeError: If any git invocation fails, or if the checkout still
            reports the quarantined paths afterwards. Both mean the claiming
            unit would proceed against a tree that was not actually cleared.
    """
    if not paths:
        return []

    records: list[QuarantineRecord] = []
    for owner_id, owned_paths in sorted(group_paths_by_owner(paths, manifests_by_unit).items()):
        message = f"{QUARANTINE_STASH_PREFIX}:{owner_id}: displaced by claim of {claiming_unit_id}"
        _git(repo_path, ["stash", "push", "--include-untracked", "--message", message, "--", *owned_paths])
        records.append(QuarantineRecord(owner_id=owner_id, paths=tuple(owned_paths), stash_message=message))
    return records


def find_quarantine_stash(repo_path: Path, owner_id: str) -> str | None:
    """Return the newest quarantine stash ref belonging to ``owner_id``.

    Matching is exact on the ``devbench-quarantine:<owner-id>:`` prefix that
    :func:`quarantine_paths` writes, so a stash a human made by hand is never
    a candidate no matter what its message mentions, and an owner ID that is
    a prefix of another (``E1-F1-S1-T1`` against ``E1-F1-S1-T11``) cannot
    cross-match.

    Args:
        repo_path: Local repo root to inspect.
        owner_id: Work-unit ID whose displaced work is being looked for.

    Returns:
        The ``stash@{n}`` ref of the most recent matching entry, or ``None``
        when the owner has nothing quarantined. git pushes each new entry at
        index 0, so the first match walking the list is the newest attempt --
        the one that supersedes any older displaced copy.

    Raises:
        RuntimeError: If the ``git stash list`` invocation fails.
    """
    if owner_id == UNATTRIBUTED_OWNER:
        # The unattributed bucket is not a work unit: there is no owner to
        # hand it back to, and no Changes Manifest to bound the restore.
        return None

    wanted = f"{QUARANTINE_STASH_PREFIX}:{owner_id}:"
    listing = _git(repo_path, ["stash", "list", "--format=%gd%x00%gs"])
    for line in listing.splitlines():
        if "\x00" not in line:
            continue
        ref, _, subject = line.partition("\x00")
        if wanted in subject:
            return ref
    return None


def _stash_entry_paths(repo_path: Path, stash_ref: str) -> tuple[str, ...]:
    """Return every repo-relative path held in ``stash_ref``, sorted.

    A stash entry created with ``--include-untracked`` carries its untracked
    additions in a third parent commit rather than in the diff against the
    first, so both are walked; listing only the diff would under-report the
    entry and let an out-of-scope untracked file slip past the Manifest check
    in :func:`restore_quarantine`.
    """
    paths: set[str] = set()
    tracked = _git(repo_path, ["stash", "show", "--name-only", "--format=", stash_ref])
    paths.update(line.strip() for line in tracked.splitlines() if line.strip())

    untracked = subprocess.run(
        ["git", "-C", str(repo_path), "show", "--name-only", "--format=", f"{stash_ref}^3"],
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    # No third parent means the entry captured no untracked files, which is
    # an ordinary entry shape rather than a fault -- hence check=False and no
    # raise here, unlike every other git call in this module.
    if untracked.returncode == 0:
        paths.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
    return tuple(sorted(paths))


def _paths_with_local_work(repo_path: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return the subset of ``paths`` that currently carry uncommitted work."""
    if not paths:
        return ()
    status = _git(repo_path, ["status", "--porcelain", "--", *paths])
    dirty = {line[3:].strip() for line in status.splitlines() if line.strip()}
    return tuple(sorted(dirty))


def restore_quarantine(repo_path: Path, owner_id: str, manifest_paths: set[str]) -> RestoreRecord | None:
    """Hand ``owner_id`` its displaced work back, into the checkout it claims.

    Called on the claim path when a unit that was previously displaced is the
    one now claiming. Without this the run silently discards a completed
    attempt: the audit trail records where the work went, but nothing ever
    reads it back, so the unit re-executes from an empty tree and pays for
    every review round a second time.

    The entry is consumed on success (``git stash pop``), so a later claim
    cannot apply the same attempt twice, and the index is restored alongside
    the worktree because the manifest-scope check reads staged state.

    Args:
        repo_path: Local repo root to restore into.
        owner_id: Work-unit ID claiming the checkout.
        manifest_paths: The claiming unit's Changes Manifest file set, which
            bounds what may legitimately return to the tree.

    Returns:
        A :class:`RestoreRecord` describing what came back, or ``None`` when
        the owner has nothing quarantined.

    Raises:
        RuntimeError: If the entry holds a path outside ``manifest_paths``, if
            any target path already carries uncommitted work, or if a git
            invocation fails. The entry is left intact in every case: a
            declined restore must never be a lost restore.
    """
    stash_ref = find_quarantine_stash(repo_path, owner_id)
    if stash_ref is None:
        return None

    subject = _git(repo_path, ["stash", "list", "--format=%gd%x00%gs"])
    stash_message = next(
        (line.partition("\x00")[2] for line in subject.splitlines() if line.startswith(f"{stash_ref}\x00")),
        "",
    )

    entry_paths = _stash_entry_paths(repo_path, stash_ref)
    out_of_scope = tuple(path for path in entry_paths if path not in manifest_paths)
    if out_of_scope:
        raise RuntimeError(
            f"quarantine restore for {owner_id!r} declined: {stash_ref} holds {len(out_of_scope)} path(s) "
            f"outside its Changes Manifest: {list(out_of_scope)}. The entry is untouched and recoverable "
            f"with 'git stash apply {stash_ref}'; amend the Manifest if those paths belong to this unit."
        )

    occupied = _paths_with_local_work(repo_path, entry_paths)
    if occupied:
        raise RuntimeError(
            f"quarantine restore for {owner_id!r} declined: the checkout already holds uncommitted work at "
            f"{list(occupied)}, which a restore would overwrite. The entry is untouched and recoverable with "
            f"'git stash apply {stash_ref}'; the tree copy is the newer attempt and wins by default."
        )

    _git(repo_path, ["stash", "pop", "--index", stash_ref])
    return RestoreRecord(owner_id=owner_id, paths=entry_paths, stash_message=stash_message)


# Ref namespace for checkpoint commits. A ref keeps its commit reachable, so
# unlike a stash entry it survives `git stash clear`, an unrelated `git stash
# drop`, and garbage collection.
CHECKPOINT_REF_PREFIX = "refs/devbench/checkpoint"


def checkpoint_ref(unit_id: str) -> str:
    """Return the full ref name holding ``unit_id``'s latest checkpoint."""
    return f"{CHECKPOINT_REF_PREFIX}/{unit_id}"


def checkpoint_work(repo_path: Path, unit_id: str) -> str | None:
    """Snapshot the checkout's current state to ``unit_id``'s checkpoint ref.

    Called at the boundaries where in-flight work is about to become
    unreachable -- the stop handler, and the quarantine path -- so that an
    interrupted unit has a durable copy of what it had produced. A stash entry
    alone is not durable enough to be the only copy: it lives on a stack any
    later ``git stash clear`` or mis-indexed ``git stash drop`` can discard,
    and an interrupted executor turn is expensive enough to be worth a ref of
    its own.

    ``git stash create`` builds the commit object without touching the stash
    stack, the index, or the worktree, so this is safe to call at any point
    including immediately before the process exits. The ref is force-updated:
    the newest snapshot of a unit supersedes its older ones, and keeping a
    chain of them would grow without bound across a long run.

    Args:
        repo_path: Local repo root to snapshot.
        unit_id: Work-unit ID the snapshot belongs to.

    Returns:
        The commit SHA written to the ref, or ``None`` when the checkout is
        clean and there is therefore nothing to checkpoint.

    Raises:
        RuntimeError: If a git invocation fails.
    """
    sha = _git(repo_path, ["stash", "create", f"devbench-checkpoint:{unit_id}"]).strip()
    if not sha:
        # A clean checkout produces no commit. Nothing in flight, nothing lost.
        return None
    _git(repo_path, ["update-ref", checkpoint_ref(unit_id), sha])
    return sha


def find_checkpoint(repo_path: Path, unit_id: str) -> str | None:
    """Return the commit SHA of ``unit_id``'s checkpoint, or ``None`` if it has none.

    Used to tell an operator (and the audit trail) that a recoverable snapshot
    exists, without asserting anything about whether it is still needed.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", checkpoint_ref(unit_id)],
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    # A missing ref exits non-zero, which is the ordinary "no checkpoint yet"
    # answer rather than a fault -- hence check=False and no raise.
    return result.stdout.strip() or None
