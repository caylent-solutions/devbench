"""Tests for ``devbench.plugin_helpers.name_coverage``.

E12-F3-S1-T1: Deterministic name-coverage pre-pass that greps every
named work-item/module/unit/workflow/app/config element enumerated by
a spec against task manifests to seed the coverage audit.

Spec Section 4 E12-F3-S1 AC-1, AC-2, AC-3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.plugin_helpers.name_coverage import (
    ELEMENT_CATEGORIES,
    CoverageResult,
    GapReport,
    enumerate_spec_elements,
    run_name_coverage_pre_pass,
    verify_gap,
)

# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------

_SPEC_WITH_ELEMENTS = """\
# My Project Spec

## Section 2 -- Goals

FR-1: The system shall implement the `UserAuthWorkflow` workflow.
FR-2: The system shall provide the `TokenService` module.
FR-3: The system shall expose the `AuthController` unit.
FR-4: The system shall ship the `my-app` app.
FR-5: Configuration is managed via the `auth.yaml` config.
FR-6: The `PaymentWorkItem` work-item must be tracked.

## Section 6 -- Acceptance Criteria

- AC-1: UserAuthWorkflow processes tokens (FR-1).
"""

_MANIFEST_COVERING_ALL = """\
# E1-F1-S1-T1: Implement UserAuthWorkflow

## Status: in-queue

## Changes Manifest

| File | Change |
|------|--------|
| `src/workflows/user_auth_workflow.py` | add |
| `src/services/token_service.py` | add |
| `src/controllers/auth_controller.py` | add |
| `apps/my_app/main.py` | add |
| `config/auth.yaml` | add |
| `docs/payment_work_item.md` | add |
"""

_MANIFEST_PARTIAL = """\
# E1-F1-S1-T1: Implement TokenService

## Status: in-queue

## Changes Manifest

| File | Change |
|------|--------|
| `src/services/token_service.py` | add |
"""

_MANIFEST_EMPTY = """\
# E1-F1-S1-T2: Stub task

## Status: in-queue

## Changes Manifest

