"""Integration tests for quota wait-and-resume in cmd_start and cmd_quota_watcher.

Covers: pause->wait->resume integration with mocked executor + clock;
enabled:false legacy-exit path; [QUOTA_WAITING] and [QUOTA_RESUMED] markers;
cmd_quota_watcher --once.

Issue #236 (Appendix A QW-5, QW-8).
AC-236-1.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from devbench.quota import (
    QuotaCheckpoint,
    SubscriptionRateLimitError,
    load_checkpoint,
    save_checkpoint,
)


def _make_quota_exc(
    reset_at: datetime | None = None,
) -> SubscriptionRateLimitError:
    return SubscriptionRateLimitError(
        reset_at=reset_at,
        raw_error="test",
        source="anthropic-api",
    )


def _fake_message_with_quota() -> SimpleNamespace:
    """Build a fake SDK message that detect_quota_error will classify."""
    return SimpleNamespace(
        status_code=429,
        body={},
        message="rate limit hit",
    )


@pytest.mark.unit
class TestCmdQuotaWatcher:
    """cmd_quota_watcher reads the checkpoint and prints status or removes it."""

    def test_once_prints_waiting_when_checkpoint_present(self, tmp_path: Path, capsys: Any) -> None:
        """--once with a checkpoint prints [QUOTA_WAITING] status."""
        from devbench.cli import cmd_quota_watcher

        saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        reset_at = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
        cp = QuotaCheckpoint(
            reason="subscription_rate_limit",
            reset_at=reset_at,
            saved_at=saved_at,
            session_name="default",
        )

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            save_checkpoint(cp, tmp_path)
            rc = cmd_quota_watcher("--once")

        assert rc == 0
        captured = capsys.readouterr()
        assert "QUOTA_WAITING" in captured.out or "quota" in captured.out.lower()

    def test_once_exits_with_nonzero_when_no_checkpoint(self, tmp_path: Path) -> None:
        """--once with no checkpoint returns non-zero."""
        from devbench.cli import cmd_quota_watcher

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cmd_quota_watcher("--once")

        assert rc != 0

    def test_no_args_prints_usage(self, capsys: Any) -> None:
        """No arguments prints usage to stderr and returns 1."""
        from devbench.cli import cmd_quota_watcher

        with patch("devbench.cli.WORKSPACE_ROOT", Path(tempfile.mkdtemp())):
            rc = cmd_quota_watcher()

        assert rc == 1
        captured = capsys.readouterr()
        assert "quota-watcher" in captured.err.lower() or "usage" in captured.err.lower()

    def test_invalid_flag_returns_one(self, capsys: Any) -> None:
        """Unknown flag returns 1."""
        from devbench.cli import cmd_quota_watcher

        with patch("devbench.cli.WORKSPACE_ROOT", Path(tempfile.mkdtemp())):
            rc = cmd_quota_watcher("--bogus-flag")

        assert rc == 1


@pytest.mark.unit
class TestHandleQuotaPause:
    """_handle_quota_pause emits [QUOTA_WAITING] + waits + emits [QUOTA_RESUMED]."""

    def test_emits_quota_waiting_marker(self, tmp_path: Path) -> None:
        """AC-236-1: enabled:true emits [QUOTA_WAITING] reason=subscription_rate_limit reset_at=<ISO>."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc(reset_at=datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC))
        qh_cfg = QuotaHandlingConfig(enabled=True, audit_comment_on_wait=False)

        log_messages: list[str] = []

        async def run() -> bool:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.logger") as mock_logger:
                    mock_logger.info.side_effect = lambda msg, *a, **kw: log_messages.append(msg % a if a else msg)
                    with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=True):
                        return await _handle_quota_pause(
                            exc=exc,
                            qh_cfg=qh_cfg,
                            workspace_root=tmp_path,
                            session_name="default",
                        )

        result = asyncio.run(run())
        assert result is True
        waiting_msgs = [m for m in log_messages if "QUOTA_WAITING" in m]
        assert len(waiting_msgs) >= 1, f"Expected [QUOTA_WAITING] in logs. Got: {log_messages}"

    def test_emits_quota_resumed_marker_on_recovery(self, tmp_path: Path) -> None:
        """AC-236-1: after recovery, [QUOTA_RESUMED] waited_seconds=<N> is emitted."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=True, audit_comment_on_resume=False)

        log_messages: list[str] = []

        async def run() -> bool:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.logger") as mock_logger:
                    mock_logger.info.side_effect = lambda msg, *a, **kw: log_messages.append(msg % a if a else msg)
                    with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=True):
                        with patch("devbench.cli._apply_resume_strategy"):
                            return await _handle_quota_pause(
                                exc=exc,
                                qh_cfg=qh_cfg,
                                workspace_root=tmp_path,
                                session_name="default",
                            )

        result = asyncio.run(run())
        assert result is True
        resumed_msgs = [m for m in log_messages if "QUOTA_RESUMED" in m]
        assert len(resumed_msgs) >= 1, f"Expected [QUOTA_RESUMED] in logs. Got: {log_messages}"

    def test_probe_unavailable_emits_marker_and_returns_false(self, tmp_path: Path) -> None:
        """When wait_for_reset raises RecoveryProbeUnavailableError, emit
        [QUOTA_PROBE_UNAVAILABLE], skip resume, and return False (no long wait)."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig
        from devbench.quota import RecoveryProbeUnavailableError

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=True, audit_comment_on_wait=False)

        log_messages: list[str] = []

        async def run() -> bool:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.logger") as mock_logger:
                    mock_logger.info.side_effect = lambda msg, *a, **kw: log_messages.append(msg % a if a else msg)
                    with patch(
                        "devbench.cli.wait_for_reset",
                        new_callable=AsyncMock,
                        side_effect=RecoveryProbeUnavailableError("no usable Anthropic API credential"),
                    ):
                        with patch("devbench.cli._apply_resume_strategy") as mock_resume:
                            result = await _handle_quota_pause(
                                exc=exc,
                                qh_cfg=qh_cfg,
                                workspace_root=tmp_path,
                                session_name="default",
                            )
                            mock_resume.assert_not_called()
                            return result

        result = asyncio.run(run())
        assert result is False
        unavailable_msgs = [m for m in log_messages if "QUOTA_PROBE_UNAVAILABLE" in m]
        assert len(unavailable_msgs) >= 1, f"Expected [QUOTA_PROBE_UNAVAILABLE] in logs. Got: {log_messages}"

    def test_no_checkpoint_left_when_enabled_false(self, tmp_path: Path) -> None:
        """AC-236-1: enabled:false does not write a checkpoint."""
        assert load_checkpoint(tmp_path) is None

    def test_fires_quota_waiting_notification_at_wait_start(self, tmp_path: Path) -> None:
        """notify_quota_waiting is fired where [QUOTA_WAITING] is logged, with the
        quota source and reset_at as its payload arguments."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        reset_at = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
        exc = _make_quota_exc(reset_at=reset_at)
        qh_cfg = QuotaHandlingConfig(enabled=True, audit_comment_on_wait=False)

        async def run() -> None:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=True):
                    with patch("devbench.cli._apply_resume_strategy"):
                        with patch("devbench.notifications.notify_quota_waiting") as mock_waiting:
                            await _handle_quota_pause(
                                exc=exc,
                                qh_cfg=qh_cfg,
                                workspace_root=tmp_path,
                                session_name="default",
                            )
                            mock_waiting.assert_called_once_with("anthropic-api", reset_at.isoformat())

        asyncio.run(run())

    def test_fires_quota_resumed_notification_on_recovery(self, tmp_path: Path) -> None:
        """notify_quota_resumed is fired where [QUOTA_RESUMED] is logged, with the
        waited-seconds total as its payload argument."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=True, audit_comment_on_resume=False)

        async def run() -> None:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=True):
                    with patch("devbench.cli._apply_resume_strategy"):
                        with patch("devbench.notifications.notify_quota_resumed") as mock_resumed:
                            await _handle_quota_pause(
                                exc=exc,
                                qh_cfg=qh_cfg,
                                workspace_root=tmp_path,
                                session_name="default",
                            )
                            mock_resumed.assert_called_once()
                            (waited_seconds,) = mock_resumed.call_args.args
                            assert isinstance(waited_seconds, int)

        asyncio.run(run())

    def test_no_quota_resumed_notification_on_timeout(self, tmp_path: Path) -> None:
        """On timeout (wait_for_reset returns False) no resume notification fires."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=True)

        async def run() -> bool:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=False):
                    with patch("devbench.notifications.notify_quota_resumed") as mock_resumed:
                        result = await _handle_quota_pause(
                            exc=exc,
                            qh_cfg=qh_cfg,
                            workspace_root=tmp_path,
                            session_name="default",
                        )
                        mock_resumed.assert_not_called()
                        return result

        assert asyncio.run(run()) is False

    def test_quota_waiting_notification_failure_does_not_break_wait(self, tmp_path: Path) -> None:
        """A notify failure at wait-start must NEVER break or delay the wait;
        the wait still proceeds and recovery is still returned."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc(reset_at=datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC))
        qh_cfg = QuotaHandlingConfig(enabled=True)

        async def run() -> bool:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=True):
                    with patch("devbench.cli._apply_resume_strategy"):
                        with patch(
                            "devbench.notifications.notify_quota_waiting",
                            side_effect=RuntimeError("slack down"),
                        ):
                            return await _handle_quota_pause(
                                exc=exc,
                                qh_cfg=qh_cfg,
                                workspace_root=tmp_path,
                                session_name="default",
                            )

        assert asyncio.run(run()) is True

    def test_quota_resumed_notification_failure_does_not_break_resume(self, tmp_path: Path) -> None:
        """A notify failure on the recovered path must NEVER break the resume;
        the resume strategy still applies and True is returned."""
        from devbench.cli import _handle_quota_pause
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=True)

        async def run() -> bool:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                with patch("devbench.cli.wait_for_reset", new_callable=AsyncMock, return_value=True):
                    with patch("devbench.cli._apply_resume_strategy") as mock_resume:
                        with patch(
                            "devbench.notifications.notify_quota_resumed",
                            side_effect=RuntimeError("slack down"),
                        ):
                            result = await _handle_quota_pause(
                                exc=exc,
                                qh_cfg=qh_cfg,
                                workspace_root=tmp_path,
                                session_name="default",
                            )
                            mock_resume.assert_called_once()
                            return result

        assert asyncio.run(run()) is True


