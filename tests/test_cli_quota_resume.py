"""Tests for E2-F4-S1-T1 quota detection and E2-F4-S2-T1 pause dispatch.

Covers the ``_QuotaDetected`` sentinel (spec AC-20, decision D-4), the
``_check_quota_and_drain`` extraction that fuses quota detection with the
existing drain-on-claim short-circuit (issues #188/#212) so ``_run`` stays
under ruff's PLR0912 branch cap (issue #236, #234, #235), and the
``_dispatch_quota_detection`` / ``_handle_quota_pause`` /
``_dispatch_quota_timeout`` / ``_cancel_drain_unless_requested`` dispatch
policy and pause sequence (spec FR-2.9, FR-2.10, FR-2.12).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.manager import BacklogManager
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config_loader import QuotaHandlingConfig
from devbench.constants import SESSION_DEFAULT_NAME, SESSION_SESSIONS_BASE_DIR
from devbench.drain import read_drain_state
from devbench.quota import QuotaExhaustedError, RecoveryProbeUnavailableError, SubscriptionRateLimitError


def _make_sdk_exc(status_code: int) -> MagicMock:
    """Build a synthetic Anthropic-SDK-style exception recognized by detect_quota_error rule 2."""
    exc = MagicMock(spec=Exception)
    exc.status_code = status_code
    exc.message = "rate limited"
    exc.body = {"error": {"message": "rate limited"}}
    return exc


def _make_rate_limit_message() -> SimpleNamespace:
    """Build an SDK-message-shaped object matching detect_quota_error rule 7 (error='rate_limit')."""
    return SimpleNamespace(error="rate_limit", content=None, status_code=None, body={})


def _make_claim_message() -> object:
    """Build an AssistantMessage containing a Bash 'devbench claim' tool-use."""
    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    return AssistantMessage(
        content=[
            ToolUseBlock(
                id="tu-claim",
                name="Bash",
                input={"command": "uv run devbench claim E1-F2-S1-T1"},
            )
        ],
        model="claude-opus-4-5",
    )


def _make_quota_exc(source: str = "anthropic-api", reset_at: datetime | None = None) -> SubscriptionRateLimitError:
    """Build a real QuotaExhaustedError subclass instance for dispatch/pause tests."""
    return SubscriptionRateLimitError(reset_at=reset_at, raw_error="raw", source=source)


def _make_quota_detected(source: str = "anthropic-api", reset_at: datetime | None = None) -> cli._QuotaDetected:
    """Build a real _QuotaDetected sentinel wrapping a fresh quota exception."""
    return cli._QuotaDetected(_make_quota_exc(source=source, reset_at=reset_at))


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


@pytest.fixture
def quota_pause_mocks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch the six collaborators ``_handle_quota_pause`` depends on.

    Extracted so the six-mock patch stack is not copy-pasted across every
    ``TestHandleQuotaPause`` test (test_review DRY_VIOLATION, attempt 1).
    ``wait_for_reset`` defaults to recovering immediately; callers override
    ``mocks.wait_for_reset.return_value`` / ``.side_effect`` per scenario.
    """
    mocks = SimpleNamespace(
        save_checkpoint=MagicMock(),
        wait_for_reset=AsyncMock(return_value=True),
        fire_waiting=MagicMock(),
        fire_resumed=MagicMock(),
        apply_resume=MagicMock(),
        append_audit=MagicMock(),
    )
    monkeypatch.setattr(cli, "save_checkpoint", mocks.save_checkpoint)
    monkeypatch.setattr(cli, "wait_for_reset", mocks.wait_for_reset)
    monkeypatch.setattr(cli, "_fire_quota_waiting_notification", mocks.fire_waiting)
    monkeypatch.setattr(cli, "_fire_quota_resumed_notification", mocks.fire_resumed)
    monkeypatch.setattr(cli, "_apply_resume_strategy", mocks.apply_resume)
    monkeypatch.setattr(cli, "_append_quota_audit_comment", mocks.append_audit)
    return mocks


