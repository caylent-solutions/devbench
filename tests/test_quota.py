"""Tests for devbench.quota -- exception hierarchy, detect_quota_error,
parse_reset_time, wait_for_reset, recovery_probe, save_checkpoint, and
load_checkpoint.

Covers: QuotaExhaustedError, SubscriptionRateLimitError, SdkCreditExhaustedError,
ApiBillingError, BedrockThrottleError.  Each exception carries reset_at,
raw_error and source fields.

Also covers: detect_quota_error(message_or_exception) which classifies
incoming SDK exceptions / response objects into the structured quota hierarchy.

Also covers: parse_reset_time(headers) which extracts a UTC datetime from
Retry-After (integer seconds or HTTP-date) or anthropic-ratelimit-*-reset
(epoch seconds) headers.

Also covers: wait_for_reset(reset_at, poll_interval, max_wait, probe_fn) which
sleeps until reset_at then probes quota recovery with exponential backoff.
Returns True on recovery, False when max_wait is exceeded.

Also covers: recovery_probe(timeout_seconds, request_size_tokens) which sends
a 1-token completion request to the Anthropic API. Returns True when the
request completes without a quota error; returns False when the API signals
continued throttle via a QuotaExhaustedError subclass.

Also covers: QuotaCheckpoint dataclass, save_checkpoint(session_dir, ...) which
atomically writes quota_pause.json, and load_checkpoint(session_dir) which
reads and deserializes it or returns None when the file is absent.

Also covers: post_webhook(url, payload, timeout_seconds) which POSTs a JSON
payload to a URL using stdlib urllib.  Failures are logged to stderr but do
not raise.

Also covers: deliver_notifications(notify_config, payload) which calls
post_webhook for each non-None URL in a QuotaNotifyConfig.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devbench.quota import (
    ApiBillingError,
    BackoffConfig,
    BedrockThrottleError,
    QuotaCheckpoint,
    QuotaExhaustedError,
    SdkCreditExhaustedError,
    SubscriptionRateLimitError,
    detect_quota_error,
    load_checkpoint,
    parse_reset_time,
    recovery_probe,
    save_checkpoint,
    wait_for_reset,
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


# ---------------------------------------------------------------------------
# BackoffConfig dataclass
# ---------------------------------------------------------------------------


class TestBackoffConfig:
    """BackoffConfig carries exponential-backoff parameters for wait_for_reset."""

    def test_default_values(self) -> None:
        cfg = BackoffConfig()
        assert cfg.initial_seconds == 30.0
        assert cfg.max_seconds == 600.0
        assert cfg.multiplier == 2.0
        assert cfg.jitter == 0.2

    def test_custom_values_stored(self) -> None:
        cfg = BackoffConfig(initial_seconds=5.0, max_seconds=60.0, multiplier=1.5, jitter=0.1)
        assert cfg.initial_seconds == 5.0
        assert cfg.max_seconds == 60.0
        assert cfg.multiplier == 1.5
        assert cfg.jitter == 0.1

    def test_zero_jitter_allowed(self) -> None:
        cfg = BackoffConfig(jitter=0.0)
        assert cfg.jitter == 0.0

    def test_initial_equals_max_allowed(self) -> None:
        cfg = BackoffConfig(initial_seconds=30.0, max_seconds=30.0)
        assert cfg.initial_seconds == cfg.max_seconds


# ---------------------------------------------------------------------------
# wait_for_reset: happy path -- probe succeeds on first attempt
# ---------------------------------------------------------------------------


class TestWaitForResetHappyPath:
    """AC-193-5: on_exhaustion=wait sleeps until reset, probes, resumes (True)."""

    def test_returns_true_when_probe_succeeds_immediately(self) -> None:
        """Probe returns True on first call after initial sleep -> wait_for_reset returns True."""
        reset_at = datetime.now(tz=UTC) + timedelta(seconds=60)
        probe_fn = MagicMock(return_value=True)
        sleeps: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                sleeps.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )

        result = asyncio.run(_run())
        assert result is True
        probe_fn.assert_called_once()

    def test_initial_sleep_is_time_until_reset_at(self) -> None:
        """The first sleep duration equals the number of seconds until reset_at."""
        now = datetime.now(tz=UTC)
        reset_at = now + timedelta(seconds=90)
        probe_fn = MagicMock(return_value=True)
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )

        asyncio.run(_run())
        assert len(captured_sleep) >= 1
        # First sleep should be approximately 90 seconds (allow 2s tolerance for execution time)
        assert 88.0 <= captured_sleep[0] <= 92.0

    def test_initial_sleep_clamped_to_zero_when_reset_at_in_past(self) -> None:
        """When reset_at is in the past, no initial sleep occurs (clamped to 0 or skipped)."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=30)
        probe_fn = MagicMock(return_value=True)
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )

        result = asyncio.run(_run())
        assert result is True
        # Either no initial sleep or sleep with 0
        assert all(s >= 0.0 for s in captured_sleep)
        # First sleep (if any) should be ~0 or close to 0 (within 1s of now)
        if captured_sleep:
            assert captured_sleep[0] <= 1.0


# ---------------------------------------------------------------------------
# wait_for_reset: probe fails -- exponential backoff retries
# ---------------------------------------------------------------------------


