"""``guard-bash.sh`` blocks daemon-control verbs (TDI-004).

An executor sub-agent (the worker assigned to a single work unit) has
unrestricted Bash and can run ``devbench stop --session <name>`` -- which sends
SIGTERM to its OWN orchestrator and halts the entire run, not just the unit it
was assigned. This was observed verbatim in a real run: an executor reasoned
about "stopping the daemon" as an investigative step and ran
``uv run devbench stop --session kanon``, cleanly terminating the orchestrator.

This is the first layer of a defense-in-depth fix: ``guard-bash.sh`` now
deterministically DENIES (exit 2) any Bash command that invokes a daemon-control
verb (``devbench stop`` / ``start`` / ``drain`` / ``restart`` /
``sessions --cleanup``). The second layer is a caller-role gate inside
``cmd_stop`` (and ``start`` / ``drain`` / ``restart`` / ``sessions --cleanup``)
in ``cli.py`` -- see ``tests/test_cli.py``.

Legitimate, non-daemon-control devbench commands (``read-unit``, ``report``,
``log-comment``, etc.) and arbitrary unrelated Bash must remain ALLOWED.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GUARD_SCRIPT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "scripts" / "guard-bash.sh"


def _run(command: str) -> subprocess.CompletedProcess[str]:
    """Invoke the guard hook with a crafted Bash PreToolUse payload."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {k: v for k, v in os.environ.items() if k != "BASH_ENV"}
    return subprocess.run(
        ["bash", str(GUARD_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.mark.unit
class TestGuardBashScriptExists:
    def test_script_present_and_executable(self) -> None:
        assert GUARD_SCRIPT.is_file(), f"guard-bash.sh must exist at {GUARD_SCRIPT}"
        assert os.access(GUARD_SCRIPT, os.X_OK), "guard-bash.sh must be executable"


@pytest.mark.unit
class TestGuardBashBlocksDaemonControl:
    """Daemon-control verbs must HARD-DENY with exit 2 regardless of invocation prefix."""

    @pytest.mark.parametrize(
        ("command", "label"),
        [
            ("uv run devbench stop --session kanon", "stop-uv-run"),
            ("devbench stop --session kanon", "stop-bare"),
            ("uv run --project . devbench stop --session default", "stop-uv-project"),
            ("devbench start --daemon", "start"),
            ("uv run devbench start --name kanon --daemon", "start-uv-run"),
            ("devbench drain --all", "drain-all"),
            ("devbench drain --session kanon", "drain-session"),
            ("uv run devbench restart kanon", "restart"),
            ("devbench sessions --cleanup", "sessions-cleanup"),
            ("uv run devbench sessions --cleanup", "sessions-cleanup-uv"),
        ],
    )
    def test_daemon_control_blocked(self, command: str, label: str) -> None:
        result = _run(command)
        assert result.returncode == 2, (
            f"[{label}] guard-bash.sh must exit 2 for daemon-control command {command!r}; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        assert "daemon-control" in result.stderr.lower() or "orchestrator" in result.stderr.lower(), (
            f"[{label}] stderr must explain the daemon-control denial; got: {result.stderr!r}"
        )


@pytest.mark.unit
class TestGuardBashAllowsLegitimateCommands:
    """Non-daemon-control commands -- the executor's normal toolkit -- stay ALLOWED (exit 0)."""

    @pytest.mark.parametrize(
        ("command", "label"),
        [
            ("uv run devbench read-unit E10-F1-S1-T2", "read-unit"),
            ("uv run devbench report", "report"),
            ("devbench log-comment E1-F1-S1-T1 'investigating'", "log-comment"),
            ("uv run devbench instances", "instances"),
            ("git status --porcelain=v1", "git-status"),
            ("ls -la", "ls"),
            ("pytest tests/test_cli.py", "pytest"),
            # A non-daemon-control verb whose name merely CONTAINS a blocked
            # verb as a substring must not be wrongly blocked.
            ("uv run devbench stop-instance abc123", "stop-instance"),
        ],
    )
    def test_legitimate_command_allowed(self, command: str, label: str) -> None:
        result = _run(command)
        assert result.returncode == 0, (
            f"[{label}] guard-bash.sh must allow legitimate command {command!r}; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
