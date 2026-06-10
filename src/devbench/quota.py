"""Quota detection, wait-and-resume, and checkpoint module for devbench.

Provides:
- ``QuotaExhaustedError`` exception hierarchy (base + four LSP subclasses).
- ``detect_quota_error`` dispatcher (ten ordered rules, never raises).
- ``_has_quota_marker`` CLI-text matcher (verbatim limit markers + the precise
  ``_RATE_LIMIT_RE`` exhaustion-phrasing regex).
- ``_parse_reset_at_from_text`` reset-time parser.
- ``wait_for_reset`` async poller with jittered backoff (no shield).
- ``QuotaCheckpoint``, ``save_checkpoint``, ``load_checkpoint``, ``remove_checkpoint``
  for persisting pause state across SIGTERM.
- ``recovery_probe`` thin API probe to confirm quota has recovered.
- ``_apply_resume_strategy`` dispatcher for post-wait resume behaviour.

Issue #234 (Appendix A QW-1 / QW-2 / QW-10).
Issue #236 (Appendix A QW-3 / QW-4 / QW-5).
AC-234-1, AC-234a-1, AC-236-1.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from devbench.drain import request_drain

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

_QUOTA_MARKERS: tuple[str, ...] = (
    "You\u2019ve hit your limit",  # verbatim CLI line -- real Unicode apostrophe
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
# The (UTC) timezone label is required; any other label returns None.
_RESET_AT_RE = re.compile(
    r"resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)",
    re.IGNORECASE,
)

# Regex: matches "rate limit" / "rate-limit" / "rate limits" ONLY when an
# exhaustion verb follows immediately. This is the precise replacement for the
# former bare ``"rate limit"`` substring marker, which falsely matched benign
# prose such as "implement rate limiting", "missing rate limiting", or
# "rate limit not exceeded" (the verb is not adjacent in those cases). The
# genuine CLI limit line is still matched by the verbatim ``_QUOTA_MARKERS``
# entries above; this regex only adds the non-verbatim "rate limit exceeded"
# family.
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
# CLI-byte substring scanner
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
    those files while debugging. Matching the broad regex against such content
    produced false ``[QUOTA_WAITING]`` pauses. Only the verbatim CLI line is a
    trustworthy quota signal inside tool output. The ``_RATE_LIMIT_RE`` family
    is reserved for an actual exception message (Rule 9), which is authoritative.
    Non-string input returns False without raising.
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
    # Rule 6: UserMessage with ToolResultBlock (content blocks with .content field).
    # Two-part gate (both required) so benign tool output never trips detection:
    #   (a) ``is_error is True`` -- ONLY an explicit error tool result is a
    #       candidate. A successful Read/Grep/Glob returns ``is_error=None`` and
    #       Bash returns ``is_error=False``; neither is a quota signal -- their
    #       content is arbitrary file/tool data. (A genuine sub-agent quota limit
    #       surfaces as an *error* result.)
    #   (b) verbatim marker only -- scan with ``_has_verbatim_quota_marker``, not
    #       the broad ``_RATE_LIMIT_RE``, because tool content (e.g. devbench's
    #       own ``amendment.py`` "Amendment rate limit exceeded: ...") legitimately
    #       contains that phrasing while the agent debugs the quota/amendment code.
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
    # .result. The result text is arbitrary CLI output; a genuine subscription
    # limit is the verbatim "You've hit your limit ... resets ...(UTC)" line, so
    # the broad regex is not applied here (same rationale as Rule 6).
    if _safe_getattr(obj, "is_error") is True:
        result_field = _safe_getattr(obj, "result")
        if isinstance(result_field, str) and _has_verbatim_quota_marker(result_field):
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
    attribute access errors on pathological objects.
    """
    try:
        return _detect_quota_error_inner(obj)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# BackoffConfig dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackoffConfig:
    """Jittered exponential backoff configuration for ``wait_for_reset``.

    Attributes:
        initial_seconds: Starting backoff delay (must equal ``poll_interval_seconds``
            passed to ``wait_for_reset`` to avoid a conflict guard error).
        max_seconds: Upper bound for any single backoff delay after jitter.
        multiplier: Factor by which the raw delay grows each iteration.
            Must be >= 1.0.
        jitter: Fractional jitter range applied as ``+/- (delay * jitter)``
            via a cryptographically secure RNG. Must be in [0, 1].
    """

    initial_seconds: int = 30
    max_seconds: int = 600
    multiplier: float = 2.0
    jitter: float = 0.2


