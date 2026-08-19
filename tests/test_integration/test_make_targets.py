"""Integration tests for Makefile target behaviours.

Covers:
- AC-FUNC-001: start-interactive adds --dangerously-skip-permissions by default
- AC-FUNC-002: DEVBENCH_SAFE_PERMISSIONS=1 suppresses --dangerously-skip-permissions
- AC-FUNC-003: help output includes env-var token for start-interactive
- AC-FUNC-004: help output includes env-var tokens for report-session and watch-live
- AC-FUNC-005: make -n start resolves unchanged (uv run python -m devbench.cli start)
- AC-CYCLE-001: end-to-end invocation via subprocess asserting observed CLI behaviour
- install wires the guard-hook runtime deps (jq + PyYAML for the system python3)
  through scripts/install-hook-deps.sh so a fresh checkout gets them from
  `make install`, not from tribal knowledge
"""

from __future__ import annotations

import inspect
import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import test_backlog.test_review_feedback_vocabulary as _membership_coverage_module

from devbench.vocabulary_generation import DOC_RELATIVE_PATH

# Repo root is two levels above this test file:
# tests/test_integration/test_make_targets.py -> tests/test_integration -> tests -> repo root
_REPO_ROOT = Path(__file__).parent.parent.parent

# Repo-relative path of finding 322-D21's membership-coverage module,
# derived from the imported module's own file identity rather than a
# hand-typed literal, so a future rename of the test file cannot leave a
# stale path string behind unnoticed.
_MEMBERSHIP_COVERAGE_MODULE_RELATIVE_PATH = (
    Path(inspect.getfile(_membership_coverage_module)).relative_to(_REPO_ROOT).as_posix()
)


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
        # Build a fake shim repo with a Makefile copy + stub python wrapper.
        shim_repo = tmp_path / "shim_repo"
        shim_repo.mkdir()
        # Copy real Makefile so we exercise the actual recipe text.
        real_makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        (shim_repo / "Makefile").write_text(real_makefile, encoding="utf-8")
        # Stub `uv` that increments a counter and always exits 42.
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
        # At least one line must contain the full env-var token
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

    def test_test_coverage_enforces_98_percent_floor(self) -> None:
        output = _make_dry_run("test-coverage")
        assert "--cov-fail-under=98" in output, (
            f"Expected '--cov-fail-under=98' in make -n test-coverage output, got:\n{output}"
        )


@pytest.fixture
def _restored_vocab_doc() -> Iterator[Path]:
    """Yield the real vocabulary doc path, restoring its original content on teardown.

    A full filesystem copy of the checkout (so `uv run` inside the recipe
    resolves an independent project) was tried and rejected: `uv run`
    re-resolves the editable `devbench` install against whichever
    `pyproject.toml` identity it is invoked from, so running it from a
    second copy of this checkout re-links the *shared* `.venv` to that
    copy and leaves it dangling once the copy is deleted -- corrupting the
    venv for every other process using this checkout (including the
    orchestrator that may be running this very test). Mutating the tracked
    file in place and restoring it in this fixture's teardown (which pytest
    always runs, even on assertion failure) avoids that shared-state risk
    entirely: the target `make check-vocabulary-drift` recipe never changes
    directory, so `uv run` keeps resolving the one real project throughout.
    """
    target = _REPO_ROOT / DOC_RELATIVE_PATH
    original = target.read_text(encoding="utf-8")
    try:
        yield target
    finally:
        target.write_text(original, encoding="utf-8")


