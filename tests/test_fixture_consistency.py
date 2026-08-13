"""Tests for src/devbench/fixture_consistency.py.

Covers:
- collect_identifiers: JSON/YAML loading, recursive identifier-value
  collection across list-of-records, dict-of-records, and nested-envelope
  fixture shapes.
- check_fixture_consistency: opt-out no-op when no canonical_sources are
  configured; a fixture whose keys are all present in the canonical
  source passes; a fixture referencing a key absent from the canonical
  source is flagged (missing_key); an explicitly allow_missing-scoped
  edge-case fixture does not false-positive
  (caylent-solutions/devbench-internal-backlog#17 AC3); a canonical
  source's coverage shortfall relative to expected_count is flagged
  (backfill-coverage / AC4); missing/unparseable files are flagged as
  load_error rather than raising.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import ModuleType

import pytest


def _config_loader() -> ModuleType:
    """Import ``devbench.config_loader`` on demand (never at module scope).

    Deferred to each test's own body so pytest can collect this file even
    when this task's ``FixtureCanonicalSource`` / ``FixtureConsistencyConfig``
    / ``FixtureScanTarget`` dataclasses have not landed in
    ``config_loader.py`` yet: the RED gate (``devbench.tdd_gate``) proves a
    genuine pre-pick failure by stashing every production-source Changes
    Manifest row -- which includes this task's edits to
    ``config_loader.py`` -- and re-running this file. A module-scope import
    of those not-yet-existing names would turn the missing symbols into a
    collection error (pytest exit 2, "interrupted"); this deferred import
    instead raises inside the named test's own call phase, which pytest
    reports as a genuine FAILED test (pytest exit 1) -- the outcome the
    gate requires.
    """
    import devbench.config_loader as module

    return module


def _fixture_consistency() -> ModuleType:
    """Import ``devbench.fixture_consistency`` on demand.

    Same rationale as :func:`_config_loader`: this task's new production
    module is stashed by the RED gate alongside ``config_loader.py``, so
    the import is deferred into each test body rather than left at module
    scope.
    """
    import devbench.fixture_consistency as module

    return module


@pytest.mark.unit
class TestCollectIdentifiers:
    def test_collects_from_list_of_records_json(self, tmp_path: Path) -> None:
        """A flat JSON list of {field: value} records collects every value."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"sku": "A1"}, {"sku": "A2"}, {"sku": "A3"}]), encoding="utf-8")

        assert fc.collect_identifiers(path, "sku") == {"A1", "A2", "A3"}

    def test_collects_from_nested_envelope_json(self, tmp_path: Path) -> None:
        """A nested {"data": {"items": [...]}} envelope is descended into."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.json"
        path.write_text(
            json.dumps({"data": {"items": [{"sku": "A1"}, {"sku": "A2"}]}}),
            encoding="utf-8",
        )

        assert fc.collect_identifiers(path, "sku") == {"A1", "A2"}

    def test_collects_from_dict_keyed_by_id(self, tmp_path: Path) -> None:
        """A dict keyed by an arbitrary id, whose values are records, still contributes."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.json"
        path.write_text(
            json.dumps({"row-1": {"sku": "A1"}, "row-2": {"sku": "A2"}}),
            encoding="utf-8",
        )

        assert fc.collect_identifiers(path, "sku") == {"A1", "A2"}

    def test_collects_from_yaml(self, tmp_path: Path) -> None:
        """A .yaml fixture parses as YAML instead of JSON."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.yaml"
        path.write_text(
            "records:\n  - sku: A1\n  - sku: A2\n",
            encoding="utf-8",
        )

        assert fc.collect_identifiers(path, "sku") == {"A1", "A2"}

    def test_numeric_identifier_values_are_stringified(self, tmp_path: Path) -> None:
        """Integer/float identifier values collect as their string form."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"product_id": 101}, {"product_id": 202}]), encoding="utf-8")

        assert fc.collect_identifiers(path, "product_id") == {"101", "202"}

    def test_boolean_field_values_are_excluded(self, tmp_path: Path) -> None:
        """A field whose value is a bool (not a real identifier literal) is not collected."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"active": True}, {"active": False}]), encoding="utf-8")

        assert fc.collect_identifiers(path, "active") == set()

    def test_missing_field_yields_empty_set(self, tmp_path: Path) -> None:
        """Records that never contain the target field yield an empty set, not an error."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"name": "widget"}]), encoding="utf-8")

        assert fc.collect_identifiers(path, "sku") == set()