# ---------------------------------------------------------------------------
# wait_for_reset
# ---------------------------------------------------------------------------

_DEFAULT_BACKOFF: BackoffConfig = BackoffConfig()


async def wait_for_reset(
    *,
    reset_at: datetime | None,
    poll_interval_seconds: int,
    max_wait_seconds: int,
    probe_fn: Callable[[], bool],
    backoff_config: BackoffConfig | None = None,
) -> bool:
    """Async poller that waits until the quota resets and a probe confirms recovery.

    Algorithm:
    1. Compute ``initial_sleep = max(0, min(max_wait, (reset_at - now)))``.
       When ``reset_at`` is ``None`` or in the past, ``initial_sleep = 0``.
    2. If ``max_wait_seconds == 0`` return ``False`` immediately.
    3. ``await asyncio.sleep(initial_sleep)``.
    4. Loop: check elapsed >= max_wait -> return False; call probe_fn() ->
       True on success; jittered backoff up to max_seconds; sleep; repeat.

    The ``backoff_config.initial_seconds`` must equal ``poll_interval_seconds``
    to avoid conflating two different cadence parameters. A mismatch raises
    ``ValueError`` before any I/O.

    Args:
        reset_at: Expected UTC reset time (or ``None`` when unknown).
        poll_interval_seconds: Base polling cadence in seconds (must equal
            ``backoff_config.initial_seconds`` when a custom ``backoff_config``
            is supplied).
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
        probe is best-effort and only consulted while ``reset_at`` is unknown.

    Raises:
        ValueError: When ``backoff_config.initial_seconds != poll_interval_seconds``.
        RecoveryProbeUnavailableError: When the probe is permanently unavailable
            (no/invalid credential) AND no usable ``reset_at`` is known (unknown
            or not yet reached) -- the caller must fail fast rather than poll a
            probe that can never succeed.
        Any other exception raised by ``probe_fn``.
    """
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

    now = _get_current_utc()
    start_time = now

    if reset_at is not None and reset_at > now:
        gap_seconds = (reset_at - now).total_seconds()
        initial_sleep = min(float(max_wait_seconds), gap_seconds)
    else:
        initial_sleep = 0.0

    await asyncio.sleep(initial_sleep)

    raw_delay = float(backoff_config.initial_seconds)
    rng = secrets.SystemRandom()

    while True:
        now = _get_current_utc()
        elapsed = (now - start_time).total_seconds()
        if elapsed >= max_wait_seconds:
            return False

        # TDI-003a: a known, elapsed reset time is the authoritative readiness
        # signal -- resume without probing. The recovery probe tests the raw
        # Anthropic API channel, not the CLI/SDK subscription channel the
        # orchestrator runs on, so once the provider-stated reset has passed the
        # probe adds nothing (and on subscription auth can never succeed). The
        # probe is best-effort and only consulted when reset_at is unknown.
        if reset_at is not None and now >= reset_at:
            return True

        try:
            if probe_fn():
                return True
        except RecoveryProbeUnavailableError:
            # The probe cannot confirm recovery (no/invalid credential) and no
            # usable reset time is known (unknown, or not yet reached) -- fail
            # fast rather than poll a probe that can never succeed. A known,
            # elapsed reset time is handled by the short-circuit above.
            raise

        # Jittered delay: raw_delay * (1 +/- jitter), clamped to max_seconds.
        jitter_factor = 1.0 + rng.uniform(-backoff_config.jitter, backoff_config.jitter)
        delay = min(raw_delay * jitter_factor, float(backoff_config.max_seconds))
        await asyncio.sleep(delay)
        raw_delay = min(raw_delay * backoff_config.multiplier, float(backoff_config.max_seconds))


# ---------------------------------------------------------------------------
# Checkpoint -- persist pause state across SIGTERM
# ---------------------------------------------------------------------------

