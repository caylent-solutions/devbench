"""Issue #127 regression: manifest-amender SCOPE must not require requested files
to already be in the Manifest.

The manifest amender is a runtime prompt the LLM reads via Claude Agent
SDK; there is no Python entry point to call-and-assert against. This
test pins the prompt's canonical SCOPE-rule language by-content so a
future edit cannot silently re-introduce the buggy framing.

Bug: at runtime the manifest amender confirmed APPROACH_AUTH PASS and
JUSTIFICATION_COHERENCE PASS on a legitimate ``tdd_green_production_fix``
amendment that requested adding two files (``services/api/pyproject.toml``
+ ``uv.lock``) to E2-F3-S2-T1's Changes Manifest. The amender then FAILed
on SCOPE because "the requested files are not in the Changes Manifest"
-- which is the entire purpose of an amendment. Issue #127.

Fix: ``plugin/devbench-orchestrate/agents/manifest-amender.md`` SCOPE rule (rule 2)
now contains an explicit "Critical (issue #127)" sub-section that:
  1. Forbids using "the requested file is not in the current Changes
     Manifest" as a SCOPE-failure reason.
  2. References the deterministic pre-filter rule that already verified
     each requested file is present in the staged diff.
  3. References the Layer-3 post-check that verifies AC-FINAL-015 after
     `apply-amendment` runs.
  4. Re-states the SCOPE evaluation criterion: minimal diff +
     Approach-coherent, not pre-existence in the Manifest.
  5. Includes a self-correcting heuristic ("if you find yourself
     writing 'the requested files are not in the Changes Manifest' as
     a SCOPE-FAIL justification, stop and re-evaluate").

This test asserts each of the five protective fragments is present so
an accidental edit cannot return the bug.
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
class TestManifestAmenderScopeRule:
    """Pin the issue #127 SCOPE-rule text so the circular-rejection bug cannot return."""

    def test_prompt_file_exists(self) -> None:
        assert PROMPT_PATH.is_file(), f"manifest-amender prompt missing at {PROMPT_PATH}"

    def test_critical_block_present(self, prompt_text: str) -> None:
        """The 'Critical (issue #127)' sub-block under SCOPE rule 2 must be present."""
        assert "Critical (issue #127)" in prompt_text, (
            "manifest-amender.md SCOPE rule is missing the issue #127 'Critical' "
            "sub-block. That sub-block forbids the buggy 'requested files are not "
            "in the Manifest' rejection reason; do not remove it."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            # 1. The explicit forbidden-reason statement
            "is **never** a SCOPE-failure reason",
            # 2. Reference to deterministic pre-filter rule 5
            "pre-filter rule 5 has already confirmed every requested file is present in the staged diff",
            # 3. Reference to Layer-3 post-check
            "Layer-3 post-check after `apply-amendment` will verify AC-FINAL-015",
            # 4. SCOPE re-statement: minimal + Approach-coherent, not pre-existence
            "evaluates whether each requested *diff* is minimal and Approach-coherent",
            # 5. Self-correcting heuristic
            "stop and re-evaluate against the Approach + diff text instead",
        ],
    )
    def test_each_protective_fragment_present(self, prompt_text: str, fragment: str) -> None:
        assert fragment in prompt_text, (
            f"manifest-amender.md is missing SCOPE protective fragment: {fragment!r}. "
            "All five protective fragments must be present so issue #127 cannot return."
        )

    def test_regression_test_path_referenced(self, prompt_text: str) -> None:
        """The prompt mentions this test file by name so future readers find the regression."""
        assert "test_manifest_amender_scope.py" in prompt_text, (
            "manifest-amender.md should mention "
            "`tests/test_integration/test_manifest_amender_scope.py` so future "
            "readers know where the SCOPE rule is regression-tested."
        )

    def test_existing_three_question_framing_kept(self, prompt_text: str) -> None:
        """The pre-existing 'three semantic questions' framing must remain; the issue
        #127 fix layers a clarification onto SCOPE (question 2), not a rewrite."""
        for question in (
            "Approach authorisation",
            "Scope minimality",
            "Justification coherence",
        ):
            assert question in prompt_text, (
                f"manifest-amender.md should still ask the canonical semantic question "
                f"{question!r}. The issue #127 fix is additive on SCOPE, not a rewrite "
                "of the three-question framing."
            )
