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


def _write_fixture(path: Path, text: str) -> None:
    """Write *text* to *path*, creating parent directories as needed.

    Module-level helper shared by every test class in this file (DRY):
    previously each of ``TestCheckFixtureConsistency``,
    ``TestIdentifierFieldMatchesZeroRecords``, ``TestEmptyCanonicalIdentifierSet``,
    ``TestEmptyScanListIsLoud`` and ``TestExplicitExtensionDispatch`` declared
    its own byte-identical (or near-identical) ``_write_json``/``_write``
    method.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json_fixture(path: Path, data: object) -> None:
    """Write *data* to *path* as JSON. Built on :func:`_write_fixture` (DRY)."""
    _write_fixture(path, json.dumps(data))


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

    def test_unsupported_extension_propagates_as_value_error(self, tmp_path: Path) -> None:
        """``collect_identifiers`` is public/exported and has no try/except of its own (doc_review
        round 2): an unrecognized extension must propagate all the way out as the ``ValueError``
        the function's own ``Raises`` section promises, not be swallowed here the way the two
        internal callers (``_check_canonical_sources``/``_check_scan_targets``) swallow it into a
        ``load_error`` finding."""
        fc = _fixture_consistency()
        path = tmp_path / "catalog.txt"
        path.write_text(json.dumps([{"sku": "A1"}]), encoding="utf-8")

        with pytest.raises(ValueError):
            fc.collect_identifiers(path, "sku")


@pytest.mark.unit
class TestCheckFixtureConsistency:
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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_fixture_with_key_absent_from_canonical_is_flagged(self, tmp_path: Path) -> None:
        """A scan-target key absent from the canonical source produces a missing_key finding."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}, {"sku": "GHOST-SKU"}])

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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_not_found.json", [{"sku": "SKU-DOES-NOT-EXIST"}])

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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(
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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": f"A{i}"} for i in range(5)])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A0"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku", expected_count=24),
            ),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": f"A{i}"} for i in range(24)])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A0"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku", expected_count=24),
            ),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_missing_canonical_file_is_a_load_error(self, tmp_path: Path) -> None:
        """A configured canonical file that does not exist on disk is a load_error, not an exception."""
        cl = _config_loader()
        fc = _fixture_consistency()
        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="does_not_exist.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="unused_scan.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "does_not_exist.json" in findings[0].message

    def test_missing_scan_file_is_a_load_error(self, tmp_path: Path) -> None:
        """A configured scan file that does not exist on disk is a load_error, not an exception."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])

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
            scan=(cl.FixtureScanTarget(path="unused_scan.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"

    def test_unparseable_scan_file_is_a_load_error(self, tmp_path: Path) -> None:
        """Malformed JSON in a scan target is reported as a finding, not raised."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
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
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

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
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

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

    def test_missing_canonical_file_message_names_gates_fixture_consistency_key(self, tmp_path: Path) -> None:
        """The load_error message for a missing canonical file names the gates.fixture_consistency
        key (spec 4.1 migration: the retired top-level fixture_consistency: block moved under
        gates:), not the retired bare top-level spelling."""
        cl = _config_loader()
        fc = _fixture_consistency()
        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="does_not_exist.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="unused_scan.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert "gates.fixture_consistency.canonical_sources" in findings[0].message

    def test_missing_scan_file_message_names_gates_fixture_consistency_key(self, tmp_path: Path) -> None:
        """The load_error message for a missing scan file names gates.fixture_consistency.scan."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="does_not_exist.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert "gates.fixture_consistency.scan" in findings[0].message

    def test_ambiguous_scan_target_message_names_gates_fixture_consistency_key(self, tmp_path: Path) -> None:
        """The load_error message for an ambiguous scan target names
        gates.fixture_consistency.scan[].canonical_source."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "vendors.json", [{"vendor_id": "V1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),
                cl.FixtureCanonicalSource(path="vendors.json", identifier_field="vendor_id"),
            ),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert "gates.fixture_consistency.scan[].canonical_source" in findings[0].message

    def test_missing_key_message_names_gates_fixture_consistency_allow_missing_key(self, tmp_path: Path) -> None:
        """The missing_key remediation message names gates.fixture_consistency.scan[].allow_missing."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "GHOST-SKU"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert "gates.fixture_consistency.scan[].allow_missing" in findings[0].message

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


@pytest.mark.unit
class TestIdentifierFieldMatchesZeroRecords:
    """322-D02: a typo'd/renamed identifier_field silently self-disabled the gate; it must instead
    raise a loud error naming the field and the canonical path (spec 4.7 bullet 1, AC-19)."""

    def test_raises_loud_error_naming_field_and_path(self, tmp_path: Path) -> None:
        """A configured identifier_field absent from every canonical record raises (322-D02)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"id": "A1"}, {"id": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"id": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="idd"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="id"),),
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        expected = fc._MSG_IDENTIFIER_FIELD_ZERO_MATCH.format(field="idd", path="catalog.json")
        assert expected in str(exc_info.value)

    def test_does_not_report_scanned_references_as_mismatches(self, tmp_path: Path) -> None:
        """The typo'd-field error path pre-empts the scan loop -- no mass missing_key finding (322-D03)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"id": "A1"}, {"id": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="idd"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="id"),),
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        # The raised error is the loud config error, never a findings list
        # carrying one missing_key entry per scanned identifier value.
        expected = fc._MSG_IDENTIFIER_FIELD_ZERO_MATCH.format(field="idd", path="catalog.json")
        assert expected in str(exc_info.value)
        assert "A1" not in str(exc_info.value)
        assert "A3" not in str(exc_info.value)

    def test_message_constant_pins_the_exact_spec_wording(self) -> None:
        """Literal anchor (test_review round 2): every other assertion in this class derives its
        expected text from ``fc._MSG_IDENTIFIER_FIELD_ZERO_MATCH`` itself, so a wording mutation of
        the constant flips both sides in lockstep and can never fail. AC-E6-F1-S1-T1-1 and the
        Description both quote this sentence literally ("the exact messages the spec fixes"), and it
        ships verbatim in CHANGELOG.md and docs/cli-reference.md, so this one test pins the template
        against a hand-typed literal instead of the constant it is checking."""
        fc = _fixture_consistency()
        assert fc._MSG_IDENTIFIER_FIELD_ZERO_MATCH == "identifier field '{field}' matched zero records in {path}"


@pytest.mark.unit
class TestEmptyCanonicalIdentifierSet:
    """322-D03: an empty canonical identifier set (regardless of root cause) must take the same loud
    error path as a typo'd identifier_field, never a mass false-positive over every scanned reference."""

    @pytest.mark.parametrize(
        "canonical_content",
        [
            pytest.param([], id="empty-canonical-list"),
            pytest.param([{"name": "widget-a"}, {"name": "widget-b"}], id="records-lack-identifier-field"),
        ],
    )
    def test_empty_canonical_identifier_set_raises_loud_error(self, tmp_path: Path, canonical_content: object) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", canonical_content)
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        expected = fc._MSG_IDENTIFIER_FIELD_ZERO_MATCH.format(field="sku", path="catalog.json")
        assert expected in str(exc_info.value)


