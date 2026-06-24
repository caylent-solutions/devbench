"""Read-only status/info helpers + verbs (FR-9, FR-10, FR-11, Section 4.4/4.5).

P4 lands the read-only observation surface. The pure formatting/reconcile helpers
live in :mod:`devbench.supervise` so they are unit-testable without a live screen;
the CLI ``status``/``info`` verbs wire them onto the :class:`SuperviseRegistry`.

- ``status`` (FR-9/FR-10): per-session + all-session lines; ``quota-waiting``
  surfaces ``expected-resume`` + ``resumes-used=<n>/<cap>``; every line carries
  ``billing-channel: subscription``.
- ``info`` (FR-11): join ``screen -ls`` with the registry; an orphan screen (no
  registry entry) is ``state=unknown``; a registry entry with no screen is
  ``state=stale``; the ATTACH column is the exact ``supervise attach --name N``.
"""

from __future__ import annotations

import os as _os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from devbench import cli
from devbench.constants import (
    SUPERVISE_BILLING_CHANNEL,
    SUPERVISE_STATE_QUOTA_WAITING,
    SUPERVISE_STATE_RUNNING,
)
from devbench.supervise import (
    InfoRow,
    SuperviseRegistry,
    SuperviseSessionState,
    format_status_line,
    new_session_state,
    parse_screen_ls,
    reconcile_info_rows,
)


