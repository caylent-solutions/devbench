"""Tests for the shared dependency-cycle detector (TDI-009).

``devbench.backlog.dep_cycle.find_cycles`` is the single routine
``validate-backlog`` and ``next`` use, so it must report the ACTUAL cycle
members (normalised + de-duplicated) and ignore edges to unknown nodes.
"""

from __future__ import annotations

import pytest

from devbench.backlog.dep_cycle import find_cycles, render_cycle

pytestmark = pytest.mark.unit


def test_empty_graph_has_no_cycles() -> None:
    assert find_cycles({}) == []


def test_dag_has_no_cycles() -> None:
    graph = {"a": ["b"], "b": ["c"], "c": []}
    assert find_cycles(graph) == []


def test_two_node_cycle_reports_members() -> None:
    graph = {"a": ["b"], "b": ["a"]}
    cycles = find_cycles(graph)
    assert cycles == [("a", "b")]


def test_self_loop_is_a_one_member_cycle() -> None:
    assert find_cycles({"a": ["a"]}) == [("a",)]


def test_three_node_cycle_members() -> None:
    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    cycles = find_cycles(graph)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a", "b", "c"}


def test_disjoint_cycles_each_reported_once() -> None:
    graph = {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}
    cycles = find_cycles(graph)
    assert len(cycles) == 2
    assert {frozenset(c) for c in cycles} == {frozenset({"a", "b"}), frozenset({"c", "d"})}


def test_edge_to_unknown_node_ignored() -> None:
    # A dependency on a node not in the graph is not a cycle.
    graph = {"a": ["missing"], "b": ["a"]}
    assert find_cycles(graph) == []


def test_cycle_normalised_to_smallest_member_and_deduped() -> None:
    # Entering the same 3-cycle from any root reports it once, rotated to the
    # lexicographically smallest member.
    graph = {"b": ["c"], "c": ["a"], "a": ["b"]}
    cycles = find_cycles(graph)
    assert cycles == [("a", "b", "c")]


def test_render_cycle_closes_the_chain() -> None:
    assert render_cycle(("a", "b", "c")) == "a -> b -> c -> a"
    assert render_cycle(("a",)) == "a -> a"
