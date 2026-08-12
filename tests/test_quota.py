"""Tests for src/devbench/quota.py -- detection tier, wait tier, probe, and checkpoint.

Coverage requirement: 100% line + branch on the detection-tier symbols (FR-2.1
/ FR-2.2 / FR-2.3), the wait-tier symbols (FR-2.4), the recovery-probe
symbols (FR-2.5), and the checkpoint / resume-strategy symbols (FR-2.6,
FR-2.8) added across E2-F1-S1-T1, E2-F1-S2-T1, E2-F1-S2-T2, and E2-F1-S2-T3.

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
- BackoffConfig: field defaults and the __post_init__ validation of its
  documented invariants (initial_seconds > 0, max_seconds >= initial_seconds,
  multiplier >= 1.0, jitter in [0, 1]) (FR-2.4).
- _wait_toward_reset: stepped sleeps in poll_interval_seconds increments
  toward a known future reset_at (never one blind long sleep), the
  [QUOTA_POLLING] heartbeat emitted before each step, and the local-
  accumulator elapsed bookkeeping that terminates deterministically under a
  mocked clock (FR-2.4, spec AC-14).
- wait_for_reset: the TDI-003a elapsed-reset short-circuit (zero probe
  calls), the cadence guard and the poll_interval_seconds fail-fast guard
  (both ValueError before any I/O), the max_wait_seconds == 0 fast path, the
  full poll-loop call order (timeout -> elapsed-reset short-circuit ->
  jittered delay -> heartbeat -> probe -> sleep -> delay growth), jittered
  backoff bounds and growth/clamp, heartbeat coverage (per-poll, on the
  reset_at path, reaching the root logging handler, and swallowed emitter
  failures), and RecoveryProbeUnavailableError / other probe_fn exception
  propagation (FR-2.4, spec AC-15 through AC-17, S10.1).
- Every wait test mocks the clock at devbench.quota._get_current_utc and
  patches asyncio.sleep with a simulated clock that accumulates the fake
  sleep durations -- no test performs a real sleep (spec S10.1).
- recovery_probe / _probe_api_call (FR-2.5): the five-arm exception ladder
  ordering (QuotaExhaustedError -> AuthenticationError/PermissionDeniedError
  -> APIError -> bare AnthropicError -> any other), the two ValueError guards
  before any I/O, and the success path issuing exactly one 1-token
  messages.create against RECOVERY_PROBE_MODEL. All probe tests install a
  fake anthropic exception hierarchy via unittest.mock.patch.multiple that
  mirrors the real subclass graph (AuthenticationError and
  PermissionDeniedError subclass APIError; APIError subclasses
  AnthropicError) -- no network access, no credentials (spec S10.1).
- QuotaCheckpoint / save_checkpoint / load_checkpoint / remove_checkpoint
  (FR-2.6, spec S5.1, AC-19): the mkstemp + Path.replace atomic write (a
  simulated mid-write crash leaves the previous checkpoint intact), the
  empty-reason and naive-datetime ValueError guards, a None reset_at round
  -tripping to None, the four-field round trip, load_checkpoint's path-
  naming ValueError on malformed JSON / missing keys / unparseable
  datetimes, and remove_checkpoint's idempotency. All checkpoint tests run
  against real tmp_path workspaces -- no mocked filesystem.
- _apply_resume_strategy / _force_status_in_queue (FR-2.8, spec AC-23): the
  three resume strategies (continue_current_wu, restart_wu,
  drain_and_resume) exercised against real BACKLOG.md + work-unit files
  under tmp_path via the real BacklogManager.force_status and
  devbench.drain.request_drain primitives, plus the ValueError on an
  unknown strategy naming the allowed set.

Issue #234 (Appendix A QW-1 / QW-2 / QW-10).
Issue #236 (Appendix A QW-3 / QW-4 / QW-5).
AC-234-1, AC-234a-1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from devbench.quota import (
    _QUOTA_MARKERS,
    ApiBillingError,
    BackoffConfig,
    BedrockThrottleError,
    QuotaCheckpoint,
    QuotaExhaustedError,
    RecoveryProbeUnavailableError,
    SdkCreditExhaustedError,
    SubscriptionRateLimitError,
    _apply_resume_strategy,
    _checkpoint_path,
    _has_quota_marker,
    _has_verbatim_quota_marker,
    _parse_reset_at_from_text,
    _probe_api_call,
    _wait_toward_reset,
    detect_quota_error,
    load_checkpoint,
    recovery_probe,
    remove_checkpoint,
    save_checkpoint,
    wait_for_reset,
)

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    """A mutable clock box read by a mocked ``_get_current_utc``.

    ``self.now`` only ever advances when ``advance()`` is called -- by a
    fake ``asyncio.sleep`` (see :func:`_make_fake_sleep`) -- never on its
    own. This keeps the wait engine's clock reads and its internal
    "elapsed" bookkeeping deterministic under test without ever blocking on
    a real timer (spec S10.1: the simulated clock accumulates the fake
    sleep durations).
    """

    def __init__(self, start: datetime) -> None:
        self.now = start

    def get(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _make_fake_sleep(clock: _FakeClock, recorder: list[tuple[str, float]] | None = None) -> AsyncMock:
    """Build an ``AsyncMock`` standing in for ``asyncio.sleep`` that advances ``clock``.

    Never performs a real sleep (spec AC-E2-F1-S2-T1-7): the mock's
    ``side_effect`` advances the fake clock by the requested duration
    instead of blocking.
    """

    async def _sleep(seconds: float) -> None:
        if recorder is not None:
            recorder.append(("sleep", seconds))
        clock.advance(seconds)

    return AsyncMock(side_effect=_sleep)


class _FixedRng:
    """Stand-in for ``secrets.SystemRandom`` with a fixed ``uniform()`` result.

    Makes the jittered-backoff delay in ``wait_for_reset`` deterministic for
    testing, instead of depending on the real cryptographic RNG.
    """

    def __init__(self, value: float) -> None:
        self._value = value

    def uniform(self, _a: float, _b: float) -> float:
        return self._value


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


# ---------------------------------------------------------------------------
# Fake anthropic exception hierarchy (FR-2.5, spec S10.1)
#
# Reproduces the REAL anthropic SDK's subclass graph -- AuthenticationError
# and PermissionDeniedError subclass APIError; APIError subclasses
# AnthropicError -- so the ordering tests below exercise the genuine MRO
# hazard the load-bearing except-clause ordering in recovery_probe guards
# against, rather than a flattened stand-in that would pass even if the
# APIError arm were moved above the auth arms.
#
# Reusable: downstream E2-F4 loop tests that also need to simulate the probe
# channel without depending on the real anthropic SDK can import
# ``fake_anthropic_hierarchy`` and ``patch_anthropic`` from this module.
# ---------------------------------------------------------------------------


class _FakeAnthropicError(Exception):
    """Fake stand-in for anthropic.AnthropicError -- root of the SDK hierarchy."""


class _FakeAPIError(_FakeAnthropicError):
    """Fake stand-in for anthropic.APIError -- subclasses AnthropicError."""


class _FakeAuthenticationError(_FakeAPIError):
    """Fake stand-in for anthropic.AuthenticationError -- subclasses APIError."""


class _FakePermissionDeniedError(_FakeAPIError):
    """Fake stand-in for anthropic.PermissionDeniedError -- subclasses APIError."""


@pytest.fixture
def fake_anthropic_hierarchy() -> dict[str, type[Exception]]:
    """The four fake anthropic exception classes, keyed by their real SDK names.

    Installed on the real (already-imported) ``anthropic`` module via
    ``unittest.mock.patch.multiple`` in every probe test -- see
    :func:`patch_anthropic`.
    """
    return {
        "AnthropicError": _FakeAnthropicError,
        "APIError": _FakeAPIError,
        "AuthenticationError": _FakeAuthenticationError,
        "PermissionDeniedError": _FakePermissionDeniedError,
    }


def _make_fake_anthropic_client(
    *, side_effect: BaseException | None = None, response: object = "probe-ok"
) -> MagicMock:
    """Build a fake ``anthropic.Anthropic()`` client instance.

    ``client.messages.create(...)`` either raises ``side_effect`` or returns
    ``response`` -- no real HTTP call is ever made.
    """
    client = MagicMock(name="fake_anthropic_client")
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        client.messages.create.return_value = response
    return client


def patch_anthropic(
    hierarchy: dict[str, type[Exception]],
    *,
    anthropic_ctor: MagicMock | None = None,
) -> Any:
    """Return a ``patch.multiple`` context manager over the real ``anthropic`` module.

    Temporarily replaces ``AnthropicError``, ``APIError``,
    ``AuthenticationError``, ``PermissionDeniedError``, and ``Anthropic`` on
    the already-installed ``anthropic`` package for the duration of the
    context -- no ``sys.modules`` substitution, no network access, no
    credentials. ``anthropic_ctor`` defaults to a ``MagicMock`` that
    constructs no client (tests that expect a ``ValueError`` before any I/O
    assert this default mock was never called).
    """
    if anthropic_ctor is None:
        anthropic_ctor = MagicMock(name="fake_anthropic_ctor")
    return patch.multiple(
        "anthropic",
        Anthropic=anthropic_ctor,
        AnthropicError=hierarchy["AnthropicError"],
        APIError=hierarchy["APIError"],
        AuthenticationError=hierarchy["AuthenticationError"],
        PermissionDeniedError=hierarchy["PermissionDeniedError"],
    )


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


# ---------------------------------------------------------------------------
# Wait engine (FR-2.4). Every test below mocks the clock at
# ``devbench.quota._get_current_utc`` and patches ``asyncio.sleep`` -- never
# a real sleep -- per spec S10.1 / AC-E2-F1-S2-T1-7.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackoffConfigValidation:
    """BackoffConfig.__post_init__ enforces its documented invariants
    (fail-fast: no silent normalisation of an out-of-contract config)."""

    def test_default_construction_does_not_raise(self) -> None:
        BackoffConfig()

    @pytest.mark.parametrize("initial_seconds", [0, -1, -30])
    def test_non_positive_initial_seconds_raises(self, initial_seconds: int) -> None:
        with pytest.raises(ValueError, match="initial_seconds"):
            BackoffConfig(initial_seconds=initial_seconds, max_seconds=600)

    def test_max_seconds_less_than_initial_seconds_raises(self) -> None:
        with pytest.raises(ValueError, match="max_seconds"):
            BackoffConfig(initial_seconds=60, max_seconds=30)

    def test_max_seconds_equal_to_initial_seconds_does_not_raise(self) -> None:
        BackoffConfig(initial_seconds=60, max_seconds=60)

    @pytest.mark.parametrize("multiplier", [0.0, 0.5, 0.999])
    def test_multiplier_below_one_raises(self, multiplier: float) -> None:
        with pytest.raises(ValueError, match="multiplier"):
            BackoffConfig(multiplier=multiplier)

    def test_multiplier_equal_to_one_does_not_raise(self) -> None:
        BackoffConfig(multiplier=1.0)

    @pytest.mark.parametrize("jitter", [-0.1, -1.0, 1.1, 2.0])
    def test_jitter_outside_unit_interval_raises(self, jitter: float) -> None:
        with pytest.raises(ValueError, match="jitter"):
            BackoffConfig(jitter=jitter)

    @pytest.mark.parametrize("jitter", [0.0, 1.0])
    def test_jitter_at_unit_interval_bounds_does_not_raise(self, jitter: float) -> None:
        BackoffConfig(jitter=jitter)


@pytest.mark.unit
class TestWaitTowardReset:
    """_wait_toward_reset sleeps in poll_interval_seconds steps (spec AC-14)."""

    def test_steps_toward_future_reset_never_one_blind_sleep(self, caplog: pytest.LogCaptureFixture) -> None:
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            caplog.at_level(logging.INFO, logger="devbench.quota"),
        ):
            asyncio.run(
                _wait_toward_reset(
                    reset_at=_NOW + timedelta(seconds=130),
                    poll_interval_seconds=50,
                    max_wait_seconds=1000,
                )
            )
        sleep_durations = [call.args[0] for call in fake_sleep.await_args_list]
        # Never one blind long sleep of 130s: three bounded steps instead.
        assert sleep_durations == [50, 50, 30]
        assert len(sleep_durations) > 1
        heartbeat_lines = [r.getMessage() for r in caplog.records if "[QUOTA_POLLING]" in r.getMessage()]
        assert heartbeat_lines == [
            "[QUOTA_POLLING] elapsed=0s probe=0 next_in=50s",
            "[QUOTA_POLLING] elapsed=50s probe=0 next_in=50s",
            "[QUOTA_POLLING] elapsed=100s probe=0 next_in=30s",
        ]

    def test_elapsed_is_local_accumulator_not_clock_reads(self) -> None:
        """``elapsed`` is a local accumulator of the sleep durations already
        performed, not derived from re-reading the clock, so the step count
        is pre-computed from a single ``now`` snapshot and the wait
        terminates deterministically under the mocked clock."""
        clock = _FakeClock(_NOW)
        recorder: list[tuple[str, float]] = []
        fake_sleep = _make_fake_sleep(clock, recorder)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
        ):
            asyncio.run(
                _wait_toward_reset(
                    reset_at=_NOW + timedelta(seconds=95),
                    poll_interval_seconds=40,
                    max_wait_seconds=1000,
                )
            )
        durations = [d for _, d in recorder]
        assert durations == [40, 40, 15]
        assert sum(durations) == 95

    def test_reset_at_none_returns_without_sleeping(self) -> None:
        fake_sleep = AsyncMock()
        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", fake_sleep),
        ):
            asyncio.run(_wait_toward_reset(reset_at=None, poll_interval_seconds=30, max_wait_seconds=100))
        fake_sleep.assert_not_awaited()

    def test_reset_at_already_past_returns_without_sleeping(self) -> None:
        fake_sleep = AsyncMock()
        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", fake_sleep),
        ):
            asyncio.run(
                _wait_toward_reset(
                    reset_at=_NOW - timedelta(seconds=1),
                    poll_interval_seconds=30,
                    max_wait_seconds=100,
                )
            )
        fake_sleep.assert_not_awaited()

    def test_window_bounded_by_max_wait_seconds(self) -> None:
        """When max_wait_seconds is shorter than the gap to reset_at, the
        step window is capped by max_wait_seconds, not the full gap."""
        clock = _FakeClock(_NOW)
        recorder: list[tuple[str, float]] = []
        fake_sleep = _make_fake_sleep(clock, recorder)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
        ):
            asyncio.run(
                _wait_toward_reset(
                    reset_at=_NOW + timedelta(seconds=500),
                    poll_interval_seconds=20,
                    max_wait_seconds=45,
                )
            )
        assert sum(d for _, d in recorder) == 45

    def test_emit_structured_events_false_suppresses_heartbeat(self, caplog: pytest.LogCaptureFixture) -> None:
        """AC-E9-F1-S2-T1-3 (spec AC-9): emit_structured_events=False emits no [QUOTA_POLLING] line

        while the sleeps themselves are unaffected (same step durations as the
        default-True case in test_steps_toward_future_reset_never_one_blind_sleep)."""
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            caplog.at_level(logging.INFO, logger="devbench.quota"),
        ):
            asyncio.run(
                _wait_toward_reset(
                    reset_at=_NOW + timedelta(seconds=130),
                    poll_interval_seconds=50,
                    max_wait_seconds=1000,
                    emit_structured_events=False,
                )
            )
        sleep_durations = [call.args[0] for call in fake_sleep.await_args_list]
        assert sleep_durations == [50, 50, 30]
        heartbeat_lines = [r.getMessage() for r in caplog.records if "[QUOTA_POLLING]" in r.getMessage()]
        assert heartbeat_lines == []

    def test_emit_structured_events_default_true_matches_documented_default(self) -> None:
        """The keyword-only parameter defaults to True (spec AC-9: default emits exactly as today)."""
        import inspect

        signature = inspect.signature(_wait_toward_reset)
        assert signature.parameters["emit_structured_events"].default is True


@pytest.mark.unit
class TestWaitForResetTDI003a:
    """An elapsed known reset_at short-circuits to True without probing (TDI-003a, spec AC-15)."""

    def test_elapsed_reset_returns_true_without_probe(self) -> None:
        probe_fn = MagicMock(return_value=False)
        fake_sleep = AsyncMock()
        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", fake_sleep),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=_NOW - timedelta(seconds=1),
                    poll_interval_seconds=30,
                    max_wait_seconds=300,
                    probe_fn=probe_fn,
                )
            )
        assert result is True
        probe_fn.assert_not_called()
        fake_sleep.assert_not_awaited()

    def test_reset_at_exactly_now_counts_as_elapsed(self) -> None:
        """``now >= reset_at`` -- a reset_at equal to now counts as elapsed."""
        probe_fn = MagicMock(return_value=False)
        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=_NOW,
                    poll_interval_seconds=30,
                    max_wait_seconds=300,
                    probe_fn=probe_fn,
                )
            )
        assert result is True
        probe_fn.assert_not_called()


@pytest.mark.unit
class TestWaitForResetCadenceGuard:
    """backoff_config.initial_seconds != poll_interval_seconds raises before I/O (spec AC-16)."""

    def test_mismatch_raises_value_error_before_any_io(self) -> None:
        probe_fn = MagicMock(return_value=True)
        mismatched = BackoffConfig(initial_seconds=45)
        with (
            patch("devbench.quota._get_current_utc") as fake_clock,
            patch("asyncio.sleep", AsyncMock()) as fake_sleep,
            pytest.raises(ValueError, match="must equal poll_interval_seconds"),
        ):
            asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=30,
                    max_wait_seconds=300,
                    probe_fn=probe_fn,
                    backoff_config=mismatched,
                )
            )
        probe_fn.assert_not_called()
        fake_sleep.assert_not_awaited()
        fake_clock.assert_not_called()

    def test_matching_cadence_does_not_raise(self) -> None:
        probe_fn = MagicMock(return_value=True)
        matching = BackoffConfig(initial_seconds=30)
        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", AsyncMock()),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=30,
                    max_wait_seconds=300,
                    probe_fn=probe_fn,
                    backoff_config=matching,
                )
            )
        assert result is True


@pytest.mark.unit
class TestWaitForResetPollIntervalGuard:
    """poll_interval_seconds <= 0 raises ValueError before any I/O, so an
    invalid cadence fails loudly instead of busy-spinning (fail-fast fix
    alongside the cadence guard)."""

    @pytest.mark.parametrize("poll_interval_seconds", [0, -1, -30])
    def test_non_positive_poll_interval_raises_before_any_io(self, poll_interval_seconds: int) -> None:
        probe_fn = MagicMock(return_value=True)
        with (
            patch("devbench.quota._get_current_utc") as fake_clock,
            patch("asyncio.sleep", AsyncMock()) as fake_sleep,
            pytest.raises(ValueError, match="poll_interval_seconds"),
        ):
            asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=poll_interval_seconds,
                    max_wait_seconds=300,
                    probe_fn=probe_fn,
                )
            )
        probe_fn.assert_not_called()
        fake_sleep.assert_not_awaited()
        fake_clock.assert_not_called()


@pytest.mark.unit
class TestWaitForResetMaxWaitZero:
    """max_wait_seconds == 0 returns False immediately, no probe, no sleep, no I/O."""

    def test_zero_max_wait_returns_false_immediately(self) -> None:
        probe_fn = MagicMock(return_value=True)
        with (
            patch("devbench.quota._get_current_utc") as fake_clock,
            patch("asyncio.sleep", AsyncMock()) as fake_sleep,
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=_NOW + timedelta(seconds=10),
                    poll_interval_seconds=30,
                    max_wait_seconds=0,
                    probe_fn=probe_fn,
                )
            )
        assert result is False
        probe_fn.assert_not_called()
        fake_sleep.assert_not_awaited()
        fake_clock.assert_not_called()


@pytest.mark.unit
class TestWaitForResetLoopOrder:
    """Full-loop call order per FR-2.4: timeout check, elapsed-reset
    short-circuit, jittered delay computation, heartbeat, probe, sleep,
    delay growth (spec AC-E2-F1-S2-T1-5).

    The elapsed-reset short-circuit and probe are mutually exclusive per
    call to ``reset_at`` being known or not (TestWaitForResetTDI003a already
    proves the short-circuit pre-empts the probe when reset_at is known).
    This class proves the remaining order -- heartbeat before probe before
    sleep, repeated per iteration, with the delay growing between
    iterations -- on the probe-only path (``reset_at=None``).
    """

    def test_heartbeat_probe_sleep_order_repeats_per_iteration(self) -> None:
        clock = _FakeClock(_NOW)
        recorder: list[str] = []

        def _fake_heartbeat(*, elapsed: float, probe: int, next_in: float) -> None:
            recorder.append("heartbeat")

        probe_results = [False, False, True]

        def _fake_probe() -> bool:
            recorder.append("probe")
            return probe_results.pop(0)

        async def _fake_sleep(seconds: float) -> None:
            recorder.append("sleep")
            clock.advance(seconds)

        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("devbench.quota._emit_polling_heartbeat", side_effect=_fake_heartbeat),
            patch("asyncio.sleep", side_effect=_fake_sleep),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=_fake_probe,
                )
            )
        assert result is True
        # Two full cycles (probe still exhausted -> sleep), then a third
        # iteration whose probe recovers and returns immediately (no sleep).
        assert recorder == [
            "heartbeat",
            "probe",
            "sleep",
            "heartbeat",
            "probe",
            "sleep",
            "heartbeat",
            "probe",
        ]


@pytest.mark.unit
class TestWaitForResetBackoffBounds:
    """Jittered delay stays within the configured bounds and grows per the
    growth factor, clamped at ``max_seconds`` (spec AC-E2-F1-S2-T1-8 approach bullet)."""

    def test_delay_grows_by_multiplier_and_clamps_at_max_seconds(self) -> None:
        clock = _FakeClock(_NOW)
        recorder: list[tuple[str, float]] = []
        fake_sleep = _make_fake_sleep(clock, recorder)
        probe_results = [False, False, False, False, True]
        backoff = BackoffConfig(initial_seconds=10, max_seconds=100, multiplier=3.0, jitter=0.5)

        def _fake_probe() -> bool:
            return probe_results.pop(0)

        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            # Zero jitter (fixed rng.uniform == 0.0) isolates the growth
            # factor from the jitter contribution for this assertion.
            patch("devbench.quota.secrets.SystemRandom", return_value=_FixedRng(0.0)),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=10000,
                    probe_fn=_fake_probe,
                    backoff_config=backoff,
                )
            )
        assert result is True
        delays = [d for _, d in recorder]
        # raw_delay: 10 -> 30 -> 90 -> clamp(270, 100)=100 -> clamp(300,100)=100
        assert delays == [10, 30, 90, 100]

    @pytest.mark.parametrize(
        ("jitter_uniform_value", "expected_delay"),
        [
            (0.3, 13.0),
            (-0.3, 7.0),
        ],
    )
    def test_delay_applies_jitter_within_configured_bounds(
        self, jitter_uniform_value: float, expected_delay: float
    ) -> None:
        clock = _FakeClock(_NOW)
        recorder: list[tuple[str, float]] = []
        fake_sleep = _make_fake_sleep(clock, recorder)
        # multiplier=1.0 keeps raw_delay constant so only the jitter term
        # varies the observed delay.
        backoff = BackoffConfig(initial_seconds=10, max_seconds=1000, multiplier=1.0, jitter=0.3)
        probe_results = [False, True]

        def _fake_probe() -> bool:
            return probe_results.pop(0)

        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            patch("devbench.quota.secrets.SystemRandom", return_value=_FixedRng(jitter_uniform_value)),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=10000,
                    probe_fn=_fake_probe,
                    backoff_config=backoff,
                )
            )
        assert result is True
        assert recorder == [("sleep", expected_delay)]


@pytest.mark.unit
class TestWaitForResetHeartbeat:
    """Heartbeat coverage per spec S10.1: per poll, on the reset_at path,
    reaching the root logging handler, and failure tolerance."""

    def test_heartbeat_emitted_once_per_poll(self, caplog: pytest.LogCaptureFixture) -> None:
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        probe_results = [False, True]

        def _fake_probe() -> bool:
            return probe_results.pop(0)

        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            patch("devbench.quota.secrets.SystemRandom", return_value=_FixedRng(0.0)),
            caplog.at_level(logging.INFO, logger="devbench.quota"),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=_fake_probe,
                )
            )
        assert result is True
        polling_lines = [r.getMessage() for r in caplog.records if "[QUOTA_POLLING]" in r.getMessage()]
        # Default BackoffConfig multiplier is 2.0: raw_delay grows 10 -> 20
        # between the first and second poll (jitter is pinned to 0 above).
        assert polling_lines == [
            "[QUOTA_POLLING] elapsed=0s probe=1 next_in=10s",
            "[QUOTA_POLLING] elapsed=10s probe=2 next_in=20s",
        ]

    def test_heartbeat_on_reset_at_path_via_wait_for_reset(self, caplog: pytest.LogCaptureFixture) -> None:
        """The reset_at (probe=0) heartbeat fires through the full
        ``wait_for_reset`` call, not just the internal stepper directly."""
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        probe_fn = MagicMock(return_value=False)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            caplog.at_level(logging.INFO, logger="devbench.quota"),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=_NOW + timedelta(seconds=20),
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=probe_fn,
                )
            )
        assert result is True
        probe_fn.assert_not_called()
        polling_lines = [r.getMessage() for r in caplog.records if "[QUOTA_POLLING]" in r.getMessage()]
        assert polling_lines == [
            "[QUOTA_POLLING] elapsed=0s probe=0 next_in=10s",
            "[QUOTA_POLLING] elapsed=10s probe=0 next_in=10s",
        ]

    def test_heartbeat_reaches_root_logging_handler(self, caplog: pytest.LogCaptureFixture) -> None:
        """caplog captures the heartbeat at the ROOT logger (no explicit
        ``logger=`` scope), proving the record propagates all the way up
        from ``devbench.quota`` rather than being swallowed en route.

        ``reset_at=None`` so the probe path (which always emits a heartbeat
        before consulting the probe) runs instead of the TDI-003a
        short-circuit, which resolves before any heartbeat would be emitted.
        """
        probe_fn = MagicMock(return_value=True)
        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", AsyncMock()),
            caplog.at_level(logging.INFO),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=probe_fn,
                )
            )
        assert result is True
        assert any("[QUOTA_POLLING]" in r.getMessage() for r in caplog.records)

    def test_heartbeat_failure_is_swallowed(self) -> None:
        """A raising log handler/emitter never aborts the wait (sanctioned
        swallow, spec S7.1) -- the wait still resolves normally."""
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        probe_results = [False, True]

        def _fake_probe() -> bool:
            return probe_results.pop(0)

        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            patch("devbench.quota.logger.info", side_effect=RuntimeError("handler exploded")),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=_fake_probe,
                )
            )
        assert result is True

    def test_emit_structured_events_false_suppresses_probe_loop_heartbeat(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-E9-F1-S2-T1-3 (spec AC-9): emit_structured_events=False emits no [QUOTA_POLLING]

        on the probe-loop path either (mirrors test_heartbeat_emitted_once_per_poll with the
        flag flipped) while the recovery outcome is unchanged."""
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        probe_results = [False, True]

        def _fake_probe() -> bool:
            return probe_results.pop(0)

        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            patch("devbench.quota.secrets.SystemRandom", return_value=_FixedRng(0.0)),
            caplog.at_level(logging.INFO, logger="devbench.quota"),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=_fake_probe,
                    emit_structured_events=False,
                )
            )
        assert result is True
        polling_lines = [r.getMessage() for r in caplog.records if "[QUOTA_POLLING]" in r.getMessage()]
        assert polling_lines == []

    def test_emit_structured_events_false_suppresses_reset_at_path_heartbeat(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-E9-F1-S2-T1-3 (spec AC-9): the flag also reaches the _wait_toward_reset

        heartbeat when threaded through the full wait_for_reset call (mirrors
        test_heartbeat_on_reset_at_path_via_wait_for_reset with the flag flipped)."""
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        probe_fn = MagicMock(return_value=False)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
            caplog.at_level(logging.INFO, logger="devbench.quota"),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=_NOW + timedelta(seconds=20),
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=probe_fn,
                    emit_structured_events=False,
                )
            )
        assert result is True
        probe_fn.assert_not_called()
        polling_lines = [r.getMessage() for r in caplog.records if "[QUOTA_POLLING]" in r.getMessage()]
        assert polling_lines == []

    def test_emit_structured_events_default_true_matches_documented_default(self) -> None:
        """The keyword-only parameter defaults to True (spec AC-9: default emits exactly as today)."""
        import inspect

        signature = inspect.signature(wait_for_reset)
        assert signature.parameters["emit_structured_events"].default is True


@pytest.mark.unit
class TestWaitForResetTimeoutAndExceptions:
    """Timeout returns False fast; probe exceptions propagate out of the loop."""

    def test_max_wait_timeout_returns_false(self) -> None:
        clock = _FakeClock(_NOW)
        fake_sleep = _make_fake_sleep(clock)
        probe_fn = MagicMock(return_value=False)
        with (
            patch("devbench.quota._get_current_utc", side_effect=clock.get),
            patch("asyncio.sleep", fake_sleep),
        ):
            result = asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=25,
                    probe_fn=probe_fn,
                )
            )
        assert result is False
        assert probe_fn.called

    def test_recovery_probe_unavailable_error_propagates(self) -> None:
        def _raising_probe() -> bool:
            raise RecoveryProbeUnavailableError("no credentials configured")

        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(RecoveryProbeUnavailableError),
        ):
            asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=_raising_probe,
                )
            )

    def test_other_probe_exception_propagates(self) -> None:
        def _raising_probe() -> bool:
            raise RuntimeError("upstream network error")

        with (
            patch("devbench.quota._get_current_utc", return_value=_NOW),
            patch("asyncio.sleep", AsyncMock()),
            pytest.raises(RuntimeError, match="upstream network error"),
        ):
            asyncio.run(
                wait_for_reset(
                    reset_at=None,
                    poll_interval_seconds=10,
                    max_wait_seconds=1000,
                    probe_fn=_raising_probe,
                )
            )


@pytest.mark.unit
class TestProbeApiCall:
    """_probe_api_call issues one 1-token messages.create against RECOVERY_PROBE_MODEL."""

    def test_success_issues_one_token_call_against_recovery_probe_model(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        from devbench.constants import RECOVERY_PROBE_MODEL

        fake_client = _make_fake_anthropic_client(response="probe-ok")
        anthropic_ctor = MagicMock(return_value=fake_client)
        with patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor):
            result = _probe_api_call(5.0, 1)

        anthropic_ctor.assert_called_once_with(timeout=5.0)
        fake_client.messages.create.assert_called_once_with(
            model=RECOVERY_PROBE_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "x"}],
        )
        assert result == "probe-ok"

    def test_prompt_size_scales_with_request_size_tokens(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        fake_client = _make_fake_anthropic_client(response="probe-ok")
        anthropic_ctor = MagicMock(return_value=fake_client)
        with patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor):
            _probe_api_call(5.0, 7)

        call_kwargs = fake_client.messages.create.call_args.kwargs
        assert call_kwargs["messages"] == [{"role": "user", "content": "x" * 7}]
        assert call_kwargs["max_tokens"] == 1


@pytest.mark.unit
class TestRecoveryProbeGuards:
    """AC-E2-F1-S2-T2-3: guard ValueErrors fire before any I/O."""

    @pytest.mark.parametrize("timeout_seconds", [0, -1, -0.5])
    def test_timeout_seconds_not_positive_raises_before_io(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]], timeout_seconds: float
    ) -> None:
        anthropic_ctor = MagicMock()
        with (
            patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor),
            pytest.raises(ValueError, match="timeout_seconds"),
        ):
            recovery_probe(timeout_seconds=timeout_seconds, request_size_tokens=1)
        anthropic_ctor.assert_not_called()

    @pytest.mark.parametrize("request_size_tokens", [0, -1, -5])
    def test_request_size_tokens_below_one_raises_before_io(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]], request_size_tokens: int
    ) -> None:
        anthropic_ctor = MagicMock()
        with (
            patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor),
            pytest.raises(ValueError, match="request_size_tokens"),
        ):
            recovery_probe(timeout_seconds=5.0, request_size_tokens=request_size_tokens)
        anthropic_ctor.assert_not_called()


@pytest.mark.unit
class TestRecoveryProbeExceptionOrdering:
    """AC-E2-F1-S2-T2-1/2 (spec FR-2.5, AC-18): the five-arm exception ladder,
    in the exact order load-bearing per the docstring in recovery_probe.

    QuotaExhaustedError -> False
    AuthenticationError / PermissionDeniedError (subclass APIError) -> RecoveryProbeUnavailableError
    APIError (not an auth subclass) -> False
    bare AnthropicError -> RecoveryProbeUnavailableError
    any other exception -> False
    """

    def test_quota_exhausted_error_returns_false(self, fake_anthropic_hierarchy: dict[str, type[Exception]]) -> None:
        quota_exc = SubscriptionRateLimitError(reset_at=None, raw_error="raw", source="anthropic-api")
        fake_client = _make_fake_anthropic_client(side_effect=quota_exc)
        anthropic_ctor = MagicMock(return_value=fake_client)
        with patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor):
            result = recovery_probe(timeout_seconds=5.0, request_size_tokens=1)
        assert result is False

    def test_authentication_error_raises_recovery_probe_unavailable(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        """PROVES the AuthenticationError arm is not swallowed by APIError:
        AuthenticationError subclasses APIError, so if the APIError arm were
        listed first this would return False instead of raising."""
        auth_exc = fake_anthropic_hierarchy["AuthenticationError"]("invalid api key")
        assert isinstance(auth_exc, fake_anthropic_hierarchy["APIError"])
        fake_client = _make_fake_anthropic_client(side_effect=auth_exc)
        anthropic_ctor = MagicMock(return_value=fake_client)
        with (
            patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor),
            pytest.raises(RecoveryProbeUnavailableError, match="AuthenticationError"),
        ):
            recovery_probe(timeout_seconds=5.0, request_size_tokens=1)

    def test_permission_denied_error_raises_recovery_probe_unavailable(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        """PROVES the PermissionDeniedError arm is not swallowed by APIError:
        PermissionDeniedError subclasses APIError, so if the APIError arm
        were listed first this would return False instead of raising."""
        perm_exc = fake_anthropic_hierarchy["PermissionDeniedError"]("access denied")
        assert isinstance(perm_exc, fake_anthropic_hierarchy["APIError"])
        fake_client = _make_fake_anthropic_client(side_effect=perm_exc)
        anthropic_ctor = MagicMock(return_value=fake_client)
        with (
            patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor),
            pytest.raises(RecoveryProbeUnavailableError, match="PermissionDeniedError"),
        ):
            recovery_probe(timeout_seconds=5.0, request_size_tokens=1)

    def test_api_error_not_auth_subclass_returns_false(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        api_exc = fake_anthropic_hierarchy["APIError"]("connection reset")
        fake_client = _make_fake_anthropic_client(side_effect=api_exc)
        anthropic_ctor = MagicMock(return_value=fake_client)
        with patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor):
            result = recovery_probe(timeout_seconds=5.0, request_size_tokens=1)
        assert result is False

    def test_bare_anthropic_error_raises_recovery_probe_unavailable(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        """A bare AnthropicError (not an APIError) means no credential is
        configured at all -- e.g. client construction failed."""
        bare_exc = fake_anthropic_hierarchy["AnthropicError"]("no credential configured")
        assert not isinstance(bare_exc, fake_anthropic_hierarchy["APIError"])
        fake_client = _make_fake_anthropic_client(side_effect=bare_exc)
        anthropic_ctor = MagicMock(return_value=fake_client)
        with (
            patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor),
            pytest.raises(RecoveryProbeUnavailableError, match="no usable Anthropic API credential"),
        ):
            recovery_probe(timeout_seconds=5.0, request_size_tokens=1)

    def test_any_other_exception_returns_false(self, fake_anthropic_hierarchy: dict[str, type[Exception]]) -> None:
        fake_client = _make_fake_anthropic_client(side_effect=RuntimeError("unexpected"))
        anthropic_ctor = MagicMock(return_value=fake_client)
        with patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor):
            result = recovery_probe(timeout_seconds=5.0, request_size_tokens=1)
        assert result is False


@pytest.mark.unit
class TestRecoveryProbeSuccessPath:
    """A success returns True and issues exactly one 1-token probe call."""

    def test_success_returns_true_and_issues_single_probe_call(
        self, fake_anthropic_hierarchy: dict[str, type[Exception]]
    ) -> None:
        from devbench.constants import RECOVERY_PROBE_MODEL

        fake_client = _make_fake_anthropic_client(response="probe-ok")
        anthropic_ctor = MagicMock(return_value=fake_client)
        with patch_anthropic(fake_anthropic_hierarchy, anthropic_ctor=anthropic_ctor):
            result = recovery_probe(timeout_seconds=5.0, request_size_tokens=3)

        assert result is True
        fake_client.messages.create.assert_called_once_with(
            model=RECOVERY_PROBE_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "xxx"}],
        )


# ---------------------------------------------------------------------------
# Checkpoint / resume-strategy fixtures (FR-2.6, FR-2.8, spec S5.1, AC-23)
# ---------------------------------------------------------------------------

_SAVED_AT = datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC)
_RESET_AT = datetime(2026, 3, 1, 10, 30, 0, tzinfo=UTC)


def _make_checkpoint(
    *,
    reason: str = "anthropic-api",
    reset_at: datetime | None = _RESET_AT,
    saved_at: datetime = _SAVED_AT,
    session_name: str = "default",
) -> QuotaCheckpoint:
    return QuotaCheckpoint(reason=reason, reset_at=reset_at, saved_at=saved_at, session_name=session_name)


def _write_backlog_workspace(tmp_path: Path, units: list[tuple[str, str]]) -> Path:
    """Write a real BACKLOG.md plus one work-unit .md file per entry in *units*.

    Args:
        tmp_path: Workspace root.
        units: List of ``(unit_id, status)`` pairs; ``status`` is the exact
            lowercase CLI-form status string (e.g. ``"in-progress"``).

    Returns:
        The workspace root (``tmp_path``), the same value passed in --
        returned for call-site symmetry with the other checkpoint helpers.
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    header = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
    )
    rows = []
    for unit_id, status in units:
        row = f"| {unit_id} | Test Task {unit_id} | Task | {status} | None | git-repo | `backlog/{unit_id}.md` |\n"
        rows.append(row)
        wu_content = f"# {unit_id}: Test Task {unit_id}\n\n## Status: {status}\n\n## Comments\n"
        (backlog_dir / f"{unit_id}.md").write_text(wu_content)
    (tmp_path / "BACKLOG.md").write_text(header + "".join(rows))
    return tmp_path


