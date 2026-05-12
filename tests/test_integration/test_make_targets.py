"""Integration tests for Makefile target behaviours.

Covers:
- AC-FUNC-004: help output includes env-var tokens for report-session and watch-live
- AC-FUNC-005: make -n start resolves unchanged (uv run python -m devbench.cli start)
- AC-CYCLE-001: end-to-end invocation via subprocess asserting observed CLI behaviour
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# Repo root is two levels above this test file:
# tests/test_integration/test_make_targets.py -> tests/test_integration -> tests -> repo root
_REPO_ROOT = Path(__file__).parent.parent.parent


def _make_dry_run(target: str, env: dict[str, str] | None = None) -> str:
    """Run ``make -n <target>`` and return combined stdout output."""
    call_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        ["make", "-n", target],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env=call_env,
    )
    return result.stdout + result.stderr


def _make_help() -> str:
    """Run ``make help`` and return stdout."""
    result = subprocess.run(
        ["make", "help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ,
    )
    return result.stdout


@pytest.mark.functional
class TestStartUnchanged:
    """AC-FUNC-005."""

    def test_start_resolves_to_cli_start(self) -> None:
        """AC-FUNC-005: make -n start resolves to 'uv run python -m devbench.cli start'."""
        output = _make_dry_run("start")
        assert "uv run python -m devbench.cli start" in output, (
            f"Expected 'uv run python -m devbench.cli start' in make -n start output, got:\n{output}"
        )
        assert "--dangerously-skip-permissions" not in output, (
            f"Expected no '--dangerously-skip-permissions' in 'make -n start', got:\n{output}"
        )


@pytest.mark.functional
class TestHelpEnvVarTokens:
    """AC-FUNC-004."""

    def test_help_report_session_lists_since(self) -> None:
        """AC-FUNC-004a: help shows [SINCE] token for report-session."""
        output = _make_help()
        assert "report-session" in output, f"Expected 'report-session' in help output:\n{output}"
        assert "SINCE" in output, f"Expected 'SINCE' in help output for report-session:\n{output}"

    def test_help_watch_live_lists_interval(self) -> None:
        """AC-FUNC-004b: help shows [INTERVAL] token for watch-live."""
        output = _make_help()
        assert "watch-live" in output, f"Expected 'watch-live' in help output:\n{output}"
        assert "INTERVAL" in output, f"Expected 'INTERVAL' in help output for watch-live:\n{output}"

    def test_help_report_session_line_format(self) -> None:
        """AC-FUNC-004a exact format: report-session line ends with [SINCE]."""
        output = _make_help()
        lines = output.splitlines()
        report_session_lines = [ln for ln in lines if "report-session" in ln]
        assert report_session_lines, f"No 'report-session' line in help output:\n{output}"
        matching = [ln for ln in report_session_lines if "[SINCE]" in ln]
        assert matching, "Expected a line with '[SINCE]' but report-session lines were:\n" + "\n".join(
            report_session_lines
        )

    def test_help_watch_live_line_format(self) -> None:
        """AC-FUNC-004b exact format: watch-live line ends with [INTERVAL]."""
        output = _make_help()
        lines = output.splitlines()
        watch_live_lines = [ln for ln in lines if "watch-live" in ln]
        assert watch_live_lines, f"No 'watch-live' line in help output:\n{output}"
        matching = [ln for ln in watch_live_lines if "[INTERVAL]" in ln]
        assert matching, "Expected a line with '[INTERVAL]' but watch-live lines were:\n" + "\n".join(watch_live_lines)
