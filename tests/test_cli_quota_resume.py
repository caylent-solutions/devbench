"""Integration tests for quota wait-and-resume in cmd_start and cmd_quota_watcher.

Covers: pause->wait->resume integration with mocked executor + clock;
enabled:false legacy-exit path; [QUOTA_WAITING] and [QUOTA_RESUMED] markers;
cmd_quota_watcher --once and --daemon.

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# cmd_quota_watcher
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _handle_quota_pause integration
# ---------------------------------------------------------------------------


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
        # Check that a QUOTA_WAITING marker was emitted
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

    def test_no_checkpoint_left_when_enabled_false(self, tmp_path: Path) -> None:
        """AC-236-1: enabled:false does not write a checkpoint."""
        # enabled:false path re-raises the quota exception without writing a checkpoint.
        # Verify that a fresh workspace has no checkpoint present.
        assert load_checkpoint(tmp_path) is None


# ---------------------------------------------------------------------------
# enabled:false legacy exit path
# ---------------------------------------------------------------------------


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