def _read_status_line(tmp_path: Path, unit_id: str) -> str:
    content = (tmp_path / "backlog" / f"{unit_id}.md").read_text()
    for line in content.splitlines():
        if line.startswith("## Status:"):
            return line.removeprefix("## Status:").strip()
    raise AssertionError(f"No '## Status:' line found for {unit_id}")


@pytest.mark.unit
class TestCheckpointPath:
    """_checkpoint_path targets <workspace_root>/.devbench/quota_pause.json (spec S5.1)."""

    def test_checkpoint_path_targets_devbench_quota_pause_json(self, tmp_path: Path) -> None:
        path = _checkpoint_path(tmp_path)
        assert path == tmp_path / ".devbench" / "quota_pause.json"


@pytest.mark.unit
class TestSaveCheckpointValidation:
    """AC-E2-F1-S2-T3-2: save_checkpoint raises ValueError before any write on bad input."""

    def test_empty_reason_raises_value_error(self, tmp_path: Path) -> None:
        checkpoint = _make_checkpoint(reason="")
        with pytest.raises(ValueError, match="reason"):
            save_checkpoint(checkpoint, tmp_path)
        assert not _checkpoint_path(tmp_path).exists()

    def test_naive_saved_at_raises_value_error(self, tmp_path: Path) -> None:
        checkpoint = _make_checkpoint(saved_at=_SAVED_AT.replace(tzinfo=None))
        with pytest.raises(ValueError, match="saved_at"):
            save_checkpoint(checkpoint, tmp_path)
        assert not _checkpoint_path(tmp_path).exists()

    def test_naive_reset_at_raises_value_error(self, tmp_path: Path) -> None:
        checkpoint = _make_checkpoint(reset_at=_RESET_AT.replace(tzinfo=None))
        with pytest.raises(ValueError, match="reset_at"):
            save_checkpoint(checkpoint, tmp_path)
        assert not _checkpoint_path(tmp_path).exists()

    def test_none_reset_at_is_accepted(self, tmp_path: Path) -> None:
        checkpoint = _make_checkpoint(reset_at=None)
        save_checkpoint(checkpoint, tmp_path)
        loaded = load_checkpoint(tmp_path)
        assert loaded is not None
        assert loaded.reset_at is None


