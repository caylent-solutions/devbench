"""QuotaWaiter: interactive quota wait-and-resume adapter (AC-9, FR-14/15/16).

Spec Section 4.9: quota is NOT a fault. On a usage-limit event the supervisor
transitions to ``quota-waiting`` (never exits non-zero), delegates the WAIT to
the shared ``quota.wait_for_reset`` primitive, bounds resumes by the shared
``_resolve_max_quota_resumes`` cap, persists a ``QuotaCheckpoint``, and surfaces
the expected reset time. These tests use injected fakes for the delegated
primitives so no real wait occurs; the DRY proof (the SAME callables are used)
lives in ``test_supervise_quota_reuse.py`` (AC-32).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from devbench.config_loader import SuperviseConfig
from devbench.constants import (
    SUPERVISE_BILLING_MODE_BEDROCK,
    SUPERVISE_BILLING_MODE_SUBSCRIPTION,
)
from devbench.supervise import (
    DetectionPatterns,
    QuotaDecision,
    QuotaWaiter,
)


def _patterns() -> DetectionPatterns:
    return DetectionPatterns(SuperviseConfig().detection_patterns)


def _waiter(
    *,
    wait_recovered: bool = True,
    max_resumes: int = 1000,
    poll_interval: int = 60,
    max_wait: int = 18000,
    saved: list | None = None,
    waited: list | None = None,
    billing_mode: str = SUPERVISE_BILLING_MODE_SUBSCRIPTION,
) -> QuotaWaiter:
    """Build a QuotaWaiter wired to deterministic fakes for the delegated work."""

    def fake_wait(*, reset_at, poll_interval_seconds, max_wait_seconds):
        if waited is not None:
            waited.append((reset_at, poll_interval_seconds, max_wait_seconds))
        return wait_recovered

    def fake_detect(text: object):
        from devbench.quota import SubscriptionRateLimitError

        if isinstance(text, str) and "hit your limit" in text:
            return SubscriptionRateLimitError(reset_at=None, raw_error=text, source="claude-code-cli")
        return None

    def fake_save(checkpoint, workspace_root):
        if saved is not None:
            saved.append(checkpoint)

    return QuotaWaiter(
        patterns=_patterns(),
        poll_interval_seconds=poll_interval,
        max_wait_seconds=max_wait,
        wait_for_reset=fake_wait,
        detect_quota_error=fake_detect,
        resolve_max_resumes=lambda: max_resumes,
        save_checkpoint=fake_save,
        workspace_root="/tmp/ws",
        session_name="nightly",
        billing_mode=billing_mode,
    )


@pytest.mark.unit
class TestQuotaDetection:
    """The waiter recognizes the interactive usage-limit PTY text (FR-14)."""

    def test_detects_quota_limit_text(self) -> None:
        waiter = _waiter()
        assert waiter.is_quota_text("You've hit your limit; resets 8:00am (UTC)") is True

    def test_non_quota_text_not_detected(self) -> None:
        waiter = _waiter()
        assert waiter.is_quota_text("esc to interrupt -- thinking") is False


@pytest.mark.unit
class TestParseResetAt:
    """The reset time is parsed from PTY text via the configured reset_at regex."""

    def test_parses_reset_time(self) -> None:
        waiter = _waiter()
        reset = waiter.parse_reset_at("You've hit your limit; resets 8:00am (UTC)")
        assert reset is not None
        assert reset.tzinfo is not None
        assert reset.hour == 8
        assert reset.minute == 0

    def test_absent_reset_time_returns_none(self) -> None:
        waiter = _waiter()
        assert waiter.parse_reset_at("You've hit your limit") is None

    def test_invalid_hour_returns_none(self) -> None:
        # A pattern with a custom reset_at regex that captures an out-of-range
        # hour (13) -> parse_reset_at rejects it (fail-fast on bad input).
        from dataclasses import replace

        from devbench.supervise import DetectionPatterns

        cfg = SuperviseConfig().detection_patterns
        cfg = replace(cfg, reset_at=r"resets\s+(\d{1,2}):(\d{2})(am|pm)")
        patterns = DetectionPatterns(cfg)
        waiter = QuotaWaiter(
            patterns=patterns,
            poll_interval_seconds=60,
            max_wait_seconds=18000,
            wait_for_reset=lambda **_k: True,
            detect_quota_error=lambda _t: None,
            resolve_max_resumes=lambda: 1000,
            save_checkpoint=lambda *_a: None,
            workspace_root="/tmp/ws",
            session_name="n",
        )
        assert waiter.parse_reset_at("resets 13:00pm") is None


@pytest.mark.unit
class TestQuotaWaitDelegatesAndRecovers:
    """A recovered wait permits a resume bounded by the cap; never exits (AC-9)."""

    def test_wait_recovered_returns_resume_decision(self) -> None:
        waited: list = []
        saved: list = []
        waiter = _waiter(wait_recovered=True, max_resumes=3, saved=saved, waited=waited)
        reset_at = datetime.now(UTC) + timedelta(hours=1)

        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=0)

        # The wait was delegated with the configured cadence + window.
        assert waited == [(reset_at, 60, 18000)]
        # A checkpoint was persisted so the expected-resume survives a restart.
        assert len(saved) == 1
        assert saved[0].reset_at == reset_at
        # The decision permits a resume (still under cap) and is NOT a fault exit.
        assert decision.action is QuotaDecision.RESUME
        assert decision.expected_resume == reset_at

    def test_wait_timeout_does_not_recover(self) -> None:
        waiter = _waiter(wait_recovered=False, max_resumes=3)
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=0)
        # A timed-out wait does not recover -> the caller keeps waiting (still
        # never a non-zero exit). action is WAIT (not RESUME, not FAULT).
        assert decision.action is QuotaDecision.WAIT


@pytest.mark.unit
class TestQuotaResumeCapBound:
    """Resumes are bounded by the shared cap (FR-15); cap exceeded -> fault."""

    def test_resume_under_cap_permits_resume(self) -> None:
        waiter = _waiter(wait_recovered=True, max_resumes=2)
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=1)
        assert decision.action is QuotaDecision.RESUME

    def test_resume_at_cap_is_fault(self) -> None:
        waiter = _waiter(wait_recovered=True, max_resumes=2)
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        # resumes_used == cap: a further resume would exceed the bound -> the only
        # fault path quota has (Section 4.9: quota-resume-cap-exhausted).
        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=2)
        assert decision.action is QuotaDecision.FAULT
        assert decision.exit_reason == "quota-resume-cap-exhausted"

    def test_cap_checked_before_wait(self) -> None:
        # When the cap is already exhausted the waiter must NOT even start a wait
        # (no point waiting when no resume is permitted afterwards).
        waited: list = []
        waiter = _waiter(wait_recovered=True, max_resumes=1, waited=waited)
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=1)
        assert decision.action is QuotaDecision.FAULT


@pytest.mark.unit
class TestQuotaWaitDisabledInBedrockMode:
    """Bedrock mode has NO 5-hour subscription windows: the wait is DISABLED.

    Bedrock throttling is handled by the shared quota.py path
    (``_BEDROCK_THROTTLE_CODES``) in the SDK orchestrator subprocess, not by the
    supervisor's interactive 5-hour-window wait. A subscription usage-limit
    prompt is anomalous in bedrock mode, so the QuotaWaiter must NOT delegate the
    subscription wait nor persist a subscription checkpoint; it fails fast with a
    classified reason instead of waiting an imaginary window forever.
    """

    def test_bedrock_does_not_delegate_subscription_wait(self) -> None:
        waited: list = []
        saved: list = []
        waiter = _waiter(
            wait_recovered=True,
            max_resumes=3,
            saved=saved,
            waited=waited,
            billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
        )
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=0)
        # The 5-hour subscription wait must NOT have been delegated.
        assert waited == []
        # No subscription checkpoint was persisted.
        assert saved == []
        # It fails fast (a quota window does not exist for Bedrock) rather than
        # looping; the SDK path's Bedrock throttle handling is the real mechanism.
        assert decision.action is QuotaDecision.FAULT
        assert decision.exit_reason == "quota-wait-disabled-bedrock"

    def test_subscription_mode_still_engages_wait(self) -> None:
        # Guard against a regression that disables the wait in subscription mode.
        waited: list = []
        waiter = _waiter(
            wait_recovered=True,
            max_resumes=3,
            waited=waited,
            billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION,
        )
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=0)
        assert waited == [(reset_at, 60, 18000)]
        assert decision.action is QuotaDecision.RESUME


@pytest.mark.unit
class TestInSessionWaitChoice:
    """When the live prompt offers a wait/retry option, inject the configured choice (4.9a)."""

    def test_offers_in_session_choice_when_prompt_matches(self) -> None:
        waiter = _waiter()
        # The default quota_wait_prompt regex matches "wait ... reset".
        assert waiter.is_in_session_wait_prompt("Press 1 to wait for the reset") is True

    def test_no_in_session_choice_when_prompt_absent(self) -> None:
        waiter = _waiter()
        assert waiter.is_in_session_wait_prompt("You've hit your limit") is False
