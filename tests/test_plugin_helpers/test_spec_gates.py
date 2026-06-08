"""Tests for ``devbench.plugin_helpers.spec_gates``.

Issue #264 E12-F2-S3-T1: deterministic gates that run between agent rounds
in the ``create-spec`` adversarial hardening loop.

AC-1: cheap programmatic invariants -- balanced fenced/mermaid blocks,
      banned glyphs (em-dash), cross-file version/identifier consistency,
      and acyclic declared dependency graph.

Each gate is tested independently for both the passing case and the
failing (confirmed-blocker) case. The ``run_gates`` aggregator test
verifies that all findings are collected and returned.
"""

from __future__ import annotations

import pytest

from devbench.plugin_helpers.spec_gates import (
    Blocker,
    BlockerKind,
    check_acyclic_deps,
    check_balanced_blocks,
    check_no_banned_glyphs,
    check_version_consistency,
    run_gates,
)

# ---------------------------------------------------------------------------
# check_balanced_blocks -- balanced fenced and mermaid code blocks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckBalancedBlocks:
    """Fenced and mermaid blocks must be balanced (even open/close pairs)."""

    def test_no_fenced_blocks_passes(self) -> None:
        content = "# Heading\n\nSome plain text.\n"
        result = check_balanced_blocks("spec.md", content)
        assert result == []

    def test_balanced_fenced_block_passes(self) -> None:
        content = "```python\ncode here\n```\n"
        result = check_balanced_blocks("spec.md", content)
        assert result == []

    def test_balanced_multiple_fenced_blocks_passes(self) -> None:
        content = "```python\ncode\n```\n\n```yaml\nkey: val\n```\n"
        result = check_balanced_blocks("spec.md", content)
        assert result == []

    def test_unbalanced_fenced_block_fails(self) -> None:
        content = "```python\ncode here\n"
        result = check_balanced_blocks("spec.md", content)
        assert len(result) == 1
        assert result[0].kind == BlockerKind.UNBALANCED_BLOCKS
        assert "spec.md" in result[0].detail

    def test_balanced_mermaid_block_passes(self) -> None:
        content = "```mermaid\ngraph TD;\n    A-->B;\n```\n"
        result = check_balanced_blocks("spec.md", content)
        assert result == []

    def test_unbalanced_mermaid_block_fails(self) -> None:
        content = "```mermaid\ngraph TD;\n    A-->B;\n"
        result = check_balanced_blocks("spec.md", content)
        assert len(result) == 1
        assert result[0].kind == BlockerKind.UNBALANCED_BLOCKS

    @pytest.mark.parametrize(
        "content",
        [
            "Before\n```\nblock\n```\nAfter\n",
            "```\nblock1\n```\n\n```\nblock2\n```\n",
        ],
    )
    def test_generic_balanced_blocks_pass(self, content: str) -> None:
        result = check_balanced_blocks("spec.md", content)
        assert result == []

    def test_odd_fence_count_fails(self) -> None:
        """Three triple-backtick markers (odd) means an unbalanced block."""
        content = "```\nblock\n```\n\n```\nnot closed\n"
        result = check_balanced_blocks("spec.md", content)
        assert len(result) == 1
        assert result[0].kind == BlockerKind.UNBALANCED_BLOCKS


# ---------------------------------------------------------------------------
# check_no_banned_glyphs -- em-dash U+2014 is banned
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckNoBannedGlyphs:
    """Em-dash (U+2014) is banned; double hyphen is acceptable."""

    def test_clean_content_passes(self) -> None:
        content = "# Section\n\nSome text -- with double hyphen.\n"
        result = check_no_banned_glyphs("spec.md", content)
        assert result == []

    def test_em_dash_fails(self) -> None:
        content = "This is a sentence\u2014with an em-dash.\n"
        result = check_no_banned_glyphs("spec.md", content)
        assert len(result) == 1
        assert result[0].kind == BlockerKind.BANNED_GLYPH
        assert "spec.md" in result[0].detail

    def test_multiple_em_dashes_each_reported(self) -> None:
        content = "word\u2014word\nother\u2014thing\n"
        result = check_no_banned_glyphs("spec.md", content)
        assert len(result) >= 1
        assert all(b.kind == BlockerKind.BANNED_GLYPH for b in result)

    def test_double_hyphen_passes(self) -> None:
        content = "Use -- instead.\n"
        result = check_no_banned_glyphs("spec.md", content)
        assert result == []

    @pytest.mark.parametrize("safe_char", ["-", "--", "---", "\u2013"])
    def test_non_em_dash_passes(self, safe_char: str) -> None:
        """Characters other than U+2014 are not flagged."""
        content = f"prefix{safe_char}suffix\n"
        result = check_no_banned_glyphs("spec.md", content)
        assert result == []


