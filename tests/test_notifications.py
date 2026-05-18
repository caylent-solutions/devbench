"""Unit tests for ``devbench.notifications`` (PR #202).

Pins the per-event dispatcher contract: each ``notify_*`` helper is
best-effort, returns early when the per-event toggle is off or the
master switch is off, builds a Slack block-kit payload with ``<@user>``
mention when configured, posts the raw JSON to the generic ``webhook_url``
slot, and swallows every HTTP exception (logged as ``[WARN]`` to stderr).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

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
    slack_url: str | None = "https://hooks.slack.com/services/T/B/X",
    slack_user_id: str | None = "U12345678",
    generic_url: str | None = None,
    events: dict[str, bool] | None = None,
) -> NotificationsConfig:
    """Build a ``NotificationsConfig`` with the toggles flipped per ``events``."""
    return NotificationsConfig(
        enabled=enabled,
        slack=NotificationsSlackConfig(webhook_url=slack_url, user_id=slack_user_id),
        webhook_url=generic_url,
        timeout_seconds=10.0,
        events=NotificationsEventsConfig(**(events or {})),
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
# _mention + _build_slack_payload
# ---------------------------------------------------------------------------


class TestSlackPayload:
    def test_mention_empty_when_no_user_id(self) -> None:
        assert notifications._mention(None) == ""

    def test_mention_renders_with_trailing_space(self) -> None:
        assert notifications._mention("U12345678") == "<@U12345678> "

    def test_payload_top_line_includes_mention(self) -> None:
        payload = notifications._build_slack_payload(
            summary="Sample headline",
            fields=[("Task", "`E0-F1-S1-T1`")],
            context=None,
            user_id="U12345678",
        )
        assert payload["text"].startswith("<@U12345678> ")
        assert "Sample headline" in payload["text"]

    def test_payload_top_line_skips_mention_when_no_user(self) -> None:
        payload = notifications._build_slack_payload(
            summary="Sample headline",
            fields=[],
            context=None,
            user_id=None,
        )
        assert payload["text"] == "Sample headline"

    def test_payload_blocks_carry_fields_section_when_provided(self) -> None:
        payload = notifications._build_slack_payload(
            summary="x",
            fields=[("Task", "`E0-F1-S1-T1`"), ("Title", "demo")],
            context="Reason: dep",
            user_id="U12345678",
        )
        block_types = [b["type"] for b in payload["blocks"]]
        assert block_types == ["section", "section", "context"]

    def test_payload_blocks_omit_fields_section_when_none(self) -> None:
        payload = notifications._build_slack_payload(summary="x", fields=[], context=None, user_id=None)
        assert len(payload["blocks"]) == 1
        assert payload["blocks"][0]["type"] == "section"


# ---------------------------------------------------------------------------
# _build_generic_payload
# ---------------------------------------------------------------------------


class TestGenericPayload:
    def test_event_kind_at_top_level(self) -> None:
        payload = notifications._build_generic_payload("work_unit_done", unit_id="E0-F1-S1-T1")
        assert payload["event"] == "work_unit_done"
        assert payload["unit_id"] == "E0-F1-S1-T1"


# ---------------------------------------------------------------------------
# Per-event dispatchers — gating + payload shape
# ---------------------------------------------------------------------------


class TestNotifyWorkUnitDone:
    def test_no_post_when_event_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.quota.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample")
        posted.assert_not_called()

    def test_slack_post_fires_when_toggle_on(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.quota.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample title")
        posted.assert_called_once()
        args, _ = posted.call_args
        url, payload, timeout = args
        assert url == "https://hooks.slack.com/services/T/B/X"
        assert "<@U12345678>" in payload["text"]
        assert "E0-F1-S1-T1" in payload["text"]
        assert timeout == 10.0

    def test_generic_post_also_fires_when_url_set(self) -> None:
        cfg = _make_config(enabled=True, generic_url="https://example.com/hook", events={"work_unit_done": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.quota.post_webhook") as posted,
        ):
            notifications.notify_work_unit_done("E0-F1-S1-T1", "Sample")
        # Two POSTs: Slack + generic.
        assert posted.call_count == 2

    def test_webhook_failure_does_not_propagate(self, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": True})

        def boom(*_a: Any, **_kw: Any) -> None:
            raise RuntimeError("network down")

        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.quota.post_webhook", side_effect=boom),
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
            patch("devbench.quota.post_webhook", side_effect=_grab),
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

    def test_quota_pause(self) -> None:
        now = datetime.now(tz=UTC)
        payload = self._capture_one(
            notifications.notify_quota_pause,
            "subscription rate limit",
            now,
            now,
            event_name="quota_pause",
        )
        assert "Quota pause" in payload["text"]

    def test_quota_resume(self) -> None:
        now = datetime.now(tz=UTC)
        payload = self._capture_one(
            notifications.notify_quota_resume,
            now,
            60,
            event_name="quota_resume",
        )
        assert "Quota recovered" in payload["text"]


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
            patch("devbench.quota.post_webhook") as posted,
        ):
            notifications.send_test_notification("work_unit_done")
        # Despite work_unit_done=False in the config, send_test_notification
        # temporarily flips it on so the operator can verify regardless.
        posted.assert_called_once()

    def test_restores_toggle_after_dispatch(self) -> None:
        cfg = _make_config(enabled=True, events={"work_unit_done": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.quota.post_webhook"),
        ):
            notifications.send_test_notification("work_unit_done")
        assert cfg.events.work_unit_done is False

    @pytest.mark.parametrize("event", notifications.ALL_EVENTS)
    def test_every_event_has_a_sample(self, event: str) -> None:
        cfg = _make_config(enabled=True)
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.quota.post_webhook") as posted,
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
        assert rt.notifications.slack.webhook_url is None
        assert rt.notifications.slack.user_id is None
        assert rt.notifications.events.work_unit_done is False

    def test_full_block_parses_every_field(self) -> None:
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              enabled: true
              slack:
                webhook_url: "https://hooks.slack.com/services/T/B/X"
                user_id: "U12345678"
              webhook_url: "https://example.com/hook"
              timeout_seconds: 15
              events:
                work_unit_done: true
                quota_pause: true
            """
        )
        n = rt.notifications
        assert n.enabled is True
        assert n.slack.webhook_url == "https://hooks.slack.com/services/T/B/X"
        assert n.slack.user_id == "U12345678"
        assert n.webhook_url == "https://example.com/hook"
        assert n.timeout_seconds == 15.0
        assert n.events.work_unit_done is True
        assert n.events.quota_pause is True
        assert n.events.work_unit_blocked_operator is False  # default

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

    def test_malformed_user_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="malformed Slack user id"):
            self._load(
                """\
                repos:
                  org/repo:
                    default_branch: main
                notifications:
                  enabled: true
                  slack:
                    user_id: "not-a-slack-id"
                """
            )

    def test_workspace_grade_w_prefix_user_id_accepted(self) -> None:
        rt = self._load(
            """\
            repos:
              org/repo:
                default_branch: main
            notifications:
              slack:
                user_id: "W12345678"
            """
        )
        assert rt.notifications.slack.user_id == "W12345678"