| File | Change |
|------|--------|
"""

# ---------------------------------------------------------------------------
# ELEMENT_CATEGORIES -- the six named categories must be present
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestElementCategories:
    """ELEMENT_CATEGORIES must enumerate all six spec-defined element types."""

    def test_has_six_categories(self) -> None:
        assert len(ELEMENT_CATEGORIES) == 6

    @pytest.mark.parametrize(
        "category",
        ["work-item", "module", "unit", "workflow", "app", "config"],
    )
    def test_all_required_categories_present(self, category: str) -> None:
        assert category in ELEMENT_CATEGORIES

    def test_each_category_has_a_regex(self) -> None:
        import re

        for cat, pattern in ELEMENT_CATEGORIES.items():
            assert isinstance(pattern, re.Pattern), f"Category '{cat}' must map to a compiled re.Pattern"


# ---------------------------------------------------------------------------
# enumerate_spec_elements -- extracts named items across all categories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnumerateSpecElements:
    """enumerate_spec_elements returns named elements grouped by category."""

    def test_detects_workflow_element(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        names = {e.name for e in elements}
        assert "UserAuthWorkflow" in names

    def test_detects_module_element(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        names = {e.name for e in elements}
        assert "TokenService" in names

    def test_detects_unit_element(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        names = {e.name for e in elements}
        assert "AuthController" in names

    def test_detects_app_element(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        names = {e.name for e in elements}
        assert "my-app" in names

    def test_detects_config_element(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        names = {e.name for e in elements}
        assert "auth.yaml" in names

    def test_detects_work_item_element(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        names = {e.name for e in elements}
        assert "PaymentWorkItem" in names

    def test_elements_carry_category(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        categories_found = {e.category for e in elements}
        assert len(categories_found) > 1

    def test_each_element_has_nonempty_name(self) -> None:
        elements = enumerate_spec_elements(_SPEC_WITH_ELEMENTS)
        for elem in elements:
            assert elem.name.strip() != "", f"Empty name in element: {elem}"

    def test_empty_spec_returns_no_elements(self) -> None:
        elements = enumerate_spec_elements("# Empty spec\n\nNo elements here.\n")
        assert elements == []

    def test_deduplicates_repeated_names_in_same_category(self) -> None:
        spec = "FR-1: Use `MyModule` module.\nFR-2: Also use `MyModule` module.\n"
        elements = enumerate_spec_elements(spec)
        names = [e.name for e in elements]
        assert names.count("MyModule") == 1

    def test_skips_match_when_all_groups_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If a regex match has all-None groups, the element is skipped."""
        import re

        import devbench.plugin_helpers.name_coverage as nc

        # A pattern with one optional group that matches but captures nothing.
        original = dict(nc.ELEMENT_CATEGORIES)
        nc.ELEMENT_CATEGORIES.clear()
        nc.ELEMENT_CATEGORIES["module"] = re.compile(r"TRIGGER(?:( ))?NONE")
        try:
            # "TRIGGERNONE" matches but the optional group is None.
            elements = enumerate_spec_elements("TRIGGERNONE")
            assert elements == []
        finally:
            nc.ELEMENT_CATEGORIES.clear()
            nc.ELEMENT_CATEGORIES.update(original)

    def test_skips_empty_name_after_strip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the captured group is whitespace-only, the element is skipped."""
        import re

        import devbench.plugin_helpers.name_coverage as nc

        original = dict(nc.ELEMENT_CATEGORIES)
        nc.ELEMENT_CATEGORIES.clear()
        # Pattern that captures one or more spaces.
        nc.ELEMENT_CATEGORIES["module"] = re.compile(r"TRIGGER( +)EMPTY")
        try:
            elements = enumerate_spec_elements("TRIGGER   EMPTY")
            assert elements == []
        finally:
            nc.ELEMENT_CATEGORIES.clear()
            nc.ELEMENT_CATEGORIES.update(original)


# ---------------------------------------------------------------------------
# run_name_coverage_pre_pass -- core function: spec elements vs manifests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunNameCoveragePrePass:
    """run_name_coverage_pre_pass reports covered and uncovered elements."""

    def test_reports_element_as_uncovered_when_no_manifest_matches(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_EMPTY)

        results = run_name_coverage_pre_pass(spec_text=_SPEC_WITH_ELEMENTS, manifest_dir=manifest_dir)
        uncovered = [r for r in results if not r.is_covered]
        assert len(uncovered) > 0, "Expected at least one uncovered element"

    def test_reports_element_as_covered_when_manifest_contains_name(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_COVERING_ALL)

        results = run_name_coverage_pre_pass(spec_text=_SPEC_WITH_ELEMENTS, manifest_dir=manifest_dir)
        covered = [r for r in results if r.is_covered]
        assert len(covered) > 0, "Expected at least one covered element"

    def test_uncovered_element_has_covering_task_id_none(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_EMPTY)

        results = run_name_coverage_pre_pass(
            spec_text="FR-1: Use the `GhostModule` module.\n",
            manifest_dir=manifest_dir,
        )
        assert len(results) == 1
        assert results[0].covering_task_id is None
        assert not results[0].is_covered

    def test_covered_element_has_covering_task_id_set(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_PARTIAL)

        results = run_name_coverage_pre_pass(
            spec_text="FR-2: The `TokenService` module handles auth.\n",
            manifest_dir=manifest_dir,
        )
        assert len(results) == 1
        assert results[0].covering_task_id is not None
        assert results[0].is_covered

    def test_multi_category_enumeration(self, tmp_path: Path) -> None:
        """Pre-pass enumerates elements across more than one category."""
        spec = (
            "FR-1: Use `AnalyticsWorkflow` workflow.\n"
            "FR-2: The `DataStore` module persists records.\n"
            "FR-3: Configure via `store.yaml` config.\n"
        )
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_EMPTY)

        results = run_name_coverage_pre_pass(spec_text=spec, manifest_dir=manifest_dir)
        categories_in_results = {r.element.category for r in results}
        assert len(categories_in_results) >= 2, "Pre-pass must enumerate elements from multiple categories"

    def test_empty_spec_returns_empty_results(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        results = run_name_coverage_pre_pass(spec_text="# No elements\n", manifest_dir=manifest_dir)
        assert results == []

    def test_raises_when_manifest_dir_does_not_exist(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError, match="manifest_dir"):
            run_name_coverage_pre_pass(
                spec_text="FR-1: Use `Foo` module.\n",
                manifest_dir=missing_dir,
            )

    def test_raises_os_error_on_unreadable_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during manifest read is re-raised with context."""
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text("content")

        def _always_raise(self: Path, encoding: str = "utf-8") -> str:
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "read_text", _always_raise)
        with pytest.raises(OSError, match="Permission denied"):
            run_name_coverage_pre_pass(
                spec_text="FR-1: Use `Foo` module.\n",
                manifest_dir=manifest_dir,
            )


# ---------------------------------------------------------------------------
# CoverageResult -- data class shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoverageResult:
    """CoverageResult carries element, covering_task_id, and is_covered."""

    def test_covered_result_is_covered(self) -> None:
        from devbench.plugin_helpers.name_coverage import SpecElement

        elem = SpecElement(name="MyModule", category="module")
        result = CoverageResult(element=elem, covering_task_id="E1-F1-S1-T1", is_covered=True)
        assert result.is_covered
        assert result.covering_task_id == "E1-F1-S1-T1"

    def test_uncovered_result_has_none_task_id(self) -> None:
        from devbench.plugin_helpers.name_coverage import SpecElement

        elem = SpecElement(name="GhostUnit", category="unit")
        result = CoverageResult(element=elem, covering_task_id=None, is_covered=False)
        assert not result.is_covered
        assert result.covering_task_id is None


