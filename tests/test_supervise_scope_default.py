"""Scope conveyance write + whole-backlog default (AC-31, Section 5.6, FR-8).

Covers the deterministic scope conveyance: ``write_session_scope`` persists the
expanded scope to the canonical per-session ``scope.json`` path via the REUSED
``ScopeFilter.to_file`` writer, and an empty ``--include`` expands to the ENTIRE
backlog minus exclusions (AC-31). The path is the SDK session-tree path so the
orchestrate skill + ``devbench next`` read the same file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbench.scope import ScopeFilter, session_scope_file_path
from devbench.supervise import write_session_scope


@pytest.mark.unit
class TestWholeBacklogDefault:
    """AC-31: empty include expands to the whole backlog minus exclusions."""

    def test_empty_include_is_all(self) -> None:
        backlog_ids = ["E1-F1-S1-T1", "E1-F1-S1-T2", "E2-F1-S1-T1"]
        scope = ScopeFilter.parse("", "", backlog_ids)
        assert scope.expanded_ids == set(backlog_ids)

    def test_empty_include_with_exclude(self) -> None:
        backlog_ids = ["E1-F1-S1-T1", "E1-F1-S1-T2", "E2-F1-S1-T1"]
        scope = ScopeFilter.parse("", "E2", backlog_ids)
        assert scope.expanded_ids == {"E1-F1-S1-T1", "E1-F1-S1-T2"}


@pytest.mark.unit
class TestWriteSessionScope:
    """AC-31/AC-30: write_session_scope writes the canonical scope.json."""

    def test_writes_to_session_path(self, tmp_path: Path) -> None:
        backlog_ids = ["E11-F1-S1-T1", "E11-F1-S1-T2", "E12-F1-S1-T1"]
        expanded = write_session_scope(
            workspace_root=tmp_path,
            session_name="nightly",
            include="E11",
            exclude="",
            backlog_ids=backlog_ids,
        )
        scope_path = session_scope_file_path(tmp_path, "nightly")
        assert scope_path.exists()
        data = json.loads(scope_path.read_text(encoding="utf-8"))
        assert data["include"] == ["E11"]
        assert set(data["expanded_ids"]) == {"E11-F1-S1-T1", "E11-F1-S1-T2"}
        assert set(expanded) == {"E11-F1-S1-T1", "E11-F1-S1-T2"}

    def test_no_include_writes_whole_backlog(self, tmp_path: Path) -> None:
        backlog_ids = ["E1-F1-S1-T1", "E2-F1-S1-T1"]
        expanded = write_session_scope(
            workspace_root=tmp_path,
            session_name="bulk",
            include="",
            exclude="",
            backlog_ids=backlog_ids,
        )
        scope_path = session_scope_file_path(tmp_path, "bulk")
        data = json.loads(scope_path.read_text(encoding="utf-8"))
        assert set(data["expanded_ids"]) == set(backlog_ids)
        assert set(expanded) == set(backlog_ids)

    def test_canonical_schema_fields(self, tmp_path: Path) -> None:
        expanded = write_session_scope(
            workspace_root=tmp_path,
            session_name="n",
            include="",
            exclude="",
            backlog_ids=["E1-F1-S1-T1"],
        )
        data = json.loads(session_scope_file_path(tmp_path, "n").read_text(encoding="utf-8"))
        for key in ("include", "exclude", "expanded_ids", "started_at", "started_by"):
            assert key in data
        assert expanded == sorted(expanded)
