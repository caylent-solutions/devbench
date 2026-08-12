"""Tests for E2-F4-S3-T1: the in-process quota-resume loop.

Covers ``_resolve_max_quota_resumes`` (fail-safe env parse, spec AC-22),
``_should_resume_after_quota_recovery`` (audit markers + bound, spec FR-2.10),
``_OrchestrateLoopResult`` (clean / drain / non-recovering-quota shapes), and
``_drive_orchestrate_with_quota_resume`` (fresh-session resume per decision
D-6, spec AC-21) together with the three ``resume_strategy`` behaviours it
surfaces via ``_dispatch_quota_detection`` -> ``_handle_quota_pause`` (spec
AC-23) and drain-preservation across the loop's exit paths (spec AC-25).

Also covers ``cmd_start`` wiring: the entry point drives
``_drive_orchestrate_with_quota_resume`` instead of a bare
``asyncio.run(_run())``, and both of its exit-path drain cleanups go through
``_cancel_drain_unless_requested`` instead of an unconditional
``cancel_drain`` call.
"""

from __future__ import annotations

import inspect
import logging
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config_loader import QuotaHandlingConfig
from devbench.drain import read_drain_state
from devbench.quota import SubscriptionRateLimitError


def _make_quota_exc(source: str = "anthropic-api", reset_at: datetime | None = None) -> SubscriptionRateLimitError:
    """Build a real QuotaExhaustedError subclass instance."""
    return SubscriptionRateLimitError(reset_at=reset_at, raw_error="raw", source=source)


def _make_quota_detected(source: str = "anthropic-api") -> cli._QuotaDetected:
    """Build a real _QuotaDetected sentinel wrapping a fresh quota exception."""
    return cli._QuotaDetected(_make_quota_exc(source=source))


def _write_in_progress_wu(tmp_path: Path, unit_id: str = "E1-F1-S1-T1") -> WorkUnit:
    """Write a minimal in-progress work-unit .md file to disk and return its WorkUnit."""
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    wu_file = backlog_dir / f"{unit_id}.md"
    wu_file.write_text(
        f"# {unit_id}: Test Task\n\n## Status: in-progress\n\n## Comments\n",
        encoding="utf-8",
    )
    return WorkUnit(
        id=unit_id,
        title="Test Task",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=wu_file,
        repo="test/repo",
    )


def _write_backlog_workspace(tmp_path: Path, units: list[tuple[str, str]]) -> Path:
    """Write a real BACKLOG.md plus one work-unit .md file per entry in *units*.

    Args:
        tmp_path: Workspace root.
        units: List of ``(unit_id, status)`` pairs; ``status`` is the exact
            lowercase CLI-form status string (e.g. ``"in-progress"``).

    Returns:
        The file path of the FIRST unit in *units* (call-site convenience for
        the common single-unit case).
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    header = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
    )
    rows = []
    first_path: Path | None = None
    for unit_id, status in units:
        row = f"| {unit_id} | Test Task {unit_id} | Task | {status} | None | git-repo | `backlog/{unit_id}.md` |\n"
        rows.append(row)
        wu_content = f"# {unit_id}: Test Task {unit_id}\n\n## Status: {status}\n\n## Comments\n"
        wu_path = backlog_dir / f"{unit_id}.md"
        wu_path.write_text(wu_content, encoding="utf-8")
        if first_path is None:
            first_path = wu_path
    (tmp_path / "BACKLOG.md").write_text(header + "".join(rows), encoding="utf-8")
    assert first_path is not None
    return first_path


class TestResolveMaxQuotaResumes:
    """``_resolve_max_quota_resumes`` reads DEVBENCH_MAX_QUOTA_RESUMES fail-safe (spec AC-22)."""

    @pytest.mark.unit
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E2-F4-S3-T1-3: no env var yields DEFAULT_MAX_QUOTA_RESUMES."""
        monkeypatch.delenv("DEVBENCH_MAX_QUOTA_RESUMES", raising=False)
        assert cli._resolve_max_quota_resumes() == cli.DEFAULT_MAX_QUOTA_RESUMES

    @pytest.mark.unit
    def test_valid_positive_int_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "5")
        assert cli._resolve_max_quota_resumes() == 5

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw",
        ["not-an-int", "0", "-3", "   ", "3.5"],
        ids=["non-integer", "zero", "negative", "blank", "float-string"],
    )
    def test_fail_safe_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        """AC-E2-F4-S3-T1-3: invalid or non-positive values fall back to the default (spec AC-22)."""
        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", raw)
        assert cli._resolve_max_quota_resumes() == cli.DEFAULT_MAX_QUOTA_RESUMES