class TestQuotaDetectedSentinel:
    """``_QuotaDetected`` is a BaseException subclass, not an Exception subclass (spec AC-20, D-4)."""

    @pytest.mark.unit
    def test_quota_detected_subclasses_base_exception(self) -> None:
        """AC-E2-F4-S1-T1-1: _QuotaDetected subclasses BaseException."""
        assert issubclass(cli._QuotaDetected, BaseException)

    @pytest.mark.unit
    def test_quota_detected_does_not_subclass_exception(self) -> None:
        """AC-E2-F4-S1-T1-1: _QuotaDetected must NOT subclass Exception (D-4)."""
        assert not issubclass(cli._QuotaDetected, Exception)

    @pytest.mark.unit
    def test_quota_detected_carries_wrapped_quota_exc(self) -> None:
        """_QuotaDetected preserves the wrapped QuotaExhaustedError instance."""
        quota_exc = SubscriptionRateLimitError(reset_at=None, raw_error="raw", source="anthropic-api")
        detected = cli._QuotaDetected(quota_exc)
        assert detected.quota_exc is quota_exc

    @pytest.mark.unit
    def test_quota_detected_escapes_broad_exception_handler(self) -> None:
        """AC-E2-F4-S1-T1-2: the sentinel propagates through asyncio.run past a broad except Exception (D-4)."""
        quota_exc = SubscriptionRateLimitError(reset_at=None, raw_error="raw", source="anthropic-api")

        async def _raise_quota() -> None:
            raise cli._QuotaDetected(quota_exc)

        with pytest.raises(cli._QuotaDetected) as excinfo:
            try:
                asyncio.run(_raise_quota())
            except Exception:
                # A broad `except Exception` MUST NOT catch _QuotaDetected because
                # it subclasses BaseException directly, not Exception (D-4).
                pytest.fail("except Exception incorrectly caught _QuotaDetected")
        assert excinfo.value.quota_exc is quota_exc


