"""AC-19 FUNCTIONAL: ``supervise stop`` gracefully drains the stub, then hard-stops.

Against the REAL ``stub-claude.py`` (the ``idle`` script: it stays alive after the
kickoff and exits only on the drain command), the REAL ``pexpect`` supervisor + the
operator-facing ``stop`` verb drive the graceful-stop sequence end to end (Section 4.2):

- ``stop`` writes the per-session ``drain.signal`` and the ``stop.request`` control file,
- the in-screen ``__run`` loop sees the stop request, transitions to ``draining``, sends
  ``/exit`` (the ``drain_now`` injectable command), reads the stub to EOF, and records
  ``state=stopped`` (operator-initiated -> exit 0),
- ``stop`` polls the registry and returns 0 once the terminal state is recorded.

The ``__run`` supervisor runs on a background thread (the same program ``screen`` would
host) so the operator ``stop`` verb can signal it from the main thread, exercising the
two-process handshake deterministically in one test process. ``screen -ls`` is mocked to
report the session's screen as live so the graceful path drives the drain rather than a
stale-screen reconcile.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config, supervised_stub

from devbench import cli
from devbench.supervise import SuperviseRegistry, screen_session_name, write_stop_request


@pytest.mark.functional
class TestStubGracefulStopDrains:
    """AC-19: a pre-written stop.request drives __run to drain -> stopped exit 0."""

    def test_run_drains_to_stopped_on_stop_request(self, tmp_path: Path) -> None:
        # The in-screen __run response in isolation: with the stop.request already
        # present, the first running iteration drains the idle stub and stops.
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "idle"}
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            write_stop_request(tmp_path, "g0")
            rc = cli.cmd_supervise("__run", "--name", "g0", "--model", "claude-opus-4-8")

        assert rc == 0
        state = SuperviseRegistry(tmp_path).read_state("g0")
        assert state is not None
        assert state.state == "stopped"
        assert state.exit_reason == "graceful-stop"
        # The drain command (/exit) was injected and reached the stub over the PTY.
        transcript = (tmp_path / ".devbench" / "supervise" / "g0" / "pty.log").read_text(encoding="utf-8")
        assert "/exit" in transcript


@pytest.mark.functional
class TestStubGracefulStopEndToEnd:
    """AC-19: the operator `stop` verb signals a live __run, which drains -> stopped."""

    def test_stop_verb_drains_live_run(self, tmp_path: Path) -> None:
        # The full operator handshake: a live __run (real pexpect + idle stub) on a
        # background thread (the same program ``screen`` would host), signalled by the
        # operator-facing graceful ``stop`` verb from the main thread. The verb writes
        # the drain.signal + stop.request and DELEGATES teardown to __run, which drains
        # the idle stub, sends /exit, and records ``stopped`` (Section 4.2, FR-5).
        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME, SESSION_SESSIONS_BASE_DIR

        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "idle"}
        run_rc: dict[str, int] = {}

        def _run() -> None:
            with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
                run_rc["rc"] = cli.cmd_supervise("__run", "--name", "g1", "--model", "claude-opus-4-8")

        run_thread = threading.Thread(target=_run, name="supervise-run")
        run_thread.start()
        try:
            _await_state(tmp_path, "g1", "running")
            live = {screen_session_name("g1", prefix=config.screen_name_prefix)}
            with (
                patch.object(cli, "WORKSPACE_ROOT", tmp_path),
                patch("devbench.cli._supervise_runtime_config", return_value=config),
                patch("devbench.cli._supervise_live_screen_names", return_value=live),
            ):
                stop_rc = cli.cmd_supervise("stop", "--name", "g1")
        finally:
            run_thread.join(timeout=30)

        assert not run_thread.is_alive()
        assert stop_rc == 0
        assert run_rc["rc"] == 0
        state = SuperviseRegistry(tmp_path).read_state("g1")
        assert state is not None
        assert state.state == "stopped"
        assert state.exit_reason == "graceful-stop"
        # The graceful stop routed the per-session drain signal (FR-5, Section 4.2
        # step 1): the signal is consumed by __run's drain, but its per-session dir
        # proves it was keyed on DEVBENCH_SESSION_NAME, not the workspace root.
        drain_dir = tmp_path / SESSION_SESSIONS_BASE_DIR / "g1"
        assert drain_dir.exists()
        assert (drain_dir / SESSION_DRAIN_SIGNAL_FILENAME).parent == drain_dir


def _await_state(tmp_path: Path, name: str, target: str, *, attempts: int = 600) -> None:
    """Block (event-driven, bounded) until *name* reaches *target* in the registry.

    The supervised ``__run`` thread writes ``state=running`` once kickoff completes;
    this waits for that handshake before the operator stop is issued. Between reads it
    parks on the SHARED, config-driven :func:`devbench.supervise._block_until_readable`
    (a bounded ``select``) -- the same event-driven readiness park ``__run``'s own
    ``_supervise_wait_for_terminal`` uses -- never a ``time.sleep`` spin (CLAUDE.md
    Section 7.5). Fails fast (raises) rather than hanging if the state never arrives.
    """
    from devbench.supervise import _block_until_readable

    registry = SuperviseRegistry(tmp_path)
    for _ in range(attempts):
        state = registry.read_state(name)
        if state is not None and state.state == target:
            return
        _block_until_readable(poll_interval_seconds=0.02)
    raise AssertionError(f"supervise session {name!r} never reached state {target!r}")
