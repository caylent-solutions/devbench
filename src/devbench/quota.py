"""Quota detection, wait-and-resume, checkpoint, and recovery-probe module for devbench.

Provides the ``QuotaExhaustedError`` exception hierarchy (base + four LSP
subclasses), the ``detect_quota_error`` dispatcher (ten ordered rules, never
raises), the two-matcher CLI-text scanners (``_has_quota_marker`` and
``_has_verbatim_quota_marker``), the ``_parse_reset_at_from_text``
reset-time parser, the async wait engine (``BackoffConfig``,
``_emit_polling_heartbeat``, ``_wait_toward_reset``, ``wait_for_reset``) that
polls for quota recovery with jittered exponential backoff, stepping toward
a known provider ``reset_at`` in bounded, heartbeat-emitting sleeps rather
than one blind long sleep, the recovery probe (``_probe_api_call``,
``recovery_probe``) that issues a 1-token ``messages.create`` request against
``RECOVERY_PROBE_MODEL`` to test whether quota has recovered -- consulted by
``wait_for_reset`` as its ``probe_fn`` -- and the checkpoint / resume-strategy
tier (``QuotaCheckpoint``, ``save_checkpoint``, ``load_checkpoint``,
``remove_checkpoint``, ``_apply_resume_strategy``) that persists pause state
to survive a SIGTERM. No cancellation-shielding primitive is used anywhere
in this module (D-9): a SIGTERM during a long wait must propagate naturally
so ``devbench stop`` stays responsive; durability comes from the checkpoint,
not from shielding.

This module covers the detection tier (FR-2.1, FR-2.2, FR-2.3), the wait
tier (FR-2.4), the recovery probe (FR-2.5), and the checkpoint / resume-
strategy tier (FR-2.6, FR-2.8; strategies only -- the resume loop itself
that consults these strategies after a recovered wait belongs to E2-F4).

Issue #234 (Appendix A QW-1 / QW-2 / QW-10).
Issue #236 (Appendix A QW-3 / QW-4 / QW-5).
AC-234-1, AC-234a-1, AC-236-1.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from devbench.drain import request_drain

logger = logging.getLogger("devbench.quota")

_QUOTA_POLLING_AUDIT_PREFIX: str = "[QUOTA_POLLING]"

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

# D-16: the first entry MUST be written as the ``\u2019`` escape sequence
# byte-for-byte (not a raw curly apostrophe character). The escape form
# survives encoding mishandling that a raw curly apostrophe would not, and
# the CLI's real limit message uses the curly-apostrophe form.
_QUOTA_MARKERS: tuple[str, ...] = (
    "You\u2019ve hit your limit",
    "You've hit your limit",
    "you've hit your limit",
    "You have hit your limit",
)

_BEDROCK_THROTTLE_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "ServiceQuotaExceededException",
    }
)

# Regex: matches "resets HH:MMam/pm (UTC)" -- case-insensitive meridiem.
# Captures: hour (1-12), minute (00-59), meridiem (am/pm/AM/PM).
# The (UTC) timezone label is required (D-8: no header parsing, CLI text
# form only); any other label, or a missing label, returns None.
_RESET_AT_RE = re.compile(
    r"resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)",
    re.IGNORECASE,
)

# Regex: matches "rate limit" / "rate-limit" / "rate limits" ONLY when an
# exhaustion verb follows immediately. This is the precise, narrow companion
# to the verbatim ``_QUOTA_MARKERS`` entries -- it deliberately does NOT
# match benign prose such as "implement rate limiting", "missing rate
# limiting", or "rate limit not exceeded" (the verb is not adjacent in those
# cases). Reserved for Rule 9 (an authoritative exception message), never
# for tool-result / result content (Rules 6 and 8) -- see
# ``_has_verbatim_quota_marker`` for the rationale.
_RATE_LIMIT_RE = re.compile(
    r"rate[\s-]?limits?\s+(?:exceeded|reached|hit|exhausted|resets?|try\s+again)",
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
        # No reset_at renders as "[resets=unknown]"; __str__ never raises
        # because the message is built once here from validated fields.
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


class RecoveryProbeUnavailableError(Exception):
    """The recovery probe cannot run -- a permanent, non-recoverable condition.

    Distinct from a ``QuotaExhaustedError`` (which means "still rate limited;
    keep waiting"). This is raised when the probe channel itself is
    unavailable -- e.g. no API credentials are configured, or the credentials
    are rejected (authentication / permission error). Waiting longer cannot
    clear such a condition, so the wait loop must stop fast and surface an
    actionable diagnostic rather than poll until ``max_wait_seconds`` elapses.
    """


# ---------------------------------------------------------------------------
# Clock helper (injectable via mock in tests)
# ---------------------------------------------------------------------------


def _get_current_utc() -> datetime:
    """Return the current UTC datetime. Isolated for test-time mocking."""
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Two-matcher CLI-text scanners (D-7)
# ---------------------------------------------------------------------------


def _has_quota_marker(text: object) -> bool:
    """Return True when ``text`` is a str containing a known quota marker.

    Two precise signatures are recognised:

    1. A substring match against the verbatim CLI limit lines enumerated in
       ``_QUOTA_MARKERS`` (e.g. "You've hit your limit").
    2. A regex match of ``_RATE_LIMIT_RE`` -- "rate limit" only when an
       exhaustion verb (exceeded / reached / hit / exhausted / resets /
       try again) follows immediately.

    Benign prose such as "implement rate limiting", "missing rate limiting",
    or "rate limit not exceeded" does NOT match by design -- only genuine
    exhaustion phrasing does. Non-string input returns False without raising.

    This is the BROAD matcher: it is used ONLY by Rule 9 (an actual
    exception message), which is an authoritative signal. It is NOT used
    for tool-result or result content -- see ``_has_verbatim_quota_marker``.
    """
    if not isinstance(text, str):
        return False
    if any(marker in text for marker in _QUOTA_MARKERS):
        return True
    return _RATE_LIMIT_RE.search(text) is not None


def _has_verbatim_quota_marker(text: object) -> bool:
    """Return True only for a VERBATIM CLI limit line in ``text`` (no regex).

    Stricter than :func:`_has_quota_marker`: matches solely the unambiguous
    ``_QUOTA_MARKERS`` lines (e.g. "You've hit your limit") and deliberately
    does NOT apply the ``_RATE_LIMIT_RE`` exhaustion-phrasing regex.

    This is the matcher used when scanning **tool-result / result content**
    (``detect_quota_error`` Rules 6 and 8). That content is arbitrary tool
    output -- files the agent read, grep results -- which routinely contains
    quota phrasing as benign data. devbench's own ``amendment.py`` emits the
    literal string "Amendment rate limit exceeded: ..." and this module plus
    ``tests/test_quota.py`` contain "rate limit"/"You've hit your limit" as
    error strings, docstrings, and fixtures; the orchestrator reads exactly
    those files while debugging. Matching the broad regex against such
    content produced false ``[QUOTA_WAITING]`` pauses. Only the verbatim CLI
    line is a trustworthy quota signal inside tool output. The
    ``_RATE_LIMIT_RE`` family is reserved for an actual exception message
    (Rule 9), which is authoritative. Non-string input returns False without
    raising.
    """
    if not isinstance(text, str):
        return False
    return any(marker in text for marker in _QUOTA_MARKERS)


# ---------------------------------------------------------------------------
# Reset-time parser (FR-2.3)
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

    D-8: only the CLI text form is parsed; there is deliberately no header
    parsing.

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
        content_field = _safe_getattr(block, "content")
        if isinstance(content_field, str):
            result = _parse_reset_at_from_text(content_field)
            if result is not None:
                return result
    return None


# ---------------------------------------------------------------------------
# detect_quota_error rule helpers (FR-2.2)
# ---------------------------------------------------------------------------


def _apply_rules_1_to_5(obj: object, status_code: object) -> QuotaExhaustedError | None:
    """Rules 1-5: already a quota error, HTTP status codes, Bedrock throttle."""
    if isinstance(obj, QuotaExhaustedError):
        return obj
    if status_code == 429:
        return SubscriptionRateLimitError(reset_at=None, raw_error=obj, source="anthropic-api")
    if status_code == 402:
        body = _safe_getattr(obj, "body", {})
        if _get_error_type(body) == "insufficient_quota":
            return SdkCreditExhaustedError(reset_at=None, raw_error=obj, source="sdk")
        return ApiBillingError(reset_at=None, raw_error=obj, source="anthropic-api")
    bedrock_code = _get_bedrock_error_code(obj)
    if bedrock_code is not None and bedrock_code in _BEDROCK_THROTTLE_CODES:
        return BedrockThrottleError(reset_at=None, raw_error=obj, source="bedrock")
    return None


def _apply_rules_6_to_9(obj: object) -> QuotaExhaustedError | None:
    """Rules 6-9: CLI message surfaces and BaseException with quota marker."""
    # Rule 6: UserMessage with a ToolResultBlock. Two-part gate (both
    # required) so benign tool output never trips detection:
    #   (a) ``is_error is True`` -- ONLY an explicit error tool result is a
    #       candidate. A successful Read/Grep/Glob returns ``is_error=None``
    #       and Bash returns ``is_error=False``; neither is a quota signal --
    #       their content is arbitrary file/tool data. (A genuine sub-agent
    #       quota limit surfaces as an *error* result.)
    #   (b) verbatim marker only -- scan with ``_has_verbatim_quota_marker``,
    #       not the broad ``_RATE_LIMIT_RE``, because tool content (e.g.
    #       devbench's own ``amendment.py`` "Amendment rate limit exceeded:
    #       ...") legitimately contains that phrasing while the agent debugs
    #       the quota/amendment code.
    content = _safe_getattr(obj, "content")
    if isinstance(content, (list, tuple)):
        for block in content:
            if _safe_getattr(block, "is_error") is not True:
                continue
            block_content = _safe_getattr(block, "content")
            if isinstance(block_content, str) and _has_verbatim_quota_marker(block_content):
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
    # Rule 8: ResultMessage with is_error=True and a VERBATIM quota line in
    # .result. The result text is arbitrary CLI output; a genuine
    # subscription limit is the verbatim "You've hit your limit ... resets
    # ...(UTC)" line, so the broad regex is not applied here (same
    # rationale as Rule 6).
    if _safe_getattr(obj, "is_error") is True:
        result_field = _safe_getattr(obj, "result")
        if isinstance(result_field, str) and _has_verbatim_quota_marker(result_field):
            return SubscriptionRateLimitError(
                reset_at=_parse_reset_at_from_text(result_field),
                raw_error=obj,
                source="claude-code-cli",
            )
    # Rule 9: BaseException with a quota marker in str(obj). An exception
    # message is authoritative, not arbitrary tool content, so the full
    # matcher (verbatim OR the broad regex) is used here.
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
    6. UserMessage with a ToolResultBlock that is an explicit error
       (``is_error is True``) AND whose content contains a VERBATIM CLI limit
       line (``_has_verbatim_quota_marker``) -- ``SubscriptionRateLimitError``
       (source=claude-code-cli). Successful results (``is_error`` ``None``/``False``)
       and the broad ``rate limit ...`` regex are deliberately NOT matched here:
       tool content is arbitrary file/tool data (the agent frequently reads
       devbench's own quota/amendment code, which quotes that phrasing).
    7. Object with ``error == 'rate_limit'`` (AssistantMessage surface)
       -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
    8. Object with ``is_error == True`` and a VERBATIM CLI limit line in
       ``result`` (str) -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
       (Verbatim-only, same rationale as Rule 6.)
    9. ``BaseException`` with a quota marker in ``str(obj)`` -- matched with the
       full ``_has_quota_marker`` (verbatim OR the ``_RATE_LIMIT_RE`` regex),
       because an exception message is an authoritative signal, not arbitrary
       tool content -- ``SubscriptionRateLimitError`` (source=claude-code-cli).
    10. Everything else -- None.

    This function never raises regardless of the input type or the internal
    attribute access errors on pathological objects: a malformed SDK message
    must pass through the orchestrate loop silently rather than crash it
    (sanctioned swallow, spec S7.1). All attribute access is routed through
    ``_safe_getattr`` so a hostile ``__getattr__`` cannot escape this
    guarantee either.
    """
    try:
        return _detect_quota_error_inner(obj)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Wait engine (FR-2.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackoffConfig:
    """Jittered exponential backoff configuration for ``wait_for_reset``.

    Every invariant documented below is enforced by ``__post_init__`` --
    fail-fast (Code Standards rule 3): an out-of-contract value raises
    ``ValueError`` at construction time instead of being silently
    normalised or clamped later, deep inside the poll loop.

    Attributes:
        initial_seconds: Starting backoff delay (must equal ``poll_interval_seconds``
            passed to ``wait_for_reset`` to avoid a conflict guard error).
            Must be > 0.
        max_seconds: Upper bound for any single backoff delay after jitter.
            Must be >= ``initial_seconds``.
        multiplier: Factor by which the raw delay grows each iteration.
            Must be >= 1.0.
        jitter: Fractional jitter range applied as ``+/- (delay * jitter)``
            via a cryptographically secure RNG. Must be in [0, 1].

    Raises:
        ValueError: When ``initial_seconds <= 0``, ``max_seconds <
            initial_seconds``, ``multiplier < 1.0``, or ``jitter`` is
            outside the closed interval ``[0, 1]``.
    """

    initial_seconds: int = 30
    max_seconds: int = 600
    multiplier: float = 2.0
    jitter: float = 0.2

    def __post_init__(self) -> None:
        if self.initial_seconds <= 0:
            raise ValueError(f"BackoffConfig.initial_seconds must be > 0, got {self.initial_seconds}.")
        if self.max_seconds < self.initial_seconds:
            raise ValueError(
                f"BackoffConfig.max_seconds ({self.max_seconds}) must be >= initial_seconds ({self.initial_seconds})."
            )
        if self.multiplier < 1.0:
            raise ValueError(f"BackoffConfig.multiplier must be >= 1.0, got {self.multiplier}.")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError(f"BackoffConfig.jitter must be in [0, 1], got {self.jitter}.")


_DEFAULT_BACKOFF: BackoffConfig = BackoffConfig()


def _emit_polling_heartbeat(*, elapsed: float, probe: int, next_in: float) -> None:
    """Emit one ``[QUOTA_POLLING]`` heartbeat line -- strictly best-effort.

    A visible heartbeat lets an operator SEE the orchestrator is actively
    waiting during a long quota pause, rather than the log looking dead
    between ``[QUOTA_WAITING]`` and ``[QUOTA_RESUMED]``. It is emitted at the
    poll interval on EVERY waiting path, including a pure provider-stated
    ``reset_at`` wait where no recovery probe runs (``probe`` is then ``0``).

    Any logging failure is swallowed here (sanctioned swallow, spec S7.1): a
    heartbeat or its log handler must NEVER break or delay the wait or the
    resume -- the wait's correctness does not depend on the line being
    written. This is the one place in the wait engine that deliberately
    suppresses an error, and only for the cosmetic liveness line.
    """
    with contextlib.suppress(Exception):
        logger.info(
            "%s elapsed=%ds probe=%d next_in=%ds",
            _QUOTA_POLLING_AUDIT_PREFIX,
            int(elapsed),
            probe,
            int(next_in),
        )


async def _wait_toward_reset(
    *,
    reset_at: datetime | None,
    poll_interval_seconds: int,
    max_wait_seconds: int,
) -> None:
    """Sleep toward a provider-stated ``reset_at`` in poll-interval-bounded steps.

    Used only on the production path where ``reset_at`` is known and still
    in the future. Sleeps in ``poll_interval_seconds`` increments -- never
    one blind long sleep -- emitting a ``[QUOTA_POLLING]`` heartbeat
    (``probe=0``, no probe run) before each interval so a long wait is
    visibly alive in the log between ``[QUOTA_WAITING]`` and
    ``[QUOTA_RESUMED]``. The recovery probe is NOT consulted here: an
    elapsed provider reset is the authoritative readiness signal (TDI-003a)
    and the probe tests a different auth channel that can never confirm the
    subscription quota.

    This is a pure side-effecting stepper: it returns ``None`` and never
    decides the outcome. After it returns, ``wait_for_reset``'s probe loop
    is the single source of truth -- it short-circuits to recovery on the
    now-elapsed reset, returns ``False`` on the max-wait timeout, or
    consults the probe. The wait window is bounded by whichever comes
    first, the reset or ``max_wait_seconds``, and ``elapsed`` is a local
    accumulator of the sleep durations already performed -- never a clock
    read -- so the step count is pre-computed from a single ``now`` snapshot
    and the loop terminates deterministically regardless of whether the
    (mockable) clock advances during the sleeps.

    Args:
        reset_at: Expected UTC reset time, or ``None`` (no reset-wait performed).
        poll_interval_seconds: Heartbeat/sleep cadence in seconds. Must be
            > 0 -- ``wait_for_reset`` validates this before calling here, so
            this stepper never has to normalise or clamp an invalid cadence.
        max_wait_seconds: Maximum total wait in seconds.
    """
    now = _get_current_utc()
    if reset_at is None or now >= reset_at:
        return
    poll_step = float(poll_interval_seconds)
    gap_to_reset = (reset_at - now).total_seconds()
    window = min(gap_to_reset, float(max_wait_seconds))
    elapsed = 0.0
    while elapsed < window:
        sleep_for = min(poll_step, window - elapsed)
        _emit_polling_heartbeat(elapsed=elapsed, probe=0, next_in=sleep_for)
        await asyncio.sleep(sleep_for)
        elapsed += sleep_for


async def wait_for_reset(
    *,
    reset_at: datetime | None,
    poll_interval_seconds: int,
    max_wait_seconds: int,
    probe_fn: Callable[[], bool],
    backoff_config: BackoffConfig | None = None,
) -> bool:
    """Async poller that waits until the quota resets and a probe confirms recovery.

    Algorithm, in this exact order (FR-2.4; not negotiable -- this is the
    piece that survived real production quota exhaustion, spec S1.6):

    1. Cadence validation: ``poll_interval_seconds <= 0`` raises
       ``ValueError`` before any I/O -- an invalid cadence must fail loudly
       rather than being silently clamped and busy-spinning the poll loop.
    2. Default ``backoff_config`` from ``poll_interval_seconds`` when not
       supplied.
    3. Cadence guard: ``backoff_config.initial_seconds != poll_interval_seconds``
       raises ``ValueError`` before any I/O.
    4. ``max_wait_seconds == 0`` returns ``False`` immediately.
    5. ``_wait_toward_reset`` performs stepped sleeps toward a known future
       ``reset_at`` (no-op when ``reset_at`` is ``None`` or already past).
    6. Poll loop: timeout check -> elapsed-reset short-circuit (TDI-003a,
       no probe) -> jittered delay computation -> heartbeat -> probe ->
       sleep -> delay growth.

    No cancellation-shielding primitive is used anywhere in this wait (D-9):
    a SIGTERM must propagate naturally so ``devbench stop`` stays responsive
    while a wait is in progress; durability comes from the checkpoint
    (:func:`save_checkpoint` / :func:`load_checkpoint`, FR-2.6), not from
    shielding.

    Args:
        reset_at: Expected UTC reset time (or ``None`` when unknown).
        poll_interval_seconds: Base polling cadence in seconds. Must be > 0
            (validated before any I/O) and must equal
            ``backoff_config.initial_seconds`` when a custom ``backoff_config``
            is supplied.
        max_wait_seconds: Maximum total wait in seconds. 0 means no wait.
        probe_fn: Callable returning ``True`` when the quota has recovered,
            ``False`` when still exhausted. Non-quota exceptions propagate.
        backoff_config: Optional backoff configuration. When ``None`` the
            function uses a default aligned with ``poll_interval_seconds``.

    Returns:
        ``True`` when a known ``reset_at`` has elapsed (the provider-stated
        reset time is the authoritative readiness signal -- no probe needed;
        TDI-003a), OR when the probe confirmed recovery before
        ``max_wait_seconds`` elapsed; ``False`` when the timeout was hit. The
        probe is best-effort and only consulted while ``reset_at`` is
        unknown or not yet reached.

    Raises:
        ValueError: When ``poll_interval_seconds <= 0``, or when
            ``backoff_config.initial_seconds != poll_interval_seconds``.
        RecoveryProbeUnavailableError: When the probe is permanently
            unavailable (no/invalid credential) AND no usable ``reset_at``
            is known (unknown, or not yet reached) -- the caller must fail
            fast rather than poll a probe that can never succeed.
        Any other exception raised by ``probe_fn``.
    """
    if poll_interval_seconds <= 0:
        raise ValueError(f"wait_for_reset: poll_interval_seconds must be > 0, got {poll_interval_seconds}.")

    if backoff_config is None:
        backoff_config = BackoffConfig(
            initial_seconds=poll_interval_seconds,
            max_seconds=_DEFAULT_BACKOFF.max_seconds,
            multiplier=_DEFAULT_BACKOFF.multiplier,
            jitter=_DEFAULT_BACKOFF.jitter,
        )
    if backoff_config.initial_seconds != poll_interval_seconds:
        raise ValueError(
            f"wait_for_reset: backoff_config.initial_seconds ({backoff_config.initial_seconds}) "
            f"must equal poll_interval_seconds ({poll_interval_seconds}) to avoid ambiguous cadence."
        )

    if max_wait_seconds == 0:
        return False

    start_time = _get_current_utc()

    # Provider-stated-reset_at wait (the common production path): step
    # toward the reset time in poll-interval-bounded sleeps, emitting a
    # [QUOTA_POLLING] heartbeat each interval so a long wait is visibly
    # alive in the log. This is a pure stepper -- the probe loop below
    # remains the single source of truth for the outcome (short-circuits to
    # True on the now-elapsed reset per TDI-003a, returns False on the
    # max-wait timeout, or consults the probe).
    await _wait_toward_reset(
        reset_at=reset_at,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
    )

    raw_delay = float(backoff_config.initial_seconds)
    rng = secrets.SystemRandom()
    probe_count = 0

    while True:
        now = _get_current_utc()
        elapsed = (now - start_time).total_seconds()
        if elapsed >= max_wait_seconds:
            return False

        # TDI-003a: a known, elapsed reset time is the authoritative
        # readiness signal -- resume without probing. The recovery probe
        # tests the raw Anthropic API channel, not the CLI/SDK subscription
        # channel the orchestrator runs on, so once the provider-stated
        # reset has passed the probe adds nothing (and on subscription auth
        # can never succeed). The probe is best-effort and only consulted
        # when reset_at is unknown or not yet reached.
        if reset_at is not None and now >= reset_at:
            return True

        probe_count += 1
        # Jittered delay: raw_delay * (1 +/- jitter), clamped to max_seconds.
        jitter_factor = 1.0 + rng.uniform(-backoff_config.jitter, backoff_config.jitter)
        delay = min(raw_delay * jitter_factor, float(backoff_config.max_seconds))

        # Visible heartbeat: exactly one line per poll so an operator can
        # SEE the orchestrator is actively polling during a long wait,
        # rather than the log looking dead between [QUOTA_WAITING] and
        # [QUOTA_RESUMED]. Best-effort: a logging failure must never break
        # the probe (same guarantee as the reset_at path).
        _emit_polling_heartbeat(elapsed=elapsed, probe=probe_count, next_in=delay)

        # RecoveryProbeUnavailableError (probe permanently unavailable, no
        # usable reset_at known) and any other probe_fn exception propagate
        # unhandled here -- fail fast rather than poll a probe that can
        # never succeed. A known, elapsed reset time is handled by the
        # short-circuit above, before the probe is ever consulted.
        if probe_fn():
            return True

        await asyncio.sleep(delay)
        raw_delay = min(raw_delay * backoff_config.multiplier, float(backoff_config.max_seconds))


# ---------------------------------------------------------------------------
# Checkpoint persistence (FR-2.6, spec S5.1)
# ---------------------------------------------------------------------------

_CHECKPOINT_DIR: str = ".devbench"
_CHECKPOINT_FILENAME: str = "quota_pause.json"


@dataclass
class QuotaCheckpoint:
    """Persisted state for a quota pause.

    Written atomically to ``<workspace_root>/.devbench/quota_pause.json`` by
    :func:`save_checkpoint`. This is the durability mechanism that replaced
    an ``asyncio.shield``-based approach considered during design (D-9): a
    SIGTERM arriving during a long :func:`wait_for_reset` call is allowed to
    kill the process outright -- spec FR-2.4 / decision D-9 / ADR-24:134-140
    require SIGTERM to propagate naturally so ``devbench stop`` stays
    responsive even mid-wait -- so the pause state must survive the process
    death on disk instead, letting a restarted orchestrator pick the wait
    back up rather than losing track of it.

    Attributes:
        reason: Short string identifying why the pause was entered (the
            ``QuotaExhaustedError.source`` field, e.g. ``"anthropic-api"``).
            Must be a non-empty string (validated by ``save_checkpoint``).
        reset_at: Expected UTC reset time, or ``None`` when the provider did
            not supply one. Must be timezone-aware when set (validated by
            ``save_checkpoint``).
        saved_at: UTC datetime when the checkpoint was written. Must be
            timezone-aware -- ``save_checkpoint`` is the timezone gate for
            this field (FR-2.1): a naive datetime raises ``ValueError`` at
            write time rather than being silently accepted and producing an
            ambiguous on-disk timestamp.
        session_name: Name of the devbench session that wrote the checkpoint.
    """

    reason: str
    reset_at: datetime | None
    saved_at: datetime
    session_name: str


def _checkpoint_path(workspace_root: Path) -> Path:
    """Return the absolute path to the quota checkpoint file for *workspace_root*."""
    return workspace_root / _CHECKPOINT_DIR / _CHECKPOINT_FILENAME


def save_checkpoint(checkpoint: QuotaCheckpoint, workspace_root: Path) -> None:
    """Atomically persist *checkpoint* to ``<workspace_root>/.devbench/quota_pause.json``.

    Validation runs BEFORE any filesystem I/O (fail-fast, Code Standards
    rule 3): an empty ``reason`` or a naive (tz-unaware) ``saved_at`` /
    ``reset_at`` raises ``ValueError`` immediately, so a malformed
    checkpoint is never written to disk.

    The write itself goes through ``tempfile.mkstemp`` -- a uniquely-named
    sibling temp file created in the same directory as the destination, so
    the eventual rename never crosses filesystems -- followed by
    ``Path.replace``. ``Path.replace`` is atomic on POSIX: the directory
    entry only flips once the temp file is fully written and closed, so
    neither a concurrent reader nor a process crash mid-write ever observes
    a partially-written file; the previous checkpoint (if any) stays intact
    and visible right up until the instant the new one is atomically swapped
    in. Nothing in this function opens ``dest`` directly for writing.

    Args:
        checkpoint: The checkpoint to persist.
        workspace_root: Workspace root directory. The checkpoint directory
            (``<workspace_root>/.devbench``) is created if it does not
            already exist.

    Raises:
        ValueError: When ``checkpoint.reason`` is empty, or when
            ``checkpoint.saved_at`` or ``checkpoint.reset_at`` (when set) is
            a naive (tz-unaware) datetime.
        OSError: On filesystem write errors (disk full, permission denied,
            etc.), or when the caller's crash simulation interrupts the
            write -- propagated unchanged for fail-fast diagnostics. The
            temp file is removed before re-raising.
    """
    if not checkpoint.reason:
        raise ValueError("QuotaCheckpoint.reason must be a non-empty string.")
    if checkpoint.saved_at.tzinfo is None:
        raise ValueError(
            f"QuotaCheckpoint.saved_at must be timezone-aware; got a naive datetime: {checkpoint.saved_at!r}. "
            "Use datetime.now(tz=UTC) or an equivalent timezone-aware value."
        )
    if checkpoint.reset_at is not None and checkpoint.reset_at.tzinfo is None:
        raise ValueError(
            f"QuotaCheckpoint.reset_at must be timezone-aware when set; got a naive datetime: "
            f"{checkpoint.reset_at!r}. Use a UTC-aware datetime or None."
        )

    dest = _checkpoint_path(workspace_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "reason": checkpoint.reason,
        "reset_at": checkpoint.reset_at.isoformat() if checkpoint.reset_at is not None else None,
        "saved_at": checkpoint.saved_at.isoformat(),
        "session_name": checkpoint.session_name,
    }

    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        tmp_path.replace(dest)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def load_checkpoint(workspace_root: Path) -> QuotaCheckpoint | None:
    """Load the quota pause checkpoint, or return ``None`` when absent.

    Args:
        workspace_root: Workspace root directory.

    Returns:
        The persisted ``QuotaCheckpoint`` when the checkpoint file exists
        and is well-formed. ``None`` when the file does not exist -- this is
        the normal "no active pause" state, not an error.

    Raises:
        ValueError: When the file exists but is malformed -- invalid JSON, a
            missing required field, or an unparseable ``saved_at`` /
            ``reset_at`` datetime. The message names the checkpoint path so
            an operator can locate and inspect the corrupt file directly
            (this is the exact message ``quota-watcher``, FR-2.11, surfaces
            to the operator).
    """
    path = _checkpoint_path(workspace_root)
    if not path.exists():
        return None
    path_str = str(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"quota_pause.json at '{path_str}' contains invalid JSON: {exc}.") from exc

    required = {"reason", "reset_at", "saved_at", "session_name"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"quota_pause.json at '{path_str}' is missing required fields: {sorted(missing)}.")

    try:
        saved_at = datetime.fromisoformat(data["saved_at"])
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"quota_pause.json at '{path_str}': saved_at value {data['saved_at']!r} "
            f"is not a valid ISO 8601 datetime: {exc}."
        ) from exc

    reset_at: datetime | None = None
    if data["reset_at"] is not None:
        try:
            reset_at = datetime.fromisoformat(data["reset_at"])
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"quota_pause.json at '{path_str}': reset_at value {data['reset_at']!r} "
                f"is not a valid ISO 8601 datetime: {exc}."
            ) from exc

    return QuotaCheckpoint(
        reason=str(data["reason"]),
        reset_at=reset_at,
        saved_at=saved_at,
        session_name=str(data["session_name"]),
    )


