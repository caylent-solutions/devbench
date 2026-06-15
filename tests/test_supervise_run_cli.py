"""cmd_supervise __run + launch-screen + version-record wiring (FR-5..7, FR-25).

Exercises the in-screen ``__run`` supervisor body and the screen-launch /
version-record helpers with ``pexpect.spawn`` and ``subprocess.run`` mocked, so
the launch->ready->kickoff->running pipeline is driven without a real
``claude``/``screen``. Complements the unit-level core tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench import cli
from devbench.config_loader import SuperviseConfig
from devbench.supervise import SuperviseError, SuperviseRegistry


def _patch_run(tmp_path: Path, child: FakePexpectChild):
    return [
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
        patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        patch("devbench.cli._resolve_plugin_path", return_value=tmp_path / "plugin"),
        patch("devbench.cli.pexpect.spawn", return_value=child),
        patch("devbench.cli._record_tool_version", return_value="claude 1.2.3"),
    ]


def _ctx(patches: list):
    import contextlib

    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


@pytest.mark.unit
class TestSuperviseRunReachesRunning:
    """FR-7/FR-8: __run drives the spawned child to running + records the registry."""

    def test_run_records_running(self, tmp_path: Path) -> None:
        child = FakePexpectChild(
            [
                _ScriptStep(emit="> "),  # ready prompt
                _ScriptStep(emit="esc to interrupt", on_send="orchestrate"),  # ack
            ]
        )
        with _ctx(_patch_run(tmp_path, child)):
            rc = cli.cmd_supervise("__run", "--name", "nightly", "--model", "claude-opus-4-8")
        assert rc == 0
        state = SuperviseRegistry(tmp_path).read_state("nightly")
        assert state is not None
        assert state.state == "running"
        assert state.claude_version == "claude 1.2.3"
        assert state.claude_path == "/usr/bin/claude"
        assert child.sent == ["/devbench-orchestrate:orchestrate"]


@pytest.mark.unit
class TestSuperviseRunFaultsOnReadyTimeout:
    """FR-7/Section 4.6: a ready-prompt timeout faults the session (non-zero)."""

    def test_ready_timeout_faults(self, tmp_path: Path) -> None:
        child = FakePexpectChild([])  # never becomes ready
        with _ctx(_patch_run(tmp_path, child)):
            rc = cli.cmd_supervise("__run", "--name", "nightly", "--model", "claude-opus-4-8")
        assert rc == 1
        state = SuperviseRegistry(tmp_path).read_state("nightly")
        assert state is not None
        assert state.state == "faulted"
        assert state.exit_reason == "ready-prompt-timeout"
        assert child.terminated is True


@pytest.mark.unit
class TestSuperviseRunClaudeMissing:
    """FR-25: __run fails fast (exit 2) when claude is not on PATH."""

    def test_claude_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        patches = [
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
            patch("devbench.cli.shutil.which", lambda name: None),
        ]
        with _ctx(patches):
            rc = cli.cmd_supervise("__run", "--name", "n", "--model", "opus")
        assert rc == 2
        assert "claude" in capsys.readouterr().err


@pytest.mark.unit
class TestLaunchScreen:
    """FR-6: _supervise_launch_screen invokes screen -dmS; fails fast non-zero."""

    def test_launch_success(self, tmp_path: Path) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=completed) as run:
            cli._supervise_launch_screen(
                name="n",
                screen_name="devbench-supervise-n",
                env={"PATH": "/usr/bin"},
                run_argv=["uv", "run", "devbench", "supervise", "__run", "--name", "n"],
                screen_path="/usr/bin/screen",
            )
        called = run.call_args
        cmd = called.args[0]
        assert cmd[0] == "/usr/bin/screen"
        assert cmd[1] == "-dmS"
        assert cmd[2] == "devbench-supervise-n"
        assert "__run" in cmd

    def test_launch_failure_raises(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        with (
            patch("devbench.cli.subprocess.run", return_value=completed),
            pytest.raises(SuperviseError, match="failed to create screen"),
        ):
            cli._supervise_launch_screen(
                name="n",
                screen_name="devbench-supervise-n",
                env={},
                run_argv=["uv", "run"],
                screen_path="/usr/bin/screen",
            )


@pytest.mark.unit
class TestRecordToolVersion:
    """FR-25: _record_tool_version returns first line; degrades gracefully."""

    def test_records_version(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="claude 1.2.3\nmore", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=completed):
            assert cli._record_tool_version("/usr/bin/claude") == "claude 1.2.3"

    def test_nonzero_returns_none(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="nope")
        with patch("devbench.cli.subprocess.run", return_value=completed):
            assert cli._record_tool_version("/usr/bin/claude") is None

    def test_oserror_returns_none(self) -> None:
        with patch("devbench.cli.subprocess.run", side_effect=OSError("no such file")):
            assert cli._record_tool_version("/usr/bin/claude") is None

    def test_empty_output_returns_none(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=completed):
            assert cli._record_tool_version("/usr/bin/claude") is None


@pytest.mark.unit
class TestStartRunningConfirmation:
    """Section 4.1 step 4: start surfaces a launch that never reached running."""

    def _common(self, tmp_path: Path, creds: Path):
        return [
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
            patch("devbench.cli._resolve_orchestrator_model", return_value="claude-opus-4-8"),
            patch("devbench.cli._supervise_backlog_ids", return_value=["E1-F1-S1-T1"]),
            patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
            patch("devbench.cli._supervise_use_bedrock", return_value=False),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        ]

    def _creds(self, tmp_path: Path) -> Path:
        import json

        creds = tmp_path / ".credentials.json"
        creds.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "t", "scopes": ["user:inference"]}}),
            encoding="utf-8",
        )
        return creds

    def test_no_registry_record_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds = self._creds(tmp_path)
        patches = self._common(tmp_path, creds)
        # The launch is a no-op (no registry record written) -> start reports failure.
        patches.append(patch("devbench.cli._supervise_launch_screen", MagicMock(return_value=0)))
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "ghost")
        assert rc == 1
        assert "failed to reach running" in capsys.readouterr().err

    def test_already_running_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.constants import SUPERVISE_STATE_RUNNING
        from devbench.supervise import new_session_state

        creds = self._creds(tmp_path)
        # Pre-seed a running session with this name, alive (use the current pid).
        reg = SuperviseRegistry(tmp_path)
        import os as _os

        st = new_session_state(
            name="dup",
            pid=_os.getpid(),
            screen_name="devbench-supervise-dup",
            model="opus",
            effort="xhigh",
            started_by="t",
        )
        st.state = SUPERVISE_STATE_RUNNING
        reg.write_state(st)

        patches = self._common(tmp_path, creds)
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "dup")
        assert rc == 2
        assert "already running" in capsys.readouterr().err

    def test_effort_flag_passthrough(self, tmp_path: Path) -> None:
        creds = self._creds(tmp_path)
        spawned: dict[str, Any] = {}

        def _fake_launch(*, name, screen_name, env, run_argv, screen_path):
            spawned["run_argv"] = run_argv
            from devbench.constants import SUPERVISE_STATE_RUNNING
            from devbench.supervise import new_session_state

            reg = SuperviseRegistry(tmp_path)
            st = new_session_state(
                name=name,
                pid=1,
                screen_name=screen_name,
                model="claude-opus-4-8",
                effort="high",
                started_by="t",
            )
            st.state = SUPERVISE_STATE_RUNNING
            reg.write_state(st)
            return 0

        patches = self._common(tmp_path, creds)
        patches.append(patch("devbench.cli._supervise_launch_screen", _fake_launch))
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n", "--effort", "high")
        assert rc == 0
        run_argv = spawned["run_argv"]
        assert "--effort" in run_argv
        assert run_argv[run_argv.index("--effort") + 1] == "high"

    def test_invalid_scope_token_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds = self._creds(tmp_path)
        patches = self._common(tmp_path, creds)
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n", "--include", "E1--E3")
        assert rc == 2
        assert "invalid scope token" in capsys.readouterr().err

    def test_scope_overlap_returns_nonzero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Pre-seed an SDK session claiming E1-F1-S1-T1; a supervise start on the
        # same scope without --allow-overlap is rejected (FR-18).
        from datetime import UTC, datetime

        from devbench.session import Session, SessionRegistry

        sdk = SessionRegistry(tmp_path)
        sdk.save(
            [
                Session(
                    name="sdk",
                    pid=1,
                    scope=["E1-F1-S1-T1"],
                    started_at=datetime.now(UTC),
                    started_by="t",
                    state_dir=tmp_path / ".devbench" / "sessions" / "sdk",
                )
            ]
        )
        creds = self._creds(tmp_path)
        patches = self._common(tmp_path, creds)
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n", "--include", "E1")
        assert rc != 0
        assert "overlap" in capsys.readouterr().err


@pytest.mark.unit
class TestPreflightModelAndClaude:
    """Preflight fail-fast on unresolved model and missing claude (FR-19, FR-25)."""

    def _creds(self, tmp_path: Path) -> Path:
        import json

        creds = tmp_path / ".credentials.json"
        creds.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "t", "scopes": ["user:inference"]}}),
            encoding="utf-8",
        )
        return creds

    def test_model_unresolved_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.cli import _OrchestratorModelUnsetError

        creds = self._creds(tmp_path)
        patches = [
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
            patch("devbench.cli._resolve_orchestrator_model", side_effect=_OrchestratorModelUnsetError("x")),
            patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
            patch("devbench.cli._supervise_use_bedrock", return_value=False),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
        ]
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n")
        assert rc == 2
        assert "no model" in capsys.readouterr().err

    def test_claude_missing_in_preflight_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds = self._creds(tmp_path)

        def _which(name: str) -> str | None:
            return None if name == "claude" else f"/usr/bin/{name}"

        patches = [
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
            patch("devbench.cli._resolve_orchestrator_model", return_value="claude-opus-4-8"),
            patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
            patch("devbench.cli._supervise_use_bedrock", return_value=False),
            patch("devbench.cli.shutil.which", _which),
        ]
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n")
        assert rc == 2
        assert "claude" in capsys.readouterr().err


@pytest.mark.unit
class TestAttachReadOnlyDeferred:
    """attach (no flags) read-only follow is deferred (fails fast, not silent)."""

    def test_attach_no_flags_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            cli.cmd_supervise("attach", "--name", "nightly")


@pytest.mark.unit
class TestSuperviseSeams:
    """The thin config/backlog seam functions read the single sources of truth."""

    def test_runtime_config_returns_supervise_block(self) -> None:
        assert isinstance(cli._supervise_runtime_config(), SuperviseConfig)

    def test_use_bedrock_returns_bool(self) -> None:
        assert isinstance(cli._supervise_use_bedrock(), bool)

    def test_backlog_ids_delegates_to_parser(self) -> None:
        unit = MagicMock()
        unit.id = "E1-F1-S1-T1"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]
        with patch("devbench.cli.BacklogParser", return_value=parser):
            assert cli._supervise_backlog_ids() == ["E1-F1-S1-T1"]


@pytest.mark.unit
class TestStartLaunchError:
    """A launch error under the flock surfaces as exit 2 (FR-30)."""

    def _creds(self, tmp_path: Path) -> Path:
        import json

        creds = tmp_path / ".credentials.json"
        creds.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "t", "scopes": ["user:inference"]}}),
            encoding="utf-8",
        )
        return creds

    def test_screen_launch_error_returns_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        creds = self._creds(tmp_path)
        patches = [
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
            patch("devbench.cli._resolve_orchestrator_model", return_value="claude-opus-4-8"),
            patch("devbench.cli._supervise_backlog_ids", return_value=["E1-F1-S1-T1"]),
            patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
            patch("devbench.cli._supervise_use_bedrock", return_value=False),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
            patch("devbench.cli._supervise_launch_screen", side_effect=SuperviseError("screen blew up")),
        ]
        with _ctx(patches), patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n")
        assert rc == 2
        assert "screen blew up" in capsys.readouterr().err
