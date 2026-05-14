"""Tests for devbench.quota -- exception hierarchy and detect_quota_error.

Covers: QuotaExhaustedError, SubscriptionRateLimitError, SdkCreditExhaustedError,
ApiBillingError, BedrockThrottleError.  Each exception carries reset_at,
raw_error and source fields.

Also covers: detect_quota_error(message_or_exception) which classifies
incoming SDK exceptions / response objects into the structured quota hierarchy.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
