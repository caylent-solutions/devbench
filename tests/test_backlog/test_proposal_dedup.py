"""Issue #141 regression: proposal dedup signature + intent-phrase helpers.

These pin the structural-hash contract used by ``find_matching_pending_proposal``
to detect duplicate recovery proposals. The hash is deterministic, normalisation-
stable, and operationally collision-free. Two recovery proposals with the same
target_repo + same files_to_own + same semantic intent collapse to the same
signature; two with any of those three different produce different signatures.
"""

from __future__ import annotations

import pytest

from devbench.backlog.proposal import _compute_fix_signature, _extract_intent_phrase


class TestComputeFixSignature:
    """Pin the SHA-256 hash contract."""

    def test_deterministic(self) -> None:
        sig1 = _compute_fix_signature("org/repo", ["a.py", "b.py"], "fix-something")
        sig2 = _compute_fix_signature("org/repo", ["a.py", "b.py"], "fix-something")
        assert sig1 == sig2

    def test_returns_64_hex_chars(self) -> None:
        sig = _compute_fix_signature("org/repo", ["a.py"], "fix-thing")
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_files_order_normalised(self) -> None:
        """Sorted file list -> identical signature regardless of input order."""
        sig1 = _compute_fix_signature("org/repo", ["a.py", "b.py"], "x")
        sig2 = _compute_fix_signature("org/repo", ["b.py", "a.py"], "x")
        assert sig1 == sig2

    def test_different_repo_yields_different_signature(self) -> None:
        sig1 = _compute_fix_signature("org/repo1", ["a.py"], "x")
        sig2 = _compute_fix_signature("org/repo2", ["a.py"], "x")
        assert sig1 != sig2

    def test_different_files_yield_different_signature(self) -> None:
        sig1 = _compute_fix_signature("org/repo", ["a.py"], "x")
        sig2 = _compute_fix_signature("org/repo", ["b.py"], "x")
        assert sig1 != sig2

    def test_different_intent_yields_different_signature(self) -> None:
        sig1 = _compute_fix_signature("org/repo", ["a.py"], "fix")
        sig2 = _compute_fix_signature("org/repo", ["a.py"], "remove")
        assert sig1 != sig2

    def test_empty_files_handled(self) -> None:
        """Empty files_to_own is a real case (validation-gate proposals)."""
        sig = _compute_fix_signature("org/repo", [], "generic")
        assert len(sig) == 64

    def test_empty_repo_string_handled(self) -> None:
        """Source task not in backlog index -> repo lookup returns ""; signature
        still computes. Different repos still hash differently."""
        sig = _compute_fix_signature("", ["a.py"], "x")
        assert len(sig) == 64
        sig_with_repo = _compute_fix_signature("org/repo", ["a.py"], "x")
        assert sig != sig_with_repo


class TestExtractIntentPhrase:
    """Pin the regex-based verb+noun extraction."""

    @pytest.mark.parametrize(
        "approach, expected",
        [
            ("Remove the pyproject.toml row from T1", "remove-row"),
            ("REMOVE the Makefile ROW from the manifest table", "remove-row"),
            ("Delete the services/api/uv.lock entry", "delete-entry"),
            ("Drop the conflicting manifest row", "drop-conflict-row"),
            ("Correct the manifest table in T1", "correct-manifest"),
            ("Fix the Changes Manifest in T1", "fix-manifest"),
            ("Untrack .coverage from git", "untrack"),
            ("Add .coverage to .gitignore", "gitignore-add"),
            ("Register smoke marker in pyproject", "register-marker"),
            ("Fix pyproject.toml placeholder", "fix-placeholder"),
            ("", "generic"),
            ("Implement the feature", "generic"),
            ("Some random approach text", "generic"),
        ],
    )
    def test_extracts_canonical_intent(self, approach: str, expected: str) -> None:
        assert _extract_intent_phrase(approach) == expected

    def test_case_insensitive_matching(self) -> None:
        """Lowercased + uppercased input both match the same pattern."""
        assert _extract_intent_phrase("REMOVE THE x ROW") == "remove-row"
        assert _extract_intent_phrase("remove the y row") == "remove-row"

    def test_intent_phrase_used_in_signature(self) -> None:
        """End-to-end: same approach text -> same intent -> same signature."""
        sig1 = _compute_fix_signature("org/repo", ["a.py"], _extract_intent_phrase("Remove the X row"))
        sig2 = _compute_fix_signature("org/repo", ["a.py"], _extract_intent_phrase("REMOVE THE Y row"))
        assert sig1 == sig2
