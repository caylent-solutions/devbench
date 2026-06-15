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

from devbench.constants import (
    DEFAULT_LOG_FILENAME,
    SESSION_DRAIN_SIGNAL_FILENAME,
    SESSION_SESSIONS_BASE_DIR,
)
from devbench.reporting.report import _orchestrator_liveness_banner, _session_banner_lines
from devbench.session import Session, SessionRegistry

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


# ---------------------------------------------------------------------------
# Session-aware banner tests (report banner not session-aware in multi-session)
# ---------------------------------------------------------------------------


def _make_session(workspace_root: Path, name: str, pid: int, scope: list[str]) -> Session:
    """Build a :class:`Session` whose ``state_dir`` lives under *workspace_root*.

    The state dir is created so per-session log / drain files can be written.
    """
    state_dir = workspace_root / SESSION_SESSIONS_BASE_DIR / name
    state_dir.mkdir(parents=True, exist_ok=True)
    return Session(
        name=name,
        pid=pid,
        scope=scope,
        started_at=datetime(2026, 3, 5, 8, 0, 0, tzinfo=UTC),
        started_by="tester",
        state_dir=state_dir,
    )


def _write_session_log(session: Session, last_ts_iso: str) -> None:
    """Write a parseable per-session orchestrator log for *session*."""
    log = session.state_dir / DEFAULT_LOG_FILENAME
    log.write_text(
        f"2026-03-05T08:00:00Z [devbench.orch] INFO Started\n{last_ts_iso} [devbench.orch] INFO Tick\n",
        encoding="utf-8",
    )


def _write_session_drain(session: Session) -> None:
    """Write a per-session drain signal file so the session reads as draining."""
    drain = session.state_dir / SESSION_DRAIN_SIGNAL_FILENAME
    drain.write_text(
        json.dumps(
            {
                "requested_at": "2026-03-05T09:00:00Z",
                "requested_by": "tester",
                "reason": "test drain",
            }
        ),
        encoding="utf-8",
    )


def _save_registry(workspace_root: Path, sessions: list[Session]) -> None:
    """Persist *sessions* to ``<workspace_root>/.devbench/sessions/registry.json``."""
    SessionRegistry(workspace_root).save(sessions)