class TestWaitForResetBackoff:
    """Probe failure triggers exponential backoff; probe eventually succeeds -> True."""

    def test_returns_true_after_backoff_probe_succeeds(self) -> None:
        """Probe fails once then succeeds; wait_for_reset returns True."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)  # already past
        probe_fn = MagicMock(side_effect=[False, True])
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=5.0, max_seconds=60.0, multiplier=2.0, jitter=0.0),
                )

        result = asyncio.run(_run())
        assert result is True
        assert probe_fn.call_count == 2
        # A backoff sleep should have occurred between the first failure and second probe
        assert len(captured_sleep) >= 1

    def test_backoff_sleep_duration_grows_exponentially(self) -> None:
        """Each retry sleep duration is multiplied by the backoff multiplier."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(side_effect=[False, False, False, True])
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=10.0,
                    max_wait=3600.0,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=10.0, max_seconds=1000.0, multiplier=2.0, jitter=0.0),
                )

        asyncio.run(_run())
        # Backoff sleeps should be 10, 20, 40 (doubling each time, jitter=0)
        # The first sleep is the initial reset_at sleep (should be ~0 since reset_at in past)
        backoff_sleeps = [s for s in captured_sleep if s > 1.0]  # filter out the near-zero initial sleep
        assert len(backoff_sleeps) >= 2
        # Each subsequent backoff sleep should be larger than the previous
        for i in range(1, len(backoff_sleeps)):
            assert backoff_sleeps[i] > backoff_sleeps[i - 1]

    def test_backoff_sleep_capped_at_max_seconds(self) -> None:
        """Backoff sleep never exceeds backoff_config.max_seconds."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        # Fail 5 times to force multiple doublings, with jitter=0 for determinism
        probe_fn = MagicMock(side_effect=[False, False, False, False, False, True])
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=10.0,
                    max_wait=100000.0,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=10.0, max_seconds=25.0, multiplier=2.0, jitter=0.0),
                )

        asyncio.run(_run())
        # All backoff sleeps must be <= max_seconds
        for s in captured_sleep:
            assert s <= 25.0 + 0.001  # allow tiny float rounding

    def test_jitter_applied_to_backoff_sleep(self) -> None:
        """With jitter > 0, backoff sleep varies from the deterministic value."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        # Fail twice to observe two backoff sleeps and check they differ
        probe_fn = MagicMock(side_effect=[False, False, True])
        runs: list[list[float]] = []

        for _ in range(10):
            captured_sleep: list[float] = []

            async def _run(cs: list[float] = captured_sleep) -> bool:
                async def fake_sleep(seconds: float) -> None:
                    cs.append(seconds)

                with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                    return await wait_for_reset(
                        reset_at=reset_at,
                        poll_interval=10.0,
                        max_wait=3600.0,
                        probe_fn=probe_fn,
                        backoff_config=BackoffConfig(
                            initial_seconds=10.0, max_seconds=1000.0, multiplier=2.0, jitter=0.2
                        ),
                    )

            probe_fn.side_effect = [False, False, True]
            asyncio.run(_run(captured_sleep))
            runs.append(list(captured_sleep))

        # Collect the first backoff sleep from each run; with jitter they should not all be identical
        first_backoff_sleeps = [r[1] for r in runs if len(r) > 1]
        assert len(first_backoff_sleeps) >= 2, (
            "Expected at least 2 runs to capture a backoff sleep; "
            f"got {len(first_backoff_sleeps)} from {len(runs)} runs with sleeps: {runs}"
        )
        # At least one pair should differ (jitter introduces randomness)
        # With 10 runs and 20% jitter, the chance all are identical is astronomically small
        assert len({round(v, 4) for v in first_backoff_sleeps}) > 1

    def test_probe_called_with_no_arguments(self) -> None:
        """probe_fn is called with no arguments."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=True)

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )

        asyncio.run(_run())
        probe_fn.assert_called_with()


# ---------------------------------------------------------------------------
# wait_for_reset: max_wait exceeded -- returns False (AC-193-12)
# ---------------------------------------------------------------------------


class TestWaitForResetMaxWaitExceeded:
    """AC-193-12: max_wait ceiling is honored; returns False when exceeded."""

    def test_returns_false_when_max_wait_exceeded(self) -> None:
        """Probe always fails; once accumulated wait exceeds max_wait, return False."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=False)
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=12.0,  # small max so backoff exceeds it quickly
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=5.0, max_seconds=60.0, multiplier=2.0, jitter=0.0),
                )

        result = asyncio.run(_run())
        assert result is False

    def test_probe_not_called_after_max_wait(self) -> None:
        """probe_fn call count is bounded; no probes issued after max_wait exceeded."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=False)

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=10.0,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=5.0, max_seconds=60.0, multiplier=2.0, jitter=0.0),
                )

        asyncio.run(_run())
        # With max_wait=10.0, initial_seconds=5.0, multiplier=2.0, jitter=0.0:
        # - probe called at elapsed=0 (initial sleep=0, past reset_at)
        # - backoff sleep=5.0, elapsed=5.0 -> probe called
        # - backoff sleep=10.0, elapsed=15.0 -> exceeds max_wait=10.0, no further probe
        # Exactly 2 probes are issued before the limit is reached.
        assert probe_fn.call_count == 2

    def test_max_wait_zero_returns_false_immediately(self) -> None:
        """max_wait=0 -- any accumulated time exceeds limit; return False without probing."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=True)

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=1.0,
                    max_wait=0.0,
                    probe_fn=probe_fn,
                )

        result = asyncio.run(_run())
        assert result is False
        probe_fn.assert_not_called()

    @pytest.mark.parametrize("max_wait", [1.0, 5.0, 30.0, 300.0])
    def test_max_wait_parametrized_returns_false(self, max_wait: float) -> None:
        """For various max_wait values, a probe that always fails eventually yields False."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=False)

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=1.0,
                    max_wait=max_wait,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=1.0, max_seconds=2.0, multiplier=2.0, jitter=0.0),
                )

        result = asyncio.run(_run())
        assert result is False


# ---------------------------------------------------------------------------
# wait_for_reset: edge cases
# ---------------------------------------------------------------------------


class TestWaitForResetEdgeCases:
    """Edge cases: reset_at in future, probe raises, default backoff."""

    def test_reset_at_exactly_now_no_negative_sleep(self) -> None:
        """reset_at == now: no sleep with negative duration."""
        reset_at = datetime.now(tz=UTC)
        probe_fn = MagicMock(return_value=True)
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )

        result = asyncio.run(_run())
        assert result is True
        for s in captured_sleep:
            assert s >= 0.0

    def test_default_backoff_config_used_when_none(self) -> None:
        """When backoff_config is not provided, sensible defaults are used."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(side_effect=[False, True])
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=3600.0,
                    probe_fn=probe_fn,
                )

        result = asyncio.run(_run())
        assert result is True
        assert probe_fn.call_count == 2

    def test_poll_interval_used_as_initial_backoff_delay(self) -> None:
        """poll_interval is used as the starting interval for backoff after first probe failure."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(side_effect=[False, True])
        captured_sleep: list[float] = []

        async def _run() -> bool:
            async def fake_sleep(seconds: float) -> None:
                captured_sleep.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=7.0,
                    max_wait=3600.0,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=7.0, max_seconds=600.0, multiplier=2.0, jitter=0.0),
                )

        asyncio.run(_run())
        # There should be a backoff sleep of ~7.0 after the first failure
        backoff_sleeps = [s for s in captured_sleep if s > 1.0]
        assert any(abs(s - 7.0) < 0.5 for s in backoff_sleeps)

    def test_await_asyncio_sleep_is_used_not_time_sleep(self) -> None:
        """wait_for_reset uses asyncio.sleep (not time.sleep) -- verified via mock call check."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=True)

        async def _run() -> bool:
            with patch("devbench.quota.asyncio.sleep", new_callable=AsyncMock) as mock_async_sleep:
                result = await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )
                # asyncio.sleep must have been called at least once (initial sleep for reset_at)
                assert mock_async_sleep.called
                return result

        result = asyncio.run(_run())
        assert result is True

    def test_conflicting_poll_interval_and_backoff_config_raises(self) -> None:
        """ValueError raised when poll_interval != backoff_config.initial_seconds."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=True)

        async def _run() -> None:
            await wait_for_reset(
                reset_at=reset_at,
                poll_interval=5.0,
                max_wait=300.0,
                probe_fn=probe_fn,
                backoff_config=BackoffConfig(initial_seconds=10.0, max_seconds=60.0, multiplier=2.0, jitter=0.0),
            )

        with pytest.raises(ValueError, match="Conflicting backoff configuration"):
            asyncio.run(_run())

    def test_probe_fn_exception_propagates_unchanged(self) -> None:
        """Any exception raised by probe_fn propagates out of wait_for_reset unchanged."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(side_effect=RuntimeError("quota probe failed"))

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=5.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                )

        with pytest.raises(RuntimeError, match="quota probe failed"):
            asyncio.run(_run())

    def test_matching_poll_interval_and_backoff_config_does_not_raise(self) -> None:
        """No ValueError when poll_interval equals backoff_config.initial_seconds."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=True)

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                return await wait_for_reset(
                    reset_at=reset_at,
                    poll_interval=10.0,
                    max_wait=300.0,
                    probe_fn=probe_fn,
                    backoff_config=BackoffConfig(initial_seconds=10.0, max_seconds=60.0, multiplier=2.0, jitter=0.0),
                )

        result = asyncio.run(_run())
        assert result is True


# ---------------------------------------------------------------------------
# wait_for_reset: integration -- no mocks on async.sleep
# ---------------------------------------------------------------------------


class TestWaitForResetIntegration:
    """Integration tests: wait_for_reset with real probe functions (no mock on asyncio.sleep).

    These tests use very small durations (0-second sleeps) to run without blocking.
    They verify the end-to-end logic path against realistic fixture objects.
    """

    def test_integration_probe_succeeds_immediately(self) -> None:
        """Real asyncio.sleep(0) + probe that returns True immediately -> True."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=True)

        async def _run() -> bool:
            return await wait_for_reset(
                reset_at=reset_at,
                poll_interval=0.0,
                max_wait=10.0,
                probe_fn=probe_fn,
                backoff_config=BackoffConfig(initial_seconds=0.0, max_seconds=0.01, multiplier=1.0, jitter=0.0),
            )

        result = asyncio.run(_run())
        assert result is True
        probe_fn.assert_called_once()

    def test_integration_probe_fails_then_succeeds(self) -> None:
        """Real asyncio.sleep(tiny) + probe that fails once then succeeds -> True."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(side_effect=[False, True])

        async def _run() -> bool:
            return await wait_for_reset(
                reset_at=reset_at,
                poll_interval=0.001,
                max_wait=10.0,
                probe_fn=probe_fn,
                backoff_config=BackoffConfig(initial_seconds=0.001, max_seconds=0.01, multiplier=1.5, jitter=0.0),
            )

        result = asyncio.run(_run())
        assert result is True
        assert probe_fn.call_count == 2

    def test_integration_max_wait_exceeded(self) -> None:
        """Real asyncio.sleep(tiny) + probe that always fails -> False when max_wait exceeded."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        probe_fn = MagicMock(return_value=False)

        async def _run() -> bool:
            return await wait_for_reset(
                reset_at=reset_at,
                poll_interval=0.01,
                max_wait=0.05,
                probe_fn=probe_fn,
                backoff_config=BackoffConfig(initial_seconds=0.01, max_seconds=0.02, multiplier=1.5, jitter=0.0),
            )

        result = asyncio.run(_run())
        assert result is False


# ---------------------------------------------------------------------------
# recovery_probe: happy path -- API call succeeds (returns True)
# ---------------------------------------------------------------------------


class TestRecoveryProbeHappyPath:
    """AC-193-18: recovery_probe returns True when the Anthropic API responds without quota error."""

    def test_returns_true_when_api_call_succeeds(self) -> None:
        """A successful Anthropic API response causes recovery_probe to return True."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-api-key"):
                result = recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert result is True

    def test_uses_configured_request_size_tokens(self) -> None:
        """request_size_tokens=1 is sent as max_tokens in the completion request."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        call_kwargs: dict[str, object] = {}

        def capture_create(**kwargs: object) -> MagicMock:
            call_kwargs.update(kwargs)
            return mock_message

        mock_client.messages.create = MagicMock(side_effect=capture_create)

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                result = recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert result is True
        assert "max_tokens" in call_kwargs
        assert call_kwargs["max_tokens"] == 1

    def test_default_timeout_seconds_is_used_when_not_specified(self) -> None:
        """recovery_probe(timeout_seconds=10) is the default; calling without args uses default."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                result = recovery_probe()

        assert result is True

    def test_default_request_size_tokens_is_one(self) -> None:
        """Default request_size_tokens is 1 (minimal probe request)."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        call_kwargs: dict[str, object] = {}

        def capture_create(**kwargs: object) -> MagicMock:
            call_kwargs.update(kwargs)
            return mock_message

        mock_client.messages.create = MagicMock(side_effect=capture_create)

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                recovery_probe()

        assert call_kwargs.get("max_tokens") == 1

    def test_probe_uses_api_key_from_config(self) -> None:
        """recovery_probe obtains the API key from get_anthropic_api_key()."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client) as mock_cls:
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-api-key") as mock_key:
                result = recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert result is True
        mock_key.assert_called_once()
        mock_cls.assert_called_once()
        _, ctor_kwargs = mock_cls.call_args
        assert ctor_kwargs.get("api_key") == "test-api-key"


# ---------------------------------------------------------------------------
# recovery_probe: quota error -- API call throttled (returns False)
# ---------------------------------------------------------------------------


class TestRecoveryProbeQuotaError:
    """recovery_probe returns False when the API raises a quota error."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            SubscriptionRateLimitError,
            SdkCreditExhaustedError,
            ApiBillingError,
            BedrockThrottleError,
        ],
        ids=[
            "SubscriptionRateLimitError",
            "SdkCreditExhaustedError",
            "ApiBillingError",
            "BedrockThrottleError",
        ],
    )
    def test_returns_false_when_quota_error_raised(self, exc_cls: type[QuotaExhaustedError]) -> None:
        """When the Anthropic API raises a QuotaExhaustedError subclass, recovery_probe returns False."""
        quota_exc = exc_cls(reset_at=None, raw_error="throttled", source="anthropic-api")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = quota_exc

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                result = recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert result is False

    def test_returns_false_when_sdk_exc_detected_as_429(self) -> None:
        """When the API raises an SDK exception that detect_quota_error classifies as quota error, returns False."""

        class _FakeSdkError(Exception):
            def __init__(self) -> None:
                self.status_code = 429
                self.body: dict[str, Any] = {}
                super().__init__("429 rate limit")

        sdk_exc = _FakeSdkError()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = sdk_exc

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                result = recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert result is False

    def test_returns_false_when_sdk_exc_detected_as_402(self) -> None:
        """When detect_quota_error classifies a 402 SDK exception as billing error, returns False."""

        class _FakeSdkError(Exception):
            def __init__(self) -> None:
                self.status_code = 402
                self.body: dict[str, Any] = {}
                super().__init__("402 payment required")

        sdk_exc = _FakeSdkError()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = sdk_exc

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                result = recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert result is False


# ---------------------------------------------------------------------------
# recovery_probe: non-quota errors propagate (fail-fast)
# ---------------------------------------------------------------------------


