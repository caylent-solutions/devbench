"""Tests for devbench.quota -- exception hierarchy.

Covers: QuotaExhaustedError, SubscriptionRateLimitError, SdkCreditExhaustedError,
ApiBillingError, BedrockThrottleError.  Each exception carries reset_at,
raw_error and source fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from devbench.quota import (
    ApiBillingError,
    BedrockThrottleError,
    QuotaExhaustedError,
    SdkCreditExhaustedError,
    SubscriptionRateLimitError,
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