def remove_checkpoint(workspace_root: Path) -> None:
    """Remove the quota pause checkpoint file if present. Idempotent.

    Safe to call when no checkpoint exists, and safe to call repeatedly --
    ``Path.unlink(missing_ok=True)`` means neither case raises.

    Args:
        workspace_root: Workspace root directory.
    """
    _checkpoint_path(workspace_root).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Recovery probe (FR-2.5)
# ---------------------------------------------------------------------------


def _probe_api_call(timeout_seconds: float, request_size_tokens: int) -> object:
    """Issue a minimal Anthropic ``messages.create`` call for quota probing.

    This thin shim is isolated so tests can patch ``anthropic.Anthropic`` (via
    ``unittest.mock.patch.multiple`` on the already-imported ``anthropic``
    module) without needing the full SDK imported at test-collection time --
    the import happens here, inside the call, not at module load.

    Args:
        timeout_seconds: HTTP timeout for the probe call.
        request_size_tokens: Approximate input token count (affects prompt size).

    Returns:
        The API response object (opaque; callers check for success by the
        absence of a raised exception).
    """
    import anthropic

    from devbench.constants import RECOVERY_PROBE_MODEL

    client = anthropic.Anthropic(timeout=timeout_seconds)
    prompt = "x" * max(1, request_size_tokens)
    return client.messages.create(
        model=RECOVERY_PROBE_MODEL,
        max_tokens=1,
        messages=[{"role": "user", "content": prompt}],
    )


