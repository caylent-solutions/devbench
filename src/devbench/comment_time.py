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


def audit_timestamp_to_utc(raw: str, zone_token: str) -> datetime:
    """Convert one audit-comment timestamp to UTC, honouring the zone it names.

    The comment header carries a zone abbreviation rather than a numeric
    offset, and abbreviations are not globally unique, so this resolves it the
    only way that is sound: ``UTC`` means UTC, and anything else is read in the
    workspace's configured comment zone, which is the zone that wrote it. Every
    file written before ``display_timezone`` was honoured is stamped ``UTC``
    and therefore keeps parsing correctly no matter what the workspace
    configures later.

    Args:
        raw: The ``YYYY-MM-DD HH:MM`` portion of the header.
        zone_token: The zone abbreviation that followed it.

    Returns:
        The instant as an aware UTC datetime.

    Raises:
        ValueError: ``raw`` is not in the expected shape. Callers skip the row
            rather than failing, matching the best-effort reads around them.
    """
    # ``fromisoformat`` rather than ``strptime``: the header portion is already
    # an ISO-8601 date-time, and parsing it as one keeps the value naive
    # without a format string that claims an offset the text does not carry.
    naive = datetime.fromisoformat(raw)
    if zone_token.upper() == "UTC":
        return naive.replace(tzinfo=UTC)
    return naive.replace(tzinfo=resolve_comment_timezone()).astimezone(UTC)


def tdd_timestamp(moment: datetime | None = None) -> str:
    """Return ``moment`` formatted for a TDD Cycle Log entry.

    The Cycle Log is read by the same person reading the audit comments beside
    it, so it follows the same zone. It keeps a full ISO-8601 representation
    rather than the comment header's shorter shape, and that is what makes
    zoning it free: an explicit numeric offset stays unambiguous and
    round-trips through :meth:`datetime.fromisoformat`, whereas the comment
    header's bare abbreviation needed a resolver on the read side. Both TDD
    entry readers match this field as an opaque token, so nothing downstream
    has to change.

    Args:
        moment: The instant to render. Defaults to now. A naive value is read
            as UTC, matching every call site in devbench.

    Returns:
        An ISO-8601 timestamp in the resolved zone, carrying its offset.
    """
    instant = datetime.now(tz=UTC) if moment is None else moment
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(resolve_comment_timezone()).isoformat()
