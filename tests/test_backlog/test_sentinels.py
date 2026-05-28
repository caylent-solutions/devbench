"""Tests for devbench.backlog.sentinels module (issue #221 B3)."""

from __future__ import annotations

import pytest

from devbench.backlog.sentinels import (
    BACKLOG_SENTINEL_VALUES,
    SENTINEL_PATTERN,
    is_sentinel_manifest_value,
)


class TestExplicitAllowlist:
    """Every value in the explicit allowlist is recognised as a sentinel."""

    @pytest.mark.parametrize("value", sorted(BACKLOG_SENTINEL_VALUES))
    def test_allowlisted_value_is_sentinel(self, value: str) -> None:
        assert is_sentinel_manifest_value(value) is True


class TestPatternMatching:
    """Operator-defined ``<name>``-shaped tokens are also sentinels."""

    @pytest.mark.parametrize(
        "value",
        [
            "<verification-only:E15-F5-S1-T2>",
            "<decision-only:E16-F3-S1-T1>",
            "<custom-sentinel>",
            "<a>",
            "<some-long-multi-word-sentinel>",
        ],
    )
    def test_pattern_matched_value_is_sentinel(self, value: str) -> None:
        assert is_sentinel_manifest_value(value) is True
        assert SENTINEL_PATTERN.fullmatch(value) is not None


class TestNonSentinels:
    """Real file paths and empty input are NOT sentinels."""

    @pytest.mark.parametrize(
        "value",
        [
            "src/devbench/cli.py",
            "tests/test_a.py",
            "<incomplete",
            "incomplete>",
            "no-angle-brackets",
            ".md",
            "src/<not-a-sentinel>",
            "  ",
        ],
    )
    def test_real_path_or_garbage_not_sentinel(self, value: str) -> None:
        assert is_sentinel_manifest_value(value) is False

    def test_empty_string_not_sentinel(self) -> None:
        assert is_sentinel_manifest_value("") is False


class TestWhitespaceTolerance:
    """Leading / trailing whitespace around a sentinel doesn't disqualify it."""

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert is_sentinel_manifest_value("  <verification-only>  ") is True
        assert is_sentinel_manifest_value("\t<decision-only>\n") is True