# ---------------------------------------------------------------------------
# check_version_consistency -- cross-file version/identifier consistency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckVersionConsistency:
    """Cross-file version/identifier keys must have consistent values."""

    def test_single_file_no_versions_passes(self) -> None:
        files = {"spec.md": "No version references here.\n"}
        result = check_version_consistency(files)
        assert result == []

    def test_consistent_versions_across_files_pass(self) -> None:
        files = {
            "spec.md": "The schema version is `v2.1`.\n",
            "appendix.md": "Schema version: `v2.1`.\n",
        }
        result = check_version_consistency(files)
        assert result == []

    def test_inconsistent_versions_fail(self) -> None:
        files = {
            "spec.md": "schema_version: v2.1\n",
            "appendix.md": "schema_version: v2.2\n",
        }
        result = check_version_consistency(files)
        assert len(result) >= 1
        assert result[0].kind == BlockerKind.VERSION_INCONSISTENCY

    def test_single_file_consistent_passes(self) -> None:
        files = {"spec.md": "schema_version: v1.0\nschema_version: v1.0\n"}
        result = check_version_consistency(files)
        assert result == []

    def test_single_key_two_different_values_in_single_file_fails(self) -> None:
        files = {"spec.md": "schema_version: v1.0\nschema_version: v2.0\n"}
        result = check_version_consistency(files)
        assert len(result) >= 1
        assert result[0].kind == BlockerKind.VERSION_INCONSISTENCY

    def test_empty_files_dict_passes(self) -> None:
        result = check_version_consistency({})
        assert result == []

    def test_multiple_keys_one_inconsistent_fails(self) -> None:
        files = {
            "a.md": "api_version: v3\nschema_version: v1\n",
            "b.md": "api_version: v3\nschema_version: v2\n",
        }
        result = check_version_consistency(files)
        kinds = {b.kind for b in result}
        assert BlockerKind.VERSION_INCONSISTENCY in kinds


