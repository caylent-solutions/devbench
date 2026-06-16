"""cmd_supervise start preflight pipeline + __run wiring (FR-3..FR-8, FR-19..25).

Drives ``cmd_supervise start`` with the screen-launch step mocked (no real
``screen``/``claude`` this phase) and asserts: the preflight fail-fast paths
(missing screen -> exit 2 AC-7, API-key present -> exit 2 AC-5, subscription auth
absent -> exit 2 AC-6), the scope.json write (AC-30/31 deterministic conveyance),
the registry record, and that ``__run`` drives the FakePexpectChild to ``running``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench import cli
from devbench.config_loader import SuperviseConfig
from devbench.scope import session_scope_file_path
from devbench.supervise import (
    DetectionPatterns,
    PtyDriver,
    SuperviseRegistry,
)


@pytest.fixture
def _creds(tmp_path: Path) -> Path:
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok", "scopes": ["user:inference"]}}),
        encoding="utf-8",
    )
    return creds


def _patch_common(tmp_path: Path, creds: Path):
    """Return a list of patches shared by the start preflight tests."""
    return [
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
        patch("devbench.cli._resolve_orchestrator_model", return_value="claude-opus-4-8"),
        patch("devbench.cli._supervise_backlog_ids", return_value=["E1-F1-S1-T1", "E2-F1-S1-T1"]),
        patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
        patch("devbench.cli._supervise_use_bedrock", return_value=False),
    ]


@pytest.mark.unit
class TestStartPreflightFailFast:
    """AC-5/6/7: preflight fail-fast paths exit 2 with the documented message."""

    def test_screen_missing(self, tmp_path: Path, _creds: Path, capsys: pytest.CaptureFixture[str]) -> None:
        patches = _patch_common(tmp_path, _creds)
        patches.append(patch("devbench.cli.shutil.which", lambda name: None))
        with _ctx(patches), patch.dict("os.environ", {}, clear=False):
            rc = cli.cmd_supervise("start", "--name", "n")
        assert rc == 2
        assert "'screen' is not installed" in capsys.readouterr().err

    def test_api_key_present(self, tmp_path: Path, _creds: Path, capsys: pytest.CaptureFixture[str]) -> None:
        patches = _patch_common(tmp_path, _creds)
        patches.append(patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"))
        with _ctx(patches), patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}, clear=False):
            rc = cli.cmd_supervise("start", "--name", "n")
        assert rc == 2
        assert "ANTHROPIC_API_KEY" in capsys.readouterr().err

    def test_subscription_auth_absent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        missing = tmp_path / "absent.json"
        patches = _patch_common(tmp_path, missing)
        patches.append(patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"))
        env = dict(_clean_env())
        with _ctx(patches), patch.dict("os.environ", env, clear=True):
            rc = cli.cmd_supervise("start", "--name", "n")
        assert rc == 2
        assert "subscription auth not found" in capsys.readouterr().err


@pytest.mark.unit
class TestStartLaunchesAndRecords:
    """FR-6/FR-8: start writes scope.json and records the registry session."""

    def test_scope_json_written_and_registry_recorded(self, tmp_path: Path, _creds: Path) -> None:
        patches = _patch_common(tmp_path, _creds)
        patches.append(patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"))
        # Mock the screen-spawn so no real screen/claude runs; it records the
        # session as running (as the in-screen __run would via the registry).
        spawned: dict[str, Any] = {}

        def _fake_launch(*, name, screen_name, env, run_argv, screen_path):
            spawned["name"] = name
            spawned["screen_name"] = screen_name
            spawned["env"] = env
            spawned["run_argv"] = run_argv
            # Mark running in the registry as __run would.
            reg = SuperviseRegistry(tmp_path)
            from devbench.constants import SUPERVISE_STATE_RUNNING
            from devbench.supervise import new_session_state

            st = new_session_state(
                name=name,
                pid=4321,
                screen_name=screen_name,
                model="claude-opus-4-8",
                effort="xhigh",
                started_by="tester",
            )
            st.state = SUPERVISE_STATE_RUNNING
            reg.write_state(st)
            return 4321

        patches.append(patch("devbench.cli._supervise_launch_screen", _fake_launch))
        env = dict(_clean_env())
        with _ctx(patches), patch.dict("os.environ", env, clear=True):
            rc = cli.cmd_supervise("start", "--name", "nightly", "--include", "E1")
        assert rc == 0

        scope_path = session_scope_file_path(tmp_path, "nightly")
        assert scope_path.exists()
        data = json.loads(scope_path.read_text(encoding="utf-8"))
        assert data["include"] == ["E1"]
        assert set(data["expanded_ids"]) == {"E1-F1-S1-T1"}

        # Registry shows the running session.
        reg = SuperviseRegistry(tmp_path)
        state = reg.read_state("nightly")
        assert state is not None
        assert state.state == "running"

        # The screen env carries the scope-conveyance vars (Section 5.6).
        env_passed = spawned["env"]
        assert env_passed["DEVBENCH_WORKSPACE_ROOT"] == str(tmp_path)
        assert env_passed["DEVBENCH_SESSION_NAME"] == "nightly"
        assert "ANTHROPIC_API_KEY" not in env_passed
        # The run argv invokes the hidden __run sub-verb.
        assert "__run" in spawned["run_argv"]


@pytest.mark.unit
class TestRunDrivesPexpectToRunning:
    """FR-7/FR-8: the __run supervisor reaches running via the FakePexpectChild."""

    def test_run_reaches_running(self, tmp_path: Path) -> None:
        from devbench.config_loader import SuperviseDetectionPatternsConfig
        from devbench.supervise import run_supervised_kickoff

        child = FakePexpectChild(
            [
                _ScriptStep(emit="> "),  # ready prompt
                # The orchestrate literal is a SLASH command: it is typed (no
                # newline), the autocomplete render settles, then a single Enter
                # (\r) submits it. Gate the ack on that submit \r so the
                # render-settle quiescence wait does NOT prematurely consume it.
                _ScriptStep(emit="esc to interrupt", on_send=r"\r"),  # ack
            ]
        )
        driver = PtyDriver(
            child=child,
            patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()),
        )
        sm = run_supervised_kickoff(
            driver=driver,
            injectable_commands={"orchestrate": "/devbench-orchestrate:orchestrate"},
            ready_timeout_seconds=5,
            command_ack_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        assert sm.state == "running"
        # Slash submission: type the literal then submit a single Enter.
        assert child.sent == ["/devbench-orchestrate:orchestrate", "\r"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ctx(patches: list):
    """Combine a list of patch context managers into one ExitStack-like ctx."""
    import contextlib

    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


def _clean_env() -> dict[str, str]:
    """A minimal env with no API-key vars and a PATH (for the preflight)."""
    return {"PATH": "/usr/bin", "DEVBENCH_WORKSPACE_ROOT": "/tmp/test-workspace"}
