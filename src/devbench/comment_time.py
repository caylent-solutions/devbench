"""Timezone applied to work-unit audit-comment timestamps.

``display_timezone`` is documented as the zone every devbench command that
renders timestamps uses. Work-unit comments were the one surface that ignored
it and hard-coded UTC, so a run's own audit trail read in a different zone from
the ``hook-tail`` and ``report`` output an operator has open beside it, and
every comparison between the two needed a mental offset.

The default stays UTC rather than following the OS local zone, which is where
this deliberately diverges from ``report`` and ``hook-tail``. Those render to a
terminal for whoever is watching; a work-unit file is committed and read later
by other people on other machines. Defaulting it to the runner's local zone
would make one file's timestamps depend on who happened to write each line, and
would silently rewrite the meaning of every existing comment. So an
unconfigured workspace behaves exactly as before, and only an explicit
``display_timezone`` moves comments off UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from devbench.constants import COMMENT_TIMESTAMP_FORMAT


def resolve_comment_timezone() -> tzinfo:
    """Return the zone work-unit comments are stamped in.

    Reads ``display_timezone`` through :mod:`devbench.config` at call time
    rather than at import, so a test or a caller that reconfigures the
    workspace is honoured without reloading this module.

    Unlike ``hook-tail``, an unresolvable zone here degrades to UTC instead of
    raising. Writing the audit comment is the primary work; refusing to
    timestamp it would abort an unattended run over a display preference. The
    same misconfigured value still fails loudly on ``hook-tail`` and
    ``report``, which is where an operator is looking when they set it.

    Returns:
        The configured :class:`~datetime.tzinfo`, or UTC when unset or
        unresolvable.
    """
    from devbench.config import DISPLAY_TIMEZONE
    from devbench.hook_tail import InvalidTimezoneError, resolve_timezone

    if not DISPLAY_TIMEZONE:
        return UTC
    try:
        # Shares hook-tail's resolver so the two surfaces can never disagree
        # about what a given zone name means.
        return resolve_timezone(DISPLAY_TIMEZONE)
    except InvalidTimezoneError:
        return UTC


def comment_timestamp(moment: datetime | None = None) -> str:
    """Return ``moment`` formatted for a work-unit comment header.

    Args:
        moment: The instant to render. Defaults to now. A naive value is read
            as UTC, matching every call site in devbench, so a missing tzinfo
            can never silently shift the rendered time by the local offset.

    Returns:
        The timestamp in :data:`~devbench.constants.COMMENT_TIMESTAMP_FORMAT`,
        carrying the zone abbreviation of the resolved zone. The abbreviation
        is taken from the moment itself, so a summer comment reads ``EDT`` and
        a winter one ``EST`` in the same workspace.
    """
    instant = datetime.now(tz=UTC) if moment is None else moment
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(resolve_comment_timezone()).strftime(COMMENT_TIMESTAMP_FORMAT)