class TestCheckQuotaAndDrain:
    """``_check_quota_and_drain`` fuses quota detection with drain-on-claim (issues #188/#212/#236)."""

    @pytest.mark.unit
    def test_check_quota_and_drain_raises_quota_detected_on_quota_message(self, tmp_path: Path) -> None:
        """AC-E2-F4-S1-T1-3: a quota-shaped message raises _QuotaDetected wrapping the detected error."""
        message = _make_rate_limit_message()
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            with pytest.raises(cli._QuotaDetected) as excinfo:
                cli._check_quota_and_drain(message)
        assert isinstance(excinfo.value.quota_exc, QuotaExhaustedError)

    @pytest.mark.unit
    def test_check_quota_and_drain_raises_quota_detected_on_http_429(self, tmp_path: Path) -> None:
        """A raw SDK 429 exception surfacing as an SDK message also raises _QuotaDetected."""
        message = _make_sdk_exc(429)
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            with pytest.raises(cli._QuotaDetected) as excinfo:
                cli._check_quota_and_drain(message)
        assert isinstance(excinfo.value.quota_exc, SubscriptionRateLimitError)

    @pytest.mark.unit
    def test_check_quota_and_drain_raises_drain_requested_on_claim(self, tmp_path: Path) -> None:
        """AC-E2-F4-S1-T1-4: a claim tool-use with pending drain state raises _DrainRequested with the reason."""
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": "freeze"}',
            encoding="utf-8",
        )
        message = _make_claim_message()
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": SESSION_DEFAULT_NAME}),
        ):
            with pytest.raises(cli._DrainRequested) as excinfo:
                cli._check_quota_and_drain(message)
        assert excinfo.value.reason == "freeze"

    @pytest.mark.unit
    def test_check_quota_and_drain_ignores_claim_without_drain_state(self, tmp_path: Path) -> None:
        """AC-E2-F4-S1-T1-5: a claim tool-use with no pending drain state raises nothing."""
        message = _make_claim_message()
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": SESSION_DEFAULT_NAME}),
        ):
            # No `pytest.raises` context: an uncaught exception here fails the
            # test, which is the assertion -- _check_quota_and_drain must
            # raise nothing when no drain signal is pending (AC-E2-F4-S1-T1-5).
            cli._check_quota_and_drain(message)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "message",
        [
            "plain string message",
            SimpleNamespace(),
            SimpleNamespace(content="not a list"),
            object(),
        ],
        ids=["plain-string", "empty-namespace", "non-list-content", "bare-object"],
    )
    def test_check_quota_and_drain_malformed_message_passes_silently(self, tmp_path: Path, message: object) -> None:
        """AC-E2-F4-S1-T1-5: malformed / unrelated messages raise nothing (detect_quota_error no-raise contract)."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # No `pytest.raises` context: an uncaught exception here fails the
            # test, which is the assertion -- a malformed/unrelated message
            # must pass through silently (AC-E2-F4-S1-T1-5).
            cli._check_quota_and_drain(message)


class TestRunCallsCheckQuotaAndDrainPerMessage:
    """AC-E2-F4-S1-T1-3/4: ``_run`` calls ``_check_quota_and_drain`` once per SDK message.

    Proves the previous inline drain-on-claim conditional was replaced, not duplicated.
    """

    def _drive_cmd_start_with_messages(self, tmp_path: Path, messages: list[object]) -> int | None:
        """Run cmd_start against a fake SDK yielding *messages*; returns rc or None if it raised out."""
        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            for message in messages:
                yield message

        mock_sdk.query = mock_query

        import sys

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            return cli.cmd_start()

    @pytest.mark.unit
    def test_run_invokes_check_quota_and_drain_once_per_message(self, tmp_path: Path) -> None:
        """_check_quota_and_drain is invoked exactly once for each SDK message the loop observes."""
        messages: list[object] = ["msg-one", "msg-two", "msg-three"]
        with patch("devbench.cli._check_quota_and_drain") as mock_check:
            rc = self._drive_cmd_start_with_messages(tmp_path, messages)
        assert rc == 0
        assert mock_check.call_count == len(messages)
        assert [call.args[0] for call in mock_check.call_args_list] == messages

    @pytest.mark.unit
    def test_cmd_start_propagates_quota_detected_out_of_the_sdk_loop(self, tmp_path: Path) -> None:
        """A quota-shaped SDK message causes _QuotaDetected to escape cmd_start (D-4: no dispatch wired here)."""
        rate_limit_message = _make_rate_limit_message()
        with pytest.raises(cli._QuotaDetected):
            self._drive_cmd_start_with_messages(tmp_path, [rate_limit_message])

    @pytest.mark.unit
    def test_cmd_start_no_duplicate_drain_check_still_enforces_drain(self, tmp_path: Path) -> None:
        """The inline drain-on-claim conditional is fully replaced: claim+drain still returns rc=0 (#188/#212)."""
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": "freeze"}',
            encoding="utf-8",
        )
        rc = self._drive_cmd_start_with_messages(tmp_path, [_make_claim_message()])
        assert rc == 0
        assert not signal_path.exists()


class TestDispatchQuotaTimeoutActions:
    """_QUOTA_TIMEOUT_ACTIONS is the single source of truth for the timeout guard and its ValueError."""

    @pytest.mark.unit
    def test_quota_timeout_actions_frozenset_matches_documented_values(self) -> None:
        assert frozenset({"drain", "fail", "keep_waiting"}) == cli._QUOTA_TIMEOUT_ACTIONS


