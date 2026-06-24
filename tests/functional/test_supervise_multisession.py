"""AC-17 FUNCTIONAL: two stub-backed sessions run in parallel; ``info`` lists both.

Two REAL ``pexpect`` ``__run`` supervisors (each driving the REAL ``stub-claude.py``
``idle`` script, the same program ``screen`` would host) run concurrently under distinct
``--name``s. Both reach ``running``; ``supervise info`` joins ``screen -ls`` with the
registry and lists both screens with distinct names and the exact per-session attach
command (Section 9, FR-11, FR-32). Each session is then drained to a clean stop.

``screen -ls`` is mocked to report both sessions' screens as live (no real screen in CI);
the registry is the real, file-backed multi-session state both ``__run`` processes wrote.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config, supervised_stub

from devbench import cli
from devbench.supervise import SuperviseRegistry, screen_session_name, write_stop_request


def _run_session(name: str, rc_out: dict[str, int]) -> None:
    """Background-thread target: run ``__run`` for *name* (shared harness already active).

    The single ``supervised_stub`` context is entered ONCE in the main thread and
    covers both ``__run`` invocations: ``unittest.mock.patch`` is not thread-safe, so
    per-thread patch contexts would race on the shared CLI globals / ``os.environ``.
    Each ``__run`` is keyed on its own ``--name`` (the registry / stop-request routing
    is name-based, not env-based), so one shared harness drives both sessions cleanly.
    """
    rc_out[name] = cli.cmd_supervise("__run", "--name", name, "--model", "claude-opus-4-8")


def _await_running(tmp_path: Path, name: str, *, attempts: int = 600) -> None:
    """Block (bounded, event-driven) until *name* is ``running`` in the registry."""
    from devbench.supervise import _block_until_readable

    registry = SuperviseRegistry(tmp_path)
    for _ in range(attempts):
        state = registry.read_state(name)
        if state is not None and state.state == "running":
            return
        _block_until_readable(poll_interval_seconds=0.02)
    raise AssertionError(f"supervise session {name!r} never reached 'running'")


@pytest.mark.functional
class TestStubMultiSession:
    """AC-17: two parallel stub sessions; info lists both with distinct names."""

    def test_two_sessions_run_and_info_lists_both(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        rc_out: dict[str, int] = {}
        names = ("fast", "bulk")
        captured: dict[str, str] = {}

        def _capture_print(*args: object, **_kwargs: object) -> None:
            captured["out"] = captured.get("out", "") + " ".join(str(a) for a in args) + "\n"

        with supervised_stub(workspace_root=tmp_path, config=config, stub_env={"STUB_CLAUDE_SCRIPT": "idle"}):
            threads = [threading.Thread(target=_run_session, args=(name, rc_out), name=f"run-{name}") for name in names]
            for thread in threads:
                thread.start()
            try:
                for name in names:
                    _await_running(tmp_path, name)

                live = {screen_session_name(name, prefix=config.screen_name_prefix) for name in names}
                with (
                    patch("devbench.cli._supervise_live_screen_names", return_value=live),
                    patch("builtins.print", _capture_print),
                ):
                    info_rc = cli.cmd_supervise("info")
            finally:
                for name in names:
                    write_stop_request(tmp_path, name)
                for thread in threads:
                    thread.join(timeout=30)

        assert info_rc == 0
        assert all(rc_out.get(name) == 0 for name in names)
        out = captured["out"]
        for name in names:
            assert screen_session_name(name, prefix=config.screen_name_prefix) in out
            assert f"supervise attach --name {name}" in out
        registry_names = {s.name for s in SuperviseRegistry(tmp_path).load()}
        assert registry_names == set(names)
