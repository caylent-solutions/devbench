"""Tests for devbench.backlog.review_feedback_vocabulary."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from devbench.backlog.review_feedback_vocabulary import (
    JUDGE_CATEGORIES,
    is_known_judge,
    is_valid_code,
)

# ---------------------------------------------------------------------------
# Finding 322-D21 (caylent-solutions/devbench-internal-backlog#17): PR #322
# shipped `FIXTURE_CATALOG_MISMATCH` with a positive membership assertion
# only -- nothing proved the code was absent from every other judge's set.
# `_LEGACY_CODES` and `_CAMPAIGN_CODES` partition every code in
# `JUDGE_CATEGORIES` into "shipped before the membership-test convention
# existed" (closed forever, and pinned against `_LEGACY_CODES_SNAPSHOT` by
# `TestLegacyCodesAreFrozen` so it cannot be silently extended) and "shipped
# under the convention" (grows by exactly one entry per new code, each
# proven in `TestCampaignCodeMembership`). A new code must therefore be
# registered in `_CAMPAIGN_CODES`: registering it in `_LEGACY_CODES` instead
# fails `TestLegacyCodesAreFrozen`, and omitting it from both sets fails
# `TestJudgeCategoryMembershipCoverage` -- together the two gates mean a
# code can never ship without a dedicated `TestCampaignCodeMembership`
# assertion.
# ---------------------------------------------------------------------------

#: Codes that existed before finding 322-D21 established the per-code
#: membership-test convention. This snapshot is closed permanently: it is
#: NEVER extended. Every code added after this task belongs in
#: `_CAMPAIGN_CODES` instead, never here -- enforced by
#: `TestLegacyCodesAreFrozen` below, which pins this set against
#: `_LEGACY_CODES_SNAPSHOT`.
_LEGACY_CODES: Final[frozenset[str]] = frozenset(
    {
        "AGENT_LOG_CONTRADICTS_DIFF",
        "API_DOCS_STALE",
        "APPROACH_AUTH",
        "CHANGELOG_SYNC",
        "CONFIG_DOCS",
        "COVERAGE_REGRESSION",
        "DRY_VIOLATION",
        "EVIDENCE_BASED_CLAIM",
        "GIT_COMPLETENESS",
        "HARDCODED_URL",
        "JUSTIFICATION_COHERENCE",
        "MAKE_VALIDATE_FAILURE",
        "MANIFEST_MISMATCH",
        "MANIFEST_TODO_UNFILLED",
        "MISSING_AC_EVIDENCE",
        "OTHER",
        "OUT_OF_SCOPE_FILES",
        "PRE_FILTER",
        "README_SYNC",
        "SCOPE",
        "SCOPE_GAP",
        "SCOPE_VIOLATION",
        "SECRET_LEAK",
        "SECURITY_BYPASS_ANNOTATION",
        "SOLID_VIOLATION",
        "STAGING_GAP",
        "STUB_TEST",
        "TDD_CYCLE_MISSING",
        "UNAUTHORIZED_DEP",
    }
)

#: Independent snapshot of `_LEGACY_CODES` recorded when finding 322-D21
#: closed. `_LEGACY_CODES` above is declared closed permanently by
#: convention alone; comparing it against this separately-maintained
#: snapshot in `TestLegacyCodesAreFrozen` turns that convention into a
#: machine check -- a future author who appends a new code to
#: `_LEGACY_CODES` (instead of registering it in `_CAMPAIGN_CODES`, where
#: `TestCampaignCodeMembership` forces an ownership and non-membership
#: assertion) makes the two sets diverge and fails that test.
_LEGACY_CODES_SNAPSHOT: Final[frozenset[str]] = frozenset(
    {
        "AGENT_LOG_CONTRADICTS_DIFF",
        "API_DOCS_STALE",
        "APPROACH_AUTH",
        "CHANGELOG_SYNC",
        "CONFIG_DOCS",
        "COVERAGE_REGRESSION",
        "DRY_VIOLATION",
        "EVIDENCE_BASED_CLAIM",
        "GIT_COMPLETENESS",
        "HARDCODED_URL",
        "JUSTIFICATION_COHERENCE",
        "MAKE_VALIDATE_FAILURE",
        "MANIFEST_MISMATCH",
        "MANIFEST_TODO_UNFILLED",
        "MISSING_AC_EVIDENCE",
        "OTHER",
        "OUT_OF_SCOPE_FILES",
        "PRE_FILTER",
        "README_SYNC",
        "SCOPE",
        "SCOPE_GAP",
        "SCOPE_VIOLATION",
        "SECRET_LEAK",
        "SECURITY_BYPASS_ANNOTATION",
        "SOLID_VIOLATION",
        "STAGING_GAP",
        "STUB_TEST",
        "TDD_CYCLE_MISSING",
        "UNAUTHORIZED_DEP",
    }
)

#: Codes introduced by the integration-reality-gates-hardening campaign
#: (spec `integration-reality-gates-hardening.md` section 4.10), each proven
#: by a dedicated ownership + non-membership assertion in
#: `TestCampaignCodeMembership` below.
_CAMPAIGN_CODES: Final[tuple[str, ...]] = (
    # caylent-solutions/devbench-internal-backlog#10
    "UNREACHABLE_ARTIFACT",
    # caylent-solutions/devbench-internal-backlog#11
    "COMPOSITION_ROOT_MISSING",
    # caylent-solutions/devbench-internal-backlog#14
    "LAYOUT_STUB_WITHOUT_LIVE_TEST",
    # caylent-solutions/devbench-internal-backlog#15 (newly-reachable-paths mechanism)
    "NEWLY_REACHABLE_PATH_UNVERIFIED",
    # caylent-solutions/devbench-internal-backlog#17 -- finding 322-D21
    "FIXTURE_CATALOG_MISMATCH",
    # caylent-solutions/devbench-internal-backlog#16 -- finding 321-D21
    "WRITE_PATH_UNVERIFIED",
)

#: Literal code -> owning-judge contract for every entry in
#: `_CAMPAIGN_CODES`, cross-checked against the published mapping in
#: `docs/cli-reference.md` (line ~1355, `FIXTURE_CATALOG_MISMATCH` as a
#: `test_review` code) and the generated `code_review` / `test_review`
#: tables in `docs/review-feedback-vocabulary.md`. Deriving the "owning
#: judge" from `JUDGE_CATEGORIES` itself (as `_owner` below does) cannot
#: detect a code being reassigned to a different judge's frozenset, because
#: the derivation and the assertion would move together; pinning the
#: mapping here as data means `TestCampaignCodeMembership.test_owned_by_its_judge`
#: fails directly when a reassignment happens, the same way the seven
#: ad-hoc single-code tests this class superseded did for one code apiece.
_CAMPAIGN_CODE_OWNERS: Final[dict[str, str]] = {
    "UNREACHABLE_ARTIFACT": "code_review",
    "COMPOSITION_ROOT_MISSING": "test_review",
    "LAYOUT_STUB_WITHOUT_LIVE_TEST": "test_review",
    "NEWLY_REACHABLE_PATH_UNVERIFIED": "code_review",
    "FIXTURE_CATALOG_MISMATCH": "test_review",
    "WRITE_PATH_UNVERIFIED": "code_review",
}


def _owner(code: str) -> str:
    """Return the single judge whose `JUDGE_CATEGORIES` frozenset contains *code*.

    Raises AssertionError if *code* belongs to zero or more than one judge.
    Every campaign code follows spec 4.10's ownership rule (exactly one
    judge owns each code) -- unlike a handful of legacy codes (e.g.
    `SCOPE_VIOLATION`) that are intentionally shared across judges.
    """
    owners = sorted(judge for judge, codes in JUDGE_CATEGORIES.items() if code in codes)
    assert len(owners) == 1, f"{code!r} must belong to exactly one judge, found: {owners}"
    return owners[0]


class TestCampaignCodeMembership:
    """Every campaign code proves both halves of spec 4.10's ownership rule:
    membership in exactly its owning judge's frozenset, and non-membership
    in every other judge's. The positive half is parametrized off the
    literal `_CAMPAIGN_CODE_OWNERS` table, so reassigning a code to a
    different judge's frozenset fails the test directly; the negative half
    remains parametrized off `_CAMPAIGN_CODES` and `JUDGE_CATEGORIES`
    directly so the cross-product can never silently skip a judge added
    later."""

    @pytest.mark.parametrize(("code", "owner"), sorted(_CAMPAIGN_CODE_OWNERS.items()))
    def test_owned_by_its_judge(self, code: str, owner: str) -> None:
        """Pinned against the literal `owner`, not a judge derived from
        `JUDGE_CATEGORIES` itself: a code moved to a different judge's
        frozenset makes `is_valid_code(owner, code)` false here."""
        assert is_valid_code(owner, code) is True

    def test_owners_table_matches_campaign_codes(self) -> None:
        """`_CAMPAIGN_CODE_OWNERS` and `_CAMPAIGN_CODES` are two views onto
        the same code set; a code added to one and not the other would
        leave it without the literal ownership pin above."""
        assert set(_CAMPAIGN_CODE_OWNERS) == set(_CAMPAIGN_CODES)

    @pytest.mark.parametrize("code", _CAMPAIGN_CODES)
    def test_owner_matches_literal_table(self, code: str) -> None:
        """`_owner` fails fast (via its own `assert`) if `code` belongs to
        zero or more than one judge in `JUDGE_CATEGORIES`; cross-checking
        its result against `_CAMPAIGN_CODE_OWNERS` keeps that single-owner
        precondition wired to the same literal contract the positive
        assertion above pins, instead of drifting independently."""
        assert _owner(code) == _CAMPAIGN_CODE_OWNERS[code]

    @pytest.mark.parametrize(
        ("code", "other_judge"),
        [
            (code, other_judge)
            for code in _CAMPAIGN_CODES
            for other_judge in sorted(JUDGE_CATEGORIES)
            if other_judge != _owner(code)
        ],
    )
    def test_not_owned_by_other_judges(self, code: str, other_judge: str) -> None:
        assert is_valid_code(other_judge, code) is False


class TestLegacyCodesAreFrozen:
    """`_LEGACY_CODES` is closed permanently (see its docstring above): a
    new code belongs in `_CAMPAIGN_CODES` instead, where
    `TestCampaignCodeMembership` forces a dedicated ownership and
    non-membership assertion. Without this test, `_LEGACY_CODES` and
    `_CAMPAIGN_CODES` are both open Python collections that
    `test_every_code_is_accounted_for` treats as a plain union -- nothing
    stops a future author from appending a new code to `_LEGACY_CODES`
    instead of `_CAMPAIGN_CODES` and shipping with zero membership
    assertion. Comparing `_LEGACY_CODES` against the independently
    maintained `_LEGACY_CODES_SNAPSHOT` closes that gap: any divergence
    fails here, naming exactly what changed."""

    def test_legacy_codes_matches_frozen_snapshot(self) -> None:
        added = sorted(_LEGACY_CODES - _LEGACY_CODES_SNAPSHOT)
        removed = sorted(_LEGACY_CODES_SNAPSHOT - _LEGACY_CODES)
        assert not added and not removed, (
            "_LEGACY_CODES is closed permanently and must never change. "
            f"Unexpected additions: {added}. Unexpected removals: {removed}. "
            "Register a new code in _CAMPAIGN_CODES instead, with a "
            "TestCampaignCodeMembership assertion proving its ownership and "
            "non-membership in every other judge's set."
        )


class TestJudgeCategoryMembershipCoverage:
    """Finding 322-D21: every code in `JUDGE_CATEGORIES` must be accounted
    for by `_LEGACY_CODES` or `_CAMPAIGN_CODES`, so a new vocabulary code can
    never ship without a dedicated membership assertion proving both its
    ownership and its non-membership in another judge's set (see
    `TestCampaignCodeMembership`). `TestLegacyCodesAreFrozen` closes the
    remaining gap: a new code registered in `_LEGACY_CODES` instead of
    `_CAMPAIGN_CODES` would satisfy this test's union check with zero
    membership assertion, so `_LEGACY_CODES` is separately pinned there."""

    def test_every_code_is_accounted_for(self) -> None:
        all_codes = {code for codes in JUDGE_CATEGORIES.values() for code in codes}
        accounted_for = _LEGACY_CODES | set(_CAMPAIGN_CODES)
        missing = sorted(all_codes - accounted_for)
        assert not missing, (
            "code(s) in JUDGE_CATEGORIES with no membership-test coverage: "
            f"{missing}. Add each to _CAMPAIGN_CODES in this module and prove "
            "its ownership and non-membership via TestCampaignCodeMembership."
        )

    def test_no_code_is_double_counted(self) -> None:
        """A code must not be simultaneously legacy AND campaign -- that
        would mean the partition drifted out of sync with itself."""
        overlap = sorted(_LEGACY_CODES & set(_CAMPAIGN_CODES))
        assert not overlap, f"code(s) counted in both _LEGACY_CODES and _CAMPAIGN_CODES: {overlap}"


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


# ---------------------------------------------------------------------------
# WRITE_PATH_UNVERIFIED surfaces (spec 4.8, 4.10; AC-WP-016, AC-WP-018): the
# generated docs table, the generated code-reviewer prompt sentence, and the
# hand-authored conditional Evidence re-run instruction all name the code.
# ---------------------------------------------------------------------------

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DOC_PATH: Final[Path] = _REPO_ROOT / "docs" / "review-feedback-vocabulary.md"
_CODE_REVIEWER_PATH: Final[Path] = (
    _REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "code-reviewer.md"
)
_GUARD_START: Final[str] = "<!-- generated:vocabulary -->"
_GUARD_END: Final[str] = "<!-- /generated:vocabulary -->"


def _section_between(text: str, start_heading: str, end_heading: str | None, *, source: Path) -> str:
    """Return *text* sliced from *start_heading* up to (excluding) *end_heading*.

    Raises AssertionError naming the missing heading and *source* rather than
    silently returning an empty or wrong slice -- callers use this to scope a
    guard-marker search to the correct judge's section of a multi-section
    file.
    """
    start = text.find(start_heading)
    assert start != -1, f"no {start_heading!r} heading found in {source}"
    if end_heading is None:
        return text[start:]
    end = text.find(end_heading, start)
    assert end != -1, f"no {end_heading!r} heading found after {start_heading!r} in {source}"
    return text[start:end]


def _first_guarded_block(text: str) -> str:
    """Return the content of the first guard-marker pair found in *text*.

    Raises AssertionError naming whichever marker is missing, rather than
    returning an empty slice that would let a mis-scoped search pass
    silently.
    """
    start = text.find(_GUARD_START)
    assert start != -1, f"no {_GUARD_START!r} marker found in the scanned text"
    end = text.find(_GUARD_END, start)
    assert end != -1, f"no {_GUARD_END!r} marker found after the opening marker"
    return text[start + len(_GUARD_START) : end]


class TestWritePathUnverifiedSurfaces:
    """AC-WP-018: the code's docs-table row and the code-reviewer prompt's
    inline sentence are both generated between the
    `<!-- generated:vocabulary -->` guard markers (run `make
    generate-vocabulary` to regenerate them from `JUDGE_CATEGORIES` after a
    source change). AC-WP-016: the code-reviewer's hand-authored `##
    Evidence` section instructs the conditional `check-write-path` re-run
    the vocabulary code exists to gate."""

    def test_docs_table_lists_code_inside_code_review_guard_markers(self) -> None:
        text = _DOC_PATH.read_text(encoding="utf-8")
        code_review_section = _section_between(text, "## `code_review`", "## `test_review`", source=_DOC_PATH)
        block = _first_guarded_block(code_review_section)
        assert "WRITE_PATH_UNVERIFIED" in block, (
            f"{_DOC_PATH}'s code_review table (inside its guard markers) must list "
            "WRITE_PATH_UNVERIFIED; run `make generate-vocabulary`."
        )

    def test_prompt_inline_sentence_names_code_inside_guard_markers(self) -> None:
        text = _CODE_REVIEWER_PATH.read_text(encoding="utf-8")
        block = _first_guarded_block(text)
        assert "WRITE_PATH_UNVERIFIED" in block, (
            f"{_CODE_REVIEWER_PATH}'s guard-marked inline vocabulary sentence must list "
            "WRITE_PATH_UNVERIFIED; run `make generate-vocabulary`."
        )

    def test_evidence_section_instructs_conditional_check_write_path_rerun(self) -> None:
        text = _CODE_REVIEWER_PATH.read_text(encoding="utf-8")
        evidence_section = _section_between(text, "## Evidence", "\n---\n", source=_CODE_REVIEWER_PATH)
        assert "check-write-path" in evidence_section, (
            f"{_CODE_REVIEWER_PATH}'s ## Evidence section must hand-author a conditional "
            "`check-write-path` re-run instruction (spec 4.8, AC-WP-016)."
        )
        assert "no_write_path" in evidence_section and "not_found" in evidence_section, (
            f"{_CODE_REVIEWER_PATH}'s ## Evidence section must name the failing "
            "`check-write-path` verdicts (`default`, `no_write_path`, `not_found`)."
        )
        assert "WRITE_PATH_UNVERIFIED" in evidence_section, (
            f"{_CODE_REVIEWER_PATH}'s ## Evidence section must state that a failing verdict "
            "is treated as a WRITE_PATH_UNVERIFIED rejection."
        )
