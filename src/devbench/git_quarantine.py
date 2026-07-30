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
after the run has moved on. Nothing is auto-restored: a blocked unit
re-executes from its Changes Manifest when it unblocks, and silently
re-injecting a superseded attempt into a later run's tree would recreate the
contamination this module exists to remove.

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