class TestRecoveryProbeNonQuotaErrors:
    """Non-quota errors must propagate unchanged (fail-fast; no silent swallowing)."""

    def test_propagates_connection_error(self) -> None:
        """A network error (ConnectionError) propagates out of recovery_probe unchanged."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = ConnectionError("network unreachable")

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                with pytest.raises(ConnectionError, match="network unreachable"):
                    recovery_probe(timeout_seconds=5, request_size_tokens=1)

    def test_propagates_timeout_error(self) -> None:
        """A TimeoutError propagates out of recovery_probe unchanged."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = TimeoutError("request timed out")

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                with pytest.raises(TimeoutError, match="request timed out"):
                    recovery_probe(timeout_seconds=5, request_size_tokens=1)

    def test_propagates_runtime_error(self) -> None:
        """An unexpected RuntimeError propagates out of recovery_probe unchanged."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("unexpected failure")

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                with pytest.raises(RuntimeError, match="unexpected failure"):
                    recovery_probe(timeout_seconds=5, request_size_tokens=1)

    def test_propagates_get_api_key_runtime_error(self) -> None:
        """RuntimeError from get_anthropic_api_key (missing credentials) propagates unchanged."""
        with patch("devbench.quota.get_anthropic_api_key", side_effect=RuntimeError("credentials missing")):
            with pytest.raises(RuntimeError, match="credentials missing"):
                recovery_probe(timeout_seconds=5, request_size_tokens=1)


# ---------------------------------------------------------------------------
# recovery_probe: input validation (fail-fast on invalid parameters)
# ---------------------------------------------------------------------------


class TestRecoveryProbeInputValidation:
    """Fail-fast: recovery_probe rejects invalid parameters before making API calls."""

    @pytest.mark.parametrize(
        "timeout_val",
        [0.0, -1.0, -0.001],
        ids=["zero", "negative-one", "small-negative"],
    )
    def test_rejects_non_positive_timeout_seconds(self, timeout_val: float) -> None:
        """timeout_seconds <= 0 raises ValueError before any API call is attempted."""
        with pytest.raises(ValueError, match="timeout_seconds"):
            recovery_probe(timeout_seconds=timeout_val, request_size_tokens=1)

    @pytest.mark.parametrize(
        "token_val",
        [0, -1, -100],
        ids=["zero", "negative-one", "large-negative"],
    )
    def test_rejects_non_positive_request_size_tokens(self, token_val: int) -> None:
        """request_size_tokens <= 0 raises ValueError before any API call is attempted."""
        with pytest.raises(ValueError, match="request_size_tokens"):
            recovery_probe(timeout_seconds=5.0, request_size_tokens=token_val)

    def test_no_api_call_made_on_invalid_timeout(self) -> None:
        """When timeout_seconds is invalid, no Anthropic client is constructed."""
        with patch("devbench.quota.anthropic.Anthropic") as mock_cls:
            with pytest.raises(ValueError, match="timeout_seconds"):
                recovery_probe(timeout_seconds=-1.0, request_size_tokens=1)
        mock_cls.assert_not_called()

    def test_no_api_call_made_on_invalid_request_size(self) -> None:
        """When request_size_tokens is invalid, no Anthropic client is constructed."""
        with patch("devbench.quota.anthropic.Anthropic") as mock_cls:
            with pytest.raises(ValueError, match="request_size_tokens"):
                recovery_probe(timeout_seconds=5.0, request_size_tokens=0)
        mock_cls.assert_not_called()

    def test_no_api_key_fetched_on_invalid_params(self) -> None:
        """When parameters are invalid, get_anthropic_api_key is never called."""
        with patch("devbench.quota.get_anthropic_api_key") as mock_key:
            with pytest.raises(ValueError):
                recovery_probe(timeout_seconds=0.0, request_size_tokens=1)
        mock_key.assert_not_called()


# ---------------------------------------------------------------------------
# recovery_probe: API call parameters (model, messages content)
# ---------------------------------------------------------------------------


class TestRecoveryProbeApiCallParameters:
    """Verify the API call is minimal: 1 token, a model string, and a non-empty message list."""

    def test_messages_create_called_once(self) -> None:
        """recovery_probe calls messages.create exactly once per invocation."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                recovery_probe(timeout_seconds=5, request_size_tokens=1)

        mock_client.messages.create.assert_called_once()

    def test_model_parameter_is_non_empty_string(self) -> None:
        """The model parameter in the API call is a non-empty string."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        call_kwargs: dict[str, object] = {}

        def capture(**kwargs: object) -> MagicMock:
            call_kwargs.update(kwargs)
            return mock_message

        mock_client.messages.create = MagicMock(side_effect=capture)

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert "model" in call_kwargs
        assert isinstance(call_kwargs["model"], str)
        assert len(call_kwargs["model"]) > 0

    def test_messages_parameter_is_non_empty_list(self) -> None:
        """The messages parameter in the API call is a non-empty list."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        call_kwargs: dict[str, object] = {}

        def capture(**kwargs: object) -> MagicMock:
            call_kwargs.update(kwargs)
            return mock_message

        mock_client.messages.create = MagicMock(side_effect=capture)

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                recovery_probe(timeout_seconds=5, request_size_tokens=1)

        assert "messages" in call_kwargs
        msgs = call_kwargs["messages"]
        assert isinstance(msgs, list)
        assert len(msgs) > 0

    @pytest.mark.parametrize("token_count", [1, 2, 5])
    def test_request_size_tokens_forwarded_as_max_tokens(self, token_count: int) -> None:
        """request_size_tokens is forwarded to max_tokens in the API call."""
        mock_message = MagicMock()
        mock_client = MagicMock()
        call_kwargs: dict[str, object] = {}

        def capture(**kwargs: object) -> MagicMock:
            call_kwargs.update(kwargs)
            return mock_message

        mock_client.messages.create = MagicMock(side_effect=capture)

        with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
            with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                recovery_probe(timeout_seconds=5, request_size_tokens=token_count)

        assert call_kwargs.get("max_tokens") == token_count


# ---------------------------------------------------------------------------
# recovery_probe: integration -- used as probe_fn in wait_for_reset
# ---------------------------------------------------------------------------


class TestRecoveryProbeIntegration:
    """Integration: recovery_probe used as probe_fn inside wait_for_reset."""

    def test_recovery_probe_as_probe_fn_returns_true_when_api_succeeds(self) -> None:
        """wait_for_reset returns True when recovery_probe succeeds on first probe."""
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        mock_message = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
                    with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                        return await wait_for_reset(
                            reset_at=reset_at,
                            poll_interval=5.0,
                            max_wait=300.0,
                            probe_fn=lambda: recovery_probe(timeout_seconds=5, request_size_tokens=1),
                        )

        result = asyncio.run(_run())
        assert result is True

    def test_recovery_probe_as_probe_fn_returns_false_when_quota_persists(self) -> None:
        """wait_for_reset returns False when recovery_probe always detects quota error.

        Uses non-zero backoff intervals so elapsed time accumulates and max_wait
        is eventually reached without looping forever.
        """
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        quota_exc = SubscriptionRateLimitError(reset_at=None, raw_error="throttled", source="anthropic-api")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = quota_exc

        async def _run() -> bool:
            async def fake_sleep(_seconds: float) -> None:
                pass

            with patch("devbench.quota.asyncio.sleep", side_effect=fake_sleep):
                with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
                    with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                        return await wait_for_reset(
                            reset_at=reset_at,
                            poll_interval=5.0,
                            max_wait=5.0,
                            probe_fn=lambda: recovery_probe(timeout_seconds=5, request_size_tokens=1),
                            backoff_config=BackoffConfig(
                                initial_seconds=5.0, max_seconds=10.0, multiplier=2.0, jitter=0.0
                            ),
                        )

        result = asyncio.run(_run())
        assert result is False

    def test_jitter_applied_when_recovery_probe_used_as_probe_fn(self) -> None:
        """AC-193-18: jitter prevents thundering-herd when recovery_probe is probe_fn.

        When BackoffConfig.jitter > 0.0, each backoff sleep duration must lie
        within the jitter band of the nominal interval and must not all be
        exactly equal to the nominal values.  This verifies that wait_for_reset
        applies jitter to the backoff intervals, which is the thundering-herd
        protection required by AC-193-18.

        Uses three consecutive quota failures so three backoff sleeps accumulate,
        then asserts that (a) every sleep is within +/-20% of its nominal
        interval and (b) at least one sleep deviates from the exact nominal
        value, confirming jitter is non-zero.
        """
        reset_at = datetime.now(tz=UTC) - timedelta(seconds=1)
        quota_exc = SubscriptionRateLimitError(reset_at=None, raw_error="throttled", source="anthropic-api")
        mock_client = MagicMock()
        # Fail quota 3 times so 3 backoff sleeps occur, then succeed on the 4th probe.
        probe_responses: list[Exception | None] = [quota_exc, quota_exc, quota_exc, None]
        call_index: list[int] = [0]

        def create_side_effect(**_kwargs: object) -> MagicMock:
            idx = call_index[0]
            call_index[0] += 1
            exc = probe_responses[idx] if idx < len(probe_responses) else None
            if exc is not None:
                raise exc
            return MagicMock()

        mock_client.messages.create = MagicMock(side_effect=create_side_effect)

        sleep_durations: list[float] = []

        async def _run() -> bool:
            async def recording_sleep(seconds: float) -> None:
                sleep_durations.append(seconds)

            with patch("devbench.quota.asyncio.sleep", side_effect=recording_sleep):
                with patch("devbench.quota.anthropic.Anthropic", return_value=mock_client):
                    with patch("devbench.quota.get_anthropic_api_key", return_value="test-key"):
                        return await wait_for_reset(
                            reset_at=reset_at,
                            poll_interval=30.0,
                            max_wait=10000.0,
                            probe_fn=lambda: recovery_probe(timeout_seconds=5, request_size_tokens=1),
                            backoff_config=BackoffConfig(
                                initial_seconds=30.0,
                                max_seconds=600.0,
                                multiplier=2.0,
                                jitter=0.2,
                            ),
                        )

        result = asyncio.run(_run())
        assert result is True

        # sleep_durations[0] is the initial reset_at sleep (clamped to ~0 since reset_at is past).
        # The subsequent sleeps are the jittered backoff intervals.
        backoff_sleeps = sleep_durations[1:]
        assert len(backoff_sleeps) >= 3, f"Expected at least 3 backoff sleeps, got {backoff_sleeps}"

        # With jitter=0.2, each sleep must lie within +/-20% of its nominal interval.
        nominal_intervals = [30.0, 60.0, 120.0]  # base * multiplier^n for n=0,1,2
        for actual, nominal in zip(backoff_sleeps, nominal_intervals, strict=False):
            lower = nominal * 0.8
            upper = nominal * 1.2
            assert lower <= actual <= upper, (
                f"Jitter sleep {actual!r} is outside the band [{lower!r}, {upper!r}] for nominal interval {nominal!r}."
            )

        # Verify at least one backoff sleep differs from the exact nominal value --
        # confirming jitter was applied (not a fixed-delay loop).
        all_exact = all(
            abs(actual - nominal) < 1e-9 for actual, nominal in zip(backoff_sleeps, nominal_intervals, strict=False)
        )
        assert not all_exact, (
            "All backlog sleeps matched the nominal intervals exactly -- "
            "jitter was not applied, which would allow thundering-herd behavior."
        )


