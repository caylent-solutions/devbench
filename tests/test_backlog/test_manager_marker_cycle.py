"""Tests for marker-cycle detection in BacklogManager.validate().

Issue #253a. validate-backlog builds the marker graph via the shared
_extract_pending_proposal_markers extractor and reports verbatim cycle
errors with rc 1.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager

_INDEX_HEADER = (
    "# Backlog\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|-----|-------|------|--------|-------------|------|-----------|\n"
)


def _minimal_wu(unit_id: str, marker_ids: list[str]) -> str:
    """Return minimal work-unit markdown carrying BLOCKED_PENDING_PROPOSAL markers."""
    comments_body = "\n".join(f"[BLOCKED_PENDING_PROPOSAL] {mid}" for mid in marker_ids)
    return f"# {unit_id}: Title\n\n## Status: in-queue\n\n## Comments\n\n{comments_body}\n"


def _build_fixture(
    tmp_path: Path,
    unit_ids: list[str],
    marker_map: dict[str, list[str]],
) -> tuple[Path, Path]:
    """Build a minimal BACKLOG.md + work-unit files in tmp_path.

    Args:
        tmp_path: Temporary directory.
        unit_ids: All task IDs to include in the index.
        marker_map: Maps unit_id -> list of marker target IDs it carries.

    Returns:
        (backlog_index_path, workspace_root).
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    rows = []
    for uid in unit_ids:
        rows.append(f"| {uid} | T | Task | in-queue | None | example/repo | `backlog/{uid}.md` |")
        markers = marker_map.get(uid, [])
        (backlog_dir / f"{uid}.md").write_text(_minimal_wu(uid, markers), encoding="utf-8")
    index = tmp_path / "BACKLOG.md"
    index.write_text(_INDEX_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return index, tmp_path


@pytest.mark.unit
class TestMarkerCycleTwoNode:
    """validate() reports the verbatim 2-node marker-cycle error (sorted IDs)."""

    def test_two_node_cycle_detected(self, tmp_path: Path) -> None:
        """T2 -> T3 -> T2 via BLOCKED_PENDING_PROPOSAL markers."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T2", "E1-F1-S1-T3"],
            marker_map={
                "E1-F1-S1-T2": ["E1-F1-S1-T3"],
                "E1-F1-S1-T3": ["E1-F1-S1-T2"],
            },
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        assert any(e == "marker cycle: E1-F1-S1-T2 <-> E1-F1-S1-T3" for e in errors), (
            f"Expected verbatim 2-node cycle message; got: {errors}"
        )

    def test_two_node_cycle_sorted_ids(self, tmp_path: Path) -> None:
        """IDs in the 2-node form are alphabetically sorted regardless of traversal order."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T9", "E1-F1-S1-T2"],
            marker_map={
                "E1-F1-S1-T9": ["E1-F1-S1-T2"],
                "E1-F1-S1-T2": ["E1-F1-S1-T9"],
            },
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        assert any(e == "marker cycle: E1-F1-S1-T2 <-> E1-F1-S1-T9" for e in errors), (
            f"Expected sorted 2-node cycle: {errors}"
        )

    def test_two_node_cycle_reported_once(self, tmp_path: Path) -> None:
        """A single 2-node cycle produces exactly one error message."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T2", "E1-F1-S1-T3"],
            marker_map={
                "E1-F1-S1-T2": ["E1-F1-S1-T3"],
                "E1-F1-S1-T3": ["E1-F1-S1-T2"],
            },
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        cycle_errors = [e for e in errors if "marker cycle" in e]
        assert len(cycle_errors) == 1, f"Expected 1 cycle error; got: {cycle_errors}"

    def test_two_node_cycle_in_validate_returns_error(self, tmp_path: Path) -> None:
        """validate() includes the marker cycle error in its returned list."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T2", "E1-F1-S1-T3"],
            marker_map={
                "E1-F1-S1-T2": ["E1-F1-S1-T3"],
                "E1-F1-S1-T3": ["E1-F1-S1-T2"],
            },
        )
        errors = BacklogManager().validate(index, ws)
        assert any("marker cycle" in e for e in errors)