class TestShouldResumeAfterQuotaRecovery:
    """``_should_resume_after_quota_recovery`` emits the two resume audit markers (spec AC-22)."""

    @pytest.mark.unit
    def test_permitted_resume_emits_marker_and_returns_true(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            result = cli._should_resume_after_quota_recovery(resumes_used=0, max_resumes=3)
        assert result is True
        assert "[ORCHESTRATOR_QUOTA_RESUME] resume=1 max=3" in caplog.text

    @pytest.mark.unit
    def test_second_permitted_resume_increments_the_resume_number(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            result = cli._should_resume_after_quota_recovery(resumes_used=1, max_resumes=3)
        assert result is True
        assert "[ORCHESTRATOR_QUOTA_RESUME] resume=2 max=3" in caplog.text

    @pytest.mark.unit
    def test_bound_exhausted_emits_marker_and_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            result = cli._should_resume_after_quota_recovery(resumes_used=3, max_resumes=3)
        assert result is False
        assert "[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=3" in caplog.text
        assert "[ORCHESTRATOR_QUOTA_RESUME]" not in caplog.text.replace("[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]", "")

    @pytest.mark.unit
    def test_bound_already_exceeded_still_refuses(self, caplog: pytest.LogCaptureFixture) -> None:
        """resumes_used beyond max_resumes (should never happen, but must still refuse)."""
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            result = cli._should_resume_after_quota_recovery(resumes_used=5, max_resumes=3)
        assert result is False
        assert "[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=3" in caplog.text


class TestNoClaudeSdkClientRefactorPorted:
    """Decision D-6: the source branch's ClaudeSDKClient refactor is NOT ported."""

    @pytest.mark.unit
    def test_no_claude_sdk_client_reference_in_cli_source(self) -> None:
        source = inspect.getsource(cli)
        assert "ClaudeSDKClient" not in source


class TestDriveOrchestrateWithQuotaResume:
    """``_drive_orchestrate_with_quota_resume`` drives a bounded, fresh-session resume loop."""

    @pytest.mark.unit
    def test_fresh_session_after_recovery_opens_exactly_two(self, tmp_path: Path) -> None:
        """AC-E2-F4-S3-T1-1: after one quota-detect-then-recover cycle, exactly two
        sessions were constructed; the first conversation is never resumed (spec AC-21)."""
        sessions = {"opened": 0}

        async def run() -> None:
            sessions["opened"] += 1
            if sessions["opened"] == 1:
                raise cli._QuotaDetected(_make_quota_exc())

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_WAIT_RECOVERED,
            ) as mock_dispatch,
        ):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")

        assert sessions["opened"] == 2
        assert result.terminal_rc is None
        assert result.stop_reason == "clean"
        mock_dispatch.assert_called_once()

    @pytest.mark.unit
    def test_context_comes_from_backlog_on_disk_not_transcript(self, tmp_path: Path) -> None:
        """D-6: ``run`` is invoked as a bare zero-arg coroutine factory on every
        iteration -- no transcript, message history, or client handle is threaded
        between calls. Context flows only through the backlog on disk."""
        call_log: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def run(*args: object, **kwargs: object) -> None:
            call_log.append((args, kwargs))
            if len(call_log) == 1:
                raise cli._QuotaDetected(_make_quota_exc())

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_WAIT_RECOVERED,
            ),
        ):
            cli._drive_orchestrate_with_quota_resume(run, "default")

        assert call_log == [((), {}), ((), {})]

    @pytest.mark.unit
    def test_clean_run_returns_terminal_rc_none(self, tmp_path: Path) -> None:
        """``_run`` returning normally on the first try yields the clean-exit shape."""

        async def run() -> None:
            return None

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")

        assert result == cli._OrchestrateLoopResult(None, "clean", False)

    @pytest.mark.unit
    def test_drain_requested_inside_loop_returns_terminal_drain_result(self, tmp_path: Path) -> None:
        """A _DrainRequested raised by ``run`` is consumed and reported as a terminal drain exit."""
        from devbench.drain import request_drain

        request_drain(tmp_path, reason="operator-freeze")

        async def run() -> None:
            raise cli._DrainRequested("operator-freeze")

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")

        assert result.terminal_rc == 0
        assert result.stop_reason == "drain enforced: operator-freeze"
        assert result.quota_drain_requested is False
        assert read_drain_state(tmp_path) is None

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("stop_reason", "expect_drain_requested"),
        [
            (cli._QUOTA_STOP_REASON_DRAIN_DETECTION, True),
            (cli._QUOTA_STOP_REASON_DRAIN_TIMEOUT, True),
            (cli._QUOTA_STOP_REASON_TIMEOUT_KEEP_WAITING, False),
        ],
        ids=["drain-detection", "drain-timeout", "keep-waiting"],
    )
    def test_non_recovering_quota_disposition_is_terminal(
        self, tmp_path: Path, stop_reason: str, expect_drain_requested: bool
    ) -> None:
        """AC-E2-F4-S3-T1-7: a non-recovering quota disposition ends the loop with
        terminal_rc=0 and quota_drain_requested reflecting whether the disposition
        was a drain (so cmd_start's finally block preserves it, spec AC-25)."""

        async def run() -> None:
            raise cli._QuotaDetected(_make_quota_exc())

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._dispatch_quota_detection", return_value=stop_reason),
        ):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")

        assert result.terminal_rc == 0
        assert result.stop_reason == stop_reason
        assert result.quota_drain_requested is expect_drain_requested

    @pytest.mark.unit
    def test_resume_cap_exhausted_stops_looping(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E2-F4-S3-T1-4: the loop never spins forever -- it stops at the resolved cap."""
        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "2")
        call_count = {"n": 0}

        async def run() -> None:
            call_count["n"] += 1
            raise cli._QuotaDetected(_make_quota_exc())

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_WAIT_RECOVERED,
            ),
        ):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")

        # 1 initial attempt + 2 permitted resumes = 3 sessions opened before the
        # cap refuses a 4th.
        assert call_count["n"] == 3
        assert result.terminal_rc == 0
        assert result.stop_reason == "quota-resume-cap-exhausted"
        assert result.quota_drain_requested is False

    @pytest.mark.unit
    def test_resume_markers_emitted_across_a_full_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-E2-F4-S3-T1-4: [ORCHESTRATOR_QUOTA_RESUME] on each permitted resume, then
        [ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] exactly when the bound is reached."""
        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "1")

        async def run() -> None:
            raise cli._QuotaDetected(_make_quota_exc())

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_WAIT_RECOVERED,
            ),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")

        assert "[ORCHESTRATOR_QUOTA_RESUME] resume=1 max=1" in caplog.text
        assert "[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=1" in caplog.text
        assert result.stop_reason == "quota-resume-cap-exhausted"

    @pytest.mark.unit
    def test_dispatch_quota_detection_receives_session_name(self, tmp_path: Path) -> None:
        async def run() -> None:
            raise cli._QuotaDetected(_make_quota_exc())

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_TIMEOUT_KEEP_WAITING,
            ) as mock_dispatch,
        ):
            cli._drive_orchestrate_with_quota_resume(run, "my-named-session")

        args = mock_dispatch.call_args.args
        assert args[1] == "my-named-session"


