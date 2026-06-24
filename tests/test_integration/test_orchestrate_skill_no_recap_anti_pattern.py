"""Issue #140 regression: orchestrate SKILL must forbid recap-prose turn endings.

Background: Claude Code's Stop event fires when the model's turn ends
without a tool call. The Stop hook's block decision arrives AFTER that --
if the orchestrator emitted a prose recap as its final assistant message
(e.g. ``Next: log T4 verdicts and re-invoke executor.``), Claude Code
considers the turn done and the orchestrator effectively terminates.

Live evidence (2026-05-02T00:19): the session log showed
``※ recap: ... Next: log T4 verdicts and re-invoke executor.`` followed
by a Stop event, the hook's block decision, and orchestrator
self-termination.

Fix: SKILL.md adds an explicit rule that every turn MUST end with EITHER
a tool call OR a ``uv run devbench next`` invocation; recap-prose is
forbidden. This test pins the rule by-content via the existing
``test_security_review_scope.py`` / ``test_executor_review_pass_terminality.py``
pattern.

Extended (E10-F3-S1-T1): review-fail scenarios are the highest-risk
points for a prose recap turn-end because the orchestrator receives a
REVIEW_FAIL result and may be tempted to narrate the next step instead
of immediately invoking the executor. The back-to-back-tool-call contract
and step-6 post-review-fail passage are tightened to a strict
"no prose, emit the next tool call immediately" template.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "plugin"
    / "devbench-orchestrate"
    / "skills"
    / "orchestrate"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.mark.integration
class TestOrchestrateSkillNoRecapAntiPattern:
    """Pin the issue #140 no-recap rule so the bug cannot return."""

    def test_skill_file_exists(self) -> None:
        assert SKILL_PATH.is_file(), f"orchestrate SKILL.md missing at {SKILL_PATH}"

    def test_critical_marker_present(self, skill_text: str) -> None:
        assert "**CRITICAL (no recap at end of turn -- issue #140)" in skill_text, (
            "SKILL.md is missing the issue #140 CRITICAL marker. The marker "
            "anchors the rule that prevents recap-prose turn-ends from "
            "terminating the orchestrator."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            "recap-prose pattern",
            "FORBIDDEN",
            "Every turn MUST end with EITHER (a) a tool call",
            "uv run devbench next",
            "Claude Code interprets a turn-end-without-tool-call",
            "Stop hook's block decision arrives after the turn has already wound down",
            "delete the recap, and emit the actual tool call instead",
            "test_orchestrate_skill_no_recap_anti_pattern.py",
        ],
    )
    def test_each_protective_fragment_present(self, skill_text: str, fragment: str) -> None:
        assert fragment in skill_text, (
            f"SKILL.md is missing protective fragment: {fragment!r}. "
            "All fragments must be present so issue #140 cannot return."
        )


@pytest.mark.integration
class TestOrchestrateSkillReviewFailNoRecap:
    """Pin the strict no-prose template at the two highest-risk turn-end points.

    The post-review-fail point (step 6a) and the back-to-back-tool-call
    contract are where recap-prose is most likely to creep back in because
    the model receives a REVIEW_FAIL result and may narrate the next step.
    These tests ensure both passages use the strict no-prose template so
    the bug from issue #140 cannot return at those specific points.
    """

    def test_back_to_back_contract_has_no_prose_label(self, skill_text: str) -> None:
        """The back-to-back-tool-call contract must use the 'no prose' label."""
        assert "no prose" in skill_text, (
            "SKILL.md back-to-back-tool-call contract is missing the strict "
            "'no prose' label. The label is the strict template anchor that "
            "prevents recap-prose at the highest-risk turn-end point."
        )

    def test_step6_post_review_fail_has_no_prose_label(self, skill_text: str) -> None:
        """Step 6 (post-review-fail) must carry the 'no prose' label explicitly."""
        assert "No prose" in skill_text or "no prose" in skill_text, (
            "SKILL.md step-6 post-review-fail passage is missing the 'no prose' "
            "label. The label closes the highest-risk recap-prose window -- "
            "the moment after a REVIEW_FAIL result when the model is most "
            "likely to narrate the next step instead of emitting the Agent call."
        )

    def test_step6_review_fail_label_present(self, skill_text: str) -> None:
        """Step 6 must be labelled so the post-review-fail context is unambiguous."""
        assert "On REVIEW_FAIL:" in skill_text, (
            "SKILL.md is missing the 'On REVIEW_FAIL:' section label. "
            "The label anchors the no-prose rule to the exact scenario -- "
            "REVIEW_FAIL -- where recap-prose most often caused the loop "
            "to silently exit."
        )

    def test_step6_names_agent_call_as_very_next_tool_use(self, skill_text: str) -> None:
        """Step 6 must require the Agent call as the very next tool use."""
        assert "the very next tool use after the REVIEW_FAIL result" in skill_text, (
            "SKILL.md step-6 post-review-fail passage is missing the phrase "
            "'the very next tool use after the REVIEW_FAIL result'. "
            "This strict template leaves no room for a prose recap between "
            "the REVIEW_FAIL result and the executor Agent call."
        )

    @pytest.mark.parametrize(
        "no_prose_directive",
        [
            "Do NOT summarise or narrate between tool calls",
            "Do NOT summarise, do NOT explain, do NOT log a comment first",
        ],
    )
    def test_no_prose_directives_present(self, skill_text: str, no_prose_directive: str) -> None:
        """Both key no-prose directives must appear in the skill text."""
        assert no_prose_directive in skill_text, (
            f"SKILL.md is missing no-prose directive: {no_prose_directive!r}. "
            "Both directives (back-to-back contract and step-6 post-review-fail) "
            "must be present to form the strict no-prose template at the "
            "two highest-risk turn-end points."
        )