@pytest.mark.unit
class TestSaveCheckpointAtomicity:
    """AC-E2-F1-S2-T3-1 (spec AC-19): mkstemp + Path.replace, never a direct open of the final path."""

    def test_write_goes_through_mkstemp(self, tmp_path: Path) -> None:
        import tempfile as tempfile_module

        real_mkstemp = tempfile_module.mkstemp
        calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def _spy_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
            calls.append((args, kwargs))
            return real_mkstemp(*args, **kwargs)

        with patch("devbench.quota.tempfile.mkstemp", side_effect=_spy_mkstemp) as mock_mkstemp:
            save_checkpoint(_make_checkpoint(), tmp_path)

        mock_mkstemp.assert_called_once()
        assert calls[0][1].get("dir") == str(_checkpoint_path(tmp_path).parent)

    def test_mid_write_crash_leaves_previous_checkpoint_intact(self, tmp_path: Path) -> None:
        original = _make_checkpoint(reason="anthropic-api")
        save_checkpoint(original, tmp_path)
        original_bytes = _checkpoint_path(tmp_path).read_bytes()

        replacement = _make_checkpoint(reason="bedrock")
        with (
            patch("devbench.quota.Path.replace", side_effect=OSError("simulated crash mid-write")),
            pytest.raises(OSError, match="simulated crash mid-write"),
        ):
            save_checkpoint(replacement, tmp_path)

        assert _checkpoint_path(tmp_path).read_bytes() == original_bytes
        loaded = load_checkpoint(tmp_path)
        assert loaded is not None
        assert loaded.reason == "anthropic-api"

    def test_no_leftover_temp_file_after_mid_write_crash(self, tmp_path: Path) -> None:
        with (
            patch("devbench.quota.Path.replace", side_effect=OSError("simulated crash mid-write")),
            pytest.raises(OSError),
        ):
            save_checkpoint(_make_checkpoint(), tmp_path)

        leftovers = list((tmp_path / ".devbench").glob("*.tmp"))
        assert leftovers == []


