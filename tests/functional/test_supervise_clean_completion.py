"""AC-13 FUNCTIONAL: stub-claude clean completion drives ``completed-clean`` exit 0.

Against the REAL ``stub-claude.py`` executable through the REAL ``pexpect`` supervisor,
``supervise __run`` (the in-screen body) waits for the stub's ready prompt, injects
``/devbench-orchestrate:orchestrate``, observes the scripted ``ALL_DONE`` terminal, and
exits 0 with ``state=completed-clean`` recorded in the registry (Section 4.1, 4.6).

This is the canonical Phase 5 happy path: no test double for the child -- a real
subprocess is launched and driven over a PTY.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from functional.harness import functional_supervise_config, supervised_stub

from devbench import cli
from devbench.supervise import SuperviseRegistry


@pytest.mark.functional
class TestStubCleanCompletion:
    """AC-13: ALL_DONE -> completed-clean -> exit 0 (real pexpect, real stub)."""

    def test_all_done_reaches_completed_clean(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "clean", "STUB_CLAUDE_EXIT_CODE": "0"}
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "clean1", "--model", "claude-opus-4-8")

        assert rc == 0
        state = SuperviseRegistry(tmp_path).read_state("clean1")
        assert state is not None
        assert state.state == "completed-clean"
        assert state.exit_reason == "all-done"
        # The supervisor recorded the resolved (stub) claude binary for audit (FR-25).
        assert state.claude_path is not None
        assert state.claude_path.endswith("stub-claude.py")
        assert state.claude_version == "stub-claude 0.0.1"

    def test_no_actionable_is_clean_exit_zero(self, tmp_path: Path) -> None:
        # An operator-gated NO_ACTIONABLE (no RUNTIME_DEGRADATION restart pending) is a
        # CLEAN completion (Section 4.6 row 2): exit 0, completed-clean.
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "no_actionable", "STUB_CLAUDE_EXIT_CODE": "0"}
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "na1", "--model", "claude-opus-4-8")

        assert rc == 0
        state = SuperviseRegistry(tmp_path).read_state("na1")
        assert state is not None
        assert state.state == "completed-clean"
        assert state.exit_reason == "no-actionable"

    def test_pty_log_written_and_locked_down(self, tmp_path: Path) -> None:
        # FR-24: the live PTY stream is tee'd to a 0600 pty.log under the state dir.
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "clean", "STUB_CLAUDE_EXIT_CODE": "0"}
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "clean2", "--model", "claude-opus-4-8")

        assert rc == 0
        pty_log = tmp_path / ".devbench" / "supervise" / "clean2" / "pty.log"
        assert pty_log.exists()
        assert (pty_log.stat().st_mode & 0o777) == 0o600
        # The injected kickoff and the terminal sentinel both passed through the PTY.
        transcript = pty_log.read_text(encoding="utf-8")
        assert "ALL_DONE" in transcript
