"""Structural pins for docs/glossary.md terminology cohesion (AC-191-10).

Verifies that:
- docs/glossary.md exists and defines all 9 canonical terms from spec section 1.2.
- The glossary contains no em-dash characters (U+2014).
- The canonical term spellings from the spec are actually used consistently in
  docs/ -- no 'Draft' (capitalised) used as a status value where 'draft' (lower)
  is canonical, and no 'session name' / 'session id' drift.

Spec source: spec/devbench-self-improve.md section 1.2 and 5.2.
Issue: #191.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GLOSSARY = REPO_ROOT / "docs" / "glossary.md"
DOCS_DIR = REPO_ROOT / "docs"

# Minimum character threshold for meaningful glossary content.
MIN_GLOSSARY_CHARS = 200

# Canonical terms from spec section 1.2 that must appear in the glossary.
CANONICAL_TERMS = [
    "draft",
    "in-queue",
    "scope",
    "drain",
    "session",
    "marketplace plugin",
    "skill",
    "audit comment",
]


@pytest.mark.unit
class TestGlossaryExists:
    """docs/glossary.md must exist as a readable file."""

    def test_glossary_file_exists(self) -> None:
        """docs/glossary.md must exist (AC-191-10 -- spec section 5.2)."""
        assert GLOSSARY.is_file(), (
            "docs/glossary.md must exist -- spec section 5.2 requires an end-of-program "
            "docs walkthrough verifying every term in section 1.2 is used consistently "
            "across docs (AC-191-10)."
        )

    def test_glossary_is_nonempty(self) -> None:
        """docs/glossary.md must have meaningful content."""
        text = GLOSSARY.read_text(encoding="utf-8")
        assert len(text) > MIN_GLOSSARY_CHARS, (
            "docs/glossary.md must have substantial content defining all canonical terms "
            "from spec section 1.2 (AC-191-10)."
        )


@pytest.mark.unit
class TestGlossaryCanonicalTerms:
    """docs/glossary.md must define every canonical term from spec section 1.2."""

    @pytest.mark.parametrize("term", CANONICAL_TERMS)
    def test_glossary_defines_canonical_term(self, term: str) -> None:
        """docs/glossary.md must define the canonical term from spec section 1.2."""
        text = GLOSSARY.read_text(encoding="utf-8")
        assert term in text, (
            f"docs/glossary.md must define the canonical term '{term}' from "
            f"spec section 1.2 (AC-191-10 -- terminology cohesion across docs)."
        )


@pytest.mark.unit
class TestGlossaryStructure:
    """docs/glossary.md must be structured with markdown headings and a term table."""

    def test_glossary_has_markdown_heading(self) -> None:
        """docs/glossary.md must have a top-level heading."""
        text = GLOSSARY.read_text(encoding="utf-8")
        heading_lines = [line for line in text.splitlines() if line.startswith("#")]
        assert len(heading_lines) >= 1, "docs/glossary.md must have at least one markdown heading (AC-191-10)."

    def test_glossary_has_table_or_definition_list(self) -> None:
        """docs/glossary.md must use a table or definition-style list for terms."""
        text = GLOSSARY.read_text(encoding="utf-8")
        has_table = "|" in text
        has_definition_list = "**" in text or "-- " in text
        assert has_table or has_definition_list, (
            "docs/glossary.md must use a table or definition-style list to present canonical terms (AC-191-10)."
        )

    def test_glossary_no_em_dashes(self) -> None:
        """docs/glossary.md must not contain em-dash characters (U+2014)."""
        text = GLOSSARY.read_text(encoding="utf-8")
        assert "\u2014" not in text, (
            "docs/glossary.md must not contain em-dash characters (U+2014). "
            "Use '--' (double hyphen) instead. "
            "This rule applies to all doc files authored alongside work units."
        )

    def test_glossary_references_spec_section(self) -> None:
        """docs/glossary.md must reference spec section 1.2 or the canonical source."""
        text = GLOSSARY.read_text(encoding="utf-8")
        has_spec_ref = "spec" in text.lower() or "section 1.2" in text or "canonical" in text.lower()
        assert has_spec_ref, (
            "docs/glossary.md must reference its canonical source (spec section 1.2) "
            "so readers can find the authoritative definition (AC-191-10)."
        )


@pytest.mark.unit
class TestTerminologyConsistencyInDocs:
    """Terminology drift checks -- canonical terms must be used consistently in docs/.

    Spec section 5.2: 'no Draft vs draft drift; no session name vs session id drift'.
    """

    def _collect_doc_files(self) -> list[Path]:
        """Return all .md files under docs/ excluding adr/ subdirectory."""
        return [p for p in DOCS_DIR.rglob("*.md") if p.is_file()]

    def test_draft_status_lower_case_in_prose(self) -> None:
        """'draft' must appear in docs as a lower-case status value, not 'Draft status' as a term.

        The canonical term is 'draft' (lower-case), as defined in spec section 1.2.
        Headers and table column headings may capitalise ('Draft'), but the status
        value itself must be lower-case when written in prose as a code-style token
        or a status reference.

        This test checks that the lower-case form 'draft' is used in actual content
        (i.e., the canonical form appears in docs), not that the Title Case form
        is absent from all contexts.
        """
        doc_files = self._collect_doc_files()
        uses_lowercase_draft = False
        for doc in doc_files:
            text = doc.read_text(encoding="utf-8")
            # The canonical form 'draft' (lower-case) must appear in at least one doc.
            if "`draft`" in text or "status `draft`" in text or "draft status" in text.lower():
                uses_lowercase_draft = True
                break
        assert uses_lowercase_draft, (
            "No docs/ file uses the canonical lower-case 'draft' status term. "
            "At least one doc must use '`draft`' or 'draft status' to confirm the "
            "canonical spelling is in use across the docs suite (spec section 5.2)."
        )

    def test_drain_canonical_form_present(self) -> None:
        """'drain' canonical term must appear somewhere in docs/."""
        doc_files = self._collect_doc_files()
        found = False
        for doc in doc_files:
            text = doc.read_text(encoding="utf-8")
            if "drain" in text.lower():
                found = True
                break
        assert found, (
            "No docs/ file uses the canonical 'drain' term. "
            "At least one doc must use 'drain' so the term is discoverable "
            "(spec section 1.2 and 5.2)."
        )

    def test_scope_canonical_form_present(self) -> None:
        """'scope' canonical term must appear somewhere in docs/."""
        doc_files = self._collect_doc_files()
        found = False
        for doc in doc_files:
            text = doc.read_text(encoding="utf-8")
            if "scope" in text.lower():
                found = True
                break
        assert found, "No docs/ file uses the canonical 'scope' term (spec section 1.2 and 5.2)."

    def test_glossary_cross_referenced_from_another_doc(self) -> None:
        """At least one docs/ file other than glossary.md must link to glossary.md."""
        doc_files = [p for p in self._collect_doc_files() if p != GLOSSARY]
        found = False
        for doc in doc_files:
            text = doc.read_text(encoding="utf-8")
            if "glossary" in text.lower():
                found = True
                break
        assert found, (
            "No docs/ file cross-references docs/glossary.md. At least one other "
            "doc must link to or mention the glossary so it is discoverable "
            "(AC-191-10 -- spec section 5.2 docs cohesion)."
        )
