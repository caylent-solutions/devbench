"""Interview-completeness and every-invocation pins for E2-F8-S1-T2 (spec
`integration-reality-gates-hardening.md` section 4.15; D-16, G12;
AC-E2-F8-S1-T2-1 through -5).

The `bootstrap-environment` skill's Step 0 rewrite has no automated coverage
of its own: `test_review` proved by mutation that deleting the entire Step 0
block from `SKILL.md` left the full suite green (COVERAGE_REGRESSION), and
`code_review` independently reproduced the same deletion (MISSING_AC_EVIDENCE).
This module closes that gap with real, falsifiable assertions against the
shipped `SKILL.md`, `docs/skills/bootstrap-environment.md` and
`docs/onboarding.md` -- never by reading the prose, matching the unit's own
Definition of Ready.

The `#### \\`KEY\\`` block-parsing and Recommended/Alternatives/Free-form
completeness check are shared with the `configure-devbench` skill's own
structural pin (E2-F8-S1-T1) via `fixtures.interview_block_helpers` rather
than re-implemented here (test_review DRY_VIOLATION).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fixtures.interview_block_helpers import (
    assert_interview_blocks_complete,
    assert_interview_blocks_show_current_value,
    parse_interview_blocks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SKILL_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "bootstrap-environment" / "SKILL.md"
DOC_PATH = REPO_ROOT / "docs" / "skills" / "bootstrap-environment.md"
ONBOARDING_DOC_PATH = REPO_ROOT / "docs" / "onboarding.md"

# The six environment decisions Step 0 owns, in the order SKILL.md presents
# them (AC-E2-F8-S1-T2-1). `GH_TOKEN` is the block key even though the
# heading itself reads "`GH_TOKEN` / `DEVBENCH_GH_TOKEN_FILE`" -- the shared
# parser only captures the FIRST backtick-quoted token in a `####` heading.
_ENV_DECISION_KEYS: tuple[str, ...] = (
    "DEVBENCH_USE_BEDROCK",
    "DEVBENCH_BEDROCK_REGION",
    "DEVBENCH_CLAUDE_CREDENTIALS_FILE",
    "DEVBENCH_CLAUDE_MODEL",
    "GH_TOKEN",
    "DEVBENCH_GH_ORG",
)

# The two literal phrases the every-invocation contract (AC-E2-F8-S1-T2-3,
# D-16) must state, matching the phrasing sibling task E2-F8-S1-T1 already
# established for configure-devbench.
_EVERY_INVOCATION_PHRASE = "runs in full on every invocation"
_NEVER_REUSE_PHRASE = "never silently reuses a prior answer"

_STEP_0_HEADING = "## Step 0 -- Interview environment decisions"
_STEP_1_HEADING = "## Step 1 -- Read the target-repo list"

_ONBOARDING_STEP_4_HEADING = "## Step 4: bootstrap-environment"
_ONBOARDING_STEP_5_HEADING = "## Step 5: make start"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _onboarding_text() -> str:
    return ONBOARDING_DOC_PATH.read_text(encoding="utf-8")


def _extract_section(text: str, start_heading: str, end_heading: str) -> str:
    """Return the substring of `text` from `start_heading` (inclusive) up to
    `end_heading` (exclusive). Raises `AssertionError` naming whichever
    heading is absent -- never silently returns an empty or partial slice."""
    start = text.find(start_heading)
    if start == -1:
        raise AssertionError(f"expected heading {start_heading!r} not found in text")
    end = text.find(end_heading, start)
    if end == -1:
        raise AssertionError(f"expected heading {end_heading!r} not found after {start_heading!r}")
    return text[start:end]


def _render_synthetic_block(key: str, *, with_current_value: bool = True) -> str:
    lines = [f"#### `{key}` -- Synthetic decision"]
    lines.append("")
    lines.append("A synthetic explanation for a seeded-control fixture.")
    lines.append("")
    lines.append("- **Recommended:** `x` -- because.")
    lines.append("- **Alternatives:** `y` (consequence)")
    lines.append("- **Free-form:** type your own value.")
    lines.append("")
    if with_current_value:
        lines.append(f"Current value shown to the operator: this session's exported `{key}` value, if set.")
    lines.append("")
    return "\n".join(lines)


@pytest.mark.unit
class TestRealSkillInterviewBlocksComplete:
    """AC-E2-F8-S1-T2-1/-2: every environment decision Step 0 owns gets a
    complete Recommended/Alternatives/Free-form interview block."""

    def test_every_owned_decision_has_a_complete_interview_block(self) -> None:
        blocks = parse_interview_blocks(_skill_text())
        assert_interview_blocks_complete(blocks, list(_ENV_DECISION_KEYS), skill_label="bootstrap-environment SKILL.md")

    def test_parsed_block_count_is_at_least_six(self) -> None:
        """Sanity: the parser finds at least one block per owned decision (a
        vacuous parser that found zero blocks would let the completeness
        check above pass trivially on an empty list)."""
        blocks = parse_interview_blocks(_skill_text())
        assert len(blocks) >= len(_ENV_DECISION_KEYS), (
            f"expected at least {len(_ENV_DECISION_KEYS)} parsed interview blocks in the real SKILL.md, "
            f"got {len(blocks)}"
        )

    def test_every_block_shows_current_value(self) -> None:
        blocks = parse_interview_blocks(_skill_text())
        assert_interview_blocks_show_current_value(
            blocks, list(_ENV_DECISION_KEYS), skill_label="bootstrap-environment SKILL.md"
        )


@pytest.mark.unit
class TestRealSkillEveryInvocationContract:
    """AC-E2-F8-S1-T2-3 (D-16): the SKILL and its doc both state the
    every-invocation, never-silently-reuse contract."""

    def test_skill_states_every_invocation_contract(self) -> None:
        text = _skill_text()
        assert _EVERY_INVOCATION_PHRASE in text, (
            f"SKILL.md must state the interview {_EVERY_INVOCATION_PHRASE!r} (AC-E2-F8-S1-T2-3)"
        )

    def test_skill_states_never_reuse_phrase(self) -> None:
        text = _skill_text()
        assert _NEVER_REUSE_PHRASE in text, f"SKILL.md must state it {_NEVER_REUSE_PHRASE!r} (AC-E2-F8-S1-T2-3)"

    def test_doc_states_every_invocation_contract(self) -> None:
        text = _doc_text()
        assert _EVERY_INVOCATION_PHRASE in text, (
            f"docs/skills/bootstrap-environment.md must state {_EVERY_INVOCATION_PHRASE!r} (AC-E2-F8-S1-T2-3/-4)"
        )


@pytest.mark.unit
class TestSkillDocDescribesInterviewContract:
    """AC-E2-F8-S1-T2-4: the skill doc describes the interview contract -- it
    carries an '## Every-invocation contract' section and its step-by-step
    list opens with the Step 0 interview rather than the clone-first flow."""

    def test_doc_has_every_invocation_contract_section(self) -> None:
        text = _doc_text()
        assert "## Every-invocation contract" in text, (
            "docs/skills/bootstrap-environment.md must carry an "
            "'## Every-invocation contract' section (AC-E2-F8-S1-T2-4)"
        )

    def test_doc_step_by_step_lists_step_0_interview_first(self) -> None:
        text = _doc_text()
        step_by_step = _extract_section(text, "## What the skill does (step by step)", "## Self-verify retry loop")
        assert "0. **Interviews environment decisions**" in step_by_step, (
            "docs/skills/bootstrap-environment.md's step-by-step list must open with the Step 0 "
            "interview, not the superseded clone-first flow (AC-E2-F8-S1-T2-4)"
        )


@pytest.mark.unit
class TestOnboardingStep4Sync:
    """spec Section 8 (documentation same-commit rule): docs/onboarding.md
    Step 4 must reflect the every-invocation Step 0 interview rather than
    the superseded interview-free flow (doc_review DOC_SYNC FAIL)."""

    def test_onboarding_step4_states_every_invocation_interview(self) -> None:
        text = _onboarding_text()
        step4 = _extract_section(text, _ONBOARDING_STEP_4_HEADING, _ONBOARDING_STEP_5_HEADING)
        assert _EVERY_INVOCATION_PHRASE in step4, f"docs/onboarding.md Step 4 must state {_EVERY_INVOCATION_PHRASE!r}"

    def test_onboarding_step4_mentions_step_0_interview(self) -> None:
        text = _onboarding_text()
        step4 = _extract_section(text, _ONBOARDING_STEP_4_HEADING, _ONBOARDING_STEP_5_HEADING)
        assert "Step 0" in step4, "docs/onboarding.md Step 4 must mention the Step 0 interview"

    def test_onboarding_step4_self_verify_mentions_step_0(self) -> None:
        """The self-verify bullet must name Step 0 alongside clone/asdf/make
        validate, or the doc silently contradicts docs/skills/bootstrap-
        environment.md's self-verify list (doc_review DOC_SYNC FAIL)."""
        text = _onboarding_text()
        step4 = _extract_section(text, _ONBOARDING_STEP_4_HEADING, _ONBOARDING_STEP_5_HEADING)
        assert step4.count("Step 0") >= 2, (
            "docs/onboarding.md Step 4 must reference Step 0 both when introducing the interview "
            "and in the self-verify description; got fewer than 2 references"
        )


