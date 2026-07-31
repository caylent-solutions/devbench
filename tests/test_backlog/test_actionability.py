"""Tests for the shared actionability line (issue #251).

``status`` and ``report`` must answer "can the run proceed?" identically.
Before this was shared, only ``status`` answered it at all, and the
per-status counts ``report`` did show cannot substitute: a backlog can hold
many ``in-queue`` units while nothing is actionable, because only leaf Tasks
execute and every one of them may be waiting on a dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.actionability import actionability_line
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


def _unit(
    unit_id: str,
    status: WorkUnitStatus,
    *,
    unit_type: WorkUnitType = WorkUnitType.TASK,
    deps: list[str] | None = None,
    title: str = "Some Task",
) -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        title=title,
        status=status,
        unit_type=unit_type,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="org/repo",
        dependencies=deps or [],
    )


@pytest.fixture
def parser(tmp_path: Path) -> BacklogParser:
    return BacklogParser(backlog_root=tmp_path / "backlog", backlog_index=tmp_path / "BACKLOG.md")


class TestActionabilityLine:
    def test_names_the_next_claimable_unit(self, parser: BacklogParser) -> None:
        units = [_unit("E1-F1-S1-T1", WorkUnitStatus.IN_QUEUE, title="Do the thing")]
        assert actionability_line(parser, units) == "Next actionable: E1-F1-S1-T1 -- Do the thing"

    def test_reports_all_done_when_nothing_remains(self, parser: BacklogParser) -> None:
        units = [_unit("E1-F1-S1-T1", WorkUnitStatus.DONE)]
        assert actionability_line(parser, units) == "All work units are DONE."

    def test_reports_blocked_count_when_work_remains_but_none_can_start(self, parser: BacklogParser) -> None:
        units = [
            _unit("E1-F1-S1-T1", WorkUnitStatus.BLOCKED),
            _unit("E1-F1-S1-T2", WorkUnitStatus.BLOCKED),
        ]
        assert actionability_line(parser, units) == "No actionable units. 2 blocked."

    def test_in_progress_unit_is_not_offered_as_the_next_one(self, parser: BacklogParser) -> None:
        """Issue #185: candidates include IN_PROGRESS so a run can resume; the line must not."""
        units = [
            _unit("E1-F1-S1-T1", WorkUnitStatus.IN_PROGRESS),
            _unit("E1-F1-S1-T2", WorkUnitStatus.BLOCKED),
        ]
        line = actionability_line(parser, units, active_ids=["E1-F1-S1-T1"])
        assert line == "No actionable units. 1 blocked."

    def test_queued_units_gated_by_dependencies_are_not_actionable(self, parser: BacklogParser) -> None:
        """The case per-status counts cannot express: in-queue but nothing runnable."""
        units = [
            _unit("E1-F1-S1-T1", WorkUnitStatus.BLOCKED),
            _unit("E1-F1-S1-T2", WorkUnitStatus.IN_QUEUE, deps=["E1-F1-S1-T1"]),
        ]
        line = actionability_line(parser, units)
        assert "Next actionable" not in line
        assert line == "No actionable units. 1 blocked."


class TestStatusAndReportAgree:
    """The two commands must never disagree about whether the run can proceed."""

    def test_report_ends_with_the_same_line_status_prints(self, tmp_path: Path) -> None:
        from devbench.reporting import report as report_mod

        log_file = tmp_path / "orch.log"
        log_file.write_text("2026-03-05T10:00:00Z [devbench.orch] INFO Tick\n", encoding="utf-8")
        units = [
            _unit("E1-F1-S1-T1", WorkUnitStatus.BLOCKED),
            _unit("E1-F1-S1-T2", WorkUnitStatus.IN_QUEUE, deps=["E1-F1-S1-T1"]),
        ]

        from unittest.mock import patch

        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
        ):
            mock_cls.return_value.parse_index.return_value = units
            mock_cls.return_value.get_parallel_candidates.return_value = []
            mock_cls.return_value.all_done.return_value = False
            mock_cls.return_value.get_blocked_units.return_value = [units[0]]
            output = report_mod.generate_report(log_path=log_file)

        assert output.rstrip().endswith("No actionable units. 1 blocked.")