@pytest.mark.unit
class TestCheckpointRoundTrip:
    """AC-E2-F1-S2-T3-4: save then load reproduces all four S5.1 fields exactly."""

    def test_round_trip_reproduces_all_fields(self, tmp_path: Path) -> None:
        checkpoint = _make_checkpoint(
            reason="anthropic-api",
            reset_at=_RESET_AT,
            saved_at=_SAVED_AT,
            session_name="my-session",
        )
        save_checkpoint(checkpoint, tmp_path)
        loaded = load_checkpoint(tmp_path)

        assert loaded is not None
        assert loaded.reason == "anthropic-api"
        assert loaded.reset_at == _RESET_AT
        assert loaded.saved_at == _SAVED_AT
        assert loaded.session_name == "my-session"

    def test_load_returns_none_when_no_checkpoint_exists(self, tmp_path: Path) -> None:
        assert load_checkpoint(tmp_path) is None


@pytest.mark.unit
class TestLoadCheckpointErrors:
    """AC-E2-F1-S2-T3-4: load_checkpoint raises ValueError naming the checkpoint path."""

    def test_malformed_json_raises_value_error_naming_path(self, tmp_path: Path) -> None:
        path = _checkpoint_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{not valid json")

        with pytest.raises(ValueError, match=r"not valid|invalid JSON") as exc_info:
            load_checkpoint(tmp_path)
        assert str(path) in str(exc_info.value)

    @pytest.mark.parametrize(
        "missing_key",
        ["reason", "reset_at", "saved_at", "session_name"],
    )
    def test_missing_required_key_raises_value_error_naming_path(self, tmp_path: Path, missing_key: str) -> None:
        path = _checkpoint_path(tmp_path)
        path.parent.mkdir(parents=True)
        data = {
            "reason": "anthropic-api",
            "reset_at": None,
            "saved_at": _SAVED_AT.isoformat(),
            "session_name": "default",
        }
        del data[missing_key]
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match=missing_key) as exc_info:
            load_checkpoint(tmp_path)
        assert str(path) in str(exc_info.value)

    def test_unparseable_saved_at_raises_value_error_naming_path(self, tmp_path: Path) -> None:
        path = _checkpoint_path(tmp_path)
        path.parent.mkdir(parents=True)
        data = {
            "reason": "anthropic-api",
            "reset_at": None,
            "saved_at": "not-a-datetime",
            "session_name": "default",
        }
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="saved_at") as exc_info:
            load_checkpoint(tmp_path)
        assert str(path) in str(exc_info.value)

    def test_unparseable_reset_at_raises_value_error_naming_path(self, tmp_path: Path) -> None:
        path = _checkpoint_path(tmp_path)
        path.parent.mkdir(parents=True)
        data = {
            "reason": "anthropic-api",
            "reset_at": "not-a-datetime",
            "saved_at": _SAVED_AT.isoformat(),
            "session_name": "default",
        }
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="reset_at") as exc_info:
            load_checkpoint(tmp_path)
        assert str(path) in str(exc_info.value)