class TestDispatchQuotaDetection:
    """_dispatch_quota_detection applies the quota_handling on_exhaustion policy (FR-2.9)."""

    @pytest.mark.unit
    def test_disabled_reraises_legacy_rc1(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-1: enabled=false re-raises the wrapped error (spec AC-24, #193 AC-4)."""
        quota_exc = _make_quota_exc()
        detected = cli._QuotaDetected(quota_exc)
        cfg = SimpleNamespace(quota_handling=QuotaHandlingConfig(enabled=False))
        with patch("devbench.cli.RUNTIME_CONFIG", cfg), patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            with pytest.raises(SubscriptionRateLimitError) as excinfo:
                cli._dispatch_quota_detection(detected, session_name="default")
        assert excinfo.value is quota_exc

    @pytest.mark.unit
    def test_fail_logs_marker_and_reraises(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """AC-E2-F4-S2-T1-2: on_exhaustion=fail emits [QUOTA_FAIL_FAST] then re-raises."""
        quota_exc = _make_quota_exc(source="bedrock")
        detected = cli._QuotaDetected(quota_exc)
        cfg = SimpleNamespace(quota_handling=QuotaHandlingConfig(enabled=True, on_exhaustion="fail"))
        with (
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            with pytest.raises(SubscriptionRateLimitError) as excinfo:
                cli._dispatch_quota_detection(detected, session_name="default")
        assert excinfo.value is quota_exc
        assert "[QUOTA_FAIL_FAST]" in caplog.text
        assert "reason=bedrock" in caplog.text

    @pytest.mark.unit
    def test_drain_requests_drain_and_stops(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-E2-F4-S2-T1-2: on_exhaustion=drain emits [QUOTA_DRAIN_REQUESTED] phase=detection, requests, stops."""
        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        quota_exc = _make_quota_exc(source="anthropic-api")
        detected = cli._QuotaDetected(quota_exc)
        cfg = SimpleNamespace(quota_handling=QuotaHandlingConfig(enabled=True, on_exhaustion="drain"))
        with (
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            result = cli._dispatch_quota_detection(detected, session_name="default")
        assert result == cli._QUOTA_STOP_REASON_DRAIN_DETECTION
        assert "[QUOTA_DRAIN_REQUESTED]" in caplog.text
        assert "phase=detection" in caplog.text
        drain_state = read_drain_state(tmp_path)
        assert drain_state is not None
        assert drain_state.reason == "quota-exhaustion:anthropic-api"

    @pytest.mark.unit
    def test_wait_default_delegates_to_handle_quota_pause_and_returns_recovered(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-2: on_exhaustion=wait (default) enters _handle_quota_pause."""
        quota_exc = _make_quota_exc()
        detected = cli._QuotaDetected(quota_exc)
        cfg = SimpleNamespace(quota_handling=QuotaHandlingConfig(enabled=True, on_exhaustion="wait"))
        with (
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._handle_quota_pause", new=AsyncMock(return_value=True)) as mock_pause,
        ):
            result = cli._dispatch_quota_detection(detected, session_name="my-session")
        assert result == cli._QUOTA_STOP_REASON_WAIT_RECOVERED
        mock_pause.assert_awaited_once()
        kwargs = mock_pause.call_args.kwargs
        assert kwargs["exc"] is quota_exc
        assert kwargs["session_name"] == "my-session"
        assert kwargs["workspace_root"] == tmp_path

    @pytest.mark.unit
    def test_wait_default_timeout_delegates_to_dispatch_timeout(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-2/3: a wait timeout hands off to _dispatch_quota_timeout with on_exhaustion_timeout."""
        quota_exc = _make_quota_exc()
        detected = cli._QuotaDetected(quota_exc)
        cfg = SimpleNamespace(
            quota_handling=QuotaHandlingConfig(enabled=True, on_exhaustion="wait", on_exhaustion_timeout="keep_waiting")
        )
        with (
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._handle_quota_pause", new=AsyncMock(return_value=False)),
        ):
            result = cli._dispatch_quota_detection(detected, session_name="default")
        assert result == cli._QUOTA_STOP_REASON_TIMEOUT_KEEP_WAITING


class TestDispatchQuotaTimeout:
    """_dispatch_quota_timeout applies on_exhaustion_timeout after the wait cap elapses (FR-2.9, spec AC-24)."""

    @pytest.mark.unit
    def test_drain_action_requests_drain_and_returns_timeout_stop_reason(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-E2-F4-S2-T1-3: 'drain' emits [QUOTA_DRAIN_REQUESTED] phase=timeout and requests the drain."""
        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        detected = _make_quota_detected(source="anthropic-api")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            result = cli._dispatch_quota_timeout(detected, "drain")
        assert result == cli._QUOTA_STOP_REASON_DRAIN_TIMEOUT
        assert "[QUOTA_DRAIN_REQUESTED]" in caplog.text
        assert "phase=timeout" in caplog.text
        drain_state = read_drain_state(tmp_path)
        assert drain_state is not None
        assert drain_state.reason == "quota-timeout:anthropic-api"

    @pytest.mark.unit
    def test_fail_action_logs_marker_and_reraises(self, caplog: pytest.LogCaptureFixture) -> None:
        """AC-E2-F4-S2-T1-3: 'fail' emits [QUOTA_FAIL_FAST] and re-raises the wrapped error."""
        quota_exc = _make_quota_exc(source="bedrock")
        detected = cli._QuotaDetected(quota_exc)
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            with pytest.raises(SubscriptionRateLimitError) as excinfo:
                cli._dispatch_quota_timeout(detected, "fail")
        assert excinfo.value is quota_exc
        assert "[QUOTA_FAIL_FAST]" in caplog.text

    @pytest.mark.unit
    def test_keep_waiting_action_logs_marker_and_returns_terminal_stop_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-E2-F4-S2-T1-3: 'keep_waiting' emits [QUOTA_TIMEOUT_KEEP_WAITING] and returns the terminal reason."""
        detected = _make_quota_detected(source="anthropic-api")
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            result = cli._dispatch_quota_timeout(detected, "keep_waiting")
        assert result == cli._QUOTA_STOP_REASON_TIMEOUT_KEEP_WAITING
        assert "[QUOTA_TIMEOUT_KEEP_WAITING]" in caplog.text

    @pytest.mark.unit
    def test_unknown_action_raises_value_error_naming_allowed_set(self) -> None:
        """AC-E2-F4-S2-T1-3: an unrecognised action raises ValueError naming the allowed set (defense in depth)."""
        detected = _make_quota_detected()
        with pytest.raises(ValueError) as excinfo:
            cli._dispatch_quota_timeout(detected, "bogus")
        assert "bogus" in str(excinfo.value)
        assert str(sorted(cli._QUOTA_TIMEOUT_ACTIONS)) in str(excinfo.value)


class TestHandleQuotaPause:
    """_handle_quota_pause follows the fixed sequence: checkpoint, marker, notify, wait, resume (spec AC-26)."""

    @pytest.mark.unit
    def test_sequence_order_on_recovery(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-4: checkpoint -> waiting marker/notify/audit -> wait -> resumed marker/notify/audit."""
        order: list[str] = []
        exc = _make_quota_exc(source="anthropic-api")
        qh_cfg = QuotaHandlingConfig(audit_comment_on_wait=True, audit_comment_on_resume=True)

        def _save_checkpoint(checkpoint: object, workspace_root: object) -> None:
            order.append("checkpoint")

        async def _wait_for_reset(**kwargs: object) -> bool:
            order.append("wait")
            return True

        def _fire_waiting(reason: str, reset_at: str) -> None:
            order.append("notify_waiting")

        def _fire_resumed(waited_seconds: int) -> None:
            order.append("notify_resumed")

        def _append_audit(message: str) -> None:
            order.append("audit_waiting" if "QUOTA_WAITING" in message else "audit_resumed")

        def _apply_resume(strategy: str, workspace_root: object) -> None:
            order.append("resume_strategy")

        with (
            patch("devbench.cli.save_checkpoint", side_effect=_save_checkpoint),
            patch("devbench.cli.wait_for_reset", side_effect=_wait_for_reset),
            patch("devbench.cli._fire_quota_waiting_notification", side_effect=_fire_waiting),
            patch("devbench.cli._fire_quota_resumed_notification", side_effect=_fire_resumed),
            patch("devbench.cli._append_quota_audit_comment", side_effect=_append_audit),
            patch("devbench.cli._apply_resume_strategy", side_effect=_apply_resume),
        ):
            result = asyncio.run(
                cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="default")
            )

        assert result is True
        assert order == [
            "checkpoint",
            "notify_waiting",
            "audit_waiting",
            "wait",
            "notify_resumed",
            "audit_resumed",
            "resume_strategy",
        ]

    @pytest.mark.unit
    def test_saves_checkpoint_with_exc_source_and_session_name(
        self, tmp_path: Path, quota_pause_mocks: SimpleNamespace
    ) -> None:
        exc = _make_quota_exc(source="anthropic-api")
        qh_cfg = QuotaHandlingConfig()
        asyncio.run(cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1"))
        quota_pause_mocks.save_checkpoint.assert_called_once()
        checkpoint_arg = quota_pause_mocks.save_checkpoint.call_args.args[0]
        assert checkpoint_arg.reason == "anthropic-api"
        assert checkpoint_arg.session_name == "s1"

    @pytest.mark.unit
    def test_recovery_fires_resumed_notification_and_applies_resume_strategy(
        self, tmp_path: Path, quota_pause_mocks: SimpleNamespace
    ) -> None:
        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(resume_strategy="restart_wu")
        quota_pause_mocks.wait_for_reset.return_value = True
        result = asyncio.run(
            cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1")
        )
        assert result is True
        quota_pause_mocks.fire_resumed.assert_called_once()
        quota_pause_mocks.apply_resume.assert_called_once_with("restart_wu", tmp_path)

    @pytest.mark.unit
    def test_timeout_returns_false_without_resume_side_effects(
        self, tmp_path: Path, quota_pause_mocks: SimpleNamespace
    ) -> None:
        """AC-E2-F4-S2-T1-4: on timeout the handler returns False and never fires resume side effects."""
        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig()
        quota_pause_mocks.wait_for_reset.return_value = False
        result = asyncio.run(
            cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1")
        )
        assert result is False
        quota_pause_mocks.fire_resumed.assert_not_called()
        quota_pause_mocks.apply_resume.assert_not_called()

    @pytest.mark.unit
    def test_probe_unavailable_returns_immediately_and_logs_marker(
        self, tmp_path: Path, quota_pause_mocks: SimpleNamespace, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-E2-F4-S2-T1-4: RecoveryProbeUnavailableError emits [QUOTA_PROBE_UNAVAILABLE] and returns immediately."""
        exc = _make_quota_exc(source="anthropic-api")
        qh_cfg = QuotaHandlingConfig()
        quota_pause_mocks.wait_for_reset.side_effect = RecoveryProbeUnavailableError("no credential")
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            result = asyncio.run(
                cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1")
            )
        assert result is False
        assert "[QUOTA_PROBE_UNAVAILABLE]" in caplog.text
        assert "reason=anthropic-api" in caplog.text
        assert "no credential" in caplog.text
        quota_pause_mocks.fire_resumed.assert_not_called()
        quota_pause_mocks.apply_resume.assert_not_called()

    @pytest.mark.unit
    def test_audit_comment_toggles_false_skip_append(self, tmp_path: Path, quota_pause_mocks: SimpleNamespace) -> None:
        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig(audit_comment_on_wait=False, audit_comment_on_resume=False)
        quota_pause_mocks.wait_for_reset.return_value = True
        asyncio.run(cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1"))
        quota_pause_mocks.append_audit.assert_not_called()

    @pytest.mark.unit
    def test_recovery_probe_partial_uses_constants_module_values(
        self, tmp_path: Path, quota_pause_mocks: SimpleNamespace
    ) -> None:
        """Critical Rule 4: the probe timeout/request-size are sourced from constants.py, not local literals."""
        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig()
        captured: dict[str, object] = {}

        async def _capture_wait_for_reset(**kwargs: object) -> bool:
            captured["probe_fn"] = kwargs["probe_fn"]
            return True

        quota_pause_mocks.wait_for_reset.side_effect = _capture_wait_for_reset
        asyncio.run(cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1"))
        probe_fn = captured["probe_fn"]
        assert isinstance(probe_fn, functools.partial)
        assert probe_fn.keywords["timeout_seconds"] == cli.RECOVERY_PROBE_TIMEOUT_SECONDS
        assert probe_fn.keywords["request_size_tokens"] == cli.RECOVERY_PROBE_REQUEST_SIZE_TOKENS


class TestNotificationFailSafety:
    """A Slack notification failure never breaks or delays a quota wait (spec AC-27, Section 7.1 swallow 3)."""

    @pytest.mark.unit
    def test_fire_quota_waiting_notification_swallows_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("devbench.notifications.notify_quota_waiting", side_effect=RuntimeError("slack down")),
            caplog.at_level(logging.WARNING, logger="devbench.cli"),
        ):
            cli._fire_quota_waiting_notification("anthropic-api", "unknown")
        assert "notify_quota_waiting failed" in caplog.text
        assert "slack down" in caplog.text

    @pytest.mark.unit
    def test_fire_quota_resumed_notification_swallows_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("devbench.notifications.notify_quota_resumed", side_effect=RuntimeError("slack down")),
            caplog.at_level(logging.WARNING, logger="devbench.cli"),
        ):
            cli._fire_quota_resumed_notification(120)
        assert "notify_quota_resumed failed" in caplog.text
        assert "slack down" in caplog.text

    @pytest.mark.unit
    def test_handle_quota_pause_proceeds_undelayed_despite_notification_failure(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-5: both raising notifiers are swallowed and the wait still completes."""
        exc = _make_quota_exc()
        qh_cfg = QuotaHandlingConfig()
        with (
            patch("devbench.cli.save_checkpoint"),
            patch("devbench.cli.wait_for_reset", new=AsyncMock(return_value=True)),
            patch("devbench.cli._apply_resume_strategy"),
            patch("devbench.cli._append_quota_audit_comment"),
            patch("devbench.notifications.notify_quota_waiting", side_effect=RuntimeError("boom")),
            patch("devbench.notifications.notify_quota_resumed", side_effect=RuntimeError("boom")),
        ):
            result = asyncio.run(
                cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1")
            )
        assert result is True


class TestQuotaAuditComments:
    """FR-2.12/D-10: audit_comment_on_wait/on_resume are implemented fresh, not ported dead (spec AC-29)."""

    @pytest.mark.unit
    def test_append_quota_audit_comment_writes_marker_to_real_wu_file(self, tmp_path: Path) -> None:
        wu = _write_in_progress_wu(tmp_path)
        with (
            patch("devbench.cli.BacklogParser") as mock_parser_cls,
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
        ):
            mock_parser_cls.return_value.parse_index.return_value = [wu]
            cli._append_quota_audit_comment("[QUOTA_WAITING] reason=anthropic-api reset_at=unknown")
        content = wu.file_path.read_text(encoding="utf-8")
        assert "[QUOTA_WAITING] reason=anthropic-api reset_at=unknown" in content
        assert "[agent/orchestrator]" in content

    @pytest.mark.unit
    def test_audit_comments_appended_when_toggles_true_via_handle_quota_pause(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-6: with both toggles true, both markers land in the on-disk work-unit file."""
        wu = _write_in_progress_wu(tmp_path)
        qh_cfg = QuotaHandlingConfig(audit_comment_on_wait=True, audit_comment_on_resume=True)
        exc = _make_quota_exc(source="anthropic-api")
        with (
            patch("devbench.cli.BacklogParser") as mock_parser_cls,
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.save_checkpoint"),
            patch("devbench.cli.wait_for_reset", new=AsyncMock(return_value=True)),
            patch("devbench.cli._fire_quota_waiting_notification"),
            patch("devbench.cli._fire_quota_resumed_notification"),
            patch("devbench.cli._apply_resume_strategy"),
        ):
            mock_parser_cls.return_value.parse_index.return_value = [wu]
            asyncio.run(cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1"))
        content = wu.file_path.read_text(encoding="utf-8")
        assert "[QUOTA_WAITING] reason=anthropic-api" in content
        assert "[QUOTA_RESUMED] waited_seconds=" in content

    @pytest.mark.unit
    def test_audit_comments_toggles_false_append_nothing(self, tmp_path: Path) -> None:
        """AC-E2-F4-S2-T1-6: toggles false append nothing to the on-disk work-unit file."""
        wu = _write_in_progress_wu(tmp_path)
        original_content = wu.file_path.read_text(encoding="utf-8")
        qh_cfg = QuotaHandlingConfig(audit_comment_on_wait=False, audit_comment_on_resume=False)
        exc = _make_quota_exc()
        with (
            patch("devbench.cli.BacklogParser") as mock_parser_cls,
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.save_checkpoint"),
            patch("devbench.cli.wait_for_reset", new=AsyncMock(return_value=True)),
            patch("devbench.cli._fire_quota_waiting_notification"),
            patch("devbench.cli._fire_quota_resumed_notification"),
            patch("devbench.cli._apply_resume_strategy"),
        ):
            mock_parser_cls.return_value.parse_index.return_value = [wu]
            asyncio.run(cli._handle_quota_pause(exc=exc, qh_cfg=qh_cfg, workspace_root=tmp_path, session_name="s1"))
        assert wu.file_path.read_text(encoding="utf-8") == original_content

    @pytest.mark.unit
    def test_append_quota_audit_comment_no_op_when_no_in_flight_wu(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """test_review STUB_TEST fix: proves the append is genuinely never called, not merely 'did not raise'."""
        with (
            patch("devbench.cli.BacklogParser") as mock_parser_cls,
            patch.object(BacklogManager, "_append_agent_comment") as mock_append,
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            caplog.at_level(logging.WARNING, logger="devbench.cli"),
        ):
            mock_parser_cls.return_value.parse_index.return_value = []
            cli._append_quota_audit_comment("[QUOTA_WAITING] reason=anthropic-api reset_at=unknown")
        mock_append.assert_not_called()
        assert "[WARN]" not in caplog.text

    @pytest.mark.unit
    def test_append_quota_audit_comment_failure_logs_warning_and_continues(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-E2-F4-S2-T1-7: a comment-append failure logs a WARNING and never raises."""
        with (
            patch("devbench.cli.BacklogParser", side_effect=OSError("backlog unreadable")),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            caplog.at_level(logging.WARNING, logger="devbench.cli"),
        ):
            cli._append_quota_audit_comment("[QUOTA_WAITING] reason=anthropic-api reset_at=unknown")
        assert "quota audit comment append failed" in caplog.text
        assert "backlog unreadable" in caplog.text


class TestCancelDrainUnlessRequested:
    """AC-E2-F4-S2-T1-8: a requested drain survives process exit (spec AC-25)."""

    @pytest.mark.unit
    def test_skipped_when_quota_requested_drain(self, tmp_path: Path) -> None:
        with patch("devbench.cli.cancel_drain") as mock_cancel:
            cli._cancel_drain_unless_requested(tmp_path, quota_drain_requested=True)
        mock_cancel.assert_not_called()

    @pytest.mark.unit
    def test_called_when_drain_not_requested(self, tmp_path: Path) -> None:
        with patch("devbench.cli.cancel_drain") as mock_cancel:
            cli._cancel_drain_unless_requested(tmp_path, quota_drain_requested=False)
        mock_cancel.assert_called_once_with(tmp_path)

    @pytest.mark.unit
    def test_cancel_drain_oserror_suppressed(self, tmp_path: Path) -> None:
        """test_review COVERAGE_REGRESSION fix: the contextlib.suppress(OSError) path is actually exercised."""
        with patch("devbench.cli.cancel_drain", side_effect=OSError("read-only fs")):
            cli._cancel_drain_unless_requested(tmp_path, quota_drain_requested=False)


class TestNoAsyncioShieldInPausePath:
    """AC-E2-F4-S2-T1-9/D-9: no asyncio.shield appears anywhere in the pause path."""

    @pytest.mark.unit
    def test_no_asyncio_shield_in_cli_source(self) -> None:
        import inspect

        source = inspect.getsource(cli)
        assert "asyncio.shield" not in source
