"""Shared actionability helper for cli and report.

This neutral module encapsulates the actionability check so that
:mod:`devbench.cli` and :mod:`devbench.reporting.report` can both use it
without creating an import cycle between the two.

The function :func:`check_actionability` is the single authoritative place
that computes:

- The list of actionable work units (candidates not already active).
- Whether all units are done.
- The count of blocked units.

Both ``cmd_status`` and ``generate_report`` delegate to this function so
the output lines they produce are always consistent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devbench.backlog.parser import BacklogParser
    from devbench.backlog.work_unit import WorkUnit


def check_actionability(
    parser: BacklogParser,
    units: list[WorkUnit],
) -> tuple[list[WorkUnit], bool, int]:
    """Return the actionable set, all-done flag, and blocked count.

    Args:
        parser: A :class:`~devbench.backlog.parser.BacklogParser` instance
            (or any compatible object with ``get_parallel_candidates``,
            ``all_done``, and ``get_blocked_units`` methods).
        units: Full list of parsed work units to evaluate.

    Returns:
        A three-element tuple ``(actionable, all_done, blocked_count)`` where:

        - ``actionable`` -- list of work units returned by
          ``parser.get_parallel_candidates``.  Empty when nothing is
          actionable.
        - ``all_done`` -- ``True`` when every unit in ``units`` is done.
          Determined by ``parser.all_done``.
        - ``blocked_count`` -- number of units whose status is BLOCKED, as
          returned by ``parser.get_blocked_units``.  Zero when
          ``all_done`` is ``True`` (the caller should not print the
          no-actionable line in that case).
    """
    actionable = parser.get_parallel_candidates(units)
    all_done_flag = parser.all_done(units)
    blocked_count = len(parser.get_blocked_units(units))
    return actionable, all_done_flag, blocked_count
