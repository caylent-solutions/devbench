"""Restart loop + ``supervise restart`` verb (AC-10, FR-12, Section 4.3/4.6).

Spec Section 4.3 / 4.6: the supervisor owns a bounded auto-restart loop honoring
devbench's exit-42-equivalent restart signal (ORCHESTRATOR_RESTART_EXIT_CODE /
the [ORCHESTRATOR_AUTO_RESTART] log marker), bounded by
``supervise.restart.max_attempts``; on the bound being exceeded the session
faults with ``restart-cap-exhausted``. The relaunch uses --continue/--resume per
``resume_mode`` (build_claude_launch_argv already supports both). ``supervise
restart`` performs a graceful stop preserving the captured session id, then a
start with the resume flags.
"""

from __future__ import annotations

import pytest

from devbench.config_loader import SuperviseRestartConfig
from devbench.supervise import RestartBudget, build_resume_argv


@pytest.mark.unit
class TestRestartBudget:
    """RestartBudget bounds auto-restarts by max_attempts (FR-12)."""

    def test_first_restart_permitted(self) -> None:
        budget = RestartBudget(max_attempts=3)
        assert budget.may_restart(attempts_used=0) is True

    def test_under_cap_permitted(self) -> None:
        budget = RestartBudget(max_attempts=3)
        assert budget.may_restart(attempts_used=2) is True

    def test_at_cap_denied(self) -> None:
        budget = RestartBudget(max_attempts=3)
        assert budget.may_restart(attempts_used=3) is False

    def test_over_cap_denied(self) -> None:
        budget = RestartBudget(max_attempts=3)
        assert budget.may_restart(attempts_used=4) is False

    def test_zero_attempts_disables_restart(self) -> None:
        budget = RestartBudget(max_attempts=0)
        assert budget.may_restart(attempts_used=0) is False


@pytest.mark.unit
class TestBuildResumeArgv:
    """build_resume_argv selects --resume <id> vs --continue per resume_mode (Section 4.3)."""

    def test_resume_mode_with_session_id(self) -> None:
        argv = build_resume_argv(
            claude_path="/usr/bin/claude",
            model="claude-opus-4-8",
            effort="xhigh",
            plugin_dir="/p",
            restart_config=SuperviseRestartConfig(resume_mode="resume"),
            claude_session_id="abc-123",
        )
        assert "--resume" in argv
        assert argv[argv.index("--resume") + 1] == "abc-123"
        assert "--continue" not in argv

    def test_continue_mode_uses_continue(self) -> None:
        argv = build_resume_argv(
            claude_path="/usr/bin/claude",
            model="claude-opus-4-8",
            effort="xhigh",
            plugin_dir="/p",
            restart_config=SuperviseRestartConfig(resume_mode="continue"),
            claude_session_id="abc-123",
        )
        # continue mode ignores the captured id and uses --continue.
        assert "--continue" in argv
        assert "--resume" not in argv

    def test_resume_mode_without_id_falls_back_to_continue(self) -> None:
        # resume_mode=resume but no captured id: --continue is the only safe relaunch.
        argv = build_resume_argv(
            claude_path="/usr/bin/claude",
            model="claude-opus-4-8",
            effort="xhigh",
            plugin_dir="/p",
            restart_config=SuperviseRestartConfig(resume_mode="resume"),
            claude_session_id=None,
        )
        assert "--continue" in argv
        assert "--resume" not in argv


# ---------------------------------------------------------------------------
# supervise restart CLI body (Section 4.3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuperviseRestartCli:
    """`supervise restart` = graceful stop (capture id) then start with resume flags."""

    def test_restart_unknown_name_returns_2(self, tmp_path, capsys) -> None:
        from unittest.mock import patch

        from devbench import cli

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("restart", "--name", "ghost")
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err

    def test_restart_calls_stop_then_start(self, tmp_path) -> None:
        import os as _os
        from unittest.mock import MagicMock, patch

        from devbench import cli
        from devbench.constants import SUPERVISE_STATE_RUNNING
        from devbench.supervise import SuperviseRegistry, new_session_state

        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name="nightly",
            pid=_os.getpid(),
            screen_name="devbench-supervise-nightly",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        st.claude_session_id = "captured-id"
        reg.write_state(st)

        stop_mock = MagicMock(return_value=0)
        start_mock = MagicMock(return_value=0)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._cmd_supervise_stop", stop_mock),
            patch("devbench.cli._cmd_supervise_start", start_mock),
        ):
            rc = cli.cmd_supervise("restart", "--name", "nightly")
        assert rc == 0
        # restart performs a graceful stop first, then a start.
        assert stop_mock.called
        assert start_mock.called

    def test_stop_unknown_name_returns_2(self, tmp_path, capsys) -> None:
        from unittest.mock import patch

        from devbench import cli

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("stop", "--name", "ghost")
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err

    def test_stop_graceful_drains_and_marks_stopped(self, tmp_path) -> None:
        import os as _os
        from unittest.mock import patch

        from devbench import cli
        from devbench.constants import SUPERVISE_STATE_RUNNING, SUPERVISE_STATE_STOPPED
        from devbench.supervise import SuperviseRegistry, new_session_state, supervise_stop_request_path

        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name="nightly",
            pid=_os.getpid(),
            screen_name="devbench-supervise-nightly",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        reg.write_state(st)

        # The screen is live, so the graceful drain path runs: stop signals __run
        # and WAITS for it to reach a terminal. The injected wait stands in for the
        # in-screen __run supervisor draining + recording the stop (Section 4.2).
        def _fake_wait(*, name, registry, timeout_seconds):
            inner = registry.read_state(name)
            inner.state = SUPERVISE_STATE_STOPPED
            inner.exit_reason = "graceful-stop"
            registry.write_state(inner)
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
        assert after.state == "stopped"
        assert after.exit_reason == "graceful-stop"
        # The stop.request control file the __run loop polls was written.
        assert supervise_stop_request_path(tmp_path, "nightly").exists()

    def test_stop_hard_skips_drain(self, tmp_path) -> None:
        import os as _os
        from unittest.mock import patch

        from devbench import cli
        from devbench.constants import SUPERVISE_STATE_RUNNING
        from devbench.supervise import SuperviseRegistry, new_session_state, supervise_stop_request_path

        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name="n",
            pid=_os.getpid(),
            screen_name="devbench-supervise-n",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        reg.write_state(st)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-n"}),
            patch("devbench.cli._supervise_screen_quit") as quit_mock,
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        ):
            rc = cli.cmd_supervise("stop", "--name", "n", "--hard")
        assert rc == 0
        after = reg.read_state("n")
        assert after is not None
        assert after.state == "stopped"
        assert after.exit_reason == "hard-stop"
        # --hard tears the screen down (and never writes the graceful stop.request).
        assert quit_mock.called
        assert not supervise_stop_request_path(tmp_path, "n").exists()

    def test_restart_propagates_stop_failure(self, tmp_path) -> None:
        import os as _os
        from unittest.mock import MagicMock, patch

        from devbench import cli
        from devbench.constants import SUPERVISE_STATE_RUNNING
        from devbench.supervise import SuperviseRegistry, new_session_state

        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name="nightly",
            pid=_os.getpid(),
            screen_name="devbench-supervise-nightly",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        reg.write_state(st)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._cmd_supervise_stop", MagicMock(return_value=2)),
            patch("devbench.cli._cmd_supervise_start", MagicMock(return_value=0)) as start_mock,
        ):
            rc = cli.cmd_supervise("restart", "--name", "nightly")
        # A failed stop short-circuits: start must NOT run.
        assert rc == 2
        assert not start_mock.called
