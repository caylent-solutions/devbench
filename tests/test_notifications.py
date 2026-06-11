"""Unit tests for ``devbench.notifications`` (PR #202).

Pins the per-event dispatcher contract: each ``notify_*`` helper is
best-effort, returns early when the per-event toggle is off or the
master switch is off, builds a Slack block-kit payload with the
``<!here>`` mention (channel-broadcast that pushes to every online
member of the bound channel), and swallows every HTTP exception
(logged as ``[WARN]`` to stderr).
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import notifications
from devbench.config_loader import (
    NotificationsConfig,
    NotificationsEventsConfig,
    NotificationsSlackConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    enabled: bool = True,
    slack_enabled: bool = True,
    slack_url: str | None = "https://hooks.slack.com/services/T/B/X",
    events: dict[str, bool] | None = None,
) -> NotificationsConfig:
    """Build a ``NotificationsConfig`` with the toggles flipped per ``events``."""
    return NotificationsConfig(
        enabled=enabled,
        timeout_seconds=10.0,
        events=NotificationsEventsConfig(**(events or {})),
        slack=NotificationsSlackConfig(enabled=slack_enabled, webhook_url=slack_url),
    )


# ---------------------------------------------------------------------------
# _mask_url
# ---------------------------------------------------------------------------


class TestMaskUrl:
    def test_returns_triple_star_for_empty(self) -> None:
        assert notifications._mask_url("") == "***"

    def test_returns_triple_star_for_short(self) -> None:
        assert notifications._mask_url("12345") == "***"

    def test_returns_last_8_chars_prefixed_with_ellipsis(self) -> None:
        assert notifications._mask_url("https://hooks.slack.com/services/AAA/BBB/SECRET01") == "...SECRET01"


# ---------------------------------------------------------------------------
# is_event_enabled
# ---------------------------------------------------------------------------


class TestIsEventEnabled:
    def test_returns_false_when_master_switch_off(self) -> None:
        cfg = _make_config(enabled=False, events={"work_unit_done": True})
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("work_unit_done") is False

    def test_returns_false_when_config_is_none(self) -> None:
        with patch.object(notifications, "_load_notifications_config", return_value=None):
            assert notifications.is_event_enabled("work_unit_done") is False

    def test_returns_false_when_event_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": False})
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("work_unit_done") is False

    def test_returns_true_when_master_and_event_on(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": True})
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("work_unit_done") is True

    def test_unknown_event_kind_returns_false(self) -> None:
        cfg = _make_config(enabled=True)
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            assert notifications.is_event_enabled("not_an_event") is False


# ---------------------------------------------------------------------------
# _build_slack_payload
# ---------------------------------------------------------------------------


class TestSlackPayload:
    def test_payload_top_line_starts_with_here_mention(self) -> None:
        payload = notifications._build_slack_payload(
            summary="Sample headline",
            fields=[("Task", "`E0-F1-S1-T1`")],
            context=None,
        )
        assert payload["text"].startswith("<!here> ")
        assert "Sample headline" in payload["text"]

    def test_payload_first_block_also_carries_here_mention(self) -> None:
        payload = notifications._build_slack_payload(
            summary="Sample headline",
            fields=[],
            context=None,
        )
        first_block_text = payload["blocks"][0]["text"]["text"]
        assert "<!here>" in first_block_text

    def test_payload_blocks_carry_fields_section_when_provided(self) -> None:
        payload = notifications._build_slack_payload(
            summary="x",
            fields=[("Task", "`E0-F1-S1-T1`"), ("Title", "demo")],
            context="Reason: dep",
        )
        block_types = [b["type"] for b in payload["blocks"]]
        assert block_types == ["section", "section", "context"]

    def test_payload_always_carries_fields_section_with_backlog_label(self) -> None:
        """The Backlog field is auto-injected, so the fields-section block
        is always present even when the caller passes no event-specific fields."""
        payload = notifications._build_slack_payload(summary="x", fields=[], context=None)
        block_types = [b["type"] for b in payload["blocks"]]
        assert block_types == ["section", "section"]
        # Backlog is the sole field when callers supply none.
        fields_block = payload["blocks"][1]
        assert len(fields_block["fields"]) == 1
        assert "*Backlog*" in fields_block["fields"][0]["text"]

    def test_payload_backlog_field_is_first(self) -> None:
        """The Backlog field is always the first row of the fields block so
        operators monitoring multiple workspaces see source-of-ping at a glance."""
        payload = notifications._build_slack_payload(
            summary="x",
            fields=[("Task", "`E0-F1-S1-T1`")],
            context=None,
        )
        fields_block = payload["blocks"][1]
        assert "*Backlog*" in fields_block["fields"][0]["text"]
        assert "*Task*" in fields_block["fields"][1]["text"]


# ---------------------------------------------------------------------------
# Per-event dispatchers — gating + payload shape
# ---------------------------------------------------------------------------


class TestNotifyWorkUnitDone:
    def test_no_post_when_event_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample")
        posted.assert_not_called()

    def test_slack_post_fires_when_toggle_on(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample title")
        posted.assert_called_once()
        args, _ = posted.call_args
        url, payload, timeout = args
        assert url == "https://hooks.slack.com/services/T/B/X"
        assert "<!here>" in payload["text"]
        assert "E0-F1-S1-T1" in payload["text"]
        assert timeout == 10.0

    def test_no_post_when_slack_endpoint_disabled(self) -> None:
        cfg = _make_config(enabled=True, slack_enabled=False, events={"work_unit_done": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample")
        posted.assert_not_called()

    def test_no_post_when_slack_url_is_null(self) -> None:
        cfg = _make_config(enabled=True, slack_url=None, events={"work_unit_done": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample")
        posted.assert_not_called()

    def test_webhook_failure_does_not_propagate(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": True})

        def boom(*_a: Any, **_kw: Any) -> None:
            raise RuntimeError("network down")

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook", side_effect=boom),
        ):
            # Must not raise.
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample")
        err = capsys.readouterr().err
        assert "[WARN]" in err
        # URL must be masked in the error log.
        assert "https://hooks.slack.com/services/T/B/X" not in err


class TestPerEventPayloads:
    """Spot-check the headline + first-field shape for every event helper."""

    def _capture_one(self, dispatch: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        event_name = kwargs.pop("event_name")
        cfg = _make_config(enabled=True, events={event_name: True})
        captured: list[dict[str, Any]] = []

        def _grab(_url: str, payload: dict[str, Any], _timeout: float) -> None:
            captured.append(payload)

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook", side_effect=_grab),
        ):
            dispatch(*args, **kwargs)
        assert captured, "expected at least one POST"
        return captured[0]

    def test_blocked_operator(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_operator,
            "E0-F1-S1-T1",
            "Sample",
            "dep unsatisfied",
            event_name="work_unit_blocked_operator",
        )
        assert "Operator action required" in payload["text"]

    def test_blocked_runtime_degradation(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_runtime_degradation,
            "E0-F1-S1-T1",
            "Sample",
            "agent-tool-unavailable",
            event_name="work_unit_blocked_runtime_degradation",
        )
        assert "Runtime degradation" in payload["text"]

    def test_blocked_held(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_held,
            "E0-F1-S1-T1",
            "Sample",
            "status is hold",
            event_name="work_unit_blocked_held",
        )
        assert "On hold" in payload["text"]

    def test_blocked_on_held(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_on_held,
            "E0-F1-S1-T1",
            "Sample",
            "marker target is hold",
            event_name="work_unit_blocked_on_held",
        )
        assert "Blocked on held" in payload["text"]

    def test_blocked_auto_clearing(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_auto_clearing,
            "E0-F1-S1-T1",
            "Sample",
            "marker target pending",
            event_name="work_unit_blocked_auto_clearing",
        )
        assert "Auto-clearing" in payload["text"]

    def test_blocked_awaiting_dependency(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_awaiting_dependency,
            "E0-F1-S1-T1",
            "Sample",
            "dep not yet terminal",
            event_name="work_unit_blocked_awaiting_dependency",
        )
        assert "Awaiting dependency" in payload["text"]

    def test_blocked_amendment_recovery(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_blocked_amendment_recovery,
            "E0-F1-S1-T1",
            "Sample",
            "rejected-requests archive present",
            event_name="work_unit_blocked_amendment_recovery",
        )
        assert "amendment recovery" in payload["text"].lower()

    def test_every_payload_carries_backlog_field(self) -> None:
        """Every Slack payload includes a top-level ``Backlog`` field naming the
        active workspace (operator request 2026-05-19).  Smoke-tests with the
        ``work_unit_done`` helper since the field is injected by the shared
        payload builder."""
        payload = self._capture_one(
            notifications.notify_work_unit_done,
            "E0-F1-S1-T1",
            "Sample title",
            event_name="work_unit_done",
        )
        first_fields_block = next(b for b in payload["blocks"] if b["type"] == "section" and "fields" in b)
        backlog_field = next(f for f in first_fields_block["fields"] if "*Backlog*" in f["text"])
        assert backlog_field is not None

    def test_materialised(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_materialised,
            "E0-F1-S1-T2",
            "Cleanup",
            "E0-F1-S1-T1",
            event_name="work_unit_materialised",
        )
        assert "materialised" in payload["text"]

    def test_promoted(self) -> None:
        payload = self._capture_one(
            notifications.notify_work_unit_promoted,
            "E0-F1-S1-T2",
            "Promoted",
            event_name="work_unit_promoted",
        )
        assert "promoted" in payload["text"]

    def test_pr_opened(self) -> None:
        payload = self._capture_one(
            notifications.notify_pr_opened,
            "E0-F1-S1-T1",
            "acme/widget",
            "https://github.com/acme/widget/pull/1",
            event_name="pr_opened",
        )
        assert "PR opened" in payload["text"]
        assert "https://github.com/acme/widget/pull/1" in payload["text"]

    def test_pr_merged(self) -> None:
        payload = self._capture_one(
            notifications.notify_pr_merged,
            "E0-F1-S1-T1",
            "acme/widget",
            "https://github.com/acme/widget/pull/1",
            event_name="pr_merged",
        )
        assert "PR merged" in payload["text"]

    def test_ci_failure(self) -> None:
        payload = self._capture_one(
            notifications.notify_ci_failure,
            "E0-F1-S1-T1",
            "acme/widget",
            "https://github.com/acme/widget/pull/1",
            2,
            event_name="ci_failure",
        )
        assert "CI failed" in payload["text"]
        assert "attempt 2" in payload["text"]

    def test_ci_pass(self) -> None:
        """Issue #219 Bundle C: notify_ci_pass fires on CIResult.GREEN
        in the finalize path so operators under ``auto_merge: false``
        get an explicit "ready for manual merge" signal."""
        payload = self._capture_one(
            notifications.notify_ci_pass,
            "E0-F1-S1-T1",
            "acme/widget",
            "https://github.com/acme/widget/pull/1",
            event_name="ci_pass",
        )
        assert "CI passed" in payload["text"]
        assert "ready for manual merge" in payload["text"].lower()

    def test_orchestrator_stop_with_inflight(self) -> None:
        payload = self._capture_one(
            notifications.notify_orchestrator_stop,
            "clean",
            "E0-F1-S1-T1",
            event_name="orchestrator_stop",
        )
        assert "Orchestrator stopped" in payload["text"]
        # In-flight WU appears in a structured field.
        field_blob = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "E0-F1-S1-T1" in field_blob

    def test_orchestrator_stop_without_inflight(self) -> None:
        payload = self._capture_one(
            notifications.notify_orchestrator_stop,
            "clean",
            None,
            event_name="orchestrator_stop",
        )
        # In-flight field absent (only the Reason field present).
        for block in payload["blocks"]:
            for f in block.get("fields", []):
                assert "In-flight" not in f.get("text", "")

    def test_quota_waiting(self) -> None:
        """The orchestrator hit a quota and started waiting; payload carries the
        source/reason and the reset-at time."""
        payload = self._capture_one(
            notifications.notify_quota_waiting,
            "anthropic-api",
            "2026-01-01T16:10:00+00:00",
            event_name="quota_waiting",
        )
        assert "quota" in payload["text"].lower()
        field_blob = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "anthropic-api" in field_blob
        assert "2026-01-01T16:10:00+00:00" in field_blob

    def test_quota_resumed(self) -> None:
        """The quota recovered; payload carries the waited-seconds total."""
        payload = self._capture_one(
            notifications.notify_quota_resumed,
            1234,
            event_name="quota_resumed",
        )
        assert "quota" in payload["text"].lower()
        field_blob = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "1234" in field_blob

    def test_auto_restart(self) -> None:
        payload = self._capture_one(
            notifications.notify_orchestrator_auto_restart,
            ["E0-F1-S1-T1", "E0-F1-S1-T2"],
            event_name="orchestrator_auto_restart",
        )
        assert "auto-restarting" in payload["text"]

    def test_auto_restart_truncates_long_list(self) -> None:
        payload = self._capture_one(
            notifications.notify_orchestrator_auto_restart,
            [f"E0-F1-S1-T{i}" for i in range(20)],
            event_name="orchestrator_auto_restart",
        )
        field_blob = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "+15 more" in field_blob


# ---------------------------------------------------------------------------
# Quota notification events (quota_waiting / quota_resumed)
# ---------------------------------------------------------------------------


class TestQuotaNotificationEvents:
    """The two quota lifecycle events follow the shared notify_* contract:
    they are in ALL_EVENTS, have per-event toggles, gate on the toggle +
    master switch + webhook presence, and are best-effort on transport
    failure."""

    def test_events_registered_in_all_events(self) -> None:
        assert notifications.EVENT_QUOTA_WAITING in notifications.ALL_EVENTS
        assert notifications.EVENT_QUOTA_RESUMED in notifications.ALL_EVENTS
        # Constant strings match their config-toggle field names.
        assert notifications.EVENT_QUOTA_WAITING == "quota_waiting"
        assert notifications.EVENT_QUOTA_RESUMED == "quota_resumed"

    def test_toggle_fields_default_false(self) -> None:
        events = NotificationsEventsConfig()
        assert events.quota_waiting is False
        assert events.quota_resumed is False

    def test_quota_waiting_no_post_when_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_waiting": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_waiting("anthropic-api", "unknown")
        posted.assert_not_called()

    def test_quota_resumed_no_post_when_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_resumed": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_resumed(42)
        posted.assert_not_called()

    def test_quota_waiting_no_post_when_master_switch_off(self) -> None:
        cfg = _make_config(enabled=False, events={"quota_waiting": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_waiting("anthropic-api", "unknown")
        posted.assert_not_called()

    def test_quota_waiting_no_post_when_webhook_absent(self) -> None:
        cfg = _make_config(enabled=True, slack_url=None, events={"quota_waiting": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_waiting("anthropic-api", "unknown")
        posted.assert_not_called()

    def test_quota_waiting_posts_when_toggle_on(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_waiting": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_waiting("anthropic-api", "2026-01-01T16:10:00+00:00")
        posted.assert_called_once()

    def test_quota_resumed_posts_when_toggle_on(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_resumed": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_resumed(99)
        posted.assert_called_once()

    def test_quota_waiting_webhook_failure_does_not_propagate(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_config(enabled=True, events={"quota_waiting": True})

        def boom(*_a: Any, **_kw: Any) -> None:
            raise RuntimeError("network down")

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook", side_effect=boom),
        ):
            # Must not raise -- best-effort.
            notifications.notify_quota_waiting("anthropic-api", "unknown")
        assert "[WARN]" in capsys.readouterr().err

    def test_quota_resumed_webhook_failure_does_not_propagate(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_config(enabled=True, events={"quota_resumed": True})

        def boom(*_a: Any, **_kw: Any) -> None:
            raise RuntimeError("network down")

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook", side_effect=boom),
        ):
            notifications.notify_quota_resumed(7)
        assert "[WARN]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# send_test_notification (CLI self-test driver)
# ---------------------------------------------------------------------------


class TestSendTestNotification:
    def test_unknown_event_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown event"):
            notifications.send_test_notification("not_a_real_event")

    def test_master_switch_off_emits_info(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_config(enabled=False)
        with patch.object(notifications, "_load_notifications_config", return_value=cfg):
            notifications.send_test_notification("work_unit_done")
        err = capsys.readouterr().err
        assert "notifications.enabled is false" in err

    def test_forces_event_on_for_test_dispatch(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.send_test_notification("work_unit_done")
        # Despite work_unit_done=False in the config, send_test_notification
        # temporarily flips it on so the operator can verify regardless.
        posted.assert_called_once()

    def test_restores_toggle_after_dispatch(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook"),
        ):
            notifications.send_test_notification("work_unit_done")
        assert cfg.events.work_unit_done is False

    @pytest.mark.parametrize("event", notifications.ALL_EVENTS)
    def test_every_event_has_a_sample(self, event: str) -> None:
        cfg = _make_config(enabled=True)
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.send_test_notification(event)
        posted.assert_called_once()


# ---------------------------------------------------------------------------
# Config-loader integration (parser + validation)
# ---------------------------------------------------------------------------


class TestNotificationsConfigParser:
    """Pin the schema -> dataclass mapping + value-level fail-fast checks."""

    def _load(self, yaml_text: str) -> Any:
        import textwrap
        from pathlib import Path

        from devbench.config_loader import load_runtime_config

        tmp = Path("/tmp") / "notif-test.yaml"
        tmp.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        return load_runtime_config(tmp, {})

    def test_absent_block_yields_defaults(self) -> None:
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            """
        )
        assert rt.notifications.enabled is False
        assert rt.notifications.slack.enabled is False
        assert rt.notifications.slack.webhook_url is None
        assert rt.notifications.events.work_unit_done is False

    def test_full_block_parses_every_field(self) -> None:
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
              timeout_seconds: 15
              events:
                work_unit_done: true
              slack:
                enabled: true
                webhook_url: "https://hooks.slack.com/services/T/B/X"
            """
        )
        n = rt.notifications
        assert n.enabled is True
        assert n.slack.enabled is True
        assert n.slack.webhook_url == "https://hooks.slack.com/services/T/B/X"
        assert n.timeout_seconds == 15.0
        assert n.events.work_unit_done is True
        assert n.events.work_unit_blocked_operator is False  # default

    def test_per_class_blocked_event_toggles_default_false_and_parse(self) -> None:
        """The 6 new per-class blocked-event toggles (#209) default to false and
        accept independent boolean settings from yaml."""
        # Defaults when absent.
        rt_default = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
            """
        )
        assert rt_default.notifications.events.work_unit_blocked_runtime_degradation is False
        assert rt_default.notifications.events.work_unit_blocked_held is False
        assert rt_default.notifications.events.work_unit_blocked_on_held is False
        assert rt_default.notifications.events.work_unit_blocked_auto_clearing is False
        assert rt_default.notifications.events.work_unit_blocked_awaiting_dependency is False
        assert rt_default.notifications.events.work_unit_blocked_amendment_recovery is False

        # Explicit yaml values flip each independently.
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
              events:
                work_unit_blocked_runtime_degradation: true
                work_unit_blocked_held: true
                work_unit_blocked_on_held: true
                work_unit_blocked_auto_clearing: true
                work_unit_blocked_awaiting_dependency: true
                work_unit_blocked_amendment_recovery: true
            """
        )
        assert rt.notifications.events.work_unit_blocked_runtime_degradation is True
        assert rt.notifications.events.work_unit_blocked_held is True
        assert rt.notifications.events.work_unit_blocked_on_held is True
        assert rt.notifications.events.work_unit_blocked_auto_clearing is True
        assert rt.notifications.events.work_unit_blocked_awaiting_dependency is True
        assert rt.notifications.events.work_unit_blocked_amendment_recovery is True

    def test_ci_pass_event_toggle_default_false_and_parse(self) -> None:
        """Issue #219 Bundle C: the new ``ci_pass`` event toggle defaults
        to False and accepts a boolean override from yaml.  Defaulting
        false on upgrade keeps existing workspaces silent until they opt
        in."""
        # Default false when absent from yaml.
        rt_default = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
            """
        )
        assert rt_default.notifications.events.ci_pass is False

        # Explicit override flips it.
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
              events:
                ci_pass: true
            """
        )
        assert rt.notifications.events.ci_pass is True

    def test_quota_event_toggles_default_false_and_parse(self) -> None:
        """The two quota lifecycle event toggles default to False and accept
        independent boolean overrides from yaml."""
        # Default false when absent.
        rt_default = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
            """
        )
        assert rt_default.notifications.events.quota_waiting is False
        assert rt_default.notifications.events.quota_resumed is False

        # Explicit yaml values flip each independently.
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
              events:
                quota_waiting: true
                quota_resumed: true
            """
        )
        assert rt.notifications.events.quota_waiting is True
        assert rt.notifications.events.quota_resumed is True

    def test_non_https_webhook_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="must start with 'https://'"):
            self._load(
                """\
                repos:
                  org/repo:
                    default_branch: main
                notifications:
                  enabled: true
                  slack:
                    webhook_url: "http://hooks.slack.com/services/T/B/X"
                """
            )


# ---------------------------------------------------------------------------
# post_webhook -- best-effort HTTP POST helper (webhook transport).
#
# Relocated webhook-transport tests: post_webhook / _http_post now live in
# devbench.notifications. The webhook transport lives here, so its tests do too.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostWebhook:
    """post_webhook POSTs JSON payload to url; failures are logged but do not raise.

    Webhook delivery is best-effort -- failures must be logged to stderr but
    must not crash the orchestrator.
    """

    def _make_payload(self) -> dict[str, Any]:
        return {
            "event": "work_unit_done",
            "reason": "completed",
            "unit_id": "E0-F1-S1-T1",
            "emitted_at": "2026-01-01T12:00:00+00:00",
        }

    def test_post_webhook_sends_json_body(self) -> None:
        """post_webhook encodes payload as JSON and calls _http_post with correct args."""

        from devbench.notifications import post_webhook

        captured: list[dict[str, Any]] = []

        def _fake_http_post(
            parsed: urllib.parse.SplitResult,
            body: bytes,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> None:
            captured.append({"parsed": parsed, "body": body, "headers": headers, "timeout": timeout_seconds})

        payload = self._make_payload()
        with patch("devbench.notifications._http_post", side_effect=_fake_http_post):
            post_webhook("https://example.com/hook", payload, timeout_seconds=5.0)

        assert len(captured) == 1
        assert captured[0]["parsed"].scheme == "https"
        assert captured[0]["parsed"].hostname == "example.com"
        body_decoded = json.loads(captured[0]["body"].decode("utf-8"))
        assert body_decoded["event"] == "work_unit_done"
        assert body_decoded["reason"] == "completed"

    def test_post_webhook_sets_content_type_json_header(self) -> None:
        """post_webhook includes Content-Type: application/json in the headers passed to _http_post."""

        from devbench.notifications import post_webhook

        captured_headers: list[dict[str, str]] = []

        def _fake_http_post(
            parsed: urllib.parse.SplitResult,
            body: bytes,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> None:
            captured_headers.append(headers)

        with patch("devbench.notifications._http_post", side_effect=_fake_http_post):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        assert len(captured_headers) == 1
        header_keys_lower = {k.lower() for k in captured_headers[0]}
        assert "content-type" in header_keys_lower
        content_type_value = next(v for k, v in captured_headers[0].items() if k.lower() == "content-type")
        assert "application/json" in content_type_value

    def test_post_webhook_logs_http_error_to_stderr_without_raising(self, capsys: pytest.CaptureFixture[str]) -> None:
        """post_webhook logs HTTP errors to stderr and returns normally (best-effort)."""
        from devbench.notifications import post_webhook

        def _raise_http_error(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("HTTP 500 Internal Server Error")

        with patch("devbench.notifications._http_post", side_effect=_raise_http_error):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert "webhook" in stderr_output.lower() or "500" in stderr_output or "http" in stderr_output.lower()

    def test_post_webhook_logs_url_error_to_stderr_without_raising(self, capsys: pytest.CaptureFixture[str]) -> None:
        """post_webhook logs connection failures to stderr and returns normally."""
        from devbench.notifications import post_webhook

        def _raise_connection_error(*args: Any, **kwargs: Any) -> None:
            raise ConnectionRefusedError("Connection refused")

        with patch("devbench.notifications._http_post", side_effect=_raise_connection_error):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert "webhook" in stderr_output.lower() or "connection" in stderr_output.lower()

    def test_post_webhook_logs_timeout_to_stderr_without_raising(self, capsys: pytest.CaptureFixture[str]) -> None:
        """post_webhook logs TimeoutError to stderr and returns normally (best-effort)."""
        from devbench.notifications import post_webhook

        def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise TimeoutError("timed out")

        with patch("devbench.notifications._http_post", side_effect=_raise_timeout):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert "webhook" in stderr_output.lower() or "timeout" in stderr_output.lower()

    def test_post_webhook_logs_unexpected_exception_to_stderr_without_raising(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """post_webhook logs any unexpected exception to stderr and returns normally."""
        from devbench.notifications import post_webhook

        def _raise_unexpected(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("unexpected failure")

        with patch("devbench.notifications._http_post", side_effect=_raise_unexpected):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert stderr_output.strip() != "", "must log something on unexpected failure"

    @pytest.mark.parametrize(
        "url,payload,timeout_seconds,error_fragment",
        [
            ("", {"event": "pause"}, 5.0, "url"),
            ("https://example.com/hook", {}, 5.0, "payload"),
            ("https://example.com/hook", {"event": "pause"}, 0.0, "timeout"),
            ("https://example.com/hook", {"event": "pause"}, -1.0, "timeout"),
            ("file:///etc/passwd", {"event": "pause"}, 5.0, "scheme"),
            ("ftp://example.com/hook", {"event": "pause"}, 5.0, "scheme"),
        ],
    )
    def test_post_webhook_validates_inputs(
        self,
        url: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        error_fragment: str,
    ) -> None:
        """post_webhook raises ValueError for invalid inputs (fail-fast)."""
        from devbench.notifications import post_webhook

        with pytest.raises(ValueError, match=error_fragment):
            post_webhook(url, payload, timeout_seconds=timeout_seconds)


# ---------------------------------------------------------------------------
# Issue #203: _http_post internals -- direct coverage of the network-level
# helper that post_webhook delegates to. Existing TestPostWebhook stubs
# _http_post itself; this class drives _http_post directly with patched
# http.client connection classes so the HTTPS/HTTP branches, path/query
# construction, and finally-block close are all exercised.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHttpPostInternals:
    """Issue #203: cover the network-level POST helper without real I/O.

    Drives :func:`devbench.notifications._http_post` directly with patched
    ``http.client.HTTPSConnection`` and ``HTTPConnection`` classes that
    record constructor arguments and the ``request`` / ``getresponse`` /
    ``read`` / ``close`` call sequence.  No real sockets are opened.
    """

    @staticmethod
    def _split(url: str) -> urllib.parse.SplitResult:
        return urllib.parse.urlsplit(url)

    @staticmethod
    def _conn_factories(
        monkeypatch: pytest.MonkeyPatch,
        *,
        request_raises: Exception | None = None,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Patch http.client connection classes; return (https_cls, http_cls, conn_instance)."""
        from devbench import notifications as notifications_mod

        conn = MagicMock(name="conn")
        if request_raises is not None:
            conn.request.side_effect = request_raises
        conn.getresponse.return_value = MagicMock(read=MagicMock(return_value=b""))

        https_cls = MagicMock(name="HTTPSConnection", return_value=conn)
        http_cls = MagicMock(name="HTTPConnection", return_value=conn)
        monkeypatch.setattr(notifications_mod.http.client, "HTTPSConnection", https_cls)
        monkeypatch.setattr(notifications_mod.http.client, "HTTPConnection", http_cls)
        return https_cls, http_cls, conn

    def test_https_scheme_uses_https_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An https:// URL is dispatched through HTTPSConnection with hostname + port + timeout."""
        from devbench.notifications import _http_post

        https_cls, http_cls, conn = self._conn_factories(monkeypatch)
        _http_post(self._split("https://example.com/hook"), b"{}", {"Content-Type": "application/json"}, 7.5)

        https_cls.assert_called_once_with("example.com", port=None, timeout=7.5)
        http_cls.assert_not_called()
        conn.request.assert_called_once_with("POST", "/hook", body=b"{}", headers={"Content-Type": "application/json"})
        conn.getresponse.assert_called_once_with()
        conn.close.assert_called_once_with()

    def test_http_scheme_with_port_uses_http_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An http:// URL with an explicit port is dispatched through HTTPConnection."""
        from devbench.notifications import _http_post

        https_cls, http_cls, conn = self._conn_factories(monkeypatch)
        _http_post(self._split("http://example.com:8080/hook"), b"{}", {"Content-Type": "application/json"}, 3.0)

        http_cls.assert_called_once_with("example.com", port=8080, timeout=3.0)
        https_cls.assert_not_called()
        conn.request.assert_called_once_with("POST", "/hook", body=b"{}", headers={"Content-Type": "application/json"})

    def test_path_with_query_string_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A URL with a query string is passed to conn.request with the query intact."""
        from devbench.notifications import _http_post

        _, _, conn = self._conn_factories(monkeypatch)
        _http_post(self._split("https://example.com/hook?x=1&y=2"), b"{}", {"Content-Type": "application/json"}, 5.0)

        conn.request.assert_called_once_with(
            "POST", "/hook?x=1&y=2", body=b"{}", headers={"Content-Type": "application/json"}
        )

    def test_empty_path_defaults_to_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A URL with no path component is dispatched with path '/'."""
        from devbench.notifications import _http_post

        _, _, conn = self._conn_factories(monkeypatch)
        _http_post(self._split("https://example.com"), b"{}", {"Content-Type": "application/json"}, 5.0)

        conn.request.assert_called_once_with("POST", "/", body=b"{}", headers={"Content-Type": "application/json"})

    def test_exception_in_request_still_closes_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When conn.request raises, the finally-block must still call conn.close."""
        from devbench.notifications import _http_post

        boom = RuntimeError("boom")
        _, _, conn = self._conn_factories(monkeypatch, request_raises=boom)

        with pytest.raises(RuntimeError, match="boom"):
            _http_post(
                self._split("https://example.com/hook"),
                b"{}",
                {"Content-Type": "application/json"},
                5.0,
            )

        conn.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# E14-F2-S1-T1: Stop-class to mention-level mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStopClassConstants:
    """Stop-class and mention-level constants are single-sourced in notifications.py."""

    def test_all_stop_classes_exported(self) -> None:
        assert hasattr(notifications, "STOP_CLASS_PREMATURE_TURN_END")
        assert hasattr(notifications, "STOP_CLASS_COMPLETION")
        assert hasattr(notifications, "STOP_CLASS_DRAIN")
        assert hasattr(notifications, "STOP_CLASS_OPERATOR_INTERRUPT")
        assert hasattr(notifications, "STOP_CLASS_CRASH")
        assert hasattr(notifications, "STOP_CLASS_QUOTA_EXHAUSTED")

    def test_all_mention_levels_exported(self) -> None:
        assert hasattr(notifications, "MENTION_LEVEL_HERE")
        assert hasattr(notifications, "MENTION_LEVEL_NONE")

    def test_all_stop_classes_tuple_exported(self) -> None:
        assert hasattr(notifications, "ALL_STOP_CLASSES")
        sc = notifications.ALL_STOP_CLASSES
        assert isinstance(sc, tuple)
        assert notifications.STOP_CLASS_PREMATURE_TURN_END in sc
        assert notifications.STOP_CLASS_COMPLETION in sc
        assert notifications.STOP_CLASS_DRAIN in sc
        assert notifications.STOP_CLASS_OPERATOR_INTERRUPT in sc
        assert notifications.STOP_CLASS_CRASH in sc
        assert notifications.STOP_CLASS_QUOTA_EXHAUSTED in sc

    def test_all_mention_levels_tuple_exported(self) -> None:
        assert hasattr(notifications, "ALL_MENTION_LEVELS")
        ml = notifications.ALL_MENTION_LEVELS
        assert isinstance(ml, tuple)
        assert notifications.MENTION_LEVEL_HERE in ml
        assert notifications.MENTION_LEVEL_NONE in ml


@pytest.mark.unit
class TestClassifyStopClass:
    """classify_stop_class maps reason strings to stop-class tokens."""

    @pytest.mark.parametrize(
        "reason,expected_class",
        [
            ("premature-turn-end", notifications.STOP_CLASS_PREMATURE_TURN_END),
            ("clean exit: ALL_DONE", notifications.STOP_CLASS_COMPLETION),
            ("clean exit: NO_ACTIONABLE -- 5 done", notifications.STOP_CLASS_COMPLETION),
            ("clean exit (SystemExit 0)", notifications.STOP_CLASS_COMPLETION),
            ("drain enforced: operator requested", notifications.STOP_CLASS_DRAIN),
            ("drain enforced: ", notifications.STOP_CLASS_DRAIN),
            ("interrupted by operator (Ctrl+C / SIGINT)", notifications.STOP_CLASS_OPERATOR_INTERRUPT),
            ("crash: RuntimeError: something bad", notifications.STOP_CLASS_CRASH),
            ("crash: ValueError: oops", notifications.STOP_CLASS_CRASH),
            ("quota-wait-recovered", notifications.STOP_CLASS_QUOTA_EXHAUSTED),
            ("quota-wait-timeout", notifications.STOP_CLASS_QUOTA_EXHAUSTED),
            ("continuation budget exhausted", notifications.STOP_CLASS_CRASH),
        ],
    )
    def test_reason_maps_to_class(self, reason: str, expected_class: str) -> None:
        assert notifications.classify_stop_class(reason) == expected_class

    def test_unknown_reason_maps_to_crash(self) -> None:
        """Unrecognized reason strings are classified as crash (fail-safe, not silent)."""
        assert notifications.classify_stop_class("something-totally-unknown") == notifications.STOP_CLASS_CRASH


@pytest.mark.unit
class TestResolveMentionText:
    """resolve_mention_text converts a mention-level token to the Slack mention string."""

    def test_here_level_returns_here_string(self) -> None:
        text = notifications.resolve_mention_text(notifications.MENTION_LEVEL_HERE)
        assert text == "<!here>"

    def test_none_level_returns_empty_string(self) -> None:
        text = notifications.resolve_mention_text(notifications.MENTION_LEVEL_NONE)
        assert text == ""

    def test_unknown_level_raises(self) -> None:
        with pytest.raises(ValueError, match="mention level"):
            notifications.resolve_mention_text("invalid-level")


@pytest.mark.unit
class TestDefaultMentionMap:
    """DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP is noise-reducing: attention-worthy -> here, quiet -> none."""

    def test_attention_worthy_classes_default_to_here(self) -> None:
        m = notifications.DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP
        for cls in (
            notifications.STOP_CLASS_PREMATURE_TURN_END,
            notifications.STOP_CLASS_OPERATOR_INTERRUPT,
            notifications.STOP_CLASS_CRASH,
            notifications.STOP_CLASS_QUOTA_EXHAUSTED,
        ):
            assert m[cls] == notifications.MENTION_LEVEL_HERE, f"{cls} should default to 'here'"

    def test_quiet_classes_default_to_none(self) -> None:
        m = notifications.DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP
        for cls in (notifications.STOP_CLASS_COMPLETION, notifications.STOP_CLASS_DRAIN):
            assert m[cls] == notifications.MENTION_LEVEL_NONE, f"{cls} should default to 'none'"

    def test_map_covers_all_stop_classes(self) -> None:
        m = notifications.DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP
        for cls in notifications.ALL_STOP_CLASSES:
            assert cls in m, f"stop class {cls!r} missing from DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP"


@pytest.mark.unit
class TestOrchestratorStopMentionDispatch:
    """notify_orchestrator_stop emits a mention matching the resolved mention level per stop class."""

    def _capture_payload(self, reason: str) -> dict[str, Any]:
        """Call notify_orchestrator_stop with the given reason and capture the posted payload."""
        cfg = _make_config(enabled=True, events={"orchestrator_stop": True})
        captured: list[dict[str, Any]] = []

        def _grab(_url: str, payload: dict[str, Any], _timeout: float) -> None:
            captured.append(payload)

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook", side_effect=_grab),
        ):
            notifications.notify_orchestrator_stop(reason, None)
        assert captured, "expected at least one POST"
        return captured[0]

    @pytest.mark.parametrize(
        "reason",
        [
            "premature-turn-end",
            "interrupted by operator (Ctrl+C / SIGINT)",
            "crash: RuntimeError: bang",
            "quota-wait-timeout",
        ],
    )
    def test_attention_worthy_stop_emits_here_mention(self, reason: str) -> None:
        """Attention-worthy stop classes include <!here> in the payload."""
        payload = self._capture_payload(reason)
        assert "<!here>" in payload["text"], f"expected <!here> for reason {reason!r}"

    @pytest.mark.parametrize(
        "reason",
        [
            "clean exit: ALL_DONE",
            "drain enforced: manual request",
        ],
    )
    def test_quiet_stop_omits_here_mention(self, reason: str) -> None:
        """Quiet stop classes (completion, drain) do NOT include <!here> in the payload."""
        payload = self._capture_payload(reason)
        assert "<!here>" not in payload["text"], f"expected no <!here> for reason {reason!r}"

    def test_no_hardcoded_mention_literal_in_dispatch_path(self) -> None:
        """The dispatch path must resolve mention from config, not embed a literal string."""
        import ast
        import inspect

        source = inspect.getsource(notifications.notify_orchestrator_stop)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "<!here>" not in node.value, (
                    "notify_orchestrator_stop must not contain a hard-coded '<!here>' literal"
                )

    def test_unknown_stop_class_in_map_raises(self) -> None:
        """A map with an unknown stop-class key raises ValueError at validation time."""
        from devbench.notifications import validate_stop_mention_map

        with pytest.raises(ValueError, match="stop-class"):
            validate_stop_mention_map({"not-a-class": "here"})

    def test_invalid_mention_level_in_map_raises(self) -> None:
        """A map with an invalid mention level raises ValueError at validation time."""
        from devbench.notifications import validate_stop_mention_map

        with pytest.raises(ValueError, match="mention level"):
            validate_stop_mention_map({notifications.STOP_CLASS_COMPLETION: "loud"})

    def test_config_import_error_falls_back_to_default_map(self) -> None:
        """When devbench.config cannot be imported, _resolve_stop_mention uses the default map."""
        from devbench.notifications import _resolve_stop_mention

        with patch.dict("sys.modules", {"devbench.config": None}):
            # STOP_CLASS_CRASH -> MENTION_LEVEL_HERE by default map -> "<!here>"
            text = _resolve_stop_mention(notifications.STOP_CLASS_CRASH)
        assert text == "<!here>"

    def test_config_missing_attr_falls_back_to_default_map(self) -> None:
        """When ORCHESTRATOR_STOP_MENTION_MAP is absent from the config module, ImportError is raised
        by the from-import and _resolve_stop_mention falls back to the default map."""
        import types

        from devbench.notifications import _resolve_stop_mention

        fake_config = types.ModuleType("devbench.config")
        # fake_config has no ORCHESTRATOR_STOP_MENTION_MAP attribute;
        # 'from devbench.config import ORCHESTRATOR_STOP_MENTION_MAP' raises ImportError
        with patch.dict("sys.modules", {"devbench.config": fake_config}):
            text = _resolve_stop_mention(notifications.STOP_CLASS_DRAIN)
        # STOP_CLASS_DRAIN -> MENTION_LEVEL_NONE -> ""
        assert text == ""


@pytest.mark.unit
class TestOrchestratorStopProgressContext:
    """E14-F2-S2-T1: notify_orchestrator_stop renders done/total progress context.

    AC-1: the stop notification includes done/total work-unit counts and the
    in-flight unit id when applicable, read best-effort from the backlog parser.
    """

    def _capture_stop_payload(
        self,
        reason: str,
        in_flight_unit_id: str | None,
        done_count: int | None,
        total_count: int | None,
    ) -> dict[str, Any]:
        """Call notify_orchestrator_stop with progress context and capture the Slack payload."""
        cfg = _make_config(enabled=True, events={"orchestrator_stop": True})
        captured: list[dict[str, Any]] = []

        def _grab(_url: str, payload: dict[str, Any], _timeout: float) -> None:
            captured.append(payload)

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook", side_effect=_grab),
        ):
            notifications.notify_orchestrator_stop(reason, in_flight_unit_id, done_count, total_count)
        assert captured, "expected at least one POST"
        return captured[0]

    def test_progress_string_contains_done_and_total(self) -> None:
        """The payload blocks contain the done/total counts as a progress string."""
        payload = self._capture_stop_payload(
            reason="clean exit: ALL_DONE",
            in_flight_unit_id=None,
            done_count=7,
            total_count=10,
        )
        all_text = " ".join(
            str(v)
            for block in payload["blocks"]
            for v in ([block.get("text", {}).get("text", "")] + [f.get("text", "") for f in block.get("fields", [])])
        ) + payload.get("text", "")
        assert "7" in all_text, "done count 7 must appear in the payload"
        assert "10" in all_text, "total count 10 must appear in the payload"

    def test_progress_string_contains_inflight_unit_id(self) -> None:
        """The payload includes the in-flight unit id when provided alongside progress counts."""
        payload = self._capture_stop_payload(
            reason="premature-turn-end",
            in_flight_unit_id="E5-F1-S1-T3",
            done_count=3,
            total_count=8,
        )
        all_text = " ".join(
            str(v)
            for block in payload["blocks"]
            for v in ([block.get("text", {}).get("text", "")] + [f.get("text", "") for f in block.get("fields", [])])
        ) + payload.get("text", "")
        assert "E5-F1-S1-T3" in all_text, "in-flight unit id must appear in the payload"
        assert "3" in all_text, "done count 3 must appear in the payload"
        assert "8" in all_text, "total count 8 must appear in the payload"

    def test_progress_string_contains_no_em_dash(self) -> None:
        """The rendered progress string must not contain the em-dash character (U+2014)."""
        payload = self._capture_stop_payload(
            reason="clean exit: ALL_DONE",
            in_flight_unit_id="E1-F1-S1-T1",
            done_count=5,
            total_count=5,
        )
        full_text = str(payload)
        assert "\u2014" not in full_text, "em-dash must not appear anywhere in the stop notification payload"

    def test_progress_omitted_when_counts_are_none(self) -> None:
        """When done_count and total_count are None, no progress field is added."""
        payload = self._capture_stop_payload(
            reason="clean exit: ALL_DONE",
            in_flight_unit_id=None,
            done_count=None,
            total_count=None,
        )
        all_fields_text = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "Progress" not in all_fields_text, "Progress field must be absent when counts are None"

    @pytest.mark.parametrize(
        ("reason", "in_flight_id", "done_count", "total_count"),
        [
            ("clean exit: ALL_DONE", None, 10, 10),
            ("premature-turn-end", "E1-F1-S1-T2", 4, 12),
            ("drain enforced: manual", None, 0, 5),
        ],
    )
    def test_progress_parametrized_scenarios(
        self,
        reason: str,
        in_flight_id: str | None,
        done_count: int,
        total_count: int,
    ) -> None:
        """Multiple progress scenarios all include done/total counts without em-dashes."""
        payload = self._capture_stop_payload(
            reason=reason,
            in_flight_unit_id=in_flight_id,
            done_count=done_count,
            total_count=total_count,
        )
        full_text = str(payload)
        assert "\u2014" not in full_text, f"em-dash in payload for reason={reason!r}"
        assert str(done_count) in full_text, f"done count {done_count} missing from payload"
        assert str(total_count) in full_text, f"total count {total_count} missing from payload"
