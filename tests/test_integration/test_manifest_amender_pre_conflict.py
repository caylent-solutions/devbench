"""Issue #137 regression: manifest-amender must reject (or auto-recommend dep)
when an amendment adds a file already claimed by another task's Manifest.

Background: the manifest-amender judges a `tdd_green_production_fix`
amendment for scope minimality, justification coherence, and approach
authorisation. It did NOT check whether the new file is already claimed
by another task's Manifest -- so amendments that introduce Manifest
Conflicts were approved, the source task transitioned to ``blocked`` on
the next ``validate-backlog``, and the recovery cascade had to clean up
after the fact.

Fix: ``plugin/devbench-orchestrate/agents/manifest-amender.md`` adds a fourth
pre-filter rule -- a "pre-conflict check" that scans every other
work-unit's Manifest before approving. Reject (or auto-recommend dep)
on conflict. This test pins the rule by-content via the existing
``test_manifest_amender_scope.py`` pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate" / "agents" / "manifest-amender.md"
)


@pytest.fixture(scope="module")
def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@pytest.mark.integration
class TestManifestAmenderPreConflict:
    """Pin the issue #137 rule so amendments that would create Manifest
    Conflicts are rejected at the source."""

    def test_prompt_file_exists(self) -> None:
        assert PROMPT_PATH.is_file(), f"manifest-amender prompt missing at {PROMPT_PATH}"

    def test_pre_conflict_rule_present(self, prompt_text: str) -> None:
        assert "**Pre-conflict check (issue #137).**" in prompt_text, (
            "manifest-amender.md is missing the issue #137 'Pre-conflict "
            "check' rule. That rule prevents amendments that would create "
            "Manifest Conflicts from being authored in the first place."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            # The check itself.
            "scan every other work-unit's Changes Manifest table for the same file path",
            # The terminal-status allow path. Issue #142 changed the wording
            # from "recommend dep edge" to "auto-wire the dep"; the
            # [CONFLICT_AUTODEP] audit shape is preserved.
            "If the conflict task is in a terminal state (`done` / `declined`)",
            "[CONFLICT_AUTODEP]",
            # The default reject path.
            "REJECT the amendment with a structured reason naming the conflict task",
            # Why: prevents the cascade-then-recover loop.
            "prevents new conflicts from being authored in the first place",
            # Cross-link to this regression test.
            "test_manifest_amender_pre_conflict.py",
        ],
    )
    def test_each_protective_fragment_present(self, prompt_text: str, fragment: str) -> None:
        assert fragment in prompt_text, (
            f"manifest-amender.md is missing protective fragment: {fragment!r}. "
            "All fragments must be present so issue #137 cannot return."
        )

    def test_three_question_framing_extended_to_four(self, prompt_text: str) -> None:
        """The pre-existing 'three semantic questions' framing extends to four;
        the new rule appends rather than replacing existing rules 1-3."""
        for question in (
            "Approach authorisation",
            "Scope minimality",
            "Justification coherence",
            "Pre-conflict check",
        ):
            assert question in prompt_text, (
                f"manifest-amender.md should ask all four semantic questions; missing: {question!r}"
            )
        # Verify the count update happened in the trailing sentence.
        assert "any of the four questions" in prompt_text, (
            "The trailing 'if the answer to any of the X questions is unclear or "
            "negative, reject' sentence must say 'four' now (was 'three')."
        )
