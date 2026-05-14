"""Tests for devbench.quota -- exception hierarchy, detect_quota_error,
and parse_reset_time.

Covers: QuotaExhaustedError, SubscriptionRateLimitError, SdkCreditExhaustedError,
ApiBillingError, BedrockThrottleError.  Each exception carries reset_at,
raw_error and source fields.

Also covers: detect_quota_error(message_or_exception) which classifies
incoming SDK exceptions / response objects into the structured quota hierarchy.

Also covers: parse_reset_time(headers) which extracts a UTC datetime from
Retry-After (integer seconds or HTTP-date) or anthropic-ratelimit-*-reset
(epoch seconds) headers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from devbench.quota import (
    ApiBillingError,
    BedrockThrottleError,
    QuotaExhaustedError,
    SdkCreditExhaustedError,
    SubscriptionRateLimitError,
    detect_quota_error,
    parse_reset_time,
)

# ---------------------------------------------------------------------------
# Helpers
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
    headers: dict[str, str] | None = None,
    error_type: str | None = None,
    message: str = "error",
) -> MagicMock:
    """Build a synthetic Anthropic-SDK-style exception."""
    exc = MagicMock(spec=Exception)
    exc.status_code = status_code
    exc.message = message
    response = MagicMock()
    response.headers = headers or {}
    exc.response = response
    body: dict[str, Any] = {"error": {"message": message}}
    if error_type is not None:
        body["error"]["type"] = error_type
    exc.body = body
    return exc


def _make_bedrock_exc(error_code: str, message: str = "throttled") -> MagicMock:
    """Build a synthetic botocore ClientError-style exception."""
    exc = MagicMock(spec=Exception)
    exc.response = {
        "Error": {
            "Code": error_code,
            "Message": message,
        }
    }
    exc.status_code = None
    exc.body = {}
    return exc


# ---------------------------------------------------------------------------
# Base class: QuotaExhaustedError
# ---------------------------------------------------------------------------


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

    def test_str_includes_reset_at_when_set(self) -> None:
        exc = _make_exc(QuotaExhaustedError, reset_at=_NOW)
        assert "2026-01-01" in str(exc)

    def test_str_when_reset_at_is_none(self) -> None:
        exc = _make_exc(QuotaExhaustedError, reset_at=None)
        result = str(exc)
        assert "unknown" in result

    def test_is_base_exception_subclass(self) -> None:
        assert issubclass(QuotaExhaustedError, Exception)

    def test_raise_and_catch_as_quota_exhausted(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(QuotaExhaustedError)


# ---------------------------------------------------------------------------
# SubscriptionRateLimitError
# ---------------------------------------------------------------------------


class TestSubscriptionRateLimitError:
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


# ---------------------------------------------------------------------------
# SdkCreditExhaustedError
# ---------------------------------------------------------------------------


class TestSdkCreditExhaustedError:
    def test_inherits_base(self) -> None:
        exc = _make_exc(SdkCreditExhaustedError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(SdkCreditExhaustedError, source="sdk")
        assert exc.source == "sdk"
        assert exc.reset_at == _NOW

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(SdkCreditExhaustedError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(SdkCreditExhaustedError):
            raise _make_exc(SdkCreditExhaustedError)

    def test_not_subscription_rate_limit(self) -> None:
        exc = _make_exc(SdkCreditExhaustedError)
        assert not isinstance(exc, SubscriptionRateLimitError)


# ---------------------------------------------------------------------------
# ApiBillingError
# ---------------------------------------------------------------------------


class TestApiBillingError:
    def test_inherits_base(self) -> None:
        exc = _make_exc(ApiBillingError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(ApiBillingError, source="billing")
        assert exc.source == "billing"

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(ApiBillingError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(ApiBillingError):
            raise _make_exc(ApiBillingError)

    def test_not_sdk_credit_exhausted(self) -> None:
        exc = _make_exc(ApiBillingError)
        assert not isinstance(exc, SdkCreditExhaustedError)

    @pytest.mark.parametrize("raw", [None, 42, {"code": "insufficient_quota"}, b"bytes"])
    def test_raw_error_various_types(self, raw: object) -> None:
        exc = _make_exc(ApiBillingError, raw_error=raw)
        assert exc.raw_error == raw


# ---------------------------------------------------------------------------
# BedrockThrottleError
# ---------------------------------------------------------------------------


class TestBedrockThrottleError:
    def test_inherits_base(self) -> None:
        exc = _make_exc(BedrockThrottleError)
        assert isinstance(exc, QuotaExhaustedError)

    def test_fields_stored(self) -> None:
        exc = _make_exc(BedrockThrottleError, source="bedrock")
        assert exc.source == "bedrock"
        assert exc.reset_at == _NOW

    def test_catchable_as_base(self) -> None:
        with pytest.raises(QuotaExhaustedError):
            raise _make_exc(BedrockThrottleError)

    def test_catchable_as_specific_type(self) -> None:
        with pytest.raises(BedrockThrottleError):
            raise _make_exc(BedrockThrottleError)

    def test_not_subscription_rate_limit(self) -> None:
        exc = _make_exc(BedrockThrottleError)
        assert not isinstance(exc, SubscriptionRateLimitError)

    def test_not_api_billing_error(self) -> None:
        exc = _make_exc(BedrockThrottleError)
        assert not isinstance(exc, ApiBillingError)


# ---------------------------------------------------------------------------
# Cross-type isolation: subclasses do NOT alias each other
# ---------------------------------------------------------------------------


class TestHierarchyIsolation:
    """Verify that the four subclasses are mutually exclusive at runtime."""

    @pytest.mark.parametrize(
        "caught_as,raised_as,should_match",
        [
            (SubscriptionRateLimitError, SubscriptionRateLimitError, True),
            (SubscriptionRateLimitError, SdkCreditExhaustedError, False),
            (SubscriptionRateLimitError, ApiBillingError, False),
            (SubscriptionRateLimitError, BedrockThrottleError, False),
            (SdkCreditExhaustedError, SdkCreditExhaustedError, True),
            (SdkCreditExhaustedError, SubscriptionRateLimitError, False),
            (SdkCreditExhaustedError, ApiBillingError, False),
            (SdkCreditExhaustedError, BedrockThrottleError, False),
            (ApiBillingError, ApiBillingError, True),
            (ApiBillingError, SubscriptionRateLimitError, False),
            (ApiBillingError, SdkCreditExhaustedError, False),
            (ApiBillingError, BedrockThrottleError, False),
            (BedrockThrottleError, BedrockThrottleError, True),
            (BedrockThrottleError, SubscriptionRateLimitError, False),
            (BedrockThrottleError, SdkCreditExhaustedError, False),
            (BedrockThrottleError, ApiBillingError, False),
        ],
    )
    def test_isinstance_isolation(
        self,
        caught_as: type[QuotaExhaustedError],
        raised_as: type[QuotaExhaustedError],
        should_match: bool,
    ) -> None:
        exc = _make_exc(raised_as)
        assert isinstance(exc, caught_as) == should_match

    def test_all_subclasses_catch_as_quota_exhausted(self) -> None:
        for cls in (
            SubscriptionRateLimitError,
            SdkCreditExhaustedError,
            ApiBillingError,
            BedrockThrottleError,
        ):
            exc = _make_exc(cls)
            assert isinstance(exc, QuotaExhaustedError)


# ---------------------------------------------------------------------------
# Field preservation under re-raise
# ---------------------------------------------------------------------------


class TestFieldPreservationUnderReraise:
    """Chaining / re-raising must not lose fields."""

    def test_fields_accessible_after_catch(self) -> None:
        try:
            raise _make_exc(SubscriptionRateLimitError, reset_at=_NOW, raw_error="err", source="src")
        except QuotaExhaustedError as exc:
            assert exc.reset_at == _NOW
            assert exc.raw_error == "err"
            assert exc.source == "src"

    def test_chained_exception_preserves_context(self) -> None:
        original = ValueError("original cause")
        try:
            raise _make_exc(SdkCreditExhaustedError, raw_error=original) from original
        except QuotaExhaustedError as exc:
            assert exc.__cause__ is original
            assert exc.raw_error is original


# ---------------------------------------------------------------------------
# detect_quota_error: HTTP 429 + anthropic-ratelimit-* headers
# ---------------------------------------------------------------------------


class TestDetectQuotaError429SubscriptionRateLimit:
    """AC-193-1: HTTP 429 from Anthropic API -> SubscriptionRateLimitError unconditionally.

    detect_quota_error classifies any HTTP 429 as SubscriptionRateLimitError
    regardless of header content.  Header-based reset-time parsing is deferred
    to parse_reset_time (T3); it is NOT performed here.
    """

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"anthropic-ratelimit-requests-reset": "1735732800"},
            {"anthropic-ratelimit-tokens-reset": "1735732800"},
            {"anthropic-ratelimit-input-tokens-reset": "1735732800"},
            {"retry-after": "60"},
            {"Retry-After": "120"},
            {
                "anthropic-ratelimit-requests-reset": "1735732800",
                "anthropic-ratelimit-tokens-reset": "1735732900",
            },
        ],
        ids=[
            "no-headers",
            "requests-reset",
            "tokens-reset",
            "input-tokens-reset",
            "retry-after-lowercase",
            "retry-after-mixed-case",
            "multiple-ratelimit-headers",
        ],
    )
    def test_429_returns_subscription_rate_limit_regardless_of_headers(self, headers: dict[str, str]) -> None:
        """HTTP 429 always returns SubscriptionRateLimitError; headers are ignored."""
        exc = _make_sdk_exc(status_code=429, headers=headers)
        result = detect_quota_error(exc)
        assert isinstance(result, SubscriptionRateLimitError)

    def test_429_result_is_quota_exhausted_base(self) -> None:
        exc = _make_sdk_exc(status_code=429)
        result = detect_quota_error(exc)
        assert isinstance(result, QuotaExhaustedError)

    def test_429_result_raw_error_is_original(self) -> None:
        exc = _make_sdk_exc(status_code=429)
        result = detect_quota_error(exc)
        assert result is not None
        assert result.raw_error is exc

    def test_429_result_source_is_anthropic_api(self) -> None:
        exc = _make_sdk_exc(status_code=429)
        result = detect_quota_error(exc)
        assert result is not None
        assert result.source == "anthropic-api"

    def test_429_result_reset_at_is_none(self) -> None:
        """detect_quota_error sets reset_at=None; parse_reset_time handles header parsing."""
        exc = _make_sdk_exc(status_code=429)
        result = detect_quota_error(exc)
        assert result is not None
        assert result.reset_at is None


# ---------------------------------------------------------------------------
# detect_quota_error: HTTP 402 / insufficient_quota
# ---------------------------------------------------------------------------


class TestDetectQuotaError402:
    """AC-193-2: HTTP 402 / insufficient_quota -> SdkCreditExhaustedError or ApiBillingError."""

    def test_402_insufficient_quota_type_is_sdk_credit(self) -> None:
        exc = _make_sdk_exc(
            status_code=402,
            error_type="insufficient_quota",
        )
        result = detect_quota_error(exc)
        assert isinstance(result, SdkCreditExhaustedError)

    def test_402_no_error_type_is_api_billing(self) -> None:
        exc = _make_sdk_exc(status_code=402)
        result = detect_quota_error(exc)
        assert isinstance(result, ApiBillingError)

    def test_402_billing_error_type_is_api_billing(self) -> None:
        exc = _make_sdk_exc(
            status_code=402,
            error_type="billing_error",
        )
        result = detect_quota_error(exc)
        assert isinstance(result, ApiBillingError)

    def test_402_sdk_credit_is_quota_exhausted(self) -> None:
        exc = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(exc)
        assert isinstance(result, QuotaExhaustedError)

    def test_402_api_billing_is_quota_exhausted(self) -> None:
        exc = _make_sdk_exc(status_code=402)
        result = detect_quota_error(exc)
        assert isinstance(result, QuotaExhaustedError)

    def test_402_raw_error_is_original(self) -> None:
        exc = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(exc)
        assert result is not None
        assert result.raw_error is exc

    def test_402_sdk_credit_source_is_sdk(self) -> None:
        exc = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(exc)
        assert result is not None
        assert result.source == "sdk"

    def test_402_api_billing_source_is_anthropic_api(self) -> None:
        exc = _make_sdk_exc(status_code=402)
        result = detect_quota_error(exc)
        assert result is not None
        assert result.source == "anthropic-api"

    def test_402_sdk_not_api_billing(self) -> None:
        exc = _make_sdk_exc(status_code=402, error_type="insufficient_quota")
        result = detect_quota_error(exc)
        assert not isinstance(result, ApiBillingError)

    def test_402_api_billing_not_sdk_credit(self) -> None:
        exc = _make_sdk_exc(status_code=402)
        result = detect_quota_error(exc)
        assert not isinstance(result, SdkCreditExhaustedError)


# ---------------------------------------------------------------------------
# detect_quota_error: Bedrock throttle errors
# ---------------------------------------------------------------------------


class TestDetectQuotaErrorBedrock:
    """AC-193-3: Bedrock ThrottlingException / ServiceQuotaExceededException -> BedrockThrottleError."""

    def test_bedrock_throttling_exception(self) -> None:
        exc = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(exc)
        assert isinstance(result, BedrockThrottleError)

    def test_bedrock_service_quota_exceeded(self) -> None:
        exc = _make_bedrock_exc("ServiceQuotaExceededException")
        result = detect_quota_error(exc)
        assert isinstance(result, BedrockThrottleError)

    def test_bedrock_throttle_is_quota_exhausted(self) -> None:
        exc = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(exc)
        assert isinstance(result, QuotaExhaustedError)

    def test_bedrock_raw_error_is_original(self) -> None:
        exc = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(exc)
        assert result is not None
        assert result.raw_error is exc

    def test_bedrock_source_is_bedrock(self) -> None:
        exc = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(exc)
        assert result is not None
        assert result.source == "bedrock"

    def test_bedrock_reset_at_is_none(self) -> None:
        """Bedrock throttle errors carry no reset timestamp by default."""
        exc = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(exc)
        assert result is not None
        assert result.reset_at is None

    def test_bedrock_not_subscription_rate_limit(self) -> None:
        exc = _make_bedrock_exc("ThrottlingException")
        result = detect_quota_error(exc)
        assert not isinstance(result, SubscriptionRateLimitError)

    @pytest.mark.parametrize(
        "error_code",
        ["ThrottlingException", "ServiceQuotaExceededException"],
    )
    def test_bedrock_parametrized_error_codes(self, error_code: str) -> None:
        exc = _make_bedrock_exc(error_code)
        result = detect_quota_error(exc)
        assert isinstance(result, BedrockThrottleError)


# ---------------------------------------------------------------------------
# detect_quota_error: non-quota inputs return None
# ---------------------------------------------------------------------------


class TestDetectQuotaErrorReturnsNone:
    """Non-quota inputs must return None, not raise."""

    def test_200_status_returns_none(self) -> None:
        exc = _make_sdk_exc(status_code=200)
        result = detect_quota_error(exc)
        assert result is None

    def test_400_status_returns_none(self) -> None:
        exc = _make_sdk_exc(status_code=400)
        result = detect_quota_error(exc)
        assert result is None

    def test_500_status_returns_none(self) -> None:
        exc = _make_sdk_exc(status_code=500)
        result = detect_quota_error(exc)
        assert result is None

    def test_plain_exception_returns_none(self) -> None:
        result = detect_quota_error(ValueError("not a quota error"))
        assert result is None

    def test_string_returns_none(self) -> None:
        result = detect_quota_error("some random string")
        assert result is None

    def test_none_input_returns_none(self) -> None:
        result = detect_quota_error(None)
        assert result is None

    def test_dict_without_quota_returns_none(self) -> None:
        result = detect_quota_error({"status": 200, "body": "ok"})
        assert result is None

    def test_bedrock_other_error_code_returns_none(self) -> None:
        exc = _make_bedrock_exc("ValidationException")
        result = detect_quota_error(exc)
        assert result is None

    def test_401_unauthorized_returns_none(self) -> None:
        exc = _make_sdk_exc(status_code=401)
        result = detect_quota_error(exc)
        assert result is None

    def test_object_without_status_code_returns_none(self) -> None:
        """Objects that lack status_code and are not Bedrock-shaped return None."""
        obj = object()
        result = detect_quota_error(obj)
        assert result is None


# ---------------------------------------------------------------------------
# detect_quota_error: already-classified QuotaExhaustedError passthrough
# ---------------------------------------------------------------------------


class TestDetectQuotaErrorPassthrough:
    """A QuotaExhaustedError passed directly is returned as-is."""

    def test_quota_exhausted_error_returned_directly(self) -> None:
        quota_exc = _make_exc(SubscriptionRateLimitError)
        result = detect_quota_error(quota_exc)
        assert result is quota_exc

    def test_bedrock_throttle_passthrough(self) -> None:
        quota_exc = _make_exc(BedrockThrottleError)
        result = detect_quota_error(quota_exc)
        assert result is quota_exc

    def test_sdk_credit_passthrough(self) -> None:
        quota_exc = _make_exc(SdkCreditExhaustedError)
        result = detect_quota_error(quota_exc)
        assert result is quota_exc


# ---------------------------------------------------------------------------
# detect_quota_error: edge cases in helper branches
# ---------------------------------------------------------------------------


class TestDetectQuotaErrorEdgeCases:
    """Cover edge cases in private helper branches for 100% line coverage."""

    def test_402_body_not_dict_is_api_billing(self) -> None:
        """_get_error_type returns None when body is not a dict -> ApiBillingError."""
        exc = MagicMock(spec=Exception)
        exc.status_code = 402
        exc.body = "not-a-dict"
        result = detect_quota_error(exc)
        assert isinstance(result, ApiBillingError)

    def test_402_body_error_not_dict_is_api_billing(self) -> None:
        """_get_error_type returns None when body['error'] is not a dict -> ApiBillingError."""
        exc = MagicMock(spec=Exception)
        exc.status_code = 402
        exc.body = {"error": "string-not-dict"}
        result = detect_quota_error(exc)
        assert isinstance(result, ApiBillingError)

    def test_bedrock_response_error_section_not_dict_returns_none(self) -> None:
        """_get_bedrock_error_code returns None when response['Error'] is not a dict."""
        exc = MagicMock(spec=Exception)
        exc.status_code = None
        exc.body = {}
        exc.response = {"Error": "string-not-dict"}
        result = detect_quota_error(exc)
        assert result is None


# ---------------------------------------------------------------------------
# Integration tests: detect_quota_error with real (non-mock) exception objects
# ---------------------------------------------------------------------------


class _SdkLikeError(Exception):
    """Minimal real exception class that mimics the Anthropic SDK exception shape.

    Used for integration tests to exercise detect_quota_error with genuine
    Python objects rather than MagicMock proxies.
    """

    def __init__(
        self,
        status_code: int,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body or {}
        super().__init__(f"HTTP {status_code}")


class _BedrockLikeError(Exception):
    """Minimal real exception class that mimics a botocore ClientError shape.

    Used for integration tests to exercise detect_quota_error with genuine
    Python objects rather than MagicMock proxies.
    """

    def __init__(self, error_code: str, message: str = "throttled") -> None:
        self.response = {"Error": {"Code": error_code, "Message": message}}
        self.status_code: int | None = None
        self.body: dict[str, Any] = {}
        super().__init__(message)


class TestDetectQuotaErrorIntegration:
    """Integration tests: detect_quota_error end-to-end with real exception objects.

    These tests construct genuine Python exception instances (not MagicMock) and
    pass them through detect_quota_error to verify the full detection path.
    """

    def test_real_429_exception_returns_subscription_rate_limit(self) -> None:
        """A real SdkLikeError with status_code=429 produces SubscriptionRateLimitError."""
        exc = _SdkLikeError(status_code=429)
        result = detect_quota_error(exc)
        assert isinstance(result, SubscriptionRateLimitError)
        assert result.raw_error is exc
        assert result.source == "anthropic-api"
        assert result.reset_at is None

    def test_real_402_insufficient_quota_returns_sdk_credit(self) -> None:
        """A real SdkLikeError with 402 + insufficient_quota body -> SdkCreditExhaustedError."""
        exc = _SdkLikeError(
            status_code=402,
            body={"error": {"type": "insufficient_quota", "message": "credits depleted"}},
        )
        result = detect_quota_error(exc)
        assert isinstance(result, SdkCreditExhaustedError)
        assert result.raw_error is exc
        assert result.source == "sdk"
        assert result.reset_at is None

    def test_real_402_generic_returns_api_billing_error(self) -> None:
        """A real SdkLikeError with 402 and no error type -> ApiBillingError."""
        exc = _SdkLikeError(
            status_code=402,
            body={"error": {"message": "billing issue"}},
        )
        result = detect_quota_error(exc)
        assert isinstance(result, ApiBillingError)
        assert result.raw_error is exc
        assert result.source == "anthropic-api"
        assert result.reset_at is None

    def test_real_bedrock_throttling_returns_bedrock_throttle(self) -> None:
        """A real BedrockLikeError with ThrottlingException -> BedrockThrottleError."""
        exc = _BedrockLikeError(error_code="ThrottlingException")
        result = detect_quota_error(exc)
        assert isinstance(result, BedrockThrottleError)
        assert result.raw_error is exc
        assert result.source == "bedrock"
        assert result.reset_at is None

    def test_real_bedrock_service_quota_returns_bedrock_throttle(self) -> None:
        """A real BedrockLikeError with ServiceQuotaExceededException -> BedrockThrottleError."""
        exc = _BedrockLikeError(error_code="ServiceQuotaExceededException")
        result = detect_quota_error(exc)
        assert isinstance(result, BedrockThrottleError)
        assert result.raw_error is exc
        assert result.source == "bedrock"

    def test_real_non_quota_exception_returns_none(self) -> None:
        """A real exception with a non-quota status code returns None."""
        exc = _SdkLikeError(status_code=500)
        result = detect_quota_error(exc)
        assert result is None

    def test_real_quota_exhausted_passthrough(self) -> None:
        """A real QuotaExhaustedError passed directly is returned unchanged."""
        original = SubscriptionRateLimitError(
            reset_at=None,
            raw_error=_SdkLikeError(status_code=429),
            source="anthropic-api",
        )
        result = detect_quota_error(original)
        assert result is original

    @pytest.mark.parametrize(
        "error_code,expected_cls",
        [
            ("ThrottlingException", BedrockThrottleError),
            ("ServiceQuotaExceededException", BedrockThrottleError),
        ],
    )
    def test_real_bedrock_parametrized(self, error_code: str, expected_cls: type[QuotaExhaustedError]) -> None:
        """Both Bedrock error codes route to BedrockThrottleError via real objects."""
        exc = _BedrockLikeError(error_code=error_code)
        result = detect_quota_error(exc)
        assert isinstance(result, expected_cls)
        assert isinstance(result, QuotaExhaustedError)


# ---------------------------------------------------------------------------
# parse_reset_time: Retry-After integer seconds
# ---------------------------------------------------------------------------


class TestParseResetTimeRetryAfterSeconds:
    """parse_reset_time with Retry-After as an integer (seconds from now)."""

    def test_retry_after_integer_returns_future_datetime(self) -> None:
        """Retry-After: 60 should return a datetime ~60 seconds in the future."""
        before = datetime.now(tz=UTC)
        result = parse_reset_time({"Retry-After": "60"})
        after = datetime.now(tz=UTC)
        assert result is not None
        assert result.tzinfo == UTC
        # Result should be between before+60 and after+60 (with small tolerance)
        assert result >= before + timedelta(seconds=59)
        assert result <= after + timedelta(seconds=61)

    def test_retry_after_zero_returns_datetime_near_now(self) -> None:
        """Retry-After: 0 should return a datetime at approximately now."""
        before = datetime.now(tz=UTC)
        result = parse_reset_time({"Retry-After": "0"})
        after = datetime.now(tz=UTC)
        assert result is not None
        assert result >= before - timedelta(seconds=1)
        assert result <= after + timedelta(seconds=1)

    def test_retry_after_lowercase_key_returns_datetime(self) -> None:
        """Retry-after (lowercase) should also be recognized."""
        result = parse_reset_time({"retry-after": "30"})
        assert result is not None
        assert result.tzinfo == UTC

    def test_retry_after_large_value_returns_datetime(self) -> None:
        """Retry-After: 3600 should return a datetime ~1 hour in the future."""
        before = datetime.now(tz=UTC)
        result = parse_reset_time({"Retry-After": "3600"})
        assert result is not None
        assert result >= before + timedelta(seconds=3599)

    @pytest.mark.parametrize("key", ["Retry-After", "retry-after", "RETRY-AFTER", "Retry-after"])
    def test_retry_after_case_insensitive_key(self, key: str) -> None:
        """Retry-After header key lookup is case-insensitive."""
        result = parse_reset_time({key: "120"})
        assert result is not None
        assert result.tzinfo == UTC


# ---------------------------------------------------------------------------
# parse_reset_time: Retry-After HTTP-date
# ---------------------------------------------------------------------------


class TestParseResetTimeRetryAfterHttpDate:
    """parse_reset_time with Retry-After as an HTTP-date string."""

    def test_retry_after_rfc1123_date_returns_correct_datetime(self) -> None:
        """Retry-After: Thu, 01 Jan 2026 12:00:00 GMT -> datetime(2026,1,1,12,0,0,UTC)."""
        result = parse_reset_time({"Retry-After": "Thu, 01 Jan 2026 12:00:00 GMT"})
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_retry_after_http_date_is_utc(self) -> None:
        """HTTP-date parsed from Retry-After has UTC timezone."""
        result = parse_reset_time({"Retry-After": "Mon, 15 Jun 2026 08:30:00 GMT"})
        assert result is not None
        assert result.tzinfo is not None
        # UTC offset should be zero
        assert result.utcoffset() == timedelta(0)

    def test_retry_after_http_date_with_various_months(self) -> None:
        """HTTP-date month names are parsed correctly."""
        result = parse_reset_time({"Retry-After": "Fri, 01 May 2026 00:00:00 GMT"})
        assert result is not None
        assert result.month == 5
        assert result.day == 1
        assert result.year == 2026

    def test_retry_after_invalid_date_falls_through_to_none(self) -> None:
        """An unparseable Retry-After value with no other headers returns None."""
        result = parse_reset_time({"Retry-After": "not-a-valid-date"})
        assert result is None

    def test_retry_after_negative_integer_returns_none(self) -> None:
        """Retry-After: -10 is invalid and should return None."""
        result = parse_reset_time({"Retry-After": "-10"})
        assert result is None


# ---------------------------------------------------------------------------
# parse_reset_time: anthropic-ratelimit-*-reset headers (epoch seconds)
# ---------------------------------------------------------------------------


class TestParseResetTimeAnthropicRateLimitReset:
    """parse_reset_time with anthropic-ratelimit-*-reset epoch-second headers."""

    def test_requests_reset_epoch_returns_utc_datetime(self) -> None:
        """anthropic-ratelimit-requests-reset with epoch seconds returns correct UTC datetime."""
        # 1767268800 == 2026-01-01T12:00:00Z
        result = parse_reset_time({"anthropic-ratelimit-requests-reset": "1767268800"})
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_tokens_reset_epoch_returns_utc_datetime(self) -> None:
        """anthropic-ratelimit-tokens-reset with epoch seconds returns correct UTC datetime."""
        result = parse_reset_time({"anthropic-ratelimit-tokens-reset": "1767268800"})
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_input_tokens_reset_epoch_returns_utc_datetime(self) -> None:
        """anthropic-ratelimit-input-tokens-reset with epoch seconds returns correct UTC datetime."""
        result = parse_reset_time({"anthropic-ratelimit-input-tokens-reset": "1767268800"})
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_multiple_ratelimit_headers_returns_earliest(self) -> None:
        """When multiple anthropic-ratelimit-*-reset headers present, earliest is returned."""
        # Earlier: 1767268800 (2026-01-01T12:00:00Z)
        # Later: 1767268900 (2026-01-01T12:01:40Z)
        result = parse_reset_time(
            {
                "anthropic-ratelimit-requests-reset": "1767268900",
                "anthropic-ratelimit-tokens-reset": "1767268800",
            }
        )
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_ratelimit_reset_result_is_utc(self) -> None:
        """Reset datetime from anthropic-ratelimit-*-reset is always UTC."""
        result = parse_reset_time({"anthropic-ratelimit-requests-reset": "1735732800"})
        assert result is not None
        assert result.tzinfo == UTC

    def test_ratelimit_reset_invalid_value_falls_through_to_none(self) -> None:
        """Non-numeric anthropic-ratelimit-*-reset value returns None (no other parseable headers)."""
        result = parse_reset_time({"anthropic-ratelimit-requests-reset": "not-a-number"})
        assert result is None

    def test_ratelimit_reset_negative_epoch_returns_none(self) -> None:
        """anthropic-ratelimit-*-reset with a negative epoch (pre-1970) returns None.

        Negative epoch timestamps are nonsensical quota-reset values and must be
        rejected so callers never receive a pre-1970 datetime as a reset signal.
        """
        result = parse_reset_time({"anthropic-ratelimit-requests-reset": "-1"})
        assert result is None

    @pytest.mark.parametrize(
        "header_name",
        [
            "anthropic-ratelimit-requests-reset",
            "anthropic-ratelimit-tokens-reset",
            "anthropic-ratelimit-input-tokens-reset",
        ],
    )
    def test_each_ratelimit_header_individually(self, header_name: str) -> None:
        """Each individual anthropic-ratelimit-*-reset header is recognized."""
        # 1767268800 == 2026-01-01T12:00:00Z
        result = parse_reset_time({header_name: "1767268800"})
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# parse_reset_time: empty / missing headers
# ---------------------------------------------------------------------------


class TestParseResetTimeNoHeaders:
    """parse_reset_time with no parseable headers returns None."""

    def test_empty_headers_returns_none(self) -> None:
        result = parse_reset_time({})
        assert result is None

    def test_unrelated_headers_returns_none(self) -> None:
        result = parse_reset_time({"Content-Type": "application/json", "X-Request-Id": "abc123"})
        assert result is None

    def test_none_equivalent_dict_returns_none(self) -> None:
        """A dict with no quota-related keys returns None."""
        result = parse_reset_time({"Authorization": "Bearer token"})
        assert result is None


# ---------------------------------------------------------------------------
# parse_reset_time: priority -- ratelimit-reset vs Retry-After
# ---------------------------------------------------------------------------


class TestParseResetTimePriority:
    """When both anthropic-ratelimit-*-reset and Retry-After are present, the
    earliest datetime wins (so the caller waits only as long as needed)."""

    def test_ratelimit_earlier_than_retry_after_returns_ratelimit(self) -> None:
        """When ratelimit-reset is earlier than Retry-After, return the ratelimit time."""
        # ratelimit-reset: 1767268800 (2026-01-01T12:00:00Z) -- earlier than Retry-After of 3600s from now
        # Retry-After: 3600 seconds from now -- well past 2026-01-01T12:00:00Z while this epoch remains in the past.
        result = parse_reset_time(
            {
                "anthropic-ratelimit-requests-reset": "1767268800",
                "Retry-After": "3600",
            }
        )
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_retry_after_date_combined_with_ratelimit_reset(self) -> None:
        """HTTP-date Retry-After and ratelimit-reset: the earlier datetime is returned."""
        # Both refer to same time; result is that datetime
        result = parse_reset_time(
            {
                "anthropic-ratelimit-tokens-reset": "1767268800",
                "Retry-After": "Thu, 01 Jan 2026 12:00:00 GMT",
            }
        )
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_all_three_headers_present_returns_earliest(self) -> None:
        """All three header types present: earliest datetime is returned."""
        # requests-reset: 1767268900 (2026-01-01T12:01:40Z -- later)
        # tokens-reset: 1767268800 (2026-01-01T12:00:00Z -- earliest)
        # Retry-After HTTP-date: 2026-01-01T12:30:00Z (middle)
        result = parse_reset_time(
            {
                "anthropic-ratelimit-requests-reset": "1767268900",
                "anthropic-ratelimit-tokens-reset": "1767268800",
                "Retry-After": "Thu, 01 Jan 2026 12:30:00 GMT",
            }
        )
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# parse_reset_time: integration tests (real header dicts, no mocks)
# ---------------------------------------------------------------------------


class TestParseResetTimeIntegration:
    """Integration tests: parse_reset_time end-to-end with real header dicts.

    These tests construct real header dictionaries (as would be received from
    an Anthropic API response) and verify the full parsing path.
    """

    def test_real_anthropic_429_headers_with_requests_reset(self) -> None:
        """A realistic Anthropic 429 response header set with requests-reset."""
        headers = {
            "content-type": "application/json",
            "request-id": "req_abc123",
            "anthropic-ratelimit-requests-limit": "50",
            "anthropic-ratelimit-requests-remaining": "0",
            "anthropic-ratelimit-requests-reset": "1767268800",
            "anthropic-ratelimit-tokens-limit": "40000",
            "anthropic-ratelimit-tokens-remaining": "0",
            "anthropic-ratelimit-tokens-reset": "1767268900",
        }
        result = parse_reset_time(headers)
        assert result is not None
        # requests-reset (1767268800) is earlier than tokens-reset (1767268900)
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_real_retry_after_seconds_only_headers(self) -> None:
        """Headers with only Retry-After seconds (no ratelimit-reset headers)."""
        headers = {
            "content-type": "application/json",
            "retry-after": "120",
            "x-request-id": "req_xyz789",
        }
        before = datetime.now(tz=UTC)
        result = parse_reset_time(headers)
        after = datetime.now(tz=UTC)
        assert result is not None
        assert result >= before + timedelta(seconds=119)
        assert result <= after + timedelta(seconds=121)

    def test_real_retry_after_http_date_headers(self) -> None:
        """Headers with Retry-After as an HTTP-date string (as per RFC 7231)."""
        headers = {
            "Retry-After": "Wed, 01 Jan 2026 00:00:00 GMT",
        }
        result = parse_reset_time(headers)
        assert result is not None
        assert result == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    def test_real_no_reset_headers_returns_none(self) -> None:
        """Headers with no reset time indicators return None."""
        headers = {
            "content-type": "application/json",
            "x-request-id": "req_abc123",
            "anthropic-ratelimit-requests-limit": "50",
            "anthropic-ratelimit-requests-remaining": "5",
        }
        result = parse_reset_time(headers)
        assert result is None

    def test_real_mixed_valid_and_invalid_reset_headers(self) -> None:
        """When one header is invalid and another is valid, the valid one is used."""
        headers = {
            "anthropic-ratelimit-requests-reset": "not-a-number",
            "anthropic-ratelimit-tokens-reset": "1767268800",
        }
        result = parse_reset_time(headers)
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_parse_reset_time_returns_utc_aware_datetime(self) -> None:
        """All paths through parse_reset_time return timezone-aware UTC datetimes."""
        # epoch seconds path
        result = parse_reset_time({"anthropic-ratelimit-tokens-reset": "1735732800"})
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_parse_reset_time_with_combined_http_date_and_epoch(self) -> None:
        """HTTP-date Retry-After and epoch ratelimit-reset: consistent UTC comparison."""
        # Retry-After HTTP-date: 2026-01-01T14:00:00Z
        # ratelimit-reset epoch: 1767268800 = 2026-01-01T12:00:00Z (earlier)
        headers = {
            "Retry-After": "Thu, 01 Jan 2026 14:00:00 GMT",
            "anthropic-ratelimit-requests-reset": "1767268800",
        }
        result = parse_reset_time(headers)
        assert result is not None
        assert result == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