class TestOrchestrateLoopResultShapes:
    """``_OrchestrateLoopResult`` distinguishes clean, drain, and non-recovering
    quota outcomes (Definition of Done)."""

    @pytest.mark.unit
    def test_fields_are_positional_and_named(self) -> None:
        result = cli._OrchestrateLoopResult(
            terminal_rc=0, stop_reason="quota-resume-cap-exhausted", quota_drain_requested=True
        )
        assert result.terminal_rc == 0
        assert result.stop_reason == "quota-resume-cap-exhausted"
        assert result.quota_drain_requested is True

    @pytest.mark.unit
    def test_clean_shape_is_terminal_rc_none(self) -> None:
        result = cli._OrchestrateLoopResult(None, "clean", False)
        assert result.terminal_rc is None
        assert result.quota_drain_requested is False

    @pytest.mark.unit
    def test_three_distinct_shapes_are_not_equal(self) -> None:
        clean = cli._OrchestrateLoopResult(None, "clean", False)
        drain = cli._OrchestrateLoopResult(0, "drain enforced: reason", False)
        non_recovering_quota = cli._OrchestrateLoopResult(0, cli._QUOTA_STOP_REASON_DRAIN_DETECTION, True)
        assert clean != drain
        assert drain != non_recovering_quota
        assert clean != non_recovering_quota


