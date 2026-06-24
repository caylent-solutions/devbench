"""Tests for ``devbench.plugin_helpers.spec_backlog_contract``.

E12-F1-S3-T1: Marker-based FR / AC-N extraction and backlog-readiness
self-check for the create-spec -> spec-to-backlog pipeline.

Spec Section 4 E12-F1-S3 AC-1, AC-2, AC-3.
"""

from __future__ import annotations

import pytest

from devbench.plugin_helpers.spec_backlog_contract import (
    AC_MARKER,
    ReadinessError,
    check_backlog_readiness,
    extract_ac_section,
    extract_fr_list,
)

MARKER_SPEC = f"""# My Project Spec

## Target Repository

- **Repo:** `example/repo`
- **Branch:** `feat/my-feature`

## Section 2 -- Goals

FR-1: The system shall do X.
FR-2: The system shall do Y.
FR-3: The system shall handle Z errors.

{AC_MARKER}

- AC-1: The output contains X (Section 2).
- AC-2: Error Z is reported to stderr (Section 7).

## Section 6 -- Version semantics

Some version content here.

## Unit Inventory

- unit-1: First work item
- unit-2: Second work item
"""

LEGACY_SPEC = """# Legacy Project Spec

## Target Repository

- **Repo:** `example/legacy-repo`
- **Branch:** `main`

## Section 2 -- Goals

FR-1: The legacy system shall do A.
FR-2: The legacy system shall do B.

## Section 6 -- Acceptance Criteria

- AC-1: Legacy feature A works (Section 2).
- AC-2: Legacy feature B works (Section 2).
"""

SINGLE_UNIT_SPEC = f"""# Single Unit Spec

## Target Repository

- **Repo:** `example/repo`
- **Branch:** `main`

## Functional Requirements

FR-1: Do the thing.

{AC_MARKER}

- AC-1: The thing is done (FR-1).
"""

MISSING_FR_SPEC = f"""# No FR Spec

## Target Repository

- **Repo:** `example/repo`
- **Branch:** `main`

{AC_MARKER}

- AC-1: Something (Section 2).
"""

MISSING_REPO_SPEC = f"""# No Repo Spec

## Functional Requirements

FR-1: Do something.

{AC_MARKER}

- AC-1: Something done (FR-1).
"""

MISSING_AC_SPEC = """# No AC Spec

## Target Repository

- **Repo:** `example/repo`
- **Branch:** `main`

## Functional Requirements

FR-1: Do something.
"""

MULTI_UNIT_MISSING_INVENTORY_SPEC = f"""# Multi Unit No Inventory

## Target Repository

- **Repo:** `example/repo`
- **Branch:** `main`

## Functional Requirements

FR-1: Do A.
FR-2: Do B.

{AC_MARKER}

- AC-1: A done (FR-1).
- AC-2: B done (FR-2).
"""


@pytest.mark.unit
class TestAcMarker:
    """AC_MARKER is a stable, non-empty string usable as a section delimiter."""

    def test_ac_marker_is_nonempty_string(self) -> None:
        assert isinstance(AC_MARKER, str)
        assert len(AC_MARKER) > 0

    def test_ac_marker_does_not_contain_backtick(self) -> None:
        """Marker must be safe to embed in Markdown code fences."""
        assert "`" not in AC_MARKER

    def test_ac_marker_is_deterministic(self) -> None:
        """Importing twice yields the identical object."""
        import devbench.plugin_helpers.spec_backlog_contract as _sbc

        assert AC_MARKER is _sbc.AC_MARKER


@pytest.mark.unit
class TestExtractFrList:
    """extract_fr_list returns all FR-N lines found in the spec text."""

    def test_extracts_multiple_frs_from_marker_spec(self) -> None:
        frs = extract_fr_list(MARKER_SPEC)
        assert len(frs) == 3
        assert any("FR-1" in fr for fr in frs)
        assert any("FR-2" in fr for fr in frs)
        assert any("FR-3" in fr for fr in frs)

    def test_extracts_frs_from_legacy_spec(self) -> None:
        frs = extract_fr_list(LEGACY_SPEC)
        assert len(frs) == 2
        assert any("FR-1" in fr for fr in frs)

    def test_returns_empty_list_when_no_frs(self) -> None:
        frs = extract_fr_list(MISSING_FR_SPEC)
        assert frs == []

    def test_each_entry_contains_fr_identifier(self) -> None:
        frs = extract_fr_list(MARKER_SPEC)
        for fr in frs:
            assert "FR-" in fr

    def test_preserves_fr_text_content(self) -> None:
        frs = extract_fr_list(MARKER_SPEC)
        combined = " ".join(frs)
        assert "system shall do X" in combined or "do X" in combined


