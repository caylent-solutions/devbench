"""Doc-consistency tests for docs/block-types.md code snippets.

Verifies that the ``_RECOVERY_BODY_RE`` regex snippet and the pattern table
in docs/block-types.md are consistent with the actual compiled regex in
``src/devbench/backlog/proposal.py`` and syntactically valid Python.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.backlog.proposal import _RECOVERY_BODY_RE

REPO_ROOT = Path(__file__).parent.parent.parent
BLOCK_TYPES_DOC = REPO_ROOT / "docs" / "block-types.md"


def _extract_recovery_body_re_snippet(doc_text: str) -> str:
    """Extract the Python code block following the _RECOVERY_BODY_RE heading.

    Returns the raw code-fence content (everything between ```python and ```).

    Raises:
        ValueError: If no _RECOVERY_BODY_RE code snippet is found.
    """
    pattern = re.compile(
        r"### `_RECOVERY_BODY_RE`.*?\n```python\n(.*?)```",
        re.DOTALL,
    )
    match = pattern.search(doc_text)
    if match is None:
        raise ValueError("docs/block-types.md does not contain a _RECOVERY_BODY_RE Python code snippet")
    return match.group(1).strip()


def _compile_snippet(snippet: str) -> re.Pattern[str]:
    """Compile the _RECOVERY_BODY_RE snippet extracted from the doc.

    Extracts the raw regex string literals from the snippet, concatenates
    them, and compiles with IGNORECASE to produce the same compiled regex
    the snippet would produce at runtime.

    Raises:
        ValueError: If no raw-string literals are found in the snippet.
    """
    raw_parts = re.findall(r'r"([^"]*)"', snippet)
    if not raw_parts:
        raise ValueError('Snippet does not contain any r"..." raw string literals')
    combined = "".join(raw_parts)
    if "IGNORECASE" not in snippet:
        raise ValueError("Snippet does not specify re.IGNORECASE")
    return re.compile(combined, re.IGNORECASE)


# -- Positive cases: derived from the production regex -----------------------

_POSITIVE_CASES: list[tuple[str, str]] = [
    ("amendment-reject", "kebab-case base form"),
    ("Amendment-reject", "kebab-case title-case (IGNORECASE)"),
    ("out-of-scope", "out-of-scope signal"),
    ("ALL_REVIEWS_FAILED", "all reviews failed signal"),
    ("REVIEW_REJECTED", "review rejected signal"),
    ("dependency foo not yet terminal", "dependency long form"),
    ("dep bar not yet terminal", "dependency short form"),
]

# -- Negative cases ----------------------------------------------------------

_NEGATIVE_CASES: list[tuple[str, str]] = [
    ("unrelated blocked reason", "unrelated text"),
    ("amend reject", "truncated keyword"),
    ("amendment", "keyword alone without reject"),
    ("rejected amendment", "reversed word order"),
]


@pytest.mark.unit
class TestRecoveryBodyReDocSnippet:
    """Verify the _RECOVERY_BODY_RE code snippet in docs/block-types.md."""

    def test_block_types_doc_exists(self) -> None:
        assert BLOCK_TYPES_DOC.is_file(), (
            "docs/block-types.md must exist -- it is the operator reference for BlockedTaskState."
        )

    def test_snippet_extracts_successfully(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        snippet = _extract_recovery_body_re_snippet(doc_text)
        assert "_RECOVERY_BODY_RE" in snippet

    def test_snippet_compiles_to_valid_regex(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        snippet = _extract_recovery_body_re_snippet(doc_text)
        compiled = _compile_snippet(snippet)
        assert isinstance(compiled, re.Pattern)

    def test_snippet_pattern_matches_production_regex(self) -> None:
        """AC-FIX-003: Assert the doc snippet compiles to the same pattern
        as the actual _RECOVERY_BODY_RE from proposal.py."""
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        snippet = _extract_recovery_body_re_snippet(doc_text)
        compiled = _compile_snippet(snippet)
        assert compiled.pattern == _RECOVERY_BODY_RE.pattern, (
            f"Doc snippet pattern {compiled.pattern!r} does not match "
            f"production _RECOVERY_BODY_RE pattern {_RECOVERY_BODY_RE.pattern!r}"
        )
        assert compiled.flags == _RECOVERY_BODY_RE.flags, (
            f"Doc snippet flags {compiled.flags!r} do not match "
            f"production _RECOVERY_BODY_RE flags {_RECOVERY_BODY_RE.flags!r}"
        )

    @pytest.mark.parametrize(
        ("text", "description"),
        _POSITIVE_CASES,
        ids=[desc for _, desc in _POSITIVE_CASES],
    )
    def test_snippet_matches_positive_case(self, text: str, description: str) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        snippet = _extract_recovery_body_re_snippet(doc_text)
        compiled = _compile_snippet(snippet)
        assert compiled.search(text) is not None, (
            f"_RECOVERY_BODY_RE doc snippet should match: {text!r} ({description})"
        )

    @pytest.mark.parametrize(
        ("text", "description"),
        _NEGATIVE_CASES,
        ids=[desc for _, desc in _NEGATIVE_CASES],
    )
    def test_snippet_rejects_negative_case(self, text: str, description: str) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        snippet = _extract_recovery_body_re_snippet(doc_text)
        compiled = _compile_snippet(snippet)
        assert compiled.search(text) is None, (
            f"_RECOVERY_BODY_RE doc snippet should NOT match: {text!r} ({description})"
        )


@pytest.mark.unit
class TestRecoveryBodyRePatternTable:
    """Verify the pattern table in docs/block-types.md lists all spec-required patterns."""

    def test_table_contains_amendment_reject_row(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        assert "amendment-reject" in doc_text, "Pattern table must contain a row for 'amendment-reject'"

    def test_table_contains_out_of_scope_row(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        assert "out-of-scope" in doc_text, "Pattern table must contain a row for 'out-of-scope'"

    def test_table_contains_all_reviews_failed_row(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        assert "ALL_REVIEWS_FAILED" in doc_text, "Pattern table must contain a row for 'ALL_REVIEWS_FAILED'"

    def test_table_contains_review_rejected_row(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        assert "REVIEW_REJECTED" in doc_text, "Pattern table must contain a row for 'REVIEW_REJECTED'"

    def test_table_contains_dependency_not_yet_terminal_row(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        assert "dependency .* not yet terminal" in doc_text, (
            "Pattern table must contain a row for 'dependency .* not yet terminal'"
        )

    def test_table_contains_dep_short_form_row(self) -> None:
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        assert "dep .* not yet terminal" in doc_text, "Pattern table must contain a row for 'dep .* not yet terminal'"

    def test_line_reference_matches_production(self) -> None:
        """Verify the heading line reference matches the actual line in proposal.py."""
        doc_text = BLOCK_TYPES_DOC.read_text(encoding="utf-8")
        # The heading should reference the correct line number
        assert "(line 221)" in doc_text, (
            "The _RECOVERY_BODY_RE heading must reference line 221 (current production line in proposal.py)"
        )
