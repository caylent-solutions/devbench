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

    def test_newly_reachable_path_unverified_is_a_valid_code_review_code(self) -> None:
        assert is_valid_code("code_review", "NEWLY_REACHABLE_PATH_UNVERIFIED") is True

    def test_unreachable_artifact_is_a_code_review_code(self) -> None:
        """caylent-solutions/devbench-internal-backlog#10: the reachability-check gate
        emits code_review:UNREACHABLE_ARTIFACT."""
        assert is_valid_code("code_review", "UNREACHABLE_ARTIFACT") is True

    def test_fixture_catalog_mismatch_is_a_test_review_code(self) -> None:
        """caylent-solutions/devbench-internal-backlog#17: the fixture-catalog
        cross-reference gate emits test_review:FIXTURE_CATALOG_MISMATCH."""
        assert is_valid_code("test_review", "FIXTURE_CATALOG_MISMATCH") is True

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

    def test_composition_root_missing_valid_for_test_review(self) -> None:
        """caylent-solutions/devbench-internal-backlog#11: composition-root verification rejection code."""
        assert is_valid_code("test_review", "COMPOSITION_ROOT_MISSING") is True

    def test_composition_root_missing_not_valid_for_other_judges(self) -> None:
        """The code is test_review-specific, not shared across judges."""
        other_judges = [j for j in JUDGE_CATEGORIES if j != "test_review"]
        for judge in other_judges:
            assert is_valid_code(judge, "COMPOSITION_ROOT_MISSING") is False

    def test_layout_stub_without_live_test_valid_for_test_review(self) -> None:
        """caylent-solutions/devbench-internal-backlog#14: the layout-geometry gate
        emits test_review:LAYOUT_STUB_WITHOUT_LIVE_TEST."""
        assert is_valid_code("test_review", "LAYOUT_STUB_WITHOUT_LIVE_TEST") is True

    def test_layout_stub_without_live_test_not_valid_for_code_review(self) -> None:
        """The code is test_review-specific, not shared across judges."""
        assert is_valid_code("code_review", "LAYOUT_STUB_WITHOUT_LIVE_TEST") is False
