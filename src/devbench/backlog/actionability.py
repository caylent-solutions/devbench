"""Shared actionability line for ``status`` and ``report``.

Issue #251: ``devbench status`` ended its summary with one of three lines
telling the operator whether anything could run next, and ``devbench report``
ended with none of them. An operator watching ``report`` saw per-status
counts and no statement of whether the run was progressing, which is the one
thing the counts do not say: a backlog can hold thirty ``in-queue`` units and
still have nothing actionable, because only leaf Tasks execute and every one
of them may be waiting on a dependency.

The line lives here, rather than in either caller, so the two commands cannot
drift into disagreeing about the same question.

``devbench next`` deliberately does NOT use this. Its ``ALL_DONE`` /
``NO_ACTIONABLE`` / ``NO_ACTIONABLE_IN_SCOPE`` tokens are a machine contract
consumed by the orchestrate skill's loop-continuation check; prose belongs in
the operator-facing commands only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from devbench.backlog.parser import BacklogParser
    from devbench.backlog.work_unit import WorkUnit


def actionability_line(
    parser: BacklogParser,
    units: list[WorkUnit],
    active_ids: Iterable[str] = (),
) -> str:
    """Return the one-line summary of what the orchestrator can do next.

    Exactly one of three statements, in priority order:

    - ``Next actionable: <id> -- <title>`` -- at least one unit is claimable.
    - ``All work units are DONE.`` -- nothing remains.
    - ``No actionable units. <N> blocked.`` -- work remains but none of it
      can start.

    Args:
        parser: The :class:`BacklogParser` used to resolve candidates and
            blocked units.
        units: Full list of parsed work units.
        active_ids: IDs already ``in-progress`` / ``in-review``. Issue #185:
            ``get_parallel_candidates`` includes IN_PROGRESS units so an
            interrupted run can resume, but the "next" line must point at a
            unit that is not already running.

    Returns:
        The summary line, without a trailing newline or leading blank line.
        Callers own their own spacing.
    """
    excluded = set(active_ids)
    actionable = [u for u in parser.get_parallel_candidates(units) if u.id not in excluded]
    if actionable:
        return f"Next actionable: {actionable[0].id} -- {actionable[0].title}"
    if parser.all_done(units):
        return "All work units are DONE."
    return f"No actionable units. {len(parser.get_blocked_units(units))} blocked."
