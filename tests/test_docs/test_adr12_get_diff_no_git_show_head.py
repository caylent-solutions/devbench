"""Regression pins for E4-F1-S1-T2 -- doc-staleness guard.

Asserts that the four documentation files updated by E4-F1-S1-T2 no longer
reference the removed ``git show HEAD`` fallback as current defer-mode
behavior. The new behavior (as implemented by E4-F1-S1-T1) is a
task-attributed commit lookup via ``git log --grep "^<unit-id>:"`` that
exits 45 (GET_DIFF_NO_ATTRIBUTABLE) when no matching commit is found.

Acceptance criteria covered: AC-DOC-001 through AC-DOC-005.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REF = REPO_ROOT / "docs" / "cli-reference.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
ADR_12 = REPO_ROOT / "docs" / "adr" / "12-mode-aware-get-diff.md"
FAQ = REPO_ROOT / "docs" / "faq.md"

_STALE_PHRASE = "git show HEAD"

_NEW_BEHAVIOR_LOG_GREP = "git log --grep"
_NEW_BEHAVIOR_EXIT_45 = "GET_DIFF_NO_ATTRIBUTABLE"
_NEW_BEHAVIOR_EXIT_45_NUM = "exit 45"


@pytest.mark.unit
class TestAdr12DocNoGitShowHeadFallback:
    """Guard against stale 'git show HEAD' references in the four doc files.

    Each file must not mention the old fallback and must describe the new
    task-attributed commit lookup + exit 45 behavior.
    """

    def test_cli_reference_no_git_show_head(self) -> None:
        """docs/cli-reference.md must not describe git show HEAD as current defer-mode behavior."""
        text = CLI_REF.read_text(encoding="utf-8")
        assert _STALE_PHRASE not in text, (
            f"docs/cli-reference.md still references '{_STALE_PHRASE}' "
            "which was removed by E4-F1-S1-T1. "
            "Update the defer_pr mode bullet to describe the git log --grep "
            "task-attributed lookup and exit 45 (GET_DIFF_NO_ATTRIBUTABLE) path."
        )

    def test_cli_reference_describes_log_grep(self) -> None:
        """docs/cli-reference.md must describe the git log --grep lookup."""
        text = CLI_REF.read_text(encoding="utf-8")
        assert _NEW_BEHAVIOR_LOG_GREP in text or "task-attributed" in text, (
            "docs/cli-reference.md must describe the 'git log --grep' task-attributed "
            "commit lookup introduced by E4-F1-S1-T1 for the post-commit empty case."
        )

    def test_cli_reference_describes_exit_45(self) -> None:
        """docs/cli-reference.md must reference GET_DIFF_NO_ATTRIBUTABLE or exit 45."""
        text = CLI_REF.read_text(encoding="utf-8")
        assert _NEW_BEHAVIOR_EXIT_45 in text or _NEW_BEHAVIOR_EXIT_45_NUM in text, (
            "docs/cli-reference.md must document the exit 45 (GET_DIFF_NO_ATTRIBUTABLE) "
            "diagnostic path introduced by E4-F1-S1-T1."
        )

    def test_architecture_no_git_show_head(self) -> None:
        """docs/architecture.md must not describe git show HEAD as current defer-mode behavior."""
        text = ARCHITECTURE.read_text(encoding="utf-8")
        assert _STALE_PHRASE not in text, (
            f"docs/architecture.md still references '{_STALE_PHRASE}' "
            "which was removed by E4-F1-S1-T1. "
            "Update the get-diff bullet to describe the task-attributed commit "
            "lookup and exit 45 path."
        )

    def test_architecture_describes_new_behavior(self) -> None:
        """docs/architecture.md must describe the new task-attributed lookup or exit 45."""
        text = ARCHITECTURE.read_text(encoding="utf-8")
        assert _NEW_BEHAVIOR_LOG_GREP in text or "task-attributed" in text or _NEW_BEHAVIOR_EXIT_45 in text, (
            "docs/architecture.md must describe the task-attributed commit lookup "
            "or reference GET_DIFF_NO_ATTRIBUTABLE to reflect the new behavior."
        )

    def test_adr12_consequences_no_git_show_head_as_current(self) -> None:
        """ADR-12 Consequences section must not state git show HEAD is returned post-commit."""
        text = ADR_12.read_text(encoding="utf-8")
        consequences_start = text.find("## Consequences")
        alternatives_start = text.find("## Alternatives")
        if consequences_start == -1:
            pytest.fail("ADR-12 is missing a '## Consequences' section.")
        section_end = alternatives_start if alternatives_start > consequences_start else len(text)
        consequences_text = text[consequences_start:section_end]
        assert _STALE_PHRASE not in consequences_text, (
            f"ADR-12 ## Consequences section still states '{_STALE_PHRASE}' "
            "is returned post-commit. This was the old behavior removed by E4-F1-S1-T1. "
            "Update the bullet to reflect the git log --grep lookup and exit 45 path."
        )

    def test_adr12_related_files_no_git_show_head(self) -> None:
        """ADR-12 Related files section must not reference git show HEAD substitution."""
        text = ADR_12.read_text(encoding="utf-8")
        related_start = text.find("## Related files")
        if related_start == -1:
            pytest.fail("ADR-12 is missing a '## Related files' section.")
        related_text = text[related_start:]
        assert _STALE_PHRASE not in related_text, (
            f"ADR-12 ## Related files section still mentions '{_STALE_PHRASE}'. "
            "Update the cli.py bullet to describe the new task-attributed lookup behavior."
        )

    def test_faq_no_git_show_head(self) -> None:
        """docs/faq.md must not describe git show HEAD as current defer-mode behavior."""
        text = FAQ.read_text(encoding="utf-8")
        assert _STALE_PHRASE not in text, (
            f"docs/faq.md still references '{_STALE_PHRASE}' "
            "which was removed by E4-F1-S1-T1. "
            "Update the FAQ answer to describe the task-attributed commit lookup "
            "and exit 45 (GET_DIFF_NO_ATTRIBUTABLE) path."
        )

    def test_faq_describes_new_behavior(self) -> None:
        """docs/faq.md must describe the new task-attributed lookup or exit 45."""
        text = FAQ.read_text(encoding="utf-8")
        assert _NEW_BEHAVIOR_LOG_GREP in text or "task-attributed" in text or _NEW_BEHAVIOR_EXIT_45 in text, (
            "docs/faq.md must describe the git log --grep task-attributed commit lookup "
            "or reference GET_DIFF_NO_ATTRIBUTABLE to reflect the new behavior."
        )

    def test_no_stale_references_in_any_target_file(self) -> None:
        """None of the four target doc files may contain the stale git show HEAD phrase."""
        files_with_stale = []
        for doc_path in [CLI_REF, ARCHITECTURE, ADR_12, FAQ]:
            if _STALE_PHRASE in doc_path.read_text(encoding="utf-8"):
                files_with_stale.append(str(doc_path.relative_to(REPO_ROOT)))
        assert not files_with_stale, (
            f"The following doc files still contain '{_STALE_PHRASE}' "
            f"(removed by E4-F1-S1-T1): {files_with_stale}. "
            "Update each file to describe the task-attributed commit lookup and exit 45 path."
        )
