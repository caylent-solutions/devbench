"""Quota detection module for devbench.

Provides the ``QuotaExhaustedError`` exception hierarchy (base + four LSP
subclasses), the ``detect_quota_error`` dispatcher (ten ordered rules, never
raises), the ``_has_quota_marker`` CLI-byte substring scanner, and the
``_parse_reset_at_from_text`` reset-time parser.

Issue #234 (Appendix A QW-1 / QW-2 / QW-10).
AC-234-1, AC-234a-1.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

_QUOTA_MARKERS: tuple[str, ...] = (
    "You\u2019ve hit your limit",  # verbatim CLI line -- real Unicode apostrophe
    "You've hit your limit",
    "you've hit your limit",
    "You have hit your limit",
    "rate limit",
)

_BEDROCK_THROTTLE_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "ServiceQuotaExceededException",
    }
)

# Regex: matches "resets HH:MMam/pm (UTC)" -- case-insensitive meridiem.
# Captures: hour (1-12), minute (00-59), meridiem (am/pm/AM/PM).
# The (UTC) timezone label is required; any other label returns None.
_RESET_AT_RE = re.compile(
    r"resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)",
    re.IGNORECASE,
)


class QuotaExhaustedError(Exception):
    """Base class for all quota-exhaustion signals.

    Attributes:
        reset_at: UTC datetime at which the quota is expected to reset, or
                  None when the information is not available.
        raw_error: The original error object (any type) that triggered
                   quota detection.
        source: A short string identifying where the signal originated
                (e.g. ``"anthropic-api"``, ``"bedrock"``, ``"claude-code-cli"``).
    """

    def __init__(
        self,
        *,
        reset_at: datetime | None,
        raw_error: object,
        source: str,
    ) -> None:
        self.reset_at = reset_at
        self.raw_error = raw_error
        self.source = source
        reset_str = reset_at.isoformat() if reset_at is not None else "unknown"
        super().__init__(f"Quota exhausted [source={source}] [resets={reset_str}]")


class SubscriptionRateLimitError(QuotaExhaustedError):
    """Rate limit hit on the Anthropic API or Claude Code CLI subscription tier."""


class SdkCreditExhaustedError(QuotaExhaustedError):
    """API credit balance exhausted (HTTP 402 + insufficient_quota)."""


class ApiBillingError(QuotaExhaustedError):
    """Billing or payment error on the Anthropic API (HTTP 402, other type)."""


class BedrockThrottleError(QuotaExhaustedError):
    """Throttle or quota exceeded on AWS Bedrock."""


# ---------------------------------------------------------------------------
# Clock helper (injectable via mock in tests)
# ---------------------------------------------------------------------------


def _get_current_utc() -> datetime:
    """Return the current UTC datetime. Isolated for test-time mocking."""
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# CLI-byte substring scanner
# ---------------------------------------------------------------------------


def _has_quota_marker(text: object) -> bool:
    """Return True when ``text`` is a str containing a known quota marker.

    The scan is a simple substring search over the exact CLI byte sequences
    enumerated in ``_QUOTA_MARKERS``. Non-string input returns False without
    raising.
    """
    if not isinstance(text, str):
        return False
    return any(marker in text for marker in _QUOTA_MARKERS)


# ---------------------------------------------------------------------------
# Reset-time parser
# ---------------------------------------------------------------------------


def _convert_to_24h(raw_hour: int, meridiem: str) -> int:
    """Convert a 12-hour clock value to 24-hour. Assumes validated input (1-12)."""
    if meridiem == "am":
        return 0 if raw_hour == 12 else raw_hour
    return raw_hour if raw_hour == 12 else raw_hour + 12


def _parse_reset_at_from_text(text: object) -> datetime | None:
    """Parse the next-future UTC reset time from a CLI-emitted message.

    Accepts strings of the form ``"resets H:MMam/pm (UTC)"`` embedded
    anywhere in ``text``. Returns the next-future UTC-aware datetime whose
    hour/minute match the parsed value. If the parsed time is at or before
    the current clock, adds one day (next-day rollover).

    Returns None when:
    - ``text`` is not a str.
    - No ``resets ... (UTC)`` pattern is found.
    - The timezone label is not exactly ``(UTC)``.
    - The parsed hour/minute values are invalid (e.g. hour 13pm, minute 99).
    """
    if not isinstance(text, str):
        return None
    match = _RESET_AT_RE.search(text)
    if match is None:
        return None
    raw_hour = int(match.group(1))
    raw_minute = int(match.group(2))
    meridiem = match.group(3).lower()
    # Validate ranges: hour must be 1-12 for 12-hour clock; minute 0-59.
    if raw_hour < 1 or raw_hour > 12:
        return None
    if raw_minute < 0 or raw_minute > 59:
        return None
    hour_24 = _convert_to_24h(raw_hour, meridiem)
    now = _get_current_utc()
    candidate = now.replace(hour=hour_24, minute=raw_minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


# ---------------------------------------------------------------------------
# Internal attribute helpers
# ---------------------------------------------------------------------------


def _safe_getattr(obj: object, name: str, default: Any = None) -> Any:
    """Get attribute without raising; return ``default`` on any error."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _get_error_type(body: object) -> str | None:
    """Extract ``body['error']['type']`` safely, returning None on any failure."""
    if not isinstance(body, dict):
        return None
    error_section = body.get("error")
    if not isinstance(error_section, dict):
        return None
    value = error_section.get("type")
    return value if isinstance(value, str) else None


