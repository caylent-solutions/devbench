"""Tests for src/devbench/quota.py -- detection tier.

Coverage requirement: 100% line + branch on the detection-tier symbols added
in this task (the wait/checkpoint/probe symbols belong to E2-F1-S2-T1 and are
not covered here).

Covers:
- QuotaExhaustedError and its four LSP subclasses (SubscriptionRateLimitError,
  SdkCreditExhaustedError, ApiBillingError, BedrockThrottleError) plus the
  standalone RecoveryProbeUnavailableError (FR-2.1).
- detect_quota_error(obj): the ten ordered rules (FR-2.2), never raises.
- The two-matcher design (D-7): _has_quota_marker (markers OR _RATE_LIMIT_RE,
  used only by Rule 9) and _has_verbatim_quota_marker (markers ONLY, used by
  Rules 6 and 8).
- _parse_reset_at_from_text(text): the CLI-text reset-time parser (FR-2.3).
- The seven named false-positive regression tests from spec S10.1, ported
  verbatim from branch commit bd5945e (the false-pause fix).
- The four #234 signal surfaces (spec AC-10).
- Marker-integrity: the first _QUOTA_MARKERS entry is the curly-apostrophe
  escape sequence byte-for-byte, in both value and source spelling.

Issue #234 (Appendix A QW-1 / QW-2 / QW-10).
AC-234-1, AC-234a-1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench.quota import (
    _QUOTA_MARKERS,
    ApiBillingError,
    BedrockThrottleError,
    QuotaExhaustedError,
    RecoveryProbeUnavailableError,
    SdkCreditExhaustedError,
    SubscriptionRateLimitError,
    _has_quota_marker,
    _has_verbatim_quota_marker,
    _parse_reset_at_from_text,
    detect_quota_error,
)

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
    """Build a UserMessage-shaped object with an ERROR ToolResultBlock containing quota text.

    Rule 6 only scans explicit-error tool results (``is_error is True``); a
    genuine sub-agent quota limit surfaces as an error result, so the fixture
    sets ``is_error=True``.
    """
    block = SimpleNamespace(
        tool_use_id="test-tool-id",
        content=marker_text,
        is_error=True,
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
        assert "[resets=unknown]" in str(exc)

    def test_is_base_exception_subclass(self) -> None:
        assert issubclass(QuotaExhaustedError, Exception)

    def test_raise_and_catch_as_quota_exhausted(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(QuotaExhaustedError)


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


@pytest.mark.unit
class TestRecoveryProbeUnavailableError:
    """RecoveryProbeUnavailableError is a standalone exception (not a QuotaExhaustedError)."""

    def test_is_exception(self) -> None:
        assert isinstance(RecoveryProbeUnavailableError("probe down"), Exception)

    def test_is_not_a_quota_exhausted_error(self) -> None:
        """Distinct from QuotaExhaustedError: 'probe unavailable' != 'still rate limited'."""
        assert not issubclass(RecoveryProbeUnavailableError, QuotaExhaustedError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(RecoveryProbeUnavailableError):
            raise RecoveryProbeUnavailableError("no credential configured")


@pytest.mark.unit
class TestQuotaMarkerIntegrity:
    """AC-E2-F1-S1-T1-6: the first _QUOTA_MARKERS entry is the curly-apostrophe
    escape sequence byte-for-byte, and the source spells it as ``\\u2019``
    (not a raw curly apostrophe character), so the value survives encoding
    mishandling the way the CLI's real message does."""

    def test_first_marker_value_is_curly_apostrophe_form(self) -> None:
        assert _QUOTA_MARKERS[0] == "You\u2019ve hit your limit"

    def test_source_uses_escape_spelling_not_raw_character(self) -> None:
        module_path = Path(__file__).resolve().parent.parent / "src" / "devbench" / "quota.py"
        source_text = module_path.read_text(encoding="utf-8")
        marker_line = next(
            line
            for line in source_text.splitlines()
            if "You" in line and "hit your limit" in line and "u2019" in line.lower()
        )
        assert "\\u2019" in marker_line
        assert "\u2019" not in marker_line


