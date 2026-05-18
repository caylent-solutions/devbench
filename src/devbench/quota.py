"""Quota exception hierarchy, detection, and wait-for-reset protocol for devbench.

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
- :class:`BackoffConfig` -- exponential-backoff parameters used by
  :func:`wait_for_reset`.
- :func:`wait_for_reset` -- async function that sleeps until ``reset_at``,
  then polls ``probe_fn`` with exponential backoff until recovery or
  ``max_wait`` is exceeded.
- :func:`recovery_probe` -- sends a minimal 1-token Anthropic API completion
  request to test whether quota has been restored.  Returns ``True`` on
  success, ``False`` when the response signals continued quota exhaustion, and
  propagates all other exceptions unchanged.
- :class:`QuotaCheckpoint` -- dataclass capturing all pause-state fields
  written to ``quota_pause.json`` when the orchestrator detects quota
  exhaustion.
- :func:`save_checkpoint` -- atomically writes a :class:`QuotaCheckpoint` to
  ``<session_dir>/.devbench/quota_pause.json`` using a temp-then-rename
  strategy (POSIX ``os.replace``) so readers never see a partial file.
- :func:`remove_checkpoint` -- deletes ``quota_pause.json`` from
  ``<session_dir>/.devbench/`` after quota recovery.  Idempotent when the
  file is already absent.
- :func:`load_checkpoint` -- reads and deserializes ``quota_pause.json`` from
  ``<session_dir>/.devbench/``.  Returns ``None`` when the file is absent,
  raises :exc:`ValueError` when the file is present but malformed.
- :func:`post_webhook` -- best-effort POST of a JSON payload to a single URL
  using stdlib ``http.client``.  Failures are logged to stderr but do not raise.
  Reused by :mod:`devbench.notifications` for the unified Slack / generic
  notification dispatcher (PR #202); the prior ``deliver_notifications``
  helper here was removed in the same PR after every caller moved to the
  new module.

Raises:
    None -- this module only defines exception classes and pure-function
    helpers; it does not raise at module scope.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import secrets
import sys
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import anthropic

from devbench.config import get_anthropic_api_key
from devbench.constants import (
    QUOTA_CHECKPOINT_FILENAME,
    QUOTA_DEVBENCH_SUBDIR,
    RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS,
    RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS,
    RECOVERY_PROBE_MESSAGE_CONTENT,
    RECOVERY_PROBE_MODEL,
)

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
# Backoff configuration -- named constants (spec section 4.5.6 defaults)
# ---------------------------------------------------------------------------

# Rule 4 exemption: constants.py amendment out of scope (source-test atomicity).
# These constants are intentionally module-level so that callers can import
# and inspect them without instantiating a BackoffConfig.  They also satisfy
# the no-inline-literals rule: field() defaults reference the constants rather
# than bare numeric literals.
_BACKOFF_DEFAULT_INITIAL: float = 30.0
_BACKOFF_DEFAULT_MAX: float = 600.0
_BACKOFF_DEFAULT_MULTIPLIER: float = 2.0
_BACKOFF_DEFAULT_JITTER: float = 0.2

# Minimum sleep guard: prevents negative sleep durations (e.g. when reset_at
# is already in the past).
_MIN_SLEEP_SECONDS: float = 0.0

# ---------------------------------------------------------------------------
# Webhook notification constants
# ---------------------------------------------------------------------------

# Rule 4 exemption: constants.py amendment out of scope (source-test atomicity).
# Default timeout for best-effort webhook POST calls (spec section 4.5.6).
_WEBHOOK_DEFAULT_TIMEOUT_SECONDS: float = 10.0

# Allowed URL schemes for webhook POSTs.  Only http and https are permitted;
# file: and other custom schemes are disallowed (security: untrusted output sinks).
_WEBHOOK_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


# ---------------------------------------------------------------------------
# Backoff configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class BackoffConfig:
    """Exponential-backoff configuration for :func:`wait_for_reset` probe retries.

    All fields have spec-default values defined in spec section 4.5.6.
    Pass a custom instance to override any field.

    Args:
        initial_seconds: Starting backoff interval in seconds (default 30).
        max_seconds: Maximum backoff interval in seconds (default 600).
        multiplier: Exponent base applied after each failed probe (default 2.0).
        jitter: Fractional jitter to add/subtract from each backoff duration
            (default 0.2).  A jitter of 0.2 means the actual sleep duration is
            chosen uniformly from ``[delay * 0.8, delay * 1.2]``.  Set to 0.0
            to disable jitter for deterministic tests.

    Raises:
        None -- dataclass constructor only.
    """

    initial_seconds: float = field(default=_BACKOFF_DEFAULT_INITIAL)
    max_seconds: float = field(default=_BACKOFF_DEFAULT_MAX)
    multiplier: float = field(default=_BACKOFF_DEFAULT_MULTIPLIER)
    jitter: float = field(default=_BACKOFF_DEFAULT_JITTER)


# ---------------------------------------------------------------------------
# wait_for_reset -- async quota-recovery wait protocol
# ---------------------------------------------------------------------------


async def wait_for_reset(
    reset_at: datetime,
    poll_interval: float,
    max_wait: float,
    probe_fn: Callable[[], bool],
    backoff_config: BackoffConfig | None = None,
) -> bool:
    """Async quota-recovery wait loop.

    Sleeps until *reset_at* then probes quota recovery via *probe_fn*.  If the
    probe returns ``False`` (quota still exhausted), the loop backs off
    exponentially (per *backoff_config*) and retries.  Returns ``True`` when
    the probe succeeds, or ``False`` when the total time elapsed since the
    function was called exceeds *max_wait*.

    The initial sleep duration is the number of seconds between now and
    *reset_at*, clamped to 0 when *reset_at* is already in the past.

    Algorithm::

        elapsed = 0
        sleep(max(0, reset_at - now))
        elapsed += initial_sleep
        loop:
            if elapsed >= max_wait: return False
            if probe_fn(): return True
            delay = current_interval +/- jitter (capped at max_seconds)
            sleep(delay)
            elapsed += delay
            current_interval = min(current_interval * multiplier, max_seconds)

    Args:
        reset_at: UTC datetime at which the quota is expected to reset.
            When in the past, the initial sleep is skipped (clamped to 0).
        poll_interval: Starting backoff interval in seconds.  This value
            is used as the *initial_seconds* override when *backoff_config* is
            ``None``.  When *backoff_config* is provided and its
            ``initial_seconds`` differs from *poll_interval*, a
            :class:`ValueError` is raised to prevent silent precedence
            surprises.  Pass the same value to both; when
            ``backoff_config.initial_seconds`` equals *poll_interval*, no
            conflict is detected and *poll_interval* is used as the initial
            backoff interval.
        max_wait: Maximum total seconds to wait (inclusive of the initial sleep
            until *reset_at*).  When the accumulated elapsed time exceeds this
            value, the function returns ``False`` without calling *probe_fn*.
            A value of ``0.0`` causes ``False`` to be returned after the
            initial *reset_at* sleep completes, without calling *probe_fn*.
        probe_fn: Zero-argument callable that returns ``True`` when quota has
            recovered, ``False`` otherwise.  Any exception raised by probe_fn
            propagates unchanged to the caller.
        backoff_config: Exponential-backoff parameters.  When ``None``, a
            :class:`BackoffConfig` is constructed with ``initial_seconds``
            set to *poll_interval* and all other fields at their spec defaults.

    Returns:
        ``True`` when *probe_fn* returns ``True`` within *max_wait* seconds.
        ``False`` when *max_wait* is exceeded before the probe succeeds.

    Raises:
        ValueError: When *backoff_config* is provided and
            ``backoff_config.initial_seconds`` differs from *poll_interval*,
            indicating conflicting configuration that would otherwise be
            silently ignored.
        Exception: Any exception raised by *probe_fn* propagates unchanged.
    """
    if backoff_config is not None and backoff_config.initial_seconds != poll_interval:
        raise ValueError(
            f"Conflicting backoff configuration: poll_interval={poll_interval!r} differs from "
            f"backoff_config.initial_seconds={backoff_config.initial_seconds!r}. "
            "Set poll_interval equal to backoff_config.initial_seconds, or omit backoff_config "
            "to have it constructed automatically from poll_interval."
        )
    cfg = backoff_config if backoff_config is not None else BackoffConfig(initial_seconds=poll_interval)

    now = datetime.now(tz=UTC)
    initial_sleep = max(_MIN_SLEEP_SECONDS, (reset_at - now).total_seconds())
    await asyncio.sleep(initial_sleep)

    elapsed = initial_sleep
    current_interval = cfg.initial_seconds

    while True:
        if elapsed >= max_wait:
            return False
        if probe_fn():
            return True
        # Compute jittered backoff delay.  secrets.SystemRandom provides
        # uniform distribution without triggering S311 (standard PRNG warning).
        jitter_delta = current_interval * cfg.jitter
        delay = current_interval + secrets.SystemRandom().uniform(-jitter_delta, jitter_delta)
        delay = max(_MIN_SLEEP_SECONDS, min(delay, cfg.max_seconds))
        await asyncio.sleep(delay)
        elapsed += delay
        # Advance the un-jittered interval for the next iteration (capped at max).
        current_interval = min(current_interval * cfg.multiplier, cfg.max_seconds)


# ---------------------------------------------------------------------------
# recovery_probe -- minimal 1-token API probe for quota recovery detection
# ---------------------------------------------------------------------------


def recovery_probe(
    timeout_seconds: float = RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS,
    request_size_tokens: int = RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS,
) -> bool:
    """Send a minimal Anthropic API completion request to test quota recovery.

    Constructs an :class:`anthropic.Anthropic` client using the API key
    returned by :func:`~devbench.config.get_anthropic_api_key`, then issues
    a ``messages.create`` call requesting at most *request_size_tokens* output
    tokens.  This is designed to be the cheapest possible probe that exercises
    the real API path.

    Return value:

    - ``True``  -- the request completed without any quota signal; quota has
      recovered and normal operations can resume.
    - ``False`` -- the API raised (or the response contained) a quota signal
      that :func:`detect_quota_error` classifies as a
      :class:`QuotaExhaustedError` subclass; quota is still exhausted.

    All other exceptions (network errors, authentication failures,
    :exc:`RuntimeError` from missing credentials, etc.) propagate unchanged to
    the caller.  The caller is responsible for deciding whether to retry or
    surface the error.

    Args:
        timeout_seconds: Timeout in seconds for the Anthropic API call.  The
            value is passed directly as the timeout argument to the
            :class:`anthropic.Anthropic` client constructor.  Defaults to
            ``RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS`` (10 seconds) from
            :mod:`devbench.constants`.
        request_size_tokens: Maximum number of tokens to request in the
            completion response.  A value of 1 produces the shortest (cheapest)
            valid response.  Defaults to
            ``RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS`` (1 token) from
            :mod:`devbench.constants`.

    Returns:
        ``True`` when the probe call succeeds (quota recovered).
        ``False`` when the API signals continued quota exhaustion.

    Raises:
        ValueError: When *timeout_seconds* or *request_size_tokens* is not
            positive.  Raised before any network I/O or credential lookup
            (fail-fast).
        RuntimeError: When :func:`~devbench.config.get_anthropic_api_key`
            cannot obtain a valid API key (missing or unreadable credentials
            file, or missing access token).
        ConnectionError: When the Anthropic API is unreachable due to a
            network error.
        TimeoutError: When the API call exceeds *timeout_seconds*.
        Exception: Any other exception raised by the Anthropic SDK propagates
            unchanged -- caller decides how to handle it.
    """
    if timeout_seconds <= 0:
        msg = f"timeout_seconds must be positive, got {timeout_seconds!r}"
        raise ValueError(msg)
    if request_size_tokens <= 0:
        msg = f"request_size_tokens must be positive, got {request_size_tokens!r}"
        raise ValueError(msg)

    api_key = get_anthropic_api_key()
    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=timeout_seconds,
    )
    try:
        client.messages.create(
            model=RECOVERY_PROBE_MODEL,
            max_tokens=request_size_tokens,
            messages=[{"role": "user", "content": RECOVERY_PROBE_MESSAGE_CONTENT}],
        )
    except Exception as exc:
        quota_error = detect_quota_error(exc)
        if quota_error is not None:
            return False
        raise
    return True


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


# ---------------------------------------------------------------------------
# QuotaCheckpoint dataclass -- in-memory representation of quota_pause.json
# ---------------------------------------------------------------------------


@dataclass
class QuotaCheckpoint:
    """Pause-state snapshot written to ``quota_pause.json`` on quota exhaustion.

    Captures all information needed to resume orchestration after the quota
    window has elapsed.  Serialized to disk by :func:`save_checkpoint` and
    deserialized by :func:`load_checkpoint`.

    Args:
        paused_at: UTC datetime at which the orchestrator detected quota
            exhaustion and wrote this checkpoint.
        reset_at: UTC datetime at which the quota is expected to reset, or
            ``None`` when the vendor does not publish a reset time.
        reason: Short human-readable identifier for the quota signal type
            (e.g. ``"subscription_rate_limit"``, ``"bedrock_throttle"``).
        raw_error: The string representation of the original exception or
            error payload that triggered the pause.
        in_flight_wu: The work-unit ID that was in-flight at pause time, or
            ``None`` when no work unit was actively executing.
        in_flight_phase: The TDD phase (``"RED"``, ``"GREEN"``, ``"REFACTOR"``)
            that was in-flight, or ``None`` when ``in_flight_wu`` is ``None``.
        completed_judges: List of review-judge names that had already returned
            ``REVIEW_PASS`` before the pause (used by
            ``resume_strategy: continue_current_wu``).
        pending_judges: List of review-judge names that had not yet been
            invoked at pause time.
        stage_artefacts: Arbitrary dict of artefacts captured at pause time
            (e.g. branch name, commit SHA, PR number) for use by the resume
            protocol.

    Raises:
        None -- dataclass constructor only.
    """

    paused_at: datetime
    reset_at: datetime | None
    reason: str
    raw_error: str
    in_flight_wu: str | None
    in_flight_phase: str | None
    completed_judges: list[str]
    pending_judges: list[str]
    stage_artefacts: dict[str, object]


# ---------------------------------------------------------------------------
# Checkpoint path helper (private)
# ---------------------------------------------------------------------------


def _checkpoint_path(session_dir: Path) -> Path:
    """Return the canonical path for ``quota_pause.json`` under *session_dir*.

    The file is always placed at ``<session_dir>/.devbench/quota_pause.json``,
    mirroring the layout used by other devbench per-session state files.
    """
    return session_dir / QUOTA_DEVBENCH_SUBDIR / QUOTA_CHECKPOINT_FILENAME


# ---------------------------------------------------------------------------
# save_checkpoint -- atomic write of QuotaCheckpoint to quota_pause.json
# ---------------------------------------------------------------------------


def save_checkpoint(
    session_dir: Path,
    paused_at: datetime,
    reset_at: datetime | None,
    reason: str,
    raw_error: str,
    in_flight_wu: str | None,
    in_flight_phase: str | None,
    completed_judges: list[str],
    pending_judges: list[str],
    stage_artefacts: dict[str, object],
) -> None:
    """Atomically write a quota pause checkpoint to ``quota_pause.json``.

    The checkpoint is written to ``<session_dir>/.devbench/quota_pause.json``
    using a temp-file-then-rename strategy (POSIX ``os.replace``) so that
    concurrent readers never observe a partial file.

    The parent ``<session_dir>/.devbench/`` directory is created if absent.

    Args:
        session_dir: Root directory for this session (or workspace root when
            no named session is active).  The checkpoint file is placed under
            ``<session_dir>/.devbench/``.
        paused_at: UTC datetime at which the pause was detected.
        reset_at: UTC datetime at which the quota is expected to reset, or
            ``None`` when the vendor does not publish a reset time.
        reason: Short identifier for the quota signal type.
        raw_error: String representation of the original exception.
        in_flight_wu: Work-unit ID that was in-flight at pause time, or
            ``None``.
        in_flight_phase: TDD phase in-flight at pause time, or ``None``.
        completed_judges: List of judge names that had already passed.
        pending_judges: List of judge names not yet invoked.
        stage_artefacts: Dict of additional artefacts for the resume protocol.

    Raises:
        ValueError: When *reason* or *raw_error* is empty or whitespace-only,
            or when *paused_at* (or *reset_at*, when provided) lacks timezone
            info.  Raised before any file I/O (fail-fast).
        OSError: When the directory cannot be created or the file cannot be
            written (e.g. permission denied, disk full).
    """
    if not reason or not reason.strip():
        msg = f"reason must be a non-empty string, got {reason!r}"
        raise ValueError(msg)
    if not raw_error or not raw_error.strip():
        msg = f"raw_error must be a non-empty string, got {raw_error!r}"
        raise ValueError(msg)
    if paused_at.tzinfo is None:
        msg = (
            f"paused_at must be timezone-aware (got naive datetime {paused_at!r}); "
            "pass a UTC-aware datetime to avoid ambiguous timestamps in quota_pause.json"
        )
        raise ValueError(msg)
    if reset_at is not None and reset_at.tzinfo is None:
        msg = (
            f"reset_at must be timezone-aware when provided (got naive datetime {reset_at!r}); "
            "pass a UTC-aware datetime or None"
        )
        raise ValueError(msg)

    devbench_dir = session_dir / QUOTA_DEVBENCH_SUBDIR
    devbench_dir.mkdir(parents=True, exist_ok=True)

    target = devbench_dir / QUOTA_CHECKPOINT_FILENAME

    payload: dict[str, object] = {
        "paused_at": paused_at.isoformat(),
        "reset_at": reset_at.isoformat() if reset_at is not None else None,
        "reason": reason,
        "raw_error": raw_error,
        "in_flight_wu": in_flight_wu,
        "in_flight_phase": in_flight_phase,
        "completed_judges": completed_judges,
        "pending_judges": pending_judges,
        "stage_artefacts": stage_artefacts,
    }

    # Write atomically: serialize to a temp file in the same directory (so
    # Path.replace() is guaranteed to be a rename, not a cross-device copy),
    # then rename atomically.  This ensures readers always see either the old
    # file or the complete new file -- never a partial write.
    tmp_path = target.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(target)
    except Exception:
        # Best-effort cleanup of the temp file; failure here is non-fatal
        # (the original exception is re-raised regardless).
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# remove_checkpoint -- delete quota_pause.json on quota recovery
# ---------------------------------------------------------------------------


def remove_checkpoint(session_dir: Path) -> None:
    """Remove ``quota_pause.json`` from the session directory on quota recovery.

    Deletes ``<session_dir>/.devbench/quota_pause.json`` when it exists.
    This is called after the quota wait protocol completes successfully so
    subsequent runs (or the quota-watcher daemon) do not interpret a stale
    file as an active pause.

    The operation is idempotent: when the file does not exist (already removed
    by a concurrent process, or never written), this function returns normally
    without raising.

    Args:
        session_dir: Root directory for this session.  The checkpoint file is
            expected at ``<session_dir>/.devbench/quota_pause.json``.

    Raises:
        OSError: When the file exists but cannot be removed (e.g. permission
            denied).  Callers must not silently swallow this -- a file that
            cannot be removed causes the watcher daemon to re-enter wait mode
            unnecessarily.
    """
    target = _checkpoint_path(session_dir)
    target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# load_checkpoint -- read and deserialize quota_pause.json
# ---------------------------------------------------------------------------


def load_checkpoint(session_dir: Path) -> QuotaCheckpoint | None:
    """Read and deserialize ``quota_pause.json`` from the session directory.

    Returns ``None`` when the file does not exist (normal condition before the
    first pause or after a successful resume that removed the file).

    Raises :exc:`ValueError` when the file is present but malformed -- this
    signals a corrupt checkpoint that the caller must handle explicitly (e.g.
    by alerting the operator or removing the file).

    Args:
        session_dir: Root directory for this session.  The checkpoint file is
            expected at ``<session_dir>/.devbench/quota_pause.json``.

    Returns:
        A :class:`QuotaCheckpoint` instance when the file is present and
        valid, or ``None`` when the file does not exist.

    Raises:
        ValueError: When ``quota_pause.json`` exists but contains invalid JSON,
            a non-dict root, a missing required field, or an unparseable
            datetime string.  The message always includes the file path and a
            description of what is wrong.
    """
    target = _checkpoint_path(session_dir)
    if not target.exists():
        return None

    raw_text = target.read_text(encoding="utf-8")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"quota_pause.json at {target} contains invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"quota_pause.json at {target} must contain a JSON object at the root; got {type(data).__name__!r}"
        )

    # Validate and extract required fields.
    _require_key(data, "paused_at", target)
    _require_key(data, "reason", target)
    _require_key(data, "raw_error", target)
    _require_key(data, "completed_judges", target)
    _require_key(data, "pending_judges", target)
    _require_key(data, "stage_artefacts", target)

    paused_at = _parse_checkpoint_dt(data["paused_at"], "paused_at", target)
    reset_at_raw = data.get("reset_at")
    reset_at = _parse_checkpoint_dt(reset_at_raw, "reset_at", target) if reset_at_raw is not None else None

    # Validate string fields.
    _require_string(data, "reason", target)
    _require_string(data, "raw_error", target)

    # Validate list fields.
    _require_list(data, "completed_judges", target)
    _require_list(data, "pending_judges", target)

    # Validate dict field.
    _require_dict(data, "stage_artefacts", target)

    return QuotaCheckpoint(
        paused_at=paused_at,
        reset_at=reset_at,
        reason=data["reason"],
        raw_error=data["raw_error"],
        in_flight_wu=data.get("in_flight_wu"),
        in_flight_phase=data.get("in_flight_phase"),
        completed_judges=data["completed_judges"],
        pending_judges=data["pending_judges"],
        stage_artefacts=data["stage_artefacts"],
    )


# ---------------------------------------------------------------------------
# Checkpoint deserialization helpers (private)
# ---------------------------------------------------------------------------


def _require_string(data: dict[str, object], key: str, path: Path) -> None:
    """Raise :exc:`ValueError` when *data[key]* is not a string.

    Args:
        data: The deserialized JSON dict.
        key: The field name that must be a string.
        path: The file path, used in the error message.

    Raises:
        ValueError: When *data[key]* is not a ``str``.
    """
    value = data[key]
    if not isinstance(value, str):
        raise ValueError(f"quota_pause.json at {path}: field {key!r} must be a string; got {type(value).__name__!r}.")


def _require_list(data: dict[str, object], key: str, path: Path) -> None:
    """Raise :exc:`ValueError` when *data[key]* is not a list.

    Args:
        data: The deserialized JSON dict.
        key: The field name that must be a list.
        path: The file path, used in the error message.

    Raises:
        ValueError: When *data[key]* is not a ``list``.
    """
    value = data[key]
    if not isinstance(value, list):
        raise ValueError(f"quota_pause.json at {path}: field {key!r} must be a list; got {type(value).__name__!r}.")


def _require_dict(data: dict[str, object], key: str, path: Path) -> None:
    """Raise :exc:`ValueError` when *data[key]* is not a dict.

    Args:
        data: The deserialized JSON dict.
        key: The field name that must be a dict.
        path: The file path, used in the error message.

    Raises:
        ValueError: When *data[key]* is not a ``dict``.
    """
    value = data[key]
    if not isinstance(value, dict):
        raise ValueError(f"quota_pause.json at {path}: field {key!r} must be a dict; got {type(value).__name__!r}.")


def _require_key(data: dict[str, object], key: str, path: Path) -> None:
    """Raise :exc:`ValueError` when *key* is absent from *data*.

    Args:
        data: The deserialized JSON dict.
        key: The field name that must be present.
        path: The file path, used in the error message.

    Raises:
        ValueError: When *key* is not present in *data*.
    """
    if key not in data:
        raise ValueError(f"quota_pause.json at {path} is missing required field {key!r}.")


# ---------------------------------------------------------------------------
# post_webhook -- best-effort HTTP POST helper (spec section 4.5.6)
# ---------------------------------------------------------------------------


def post_webhook(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float = _WEBHOOK_DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """POST *payload* as JSON to *url*.  Failures are logged to stderr but do not raise.

    Uses stdlib ``http.client`` directly -- no third-party HTTP library and no
    ``urllib.request.urlopen`` (which triggers bandit B310).  Only ``http://``
    and ``https://`` scheme URLs are accepted; other schemes raise
    :exc:`ValueError` before any network I/O (security: untrusted output sinks
    must not be permitted to use file: or custom scheme handlers).

    Args:
        url: The webhook URL to POST to.  Must be a non-empty ``http://`` or
            ``https://`` URL.
        payload: A non-empty JSON-serialisable dict to send as the request body.
            Must contain at least one key.
        timeout_seconds: HTTP request timeout in seconds.  Must be positive.
            Defaults to ``_WEBHOOK_DEFAULT_TIMEOUT_SECONDS`` (10 seconds).

    Raises:
        ValueError: When *url* is empty, uses a disallowed scheme, *payload* is
            empty, or *timeout_seconds* is not positive.  Raised before any
            network I/O (fail-fast for invalid caller inputs).

    Returns:
        None -- always; network-level errors are logged to stderr, not raised.
    """
    if not url:
        msg = f"url must be a non-empty string, got {url!r}"
        raise ValueError(msg)
    if not payload:
        msg = "payload must be a non-empty dict"
        raise ValueError(msg)
    if timeout_seconds <= 0:
        msg = f"timeout_seconds must be positive, got {timeout_seconds!r}"
        raise ValueError(msg)

    # Validate scheme before any network I/O: reject file: and custom handlers.
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in _WEBHOOK_ALLOWED_SCHEMES:
        msg = f"url scheme {parsed.scheme!r} is not allowed; use one of {sorted(_WEBHOOK_ALLOWED_SCHEMES)}"
        raise ValueError(msg)

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
    }

    try:
        _http_post(parsed, body, headers, timeout_seconds)
    except Exception as exc:
        # Webhook URLs are credentials (CLAUDE.md "Sensitive Data
        # Handling").  Mask all but the last 8 chars in the log so an
        # operator can correlate the failure with the URL they
        # configured without leaking the secret to a shared stdout
        # capture.
        masked = "..." + url[-8:] if len(url) > 8 else "***"
        print(
            f"[WARN] webhook POST to {masked!r} failed: {exc!r}",
            file=sys.stderr,
        )


def _http_post(
    parsed: urllib.parse.SplitResult,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
) -> None:
    """Perform a low-level HTTP POST via ``http.client``.

    Separated from :func:`post_webhook` so the network I/O is easily mockable
    in unit tests by patching ``devbench.quota._http_post``.

    Args:
        parsed: A :class:`urllib.parse.SplitResult` for the target URL.
            ``parsed.scheme`` must already be validated to be ``http`` or
            ``https`` by the caller.
        body: UTF-8 encoded JSON body bytes.
        headers: HTTP request headers dict (must include ``Content-Type``).
        timeout_seconds: Socket-level timeout in seconds.

    Raises:
        Exception: Any network-level exception from ``http.client``.  The
            caller (:func:`post_webhook`) catches and logs all exceptions.
    """
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    if parsed.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname or "",
            port=parsed.port,
            timeout=timeout_seconds,
        )
    else:
        conn = http.client.HTTPConnection(
            parsed.hostname or "",
            port=parsed.port,
            timeout=timeout_seconds,
        )

    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()
    finally:
        conn.close()


def _parse_checkpoint_dt(value: object, field_name: str, path: Path) -> datetime:
    """Parse *value* as an ISO 8601 datetime string and return a UTC-aware datetime.

    Args:
        value: The raw value from the JSON payload.  Must be a non-empty string.
        field_name: The name of the field being parsed (for error messages).
        path: The file path (for error messages).

    Returns:
        A timezone-aware UTC :class:`~datetime.datetime`.

    Raises:
        ValueError: When *value* is not a string or is not a parseable ISO 8601
            datetime.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"quota_pause.json at {path}: field {field_name!r} must be a string; got {type(value).__name__!r}."
        )
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"quota_pause.json at {path}: field {field_name!r} is not a valid "
            f"ISO 8601 datetime: {value!r}. Original error: {exc}"
        ) from exc
    # Ensure timezone-aware (normalise to UTC).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
