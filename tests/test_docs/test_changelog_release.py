"""Structural pins for the E9.F3 doc-audit: CHANGELOG per-epic entries,
top-level release entry, pyproject.toml version bump, and the two new ADRs.

AC-E9-3a: pyproject version >= 0.2.0.
AC-E9-3: CHANGELOG has per-epic entries and a release entry; ADR-25 and ADR-26 exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
ADR_DONE_INTEGRITY = REPO_ROOT / "docs" / "adr" / "25-done-integrity.md"
ADR_SELF_COMPLETION = REPO_ROOT / "docs" / "adr" / "26-self-completion-autonomy.md"

_EPIC_TOKENS: list[tuple[str, str]] = [
    ("E0", "Opus 4.8"),
    ("E1", "SDK"),
    ("E2", "Blocked"),
    ("E3", "Observability"),
    ("E4", "Backlog"),
    ("E5", "Authoring"),
    ("E6", "Skill"),
    ("E7", "Operator"),
    ("E8", "Done-Integrity"),
]

_RELEASE_ENTRY_SENTINEL = "## [0.2.0]"

_MIN_VERSION_TUPLE: tuple[int, int, int] = (0, 2, 0)


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a PEP 440 version string into a comparable integer tuple.

    Raises ValueError if the string cannot be parsed as X.Y.Z.
    """
    parts = version_str.strip().split(".")
    if len(parts) < 3:
        raise ValueError(
            f"Version '{version_str}' does not match the expected X.Y.Z format. "
            "Ensure pyproject.toml [project] version field is set to at least 0.2.0."
        )
    return tuple(int(p) for p in parts[:3])


def _read_pyproject_version() -> str:
    """Extract the version string from pyproject.toml.

    Raises FileNotFoundError if pyproject.toml is absent, ValueError if the
    version line is missing.
    """
    if not PYPROJECT.is_file():
        raise FileNotFoundError(f"pyproject.toml not found at {PYPROJECT}. Ensure the repo root is correct.")
    text = PYPROJECT.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            _, _, raw = stripped.partition("=")
            return raw.strip().strip('"').strip("'")
    raise ValueError(
        f"version field not found in pyproject.toml at {PYPROJECT}. Add 'version = \"0.2.0\"' under [project]."
    )


@pytest.mark.unit
class TestPyprojectVersion:
    """AC-E9-3a: pyproject.toml version must be at least 0.2.0."""

    def test_pyproject_exists(self) -> None:
        assert PYPROJECT.is_file(), f"pyproject.toml not found at {PYPROJECT}. The file must exist in the repo root."

    def test_version_field_present(self) -> None:
        version = _read_pyproject_version()
        assert version, "pyproject.toml version field must be non-empty."

    def test_version_is_at_least_0_2_0(self) -> None:
        version_str = _read_pyproject_version()
        parsed = _parse_version(version_str)
        assert parsed >= _MIN_VERSION_TUPLE, (
            f"pyproject.toml version is '{version_str}' which is below the required "
            f"minimum {'.'.join(str(x) for x in _MIN_VERSION_TUPLE)}. "
            "Bump the version field to at least 0.2.0 (spec Section 6 release gate, AC-E9-3a)."
        )

    def test_version_line_has_no_em_dash(self) -> None:
        """The version line itself must not contain em-dash characters."""
        text = PYPROJECT.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                assert "\u2014" not in line, (
                    "The version line in pyproject.toml must not contain em-dash characters (U+2014). "
                    "Use '--' (double hyphen) instead."
                )
                return


@pytest.mark.unit
class TestChangelogReleaseEntry:
    """AC-E9-3: CHANGELOG must have a top-level release entry for 0.2.0."""

    def test_changelog_exists(self) -> None:
        assert CHANGELOG.is_file(), f"CHANGELOG.md not found at {CHANGELOG}. The file must exist in the repo root."

    def test_release_entry_present(self) -> None:
        text = CHANGELOG.read_text(encoding="utf-8")
        assert _RELEASE_ENTRY_SENTINEL in text, (
            f"CHANGELOG.md must contain a top-level release entry '{_RELEASE_ENTRY_SENTINEL}'. "
            "Add a versioned release section for the 0.2.0 release (AC-E9-3)."
        )

    def test_release_entry_appears_in_first_500_lines(self) -> None:
        """Release entry should be near the top of the file, not buried."""
        lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
        for line in lines[:500]:
            if _RELEASE_ENTRY_SENTINEL in line:
                return
        raise AssertionError(
            f"'{_RELEASE_ENTRY_SENTINEL}' not found in the first 500 lines of CHANGELOG.md. "
            "The release entry must appear near the top of the file."
        )

    def test_no_em_dash_in_changelog_new_release_section(self) -> None:
        """The new 0.2.0 release section body must not contain em-dash characters.

        Only the body of the [0.2.0] section (up to the next '## [' header) is
        checked -- the pre-existing [Unreleased] section is not in this task's
        Changes Manifest and may carry legacy em-dashes.
        """
        text = CHANGELOG.read_text(encoding="utf-8")
        if _RELEASE_ENTRY_SENTINEL not in text:
            return
        start = text.index(_RELEASE_ENTRY_SENTINEL)
        rest = text[start + len(_RELEASE_ENTRY_SENTINEL) :]
        next_header_pos = rest.find("\n## [")
        section_body = rest[:next_header_pos] if next_header_pos != -1 else rest
        assert "\u2014" not in section_body, (
            f"The {_RELEASE_ENTRY_SENTINEL} section body in CHANGELOG.md must not contain "
            "em-dash characters (U+2014). Use '--' (double hyphen) instead."
        )