@pytest.mark.unit
class TestRemoveCheckpointIdempotent:
    """AC-E2-F1-S2-T3-3: remove_checkpoint never raises, present or absent, once or twice."""

    def test_remove_existing_checkpoint(self, tmp_path: Path) -> None:
        save_checkpoint(_make_checkpoint(), tmp_path)
        assert _checkpoint_path(tmp_path).exists()
        remove_checkpoint(tmp_path)
        assert not _checkpoint_path(tmp_path).exists()

    def test_remove_when_no_checkpoint_exists_raises_nothing(self, tmp_path: Path) -> None:
        remove_checkpoint(tmp_path)  # must not raise
        assert not _checkpoint_path(tmp_path).exists()

    def test_remove_twice_raises_nothing(self, tmp_path: Path) -> None:
        save_checkpoint(_make_checkpoint(), tmp_path)
        remove_checkpoint(tmp_path)
        remove_checkpoint(tmp_path)  # second call must not raise
        assert not _checkpoint_path(tmp_path).exists()


@pytest.mark.unit
class TestApplyResumeStrategyContinueCurrentWu:
    """AC-E2-F1-S2-T3-5: continue_current_wu removes the checkpoint and touches nothing else."""

    def test_removes_checkpoint_only(self, tmp_path: Path) -> None:
        _write_backlog_workspace(tmp_path, [("E2-F1-S1-T1", "in-progress")])
        save_checkpoint(_make_checkpoint(), tmp_path)

        _apply_resume_strategy("continue_current_wu", tmp_path)

        assert not _checkpoint_path(tmp_path).exists()
        assert _read_status_line(tmp_path, "E2-F1-S1-T1") == "in-progress"

    def test_no_checkpoint_present_raises_nothing(self, tmp_path: Path) -> None:
        _write_backlog_workspace(tmp_path, [("E2-F1-S1-T1", "in-progress")])
        _apply_resume_strategy("continue_current_wu", tmp_path)  # must not raise
        assert _read_status_line(tmp_path, "E2-F1-S1-T1") == "in-progress"


