"""Shared dependency-cycle detection (TDI-009).

One cycle-detection routine over one canonical dependency graph so
``validate-backlog``, ``devbench next``, and ``add-dep`` agree on what
constitutes a cycle -- and report the **actual** cycle members rather than an
arbitrary detection node.

Historically three implementations disagreed: ``validate-backlog`` walked the
``BACKLOG.md`` Full Work Unit Index dependency column, ``next`` walked the
work-unit ``## Dependencies`` tables (the source of truth the orchestrator
schedules on), and ``add-dep`` checked only a direct reverse edge. A dependency
introduced by hand-editing a ``## Dependencies`` table therefore passed
``validate-backlog`` (index unchanged) while ``next`` later halted with
``NO_ACTIONABLE -- cyclic`` and a misleading node id. This module is the single
source of truth both consumers now use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def find_cycles(graph: Mapping[str, Sequence[str]]) -> list[tuple[str, ...]]:
    """Return each dependency cycle in *graph* as a tuple of its member ids.

    *graph* maps a node id to the ids it depends on (directed edges). Detection
    is DFS-with-recursion-stack: a back-edge to a node currently on the stack
    (the "gray" set) is the cycle witness. Each cycle is rotated to begin at its
    lexicographically smallest member and de-duplicated, so it is returned once
    regardless of which root the DFS enters it from. Edges to nodes absent from
    *graph* are ignored (an unknown dependency target is not a cycle).

    The returned tuple lists the cycle members in traversal order and is **not**
    closed -- the caller renders the closing edge (``-> members[0]``) when
    formatting a diagnostic. A self-edge (``a -> a``) is returned as ``("a",)``.

    Args:
        graph: ``{node_id: [dependency_id, ...]}`` directed dependency graph.

    Returns:
        A list of cycles, each a tuple of member ids in cycle order. Empty when
        the graph is acyclic.
    """
    color: dict[str, int] = dict.fromkeys(graph, 0)
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()
    cycles: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in graph.get(node, ()):
            if nxt not in color:
                continue  # dependency on a node not in the graph -- not a cycle
            if color[nxt] == 1:
                start = stack.index(nxt)
                cycle = tuple(stack[start:])
                rotation = cycle.index(min(cycle))
                normalised = cycle[rotation:] + cycle[:rotation]
                if normalised not in reported:
                    reported.add(normalised)
                    cycles.append(normalised)
            elif color[nxt] == 0:
                visit(nxt)
        stack.pop()
        color[node] = 2

    for node in sorted(graph):
        if color.get(node) == 0:
            visit(node)
    return cycles


def render_cycle(members: tuple[str, ...]) -> str:
    """Render a cycle's members as a closed chain ``a -> b -> c -> a``."""
    return " -> ".join([*members, members[0]])
