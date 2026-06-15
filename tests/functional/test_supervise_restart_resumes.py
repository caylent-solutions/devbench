"""AC-20 FUNCTIONAL: a relaunch preserves context via ``--continue`` / ``--resume``.

When the REAL ``pexpect`` supervisor relaunches the REAL ``stub-claude.py`` (here driven
by an exit-42 auto-restart, the same relaunch the ``supervise restart`` verb performs via
``build_resume_argv``), it re-spawns ``claude`` with the resume flags so orchestration
context is preserved (Section 4.3, FR-12): ``--continue`` by default, ``--resume <id>``
when ``supervise.restart.resume_mode == resume`` and a session id was captured. The
relaunched session reaches ``running`` and completes.

The relaunch argv is observed by spying on the spawn seam ``_supervise_spawn_child`` (the
single place the supervisor launches/relaunches the child), so the test asserts the
exact resume flag the relaunch carried -- not merely that a relaunch happened.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config, stub_sequence_env, supervised_stub

from devbench import cli
from devbench.config_loader import SuperviseConfig, SuperviseRestartConfig, SuperviseTimeoutsConfig
from devbench.supervise import SuperviseRegistry


def _spawn_spy(captured: list[list[str]]):
    """Return a ``_supervise_spawn_child`` wrapper recording each launch argv."""
    real_spawn = cli._supervise_spawn_child

    def _wrapped(*, launch_argv: list[str], cfg):
        captured.append(list(launch_argv))
        return real_spawn(launch_argv=launch_argv, cfg=cfg)

    return _wrapped


@pytest.mark.functional
class TestStubRestartResumesContext:
    """AC-20: relaunch uses --continue/--resume and reaches running (real pexpect)."""

    def test_relaunch_uses_continue_by_default(self, tmp_path: Path) -> None:
        config = functional_supervise_config()  # resume_mode defaults to "continue"
        state_file = tmp_path / "stub-seq.state"
        # Launch 1 exits 42 (restart signal); the relaunch (launch 2) completes clean.
        stub_env = stub_sequence_env(sequence="restart,clean", state_file=state_file)
        launches: list[list[str]] = []
        with (
            supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env),
            patch("devbench.cli._supervise_spawn_child", _spawn_spy(launches)),
        ):
            rc = cli.cmd_supervise("__run", "--name", "rs1", "--model", "claude-opus-4-8")

        assert rc == 0
        state = SuperviseRegistry(tmp_path).read_state("rs1")
        assert state is not None
        assert state.state == "completed-clean"
        assert state.restart_count == 1
        # Two launches: the initial launch (no resume flag) and the relaunch.
        assert len(launches) == 2
        initial, relaunch = launches
        assert "--continue" not in initial and "--resume" not in initial
        # The context-preserving relaunch carried --continue (resume_mode=continue).
        assert "--continue" in relaunch
        assert "--resume" not in relaunch

    def test_relaunch_uses_resume_id_when_configured(self, tmp_path: Path) -> None:
        # resume_mode=resume with a captured session id relaunches via --resume <id>
        # (the exact-transcript resume the `supervise restart` verb performs).
        timeouts = SuperviseTimeoutsConfig(
            ready_prompt_seconds=15, idle_seconds=15, command_ack_seconds=2, poll_interval_seconds=1
        )
        config = SuperviseConfig(timeouts=timeouts, restart=SuperviseRestartConfig(resume_mode="resume"))
        state_file = tmp_path / "stub-seq.state"
        stub_env = stub_sequence_env(sequence="restart,clean", state_file=state_file)
        launches: list[list[str]] = []

        # Capture a session id onto the run state the moment it is first written so the
        # relaunch (which reads state.claude_session_id) resumes that exact transcript.
        captured_id = "018f-abc-resume"
        real_write_state = SuperviseRegistry.write_state

        def _write_state_with_id(self, state):
            if state.claude_session_id is None:
                state.claude_session_id = captured_id
            return real_write_state(self, state)

        with (
            supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env),
            patch("devbench.cli._supervise_spawn_child", _spawn_spy(launches)),
            patch.object(SuperviseRegistry, "write_state", _write_state_with_id),
        ):
            rc = cli.cmd_supervise("__run", "--name", "rs2", "--model", "claude-opus-4-8")

        assert rc == 0
        assert len(launches) == 2
        relaunch = launches[1]
        assert "--resume" in relaunch
        assert relaunch[relaunch.index("--resume") + 1] == captured_id