@pytest.mark.unit
class TestApplyResumeStrategyRestartWu:
    """AC-E2-F1-S2-T3-5: restart_wu forces every in-progress unit to in-queue, then removes the checkpoint."""

    def test_all_in_progress_units_become_in_queue(self, tmp_path: Path) -> None:
        _write_backlog_workspace(
            tmp_path,
            [
                ("E2-F1-S1-T1", "in-progress"),
                ("E2-F1-S1-T2", "in-progress"),
                ("E2-F1-S1-T3", "in-queue"),
                ("E2-F1-S1-T4", "done"),
            ],
        )
        save_checkpoint(_make_checkpoint(), tmp_path)

        _apply_resume_strategy("restart_wu", tmp_path)

        assert _read_status_line(tmp_path, "E2-F1-S1-T1") == "in-queue"
        assert _read_status_line(tmp_path, "E2-F1-S1-T2") == "in-queue"
        # Units that were never in-progress are left exactly as they were.
        assert _read_status_line(tmp_path, "E2-F1-S1-T3") == "in-queue"
        assert _read_status_line(tmp_path, "E2-F1-S1-T4") == "done"
        assert not _checkpoint_path(tmp_path).exists()

    def test_no_in_progress_units_is_a_no_op_on_statuses(self, tmp_path: Path) -> None:
        _write_backlog_workspace(tmp_path, [("E2-F1-S1-T1", "in-queue")])
        save_checkpoint(_make_checkpoint(), tmp_path)

        _apply_resume_strategy("restart_wu", tmp_path)

        assert _read_status_line(tmp_path, "E2-F1-S1-T1") == "in-queue"
        assert not _checkpoint_path(tmp_path).exists()


