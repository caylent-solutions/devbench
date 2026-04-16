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
) -> subprocess.CompletedProcess:
    """Run the hook with a given workspace root."""
    env = {"JUDGE_WORKSPACE_ROOT": workspace_root, "PATH": "/usr/bin:/bin"}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
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
        # Verify counter was reset — state file should show count 1.
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
        # Env var should override YAML — set to 1.
        result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "1"})
        output = json.loads(result.stdout)
        assert output["decision"] == "block"
        # After 1 block, circuit breaker trips despite YAML saying 10.
        result = _run_hook(str(tmp_path), extra_env={"JUDGE_STOP_MAX_BLOCKS": "1"})
        assert result.stdout.strip() == ""

    def test_defaults_when_no_yaml_no_env(self, tmp_path: Path) -> None:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("| E0-F1-S1-T1 | Task | Task | in-progress | none | repo | `backlog/t1.md` |\n")
        # No config file, no env vars — should use default max_blocks=5.
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
