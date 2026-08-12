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
# Quota wait-and-resume Slack events (E2-F3-S1-T1, spec FR-2.10, ADR-24).
#
# Ported from the pre-strip commit 58048b3 (D-16: quote pre-strip commits so
# the design rationale survives the port) plus the quota-exhausted stop-class
# member added later on the branch.  ``quota_waiting`` / ``quota_resumed`` are
# opt-in event toggles, same as every other event in this module; the stop-
# class taxonomy and mention-map are new declarative infrastructure this task
# introduces so a future task can extend it with the remaining orchestrator
# stop classes without renaming anything here.
# ---------------------------------------------------------------------------


class TestQuotaEventConstants:
    """AC-E2-F3-S1-T1-2: event constants + ALL_EVENTS registration."""

    def test_event_quota_waiting_value(self) -> None:
        assert notifications.EVENT_QUOTA_WAITING == "quota_waiting"

    def test_event_quota_resumed_value(self) -> None:
        assert notifications.EVENT_QUOTA_RESUMED == "quota_resumed"

    def test_quota_waiting_registered_in_all_events(self) -> None:
        assert notifications.EVENT_QUOTA_WAITING in notifications.ALL_EVENTS

    def test_quota_resumed_registered_in_all_events(self) -> None:
        assert notifications.EVENT_QUOTA_RESUMED in notifications.ALL_EVENTS


class TestStopClassTaxonomy:
    """AC-E2-F3-S1-T1-3: quota-exhausted stop-class + here-mention default."""

    def test_stop_class_quota_exhausted_value(self) -> None:
        assert notifications.STOP_CLASS_QUOTA_EXHAUSTED == "quota-exhausted"

    def test_stop_class_quota_exhausted_is_member_of_all_stop_classes(self) -> None:
        assert notifications.STOP_CLASS_QUOTA_EXHAUSTED in notifications.ALL_STOP_CLASSES

    def test_stop_class_crash_is_member_of_all_stop_classes(self) -> None:
        assert notifications.STOP_CLASS_CRASH in notifications.ALL_STOP_CLASSES

    def test_quota_exhausted_maps_to_here_mention_in_default_map(self) -> None:
        mapping = notifications.DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP
        assert mapping[notifications.STOP_CLASS_QUOTA_EXHAUSTED] == notifications.MENTION_LEVEL_HERE