@pytest.mark.unit
class TestApplyResumeStrategyDrainAndResume:
    """AC-E2-F1-S2-T3-5: drain_and_resume removes the checkpoint and calls request_drain."""

    def test_removes_checkpoint_and_writes_real_drain_signal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.drain import resolve_drain_signal_path

        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        _write_backlog_workspace(tmp_path, [("E2-F1-S1-T1", "in-progress")])
        save_checkpoint(_make_checkpoint(), tmp_path)

        _apply_resume_strategy("drain_and_resume", tmp_path)

        assert not _checkpoint_path(tmp_path).exists()
        signal_path = resolve_drain_signal_path(tmp_path)
        assert signal_path.exists()
        # request_drain was not reinvented -- the real signal file it writes
        # is present and well-formed JSON with the expected fields.
        payload = json.loads(signal_path.read_text())
        assert "requested_at" in payload
        assert "requested_by" in payload
        # restart_wu behaviour must NOT have fired: the in-progress unit is untouched.
        assert _read_status_line(tmp_path, "E2-F1-S1-T1") == "in-progress"


@pytest.mark.unit
class TestApplyResumeStrategyUnknown:
    """AC-E2-F1-S2-T3-6: an unknown strategy raises ValueError naming the allowed set."""

    def test_unknown_strategy_raises_value_error_naming_allowed_set(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError) as exc_info:
            _apply_resume_strategy("not_a_real_strategy", tmp_path)
        message = str(exc_info.value)
        assert "continue_current_wu" in message
        assert "restart_wu" in message
        assert "drain_and_resume" in message
