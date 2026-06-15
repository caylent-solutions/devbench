"""Stop helpers: stop-request reader, screen -X quit, terminal-wait, screen-ls fail-fast.

These back the graceful/hard ``supervise stop`` bodies (Section 4.2, FR-5) and the
``info``/``stop`` screen-listing seam (FR-11):

- ``read_stop_request`` (supervise.py): the in-screen ``__run`` supervisor reads the
  operator-written ``stop.request`` control file to enter ``draining``.
- ``_supervise_screen_quit`` (cli.py): tears a screen down via ``screen -S <n> -X quit``.
- ``_supervise_wait_for_terminal`` (cli.py): event-driven, bounded wait for ``__run``
  to drive the registry to a terminal (no ``time.sleep`` busy-spin).
- ``_supervise_live_screen_names`` (cli.py): a real ``screen -ls`` invocation FAILURE
  fails fast (it is NOT silently flattened to "no screens"); only "no sockets" is empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.supervise import (
    read_stop_request,
    supervise_stop_request_path,
    write_stop_request,
)


@pytest.mark.unit
class TestStopRequestReader:
    """write_stop_request / read_stop_request round-trip (Section 4.2 step 2)."""

    def test_absent_request_reads_false(self, tmp_path: Path) -> None:
        assert read_stop_request(tmp_path, "nightly") is False

    def test_written_request_reads_true(self, tmp_path: Path) -> None:
        write_stop_request(tmp_path, "nightly")
        assert supervise_stop_request_path(tmp_path, "nightly").exists()
        assert read_stop_request(tmp_path, "nightly") is True


@pytest.mark.unit
class TestScreenQuit:
    """_supervise_screen_quit issues screen -S <name> -X quit (Section 4.2)."""

    def test_quit_invokes_screen_dash_x_quit(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=completed) as run:
            cli._supervise_screen_quit(screen_name="devbench-supervise-n", screen_path="/usr/bin/screen")
        cmd = run.call_args.args[0]
        assert cmd == ["/usr/bin/screen", "-S", "devbench-supervise-n", "-X", "quit"]

    def test_quit_absent_screen_is_not_fatal(self) -> None:
        # screen -X quit on a screen that is already gone exits non-zero ("No
        # screen session found"); that is a no-op success for teardown, not a fault.
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="No screen session found.")
        with patch("devbench.cli.subprocess.run", return_value=completed):
            cli._supervise_screen_quit(screen_name="devbench-supervise-gone", screen_path="/usr/bin/screen")

    def test_quit_oserror_is_not_fatal(self) -> None:
        with patch("devbench.cli.subprocess.run", side_effect=OSError("boom")):
            cli._supervise_screen_quit(screen_name="devbench-supervise-x", screen_path="/usr/bin/screen")


@pytest.mark.unit
class TestWaitForTerminal:
    """_supervise_wait_for_terminal polls the registry, event-driven + bounded."""

    def _seed_running(self, tmp_path: Path, name: str):
        import os as _os

        from devbench.constants import SUPERVISE_STATE_RUNNING
        from devbench.supervise import SuperviseRegistry, new_session_state

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
        return reg

    def test_returns_true_when_terminal_reached(self, tmp_path: Path) -> None:
        from devbench.constants import SUPERVISE_STATE_STOPPED

        reg = self._seed_running(tmp_path, "nightly")
        polls = {"n": 0}

        # The injected readiness gate stands in for the kernel-blocking wait. On
        # the 2nd poll it flips the registry to stopped (as __run would), so the
        # waiter observes a terminal. No time.sleep is used.
        def _gate(_timeout: float) -> None:
            polls["n"] += 1
            if polls["n"] == 2:
                st = reg.read_state("nightly")
                st.state = SUPERVISE_STATE_STOPPED
                reg.write_state(st)

        result = cli._supervise_wait_for_terminal(
            name="nightly", registry=reg, timeout_seconds=5, _gate=_gate, _now=_FakeClock([0.0, 0.0, 1.0, 2.0]).now
        )
        assert result is True

    def test_returns_false_on_timeout(self, tmp_path: Path) -> None:
        reg = self._seed_running(tmp_path, "nightly")
        # The session never leaves running; the monotonic clock crosses the budget.
        clock = _FakeClock([0.0, 0.0, 3.0, 6.0])

        def _gate(_timeout: float) -> None:
            return None

        result = cli._supervise_wait_for_terminal(
            name="nightly", registry=reg, timeout_seconds=5, _gate=_gate, _now=clock.now
        )
        assert result is False

    def test_default_gate_is_config_driven_bounded_select(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from devbench.config_loader import SuperviseConfig, SuperviseTimeoutsConfig

        # Exercise the production default gate (no injected _gate): it must build a
        # bounded _block_until_readable using the config poll interval. The session
        # is already terminal so the gate is never actually entered (no real
        # blocking), but the default-gate construction branch is covered.
        reg = self._seed_running(tmp_path, "done")
        from devbench.constants import SUPERVISE_STATE_STOPPED

        st = reg.read_state("done")
        st.state = SUPERVISE_STATE_STOPPED
        reg.write_state(st)
        cfg = SuperviseConfig(timeouts=SuperviseTimeoutsConfig(poll_interval_seconds=3))
        with patch("devbench.cli._supervise_runtime_config", return_value=cfg):
            result = cli._supervise_wait_for_terminal(name="done", registry=reg, timeout_seconds=5)
        assert result is True

    def test_default_gate_invokes_bounded_block(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from devbench.config_loader import SuperviseConfig, SuperviseTimeoutsConfig
        from devbench.constants import SUPERVISE_STATE_STOPPED

        # Drive ONE real default-gate invocation: the session flips to terminal
        # only after the first park, so the constructed gate (which calls
        # _block_until_readable with the config interval) runs exactly once.
        reg = self._seed_running(tmp_path, "slow")
        seen: dict[str, float] = {}

        def _fake_block(*, poll_interval_seconds: float) -> None:
            seen["interval"] = poll_interval_seconds
            st = reg.read_state("slow")
            st.state = SUPERVISE_STATE_STOPPED
            reg.write_state(st)

        cfg = SuperviseConfig(timeouts=SuperviseTimeoutsConfig(poll_interval_seconds=4))
        with (
            patch("devbench.cli._supervise_runtime_config", return_value=cfg),
            patch("devbench.cli._block_until_readable", _fake_block),
        ):
            result = cli._supervise_wait_for_terminal(name="slow", registry=reg, timeout_seconds=600)
        assert result is True
        # The gate bounded the select on the config poll interval (min vs remaining).
        assert seen["interval"] == 4.0


class _FakeClock:
    """A deterministic monotonic clock returning successive scripted readings."""

    def __init__(self, readings: list[float]) -> None:
        self._readings = list(readings)
        self._i = 0

    def now(self) -> float:
        value = self._readings[min(self._i, len(self._readings) - 1)]
        self._i += 1
        return value


@pytest.mark.unit
class TestLiveScreenNamesFailFast:
    """A real screen -ls invocation failure fails fast (CLAUDE.md), not silent-empty."""

    def test_no_sockets_is_empty_not_error(self) -> None:
        # screen -ls with no sessions exits 1 with "No Sockets found" -> empty set,
        # NOT a fault (this is the legitimate "no screens" signal).
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="No Sockets found in /run/screen/S-user.\n", stderr=""
        )
        with (
            patch("devbench.cli.shutil.which", lambda _n: "/usr/bin/screen"),
            patch("devbench.cli.subprocess.run", return_value=completed),
        ):
            assert cli._supervise_live_screen_names() == set()

    def test_screen_absent_fails_fast(self) -> None:
        # screen not installed: a stop/info verb that NEEDS the live screen list to
        # make a teardown decision must fail fast rather than silently treat every
        # session as stale.
        with patch("devbench.cli.shutil.which", lambda _n: None):
            with pytest.raises(cli.SuperviseError, match="screen"):
                cli._supervise_live_screen_names()

    def test_invocation_oserror_fails_fast(self) -> None:
        with (
            patch("devbench.cli.shutil.which", lambda _n: "/usr/bin/screen"),
            patch("devbench.cli.subprocess.run", side_effect=OSError("permission denied")),
        ):
            with pytest.raises(cli.SuperviseError, match="screen -ls"):
                cli._supervise_live_screen_names()
