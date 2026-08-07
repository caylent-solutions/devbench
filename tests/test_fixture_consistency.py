"""Tests for src/devbench/fixture_consistency.py.

Covers:
- collect_identifiers: JSON/YAML loading, recursive identifier-value
  collection across list-of-records, dict-of-records, and nested-envelope
  fixture shapes.
- check_fixture_consistency: opt-out no-op when no canonical_sources are
  configured; a fixture whose keys are all present in the canonical
  source passes; a fixture referencing a key absent from the canonical
  source is flagged (missing_key); an explicitly allow_missing-scoped
  edge-case fixture does not false-positive (issue #08 AC3); a canonical
  source's coverage shortfall relative to expected_count is flagged
  (backfill-coverage / AC4); missing/unparseable files are flagged as
  load_error rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbench.config_loader import (
    FixtureCanonicalSource,
    FixtureConsistencyConfig,
    FixtureScanTarget,
)
from devbench.fixture_consistency import (
    FixtureFinding,
    check_fixture_consistency,
    collect_identifiers,
)


@pytest.mark.unit
class TestCollectIdentifiers:
    def test_collects_from_list_of_records_json(self, tmp_path: Path) -> None:
        """A flat JSON list of {field: value} records collects every value."""
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"sku": "A1"}, {"sku": "A2"}, {"sku": "A3"}]), encoding="utf-8")

        assert collect_identifiers(path, "sku") == {"A1", "A2", "A3"}

    def test_collects_from_nested_envelope_json(self, tmp_path: Path) -> None:
        """A nested {"data": {"items": [...]}} envelope is descended into."""
        path = tmp_path / "catalog.json"
        path.write_text(
            json.dumps({"data": {"items": [{"sku": "A1"}, {"sku": "A2"}]}}),
            encoding="utf-8",
        )

        assert collect_identifiers(path, "sku") == {"A1", "A2"}

    def test_collects_from_dict_keyed_by_id(self, tmp_path: Path) -> None:
        """A dict keyed by an arbitrary id, whose values are records, still contributes."""
        path = tmp_path / "catalog.json"
        path.write_text(
            json.dumps({"row-1": {"sku": "A1"}, "row-2": {"sku": "A2"}}),
            encoding="utf-8",
        )

        assert collect_identifiers(path, "sku") == {"A1", "A2"}

    def test_collects_from_yaml(self, tmp_path: Path) -> None:
        """A .yaml fixture parses as YAML instead of JSON."""
        path = tmp_path / "catalog.yaml"
        path.write_text(
            "records:\n  - sku: A1\n  - sku: A2\n",
            encoding="utf-8",
        )

        assert collect_identifiers(path, "sku") == {"A1", "A2"}

    def test_numeric_identifier_values_are_stringified(self, tmp_path: Path) -> None:
        """Integer/float identifier values collect as their string form."""
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"product_id": 101}, {"product_id": 202}]), encoding="utf-8")

        assert collect_identifiers(path, "product_id") == {"101", "202"}

    def test_boolean_field_values_are_excluded(self, tmp_path: Path) -> None:
        """A field whose value is a bool (not a real identifier literal) is not collected."""
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"active": True}, {"active": False}]), encoding="utf-8")

        assert collect_identifiers(path, "active") == set()

    def test_missing_field_yields_empty_set(self, tmp_path: Path) -> None:
        """Records that never contain the target field yield an empty set, not an error."""
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps([{"name": "widget"}]), encoding="utf-8")

        assert collect_identifiers(path, "sku") == set()


@pytest.mark.unit
class TestCheckFixtureConsistency:
    def _write_json(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_no_canonical_sources_is_a_no_op(self, tmp_path: Path) -> None:
        """An empty (unconfigured) FixtureConsistencyConfig always passes -- opt-in, not default-on."""
        findings = check_fixture_consistency(tmp_path, FixtureConsistencyConfig())
        assert findings == []

    def test_fixture_with_matching_key_passes(self, tmp_path: Path) -> None:
        """A scan target whose identifier values are all present in the canonical source passes."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        assert check_fixture_consistency(tmp_path, config) == []

    def test_fixture_with_key_absent_from_canonical_is_flagged(self, tmp_path: Path) -> None:
        """A scan-target key absent from the canonical source produces a missing_key finding."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}, {"sku": "GHOST-SKU"}])

        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "missing_key"
        assert "GHOST-SKU" in findings[0].message
        assert "mock_lookup.json" in findings[0].message
        assert "catalog.json" in findings[0].message

    def test_allow_missing_scoped_edge_case_fixture_does_not_false_positive(self, tmp_path: Path) -> None:
        """A fixture intentionally modeling a not-found/empty edge case is not flagged (issue #08 AC3)."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(tmp_path / "mock_not_found.json", [{"sku": "SKU-DOES-NOT-EXIST"}])

        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(
                FixtureScanTarget(
                    path="mock_not_found.json",
                    identifier_field="sku",
                    allow_missing=frozenset({"SKU-DOES-NOT-EXIST"}),
                ),
            ),
        )

        assert check_fixture_consistency(tmp_path, config) == []

    def test_allow_missing_does_not_suppress_other_missing_keys(self, tmp_path: Path) -> None:
        """allow_missing only scopes the exact listed values, not every missing key in the fixture."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(
            tmp_path / "mock_lookup.json",
            [{"sku": "SKU-DOES-NOT-EXIST"}, {"sku": "UNRELATED-GHOST"}],
        )

        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(
                FixtureScanTarget(
                    path="mock_lookup.json",
                    identifier_field="sku",
                    allow_missing=frozenset({"SKU-DOES-NOT-EXIST"}),
                ),
            ),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "missing_key"
        assert "UNRELATED-GHOST" in findings[0].message
        assert "SKU-DOES-NOT-EXIST" not in findings[0].message

    def test_expected_count_shortfall_is_flagged(self, tmp_path: Path) -> None:
        """A canonical source covering fewer records than expected_count is flagged (backfill coverage, AC4)."""
        self._write_json(tmp_path / "catalog.json", [{"sku": f"A{i}"} for i in range(5)])

        config = FixtureConsistencyConfig(
            canonical_sources=(
                FixtureCanonicalSource(path="catalog.json", identifier_field="sku", expected_count=24),
            ),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "coverage_shortfall"
        assert "5" in findings[0].message
        assert "24" in findings[0].message

    def test_expected_count_met_produces_no_finding(self, tmp_path: Path) -> None:
        """A canonical source whose distinct-identifier count matches expected_count passes."""
        self._write_json(tmp_path / "catalog.json", [{"sku": f"A{i}"} for i in range(24)])

        config = FixtureConsistencyConfig(
            canonical_sources=(
                FixtureCanonicalSource(path="catalog.json", identifier_field="sku", expected_count=24),
            ),
        )

        assert check_fixture_consistency(tmp_path, config) == []

    def test_missing_canonical_file_is_a_load_error(self, tmp_path: Path) -> None:
        """A configured canonical file that does not exist on disk is a load_error, not an exception."""
        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="does_not_exist.json", identifier_field="sku"),),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "does_not_exist.json" in findings[0].message

    def test_missing_scan_file_is_a_load_error(self, tmp_path: Path) -> None:
        """A configured scan file that does not exist on disk is a load_error, not an exception."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])

        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(FixtureScanTarget(path="does_not_exist.json", identifier_field="sku"),),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "does_not_exist.json" in findings[0].message

    def test_unparseable_canonical_file_is_a_load_error(self, tmp_path: Path) -> None:
        """Malformed JSON in a canonical source is reported as a finding, not raised."""
        (tmp_path / "catalog.json").write_text("{not valid json", encoding="utf-8")

        config = FixtureConsistencyConfig(
            canonical_sources=(FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"

    def test_multiple_canonical_sources_scan_resolves_by_explicit_canonical_source(self, tmp_path: Path) -> None:
        """With >1 canonical source, a scan target's explicit canonical_source picks the right one."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = FixtureConsistencyConfig(
            canonical_sources=(
                FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),
                FixtureCanonicalSource(path="vendors.json", identifier_field="vendor_id"),
            ),
            scan=(
                FixtureScanTarget(
                    path="mock_lookup.json",
                    identifier_field="sku",
                    canonical_source="catalog.json",
                ),
            ),
        )

        assert check_fixture_consistency(tmp_path, config) == []

    def test_ambiguous_scan_target_without_canonical_source_is_a_load_error(self, tmp_path: Path) -> None:
        """A hand-built config bypassing the YAML loader's validation is still handled gracefully."""
        self._write_json(tmp_path / "catalog.json", [{"sku": "A1"}])
        self._write_json(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        self._write_json(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = FixtureConsistencyConfig(
            canonical_sources=(
                FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),
                FixtureCanonicalSource(path="vendors.json", identifier_field="vendor_id"),
            ),
            scan=(FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "mock_lookup.json" in findings[0].message

    def test_fixture_finding_is_frozen_dataclass(self) -> None:
        """FixtureFinding is immutable -- callers cannot mutate a returned finding in place."""
        finding = FixtureFinding(kind="missing_key", message="test")
        with pytest.raises(AttributeError):
            finding.kind = "load_error"  # type: ignore[misc]
