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


class TestNoOutputManifest:
    """`is_no_output_manifest` separates the two kinds of sentinel.

    The canonical registry mixes two semantics that the commit path must not
    conflate (docs/backlog-contract.md 'Accepted sentinel values'):

    - no-output: `<verification-only>`, `<decision-only>`, `<no changes>`,
      `<no-op>` -- "No source files are modified", evidence goes in
      `## Comments`. There will never be anything to commit.
    - deferred-resolution: `<source-drift-fix-targets-determined-at-execution>`
      -- concrete paths are enumerated mid-execution via manifest_amendment.

    git_ops._stage_for_commit treated both as deferred and told the operator to
    "resolve the sentinel to real paths via a manifest amendment", which is
    unsatisfiable for a no-output unit and blocked it permanently.
    """

    def test_each_no_output_sentinel_is_recognised(self):
        from devbench.backlog.sentinels import is_no_output_manifest

        for value in ("<verification-only>", "<decision-only>", "<no changes>", "<no-op>"):
            assert is_no_output_manifest([value]) is True, value

    def test_deferred_resolution_sentinel_is_not_no_output(self):
        from devbench.backlog.sentinels import is_no_output_manifest

        assert is_no_output_manifest(["<source-drift-fix-targets-determined-at-execution>"]) is False

    def test_per_task_variant_form_is_recognised(self):
        from devbench.backlog.sentinels import is_no_output_manifest

        assert is_no_output_manifest(["<verification-only:E15-F5-S1-T2>"]) is True

    def test_real_path_is_not_no_output(self):
        from devbench.backlog.sentinels import is_no_output_manifest

        assert is_no_output_manifest(["scripts/check_idempotency.py"]) is False

    def test_mixed_manifest_is_not_no_output(self):
        """A real path alongside a sentinel still has something to commit."""
        from devbench.backlog.sentinels import is_no_output_manifest

        assert is_no_output_manifest(["<verification-only>", "scripts/foo.py"]) is False

    def test_empty_manifest_is_not_no_output(self):
        """Empty means unscopeable, which is a different refusal -- not no-output."""
        from devbench.backlog.sentinels import is_no_output_manifest

        assert is_no_output_manifest([]) is False

    def test_backtick_and_whitespace_tolerant(self):
        from devbench.backlog.sentinels import is_no_output_manifest

        assert is_no_output_manifest(["  `<verification-only>`  "]) is True