@pytest.mark.unit
class TestChangelogPerEpicEntries:
    """AC-E9-3: CHANGELOG must carry a per-epic entry for every epic E0-E8."""

    @pytest.mark.parametrize("epic_id,token", _EPIC_TOKENS)
    def test_epic_entry_present(self, epic_id: str, token: str) -> None:
        text = CHANGELOG.read_text(encoding="utf-8")
        assert _RELEASE_ENTRY_SENTINEL in text, (
            f"'{_RELEASE_ENTRY_SENTINEL}' section not found; cannot check per-epic entries."
        )
        start = text.index(_RELEASE_ENTRY_SENTINEL)
        section = text[start:]
        assert epic_id in section and token in section, (
            f"CHANGELOG.md section '{_RELEASE_ENTRY_SENTINEL}' must contain an entry "
            f"for epic {epic_id} with token '{token}'. "
            f"Add a per-epic CHANGELOG entry for {epic_id} (AC-E9-3)."
        )


@pytest.mark.unit
class TestADRDoneIntegrity:
    """AC-E9-3: the done-integrity ADR (docs/adr/25-done-integrity.md) must exist."""

    def test_adr_file_exists(self) -> None:
        assert ADR_DONE_INTEGRITY.is_file(), (
            f"ADR file not found at {ADR_DONE_INTEGRITY}. "
            "Add docs/adr/25-done-integrity.md for the E8 done-integrity decisions (AC-E9-3)."
        )

    def test_adr_has_status_accepted(self) -> None:
        text = ADR_DONE_INTEGRITY.read_text(encoding="utf-8")
        assert "Accepted" in text, f"{ADR_DONE_INTEGRITY.name} must have Status: Accepted. Update the Status field."

    def test_adr_has_context_section(self) -> None:
        text = ADR_DONE_INTEGRITY.read_text(encoding="utf-8")
        assert "## Context" in text, f"{ADR_DONE_INTEGRITY.name} must have a '## Context' section."

    def test_adr_has_decision_section(self) -> None:
        text = ADR_DONE_INTEGRITY.read_text(encoding="utf-8")
        assert "## Decision" in text, f"{ADR_DONE_INTEGRITY.name} must have a '## Decision' section."

    def test_adr_references_done_integrity(self) -> None:
        text = ADR_DONE_INTEGRITY.read_text(encoding="utf-8")
        assert "done" in text.lower() and "integrity" in text.lower(), (
            f"{ADR_DONE_INTEGRITY.name} must reference 'done' and 'integrity' -- "
            "it is the ADR for the E8 done-integrity hardening (AC-E9-3)."
        )

    def test_adr_no_em_dash(self) -> None:
        text = ADR_DONE_INTEGRITY.read_text(encoding="utf-8")
        assert "\u2014" not in text, (
            f"{ADR_DONE_INTEGRITY.name} must not contain em-dash characters (U+2014). Use '--' (double hyphen) instead."
        )


@pytest.mark.unit
class TestADRSelfCompletion:
    """AC-E9-3: the self-completion ADR (docs/adr/26-self-completion-autonomy.md) must exist."""

    def test_adr_file_exists(self) -> None:
        assert ADR_SELF_COMPLETION.is_file(), (
            f"ADR file not found at {ADR_SELF_COMPLETION}. "
            "Add docs/adr/26-self-completion-autonomy.md for the Section 16 "
            "self-completion/autonomy posture decisions (AC-E9-3)."
        )

    def test_adr_has_status_accepted(self) -> None:
        text = ADR_SELF_COMPLETION.read_text(encoding="utf-8")
        assert "Accepted" in text, f"{ADR_SELF_COMPLETION.name} must have Status: Accepted."

    def test_adr_has_context_section(self) -> None:
        text = ADR_SELF_COMPLETION.read_text(encoding="utf-8")
        assert "## Context" in text, f"{ADR_SELF_COMPLETION.name} must have a '## Context' section."

    def test_adr_has_decision_section(self) -> None:
        text = ADR_SELF_COMPLETION.read_text(encoding="utf-8")
        assert "## Decision" in text, f"{ADR_SELF_COMPLETION.name} must have a '## Decision' section."

    def test_adr_references_autonomy_or_self_completion(self) -> None:
        text = ADR_SELF_COMPLETION.read_text(encoding="utf-8")
        lower = text.lower()
        assert "autonom" in lower or "self-completion" in lower or "completion" in lower, (
            f"{ADR_SELF_COMPLETION.name} must reference autonomy or self-completion -- "
            "it is the ADR for the Section 16 self-completion posture (AC-E9-3)."
        )

    def test_adr_no_em_dash(self) -> None:
        text = ADR_SELF_COMPLETION.read_text(encoding="utf-8")
        assert "\u2014" not in text, (
            f"{ADR_SELF_COMPLETION.name} must not contain em-dash characters (U+2014). "
            "Use '--' (double hyphen) instead."
        )