# ---------------------------------------------------------------------------
# check_acyclic_deps -- declared dependency graph must be acyclic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckAcyclicDeps:
    """The declared dependency graph must be a DAG (no cycles)."""

    def test_empty_graph_passes(self) -> None:
        result = check_acyclic_deps({})
        assert result == []

    def test_single_node_no_edges_passes(self) -> None:
        result = check_acyclic_deps({"A": []})
        assert result == []

    def test_linear_chain_passes(self) -> None:
        result = check_acyclic_deps({"A": ["B"], "B": ["C"], "C": []})
        assert result == []

    def test_diamond_dag_passes(self) -> None:
        result = check_acyclic_deps({"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []})
        assert result == []

    def test_self_loop_fails(self) -> None:
        result = check_acyclic_deps({"A": ["A"]})
        assert len(result) == 1
        assert result[0].kind == BlockerKind.DEPENDENCY_CYCLE

    def test_two_node_cycle_fails(self) -> None:
        result = check_acyclic_deps({"A": ["B"], "B": ["A"]})
        assert len(result) == 1
        assert result[0].kind == BlockerKind.DEPENDENCY_CYCLE

    def test_three_node_cycle_fails(self) -> None:
        result = check_acyclic_deps({"A": ["B"], "B": ["C"], "C": ["A"]})
        assert len(result) == 1
        assert result[0].kind == BlockerKind.DEPENDENCY_CYCLE

    def test_partial_cycle_in_larger_graph_fails(self) -> None:
        result = check_acyclic_deps({"A": ["B"], "B": ["C"], "C": ["D"], "D": ["B"], "E": []})
        assert len(result) >= 1
        assert result[0].kind == BlockerKind.DEPENDENCY_CYCLE

    def test_edge_to_unknown_node_is_skipped(self) -> None:
        """An edge to a node not in the graph is skipped without error."""
        # "A" depends on "UNKNOWN" which is not a key in the graph.
        result = check_acyclic_deps({"A": ["UNKNOWN"], "B": []})
        assert result == []

    def test_duplicate_cycle_reported_only_once(self) -> None:
        """The same normalised cycle hit twice in one DFS is deduplicated.

        Graph: {"A": ["B"], "B": ["A", "A"]}
        DFS from A: gray(A). Visit B: gray(B).
          Visit A (dep index 0 of B): color==1 -> back-edge.
            cycle=(A,B), normalised=(A,B) -> not in reported -> add, emit blocker.
          Visit A (dep index 1 of B): color==1, still gray -> back-edge again.
            cycle=(A,B), normalised=(A,B) -> already in reported -> skip (false-branch).
        B->black. A->black.

        Only one DEPENDENCY_CYCLE blocker must be returned despite the
        same cycle being encountered twice within the same DFS call.
        """
        dep_graph = {"A": ["B"], "B": ["A", "A"]}
        result = check_acyclic_deps(dep_graph)
        assert len(result) == 1
        assert result[0].kind == BlockerKind.DEPENDENCY_CYCLE
        assert "A" in result[0].detail
        assert "B" in result[0].detail

    @pytest.mark.parametrize(
        "graph",
        [
            {"X": ["Y"], "Y": []},
            {"a": [], "b": [], "c": ["a", "b"]},
        ],
    )
    def test_valid_dags_pass(self, graph: dict[str, list[str]]) -> None:
        result = check_acyclic_deps(graph)
        assert result == []


# ---------------------------------------------------------------------------
# run_gates -- aggregator: collects all blocker findings from all gates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunGates:
    """run_gates aggregates findings from every individual gate."""

    def test_clean_single_file_no_blockers(self) -> None:
        files = {"spec.md": "# Title\n\nNo issues here.\n"}
        result = run_gates(files=files, dep_graph={})
        assert result == []

    def test_em_dash_surfaced_by_run_gates(self) -> None:
        files = {"spec.md": "line\u2014with em-dash\n"}
        result = run_gates(files=files, dep_graph={})
        kinds = {b.kind for b in result}
        assert BlockerKind.BANNED_GLYPH in kinds

    def test_unbalanced_block_surfaced_by_run_gates(self) -> None:
        files = {"spec.md": "```python\nunclosed\n"}
        result = run_gates(files=files, dep_graph={})
        kinds = {b.kind for b in result}
        assert BlockerKind.UNBALANCED_BLOCKS in kinds

    def test_dependency_cycle_surfaced_by_run_gates(self) -> None:
        files = {"spec.md": "# Clean\n"}
        result = run_gates(files=files, dep_graph={"A": ["B"], "B": ["A"]})
        kinds = {b.kind for b in result}
        assert BlockerKind.DEPENDENCY_CYCLE in kinds

    def test_version_inconsistency_surfaced_by_run_gates(self) -> None:
        files = {
            "a.md": "schema_version: v1\n",
            "b.md": "schema_version: v2\n",
        }
        result = run_gates(files=files, dep_graph={})
        kinds = {b.kind for b in result}
        assert BlockerKind.VERSION_INCONSISTENCY in kinds

    def test_multiple_issues_all_collected(self) -> None:
        """Both em-dash and dependency cycle are collected in one pass."""
        files = {"spec.md": "text\u2014em\n"}
        result = run_gates(files=files, dep_graph={"X": ["X"]})
        kinds = {b.kind for b in result}
        assert BlockerKind.BANNED_GLYPH in kinds
        assert BlockerKind.DEPENDENCY_CYCLE in kinds

    def test_multiple_files_all_gated(self) -> None:
        """Gates operate on every file in the set, not just the first."""
        files = {
            "spec.md": "# Clean\n",
            "appendix.md": "broken\u2014text\n",
        }
        result = run_gates(files=files, dep_graph={})
        assert any("appendix.md" in b.detail for b in result)


# ---------------------------------------------------------------------------
# Blocker dataclass -- structural sanity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBlockerDataclass:
    """Blocker carries kind, file, and a non-empty detail string."""

    def test_blocker_has_required_fields(self) -> None:
        b = Blocker(kind=BlockerKind.BANNED_GLYPH, file="spec.md", detail="found U+2014")
        assert b.kind == BlockerKind.BANNED_GLYPH
        assert b.file == "spec.md"
        assert b.detail == "found U+2014"

    def test_blocker_kind_enum_members_present(self) -> None:
        expected = {
            "UNBALANCED_BLOCKS",
            "BANNED_GLYPH",
            "VERSION_INCONSISTENCY",
            "DEPENDENCY_CYCLE",
        }
        actual = {m.name for m in BlockerKind}
        assert expected.issubset(actual)