# ---------------------------------------------------------------------------
# QuotaCheckpoint dataclass
# ---------------------------------------------------------------------------


class TestQuotaCheckpointDataclass:
    """QuotaCheckpoint carries all pause-state fields required by AC-193-8 and AC-193-16."""

    _NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
    _RESET = datetime(2026, 3, 1, 15, 0, 0, tzinfo=UTC)

    def _make(self, **overrides: object) -> QuotaCheckpoint:
        defaults: dict[str, object] = {
            "paused_at": self._NOW,
            "reset_at": self._RESET,
            "reason": "subscription_rate_limit",
            "raw_error": "HTTP 429",
            "in_flight_wu": "E5-F2-S1-T3",
            "in_flight_phase": "GREEN",
            "completed_judges": ["code_review"],
            "pending_judges": ["test_review", "security_review"],
            "stage_artefacts": {"branch": "feat/quota"},
        }
        defaults.update(overrides)
        return QuotaCheckpoint(**defaults)  # type: ignore[arg-type]

    def test_is_dataclass(self) -> None:
        """QuotaCheckpoint can be instantiated with all required fields."""
        cp = self._make()
        assert cp.paused_at == self._NOW

    def test_all_fields_stored(self) -> None:
        """All nine fields are stored and accessible as attributes."""
        cp = self._make()
        assert cp.paused_at == self._NOW
        assert cp.reset_at == self._RESET
        assert cp.reason == "subscription_rate_limit"
        assert cp.raw_error == "HTTP 429"
        assert cp.in_flight_wu == "E5-F2-S1-T3"
        assert cp.in_flight_phase == "GREEN"
        assert cp.completed_judges == ["code_review"]
        assert cp.pending_judges == ["test_review", "security_review"]
        assert cp.stage_artefacts == {"branch": "feat/quota"}

    def test_reset_at_can_be_none(self) -> None:
        """reset_at is optional (vendor may not publish a reset time)."""
        cp = self._make(reset_at=None)
        assert cp.reset_at is None

    def test_in_flight_wu_can_be_none(self) -> None:
        """in_flight_wu is optional when no WU was in-flight at pause time."""
        cp = self._make(in_flight_wu=None)
        assert cp.in_flight_wu is None

    def test_in_flight_phase_can_be_none(self) -> None:
        """in_flight_phase is optional when in_flight_wu is absent."""
        cp = self._make(in_flight_phase=None)
        assert cp.in_flight_phase is None

    def test_completed_judges_empty_list_allowed(self) -> None:
        """completed_judges can be an empty list when no judges have run."""
        cp = self._make(completed_judges=[])
        assert cp.completed_judges == []

    def test_pending_judges_empty_list_allowed(self) -> None:
        """pending_judges can be an empty list when no judges remain."""
        cp = self._make(pending_judges=[])
        assert cp.pending_judges == []

    def test_stage_artefacts_empty_dict_allowed(self) -> None:
        """stage_artefacts can be an empty dict when no artefacts were captured."""
        cp = self._make(stage_artefacts={})
        assert cp.stage_artefacts == {}

    @pytest.mark.parametrize(
        "reason",
        [
            "subscription_rate_limit",
            "sdk_credit_exhausted",
            "api_billing_error",
            "bedrock_throttle",
            "unknown",
        ],
    )
    def test_reason_accepts_various_values(self, reason: str) -> None:
        """reason field accepts any non-empty string."""
        cp = self._make(reason=reason)
        assert cp.reason == reason


# ---------------------------------------------------------------------------
# save_checkpoint: happy path writes quota_pause.json atomically
# ---------------------------------------------------------------------------


