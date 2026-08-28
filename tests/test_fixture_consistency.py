"""Tests for src/devbench/fixture_consistency.py.

Covers:
- collect_identifiers: JSON/YAML loading, recursive identifier-value
  collection across list-of-records, dict-of-records, and nested-envelope
  fixture shapes.
- check_fixture_consistency: opt-out no-op when no canonical_sources are
  configured; a fixture whose keys are all present in the canonical
  source passes; a fixture referencing a key absent from the canonical
  source is flagged (missing_key); a record carrying the structured
  in-fixture ``allow_missing`` marker does not false-positive
  (caylent-solutions/devbench-internal-backlog#17 AC3; spec
  integration-reality-gates-hardening.md 4.7 bullet 5, E6-F1-S1-T2); a
  canonical source's coverage shortfall relative to expected_count is
  flagged (backfill-coverage / AC4); missing/unparseable files are flagged
  as load_error rather than raising; a malformed in-fixture marker raises
  loudly rather than silently suppressing.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
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

    def test_hand_built_fixture_scan_target_rejects_allow_missing_kwarg(self, tmp_path: Path) -> None:
        """``FixtureScanTarget`` no longer accepts an ``allow_missing`` field at all -- the pre-T2
        hand-built-config affordance is fully removed, not merely disconnected from YAML. There is
        no config-allowlist read path left anywhere in this module (spec 4.7 bullet 5, E6-F1-S1-T2).
        The sole production waiver mechanism for a real workspace is the in-fixture ``allow_missing``
        marker exercised by ``TestInFixtureAllowMissingMarker`` below."""
        cl = _config_loader()

        with pytest.raises(TypeError):
            cl.FixtureScanTarget(
                path="mock_not_found.json",
                identifier_field="sku",
                allow_missing=frozenset({"SKU-DOES-NOT-EXIST"}),
            )

    def test_in_fixture_allow_missing_marker_suppresses_via_check_fixture_consistency(self, tmp_path: Path) -> None:
        """Top-level smoke test: an in-fixture ``allow_missing`` marker is honoured end-to-end
        through ``check_fixture_consistency`` (not only via the dedicated ``TestInFixtureAllowMissingMarker``
        class below), and the applied waiver is itself surfaced as a ``waiver_applied`` finding
        (spec 4.7 bullet 5 AC-19/PM-5: the suppression must be visible, never silent)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(
            tmp_path / "mock_not_found.json",
            [{"sku": "SKU-DOES-NOT-EXIST", "allow_missing": {"reason": "models an empty lookup response"}}],
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_not_found.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "waiver_applied"
        assert "SKU-DOES-NOT-EXIST" in findings[0].message
        assert "models an empty lookup response" in findings[0].message

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

    def test_missing_key_message_names_the_in_fixture_allow_missing_marker(self, tmp_path: Path) -> None:
        """The missing_key remediation message names the in-fixture ``allow_missing`` marker
        (spec 4.7 bullet 5) rather than the retired ``gates.fixture_consistency.scan[].allow_missing``
        config key -- the waiver moved into the artifact itself."""
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
        assert "gates.fixture_consistency.scan[].allow_missing" not in findings[0].message
        assert '"allow_missing": {"reason"' in findings[0].message
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


@pytest.mark.unit
class TestInFixtureAllowMissingMarker:
    """spec integration-reality-gates-hardening.md 4.7 bullet 5 (PM-5's in-diff exception,
    AC-19, E6-F1-S1-T2): the ``allow_missing`` waiver moves INTO the fixture artifact as a
    structured ``{"allow_missing": {"reason": "<non-empty reason>"}}`` marker attached to the
    waived record, replacing the retired ``gates.fixture_consistency.scan[].allow_missing``
    workspace-config allowlist. A waived record produces no ``missing_key`` finding; an unwaived
    record in the same fixture still does; and the applied waiver is itself surfaced as a
    ``waiver_applied`` finding so the suppression is visible in the check's own report, not only
    in the fixture diff."""

    def test_waived_record_suppressed_unwaived_record_still_reported(self, tmp_path: Path) -> None:
        """AC-E6-F1-S1-T2-1: the waived key is suppressed; the unwaived key still mismatches."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(
            tmp_path / "mock_lookup.json",
            [
                {"sku": "SKU-DOES-NOT-EXIST", "allow_missing": {"reason": "models an empty-state lookup response"}},
                {"sku": "UNRELATED-GHOST"},
            ],
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        kinds = {finding.kind for finding in findings}
        assert kinds == {"missing_key", "waiver_applied"}
        missing_finding = next(f for f in findings if f.kind == "missing_key")
        assert "UNRELATED-GHOST" in missing_finding.message
        assert "SKU-DOES-NOT-EXIST" not in missing_finding.message

    def test_applied_waiver_is_surfaced_in_findings(self, tmp_path: Path) -> None:
        """AC-E6-F1-S1-T2-2: every applied waiver is named in the gate's findings output, with its
        reason, so the suppression is visible in the review diff and in `report`."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(
            tmp_path / "mock_lookup.json",
            [{"sku": "SKU-DOES-NOT-EXIST", "allow_missing": {"reason": "models a cart-abandonment edge case"}}],
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        assert len(findings) == 1
        assert findings[0].kind == "waiver_applied"
        assert "SKU-DOES-NOT-EXIST" in findings[0].message
        assert "models a cart-abandonment edge case" in findings[0].message
        assert "mock_lookup.json" in findings[0].message

    def test_waiver_on_a_key_that_is_not_actually_missing_produces_no_waiver_finding(self, tmp_path: Path) -> None:
        """A marker on a record whose key IS present in the canonical source has nothing to waive
        -- no waiver_applied finding for a suppression that was never needed."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(
            tmp_path / "mock_lookup.json",
            [{"sku": "A1", "allow_missing": {"reason": "superfluous marker"}}],
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_waiver_applied_message_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (test_review round 1 W2): the other assertions in this class derive
        their expected substrings from the fixture content, not from ``fc._MSG_WAIVER_APPLIED``
        itself, so a pure reword of the constant would survive every test above. Hand-typed
        against the constant, not derived from it."""
        fc = _fixture_consistency()
        assert fc._MSG_WAIVER_APPLIED == (
            "Fixture '{path}' waives missing key '{key}' via its in-fixture allow_missing marker (reason: {reason})."
        )


@pytest.mark.unit
class TestInFixtureAllowMissingMarkerInvalid:
    """spec 4.7 bullet 5 (AC-E6-F1-S1-T2-3): a malformed in-fixture ``allow_missing`` marker
    raises rather than silently suppressing a finding, naming the fixture path and the offending
    key (the record's own identifier value)."""

    @pytest.mark.parametrize(
        "marker",
        [
            pytest.param("SKU-DOES-NOT-EXIST", id="wrong-shape-string"),
            pytest.param(["models a not-found lookup"], id="wrong-shape-list"),
            pytest.param({}, id="missing-reason"),
            pytest.param({"reason": ""}, id="empty-string-reason"),
            pytest.param({"reason": "   "}, id="whitespace-only-reason"),
            pytest.param({"reason": "x", "note": "y"}, id="extra-key"),
        ],
    )
    def test_malformed_marker_raises_naming_path_and_key(self, tmp_path: Path, marker: object) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(
            tmp_path / "mock_lookup.json",
            [{"sku": "SKU-DOES-NOT-EXIST", "allow_missing": marker}],
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        message = str(exc_info.value)
        assert str(tmp_path / "mock_lookup.json") in message
        assert "SKU-DOES-NOT-EXIST" in message

    @pytest.mark.parametrize(
        ("record", "kind"),
        [
            pytest.param(
                {"skus": "SKU-GHOST", "allow_missing": {"reason": ""}},
                "malformed",
                id="misspelled-identifier-field-and-malformed-marker",
            ),
            pytest.param(
                {"allow_missing": 12345, "items": [{"sku": "SKU-GHOST"}]},
                "malformed",
                id="envelope-level-marker-and-malformed-marker",
            ),
            pytest.param(
                {"skus": "SKU-GHOST", "allow_missing": {"reason": "models an empty lookup response"}},
                "unmatched",
                id="misspelled-identifier-field-and-well-formed-marker",
            ),
        ],
    )
    def test_marker_unmatched_to_any_identifier_raises_the_matching_error(
        self, tmp_path: Path, record: dict, kind: str
    ) -> None:
        """AC-E6-F1-S1-T2-3 (code_review round 1 Blocking 1; test_review round 2 W-a/W-b): a
        marker is validated UNCONDITIONALLY, not only on a dict that also happens to resolve the
        configured ``identifier_field``, so a record whose identifier key is misspelled or an
        envelope-level dict that merely wraps the real records can never be matched to a record --
        that must raise loudly rather than silently doing nothing. Collapses the three
        byte-near-identical round-1 tests this replaces (misspelled-field + malformed marker,
        envelope-level + malformed marker, misspelled-field + well-formed marker) into one
        parametrize.

        test_review round 2 W-b: the collapsed cases assert the FULL expected message built from
        the production message constants, not just a path/field substring both raise paths would
        satisfy, so this proves WHICH of the two raise paths actually fired. In particular the
        first case pairs a malformed marker with an unmatchable record: shape validation must run
        BEFORE the unmatched check (a probe that skips shape validation for unmatched records --
        ``reason = _validate_waiver_marker(...) if has_identifier else "unvalidated"`` -- would
        make this case raise the UNMATCHED message instead of the MALFORMED one it must raise,
        and this assertion catches that)."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [record])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        scan_path = str(tmp_path / "mock_lookup.json")
        locator = fc._MSG_NO_IDENTIFIER_VALUE_LOCATOR.format(field="sku", keys=sorted(record.keys()))
        marker = record["allow_missing"]

        if kind == "malformed":
            if isinstance(marker, dict) and set(marker) == {"reason"}:
                detail = fc._MSG_MARKER_REASON_INVALID_DETAIL.format(reason_key="reason", reason=marker["reason"])
            else:
                detail = fc._MSG_MARKER_WRONG_SHAPE_DETAIL.format(reason_key="reason", marker=marker)
            expected = fc._MSG_MALFORMED_WAIVER_MARKER.format(path=scan_path, key=locator, detail=detail)
        else:
            expected = fc._MSG_UNMATCHED_WAIVER_MARKER.format(path=scan_path, field="sku", keys=sorted(record.keys()))

        assert str(exc_info.value) == f"ERROR: {expected}"

    def test_malformed_marker_on_canonical_source_also_raises(self, tmp_path: Path) -> None:
        """The malformed-marker check runs through the same shared parse-and-walk helper used by
        both the canonical-source reader and the scan-target reader (spec 4.7 bullet 5 REFACTOR:
        one helper, not a second parse path) -- a malformed marker anywhere raises, not only in a
        scan target."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(
            tmp_path / "catalog.json",
            [{"sku": "A1", "allow_missing": {"reason": ""}}],
        )
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        message = str(exc_info.value)
        assert str(tmp_path / "catalog.json") in message
        assert "A1" in message

    def test_malformed_marker_message_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (code_review/test_review round 1 W2): every other assertion in this
        class derives its expected substrings from the marker/message content, not from
        ``fc._MSG_MALFORMED_WAIVER_MARKER`` itself, so a pure reword of the constant would
        survive every test above. Hand-typed against the constant, not derived from it."""
        fc = _fixture_consistency()
        assert fc._MSG_MALFORMED_WAIVER_MARKER == (
            "Fixture '{path}' has a malformed in-fixture allow_missing marker for key '{key}': {detail}"
        )

    def test_unmatched_marker_message_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (code_review round 1 Blocking 1) for the message raised when an
        ``allow_missing`` marker is present on a dict but the configured ``identifier_field``
        resolves no value on that same dict (misspelled/absent field, or an envelope-level
        marker) -- hand-typed against the constant, not derived from it."""
        fc = _fixture_consistency()
        assert fc._MSG_UNMATCHED_WAIVER_MARKER == (
            "Fixture '{path}' has an allow_missing marker that cannot be matched to any record: "
            "identifier field '{field}' has no value in this record (keys present: {keys}). A waiver "
            "must be attached to the same record whose '{field}' value it protects."
        )

    def test_marker_wrong_shape_detail_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (code_review round 2 warn) for the ``{detail}`` sub-template plugged
        into ``_MSG_MALFORMED_WAIVER_MARKER`` when a marker is not a mapping at all, or carries
        the wrong keys -- promoted out of an inline f-string so this module carries no inline
        literal message strings. Hand-typed against the constant, not derived from it."""
        fc = _fixture_consistency()
        assert fc._MSG_MARKER_WRONG_SHAPE_DETAIL == (
            "expected a mapping of exactly {{'{reason_key}': '<non-empty reason>'}}, got {marker!r}."
        )

    def test_marker_reason_invalid_detail_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (code_review round 2 warn) for the ``{detail}`` sub-template plugged
        into ``_MSG_MALFORMED_WAIVER_MARKER`` when a marker's ``reason`` value is not a non-empty
        string -- promoted out of an inline f-string. Hand-typed against the constant, not
        derived from it."""
        fc = _fixture_consistency()
        assert fc._MSG_MARKER_REASON_INVALID_DETAIL == "'{reason_key}' must be a non-empty string, got {reason!r}."

    def test_no_identifier_value_locator_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (code_review round 2 warn) for the locator string substituted for a
        record's own identifier value when the marker's host dict never resolves the configured
        ``identifier_field`` -- used both as the offending key named by a malformed-marker raise
        and internally to compute the ``waivers`` dict key for a well-formed-but-unmatched marker
        before the unmatched raise fires -- promoted out of an inline f-string. Hand-typed against
        the constant, not derived from it."""
        fc = _fixture_consistency()
        assert fc._MSG_NO_IDENTIFIER_VALUE_LOCATOR == "<no '{field}' value on this record; keys present: {keys}>"


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


@pytest.mark.unit
class TestSourceLiteralExtractionFindsSeededLiteral:
    """spec `integration-reality-gates-hardening.md` 4.7 bullet 4 (source-literal extraction
    mode); AC-E6-F2-S1-T1-1/AC-19: with ``extract_source_literals: true``, a seeded identifier
    literal absent from the canonical catalog produces exactly one finding carrying the file
    path and the 1-based line number."""

    def test_seeded_literal_absent_from_canonical_is_flagged_with_file_and_line(self, tmp_path: Path) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        source_path = tmp_path / "app" / "routes.py"
        _write_fixture(
            source_path,
            'ROUTE_TABLE = {\n    "name": "orders",\n    "sku": "SKU-DOES-NOT-EXIST",\n}\n',
        )

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        findings = fc.check_fixture_consistency(tmp_path, config)
        source_literal_findings = [finding for finding in findings if "app/routes.py:3" in finding.message]
        assert len(source_literal_findings) == 1
        finding = source_literal_findings[0]
        assert finding.kind == "missing_key"
        assert "catalog.json" in finding.message
        # SECURITY (Blocking 1, round-4): the extracted literal is redacted unconditionally --
        # it must never be echoed verbatim into the finding, regardless of length.
        assert "SKU-DOES-NOT-EXIST" not in finding.message


@pytest.mark.unit
class TestSourceLiteralExtractionDefaultOff:
    """AC-E6-F2-S1-T1-2: with the key absent or ``false``, the same seeded literal produces zero
    findings, proving the default-off contract."""

    def _seed(self, tmp_path: Path) -> None:
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        _write_fixture(
            tmp_path / "app" / "routes.py",
            'ROUTE_TABLE = {\n    "sku": "SKU-DOES-NOT-EXIST",\n}\n',
        )

    def test_extract_source_literals_absent_produces_no_source_literal_findings(self, tmp_path: Path) -> None:
        """``FixtureConsistencyConfig``'s own default (unset) leaves the mode off."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._seed(tmp_path)

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []

    def test_extract_source_literals_explicit_false_produces_no_source_literal_findings(self, tmp_path: Path) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        self._seed(tmp_path)

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=False,
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []


@pytest.mark.unit
class TestSourceLiteralExtractionPassesWhenLiteralMatchesCanonical:
    """A literal assigned to the identifier field that IS present in the canonical source's
    value set produces no finding -- the mode only flags a genuine mismatch."""

    def test_matching_literal_produces_no_source_literal_finding(self, tmp_path: Path) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}, {"sku": "A2"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        _write_fixture(tmp_path / "app.py", 'ROUTE_TABLE = {\n    "sku": "A2",\n}\n')

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []


@pytest.mark.unit
class TestSourceLiteralExtractionMultipleCanonicalSources:
    """E6-F2-S1-T1 round-1 code_review Blocking 2: with more than one configured canonical
    source sharing the same ``identifier_field``, a source-literal match must be resolved
    against that identifier namespace as a whole (never cross-producted against every
    canonical source independently) -- a literal genuinely valid in ONE of the sources must
    not be flagged just because it is absent from a DIFFERENT, unrelated canonical source, and
    a literal absent from all of them must produce exactly one finding, never one per source."""

    def _config(self, cl: ModuleType) -> object:
        return cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog_a.json", identifier_field="sku"),
                cl.FixtureCanonicalSource(path="catalog_b.json", identifier_field="sku"),
            ),
            scan=(
                cl.FixtureScanTarget(path="mock_a.json", identifier_field="sku", canonical_source="catalog_a.json"),
                cl.FixtureScanTarget(path="mock_b.json", identifier_field="sku", canonical_source="catalog_b.json"),
            ),
            extract_source_literals=True,
        )

    def _seed(self, tmp_path: Path) -> None:
        _write_json_fixture(tmp_path / "catalog_a.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "catalog_b.json", [{"sku": "B1"}])
        _write_json_fixture(tmp_path / "mock_a.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_b.json", [{"sku": "B1"}])

    def test_literal_valid_in_one_of_two_canonical_sources_is_not_flagged(self, tmp_path: Path) -> None:
        """W5 (round-2 test_review advisory): a bare ``findings == []`` assertion also holds
        when ``extract_source_literals`` is off, so this test alone cannot distinguish "the
        union correctly passed a cross-source-valid literal" from "the mode never ran at all".
        The positive control below (a genuinely absent "GHOST" literal seeded in the SAME file)
        proves the mode is genuinely on and scanning `app.py`: mutating ``_config``'s
        ``extract_source_literals`` to ``False`` kills this test on the control assertion
        (zero findings instead of exactly one), not just on the original B2 assertion."""
        cl = _config_loader()
        fc = _fixture_consistency()
        self._seed(tmp_path)
        # "A1" is a genuinely valid identifier -- it just lives in catalog_a.json,
        # not catalog_b.json. It must not be reported as missing. "GHOST" is the positive
        # control: absent from BOTH catalog_a.json and catalog_b.json, so it must still be
        # flagged in the same run -- proving the scan genuinely executed against this file.
        _write_fixture(
            tmp_path / "app.py", 'ROUTE_TABLE = {\n    "sku": "A1",\n}\nOTHER_TABLE = {\n    "sku": "GHOST",\n}\n'
        )

        findings = fc.check_fixture_consistency(tmp_path, self._config(cl))

        # Values are redacted unconditionally (Blocking 1, round-4), so filtering by the
        # literal text itself no longer works -- filter by the seeded assignment's own
        # `file:line` location instead.
        assert not any("app.py:2" in f.message for f in findings), (
            f"'A1' (app.py:2) is valid in catalog_a.json and must not be flagged, got: {findings}"
        )
        ghost_findings = [f for f in findings if "app.py:5" in f.message]
        assert len(ghost_findings) == 1, (
            f"expected exactly one finding for the positive-control 'GHOST' literal at app.py:5 (proving "
            f"the scan mode genuinely ran against this file), got: {findings}"
        )
        assert ghost_findings[0].kind == "missing_key"
        assert "GHOST" not in ghost_findings[0].message
        assert findings == ghost_findings, f"expected no OTHER findings, got: {findings}"

    def test_literal_absent_from_all_canonical_sources_sharing_the_field_yields_exactly_one_finding(
        self, tmp_path: Path
    ) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        self._seed(tmp_path)
        _write_fixture(tmp_path / "app.py", 'ROUTE_TABLE = {\n    "sku": "GHOST",\n}\n')

        findings = fc.check_fixture_consistency(tmp_path, self._config(cl))

        # Values are redacted unconditionally (Blocking 1, round-4) -- filter by the seeded
        # assignment's own `file:line` location rather than by the literal text.
        source_literal_findings = [f for f in findings if "app.py:2" in f.message]
        assert len(source_literal_findings) == 1, (
            f"expected exactly one finding for a single-line, single literal, got: {findings}"
        )
        assert source_literal_findings[0].kind == "missing_key"
        assert "GHOST" not in source_literal_findings[0].message

    def test_canonical_source_that_failed_to_load_is_excluded_from_the_identifier_field_group(
        self, tmp_path: Path
    ) -> None:
        """A canonical source whose own load fails (e.g. a missing file) has no identifier set to
        union against and must be excluded from its ``identifier_field`` group entirely, not
        treated as an always-missing member of the group -- it is already reported by
        ``_check_canonical_sources`` as its own ``load_error`` finding, so `catalog_b.json`'s
        genuine "B1"-only value set must never suppress a literal that is only valid in the sole
        remaining, successfully-loaded source, `catalog_a.json`."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog_a.json", [{"sku": "A1"}])
        # catalog_b.json is deliberately never written -- its load fails.
        _write_json_fixture(tmp_path / "mock_a.json", [{"sku": "A1"}])
        _write_fixture(tmp_path / "app.py", 'ROUTE_TABLE = {\n    "sku": "A1",\n}\n')

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(
                cl.FixtureCanonicalSource(path="catalog_a.json", identifier_field="sku"),
                cl.FixtureCanonicalSource(path="catalog_b.json", identifier_field="sku"),
            ),
            scan=(cl.FixtureScanTarget(path="mock_a.json", identifier_field="sku", canonical_source="catalog_a.json"),),
            extract_source_literals=True,
        )

        findings = fc.check_fixture_consistency(tmp_path, config)

        load_error_findings = [f for f in findings if f.kind == "load_error" and "catalog_b.json" in f.message]
        assert len(load_error_findings) == 1
        source_literal_findings = [f for f in findings if "app.py" in f.message]
        assert source_literal_findings == [], (
            f"'A1' is valid in the successfully-loaded catalog_a.json and must not be flagged just "
            f"because catalog_b.json failed to load, got: {source_literal_findings}"
        )


@pytest.mark.unit
class TestSourceLiteralExtractionOnlyScansClassifiedSourceExtensions:
    """AC-E6-F2-S1-T1-3 (spec 4.3 / PM-3): the candidate source list comes from
    ``source_classification.py``; a file whose extension is not a classified source extension
    (e.g. ``.md``) is never scanned, even when it carries a matching literal."""

    def test_unclassified_extension_is_never_scanned(self, tmp_path: Path) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        # .md is not a member of source_classification.SOURCE_EXTENSIONS.
        _write_fixture(tmp_path / "notes.md", 'sku: "GHOST-SKU-IN-DOCS"\n')
        # A real classified source file with no matching literal keeps the
        # classified-source-file set non-empty (avoiding the zero-files error).
        _write_fixture(tmp_path / "app.py", "# nothing to see here\n")

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        assert fc.check_fixture_consistency(tmp_path, config) == []


@pytest.mark.unit
class TestSourceLiteralExtractionZeroClassifiedSourceFiles:
    """AC-E6-F2-S1-T1-4 (spec Section 7): the mode enabled with zero classified source files in
    scope exits loudly (a ``FixtureConsistencyConfigError``) naming the resolved scope and the
    config key; no passing status line is written by a caller of this function (spec 4.7 bullet
    4 mirrors the empty-``scan``-list 322-D05 shape)."""

    def test_raises_naming_scope_and_config_key(self, tmp_path: Path) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        # Only unclassified-extension files exist under tmp_path -- zero
        # classified source files for iter_classified_source_files to find.
        _write_fixture(tmp_path / "notes.md", "sku: not-code\n")

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        with pytest.raises(fc.FixtureConsistencyConfigError) as exc_info:
            fc.check_fixture_consistency(tmp_path, config)

        message = str(exc_info.value)
        assert "gates.fixture_consistency.extract_source_literals" in message
        assert str(tmp_path) in message

    def test_raises_even_when_scan_findings_would_otherwise_be_produced(self, tmp_path: Path) -> None:
        """The zero-classified-source-files raise pre-empts the mode entirely -- it is not
        conditionally skipped just because the scan-target cross-reference already produced
        findings of its own (kills a mutant that guards the raise on "no findings yet")."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "GHOST-SKU"}])

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        with pytest.raises(fc.FixtureConsistencyConfigError):
            fc.check_fixture_consistency(tmp_path, config)

    def test_message_constant_pins_the_exact_wording(self) -> None:
        """Literal anchor (mirrors the existing message-constant pin tests in this file): every
        other assertion in this class derives its expected text from
        ``fc._MSG_ZERO_CLASSIFIED_SOURCE_FILES`` itself, so this one test pins the constant
        against a hand-typed literal instead of the constant it is checking."""
        fc = _fixture_consistency()
        assert fc._MSG_ZERO_CLASSIFIED_SOURCE_FILES == (
            "{prefix}.extract_source_literals is enabled but zero classified source files were "
            "found under resolved scope '{scope}' (devbench.source_classification."
            "iter_classified_source_files returned no candidates); the mode has nothing to scan."
        )


@pytest.mark.unit
class TestSourceLiteralExtractionUnreadableDirectory:
    """E6-F2-S1-T1 round-1 code_review Blocking 1 (AC-E6-F2-S1-T1-5 / spec Section 7):
    ``os.walk``'s DEFAULT ``onerror`` policy silently discards an ``OSError`` raised while
    listing a directory, so an unreadable subdirectory was previously skipped with no signal at
    all -- a repo whose only drifted literal lives inside that subdirectory produced ZERO
    findings and a clean pass, having genuinely inspected only part of the resolved scope. This
    must never look identical to a real, complete, clean pass."""

    def test_unreadable_directory_produces_a_finding_instead_of_a_silent_clean_pass(self, tmp_path: Path) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        # A readable classified source file with nothing to flag, so the walk
        # genuinely has SOMETHING legitimate to find before it ever reaches
        # the unreadable subtree below.
        _write_fixture(tmp_path / "ok.py", "# nothing to see here\n")
        locked_dir = tmp_path / "locked"
        locked_dir.mkdir()
        _write_fixture(locked_dir / "drifted.py", 'sku = "GHOST-SKU"\n')
        # chmod 000: os.walk cannot list this directory's contents at all,
        # so the drifted literal inside it can never actually be read --
        # the fix under test is that this must be a loud, actionable
        # failure, never a clean pass that silently skipped the subtree.
        original_mode = locked_dir.stat().st_mode
        locked_dir.chmod(0o000)
        try:
            config = cl.FixtureConsistencyConfig(
                canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
                scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
                extract_source_literals=True,
            )

            findings = fc.check_fixture_consistency(tmp_path, config)

            assert findings, "an unreadable directory in scope must produce at least one finding, not a silent pass"
            blocking = [f for f in findings if f.kind in fc.BLOCKING_FINDING_KINDS]
            assert blocking, f"expected a BLOCKING finding naming the unreadable directory, got: {findings}"
            assert any("locked" in f.message for f in blocking), (
                f"expected a blocking finding naming the unreadable 'locked' directory, got: {blocking}"
            )
            # W4 (round-2 code_review): the reported directory is repo-RELATIVE,
            # matching `_MSG_SOURCE_LOAD_FAILED`'s sibling `path` slot, never the
            # absolute tmp_path-prefixed form.
            assert any("directory 'locked'" in f.message for f in blocking), (
                f"expected the finding to name the directory as repo-relative 'locked', not an "
                f"absolute path, got: {blocking}"
            )
            assert not any(f"directory '{tmp_path}" in f.message for f in blocking), (
                f"expected the directory slot to be repo-relative, not absolute-path-prefixed, got: {blocking}"
            )
        finally:
            locked_dir.chmod(original_mode)

    def test_unreadable_directory_outside_repo_path_falls_back_to_the_raw_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """W4's repo-relative conversion (``raw_directory.relative_to(repo_path)``) cannot
        succeed for every possible ``OSError.filename`` -- ``os.walk`` always reports a
        directory under the walked root in practice, but the conversion still guards the
        ``ValueError`` a filename OUTSIDE *repo_path* would raise, falling back to the raw
        (absolute) path rather than letting an unrelated ``ValueError`` propagate uncaught.
        Directly unit-tests ``_check_source_literals``, monkeypatching
        ``iter_classified_source_files`` to raise an ``OSError`` naming a path that is not a
        descendant of *repo_path* at all -- a shape a real ``os.walk`` under this repo_path
        could never itself produce, but the code must not assume that."""
        fc = _fixture_consistency()
        outside_dir = tmp_path.parent / "definitely-outside-the-repo"

        def _raise_outside_oserror(root: Path) -> list[Path]:
            raise OSError(13, "Permission denied", str(outside_dir))

        monkeypatch.setattr(fc, "iter_classified_source_files", _raise_outside_oserror)

        findings = fc._check_source_literals(tmp_path, (), {})

        assert len(findings) == 1
        assert findings[0].kind == "load_error"
        assert str(outside_dir) in findings[0].message, (
            f"expected the raw absolute path fallback since '{outside_dir}' is not under "
            f"'{tmp_path}', got: {findings[0].message}"
        )

    def test_scope_root_itself_unreadable_names_the_scope_not_a_bare_dot(self, tmp_path: Path) -> None:
        """W-b (round-3 code_review): when the unreadable directory IS the resolved scope root
        itself, the repo-relative conversion (``raw_directory.relative_to(repo_path)``)
        collapses to ``Path('.')``, whose ``.as_posix()`` is the bare string ``'.'`` -- rendering
        the unhelpful ``failed to list directory '.'``. The message must instead name the scope
        path itself in the ``directory`` slot too, not just the earlier ``under scope '<scope>'``
        clause."""
        fc = _fixture_consistency()
        locked_root = tmp_path / "locked_root"
        locked_root.mkdir()
        original_mode = locked_root.stat().st_mode
        locked_root.chmod(0o000)
        try:
            findings = fc._check_source_literals(locked_root, (), {})

            assert len(findings) == 1
            assert findings[0].kind == "load_error"
            assert "directory '.'" not in findings[0].message, (
                f"expected the scope root's own directory slot to name the scope path, not a "
                f"bare '.', got: {findings[0].message}"
            )
            assert f"directory '{locked_root}'" in findings[0].message, (
                f"expected the directory slot to name the scope path '{locked_root}', got: {findings[0].message}"
            )
        finally:
            locked_root.chmod(original_mode)


@pytest.mark.unit
class TestSourceLiteralExtractionLoadErrors:
    """AC-E6-F2-S1-T1-5 (spec Section 7): a source file raising ``UnicodeDecodeError`` or
    ``OSError`` during literal extraction produces exactly one ``load_error`` finding naming the
    file; ``except (UnicodeDecodeError, OSError): continue`` (a truly silent skip, with no
    finding recorded at all) is forbidden -- every other classified source file must still be
    scanned."""

    @pytest.mark.parametrize(
        "exc_factory",
        [
            pytest.param(lambda: UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), id="unicode-decode"),
            pytest.param(lambda: OSError("permission denied"), id="os-error"),
        ],
    )
    def test_unreadable_file_produces_exactly_one_load_error_and_scan_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc_factory: Callable[[], Exception]
    ) -> None:
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        broken_path = tmp_path / "broken.py"
        _write_fixture(broken_path, "# this file's read will be forced to raise\n")
        other_path = tmp_path / "zzz_other.py"
        _write_fixture(other_path, 'ROUTE_TABLE = {\n    "sku": "SKU-DOES-NOT-EXIST",\n}\n')

        original_read_text = Path.read_text

        def _flaky_read_text(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
            if self == broken_path:
                raise exc_factory()
            return original_read_text(self, encoding=encoding, errors=errors)

        monkeypatch.setattr(Path, "read_text", _flaky_read_text)

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        findings = fc.check_fixture_consistency(tmp_path, config)

        load_error_findings = [finding for finding in findings if finding.kind == "load_error"]
        assert len(load_error_findings) == 1
        assert "broken.py" in load_error_findings[0].message

        # The other classified source file, alphabetically after the broken
        # one, must still have been scanned -- proving the failure on
        # broken.py did not silently abort the whole run.
        missing_key_findings = [finding for finding in findings if finding.kind == "missing_key"]
        assert len(missing_key_findings) == 1
        assert "zzz_other.py:2" in missing_key_findings[0].message


@pytest.mark.unit
class TestSourceLiteralValueIsRedactedUnconditionally:
    """SECURITY (security_review AND code_review round-4, CONVERGENT findings; CLAUDE.md
    'Sensitive Data Handling' -- 'Never log, display, or expose: ... API keys, access tokens, or
    secrets; ... authentication tokens or session identifiers'; 'Mask or redact sensitive data in
    logs'): a prior length threshold of 32 characters, plus a 4-character disclosed prefix, both
    leaked real credential shapes. A Stripe live secret key (``sk_live_`` + 24 chars = 32) and a
    32-character JSESSIONID sit EXACTLY on the old threshold and were echoed in full; an AWS
    access key ID (20 chars), a PHPSESSID (26 chars), and a short database password (17 chars)
    were all shorter than the threshold and were also echoed in full. The 4-character prefix
    separately disclosed credential TYPE and ISSUER (``ghp_``, ``AKIA``, ``AIza``, ``eyJh`` are
    all exactly 4 characters), a targeting signal with no review value ``file:line`` does not
    already provide. Both reviewers converged: no length threshold is defensible, so
    ``_redact_source_literal_value`` now redacts every extracted literal UNCONDITIONALLY,
    regardless of length or shape, and discloses nothing but the value's original length. The
    finding already carries `file:line` and the matched field name, sufficient for a reviewer to
    open the file and inspect the value directly, so no part of the value -- long or short -- is
    ever needed for the finding to remain actionable."""

    @pytest.mark.parametrize(
        "length",
        [4, 17, 20, 26, 32, 200],
        ids=[
            "4-chars",
            "17-chars-db-password",
            "20-chars-aws-key-id",
            "26-chars-phpsessid",
            "32-chars-boundary",
            "200-chars",
        ],
    )
    def test_no_part_of_the_value_appears_in_the_finding_at_any_length(self, tmp_path: Path, length: int) -> None:
        """Obviously-synthetic values, one per length class security_review measured as a full
        leak under the OLD length-threshold policy. None of these are real credentials."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        value = ("SYNTHETIC-" + "9" * length)[:length]
        assert len(value) == length
        _write_fixture(tmp_path / "app.py", f'ROUTE_TABLE = {{\n    "sku": "{value}",\n}}\n')

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        findings = fc.check_fixture_consistency(tmp_path, config)

        assert len(findings) == 1
        message = findings[0].message
        assert value not in message, (
            f"the full {length}-character literal must never be echoed verbatim, got: {message}"
        )
        for start in range(len(value)):
            for stop in range(start + 1, len(value) + 1):
                fragment = value[start:stop]
                if len(fragment) >= 4:
                    assert fragment not in message, (
                        f"a {len(fragment)}-character fragment of the {length}-character literal leaked "
                        f"into the finding message, got fragment {fragment!r} in: {message}"
                    )
        assert "app.py:2" in message
        assert str(length) in message, (
            f"expected the finding to name the redacted value's original length ({length}), got: {message}"
        )

    def test_redaction_never_contaminates_the_absent_from_canonical_comparison(self, tmp_path: Path) -> None:
        """security_review round-4: two distinct 44-character values share the SAME prefix and
        the SAME length, so their redacted forms are byte-identical. Only the one genuinely
        absent from the canonical catalog must be reported -- proving the `value in union_values`
        comparison (fixture_consistency.py) still runs against the RAW extracted value, before
        any redaction, never against the redacted (and therefore collision-prone) display form."""
        cl = _config_loader()
        fc = _fixture_consistency()
        present_value = "SYNTHETIC-SHARED-PREFIX-" + "A" * 20
        absent_value = "SYNTHETIC-SHARED-PREFIX-" + "B" * 20
        assert len(present_value) == len(absent_value) == 44
        assert present_value[:24] == absent_value[:24]
        assert fc._redact_source_literal_value(present_value) == fc._redact_source_literal_value(absent_value)
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": present_value}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": present_value}])
        app_py_text = (
            f'ROUTE_TABLE_A = {{\n    "sku": "{present_value}",\n}}\n'
            f'ROUTE_TABLE_B = {{\n    "sku": "{absent_value}",\n}}\n'
        )
        _write_fixture(tmp_path / "app.py", app_py_text)

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        findings = fc.check_fixture_consistency(tmp_path, config)

        assert len(findings) == 1, (
            f"expected exactly one finding (the absent value only), got {len(findings)}: {findings}"
        )
        assert "app.py:5" in findings[0].message

    def test_short_literal_value_is_also_redacted(self, tmp_path: Path) -> None:
        """A short, genuinely identifier-shaped literal is redacted exactly the same way a long
        one is -- proving there is no length carve-out left in the policy."""
        cl = _config_loader()
        fc = _fixture_consistency()
        _write_json_fixture(tmp_path / "catalog.json", [{"sku": "A1"}])
        _write_json_fixture(tmp_path / "mock_lookup.json", [{"sku": "A1"}])
        _write_fixture(tmp_path / "app.py", 'ROUTE_TABLE = {\n    "sku": "SHORT-GHOST-SKU",\n}\n')

        config = cl.FixtureConsistencyConfig(
            canonical_sources=(cl.FixtureCanonicalSource(path="catalog.json", identifier_field="sku"),),
            scan=(cl.FixtureScanTarget(path="mock_lookup.json", identifier_field="sku"),),
            extract_source_literals=True,
        )

        findings = fc.check_fixture_consistency(tmp_path, config)

        assert len(findings) == 1
        assert "SHORT-GHOST-SKU" not in findings[0].message
        assert "15" in findings[0].message

    def test_redaction_is_a_pure_function_of_length_never_of_shape_or_content(self) -> None:
        """An ordinary, non-credential-shaped value and a credential-shaped value of the same
        length must redact to the byte-identical marker -- proving the policy is a pure function
        of length, never a pattern match against a credential shape (e.g. a `ghp_`/`sk-`/`eyJ`
        prefix) that could fail open on a format it does not recognise."""
        fc = _fixture_consistency()
        ordinary = "a-very-long-but-perfectly-ordinary-hyphenate"
        credential_shaped = "ghp_" + "9" * 40
        assert len(ordinary) == len(credential_shaped)

        assert fc._redact_source_literal_value(ordinary) == fc._redact_source_literal_value(credential_shaped)
        assert ordinary not in fc._redact_source_literal_value(ordinary)
        assert credential_shaped not in fc._redact_source_literal_value(credential_shaped)


@pytest.mark.unit
class TestSourceLiteralMessageConstantsPinExactWording:
    """test_review round-1 advisory A2: mirrors the existing ``_MSG_EMPTY_SCAN_LIST`` hand-typed
    literal pin pattern (``TestEmptyScanListIsLoud::test_message_constant_pins_the_exact_spec_wording``)
    for the source-literal extraction mode's own two message constants. Every other assertion
    that references ``_MSG_SOURCE_LOAD_FAILED``/``_MSG_SOURCE_LITERAL_MISSING_KEY`` in this file
    derives its expected text from the constant itself, so a wording mutation of either constant
    flips both sides of those assertions in lockstep and can never fail -- these two tests pin
    each constant against a hand-typed literal instead."""

    def test_source_load_failed_pins_the_exact_wording(self) -> None:
        fc = _fixture_consistency()
        assert fc._MSG_SOURCE_LOAD_FAILED == (
            "Failed to read source file '{path}' during source-literal extraction: {exc}"
        )

    def test_source_literal_missing_key_pins_the_exact_wording(self) -> None:
        fc = _fixture_consistency()
        assert fc._MSG_SOURCE_LITERAL_MISSING_KEY == (
            "Source file '{location}' assigns '{field}' the literal value '{value}', which is absent from "
            "canonical source '{canonical_path}' ({prefix}.extract_source_literals heuristic scan mode -- "
            "spec 4.7 bullet 4). Fix the literal to reference a real canonical key, correct the canonical "
            "source if it is the one that is incomplete, or disable {prefix}.extract_source_literals if this "
            "is a false positive (see docs/devbench-yaml-reference.md for the mode's documented accuracy "
            "bounds)."
        )

    def test_source_literal_value_redacted_pins_the_exact_wording(self) -> None:
        """Blocking 1 (round-4): pins the unconditional redaction marker's exact text -- three
        documentation surfaces quote this string and must match it byte-for-byte (W1)."""
        fc = _fixture_consistency()
        assert fc._MSG_SOURCE_LITERAL_VALUE_REDACTED == (
            "<redacted, {length} chars total; see file:line above to inspect it directly>"
        )

    def test_source_scan_directory_failed_pins_the_exact_wording(self) -> None:
        fc = _fixture_consistency()
        assert fc._MSG_SOURCE_SCAN_DIRECTORY_FAILED == (
            "Could not enumerate classified source files during source-literal extraction under scope "
            "'{scope}': failed to list directory '{directory}': {exc}. The walk was aborted at this "
            "directory rather than silently skipping it and reporting a clean pass having inspected only "
            "part of the resolved scope; fix the directory's permissions (or otherwise make it readable) "
            "and re-run."
        )


@pytest.mark.unit
class TestCheckSourceLiteralsUsesSharedClassifiedSourceEnumeration:
    """test_review round-1 advisory A1 (AC-E6-F2-S1-T1-3 / spec 4.3 PM-3): a structural
    regression guard, mirroring this repo's own ``inspect.getsource`` precedent (e.g.
    ``TestCmdCheckFixtureConsistencyErrorPathEnumerationDocsSync``). Without this pin, replacing
    ``iter_classified_source_files(repo_path)`` in ``_check_source_literals`` with a
    locally-declared extension tuple plus ``repo_path.rglob("*")`` -- exactly the PM-3 violation
    AC-3 forbids -- passes every other behavioral test in this file (a hand-rolled walk that
    filters on the same extensions and yields the same shape of results produces byte-identical
    findings for every scenario those tests seed)."""

    def test_source_references_the_shared_enumeration_function_by_name(self) -> None:
        import inspect

        fc = _fixture_consistency()
        source = inspect.getsource(fc._check_source_literals)
        assert "iter_classified_source_files" in source, (
            "_check_source_literals must enumerate candidate files via "
            "source_classification.iter_classified_source_files (PM-3), not a second, "
            "locally-declared extension tuple or an unfiltered rglob('*') walk"
        )
        # Stronger than a bare substring check: requires the actual CALL
        # expression (not merely a docstring/comment mention of the name,
        # which could survive a real call-site substitution unchanged),
        # and rejects a locally-declared extension tuple / rglob("*") walk
        # appearing in the same function body.
        assert "iter_classified_source_files(repo_path)" in source
        assert "rglob(" not in source
        assert ".suffix in (" not in source and ".suffix in [" not in source


@pytest.mark.unit
class TestExtractIdentifierLiteralsGrammar:
    """Direct unit coverage of ``_extract_identifier_literals``/``_compile_source_literal_patterns``
    -- the identifier_field grammar (Definition of Ready bullet 3): ``<field>`` (optionally
    quoted) followed by ``:``/``=`` followed by a quoted string OR a bare number literal,
    matched per physical line with a 1-based line number."""

    # W7 (round-2 test_review advisory): the seven single-assert cases below were
    # previously seven structurally identical methods (each a bare
    # ``assert fc._extract_identifier_literals(lines, field) == expected``); collapsed
    # into one parametrized test. Every original case's kill is preserved -- mutating any
    # one shape (dict-style, assignment-style, JS-object-style, bare-number, an unrelated
    # field name, multiple matches on one line, or the genuinely-empty-string bound) still
    # fails only its own parametrized case, re-verified by mutation.
    @pytest.mark.parametrize(
        ("lines", "field", "expected"),
        [
            pytest.param(
                ["ROUTE_TABLE = {", '    "sku": "A1",', "}"],
                "sku",
                [(2, "A1")],
                id="python-dict-style",
            ),
            pytest.param(["sku = 'A1'"], "sku", [(1, "A1")], id="python-assignment-style"),
            pytest.param(['const record = { sku: "A1" };'], "sku", [(1, "A1")], id="js-object-style"),
            pytest.param(["product_id = 101"], "product_id", [(1, "101")], id="bare-number-literal"),
            pytest.param(['"vendor_id": "V1"'], "sku", [], id="unrelated-field-name-not-matched"),
            pytest.param(
                ['{"sku": "A1"}, {"sku": "A2"}'],
                "sku",
                [(1, "A1"), (1, "A2")],
                id="multiple-matches-on-one-line",
            ),
            pytest.param(['sku = ""'], "sku", [], id="genuinely-empty-string-not-matched"),
        ],
    )
    def test_grammar_shapes(self, lines: list[str], field: str, expected: list[tuple[int, str]]) -> None:
        fc = _fixture_consistency()
        assert fc._extract_identifier_literals(lines, field) == expected

    def test_field_name_is_escaped_before_use_as_a_regex_fragment(self) -> None:
        """A field name containing a regex metacharacter (e.g. a dotted path) must match itself
        literally, never be interpreted as a regex fragment."""
        fc = _fixture_consistency()
        assert fc._extract_identifier_literals(['"sku.id": "A1"'], "sku.id") == [(1, "A1")]
        # The unescaped '.' in "sku.id" must not match an unrelated single character.
        assert fc._extract_identifier_literals(['"skuXid": "A1"'], "sku.id") == []

    def test_single_line_triple_quoted_value_is_not_matched_with_a_false_empty_value(self) -> None:
        """doc_review round-1 D2: a single-line triple-quoted assignment (``sku = \"\"\"GHOST-TQ\"\"\"``)
        must never be reported as a match carrying an empty literal value -- the pre-fix grammar
        misread the opening ``\"\"`` of the ``\"\"\"`` run as a complete empty-string literal, so the
        finding MISSTATED the source line's actual value. The fixed grammar rejects an
        immediately-self-closing quote pair (a negative lookahead on the opening quote's
        backreference), which correctly makes this an unmatched line rather than a match with a
        wrong value -- consistent with this heuristic's documented "does not detect a value spread
        across more than one physical line" bound, since a triple-quoted literal is the one-line
        prefix of that same multi-line-string grammar.
        """
        fc = _fixture_consistency()
        assert fc._extract_identifier_literals(['sku = """GHOST-TQ"""'], "sku") == []
        assert fc._extract_identifier_literals(["sku = '''GHOST-TQ'''"], "sku") == []
        # The same negative-lookahead fix also means a genuinely empty single/double-quoted
        # string (`sku = ""`) is not matched -- an empty identifier value is never a real
        # catalog key, so this is a deliberate, documented side effect of closing the
        # triple-quote misrepresentation defect above, not a separate regression. Covered by
        # `test_grammar_shapes`'s `genuinely-empty-string-not-matched` case (W7).


@pytest.mark.unit
class TestDevbenchYamlReferenceDocumentsAccuracyBounds:
    """test_review round-1 advisory A3: a drift pin for
    ``docs/devbench-yaml-reference.md``'s ``extract_source_literals`` accuracy-bounds
    documentation, since (pre-pin) deleting the entire ``## gates.fixture_consistency.
    extract_source_literals`` section killed no test in this suite -- AC-E6-F2-S1-T1-7 requires
    the key to be "documented with accuracy bounds," and this asserts the section actually
    exists with the specific bounds this mode's own code enforces."""

    _DOC_PATH = Path(__file__).parent.parent / "docs" / "devbench-yaml-reference.md"

    def _doc_text(self) -> str:
        return self._DOC_PATH.read_text(encoding="utf-8")

    def test_extract_source_literals_section_heading_is_present(self) -> None:
        assert "### `gates.fixture_consistency.extract_source_literals`" in self._doc_text()

    def test_accuracy_bounds_subsection_is_present(self) -> None:
        assert "**Documented accuracy bounds" in self._doc_text()

    def test_no_waiver_mechanism_bound_is_documented(self) -> None:
        """Blocking 3: the doc must no longer prescribe the in-fixture ``allow_missing`` waiver
        as a remedy for a source-literal finding, since the code never consults it there."""
        text = self._doc_text()
        assert "no waiver mechanism" in text.lower()

    def test_pruned_directory_scanning_boundary_is_documented(self) -> None:
        """D1: the pruned-directory scanning boundary must be documented, not just the
        `SOURCE_EXTENSIONS` classification boundary."""
        assert "CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS" in self._doc_text()

    def test_triple_quote_bound_is_documented(self) -> None:
        """D2: the triple-quote/empty-string non-detection bound must be documented."""
        assert "triple-quoted" in self._doc_text()

    def test_union_resolution_sentence_is_documented(self) -> None:
        """W-d (round-3 test_review): the round-1 Blocking 2 union-resolution behaviour (a
        matched literal is resolved against the UNION of every canonical source sharing the same
        ``identifier_field``, never cross-producted against an unrelated one) has no other
        anti-drift pin in this suite -- deleting the sentence killed no test."""
        assert "UNION of every canonical source" in self._doc_text()

    def test_value_redaction_is_documented(self) -> None:
        """SECURITY (security_review AND code_review round-4): the unconditional redaction
        posture for every extracted literal value must be documented alongside the mode's other
        accuracy bounds, so an operator enabling the mode knows the finding output never
        reproduces any part of the extracted value verbatim, regardless of length."""
        text = self._doc_text()
        assert "redact" in text.lower()
        assert "unconditional" in text.lower() or "regardless of its length" in text.lower()

    def test_symlink_scanning_boundary_is_documented(self) -> None:
        """doc_review round-4 Blocking 2a/2b: a FILE symlink whose resolved real path falls
        outside the resolved scope root is excluded (live or dangling); one resolving inside the
        root is still included; a symlinked DIRECTORY is never descended into. This must be
        documented with the same specificity the pruned-directory names already get.

        test_review round-4 WARN: sliced to the WHOLE document, this pin was satisfied by the
        neighbouring `gates.shared_file_impact.auto_derive_registry` section's own copy of the
        same three phrases (see `test_auto_derive_registry_symlink_scanning_boundary_is_documented`
        below), so it never noticed drift in this section's own prose. Sliced the same way that
        sibling test slices its own section: bounded between this section's own heading and the
        NEXT `### ` heading, so only this section's prose is examined."""
        text = self._doc_text()
        section = text.split("### `gates.fixture_consistency.extract_source_literals`")[1].split("\n### `")[0]
        assert "os.path.realpath" in section
        assert "symlinked DIRECTORY" in section
        assert "followlinks=False" in section

    def test_out_of_root_symlink_only_checkout_zero_source_files_route_is_documented(self) -> None:
        """doc_review round-4 Blocking 2b: an out-of-root-symlink-only checkout is a second,
        newly-introduced route to the zero-classified-source-files loud error, distinct from the
        pruned-directory route -- both must be named."""
        text = self._doc_text()
        assert "out-of-root symlink" in text

    def test_auto_derive_registry_symlink_scanning_boundary_is_documented(self) -> None:
        """doc_review round-4 Blocking 2a: `auto_derive_registry`'s own "full, CLOSED set" of
        pruned directory names newly falsely implies pruned directories are the SOLE reason a
        classified `.py` file under the repo root can go unscanned -- an out-of-root FILE symlink
        (`link_out.py` in doc_review's own reproduction) is also excluded and neither scans nor
        votes, and a symlinked DIRECTORY is never descended into. Both must be documented with
        the same specificity the pruned-directory names already get."""
        text = self._doc_text()
        auto_derive_section = text.split("### `gates.shared_file_impact.auto_derive_registry`")[1]
        assert "os.path.realpath" in auto_derive_section
        assert "symlinked DIRECTORY" in auto_derive_section
        assert "followlinks=False" in auto_derive_section


@pytest.mark.unit
class TestCliReferenceDocumentsExtractSourceLiteralsCause:
    """W-d (round-3 test_review): a drift pin for ``docs/cli-reference.md``'s
    ``check-fixture-consistency`` exit-code-1 table row, whose ``extract_source_literals``
    zero-classified-source-files clause and 'all four of the latter' count have no other
    anti-drift pin in this suite -- deleting either killed no test."""

    _DOC_PATH = Path(__file__).parent.parent / "docs" / "cli-reference.md"

    def _doc_text(self) -> str:
        return self._DOC_PATH.read_text(encoding="utf-8")

    def test_exit_code_one_row_names_the_extract_source_literals_cause(self) -> None:
        text = self._doc_text()
        assert "`extract_source_literals` is enabled with zero classified source files in scope" in text

    def test_exit_code_one_row_claims_all_four_causes_not_three(self) -> None:
        text = self._doc_text()
        assert "all four of the latter" in text
        assert "all three of the latter" not in text


@pytest.mark.unit
class TestConfigureDevbenchSkillDocumentsExtractSourceLiteralsAlternative:
    """W-d (round-3 test_review): a drift pin for the ``configure-devbench`` skill's
    ``gates.fixture_consistency.extract_source_literals`` interview entry's ``Alternatives``
    bullet, which has no other anti-drift pin in this suite -- reverting it to the pre-shipped
    'reserved for a future ... has no effect yet' wording killed no test."""

    _SKILL_PATH = (
        Path(__file__).parent.parent
        / "plugin-authoring"
        / "devbench-authoring"
        / "skills"
        / "configure-devbench"
        / "SKILL.md"
    )

    def _skill_text(self) -> str:
        return self._SKILL_PATH.read_text(encoding="utf-8")

    def test_alternatives_bullet_describes_the_shipped_scan_mode(self) -> None:
        text = self._skill_text()
        assert (
            "**Alternatives:** `true` (enables the source-literal scan mode in addition to "
            "the structured JSON/YAML cross-reference mode above.)" in text
        )

    def test_alternatives_bullet_no_longer_claims_the_mode_has_no_effect(self) -> None:
        text = self._skill_text()
        assert "has no effect yet" not in text
        assert "reserved for a future" not in text.lower()

    def test_entry_documents_symlink_exclusion_and_redaction(self) -> None:
        """test_review round-4 WARN: the round-4/5 symlink-boundary and unconditional-redaction
        prose mirrored into this skill's ``extract_source_literals`` interview entry had no
        drift pin at all -- stripping it killed no test."""
        text = self._skill_text()
        assert "resolved real path falls outside the walked root" in text
        assert "symlinked DIRECTORY is never descended into" in text
        assert "redacted" in text.lower()
        assert "unconditionally, regardless of length" in text.lower()


@pytest.mark.unit
class TestFormatFixtureLocation:
    """``_format_fixture_location`` -- the single builder shared by the structured scan-target
    cross-reference (no line number) and the source-literal extraction mode (always a line
    number), so the ``file`` vs ``file:line`` formatting decision lives in exactly one place
    (REFACTOR, spec 4.7 bullet 4 Approach step 9)."""

    def test_no_line_returns_bare_path(self) -> None:
        fc = _fixture_consistency()
        assert fc._format_fixture_location("mock_lookup.json") == "mock_lookup.json"

    def test_line_appends_colon_line_number(self) -> None:
        fc = _fixture_consistency()
        assert fc._format_fixture_location("app/routes.py", 3) == "app/routes.py:3"