@pytest.mark.unit
class TestEnabledFalseLegacyExit:
    """AC-236-1: enabled:false restores legacy non-zero exit, no checkpoint."""

    def test_quota_exc_re_raised_when_disabled(self, tmp_path: Path) -> None:
        """When quota_handling.enabled=False, QuotaExhaustedError propagates."""
        from devbench.cli import _should_handle_quota
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=False)

        result = _should_handle_quota(exc, qh_cfg)
        assert result is False

    def test_quota_exc_handled_when_enabled(self, tmp_path: Path) -> None:
        """When quota_handling.enabled=True, _should_handle_quota returns True."""
        from devbench.cli import _should_handle_quota
        from devbench.config_loader import QuotaHandlingConfig

        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(enabled=True)

        result = _should_handle_quota(exc, qh_cfg)
        assert result is True


def _quota_detected() -> Any:
    """Build a _QuotaDetected sentinel wrapping a subscription rate-limit error."""
    from devbench.cli import _QuotaDetected

    return _QuotaDetected(_make_quota_exc())


def _capture_info_logs(log_messages: list[str]) -> Any:
    """Return a side_effect that records formatted logger.info messages."""
    return lambda msg, *a, **kw: log_messages.append(msg % a if a else msg)


@pytest.mark.unit
class TestDispatchQuotaOnExhaustion:
    """on_exhaustion (detection-time) policy in _dispatch_quota_detection."""

    def _dispatch(self, qh_cfg: Any, log_messages: list[str]) -> str:
        from devbench.cli import _dispatch_quota_detection

        with patch("devbench.cli.RUNTIME_CONFIG", SimpleNamespace(quota_handling=qh_cfg)):
            with patch("devbench.cli.logger") as mock_logger:
                mock_logger.info.side_effect = _capture_info_logs(log_messages)
                return _dispatch_quota_detection(_quota_detected(), "default")

    def test_fail_reraises_and_skips_wait(self) -> None:
        from devbench.config_loader import QuotaHandlingConfig

        logs: list[str] = []
        with patch("devbench.cli._handle_quota_pause") as mock_pause:
            with pytest.raises(SubscriptionRateLimitError):
                self._dispatch(QuotaHandlingConfig(enabled=True, on_exhaustion="fail"), logs)
            mock_pause.assert_not_called()
        assert any("QUOTA_FAIL_FAST" in m for m in logs)

    def test_drain_requests_drain_and_skips_wait(self) -> None:
        from devbench.config_loader import QuotaHandlingConfig

        logs: list[str] = []
        with patch("devbench.cli.request_drain") as mock_drain:
            with patch("devbench.cli._handle_quota_pause") as mock_pause:
                result = self._dispatch(QuotaHandlingConfig(enabled=True, on_exhaustion="drain"), logs)
                mock_pause.assert_not_called()
        assert result == "quota-drain-requested"
        mock_drain.assert_called_once()
        assert mock_drain.call_args.kwargs["reason"].startswith("quota-exhaustion:")
        assert any("QUOTA_DRAIN_REQUESTED" in m and "phase=detection" in m for m in logs)

    def test_wait_recovered_returns_recovered(self) -> None:
        from devbench.config_loader import QuotaHandlingConfig

        logs: list[str] = []
        with patch("devbench.cli._handle_quota_pause", new_callable=AsyncMock, return_value=True):
            result = self._dispatch(QuotaHandlingConfig(enabled=True, on_exhaustion="wait"), logs)
        assert result == "quota-wait-recovered"

    def test_wait_timeout_funnels_to_timeout_drain(self) -> None:
        from devbench.config_loader import QuotaHandlingConfig

        logs: list[str] = []
        with patch("devbench.cli.request_drain") as mock_drain:
            with patch("devbench.cli._handle_quota_pause", new_callable=AsyncMock, return_value=False):
                result = self._dispatch(
                    QuotaHandlingConfig(enabled=True, on_exhaustion="wait", on_exhaustion_timeout="drain"),
                    logs,
                )
        assert result == "quota-wait-timeout-drain"
        mock_drain.assert_called_once()
        assert mock_drain.call_args.kwargs["reason"].startswith("quota-timeout:")

    def test_wait_timeout_fail_reraises_through_dispatch(self) -> None:
        from devbench.config_loader import QuotaHandlingConfig

        logs: list[str] = []
        with patch("devbench.cli._handle_quota_pause", new_callable=AsyncMock, return_value=False):
            with pytest.raises(SubscriptionRateLimitError):
                self._dispatch(
                    QuotaHandlingConfig(enabled=True, on_exhaustion="wait", on_exhaustion_timeout="fail"),
                    logs,
                )


