"""Tests for devbench.backlog.review_feedback_vocabulary."""

from __future__ import annotations

import pytest

from devbench.backlog.review_feedback_vocabulary import (
    JUDGE_CATEGORIES,
    is_known_judge,
    is_valid_code,
)


class TestIsKnownJudge:
    @pytest.mark.parametrize("judge", sorted(JUDGE_CATEGORIES.keys()))
    def test_known_judges_return_true(self, judge: str) -> None:
        assert is_known_judge(judge) is True

    @pytest.mark.parametrize(
        "judge",
        ["unknown", "", "code-review", "Code_Review", "review_supervisor"],
    )
    def test_unknown_judges_return_false(self, judge: str) -> None:
        assert is_known_judge(judge) is False


class TestIsValidCode:
    def test_valid_code_returns_true(self) -> None:
        assert is_valid_code("code_review", "MAKE_VALIDATE_FAILURE") is True

    def test_unreachable_artifact_is_a_code_review_code(self) -> None:
        """Issue #01: the reachability-check gate emits code_review:UNREACHABLE_ARTIFACT."""
        assert is_valid_code("code_review", "UNREACHABLE_ARTIFACT") is True

    def test_valid_code_for_each_judge(self) -> None:
        for judge, codes in JUDGE_CATEGORIES.items():
            for code in codes:
                assert is_valid_code(judge, code) is True

    def test_unknown_judge_returns_false_for_any_code(self) -> None:
        assert is_valid_code("not_a_judge", "MAKE_VALIDATE_FAILURE") is False

    def test_unknown_code_returns_false(self) -> None:
        assert is_valid_code("code_review", "NOT_A_REAL_CODE") is False

    def test_empty_inputs_return_false(self) -> None:
        assert is_valid_code("", "") is False
