"""AC-18 FUNCTIONAL: ``supervise attach`` is read-only; stdin never reaches the child.

The default attach is a follow of the redacted ``pty.log`` -- a pure READ of a file the
``__run`` supervisor writes (Section 4.7, FR-26). The attaching process's stdin is NEVER
wired to the ``claude`` TTY, so an observer cannot inject input into or steal the PTY.

This drives a live REAL ``pexpect`` ``__run`` (the ``idle`` stub) and a real follow of
its ``pty.log`` whose readiness park is a pipe standing in for the attach process's
stdin. Bytes written to that pipe (an operator "typing" at the observer) wake the park
but are NEVER forwarded to the child: the stub echoes everything it receives on its OWN
stdin, so the absence of the injected sentinel from the child's transcript proves the
attach cannot inject input (AC-18). The follow DID stream the real transcript, confirming
read-only observation works.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config, stub_claude_which, supervised_stub

from devbench import cli
from devbench.supervise import (
    SuperviseRegistry,
    _block_until_readable,
    follow_pty_log,
    supervise_pty_log_path,
    write_stop_request,
)

_INJECTED = "INJECTED-OPERATOR-KEYSTROKE"


def _await_running(tmp_path: Path, name: str, *, attempts: int = 600) -> None:
    """Block (bounded, event-driven) until *name* is ``running`` in the registry."""
    registry = SuperviseRegistry(tmp_path)
    for _ in range(attempts):
        state = registry.read_state(name)
        if state is not None and state.state == "running":
            return
        _block_until_readable(poll_interval_seconds=0.02)
    raise AssertionError(f"supervise session {name!r} never reached 'running'")


@pytest.mark.functional
class TestAttachReadOnly:
    """AC-18: attach follows pty.log read-only; injected stdin never reaches the child."""

    def test_injected_stdin_does_not_reach_child(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        rc_out: dict[str, int] = {}

        def _run() -> None:
            with supervised_stub(workspace_root=tmp_path, config=config, stub_env={"STUB_CLAUDE_SCRIPT": "idle"}):
                rc_out["rc"] = cli.cmd_supervise("__run", "--name", "obs", "--model", "claude-opus-4-8")

        run_thread = threading.Thread(target=_run, name="supervise-run-obs")
        run_thread.start()

        # A pipe standing in for the attach process's stdin. The follow's readiness
        # park selects on its READ end; writing to the WRITE end "types" at the observer.
        stdin_read, stdin_write = os.pipe()
        followed: list[str] = []
        reads_done = {"n": 0}

        def _should_continue() -> bool:
            # Observe a couple of read+park cycles, then stop (a bounded follow).
            return reads_done["n"] < 3

        def _block() -> None:
            reads_done["n"] += 1
            _block_until_readable(poll_interval_seconds=0.05, input_fd=stdin_read)

        try:
            _await_running(tmp_path, "obs")
            # The observer "types" the injection sentinel at the attach stdin. It wakes
            # the follow's select() but must NEVER be forwarded to the claude child.
            os.write(stdin_write, (_INJECTED + "\n").encode("utf-8"))
            log_path = supervise_pty_log_path(tmp_path, "obs")
            follow_pty_log(
                log_path,
                write=followed.append,
                should_continue=_should_continue,
                block=_block,
                wait_for_log=True,
            )
        finally:
            os.close(stdin_read)
            os.close(stdin_write)
            write_stop_request(tmp_path, "obs")
            run_thread.join(timeout=30)

        assert rc_out.get("rc") == 0
        transcript = "".join(followed)
        # Read-only observation worked: the follow streamed the real PTY transcript
        # (the supervisor-injected kickoff is visible).
        assert "/devbench-orchestrate:orchestrate" in transcript
        # The injected operator keystroke NEVER reached the child: the stub echoes
        # everything it receives on its own stdin as "[stub-claude] received: ...",
        # and that echo is absent from BOTH the follow stream AND the child's log.
        assert _INJECTED not in transcript
        child_log = supervise_pty_log_path(tmp_path, "obs").read_text(encoding="utf-8")
        assert f"received: {_INJECTED}" not in child_log

    def test_attach_verb_rejects_unknown_session(self, tmp_path: Path) -> None:
        # The attach verb fails fast (exit 2) for an unknown --name (FR-30); it never
        # silently follows a non-existent transcript.
        with (
            patch.object(cli, "WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_runtime_config", return_value=functional_supervise_config()),
            patch("devbench.cli.shutil.which", stub_claude_which),
        ):
            rc = cli.cmd_supervise("attach", "--name", "ghost")
        assert rc == 2
