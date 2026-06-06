"""Tests for the shared devbench.actionability module.

The module provides :func:`check_actionability` which encapsulates the
logic formerly duplicated across ``cli._print_actionable_summary`` and
other call sites.  These tests drive the helper directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


def _make_task(
    unit_id: str,
    status: WorkUnitStatus,
    deps: list[str] | None = None,
) -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        title=f"Task {unit_id}",
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=deps or [],
    )


@pytest.mark.unit
class TestCheckActionability:
    """Tests for :func:`devbench.actionability.check_actionability`."""

    def test_returns_actionable_list_and_blocked_count(self) -> None:
        """When candidates exist, actionable is non-empty and blocked count is accurate."""
        from devbench.actionability import check_actionability

        unit_iq = _make_task("E0-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        unit_blocked = _make_task("E0-F1-S1-T2", WorkUnitStatus.BLOCKED)
        units = [unit_iq, unit_blocked]

        mock_parser = MagicMock()
        mock_parser.get_parallel_candidates.return_value = [unit_iq]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [unit_blocked]

        actionable, all_done, blocked_count = check_actionability(mock_parser, units)

        assert actionable == [unit_iq]
        assert all_done is False
        assert blocked_count == 1

    def test_all_done_true_when_parser_reports_done(self) -> None:
        """When all units are done, all_done is True and actionable is empty."""
        from devbench.actionability import check_actionability

        unit_done = _make_task("E0-F1-S1-T1", WorkUnitStatus.DONE)
        units = [unit_done]

        mock_parser = MagicMock()
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True
        mock_parser.get_blocked_units.return_value = []

        actionable, all_done, blocked_count = check_actionability(mock_parser, units)

        assert actionable == []
        assert all_done is True
        assert blocked_count == 0

    def test_no_actionable_not_all_done_returns_blocked_count(self) -> None:
        """When nothing is actionable and not all done, returns blocked count."""
        from devbench.actionability import check_actionability

        unit_blocked = _make_task("E0-F1-S1-T1", WorkUnitStatus.BLOCKED)
        units = [unit_blocked]

        mock_parser = MagicMock()
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [unit_blocked]

        actionable, all_done, blocked_count = check_actionability(mock_parser, units)

        assert actionable == []
        assert all_done is False
        assert blocked_count == 1

    def test_empty_units_returns_empty_actionable(self) -> None:
        """An empty unit list yields empty actionable, all_done=False, blocked=0."""
        from devbench.actionability import check_actionability

        mock_parser = MagicMock()
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        actionable, all_done, blocked_count = check_actionability(mock_parser, [])

        assert actionable == []
        assert all_done is False
        assert blocked_count == 0

    def test_multiple_blocked_units_counted_correctly(self) -> None:
        """When multiple blocked units exist, blocked_count reflects all of them."""
        from devbench.actionability import check_actionability

        blocked_units = [_make_task(f"E0-F1-S1-T{i}", WorkUnitStatus.BLOCKED) for i in range(3)]
        mock_parser = MagicMock()
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = blocked_units

        actionable, all_done, blocked_count = check_actionability(mock_parser, blocked_units)

        assert actionable == []
        assert all_done is False
        assert blocked_count == 3

    @pytest.mark.parametrize(
        "num_actionable,expected_len",
        [
            (1, 1),
            (3, 3),
            (5, 5),
        ],
    )
    def test_returns_full_actionable_list(self, num_actionable: int, expected_len: int) -> None:
        """check_actionability returns all candidates, not just the first."""
        from devbench.actionability import check_actionability

        candidates = [_make_task(f"E0-F1-S1-T{i}", WorkUnitStatus.IN_QUEUE) for i in range(num_actionable)]
        mock_parser = MagicMock()
        mock_parser.get_parallel_candidates.return_value = candidates
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        actionable, all_done, blocked_count = check_actionability(mock_parser, candidates)

        assert len(actionable) == expected_len
        assert all_done is False
        assert blocked_count == 0
