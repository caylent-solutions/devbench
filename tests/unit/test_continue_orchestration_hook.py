"""Tests for the continue-orchestration Stop hook."""

import json
import subprocess
import time
from pathlib import Path

HOOK_SCRIPT = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "continue-orchestration.sh"
STATE_FILE = Path("/tmp/devbench-stop-hook-state.json")


def _run_hook(
    workspace_root: str,
    extra_env: dict[str, str] | None = None,
    stdin_payload: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the hook with a given workspace root.

    Args:
        workspace_root: Path to the workspace root directory.
        extra_env: Additional environment variables to pass to the hook.
        stdin_payload: JSON string to pass to the hook via stdin (simulates
            the Stop event payload from Claude Code).  When ``None``, no
            stdin is provided (maintains backward compat with existing tests).
    """
    env = {"JUDGE_WORKSPACE_ROOT": workspace_root, "PATH": "/usr/bin:/bin"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        input=stdin_payload if stdin_payload is not None else "",
    )


def _cleanup_state_file() -> None:
    """Remove the circuit breaker state file if it exists."""
    STATE_FILE.unlink(missing_ok=True)


class TestContinueOrchestrationHook:
    """Tests for the Stop hook that blocks stopping during active orchestration."""

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def test_blocks_stop_when_task_in_progress(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "Orchestration loop active" in output["reason"]
        assert "Never stop between tasks" in output["reason"]

    def test_allows_stop_when_no_in_progress(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "| E0-F1-S1-T1 | Task | Task | done | none | repo | `backlog/t1.md` |\n"
            "| E0-F1-S1-T2 | Task | Task | in-queue | none | repo | `backlog/t2.md` |\n"
        )
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_allows_stop_when_all_done(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | done | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_allows_stop_when_no_backlog(self, tmp_path: Path) -> None:
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_allows_stop_when_no_workspace_root(self) -> None:
        result = _run_hook("")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_block_output_is_valid_json(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T2 | Task | Task | in-progress | T1 | repo | `backlog/t2.md` |\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert "decision" in output
        assert "reason" in output

    def test_block_reason_includes_task_id_and_file_path(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "E0-F1-S1-T1" in reason
        assert "backlog/t1.md" in reason

    def test_block_reason_next_step_after_executor(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text(
            "## Status: in-progress\n## Comments\n[2026-04-14T12:00:00] [agent/executor] Implemented changes\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "review-supervisor" in reason

    def test_block_reason_next_step_after_review_pass(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text(
            "## Status: in-progress\n## Comments\n[2026-04-14T12:00:00] [judge/code_review] REVIEW_PASS code_review\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "security" in reason.lower()

    def test_block_reason_next_step_after_security_pass(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text(
            "## Status: in-progress\n"
            "## Comments\n"
            "[2026-04-14T12:00:00] [judge/security_review] security_review REVIEW_PASS\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "git-ops" in reason

    def test_block_reason_next_step_after_git_ops(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text(
            "## Status: in-progress\n## Comments\n[2026-04-14T12:00:00] [agent/git-ops] COMMIT_DEFERRED\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "mark-done" in reason

    def test_block_reason_next_step_after_done(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text("## Status: in-progress\n## Comments\n[2026-04-14T12:00:00] [agent/orchestrator] DONE\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "validate-backlog" in reason

    def test_block_reason_next_step_after_review_fail(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text(
            "## Status: in-progress\n## Comments\n[2026-04-14T12:00:00] [judge/code_review] REVIEW_FAIL code_review\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        reason = output["reason"]
        assert "executor" in reason.lower()


class TestCircuitBreaker:
    """Tests for the stop hook circuit breaker behaviour."""

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def test_circuit_breaker_allows_stop_after_max_blocks(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # Block max_blocks times (use env to set max to 2 for fast test).
        for _ in range(2):
            result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "2"})
            output = json.loads(result.stdout)
            assert output["decision"] == "block"
        # Next call should allow stop (circuit breaker trips).
        result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "2"})
        assert result.stdout.strip() == ""

    def test_circuit_breaker_resets_after_window(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # Seed state file with a first_block_ts far in the past so the window has expired.
        old_ts = int(time.time()) - 300
        STATE_FILE.write_text(json.dumps({"count": 4, "first_block_ts": old_ts}))
        # Should reset counter (window expired) and block normally.
        result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "5"})
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        # Verify counter was reset -- state file should show count 1.
        state = json.loads(STATE_FILE.read_text())
        assert state["count"] == 1

    def test_block_reason_includes_circuit_breaker_count(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert "Circuit breaker:" in output["reason"]

    def test_reads_max_blocks_from_yaml(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        config_dir = tmp_path / "backlog" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "devbench.yaml").write_text("repos:\n  org/repo: {}\nstop_hook:\n  max_blocks: 1\n")
        # First block.
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        # After 1 block, circuit breaker trips.
        result = _run_hook(str(tmp_path))
        assert result.stdout.strip() == ""

    def test_env_var_overrides_yaml(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        config_dir = tmp_path / "backlog" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "devbench.yaml").write_text("repos:\n  org/repo: {}\nstop_hook:\n  max_blocks: 10\n")
        # Env var should override YAML -- set to 1.
        result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "1"})
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        # After 1 block, circuit breaker trips despite YAML saying 10.
        result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "1"})
        assert result.stdout.strip() == ""

    def test_defaults_when_no_yaml_no_env(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # No config file, no env vars -- should use default max_blocks=5.
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert "5" in output["reason"]  # "N/5 blocks"


class TestBlockedTransitionalState:
    """Tests for detecting blocked transitional states."""

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def test_detects_blocked_transitional_state(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        unit_dir = tmp_path / "backlog"
        unit_dir.mkdir()
        unit_file = unit_dir / "t1.md"
        unit_file.write_text("## Status: blocked\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "blocked" in output["reason"].lower()
        assert "devbench next" in output["reason"]


class TestStaleTaskDetection:
    """Tests for detecting stale in-progress tasks."""

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def test_detects_stale_in_progress_task(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # Create log file with an old in-progress timestamp.
        log_dir = tmp_path.parent / "devbench" / "src" / "devbench" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        old_ts = "2026-04-13T01:00:00"
        (log_dir / "orchestrator.log").write_text(f"{old_ts} Set E0-F1-S1-T1 to 'in-progress'\n")
        result = _run_hook(
            str(tmp_path),
            extra_env={"JUDGE_STOP_STALE_MINUTES": "1"},
        )
        output = json.loads(result.stdout)
        assert "stale" in output["reason"].lower() or "may be stale" in output["reason"]


class TestBlockJsonSerialisationRobustness:
    """Issue #130 regression: BLOCK_JSON must serialise via jq, not python3.

    Bug: the original hook called ``python3 -c '...'`` to JSON-encode the
    reason text. When the hook ran in a workspace whose PATH was shadowed by
    an asdf shim with no python version configured, python3 exited 126 and
    the script silently fell back to a literal ``"(reason serialisation
    failed)"`` reason field. The diagnostic-capture python3 invocation also
    silently failed, leaving ``.devbench/stop-hook-diag/`` empty -- masking
    the very root cause the hook was meant to surface.

    Fix: replace both python3 invocations with jq (a hard dependency of the
    rest of the hook chain). These tests pin the new contract by-content so
    a future revert reintroducing python3 fails CI.
    """

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def test_hook_script_does_not_invoke_python3_directly(self) -> None:
        """The hook must not call python3 at all -- jq replaces every usage."""
        text = HOOK_SCRIPT.read_text(encoding="utf-8")
        offending_lines = [
            (i + 1, line)
            for i, line in enumerate(text.splitlines())
            if line.lstrip().startswith("python3 ") or line.lstrip().startswith("$(python3 ") or "python3 -c" in line
        ]
        assert not offending_lines, (
            "continue-orchestration.sh must not call python3 (issue #130). "
            "python3 is shimmed by asdf in some workspaces and exits 126 when "
            "no version is configured, causing BLOCK_JSON to fall back to a "
            f"useless '(reason serialisation failed)' string. Offending lines: {offending_lines!r}"
        )

    def test_block_json_reason_is_real_text_under_minimal_path(self, tmp_path: Path) -> None:
        """Issue #130 reproduction: when PATH excludes python3, the reason
        text must still be a meaningful sentence (not the legacy 'reason
        serialisation failed' fallback)."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E2-F3-S2-T1 | /health | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # PATH=/usr/bin:/bin only -- no asdf, no kanon venv shims.
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert "reason serialisation failed" not in output["reason"], (
            "Hook fell back to the python3-failed literal -- jq should have "
            "produced real reason text instead. Reason: " + output["reason"]
        )
        assert "Orchestration loop active" in output["reason"]
        assert "E2-F3-S2-T1" in output["reason"]

    def test_diagnostic_capture_file_is_written_on_block(self, tmp_path: Path) -> None:
        """Issue #130 reproduction: the diag block must produce one file per
        invocation. The legacy python3 diag block silently swallowed every
        failure (``2>/dev/null || true``), so a python3-shadow environment
        produced zero diag files for hours."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E2-F3-S2-T1 | /health | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        diag_dir = tmp_path / ".devbench" / "stop-hook-diag"
        assert diag_dir.is_dir(), "diag dir should be created"
        diag_files = list(diag_dir.glob("*.json"))
        assert len(diag_files) == 1, f"expected exactly one diag file, found {diag_files!r}"
        diag = json.loads(diag_files[0].read_text(encoding="utf-8"))
        assert diag["task_id"] == "E2-F3-S2-T1"
        assert diag["block_count"] == 1
        # emitted_stdout must be the same JSON the hook wrote to its stdout.
        assert diag["emitted_stdout"] == json.loads(result.stdout)


class TestActiveTaskSelection:
    """Issue #131 regression: pick the most-recently-claimed in-progress
    task from the orchestrator log, not the alphabetically-first BACKLOG.md
    row.

    Bug: ``grep '| in-progress |' BACKLOG.md | head -1`` returned whichever
    row was alphabetically first, masking what the orchestrator was actually
    running. On 2026-05-01 the hook reported ``E1-F2-S1-T4`` (a stale row
    from a 17-hour-old crashed session) while the orchestrator was actively
    on ``E3-F2-S3-T2``.

    Fix: read ``logs/*.log`` for the most recent ``Branch ready: ... on
    <task_id>`` or ``Set <task_id> to 'in-progress'`` entry. Fall back to
    head -1 only if no log entry parses. List all in-progress IDs in the
    reason text when more than one exists.
    """

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def _seed_two_in_progress(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "| E1-F2-S1-T4 | stale row | Task | in-progress | none | repo | `backlog/old.md` |\n"
            "| E3-F2-S3-T2 | active row | Task | in-progress | none | repo | `backlog/new.md` |\n"
        )

    def test_log_driven_picker_selects_active_task_not_head_1(self, tmp_path: Path) -> None:
        """The active task is the one most-recently named in the orchestrator
        log, regardless of its alphabetic position in BACKLOG.md."""
        self._seed_two_in_progress(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "backlog-a-orchestrator.log").write_text(
            "2026-05-01T20:00:00Z [devbench.cli] INFO old\n"
            "2026-05-01T21:06:35Z [devbench.cli] INFO Branch ready: feat/foo on E3-F2-S3-T2\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert "E3-F2-S3-T2" in output["reason"], (
            f"Active-task picker should have named E3-F2-S3-T2 (most recent claim); reason was: {output['reason']}"
        )
        # The active task must come BEFORE the also-in-progress mention.
        active_idx = output["reason"].find("E3-F2-S3-T2")
        stale_idx = output["reason"].find("E1-F2-S1-T4")
        assert active_idx < stale_idx, (
            "Active task must be named first in the reason, with stale tasks "
            f"in the (also in-progress: ...) suffix; reason was: {output['reason']}"
        )

    def test_other_in_progress_tasks_are_listed(self, tmp_path: Path) -> None:
        """When more than one task is in-progress, the reason must surface
        every ID, not silently hide the others behind head -1."""
        self._seed_two_in_progress(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "backlog-a-orchestrator.log").write_text(
            "2026-05-01T21:06:35Z [devbench.cli] INFO Branch ready: feat/foo on E3-F2-S3-T2\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert "(also in-progress: E1-F2-S1-T4)" in output["reason"]

    def test_falls_back_to_head_1_when_no_log_parseable(self, tmp_path: Path) -> None:
        """Fresh checkout / never-launched workspace: no logs/ dir. The
        hook must still emit a block (alphabetic head -1 fallback), not crash."""
        self._seed_two_in_progress(tmp_path)
        # No logs/ directory -> log-driven picker returns nothing -> fallback.
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        # E1-F2-S1-T4 sorts before E3-F2-S3-T2 alphabetically; head -1 picks it.
        assert "E1-F2-S1-T4" in output["reason"]

    def test_set_in_progress_log_entry_format_is_recognised(self, tmp_path: Path) -> None:
        """The picker accepts both 'Branch ready: ... on <id>' and the older
        backlog_manager 'Set <id> to in-progress' entry."""
        self._seed_two_in_progress(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "backlog-a-orchestrator.log").write_text(
            "2026-05-01T21:06:35Z [devbench.backlog_manager] INFO Set E3-F2-S3-T2 to 'in-progress' in BACKLOG.md\n"
        )
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        active_idx = output["reason"].find("E3-F2-S3-T2")
        stale_idx = output["reason"].find("E1-F2-S1-T4")
        assert active_idx >= 0 and active_idx < stale_idx


class TestPerSessionStateFile:
    """AC-192-15: Stop hook circuit breaker is per-session when DEVBENCH_SESSION_NAME is set.

    When DEVBENCH_SESSION_NAME is set, the state file path must be
    /tmp/devbench-stop-hook-state-<session>.json so that concurrent
    orchestrator sessions maintain independent block counters.
    When DEVBENCH_SESSION_NAME is unset, the shared path
    /tmp/devbench-stop-hook-state.json must continue to be used.
    """

    SESSION_A = "my-session"
    SESSION_B = "other-session"
    STATE_FILE_A = Path(f"/tmp/devbench-stop-hook-state-{SESSION_A}.json")
    STATE_FILE_B = Path(f"/tmp/devbench-stop-hook-state-{SESSION_B}.json")

    def setup_method(self) -> None:
        STATE_FILE.unlink(missing_ok=True)
        self.STATE_FILE_A.unlink(missing_ok=True)
        self.STATE_FILE_B.unlink(missing_ok=True)

    def teardown_method(self) -> None:
        STATE_FILE.unlink(missing_ok=True)
        self.STATE_FILE_A.unlink(missing_ok=True)
        self.STATE_FILE_B.unlink(missing_ok=True)

    def test_uses_session_scoped_state_file_when_session_name_set(self, tmp_path: Path) -> None:
        """When DEVBENCH_SESSION_NAME is set, state is written to the session-scoped path."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path), extra_env={"DEVBENCH_SESSION_NAME": self.SESSION_A})
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert self.STATE_FILE_A.exists(), (
            f"Session-scoped state file {self.STATE_FILE_A} must be created when "
            f"DEVBENCH_SESSION_NAME={self.SESSION_A!r}"
        )
        assert not STATE_FILE.exists(), (
            f"Shared state file {STATE_FILE} must NOT be created when DEVBENCH_SESSION_NAME is set"
        )

    def test_uses_shared_state_file_when_session_name_unset(self, tmp_path: Path) -> None:
        """When DEVBENCH_SESSION_NAME is unset, state is written to the shared path."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        assert STATE_FILE.exists(), (
            f"Shared state file {STATE_FILE} must be created when DEVBENCH_SESSION_NAME is unset"
        )
        assert not self.STATE_FILE_A.exists(), (
            f"Session-scoped state file {self.STATE_FILE_A} must NOT be created when DEVBENCH_SESSION_NAME is unset"
        )

    def test_session_counters_are_independent(self, tmp_path: Path) -> None:
        """Two sessions running against the same workspace must have independent block counters.

        A max_blocks=2 setting means the circuit breaker trips after 2 blocks per session.
        Session A at block 2 should trip; Session B should still be at block 1 (not tripped).
        """
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        env_a = {"DEVBENCH_SESSION_NAME": self.SESSION_A, "JUDGE_STOP_MAX_BLOCKS": "2"}
        env_b = {"DEVBENCH_SESSION_NAME": self.SESSION_B, "JUDGE_STOP_MAX_BLOCKS": "2"}

        # Session A: two blocks -- circuit breaker trips on the 3rd call.
        for _ in range(2):
            result = _run_hook(str(tmp_path), extra_env=env_a)
            assert json.loads(result.stdout)["decision"] == "block"

        # Session B: one block -- must NOT be tripped yet.
        result_b = _run_hook(str(tmp_path), extra_env=env_b)
        assert result_b.stdout.strip() != "", (
            "Session B ran 1 block but should only trip after 2 blocks, not inherit Session A's count"
        )
        assert json.loads(result_b.stdout)["decision"] == "block", (
            "Session B should still block after 1 block (max=2); counters must be independent"
        )

        # Verify session A's state reflects its count.
        state_a = json.loads(self.STATE_FILE_A.read_text())
        assert state_a["count"] == 2, f"Session A should have count=2, got {state_a['count']}"

        # Verify session B's state reflects its count.
        state_b = json.loads(self.STATE_FILE_B.read_text())
        assert state_b["count"] == 1, f"Session B should have count=1, got {state_b['count']}"

        # Session A trips circuit breaker on next call.
        result_a_tripped = _run_hook(str(tmp_path), extra_env=env_a)
        assert result_a_tripped.stdout.strip() == "", (
            "Session A should have tripped the circuit breaker (count reached max_blocks=2)"
        )

    def test_session_state_file_cleared_when_no_in_progress(self, tmp_path: Path) -> None:
        """When no in-progress tasks remain, the session-scoped state file must be removed."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # First, accumulate a block count.
        _run_hook(str(tmp_path), extra_env={"DEVBENCH_SESSION_NAME": self.SESSION_A})
        assert self.STATE_FILE_A.exists(), "Precondition: session state file should exist after one block"

        # Now all tasks done -- hook should remove the session state file.
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | done | none | repo | `backlog/t1.md` |\n")
        result = _run_hook(str(tmp_path), extra_env={"DEVBENCH_SESSION_NAME": self.SESSION_A})
        assert result.returncode == 0
        assert result.stdout.strip() == "", "Hook should allow stop when no in-progress tasks"
        assert not self.STATE_FILE_A.exists(), (
            f"Session-scoped state file {self.STATE_FILE_A} must be removed when no in-progress tasks remain"
        )

    def test_circuit_breaker_trips_per_session(self, tmp_path: Path) -> None:
        """Circuit breaker trips at max_blocks for the session, not the global counter."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        env = {"DEVBENCH_SESSION_NAME": self.SESSION_A, "JUDGE_STOP_MAX_BLOCKS": "2"}
        # Block twice.
        for _ in range(2):
            result = _run_hook(str(tmp_path), extra_env=env)
            assert json.loads(result.stdout)["decision"] == "block"
        # Third call trips circuit breaker.
        result = _run_hook(str(tmp_path), extra_env=env)
        assert result.stdout.strip() == "", "Circuit breaker must trip after max_blocks for a named session"
        # State file must be removed after circuit breaker trips.
        assert not self.STATE_FILE_A.exists(), "Session-scoped state file must be removed after circuit breaker trips"


class TestStopHookEnvelopeShape:
    """Issue #139 regression: emit BOTH the legacy ``decision``/``reason``
    envelope AND the modern ``hookSpecificOutput`` envelope so the block
    decision is honoured across Claude Code 2.x dispatcher versions.

    Bug context: the orchestrator self-terminated 2026-05-02T00:19:21
    despite ``continue-orchestration.sh`` emitting well-formed
    ``{"decision":"block","reason":"..."}`` JSON. One hypothesis: Claude
    Code 2.1.x prefers (or requires) the ``hookSpecificOutput`` envelope
    introduced for the Stop event family. Shipping both shapes in the
    same JSON object is forward-compatible across versions and
    eliminates the schema-drift root cause from the candidate list.
    """

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    def test_block_json_has_both_legacy_and_modern_envelope(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E2-F3-S2-T4 | example | Task | in-progress | none | repo | `backlog/t.md` |\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        # Legacy shape (existing).
        assert output["decision"] == "block"
        assert output.get("reason")
        # Modern shape (issue #139).
        assert "hookSpecificOutput" in output, (
            "BLOCK_JSON must include the hookSpecificOutput envelope so Claude "
            "Code 2.x honours the block decision regardless of which schema "
            "version the dispatcher prefers."
        )
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "Stop"
        assert hso["additionalContext"], (
            "hookSpecificOutput.additionalContext must be non-empty so the "
            "operator-visible reason text reaches the next agent turn."
        )

    def test_legacy_reason_matches_modern_additional_context(self, tmp_path: Path) -> None:
        """The two envelopes must carry the same human-readable reason so an
        operator scanning either shape sees identical context."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E2-F3-S2-T4 | example | Task | in-progress | none | repo | `backlog/t.md` |\n")
        result = _run_hook(str(tmp_path))
        output = json.loads(result.stdout)
        assert output["reason"] == output["hookSpecificOutput"]["additionalContext"], (
            "Legacy 'reason' field and modern 'hookSpecificOutput.additionalContext' "
            "must carry the same text so the two envelopes are interchangeable."
        )


def _make_transcript_line(content_text: str) -> str:
    """Return a single JSONL transcript line containing quota-error text.

    Produces the minimal transcript entry shape that the hook's pattern scanner
    must recognise: a JSON object with a ``message`` key whose ``content``
    array includes a ``text`` block containing ``content_text``.
    """
    entry = {
        "role": "assistant",
        "message": {
            "id": "msg_quota_test",
            "role": "assistant",
            "content": [{"type": "text", "text": content_text}],
        },
    }
    return json.dumps(entry)


def _make_transcript_file(tmp_path: Path, content_text: str) -> Path:
    """Write a minimal JSONL transcript file and return its path."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(_make_transcript_line(content_text) + "\n", encoding="utf-8")
    return transcript


def _make_stop_payload(transcript_path: str) -> str:
    """Return a JSON Stop-event payload containing the given transcript path."""
    return json.dumps({"hook_event_name": "Stop", "transcript_path": transcript_path})


class TestQuotaPatternDetection:
    """AC-193-14: Stop hook scans transcript for quota-error patterns and writes quota_pause.json.

    When the Claude Code Stop hook fires, the hook receives the Stop event
    payload on stdin.  The payload includes ``transcript_path`` pointing to the
    current session transcript JSONL.  The hook must scan recent transcript
    messages for quota-exhaustion text patterns and, on a match, atomically
    write ``quota_pause.json`` to the workspace-root ``.devbench/`` directory
    (or the session-scoped equivalent when ``DEVBENCH_SESSION_NAME`` is set).
    """

    def setup_method(self) -> None:
        _cleanup_state_file()

    def teardown_method(self) -> None:
        _cleanup_state_file()

    # ------------------------------------------------------------------
    # Happy-path: each recognised quota-error pattern triggers a write
    # ------------------------------------------------------------------

    def test_rate_limit_429_pattern_writes_quota_pause_json(self, tmp_path: Path) -> None:
        """SubscriptionRateLimit pattern (HTTP 429 / rate_limit) writes checkpoint."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "Error 429 Too Many Requests: rate_limit exceeded")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint.exists(), (
            "quota_pause.json must be written when a rate-limit pattern is found in the transcript"
        )
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "subscription_rate_limit"
        assert "paused_at" in data

    def test_overloaded_pattern_writes_quota_pause_json(self, tmp_path: Path) -> None:
        """Overloaded error (another 429-class pattern) triggers checkpoint write."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "overloaded_error: The API is temporarily overloaded.")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint.exists(), "quota_pause.json must be written for overloaded_error pattern"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "subscription_rate_limit"

    def test_insufficient_quota_pattern_writes_quota_pause_json(self, tmp_path: Path) -> None:
        """SdkCreditExhausted pattern (insufficient_quota) triggers checkpoint write."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(
            tmp_path, "error_type: insufficient_quota -- your account has no remaining credits"
        )
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint.exists(), "quota_pause.json must be written for insufficient_quota pattern"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "sdk_credit_exhausted"

    def test_bedrock_throttle_pattern_writes_quota_pause_json(self, tmp_path: Path) -> None:
        """BedrockThrottle pattern (ThrottlingException) triggers checkpoint write."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "ThrottlingException: Rate exceeded on the Bedrock endpoint")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint.exists(), "quota_pause.json must be written for ThrottlingException pattern"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "bedrock_throttle"

    def test_bedrock_quota_exceeded_pattern_writes_quota_pause_json(self, tmp_path: Path) -> None:
        """ServiceQuotaExceededException triggers checkpoint write."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "ServiceQuotaExceededException: The account quota is exceeded")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint.exists(), "quota_pause.json must be written for ServiceQuotaExceededException pattern"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "bedrock_throttle"

    def test_api_billing_error_pattern_writes_quota_pause_json(self, tmp_path: Path) -> None:
        """ApiBillingError pattern (HTTP 402 without insufficient_quota) triggers checkpoint."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "HTTP 402 Payment Required: billing account issue detected")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint.exists(), "quota_pause.json must be written for HTTP 402 billing error pattern"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "api_billing_error"

    # ------------------------------------------------------------------
    # Checkpoint JSON structure
    # ------------------------------------------------------------------

    def test_checkpoint_json_has_required_fields(self, tmp_path: Path) -> None:
        """The written quota_pause.json must contain all required fields."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        _run_hook(str(tmp_path), stdin_payload=payload)
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert "paused_at" in data, "checkpoint must include paused_at"
        assert "reset_at" in data, "checkpoint must include reset_at (may be null)"
        assert "reason" in data, "checkpoint must include reason"

    def test_checkpoint_paused_at_is_iso8601_utc(self, tmp_path: Path) -> None:
        """paused_at in the checkpoint must be a parseable ISO-8601 UTC datetime string."""
        import re

        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        _run_hook(str(tmp_path), stdin_payload=payload)
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        paused_at = data["paused_at"]
        # Must match ISO-8601 pattern with UTC offset or Z suffix.
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        assert re.match(pattern, paused_at), f"paused_at must be ISO-8601 UTC, got: {paused_at!r}"

    def test_checkpoint_reset_at_null_when_not_in_transcript(self, tmp_path: Path) -> None:
        """When no reset time is found in the transcript, reset_at must be null."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        _run_hook(str(tmp_path), stdin_payload=payload)
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        # Without a parseable reset time in the transcript, reset_at must be null.
        assert data["reset_at"] is None, (
            f"reset_at should be null when no reset time is present in transcript; got: {data['reset_at']!r}"
        )

    # ------------------------------------------------------------------
    # No false positives: normal transcript content must not trigger writes
    # ------------------------------------------------------------------

    def test_no_quota_pattern_does_not_write_checkpoint(self, tmp_path: Path) -> None:
        """Normal transcript content (no quota errors) must NOT write quota_pause.json."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "Implementation complete. All tests pass. No errors encountered.")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert not checkpoint.exists(), (
            "quota_pause.json must NOT be written when no quota-error pattern is in the transcript"
        )

    def test_no_stdin_payload_does_not_write_checkpoint(self, tmp_path: Path) -> None:
        """When no stdin payload is provided, the hook must not write quota_pause.json."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        result = _run_hook(str(tmp_path))
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert not checkpoint.exists(), "quota_pause.json must NOT be written when stdin provides no transcript_path"

    def test_missing_transcript_file_does_not_write_checkpoint(self, tmp_path: Path) -> None:
        """When the transcript_path in the payload points to a non-existent file, no checkpoint."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        payload = _make_stop_payload(str(tmp_path / "nonexistent.jsonl"))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert not checkpoint.exists(), "quota_pause.json must NOT be written when transcript_path does not exist"

    def test_empty_transcript_does_not_write_checkpoint(self, tmp_path: Path) -> None:
        """An empty transcript file must not trigger a checkpoint write."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert not checkpoint.exists(), "quota_pause.json must NOT be written when transcript is empty"

    # ------------------------------------------------------------------
    # Session-scoped checkpoint path (AC-193-16)
    # ------------------------------------------------------------------

    def test_session_scoped_checkpoint_path_when_session_name_set(self, tmp_path: Path) -> None:
        """When DEVBENCH_SESSION_NAME is set, checkpoint goes to the session-scoped path."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        session_name = "my-test-session"
        result = _run_hook(
            str(tmp_path),
            extra_env={"DEVBENCH_SESSION_NAME": session_name},
            stdin_payload=payload,
        )
        assert result.returncode == 0
        # Session-scoped path: <workspace>/.devbench/sessions/<name>/.devbench/quota_pause.json
        session_checkpoint = tmp_path / ".devbench" / "sessions" / session_name / ".devbench" / "quota_pause.json"
        workspace_checkpoint = tmp_path / ".devbench" / "quota_pause.json"
        assert session_checkpoint.exists(), (
            f"Session-scoped quota_pause.json must be written at {session_checkpoint} "
            f"when DEVBENCH_SESSION_NAME={session_name!r}"
        )
        assert not workspace_checkpoint.exists(), (
            "Workspace-root quota_pause.json must NOT be written when a named session is active"
        )
        data = json.loads(session_checkpoint.read_text(encoding="utf-8"))
        assert data["reason"] == "subscription_rate_limit"

    # ------------------------------------------------------------------
    # Checkpoint is written atomically (temp-then-rename)
    # ------------------------------------------------------------------

    def test_checkpoint_written_via_atomic_temp_rename(self, tmp_path: Path) -> None:
        """The checkpoint file write must be atomic: a tmp file must not remain after the hook exits."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        _run_hook(str(tmp_path), stdin_payload=payload)
        devbench_dir = tmp_path / ".devbench"
        # No tmp files should remain after the hook exits.
        tmp_files = list(devbench_dir.glob("quota_pause.json.tmp*"))
        assert not tmp_files, f"Temporary files must not remain after atomic write; found: {tmp_files!r}"
        # The canonical file must exist.
        assert (devbench_dir / "quota_pause.json").exists()

    # ------------------------------------------------------------------
    # Existing checkpoint is NOT overwritten when already present
    # ------------------------------------------------------------------

    def test_existing_checkpoint_not_overwritten(self, tmp_path: Path) -> None:
        """If quota_pause.json already exists, the hook must not overwrite it."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True)
        existing_checkpoint = devbench_dir / "quota_pause.json"
        original_content = json.dumps(
            {
                "paused_at": "2026-01-01T00:00:00+00:00",
                "reset_at": "2026-01-01T01:00:00+00:00",
                "reason": "subscription_rate_limit",
                "raw_error": "original",
            }
        )
        existing_checkpoint.write_text(original_content, encoding="utf-8")

        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests -- new event")
        payload = _make_stop_payload(str(transcript))
        _run_hook(str(tmp_path), stdin_payload=payload)

        # Content must be unchanged.
        actual = existing_checkpoint.read_text(encoding="utf-8")
        assert json.loads(actual) == json.loads(original_content), (
            "quota_pause.json must NOT be overwritten when it already exists; "
            "the quota-watcher daemon is responsible for clearing the checkpoint"
        )

    # ------------------------------------------------------------------
    # Quota detection does not break normal stop-hook continuation behaviour
    # ------------------------------------------------------------------

    def test_quota_detection_does_not_suppress_block_decision(self, tmp_path: Path) -> None:
        """The stop hook must still emit a block decision even when a quota pattern is found."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        # The hook must still block (task is in-progress).
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block", "Stop hook must still emit decision=block after writing quota checkpoint"

    def test_quota_detection_does_not_affect_allow_stop_when_no_in_progress(self, tmp_path: Path) -> None:
        """Quota detection must not cause a block when no in-progress task exists."""
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | done | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        assert result.returncode == 0
        assert result.stdout.strip() == "", (
            "Stop hook must allow stop (no in-progress task) even when quota pattern is found"
        )

    # ------------------------------------------------------------------
    # Error paths: failures in _detect_and_write_quota_checkpoint must
    # log a diagnostic to stderr and still allow the hook to continue
    # ------------------------------------------------------------------

    def test_unreadable_transcript_logs_error_to_stderr(self, tmp_path: Path) -> None:
        """When the transcript file exists but is not readable, hook logs to stderr and continues.

        The hook should not crash -- it should emit an [ERROR] diagnostic to
        stderr and return without writing quota_pause.json.
        """
        import sys

        if sys.platform == "win32":
            return  # chmod-based permission tests are POSIX-only

        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        # Create transcript file then make it unreadable.
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        Path(transcript).chmod(0o000)
        try:
            payload = _make_stop_payload(str(transcript))
            result = _run_hook(str(tmp_path), stdin_payload=payload)
            # Hook must still exit 0 (best-effort detection).
            assert result.returncode == 0
            # No checkpoint must be written when transcript is unreadable.
            checkpoint = tmp_path / ".devbench" / "quota_pause.json"
            assert not checkpoint.exists(), "quota_pause.json must NOT be written when transcript file is unreadable"
            # An [ERROR] diagnostic must appear on stderr.
            assert "[ERROR] continue-orchestration" in result.stderr, (
                f"Hook must log [ERROR] to stderr when transcript is unreadable; got stderr: {result.stderr!r}"
            )
        finally:
            Path(transcript).chmod(0o644)

    def test_mkdir_failure_logs_error_to_stderr(self, tmp_path: Path) -> None:
        """When the checkpoint directory cannot be created, hook logs to stderr and continues.

        Simulated by placing a regular file where the .devbench directory
        should be, so mkdir -p fails with ENOTDIR.
        """
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E5-F5-S1-T3 | Task | Task | in-progress | none | repo | `backlog/t.md` |\n")
        transcript = _make_transcript_file(tmp_path, "rate_limit: 429 Too Many Requests")
        # Block mkdir by placing a regular file where .devbench/ would be created.
        devbench_blocker = tmp_path / ".devbench"
        devbench_blocker.write_text("not-a-directory", encoding="utf-8")
        payload = _make_stop_payload(str(transcript))
        result = _run_hook(str(tmp_path), stdin_payload=payload)
        # Hook must still exit 0 (best-effort detection).
        assert result.returncode == 0
        # An [ERROR] diagnostic must appear on stderr.
        assert "[ERROR] continue-orchestration" in result.stderr, (
            "Hook must log [ERROR] to stderr when checkpoint directory "
            f"cannot be created; got stderr: {result.stderr!r}"
        )
        # Clean up so teardown doesn't leave stray file.
        devbench_blocker.unlink()
