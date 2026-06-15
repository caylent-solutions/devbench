"""Graceful drain-then-stop, hard screen-quit, and stale reconcile for ``supervise stop``.

Section 4.2 / FR-5. The operator-facing ``stop`` does NOT stamp the registry
``stopped`` itself when a live ``__run`` supervisor owns the session: graceful
writes the per-session drain signal + the ``stop.request`` control file, then
WAITS (event-driven, bounded by ``graceful_stop_seconds``) for the in-screen
``__run`` supervisor to drain the in-flight WU and transition the registry to a
terminal; on timeout it ESCALATES to hard. ``--hard`` tears the screen down via
``screen -S <screen> -X quit`` and then records ``stopped exit-reason=hard-stop``.

The stale-screen reconcile (registry says running but ``screen -ls`` no longer
lists it) reconciles to ``state=stopped`` with a note, never blocking on a
supervisor that is gone.
"""

from __future__ import annotations

import os as _os
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.constants import (
    SUPERVISE_STATE_COMPLETED_CLEAN,
    SUPERVISE_STATE_RUNNING,
    SUPERVISE_STATE_STOPPED,
)
from devbench.supervise import (
    SuperviseRegistry,
    new_session_state,
    read_stop_request,
    supervise_stop_request_path,
)


def _seed_running(tmp_path: Path, name: str) -> SuperviseRegistry:
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


@pytest.mark.unit
class TestStopStaleScreenReconcile:
    """A registry-running session whose screen is gone reconciles to stopped (FR-5)."""

    def test_stale_screen_reconciles_to_stopped_no_drain(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reg = _seed_running(tmp_path, "nightly")
        # The screen is NOT present -> reconcile, no drain signal written.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value=set()),
        ):
            rc = cli.cmd_supervise("stop", "--name", "nightly")
        assert rc == 0
        after = reg.read_state("nightly")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "stale-screen-reconciled"
        # No graceful drain control-file when the screen is already gone.
        assert not supervise_stop_request_path(tmp_path, "nightly").exists()
        assert "reconciled" in capsys.readouterr().out


@pytest.mark.unit
class TestStopGracefulDrain:
    """Graceful stop signals __run and waits for it to reach a terminal (Section 4.2)."""

    def test_graceful_writes_signals_and_waits_for_run_to_stop(self, tmp_path: Path) -> None:
        reg = _seed_running(tmp_path, "nightly")

        # The injected wait stands in for the in-screen __run supervisor: on the
        # FIRST registry poll it transitions the session to ``stopped`` (as __run
        # would after draining the in-flight WU), so the operator stop observes a
        # terminal and returns 0 WITHOUT stamping the registry itself.
        def _fake_wait(*, name, registry, timeout_seconds):
            st = registry.read_state(name)
            st.state = SUPERVISE_STATE_STOPPED
            st.exit_reason = "graceful-stop"
            registry.write_state(st)
            return True  # __run reached a terminal within the budget

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-nightly"}),
            patch("devbench.cli._supervise_wait_for_terminal", _fake_wait),
        ):
            rc = cli.cmd_supervise("stop", "--name", "nightly")
        assert rc == 0
        after = reg.read_state("nightly")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "graceful-stop"
        # The drain + stop.request control files were written for __run to read.
        stop_req = supervise_stop_request_path(tmp_path, "nightly")
        assert stop_req.exists()
        assert read_stop_request(tmp_path, "nightly") is True

    def test_graceful_accepts_completed_clean_terminal(self, tmp_path: Path) -> None:
        # If the in-flight WU finishing drove __run to ``completed-clean`` instead
        # of ``stopped``, the operator stop still succeeds (the session IS down).
        reg = _seed_running(tmp_path, "nightly")

        def _fake_wait(*, name, registry, timeout_seconds):
            st = registry.read_state(name)
            st.state = SUPERVISE_STATE_COMPLETED_CLEAN
            st.exit_reason = "all-done"
            registry.write_state(st)
            return True

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-nightly"}),
            patch("devbench.cli._supervise_wait_for_terminal", _fake_wait),
        ):
            rc = cli.cmd_supervise("stop", "--name", "nightly")
        assert rc == 0
        after = reg.read_state("nightly")
        assert after is not None
        assert after.state == "completed-clean"

    def test_graceful_does_not_stamp_stopped_before_run_acts(self, tmp_path: Path) -> None:
        # CRITICAL regression: the operator stop must NOT immediately mark the
        # registry stopped. It writes the signals and DELEGATES the terminal to
        # __run. We capture the registry state at the moment the wait is entered.
        _seed_running(tmp_path, "nightly")
        observed: dict[str, str] = {}

        def _fake_wait(*, name, registry, timeout_seconds):
            observed["state_when_wait_entered"] = registry.read_state(name).state
            st = registry.read_state(name)
            st.state = SUPERVISE_STATE_STOPPED
            st.exit_reason = "graceful-stop"
            registry.write_state(st)
            return True

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-nightly"}),
            patch("devbench.cli._supervise_wait_for_terminal", _fake_wait),
        ):
            cli.cmd_supervise("stop", "--name", "nightly")
        # The session was still ``running`` when the wait began: the operator stop
        # signalled __run and waited; it did not stamp the terminal itself.
        assert observed["state_when_wait_entered"] == "running"