@pytest.mark.functional
class TestVocabularyDriftCheck:
    """AC-E2-F5-S1-T2-1/2/3 (spec 4.10; AC-11): `make check-vocabulary-drift`
    regenerates every guard-marked surface into a scratch directory and
    diffs it against the committed tree, and `validate` runs it."""

    def test_freshly_generated_tree_passes_drift_check(self) -> None:
        """AC-E2-F5-S1-T2-2: the committed tree is not itself drifted."""
        result = subprocess.run(
            ["make", "check-vocabulary-drift"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Expected rc=0 on the freshly generated tree, got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_hand_edited_generated_block_fails_naming_regeneration_command(self, _restored_vocab_doc: Path) -> None:
        """AC-E2-F5-S1-T2-1: a hand-edit inside the guard-marked block makes
        the drift check exit non-zero, naming both the offending file and
        `make generate-vocabulary` as the fix."""
        target = _restored_vocab_doc
        original = target.read_text(encoding="utf-8")
        marker = "<!-- generated:vocabulary -->"
        marker_index = original.index(marker)
        insertion_point = marker_index + len(marker)
        hand_edit = "\n| `HAND_EDITED_ROW` | injected by test | should be overwritten |"
        mutated = original[:insertion_point] + hand_edit + original[insertion_point:]
        assert mutated != original
        target.write_text(mutated, encoding="utf-8")

        result = subprocess.run(
            ["make", "check-vocabulary-drift"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            f"Expected non-zero rc on a hand-edited generated block, got 0.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "make generate-vocabulary" in result.stderr, (
            f"Expected the regeneration command named on stderr, got:\n{result.stderr}"
        )
        assert DOC_RELATIVE_PATH in result.stderr, (
            f"Expected the offending file '{DOC_RELATIVE_PATH}' named on stderr, got:\n{result.stderr}"
        )

    def test_validate_runs_the_drift_check(self) -> None:
        """AC-E2-F5-S1-T2-3 / AC-E2-F5-S1-T3-7: the drift check is a
        prerequisite of `validate`, pinned by the drift target's own dry-run
        recipe lines rather than an internal function/symbol name -- so this
        test survives a refactor of how the drift check is implemented."""
        drift_output = _make_dry_run("check-vocabulary-drift")
        drift_recipe_lines = [line for line in drift_output.splitlines() if line.strip()]
        assert drift_recipe_lines, (
            f"Expected 'make -n check-vocabulary-drift' to produce at least one recipe line, got:\n{drift_output}"
        )

        validate_output = _make_dry_run("validate")
        for line in drift_recipe_lines:
            assert line in validate_output, (
                f"Expected drift target's recipe line to appear in 'make -n validate' output.\n"
                f"Missing line: {line!r}\nvalidate output:\n{validate_output}"
            )


@pytest.mark.functional
class TestMembershipCoverageGateReachableFromValidate:
    """AC-E2-F5-S1-T2-4/-5/-6 (spec 4.10; finding 322-D21): `validate`'s test
    stage must actually execute the membership-coverage module, the same way
    `TestVocabularyDriftCheck.test_validate_runs_the_drift_check` above pins
    that the drift target is reachable from `validate`. `test-coverage`'s
    recipe is a single ``pytest tests/`` invocation with no marker filter or
    explicit path list today; this test proves that invocation, run for
    real in collect-only mode, actually collects the membership-coverage
    module, so a future narrowing of the invocation fails here instead of
    silently dropping finding 322-D21's completeness gate out of
    `make validate`."""

    def test_validate_pytest_invocation_collects_the_membership_module(self) -> None:
        coverage_recipe = _make_dry_run("test-coverage")
        pytest_invocation_lines = [line for line in coverage_recipe.splitlines() if "pytest" in line]
        assert len(pytest_invocation_lines) == 1, (
            f"Expected exactly one pytest invocation line in 'make -n test-coverage' output, got:\n{coverage_recipe}"
        )
        pytest_command = shlex.split(pytest_invocation_lines[0])

        collection = subprocess.run(
            [*pytest_command, "--collect-only", "-q"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert collection.returncode == 0, (
            f"Expected validate's pytest invocation to collect cleanly, got rc={collection.returncode}.\n"
            f"stdout:\n{collection.stdout}\nstderr:\n{collection.stderr}"
        )
        assert _MEMBERSHIP_COVERAGE_MODULE_RELATIVE_PATH in collection.stdout, (
            "Expected validate's pytest invocation to collect tests from "
            f"'{_MEMBERSHIP_COVERAGE_MODULE_RELATIVE_PATH}', but it is absent from the collection output "
            "-- a narrowed path list or marker filter would silently drop finding 322-D21's completeness "
            f"gate out of 'make validate'.\ncollection output:\n{collection.stdout}"
        )

    def test_test_coverage_is_a_validate_prerequisite(self) -> None:
        """Pins that `test-coverage` -- and therefore the membership-coverage
        module `test_validate_pytest_invocation_collects_the_membership_module`
        above proves it collects -- is actually reachable from `validate`,
        the same way `TestVocabularyDriftCheck.test_validate_runs_the_drift_check`
        pins the drift target's wiring. Without this test, removing
        `test-coverage` from the `validate` prerequisite list in `Makefile`
        would leave the test above green (it invokes `test-coverage`
        directly) while silently dropping finding 322-D21's completeness
        gate out of `make validate`."""
        coverage_recipe = _make_dry_run("test-coverage")
        pytest_invocation_lines = [line for line in coverage_recipe.splitlines() if "pytest" in line]
        assert pytest_invocation_lines, (
            f"Expected 'make -n test-coverage' to produce at least one pytest invocation line, got:\n{coverage_recipe}"
        )

        validate_output = _make_dry_run("validate")
        for line in pytest_invocation_lines:
            assert line in validate_output, (
                "Expected test-coverage's pytest invocation line to appear in 'make -n validate' output.\n"
                f"Missing line: {line!r}\nvalidate output:\n{validate_output}"
            )


@pytest.mark.functional
class TestInstallProvisionsHookDeps:
    """`make install` must provision the guard hooks' runtime deps (jq, PyYAML
    for the system python3 that guard-work-unit-write.sh resolves) via
    scripts/install-hook-deps.sh, in addition to `uv sync`. Without this a
    fresh macOS / minimal-Linux operator gets a Rule 11 guard that cannot
    run."""

    def test_install_runs_uv_sync_then_hook_deps(self) -> None:
        output = _make_dry_run("install")
        assert "uv sync --all-extras" in output, output
        assert "install-hook-deps" in output, output

    def test_install_hook_deps_target_invokes_script(self) -> None:
        output = _make_dry_run("install-hook-deps")
        assert "scripts/install-hook-deps.sh" in output, output

    def test_install_hook_deps_script_exists_executable_and_parses(self) -> None:
        script = _REPO_ROOT / "scripts" / "install-hook-deps.sh"
        assert script.exists(), f"missing {script}"
        assert os.access(script, os.X_OK), f"not executable: {script}"
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_help_lists_install_hook_deps(self) -> None:
        assert "install-hook-deps" in _make_help()
