"""Unit tests for the guard-quota-aware.sh PreToolUse hook script.

The hook intercepts ``uv run devbench *`` Bash invocations and defers them
(exit 2, block) when a ``quota_pause.json`` checkpoint exists at the
workspace-root or session-scoped path AND the ``reset_at`` epoch has not yet
passed.

The hook is registered in ``plugin/devbench/hooks/hooks.json`` under the Bash
matcher.  It is invoked before any ``uv run devbench`` command so that the
orchestrator does not race past an active quota wait window.

Runtime invariant under test: when ``quota_pause.json`` is absent, or when the
``reset_at`` timestamp is in the past, the hook exits 0 and the devbench
command proceeds.  When the file is present and the reset window has not yet
elapsed, the hook exits 2 with a stderr message that tells the orchestrator how
long to wait and names the checkpoint file path.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "guard-quota-aware.sh"


def _run_hook(
    payload: dict,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _make_payload(command: str) -> dict:
    """Build a minimal PreToolUse Bash hook payload."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def _future_reset_at(seconds: int = 3600) -> str:
    """Return an ISO-8601 UTC timestamp that is *seconds* in the future."""
    return (datetime.now(tz=UTC) + timedelta(seconds=seconds)).isoformat()


def _past_reset_at(seconds: int = 60) -> str:
    """Return an ISO-8601 UTC timestamp that is *seconds* in the past."""
    return (datetime.now(tz=UTC) - timedelta(seconds=seconds)).isoformat()


def _write_quota_pause(
    directory: Path,
    reset_at: str | None,
    reason: str = "subscription_rate_limit",
) -> Path:
    """Write a quota_pause.json file into *directory*/.devbench/.

    Returns the path to the file written.
    """
    devbench_dir = directory / ".devbench"
    devbench_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = devbench_dir / "quota_pause.json"
    payload: dict = {
        "paused_at": datetime.now(tz=UTC).isoformat(),
        "reset_at": reset_at,
        "reason": reason,
        "raw_error": "HTTP 429 rate limit exceeded",
        "in_flight_wu": "E5-F5-S1-T2",
        "in_flight_phase": "GREEN",
        "completed_judges": [],
        "pending_judges": ["code_review"],
        "stage_artefacts": {},
    }
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    return checkpoint_path


