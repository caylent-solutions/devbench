"""Integration tests for Makefile target behaviours.

Covers:
- AC-FUNC-001: start-interactive adds --dangerously-skip-permissions by default
- AC-FUNC-002: DEVBENCH_SAFE_PERMISSIONS=1 suppresses --dangerously-skip-permissions
- AC-FUNC-003: help output includes env-var token for start-interactive
- AC-FUNC-004: help output includes env-var tokens for report-session and watch-live
- AC-FUNC-005: make -n start resolves unchanged (uv run python -m devbench.cli start)
- AC-CYCLE-001: end-to-end invocation via subprocess asserting observed CLI behaviour
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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
class TestStartInteractiveFlag:
    """AC-FUNC-001 / AC-FUNC-002 / AC-CYCLE-001."""

    def test_default_includes_dangerously_skip_permissions(self) -> None:
        """AC-FUNC-001: make -n start-interactive includes --dangerously-skip-permissions by default."""
        env = {k: v for k, v in os.environ.items() if k != "DEVBENCH_SAFE_PERMISSIONS"}
        output = _make_dry_run("start-interactive", env=env)
        assert "--dangerously-skip-permissions" in output, (
            f"Expected '--dangerously-skip-permissions' in make -n start-interactive output, got:\n{output}"
        )

    def test_safe_permissions_one_omits_flag(self) -> None:
        """AC-FUNC-002: DEVBENCH_SAFE_PERMISSIONS=1 suppresses --dangerously-skip-permissions."""
        output = _make_dry_run("start-interactive", env={"DEVBENCH_SAFE_PERMISSIONS": "1"})
        assert "--dangerously-skip-permissions" not in output, (
            f"Expected '--dangerously-skip-permissions' to be absent when DEVBENCH_SAFE_PERMISSIONS=1, got:\n{output}"
        )

    def test_safe_permissions_zero_includes_flag(self) -> None:
        """AC-FUNC-001 variant: DEVBENCH_SAFE_PERMISSIONS=0 still includes the flag."""
        output = _make_dry_run("start-interactive", env={"DEVBENCH_SAFE_PERMISSIONS": "0"})
        assert "--dangerously-skip-permissions" in output, (
            f"Expected '--dangerously-skip-permissions' when DEVBENCH_SAFE_PERMISSIONS=0, got:\n{output}"
        )


@pytest.mark.functional
class TestStartUnchanged:
    """AC-FUNC-005 + auto-restart loop guards."""

    def test_start_resolves_to_cli_start(self) -> None:
        """AC-FUNC-005: make -n start invokes 'uv run python -m devbench.cli start'."""
        output = _make_dry_run("start")
        assert "uv run python -m devbench.cli start" in output, (
            f"Expected 'uv run python -m devbench.cli start' in make -n start output, got:\n{output}"
        )
        assert "--dangerously-skip-permissions" not in output, (
            f"Expected no '--dangerously-skip-permissions' in 'make -n start', got:\n{output}"
        )

    def test_start_recipe_includes_bounded_auto_restart_loop(self) -> None:
        """The start target must wrap cli.start in a while-loop bounded by
        DEVBENCH_MAX_AUTO_RESTARTS (default 3); only exit code 42 triggers
        a restart, anything else exits with the orchestrator's own rc. Any
        regression that drops the loop (or makes it unbounded) would
        re-introduce the bug where a RUNTIME_DEGRADATION-only NO_ACTIONABLE
        exit silently stops the run."""
        output = _make_dry_run("start")
        assert "DEVBENCH_MAX_AUTO_RESTARTS" in output, (
            f"Expected DEVBENCH_MAX_AUTO_RESTARTS in make -n start output, got:\n{output}"
        )
        assert "-ne 42" in output, f"Expected exit-code-42 conditional in make -n start output, got:\n{output}"
        assert "while" in output, f"Expected restart while-loop in make -n start output, got:\n{output}"
        assert "auto-restart" in output, f"Expected 'auto-restart' INFO message in make -n start output, got:\n{output}"
        assert "restart cap" in output, f"Expected 'restart cap' ERROR message in make -n start output, got:\n{output}"

    def test_start_recipe_loop_aborts_on_cap(self, tmp_path: Path) -> None:
        """Integration: stub `python -m devbench.cli start` to exit 42 every
        time. With DEVBENCH_MAX_AUTO_RESTARTS=2, the Makefile loop must run
        the command exactly 2 times, print the cap-exhausted error, and
        the recipe exits non-zero. GNU make returns 2 on any recipe failure
        regardless of the recipe's specific exit code, so we assert rc != 0
        + the cap-exhausted message on stderr to pin the actual behaviour
        rather than make's wrapping."""
        shim_repo = tmp_path / "shim_repo"
        shim_repo.mkdir()
        real_makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        (shim_repo / "Makefile").write_text(real_makefile, encoding="utf-8")
        counter = shim_repo / "calls.txt"
        stub_uv = shim_repo / "uv"
        stub_uv.write_text(
            f'#!/usr/bin/env bash\necho "call" >> "{counter}"\nexit 42\n',
            encoding="utf-8",
        )
        stub_uv.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_repo}:{os.environ['PATH']}", "DEVBENCH_MAX_AUTO_RESTARTS": "2"}
        result = subprocess.run(
            ["make", "start"],
            cwd=shim_repo,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0, (
            f"Expected non-zero rc (cap exhausted), got 0.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        call_count = counter.read_text(encoding="utf-8").count("call") if counter.exists() else 0
        assert call_count == 2, (
            f"Expected 2 attempts (cap=2), got {call_count}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "restart cap" in result.stderr, f"Expected 'restart cap' error message on stderr, got:\n{result.stderr}"

    def test_start_recipe_loop_succeeds_after_one_restart(self, tmp_path: Path) -> None:
        """Integration: stub exits 42 once then 0 the next call. The loop
        should run exactly 2 times and exit with the wrapped command's
        final rc=0."""
        shim_repo = tmp_path / "shim_repo"
        shim_repo.mkdir()
        real_makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        (shim_repo / "Makefile").write_text(real_makefile, encoding="utf-8")
        counter = shim_repo / "calls.txt"
        stub_uv = shim_repo / "uv"
        stub_uv.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "call" >> "{counter}"\n'
            f'n=$(wc -l < "{counter}" | tr -d " ")\n'
            'if [ "$n" -lt 2 ]; then exit 42; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        stub_uv.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_repo}:{os.environ['PATH']}", "DEVBENCH_MAX_AUTO_RESTARTS": "5"}
        result = subprocess.run(
            ["make", "start"],
            cwd=shim_repo,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"Expected rc=0 after one restart succeeded, got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        call_count = counter.read_text(encoding="utf-8").count("call")
        assert call_count == 2, (
            f"Expected 2 attempts (one 42 + one 0), got {call_count}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_start_recipe_does_not_restart_on_non_42_exit_code(self, tmp_path: Path) -> None:
        """Integration: stub exits with rc=7 (any non-42 failure). The loop
        must NOT restart -- the recipe is invoked exactly once. GNU make
        wraps the failure as rc=2 regardless of the original code; we
        pin the no-restart behaviour via the call count."""
        shim_repo = tmp_path / "shim_repo"
        shim_repo.mkdir()
        real_makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        (shim_repo / "Makefile").write_text(real_makefile, encoding="utf-8")
        counter = shim_repo / "calls.txt"
        stub_uv = shim_repo / "uv"
        stub_uv.write_text(
            f'#!/usr/bin/env bash\necho "call" >> "{counter}"\nexit 7\n',
            encoding="utf-8",
        )
        stub_uv.chmod(0o755)
        env = {**os.environ, "PATH": f"{shim_repo}:{os.environ['PATH']}", "DEVBENCH_MAX_AUTO_RESTARTS": "5"}
        result = subprocess.run(
            ["make", "start"],
            cwd=shim_repo,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0, (
            f"Expected non-zero rc on stub failure, got 0.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        call_count = counter.read_text(encoding="utf-8").count("call")
        assert call_count == 1, f"Expected exactly 1 attempt (no restart on non-42), got {call_count}"
        assert "auto-restart" not in result.stderr, f"Loop must NOT restart on non-42 exit. stderr:\n{result.stderr}"


@pytest.mark.functional
class TestHelpEnvVarTokens:
    """AC-FUNC-003 / AC-FUNC-004."""

    def test_help_start_interactive_lists_env_vars(self) -> None:
        """AC-FUNC-003: help shows env-var token for start-interactive."""
        output = _make_help()
        assert "start-interactive" in output, f"Expected 'start-interactive' in help output:\n{output}"
        assert "DEVBENCH_WORKSPACE_ROOT" in output, (
            f"Expected 'DEVBENCH_WORKSPACE_ROOT' in help output for start-interactive:\n{output}"
        )
        assert "DEVBENCH_CLAUDE_MODEL" in output, (
            f"Expected 'DEVBENCH_CLAUDE_MODEL' in help output for start-interactive:\n{output}"
        )
        assert "DEVBENCH_SAFE_PERMISSIONS" in output, (
            f"Expected 'DEVBENCH_SAFE_PERMISSIONS' in help output for start-interactive:\n{output}"
        )

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

    def test_help_start_interactive_line_format(self) -> None:
        """AC-FUNC-003 exact format: start-interactive line ends with env-var bracket token."""
        output = _make_help()
        lines = output.splitlines()
        start_interactive_lines = [ln for ln in lines if "start-interactive" in ln]
        assert start_interactive_lines, f"No 'start-interactive' line found in help output:\n{output}"
        token = "[DEVBENCH_WORKSPACE_ROOT, DEVBENCH_CLAUDE_MODEL, DEVBENCH_SAFE_PERMISSIONS]"
        matching = [ln for ln in start_interactive_lines if token in ln]
        assert matching, f"Expected a line containing '{token}' but start-interactive lines were:\n" + "\n".join(
            start_interactive_lines
        )

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


@pytest.mark.functional
class TestCoverageGate:
    """The single consolidated ``test-coverage`` target enforces the package
    coverage floor.  Replaces the prior ``test-coverage-new`` per-module gate
    so there is exactly one coverage command surfaced to operators and CI.
    """

    def test_test_coverage_runs_against_devbench_package(self) -> None:
        output = _make_dry_run("test-coverage")
        assert "--cov=devbench" in output, f"Expected '--cov=devbench' in make -n test-coverage output, got:\n{output}"

    def test_test_coverage_enforces_98_percent_floor_via_coverage_cli(self) -> None:
        output = _make_dry_run("test-coverage")
        assert "coverage report" in output, (
            f"Expected 'coverage report' gate in make -n test-coverage output, got:\n{output}"
        )
        assert "--fail-under=98" in output, (
            f"Expected '--fail-under=98' in make -n test-coverage output, got:\n{output}"
        )
        assert "--cov-fail-under" not in output, (
            f"pytest-cov's --cov-fail-under should be replaced by the coverage CLI gate, got:\n{output}"
        )
