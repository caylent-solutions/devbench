"""Deterministic gates for the ``create-spec`` adversarial hardening loop.

Issue #264 E12-F2-S3-T1. Cheap programmatic invariants that run between
agent rounds. Each gate returns a (possibly empty) list of ``Blocker``
objects. The ``run_gates`` aggregator runs all gates over the full file
set and returns every confirmed blocker.

Gates implemented:

- ``check_balanced_blocks`` -- each file must have an even number of
  triple-backtick fence markers so every opened block is closed.
- ``check_no_banned_glyphs`` -- the em-dash character U+2014 is banned
  from every spec file; double hyphen (``--``) is the required substitute.
- ``check_version_consistency`` -- version/identifier keys (of the form
  ``key: value`` in spec Markdown) must have the same value across all
  files in the set.
- ``check_acyclic_deps`` -- the declared dependency graph must be a DAG.
  Cycle detection reuses the DFS-with-recursion-stack algorithm from
  ``devbench.backlog.manager`` rather than duplicating it (DRY).

Public API
----------
- ``BlockerKind`` -- enum of all gate failure categories.
- ``Blocker`` -- dataclass returned by every gate.
- ``check_balanced_blocks`` -- balanced fenced/mermaid block gate.
- ``check_no_banned_glyphs`` -- banned-glyph gate.
- ``check_version_consistency`` -- cross-file version/identifier gate.
- ``check_acyclic_deps`` -- acyclic dependency graph gate.
- ``run_gates`` -- aggregator: runs all gates over a file set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Blocker",
    "BlockerKind",
    "check_acyclic_deps",
    "check_balanced_blocks",
    "check_no_banned_glyphs",
    "check_version_consistency",
    "run_gates",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EM_DASH = "\u2014"
"""The em-dash character banned from all spec files (use -- instead)."""

_FENCE_MARKER = "```"
"""Triple-backtick that opens or closes a fenced code block in Markdown."""

# Pattern that matches ``key: value`` pairs where the key is a lower-case
# identifier (word characters and hyphens) and the value is the remainder
# of the line after optional whitespace. Both sides are trimmed.
_VERSION_KEY_RE = re.compile(r"^\s*([\w][\w_-]*)\s*:\s*(.+?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Public enums and dataclasses
# ---------------------------------------------------------------------------


class BlockerKind(StrEnum):
    """Category of a confirmed gate blocker.

    Attributes:
        UNBALANCED_BLOCKS: A file has an odd number of triple-backtick
            fence markers, meaning at least one block is not closed.
        BANNED_GLYPH: A file contains the em-dash character U+2014.
        VERSION_INCONSISTENCY: The same key appears with different values
            in two or more files (or twice within one file).
        DEPENDENCY_CYCLE: The declared dependency graph has a cycle.
    """

    UNBALANCED_BLOCKS = "unbalanced_blocks"
    BANNED_GLYPH = "banned_glyph"
    VERSION_INCONSISTENCY = "version_inconsistency"
    DEPENDENCY_CYCLE = "dependency_cycle"


@dataclass(frozen=True)
class Blocker:
    """A confirmed gate blocker returned by a gate function.

    Attributes:
        kind: The category of the blocker (see ``BlockerKind``).
        file: The file path (as provided to the gate) where the blocker
            was detected, or an empty string for cross-file blockers.
        detail: A human-readable description of exactly what was found
            and where, sufficient for a repair agent to locate and fix
            the issue without further investigation.
    """

    kind: BlockerKind
    file: str
    detail: str


# ---------------------------------------------------------------------------
# Gate: balanced fenced and mermaid blocks
# ---------------------------------------------------------------------------


def check_balanced_blocks(file_path: str, content: str) -> list[Blocker]:
    """Check that every triple-backtick block in *content* is closed.

    A Markdown fenced code block (including mermaid diagrams) is opened
    and closed by ````` ``` `````. An odd number of such markers in the
    file means at least one block is not properly closed.

    Args:
        file_path: The path label used in blocker detail messages.
        content: The full text of the spec file to check.

    Returns:
        An empty list when the file is well-formed, or a single-element
        list containing a ``Blocker`` of kind ``UNBALANCED_BLOCKS`` when
        the fence count is odd.
    """
    fence_count = content.count(_FENCE_MARKER)
    if fence_count % 2 != 0:
        return [
            Blocker(
                kind=BlockerKind.UNBALANCED_BLOCKS,
                file=file_path,
                detail=(
                    f"{file_path}: unbalanced fenced code blocks -- "
                    f"found {fence_count} triple-backtick marker(s) "
                    f"(must be even; every opening ``` must have a closing ```)."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Gate: banned glyphs (em-dash)
# ---------------------------------------------------------------------------


def check_no_banned_glyphs(file_path: str, content: str) -> list[Blocker]:
    """Check that *content* contains no banned glyph characters.

    The em-dash (U+2014) is banned from all spec files. Use ``--``
    (double hyphen) instead.

    Args:
        file_path: The path label used in blocker detail messages.
        content: The full text of the spec file to check.

    Returns:
        An empty list when no banned glyphs are found, or one ``Blocker``
        per line that contains a banned glyph (kind ``BANNED_GLYPH``).
    """
    blockers: list[Blocker] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if _EM_DASH in line:
            blockers.append(
                Blocker(
                    kind=BlockerKind.BANNED_GLYPH,
                    file=file_path,
                    detail=(
                        f"{file_path}:{lineno}: em-dash character U+2014 found -- replace with double hyphen (--)."
                    ),
                )
            )
    return blockers


# ---------------------------------------------------------------------------
# Gate: cross-file version/identifier consistency
# ---------------------------------------------------------------------------


def check_version_consistency(files: dict[str, str]) -> list[Blocker]:
    """Check that version/identifier keys have consistent values across files.

    Scans every file for ``key: value`` patterns. When the same key
    appears in more than one file (or more than once in the same file)
    with different values, a blocker is emitted listing the conflicting
    values.

    Args:
        files: Mapping of ``{file_path: content}`` for the full spec set.

    Returns:
        A list of ``Blocker`` objects (kind ``VERSION_INCONSISTENCY``),
        one per key that has conflicting values across the file set.
        Returns an empty list when all keys are consistent.
    """
    # Collect all (value, file_path) tuples per key.
    key_occurrences: dict[str, list[tuple[str, str]]] = {}
    for file_path, content in files.items():
        for match in _VERSION_KEY_RE.finditer(content):
            key = match.group(1)
            value = match.group(2)
            key_occurrences.setdefault(key, []).append((value, file_path))

    blockers: list[Blocker] = []
    for key, occurrences in sorted(key_occurrences.items()):
        unique_values = {v for v, _ in occurrences}
        if len(unique_values) > 1:
            conflict_desc = "; ".join(f"{fp!r} -> {val!r}" for val, fp in occurrences)
            blockers.append(
                Blocker(
                    kind=BlockerKind.VERSION_INCONSISTENCY,
                    file="",
                    detail=(
                        f"Inconsistent values for key {key!r} across spec files: "
                        f"{conflict_desc}. "
                        f"All files must use the same value for this identifier."
                    ),
                )
            )
    return blockers


# ---------------------------------------------------------------------------
# Gate: acyclic declared dependency graph
# ---------------------------------------------------------------------------


def check_acyclic_deps(dep_graph: dict[str, list[str]]) -> list[Blocker]:
    """Check that the declared dependency graph contains no cycles.

    Uses DFS with a recursion-stack (gray-set) color scheme, identical
    to the algorithm in ``devbench.backlog.manager._check_dep_cycles``.
    Each cycle is rotated to start at its lexicographically smallest
    node and deduplicated so every cycle is reported exactly once.

    Args:
        dep_graph: Mapping of ``{node_id: [dep_id, ...]}`` representing
            the declared dependency graph. Unknown edges (where the
            target is not a key in the graph) are silently skipped --
            the dependency-existence gate owns that check.

    Returns:
        An empty list when the graph is a DAG, or one ``Blocker``
        (kind ``DEPENDENCY_CYCLE``) per unique cycle detected.
    """
    if not dep_graph:
        return []

    # DFS color tracking: 0 = white (unvisited), 1 = gray (on the
    # recursion stack), 2 = black (fully processed).
    color: dict[str, int] = dict.fromkeys(dep_graph, 0)
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()
    blockers: list[Blocker] = []

    def _visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dep in dep_graph.get(node, []):
            if dep not in color:
                # Edge to a node not in the graph; skip (not our gate).
                continue
            if color[dep] == 1:
                # Back-edge -- cycle detected. Normalise and deduplicate.
                cycle_start = stack.index(dep)
                cycle = tuple(stack[cycle_start:])
                rotation = cycle.index(min(cycle))
                normalised = cycle[rotation:] + cycle[:rotation]
                if normalised not in reported:
                    reported.add(normalised)
                    chain = " -> ".join([*normalised, normalised[0]])
                    blockers.append(
                        Blocker(
                            kind=BlockerKind.DEPENDENCY_CYCLE,
                            file="",
                            detail=(
                                f"Dependency cycle detected: {chain}. "
                                f"The dependency graph must be acyclic (DAG). "
                                f"Remove or reverse one of the edges in this cycle."
                            ),
                        )
                    )
                continue
            if color[dep] == 0:
                _visit(dep)
        stack.pop()
        color[node] = 2

    for node in sorted(dep_graph):
        if color.get(node) == 0:
            _visit(node)

    return blockers


# ---------------------------------------------------------------------------
# Aggregator: run all gates over a file set
# ---------------------------------------------------------------------------


def run_gates(
    *,
    files: dict[str, str],
    dep_graph: dict[str, list[str]],
) -> list[Blocker]:
    """Run all deterministic gates over *files* and *dep_graph*.

    This is the single entry-point the ``create-spec`` SKILL.md calls
    between agent rounds. All gate findings are aggregated into one
    flat list so the caller can inspect the full confirmed-blocker set
    without calling each gate individually.

    Args:
        files: Mapping of ``{file_path: content}`` for the full spec
            file set (may be a single file or a whole directory).
        dep_graph: The declared dependency graph in
            ``{node_id: [dep_id, ...]}`` form. Pass ``{}`` when the
            spec does not declare an explicit dependency graph.

    Returns:
        A list of all confirmed ``Blocker`` objects from every gate,
        in gate-definition order (balanced-blocks, banned-glyph,
        version-consistency, acyclic-deps). An empty list means the
        spec passed all gates for this round.
    """
    blockers: list[Blocker] = []

    for file_path, content in files.items():
        blockers.extend(check_balanced_blocks(file_path, content))
        blockers.extend(check_no_banned_glyphs(file_path, content))

    blockers.extend(check_version_consistency(files))
    blockers.extend(check_acyclic_deps(dep_graph))

    return blockers
