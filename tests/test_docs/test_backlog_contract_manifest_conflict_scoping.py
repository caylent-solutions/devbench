"""Documentation guard tests for the Manifest Conflict Rule status-scoping section.

Verifies that docs/backlog-contract.md documents:
- The default in-flight ERROR set (in-queue/proposed/blocked).
- The draft/hold default WARNING behavior.
- The out-of-scope statuses (done/declined/in-progress).
- The validate-backlog --strict / --include-draft authoring-time check verbatim.

Also verifies that CHANGELOG.md has an [Unreleased] entry referencing issue #267.

Spec reference: E13-F1-S2 AC-1, spec Section 4 / Section 8.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BACKLOG_CONTRACT_DOC = REPO_ROOT / "docs" / "backlog-contract.md"
CHANGELOG_DOC = REPO_ROOT / "CHANGELOG.md"


def _get_manifest_conflict_section(text: str) -> str:
    """Return the text of the '## Manifest Conflict Rule' section.

    Slices from the heading to the next '## ' heading (or end-of-file).
    Raises AssertionError if the section is not found.
    """
    start = text.find("## Manifest Conflict Rule")
    assert start != -1, "docs/backlog-contract.md must contain a '## Manifest Conflict Rule' section."
    next_section = text.find("\n## ", start + 1)
    if next_section == -1:
        return text[start:]
    return text[start:next_section]


def _get_unreleased_section(text: str) -> str:
    """Return the text of the '[Unreleased]' section from a CHANGELOG.

    Slices from the '[Unreleased]' heading to the next versioned release heading
    (a line matching '## [<digit>') or end-of-file.
    Raises AssertionError if the '[Unreleased]' heading is absent.
    """
    start = text.find("[Unreleased]")
    assert start != -1, "CHANGELOG.md must contain an [Unreleased] section."
    next_release = re.search(r"\n## \[[\d]", text[start:])
    if next_release:
        return text[start : start + next_release.start()]
    return text[start:]


@pytest.mark.unit
class TestManifestConflictRuleStatusScoping:
    """AC-1: backlog-contract.md documents the status-scoping for the Manifest Conflict Rule."""

    def test_backlog_contract_doc_exists(self) -> None:
        assert BACKLOG_CONTRACT_DOC.is_file(), (
            "docs/backlog-contract.md must exist -- it is the authoritative "
            "contract reference for validate-backlog rules."
        )

    def test_in_flight_error_set_documented(self) -> None:
        """The doc must name in-queue, proposed, and blocked as the default ERROR set."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        for status in ("in-queue", "proposed", "blocked"):
            assert status in manifest_section, (
                f"Manifest Conflict Rule section must document '{status}' as part of "
                "the default in-flight ERROR set (in-queue/proposed/blocked)."
            )

    def test_in_flight_error_set_labeled_as_error(self) -> None:
        """The in-queue/proposed/blocked overlap must be explicitly labeled a hard ERROR."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        assert "ERROR" in manifest_section or "error" in manifest_section.lower(), (
            "Manifest Conflict Rule section must explicitly label in-queue/proposed/blocked "
            "overlap as a hard ERROR so authors know the default severity."
        )

    def test_draft_hold_warning_documented(self) -> None:
        """The doc must document draft/hold overlap as a default WARNING (not ERROR)."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        assert "draft" in manifest_section, (
            "Manifest Conflict Rule section must mention 'draft' in the status-scoping discussion."
        )
        assert "hold" in manifest_section, (
            "Manifest Conflict Rule section must mention 'hold' in the status-scoping discussion."
        )
        assert "WARNING" in manifest_section or "warning" in manifest_section.lower(), (
            "Manifest Conflict Rule section must label draft/hold overlap as a WARNING "
            "so authors understand it is non-fatal by default."
        )

    def test_out_of_scope_statuses_documented(self) -> None:
        """The doc must name done, declined, and in-progress as out-of-scope."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        for status in ("done", "declined", "in-progress"):
            assert status in manifest_section, (
                f"Manifest Conflict Rule section must document '{status}' as out-of-scope "
                "for manifest conflict detection."
            )

    def test_strict_flag_documented(self) -> None:
        """The doc must show the --strict flag verbatim."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        assert "--strict" in manifest_section, (
            "Manifest Conflict Rule section must document the '--strict' flag for the authoring-time check verbatim."
        )

    def test_include_draft_alias_documented(self) -> None:
        """The doc must show the --include-draft alias verbatim."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        assert "--include-draft" in manifest_section, (
            "Manifest Conflict Rule section must document the '--include-draft' alias "
            "for the authoring-time strict check verbatim."
        )

    def test_validate_backlog_invocation_shown(self) -> None:
        """The doc must show a validate-backlog invocation example."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        manifest_section = _get_manifest_conflict_section(text)
        assert "validate-backlog" in manifest_section, (
            "Manifest Conflict Rule section must include a validate-backlog invocation "
            "example so authors can run the authoring-time strict check."
        )

    def test_no_em_dash_in_manifest_conflict_section(self) -> None:
        """The Manifest Conflict Rule section must not contain em-dash characters."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        section = _get_manifest_conflict_section(text)
        em_dash = "\u2014"
        assert em_dash not in section, (
            "No em-dash (U+2014) is allowed in docs/backlog-contract.md -- use '--' (double hyphen) instead."
        )


@pytest.mark.unit
class TestChangelogIssue267Entry:
    """AC-1: CHANGELOG.md has an [Unreleased] entry referencing issue #267."""

    def test_changelog_exists(self) -> None:
        assert CHANGELOG_DOC.is_file(), "CHANGELOG.md must exist at the repo root."

    def test_changelog_has_unreleased_section(self) -> None:
        text = CHANGELOG_DOC.read_text(encoding="utf-8")
        assert "[Unreleased]" in text, "CHANGELOG.md must have an [Unreleased] section for in-flight changes."

    def test_changelog_unreleased_references_issue_267(self) -> None:
        """The [Unreleased] section must reference issue #267."""
        text = CHANGELOG_DOC.read_text(encoding="utf-8")
        unreleased_section = _get_unreleased_section(text)
        assert "#267" in unreleased_section, (
            "CHANGELOG.md [Unreleased] section must reference issue #267 for the "
            "draft/hold manifest conflict detection and strict flag feature."
        )

    def test_changelog_unreleased_mentions_draft_hold_conflict(self) -> None:
        """The [Unreleased] entry must describe the draft/hold detection feature."""
        text = CHANGELOG_DOC.read_text(encoding="utf-8")
        unreleased_section = _get_unreleased_section(text)
        has_draft_hold = (
            "draft" in unreleased_section and "hold" in unreleased_section
        ) or "draft/hold" in unreleased_section
        assert has_draft_hold, (
            "CHANGELOG.md [Unreleased] section must mention draft/hold manifest conflict "
            "detection to describe what issue #267 delivered."
        )

    def test_changelog_unreleased_mentions_strict_flag(self) -> None:
        """The [Unreleased] entry must mention the --strict flag or strict check."""
        text = CHANGELOG_DOC.read_text(encoding="utf-8")
        unreleased_section = _get_unreleased_section(text)
        assert "--strict" in unreleased_section or "strict" in unreleased_section.lower(), (
            "CHANGELOG.md [Unreleased] entry for issue #267 must mention the --strict flag "
            "or strict check so readers know how to invoke the authoring-time check."
        )

    def test_changelog_no_em_dash_in_issue_267_entry(self) -> None:
        """No em-dash characters in the bullet that references issue #267."""
        text = CHANGELOG_DOC.read_text(encoding="utf-8")
        # Find the bullet point that mentions #267 and verify it has no em-dash.
        entry_start = text.find("#267")
        assert entry_start != -1, "CHANGELOG.md must contain a reference to issue #267."
        # Scope to the bullet (find start of bullet line, end at next bullet or blank+bullet).
        # Walk back to the start of the bullet line.
        line_start = text.rfind("\n", 0, entry_start) + 1
        # Walk forward to the end of the bullet block (next line starting with '-' or '##').
        after = text[entry_start:]
        next_bullet = re.search(r"\n(-|\s*##)", after)
        entry_text = text[line_start : entry_start + next_bullet.start()] if next_bullet else text[line_start:]
        em_dash = "\u2014"
        assert em_dash not in entry_text, (
            "No em-dash (U+2014) is allowed in the issue #267 CHANGELOG entry -- use '--' (double hyphen) instead."
        )