@pytest.mark.unit
class TestExtractAcSection:
    """extract_ac_section returns the AC-N text block from a spec."""

    def test_returns_ac_content_from_marker_spec(self) -> None:
        text = extract_ac_section(MARKER_SPEC)
        assert "AC-1" in text
        assert "AC-2" in text

    def test_marker_spec_does_not_include_later_section_content(self) -> None:
        """Content after the next '## ' heading is not included."""
        text = extract_ac_section(MARKER_SPEC)
        assert "Section 6" not in text

    def test_legacy_fallback_returns_section_6_content(self) -> None:
        """When no AC_MARKER is present, fall back to the positional Section 6."""
        text = extract_ac_section(LEGACY_SPEC)
        assert "AC-1" in text
        assert "Legacy feature A" in text

    def test_single_unit_spec_with_marker(self) -> None:
        text = extract_ac_section(SINGLE_UNIT_SPEC)
        assert "AC-1" in text

    def test_raises_when_neither_marker_nor_section_6(self) -> None:
        """Specs with no AC_MARKER and no Section 6 must raise ReadinessError."""
        bare_spec = "# Bare\n\n## Section 2\n\nsome content\n"
        with pytest.raises(ReadinessError, match="AC"):
            extract_ac_section(bare_spec)


@pytest.mark.unit
class TestCheckBacklogReadiness:
    """check_backlog_readiness raises ReadinessError naming any missing element."""

    def test_passes_for_complete_marker_spec_single_unit(self) -> None:
        """A fully-formed single-unit spec passes with no exception."""
        check_backlog_readiness(SINGLE_UNIT_SPEC, is_multi_unit=False)

    def test_passes_for_complete_marker_spec_multi_unit(self) -> None:
        """A multi-unit spec with an inventory section passes."""
        check_backlog_readiness(MARKER_SPEC, is_multi_unit=True)

    def test_passes_for_legacy_spec_single_unit(self) -> None:
        """Legacy specs (positional Section 6) pass when repo + branch + FR present."""
        check_backlog_readiness(LEGACY_SPEC, is_multi_unit=False)

    def test_fails_when_fr_list_missing(self) -> None:
        """ReadinessError names 'FR' when no FR-N lines are found."""
        with pytest.raises(ReadinessError, match="FR"):
            check_backlog_readiness(MISSING_FR_SPEC, is_multi_unit=False)

    def test_fails_when_ac_section_missing(self) -> None:
        """ReadinessError names 'AC' when neither marker nor Section 6 is found."""
        with pytest.raises(ReadinessError, match="AC"):
            check_backlog_readiness(MISSING_AC_SPEC, is_multi_unit=False)

    def test_fails_when_repo_missing(self) -> None:
        """ReadinessError names 'repo' when the Target Repository block is absent."""
        with pytest.raises(ReadinessError, match=r"(?i)repo"):
            check_backlog_readiness(MISSING_REPO_SPEC, is_multi_unit=False)

    def test_fails_when_multi_unit_but_no_inventory(self) -> None:
        """ReadinessError names 'inventory' for multi-unit specs without Unit Inventory."""
        with pytest.raises(ReadinessError, match=r"(?i)inventory"):
            check_backlog_readiness(MULTI_UNIT_MISSING_INVENTORY_SPEC, is_multi_unit=True)

    def test_single_unit_does_not_require_inventory(self) -> None:
        """A single-unit spec without a Unit Inventory section must NOT fail."""
        check_backlog_readiness(SINGLE_UNIT_SPEC, is_multi_unit=False)

    @pytest.mark.parametrize(
        "spec_text,is_multi_unit,pattern",
        [
            (MISSING_FR_SPEC, False, "FR"),
            (MISSING_AC_SPEC, False, "AC"),
            (MISSING_REPO_SPEC, False, r"(?i)repo"),
            (MULTI_UNIT_MISSING_INVENTORY_SPEC, True, r"(?i)inventory"),
        ],
    )
    def test_parametrized_missing_elements(
        self,
        spec_text: str,
        is_multi_unit: bool,
        pattern: str,
    ) -> None:
        """Each missing element produces a ReadinessError naming that element."""
        with pytest.raises(ReadinessError, match=pattern):
            check_backlog_readiness(spec_text, is_multi_unit=is_multi_unit)

    def test_error_message_names_missing_element_verbatim(self) -> None:
        """The error message produced by check_backlog_readiness is actionable."""
        with pytest.raises(ReadinessError) as exc_info:
            check_backlog_readiness(MISSING_FR_SPEC, is_multi_unit=False)
        assert "FR" in str(exc_info.value)

    def test_readiness_error_is_subclass_of_value_error(self) -> None:
        """ReadinessError inherits from ValueError for easy catching by callers."""
        assert issubclass(ReadinessError, ValueError)