@pytest.mark.unit
class TestFormatStatusLine:
    """A single-session status line surfaces the spec fields (FR-9, FR-10)."""

    def _running(self) -> SuperviseSessionState:
        st = new_session_state(
            name="nightly",
            pid=4321,
            screen_name="devbench-supervise-nightly",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        st.last_activity = datetime(2026, 6, 15, 3, 14, 7, tzinfo=UTC)
        st.claude_session_id = "018f-a1"
        return st

    def test_running_line_carries_billing_channel(self) -> None:
        line = format_status_line(self._running(), max_resumes=1000, in_progress="E10-F1-S1-T8")
        assert "name=nightly" in line
        assert f"state={SUPERVISE_STATE_RUNNING}" in line
        assert f"billing-channel={SUPERVISE_BILLING_CHANNEL}" in line

    def test_running_line_shows_in_progress_and_activity(self) -> None:
        line = format_status_line(self._running(), max_resumes=1000, in_progress="E10-F1-S1-T8")
        assert "in-progress=E10-F1-S1-T8" in line
        assert "last-activity=2026-06-15T03:14:07" in line

    def test_running_line_no_in_progress_renders_none(self) -> None:
        line = format_status_line(self._running(), max_resumes=1000, in_progress=None)
        assert "in-progress=(none)" in line

    def test_quota_waiting_surfaces_expected_resume_and_resumes_used(self) -> None:
        st = new_session_state(
            name="nightly",
            pid=4321,
            screen_name="devbench-supervise-nightly",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_QUOTA_WAITING
        st.expected_resume = datetime(2026, 6, 15, 8, 0, 0, tzinfo=UTC)
        st.resumes_used = 2
        line = format_status_line(st, max_resumes=1000, in_progress=None)
        assert f"state={SUPERVISE_STATE_QUOTA_WAITING}" in line
        assert "expected-resume=2026-06-15T08:00:00" in line
        assert "resumes-used=2/1000" in line

    def test_non_quota_state_omits_expected_resume(self) -> None:
        line = format_status_line(self._running(), max_resumes=1000, in_progress=None)
        assert "expected-resume" not in line
        assert "resumes-used" not in line

    def test_stopped_state_surfaces_exit_reason(self) -> None:
        st = self._running()
        st.state = "stopped"
        st.exit_reason = "graceful-stop"
        line = format_status_line(st, max_resumes=1000, in_progress=None)
        assert "exit-reason=graceful-stop" in line


@pytest.mark.unit
class TestParseScreenLs:
    """``screen -ls`` output is parsed into the set of session names (FR-11)."""

    def test_parses_session_names(self) -> None:
        output = (
            "There are screens on:\n"
            "\t44310.devbench-supervise-fast\t(Detached)\n"
            "\t44755.devbench-supervise-bulk\t(Detached)\n"
            "2 Sockets in /run/screen/S-vscode.\n"
        )
        names = parse_screen_ls(output)
        assert names == {"devbench-supervise-fast", "devbench-supervise-bulk"}

    def test_no_screens_yields_empty(self) -> None:
        assert parse_screen_ls("No Sockets found in /run/screen/S-vscode.\n") == set()

    def test_ignores_non_screen_lines(self) -> None:
        output = "There is a screen on:\n\t9.something\t(Attached)\nblah blah\n1 Socket\n"
        assert parse_screen_ls(output) == {"something"}


@pytest.mark.unit
class TestReconcileInfoRows:
    """info reconciles registry sessions with live screens (FR-11, Section 4.5)."""

    def _state(self, name: str, state: str = SUPERVISE_STATE_RUNNING) -> SuperviseSessionState:
        st = new_session_state(
            name=name,
            pid=1,
            screen_name=f"devbench-supervise-{name}",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = state
        st.claude_session_id = f"{name}-sess"
        return st

    def test_running_session_with_live_screen(self) -> None:
        rows = reconcile_info_rows(
            sessions=[self._state("fast")],
            screen_names={"devbench-supervise-fast"},
            prefix="devbench-supervise-",
        )
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, InfoRow)
        assert row.screen == "devbench-supervise-fast"
        assert row.name == "fast"
        assert row.state == SUPERVISE_STATE_RUNNING
        assert row.attach == "supervise attach --name fast"
        assert row.billing == SUPERVISE_BILLING_CHANNEL

    def test_registry_entry_with_no_screen_is_stale(self) -> None:
        rows = reconcile_info_rows(
            sessions=[self._state("ghost")],
            screen_names=set(),
            prefix="devbench-supervise-",
        )
        assert rows[0].state == "stale"

    def test_orphan_screen_with_no_registry_entry_is_unknown(self) -> None:
        rows = reconcile_info_rows(
            sessions=[],
            screen_names={"devbench-supervise-orphan"},
            prefix="devbench-supervise-",
        )
        assert len(rows) == 1
        assert rows[0].name == "orphan"
        assert rows[0].state == "unknown"
        assert rows[0].attach == "supervise attach --name orphan"

    def test_screen_outside_prefix_is_ignored(self) -> None:
        rows = reconcile_info_rows(
            sessions=[],
            screen_names={"some-other-screen"},
            prefix="devbench-supervise-",
        )
        assert rows == []

    def test_rows_sorted_by_name(self) -> None:
        rows = reconcile_info_rows(
            sessions=[self._state("bulk"), self._state("fast")],
            screen_names={"devbench-supervise-bulk", "devbench-supervise-fast"},
            prefix="devbench-supervise-",
        )
        assert [r.name for r in rows] == ["bulk", "fast"]


@pytest.mark.unit
class TestSuperviseStatusCli:
    """`supervise status` reads the registry and prints per/all-session lines."""

    def _seed(self, tmp_path: Path, name: str, state: str = SUPERVISE_STATE_RUNNING):
        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name=name,
            pid=_os.getpid(),
            screen_name=f"devbench-supervise-{name}",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = state
        reg.write_state(st)
        return reg

    def test_status_named_session(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        self._seed(tmp_path, "nightly")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_in_progress_id", return_value=None),
        ):
            rc = cli.cmd_supervise("status", "--name", "nightly")
        assert rc == 0
        out = capsys.readouterr().out
        assert "name=nightly" in out
        assert f"billing-channel={SUPERVISE_BILLING_CHANNEL}" in out

    def test_status_unknown_name_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("status", "--name", "ghost")
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err

    def test_status_all_sessions(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        self._seed(tmp_path, "fast")
        self._seed(tmp_path, "bulk")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_in_progress_id", return_value=None),
        ):
            rc = cli.cmd_supervise("status")
        assert rc == 0
        out = capsys.readouterr().out
        assert "name=fast" in out
        assert "name=bulk" in out

    def test_status_no_sessions(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_supervise("status")
        assert rc == 0
        assert "No supervise sessions." in capsys.readouterr().out


@pytest.mark.unit
class TestSuperviseInfoCli:
    """`supervise info` joins screen -ls with the registry (FR-11)."""

    def _seed(self, tmp_path: Path, name: str, state: str = SUPERVISE_STATE_RUNNING):
        reg = SuperviseRegistry(tmp_path)
        st = new_session_state(
            name=name,
            pid=_os.getpid(),
            screen_name=f"devbench-supervise-{name}",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="t",
        )
        st.state = state
        reg.write_state(st)
        return reg

    def test_info_lists_session_with_attach_command(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        self._seed(tmp_path, "fast")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value={"devbench-supervise-fast"}),
        ):
            rc = cli.cmd_supervise("info")
        assert rc == 0
        out = capsys.readouterr().out
        assert "SCREEN" in out and "ATTACH" in out
        assert "devbench-supervise-fast" in out
        assert "supervise attach --name fast" in out

    def test_info_no_sessions(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value=set()),
        ):
            rc = cli.cmd_supervise("info")
        assert rc == 0
        assert "No supervise screens." in capsys.readouterr().out

    def test_info_marks_stale_when_screen_absent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import patch

        self._seed(tmp_path, "ghost")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", return_value=set()),
        ):
            rc = cli.cmd_supervise("info")
        assert rc == 0
        assert "stale" in capsys.readouterr().out

    def test_info_degrades_with_note_when_screen_unavailable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import patch

        self._seed(tmp_path, "ghost")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_live_screen_names", side_effect=cli.SuperviseError("screen -ls failed")),
        ):
            rc = cli.cmd_supervise("info")
        assert rc == 0
        out = capsys.readouterr().out
        assert "screen list unavailable" in out
        assert "ghost" in out