@pytest.mark.unit
class TestHasQuotaMarker:
    """_has_quota_marker matches the verbatim markers OR _RATE_LIMIT_RE. Used only by Rule 9."""

    @pytest.mark.parametrize(
        "text",
        [
            "You've hit your limit",
            "you've hit your limit",
            "You have hit your limit",
            "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)",
            "rate limit exceeded",
            "rate limit reached",
            "rate limit hit",
            "rate limit exhausted",
            "rate-limit exceeded",
            "rate limits reached",
            "Rate Limit Exceeded",
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
            "rate limit",
            "rate limiting",
            "Implement rate limiting to prevent abuse",
            "rate limit not exceeded",
            "API endpoints implement rate limiting, CORS policies, and required security headers.",
            "Missing security headers, overly permissive CORS, missing rate limiting.",
        ],
    )
    def test_non_matching_text(self, text: str) -> None:
        assert _has_quota_marker(text) is False

    def test_code_reviewer_prose_is_not_quota(self) -> None:
        """Regression: the code-reviewer criterion must not be a quota marker.

        Source: plugin/devbench-orchestrate/agents/review_team/code-reviewer.md.
        The bare "rate limit" substring used to match the "rate limiting" in
        such review prose, falsely pausing the orchestrator on every security
        review.
        """
        prose = "API endpoints implement rate limiting, CORS policies, and required security headers."
        assert _has_quota_marker(prose) is False

    def test_non_string_returns_false(self) -> None:
        assert _has_quota_marker(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert _has_quota_marker("") is False

    def test_integer_returns_false(self) -> None:
        assert _has_quota_marker(42) is False

    def test_verbatim_cli_line_matches(self) -> None:
        """The exact verbatim CLI line must match (AC-234-1)."""
        verbatim = "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)"
        assert _has_quota_marker(verbatim) is True

    def test_partial_match_in_longer_string(self) -> None:
        assert _has_quota_marker("prefix You've hit your limit suffix") is True


@pytest.mark.unit
class TestHasVerbatimQuotaMarker:
    """_has_verbatim_quota_marker matches ONLY the verbatim CLI lines, never the regex.

    Used for tool-result/result content scanning (Rules 6/8) so benign tool
    output -- including devbench's own source code that quotes 'rate limit
    exceeded' -- never trips a false quota pause.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "You've hit your limit",
            "you've hit your limit",
            "You have hit your limit",
            "You\u2019ve hit your limit \u00b7 resets 4:10pm (UTC)",
            "prefix You've hit your limit suffix",
        ],
    )
    def test_verbatim_lines_match(self, text: str) -> None:
        assert _has_verbatim_quota_marker(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            # The regex family is deliberately NOT applied to tool content.
            "rate limit exceeded",
            "rate limit reached",
            "Rate Limit Exceeded",
            'raise AmendmentError(f"Amendment rate limit exceeded: {n} applied")',
            "implement rate limiting",
            "",
        ],
    )
    def test_regex_and_benign_phrases_do_not_match(self, text: str) -> None:
        assert _has_verbatim_quota_marker(text) is False

    def test_non_string_returns_false(self) -> None:
        assert _has_verbatim_quota_marker(None) is False
        assert _has_verbatim_quota_marker(42) is False


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
        assert result.day == 2
        assert result.hour == 16
        assert result.minute == 10

    def test_next_day_rollover_result_strictly_future(self) -> None:
        """The rolled-over candidate is strictly after the current clock."""
        late_clock = datetime(2026, 1, 1, 18, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=late_clock):
            result = _parse_reset_at_from_text("resets 4:10pm (UTC)")
        assert result is not None
        assert result > late_clock

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

    def test_none_on_malformed_hour_0(self) -> None:
        """Hour 0 is invalid (12-hour clock uses 1-12) -> None."""
        assert _parse_reset_at_from_text("resets 0:30pm (UTC)") is None

    def test_none_on_malformed_minute_60(self) -> None:
        """Minute 60 is invalid -> None."""
        assert _parse_reset_at_from_text("resets 4:60pm (UTC)") is None

    def test_none_on_non_utc_timezone(self) -> None:
        """Non-(UTC) timezone label -> None (the (UTC) literal is required, D-8)."""
        assert _parse_reset_at_from_text("resets 4:10pm (EST)") is None

    def test_none_on_missing_utc_literal_entirely(self) -> None:
        """No timezone label at all -> None."""
        assert _parse_reset_at_from_text("resets 4:10pm") is None

    def test_none_on_non_string(self) -> None:
        """Non-string input -> None."""
        assert _parse_reset_at_from_text(None) is None
        assert _parse_reset_at_from_text(42) is None

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

    def test_ascii_apostrophe_form_parses_identically(self) -> None:
        """Both apostrophe forms (curly and ASCII) parse the reset time identically."""
        ascii_form = "You've hit your limit -- resets 9:45am (UTC)"
        clock = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = _parse_reset_at_from_text(ascii_form)
        assert result is not None
        assert result.hour == 9
        assert result.minute == 45


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
    """detect_quota_error applies the ten rules in order (AC-234a-1, spec AC-9)."""

    # Rule 1
    def test_rule1_passthrough_already_quota_error(self) -> None:
        exc = _make_exc(QuotaExhaustedError)
        assert detect_quota_error(exc) is exc

    def test_rule1_passthrough_subclass_instances(self) -> None:
        exc = _make_exc(SubscriptionRateLimitError)
        assert detect_quota_error(exc) is exc

    # Rule 2
    def test_rule2_http_429_returns_subscription_rate_limit(self) -> None:
        obj = _make_sdk_exc(status_code=429)
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)

    def test_rule2_source_is_anthropic_api(self) -> None:
        obj = _make_sdk_exc(status_code=429)
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "anthropic-api"

    # Rule 3
    def test_rule3_http_402_insufficient_quota_returns_sdk_credit_error(self) -> None:
        obj = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(obj)
        assert isinstance(result, SdkCreditExhaustedError)

    def test_rule3_source_is_sdk(self) -> None:
        obj = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(obj)
        assert result is not None
        assert result.source == "sdk"

    # Rule 4
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

    # Rule 5
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

    # Rule 6
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
        """False-positive regression (spec S10.1): a sub-agent reviewer's prose
        mentioning rate limiting is not a quota hit."""
        obj = _make_user_message_with_quota_marker(
            "API endpoints implement rate limiting, CORS policies, and required security headers."
        )
        assert detect_quota_error(obj) is None

    def test_rule6_is_error_true_with_verbatim_marker_detected(self) -> None:
        """An errored tool result carrying a VERBATIM CLI limit line is detected (genuine sub-agent limit)."""
        block = SimpleNamespace(tool_use_id="t", content="You've hit your limit", is_error=True)
        obj = SimpleNamespace(content=[block])
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule6_is_error_true_with_regex_phrase_not_detected(self) -> None:
        """False-positive regression (spec S10.1): even an ERROR tool result is
        verbatim-only -- the broad 'rate limit exceeded' regex is NOT applied to
        tool content (it appears in devbench's own source the agent reads)."""
        block = SimpleNamespace(tool_use_id="t", content="rate limit exceeded", is_error=True)
        obj = SimpleNamespace(content=[block])
        assert detect_quota_error(obj) is None

    def test_rule6_is_error_none_successful_read_not_detected(self) -> None:
        """False-positive regression (spec S10.1): a SUCCESSFUL tool result has
        is_error=None (Read/Grep/Glob) and must NOT be scanned -- even with a
        verbatim-looking marker."""
        block = SimpleNamespace(tool_use_id="t", content="You've hit your limit", is_error=None)
        obj = SimpleNamespace(content=[block])
        assert detect_quota_error(obj) is None

    def test_rule6_successful_read_of_amendment_source_not_detected(self) -> None:
        """False-positive regression (spec S10.1): a successful Read (is_error=None)
        of devbench's amendment.py -- whose check_rate_limit emits 'Amendment
        rate limit exceeded: ...' -- must NOT trip a false [QUOTA_WAITING]."""
        amendment_src = (
            "def check_rate_limit(self, prior_applied_count: int) -> None:\n"
            "    if prior_applied_count >= self._config.max_requests_per_execution:\n"
            "        raise AmendmentError(\n"
            '            f"Amendment rate limit exceeded: {prior_applied_count} amendment(s) already applied"\n'
            "        )\n"
        )
        block = SimpleNamespace(tool_use_id="t", content=amendment_src, is_error=None)
        obj = SimpleNamespace(content=[block])
        assert detect_quota_error(obj) is None

    def test_rule6_is_error_false_skipped(self) -> None:
        """A successful Bash tool result (is_error=False) is never a quota signal."""
        block = SimpleNamespace(tool_use_id="t", content="You've hit your limit", is_error=False)
        obj = SimpleNamespace(content=[block])
        assert detect_quota_error(obj) is None

    # Rule 7
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

    # Rule 8
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

    def test_rule8_regex_phrase_not_detected(self) -> None:
        """False-positive regression (spec S10.1): Rule 8 is verbatim-only --
        'rate limit exceeded' in result text is NOT a quota signal."""
        obj = _make_result_message_error("error: rate limit exceeded for this operation")
        assert detect_quota_error(obj) is None

    # Rule 9
    def test_rule9_base_exception_with_quota_marker(self) -> None:
        obj = Exception("You've hit your limit -- rate limit")
        result = detect_quota_error(obj)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.source == "claude-code-cli"

    def test_rule9_regex_only_exception_still_detected(self) -> None:
        """False-positive regression counterpart (spec S10.1): Rule 9 keeps the
        full matcher -- an exception message with only the 'rate limit exceeded'
        regex phrase (no verbatim line) IS detected -- an exception message is
        authoritative, unlike arbitrary tool content."""
        obj = Exception("anthropic.RateLimitError: rate limit exceeded, try again later")
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

    # Rule 10
    def test_rule10_unrecognized_input_returns_none(self) -> None:
        assert detect_quota_error(None) is None
        assert detect_quota_error(42) is None
        assert detect_quota_error("random string") is None
        assert detect_quota_error(SimpleNamespace()) is None


@pytest.mark.unit
class TestAC234FourSurfaces:
    """spec AC-10 / AC-234-1: each of the four CLI surfaces yields SubscriptionRateLimitError."""

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


@pytest.mark.unit
class TestDetectQuotaErrorEdgeCases:
    """spec AC-12: detect_quota_error handles pathological inputs without raising."""

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

    def test_no_raise_on_non_iterable_content(self) -> None:
        """content is present but not list/tuple -- Rule 6 must not raise iterating it."""
        obj = SimpleNamespace(content=42)
        result = detect_quota_error(obj)
        assert result is None

    def test_no_raise_on_content_with_hostile_block(self) -> None:
        """A content block whose attribute access raises must not propagate."""

        class HostileBlock:
            @property
            def is_error(self) -> bool:
                raise RuntimeError("block boom")

        obj = SimpleNamespace(content=[HostileBlock()])
        result = detect_quota_error(obj)
        assert result is None

    def test_no_raise_on_object_whose_dunder_getattr_raises(self) -> None:
        """An object whose __getattr__ always raises must not propagate."""

        class RaisingGetattr:
            def __getattr__(self, name: str) -> Any:
                raise AttributeError(f"cannot access {name}")

        result = detect_quota_error(RaisingGetattr())
        assert result is None


@pytest.mark.unit
class TestInternalHelperBranchCoverage:
    """Branch-coverage tests for internal helpers not fully exercised by rule tests."""

    def test_parse_reset_at_invalid_minute_over_59(self) -> None:
        """Minute > 59 returns None (unreachable via normal regex but defensively checked)."""
        assert _parse_reset_at_from_text("resets 4:99pm (UTC)") is None

    def test_get_error_type_non_dict_body(self) -> None:
        """status_code=402 and a non-dict body -> ApiBillingError."""
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
        block = SimpleNamespace(content="resets 4:10pm (UTC)")
        obj = SimpleNamespace(error="rate_limit", content=[block])
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)

    def test_extract_reset_at_text_not_parseable_falls_through_to_content(self) -> None:
        """When block.text is a str but not parseable, _extract_reset_at_from_content
        falls through to check block.content for a parseable reset time."""
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
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
        block = SimpleNamespace(text="no reset", content="also no reset")
        obj = SimpleNamespace(error="rate_limit", content=[block])
        result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is None

    def test_detect_quota_error_catches_inner_exception(self) -> None:
        """detect_quota_error catches any exception from _detect_quota_error_inner."""

        class BadError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("str() raises")

        result = detect_quota_error(BadError())
        assert result is None

    def test_extract_reset_at_non_list_content_returns_none(self) -> None:
        """_extract_reset_at_from_content returns None when content is not list/tuple."""
        obj = SimpleNamespace(error="rate_limit", content="not-a-list")
        result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is None

    def test_extract_reset_at_block_content_field_non_str_continues_loop(self) -> None:
        """When block.content is not a str, loop continues to next block without reset."""
        clock = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        block_no_str_content = SimpleNamespace(content=42)
        block_with_reset = SimpleNamespace(text="resets 4:10pm (UTC)")
        obj = SimpleNamespace(error="rate_limit", content=[block_no_str_content, block_with_reset])
        with patch("devbench.quota._get_current_utc", return_value=clock):
            result = detect_quota_error(obj)
        assert result is not None
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.reset_at is not None
        assert result.reset_at.hour == 16