def recovery_probe(*, timeout_seconds: float, request_size_tokens: int) -> bool:
    """Issue a minimal probe API call to check whether the quota has recovered.

    The except-clause ORDER below is load-bearing and MUST NOT be reordered
    (spec FR-2.5, AC-18). ``anthropic.AuthenticationError`` and
    ``anthropic.PermissionDeniedError`` both subclass ``anthropic.APIError``,
    which itself subclasses ``anthropic.AnthropicError``. Python's ``except``
    matches the FIRST arm whose type the raised exception is an instance of,
    so the two narrower auth subclasses MUST be caught before the broader
    ``APIError`` arm, and both API arms MUST be caught before the broadest
    ``AnthropicError`` arm. If ``APIError`` were listed above the auth arms,
    an auth failure would be silently swallowed as "still exhausted"
    (``False``) instead of raising ``RecoveryProbeUnavailableError`` -- the
    wait loop would then spin on a probe channel that can never succeed until
    ``max_wait_seconds`` finally times it out. This ordering (and its
    rationale) restores what commit ``6188aab`` stripped from the tip of
    ``origin/feat/flatten-review-pipeline``; see the pre-strip commits
    ``162f932`` (introduces ``recovery_probe``) and ``f2b4644`` (introduces
    ``RecoveryProbeUnavailableError`` and the bare-``AnthropicError`` arm)
    (D-16).

    Arm order:

    1. ``QuotaExhaustedError`` -- still exhausted; return ``False``.
    2. ``anthropic.AuthenticationError`` / ``anthropic.PermissionDeniedError``
       -- rejected credential; raise ``RecoveryProbeUnavailableError``.
    3. ``anthropic.APIError`` -- other API/network error; return ``False``.
    4. ``anthropic.AnthropicError`` -- no credential configured; raise
       ``RecoveryProbeUnavailableError``.
    5. Any other exception -- return ``False``.

    Args:
        timeout_seconds: HTTP timeout in seconds. Must be > 0.
        request_size_tokens: Input token count for the probe prompt. Must be >= 1.

    Returns:
        ``True`` when the probe call succeeded (quota has cleared).
        ``False`` when the probe hit a quota error, a non-auth API error, or
        any other exception (quota may still be exhausted, or the network is
        temporarily down); treated as "not yet recovered" without crashing
        the wait loop.

    Raises:
        ValueError: When ``timeout_seconds <= 0`` or ``request_size_tokens < 1``
            (fail-fast, checked before any I/O).
        RecoveryProbeUnavailableError: When the probe channel itself is
            permanently unavailable -- the credential is rejected
            (``AuthenticationError`` / ``PermissionDeniedError``) or absent
            (a bare ``AnthropicError``). Waiting longer cannot clear either
            condition, so the caller must stop polling and surface an
            actionable diagnostic instead of spinning until
            ``max_wait_seconds`` elapses.
    """
    if timeout_seconds <= 0:
        raise ValueError(f"recovery_probe: timeout_seconds must be > 0; got {timeout_seconds!r}.")
    if request_size_tokens < 1:
        raise ValueError(f"recovery_probe: request_size_tokens must be >= 1; got {request_size_tokens!r}.")

    import anthropic

    try:
        _probe_api_call(timeout_seconds, request_size_tokens)
        return True
    except QuotaExhaustedError:
        return False
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise RecoveryProbeUnavailableError(
            f"recovery probe could not authenticate to the Anthropic API ({type(exc).__name__}). "
            "Configure a valid API credential so quota recovery can be confirmed, "
            "or rely on the provider-supplied reset time."
        ) from exc
    except anthropic.APIError:
        return False
    except anthropic.AnthropicError as exc:
        raise RecoveryProbeUnavailableError(
            "recovery probe has no usable Anthropic API credential configured. "
            "Quota recovery cannot be probed; configure a credential or rely on "
            "the provider-supplied reset time."
        ) from exc
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Resume-strategy dispatcher (FR-2.8, spec AC-23)
#
# Strategies only -- the resume loop that consults these after a recovered
# wait belongs to downstream task E2-F4.
# ---------------------------------------------------------------------------

