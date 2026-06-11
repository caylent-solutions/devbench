"""Tests for in-process quota resume in cmd_start (TDI).

A recovered quota wait must RESUME the orchestrate skill in-process by opening a
fresh ``ClaudeSDKClient`` session and re-running ``_run`` on the remaining
backlog, rather than returning ``"quota-wait-recovered"`` as a terminal stop.
This makes an unattended ``devbench start --daemon`` survive quota windows with
no external ``make start`` restart wrapper.

Covers:

- ``_resolve_max_quota_resumes`` env-int resolution (default / override /
  invalid / non-positive fall back to the default; never fail-open).
- ``_should_resume_after_quota_recovery`` audit + cap decision.
- ``_drive_orchestrate_with_quota_resume`` loop semantics: recovered quota
  re-invokes ``_run`` and continues; the resume cap terminates with the
  ``[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]`` audit; every non-quota / non-
  recovering disposition (clean return, drain enforced, quota drain / keep-
  waiting / fail) remains terminal exactly as before (no regression).
- End-to-end ``cmd_start``: a quota error on the first SDK session followed by a
  clean second session returns 0 with a second session actually opened
  (``--daemon``-agnostic; no external wrapper).
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devbench.quota import QuotaExhaustedError, SubscriptionRateLimitError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_quota_exc(reset_at: datetime | None = None) -> SubscriptionRateLimitError:
    """Build a subscription rate-limit error for the quota sentinel."""
    return SubscriptionRateLimitError(reset_at=reset_at, raw_error="test", source="anthropic-api")


def _quota_detected() -> Any:
    """Build a _QuotaDetected sentinel wrapping a subscription rate-limit error."""
    from devbench.cli import _QuotaDetected

    return _QuotaDetected(_make_quota_exc())


def _capture_info_logs(log_messages: list[str]) -> Any:
    """Return a side_effect that records formatted logger.info messages."""
    return lambda msg, *a, **kw: log_messages.append(msg % a if a else msg)


# ---------------------------------------------------------------------------
# _resolve_max_quota_resumes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveMaxQuotaResumes:
    """DEVBENCH_MAX_QUOTA_RESUMES env-int resolution is unset-safe and fail-safe."""

    def test_default_when_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.cli import _resolve_max_quota_resumes
        from devbench.constants import DEFAULT_MAX_QUOTA_RESUMES

        monkeypatch.delenv("DEVBENCH_MAX_QUOTA_RESUMES", raising=False)
        assert _resolve_max_quota_resumes() == DEFAULT_MAX_QUOTA_RESUMES

    def test_env_override_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.cli import _resolve_max_quota_resumes

        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "7")
        assert _resolve_max_quota_resumes() == 7

    def test_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.cli import _resolve_max_quota_resumes
        from devbench.constants import DEFAULT_MAX_QUOTA_RESUMES

        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "not-an-int")
        assert _resolve_max_quota_resumes() == DEFAULT_MAX_QUOTA_RESUMES

    def test_non_positive_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.cli import _resolve_max_quota_resumes
        from devbench.constants import DEFAULT_MAX_QUOTA_RESUMES

        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "0")
        assert _resolve_max_quota_resumes() == DEFAULT_MAX_QUOTA_RESUMES
        monkeypatch.setenv("DEVBENCH_MAX_QUOTA_RESUMES", "-5")
        assert _resolve_max_quota_resumes() == DEFAULT_MAX_QUOTA_RESUMES

    def test_default_constant_is_high(self) -> None:
        """The default must be high so overnight runs crossing many quota windows
        are never cut short by the cap."""
        from devbench.constants import DEFAULT_MAX_QUOTA_RESUMES

        assert DEFAULT_MAX_QUOTA_RESUMES >= 100


# ---------------------------------------------------------------------------
# _should_resume_after_quota_recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShouldResumeAfterQuotaRecovery:
    """Resume decision: True under the cap (with audit), False at the cap (with audit)."""

    def test_resume_permitted_under_cap_emits_resume_audit(self) -> None:
        from devbench.cli import _should_resume_after_quota_recovery

        logs: list[str] = []
        with patch("devbench.cli.logger") as mock_logger:
            mock_logger.info.side_effect = _capture_info_logs(logs)
            assert _should_resume_after_quota_recovery(resumes_used=0, max_resumes=3) is True
        assert any("ORCHESTRATOR_QUOTA_RESUME" in m and "resume=1" in m and "max=3" in m for m in logs)

    def test_resume_denied_at_cap_emits_exhausted_audit(self) -> None:
        from devbench.cli import _should_resume_after_quota_recovery

        logs: list[str] = []
        with patch("devbench.cli.logger") as mock_logger:
            mock_logger.info.side_effect = _capture_info_logs(logs)
            assert _should_resume_after_quota_recovery(resumes_used=3, max_resumes=3) is False
        assert any("ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED" in m and "max=3" in m for m in logs)


# ---------------------------------------------------------------------------
# _drive_orchestrate_with_quota_resume loop semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriveOrchestrateWithQuotaResume:
    """The resume loop re-invokes _run on recovery, caps resumes, and keeps all
    non-recovering dispositions terminal."""

    def _drive(self, run: Any) -> Any:
        from devbench.cli import _drive_orchestrate_with_quota_resume

        return _drive_orchestrate_with_quota_resume(run, "default")

    def _make_run(self, behaviors: list[str]) -> tuple[Any, dict[str, int]]:
        """Return a no-arg coroutine factory plus a mutable call counter.

        ``behaviors`` drives each successive ``_run`` invocation:

        - ``"clean"`` -> returns normally (session finished).
        - ``"quota"`` -> raises ``_QuotaDetected`` (quota hit mid-session).
        - ``"drain"`` -> raises ``_DrainRequested`` (drain enforced).

        The factory is consumed by ``asyncio.run(run())`` exactly as cmd_start
        drives it; each call returns a FRESH coroutine (mirrors how ``_run``
        opens a fresh ClaudeSDKClient on every invocation).
        """
        from devbench.cli import _DrainRequested, _QuotaDetected

        calls = {"count": 0}

        def factory() -> Any:
            async def _coro() -> None:
                idx = calls["count"]
                calls["count"] += 1
                behavior = behaviors[idx] if idx < len(behaviors) else behaviors[-1]
                if behavior == "quota":
                    raise _QuotaDetected(_make_quota_exc())
                if behavior == "drain":
                    raise _DrainRequested("drain-now")
                # "clean": the session finished normally (implicit return None).

            return _coro()

        return factory, calls

    def test_clean_first_run_is_terminal_with_single_invocation(self) -> None:
        """A clean _run return (no quota / no drain) falls through to cmd_start's
        normal classification: terminal_rc is None and _run ran exactly once."""
        run, calls = self._make_run(["clean"])
        result = self._drive(run)
        assert result.terminal_rc is None
        assert result.quota_drain_requested is False
        assert calls["count"] == 1

    def test_recovered_quota_then_clean_reinvokes_run(self) -> None:
        """AC-1: a recovered quota wait re-opens a session and re-runs _run; the
        second (clean) run is terminal. _run is invoked twice."""
        run, calls = self._make_run(["quota", "clean"])
        with patch(
            "devbench.cli._dispatch_quota_detection",
            return_value="quota-wait-recovered",
        ):
            result = self._drive(run)
        # Fell through to normal classification after the clean second run.
        assert result.terminal_rc is None
        assert calls["count"] == 2

    def test_multiple_recoveries_then_clean(self) -> None:
        """Several consecutive recoveries each re-invoke _run before the clean run."""
        run, calls = self._make_run(["quota", "quota", "quota", "clean"])
        with patch(
            "devbench.cli._dispatch_quota_detection",
            return_value="quota-wait-recovered",
        ):
            result = self._drive(run)
        assert result.terminal_rc is None
        assert calls["count"] == 4

    def test_resume_cap_terminates_with_exhausted_audit(self) -> None:
        """AC-2: the resume loop is capped; exceeding it terminates with rc=0, the
        quota-resume-cap-exhausted stop reason, and the exhausted audit line."""
        # Always recovers -> would loop forever without the cap.
        run, calls = self._make_run(["quota"])
        logs: list[str] = []
        with (
            patch("devbench.cli._resolve_max_quota_resumes", return_value=2),
            patch("devbench.cli._dispatch_quota_detection", return_value="quota-wait-recovered"),
            patch("devbench.cli.logger") as mock_logger,
        ):
            mock_logger.info.side_effect = _capture_info_logs(logs)
            result = self._drive(run)
        assert result.terminal_rc == 0
        assert result.stop_reason == "quota-resume-cap-exhausted"
        # 2 resumes performed (3 _run invocations) then the cap stops it.
        assert calls["count"] == 3
        assert any("ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED" in m and "max=2" in m for m in logs)

    def test_drain_enforced_is_terminal_no_resume(self) -> None:
        """AC-3: a drain-enforced exit terminates immediately (rc=0) with no resume."""
        run, calls = self._make_run(["drain"])
        with patch("devbench.cli.consume_drain", return_value=None):
            result = self._drive(run)
        assert result.terminal_rc == 0
        assert result.stop_reason.startswith("drain enforced")
        assert result.quota_drain_requested is False
        assert calls["count"] == 1

    def test_quota_drain_disposition_is_terminal_and_preserves_drain(self) -> None:
        """AC-3: a quota disposition that requests a drain terminates (rc=0) and
        flags quota_drain_requested so cmd_start preserves the drain signal."""
        run, calls = self._make_run(["quota"])
        with patch(
            "devbench.cli._dispatch_quota_detection",
            return_value="quota-wait-timeout-drain",
        ):
            result = self._drive(run)
        assert result.terminal_rc == 0
        assert result.stop_reason == "quota-wait-timeout-drain"
        assert result.quota_drain_requested is True
        assert calls["count"] == 1

    def test_quota_keep_waiting_disposition_is_terminal(self) -> None:
        """AC-3: a keep-waiting timeout disposition terminates (rc=0), no resume,
        no drain preserved."""
        run, calls = self._make_run(["quota"])
        with patch(
            "devbench.cli._dispatch_quota_detection",
            return_value="quota-wait-timeout-keep-waiting",
        ):
            result = self._drive(run)
        assert result.terminal_rc == 0
        assert result.stop_reason == "quota-wait-timeout-keep-waiting"
        assert result.quota_drain_requested is False
        assert calls["count"] == 1

    def test_quota_fail_disposition_propagates(self) -> None:
        """AC-3: a fail disposition (dispatch re-raises QuotaExhaustedError)
        propagates out of the loop unchanged -- no resume, no swallowing."""
        run, _calls = self._make_run(["quota"])
        with patch(
            "devbench.cli._dispatch_quota_detection",
            side_effect=_make_quota_exc(),
        ):
            with pytest.raises(QuotaExhaustedError):
                self._drive(run)


# ---------------------------------------------------------------------------
# End-to-end cmd_start in-process resume (daemon-agnostic)
# ---------------------------------------------------------------------------


def _make_resuming_sdk(session_outcomes: list[str]) -> tuple[types.ModuleType, dict[str, int]]:
    """Return a fake claude_agent_sdk whose successive sessions follow outcomes.

    Each ``ClaudeSDKClient`` context (one per ``_run`` invocation) yields a
    single message tagged with the outcome for that session:

    - ``"quota"`` -> a message that the patched ``detect_quota_error`` classifies
      as a quota error, causing ``_run`` to raise ``_QuotaDetected``.
    - ``"clean"`` -> a benign message that finishes the session normally.

    A shared counter records how many sessions were opened so the test can assert
    that a SECOND fresh session was actually created after the recovery.
    """
    fake_sdk: types.ModuleType = types.ModuleType("claude_agent_sdk")
    sdk_any: Any = fake_sdk
    sdk_any.ClaudeAgentOptions = MagicMock()
    sessions = {"opened": 0}

    class _FakeClient:
        def __init__(self, options: object | None = None) -> None:
            self._idx = sessions["opened"]
            sessions["opened"] += 1

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def query(self, *args: object, **kwargs: object) -> None:
            return None

        async def receive_response(self) -> Any:
            outcome = session_outcomes[self._idx] if self._idx < len(session_outcomes) else session_outcomes[-1]
            yield SimpleNamespace(outcome=outcome)

    sdk_any.ClaudeSDKClient = _FakeClient
    return fake_sdk, sessions


@pytest.mark.unit
class TestCmdStartInProcessResumeEndToEnd:
    """AC-1 / AC-4: cmd_start resumes in-process after a recovered quota wait."""

    def test_cmd_start_opens_fresh_session_after_quota_recovery(self, tmp_path: Path) -> None:
        """A quota error on the first session, recovered, then a clean second
        session: cmd_start returns 0 AND a second ClaudeSDKClient was opened
        (proving in-process resume, not a terminal exit). No external wrapper."""
        fake_sdk, sessions = _make_resuming_sdk(["quota", "clean"])

        def fake_detect(message: object) -> QuotaExhaustedError | None:
            if getattr(message, "outcome", None) == "quota":
                return _make_quota_exc(reset_at=datetime(2026, 1, 1, 16, 0, 0, tzinfo=UTC))
            return None

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.detect_quota_error", side_effect=fake_detect),
            # Recover instantly (no real wait); covers the wait->resume path.
            patch("devbench.cli._handle_quota_pause", new_callable=AsyncMock, return_value=True),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cmd_start_no_args()

        assert rc == 0
        assert sessions["opened"] == 2, (
            f"Expected a fresh second ClaudeSDKClient session after quota recovery, "
            f"but {sessions['opened']} session(s) were opened. A single quota window "
            "must not terminate an unattended run."
        )

    def test_cmd_start_clean_first_session_opens_only_one(self, tmp_path: Path) -> None:
        """Regression guard: with no quota error the run opens exactly one session
        and returns 0 -- the resume loop adds no extra session on the happy path."""
        fake_sdk, sessions = _make_resuming_sdk(["clean"])

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.detect_quota_error", return_value=None),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cmd_start_no_args()

        assert rc == 0
        assert sessions["opened"] == 1


def cmd_start_no_args() -> int:
    """Invoke cmd_start with no flags (foreground, default session)."""
    from devbench import cli

    return cli.cmd_start()
