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
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench" / "skills" / "orchestrate" / "SKILL.md"
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
            # Forbidden pattern explicitly named.
            "recap-prose pattern",
            "FORBIDDEN",
            # The two valid turn-ends.
            "Every turn MUST end with EITHER (a) a tool call",
            "uv run devbench next",
            # Explanation of why the bug fires.
            "Claude Code interprets a turn-end-without-tool-call",
            "Stop hook's block decision arrives after the turn has already wound down",
            # Self-correcting heuristic.
            "delete the recap, and emit the actual tool call instead",
            # Cross-link to this regression test.
            "test_orchestrate_skill_no_recap_anti_pattern.py",
        ],
    )
    def test_each_protective_fragment_present(self, skill_text: str, fragment: str) -> None:
        assert fragment in skill_text, (
            f"SKILL.md is missing protective fragment: {fragment!r}. "
            "All fragments must be present so issue #140 cannot return."
        )