_RESUME_STRATEGIES: frozenset[str] = frozenset({"continue_current_wu", "restart_wu", "drain_and_resume"})


def _force_status_in_queue(workspace_root: Path) -> None:
    """Force every ``in-progress`` work unit under *workspace_root* back to ``in-queue``.

    Delegates entirely to the existing ``BacklogParser`` / ``BacklogManager``
    primitives (spec Section 3: reuse, do not reinvent) -- this function
    introduces no bespoke backlog-file parsing or status-writing logic; it
    only decides WHICH units to force and calls the existing
    ``BacklogManager.force_status`` for each one.

    Args:
        workspace_root: Workspace root directory. The backlog index
            (``<workspace_root>/BACKLOG.md``) and backlog directory
            (``<workspace_root>/backlog``) are derived from this parameter
            so the function always operates on the same workspace the
            checkpoint itself was written to, rather than a process-global
            default.
    """
    from devbench.backlog.manager import BacklogManager
    from devbench.backlog.parser import BacklogParser
    from devbench.backlog.work_unit import WorkUnitStatus
    from devbench.constants import BACKLOG_SUBDIR, STATUS_IN_QUEUE

    backlog_index = workspace_root / "BACKLOG.md"
    backlog_root = workspace_root / BACKLOG_SUBDIR
    parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
    units = parser.parse_index()
    manager = BacklogManager()
    for unit in units:
        if unit.status is WorkUnitStatus.IN_PROGRESS:
            manager.force_status(unit.file_path, backlog_index, unit.id, STATUS_IN_QUEUE)


def _apply_resume_strategy(strategy: str, workspace_root: Path) -> None:
    """Dispatch the post-wait resume action for *strategy* (FR-2.8, spec AC-23).

    Args:
        strategy: One of ``"continue_current_wu"`` (the default -- simply
            resumes the claimed work unit in place), ``"restart_wu"`` (every
            ``in-progress`` unit is forced back to ``in-queue`` so the next
            orchestrator pass re-claims it from a clean state), or
            ``"drain_and_resume"`` (requests a graceful drain instead of
            resuming automatically).
        workspace_root: Workspace root directory, used by both the
            checkpoint removal and (for ``drain_and_resume``) the
            ``request_drain`` primitive.

    Raises:
        ValueError: When *strategy* is not one of the three recognised
            values; the message names the allowed set.
    """
    if strategy not in _RESUME_STRATEGIES:
        raise ValueError(f"unknown resume strategy {strategy!r}. Allowed values: {sorted(_RESUME_STRATEGIES)}.")

    if strategy == "continue_current_wu":
        remove_checkpoint(workspace_root)
    elif strategy == "restart_wu":
        _force_status_in_queue(workspace_root)
        remove_checkpoint(workspace_root)
    else:
        remove_checkpoint(workspace_root)
        request_drain(workspace_root)