_CHECKPOINT_DIR: str = ".devbench"
_CHECKPOINT_FILENAME: str = "quota_pause.json"


@dataclass
class QuotaCheckpoint:
    """Persisted state for a quota pause.

    Written to ``<workspace_root>/.devbench/quota_pause.json`` atomically.

    Attributes:
        reason: Short string from the ``QuotaExhaustedError.source`` field.
        reset_at: Expected UTC reset time, or ``None`` when unknown.
        saved_at: UTC datetime when the checkpoint was written (timezone-aware).
        session_name: Name of the devbench session that wrote the checkpoint.
    """

    reason: str
    reset_at: datetime | None
    saved_at: datetime
    session_name: str


def _checkpoint_path(workspace_root: Path) -> Path:
    """Return the absolute path to the quota checkpoint file."""
    return workspace_root / _CHECKPOINT_DIR / _CHECKPOINT_FILENAME


def save_checkpoint(checkpoint: QuotaCheckpoint, workspace_root: Path) -> None:
    """Atomically write *checkpoint* to the quota pause file.

    Uses a sibling temp file + ``Path.replace`` so a mid-write crash leaves
    the previous checkpoint intact.

    Args:
        checkpoint: The checkpoint to persist.
        workspace_root: Workspace root directory.

    Raises:
        ValueError: If ``checkpoint.reason`` is empty or either datetime is
            naive (no ``tzinfo``).
        OSError: On filesystem write errors.
    """
    if not checkpoint.reason:
        raise ValueError("QuotaCheckpoint.reason must be a non-empty string.")
    if checkpoint.saved_at.tzinfo is None:
        raise ValueError(
            f"QuotaCheckpoint.saved_at must be timezone-aware; got a naive datetime: {checkpoint.saved_at!r}. "
            "Use datetime.now(tz=UTC) or equivalent."
        )
    if checkpoint.reset_at is not None and checkpoint.reset_at.tzinfo is None:
        raise ValueError(
            f"QuotaCheckpoint.reset_at must be timezone-aware when set; got a naive datetime: {checkpoint.reset_at!r}. "
            "Use a UTC-aware datetime or None."
        )
    dest = _checkpoint_path(workspace_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "reason": checkpoint.reason,
        "reset_at": checkpoint.reset_at.isoformat() if checkpoint.reset_at is not None else None,
        "saved_at": checkpoint.saved_at.isoformat(),
        "session_name": checkpoint.session_name,
    }
    import contextlib
    import os as _os

    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with _os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        Path(tmp_path_str).replace(dest)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp_path_str).unlink(missing_ok=True)
        raise


def load_checkpoint(workspace_root: Path) -> QuotaCheckpoint | None:
    """Load the quota pause checkpoint, or return ``None`` if absent.

    Args:
        workspace_root: Workspace root directory.

    Returns:
        ``QuotaCheckpoint`` when the checkpoint file exists and is valid.
        ``None`` when the file is absent.

    Raises:
        ValueError: When the file exists but is malformed (bad JSON, missing
            key, or unparseable datetime). The error message names the file path.
    """
    path = _checkpoint_path(workspace_root)
    if not path.exists():
        return None
    path_str = str(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"quota_pause.json at '{path_str}' contains invalid JSON: {exc}.") from exc
    required = {"reason", "saved_at", "session_name"}
    missing = required - set(data.keys())
    if "reset_at" not in data:
        missing.add("reset_at")
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
    """Remove the quota pause checkpoint file if it exists. Idempotent.

    Args:
        workspace_root: Workspace root directory.
    """
    path = _checkpoint_path(workspace_root)
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# recovery_probe -- thin API probe to confirm quota has cleared
# ---------------------------------------------------------------------------


def _probe_api_call(timeout_seconds: float, request_size_tokens: int) -> object:
    """Issue a minimal Anthropic messages.create call for quota probing.

    This thin shim is isolated so tests can patch it without importing
    the full SDK at test-collection time.

    Args:
        timeout_seconds: HTTP timeout for the probe call.
        request_size_tokens: Approximate input token count (affects prompt size).

    Returns:
        The API response object (opaque; callers check for success by
        absence of a raised exception).
    """
    import anthropic

    from devbench.constants import RECOVERY_PROBE_MODEL

    client = anthropic.Anthropic(timeout=timeout_seconds)
    # Minimal prompt -- we only care whether the call succeeds.
    prompt = "x" * max(1, request_size_tokens)
    return client.messages.create(
        model=RECOVERY_PROBE_MODEL,
        max_tokens=1,
        messages=[{"role": "user", "content": prompt}],
    )


