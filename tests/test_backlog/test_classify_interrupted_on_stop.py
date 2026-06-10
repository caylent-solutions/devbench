"""Tests for the INTERRUPTED_ON_STOP classifier bucket (TDI-002).

A unit whose only blocking signal is the SIGTERM ``[FORCED_BLOCKED_ON_STOP]``
marker classifies as ``INTERRUPTED_ON_STOP`` (auto-recoverable), but any
co-existing structural blocker (dependency, marker, degradation) wins so a real
blocker is never masked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from devbench.backlog.proposal import (
    BlockedTaskState,
    _has_forced_blocked_on_stop_signal,
    classify_blocked_task,
)

pytestmark = pytest.mark.unit

_FORCED = "[2026-06-09 12:00 UTC] [agent/orchestrator] [FORCED_BLOCKED_ON_STOP] session=overnight\n"


def _write_backlog(tmp_path: Path, rows: list[str]) -> None:
    (tmp_path / "BACKLOG.md").write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
    )


def _make_unit(
    tmp_path: Path,
    *,
    dep_rows: str = "| none | | |",
    comments: str = _FORCED,
    extra_rows: list[str] | None = None,
) -> Path:
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True, exist_ok=True)
    (story_dir / "E0-F1-S1-T1.md").write_text(
        "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n## Description\n\nx\n\n"
        f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n{dep_rows}\n\n"
        f"## Comments\n\n{comments}"
    )
    rows = ["| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"]
    rows.extend(extra_rows or [])
    _write_backlog(tmp_path, rows)
    return tmp_path


def _classify(tmp_path: Path) -> BlockedTaskState:
    return classify_blocked_task(tmp_path / "backlog", tmp_path / "BACKLOG.md", "E0-F1-S1-T1", workspace_root=tmp_path)


def test_forced_blocked_only_classifies_interrupted_on_stop(tmp_path: Path) -> None:
    """AC-1: only signal is [FORCED_BLOCKED_ON_STOP] -> INTERRUPTED_ON_STOP."""
    _make_unit(tmp_path)
    assert _classify(tmp_path) is BlockedTaskState.INTERRUPTED_ON_STOP


def test_no_forced_marker_falls_through_to_operator(tmp_path: Path) -> None:
    """Without the forced-stop marker, a signal-less blocked unit stays OPERATOR_ACTION_REQUIRED."""
    _make_unit(tmp_path, comments="(no markers)\n")
    assert _classify(tmp_path) is BlockedTaskState.OPERATOR_ACTION_REQUIRED


def test_forced_blocked_plus_unmet_dependency_is_awaiting_dependency(tmp_path: Path) -> None:
    """AC-2: a co-existing unmet dependency wins over the forced-stop marker."""
    _make_unit(
        tmp_path,
        dep_rows="| E0-F1-S1-T2 | Dep | in-queue |",
        extra_rows=[
            "| E0-F1-S1-T2 | Dep | Task | in-queue | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |",
        ],
    )
    # Materialise the dependency file so the parser sees it as non-terminal.
    (tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T2.md").write_text(
        "# E0-F1-S1-T2: Dep\n\n## Status: in-queue\n"
    )
    assert _classify(tmp_path) is BlockedTaskState.AWAITING_DEPENDENCY


def test_forced_blocked_plus_open_marker_is_auto_clearing(tmp_path: Path) -> None:
    """AC-2: a co-existing non-terminal [BLOCKED_PENDING_PROPOSAL] marker wins."""
    _make_unit(
        tmp_path,
        comments=_FORCED + "[2026-06-09 12:01 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
        extra_rows=[
            "| E0-F1-S1-T2 | Marker | Task | in-queue | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |",
        ],
    )
    (tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T2.md").write_text(
        "# E0-F1-S1-T2: Marker\n\n## Status: in-queue\n"
    )
    assert _classify(tmp_path) is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL


def test_forced_blocked_plus_degradation_is_runtime_degradation(tmp_path: Path) -> None:
    """AC-2: a co-existing runtime-degradation signal (priority 0) wins."""
    ts = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M UTC")
    degradation = f"[{ts}] [agent/orchestrator] [BLOCKED] agent-tool-unavailable: review-supervisor only Bash remains\n"
    _make_unit(tmp_path, comments=_FORCED + degradation)
    assert _classify(tmp_path) is BlockedTaskState.RUNTIME_DEGRADATION


def test_has_forced_blocked_on_stop_signal_missing_file_is_false() -> None:
    """The helper falls back to False on a read error (never masks a blocked state)."""
    assert _has_forced_blocked_on_stop_signal(Path("/nonexistent/does-not-exist.md")) is False