# ---------------------------------------------------------------------------
# GapReport -- structured gap shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGapReport:
    """GapReport carries severity, spec_requirement_quote, covering_task_id,
    what_is_missing, and fix fields."""

    def test_gap_report_new_task_fix(self) -> None:
        report = GapReport(
            severity="high",
            spec_requirement_quote="FR-1: Use `GhostModule` module.",
            covering_task_id=None,
            what_is_missing="No task manifest references GhostModule",
            fix="NEW TASK",
        )
        assert report.severity == "high"
        assert report.covering_task_id is None
        assert report.fix == "NEW TASK"
        assert "GhostModule" in report.spec_requirement_quote

    def test_gap_report_enhance_fix(self) -> None:
        report = GapReport(
            severity="medium",
            spec_requirement_quote="FR-2: Implement `TokenService` module.",
            covering_task_id="E1-F1-S1-T1",
            what_is_missing="Task mentions the module but does not test it",
            fix="ENHANCE E1-F1-S1-T1",
        )
        assert report.fix == "ENHANCE E1-F1-S1-T1"
        assert report.covering_task_id == "E1-F1-S1-T1"

    @pytest.mark.parametrize(
        "severity",
        ["high", "medium", "low"],
    )
    def test_gap_report_accepts_standard_severities(self, severity: str) -> None:
        report = GapReport(
            severity=severity,
            spec_requirement_quote="FR-1: something.",
            covering_task_id=None,
            what_is_missing="missing",
            fix="NEW TASK",
        )
        assert report.severity == severity


# ---------------------------------------------------------------------------
# verify_gap -- per-gap independent verification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyGap:
    """verify_gap confirms or rejects a gap to eliminate false positives."""

    def test_confirms_genuine_gap_when_name_truly_absent(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_EMPTY)

        from devbench.plugin_helpers.name_coverage import SpecElement

        elem = SpecElement(name="GhostModule", category="module")
        gap = GapReport(
            severity="high",
            spec_requirement_quote="FR-1: Use `GhostModule` module.",
            covering_task_id=None,
            what_is_missing="No manifest references GhostModule",
            fix="NEW TASK",
        )
        is_genuine = verify_gap(gap=gap, element=elem, manifest_dir=manifest_dir)
        assert is_genuine is True

    def test_rejects_false_positive_when_name_found_on_reverification(self, tmp_path: Path) -> None:
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text(_MANIFEST_PARTIAL)

        from devbench.plugin_helpers.name_coverage import SpecElement

        elem = SpecElement(name="TokenService", category="module")
        gap = GapReport(
            severity="high",
            spec_requirement_quote="FR-2: Use `TokenService` module.",
            covering_task_id=None,
            what_is_missing="No manifest references TokenService",
            fix="NEW TASK",
        )
        is_genuine = verify_gap(gap=gap, element=elem, manifest_dir=manifest_dir)
        # The gap was declared uncovered, but TokenService IS in the manifest.
        # Independent verification must reject this as a false positive.
        assert is_genuine is False

    def test_raises_when_manifest_dir_does_not_exist(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "nonexistent"
        from devbench.plugin_helpers.name_coverage import SpecElement

        elem = SpecElement(name="X", category="module")
        gap = GapReport(
            severity="high",
            spec_requirement_quote="FR-1: Use `X`.",
            covering_task_id=None,
            what_is_missing="missing",
            fix="NEW TASK",
        )
        with pytest.raises(FileNotFoundError, match="manifest_dir"):
            verify_gap(gap=gap, element=elem, manifest_dir=missing_dir)

    def test_raises_os_error_on_unreadable_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during manifest read in verify_gap is re-raised with context."""
        manifest_dir = tmp_path / "backlog"
        manifest_dir.mkdir()
        (manifest_dir / "E1-F1-S1-T1.md").write_text("content")

        def _always_raise(self: Path, encoding: str = "utf-8") -> str:
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "read_text", _always_raise)

        from devbench.plugin_helpers.name_coverage import SpecElement

        elem = SpecElement(name="TokenService", category="module")
        gap = GapReport(
            severity="high",
            spec_requirement_quote="FR-1: Use `TokenService`.",
            covering_task_id=None,
            what_is_missing="missing",
            fix="NEW TASK",
        )
        with pytest.raises(OSError, match="Permission denied"):
            verify_gap(gap=gap, element=elem, manifest_dir=manifest_dir)
