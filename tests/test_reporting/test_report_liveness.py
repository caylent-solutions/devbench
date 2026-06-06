"""PID-authoritative liveness banner tests (issue #250).

Tests for ``_orchestrator_liveness_banner`` after the decision was rewritten
to use live-PID state rather than log-recency:

- ALIVE only when a live PID is present and the log has a parseable timestamp.
- STOPPED when no live PID exists, regardless of log recency.
- STARTING when a live PID exists but no parseable log timestamp is found.
- Never ALIVE for an untimestamped traceback tail (non-empty file, no parseable
  timestamp).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.reporting.report import _orchestrator_liveness_banner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pid(pid_path: Path, pid: int) -> None:
    """Write a minimal PID file so ``read_pid_file`` succeeds."""
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(
        json.dumps(
            {
                "instance_id": f"test-{pid}",
                "pid": pid,
                "workspace": str(pid_path.parent.parent),
                "workspace_name": "test-ws",
                "session": "default",
                "mode": "daemon",
                "started_at": "2026-03-05T09:00:00Z",
                "model": "",
                "host": "localhost",
            }
        ),
        encoding="utf-8",
    )


def _write_log_with_ts(log_path: Path, last_ts_iso: str) -> None:
    log_path.write_text(
        f"2026-03-05T09:00:00Z [devbench.orch] INFO Started\n{last_ts_iso} [devbench.orch] INFO Tick\n",
        encoding="utf-8",
    )


def _write_traceback_tail(log_path: Path) -> None:
    """Write a log file whose tail has no parseable timestamp (traceback only).

    The file must not contain any parseable log line in the tail window
    (``_LIVENESS_TAIL_BYTES`` = 4096 bytes) so that ``_read_last_log_timestamp``
    returns ``None``.  We write purely unstructured traceback lines with no
    leading timestamp.
    """
    log_path.write_text(
        "Traceback (most recent call last):\n"
        "  File '/app/cli.py', line 42, in run\n"
        "    raise RuntimeError('boom')\n"
        "RuntimeError: boom\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# PID-authoritative decision tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBannerPidAuthoritative:
    """Banner must derive state from live-PID presence, not log recency."""

    def test_alive_with_live_pid_and_stale_log(self, tmp_path: Path) -> None:
        """A live PID overrides a stale log -- banner must report ALIVE."""
        log = tmp_path / "orch.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)
        # Log is 10 minutes old -- beyond any reasonable threshold.
        _write_log_with_ts(log, "2026-03-05T09:50:00Z")
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=True),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file, now=now)

        assert "[ORCHESTRATOR ALIVE]" in banner
        assert "[ORCHESTRATOR STOPPED]" not in banner

    def test_stopped_with_no_live_pid_and_fresh_log(self, tmp_path: Path) -> None:
        """No live PID overrides a fresh log -- banner must report STOPPED."""
        log = tmp_path / "orch.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)
        # Log is only 10 seconds old -- within any reasonable threshold.
        _write_log_with_ts(log, "2026-03-05T09:59:50Z")
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=False),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file, now=now)

        assert "[ORCHESTRATOR STOPPED]" in banner
        assert "[ORCHESTRATOR ALIVE]" not in banner

    def test_stopped_with_no_pid_file_and_fresh_log(self, tmp_path: Path) -> None:
        """Missing PID file means no live PID -- banner must report STOPPED."""
        log = tmp_path / "orch.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        # No PID file written.
        _write_log_with_ts(log, "2026-03-05T09:59:50Z")
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)

        with patch("devbench.reporting.report._should_use_color", return_value=False):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file, now=now)

        assert "[ORCHESTRATOR STOPPED]" in banner
        assert "[ORCHESTRATOR ALIVE]" not in banner

    def test_starting_with_live_pid_and_no_parseable_log(self, tmp_path: Path) -> None:
        """Live PID but log file missing -> STARTING."""
        log = tmp_path / "no-such.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=True),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file)

        assert "[ORCHESTRATOR STARTING]" in banner
        assert "[ORCHESTRATOR ALIVE]" not in banner
        assert "[ORCHESTRATOR STOPPED]" not in banner

    def test_starting_with_live_pid_and_empty_log(self, tmp_path: Path) -> None:
        """Live PID but log file empty -> STARTING."""
        log = tmp_path / "orch.log"
        log.write_text("", encoding="utf-8")
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=True),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file)

        assert "[ORCHESTRATOR STARTING]" in banner
        assert "[ORCHESTRATOR ALIVE]" not in banner

    def test_never_alive_for_traceback_tail(self, tmp_path: Path) -> None:
        """Non-empty log with no parseable timestamp is never ALIVE -- not even with a live PID."""
        log = tmp_path / "orch.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)
        _write_traceback_tail(log)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=True),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file)

        assert "[ORCHESTRATOR ALIVE]" not in banner
        assert "[ORCHESTRATOR STARTING]" in banner

    def test_traceback_tail_with_no_live_pid_is_stopped(self, tmp_path: Path) -> None:
        """Traceback tail + no live PID -> STOPPED."""
        log = tmp_path / "orch.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)
        _write_traceback_tail(log)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=False),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file)

        assert "[ORCHESTRATOR STOPPED]" in banner
        assert "[ORCHESTRATOR ALIVE]" not in banner

    @pytest.mark.parametrize(
        "has_live_pid,has_parseable_log,expected_tag",
        [
            (True, True, "[ORCHESTRATOR ALIVE]"),
            (True, False, "[ORCHESTRATOR STARTING]"),
            (False, True, "[ORCHESTRATOR STOPPED]"),
            (False, False, "[ORCHESTRATOR STOPPED]"),
        ],
    )
    def test_state_matrix(
        self,
        tmp_path: Path,
        has_live_pid: bool,
        has_parseable_log: bool,
        expected_tag: str,
    ) -> None:
        """All four (PID x log) combinations yield the expected tag."""
        log = tmp_path / "orch.log"
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        _write_pid(pid_file, 99999)

        if has_parseable_log:
            _write_log_with_ts(log, "2026-03-05T09:00:00Z")
        else:
            log.write_text("", encoding="utf-8")

        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.reporting.report.is_pid_alive", return_value=has_live_pid),
        ):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file=pid_file, now=now)

        assert expected_tag in banner
