"""Unit tests for shared workflow authoring-patterns documentation (E12-F1-S2-T1).

Asserts:
  (a) The shared doc exists at the expected path and contains a named section
      for each of the six required patterns.
  (b) Both create-spec and spec-to-backlog SKILL.md files reference the shared
      doc via a link rather than restating the full pattern bodies inline (DRY).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

SHARED_DOC_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "docs" / "workflow-authoring-patterns.md"

CREATE_SPEC_SKILL_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "create-spec" / "SKILL.md"

SPEC_TO_BACKLOG_SKILL_PATH = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
)

REQUIRED_PATTERNS = [
    ("dimension-fan-out", "dimension fan-out"),
    ("per-finding-adversarial-verification", "per-finding adversarial verification"),
    ("decisions-ledger", "decisions-ledger"),
    ("deterministic-gates", "deterministic gates"),
    ("file-partitioned-parallel-repair", "file-partitioned parallel repair"),
    ("file-based-agent-output", "file-based agent output"),
]

SHARED_DOC_RELATIVE_LINK = "docs/workflow-authoring-patterns.md"


@pytest.mark.unit
class TestSharedDocExists:
    """AC-1: The shared workflow-authoring-patterns doc must exist."""

    def test_shared_doc_file_exists(self) -> None:
        """The doc must be present at the canonical path."""
        assert SHARED_DOC_PATH.exists(), (
            f"Shared workflow patterns doc not found at {SHARED_DOC_PATH}. "
            "Create plugin-authoring/devbench-authoring/docs/workflow-authoring-patterns.md"
        )

    def test_shared_doc_is_non_empty(self) -> None:
        """The doc must contain content, not just whitespace."""
        assert SHARED_DOC_PATH.exists(), f"File missing: {SHARED_DOC_PATH}"
        content = SHARED_DOC_PATH.read_text()
        assert content.strip(), f"Shared doc at {SHARED_DOC_PATH} is empty."


@pytest.mark.unit
class TestSharedDocPatternSections:
    """AC-1: The shared doc must define a named section for each of the six patterns."""

    @pytest.mark.parametrize("pattern_key,heading_fragment", REQUIRED_PATTERNS)
    def test_pattern_section_exists(self, pattern_key: str, heading_fragment: str) -> None:
        """Each pattern must appear as a Markdown heading in the shared doc."""
        assert SHARED_DOC_PATH.exists(), f"File missing: {SHARED_DOC_PATH}"
        content = SHARED_DOC_PATH.read_text().lower()
        assert heading_fragment.lower() in content, (
            f"Pattern '{pattern_key}' not found in {SHARED_DOC_PATH}. "
            f"Expected a section containing '{heading_fragment}'. "
            "Add a ## or ### heading for this pattern."
        )

    @pytest.mark.parametrize("pattern_key,heading_fragment", REQUIRED_PATTERNS)
    def test_pattern_has_markdown_heading(self, pattern_key: str, heading_fragment: str) -> None:
        """Each pattern must be introduced with a Markdown heading (## or ###), not just prose."""
        assert SHARED_DOC_PATH.exists(), f"File missing: {SHARED_DOC_PATH}"
        lines = SHARED_DOC_PATH.read_text().splitlines()
        heading_lines = [line for line in lines if line.startswith("#")]
        combined = "\n".join(heading_lines).lower()
        assert heading_fragment.lower() in combined, (
            f"Pattern '{pattern_key}' has no Markdown heading in {SHARED_DOC_PATH}. "
            f"Expected a '#'-prefixed line containing '{heading_fragment}'."
        )


@pytest.mark.unit
class TestSharedDocApplicationAgnostic:
    """AC-1: The shared doc must not contain domain-specific taxonomy."""

    def test_no_domain_taxonomy_in_doc(self) -> None:
        """The doc must describe generic patterns, not domain-specific ones."""
        assert SHARED_DOC_PATH.exists(), f"File missing: {SHARED_DOC_PATH}"
        content = SHARED_DOC_PATH.read_text().lower()
        domain_markers = [
            "devbench-specific",
            "this workspace",
        ]
        for marker in domain_markers:
            assert marker not in content, (
                f"Domain-specific marker '{marker}' found in {SHARED_DOC_PATH}. "
                "The shared doc must be application-agnostic."
            )


@pytest.mark.unit
class TestNoEmDashInSharedDoc:
    """All changed files must not contain em-dash characters (U+2014)."""

    def test_no_em_dash_in_shared_doc(self) -> None:
        """The shared doc must not contain em-dash (U+2014)."""
        assert SHARED_DOC_PATH.exists(), f"File missing: {SHARED_DOC_PATH}"
        content = SHARED_DOC_PATH.read_text()
        assert "\u2014" not in content, (
            f"Em-dash (U+2014) found in {SHARED_DOC_PATH}. Use '--' (double hyphen) instead."
        )


@pytest.mark.unit
class TestCreateSpecSkillReference:
    """AC-2: create-spec SKILL.md must reference the shared doc."""

    def test_create_spec_skill_references_shared_doc(self) -> None:
        """create-spec SKILL.md must contain a link to the shared doc."""
        assert CREATE_SPEC_SKILL_PATH.exists(), f"File missing: {CREATE_SPEC_SKILL_PATH}"
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert SHARED_DOC_RELATIVE_LINK in content, (
            f"create-spec SKILL.md does not reference the shared patterns doc. "
            f"Expected to find '{SHARED_DOC_RELATIVE_LINK}' in {CREATE_SPEC_SKILL_PATH}."
        )

    def test_create_spec_skill_does_not_restate_pattern_bodies(self) -> None:
        """create-spec SKILL.md must not restate full pattern bodies inline (DRY).

        The pattern bodies belong exclusively in the shared doc.  A SKILL.md
        that includes detailed prose for all six patterns under its own headings
        -- rather than linking -- violates the DRY constraint.  We detect this
        by checking that at most two of the six heading fragments appear as
        Markdown headings inside the SKILL.md itself; the two allowed are
        incidental mentions, not inline re-definitions.
        """
        assert CREATE_SPEC_SKILL_PATH.exists(), f"File missing: {CREATE_SPEC_SKILL_PATH}"
        lines = CREATE_SPEC_SKILL_PATH.read_text().splitlines()
        heading_lines = [line for line in lines if line.startswith("#")]
        combined = "\n".join(heading_lines).lower()
        inline_count = sum(1 for _, fragment in REQUIRED_PATTERNS if fragment.lower() in combined)
        assert inline_count <= 2, (
            f"create-spec SKILL.md restates {inline_count} pattern bodies as headings "
            f"(allowed: at most 2). Pattern bodies must live in the shared doc "
            f"({SHARED_DOC_RELATIVE_LINK}), not be duplicated inline."
        )

    def test_no_em_dash_in_create_spec_skill(self) -> None:
        """create-spec SKILL.md must not contain em-dash (U+2014) in its changed sections."""
        assert CREATE_SPEC_SKILL_PATH.exists(), f"File missing: {CREATE_SPEC_SKILL_PATH}"
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "\u2014" not in content, (
            f"Em-dash (U+2014) found in {CREATE_SPEC_SKILL_PATH}. Use '--' (double hyphen) instead."
        )


@pytest.mark.unit
class TestSpecToBacklogSkillReference:
    """AC-2: spec-to-backlog SKILL.md must reference the shared doc."""

    def test_spec_to_backlog_skill_references_shared_doc(self) -> None:
        """spec-to-backlog SKILL.md must contain a link to the shared doc."""
        assert SPEC_TO_BACKLOG_SKILL_PATH.exists(), f"File missing: {SPEC_TO_BACKLOG_SKILL_PATH}"
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert SHARED_DOC_RELATIVE_LINK in content, (
            f"spec-to-backlog SKILL.md does not reference the shared patterns doc. "
            f"Expected to find '{SHARED_DOC_RELATIVE_LINK}' in {SPEC_TO_BACKLOG_SKILL_PATH}."
        )

    def test_spec_to_backlog_skill_does_not_restate_pattern_bodies(self) -> None:
        """spec-to-backlog SKILL.md must not restate full pattern bodies inline (DRY).

        Same DRY check as for create-spec: at most two pattern heading fragments
        may appear as headings inside the SKILL.md file itself.
        """
        assert SPEC_TO_BACKLOG_SKILL_PATH.exists(), f"File missing: {SPEC_TO_BACKLOG_SKILL_PATH}"
        lines = SPEC_TO_BACKLOG_SKILL_PATH.read_text().splitlines()
        heading_lines = [line for line in lines if line.startswith("#")]
        combined = "\n".join(heading_lines).lower()
        inline_count = sum(1 for _, fragment in REQUIRED_PATTERNS if fragment.lower() in combined)
        assert inline_count <= 2, (
            f"spec-to-backlog SKILL.md restates {inline_count} pattern bodies as headings "
            f"(allowed: at most 2). Pattern bodies must live in the shared doc "
            f"({SHARED_DOC_RELATIVE_LINK}), not be duplicated inline."
        )

    def test_no_em_dash_in_spec_to_backlog_skill(self) -> None:
        """spec-to-backlog SKILL.md must not contain em-dash (U+2014) in its changed sections."""
        assert SPEC_TO_BACKLOG_SKILL_PATH.exists(), f"File missing: {SPEC_TO_BACKLOG_SKILL_PATH}"
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "\u2014" not in content, (
            f"Em-dash (U+2014) found in {SPEC_TO_BACKLOG_SKILL_PATH}. Use '--' (double hyphen) instead."
        )