def recovery_probe(*, timeout_seconds: float, request_size_tokens: int) -> bool:
    """Issue a minimal probe API call to check whether the quota has recovered.

    Args:
        timeout_seconds: HTTP timeout. Must be > 0.
        request_size_tokens: Input token count for the probe. Must be >= 1.

    Returns:
        ``True`` when the probe call succeeded (quota has cleared).
        ``False`` when the probe hit a quota error or a transient API/network
        error (quota may still be exhausted or network is temporarily down;
        treat as "not yet recovered" without crashing).

    Raises:
        ValueError: When ``timeout_seconds <= 0`` or ``request_size_tokens < 1``
            (fail-fast before any I/O).
        RecoveryProbeUnavailableError: When the probe channel is permanently
            unavailable -- no API credential is configured, or the credential
            is rejected (authentication / permission error). Waiting cannot
            clear this, so the caller must stop polling and fail fast.
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
        # Rejected credentials -- permanent; waiting cannot fix it.
        raise RecoveryProbeUnavailableError(
            f"recovery probe could not authenticate to the Anthropic API ({type(exc).__name__}). "
            "Configure a valid API credential so quota recovery can be confirmed, "
            "or rely on the provider-supplied reset time."
        ) from exc
    except anthropic.APIError:
        # Other API/network errors (incl. 429, connection, timeout) are
        # transient -- treat as "still exhausted" and keep polling.
        return False
    except anthropic.AnthropicError as exc:
        # Non-API AnthropicError = client construction/config failure, e.g. no
        # API credential is configured at all -- permanent, not recoverable.
        raise RecoveryProbeUnavailableError(
            "recovery probe has no usable Anthropic API credential configured. "
            "Quota recovery cannot be probed; configure a credential or rely on "
            "the provider-supplied reset time."
        ) from exc
    except Exception:
        # Any other transient error -- treat as "still exhausted"; do not crash.
        return False


# ---------------------------------------------------------------------------
# Resume strategy dispatcher
# ---------------------------------------------------------------------------


def _force_status_in_queue(_workspace_root: Path) -> None:
    """Reset the in-progress work unit to in-queue for restart_wu strategy.

    Isolated for testability.

    Args:
        _workspace_root: Reserved for future use; currently the backlog root is
            resolved from the module-level ``BACKLOG_ROOT`` constant.
    """
    from devbench.backlog.manager import BacklogManager
    from devbench.backlog.parser import BacklogParser
    from devbench.config import BACKLOG_INDEX, BACKLOG_ROOT

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    manager = BacklogManager()
    in_progress = [u for u in units if u.status == "in-progress"]
    for wu in in_progress:
        manager.force_status(wu.file_path, BACKLOG_INDEX, wu.id, "in-queue")


def _apply_resume_strategy(strategy: str, workspace_root: Path) -> None:
    """Dispatch the post-wait resume action for *strategy*.

    Args:
        strategy: One of ``"continue_current_wu"``, ``"restart_wu"``,
            ``"drain_and_resume"``.
        workspace_root: Workspace root directory (used by checkpoint and drain ops).

    Raises:
        ValueError: When *strategy* is not a recognised value.
    """
    known_strategies = frozenset({"continue_current_wu", "restart_wu", "drain_and_resume"})
    if strategy not in known_strategies:
        raise ValueError(f"unknown resume strategy {strategy!r}. Allowed values: {sorted(known_strategies)}.")
    if strategy == "continue_current_wu":
        remove_checkpoint(workspace_root)
    elif strategy == "restart_wu":
        _force_status_in_queue(workspace_root)
        remove_checkpoint(workspace_root)
    else:  # strategy == "drain_and_resume" (guard above ensures only valid values reach here)
        remove_checkpoint(workspace_root)
        request_drain(workspace_root)
