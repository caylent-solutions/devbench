"""Tests for the no-actionable report line in :func:`generate_report`.

AC-251-1: ``generate_report`` prints ``No actionable units. <N> blocked.``
matching ``cmd_status`` when ``get_parallel_candidates`` is empty and not
all-done, including in ``--once`` (i.e. when called with an explicit
``since`` timestamp).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.reporting.report import generate_report


def _make_task(
    unit_id: str,
    status: WorkUnitStatus,
) -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        title=f"Task {unit_id}",
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=[],
    )


def _minimal_log(tmp_path: Path) -> Path:
    """Create a minimal (non-empty) orchestrator log file."""
    log_file = tmp_path / "orchestrator.log"
    log_file.write_text(
        "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n"
        "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'\n"
    )
    return log_file


@pytest.mark.unit
class TestGenerateReportNoActionableLine:
    """AC-251-1: generate_report prints the verbatim no-actionable line."""

    def test_no_actionable_line_printed_when_zero_candidates_not_all_done(self, tmp_path: Path) -> None:
        """When candidates is empty and not all done, report includes the no-actionable line."""
        log_file = _minimal_log(tmp_path)
        unit_blocked = _make_task("E0-F1-S1-T1", WorkUnitStatus.BLOCKED)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit_blocked]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [unit_blocked]

        with patch("devbench.reporting.report.BacklogParser", return_value=mock_parser):
            report = generate_report(log_path=log_file)

        assert "No actionable units. 1 blocked." in report

    def test_no_actionable_line_matches_verbatim_format(self, tmp_path: Path) -> None:
        """The exact format 'No actionable units. <N> blocked.' matches cmd_status output."""
        log_file = _minimal_log(tmp_path)
        blocked_units = [_make_task(f"E0-F1-S1-T{i}", WorkUnitStatus.BLOCKED) for i in range(3)]

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = blocked_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = blocked_units

        with patch("devbench.reporting.report.BacklogParser", return_value=mock_parser):
            report = generate_report(log_path=log_file)

        assert "No actionable units. 3 blocked." in report

    def test_no_actionable_line_not_printed_when_all_done(self, tmp_path: Path) -> None:
        """When all units are done, the no-actionable line must not appear."""
        log_file = _minimal_log(tmp_path)
        unit_done = _make_task("E0-F1-S1-T1", WorkUnitStatus.DONE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit_done]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True
        mock_parser.get_blocked_units.return_value = []

        with patch("devbench.reporting.report.BacklogParser", return_value=mock_parser):
            report = generate_report(log_path=log_file)

        assert "No actionable units." not in report

    def test_no_actionable_line_not_printed_when_candidates_exist(self, tmp_path: Path) -> None:
        """When candidates exist, the no-actionable line must not appear."""
        log_file = _minimal_log(tmp_path)
        unit_iq = _make_task("E0-F1-S1-T1", WorkUnitStatus.IN_QUEUE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit_iq]
        mock_parser.get_parallel_candidates.return_value = [unit_iq]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        with patch("devbench.reporting.report.BacklogParser", return_value=mock_parser):
            report = generate_report(log_path=log_file)

        assert "No actionable units." not in report

    def test_no_actionable_line_printed_in_once_mode(self, tmp_path: Path) -> None:
        """AC-251-1: the no-actionable line also appears in --once mode (since=<timestamp>)."""
        log_file = _minimal_log(tmp_path)
        unit_blocked = _make_task("E0-F1-S1-T1", WorkUnitStatus.BLOCKED)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit_blocked]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [unit_blocked]

        since = datetime(2026, 3, 5, 9, 0, 0, tzinfo=UTC)
        with patch("devbench.reporting.report.BacklogParser", return_value=mock_parser):
            report = generate_report(log_path=log_file, since=since)

        assert "No actionable units. 1 blocked." in report
