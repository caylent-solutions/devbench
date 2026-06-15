"""AC-16 FUNCTIONAL: a stub-claude exit-42 drives a bounded auto-restart.

Against the REAL ``stub-claude.py`` scripted (via a launch sequence) to exit 42 on the
first launch then complete clean on the relaunch, the REAL ``pexpect`` supervisor
recognizes the restart signal (Section 4.3), relaunches ``claude`` with the resume
flags, and the registry shows ``restart-count`` incremented. A sequence that ALWAYS
exits 42 is bounded by ``supervise.restart.max_attempts`` and faults with
``restart-cap-exhausted`` rather than looping forever (FR-12).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from functional.harness import functional_supervise_config, stub_sequence_env, supervised_stub

from devbench import cli
from devbench.config_loader import (
    SuperviseConfig,
    SuperviseRestartConfig,
    SuperviseTimeoutsConfig,
)
from devbench.constants import SUPERVISE_FAULT_EXIT_CODE
from devbench.supervise import SuperviseRegistry


@pytest.mark.functional
class TestStubAutoRestart:
    """AC-16: exit-42 -> bounded relaunch -> restart-count incremented (real pexpect)."""

    def test_exit_42_then_clean_increments_restart_count(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        state_file = tmp_path / "stub-seq.state"
        stub_env = stub_sequence_env(sequence="restart,clean", state_file=state_file)
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "ar1", "--model", "claude-opus-4-8")

        assert rc == 0
        state = SuperviseRegistry(tmp_path).read_state("ar1")
        assert state is not None
        # The first launch emitted exit-42 (one relaunch), the relaunch completed clean.
        assert state.state == "completed-clean"
        assert state.restart_count == 1
        assert state.exit_reason == "all-done"

    def test_repeated_exit_42_exhausts_bound(self, tmp_path: Path) -> None:
        # A stub that ALWAYS exits 42 must hit the configured cap and fault (not loop).
        timeouts = SuperviseTimeoutsConfig(
            ready_prompt_seconds=15, idle_seconds=15, command_ack_seconds=2, poll_interval_seconds=1
        )
        config = SuperviseConfig(timeouts=timeouts, restart=SuperviseRestartConfig(max_attempts=2))
        stub_env = {"STUB_CLAUDE_SCRIPT": "restart"}  # every launch exits 42
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "ar2", "--model", "claude-opus-4-8")

        assert rc == SUPERVISE_FAULT_EXIT_CODE
        state = SuperviseRegistry(tmp_path).read_state("ar2")
        assert state is not None
        assert state.state == "faulted"
        assert state.exit_reason == "restart-cap-exhausted"
        assert state.restart_count == 2
