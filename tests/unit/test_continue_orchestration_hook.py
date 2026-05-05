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
