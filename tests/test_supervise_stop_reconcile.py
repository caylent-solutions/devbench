"""Stale-screen reconcile + graceful-stop notes for ``supervise stop`` (Section 4.2, FR-5).

P4 expands ``stop`` with the stale-screen reconcile (the registry says the session
is running but ``screen -ls`` no longer lists it): the verb reconciles the registry
to ``state=stopped`` and returns 0 with a note instead of writing a pointless drain
signal. The graceful drain + ``stop.request`` path (Phase 3) is unchanged when the
screen IS live.
"""

from __future__ import annotations

import os as _os
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.constants import SUPERVISE_STATE_RUNNING
from devbench.supervise import (
    SuperviseRegistry,
    new_session_state,
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
            patch("devbench.cli._supervise_screen_names", return_value=set()),
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

    def test_live_screen_takes_graceful_path(self, tmp_path: Path) -> None:
        reg = _seed_running(tmp_path, "nightly")
        # The screen IS present -> the graceful drain + stop.request path runs.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_screen_names", return_value={"devbench-supervise-nightly"}),
        ):
            rc = cli.cmd_supervise("stop", "--name", "nightly")
        assert rc == 0
        after = reg.read_state("nightly")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "graceful-stop"
        assert supervise_stop_request_path(tmp_path, "nightly").exists()

    def test_hard_stop_ignores_screen_presence(self, tmp_path: Path) -> None:
        reg = _seed_running(tmp_path, "n")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_screen_names", return_value={"devbench-supervise-n"}),
        ):
            rc = cli.cmd_supervise("stop", "--name", "n", "--hard")
        assert rc == 0
        after = reg.read_state("n")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "hard-stop"
        # --hard never writes the graceful stop.request control file.
        assert not supervise_stop_request_path(tmp_path, "n").exists()