@pytest.mark.unit
class TestEmptyScanListIsLoud:
    """322-D05: an enabled gate (canonical_sources configured) with zero resolved scan targets must
    raise before reading any file, never print a passing status (spec 4.7 bullet 2)."""

    def test_raises_before_reading_any_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A PRESENT, VALID catalog.json is never read -- the check raises before any read.

        Mutation-proof: if the empty-scan guard ran AFTER the canonical-source
        loop instead of before it, this catalog.json would be opened and its
        content read (and, since its content is valid, would produce no
        load_error either, masking the ordering defect entirely with green
        tests). Spying on Path.read_text and asserting zero calls is the only
        way to distinguish "raised before reading" from "raised after reading
        a file that happened not to fail" -- an empty/missing canonical file
        does not exercise this distinction at all.
        """
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(),
        )

        read_calls: list[Path] = []
        original_read_text = Path.read_text

        def _spy_read_text(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
            read_calls.append(self)
            return original_read_text(self, encoding=encoding, errors=errors)

        monkeypatch.setattr(Path, "read_text", _spy_read_text)

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        assert fc._MSG_EMPTY_SCAN_LIST in str(exc_info.value)
        assert read_calls == []

    def test_message_constant_pins_the_exact_spec_wording(self) -> None:
        """Literal anchor (test_review round 2): every other assertion in this class derives its
        expected text from ``fc._MSG_EMPTY_SCAN_LIST`` itself, so a wording mutation of the constant
        flips both sides in lockstep and can never fail. AC-E6-F1-S1-T1-3 and the Description both
        quote this sentence literally ("the exact messages the spec fixes"), and it ships verbatim in
        CHANGELOG.md and docs/cli-reference.md, so this one test pins the constant against a
        hand-typed literal instead of the constant it is checking."""
        fc = _fixture_consistency()
        assert fc._MSG_EMPTY_SCAN_LIST == "gate enabled but scan list is empty"


@pytest.mark.unit
class TestExplicitExtensionDispatch:
    """spec 4.7 bullet 3: `.json`/`.yaml`/`.yml` scan targets parse via explicit dispatch; every
    other configured extension yields exactly one load_error finding naming the file, with no
    implicit JSON parse attempted."""

    @pytest.mark.parametrize("extension", [".json", ".yaml", ".yml"])
    def test_recognized_extensions_are_parsed(self, tmp_path: Path, extension: str) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        # Ties this parametrize list to the module's own dispatch table
        # rather than letting the two silently drift apart.
        assert extension in fc._EXTENSION_PARSERS
        _write_fixture(tmp_path / "catalog.json", json.dumps([{"sku": "A1"}]))
        scan_name = f"mock_lookup{extension}"
        if extension == ".json":
            _write_fixture(tmp_path / scan_name, json.dumps([{"sku": "A1"}]))
        else:
            _write_fixture(tmp_path / scan_name, "- sku: A1\n")

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path=scan_name, identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    @pytest.mark.parametrize("extension", [".txt", ".csv", ".md"])
    def test_unrecognized_extensions_produce_exactly_one_load_error(self, tmp_path: Path, extension: str) -> None:
        """A scan target whose content HAPPENS to be valid JSON must still be rejected: the
        dispatch is extension-driven, never an implicit fallback parse attempt (spec 4.7 bullet 3)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        assert extension not in fc._EXTENSION_PARSERS
        _write_fixture(tmp_path / "catalog.json", json.dumps([{"sku": "A1"}]))
        scan_name = f"mock_lookup{extension}"
        # Content is deliberately valid JSON: an implicit-JSON-fallback
        # implementation would silently accept this file and find no
        # issues, masking the misconfigured extension entirely.
        _write_fixture(tmp_path / scan_name, json.dumps([{"sku": "A1"}]))

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path=scan_name, identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        expected = fc._MSG_UNSUPPORTED_EXTENSION.format(
            ext=extension,
            path=tmp_path / scan_name,
            allowed=", ".join(sorted(fc._EXTENSION_PARSERS)),
            prefix="gates.fixture_consistency",
        )
        assert findings[0].message == expected

    def test_unrecognized_canonical_extension_produces_exactly_one_load_error(self, tmp_path: Path) -> None:
        """The same explicit dispatch table governs the canonical-source reader, not only scan."""
        cl = _config_loader()
        fc = _fixture_consistency()
        canonical_name = "catalog.txt"
        _write_fixture(tmp_path / canonical_name, json.dumps([{"sku": "A1"}]))
        _write_fixture(tmp_path / "mock_lookup.json", json.dumps([{"sku": "A1"}]))

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path=canonical_name, identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        expected = fc._MSG_UNSUPPORTED_EXTENSION.format(
            ext=".txt",
            path=tmp_path / canonical_name,
            allowed=", ".join(sorted(fc._EXTENSION_PARSERS)),
            prefix="gates.fixture_consistency",
        )
        assert findings[0].message == expected

    def test_uppercase_extension_is_recognized_case_insensitively(self, tmp_path: Path) -> None:
        """`.JSON` dispatches identically to `.json` -- the lookup is lowercased before dispatch.

        Kills the `path.suffix.lower() -> path.suffix` mutant: an unlowered
        suffix lookup would miss `_EXTENSION_PARSERS["json"]` for this
        uppercase extension and misreport it as unsupported.
        """
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_fixture(tmp_path / "catalog.json", json.dumps([{"sku": "A1"}]))
        _write_fixture(tmp_path / "mock_lookup.JSON", json.dumps([{"sku": "A1"}]))

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.JSON", identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_extensionless_scan_target_reports_none_rather_than_an_empty_string(self, tmp_path: Path) -> None:
        """A scan target with no extension at all names `(none)`, not an empty string, in the finding.

        Kills the `ext=suffix or "(none)"` -> `ext=suffix` mutant: an empty
        `path.suffix` would format as `''` in the message instead of the
        human-readable `(none)` placeholder.
        """
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_fixture(tmp_path / "catalog.json", json.dumps([{"sku": "A1"}]))
        _write_fixture(tmp_path / "mock_lookup", json.dumps([{"sku": "A1"}]))

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert "extension '(none)'" in findings[0].message
