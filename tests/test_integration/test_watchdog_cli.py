"""End-to-end tests for ``devbench watchdog``.

Spawns the subcommand as a subprocess against a tmp workspace with a
hand-crafted BACKLOG.md and orchestrator.log. Exercises the full
argument-parsing + flag-file-writing + exit-code contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_BACKLOG_IN_PROGRESS = """| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|-------------|------|-----------|
| E1-T1 | stuck task | Task | in-progress | None | r/r | `backlog/E1/t1.md` |
"""

_BACKLOG_HEALTHY = """| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|-------------|------|-----------|
| E1-T1 | queued | Task | in-queue | None | r/r | `backlog/E1/t1.md` |
"""


def _run_watchdog(
    workspace: Path,
    log_file: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace)
    env["DEVBENCH_CLAUDE_MODEL"] = env.get("DEVBENCH_CLAUDE_MODEL", "test-model")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "devbench.cli",
            "watchdog",
            "--log-file",
            str(log_file),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


def _write_log(log_file: Path, last_ts: datetime) -> None:
    log_file.write_text(
        f"{last_ts.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.cli] INFO ok\n",
        encoding="utf-8",
    )


@pytest.mark.functional
class TestCmdWatchdog:
    def test_healthy_backlog_writes_no_flag(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_HEALTHY)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC))
        result = _run_watchdog(tmp_path, log)
        assert result.returncode == 0
        assert result.stdout == ""
        assert not (tmp_path / ".devbench" / "needs-restart.flag").exists()

    def test_stuck_backlog_writes_flag(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_IN_PROGRESS)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC) - timedelta(minutes=10))
        result = _run_watchdog(tmp_path, log, "--idle-minutes", "1")
        assert result.returncode == 0
        flag = tmp_path / ".devbench" / "needs-restart.flag"
        assert flag.is_file()
        payload = json.loads(flag.read_text())
        assert payload["task_id"] == "E1-T1"
        assert payload["orchestrator_idle_seconds"] >= 600
        assert payload["idle_threshold_seconds"] == 60

    def test_print_if_stuck_emits_status_line(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_IN_PROGRESS)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC) - timedelta(minutes=10))
        result = _run_watchdog(tmp_path, log, "--idle-minutes", "1", "--print-if-stuck")
        assert result.returncode == 0
        assert "STUCK" in result.stdout
        assert "E1-T1" in result.stdout

    def test_print_if_stuck_silent_when_healthy(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_HEALTHY)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC))
        result = _run_watchdog(tmp_path, log, "--print-if-stuck")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_custom_flag_file_path(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_IN_PROGRESS)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC) - timedelta(minutes=10))
        custom_flag = tmp_path / "elsewhere" / "hung.json"
        result = _run_watchdog(
            tmp_path,
            log,
            "--idle-minutes",
            "1",
            "--flag-file",
            str(custom_flag),
        )
        assert result.returncode == 0
        assert custom_flag.is_file()
        assert not (tmp_path / ".devbench" / "needs-restart.flag").exists()

    def test_invalid_idle_minutes_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_HEALTHY)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC))
        result = _run_watchdog(tmp_path, log, "--idle-minutes", "abc")
        assert result.returncode == 2
        assert "must be an integer" in result.stderr

    def test_idle_minutes_below_one_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_HEALTHY)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC))
        result = _run_watchdog(tmp_path, log, "--idle-minutes", "0")
        assert result.returncode == 2
        assert ">= 1" in result.stderr

    def test_unknown_flag_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_HEALTHY)
        log = tmp_path / "orchestrator.log"
        _write_log(log, datetime.now(UTC))
        result = _run_watchdog(tmp_path, log, "--unknown")
        assert result.returncode == 2
        assert "unknown flag" in result.stderr
