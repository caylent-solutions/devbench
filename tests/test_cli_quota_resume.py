"""Tests for the E2-F4-S1-T1 quota-detection wiring in the orchestrate message loop.

Covers the ``_QuotaDetected`` sentinel (spec AC-20, decision D-4) and the
``_check_quota_and_drain`` extraction that fuses quota detection with the
existing drain-on-claim short-circuit (issues #188/#212) so ``_run`` stays
under ruff's PLR0912 branch cap (issue #236, #234, #235).
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.constants import SESSION_DEFAULT_NAME, SESSION_SESSIONS_BASE_DIR
from devbench.quota import QuotaExhaustedError, SubscriptionRateLimitError


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