@pytest.mark.unit
class TestMutationProof:
    """Reproduces test_review's and code_review's exact mutation to prove
    the pins above genuinely detect the Step 0 deletion that previously left
    the full suite green."""

    def test_deleting_step_0_from_the_real_skill_is_caught(self) -> None:
        skill_text = _skill_text()
        start = skill_text.index(_STEP_0_HEADING)
        end = skill_text.index(_STEP_1_HEADING)
        mutated = skill_text[:start] + skill_text[end:]
        assert _STEP_0_HEADING not in mutated

        mutated_blocks = parse_interview_blocks(mutated)
        with pytest.raises(AssertionError):
            assert_interview_blocks_complete(
                mutated_blocks, list(_ENV_DECISION_KEYS), skill_label="bootstrap-environment SKILL.md"
            )

        # Control: the real, unmutated SKILL.md must still pass -- proving
        # this is a genuine mutation-triggered failure, not a permanently
        # broken assertion.
        real_blocks = parse_interview_blocks(skill_text)
        assert_interview_blocks_complete(
            real_blocks, list(_ENV_DECISION_KEYS), skill_label="bootstrap-environment SKILL.md"
        )


@pytest.mark.unit
class TestSeededCurrentValueControls:
    """Proves `assert_interview_blocks_show_current_value` is falsifiable:
    passes on a well-formed synthetic block set and fails naming the key
    when the current-value line is missing."""

    def test_current_value_check_passes_when_all_blocks_carry_it(self) -> None:
        keys = ["ALPHA", "BETA"]
        text = "\n".join(_render_synthetic_block(k) for k in keys)
        blocks = parse_interview_blocks(text)
        assert_interview_blocks_show_current_value(blocks, keys)  # must not raise

    def test_current_value_check_fails_naming_setting_missing_current_value(self) -> None:
        keys = ["ALPHA", "BETA"]
        text = "\n".join(_render_synthetic_block(k, with_current_value=(k != "BETA")) for k in keys)
        blocks = parse_interview_blocks(text)
        with pytest.raises(AssertionError, match=re.escape("BETA")) as exc_info:
            assert_interview_blocks_show_current_value(blocks, keys)
        assert "current-value line" in str(exc_info.value)


@pytest.mark.unit
class TestNoEmDashIntroduced:
    """AC-FINAL-012: no em-dash (U+2014) introduced in the three prose files
    this unit substantively rewrites -- SKILL.md, its skill doc, and
    docs/onboarding.md Step 4. CHANGELOG.md and docs/zero-to-ready.md are
    intentionally not whole-file-scanned here: CHANGELOG.md is a large,
    append-only history file with pre-existing content outside this unit's
    control, so a whole-file scan would fail on unrelated historical
    entries rather than on anything this unit introduced."""

    def test_no_em_dash_introduced_in_skill_and_docs(self) -> None:
        checked_paths = (SKILL_PATH, DOC_PATH, ONBOARDING_DOC_PATH)
        offenders = [str(p) for p in checked_paths if "\u2014" in p.read_text(encoding="utf-8")]
        assert not offenders, f"the following files contain an em-dash (U+2014, AC-FINAL-012): {offenders}"
