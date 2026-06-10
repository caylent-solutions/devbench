"""Tests for canonical dependency-cycle detection in validate-backlog (TDI-009).

The validator's cycle check must operate on the same canonical graph
``next`` / ``add-dep`` reject on -- the union of the BACKLOG.md index column,
the work-unit ``## Dependencies`` tables (the source of truth an operator edits
directly), and ``[BLOCKED_PENDING_PROPOSAL]`` marker edges. A cycle introduced
by editing a ``## Dependencies`` table (with the index column left clean) must
ERROR, naming the actual cycle members. Pure-marker cycles defer to the
dedicated marker-cycle check.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

_REPO = "caylent-solutions/devbench"


def _make_index(tmp_path: Path, unit_ids: list[str]) -> Path:
    # The index dependency column is deliberately "none" for every row -- the
    # cycle lives only in the work-unit ## Dependencies tables / markers.
    rows = "".join(
        f"| {uid} | Task Title | Task | in-queue | none | {_REPO} | `backlog/{uid}.md` |\n" for uid in unit_ids
    )
    (tmp_path / "BACKLOG.md").write_text(
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
        f"{rows}",
        encoding="utf-8",
    )
    return tmp_path / "BACKLOG.md"


def _make_task(
    backlog_dir: Path,
    unit_id: str,
    *,
    dep_ids: list[str] | None = None,
    marker_ids: list[str] | None = None,
) -> None:
    dep_rows = "".join(f"| {d} | Dep | in-queue |\n" for d in (dep_ids or [])) or "| none | | |\n"
    comments = "\n".join(f"[BLOCKED_PENDING_PROPOSAL] {m}" for m in (marker_ids or []))
    (backlog_dir / f"{unit_id}.md").write_text(
        f"# {unit_id}: Task Title\n\n"
        f"## Status: in-queue\n\n"
        f"## Target Repository\n\n- **Repo:** `{_REPO}`\n\n"
        f"## Description\n\nTest task.\n\n"
        f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n{dep_rows}\n"
        f"## Acceptance Criteria\n\n- [ ] AC-1: documented\n\n"
        f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/f.py` | modify |\n\n"
        f"## Definition of Done\n\n- [ ] All ACs checked\n\n"
        f"## TDD Cycle Log\n\n## Comments\n\n{comments}\n",
        encoding="utf-8",
    )


def _validate(tmp_path: Path) -> list[str]:
    idx = tmp_path / "BACKLOG.md"
    cfg = RuntimeConfig(repos={_REPO: RepoConfig()})
    with patch("devbench.config.RUNTIME_CONFIG", cfg):
        return BacklogManager().validate(idx, tmp_path)


def _cycle_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if "dependency cycle detected" in e]


def test_dep_table_cycle_is_caught_even_when_index_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    """AC-1 / AC-3: a cycle in the ## Dependencies tables (index column clean) ERRORs with members."""
    _make_index(tmp_path, ["E1-F1-S1-T1", "E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T1", dep_ids=["E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T2", dep_ids=["E1-F1-S1-T1"])

    errors = _validate(tmp_path)
    cycle = _cycle_errors(errors)
    assert len(cycle) == 1
    # AC-3: the diagnostic names the actual cycle members.
    assert "E1-F1-S1-T1" in cycle[0] and "E1-F1-S1-T2" in cycle[0]


def test_cross_graph_cycle_dep_table_plus_marker_is_caught(tmp_path: Path, backlog_dir: Path) -> None:
    """AC-2: T1 ->(dep table) T2, T2 ->(marker) T1 -- the union forms a cycle add-dep would refuse."""
    _make_index(tmp_path, ["E1-F1-S1-T1", "E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T1", dep_ids=["E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T2", marker_ids=["E1-F1-S1-T1"])

    errors = _validate(tmp_path)
    cycle = _cycle_errors(errors)
    assert len(cycle) == 1
    assert "E1-F1-S1-T1" in cycle[0] and "E1-F1-S1-T2" in cycle[0]


def test_pure_marker_cycle_not_double_reported_as_dependency_cycle(tmp_path: Path, backlog_dir: Path) -> None:
    """A cycle made only of marker edges is reported by the marker check, not the dependency check."""
    _make_index(tmp_path, ["E1-F1-S1-T1", "E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T1", marker_ids=["E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T2", marker_ids=["E1-F1-S1-T1"])

    errors = _validate(tmp_path)
    assert _cycle_errors(errors) == []
    assert any("marker cycle" in e for e in errors)


def test_acyclic_dep_tables_are_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1", "E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T1", dep_ids=["E1-F1-S1-T2"])
    _make_task(backlog_dir, "E1-F1-S1-T2")

    assert _cycle_errors(_validate(tmp_path)) == []