class TestValidateStopMentionMap:
    """Task-specific error path: unknown stop-class key raises ValueError naming the allowed set."""

    def test_accepts_known_stop_class_keys(self) -> None:
        # Must not raise.
        notifications.validate_stop_mention_map(
            {
                notifications.STOP_CLASS_CRASH: notifications.MENTION_LEVEL_HERE,
                notifications.STOP_CLASS_QUOTA_EXHAUSTED: notifications.MENTION_LEVEL_HERE,
            }
        )

    def test_accepts_empty_map(self) -> None:
        notifications.validate_stop_mention_map({})

    def test_rejects_unknown_stop_class_key(self) -> None:
        with pytest.raises(ValueError, match="unknown stop-class key"):
            notifications.validate_stop_mention_map({"not-a-real-class": notifications.MENTION_LEVEL_HERE})

    def test_rejection_names_allowed_set(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            notifications.validate_stop_mention_map({"bogus": "here"})
        message = str(exc_info.value)
        assert notifications.STOP_CLASS_CRASH in message
        assert notifications.STOP_CLASS_QUOTA_EXHAUSTED in message


class TestClassifyStopClass:
    """AC-E2-F3-S1-T1-4: quota-prefixed reasons classify quota-exhausted; everything
    else falls through to the fail-visible crash bucket."""

    @pytest.mark.parametrize(
        "reason",
        [
            "quota-exhausted",
            "quota exceeded: anthropic-api",
            "quota_exhausted_bedrock_after_max_resumes",
            "quota",
        ],
    )
    def test_quota_prefixed_reasons_classify_quota_exhausted(self, reason: str) -> None:
        assert notifications.classify_stop_class(reason) == notifications.STOP_CLASS_QUOTA_EXHAUSTED

    @pytest.mark.parametrize(
        "reason",
        [
            "clean exit: ALL_DONE",
            "crash: unexpected SIGKILL",
            "interrupted by operator",
            "",
            "totally-unrecognised-reason",
        ],
    )
    def test_unrecognized_reasons_fall_through_to_crash(self, reason: str) -> None:
        """Fail-visible fallback: an unrecognised reason must never be silently
        dropped -- it always resolves to a concrete, alertable stop-class."""
        assert notifications.classify_stop_class(reason) == notifications.STOP_CLASS_CRASH


class TestNotifyQuotaWaiting:
    """notify_quota_waiting(reason, reset_at) -- payload shape + gating."""

    def test_no_post_when_event_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_waiting": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_waiting("anthropic-api", "2026-01-01T16:10:00+00:00")
        posted.assert_not_called()

    def test_slack_post_fires_when_toggle_on(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_waiting": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_waiting("anthropic-api", "2026-01-01T16:10:00+00:00")
        posted.assert_called_once()
        args, _ = posted.call_args
        _url, payload, _timeout = args
        assert "Quota hit" in payload["text"]
        field_blob = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "anthropic-api" in field_blob
        assert "2026-01-01T16:10:00+00:00" in field_blob

    def test_never_breaks_or_delays_the_wait_on_dispatch_failure(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec AC-27: a raising ``_dispatch`` must not propagate, retry, or sleep."""

        def sleep_forbidden(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("notify_quota_waiting must never sleep or retry on dispatch failure")

        monkeypatch.setattr("time.sleep", sleep_forbidden)
        with patch.object(notifications, "_dispatch", side_effect=RuntimeError("dispatch exploded")) as mock_dispatch:
            # Must not raise.
            notifications.notify_quota_waiting("anthropic-api", "2026-01-01T16:10:00+00:00")
        assert mock_dispatch.call_count == 1
        err = capsys.readouterr().err
        assert "[WARN]" in err
        assert "quota_waiting" in err


class TestNotifyQuotaResumed:
    """notify_quota_resumed(waited_seconds) -- payload shape + gating."""

    def test_no_post_when_event_toggle_off(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_resumed": False})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_resumed(1234)
        posted.assert_not_called()

    def test_slack_post_fires_when_toggle_on(self) -> None:
        cfg = _make_config(enabled=True, events={"quota_resumed": True})
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.notify_quota_resumed(1234)
        posted.assert_called_once()
        args, _ = posted.call_args
        _url, payload, _timeout = args
        assert "Quota recovered" in payload["text"]
        field_blob = " ".join(f.get("text", "") for block in payload["blocks"] for f in block.get("fields", []))
        assert "1234" in field_blob

    def test_never_breaks_or_delays_the_wait_on_dispatch_failure(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec AC-27: a raising ``_dispatch`` must not propagate, retry, or sleep."""

        def sleep_forbidden(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("notify_quota_resumed must never sleep or retry on dispatch failure")

        monkeypatch.setattr("time.sleep", sleep_forbidden)
        with patch.object(notifications, "_dispatch", side_effect=RuntimeError("dispatch exploded")) as mock_dispatch:
            # Must not raise.
            notifications.notify_quota_resumed(1234)
        assert mock_dispatch.call_count == 1
        err = capsys.readouterr().err
        assert "[WARN]" in err
        assert "quota_resumed" in err


class TestQuotaEventsHaveSamples:
    """``_fire_sample`` must grow a branch for both new events so the existing
    ``test_every_event_has_a_sample`` parametrization (which iterates
    ``ALL_EVENTS``) keeps passing once the quota events are registered."""

    def test_quota_waiting_sample_dispatches(self) -> None:
        cfg = _make_config(enabled=True)
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.send_test_notification("quota_waiting")
        posted.assert_called_once()

    def test_quota_resumed_sample_dispatches(self) -> None:
        cfg = _make_config(enabled=True)
        with (
            patch.object(notifications, "_load_notifications_config", return_value=cfg),
            patch("devbench.notifications.post_webhook") as posted,
        ):
            notifications.send_test_notification("quota_resumed")
        posted.assert_called_once()
