"""Read-only PTY-log follow for ``supervise attach`` (FR-26, Section 4.7).

The default ``supervise attach`` is a follow of the redacted ``pty.log`` -- a pure
read of a file the ``__run`` supervisor writes. The attaching process's stdin is
NEVER connected to the ``claude`` TTY, so an observer cannot inject input or steal
the PTY. ``--screen`` stays fail-fast-disabled (AC-33, covered separately).

``follow_pty_log`` is event-driven (it re-reads on a readiness predicate, never
``time.sleep``) and bounded by an injectable ``should_continue`` predicate so the
test drives it to completion deterministically.
"""

from __future__ import annotations

import os as _os
from pathlib import Path

import pytest

from devbench import cli
from devbench.constants import SUPERVISE_STATE_RUNNING
from devbench.supervise import (
    SuperviseRegistry,
    follow_pty_log,
    new_session_state,
    supervise_pty_log_path,
)


@pytest.mark.unit
class TestFollowPtyLog:
    """follow_pty_log streams only NEW bytes and never reads stdin (FR-26).

    The follow is event-driven: between reads the loop BLOCKS on an injected
    readiness gate (``block``) rather than spinning. The tests inject a no-op /
    counting ``block`` so the loop is bounded and deterministic; the production
    blocking wait is exercised separately (:class:`TestFollowBlockUntilReadable`).
    """

    def test_streams_existing_then_appended(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        log.write_text("first chunk\n", encoding="utf-8")
        written: list[str] = []
        # should_continue returns True for the first two reads then False so the
        # loop is bounded; the block step appends the next chunk before the read.
        calls = {"n": 0}

        def _should_continue() -> bool:
            calls["n"] += 1
            return calls["n"] <= 2

        def _block() -> None:
            log.write_text("first chunk\nsecond chunk\n", encoding="utf-8")

        follow_pty_log(log, write=written.append, should_continue=_should_continue, block=_block)
        joined = "".join(written)
        assert "first chunk" in joined
        assert "second chunk" in joined

    def test_blocks_between_reads_no_busy_spin(self, tmp_path: Path) -> None:
        # The loop MUST block (kernel-parked) once per iteration rather than
        # re-reading in a tight spin: assert ``block`` was called exactly as many
        # times as ``should_continue`` returned True.
        log = tmp_path / "pty.log"
        log.write_text("x\n", encoding="utf-8")
        blocks = {"n": 0}
        conts = {"n": 0}

        def _should_continue() -> bool:
            conts["n"] += 1
            return conts["n"] <= 3

        def _block() -> None:
            blocks["n"] += 1

        follow_pty_log(log, write=lambda _s: None, should_continue=_should_continue, block=_block)
        # Three iterations ran the loop body -> block was consulted on each.
        assert blocks["n"] == 3

    def test_no_duplicate_emission_of_already_followed_bytes(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        log.write_text("alpha\n", encoding="utf-8")
        written: list[str] = []
        calls = {"n": 0}

        def _should_continue() -> bool:
            calls["n"] += 1
            return calls["n"] <= 2  # two extra polls, no new bytes appended

        follow_pty_log(log, write=written.append, should_continue=_should_continue, block=lambda: None)
        assert "".join(written).count("alpha") == 1

    def test_absent_log_fails_fast(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.log"
        with pytest.raises(FileNotFoundError):
            follow_pty_log(missing, write=lambda _s: None, should_continue=lambda: False, block=lambda: None)

    def test_handles_log_appearing_after_start(self, tmp_path: Path) -> None:
        # When the log is absent at the first poll but appears on a later poll
        # (the __run supervisor has not flushed yet), follow keeps polling (does
        # not fail) when wait_for_log=True, then streams once it appears.
        log = tmp_path / "pty.log"
        written: list[str] = []
        calls = {"n": 0}

        def _should_continue() -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                # First iteration: log absent -> the loop continues without
                # reading. Create it now so the SECOND iteration finds it.
                assert not log.exists()
                return True
            if calls["n"] == 2:
                log.write_text("late start\n", encoding="utf-8")
                return True
            return False

        follow_pty_log(
            log, write=written.append, should_continue=_should_continue, block=lambda: None, wait_for_log=True
        )
        assert "late start" in "".join(written)

    def test_truncation_re_follows_from_new_start(self, tmp_path: Path) -> None:
        # A truncated/rotated log (shorter than the last offset) is re-followed
        # from its new start rather than skipped (exercises the offset reset).
        log = tmp_path / "pty.log"
        log.write_text("aaaaaaaaaa\n", encoding="utf-8")
        written: list[str] = []
        calls = {"n": 0}

        def _should_continue() -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                return True  # iteration 1 reads the long content, offset advances
            if calls["n"] == 2:
                log.write_text("b\n", encoding="utf-8")  # truncate to shorter
                return True  # iteration 2 sees len(data) < offset -> reset + read
            return False

        follow_pty_log(log, write=written.append, should_continue=_should_continue, block=lambda: None)
        joined = "".join(written)
        assert "aaaaaaaaaa" in joined
        assert joined.endswith("b\n")


@pytest.mark.unit
class TestFollowBlockUntilReadable:
    """The production blocking wait parks the process on stdin + a bounded timeout."""

    def test_block_until_readable_returns_on_timeout(self) -> None:
        # With no readable fd activity the wait returns after the bounded poll
        # interval (kernel select timeout) WITHOUT time.sleep, so the follow loop
        # re-reads on a bounded cadence rather than spinning.
        from devbench.supervise import _block_until_readable

        # A very short poll interval keeps the test fast; the call must return
        # (not raise, not hang) when nothing is readable within the interval.
        _block_until_readable(poll_interval_seconds=0.01)

    def test_block_until_readable_returns_promptly_when_fd_readable(self) -> None:
        import os as _os

        from devbench.supervise import _block_until_readable

        # A readable pipe fd stands in for the operator's stdin: select wakes
        # immediately, so the wait returns without consuming the poll interval.
        read_fd, write_fd = _os.pipe()
        try:
            _os.write(write_fd, b"x")
            _block_until_readable(poll_interval_seconds=30, input_fd=read_fd)
        finally:
            _os.close(read_fd)
            _os.close(write_fd)

    def test_block_until_readable_uses_real_stdin_when_fd_omitted(self) -> None:
        import os as _os
        from unittest.mock import patch

        from devbench import supervise

        # With no explicit fd the wait resolves the real stdin fd via
        # _stdin_fileno(); a readable pipe stands in for that fd so the success
        # branch (a usable fileno) is exercised and the wait returns promptly.
        read_fd, write_fd = _os.pipe()
        try:
            _os.write(write_fd, b"x")
            with patch.object(supervise, "_stdin_fileno", return_value=read_fd):
                supervise._block_until_readable(poll_interval_seconds=30)
        finally:
            _os.close(read_fd)
            _os.close(write_fd)

    def test_stdin_fileno_returns_int_for_real_fd(self) -> None:
        import io
        import os as _os
        from unittest.mock import patch

        from devbench import supervise

        # A real readable stream with a valid fileno() returns that fd (the
        # success branch); a stream whose fileno() raises returns None.
        read_fd, write_fd = _os.pipe()
        try:
            with patch.object(supervise.sys, "stdin", _os.fdopen(read_fd, "rb", closefd=False)):
                assert supervise._stdin_fileno() == read_fd
            with patch.object(supervise.sys, "stdin", io.StringIO("not a real fd")):
                assert supervise._stdin_fileno() is None
        finally:
            _os.close(read_fd)
            _os.close(write_fd)


@pytest.mark.unit
class TestSuperviseAttachCli:
    """`supervise attach` (no flags) follows pty.log read-only (FR-26, AC-18 unit)."""

    def _seed(self, tmp_path: Path, name: str) -> SuperviseRegistry:
        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name=name,
            pid=_os.getpid(),
            screen_name=f"devbench-supervise-{name}",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        reg.write_state(st)
        # Seed a pty.log the follow reads.
        log = supervise_pty_log_path(tmp_path, name)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("live transcript line\n", encoding="utf-8")
        return reg

    def test_attach_follows_and_returns_0_on_keyboard_interrupt(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        self._seed(tmp_path, "nightly")

        # The follow loop is interrupted by Ctrl-C (KeyboardInterrupt) -> exit 0
        # (stopping the tail never stops the orchestration, Section 4.7).
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
        assert "read-only" in out  # the banner reminds the operator it is read-only

    def test_attach_real_follow_blocks_via_bounded_select(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Exercise the production attach path: the REAL follow_pty_log runs, so the
        # CLI's _block closure (which parks on the config-driven _block_until_readable)
        # is invoked once per iteration. _block_until_readable is patched to raise
        # KeyboardInterrupt on the first park, ending the follow with exit 0 and
        # proving the loop blocks (event-driven) rather than spinning.
        from unittest.mock import patch

        self._seed(tmp_path, "nightly")
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
        # The existing transcript streamed before the first park.
        assert "live transcript line" in out
        assert "stopped watching" in out
        # The block used the config-driven poll interval (the default is a small int).
        assert parked["interval"] >= 1

    def test_attach_unknown_name_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("attach", "--name", "ghost")
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err

    def test_attach_no_pty_log_yet_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name="fresh",
            pid=_os.getpid(),
            screen_name="devbench-supervise-fresh",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        reg.write_state(st)
        # No pty.log file written -> attach fails fast (FR-30) rather than hanging.
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("attach", "--name", "fresh")
        assert rc == 2
        assert "no PTY transcript" in capsys.readouterr().err

    def test_attach_screen_still_gated(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # AC-33 regression: --screen stays fail-fast-disabled even after the
        # read-only follow lands.
        from unittest.mock import patch

        self._seed(tmp_path, "nightly")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("attach", "--name", "nightly", "--screen")
        assert rc == 2
        assert "--screen attach is not enabled" in capsys.readouterr().err
