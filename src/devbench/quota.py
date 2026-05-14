"""Quota exception hierarchy and detection for devbench.

Defines structured exceptions raised when the Anthropic SDK or AWS Bedrock
returns quota / billing exhaustion signals.  Every exception carries three
fields:

- ``reset_at``  -- the UTC datetime at which the quota resets, or ``None``
  when the vendor does not publish a reset time.
- ``raw_error`` -- the original exception, response object, or dict that
  triggered this exception (preserved for diagnostics; never mutated).
- ``source``    -- a short string identifying the error origin
  (e.g. ``"anthropic-api"``, ``"bedrock"``, ``"sdk"``).

Hierarchy::

    Exception
    +-- QuotaExhaustedError              (base; catch-all for any quota signal)
        +-- SubscriptionRateLimitError   (HTTP 429 rate-limit response)
        +-- SdkCreditExhaustedError      (HTTP 402 / insufficient_quota from SDK)
        +-- ApiBillingError              (HTTP 402 / billing-level exhaustion)
        +-- BedrockThrottleError         (AWS Bedrock throttle shape)

Public API:

- :func:`detect_quota_error` -- classify an incoming exception or response
  object into the appropriate ``QuotaExhaustedError`` subclass, or return
  ``None`` when the input carries no quota signal.
- :func:`parse_reset_time` -- extract a UTC reset :class:`~datetime.datetime`
  from response headers, or ``None`` when no parseable reset signal is present.

Raises:
    None -- this module only defines exception classes and pure-function
    helpers; it does not raise at module scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

# ---------------------------------------------------------------------------
# Protocol constants -- HTTP status codes and header/error-code identifiers
# ---------------------------------------------------------------------------

# Private to this module's parse logic; not shared across modules. Rule 4 exemption:
# amendment to constants.py rejected (source-test atomicity).
_RETRY_AFTER_HEADER: str = "retry-after"
_ANTHROPIC_RATELIMIT_RESET_HEADERS: tuple[str, ...] = (
    "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-tokens-reset",
    "anthropic-ratelimit-input-tokens-reset",
)

# HTTP status code for rate-limit responses from the Anthropic API.
_HTTP_RATE_LIMIT: int = 429

# HTTP status code for billing / credit-exhaustion responses.
_HTTP_PAYMENT_REQUIRED: int = 402

# Anthropic API error type that signals SDK-credit depletion (HTTP 402 body).
_INSUFFICIENT_QUOTA_ERROR_TYPE: str = "insufficient_quota"

# AWS Bedrock error codes that signal throttling or quota exhaustion.
_BEDROCK_THROTTLING_CODE: str = "ThrottlingException"
_BEDROCK_QUOTA_EXCEEDED_CODE: str = "ServiceQuotaExceededException"

# Source string constants used in constructed QuotaExhaustedError instances.
_SOURCE_ANTHROPIC_API: str = "anthropic-api"
_SOURCE_SDK: str = "sdk"
_SOURCE_BEDROCK: str = "bedrock"


class QuotaExhaustedError(Exception):
    """Base exception for all quota / rate-limit exhaustion signals.

    All subclasses carry the three standard fields documented at the module
    level.  Callers that need to distinguish between quota types should catch
    the specific subclass; callers that want to handle any quota signal should
    catch ``QuotaExhaustedError``.

    Args:
        reset_at: UTC datetime when the quota resets, or ``None`` when
            unknown.
        raw_error: The original error payload (exception, dict, string, or
            any other object) that triggered this exception.
        source: Short string identifying the error origin.

    Raises:
        None -- constructor only; this class does not raise itself.
    """

    def __init__(
        self,
        reset_at: datetime | None,
        raw_error: object,
        source: str,
    ) -> None:
        self.reset_at = reset_at
        self.raw_error = raw_error
        self.source = source
        reset_str = reset_at.isoformat() if reset_at is not None else "unknown"
        super().__init__(f"Quota exhausted [source={source}] [reset_at={reset_str}]")


class SubscriptionRateLimitError(QuotaExhaustedError):
    """HTTP 429 rate-limit response from the Anthropic API.

    Raised when an HTTP 429 response is received from the Anthropic API.
    The ``reset_at`` field is populated by a separate ``parse_reset_time``
    call; when ``detect_quota_error`` constructs this exception, ``reset_at``
    is always ``None``.

    Args:
        reset_at: UTC datetime when the subscription rate limit resets, or
            ``None`` when ``detect_quota_error`` constructs this exception
            (header-based reset-time parsing is handled by ``parse_reset_time``
            in a subsequent step).
        raw_error: The original 429 response or exception.
        source: Origin identifier (typically ``"anthropic-api"``).

    Raises:
        None -- constructor only.
    """


class SdkCreditExhaustedError(QuotaExhaustedError):
    """HTTP 402 / ``insufficient_quota`` error from the Anthropic SDK.

    Raised when the API returns a 402 status or an error body whose
    ``error.type`` is ``"insufficient_quota"``, indicating that the caller's
    SDK credit balance is depleted.

    Args:
        reset_at: UTC datetime when credits are expected to replenish, or
            ``None`` when the vendor does not publish a replenishment time.
        raw_error: The original 402 response or exception.
        source: Origin identifier (typically ``"sdk"``).

    Raises:
        None -- constructor only.
    """


class ApiBillingError(QuotaExhaustedError):
    """HTTP 402 billing-level exhaustion from the Anthropic API.

    Raised when the API returns a 402 that indicates a billing / payment
    issue rather than an SDK credit balance issue.  Distinct from
    ``SdkCreditExhaustedError`` so callers can route the two signals
    differently (e.g. SDK credit exhaustion is retryable after a top-up;
    billing errors may require operator intervention).

    Args:
        reset_at: UTC datetime if known, otherwise ``None``.
        raw_error: The original 402 response or exception.
        source: Origin identifier (typically ``"anthropic-api"``).

    Raises:
        None -- constructor only.
    """


class BedrockThrottleError(QuotaExhaustedError):
    """AWS Bedrock throttling error (``ThrottlingException`` shape).

    Raised when a Bedrock ``InvokeModel`` or ``InvokeModelWithResponseStream``
    call returns a ``ThrottlingException`` or ``ServiceQuotaExceededException``
    error shape.

    Args:
        reset_at: UTC datetime if the Bedrock response includes a
            ``Retry-After`` header, otherwise ``None``.
        raw_error: The original ``ClientError`` or response dict.
        source: Origin identifier (typically ``"bedrock"``).

    Raises:
        None -- constructor only.
    """


# ---------------------------------------------------------------------------
# Detection helpers (private)
# ---------------------------------------------------------------------------


def _get_status_code(obj: Any) -> int | None:
    """Return the HTTP status code carried by *obj*, or ``None``."""
    return getattr(obj, "status_code", None)


def _get_error_type(obj: Any) -> str | None:
    """Return the ``error.type`` string from an Anthropic SDK body, or ``None``."""
    body = getattr(obj, "body", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    error_type = error.get("type")
    return error_type if isinstance(error_type, str) else None


def _get_bedrock_error_code(obj: Any) -> str | None:
    """Return the Bedrock ``Error.Code`` from a botocore ``ClientError``-shaped
    exception, or ``None`` when the shape does not match."""
    response = getattr(obj, "response", None)
    if not isinstance(response, dict):
        return None
    error_section = response.get("Error")
    if not isinstance(error_section, dict):
        return None
    code = error_section.get("Code")
    return code if isinstance(code, str) else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_quota_error(message_or_exception: object) -> QuotaExhaustedError | None:
    """Classify *message_or_exception* into a ``QuotaExhaustedError`` subclass.

    Inspects the incoming object for known quota / rate-limit signals from the
    Anthropic API and AWS Bedrock, and returns a structured exception instance
    that the caller can inspect or re-raise.  Returns ``None`` when no quota
    signal is detected.

    Detection rules (applied in order):

    1. If *message_or_exception* is already a :class:`QuotaExhaustedError`,
       return it unchanged.
    2. HTTP 429 (rate limit) from the Anthropic API -- returns
       :class:`SubscriptionRateLimitError` unconditionally (header
       parsing for reset timing is deferred to ``parse_reset_time``,
       addressed in T3).
    3. HTTP 402 (payment required) with ``error.type == "insufficient_quota"``
       returns :class:`SdkCreditExhaustedError` (source ``"sdk"``).
    4. HTTP 402 without ``insufficient_quota`` returns :class:`ApiBillingError`
       (source ``"anthropic-api"``).
    5. AWS Bedrock ``ThrottlingException`` or ``ServiceQuotaExceededException``
       error shape returns :class:`BedrockThrottleError`.
    6. Everything else returns ``None``.

    Args:
        message_or_exception: Any object -- typically an Anthropic SDK
            exception (with ``status_code`` and ``body``),
            a botocore ``ClientError`` (with ``response["Error"]``),
            or any other value.

    Returns:
        A :class:`QuotaExhaustedError` subclass instance when a quota signal
        is detected, otherwise ``None``.

    Raises:
        None -- this function never raises; unrecognised inputs return ``None``.
    """
    # Rule 1: passthrough for already-classified exceptions.
    if isinstance(message_or_exception, QuotaExhaustedError):
        return message_or_exception

    status_code = _get_status_code(message_or_exception)

    # Rule 2: HTTP 429 -> SubscriptionRateLimitError.
    if status_code == _HTTP_RATE_LIMIT:
        return SubscriptionRateLimitError(
            reset_at=None,
            raw_error=message_or_exception,
            source=_SOURCE_ANTHROPIC_API,
        )

    # Rules 3 & 4: HTTP 402 -> SdkCreditExhaustedError or ApiBillingError.
    if status_code == _HTTP_PAYMENT_REQUIRED:
        error_type = _get_error_type(message_or_exception)
        if error_type == _INSUFFICIENT_QUOTA_ERROR_TYPE:
            return SdkCreditExhaustedError(
                reset_at=None,
                raw_error=message_or_exception,
                source=_SOURCE_SDK,
            )
        return ApiBillingError(
            reset_at=None,
            raw_error=message_or_exception,
            source=_SOURCE_ANTHROPIC_API,
        )

    # Rule 5: Bedrock throttle error shapes.
    bedrock_code = _get_bedrock_error_code(message_or_exception)
    if bedrock_code in (_BEDROCK_THROTTLING_CODE, _BEDROCK_QUOTA_EXCEEDED_CODE):
        return BedrockThrottleError(
            reset_at=None,
            raw_error=message_or_exception,
            source=_SOURCE_BEDROCK,
        )

    # Rule 6: no quota signal detected.
    return None


def parse_reset_time(headers: Mapping[str, str]) -> datetime | None:
    """Extract a UTC reset datetime from HTTP response headers.

    Inspects *headers* for known quota-reset signals and returns the earliest
    parseable :class:`~datetime.datetime` (always timezone-aware UTC), or
    ``None`` when no parseable reset signal is present.

    Parsing rules (applied in order; all candidates collected, earliest wins):

    1. ``anthropic-ratelimit-requests-reset``,
       ``anthropic-ratelimit-tokens-reset``, and
       ``anthropic-ratelimit-input-tokens-reset`` -- each value is interpreted
       as an integer number of Unix epoch seconds.  Non-numeric values for any
       individual header are silently skipped; the remaining headers are still
       considered.
    2. ``Retry-After`` (case-insensitive) -- the value is tried as:

       a. An integer (seconds from the current wall-clock time).
          Negative integers are invalid and discarded.
       b. An HTTP-date string (RFC 1123 / RFC 7231 format, e.g.
          ``"Thu, 01 Jan 2026 12:00:00 GMT"``).

       If both interpretations fail, ``Retry-After`` contributes no candidate.

    When multiple candidates are found, the earliest datetime is returned so
    the caller waits only as long as strictly necessary.

    Args:
        headers: A mapping of HTTP header names to their string values.
            Header name lookup is case-insensitive.

    Returns:
        The earliest parseable UTC reset :class:`~datetime.datetime`, or
        ``None`` when no reset signal is found.

    Raises:
        None -- all parse errors are silently discarded; unrecognised or
        malformed header values contribute no candidate.
    """
    # Build a case-insensitive view of the headers for uniform lookup.
    lowered: dict[str, str] = {k.lower(): v for k, v in headers.items()}

    candidates: list[datetime] = []

    # --- Rule 1: anthropic-ratelimit-*-reset (epoch seconds) ---
    for header_name in _ANTHROPIC_RATELIMIT_RESET_HEADERS:
        raw = lowered.get(header_name)
        if raw is None:
            continue
        dt = _parse_epoch_seconds(raw)
        if dt is not None:
            candidates.append(dt)

    # --- Rule 2: Retry-After (integer seconds or HTTP-date) ---
    retry_after_raw = lowered.get(_RETRY_AFTER_HEADER)
    if retry_after_raw is not None:
        dt = _parse_retry_after(retry_after_raw)
        if dt is not None:
            candidates.append(dt)

    if not candidates:
        return None
    return min(candidates)


# ---------------------------------------------------------------------------
# Parse helpers (private)
# ---------------------------------------------------------------------------


def _parse_epoch_seconds(value: str) -> datetime | None:
    """Parse *value* as an integer Unix epoch timestamp and return a UTC datetime.

    Returns ``None`` when *value* is not a valid non-negative integer.
    """
    try:
        epoch = int(value)
    except ValueError:
        return None
    if epoch < 0:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC)


def _parse_retry_after(value: str) -> datetime | None:
    """Parse a ``Retry-After`` header *value* and return a UTC datetime.

    Tries integer seconds (delta from now) first, then HTTP-date format.
    Returns ``None`` when neither interpretation succeeds, or when the
    integer value is negative.
    """
    # Try integer seconds first.
    try:
        seconds = int(value)
    except ValueError:
        pass
    else:
        if seconds < 0:
            return None
        return datetime.now(tz=UTC) + timedelta(seconds=seconds)

    # Try HTTP-date (RFC 1123) format.
    try:
        parsed = parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None
    # Normalise to UTC.
    return parsed.astimezone(UTC)