@pytest.mark.unit
class TestSuperviseLiveScreenNamesSeam:
    """_supervise_live_screen_names runs `screen -ls`; fails fast on a real error."""

    def test_parses_live_screens(self) -> None:
        import subprocess
        from unittest.mock import patch

        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="There are screens on:\n\t9.devbench-supervise-x\t(Detached)\n1 Socket\n",
            stderr="",
        )
        with (
            patch("devbench.cli.shutil.which", return_value="/usr/bin/screen"),
            patch("devbench.cli.subprocess.run", return_value=completed),
        ):
            assert cli._supervise_live_screen_names() == {"devbench-supervise-x"}

    def test_no_sockets_returncode_1_is_not_a_failure(self) -> None:
        import subprocess
        from unittest.mock import patch

        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="No Sockets found.\n")
        with (
            patch("devbench.cli.shutil.which", return_value="/usr/bin/screen"),
            patch("devbench.cli.subprocess.run", return_value=completed),
        ):
            assert cli._supervise_live_screen_names() == set()

    def test_screen_absent_fails_fast(self) -> None:
        from unittest.mock import patch

        with patch("devbench.cli.shutil.which", return_value=None):
            with pytest.raises(cli.SuperviseError, match="screen"):
                cli._supervise_live_screen_names()

    def test_subprocess_error_fails_fast(self) -> None:
        from unittest.mock import patch

        with (
            patch("devbench.cli.shutil.which", return_value="/usr/bin/screen"),
            patch("devbench.cli.subprocess.run", side_effect=OSError("boom")),
        ):
            with pytest.raises(cli.SuperviseError, match="screen -ls"):
                cli._supervise_live_screen_names()


@pytest.mark.unit
class TestSuperviseInProgressIdSeam:
    """_supervise_in_progress_id reads the single in-progress WU, or None (FR-9)."""

    def test_returns_first_in_progress_id(self) -> None:
        from unittest.mock import MagicMock, patch

        from devbench.backlog.work_unit import WorkUnitStatus

        done = MagicMock(id="E1-F1-S1-T1", status=WorkUnitStatus.DONE)
        active = MagicMock(id="E1-F1-S1-T2", status=WorkUnitStatus.IN_PROGRESS)
        parser = MagicMock()
        parser.parse_index.return_value = [done, active]
        with patch("devbench.cli.BacklogParser", return_value=parser):
            assert cli._supervise_in_progress_id() == "E1-F1-S1-T2"

    def test_no_in_progress_returns_none(self) -> None:
        from unittest.mock import MagicMock, patch

        from devbench.backlog.work_unit import WorkUnitStatus

        done = MagicMock(id="E1-F1-S1-T1", status=WorkUnitStatus.DONE)
        parser = MagicMock()
        parser.parse_index.return_value = [done]
        with patch("devbench.cli.BacklogParser", return_value=parser):
            assert cli._supervise_in_progress_id() is None

    def test_unparseable_backlog_returns_none(self) -> None:
        from unittest.mock import MagicMock, patch

        parser = MagicMock()
        parser.parse_index.side_effect = ValueError("bad backlog")
        with patch("devbench.cli.BacklogParser", return_value=parser):
            assert cli._supervise_in_progress_id() is None