@pytest.mark.unit
class TestCheckFixtureConsistency:
    def _write_json(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_no_canonical_sources_is_a_no_op(self, tmp_path: Path) -> None:
        """An empty (unconfigured) FixtureConsistencyConfig always passes -- opt-in, not default-on."""
        cl = _config_loader()
        fc = _fixture_consistency()
        findings = fc.check_fixture_consistency(tmp_path, cl.FixtureConsistencyConfig())
        assert findings == []

    def test_fixture_with_matching_key_passes(self, tmp_path: Path) -> None:
        """A scan target whose identifier values are all present in the canonical source passes."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_fixture_with_key_absent_from_canonical_is_flagged(self, tmp_path: Path) -> None:
        """A scan-target key absent from the canonical source produces a missing_key finding."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}, {"sku": "GHOST-SKU"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "missing_key"
        assert "GHOST-SKU" in findings[0].message
        assert "mock_lookup.json" in findings[0].message
        assert "catalog.json" in findings[0].message

    def test_allow_missing_scoped_edge_case_fixture_does_not_false_positive(self, tmp_path: Path) -> None:
        """A fixture intentionally modeling a not-found/empty edge case is not flagged (AC3, internal-backlog#17)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(tmp_path / "mock_not_found.json", [{"sku": "SKU-DOES-NOT-EXIST"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(
                cl.FixtureScanTarget(
                    path="mock_not_found.json",
                    identifier_field="sku",
                    allow_missing=frozenset({"SKU-DOES-NOT-EXIST"}),
                ),
            ),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_allow_missing_does_not_suppress_other_missing_keys(self, tmp_path: Path) -> None:
        """allow_missing only scopes the exact listed values, not every missing key in the fixture."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(
            tmp_path / "mock_lookup.json",
            [{"sku": "SKU-DOES-NOT-EXIST"}, {"sku": "UNRELATED-GHOST"}],
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(
                cl.FixtureScanTarget(
                    path="mock_lookup.json",
                    identifier_field="sku",
                    allow_missing=frozenset({"SKU-DOES-NOT-EXIST"}),
                ),
            ),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "missing_key"
        assert "UNRELATED-GHOST" in findings[0].message
        assert "SKU-DOES-NOT-EXIST" not in findings[0].message

    def test_expected_count_shortfall_is_flagged(self, tmp_path: Path) -> None:
        """A canonical source covering fewer records than expected_count is flagged (backfill coverage, AC4)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": f"A{i}"} for i in range(5)])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku", expected_count=24),
            ),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "coverage_shortfall"
        assert "5" in findings[0].message
        assert "24" in findings[0].message

    def test_expected_count_met_produces_no_finding(self, tmp_path: Path) -> None:
        """A canonical source whose distinct-identifier count matches expected_count passes."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": f"A{i}"} for i in range(24)])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku", expected_count=24),
            ),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_missing_canonical_file_is_a_load_error(self, tmp_path: Path) -> None:
        """A configured canonical file that does not exist on disk is a load_error, not an exception."""
        cl = _config_loader()
        fc = _fixture_consistency()
        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="does_not_exist.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "does_not_exist.json" in findings[0].message

    def test_missing_scan_file_is_a_load_error(self, tmp_path: Path) -> None:
        """A configured scan file that does not exist on disk is a load_error, not an exception."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="does_not_exist.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "does_not_exist.json" in findings[0].message

    def test_unparseable_canonical_file_is_a_load_error(self, tmp_path: Path) -> None:
        """Malformed JSON in a canonical source is reported as a finding, not raised."""
        cl = _config_loader()
        fc = _fixture_consistency()
        (tmp_path / "catalog.json").write_text("{not valid json", encoding="utf-8")

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"

    def test_unparseable_scan_file_is_a_load_error(self, tmp_path: Path) -> None:
        """Malformed JSON in a scan target is reported as a finding, not raised."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        (tmp_path / "mock_lookup.json").write_text("{not valid json", encoding="utf-8")

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "mock_lookup.json" in findings[0].message

    def test_scan_target_of_a_failed_canonical_source_does_not_double_report(self, tmp_path: Path) -> None:
        """A scan target whose canonical source itself failed to load produces no second finding."""
        cl = _config_loader()
        fc = _fixture_consistency()
        (tmp_path / "catalog.json").write_text("{not valid json", encoding="utf-8")
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "catalog.json" in findings[0].message

    def test_multiple_canonical_sources_scan_resolves_by_explicit_canonical_source(self, tmp_path: Path) -> None:
        """With >1 canonical source, a scan target's explicit canonical_source picks the right one."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),
                cl.FixtureCanonicalSource(path="vendors.json", identifier_field="vendor_id"),
            ),
            scan=(
                cl.FixtureScanTarget(
                    path="mock_lookup.json",
                    identifier_field="sku",
                    canonical_source="catalog.json",
                ),
            ),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_ambiguous_scan_target_without_canonical_source_is_a_load_error(self, tmp_path: Path) -> None:
        """A hand-built config bypassing the YAML loader's validation is still handled gracefully."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),
                cl.FixtureCanonicalSource(path="vendors.json", identifier_field="vendor_id"),
            ),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "mock_lookup.json" in findings[0].message

    def test_fixture_finding_is_frozen_dataclass(self) -> None:
        """FixtureFinding is immutable -- mutating a field raises FrozenInstanceError."""
        fc = _fixture_consistency()
        finding = fc.FixtureFinding(kind="missing_key", message="test")
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.__setattr__("kind", "load_error")

    def test_fixture_finding_dataclasses_replace_produces_new_instance(self) -> None:
        """dataclasses.replace is the sanctioned way to derive a modified copy."""
        fc = _fixture_consistency()
        finding = fc.FixtureFinding(kind="missing_key", message="test")
        replaced = dataclasses.replace(finding, kind="load_error")

        assert replaced is not finding
        assert replaced.kind == "load_error"
        assert replaced.message == "test"
        assert finding.kind == "missing_key"
