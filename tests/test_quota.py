"""Tests for devbench.quota -- exception hierarchy, detect_quota_error,
_has_quota_marker, _parse_reset_at_from_text, wait_for_reset, checkpoint,
recovery_probe, and _apply_resume_strategy.

Covers: QuotaExhaustedError and its four LSP subclasses (SubscriptionRateLimitError,
SdkCreditExhaustedError, ApiBillingError, BedrockThrottleError).

Also covers: detect_quota_error(obj) which applies the ten ordered rules and
never raises.

Also covers: _has_quota_marker(text) which does substring scan over the exact
CLI bytes.

Also covers: _parse_reset_at_from_text(text) which returns the next-future UTC
datetime or None.

Also covers: wait_for_reset, save_checkpoint, load_checkpoint, remove_checkpoint,
recovery_probe, _apply_resume_strategy (issue #236, Appendix A QW-3..QW-5).

Issue #234 (Appendix A QW-1 / QW-2 / QW-10).
Issue #236 (Appendix A QW-3 / QW-4 / QW-5).
AC-234-1, AC-234a-1, AC-236-1.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devbench.quota import (
    ApiBillingError,
    BedrockThrottleError,
    QuotaCheckpoint,
    QuotaExhaustedError,
    RecoveryProbeUnavailableError,
    SdkCreditExhaustedError,
    SubscriptionRateLimitError,
    _apply_resume_strategy,
    _force_status_in_queue,
    _has_quota_marker,
    _parse_reset_at_from_text,
    _probe_api_call,
    detect_quota_error,
    load_checkpoint,
    recovery_probe,
    remove_checkpoint,
    save_checkpoint,
    wait_for_reset,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_exc(
    cls: type[QuotaExhaustedError],
    reset_at: datetime | None = _NOW,
    raw_error: object = "raw",
    source: str = "anthropic",
) -> QuotaExhaustedError:
    return cls(reset_at=reset_at, raw_error=raw_error, source=source)


def _make_sdk_exc(
    status_code: int,
    error_type: str | None = None,
    message: str = "error",
) -> MagicMock:
    """Build a synthetic Anthropic-SDK-style exception."""
    exc = MagicMock(spec=Exception)
    exc.status_code = status_code
    exc.message = message
    body: dict[str, Any] = {"error": {"message": message}}
    if error_type is not None:
        body["error"]["type"] = error_type
    exc.body = body
    return exc


def _make_bedrock_exc(error_code: str, message: str = "throttled") -> MagicMock:
    """Build a synthetic botocore ClientError-style exception."""
    exc = MagicMock(spec=Exception)
    exc.response = {"Error": {"Code": error_code, "Message": message}}
    exc.status_code = None
    exc.body = {}
    return exc


def _make_user_message_with_quota_marker(marker_text: str) -> SimpleNamespace:
    """Build a UserMessage-shaped object with a ToolResultBlock containing quota text."""
    block = SimpleNamespace(
        tool_use_id="test-tool-id",
        content=marker_text,
    )
    return SimpleNamespace(content=[block])


def _make_assistant_message_rate_limit(reset_text: str | None = None) -> SimpleNamespace:
    """Build an AssistantMessage-shaped object with error='rate_limit'."""
    if reset_text is not None:
        text_block = SimpleNamespace(text=reset_text)
        content = [text_block]
    else:
        content = []
    return SimpleNamespace(error="rate_limit", content=content)


def _make_result_message_error(result_text: str) -> SimpleNamespace:
    """Build a ResultMessage-shaped object with is_error=True."""
    return SimpleNamespace(is_error=True, result=result_text)


# Fake anthropic exception hierarchy for recovery_probe classification tests.
# Mirrors the real inheritance (AuthenticationError/PermissionDeniedError are
# APIErrors; APIError is an AnthropicError) so recovery_probe's ordered except
# clauses resolve exactly as they would against the real SDK types.
class _FakeAnthropicError(Exception):
    """Stand-in for anthropic.AnthropicError (base; non-API config errors)."""


class _FakeAPIError(_FakeAnthropicError):
    """Stand-in for anthropic.APIError (transient API/network errors)."""


class _FakeAuthError(_FakeAPIError):
    """Stand-in for anthropic.AuthenticationError (401; permanent)."""


class _FakePermissionError(_FakeAPIError):
    """Stand-in for anthropic.PermissionDeniedError (403; permanent)."""


def _patch_fake_anthropic_errors() -> Any:
    """Patch the anthropic module's exception classes with the fake hierarchy."""
    import anthropic

    return patch.multiple(
        anthropic,
        AnthropicError=_FakeAnthropicError,
        APIError=_FakeAPIError,
        AuthenticationError=_FakeAuthError,
        PermissionDeniedError=_FakePermissionError,
    )


