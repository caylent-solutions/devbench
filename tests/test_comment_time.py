"""Tests for the timezone applied to work-unit comment timestamps.

``display_timezone`` is documented as the zone every devbench command that
renders timestamps uses. Work-unit audit comments were the one surface that
ignored it and hard-coded UTC, so a run's own audit trail disagreed with the
hook-tail and report output an operator reads beside it.

Defaulting stays UTC on purpose: a work-unit file is committed and read by
other people on other machines, so switching an unconfigured workspace to
whatever local zone the runner happened to have would make one file's
timestamps depend on who wrote each line.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from devbench.comment_time import comment_timestamp, resolve_comment_timezone


class TestResolveCommentTimezone:
    def test_unset_config_resolves_to_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unchanged behaviour for every workspace that never sets the key."""
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", None, raising=False)
        assert resolve_comment_timezone() == UTC

    def test_configured_zone_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", "America/Detroit", raising=False)
        assert resolve_comment_timezone() == ZoneInfo("America/Detroit")

    def test_explicit_utc_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", "UTC", raising=False)
        assert resolve_comment_timezone() == ZoneInfo("UTC")

    def test_unknown_zone_falls_back_to_utc_rather_than_breaking_the_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An audit comment must still be written when the zone name is wrong.

        Refusing to timestamp would stop an unattended run over a display
        preference; the misconfiguration surfaces on hook-tail and report,
        which do fail loudly on the same value.
        """
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", "Mars/Olympus_Mons", raising=False)
        assert resolve_comment_timezone() == UTC


class TestCommentTimestamp:
    def test_renders_utc_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", None, raising=False)
        moment = datetime(2026, 8, 19, 7, 21, tzinfo=UTC)
        assert comment_timestamp(moment) == "2026-08-19 07:21 UTC"

    def test_converts_the_instant_into_the_configured_zone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same instant, rendered where the operator actually is."""
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", "America/Detroit", raising=False)
        moment = datetime(2026, 8, 19, 7, 21, tzinfo=UTC)
        assert comment_timestamp(moment) == "2026-08-19 03:21 EDT"

    def test_abbreviation_follows_daylight_saving(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The zone token is read from the moment, so it is not frozen at EDT."""
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", "America/Detroit", raising=False)
        winter = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        assert comment_timestamp(winter) == "2026-01-15 07:00 EST"

    def test_a_naive_moment_is_treated_as_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every caller passes an aware UTC instant; a naive one must not shift silently."""
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", None, raising=False)
        naive = datetime.fromisoformat("2026-08-19 07:21")
        assert naive.tzinfo is None
        assert comment_timestamp(naive) == "2026-08-19 07:21 UTC"

    def test_defaults_to_now_when_no_moment_is_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", None, raising=False)
        rendered = comment_timestamp()
        assert rendered.endswith(" UTC")
        # Same minute as a UTC "now", proving it is the real clock, not a constant.
        assert rendered.startswith(datetime.now(tz=UTC).strftime("%Y-%m-%d %H:"))

    def test_rendered_shape_is_parseable_by_the_audit_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The in-progress duration helper reads this exact shape back."""
        from devbench.cli import _AUDIT_PROGRESS_RE

        monkeypatch.setattr("devbench.config.DISPLAY_TIMEZONE", "America/Detroit", raising=False)
        stamp = comment_timestamp(datetime(2026, 8, 19, 7, 21, tzinfo=UTC))
        line = f"[{stamp}] [agent/orchestrator] Set E1-F1-S1-T1 to 'in-progress'"
        match = _AUDIT_PROGRESS_RE.search(line)
        assert match is not None
        assert match.group("id") == "E1-F1-S1-T1"
        assert match.group("zone") == "EDT"
