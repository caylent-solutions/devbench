"""Shared actionability line for ``status`` and ``report``.

Issue #251: ``devbench status`` ended its summary with one of three lines
telling the operator whether anything could run next, and ``devbench report``
ended with none of them. An operator watching ``report`` saw per-status
counts and no statement of whether the run was progressing, which is the one
thing the counts do not say: a backlog can hold thirty ``in-queue`` units and
still have nothing actionable, because only leaf Tasks execute and every one
of them may be waiting on a dependency.

Issue #309: a serially-ordered backlog's steady state is exactly one unit
IN_PROGRESS and everything else BLOCKED on it. ``get_parallel_candidates``
deliberately includes IN_PROGRESS units (issue #185, resume support), so once
``active_ids`` is subtracted from the candidate list the result was always
empty in that steady state, and the stuck-state line
``No actionable units. N blocked.`` printed while work was actively
executing -- camouflaging the genuine deadlock case that same line is meant
to flag. A dedicated "active" outcome names the running unit(s) instead. The
same defect hid HOLD units from the trailing count, since it was sourced
from ``get_blocked_units`` (status BLOCKED only); the tail is now computed
directly against every status that keeps a unit from being actionable.

The line lives here, rather than in either caller, so the two commands cannot
drift into disagreeing about the same question.

``devbench next`` deliberately does NOT use this. Its ``ALL_DONE`` /
``NO_ACTIONABLE`` / ``NO_ACTIONABLE_IN_SCOPE`` tokens are a machine contract
consumed by the orchestrate skill's loop-continuation check; prose belongs in
the operator-facing commands only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from devbench.backlog.work_unit import WorkUnitStatus

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

    Exactly one of five statements, in priority order:

    - ``Next actionable: <id> -- <title>`` -- at least one unit is claimable.
    - ``All work units are DONE.`` -- nothing remains.
    - ``<id> active; nothing else can start yet. <tail>`` -- exactly one
      unit is already running and nothing else is claimable.
    - ``<N> units active; nothing else can start yet. <tail>`` -- two or
      more units are already running and nothing else is claimable.
    - ``No actionable units. <tail>`` -- work remains, nothing is running,
      and none of it can start.

    ``<tail>`` is ``<B> blocked`` when no unit is on hold, or
    ``<B> blocked, <H> on hold`` when ``H`` (units with status ``HOLD``) is
    greater than zero.

    Args:
        parser: The :class:`BacklogParser` used to resolve candidates and
            blocked units.
        units: Full list of parsed work units.
        active_ids: IDs already ``in-progress`` / ``in-review``. Issue #185:
            ``get_parallel_candidates`` includes IN_PROGRESS units so an
            interrupted run can resume, but the "next" line must point at a
            unit that is not already running. Issue #309: when the only
            candidate IS the running unit (or units), the line names it
            instead of falling through to the stuck-state message.

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
    blocked = len(parser.get_blocked_units(units))
    held = sum(1 for u in units if u.status is WorkUnitStatus.HOLD)
    tail = f"{blocked} blocked, {held} on hold." if held else f"{blocked} blocked."
    if excluded:
        if len(excluded) == 1:
            return f"{sorted(excluded)[0]} active; nothing else can start yet. {tail}"
        return f"{len(excluded)} units active; nothing else can start yet. {tail}"
    return f"No actionable units. {tail}"