class TestResumeStrategiesThroughTheLoop:
    """AC-E2-F4-S3-T1-5/6: the three ``resume_strategy`` values, applied on the
    recovery path inside ``_handle_quota_pause``, take effect within the resume
    loop and a ``drain_and_resume`` drain request survives the loop's re-entry
    (spec AC-23, AC-25)."""

    def _run_recover_then_clean(self, tmp_path: Path, resume_strategy: str) -> cli._OrchestrateLoopResult:
        """Drive one full quota-detect -> recover -> fresh-session-clean-exit cycle
        with the real dispatch/pause/apply-resume-strategy pipeline (only the
        notification and wait primitives are stubbed)."""
        call_count = {"n": 0}

        async def run() -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise cli._QuotaDetected(_make_quota_exc())

        cfg = SimpleNamespace(quota_handling=QuotaHandlingConfig(on_exhaustion="wait", resume_strategy=resume_strategy))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.save_checkpoint"),
            patch("devbench.cli.wait_for_reset", new=AsyncMock(return_value=True)),
            patch("devbench.cli._fire_quota_waiting_notification"),
            patch("devbench.cli._fire_quota_resumed_notification"),
            patch("devbench.cli._append_quota_audit_comment"),
        ):
            result = cli._drive_orchestrate_with_quota_resume(run, "default")
        assert call_count["n"] == 2
        return result

    @pytest.mark.unit
    def test_continue_current_wu_keeps_claimed_unit_in_progress(self, tmp_path: Path) -> None:
        wu = _write_in_progress_wu(tmp_path)
        result = self._run_recover_then_clean(tmp_path, "continue_current_wu")
        assert result.terminal_rc is None
        content = wu.file_path.read_text(encoding="utf-8")
        assert "## Status: in-progress" in content

    @pytest.mark.unit
    def test_restart_wu_forces_in_progress_units_back_to_in_queue(self, tmp_path: Path) -> None:
        wu_path = _write_backlog_workspace(tmp_path, [("E1-F1-S1-T1", "in-progress"), ("E1-F1-S1-T2", "in-queue")])
        result = self._run_recover_then_clean(tmp_path, "restart_wu")
        assert result.terminal_rc is None
        assert "## Status: in-queue" in wu_path.read_text(encoding="utf-8")

    @pytest.mark.unit
    def test_drain_and_resume_requests_a_drain_that_survives_the_resumed_session(self, tmp_path: Path) -> None:
        """The drain signal written by 'drain_and_resume' is still present after the
        loop has re-entered and completed a second, unrelated fresh session --
        proving it is not silently dropped by the resume itself."""
        result = self._run_recover_then_clean(tmp_path, "drain_and_resume")
        assert result.terminal_rc is None
        drain_state = read_drain_state(tmp_path)
        assert drain_state is not None

    @pytest.mark.unit
    def test_unknown_resume_strategy_raises_value_error_naming_allowed_set(self, tmp_path: Path) -> None:
        """AC-E2-F4-S3-T1-5: an unrecognised resume_strategy raises ValueError from
        inside the loop, naming the allowed set (spec AC-23)."""

        async def run() -> None:
            raise cli._QuotaDetected(_make_quota_exc())

        cfg = SimpleNamespace(
            quota_handling=QuotaHandlingConfig(on_exhaustion="wait", resume_strategy="not_a_real_strategy")
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.save_checkpoint"),
            patch("devbench.cli.wait_for_reset", new=AsyncMock(return_value=True)),
            patch("devbench.cli._fire_quota_waiting_notification"),
            patch("devbench.cli._fire_quota_resumed_notification"),
            patch("devbench.cli._append_quota_audit_comment"),
        ):
            with pytest.raises(ValueError) as excinfo:
                cli._drive_orchestrate_with_quota_resume(run, "default")
        assert "not_a_real_strategy" in str(excinfo.value)
        assert "continue_current_wu" in str(excinfo.value)
        assert "restart_wu" in str(excinfo.value)
        assert "drain_and_resume" in str(excinfo.value)