@pytest.mark.unit
class TestStopGracefulTimeoutEscalatesToHard:
    """A graceful stop that times out escalates to hard (screen quit + stopped)."""

    def test_timeout_escalates_to_hard_quit(self, tmp_path: Path) -> None:
        reg = _seed_running(tmp_path, "nightly")
        quits: list[str] = []

        # __run never reaches a terminal within the budget -> escalate to hard.
        def _fake_wait(*, name, registry, timeout_seconds):
            return False

        def _fake_quit(*, screen_name, screen_path):
            quits.append(screen_name)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-nightly"}),
            patch("devbench.cli._supervise_wait_for_terminal", _fake_wait),
            patch("devbench.cli._supervise_screen_quit", _fake_quit),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        ):
            rc = cli.cmd_supervise("stop", "--name", "nightly")
        assert rc == 0
        after = reg.read_state("nightly")
        assert after is not None
        assert after.state == "stopped"
        # An escalated stop is recorded as a hard stop (the screen was force-quit).
        assert after.exit_reason == "hard-stop"
        assert quits == ["devbench-supervise-nightly"]


@pytest.mark.unit
class TestStopHardTerminates:
    """``--hard`` actually terminates the screen (and its __run + claude child)."""

    def test_hard_quits_screen_then_marks_stopped(self, tmp_path: Path) -> None:
        reg = _seed_running(tmp_path, "n")
        quits: list[str] = []

        def _fake_quit(*, screen_name, screen_path):
            quits.append(screen_name)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-n"}),
            patch("devbench.cli._supervise_screen_quit", _fake_quit),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        ):
            rc = cli.cmd_supervise("stop", "--name", "n", "--hard")
        assert rc == 0
        after = reg.read_state("n")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "hard-stop"
        # --hard tore the screen down via screen -X quit.
        assert quits == ["devbench-supervise-n"]
        # --hard never writes the graceful stop.request control file.
        assert not supervise_stop_request_path(tmp_path, "n").exists()

    def test_hard_on_stale_screen_still_marks_stopped(self, tmp_path: Path) -> None:
        # --hard on a session whose screen is already gone must not crash on the
        # quit (the screen is absent); it still records the stop.
        reg = _seed_running(tmp_path, "n")

        def _fake_quit(*, screen_name, screen_path):
            return None  # quitting an absent screen is a no-op for the caller

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value=set()),
            patch("devbench.cli._supervise_screen_quit", _fake_quit),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        ):
            rc = cli.cmd_supervise("stop", "--name", "n", "--hard")
        assert rc == 0
        after = reg.read_state("n")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "hard-stop"

    def test_hard_records_stop_even_when_screen_not_installed(self, tmp_path: Path) -> None:
        # If `screen` is not on PATH, --hard cannot quit a screen, but the registry
        # transition to stopped is the authoritative operator outcome (it does not
        # block on the quit). _supervise_live_screen_names is patched so the early
        # list-check does not fail fast before reaching the hard body.
        reg = _seed_running(tmp_path, "n")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-n"}),
            patch("devbench.cli.shutil.which", lambda _name: None),
        ):
            rc = cli.cmd_supervise("stop", "--name", "n", "--hard")
        assert rc == 0
        after = reg.read_state("n")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "hard-stop"


@pytest.mark.unit
class TestStopUnknownName:
    """An unknown --name fails fast (exit 2) regardless of mode (FR-30)."""

    def test_unknown_name_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("stop", "--name", "ghost")
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err


@pytest.mark.unit
class TestStopScreenListFailsFast:
    """A broken `screen -ls` during stop fails fast, NOT mistaken for "gone" (FR-30)."""

    def test_screen_list_error_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _seed_running(tmp_path, "nightly")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", side_effect=cli.SuperviseError("screen -ls failed")),
        ):
            rc = cli.cmd_supervise("stop", "--name", "nightly")
        assert rc == 2
        assert "screen -ls failed" in capsys.readouterr().err