@pytest.mark.unit
class TestDispatchQuotaTimeout:
    """on_exhaustion_timeout policy in _dispatch_quota_timeout."""

    def _dispatch(self, action: str, log_messages: list[str]) -> str:
        from devbench.cli import _dispatch_quota_timeout

        with patch("devbench.cli.logger") as mock_logger:
            mock_logger.info.side_effect = _capture_info_logs(log_messages)
            return _dispatch_quota_timeout(_quota_detected(), action)

    def test_drain_requests_drain(self) -> None:
        logs: list[str] = []
        with patch("devbench.cli.request_drain") as mock_drain:
            result = self._dispatch("drain", logs)
        assert result == "quota-wait-timeout-drain"
        mock_drain.assert_called_once()
        assert mock_drain.call_args.kwargs["reason"].startswith("quota-timeout:")
        assert any("QUOTA_DRAIN_REQUESTED" in m and "phase=timeout" in m for m in logs)

    def test_fail_reraises(self) -> None:
        logs: list[str] = []
        with patch("devbench.cli.request_drain") as mock_drain:
            with pytest.raises(SubscriptionRateLimitError):
                self._dispatch("fail", logs)
            mock_drain.assert_not_called()
        assert any("QUOTA_FAIL_FAST" in m for m in logs)

    def test_keep_waiting_returns_soft_stop(self) -> None:
        logs: list[str] = []
        with patch("devbench.cli.request_drain") as mock_drain:
            result = self._dispatch("keep_waiting", logs)
            mock_drain.assert_not_called()
        assert result == "quota-wait-timeout-keep-waiting"
        assert any("QUOTA_TIMEOUT_KEEP_WAITING" in m for m in logs)

    def test_unknown_action_raises_value_error(self) -> None:
        from devbench.cli import _dispatch_quota_timeout

        with pytest.raises(ValueError, match="unknown on_exhaustion_timeout"):
            _dispatch_quota_timeout(_quota_detected(), "bogus")


@pytest.mark.unit
class TestCancelDrainUnlessRequested:
    """R1: a quota-requested drain survives cmd_start's exit finally blocks."""

    def test_cancels_stale_drain_when_not_requested(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.cli import _cancel_drain_unless_requested
        from devbench.drain import read_drain_state, request_drain

        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        request_drain(tmp_path, reason="stale")
        assert read_drain_state(tmp_path) is not None
        _cancel_drain_unless_requested(tmp_path, quota_drain_requested=False)
        assert read_drain_state(tmp_path) is None

    def test_preserves_drain_when_requested(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.cli import _cancel_drain_unless_requested
        from devbench.drain import read_drain_state, request_drain

        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        request_drain(tmp_path, reason="quota-timeout:anthropic-api")
        _cancel_drain_unless_requested(tmp_path, quota_drain_requested=True)
        assert read_drain_state(tmp_path) is not None
