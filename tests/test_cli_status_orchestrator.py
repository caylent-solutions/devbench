"""Tests for orchestrator state rendering in cmd_status (issue #252).

Covers AC-252-1 and AC-252a-1: the three exact orchestrator status lines
rendered from the canonical PID file, using real PIDs (no os.kill patching).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.cli import OrchestratorState, _resolve_orchestrator_state, cmd_status
from devbench.instances import pid_file_path


def _write_pid_file(workspace: Path, pid: int, mode: str = "daemon", started_at: str = "") -> Path:
    """Write a synthetic PID file under workspace/.devbench/."""
    pid_path = pid_file_path(workspace)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    ws_name = workspace.name
    instance_id = f"{ws_name}-{pid:04d}"[-len(ws_name) - 5 :]
    payload = {
        "instance_id": instance_id,
        "pid": pid,
        "workspace": str(workspace),
        "workspace_name": ws_name,
        "session": "test-session",
        "mode": mode,
        "started_at": started_at,
        "model": "test-model",
        "host": "testhost",
    }
    pid_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return pid_path


@pytest.mark.unit
class TestOrchestratorStateDataclass:
    """OrchestratorState is importable and has the expected fields."""

    def test_running_state(self) -> None:
        state = OrchestratorState(
            status="running",
            mode="daemon",
            pid=1234,
            instance_id="ws-1234",
            uptime="00:05:00",
            detail="",
        )
        assert state.status == "running"
        assert state.mode == "daemon"
        assert state.pid == 1234
        assert state.instance_id == "ws-1234"
        assert state.uptime == "00:05:00"

    def test_stopped_state(self) -> None:
        state = OrchestratorState(
            status="stopped",
            mode=None,
            pid=None,
            instance_id=None,
            uptime=None,
            detail="no pid file",
        )
        assert state.status == "stopped"
        assert state.detail == "no pid file"


@pytest.mark.unit
class TestResolveOrchestratorStateNoPidFile:
    """_resolve_orchestrator_state returns stopped (no pid file) when PID file is absent."""

    def test_missing_pid_file_returns_no_pid_state(self, tmp_path: Path) -> None:
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "stopped"
        assert state.detail == "no pid file"
        assert state.pid is None
        assert state.mode is None
        assert state.instance_id is None
        assert state.uptime is None

    def test_pid_file_directory_not_present(self, tmp_path: Path) -> None:
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "stopped"
        assert state.detail == "no pid file"


@pytest.mark.unit
class TestResolveOrchestratorStateStalePid:
    """_resolve_orchestrator_state returns stopped (stale pid file) for dead PIDs."""

    def test_dead_pid_returns_stale_state(self, tmp_path: Path) -> None:
        dead_pid = 2**31 - 1
        _write_pid_file(tmp_path, dead_pid, mode="daemon")
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "stopped"
        assert state.detail == "stale pid file"
        assert state.pid is None
        assert state.mode is None
        assert state.instance_id is None
        assert state.uptime is None


@pytest.mark.unit
class TestResolveOrchestratorStateLivePid:
    """_resolve_orchestrator_state returns running for a live PID."""

    def test_live_pid_returns_running_state(self, tmp_path: Path) -> None:
        live_pid = os.getpid()
        started = datetime.now(tz=UTC) - timedelta(minutes=5)
        started_at = started.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_pid_file(tmp_path, live_pid, mode="daemon", started_at=started_at)
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "running"
        assert state.mode == "daemon"
        assert state.pid == live_pid
        assert state.instance_id is not None
        assert state.uptime is not None

    def test_live_pid_uptime_format_under_24h(self, tmp_path: Path) -> None:
        live_pid = os.getpid()
        started = datetime.now(tz=UTC) - timedelta(hours=1, minutes=30, seconds=15)
        started_at = started.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_pid_file(tmp_path, live_pid, mode="foreground", started_at=started_at)
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "running"
        assert state.uptime is not None
        parts = state.uptime.split(":")
        assert len(parts) == 3, f"Expected HH:MM:SS, got {state.uptime!r}"

    def test_live_pid_uptime_unknown_when_started_at_empty(self, tmp_path: Path) -> None:
        live_pid = os.getpid()
        _write_pid_file(tmp_path, live_pid, mode="daemon", started_at="")
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "running"
        assert state.uptime == "unknown"

    def test_live_pid_over_24h_uptime_format(self, tmp_path: Path) -> None:
        live_pid = os.getpid()
        started = datetime.now(tz=UTC) - timedelta(days=2, hours=3, minutes=5, seconds=10)
        started_at = started.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_pid_file(tmp_path, live_pid, mode="daemon", started_at=started_at)
        state = _resolve_orchestrator_state(tmp_path)
        assert state.status == "running"
        assert state.uptime is not None
        assert "d " in state.uptime, f"Expected Dd HH:MM:SS format for >24h, got {state.uptime!r}"
        day_part, time_part = state.uptime.split("d ", 1)
        assert day_part.isdigit()
        time_parts = time_part.split(":")
        assert len(time_parts) == 3, f"Expected HH:MM:SS after 'd ', got {time_part!r}"


@pytest.mark.unit
class TestCmdStatusOrchestratorLines:
    """cmd_status renders exact orchestrator status lines per spec AC-252-1."""

    def _minimal_mock_parser(self) -> object:
        from unittest.mock import MagicMock

        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit = WorkUnit(
            id="E0-F0-S0-T1",
            title="Stub task",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("stub.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True
        mock_parser.get_blocked_units.return_value = []
        return mock_parser

    def test_running_line_format(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        live_pid = os.getpid()
        started = datetime.now(tz=UTC) - timedelta(minutes=12, seconds=44)
        started_at = started.strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_pid_file(tmp_path, live_pid, mode="daemon", started_at=started_at)

        mock_parser = self._minimal_mock_parser()
        running_state = OrchestratorState(
            status="running",
            mode="daemon",
            pid=live_pid,
            instance_id="ws-test-1234",
            uptime="00:12:44",
            detail="",
        )
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_orchestrator_state", return_value=running_state),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        assert f"Orchestrator: running (daemon)  pid {live_pid}  instance ws-test-1234  uptime 00:12:44" in out

    def test_stale_pid_file_line_format(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        dead_pid = 2**31 - 1
        _write_pid_file(tmp_path, dead_pid)

        mock_parser = self._minimal_mock_parser()
        stale_state = OrchestratorState(
            status="stopped",
            mode=None,
            pid=None,
            instance_id=None,
            uptime=None,
            detail="stale pid file",
        )
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_orchestrator_state", return_value=stale_state),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        assert "Orchestrator: stopped (stale pid file)" in out

    def test_no_pid_file_line_format(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_parser = self._minimal_mock_parser()
        no_pid_state = OrchestratorState(
            status="stopped",
            mode=None,
            pid=None,
            instance_id=None,
            uptime=None,
            detail="no pid file",
        )
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_orchestrator_state", return_value=no_pid_state),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        assert "Orchestrator: stopped (no pid file)" in out

    def test_exit_code_unchanged(
        self,
        tmp_path: Path,
    ) -> None:
        """cmd_status still returns 0 regardless of orchestrator state."""
        mock_parser = self._minimal_mock_parser()
        state = OrchestratorState(
            status="stopped",
            mode=None,
            pid=None,
            instance_id=None,
            uptime=None,
            detail="no pid file",
        )
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_orchestrator_state", return_value=state),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cmd_status()

        assert result == 0


@pytest.mark.unit
class TestOrchestratorStateNotDiscoverInstances:
    """AC-252a-1: state resolution uses the canonical PID file, not discover_instances."""

    def test_resolve_does_not_call_discover_instances(self, tmp_path: Path) -> None:
        """_resolve_orchestrator_state must not invoke discover_instances."""
        with patch("devbench.instances.discover_instances") as mock_discover:
            _resolve_orchestrator_state(tmp_path)
        mock_discover.assert_not_called()