def _get_bedrock_error_code(obj: object) -> str | None:
    """Extract ``obj.response['Error']['Code']`` safely, returning None on failure."""
    response = _safe_getattr(obj, "response")
    if not isinstance(response, dict):
        return None
    error_section = response.get("Error")
    if not isinstance(error_section, dict):
        return None
    code = error_section.get("Code")
    return code if isinstance(code, str) else None


def _extract_reset_at_from_content(content: object) -> datetime | None:
    """Scan a sequence of content blocks for a parseable reset time."""
    if not isinstance(content, (list, tuple)):
        return None
    for block in content:
        text = _safe_getattr(block, "text")
        if isinstance(text, str):
            result = _parse_reset_at_from_text(text)
            if result is not None:
                return result
        # Also check the raw string of the block content field (ToolResultBlock)
        content_field = _safe_getattr(block, "content")
        if isinstance(content_field, str):
            result = _parse_reset_at_from_text(content_field)
            if result is not None:
                return result
    return None


# ---------------------------------------------------------------------------
# detect_quota_error rule helpers
# ---------------------------------------------------------------------------


def _apply_rules_1_to_5(obj: object, status_code: object) -> QuotaExhaustedError | None:
    """Rules 1-5: already a quota error, HTTP status codes, Bedrock throttle."""
    # Rule 1: passthrough
    if isinstance(obj, QuotaExhaustedError):
        return obj
    # Rule 2: HTTP 429
    if status_code == 429:
        return SubscriptionRateLimitError(reset_at=None, raw_error=obj, source="anthropic-api")
    # Rules 3 and 4: HTTP 402
    if status_code == 402:
        body = _safe_getattr(obj, "body", {})
        if _get_error_type(body) == "insufficient_quota":
            return SdkCreditExhaustedError(reset_at=None, raw_error=obj, source="sdk")
        return ApiBillingError(reset_at=None, raw_error=obj, source="anthropic-api")
    # Rule 5: Bedrock throttle
    bedrock_code = _get_bedrock_error_code(obj)
    if bedrock_code is not None and bedrock_code in _BEDROCK_THROTTLE_CODES:
        return BedrockThrottleError(reset_at=None, raw_error=obj, source="bedrock")
    return None


def _apply_rules_6_to_9(obj: object) -> QuotaExhaustedError | None:
    """Rules 6-9: CLI message surfaces and BaseException with quota marker."""
    # Rule 6: UserMessage with ToolResultBlock (content blocks with .content field)
    content = _safe_getattr(obj, "content")
    if isinstance(content, (list, tuple)):
        for block in content:
            block_content = _safe_getattr(block, "content")
            if isinstance(block_content, str) and _has_quota_marker(block_content):
                return SubscriptionRateLimitError(
                    reset_at=_parse_reset_at_from_text(block_content),
                    raw_error=obj,
                    source="claude-code-cli",
                )
    # Rule 7: AssistantMessage with error='rate_limit'
    if _safe_getattr(obj, "error") == "rate_limit":
        return SubscriptionRateLimitError(
            reset_at=_extract_reset_at_from_content(_safe_getattr(obj, "content")),
            raw_error=obj,
            source="claude-code-cli",
        )
    # Rule 8: ResultMessage with is_error=True and quota marker in .result (str)
    if _safe_getattr(obj, "is_error") is True:
        result_field = _safe_getattr(obj, "result")
        if isinstance(result_field, str) and _has_quota_marker(result_field):
            return SubscriptionRateLimitError(
                reset_at=_parse_reset_at_from_text(result_field),
                raw_error=obj,
                source="claude-code-cli",
            )
    # Rule 9: BaseException with quota marker in str(obj)
    if isinstance(obj, BaseException) and _has_quota_marker(str(obj)):
        return SubscriptionRateLimitError(
            reset_at=_parse_reset_at_from_text(str(obj)),
            raw_error=obj,
            source="claude-code-cli",
        )
    return None


def _detect_quota_error_inner(obj: object) -> QuotaExhaustedError | None:
    """Inner implementation; wrapped by detect_quota_error to guarantee no-raise."""
    status_code = _safe_getattr(obj, "status_code")
    result = _apply_rules_1_to_5(obj, status_code)
    if result is not None:
        return result
    return _apply_rules_6_to_9(obj)


# ---------------------------------------------------------------------------
# detect_quota_error -- ten ordered rules, never raises
# ---------------------------------------------------------------------------


def detect_quota_error(obj: object) -> QuotaExhaustedError | None:
    """Classify ``obj`` as a quota-exhaustion signal and return the typed exception.

    Applies the following ten rules in order; returns the first match or None:

    1. Already a ``QuotaExhaustedError`` -- return as-is.
    2. ``status_code == 429`` -- ``SubscriptionRateLimitError`` (source=anthropic-api).
    3. ``status_code == 402`` AND ``body.error.type == 'insufficient_quota'``
       -- ``SdkCreditExhaustedError`` (source=sdk).
    4. ``status_code == 402`` (other or absent type) -- ``ApiBillingError``
       (source=anthropic-api).
    5. ``response.Error.Code`` in Bedrock throttle set -- ``BedrockThrottleError``
       (source=bedrock).
    6. UserMessage with ToolResultBlock containing a quota marker
       -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
    7. Object with ``error == 'rate_limit'`` (AssistantMessage surface)
       -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
    8. Object with ``is_error == True`` and quota marker in ``result`` (str)
       -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
    9. ``BaseException`` with quota marker in ``str(obj)``
       -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
    10. Everything else -- None.

    This function never raises regardless of the input type or the internal
    attribute access errors on pathological objects.
    """
    try:
        return _detect_quota_error_inner(obj)
    except Exception:
        return None
