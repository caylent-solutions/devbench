"""Link integrity verification for docs/zero-to-ready.md (AC-191-10).

Verifies that every relative file reference in docs/zero-to-ready.md resolves
to an existing file on disk.  A new operator following the guide must be able
to click every link and land on a real document -- dangling links are a blocker
for the zero-to-ready walkthrough.

Spec source: spec/devbench-self-improve.md section 5.2.
Issue: #191.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"
DOCS_DIR = REPO_ROOT / "docs"


def _read_doc() -> str:
    return ZERO_TO_READY_DOC.read_text(encoding="utf-8")


def _extract_relative_file_links(text: str) -> list[str]:
    """Return every relative file path that appears in a Markdown link.

    Captures the path portion of ``[label](path)`` expressions, excluding:
    - Bare anchor links (starting with ``#``).
    - Absolute URLs (starting with ``http://`` or ``https://``).
    - mailto: links.
    - Anchor fragments appended to file paths (stripped before returning).
    """
    raw_links = re.findall(r"\[(?:[^\]]*)\]\(([^)]+)\)", text)
    file_links: list[str] = []
    for raw in raw_links:
        stripped = raw.strip()
        # Skip bare anchors, absolute URLs, and mail links.
        if stripped.startswith("#"):
            continue
        if stripped.startswith(("http://", "https://", "mailto:")):
            continue
        # Strip trailing fragment anchor (e.g. ``foo.md#section``).
        path_part = stripped.split("#")[0]
        if path_part:
            file_links.append(path_part)
    return file_links


def _resolve_link(link: str) -> Path:
    """Resolve a relative link from docs/zero-to-ready.md to an absolute Path."""
    return (DOCS_DIR / link).resolve()


@pytest.mark.unit
class TestZeroToReadyLinkIntegrity:
    """AC-191-10: every relative file link in zero-to-ready.md must resolve to an existing file."""

    def test_zero_to_ready_doc_exists(self) -> None:
        """Pre-condition: docs/zero-to-ready.md must exist."""
        assert ZERO_TO_READY_DOC.is_file(), (
            "docs/zero-to-ready.md must exist -- it is the authoritative onboarding guide."
        )

    def test_all_relative_file_links_resolve(self) -> None:
        """Every relative link target in docs/zero-to-ready.md must point to an existing file.

        This test enumerates all Markdown links of the form ``[label](path)`` that are
        not bare anchors, not absolute URLs, and not mailto: URIs.  For each such link
        the target path is resolved relative to ``docs/`` (the directory containing
        zero-to-ready.md) and asserted to be an existing file.

        Raises:
            AssertionError: when one or more relative links point to non-existent files.
        """
        text = _read_doc()
        links = _extract_relative_file_links(text)
        assert links, (
            "docs/zero-to-ready.md must contain at least one relative Markdown link -- "
            "the document is expected to cross-reference companion docs."
        )
        dangling: list[tuple[str, Path]] = []
        for link in links:
            resolved = _resolve_link(link)
            if not resolved.exists():
                dangling.append((link, resolved))
        assert not dangling, (
            "docs/zero-to-ready.md contains relative links that do not resolve to "
            "existing files.  A new operator following the guide will hit broken "
            "links.  Fix each dangling reference:\n"
            + "\n".join(f"  link={raw!r}  resolved={resolved}" for raw, resolved in dangling)
        )

    @pytest.mark.parametrize(
        "expected_link_fragment",
        [
            "onboarding.md",
            "llm-authentication.md",
            "backlog-contract.md",
            "creating-specs-and-backlogs.md",
            "cli-reference.md",
            "manual-blockers.md",
            "live-smoke-evidence.md",
        ],
    )
    def test_expected_cross_reference_links_present(self, expected_link_fragment: str) -> None:
        """Each canonical cross-reference doc must appear as a link in zero-to-ready.md.

        Raises:
            AssertionError: when the expected link fragment is absent from the document.
        """
        text = _read_doc()
        assert expected_link_fragment in text, (
            f"docs/zero-to-ready.md must link to '{expected_link_fragment}' -- "
            f"it is a canonical cross-reference doc listed in the zero-to-ready "
            f"Cross-references section (AC-191-10)."
        )
