"""Issue #126 regression: security-reviewer prompt must scope findings to staged diff.

The security reviewer is a runtime prompt the LLM reads via Claude Agent
SDK; there is no Python entry point to call-and-assert against. This
test pins the prompt's canonical scope-contract language by-content so a
future edit cannot silently remove the rule that prevents the bug from
returning.

Bug: at runtime the security reviewer evaluated working-tree files
outside the active task's staged diff, picked up findings on a file
owned by a different work unit (E1-F2-S4-T1's
``infra/remote-state/prod/terragrunt.hcl``), and BLOCKed the in-flight
work unit on a problem that didn't belong to it. Issue #126.

Fix: ``plugin/devbench/agents/security-reviewer.md`` now contains an
explicit five-bullet "Scope contract (issue #126 -- enforced)" block
that:
  1. Mandates capturing the in-scope path set from ``devbench get-diff``
     before reading any file.
  2. Forbids reading files outside the in-scope set.
  3. Forbids raw ``git diff origin/main`` style scope computations.
  4. Requires every finding's verdict body to cite an in-scope path
     (out-of-scope findings are silently dropped).
  5. Returns PASS with a fixed summary when the in-scope set is empty.

This test asserts each of the five rule-fragments is present so an
accidental edit that removes one of them fails CI before merging.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench" / "agents" / "security-reviewer.md"


@pytest.fixture(scope="module")
def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@pytest.mark.integration
class TestSecurityReviewerScopeContract:
    """Pin the issue #126 scope-contract text so the bug cannot return."""

    def test_prompt_file_exists(self) -> None:
        assert PROMPT_PATH.is_file(), f"security-reviewer prompt missing at {PROMPT_PATH}"

    def test_scope_contract_block_present(self, prompt_text: str) -> None:
        """The canonical 'Scope contract (issue #126 -- enforced)' header must be present."""
        assert "Scope contract (issue #126 -- enforced)" in prompt_text, (
            "security-reviewer.md is missing the issue #126 scope-contract header. "
            "The header anchors the five rules that prevent out-of-scope findings; "
            "do not remove it."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            # Rule 1: capture the in-scope path set first
            "Capture the in-scope path set first",
            # Rule 2: forbid reading files outside the in-scope set
            "Do NOT read files outside the in-scope set",
            # Rule 3: forbid raw-git scope computations
            "Do NOT run `git diff origin/main`",
            # Rule 4: every finding must cite an in-scope path; out-of-scope dropped
            "Every finding's verdict body must cite an in-scope path",
            # Rule 5: empty in-scope set -> PASS with fixed summary
            "no in-scope changes",
        ],
    )
    def test_each_scope_rule_present(self, prompt_text: str, fragment: str) -> None:
        assert fragment in prompt_text, (
            f"security-reviewer.md is missing scope-contract rule fragment: {fragment!r}. "
            "All five rule fragments must be present so issue #126 cannot return."
        )

    def test_authoritative_get_diff_reference_kept(self, prompt_text: str) -> None:
        """The pre-existing ADR-12 reference to `devbench get-diff` must remain in the
        Evidence block; the new scope contract layers on top of it, not replaces it."""
        pattern = re.compile(
            r"`devbench get-diff`.*AUTHORITATIVE source",
            re.DOTALL,
        )
        assert pattern.search(prompt_text) is not None, (
            "security-reviewer.md should still reference `devbench get-diff` as the "
            "AUTHORITATIVE scope source (per ADR-12). The issue #126 fix layers "
            "stricter rules on top of that authority, not replacing it."
        )

    def test_regression_test_path_referenced(self, prompt_text: str) -> None:
        """The prompt mentions the regression-test file by name so future readers find this test."""
        assert "test_security_review_scope.py" in prompt_text, (
            "security-reviewer.md should mention "
            "`tests/test_integration/test_security_review_scope.py` so future "
            "readers know where the prompt's scope-contract is regression-tested."
        )