class TestCmdStartUsesResumeLoop:
    """AC-E2-F4-S3-T1-7: the orchestrate entry point drives
    ``_drive_orchestrate_with_quota_resume`` in place of a bare
    ``asyncio.run(_run())`` call, preserving stop-reason and notification
    semantics."""

    def _drive_cmd_start_with_batches(self, tmp_path: Path, message_batches: list[list[object]]) -> tuple[int, int]:
        """Run cmd_start against a fake SDK whose ``query()`` yields the i-th batch
        of *message_batches* on its i-th invocation. Returns (rc, sessions_opened)."""
        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()
        call_count = {"n": 0}

        async def mock_query(**kwargs: object) -> object:
            idx = call_count["n"]
            call_count["n"] += 1
            for message in message_batches[idx]:
                yield message

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_WAIT_RECOVERED,
            ),
        ):
            rc = cli.cmd_start()
        return rc, call_count["n"]

    @pytest.mark.unit
    def test_quota_detected_message_resumes_with_a_second_fresh_session(self, tmp_path: Path) -> None:
        """A quota-shaped message on the first session no longer escapes cmd_start
        uncaught (unlike the E2-F4-S1-T1-era wiring) -- it is dispatched, recovers,
        and cmd_start opens a fresh second SDK session that then exits cleanly."""
        rate_limit_message = SimpleNamespace(error="rate_limit", content=None, status_code=None, body={})
        terminal_message = SimpleNamespace(result="NO_ACTIONABLE -- 1/1 done, 0 blocked")
        rc, sessions_opened = self._drive_cmd_start_with_batches(tmp_path, [[rate_limit_message], [terminal_message]])
        assert rc == 0
        assert sessions_opened == 2

    @pytest.mark.unit
    def test_clean_single_session_run_still_returns_zero(self, tmp_path: Path) -> None:
        terminal_message = SimpleNamespace(result="ALL_DONE")
        rc, sessions_opened = self._drive_cmd_start_with_batches(tmp_path, [[terminal_message]])
        assert rc == 0
        assert sessions_opened == 1


class TestCmdStartCancelDrainUnlessRequested:
    """AC-E2-F4-S3-T1-7/DoD: cmd_start's exit-path drain cleanups go through
    ``_cancel_drain_unless_requested`` rather than an unconditional
    ``cancel_drain`` call, so a drain the quota disposition deliberately
    requested survives process exit (spec AC-25)."""

    def _drive_cmd_start_with_quota_drain_disposition(self, tmp_path: Path) -> int:
        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield SimpleNamespace(error="rate_limit", content=None, status_code=None, body={})

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
            patch(
                "devbench.cli._dispatch_quota_detection",
                return_value=cli._QUOTA_STOP_REASON_DRAIN_DETECTION,
            ),
        ):
            return cli.cmd_start()

    @pytest.mark.unit
    def test_drain_disposition_survives_cmd_start_exit(self, tmp_path: Path) -> None:
        """cancel_drain is never called unconditionally when the quota disposition
        itself requested the drain -- proven by an actual on-disk drain signal
        surviving the full cmd_start call, not by mocking cancel_drain."""
        from devbench.drain import request_drain

        request_drain(tmp_path, reason="quota-exhaustion:anthropic-api")
        rc = self._drive_cmd_start_with_quota_drain_disposition(tmp_path)
        assert rc == 0
        assert read_drain_state(tmp_path) is not None

    @pytest.mark.unit
    def test_no_drain_disposition_cancels_any_stale_drain(self, tmp_path: Path) -> None:
        """When the quota disposition is 'clean' (no drain requested), a stale
        drain signal left over from a previous run is still cancelled on exit."""
        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield SimpleNamespace(result="ALL_DONE")

        mock_sdk.query = mock_query
        from devbench.drain import request_drain

        request_drain(tmp_path, reason="stale-from-earlier-run")

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start()
        assert rc == 0
        assert read_drain_state(tmp_path) is None
