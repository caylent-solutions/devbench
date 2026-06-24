"""Tests for src/devbench/backlog/operator_resolution_catalog.py.

Covers:
- Round-trip: write then read preserves classification, normalized signature,
  remediation, counts, and timestamp.
- Atomicity: write uses tmp-then-replace; partial/interrupted writes never
  corrupt the catalog file.
- Schema-version mismatch: loads as empty without raising.
- Malformed JSON: loads as empty without raising.
- Outcome recording: applied, escalated, failed increments correct counters.
- lookup_entry: returns None for unknown keys, returns record for known keys.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from devbench.backlog.operator_resolution_catalog import (
    CATALOG_SCHEMA_VERSION,
    CatalogRecord,
    catalog_path,
    load_catalog,
    lookup_entry,
    record_outcome,
    save_catalog,
)


@pytest.mark.unit
class TestCatalogSchemaVersionConstant:
    """CATALOG_SCHEMA_VERSION is a positive integer."""

    def test_schema_version_is_int(self) -> None:
        assert isinstance(CATALOG_SCHEMA_VERSION, int)

    def test_schema_version_is_positive(self) -> None:
        assert CATALOG_SCHEMA_VERSION >= 1


@pytest.mark.unit
class TestCatalogPath:
    """catalog_path returns the expected path under .devbench/."""

    def test_catalog_path_is_under_devbench(self) -> None:
        root = pathlib.Path("/some/workspace")
        path = catalog_path(root)
        assert path == root / ".devbench" / "operator-resolution-catalog.json"

    def test_catalog_path_is_absolute_when_root_is_absolute(self) -> None:
        root = pathlib.Path("/abs/root")
        path = catalog_path(root)
        assert path.is_absolute()

    def test_catalog_path_filename(self) -> None:
        root = pathlib.Path("/ws")
        assert catalog_path(root).name == "operator-resolution-catalog.json"


@pytest.mark.unit
class TestCatalogRecord:
    """CatalogRecord dataclass stores all required fields."""

    def test_catalog_record_fields(self) -> None:
        ts = datetime.now(UTC)
        record = CatalogRecord(
            classification="RUNTIME_DEGRADATION",
            normalized_signature="restart-loop:exit-code-1",
            remediation="re-queue",
            success_count=2,
            failure_count=1,
            last_applied=ts,
        )
        assert record.classification == "RUNTIME_DEGRADATION"
        assert record.normalized_signature == "restart-loop:exit-code-1"
        assert record.remediation == "re-queue"
        assert record.success_count == 2
        assert record.failure_count == 1
        assert record.last_applied == ts

    def test_catalog_record_default_counts(self) -> None:
        ts = datetime.now(UTC)
        record = CatalogRecord(
            classification="cls",
            normalized_signature="sig",
            remediation="re-queue",
            success_count=0,
            failure_count=0,
            last_applied=ts,
        )
        assert record.success_count == 0
        assert record.failure_count == 0


@pytest.mark.unit
class TestRoundTrip:
    """write then read preserves all record fields."""

    def test_round_trip_single_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
            record = CatalogRecord(
                classification="RUNTIME_DEGRADATION",
                normalized_signature="restart-loop:exit-1",
                remediation="re-queue",
                success_count=3,
                failure_count=1,
                last_applied=ts,
            )
            save_catalog(root, {"RUNTIME_DEGRADATION:restart-loop:exit-1": record})
            loaded = load_catalog(root)
            assert "RUNTIME_DEGRADATION:restart-loop:exit-1" in loaded
            loaded_record = loaded["RUNTIME_DEGRADATION:restart-loop:exit-1"]
            assert loaded_record.classification == "RUNTIME_DEGRADATION"
            assert loaded_record.normalized_signature == "restart-loop:exit-1"
            assert loaded_record.remediation == "re-queue"
            assert loaded_record.success_count == 3
            assert loaded_record.failure_count == 1
            assert loaded_record.last_applied == ts

    def test_round_trip_multiple_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            ts = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
            records = {
                "CLS_A:sig-a": CatalogRecord(
                    classification="CLS_A",
                    normalized_signature="sig-a",
                    remediation="re-queue",
                    success_count=1,
                    failure_count=0,
                    last_applied=ts,
                ),
                "CLS_B:sig-b": CatalogRecord(
                    classification="CLS_B",
                    normalized_signature="sig-b",
                    remediation="reconcile-cascade",
                    success_count=5,
                    failure_count=2,
                    last_applied=ts,
                ),
            }
            save_catalog(root, records)
            loaded = load_catalog(root)
            assert len(loaded) == 2
            assert loaded["CLS_A:sig-a"].remediation == "re-queue"
            assert loaded["CLS_B:sig-b"].success_count == 5

    def test_empty_catalog_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            save_catalog(root, {})
            loaded = load_catalog(root)
            assert loaded == {}


@pytest.mark.unit
class TestAtomicWrite:
    """save_catalog writes via tmp-then-replace (never leaves a partial file)."""

    def test_catalog_file_created_after_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            ts = datetime.now(UTC)
            record = CatalogRecord(
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                success_count=0,
                failure_count=0,
                last_applied=ts,
            )
            save_catalog(root, {"CLS:sig": record})
            path = catalog_path(root)
            assert path.exists()

    def test_no_tmp_file_left_after_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            save_catalog(root, {})
            devbench_dir = root / ".devbench"
            tmp_files = list(devbench_dir.glob("*.tmp"))
            assert tmp_files == []

    def test_atomic_write_uses_replace(self) -> None:
        """Verify that save_catalog calls Path.replace (atomic rename)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            (root / ".devbench").mkdir(parents=True, exist_ok=True)
            replace_calls: list[pathlib.Path] = []
            original_replace = pathlib.Path.replace

            def capturing_replace(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
                replace_calls.append(target)
                return original_replace(self, target)

            with patch.object(pathlib.Path, "replace", capturing_replace):
                save_catalog(root, {})

            expected = catalog_path(root)
            assert any(p == expected for p in replace_calls), (
                f"Expected replace() to be called with {expected}; got {replace_calls}"
            )

    def test_devbench_dir_created_if_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            assert not (root / ".devbench").exists()
            save_catalog(root, {})
            assert catalog_path(root).exists()


@pytest.mark.unit
class TestSelfHealingLoad:
    """Malformed or legacy catalog loads as empty without raising."""

    def test_missing_file_loads_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            loaded = load_catalog(root)
            assert loaded == {}

    def test_malformed_json_loads_as_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("NOT VALID JSON {{{", encoding="utf-8")
            loaded = load_catalog(root)
            assert loaded == {}
            captured = capsys.readouterr()
            assert "WARNING" in captured.err or "malformed" in captured.err.lower()

    def test_schema_version_mismatch_loads_as_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            legacy_payload = {
                "schema_version": CATALOG_SCHEMA_VERSION + 99,
                "entries": {},
            }
            path.write_text(json.dumps(legacy_payload), encoding="utf-8")
            loaded = load_catalog(root)
            assert loaded == {}
            captured = capsys.readouterr()
            assert "WARNING" in captured.err or "schema" in captured.err.lower()

    def test_missing_schema_version_loads_as_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
            loaded = load_catalog(root)
            assert loaded == {}
            captured = capsys.readouterr()
            assert "WARNING" in captured.err or "schema" in captured.err.lower()

    def test_wrong_type_entries_loads_as_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            bad_payload = {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "entries": ["not", "a", "dict"],
            }
            path.write_text(json.dumps(bad_payload), encoding="utf-8")
            loaded = load_catalog(root)
            assert loaded == {}
            captured = capsys.readouterr()
            assert "WARNING" in captured.err or "malformed" in captured.err.lower()

    def test_top_level_json_array_loads_as_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON array at top level is not a valid catalog object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            loaded = load_catalog(root)
            assert loaded == {}
            captured = capsys.readouterr()
            assert "WARNING" in captured.err or "malformed" in captured.err.lower()

    def test_entry_without_timezone_round_trips_as_utc(self) -> None:
        """Entries stored without timezone info are treated as UTC on load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            naive_ts = "2026-01-01T00:00:00"
            payload = {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "entries": {
                    "CLS:sig": {
                        "classification": "CLS",
                        "normalized_signature": "sig",
                        "remediation": "re-queue",
                        "success_count": 1,
                        "failure_count": 0,
                        "last_applied": naive_ts,
                    }
                },
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_catalog(root)
            assert "CLS:sig" in loaded
            assert loaded["CLS:sig"].last_applied.tzinfo is not None

    @pytest.mark.parametrize(
        "bad_counts",
        [
            {"success_count": "not-an-int", "failure_count": 0},
            {"success_count": 1, "failure_count": "also-bad"},
        ],
    )
    def test_entry_with_non_int_counts_is_skipped(self, bad_counts: dict[str, object]) -> None:
        """Entries with non-integer count fields are silently skipped (self-healing)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "entries": {
                    "CLS:sig": {
                        "classification": "CLS",
                        "normalized_signature": "sig",
                        "remediation": "re-queue",
                        "last_applied": "2026-01-01T00:00:00+00:00",
                        **bad_counts,
                    }
                },
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_catalog(root)
            assert "CLS:sig" not in loaded

    def test_entry_that_is_not_a_dict_is_skipped(self, capsys: pytest.CaptureFixture[str]) -> None:
        """An entry whose value is not a dict is silently skipped with a WARNING."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            path = catalog_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "entries": {
                    "CLS:sig": "this-should-be-a-dict-not-a-string",
                },
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_catalog(root)
            assert "CLS:sig" not in loaded
            captured = capsys.readouterr()
            assert "WARNING" in captured.err or "skipping" in captured.err.lower()


@pytest.mark.unit
class TestLookupEntry:
    """lookup_entry returns None for unknown keys, record for known keys."""

    def test_lookup_returns_none_for_unknown_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            save_catalog(root, {})
            result = lookup_entry(root, "UNKNOWN_CLS", "unknown-sig")
            assert result is None

    def test_lookup_returns_record_for_known_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            ts = datetime.now(UTC)
            record = CatalogRecord(
                classification="AWAITING_DEPENDENCY",
                normalized_signature="dep-missing",
                remediation="reconcile-cascade",
                success_count=2,
                failure_count=0,
                last_applied=ts,
            )
            save_catalog(root, {"AWAITING_DEPENDENCY:dep-missing": record})
            result = lookup_entry(root, "AWAITING_DEPENDENCY", "dep-missing")
            assert result is not None
            assert result.remediation == "reconcile-cascade"
            assert result.success_count == 2

    def test_lookup_key_is_classification_colon_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            ts = datetime.now(UTC)
            record = CatalogRecord(
                classification="CLS_X",
                normalized_signature="sig-y",
                remediation="re-queue",
                success_count=0,
                failure_count=0,
                last_applied=ts,
            )
            save_catalog(root, {"CLS_X:sig-y": record})
            result = lookup_entry(root, "CLS_X", "sig-y")
            assert result is not None
            assert lookup_entry(root, "CLS_X", "sig-z") is None
            assert lookup_entry(root, "CLS_Z", "sig-y") is None


@pytest.mark.unit
class TestRecordOutcome:
    """record_outcome persists applied, escalated, and failed outcomes."""

    def test_record_applied_creates_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="exit-code-1",
                remediation="re-queue",
                outcome="applied",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "exit-code-1")
            assert result is not None
            assert result.success_count == 1
            assert result.failure_count == 0

    def test_record_failed_creates_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="exit-code-1",
                remediation="re-queue",
                outcome="failed",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "exit-code-1")
            assert result is not None
            assert result.success_count == 0
            assert result.failure_count == 1

    def test_record_escalated_does_not_change_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="exit-code-1",
                remediation="re-queue",
                outcome="escalated",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "exit-code-1")
            assert result is not None
            assert result.success_count == 0
            assert result.failure_count == 0

    def test_record_applied_increments_success_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            for _ in range(3):
                record_outcome(
                    root,
                    classification="CLS",
                    normalized_signature="sig",
                    remediation="re-queue",
                    outcome="applied",
                )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.success_count == 3

    def test_record_failed_increments_failure_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            for _ in range(2):
                record_outcome(
                    root,
                    classification="CLS",
                    normalized_signature="sig",
                    remediation="re-queue",
                    outcome="failed",
                )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.failure_count == 2

    def test_record_outcome_updates_last_applied_on_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome="applied",
            )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.last_applied.tzinfo is not None
            now = datetime.now(UTC)
            delta = now - result.last_applied
            assert delta.total_seconds() < 60

    def test_record_outcome_updates_last_applied_on_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome="failed",
            )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.last_applied.tzinfo is not None

    def test_record_outcome_preserves_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="reconcile-cascade",
                outcome="applied",
            )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.remediation == "reconcile-cascade"

    @pytest.mark.parametrize("outcome", ["applied", "escalated", "failed"])
    def test_record_outcome_valid_outcomes(self, outcome: str) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome=outcome,
            )

    def test_record_outcome_invalid_outcome_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            with pytest.raises(ValueError, match="invalid outcome"):
                record_outcome(
                    root,
                    classification="CLS",
                    normalized_signature="sig",
                    remediation="re-queue",
                    outcome="unknown-outcome",
                )

    def test_record_outcome_mixed_applied_and_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root, classification="CLS", normalized_signature="sig", remediation="re-queue", outcome="applied"
            )
            record_outcome(
                root, classification="CLS", normalized_signature="sig", remediation="re-queue", outcome="failed"
            )
            record_outcome(
                root, classification="CLS", normalized_signature="sig", remediation="re-queue", outcome="applied"
            )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.success_count == 2
            assert result.failure_count == 1


@pytest.mark.unit
class TestNovelSignatureRecord:
    """record_outcome supports the 'novel' outcome for unrecognized signatures."""

    def test_record_novel_creates_entry(self) -> None:
        """Recording a novel outcome creates a catalog entry for operator review."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="novel-pattern",
                remediation="re-queue",
                outcome="novel",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "novel-pattern")
            assert result is not None

    def test_record_novel_does_not_increment_success_count(self) -> None:
        """Novel outcome does not increment success_count (not yet applied)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="novel-sig",
                remediation="re-queue",
                outcome="novel",
            )
            result = lookup_entry(root, "CLS", "novel-sig")
            assert result is not None
            assert result.success_count == 0

    def test_record_novel_does_not_increment_failure_count(self) -> None:
        """Novel outcome does not increment failure_count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="novel-sig",
                remediation="re-queue",
                outcome="novel",
            )
            result = lookup_entry(root, "CLS", "novel-sig")
            assert result is not None
            assert result.failure_count == 0

    def test_record_novel_updates_last_applied(self) -> None:
        """Novel outcome sets last_applied to a recent UTC timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="novel-ts-sig",
                remediation="re-queue",
                outcome="novel",
            )
            result = lookup_entry(root, "CLS", "novel-ts-sig")
            assert result is not None
            assert result.last_applied.tzinfo is not None
            now = datetime.now(UTC)
            delta = now - result.last_applied
            assert delta.total_seconds() < 60

    def test_novel_is_a_valid_outcome(self) -> None:
        """'novel' is accepted as a valid outcome and does not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome="novel",
            )

    @pytest.mark.parametrize("outcome", ["applied", "escalated", "failed", "novel"])
    def test_record_outcome_accepts_all_valid_outcomes(self, outcome: str) -> None:
        """All four valid outcomes are accepted without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome=outcome,
            )

    def test_novel_entry_is_overwritten_by_subsequent_applied(self) -> None:
        """A novel entry followed by an apply still records the final state correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome="novel",
            )
            record_outcome(
                root,
                classification="CLS",
                normalized_signature="sig",
                remediation="re-queue",
                outcome="applied",
            )
            result = lookup_entry(root, "CLS", "sig")
            assert result is not None
            assert result.success_count == 1
            assert result.failure_count == 0