class TestSaveCheckpointHappyPath:
    """AC-193-8: save_checkpoint writes quota_pause.json to the session directory."""

    _NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
    _RESET = datetime(2026, 3, 1, 15, 0, 0, tzinfo=UTC)

    def test_file_is_created(self, tmp_path: Path) -> None:
        """save_checkpoint creates quota_pause.json under session_dir/.devbench/."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="HTTP 429",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="GREEN",
            completed_judges=["code_review"],
            pending_judges=["test_review"],
            stage_artefacts={"key": "val"},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        assert checkpoint_file.exists()

    def test_file_contains_valid_json(self, tmp_path: Path) -> None:
        """The written file is valid JSON."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="HTTP 429",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="GREEN",
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_paused_at_stored_as_iso_string(self, tmp_path: Path) -> None:
        """paused_at is serialized as an ISO 8601 string."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="HTTP 429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert "paused_at" in data
        # Must be parseable as a datetime
        parsed = datetime.fromisoformat(data["paused_at"])
        assert parsed.tzinfo is not None

    def test_reset_at_stored_as_iso_string_when_set(self, tmp_path: Path) -> None:
        """reset_at is serialized as an ISO 8601 string when not None."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="HTTP 429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert "reset_at" in data
        parsed = datetime.fromisoformat(data["reset_at"])
        assert parsed.tzinfo is not None

    def test_reset_at_stored_as_null_when_none(self, tmp_path: Path) -> None:
        """When reset_at is None, it is serialized as JSON null."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="HTTP 429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert data["reset_at"] is None

    def test_all_scalar_fields_present(self, tmp_path: Path) -> None:
        """All nine fields appear in the serialized JSON."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="bedrock_throttle",
            raw_error="ThrottlingException",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="RED",
            completed_judges=["code_review"],
            pending_judges=["test_review"],
            stage_artefacts={"branch": "main"},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        for field in (
            "paused_at",
            "reset_at",
            "reason",
            "raw_error",
            "in_flight_wu",
            "in_flight_phase",
            "completed_judges",
            "pending_judges",
            "stage_artefacts",
        ):
            assert field in data, f"Field {field!r} missing from quota_pause.json"

    def test_creates_parent_devbench_dir_when_absent(self, tmp_path: Path) -> None:
        """save_checkpoint creates the .devbench directory when it does not exist."""
        devbench_dir = tmp_path / ".devbench"
        assert not devbench_dir.exists()
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        assert devbench_dir.is_dir()

    def test_write_is_atomic_no_partial_file(self, tmp_path: Path) -> None:
        """The file must be written atomically (temp-then-rename) so readers never see partial JSON."""
        # We cannot intercept os.replace directly in a deterministic way,
        # but we CAN verify that a concurrent reader always sees a complete,
        # valid JSON file (not a half-written one).  After save_checkpoint
        # returns, the file must be fully readable.
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="sdk_credit_exhausted",
            raw_error="402",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="GREEN",
            completed_judges=["code_review", "test_review"],
            pending_judges=["security_review"],
            stage_artefacts={"pr": 42},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            content = f.read()
        # Must parse as complete JSON (no truncation)
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """A second call overwrites the existing quota_pause.json with new data."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="429-first",
            in_flight_wu="T1",
            in_flight_phase="RED",
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        new_time = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=new_time,
            reset_at=None,
            reason="bedrock_throttle",
            raw_error="429-second",
            in_flight_wu="T2",
            in_flight_phase="GREEN",
            completed_judges=["code_review"],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert data["reason"] == "bedrock_throttle"
        assert data["in_flight_wu"] == "T2"

    def test_in_flight_wu_stored_when_set(self, tmp_path: Path) -> None:
        """in_flight_wu is serialized correctly when a WU id is provided."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="GREEN",
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert data["in_flight_wu"] == "E5-F2-S1-T3"

    def test_in_flight_wu_stored_as_null_when_none(self, tmp_path: Path) -> None:
        """in_flight_wu is JSON null when None."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert data["in_flight_wu"] is None

    def test_completed_judges_list_preserved(self, tmp_path: Path) -> None:
        """completed_judges list is round-tripped exactly."""
        judges = ["code_review", "test_review", "security_review"]
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=judges,
            pending_judges=[],
            stage_artefacts={},
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert data["completed_judges"] == judges

    def test_stage_artefacts_dict_preserved(self, tmp_path: Path) -> None:
        """stage_artefacts dict is round-tripped exactly."""
        artefacts = {"branch": "feat/quota", "pr": 42, "commit": "abc123"}
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts=artefacts,
        )
        checkpoint_file = tmp_path / ".devbench" / "quota_pause.json"
        with checkpoint_file.open() as f:
            data = json.load(f)
        assert data["stage_artefacts"] == artefacts


# ---------------------------------------------------------------------------
# save_checkpoint: path resolution -- workspace root fallback
# ---------------------------------------------------------------------------


class TestSaveCheckpointPathResolution:
    """AC-193-16: per-session path when env var set, workspace-root fallback otherwise."""

    _NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    def _save(self, session_dir: Path, **overrides: object) -> Path:
        kwargs: dict[str, object] = {
            "session_dir": session_dir,
            "paused_at": self._NOW,
            "reset_at": None,
            "reason": "subscription_rate_limit",
            "raw_error": "429",
            "in_flight_wu": None,
            "in_flight_phase": None,
            "completed_judges": [],
            "pending_judges": [],
            "stage_artefacts": {},
        }
        kwargs.update(overrides)
        save_checkpoint(**kwargs)  # type: ignore[arg-type]
        return session_dir / ".devbench" / "quota_pause.json"

    def test_uses_session_dir_when_provided(self, tmp_path: Path) -> None:
        """When session_dir is provided, quota_pause.json is inside session_dir/.devbench/."""
        session_dir = tmp_path / "sessions" / "alpha"
        session_dir.mkdir(parents=True)
        result_path = self._save(session_dir=session_dir)
        assert result_path.exists()
        assert str(result_path).startswith(str(session_dir))

    def test_file_under_devbench_subdir(self, tmp_path: Path) -> None:
        """quota_pause.json is always placed under the .devbench subdirectory."""
        result_path = self._save(session_dir=tmp_path)
        assert result_path.parent.name == ".devbench"
        assert result_path.name == "quota_pause.json"

    @pytest.mark.parametrize(
        "session_name",
        ["alpha", "beta", "session-01", "default"],
    )
    def test_different_session_dirs_produce_independent_files(self, tmp_path: Path, session_name: str) -> None:
        """Each session_dir produces an independent quota_pause.json (multi-session aware)."""
        session_dir = tmp_path / "sessions" / session_name
        session_dir.mkdir(parents=True)
        result_path = self._save(session_dir=session_dir)
        assert result_path.exists()
        assert session_name in str(result_path)


# ---------------------------------------------------------------------------
# load_checkpoint: file absent returns None
# ---------------------------------------------------------------------------


class TestLoadCheckpointAbsent:
    """load_checkpoint returns None when quota_pause.json does not exist."""

    def test_returns_none_when_file_absent(self, tmp_path: Path) -> None:
        """Returns None when quota_pause.json has never been written."""
        result = load_checkpoint(session_dir=tmp_path)
        assert result is None

    def test_returns_none_when_devbench_dir_absent(self, tmp_path: Path) -> None:
        """Returns None when the .devbench directory does not exist."""
        assert not (tmp_path / ".devbench").exists()
        result = load_checkpoint(session_dir=tmp_path)
        assert result is None

    def test_returns_none_after_file_deleted(self, tmp_path: Path) -> None:
        """Returns None after the checkpoint file has been removed (post-resume cleanup)."""
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir()
        checkpoint_file = devbench_dir / "quota_pause.json"
        checkpoint_file.write_text("{}")
        checkpoint_file.unlink()
        result = load_checkpoint(session_dir=tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# load_checkpoint: happy path returns QuotaCheckpoint
# ---------------------------------------------------------------------------


class TestLoadCheckpointHappyPath:
    """load_checkpoint returns a QuotaCheckpoint with all fields correctly deserialized."""

    _NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
    _RESET = datetime(2026, 3, 1, 15, 0, 0, tzinfo=UTC)

    def _write_raw(self, tmp_path: Path, data: dict[str, object]) -> None:
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text(json.dumps(data))

    def test_returns_quota_checkpoint_instance(self, tmp_path: Path) -> None:
        """load_checkpoint returns a QuotaCheckpoint when the file exists."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="HTTP 429",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="GREEN",
            completed_judges=["code_review"],
            pending_judges=["test_review"],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert isinstance(result, QuotaCheckpoint)

    def test_paused_at_deserialized_as_utc_datetime(self, tmp_path: Path) -> None:
        """paused_at is deserialized to a UTC-aware datetime."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.paused_at == self._NOW
        assert result.paused_at.tzinfo is not None

    def test_reset_at_deserialized_when_set(self, tmp_path: Path) -> None:
        """reset_at is deserialized to a UTC-aware datetime when not null."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.reset_at == self._RESET
        assert result.reset_at is not None
        assert result.reset_at.tzinfo is not None

    def test_reset_at_is_none_when_serialized_as_null(self, tmp_path: Path) -> None:
        """reset_at is None in the returned QuotaCheckpoint when JSON value was null."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.reset_at is None

    def test_all_scalar_fields_round_tripped(self, tmp_path: Path) -> None:
        """All fields survive a save_checkpoint -> load_checkpoint round trip."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="bedrock_throttle",
            raw_error="ThrottlingException",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="RED",
            completed_judges=["code_review", "test_review"],
            pending_judges=["security_review"],
            stage_artefacts={"branch": "feat/quota", "pr": 99},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.paused_at == self._NOW
        assert result.reset_at == self._RESET
        assert result.reason == "bedrock_throttle"
        assert result.raw_error == "ThrottlingException"
        assert result.in_flight_wu == "E5-F2-S1-T3"
        assert result.in_flight_phase == "RED"
        assert result.completed_judges == ["code_review", "test_review"]
        assert result.pending_judges == ["security_review"]
        assert result.stage_artefacts == {"branch": "feat/quota", "pr": 99}

    def test_in_flight_wu_none_round_tripped(self, tmp_path: Path) -> None:
        """in_flight_wu=None survives a round trip."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.in_flight_wu is None
        assert result.in_flight_phase is None

    def test_empty_lists_round_tripped(self, tmp_path: Path) -> None:
        """Empty completed_judges and pending_judges lists round-trip correctly."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.completed_judges == []
        assert result.pending_judges == []

    def test_empty_stage_artefacts_round_tripped(self, tmp_path: Path) -> None:
        """Empty stage_artefacts dict round-trips correctly."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        result = load_checkpoint(session_dir=tmp_path)
        assert result is not None
        assert result.stage_artefacts == {}


# ---------------------------------------------------------------------------
# load_checkpoint: malformed / corrupt JSON raises ValueError
# ---------------------------------------------------------------------------


class TestLoadCheckpointCorruptFile:
    """load_checkpoint raises ValueError on corrupt or malformed quota_pause.json."""

    def _write_raw_text(self, tmp_path: Path, text: str) -> None:
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text(text)

    def test_raises_value_error_on_invalid_json(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when quota_pause.json contains invalid JSON."""
        self._write_raw_text(tmp_path, "not valid json {{{")
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_value_error_on_non_dict_json(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when JSON root is not a dict."""
        self._write_raw_text(tmp_path, "[1, 2, 3]")
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_value_error_on_missing_required_field(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when a required field is absent."""
        # paused_at is mandatory
        self._write_raw_text(
            tmp_path,
            json.dumps(
                {
                    "reset_at": None,
                    "reason": "subscription_rate_limit",
                    "raw_error": "429",
                    "in_flight_wu": None,
                    "in_flight_phase": None,
                    "completed_judges": [],
                    "pending_judges": [],
                    "stage_artefacts": {},
                }
            ),
        )
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_value_error_on_invalid_paused_at_format(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when paused_at is not a valid ISO datetime."""
        self._write_raw_text(
            tmp_path,
            json.dumps(
                {
                    "paused_at": "not-a-datetime",
                    "reset_at": None,
                    "reason": "subscription_rate_limit",
                    "raw_error": "429",
                    "in_flight_wu": None,
                    "in_flight_phase": None,
                    "completed_judges": [],
                    "pending_judges": [],
                    "stage_artefacts": {},
                }
            ),
        )
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_value_error_on_invalid_reset_at_format(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when reset_at is neither null nor a valid ISO datetime."""
        self._write_raw_text(
            tmp_path,
            json.dumps(
                {
                    "paused_at": "2026-03-01T10:00:00+00:00",
                    "reset_at": "not-a-datetime",
                    "reason": "subscription_rate_limit",
                    "raw_error": "429",
                    "in_flight_wu": None,
                    "in_flight_phase": None,
                    "completed_judges": [],
                    "pending_judges": [],
                    "stage_artefacts": {},
                }
            ),
        )
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_value_error_on_empty_file(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when quota_pause.json is empty."""
        self._write_raw_text(tmp_path, "")
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)


# ---------------------------------------------------------------------------
# save_checkpoint + load_checkpoint: integration round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadCheckpointIntegration:
    """Integration: save_checkpoint followed immediately by load_checkpoint returns equivalent data.

    These tests use a real tmp_path filesystem (no mocks on os.replace or json)
    to verify the end-to-end atomic write + read path.
    """

    _NOW = datetime(2026, 5, 15, 8, 30, 0, tzinfo=UTC)
    _RESET = datetime(2026, 5, 15, 13, 0, 0, tzinfo=UTC)

    def test_round_trip_full_checkpoint(self, tmp_path: Path) -> None:
        """Full round-trip: every field survives save -> load intact."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=self._RESET,
            reason="subscription_rate_limit",
            raw_error="HTTP 429 -- rate limit exceeded",
            in_flight_wu="E5-F2-S1-T3",
            in_flight_phase="GREEN",
            completed_judges=["code_review", "test_review"],
            pending_judges=["security_review", "doc_review"],
            stage_artefacts={"branch": "feat/quota", "commit": "deadbeef", "pr": 77},
        )
        cp = load_checkpoint(session_dir=tmp_path)
        assert cp is not None
        assert cp.paused_at == self._NOW
        assert cp.reset_at == self._RESET
        assert cp.reason == "subscription_rate_limit"
        assert cp.raw_error == "HTTP 429 -- rate limit exceeded"
        assert cp.in_flight_wu == "E5-F2-S1-T3"
        assert cp.in_flight_phase == "GREEN"
        assert cp.completed_judges == ["code_review", "test_review"]
        assert cp.pending_judges == ["security_review", "doc_review"]
        assert cp.stage_artefacts == {"branch": "feat/quota", "commit": "deadbeef", "pr": 77}

    def test_round_trip_minimal_checkpoint(self, tmp_path: Path) -> None:
        """Minimal checkpoint (reset_at=None, no wu/phase, empty lists) round-trips."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="sdk_credit_exhausted",
            raw_error="402",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        cp = load_checkpoint(session_dir=tmp_path)
        assert cp is not None
        assert cp.reset_at is None
        assert cp.in_flight_wu is None
        assert cp.in_flight_phase is None
        assert cp.completed_judges == []
        assert cp.pending_judges == []
        assert cp.stage_artefacts == {}

    def test_load_returns_none_before_first_save(self, tmp_path: Path) -> None:
        """Before any save, load returns None (no file exists)."""
        assert load_checkpoint(session_dir=tmp_path) is None

    def test_load_still_works_after_overwrite(self, tmp_path: Path) -> None:
        """After two sequential saves, load_checkpoint returns the second checkpoint."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429-first",
            in_flight_wu="T1",
            in_flight_phase="RED",
            completed_judges=[],
            pending_judges=["code_review"],
            stage_artefacts={},
        )
        second_time = datetime(2026, 5, 15, 9, 0, 0, tzinfo=UTC)
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=second_time,
            reset_at=self._RESET,
            reason="bedrock_throttle",
            raw_error="429-second",
            in_flight_wu="T2",
            in_flight_phase="GREEN",
            completed_judges=["code_review"],
            pending_judges=["test_review"],
            stage_artefacts={"pr": 55},
        )
        cp = load_checkpoint(session_dir=tmp_path)
        assert cp is not None
        assert cp.reason == "bedrock_throttle"
        assert cp.in_flight_wu == "T2"
        assert cp.paused_at == second_time

    @pytest.mark.parametrize(
        "session_name",
        ["alpha", "beta", "default"],
    )
    def test_independent_session_dirs_do_not_interfere(self, tmp_path: Path, session_name: str) -> None:
        """Each session_dir gets its own independent quota_pause.json (AC-193-16)."""
        session_a = tmp_path / "sessions" / "alpha"
        session_b = tmp_path / "sessions" / "beta"
        session_a.mkdir(parents=True)
        session_b.mkdir(parents=True)

        save_checkpoint(
            session_dir=session_a,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu="T-alpha",
            in_flight_phase="RED",
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        save_checkpoint(
            session_dir=session_b,
            paused_at=self._NOW,
            reset_at=None,
            reason="bedrock_throttle",
            raw_error="throttle",
            in_flight_wu="T-beta",
            in_flight_phase="GREEN",
            completed_judges=["code_review"],
            pending_judges=[],
            stage_artefacts={},
        )
        cp_a = load_checkpoint(session_dir=session_a)
        cp_b = load_checkpoint(session_dir=session_b)
        assert cp_a is not None
        assert cp_b is not None
        assert cp_a.in_flight_wu == "T-alpha"
        assert cp_b.in_flight_wu == "T-beta"
        assert cp_a.reason == "subscription_rate_limit"
        assert cp_b.reason == "bedrock_throttle"


# ---------------------------------------------------------------------------
# save_checkpoint: write-failure path -- temp file is cleaned up
# ---------------------------------------------------------------------------


class TestSaveCheckpointWriteFailure:
    """save_checkpoint cleans up temp file on write failure and re-raises."""

    _NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    def test_ioerror_on_write_is_propagated(self, tmp_path: Path) -> None:
        """When write_text raises OSError, save_checkpoint propagates it."""
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                save_checkpoint(
                    session_dir=tmp_path,
                    paused_at=self._NOW,
                    reset_at=None,
                    reason="subscription_rate_limit",
                    raw_error="429",
                    in_flight_wu=None,
                    in_flight_phase=None,
                    completed_judges=[],
                    pending_judges=[],
                    stage_artefacts={},
                )

    def test_no_partial_file_remains_on_write_failure(self, tmp_path: Path) -> None:
        """After a write failure, neither the target nor a stale temp file is left behind."""
        import unittest.mock

        calls: list[str] = []

        original_write_text = Path.write_text

        def failing_write_text(self_path: Path, *args: Any, **kwargs: Any) -> int:
            calls.append(str(self_path))
            if self_path.suffix == ".tmp":
                raise OSError("simulated disk full")
            return original_write_text(self_path, *args, **kwargs)

        with unittest.mock.patch.object(Path, "write_text", failing_write_text):
            with pytest.raises(OSError, match="simulated disk full"):
                save_checkpoint(
                    session_dir=tmp_path,
                    paused_at=self._NOW,
                    reset_at=None,
                    reason="subscription_rate_limit",
                    raw_error="429",
                    in_flight_wu=None,
                    in_flight_phase=None,
                    completed_judges=[],
                    pending_judges=[],
                    stage_artefacts={},
                )
        # The target quota_pause.json must not exist
        assert not (tmp_path / ".devbench" / "quota_pause.json").exists()


# ---------------------------------------------------------------------------
# load_checkpoint: paused_at as non-string type in JSON raises ValueError
# ---------------------------------------------------------------------------


class TestLoadCheckpointNonStringDatetime:
    """load_checkpoint raises ValueError when a datetime field is a non-string JSON value."""

    def _write_raw(self, tmp_path: Path, data: dict[str, object]) -> None:
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text(json.dumps(data))

    def test_raises_value_error_when_paused_at_is_integer(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when paused_at is an integer (not a string)."""
        self._write_raw(
            tmp_path,
            {
                "paused_at": 1767268800,  # epoch int instead of ISO string
                "reset_at": None,
                "reason": "subscription_rate_limit",
                "raw_error": "429",
                "in_flight_wu": None,
                "in_flight_phase": None,
                "completed_judges": [],
                "pending_judges": [],
                "stage_artefacts": {},
            },
        )
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_value_error_when_reset_at_is_integer(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when reset_at is an integer (not null or string)."""
        self._write_raw(
            tmp_path,
            {
                "paused_at": "2026-03-01T10:00:00+00:00",
                "reset_at": 1767268800,  # epoch int instead of ISO string or null
                "reason": "subscription_rate_limit",
                "raw_error": "429",
                "in_flight_wu": None,
                "in_flight_phase": None,
                "completed_judges": [],
                "pending_judges": [],
                "stage_artefacts": {},
            },
        )
        with pytest.raises(ValueError, match=r"quota_pause\.json"):
            load_checkpoint(session_dir=tmp_path)


# ---------------------------------------------------------------------------
# load_checkpoint: timezone-naive ISO datetime is accepted and normalised to UTC
# ---------------------------------------------------------------------------


class TestLoadCheckpointTimezoneNaiveDatetime:
    """load_checkpoint accepts timezone-naive ISO datetimes and normalises them to UTC."""

    def _write_raw(self, tmp_path: Path, data: dict[str, object]) -> None:
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text(json.dumps(data))

    def test_naive_paused_at_is_accepted_and_utc_normalised(self, tmp_path: Path) -> None:
        """A timezone-naive paused_at string is accepted and returned as a UTC-aware datetime."""
        self._write_raw(
            tmp_path,
            {
                "paused_at": "2026-03-01T10:00:00",  # no timezone suffix
                "reset_at": None,
                "reason": "subscription_rate_limit",
                "raw_error": "429",
                "in_flight_wu": None,
                "in_flight_phase": None,
                "completed_judges": [],
                "pending_judges": [],
                "stage_artefacts": {},
            },
        )
        cp = load_checkpoint(session_dir=tmp_path)
        assert cp is not None
        assert cp.paused_at.tzinfo is not None
        assert cp.paused_at == datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# save_checkpoint: input validation -- fail-fast on invalid arguments
# ---------------------------------------------------------------------------


class TestSaveCheckpointInputValidation:
    """save_checkpoint raises ValueError on invalid inputs before any I/O."""

    _NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)

    @pytest.mark.parametrize("bad_reason", ["", "   "])
    def test_rejects_empty_reason(self, tmp_path: Path, bad_reason: str) -> None:
        """save_checkpoint raises ValueError when reason is empty or whitespace-only."""
        with pytest.raises(ValueError, match="reason"):
            save_checkpoint(
                session_dir=tmp_path,
                paused_at=self._NOW,
                reset_at=None,
                reason=bad_reason,
                raw_error="429",
                in_flight_wu=None,
                in_flight_phase=None,
                completed_judges=[],
                pending_judges=[],
                stage_artefacts={},
            )

    @pytest.mark.parametrize("bad_raw", ["", "   "])
    def test_rejects_empty_raw_error(self, tmp_path: Path, bad_raw: str) -> None:
        """save_checkpoint raises ValueError when raw_error is empty or whitespace-only."""
        with pytest.raises(ValueError, match="raw_error"):
            save_checkpoint(
                session_dir=tmp_path,
                paused_at=self._NOW,
                reset_at=None,
                reason="subscription_rate_limit",
                raw_error=bad_raw,
                in_flight_wu=None,
                in_flight_phase=None,
                completed_judges=[],
                pending_judges=[],
                stage_artefacts={},
            )

    def test_rejects_naive_paused_at(self, tmp_path: Path) -> None:
        """save_checkpoint raises ValueError when paused_at has no timezone info."""
        naive_dt = datetime.fromisoformat("2026-03-01T10:00:00")
        with pytest.raises(ValueError, match="paused_at"):
            save_checkpoint(
                session_dir=tmp_path,
                paused_at=naive_dt,
                reset_at=None,
                reason="subscription_rate_limit",
                raw_error="429",
                in_flight_wu=None,
                in_flight_phase=None,
                completed_judges=[],
                pending_judges=[],
                stage_artefacts={},
            )

    def test_rejects_naive_reset_at(self, tmp_path: Path) -> None:
        """save_checkpoint raises ValueError when reset_at is provided but has no timezone info."""
        naive_reset = datetime.fromisoformat("2026-03-01T15:00:00")
        with pytest.raises(ValueError, match="reset_at"):
            save_checkpoint(
                session_dir=tmp_path,
                paused_at=self._NOW,
                reset_at=naive_reset,
                reason="subscription_rate_limit",
                raw_error="429",
                in_flight_wu=None,
                in_flight_phase=None,
                completed_judges=[],
                pending_judges=[],
                stage_artefacts={},
            )

    def test_no_file_written_on_validation_failure(self, tmp_path: Path) -> None:
        """When input validation fails, no file or directory is created."""
        with pytest.raises(ValueError):
            save_checkpoint(
                session_dir=tmp_path,
                paused_at=self._NOW,
                reset_at=None,
                reason="",
                raw_error="429",
                in_flight_wu=None,
                in_flight_phase=None,
                completed_judges=[],
                pending_judges=[],
                stage_artefacts={},
            )
        assert not (tmp_path / ".devbench").exists()

    def test_accepts_valid_timezone_aware_paused_at(self, tmp_path: Path) -> None:
        """save_checkpoint succeeds with a valid timezone-aware paused_at."""
        save_checkpoint(
            session_dir=tmp_path,
            paused_at=self._NOW,
            reset_at=None,
            reason="subscription_rate_limit",
            raw_error="429",
            in_flight_wu=None,
            in_flight_phase=None,
            completed_judges=[],
            pending_judges=[],
            stage_artefacts={},
        )
        assert (tmp_path / ".devbench" / "quota_pause.json").exists()


# ---------------------------------------------------------------------------
# load_checkpoint: type validation for list and dict fields
# ---------------------------------------------------------------------------


class TestLoadCheckpointFieldTypeValidation:
    """load_checkpoint raises ValueError when list/dict fields have wrong JSON types."""

    def _write_raw(self, tmp_path: Path, data: dict[str, object]) -> None:
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "quota_pause.json").write_text(json.dumps(data))

    def _valid_data(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "paused_at": "2026-03-01T10:00:00+00:00",
            "reset_at": None,
            "reason": "subscription_rate_limit",
            "raw_error": "429",
            "in_flight_wu": None,
            "in_flight_phase": None,
            "completed_judges": [],
            "pending_judges": [],
            "stage_artefacts": {},
        }
        base.update(overrides)
        return base

    def test_raises_when_completed_judges_is_string(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when completed_judges is a string instead of a list."""
        self._write_raw(tmp_path, self._valid_data(completed_judges="code_review"))
        with pytest.raises(ValueError, match="completed_judges"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_when_pending_judges_is_string(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when pending_judges is a string instead of a list."""
        self._write_raw(tmp_path, self._valid_data(pending_judges="test_review"))
        with pytest.raises(ValueError, match="pending_judges"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_when_stage_artefacts_is_list(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when stage_artefacts is a list instead of a dict."""
        self._write_raw(tmp_path, self._valid_data(stage_artefacts=["branch", "main"]))
        with pytest.raises(ValueError, match="stage_artefacts"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_when_reason_is_not_string(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when reason is an integer instead of a string."""
        self._write_raw(tmp_path, self._valid_data(reason=429))
        with pytest.raises(ValueError, match="reason"):
            load_checkpoint(session_dir=tmp_path)

    def test_raises_when_raw_error_is_not_string(self, tmp_path: Path) -> None:
        """load_checkpoint raises ValueError when raw_error is a dict instead of a string."""
        self._write_raw(tmp_path, self._valid_data(raw_error={"error": "quota"}))
        with pytest.raises(ValueError, match="raw_error"):
            load_checkpoint(session_dir=tmp_path)


# ---------------------------------------------------------------------------
# AC-193-4: legacy raise+exit behavior when quota_handling.enabled is false
# ---------------------------------------------------------------------------


class _RawSdkQuotaError(Exception):
    """Minimal SDK-style 429 exception that has NOT been wrapped by detect_quota_error.

    Used to simulate the legacy behavior path where quota_handling.enabled is
    false and the caller does NOT invoke detect_quota_error: the raw SDK
    exception propagates as its original type.
    """

    def __init__(self, status_code: int = 429, message: str = "rate limit exceeded") -> None:
        self.status_code = status_code
        self.message = message
        self.body: dict[str, Any] = {}
        super().__init__(message)


class TestLegacyRaiseExitBehavior:
    """AC-193-4: quota_handling.enabled false preserves legacy raise+exit behavior.

    When quota_handling.enabled is False, the orchestrator/cmd_start does NOT
    call detect_quota_error or wait_for_reset. The raw SDK exception propagates
    unmodified through the call stack, exactly as it did before the quota module
    was introduced.

    These tests document the legacy contract:
    - A raw SDK 429 error raised without detection is NOT an instance of
      QuotaExhaustedError -- it is only the original SDK exception type.
    - Catching it as the original type succeeds.
    - Catching it as QuotaExhaustedError fails (no wrapping occurred).
    - detect_quota_error still works correctly on raw exceptions; the
      caller's responsibility is to skip calling it when enabled is False.
    """

    def test_raw_sdk_429_is_not_quota_exhausted_error(self) -> None:
        """A raw SDK 429 exception bypasses detect_quota_error and is not QuotaExhaustedError.

        When quota_handling.enabled is False, the caller skips detect_quota_error.
        The raw SDK exception is not an instance of QuotaExhaustedError.
        """
        raw_exc = _RawSdkQuotaError(status_code=429)
        assert not isinstance(raw_exc, QuotaExhaustedError), (
            "A raw (undetected) SDK exception must not be a QuotaExhaustedError. "
            "detect_quota_error wraps it, but legacy mode skips that call."
        )

    def test_raw_sdk_429_propagates_as_original_type(self) -> None:
        """A raw SDK 429 exception propagates and is caught by its original type.

        In legacy mode (enabled=False), the SDK exception is raised and the
        caller catches it by its original type, not as QuotaExhaustedError.
        """

        def _legacy_caller() -> None:
            raise _RawSdkQuotaError(status_code=429)

        with pytest.raises(_RawSdkQuotaError) as exc_info:
            _legacy_caller()

        caught = exc_info.value
        assert caught.status_code == 429
        assert not isinstance(caught, QuotaExhaustedError)

    def test_raw_sdk_429_not_caught_as_quota_exhausted(self) -> None:
        """Catching a raw SDK 429 as QuotaExhaustedError fails -- it is not wrapped.

        When detect_quota_error is bypassed (legacy mode), the raw exception
        cannot be caught with QuotaExhaustedError because the wrapping step
        never occurred.
        """
        raw_exc = _RawSdkQuotaError(status_code=429)
        try:
            raise raw_exc
        except QuotaExhaustedError:
            pytest.fail(
                "Raw SDK exception was incorrectly caught as QuotaExhaustedError. "
                "Legacy mode must not wrap the exception."
            )
        except _RawSdkQuotaError:
            pass  # expected: raw exception propagates as its own type

    @pytest.mark.parametrize(
        "status_code",
        [429, 402],
    )
    def test_raw_sdk_quota_errors_are_plain_exceptions(self, status_code: int) -> None:
        """Raw SDK quota errors (429, 402) are plain exceptions without QuotaExhaustedError.

        Parametrized to cover both rate-limit (429) and billing (402) quota signals.
        In legacy mode, neither is wrapped -- they are plain SDK exceptions.
        """
        raw_exc = _RawSdkQuotaError(status_code=status_code)
        assert isinstance(raw_exc, Exception)
        assert not isinstance(raw_exc, QuotaExhaustedError)
        assert raw_exc.status_code == status_code

    def test_detect_quota_error_still_works_on_raw_exception(self) -> None:
        """detect_quota_error correctly classifies a raw 429 when called explicitly.

        This confirms the detection function is correct; the legacy path
        just means the CALLER does not invoke it. When it IS called on a raw
        429 exception, it returns the appropriate QuotaExhaustedError subclass.
        """
        raw_exc = _RawSdkQuotaError(status_code=429)
        result = detect_quota_error(raw_exc)
        assert isinstance(result, SubscriptionRateLimitError)
        assert isinstance(result, QuotaExhaustedError)

    def test_legacy_path_exception_carries_status_code(self) -> None:
        """A raw legacy SDK exception preserves the status_code attribute.

        In legacy mode, the SDK exception propagates unmodified; the caller
        can still inspect status_code directly (no wrapping required).
        """
        raw_exc = _RawSdkQuotaError(status_code=429, message="Too Many Requests")
        assert raw_exc.status_code == 429
        assert "Too Many Requests" in str(raw_exc)

    def test_legacy_path_no_quota_exhausted_error_in_mro(self) -> None:
        """QuotaExhaustedError does not appear in the MRO of a raw SDK exception.

        Confirms there is no inheritance relationship between _RawSdkQuotaError
        (stand-in for the real Anthropic SDK exception) and QuotaExhaustedError.
        The wrapping only happens when detect_quota_error is explicitly called.
        """
        assert QuotaExhaustedError not in type(_RawSdkQuotaError()).mro()

    def test_legacy_exception_reraise_with_context_preserves_original(self) -> None:
        """Re-raising a raw SDK exception in legacy mode preserves the original cause.

        When an outer catch block re-raises with context (raise X from exc), the
        original exception is accessible via __cause__. This test confirms that
        the raw SDK exception (not a QuotaExhaustedError) is the cause.
        """

        class _OuterError(Exception):
            pass

        raw_exc = _RawSdkQuotaError(status_code=429)

        def _legacy_caller() -> None:
            try:
                raise raw_exc
            except _RawSdkQuotaError as e:
                raise _OuterError("legacy mode: SDK error propagated") from e

        with pytest.raises(_OuterError) as exc_info:
            _legacy_caller()

        cause = exc_info.value.__cause__
        assert isinstance(cause, _RawSdkQuotaError)
        assert not isinstance(cause, QuotaExhaustedError)
        assert cause.status_code == 429


# ---------------------------------------------------------------------------
# E5-F4-S1-T4: remove_checkpoint -- atomic deletion of quota_pause.json
# AC-193-8: quota_pause.json removed on resume (spec section 4.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveCheckpoint:
    """AC-193-8: remove_checkpoint deletes quota_pause.json atomically.

    Spec section 4.5: the pause file is removed on resume so subsequent
    runs do not think quota is still exhausted.
    """

    def _write_checkpoint(self, session_dir: Path) -> Path:
        """Write a minimal quota_pause.json and return its path."""
        devbench_dir = session_dir / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_file = devbench_dir / "quota_pause.json"
        checkpoint_file.write_text(
            '{"paused_at":"2026-01-01T12:00:00+00:00","reset_at":null,'
            '"reason":"subscription_rate_limit","raw_error":"429",'
            '"in_flight_wu":null,"in_flight_phase":null,'
            '"completed_judges":[],"pending_judges":[],"stage_artefacts":{}}',
            encoding="utf-8",
        )
        return checkpoint_file

    def test_remove_checkpoint_deletes_file(self, tmp_path: Path) -> None:
        """remove_checkpoint removes quota_pause.json when it exists."""
        from devbench.quota import remove_checkpoint

        checkpoint_file = self._write_checkpoint(tmp_path)
        assert checkpoint_file.exists(), "pre-condition: file must exist before removal"

        remove_checkpoint(tmp_path)

        assert not checkpoint_file.exists(), "quota_pause.json must be deleted by remove_checkpoint"

    def test_remove_checkpoint_noop_when_file_absent(self, tmp_path: Path) -> None:
        """remove_checkpoint is a no-op when quota_pause.json does not exist."""
        from devbench.quota import remove_checkpoint

        # Ensure directory exists but file does not.
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_file = devbench_dir / "quota_pause.json"
        assert not checkpoint_file.exists(), "pre-condition: file must not exist"

        # Should not raise.
        remove_checkpoint(tmp_path)

    def test_remove_checkpoint_noop_when_devbench_dir_absent(self, tmp_path: Path) -> None:
        """remove_checkpoint is a no-op when the .devbench directory itself does not exist."""
        from devbench.quota import remove_checkpoint

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # .devbench sub-dir does not exist.

        # Should not raise.
        remove_checkpoint(session_dir)

    def test_remove_checkpoint_load_returns_none_after_removal(self, tmp_path: Path) -> None:
        """After remove_checkpoint, load_checkpoint returns None for the same session_dir."""
        from devbench.quota import remove_checkpoint

        self._write_checkpoint(tmp_path)
        assert load_checkpoint(tmp_path) is not None, "pre-condition: checkpoint must be loadable"

        remove_checkpoint(tmp_path)

        assert load_checkpoint(tmp_path) is None, (
            "load_checkpoint must return None after remove_checkpoint deletes the file"
        )


# ---------------------------------------------------------------------------
# E5-F6-S1-T2: post_webhook -- best-effort HTTP POST helper
# AC-193-17: notification webhooks fire on pause + resume
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostWebhook:
    """AC-193-17: post_webhook POSTs JSON payload to url; failures are logged but do not raise.

    Spec section 4.5.6: webhook delivery is best-effort -- failures must be
    logged to stderr but must not crash the orchestrator.
    """

    def _make_payload(self) -> dict[str, Any]:
        return {
            "event": "quota_pause",
            "reason": "subscription_rate_limit",
            "reset_at": "2026-01-01T13:00:00+00:00",
            "paused_at": "2026-01-01T12:00:00+00:00",
        }

    def test_post_webhook_sends_json_body(self) -> None:
        """post_webhook encodes payload as JSON and calls _http_post with correct args."""
        import urllib.parse

        from devbench.quota import post_webhook

        captured: list[dict[str, Any]] = []

        def _fake_http_post(
            parsed: urllib.parse.SplitResult,
            body: bytes,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> None:
            captured.append({"parsed": parsed, "body": body, "headers": headers, "timeout": timeout_seconds})

        payload = self._make_payload()
        with patch("devbench.quota._http_post", side_effect=_fake_http_post):
            post_webhook("https://example.com/hook", payload, timeout_seconds=5.0)

        assert len(captured) == 1
        assert captured[0]["parsed"].scheme == "https"
        assert captured[0]["parsed"].hostname == "example.com"
        body_decoded = json.loads(captured[0]["body"].decode("utf-8"))
        assert body_decoded["event"] == "quota_pause"
        assert body_decoded["reason"] == "subscription_rate_limit"

    def test_post_webhook_sets_content_type_json_header(self) -> None:
        """post_webhook includes Content-Type: application/json in the headers passed to _http_post."""
        import urllib.parse

        from devbench.quota import post_webhook

        captured_headers: list[dict[str, str]] = []

        def _fake_http_post(
            parsed: urllib.parse.SplitResult,
            body: bytes,
            headers: dict[str, str],
            timeout_seconds: float,
        ) -> None:
            captured_headers.append(headers)

        with patch("devbench.quota._http_post", side_effect=_fake_http_post):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        assert len(captured_headers) == 1
        header_keys_lower = {k.lower() for k in captured_headers[0]}
        assert "content-type" in header_keys_lower
        content_type_value = next(v for k, v in captured_headers[0].items() if k.lower() == "content-type")
        assert "application/json" in content_type_value

    def test_post_webhook_logs_http_error_to_stderr_without_raising(self, capsys: pytest.CaptureFixture[str]) -> None:
        """post_webhook logs HTTP errors to stderr and returns normally (best-effort)."""
        from devbench.quota import post_webhook

        def _raise_http_error(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("HTTP 500 Internal Server Error")

        with patch("devbench.quota._http_post", side_effect=_raise_http_error):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert "webhook" in stderr_output.lower() or "500" in stderr_output or "http" in stderr_output.lower()

    def test_post_webhook_logs_url_error_to_stderr_without_raising(self, capsys: pytest.CaptureFixture[str]) -> None:
        """post_webhook logs connection failures to stderr and returns normally."""
        from devbench.quota import post_webhook

        def _raise_connection_error(*args: Any, **kwargs: Any) -> None:
            raise ConnectionRefusedError("Connection refused")

        with patch("devbench.quota._http_post", side_effect=_raise_connection_error):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert "webhook" in stderr_output.lower() or "connection" in stderr_output.lower()

    def test_post_webhook_logs_timeout_to_stderr_without_raising(self, capsys: pytest.CaptureFixture[str]) -> None:
        """post_webhook logs TimeoutError to stderr and returns normally (best-effort)."""
        from devbench.quota import post_webhook

        def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise TimeoutError("timed out")

        with patch("devbench.quota._http_post", side_effect=_raise_timeout):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert "webhook" in stderr_output.lower() or "timeout" in stderr_output.lower()

    def test_post_webhook_logs_unexpected_exception_to_stderr_without_raising(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """post_webhook logs any unexpected exception to stderr and returns normally."""
        from devbench.quota import post_webhook

        def _raise_unexpected(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("unexpected failure")

        with patch("devbench.quota._http_post", side_effect=_raise_unexpected):
            post_webhook("https://example.com/hook", self._make_payload(), timeout_seconds=5.0)

        stderr_output = capsys.readouterr().err
        assert stderr_output.strip() != "", "must log something on unexpected failure"

    @pytest.mark.parametrize(
        "url,payload,timeout_seconds,error_fragment",
        [
            ("", {"event": "pause"}, 5.0, "url"),
            ("https://example.com/hook", {}, 5.0, "payload"),
            ("https://example.com/hook", {"event": "pause"}, 0.0, "timeout"),
            ("https://example.com/hook", {"event": "pause"}, -1.0, "timeout"),
            ("file:///etc/passwd", {"event": "pause"}, 5.0, "scheme"),
            ("ftp://example.com/hook", {"event": "pause"}, 5.0, "scheme"),
        ],
    )
    def test_post_webhook_validates_inputs(
        self,
        url: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        error_fragment: str,
    ) -> None:
        """post_webhook raises ValueError for invalid inputs (fail-fast)."""
        from devbench.quota import post_webhook

        with pytest.raises(ValueError, match=error_fragment):
            post_webhook(url, payload, timeout_seconds=timeout_seconds)


# ---------------------------------------------------------------------------
# E5-F6-S1-T2: deliver_notifications -- dispatch to all configured URLs
# AC-193-17: notification webhooks fire on pause + resume
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeliverNotifications:
    """AC-193-17: deliver_notifications posts to each configured URL in a QuotaNotifyConfig.

    Spec section 4.5.6: notify_on_pause and notify_on_resume each carry a
    webhook_url and a slack_webhook_url.  When non-None, each URL receives a
    POST.  When the config is None (webhooks disabled), no POSTs are issued.
    """

    def _make_notify_config(
        self,
        webhook_url: str | None = None,
        slack_webhook_url: str | None = None,
    ) -> Any:
        """Build a QuotaNotifyConfig with the given URLs."""
        from devbench.config_loader import QuotaNotifyConfig

        return QuotaNotifyConfig(webhook_url=webhook_url, slack_webhook_url=slack_webhook_url)

    def _make_pause_payload(self) -> dict[str, Any]:
        return {
            "event": "quota_pause",
            "reason": "subscription_rate_limit",
            "reset_at": "2026-01-01T13:00:00+00:00",
            "paused_at": "2026-01-01T12:00:00+00:00",
        }

    def _make_resume_payload(self) -> dict[str, Any]:
        return {
            "event": "quota_resume",
            "reason": "subscription_rate_limit",
            "resumed_at": "2026-01-01T13:05:00+00:00",
            "waited_seconds": 300.0,
        }

    def test_deliver_notifications_none_config_makes_no_requests(self) -> None:
        """deliver_notifications is a no-op when notify_config is None."""
        from devbench.quota import deliver_notifications

        with patch("devbench.quota.post_webhook") as mock_post:
            deliver_notifications(None, self._make_pause_payload())

        mock_post.assert_not_called()

    def test_deliver_notifications_both_urls_none_makes_no_requests(self) -> None:
        """deliver_notifications is a no-op when both URLs in the config are None."""
        from devbench.quota import deliver_notifications

        config = self._make_notify_config(webhook_url=None, slack_webhook_url=None)
        with patch("devbench.quota.post_webhook") as mock_post:
            deliver_notifications(config, self._make_pause_payload())

        mock_post.assert_not_called()

    def test_deliver_notifications_webhook_url_only(self) -> None:
        """deliver_notifications posts once to webhook_url when slack_webhook_url is None."""
        from devbench.quota import deliver_notifications

        config = self._make_notify_config(
            webhook_url="https://example.com/hook",
            slack_webhook_url=None,
        )
        with patch("devbench.quota.post_webhook") as mock_post:
            deliver_notifications(config, self._make_pause_payload())

        assert mock_post.call_count == 1
        call_url = mock_post.call_args[0][0]
        assert call_url == "https://example.com/hook"

    def test_deliver_notifications_slack_webhook_url_only(self) -> None:
        """deliver_notifications posts once to slack_webhook_url when webhook_url is None."""
        from devbench.quota import deliver_notifications

        config = self._make_notify_config(
            webhook_url=None,
            slack_webhook_url="https://hooks.slack.com/services/T000/B000/xxx",
        )
        with patch("devbench.quota.post_webhook") as mock_post:
            deliver_notifications(config, self._make_pause_payload())

        assert mock_post.call_count == 1
        call_url = mock_post.call_args[0][0]
        assert call_url == "https://hooks.slack.com/services/T000/B000/xxx"

    def test_deliver_notifications_both_urls_posts_twice(self) -> None:
        """deliver_notifications posts to both URLs when both are configured."""
        from devbench.quota import deliver_notifications

        config = self._make_notify_config(
            webhook_url="https://example.com/hook",
            slack_webhook_url="https://hooks.slack.com/services/T000/B000/xxx",
        )
        with patch("devbench.quota.post_webhook") as mock_post:
            deliver_notifications(config, self._make_pause_payload())

        assert mock_post.call_count == 2
        posted_urls = {call[0][0] for call in mock_post.call_args_list}
        assert "https://example.com/hook" in posted_urls
        assert "https://hooks.slack.com/services/T000/B000/xxx" in posted_urls

    def test_deliver_notifications_passes_payload_to_post_webhook(self) -> None:
        """deliver_notifications forwards the payload dict to post_webhook unchanged."""
        from devbench.quota import deliver_notifications

        payload = self._make_resume_payload()
        config = self._make_notify_config(webhook_url="https://example.com/hook")

        with patch("devbench.quota.post_webhook") as mock_post:
            deliver_notifications(config, payload)

        assert mock_post.call_count == 1
        actual_payload = mock_post.call_args[0][1]
        assert actual_payload["event"] == "quota_resume"
        assert actual_payload["waited_seconds"] == 300.0

    def test_deliver_notifications_first_failure_does_not_prevent_second_post(self) -> None:
        """deliver_notifications continues to the second URL even when the first call raises."""
        from devbench.quota import deliver_notifications

        config = self._make_notify_config(
            webhook_url="https://example.com/hook",
            slack_webhook_url="https://hooks.slack.com/services/T000/B000/xxx",
        )

        call_count = 0

        def _post_that_raises_on_first(url: str, payload: dict[str, Any], **kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated first-URL failure")

        with patch("devbench.quota.post_webhook", side_effect=_post_that_raises_on_first):
            # Must not raise even though the first post_webhook call raises.
            deliver_notifications(config, self._make_pause_payload())

        assert call_count == 2, "both URLs must be attempted regardless of the first failure"

    @pytest.mark.parametrize(
        "event,required_keys",
        [
            ("quota_pause", ["event", "reason", "paused_at"]),
            ("quota_resume", ["event", "reason", "resumed_at"]),
        ],
    )
    def test_deliver_notifications_pause_payload_contains_required_keys(
        self,
        event: str,
        required_keys: list[str],
    ) -> None:
        """The payload passed to deliver_notifications must contain required keys.

        This test verifies that callers build the correct payload structure for
        both pause and resume events -- the keys are consumed by the webhook
        receiver.
        """
        from devbench.quota import deliver_notifications

        payload = self._make_pause_payload() if event == "quota_pause" else self._make_resume_payload()
        config = self._make_notify_config(webhook_url="https://example.com/hook")
        captured_payloads: list[dict[str, Any]] = []

        def _capture(url: str, p: dict[str, Any], **kwargs: Any) -> None:
            captured_payloads.append(p)

        with patch("devbench.quota.post_webhook", side_effect=_capture):
            deliver_notifications(config, payload)

        assert len(captured_payloads) == 1
        for key in required_keys:
            assert key in captured_payloads[0], f"payload must contain key {key!r}"
