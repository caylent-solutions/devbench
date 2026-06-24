"""AC-33: read-only-by-default attach, with ``--screen`` gated until DI-4.

AC-33 has two halves and this single named file (the one VERIFY command the AC
maps to) asserts BOTH in the same run, so the AC is proven end-to-end here:

1. ``supervise attach --screen`` MUST fail fast (exit 2) with the documented
   message while DI-4 is unconfirmed (Section 3.6.5, FR-26).
2. ``supervise attach`` (no flags) IS the read-only PTY-log follow: it streams the
   redacted ``pty.log`` through the REAL production CLI body (the ``_block``
   closure that parks on the config-driven :func:`_block_until_readable`), never
   wires stdin to the child, and ``Ctrl-C`` ends the follow with exit 0 while the
   orchestration continues (Section 4.7, FR-26).

The exhaustive ``follow_pty_log`` unit matrix (offset/truncation/late-start/select
mechanics) lives in ``test_supervise_attach_follow.py``; here we assert the two
AC-33 behaviors against the real CLI dispatch so the named verification command is
self-contained.
"""

from __future__ import annotations

import os as _os
from pathlib import Path

import pytest

from devbench import cli
from devbench.constants import SUPERVISE_STATE_RUNNING
from devbench.supervise import (
    SuperviseRegistry,
    new_session_state,
    supervise_pty_log_path,
)


def _seed_running_session(tmp_path: Path, name: str, transcript: str) -> None:
    """Register a RUNNING supervise session with a redacted ``pty.log`` to follow."""
    registry = SuperviseRegistry(tmp_path)
    state = new_session_state(
        name=name,
        pid=_os.getpid(),
        screen_name=f"devbench-supervise-{name}",
        model="claude-opus-4-8",
        effort="xhigh",
        started_by="ac33",
    )
    state.state = SUPERVISE_STATE_RUNNING
    registry.write_state(state)
    log = supervise_pty_log_path(tmp_path, name)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(transcript, encoding="utf-8")


@pytest.mark.unit
class TestAttachScreenGated:
    """AC-33 half 1: --screen fails fast (exit 2) with the documented message."""

    def test_screen_flag_fails_fast(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_supervise("attach", "--name", "nightly", "--screen")
        assert rc == 2
        err = capsys.readouterr().err
        assert "--screen attach is not enabled" in err

    def test_screen_flag_fails_fast_even_for_a_live_session(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        _seed_running_session(tmp_path, "nightly", "live transcript line\n")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("attach", "--name", "nightly", "--screen")
        assert rc == 2
        assert "--screen attach is not enabled" in capsys.readouterr().err


@pytest.mark.unit
class TestAttachReadOnlyFollow:
    """AC-33 half 2: ``supervise attach`` (no flags) is the read-only PTY-log follow.

    These assertions run the REAL production CLI body end-to-end (no test double
    for the follow loop) so the single AC-33 verification command proves the
    read-only-follow behavior, not just the ``--screen`` gate.
    """

    def test_no_flags_follows_pty_log_read_only_via_real_cli_body(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        _seed_running_session(tmp_path, "nightly", "live transcript line\n")
        parked: dict[str, float] = {}

        def _fake_block(*, poll_interval_seconds: float) -> None:
            parked["interval"] = poll_interval_seconds
            raise KeyboardInterrupt

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._block_until_readable", _fake_block),
        ):
            rc = cli.cmd_supervise("attach", "--name", "nightly")
        assert rc == 0
        out = capsys.readouterr().out
        assert "live transcript line" in out
        assert "read-only" in out
        assert "owns stdin" in out
        assert "stopped watching" in out
        expected_interval = cli._supervise_runtime_config().timeouts.poll_interval_seconds
        assert parked["interval"] == float(expected_interval)

    def test_no_flags_returns_0_when_follow_is_interrupted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        _seed_running_session(tmp_path, "nightly", "live transcript line\n")

        def _fake_follow(path, *, write, should_continue, block, wait_for_log=False):
            write(path.read_text(encoding="utf-8"))
            raise KeyboardInterrupt

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.follow_pty_log", _fake_follow),
        ):
            rc = cli.cmd_supervise("attach", "--name", "nightly")
        assert rc == 0
        out = capsys.readouterr().out
        assert "live transcript line" in out
        assert "read-only" in out

    def test_unknown_name_fails_fast(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("attach", "--name", "ghost")
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err

    def test_running_session_without_pty_log_fails_fast(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        registry = SuperviseRegistry(tmp_path)
        state = new_session_state(
            name="fresh",
            pid=_os.getpid(),
            screen_name="devbench-supervise-fresh",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="ac33",
        )
        state.state = SUPERVISE_STATE_RUNNING
        registry.write_state(state)
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("attach", "--name", "fresh")
        assert rc == 2
        assert "no PTY transcript" in capsys.readouterr().err