@pytest.mark.unit
class TestMarkerCycleNNode:
    """validate() reports N-node marker cycles in arrow form, reported once."""

    def test_three_node_cycle_detected(self, tmp_path: Path) -> None:
        """T1 -> T2 -> T3 -> T1 cycle is reported with arrow form."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F1-S1-T3"],
            marker_map={
                "E1-F1-S1-T1": ["E1-F1-S1-T2"],
                "E1-F1-S1-T2": ["E1-F1-S1-T3"],
                "E1-F1-S1-T3": ["E1-F1-S1-T1"],
            },
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        cycle_errors = [e for e in errors if "marker cycle" in e]
        assert len(cycle_errors) == 1, f"Expected 1 N-node error; got: {errors}"
        assert "<->" not in cycle_errors[0], "3-node cycle must use arrow form, not <->"
        assert "->" in cycle_errors[0], f"3-node cycle must use arrow form; got: {cycle_errors[0]}"
        msg = cycle_errors[0]
        parts = msg.replace("marker cycle: ", "").split(" -> ")
        assert parts[0] == parts[-1], f"Arrow cycle must close: {parts}"

    def test_three_node_cycle_reported_once(self, tmp_path: Path) -> None:
        """A single 3-node cycle produces exactly one error message."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F1-S1-T3"],
            marker_map={
                "E1-F1-S1-T1": ["E1-F1-S1-T2"],
                "E1-F1-S1-T2": ["E1-F1-S1-T3"],
                "E1-F1-S1-T3": ["E1-F1-S1-T1"],
            },
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        cycle_errors = [e for e in errors if "marker cycle" in e]
        assert len(cycle_errors) == 1

    def test_three_node_cycle_in_validate_returns_error(self, tmp_path: Path) -> None:
        """validate() includes the 3-node marker cycle error in its returned list."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F1-S1-T3"],
            marker_map={
                "E1-F1-S1-T1": ["E1-F1-S1-T2"],
                "E1-F1-S1-T2": ["E1-F1-S1-T3"],
                "E1-F1-S1-T3": ["E1-F1-S1-T1"],
            },
        )
        errors = BacklogManager().validate(index, ws)
        assert any("marker cycle" in e for e in errors)


@pytest.mark.unit
class TestMarkerCycleNegative:
    """validate() does not raise spurious marker-cycle errors."""

    def test_no_markers_no_errors(self, tmp_path: Path) -> None:
        """Work units with no BLOCKED_PENDING_PROPOSAL markers produce no cycle errors."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2"],
            marker_map={},
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        assert errors == []

    def test_unidirectional_markers_no_cycle(self, tmp_path: Path) -> None:
        """T1 -> T2 (one direction only) is not a cycle."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2"],
            marker_map={"E1-F1-S1-T1": ["E1-F1-S1-T2"]},
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        assert errors == []

    def test_missing_marker_target_does_not_crash(self, tmp_path: Path) -> None:
        """A marker referencing a non-indexed ID is skipped gracefully."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1"],
            marker_map={"E1-F1-S1-T1": ["E9-F9-S9-T9"]},
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        assert not any("marker cycle" in e for e in errors)

    def test_missing_index_no_crash(self, tmp_path: Path) -> None:
        """_check_marker_cycles on a missing BACKLOG.md returns an empty list."""
        errors = BacklogManager()._check_marker_cycles(tmp_path / "missing.md", tmp_path)
        assert errors == []

    def test_disjoint_two_node_cycles_each_reported_once(self, tmp_path: Path) -> None:
        """Two disjoint 2-node cycles each produce exactly one error message."""
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F1-S1-T3", "E1-F1-S1-T4"],
            marker_map={
                "E1-F1-S1-T1": ["E1-F1-S1-T2"],
                "E1-F1-S1-T2": ["E1-F1-S1-T1"],
                "E1-F1-S1-T3": ["E1-F1-S1-T4"],
                "E1-F1-S1-T4": ["E1-F1-S1-T3"],
            },
        )
        errors = BacklogManager()._check_marker_cycles(index, ws)
        cycle_errors = [e for e in errors if "marker cycle" in e]
        assert len(cycle_errors) == 2

    def test_uses_shared_extractor(self, tmp_path: Path) -> None:
        """The marker graph is built via _extract_pending_proposal_markers (AC-253a-1).

        Confirms that markers in non-Comments sections are NOT included in
        the graph (the shared extractor scans only ## Comments).
        """
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        t2_content = (
            "# E1-F1-S1-T2: Title\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\n"
            "[BLOCKED_PENDING_PROPOSAL] E1-F1-S1-T3\n\n"
            "## Comments\n\n"
        )
        t3_content = (
            "# E1-F1-S1-T3: Title\n\n## Status: in-queue\n\n## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E1-F1-S1-T2\n"
        )
        (backlog_dir / "E1-F1-S1-T2.md").write_text(t2_content, encoding="utf-8")
        (backlog_dir / "E1-F1-S1-T3.md").write_text(t3_content, encoding="utf-8")
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            _INDEX_HEADER
            + "| E1-F1-S1-T2 | T | Task | in-queue | None | r | `backlog/E1-F1-S1-T2.md` |\n"
            + "| E1-F1-S1-T3 | T | Task | in-queue | None | r | `backlog/E1-F1-S1-T3.md` |\n",
            encoding="utf-8",
        )
        errors = BacklogManager()._check_marker_cycles(index, tmp_path)
        assert not any("marker cycle" in e for e in errors)

    def test_indexed_but_missing_wu_file_no_crash(self, tmp_path: Path) -> None:
        """An indexed unit whose work-unit file does not exist is treated as having no markers."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / "E1-F1-S1-T2.md").write_text(
            "# E1-F1-S1-T2: Title\n\n## Status: in-queue\n\n## Comments\n\n",
            encoding="utf-8",
        )
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            _INDEX_HEADER
            + "| E1-F1-S1-T1 | T | Task | in-queue | None | r | `backlog/E1-F1-S1-T1.md` |\n"
            + "| E1-F1-S1-T2 | T | Task | in-queue | None | r | `backlog/E1-F1-S1-T2.md` |\n",
            encoding="utf-8",
        )
        errors = BacklogManager()._check_marker_cycles(index, tmp_path)
        assert not any("marker cycle" in e for e in errors)


@pytest.mark.unit
class TestMarkerCycleDuplicateDeduplication:
    """The `reported` set suppresses duplicate cycle detection within one DFS pass.

    This exercises the False branch of `if normalised not in reported:` (line 975
    of manager.py). The branch fires when the DFS finds a back-edge whose normalised
    cycle tuple is already present in `reported`. This can only occur when a node's
    neighbour list contains the same target more than once (a multigraph edge), so
    the DFS back-edge check fires for the same ancestor twice in sequence.

    Because `_extract_pending_proposal_markers` returns a `set` (deduplicating
    markers from the file), the normal graph builder never produces duplicate edges.
    The test therefore patches `_build_marker_graph` to inject a controlled multigraph
    so the DFS algorithm's deduplication branch can be exercised directly.
    """

    def test_duplicate_back_edge_suppressed(self, tmp_path: Path) -> None:
        """A duplicate back-edge for the same cycle is silently skipped.

        Graph injected: T1 -> T2, T2 -> [T1, T1] (T1 listed twice).
        DFS traversal from T1: visits T2, which sees T1 on the stack twice
        in sequence. The first back-edge reports the (T1, T2) cycle; the
        second back-edge produces the same normalised tuple and must be
        suppressed by the `reported` set (branch 975->983).

        If the deduplication guard were removed, two identical cycle errors
        would appear. The assertion that exactly one error is reported confirms
        the guard executed the False branch and suppressed the duplicate.
        """
        index, ws = _build_fixture(
            tmp_path,
            unit_ids=["E1-F1-S1-T1", "E1-F1-S1-T2"],
            marker_map={},
        )
        injected_graph = {
            "E1-F1-S1-T1": ["E1-F1-S1-T2"],
            "E1-F1-S1-T2": ["E1-F1-S1-T1", "E1-F1-S1-T1"],
        }
        with patch.object(BacklogManager, "_build_marker_graph", return_value=injected_graph):
            errors = BacklogManager()._check_marker_cycles(index, ws)

        cycle_errors = [e for e in errors if "marker cycle" in e]
        assert len(cycle_errors) == 1, (
            f"Duplicate cycle must be suppressed; got {len(cycle_errors)} errors: {cycle_errors}"
        )
        assert cycle_errors[0] == "marker cycle: E1-F1-S1-T1 <-> E1-F1-S1-T2", f"Wrong cycle message: {cycle_errors[0]}"