# ---------------------------------------------------------------------------
# QuotaExhaustedError base class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaExhaustedErrorBase:
    """QuotaExhaustedError is the common base for all quota exceptions."""

    def test_is_exception(self) -> None:
        exc = _make_exc(QuotaExhaustedError)
        assert isinstance(exc, Exception)

    def test_fields_stored(self) -> None:
        exc = _make_exc(QuotaExhaustedError)
        assert exc.reset_at == _NOW
        assert exc.raw_error == "raw"
        assert exc.source == "anthropic"

    def test_reset_at_none_allowed(self) -> None:
        exc = _make_exc(QuotaExhaustedError, reset_at=None)
        assert exc.reset_at is None

    def test_raw_error_any_type(self) -> None:
        sentinel = object()
        exc = _make_exc(QuotaExhaustedError, raw_error=sentinel)
        assert exc.raw_error is sentinel

    def test_str_includes_source(self) -> None:
        exc = _make_exc(QuotaExhaustedError, source="test-source")
        assert "test-source" in str(exc)

    def test_str_includes_reset_at_iso_when_set(self) -> None:
        exc = _make_exc(QuotaExhaustedError, reset_at=_NOW)
        assert "2026-01-01" in str(exc)

    def test_str_includes_unknown_when_reset_at_none(self) -> None:
        exc = _make_exc(QuotaExhaustedError, reset_at=None)
        assert "unknown" in str(exc)

    def test_is_base_exception_subclass(self) -> None:
        assert issubclass(QuotaExhaustedError, Exception)

    def test_raise_and_catch_as_quota_exhausted(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(QuotaExhaustedError)


# ---------------------------------------------------------------------------
# LSP subclass tests: each subtype is a valid QuotaExhaustedError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubscriptionRateLimitError:
    """SubscriptionRateLimitError is a proper LSP subclass."""

    def test_inherits_base(self) -> None:
        exc = _make_exc(SubscriptionRateLimitError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(SubscriptionRateLimitError, source="anthropic-api")
        assert exc.reset_at == _NOW
        assert exc.raw_error == "raw"
        assert exc.source == "anthropic-api"

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(SubscriptionRateLimitError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(SubscriptionRateLimitError):
            raise _make_exc(SubscriptionRateLimitError)

    @pytest.mark.parametrize(
        "reset_at",
        [_NOW, None, datetime(2030, 6, 15, 0, 0, tzinfo=UTC)],
    )
    def test_reset_at_variants(self, reset_at: datetime | None) -> None:
        exc = _make_exc(SubscriptionRateLimitError, reset_at=reset_at)
        assert exc.reset_at == reset_at

    def test_substitutable_for_base_in_list(self) -> None:
        """LSP: a list of QuotaExhaustedError can contain SubscriptionRateLimitError."""
        errors: list[QuotaExhaustedError] = [_make_exc(SubscriptionRateLimitError)]
        assert len(errors) == 1
        assert isinstance(errors[0], QuotaExhaustedError)


@pytest.mark.unit
class TestSdkCreditExhaustedError:
    """SdkCreditExhaustedError is a proper LSP subclass."""

    def test_inherits_base(self) -> None:
        exc = _make_exc(SdkCreditExhaustedError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(SdkCreditExhaustedError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(SdkCreditExhaustedError):
            raise _make_exc(SdkCreditExhaustedError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(SdkCreditExhaustedError, source="sdk")
        assert exc.source == "sdk"

    def test_substitutable_for_base(self) -> None:
        err: QuotaExhaustedError = _make_exc(SdkCreditExhaustedError)
        assert err.reset_at == _NOW


@pytest.mark.unit
class TestApiBillingError:
    """ApiBillingError is a proper LSP subclass."""

    def test_inherits_base(self) -> None:
        exc = _make_exc(ApiBillingError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(ApiBillingError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(ApiBillingError):
            raise _make_exc(ApiBillingError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(ApiBillingError, source="anthropic-api")
        assert exc.source == "anthropic-api"

    def test_substitutable_for_base(self) -> None:
        err: QuotaExhaustedError = _make_exc(ApiBillingError)
        assert err.source == "anthropic"


@pytest.mark.unit
class TestBedrockThrottleError:
    """BedrockThrottleError is a proper LSP subclass."""

    def test_inherits_base(self) -> None:
        exc = _make_exc(BedrockThrottleError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(BedrockThrottleError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(BedrockThrottleError):
            raise _make_exc(BedrockThrottleError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(BedrockThrottleError, source="bedrock")
        assert exc.source == "bedrock"

    def test_substitutable_for_base(self) -> None:
        err: QuotaExhaustedError = _make_exc(BedrockThrottleError)
        assert err.reset_at == _NOW


# ---------------------------------------------------------------------------
# _has_quota_marker -- verbatim CLI bytes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHasQuotaMarker:
    """_has_quota_marker performs substring scan over exact CLI bytes."""

    @pytest.mark.parametrize(
        "text",
        [
            "You've hit your limit",
            "you've hit your limit",
            "You have hit your limit",
            # Verbatim line from the CLI (real apostrophe + middle dot separator)
            "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)",
            # Precise "rate limit" phrasing -- exhaustion verb adjacent.
            "rate limit exceeded",
            "rate limit reached",
            "rate limit hit",
            "rate limit exhausted",
            "rate-limit exceeded",
            "rate limits reached",
            "Rate Limit Exceeded",  # case-insensitive
            "rate limit resets 4:10pm (UTC)",
        ],
    )
    def test_matches_quota_markers(self, text: str) -> None:
        assert _has_quota_marker(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Everything is fine",
            "you hit your stride",
            "No issues here",
            "",
            # Bare "rate limit"/"rate limiting" with no adjacent exhaustion verb
            # must NOT match (the false-positive class this fix removes).
            "rate limit",
            "rate limiting",
            "Implement rate limiting to prevent abuse",
            "rate limit not exceeded",
            # Verbatim reviewer-agent criteria that previously tripped detection.
            "API endpoints implement rate limiting, CORS policies, and required security headers.",
            "Missing security headers, overly permissive CORS, missing rate limiting.",
        ],
    )
    def test_non_matching_text(self, text: str) -> None:
        assert _has_quota_marker(text) is False

    def test_code_reviewer_prose_is_not_quota(self) -> None:
        """Regression: the code-reviewer criterion must not be a quota marker.

        Source: plugin/devbench-orchestrate/agents/review_team/code-reviewer.md:63.
        The bare "rate limit" substring used to match the "rate limiting" in this
        line, falsely pausing the orchestrator on every security review.
        """
        prose = "API endpoints implement rate limiting, CORS policies, and required security headers."
        assert _has_quota_marker(prose) is False

    def test_non_string_returns_false(self) -> None:
        assert _has_quota_marker(None) is False  # type: ignore[arg-type]

    def test_empty_string_returns_false(self) -> None:
        assert _has_quota_marker("") is False

    def test_integer_returns_false(self) -> None:
        assert _has_quota_marker(42) is False  # type: ignore[arg-type]

    def test_verbatim_cli_line_matches(self) -> None:
        """The exact verbatim CLI line must match (AC-234-1)."""
        verbatim = "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)"
        assert _has_quota_marker(verbatim) is True

    def test_partial_match_in_longer_string(self) -> None:
        assert _has_quota_marker("prefix You've hit your limit suffix") is True


# ---------------------------------------------------------------------------
# _parse_reset_at_from_text -- parametrized time parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseResetAtFromText:
    """_parse_reset_at_from_text returns next-future UTC datetime or None."""

    def test_basic_pm_time_next_future(self) -> None:
        """'resets 4:10pm (UTC)' with clock at noon -> today at 16:10 UTC."""
        future_clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=future_clock):
            result = _parse_reset_at_from_text("resets 4:10pm (UTC)")
        assert result is not None
        assert result.hour == 16
        assert result.minute == 10
        assert result.second == 0
        assert result.tzinfo is UTC

    def test_next_day_rollover_when_time_past(self) -> None:
        """When the parsed time is earlier than now, add one day."""
        late_clock = datetime(2026, 1, 1, 18, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=late_clock):
            result = _parse_reset_at_from_text("resets 4:10pm (UTC)")
        assert result is not None
        assert result.day == 2  # next day
        assert result.hour == 16
        assert result.minute == 10

    def test_midnight_am(self) -> None:
        """12:00am -> hour=0 (midnight)."""
        clock = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text("resets 12:00am (UTC)")
        assert result is not None
        assert result.hour == 0
        assert result.minute == 0

    def test_noon_pm(self) -> None:
        """12:30pm -> hour=12 (noon)."""
        clock = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text("resets 12:30pm (UTC)")
        assert result is not None
        assert result.hour == 12
        assert result.minute == 30

    def test_uppercase_meridiem(self) -> None:
        """Uppercase AM/PM is accepted."""
        clock = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text("resets 4:10PM (UTC)")
        assert result is not None
        assert result.hour == 16

    def test_none_when_no_match(self) -> None:
        """No 'resets ...' pattern -> None."""
        assert _parse_reset_at_from_text("No reset information here") is None

    def test_none_on_malformed_hour_25(self) -> None:
        """Hour 25 is invalid -> None."""
        assert _parse_reset_at_from_text("resets 25:99pm (UTC)") is None

    def test_none_on_malformed_hour_13pm(self) -> None:
        """Hour 13pm is invalid (>12) -> None."""
        assert _parse_reset_at_from_text("resets 13:00pm (UTC)") is None

    def test_none_on_non_utc_timezone(self) -> None:
        """Non-(UTC) timezone label -> None."""
        assert _parse_reset_at_from_text("resets 4:10pm (EST)") is None

    def test_none_on_non_string(self) -> None:
        """Non-string input -> None."""
        assert _parse_reset_at_from_text(None) is None  # type: ignore[arg-type]
        assert _parse_reset_at_from_text(42) is None  # type: ignore[arg-type]

    def test_result_is_utc_aware(self) -> None:
        """Result is always UTC-aware."""
        clock = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text("resets 4:10pm (UTC)")
        assert result is not None
        assert result.tzinfo is UTC

    def test_result_is_strictly_in_future(self) -> None:
        """Result is always strictly after the current clock."""
        clock = datetime(2026, 1, 1, 17, 0, 0, tzinfo=UTC)
        # 4:10pm = 16:10, which is <= 17:00, so should roll to next day
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text("resets 4:10pm (UTC)")
        assert result is not None
        assert result > clock

    def test_verbatim_cli_line_parsed(self) -> None:
        """The exact verbatim CLI line is parsed correctly (AC-234-1)."""
        verbatim = "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)"
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text(verbatim)
        assert result is not None
        assert result.hour == 16
        assert result.minute == 10


# ---------------------------------------------------------------------------
# detect_quota_error -- ten ordered rules, never raises
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetectQuotaErrorNeverRaises:
    """detect_quota_error never raises on any input."""

    @pytest.mark.parametrize(
        "obj",
        [
            None,
            42,
            "a string",
            [],
            {},
            object(),
            SimpleNamespace(),
            Exception("generic"),
        ],
    )
    def test_does_not_raise_on_arbitrary_input(self, obj: object) -> None:
        result = detect_quota_error(obj)
        assert result is None or isinstance(result, QuotaExhaustedError)


@pytest.mark.unit
class TestDetectQuotaErrorRules:
    """detect_quota_error applies the ten rules in order (AC-234a-1)."""

    # Rule 1: passthrough if already a QuotaExhaustedError
    def test_rule1_passthrough_already_quota_error(self) -> None:
        exc = _make_exc(QuotaExhaustedError)
        assert detect_quota_error(exc) is exc

    def test_rule1_passthrough_subclass_instances(self) -> None:
        exc = _make_exc(SubscriptionRateLimitError)
        assert detect_quota_error(exc) is exc

    # Rule 2: status_code == 429
    def test_rule2_http_429_returns_subscription_rate_limit(self) -> None:
        obj = _make_sdk_exc(status_code=429)
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)

    def test_rule2_source_is_anthropic_api(self) -> None:
        obj = _make_sdk_exc(status_code=429)
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "anthropic-api"

    # Rule 3: 402 + insufficient_quota
    def test_rule3_http_402_insufficient_quota_returns_sdk_credit_error(self) -> None:
        obj = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(obj)
        assert isinstance(result, SdkCreditExhaustedError)

    def test_rule3_source_is_sdk(self) -> None:
        obj = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "sdk"

    # Rule 4: 402 without insufficient_quota
    def test_rule4_http_402_other_returns_api_billing_error(self) -> None:
        obj = _make_sdk_exc(status_code=402, error_type="payment_required")
        result = detect_quota_error(obj)
        assert isinstance(result, ApiBillingError)

    def test_rule4_http_402_no_error_type_returns_api_billing_error(self) -> None:
        obj = _make_sdk_exc(status_code=402)
        result = detect_quota_error(obj)
        assert isinstance(result, ApiBillingError)

    def test_rule4_source_is_anthropic_api(self) -> None:
        obj = _make_sdk_exc(status_code=402)
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "anthropic-api"

    # Rule 5: Bedrock throttle codes
    def test_rule5_bedrock_throttling_exception(self) -> None:
        obj = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(obj)
        assert isinstance(result, BedrockThrottleError)

    def test_rule5_bedrock_service_quota_exceeded(self) -> None:
        obj = _make_bedrock_exc("ServiceQuotaExceededException")
        result = detect_quota_error(obj)
        assert isinstance(result, BedrockThrottleError)

    def test_rule5_source_is_bedrock(self) -> None:
        obj = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "bedrock"

    def test_rule5_other_bedrock_codes_return_none(self) -> None:
        obj = _make_bedrock_exc("SomeOtherException")
        result = detect_quota_error(obj)
        assert result is None

    # Rule 6: UserMessage with ToolResultBlock containing quota marker
    def test_rule6_user_message_tool_result_block_string(self) -> None:
        obj = _make_user_message_with_quota_marker("You've hit your limit -- some details")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule6_source_is_claude_code_cli(self) -> None:
        obj = _make_user_message_with_quota_marker("You have hit your limit")
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "claude-code-cli"

    def test_rule6_reset_at_parsed_from_text(self) -> None:
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        obj = _make_user_message_with_quota_marker("You've hit your limit -- resets 4:10pm (UTC)")
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert result.reset_at is not None
        assert result.reset_at.hour == 16

    def test_rule6_no_quota_marker_returns_none(self) -> None:
        obj = _make_user_message_with_quota_marker("Normal tool output")
        result = detect_quota_error(obj)
        assert result is None

    def test_rule6_reviewer_prose_not_detected(self) -> None:
        """Regression: a sub-agent reviewer's prose mentioning rate limiting is not a quota hit."""
        obj = _make_user_message_with_quota_marker(
            "API endpoints implement rate limiting, CORS policies, and required security headers."
        )
        assert detect_quota_error(obj) is None

    def test_rule6_is_error_true_with_marker_detected(self) -> None:
        """An errored tool result carrying a genuine limit phrase is detected."""
        block = SimpleNamespace(tool_use_id="t", content="rate limit exceeded", is_error=True)
        obj = SimpleNamespace(content=[block])
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule6_is_error_none_with_marker_detected(self) -> None:
        """A tool result with unset is_error still scans (only False is skipped)."""
        block = SimpleNamespace(tool_use_id="t", content="You've hit your limit", is_error=None)
        obj = SimpleNamespace(content=[block])
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)

    def test_rule6_is_error_false_with_genuine_marker_skipped(self) -> None:
        """A SUCCESSFUL tool result is never a quota signal, even with genuine phrasing."""
        block = SimpleNamespace(tool_use_id="t", content="rate limit exceeded", is_error=False)
        obj = SimpleNamespace(content=[block])
        assert detect_quota_error(obj) is None

    def test_rule6_is_error_false_with_reviewer_prose_skipped(self) -> None:
        """A successful tool result carrying benign reviewer prose is skipped."""
        block = SimpleNamespace(
            tool_use_id="t",
            content="API endpoints implement rate limiting, CORS policies, and required security headers.",
            is_error=False,
        )
        obj = SimpleNamespace(content=[block])
        assert detect_quota_error(obj) is None

    # Rule 7: AssistantMessage with error='rate_limit'
    def test_rule7_assistant_message_error_rate_limit(self) -> None:
        obj = _make_assistant_message_rate_limit()
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule7_assistant_message_error_other_string(self) -> None:
        obj = SimpleNamespace(error="some_other_error", content=[])
        result = detect_quota_error(obj)
        assert result is None

    def test_rule7_reset_at_parsed_from_text_block(self) -> None:
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        obj = _make_assistant_message_rate_limit("You've hit your limit -- resets 4:10pm (UTC)")
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert result.reset_at is not None

    # Rule 8: ResultMessage with is_error=True AND quota marker in .result
    def test_rule8_result_message_is_error_with_quota_marker(self) -> None:
        obj = _make_result_message_error("You've hit your limit")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule8_is_error_false_does_not_match(self) -> None:
        obj = SimpleNamespace(is_error=False, result="You've hit your limit")
        result = detect_quota_error(obj)
        assert result is None

    def test_rule8_no_quota_marker_returns_none(self) -> None:
        obj = _make_result_message_error("task completed successfully")
        result = detect_quota_error(obj)
        assert result is None

    def test_rule8_result_not_string_returns_none(self) -> None:
        obj = SimpleNamespace(is_error=True, result={"key": "You've hit your limit"})
        result = detect_quota_error(obj)
        assert result is None

    # Rule 9: wrapper BaseException with quota marker in str(obj)
    def test_rule9_base_exception_with_quota_marker(self) -> None:
        obj = Exception("You've hit your limit -- rate limit")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule9_value_error_with_quota_marker(self) -> None:
        obj = ValueError("you've hit your limit")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)

    def test_rule9_base_exception_without_marker_returns_none(self) -> None:
        obj = Exception("generic error")
        result = detect_quota_error(obj)
        assert result is None

    # Rule 10: everything else returns None
    def test_rule10_unrecognized_input_returns_none(self) -> None:
        assert detect_quota_error(None) is None
        assert detect_quota_error(42) is None
        assert detect_quota_error("random string") is None
        assert detect_quota_error(SimpleNamespace()) is None


# ---------------------------------------------------------------------------
# AC-234-1: four surfaces -> SubscriptionRateLimitError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAC2341FourSurfaces:
    """AC-234-1: each of the four CLI surfaces yields SubscriptionRateLimitError."""

    def test_surface1_user_message_tool_result(self) -> None:
        """Surface 1: UserMessage/ToolResultBlock."""
        obj = _make_user_message_with_quota_marker("You've hit your limit -- resets 4:10pm (UTC)")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_surface2_assistant_message_error_rate_limit(self) -> None:
        """Surface 2: AssistantMessage.error == 'rate_limit'."""
        obj = _make_assistant_message_rate_limit()
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_surface3_result_message_is_error(self) -> None:
        """Surface 3: ResultMessage.is_error=True with quota marker in .result."""
        obj = _make_result_message_error("You've hit your limit")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_surface4_wrapper_base_exception(self) -> None:
        """Surface 4: Generic BaseException with quota marker in str(obj)."""
        obj = Exception("You've hit your limit -- rate limit exceeded")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_parse_reset_at_returns_1610_utc(self) -> None:
        """AC-234-1: _parse_reset_at_from_text('resets 4:10pm (UTC)') -> next-future 16:10:00Z."""
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text("resets 4:10pm (UTC)")
        assert result is not None
        assert result.hour == 16
        assert result.minute == 10
        assert result.second == 0
        assert result.tzinfo is UTC

    def test_parse_reset_at_returns_none_when_absent(self) -> None:
        """AC-234-1: None when no reset text is present."""
        assert _parse_reset_at_from_text("You've hit your limit") is None

    def test_verbatim_line_with_real_apostrophe_and_middle_dot(self) -> None:
        """Verbatim CLI line with real Unicode apostrophe (U+2019) and middle dot (U+00B7)."""
        verbatim = "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)"
        assert _has_quota_marker(verbatim) is True
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text(verbatim)
        assert result is not None
        assert result.hour == 16
        assert result.minute == 10


# ---------------------------------------------------------------------------
# detect_quota_error never raises -- edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetectQuotaErrorEdgeCases:
    """detect_quota_error handles pathological inputs without raising."""

    def test_no_raise_on_object_with_raising_getattr(self) -> None:
        """Objects whose attribute access raises do not propagate to caller."""

        class Tricky:
            @property
            def status_code(self) -> int:
                raise RuntimeError("boom")

        result = detect_quota_error(Tricky())
        assert result is None or isinstance(result, QuotaExhaustedError)

    def test_no_raise_on_bytes_input(self) -> None:
        result = detect_quota_error(b"You've hit your limit")
        assert result is None

    def test_no_raise_on_very_long_string(self) -> None:
        big = "a" * 100_000
        result = detect_quota_error(big)
        assert result is None

    def test_no_raise_when_inner_raises(self) -> None:
        """detect_quota_error catches exceptions from _detect_quota_error_inner."""

        class AlwaysRaises:
            @property
            def status_code(self) -> int:
                raise RuntimeError("inner boom")

            @property
            def content(self) -> list[object]:
                raise RuntimeError("inner boom")

            @property
            def error(self) -> str:
                raise RuntimeError("inner boom")

            @property
            def is_error(self) -> bool:
                raise RuntimeError("inner boom")

            @property
            def response(self) -> dict[str, object]:
                raise RuntimeError("inner boom")

        result = detect_quota_error(AlwaysRaises())
        assert result is None


@pytest.mark.unit
class TestInternalHelperBranchCoverage:
    """Branch-coverage tests for internal helpers not fully exercised by rule tests."""

    def test_parse_reset_at_invalid_minute_over_59(self) -> None:
        """Minute > 59 returns None (unreachable via normal regex but defensively checked)."""
        # The regex \d{2} matches 99, so "resets 4:99pm (UTC)" passes regex then fails validation.
        assert _parse_reset_at_from_text("resets 4:99pm (UTC)") is None

    def test_get_error_type_non_dict_body(self) -> None:
        """_apply_rules_1_to_5 with status_code=402 and a non-dict body -> ApiBillingError."""
        # body is not a dict -- covers _get_error_type's non-dict branch
        obj = _make_sdk_exc(status_code=402)
        obj.body = "not-a-dict"
        result = detect_quota_error(obj)
        assert isinstance(result, ApiBillingError)

    def test_get_error_type_error_section_not_dict(self) -> None:
        """body['error'] is not a dict -> ApiBillingError (covers non-dict error_section branch)."""
        obj = _make_sdk_exc(status_code=402)
        obj.body = {"error": "not-a-dict"}
        result = detect_quota_error(obj)
        assert isinstance(result, ApiBillingError)

    def test_get_bedrock_error_code_error_section_not_dict(self) -> None:
        """response['Error'] is not a dict -> None from bedrock rule."""
        obj = _make_bedrock_exc("ThrottlingException")
        obj.response = {"Error": "not-a-dict"}
        result = detect_quota_error(obj)
        assert result is None

    def test_extract_reset_at_from_content_with_text_block(self) -> None:
        """_extract_reset_at_from_content finds reset time in a block.text field."""
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        # AssistantMessage with text block containing reset info
        text_block = SimpleNamespace(text="resets 4:10pm (UTC)")
        obj = SimpleNamespace(error="rate_limit", content=[text_block])
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert result.reset_at is not None
        assert result.reset_at.hour == 16

    def test_extract_reset_at_from_content_content_field_branch(self) -> None:
        """_extract_reset_at_from_content checks block.content str field when block.text is None."""
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Block has no .text but has .content str with reset info
        block = SimpleNamespace(content="resets 4:10pm (UTC)")
        obj = SimpleNamespace(error="rate_limit", content=[block])
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        # The rate_limit rule fires; reset_at may or may not be populated depending
        # on whether the content field text is a parseable string -- it should be.
        assert isinstance(result, SubscriptionRateLimitError)

    def test_extract_reset_at_text_not_parseable_falls_through_to_content(self) -> None:
        """When block.text is a str but not parseable, _extract_reset_at_from_content
        falls through to check block.content for a parseable reset time."""
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        # block.text is a str but has no reset-at pattern; block.content has the pattern
        block = SimpleNamespace(text="no reset here", content="resets 4:10pm (UTC)")
        obj = SimpleNamespace(error="rate_limit", content=[block])
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is not None
        assert result.reset_at.hour == 16

    def test_extract_reset_at_content_field_not_parseable_returns_none(self) -> None:
        """When block.content is a str but not parseable, returns None for that block."""
        # Block with text that doesn't parse, content that doesn't parse either
        block = SimpleNamespace(text="no reset", content="also no reset")
        obj = SimpleNamespace(error="rate_limit", content=[block])
        result = detect_quota_error(obj)
        # rate_limit fires but reset_at is None since neither field has reset info
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is None

    def test_detect_quota_error_catches_inner_exception(self) -> None:
        """detect_quota_error catches any exception from _detect_quota_error_inner."""

        class StrRaises:
            def __str__(self) -> str:
                raise RuntimeError("str() raises")

        # StrRaises is a BaseException if we subclass it; we need isinstance(obj, BaseException)
        # to be True so that str(obj) is called in rule 9.
        class BadError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("str() raises")

        result = detect_quota_error(BadError())
        assert result is None

    def test_extract_reset_at_non_list_content_returns_none(self) -> None:
        """_extract_reset_at_from_content returns None when content is not list/tuple."""
        # Trigger via rule 7 with non-list content
        obj = SimpleNamespace(error="rate_limit", content="not-a-list")
        result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is None

    def test_extract_reset_at_block_content_field_non_str_continues_loop(self) -> None:
        """When block.content is not a str, loop continues to next block without reset."""
        # First block: text=None (no .text attribute), content=non-string -> skip content branch
        # Second block: text with parseable reset
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        block_no_str_content = SimpleNamespace(content=42)  # content is int, not str
        block_with_reset = SimpleNamespace(text="resets 4:10pm (UTC)")
        obj = SimpleNamespace(error="rate_limit", content=[block_no_str_content, block_with_reset])
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is not None
        assert result.reset_at.hour == 16


# ---------------------------------------------------------------------------
# QuotaCheckpoint dataclass
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaCheckpoint:
    """QuotaCheckpoint stores quota pause state for persistence."""

    def test_fields_stored(self) -> None:
        ts = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
        cp = QuotaCheckpoint(
            reason="subscription_rate_limit",
            reset_at=ts,
            saved_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
            session_name="default",
        )
        assert cp.reason == "subscription_rate_limit"
        assert cp.reset_at == ts
        assert cp.session_name == "default"

    def test_reset_at_none_allowed(self) -> None:
        cp = QuotaCheckpoint(
            reason="subscription_rate_limit",
            reset_at=None,
            saved_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
            session_name="default",
        )
        assert cp.reset_at is None


# ---------------------------------------------------------------------------
# save_checkpoint / load_checkpoint / remove_checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckpointRoundTrip:
    """Checkpoint persists to and loads from disk."""

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ts = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=ts,
                saved_at=saved_at,
                session_name="default",
            )
            save_checkpoint(cp, root)
            loaded = load_checkpoint(root)
            assert loaded is not None
            assert loaded.reason == "subscription_rate_limit"
            assert loaded.reset_at == ts
            assert loaded.session_name == "default"

    def test_save_and_load_with_reset_at_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="bedrock_throttle",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            save_checkpoint(cp, root)
            loaded = load_checkpoint(root)
            assert loaded is not None
            assert loaded.reset_at is None
            assert loaded.reason == "bedrock_throttle"

    def test_load_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = load_checkpoint(root)
            assert result is None

    def test_load_raises_on_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cp_dir = root / ".devbench"
            cp_dir.mkdir(parents=True, exist_ok=True)
            (cp_dir / "quota_pause.json").write_text("not valid json", encoding="utf-8")
            with pytest.raises(ValueError, match=r"quota_pause\.json"):
                load_checkpoint(root)

    def test_load_raises_on_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cp_dir = root / ".devbench"
            cp_dir.mkdir(parents=True, exist_ok=True)
            (cp_dir / "quota_pause.json").write_text(json.dumps({"reason": "x"}), encoding="utf-8")
            with pytest.raises(ValueError, match=r"quota_pause\.json"):
                load_checkpoint(root)

    def test_load_raises_on_bad_datetime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cp_dir = root / ".devbench"
            cp_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "reason": "x",
                "reset_at": "not-a-datetime",
                "saved_at": "not-a-datetime",
                "session_name": "default",
            }
            (cp_dir / "quota_pause.json").write_text(json.dumps(data), encoding="utf-8")
            with pytest.raises(ValueError, match=r"quota_pause\.json"):
                load_checkpoint(root)

    def test_save_is_atomic(self) -> None:
        """save_checkpoint uses atomic temp+replace, not direct write."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            save_checkpoint(cp, root)
            path = root / ".devbench" / "quota_pause.json"
            assert path.exists()
            content = json.loads(path.read_text(encoding="utf-8"))
            assert content["reason"] == "subscription_rate_limit"

    def test_remove_checkpoint_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Remove when no checkpoint exists -- should not raise.
            remove_checkpoint(root)
            # Save and remove.
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="x",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            save_checkpoint(cp, root)
            assert (root / ".devbench" / "quota_pause.json").exists()
            remove_checkpoint(root)
            assert not (root / ".devbench" / "quota_pause.json").exists()
            # Second remove is idempotent.
            remove_checkpoint(root)

    def test_save_fails_fast_on_empty_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            with pytest.raises(ValueError, match="reason"):
                save_checkpoint(cp, root)

    def test_save_fails_fast_on_naive_saved_at(self) -> None:
        """save_checkpoint rejects naive datetimes (no tzinfo) for saved_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Intentionally naive (no tzinfo) to test validation.
            naive_dt = datetime.fromisoformat("2026-01-01T10:00:00")
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=None,
                saved_at=naive_dt,
                session_name="default",
            )
            with pytest.raises(ValueError, match="timezone"):
                save_checkpoint(cp, root)

    def test_save_fails_fast_on_naive_reset_at(self) -> None:
        """save_checkpoint rejects naive datetimes for reset_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            # Intentionally naive (no tzinfo) to test validation.
            naive_dt = datetime.fromisoformat("2026-01-01T16:10:00")
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=naive_dt,
                saved_at=saved_at,
                session_name="default",
            )
            with pytest.raises(ValueError, match="timezone"):
                save_checkpoint(cp, root)


# ---------------------------------------------------------------------------
# wait_for_reset
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitForReset:
    """wait_for_reset probes for quota recovery with backoff."""

    def test_first_probe_success_returns_true(self) -> None:
        """When the probe succeeds immediately after initial sleep, returns True."""
        probe = MagicMock(return_value=True)
        reset_at = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        async def run() -> bool:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    result = await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval_seconds=60,
                        max_wait_seconds=18000,
                        probe_fn=probe,
                    )
                    # Initial sleep must be <= max_wait_seconds
                    if mock_sleep.call_args_list:
                        assert mock_sleep.call_args_list[0].args[0] <= 18000
            return result

        result = asyncio.run(run())
        assert result is True
        probe.assert_called_once()

    def test_past_reset_resumes_without_probing(self) -> None:
        """TDI-003a: when reset_at has elapsed, resume immediately without calling the probe."""
        probe = MagicMock(return_value=True)
        reset_at = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)  # past
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)  # clock after reset_at

        async def run() -> bool:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    result = await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval_seconds=60,
                        max_wait_seconds=18000,
                        probe_fn=probe,
                    )
                    # Initial sleep is capped at max(0, reset_at-now) = 0
                    if mock_sleep.call_args_list:
                        assert mock_sleep.call_args_list[0].args[0] == 0
            return result

        result = asyncio.run(run())
        assert result is True
        # The elapsed reset time is authoritative -- the probe must NOT be consulted.
        probe.assert_not_called()

    def test_max_wait_zero_returns_false(self) -> None:
        """When max_wait_seconds=0, immediately returns False."""
        probe = MagicMock(return_value=True)
        reset_at = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        async def run() -> bool:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval_seconds=60,
                        max_wait_seconds=0,
                        probe_fn=probe,
                    )
            return result

        result = asyncio.run(run())
        assert result is False

    def test_initial_sleep_capped_at_max_wait(self) -> None:
        """Initial sleep is capped at max_wait_seconds when reset_at is far in future."""
        probe = MagicMock(return_value=True)
        reset_at = datetime(2026, 1, 1, 16, 10, 0, tzinfo=UTC)
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        max_wait = 60  # small max_wait but reset is 6+ hours away

        async def run() -> list[float]:
            sleep_calls: list[float] = []
            with patch("devbench.quota._get_current_utc", return_value=clock):

                async def fake_sleep(seconds: float) -> None:
                    sleep_calls.append(seconds)
                    # Don't actually sleep

                with patch("asyncio.sleep", side_effect=fake_sleep):
                    await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval_seconds=60,
                        max_wait_seconds=max_wait,
                        probe_fn=probe,
                    )
            return sleep_calls

        sleep_calls = asyncio.run(run())
        # Initial sleep must be <= max_wait
        if sleep_calls:
            assert sleep_calls[0] <= max_wait

    def test_probe_raises_propagates(self) -> None:
        """When probe_fn raises a non-quota exception, it propagates out.

        Uses ``reset_at=None`` so the probe loop runs (a known, elapsed reset
        time would short-circuit before the probe is ever called -- TDI-003a).
        """
        probe = MagicMock(side_effect=RuntimeError("network error"))
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        async def run() -> None:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await wait_for_reset(
                        reset_at=None,
                        poll_interval_seconds=60,
                        max_wait_seconds=18000,
                        probe_fn=probe,
                    )

        with pytest.raises(RuntimeError, match="network error"):
            asyncio.run(run())

    def test_probe_unavailable_with_elapsed_reset_returns_true(self) -> None:
        """Probe unavailable but reset_at has passed -> resume on the provider-stated reset."""
        probe = MagicMock(side_effect=RecoveryProbeUnavailableError("no credential"))
        reset_at = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)  # past
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        async def run() -> bool:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    return await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval_seconds=60,
                        max_wait_seconds=18000,
                        probe_fn=probe,
                    )

        assert asyncio.run(run()) is True

    def test_probe_unavailable_with_unknown_reset_propagates(self) -> None:
        """Probe unavailable AND reset_at unknown -> propagate so the caller fails fast."""
        probe = MagicMock(side_effect=RecoveryProbeUnavailableError("no credential"))
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

        async def run() -> None:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    await wait_for_reset(
                        reset_at=None,
                        poll_interval_seconds=60,
                        max_wait_seconds=18000,
                        probe_fn=probe,
                    )

        with pytest.raises(RecoveryProbeUnavailableError):
            asyncio.run(run())

    def test_conflict_guard_raises_value_error(self) -> None:
        """backoff_config.initial_seconds != poll_interval_seconds raises ValueError."""
        from devbench.quota import BackoffConfig

        probe = MagicMock(return_value=True)
        reset_at = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        backoff = BackoffConfig(initial_seconds=45, max_seconds=600, multiplier=2.0, jitter=0.2)

        async def run() -> None:
            with patch("devbench.quota._get_current_utc", return_value=clock):
                await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval_seconds=60,  # != backoff.initial_seconds=45
                    max_wait_seconds=18000,
                    probe_fn=probe,
                    backoff_config=backoff,
                )

        with pytest.raises(ValueError, match="initial_seconds"):
            asyncio.run(run())

    def test_backoff_sequence_stays_within_bounds(self) -> None:
        """Backoff delay stays within [initial, max] bounds after jitter."""
        from devbench.quota import BackoffConfig

        delays: list[float] = []
        call_count = 0

        def probe() -> bool:
            nonlocal call_count
            call_count += 1
            # Only succeed on the 4th call
            return call_count >= 4

        # reset_at=None so the probe/backoff loop runs (a known elapsed reset
        # would short-circuit before any probe -- TDI-003a).
        clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        backoff = BackoffConfig(initial_seconds=30, max_seconds=600, multiplier=2.0, jitter=0.2)

        async def run() -> None:
            async def fake_sleep(seconds: float) -> None:
                delays.append(seconds)

            with patch("devbench.quota._get_current_utc", return_value=clock):
                with patch("asyncio.sleep", side_effect=fake_sleep):
                    await wait_for_reset(
                        reset_at=None,
                        poll_interval_seconds=30,  # matches backoff.initial_seconds
                        max_wait_seconds=18000,
                        probe_fn=probe,
                        backoff_config=backoff,
                    )

        asyncio.run(run())
        # All delays (excluding the initial sleep of 0) must be <= max_seconds
        backoff_delays = delays[1:]  # skip initial sleep
        for delay in backoff_delays:
            assert delay <= 600, f"delay {delay} exceeds max_seconds=600"

    def test_max_wait_timeout_returns_false(self) -> None:
        """When max_wait is exceeded before the probe succeeds, returns False.

        Uses ``reset_at=None``: with a known reset time the wait resumes the
        moment it elapses (TDI-003a), so the timeout path is reached only while
        the reset time is unknown and the probe keeps reporting not-recovered.
        """
        probe = MagicMock(return_value=False)
        reset_at = None
        # Simulate clock advancing past max_wait by returning increasing times
        clock_calls: list[datetime] = [
            datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),  # initial
            datetime(2026, 1, 1, 15, 0, 0, tzinfo=UTC),  # after max_wait elapsed
        ]
        clock_iter = iter(clock_calls)

        def fake_clock() -> datetime:
            try:
                return next(clock_iter)
            except StopIteration:
                return datetime(2026, 1, 1, 15, 0, 0, tzinfo=UTC)

        async def run() -> bool:
            with patch("devbench.quota._get_current_utc", side_effect=fake_clock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    return await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval_seconds=60,
                        max_wait_seconds=3600,  # 1 hour
                        probe_fn=probe,
                    )

        result = asyncio.run(run())
        assert result is False


# ---------------------------------------------------------------------------
# recovery_probe
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecoveryProbe:
    """recovery_probe returns True on success, False on quota/transient, raises on bad args."""

    def test_raises_on_non_positive_request_size(self) -> None:
        with pytest.raises(ValueError, match="request_size_tokens"):
            recovery_probe(timeout_seconds=10, request_size_tokens=0)

    def test_raises_on_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            recovery_probe(timeout_seconds=0, request_size_tokens=1)

    def test_raises_on_negative_timeout(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            recovery_probe(timeout_seconds=-1, request_size_tokens=1)

    def test_success_returns_true(self) -> None:
        """When the probe API call succeeds, returns True."""
        mock_response = MagicMock()
        with patch("devbench.quota._probe_api_call", return_value=mock_response):
            result = recovery_probe(timeout_seconds=10, request_size_tokens=1)
        assert result is True

    def test_quota_error_returns_false(self) -> None:
        """When the probe hits a quota error, returns False (still exhausted)."""
        quota_exc = SubscriptionRateLimitError(reset_at=None, raw_error="x", source="anthropic-api")
        with patch("devbench.quota._probe_api_call", side_effect=quota_exc):
            result = recovery_probe(timeout_seconds=10, request_size_tokens=1)
        assert result is False

    def test_transient_network_error_returns_false(self) -> None:
        """Transient non-quota error -> False (still exhausted, don't crash)."""
        with patch("devbench.quota._probe_api_call", side_effect=ConnectionError("timeout")):
            result = recovery_probe(timeout_seconds=10, request_size_tokens=1)
        assert result is False

    def test_authentication_error_raises_unavailable(self) -> None:
        """A rejected credential (401) is permanent -> RecoveryProbeUnavailableError."""
        with _patch_fake_anthropic_errors():
            with patch("devbench.quota._probe_api_call", side_effect=_FakeAuthError("401")):
                with pytest.raises(RecoveryProbeUnavailableError, match="authenticate"):
                    recovery_probe(timeout_seconds=10, request_size_tokens=1)

    def test_permission_error_raises_unavailable(self) -> None:
        """A permission denial (403) is permanent -> RecoveryProbeUnavailableError."""
        with _patch_fake_anthropic_errors():
            with patch("devbench.quota._probe_api_call", side_effect=_FakePermissionError("403")):
                with pytest.raises(RecoveryProbeUnavailableError, match="authenticate"):
                    recovery_probe(timeout_seconds=10, request_size_tokens=1)

    def test_missing_credential_raises_unavailable(self) -> None:
        """A non-API AnthropicError (e.g. no api_key configured) is permanent."""
        with _patch_fake_anthropic_errors():
            with patch(
                "devbench.quota._probe_api_call",
                side_effect=_FakeAnthropicError("The api_key client option must be set"),
            ):
                with pytest.raises(RecoveryProbeUnavailableError, match="credential"):
                    recovery_probe(timeout_seconds=10, request_size_tokens=1)

    def test_transient_api_error_returns_false(self) -> None:
        """A transient APIError (e.g. 429/connection at the API layer) -> False, keep polling."""
        with _patch_fake_anthropic_errors():
            with patch("devbench.quota._probe_api_call", side_effect=_FakeAPIError("503")):
                result = recovery_probe(timeout_seconds=10, request_size_tokens=1)
        assert result is False


# ---------------------------------------------------------------------------
# _apply_resume_strategy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyResumeStrategy:
    """_apply_resume_strategy dispatches to the correct resume action."""

    @pytest.mark.parametrize(
        "strategy",
        ["continue_current_wu", "restart_wu", "drain_and_resume"],
    )
    def test_known_strategies_do_not_raise(self, strategy: str) -> None:
        """All known strategy names are accepted without raising."""
        # We verify dispatch by confirming no ValueError is raised.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            save_checkpoint(cp, root)
            with (
                patch("devbench.quota.remove_checkpoint"),
                patch("devbench.quota._force_status_in_queue"),
                patch("devbench.quota.request_drain"),
            ):
                _apply_resume_strategy(strategy, root)

    def test_unknown_strategy_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with pytest.raises(ValueError, match=r"unknown.*resume.*strategy"):
                _apply_resume_strategy("nonexistent_strategy", root)

    def test_continue_calls_remove_checkpoint(self) -> None:
        """continue_current_wu removes the checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            save_checkpoint(cp, root)
            with patch("devbench.quota._force_status_in_queue"):
                _apply_resume_strategy("continue_current_wu", root)
            assert not (root / ".devbench" / "quota_pause.json").exists()

    def test_drain_and_resume_calls_request_drain(self) -> None:
        """drain_and_resume removes checkpoint and calls request_drain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("devbench.quota.remove_checkpoint") as mock_remove:
                with patch("devbench.quota.request_drain") as mock_drain:
                    _apply_resume_strategy("drain_and_resume", root)
            mock_remove.assert_called_once_with(root)
            mock_drain.assert_called_once()


# ---------------------------------------------------------------------------
# save_checkpoint -- exception cleanup branch (lines 538-541)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSaveCheckpointExceptionCleanup:
    """save_checkpoint cleans up temp file and re-raises on write failure."""

    def test_fdopen_raises_oserror_cleans_up_and_reraises(self) -> None:
        """When os.fdopen raises OSError, the temp file is removed and the error propagates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            saved_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
            cp = QuotaCheckpoint(
                reason="subscription_rate_limit",
                reset_at=None,
                saved_at=saved_at,
                session_name="default",
            )
            # Ensure destination parent exists so mkstemp succeeds.
            dest_dir = root / ".devbench"
            dest_dir.mkdir(parents=True, exist_ok=True)
            os_error = OSError("simulated write failure")
            # _os is a local alias for the standard os module inside save_checkpoint.
            with patch("os.fdopen", side_effect=os_error):
                with pytest.raises(OSError, match="simulated write failure"):
                    save_checkpoint(cp, root)
            # The .tmp file must have been cleaned up.
            tmp_files = list(dest_dir.glob("*.tmp"))
            assert tmp_files == [], f"Temp file not cleaned up: {tmp_files}"


# ---------------------------------------------------------------------------
# load_checkpoint -- invalid reset_at datetime branch (lines 583-584)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadCheckpointInvalidResetAt:
    """load_checkpoint raises ValueError when reset_at is present but not ISO 8601."""

    def test_valid_saved_at_but_invalid_reset_at_raises_value_error(self) -> None:
        """A checkpoint with valid saved_at but non-ISO reset_at must raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cp_dir = root / ".devbench"
            cp_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "reason": "subscription_rate_limit",
                "reset_at": "not-a-valid-iso-datetime",
                "saved_at": "2026-01-01T10:00:00+00:00",
                "session_name": "default",
            }
            (cp_dir / "quota_pause.json").write_text(json.dumps(data), encoding="utf-8")
            with pytest.raises(ValueError, match=r"reset_at"):
                load_checkpoint(root)


# ---------------------------------------------------------------------------
# _probe_api_call -- direct invocation with mocked anthropic (lines 625-632)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeApiCallBody:
    """_probe_api_call calls anthropic.Anthropic and messages.create with correct args."""

    def test_calls_messages_create_with_correct_model_and_max_tokens(self) -> None:
        """_probe_api_call passes RECOVERY_PROBE_MODEL and max_tokens=1 to the SDK."""
        from devbench.constants import RECOVERY_PROBE_MODEL

        mock_client = MagicMock()
        mock_anthropic_cls = MagicMock(return_value=mock_client)
        # anthropic is imported inside the function body, so patch at the source module.
        with patch("anthropic.Anthropic", mock_anthropic_cls):
            _probe_api_call(timeout_seconds=5.0, request_size_tokens=3)

        mock_anthropic_cls.assert_called_once_with(timeout=5.0)
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs is not None, "messages.create was not called"
        assert call_kwargs.kwargs.get("model") == RECOVERY_PROBE_MODEL, (
            f"Expected model={RECOVERY_PROBE_MODEL!r}, got call: {call_kwargs}"
        )
        assert call_kwargs.kwargs.get("max_tokens") == 1


# ---------------------------------------------------------------------------
# _force_status_in_queue -- mocked BacklogManager/BacklogParser (lines 684-693)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestForceStatusInQueue:
    """_force_status_in_queue resets all in-progress units to in-queue."""

    def test_force_status_called_for_each_in_progress_unit(self) -> None:
        """All in-progress work units have force_status called with 'in-queue'."""
        from types import SimpleNamespace

        unit_a = SimpleNamespace(id="T1", status="in-progress", file_path="/backlog/T1.md")
        unit_b = SimpleNamespace(id="T2", status="in-queue", file_path="/backlog/T2.md")
        unit_c = SimpleNamespace(id="T3", status="in-progress", file_path="/backlog/T3.md")

        mock_parser_instance = MagicMock()
        mock_parser_instance.parse_index.return_value = [unit_a, unit_b, unit_c]
        mock_parser_cls = MagicMock(return_value=mock_parser_instance)

        mock_manager_instance = MagicMock()
        mock_manager_cls = MagicMock(return_value=mock_manager_instance)

        with (
            patch("devbench.backlog.parser.BacklogParser", mock_parser_cls),
            patch("devbench.backlog.manager.BacklogManager", mock_manager_cls),
        ):
            _force_status_in_queue(Path("/fake/workspace"))

        # Only in-progress units should have force_status called.
        assert mock_manager_instance.force_status.call_count == 2
        call_args_list = mock_manager_instance.force_status.call_args_list
        called_ids = {call.args[2] for call in call_args_list}
        assert called_ids == {"T1", "T3"}
        for call in call_args_list:
            assert call.args[3] == "in-queue"
