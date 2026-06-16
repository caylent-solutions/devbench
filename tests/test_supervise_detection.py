"""Detection patterns: quota markers, reset-time parse, false-positive guard (AC-3).

Covers Section 4.9 / FR-14 / FR-29 and the Section 10.2 false-positive property
test: the default ``quota_limit`` regex matches every SDK-surface quota marker and
the ``reset_at`` regex parses a real reset line, while benign rate-limiting prose
does NOT match (the ADR-24 lesson).
"""

from __future__ import annotations

import re

import pytest

from devbench.config_loader import SuperviseDetectionPatternsConfig
from devbench.quota import _QUOTA_MARKERS
from devbench.supervise import DetectionPatterns


@pytest.mark.unit
class TestQuotaLimitDetection:
    """AC-3: default quota_limit matches every quota marker on the SDK surface."""

    @pytest.mark.parametrize("marker", list(_QUOTA_MARKERS))
    def test_matches_sdk_markers(self, marker: str) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_quota_limit(marker), f"quota_limit must match SDK marker {marker!r}"


@pytest.mark.unit
class TestResetAtParse:
    """AC-3: reset_at parses a real reset line to its H:MM components."""

    def test_parses_reset_line(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        match = patterns.match_reset_at("You've hit your limit; resets 8:00am (UTC)")
        assert match is not None
        assert match.group(1) == "8"
        assert match.group(2) == "00"
        assert match.group(3).lower() == "am"

    def test_no_reset_line_returns_none(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.match_reset_at("nothing here") is None


@pytest.mark.unit
class TestFalsePositiveGuard:
    """Section 10.2: benign prose must NOT match the quota_limit pattern."""

    @pytest.mark.parametrize(
        "benign",
        [
            "API endpoints implement rate limiting",
            "We added a rate limit to the gateway",
            "the limit is configurable",
        ],
    )
    def test_benign_prose_not_matched(self, benign: str) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert not patterns.is_quota_limit(benign), f"benign prose must not match: {benign!r}"


@pytest.mark.unit
class TestReadyAndWorkingPatterns:
    """The ready/working prompt patterns compile and match expected text."""

    def test_ready_prompt_matches_bare_prompt(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_ready_prompt("> ")

    def test_ready_prompt_matches_claude_arrow_prompt(self) -> None:
        # Claude Code (>=2.1.x) renders its interactive input prompt as the arrow
        # glyph U+276F ("> Try ..." with the arrow, not a bare ">"), framed in a
        # box; the detector must match the real CLI prompt or the supervisor hangs
        # forever in the "starting" state waiting for a ready prompt that the
        # legacy "^>$" pattern can never see.
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_ready_prompt('\u276f Try "how do I log an error?"')
        assert patterns.is_ready_prompt("│ \u276f │")

    def test_ready_prompt_not_matched_by_working_text(self) -> None:
        # Ready and working states must stay distinct: the supervisor injects the
        # orchestrate command only after the ready prompt, then waits for work.
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert not patterns.is_ready_prompt("esc to interrupt")
        assert not patterns.is_ready_prompt("thinking")

    def test_working_prompt_matches(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_working_prompt("esc to interrupt")

    def test_crash_pattern(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_crash("Traceback (most recent call last):")

    def test_harness_block_pattern(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_harness_block("[HARNESS_INTEGRITY] denied")

    def test_circuit_breaker_pattern(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_circuit_breaker("cascade depth exceeded")
        assert not patterns.is_circuit_breaker("a normal line")

    def test_quota_wait_prompt_pattern(self) -> None:
        patterns = DetectionPatterns(SuperviseDetectionPatternsConfig())
        assert patterns.is_quota_wait_prompt("please wait for the reset window")
        assert not patterns.is_quota_wait_prompt("ordinary output")

    def test_invalid_regex_fails_fast(self) -> None:
        bad = SuperviseDetectionPatternsConfig(ready_prompt="(unterminated")
        with pytest.raises(re.error):
            DetectionPatterns(bad)
