"""Tests for the #209 lifecycle CLI commands (instances, stop-instance, tail, restart).

Daemonisation (``_daemonize_to_background``) is not tested in-process — it
double-forks and detaches; instead it's exercised via the lifecycle
integration smoke (manual + the orchestrator restart at the end of this
PR's rollout).  These tests cover everything else:

- argv parsers (``_parse_stop_instance_args``, ``_parse_tail_args``)
- ``_wait_for_pid_exit`` polling helper
- ``cmd_instances`` table / JSON output
- ``cmd_stop_instance`` resolve + signal flow (signal mocked)
- ``cmd_tail`` resolve + log-path resolution + subprocess invocation (mocked)
- ``cmd_restart`` resolution + delegation to stop + subprocess.run (mocked)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.instances import write_pid_file


@pytest.mark.unit
class TestParseStopInstanceArgs:
    def test_target_only(self) -> None:
        target, timeout, force, rc = cli._parse_stop_instance_args(("kanon-2281",))
        assert target == "kanon-2281"
        assert timeout == 30
        assert force is False
        assert rc == 0

    def test_with_timeout_and_force(self) -> None:
        target, timeout, force, rc = cli._parse_stop_instance_args(("kanon-2281", "--timeout", "60", "--force"))
        assert target == "kanon-2281"
        assert timeout == 60
        assert force is True
        assert rc == 0

    def test_missing_timeout_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_stop_instance_args(("--timeout",))
        assert rc == 2
        assert "--timeout requires a value" in capsys.readouterr().err

    def test_non_integer_timeout(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_stop_instance_args(("kanon-2281", "--timeout", "abc"))
        assert rc == 2
        assert "must be an integer" in capsys.readouterr().err

    def test_unknown_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_stop_instance_args(("--zarble",))
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err

    def test_duplicate_target_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_stop_instance_args(("a", "b"))
        assert rc == 2
        assert "single instance id" in capsys.readouterr().err


@pytest.mark.unit
class TestParseTailArgs:
    def test_target_only(self) -> None:
        target, follow, lines, rc = cli._parse_tail_args(("kanon-2281",))
        assert target == "kanon-2281"
        assert follow is False
        assert lines == 50
        assert rc == 0

    def test_short_and_long_flags(self) -> None:
        target, follow, lines, rc = cli._parse_tail_args(("kanon-2281", "-f", "-n", "200"))
        assert follow is True
        assert lines == 200
        assert rc == 0

    def test_unknown_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_tail_args(("--zarble",))
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err

    def test_non_integer_lines(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_tail_args(("kanon-2281", "--lines", "abc"))
        assert rc == 2
        assert "must be an integer" in capsys.readouterr().err

    def test_missing_lines_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_tail_args(("--lines",))
        assert rc == 2

    def test_duplicate_target_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, _, _, rc = cli._parse_tail_args(("a", "b"))
        assert rc == 2


@pytest.mark.unit
class TestWaitForPidExit:
    def test_returns_true_when_pid_already_gone(self) -> None:
        assert cli._wait_for_pid_exit(2**31 - 1, 1) is True

    def test_returns_false_when_pid_still_alive_at_deadline(self) -> None:
        assert cli._wait_for_pid_exit(os.getpid(), 0) is False


@pytest.mark.unit
class TestCmdInstances:
    def test_empty_state_prints_no_instances(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        rc = cli.cmd_instances()
        assert rc == 0
        assert "no devbench orchestrator instances running" in capsys.readouterr().out

    def test_lists_one_live_instance_table_form(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="us.anthropic.x")
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        rc = cli.cmd_instances()
        assert rc == 0
        out = capsys.readouterr().out
        assert "INSTANCE_ID" in out
        assert "alpha-" in out

    def test_json_form(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="x")
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        rc = cli.cmd_instances("--json")
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, list) and len(payload) == 1
        assert payload[0]["workspace_name"] == "alpha"

    def test_unknown_flag_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_instances("--zarble")
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err


@pytest.mark.unit
class TestCmdStopInstance:
    def test_missing_target_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_stop_instance()
        assert rc == 2
        assert "requires an instance id" in capsys.readouterr().err

    def test_unknown_instance_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        rc = cli.cmd_stop_instance("nonexistent-9999")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_known_instance_calls_send_signal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="x")
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        with patch.object(cli, "_send_signal_and_wait", return_value=0) as send:
            rc = cli.cmd_stop_instance(str(os.getpid()))
        assert rc == 0
        send.assert_called_once()


@pytest.mark.unit
class TestSendSignalAndWait:
    def test_sigterm_error_returns_1(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        class FakeInst:
            pid = 99999
            instance_id = "fake-9999"

        def _kill(_pid: int, _sig: int) -> None:
            raise ProcessLookupError("no such process")

        monkeypatch.setattr(os, "kill", _kill)
        rc = cli._send_signal_and_wait(FakeInst(), timeout=1, force=False)
        assert rc == 1
        assert "SIGTERM" in capsys.readouterr().err

    def test_exits_cleanly_when_pid_dies_within_timeout(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class FakeInst:
            pid = 99999
            instance_id = "fake-9999"

        sent = {"count": 0}

        def _kill(_pid: int, sig: int) -> None:
            if sig == 0:
                raise OSError("not alive")
            sent["count"] += 1

        monkeypatch.setattr(os, "kill", _kill)
        monkeypatch.setattr(cli, "_wait_for_pid_exit", lambda _pid, _timeout: True)
        rc = cli._send_signal_and_wait(FakeInst(), timeout=1, force=False)
        assert rc == 0
        assert "stopped instance" in capsys.readouterr().out


@pytest.mark.unit
class TestCmdTail:
    def test_missing_target_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_tail()
        assert rc == 2
        assert "requires an instance id" in capsys.readouterr().err

    def test_unknown_instance_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        rc = cli.cmd_tail("nonexistent-9999")
        assert rc == 1

    def test_known_instance_invokes_tail_subprocess(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        (ws / "logs").mkdir()
        (ws / "logs" / "orchestrator.log").write_text("line1\nline2\n", encoding="utf-8")
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="x")
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))

        captured: dict = {}

        class FakeCompleted:
            returncode = 0

        def _fake_run(cmd, check):  # type: ignore[no-untyped-def]
            captured["cmd"] = cmd
            captured["check"] = check
            return FakeCompleted()

        import subprocess

        monkeypatch.setattr(subprocess, "run", _fake_run)
        rc = cli.cmd_tail(str(os.getpid()))
        assert rc == 0
        assert captured["cmd"][0] == "tail"
        assert str(ws / "logs" / "orchestrator.log") in captured["cmd"]


@pytest.mark.unit
class TestCmdRestart:
    def test_no_args_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_restart()
        assert rc == 2
        assert "requires an instance id" in capsys.readouterr().err

    def test_unknown_instance_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        rc = cli.cmd_restart("nonexistent-9999")
        assert rc == 1

    def test_stop_phase_failure_aborts_restart(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="x")
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        with patch.object(cli, "cmd_stop_instance", return_value=1):
            rc = cli.cmd_restart(str(os.getpid()))
        assert rc == 1
        assert "stop phase exited 1" in capsys.readouterr().err

    def test_relaunch_after_clean_stop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import subprocess

        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="x")
        monkeypatch.setenv("DEVBENCH_INSTANCE_SEARCH_ROOTS", str(tmp_path))
        captured: dict = {}

        class FakeCompleted:
            returncode = 0

        def _fake_run(cmd, env, check, cwd):  # type: ignore[no-untyped-def]
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return FakeCompleted()

        with (
            patch.object(cli, "cmd_stop_instance", return_value=0),
            patch.object(subprocess, "run", _fake_run),
        ):
            rc = cli.cmd_restart(str(os.getpid()))
        assert rc == 0
        assert "--daemon" in captured["cmd"]
        assert captured["cwd"] == str(ws)


@pytest.mark.unit
class TestSetupDaemonAndPidFile:
    """Cover the foreground-mode path; daemon path is integration-tested.

    The foreground branch (no fork) is what writes the PID file when
    operators run plain ``devbench start`` -- testing it pins both the
    file write AND the WARN-on-failure fallback.
    """

    def test_foreground_writes_pid_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setenv("DEVBENCH_CLAUDE_MODEL", "test-model")
        parsed = cli._CmdStartArgs(daemon=False, name="default")
        cli._setup_daemon_and_pid_file(parsed)
        pid_file = tmp_path / ".devbench" / "orchestrator.pid"
        assert pid_file.is_file()
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["mode"] == "foreground"
        assert payload["model"] == "test-model"

    def test_pid_file_write_failure_logs_warn_and_continues(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)

        def _boom(*_a, **_kw):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        monkeypatch.setattr("devbench.instances.write_pid_file", _boom)
        parsed = cli._CmdStartArgs(daemon=False, name="default")
        cli._setup_daemon_and_pid_file(parsed)
        assert "failed to write orchestrator PID file" in capsys.readouterr().err