@pytest.mark.unit
class TestGuardQuotaAwareStructural:
    """Structural contract: script presence, executability, JSON parsing."""

    def test_script_exists(self) -> None:
        """The script must exist on disk."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"

    def test_script_is_executable(self) -> None:
        """The script must be executable."""
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    def test_empty_command_passes_through(self) -> None:
        """A payload with no command field exits 0 cleanly."""
        payload = {"tool_name": "Bash", "tool_input": {}}
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_malformed_json_passes_through(self) -> None:
        """A non-JSON stdin payload must not raise; the hook silently passes."""
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            input="not json at all { unclosed",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_no_workspace_root_passes_through(self) -> None:
        """When JUDGE_WORKSPACE_ROOT is unset, the hook allows through without checking."""
        payload = _make_payload("uv run devbench next")
        env = os.environ.copy()
        env.pop("JUDGE_WORKSPACE_ROOT", None)
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardQuotaAwarePassthroughs:
    """Commands not matching ``uv run devbench *`` are never intercepted."""

    def test_plain_ls_passes_through(self) -> None:
        """A bare shell command is not intercepted."""
        payload = _make_payload("ls -la")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_git_command_passes_through(self) -> None:
        """git commands are not intercepted even when a checkpoint exists."""
        result = _run_hook(_make_payload("git status"))
        assert result.returncode == 0

    def test_python_command_passes_through(self) -> None:
        """python invocations are not intercepted."""
        payload = _make_payload("python -m pytest tests/")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_uv_non_devbench_passes_through(self) -> None:
        """``uv`` invocations that are not ``uv run devbench`` pass through."""
        payload = _make_payload("uv pip install requests")
        result = _run_hook(payload)
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardQuotaAwareNoCheckpointFile:
    """When quota_pause.json does not exist the hook always exits 0."""

    def test_no_checkpoint_devbench_command_passes(self, tmp_path: Path) -> None:
        """``uv run devbench next`` passes when no checkpoint file exists."""
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 0

    def test_no_checkpoint_devbench_status_passes(self, tmp_path: Path) -> None:
        """``uv run devbench status`` passes when no checkpoint file exists."""
        payload = _make_payload("uv run devbench status")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 0

    def test_no_checkpoint_devbench_log_comment_passes(self, tmp_path: Path) -> None:
        """``uv run devbench log-comment`` passes when no checkpoint file exists."""
        payload = _make_payload("uv run devbench log-comment executor E1 'done'")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardQuotaAwareFutureResetAt:
    """When quota_pause.json exists with a future reset_at, the hook blocks."""

    def test_devbench_next_blocked_during_quota_wait(self, tmp_path: Path) -> None:
        """``uv run devbench next`` is blocked when quota_pause.json is active."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600))
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2

    def test_devbench_claim_blocked_during_quota_wait(self, tmp_path: Path) -> None:
        """``uv run devbench claim`` is blocked when quota_pause.json is active."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600))
        payload = _make_payload("uv run devbench claim E1-F1-S1-T1")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2

    def test_devbench_log_comment_blocked_during_quota_wait(self, tmp_path: Path) -> None:
        """``uv run devbench log-comment`` is blocked when quota_pause.json is active."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(7200))
        payload = _make_payload("uv run devbench log-comment executor E1 'msg'")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2

    def test_stderr_names_checkpoint_path(self, tmp_path: Path) -> None:
        """The block message must name the checkpoint file path."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600))
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2
        assert "quota_pause.json" in result.stderr

    def test_stderr_names_reason(self, tmp_path: Path) -> None:
        """The block message must include the quota reason from the checkpoint."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600), reason="bedrock_throttle")
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2
        assert "bedrock_throttle" in result.stderr

    def test_stderr_names_reset_at(self, tmp_path: Path) -> None:
        """The block message must include the reset_at timestamp."""
        reset_at = _future_reset_at(3600)
        _write_quota_pause(tmp_path, reset_at=reset_at)
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2
        # The reset_at prefix (date portion) must appear in stderr.
        assert reset_at[:10] in result.stderr

    @pytest.mark.parametrize(
        "subcommand",
        [
            "next",
            "status",
            "claim E1-F1-S1-T1",
            "log-comment executor E1 'msg'",
            "read-unit E1-F1-S1-T1",
            "mark-done E1-F1-S1-T1",
        ],
    )
    def test_all_devbench_subcommands_blocked_during_wait(self, tmp_path: Path, subcommand: str) -> None:
        """Every ``uv run devbench <sub>`` is blocked during an active quota wait."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600))
        payload = _make_payload(f"uv run devbench {subcommand}")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2, (
            f"Subcommand {subcommand!r} was not blocked during quota wait; stderr={result.stderr!r}"
        )


@pytest.mark.unit
class TestGuardQuotaAwarePastResetAt:
    """When quota_pause.json has a past reset_at, the hook exits 0 (quota cleared)."""

    def test_past_reset_at_allows_through(self, tmp_path: Path) -> None:
        """``uv run devbench next`` is allowed when reset_at is in the past."""
        _write_quota_pause(tmp_path, reset_at=_past_reset_at(60))
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 0

    def test_past_reset_at_allows_claim(self, tmp_path: Path) -> None:
        """``uv run devbench claim`` is allowed when reset_at is in the past."""
        _write_quota_pause(tmp_path, reset_at=_past_reset_at(120))
        payload = _make_payload("uv run devbench claim E1-F1-S1-T1")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardQuotaAwareNullResetAt:
    """When reset_at is null in quota_pause.json, the hook blocks (unknown reset time)."""

    def test_null_reset_at_blocks(self, tmp_path: Path) -> None:
        """When reset_at is null, the hook blocks because the quota window is unknown."""
        _write_quota_pause(tmp_path, reset_at=None)
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2

    def test_null_reset_at_stderr_mentions_unknown(self, tmp_path: Path) -> None:
        """Stderr mentions that reset_at is unknown when null."""
        _write_quota_pause(tmp_path, reset_at=None)
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2
        stderr_lower = result.stderr.lower()
        assert "unknown" in stderr_lower or "null" in stderr_lower


@pytest.mark.unit
class TestGuardQuotaAwareMaxWait:
    """Max wait boundary: when max_wait_seconds has elapsed since paused_at, allow through."""

    def test_max_wait_exceeded_allows_through(self, tmp_path: Path) -> None:
        """When the quota wait has exceeded max_wait_seconds, the hook exits 0."""
        # Write a checkpoint where paused_at is far in the past (> default max_wait)
        # but reset_at is still in the future.
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = devbench_dir / "quota_pause.json"
        # Paused 6 hours ago; reset in 1 hour; default max_wait is 5 hours.
        paused_at = (datetime.now(tz=UTC) - timedelta(hours=6)).isoformat()
        reset_at = _future_reset_at(3600)
        payload: dict = {
            "paused_at": paused_at,
            "reset_at": reset_at,
            "reason": "subscription_rate_limit",
            "raw_error": "HTTP 429",
            "in_flight_wu": "E5-F5-S1-T2",
            "in_flight_phase": "GREEN",
            "completed_judges": [],
            "pending_judges": [],
            "stage_artefacts": {},
        }
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
        hook_payload = _make_payload("uv run devbench next")
        result = _run_hook(
            hook_payload,
            env_overrides={
                "JUDGE_WORKSPACE_ROOT": str(tmp_path),
                "DEVBENCH_QUOTA_MAX_WAIT_SECONDS": "3600",  # 1-hour max
            },
        )
        # paused_at was 6 hours ago, max_wait is 1 hour: should allow through.
        assert result.returncode == 0

    def test_within_max_wait_blocks(self, tmp_path: Path) -> None:
        """When the quota wait is within max_wait_seconds, the hook blocks."""
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = devbench_dir / "quota_pause.json"
        # Paused 30 minutes ago; max_wait is 2 hours.
        paused_at = (datetime.now(tz=UTC) - timedelta(minutes=30)).isoformat()
        reset_at = _future_reset_at(3600)
        cp_payload: dict = {
            "paused_at": paused_at,
            "reset_at": reset_at,
            "reason": "subscription_rate_limit",
            "raw_error": "HTTP 429",
            "in_flight_wu": "E5-F5-S1-T2",
            "in_flight_phase": "GREEN",
            "completed_judges": [],
            "pending_judges": [],
            "stage_artefacts": {},
        }
        checkpoint_path.write_text(json.dumps(cp_payload), encoding="utf-8")
        hook_payload = _make_payload("uv run devbench next")
        result = _run_hook(
            hook_payload,
            env_overrides={
                "JUDGE_WORKSPACE_ROOT": str(tmp_path),
                "DEVBENCH_QUOTA_MAX_WAIT_SECONDS": "7200",  # 2-hour max
            },
        )
        assert result.returncode == 2


@pytest.mark.unit
class TestGuardQuotaAwareSessionScoped:
    """Per-session quota_pause.json is checked when DEVBENCH_SESSION_NAME is set."""

    def test_session_scoped_checkpoint_blocks(self, tmp_path: Path) -> None:
        """When session name is set, the session-scoped checkpoint is checked."""
        session_name = "test-session-alpha"
        # Per-session path: <workspace>/.devbench/sessions/<name>/.devbench/quota_pause.json
        session_dir = tmp_path / ".devbench" / "sessions" / session_name
        _write_quota_pause(session_dir, reset_at=_future_reset_at(3600))
        payload = _make_payload("uv run devbench next")
        result = _run_hook(
            payload,
            env_overrides={
                "JUDGE_WORKSPACE_ROOT": str(tmp_path),
                "DEVBENCH_SESSION_NAME": session_name,
            },
        )
        assert result.returncode == 2

    def test_session_scoped_checkpoint_past_allows(self, tmp_path: Path) -> None:
        """A session-scoped checkpoint with past reset_at allows through."""
        session_name = "test-session-beta"
        session_dir = tmp_path / ".devbench" / "sessions" / session_name
        _write_quota_pause(session_dir, reset_at=_past_reset_at(60))
        payload = _make_payload("uv run devbench next")
        result = _run_hook(
            payload,
            env_overrides={
                "JUDGE_WORKSPACE_ROOT": str(tmp_path),
                "DEVBENCH_SESSION_NAME": session_name,
            },
        )
        assert result.returncode == 0

    def test_workspace_root_checkpoint_ignored_when_session_scoped(self, tmp_path: Path) -> None:
        """When session name is set, only the session checkpoint is checked (not workspace root)."""
        session_name = "test-session-gamma"
        # Write checkpoint at workspace root -- should be ignored.
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600))
        # Do NOT write session-scoped checkpoint.
        payload = _make_payload("uv run devbench next")
        result = _run_hook(
            payload,
            env_overrides={
                "JUDGE_WORKSPACE_ROOT": str(tmp_path),
                "DEVBENCH_SESSION_NAME": session_name,
            },
        )
        # No session-scoped checkpoint exists -- must allow through.
        assert result.returncode == 0

    def test_no_session_uses_workspace_root_checkpoint(self, tmp_path: Path) -> None:
        """Without session name, the workspace-root checkpoint is checked."""
        _write_quota_pause(tmp_path, reset_at=_future_reset_at(3600))
        payload = _make_payload("uv run devbench next")
        result = _run_hook(
            payload,
            env_overrides={
                "JUDGE_WORKSPACE_ROOT": str(tmp_path),
                "DEVBENCH_SESSION_NAME": "",
            },
        )
        assert result.returncode == 2


@pytest.mark.unit
class TestGuardQuotaAwareMalformedCheckpoint:
    """Malformed quota_pause.json must not silently allow through -- it must block."""

    def test_malformed_json_blocks(self, tmp_path: Path) -> None:
        """A checkpoint file with invalid JSON blocks the devbench command."""
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text("{ invalid json unclosed", encoding="utf-8")
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2

    def test_malformed_json_stderr_actionable(self, tmp_path: Path) -> None:
        """Stderr for a malformed checkpoint is actionable (names the file)."""
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text("{ invalid json unclosed", encoding="utf-8")
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert "quota_pause.json" in result.stderr

    def test_missing_reset_at_field_blocks(self, tmp_path: Path) -> None:
        """A checkpoint missing the reset_at key blocks (fail-fast on malformed data)."""
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        cp = {"paused_at": datetime.now(tz=UTC).isoformat(), "reason": "test"}
        (devbench_dir / "quota_pause.json").write_text(json.dumps(cp), encoding="utf-8")
        payload = _make_payload("uv run devbench next")
        result = _run_hook(payload, env_overrides={"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2


@pytest.mark.unit
class TestGuardQuotaAwareHooksJsonRegistration:
    """guard-quota-aware.sh must be registered in hooks.json under the Bash PreToolUse matcher."""

    HOOKS_JSON_PATH = Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench" / "hooks" / "hooks.json"

    def test_hooks_json_contains_guard_quota_aware(self) -> None:
        """hooks.json must register guard-quota-aware.sh for Bash PreToolUse."""
        hooks = json.loads(self.HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        pre_tool_use = hooks["hooks"]["PreToolUse"]
        bash_matchers = [block for block in pre_tool_use if block.get("matcher") == "Bash"]
        assert bash_matchers, "No Bash PreToolUse matcher found in hooks.json"
        bash_hooks = bash_matchers[0]["hooks"]
        commands = [h["command"] for h in bash_hooks]
        assert any("guard-quota-aware.sh" in cmd for cmd in commands), (
            f"guard-quota-aware.sh not registered in Bash PreToolUse hooks; found: {commands!r}"
        )
