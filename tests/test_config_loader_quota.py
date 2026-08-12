"""Tests for the ``quota_handling:`` config surface (issue #236, spec S5.2).

Covers: ``QuotaHandlingConfig`` dataclass defaults, the
``_parse_quota_handling_config`` parser (enum + range validation, fail-fast
error messages), the ``quota_handling`` JSON Schema block
(``additionalProperties: false``), and the ``notifications.events``
schema additions (``quota_waiting`` / ``quota_resumed``) that E2-F3-S1-T1
will later wire up.
"""

from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import notifications
from devbench.config_loader import (
    NotificationsConfig,
    NotificationsEventsConfig,
    QuotaHandlingConfig,
    RuntimeConfig,
    _parse_quota_handling_config,
    load_runtime_config,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# QuotaHandlingConfig defaults -- AC-E2-F2-S1-T1-2
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingConfigDefaults:
    """S5.2 defaults, verified against branch dataclass config_loader.py:847."""

    def test_dataclass_defaults(self) -> None:
        """Given no args, QuotaHandlingConfig holds the full S5.2 default set."""
        cfg = QuotaHandlingConfig()
        assert cfg.enabled is True
        assert cfg.on_exhaustion == "wait"
        assert cfg.poll_interval_seconds == 60
        assert cfg.max_wait_seconds == 18000
        assert cfg.on_exhaustion_timeout == "drain"
        assert cfg.resume_strategy == "continue_current_wu"
        assert cfg.audit_comment_on_wait is True
        assert cfg.audit_comment_on_resume is True
        assert cfg.log_structured_events is True

    def test_runtime_config_has_quota_handling_field(self) -> None:
        """RuntimeConfig exposes ``quota_handling`` populated with QuotaHandlingConfig defaults."""
        rt = RuntimeConfig()
        assert isinstance(rt.quota_handling, QuotaHandlingConfig)
        assert rt.quota_handling.enabled is True
        assert rt.quota_handling.on_exhaustion == "wait"

    def test_absent_quota_handling_block_yields_full_defaults(self, tmp_path: Path) -> None:
        """A YAML config with no ``quota_handling:`` key loads a fully populated default object."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert isinstance(rt.quota_handling, QuotaHandlingConfig)
        assert rt.quota_handling.enabled is True
        assert rt.quota_handling.on_exhaustion == "wait"
        assert rt.quota_handling.poll_interval_seconds == 60
        assert rt.quota_handling.max_wait_seconds == 18000
        assert rt.quota_handling.on_exhaustion_timeout == "drain"
        assert rt.quota_handling.resume_strategy == "continue_current_wu"
        assert rt.quota_handling.audit_comment_on_wait is True
        assert rt.quota_handling.audit_comment_on_resume is True
        assert rt.quota_handling.log_structured_events is True

    def test_absent_quota_handling_block_never_yields_none(self, tmp_path: Path) -> None:
        """An absent block must never surface as None or a partial object (spec S5.2)."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling is not None
        assert len(dataclasses.fields(rt.quota_handling)) == 9


# ---------------------------------------------------------------------------
# Enum acceptance / rejection -- AC-E2-F2-S1-T1-3, FR-2.9
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingEnumAcceptance:
    """Every legal enum value loads cleanly (spec S10.1: parametrized across all three enums)."""

    @pytest.mark.parametrize("value", ["wait", "fail", "drain"])
    def test_on_exhaustion_accepts_legal_values(self, tmp_path: Path, value: str) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              on_exhaustion: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion == value

    @pytest.mark.parametrize("value", ["drain", "fail", "keep_waiting"])
    def test_on_exhaustion_timeout_accepts_legal_values(self, tmp_path: Path, value: str) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              on_exhaustion_timeout: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion_timeout == value

    @pytest.mark.parametrize("value", ["continue_current_wu", "restart_wu", "drain_and_resume"])
    def test_resume_strategy_accepts_legal_values(self, tmp_path: Path, value: str) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              resume_strategy: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.resume_strategy == value


@pytest.mark.unit
class TestQuotaHandlingEnumRejection:
    """Illegal enum values raise ValueError naming the config path + field (FR-2.9: at load, never dispatch)."""

    @pytest.mark.parametrize("value", ["bogus", "WAIT", ""])
    def test_on_exhaustion_rejects_illegal_values(self, tmp_path: Path, value: str) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              on_exhaustion: "{value}"
            """,
        )
        with pytest.raises(ValueError, match=r"on_exhaustion"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize("value", ["bogus", "DRAIN", ""])
    def test_on_exhaustion_timeout_rejects_illegal_values(self, tmp_path: Path, value: str) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              on_exhaustion_timeout: "{value}"
            """,
        )
        with pytest.raises(ValueError, match=r"on_exhaustion_timeout"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize("value", ["bogus", "RESTART_WU", ""])
    def test_resume_strategy_rejects_illegal_values(self, tmp_path: Path, value: str) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              resume_strategy: "{value}"
            """,
        )
        with pytest.raises(ValueError, match=r"resume_strategy"):
            load_runtime_config(cfg, {})

    def test_on_exhaustion_error_names_config_path(self, tmp_path: Path) -> None:
        """The error message names the config file path, matching the standard shape."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              on_exhaustion: bogus
            """,
        )
        with pytest.raises(ValueError, match=rf"{cfg}"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# Range enforcement -- AC-E2-F2-S1-T1-3
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingRangeEnforcement:
    """``poll_interval_seconds`` in [30, 3600]; ``max_wait_seconds`` >= 1."""

    @pytest.mark.parametrize("value", [30, 3600])
    def test_poll_interval_seconds_accepts_boundary_values(self, tmp_path: Path, value: int) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              poll_interval_seconds: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.poll_interval_seconds == value

    @pytest.mark.parametrize("value", [29, 3601])
    def test_poll_interval_seconds_rejects_out_of_range_values(self, tmp_path: Path, value: int) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              poll_interval_seconds: {value}
            """,
        )
        with pytest.raises(ValueError, match=r"poll_interval_seconds"):
            load_runtime_config(cfg, {})

    def test_max_wait_seconds_accepts_minimum(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              max_wait_seconds: 1
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.max_wait_seconds == 1

    @pytest.mark.parametrize("value", [0, -1])
    def test_max_wait_seconds_rejects_below_minimum(self, tmp_path: Path, value: int) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            quota_handling:
              max_wait_seconds: {value}
            """,
        )
        with pytest.raises(ValueError, match=r"max_wait_seconds"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# Direct parser defensive guards (schema-bypass safety net)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseQuotaHandlingConfigDirect:
    """``_parse_quota_handling_config`` re-validates even if schema validation is bypassed."""

    def test_raises_on_invalid_on_exhaustion_direct(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"quota_handling.on_exhaustion"):
            _parse_quota_handling_config(fake_path, {"on_exhaustion": "bogus"})

    def test_raises_on_invalid_on_exhaustion_timeout_direct(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"quota_handling.on_exhaustion_timeout"):
            _parse_quota_handling_config(fake_path, {"on_exhaustion_timeout": "bogus"})

    def test_raises_on_invalid_resume_strategy_direct(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"quota_handling.resume_strategy"):
            _parse_quota_handling_config(fake_path, {"resume_strategy": "bogus"})

    def test_raises_on_poll_interval_below_minimum_direct(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"quota_handling.poll_interval_seconds"):
            _parse_quota_handling_config(fake_path, {"poll_interval_seconds": 1})

    def test_raises_on_max_wait_below_minimum_direct(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"quota_handling.max_wait_seconds"):
            _parse_quota_handling_config(fake_path, {"max_wait_seconds": 0})

    def test_empty_raw_dict_returns_defaults(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "cfg.yaml"
        result = _parse_quota_handling_config(fake_path, {})
        assert result == QuotaHandlingConfig()


# ---------------------------------------------------------------------------
# Unknown-key rejection -- AC-E2-F2-S1-T1-4
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingUnknownKeyRejection:
    """JSON Schema ``additionalProperties: false`` rejects unknown ``quota_handling:`` keys."""

    def test_schema_rejects_unknown_key(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              unknown_field: foo
            """,
        )
        with pytest.raises(ValueError, match=r"Additional properties are not allowed.*unknown_field"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# enabled: false -- AC-E2-F2-S1-T1-1, legacy rc=1 restoration (#193 AC-4, spec AC-24)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingEnabledFlag:
    """``enabled: false`` is the escape hatch restoring the legacy non-zero exit behaviour."""

    def test_enabled_false_parses_and_is_exposed(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is False

    def test_enabled_true_is_default(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is True


# ---------------------------------------------------------------------------
# Full sample block round-trips + minimal-config parity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingFullBlockRoundTrip:
    """A config carrying every quota_handling field loads successfully."""

    def test_full_block_loads_successfully(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              enabled: true
              on_exhaustion: wait
              poll_interval_seconds: 60
              max_wait_seconds: 18000
              on_exhaustion_timeout: drain
              resume_strategy: continue_current_wu
              audit_comment_on_wait: true
              audit_comment_on_resume: true
              log_structured_events: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling == QuotaHandlingConfig()

    def test_minimal_config_matches_full_default_block(self, tmp_path: Path) -> None:
        """A minimal config (no block) loads to identical defaults as the full S5.2 block."""
        minimal = _write(
            tmp_path / "minimal.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        full = _write(
            tmp_path / "full.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              enabled: true
              on_exhaustion: wait
              poll_interval_seconds: 60
              max_wait_seconds: 18000
              on_exhaustion_timeout: drain
              resume_strategy: continue_current_wu
              audit_comment_on_wait: true
              audit_comment_on_resume: true
              log_structured_events: true
            """,
        )
        rt_minimal = load_runtime_config(minimal, {})
        rt_full = load_runtime_config(full, {})
        assert rt_minimal.quota_handling == rt_full.quota_handling

    def test_audit_toggles_parse_false(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            quota_handling:
              audit_comment_on_wait: false
              audit_comment_on_resume: false
              log_structured_events: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.audit_comment_on_wait is False
        assert rt.quota_handling.audit_comment_on_resume is False
        assert rt.quota_handling.log_structured_events is False


# ---------------------------------------------------------------------------
# notifications.events schema additions -- AC-E2-F2-S1-T1-5
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotificationsEventsQuotaKeys:
    """quota_waiting/quota_resumed schema keys land here (single ownership of config-schema.json).

    These keys are live: E2-F3-S1-T1 wired ``is_event_enabled`` to observe them, and
    E2-F4-S3-T1 wired ``_handle_quota_pause`` (``src/devbench/cli.py``) to fire
    ``_fire_quota_waiting_notification`` / ``_fire_quota_resumed_notification`` off of
    them on every quota pause/recovery. This test proves the schema accepts them
    without raising.
    """

    def test_quota_waiting_key_accepted(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            notifications:
              events:
                quota_waiting: true
            """,
        )
        load_runtime_config(cfg, {})

    def test_quota_resumed_key_accepted(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            notifications:
              events:
                quota_resumed: true
            """,
        )
        load_runtime_config(cfg, {})

    def test_both_quota_event_keys_accepted_together(self, tmp_path: Path) -> None:
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            notifications:
              events:
                quota_waiting: true
                quota_resumed: true
            """,
        )
        load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# notifications.events quota field wiring -- AC-E2-F3-S1-T2-1..6
# ---------------------------------------------------------------------------
#
# E2-F2-S1-T1 landed the quota_waiting/quota_resumed schema keys tested
# above but never wired the matching NotificationsEventsConfig dataclass
# fields or _parse_notifications_config lines, so a value that passed
# schema validation was silently dropped before reaching the dispatcher.
# This block proves the full parse -> dataclass -> is_event_enabled round
# trip now works, matching every sibling event toggle.


@pytest.mark.unit
class TestNotificationsEventsQuotaFieldWiring:
    """quota_waiting/quota_resumed resolve onto the parsed config object."""

    @pytest.mark.parametrize(
        "quota_waiting,quota_resumed",
        [
            (True, False),
            (False, True),
            (True, True),
        ],
    )
    def test_quota_keys_resolve_to_set_value_on_parsed_config(
        self, tmp_path: Path, quota_waiting: bool, quota_resumed: bool
    ) -> None:
        """AC-E2-F3-S1-T2-3: each key resolves independently and together."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo: {{}}
            notifications:
              events:
                quota_waiting: {str(quota_waiting).lower()}
                quota_resumed: {str(quota_resumed).lower()}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.notifications.events.quota_waiting is quota_waiting
        assert rt.notifications.events.quota_resumed is quota_resumed

    def test_absent_events_block_defaults_both_quota_keys_false(self, tmp_path: Path) -> None:
        """AC-E2-F3-S1-T2-4: no ``notifications:`` block at all -> both False."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.notifications.events.quota_waiting is False
        assert rt.notifications.events.quota_resumed is False

    def test_absent_quota_waiting_key_defaults_false(self, tmp_path: Path) -> None:
        """AC-E2-F3-S1-T2-4: omitting quota_waiting alone still resolves False."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            notifications:
              events:
                quota_resumed: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.notifications.events.quota_waiting is False
        assert rt.notifications.events.quota_resumed is True

    def test_absent_quota_resumed_key_defaults_false(self, tmp_path: Path) -> None:
        """AC-E2-F3-S1-T2-4: omitting quota_resumed alone still resolves False."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            notifications:
              events:
                quota_waiting: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.notifications.events.quota_waiting is True
        assert rt.notifications.events.quota_resumed is False

    def test_dataclass_field_order_follows_orchestrator_auto_restart(self) -> None:
        """AC-E2-F3-S1-T2-1: fields land immediately after orchestrator_auto_restart."""
        names = [f.name for f in dataclasses.fields(NotificationsEventsConfig)]
        idx = names.index("orchestrator_auto_restart")
        assert names[idx + 1 : idx + 3] == ["quota_waiting", "quota_resumed"]

    def test_quota_fields_default_off_on_bare_dataclass(self) -> None:
        """AC-E2-F3-S1-T2-2: default is read from the dataclass, not a parser literal."""
        events = NotificationsEventsConfig()
        assert events.quota_waiting is False
        assert events.quota_resumed is False

    def test_unknown_events_key_still_rejected(self, tmp_path: Path) -> None:
        """AC-E2-F3-S1-T2-6: additionalProperties: false still guards notifications.events."""
        cfg = _write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            notifications:
              events:
                quota_waiting: true
                not_a_real_event: true
            """,
        )
        with pytest.raises(ValueError, match=r"Additional properties are not allowed.*not_a_real_event"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestIsEventEnabledQuotaRoundTrip:
    """AC-E2-F3-S1-T2-5: is_event_enabled observes the quota toggles; the dispatcher is live.

    Before this task, ``NotificationsEventsConfig`` had no ``quota_waiting``
    / ``quota_resumed`` attribute, so ``is_event_enabled``'s
    ``getattr(cfg.events, event_kind, False)`` fell through to the
    missing-attribute default and the schema keys were unobservable by the
    dispatcher regardless of what the operator configured. E2-F4-S3-T1 wired
    ``_handle_quota_pause`` (``src/devbench/cli.py``) into ``cmd_start``'s dispatch
    loop, so these toggles are now consumed by a real quota pause/recovery.
    """

    def test_is_event_enabled_true_for_quota_waiting_when_toggled_on(self) -> None:
        cfg = NotificationsConfig(
            enabled=True,
            events=NotificationsEventsConfig(quota_waiting=True),
        )
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("quota_waiting") is True

    def test_is_event_enabled_true_for_quota_resumed_when_toggled_on(self) -> None:
        cfg = NotificationsConfig(
            enabled=True,
            events=NotificationsEventsConfig(quota_resumed=True),
        )
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("quota_resumed") is True

    def test_is_event_enabled_false_for_quota_events_when_toggled_off(self) -> None:
        cfg = NotificationsConfig(
            enabled=True,
            events=NotificationsEventsConfig(quota_waiting=False, quota_resumed=False),
        )
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("quota_waiting") is False
            assert notifications.is_event_enabled("quota_resumed") is False

    def test_is_event_enabled_false_when_master_switch_off(self) -> None:
        cfg = NotificationsConfig(
            enabled=False,
            events=NotificationsEventsConfig(quota_waiting=True, quota_resumed=True),
        )
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("quota_waiting") is False
            assert notifications.is_event_enabled("quota_resumed") is False
