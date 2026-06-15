"""AC-14 FUNCTIONAL: a stub-claude crash drives ``running -> faulted`` non-zero exit.

Against the REAL ``stub-claude.py`` scripted to crash (print a traceback line then
``sys.exit(1)``), the REAL ``pexpect`` supervisor classifies the non-zero child exit as
a FAULT (Section 4.6: claude crash / non-zero exit), exits non-zero, and records
``exit-reason=claude-exit-1`` in the registry (FR-13).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from functional.harness import functional_supervise_config, supervised_stub

from devbench import cli
from devbench.constants import SUPERVISE_FAULT_EXIT_CODE
from devbench.supervise import SuperviseRegistry


@pytest.mark.functional
class TestStubCrashFaults:
    """AC-14: crash -> faulted -> non-zero classified exit (real pexpect, real stub)."""

    def test_crash_reaches_faulted_nonzero(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "crash"}  # default crash exit code is 1
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "boom", "--model", "claude-opus-4-8")

        assert rc != 0
        assert rc == SUPERVISE_FAULT_EXIT_CODE
        state = SuperviseRegistry(tmp_path).read_state("boom")
        assert state is not None
        assert state.state == "faulted"
        assert state.exit_reason == "claude-exit-1"

    def test_crash_exit_code_is_classified_not_passed_through(self, tmp_path: Path) -> None:
        # A different non-zero exit is still classified as claude-exit-<code> (the
        # supervisor never simply mirrors the child's raw code; Section 4.6).
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "crash", "STUB_CLAUDE_EXIT_CODE": "7"}
        with supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "boom7", "--model", "claude-opus-4-8")

        assert rc == SUPERVISE_FAULT_EXIT_CODE
        state = SuperviseRegistry(tmp_path).read_state("boom7")
        assert state is not None
        assert state.state == "faulted"
        assert state.exit_reason == "claude-exit-7"