@pytest.mark.unit
class TestSessionAwareBanner:
    """``_session_banner_lines`` must render one banner line per registered session.

    Each line reflects THAT session's own PID liveness, its own per-session
    log recency, and its own drain state -- never a single global STOPPED line
    while another session daemon is alive.  When the registry is empty/absent,
    the function returns ``None`` so the caller falls back to the classic
    single-line banner.
    """

    def test_zero_sessions_returns_none(self, tmp_path: Path) -> None:
        """No registry file -> None (caller uses the single-line fallback)."""
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=False):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is None

    def test_empty_registry_returns_none(self, tmp_path: Path) -> None:
        """An empty registry array -> None (single-line fallback)."""
        _save_registry(tmp_path, [])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=False):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is None

    def test_single_session_alive(self, tmp_path: Path) -> None:
        """One ALIVE session -> one ``[SESSION <name> ALIVE]`` line; no global STOPPED."""
        sess = _make_session(tmp_path, "vpc", 4242, ["E1-F1-S1-T1"])
        _write_session_log(sess, "2026-03-05T09:59:56Z")  # 4s before now
        _save_registry(tmp_path, [sess])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        assert len(result) == 1
        assert "[SESSION vpc ALIVE]" in result[0]
        assert "4s ago" in result[0]
        assert "[ORCHESTRATOR STOPPED]" not in result[0]

    def test_single_session_stopped(self, tmp_path: Path) -> None:
        """One dead-PID session -> ``[SESSION <name> STOPPED]`` even with a fresh log."""
        sess = _make_session(tmp_path, "p2", 4243, ["E1-F1-S1-T2"])
        _write_session_log(sess, "2026-03-05T09:59:54Z")  # fresh log, but PID dead
        _save_registry(tmp_path, [sess])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=False),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        assert len(result) == 1
        assert "[SESSION p2 STOPPED]" in result[0]
        assert "last seen" in result[0]

    def test_single_session_starting(self, tmp_path: Path) -> None:
        """Live PID but empty per-session log -> ``[SESSION <name> STARTING]``."""
        sess = _make_session(tmp_path, "boot", 4244, [])
        (sess.state_dir / DEFAULT_LOG_FILENAME).write_text("", encoding="utf-8")
        _save_registry(tmp_path, [sess])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        assert len(result) == 1
        assert "[SESSION boot STARTING]" in result[0]

    def test_n_sessions_mixed_liveness(self, tmp_path: Path) -> None:
        """N sessions -> N lines, each reflecting its own liveness; no global STOPPED."""
        alive_a = _make_session(tmp_path, "vpc", 5001, ["E1-F1-S1-T1"])
        alive_b = _make_session(tmp_path, "p1", 5002, ["E1-F1-S1-T2"])
        dead = _make_session(tmp_path, "p2", 5003, ["E1-F1-S1-T3"])
        _write_session_log(alive_a, "2026-03-05T09:59:56Z")  # 4s ago
        _write_session_log(alive_b, "2026-03-05T09:59:53Z")  # 7s ago
        _write_session_log(dead, "2026-03-05T09:54:00Z")  # 6m ago
        _save_registry(tmp_path, [alive_a, alive_b, dead])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)

        liveness = {5001: True, 5002: True, 5003: False}

        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch(
                "devbench.session.SessionRegistry.is_alive",
                side_effect=lambda pid: liveness[pid],
            ),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)

        assert result is not None
        assert len(result) == 3
        joined = "\n".join(result)
        assert "[SESSION vpc ALIVE]" in joined
        assert "4s ago" in joined
        assert "[SESSION p1 ALIVE]" in joined
        assert "7s ago" in joined
        assert "[SESSION p2 STOPPED]" in joined
        # Critical: no single global STOPPED line while two daemons are alive.
        assert "[ORCHESTRATOR STOPPED]" not in joined

    def test_draining_session_shown_as_draining(self, tmp_path: Path) -> None:
        """A live + drain-pending session -> ``[SESSION <name> DRAINING]`` + drain marker."""
        sess = _make_session(tmp_path, "serial", 6001, ["E1-F1-S1-T1"])
        _write_session_log(sess, "2026-03-05T09:59:57Z")  # 3s ago
        _write_session_drain(sess)
        _save_registry(tmp_path, [sess])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        assert len(result) == 1
        assert "[SESSION serial DRAINING]" in result[0]
        assert "drain=pending" in result[0]
        assert "3s ago" in result[0]
        # A draining session is NOT reported as plain ALIVE.
        assert "[SESSION serial ALIVE]" not in result[0]

    def test_draining_overrides_alive_only_for_that_session(self, tmp_path: Path) -> None:
        """In a mix, only the draining session shows DRAINING; the other shows ALIVE."""
        plain = _make_session(tmp_path, "plain", 7001, ["E1-F1-S1-T1"])
        draining = _make_session(tmp_path, "drn", 7002, ["E1-F1-S1-T2"])
        _write_session_log(plain, "2026-03-05T09:59:58Z")
        _write_session_log(draining, "2026-03-05T09:59:58Z")
        _write_session_drain(draining)
        _save_registry(tmp_path, [plain, draining])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        joined = "\n".join(result)
        assert "[SESSION plain ALIVE]" in joined
        assert "[SESSION drn DRAINING]" in joined
        assert "drain=pending" in joined

    def test_stopped_session_with_no_log(self, tmp_path: Path) -> None:
        """Dead PID + no per-session log -> STOPPED 'no activity recorded' (uses threshold)."""
        sess = _make_session(tmp_path, "ghost", 4250, [])
        # No log file written at all.
        _save_registry(tmp_path, [sess])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=False),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        assert len(result) == 1
        assert "[SESSION ghost STOPPED]" in result[0]
        assert "no activity recorded" in result[0]
        assert "quiet for at least" in result[0]

    def test_starting_session_with_drain_pending(self, tmp_path: Path) -> None:
        """Live PID + empty log + drain signal -> STARTING with a drain=pending marker."""
        sess = _make_session(tmp_path, "warmup", 4251, [])
        (sess.state_dir / DEFAULT_LOG_FILENAME).write_text("", encoding="utf-8")
        _write_session_drain(sess)
        _save_registry(tmp_path, [sess])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with (
            patch("devbench.reporting.report._should_use_color", return_value=False),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        assert len(result) == 1
        assert "[SESSION warmup STARTING]" in result[0]
        assert "drain=pending" in result[0]

    def test_color_emitted_per_session_when_enabled(self, tmp_path: Path) -> None:
        """Each line is independently ANSI-coloured when colour is enabled."""
        alive = _make_session(tmp_path, "a", 8001, [])
        dead = _make_session(tmp_path, "b", 8002, [])
        _write_session_log(alive, "2026-03-05T09:59:58Z")
        _write_session_log(dead, "2026-03-05T09:59:58Z")
        _save_registry(tmp_path, [alive, dead])
        now = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        liveness = {8001: True, 8002: False}
        with (
            patch("devbench.reporting.report._should_use_color", return_value=True),
            patch(
                "devbench.session.SessionRegistry.is_alive",
                side_effect=lambda pid: liveness[pid],
            ),
        ):
            result = _session_banner_lines(tmp_path, 180, now=now)
        assert result is not None
        # Alive -> green; stopped -> red; both terminated with reset.
        assert result[0].startswith("\033[32m")
        assert result[1].startswith("\033[91m")
        assert all(line.endswith("\033[0m") for line in result)
