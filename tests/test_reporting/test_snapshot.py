"""Tests for ``devbench.reporting.snapshot`` (issue #162 Phase 6, ADR-20).

Pins the freshness contract, atomic-write semantics, and schema-version
invalidation.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from devbench.reporting.snapshot import (
    SNAPSHOT_DIR_NAME,
    SNAPSHOT_FILE_NAME,
    SnapshotData,
    read_snapshot,
    snapshot_path,
    write_snapshot,
)


class TestSnapshotPath:
    def test_path_is_under_dotdevbench(self, tmp_path: Path) -> None:
        path = snapshot_path(tmp_path)
        assert path == tmp_path / SNAPSHOT_DIR_NAME / SNAPSHOT_FILE_NAME

    def test_path_uses_canonical_filename(self, tmp_path: Path) -> None:
        assert snapshot_path(tmp_path).name == "report-snapshot.json"


class TestWriteSnapshot:
    def test_creates_dotdevbench_directory_if_missing(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("a")
        write_snapshot(tmp_path, "REPORT", log)
        assert (tmp_path / SNAPSHOT_DIR_NAME).is_dir()

    def test_writes_canonical_path(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("a")
        out = write_snapshot(tmp_path, "REPORT", log)
        assert out == snapshot_path(tmp_path)
        assert out.is_file()

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("hello world")
        write_snapshot(tmp_path, "REPORT", log)
        payload = _json.loads(snapshot_path(tmp_path).read_text())
        assert payload["schema_version"] == 2  # issue #168: schema-bump for shard freshness keys
        assert payload["report_text"] == "REPORT"
        assert payload["log_size"] == len("hello world")
        assert isinstance(payload["log_mtime_ns"], int)
        assert payload["log_mtime_ns"] > 0

    def test_records_zero_key_for_missing_log(self, tmp_path: Path) -> None:
        """Writing a snapshot when the source log doesn't exist yet records
        the zero freshness key. Any later log creation invalidates the
        snapshot (current mtime != 0)."""
        write_snapshot(tmp_path, "REPORT", tmp_path / "nope.log")
        payload = _json.loads(snapshot_path(tmp_path).read_text())
        assert payload["log_mtime_ns"] == 0
        assert payload["log_size"] == 0

    def test_atomic_replace_on_overwrite(self, tmp_path: Path) -> None:
        """A second write replaces the first atomically. The .tmp file
        must NOT be left lying around after a successful write."""
        log = tmp_path / "log"
        log.write_text("a")
        write_snapshot(tmp_path, "FIRST", log)
        log.write_text("ab")  # bumps mtime + size
        write_snapshot(tmp_path, "SECOND", log)
        # Canonical file holds the latest payload.
        payload = _json.loads(snapshot_path(tmp_path).read_text())
        assert payload["report_text"] == "SECOND"
        # No leftover .tmp file.
        leftover = snapshot_path(tmp_path).with_suffix(snapshot_path(tmp_path).suffix + ".tmp")
        assert not leftover.exists()


class TestReadSnapshot:
    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("a")
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_when_log_missing(self, tmp_path: Path) -> None:
        """Source log deleted after snapshot was written -> stale."""
        log = tmp_path / "log"
        log.write_text("a")
        write_snapshot(tmp_path, "R", log)
        log.unlink()
        assert read_snapshot(tmp_path, log) is None

    def test_returns_data_when_log_unchanged(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("hello")
        write_snapshot(tmp_path, "REPORT", log)
        result = read_snapshot(tmp_path, log)
        assert isinstance(result, SnapshotData)
        assert result.report_text == "REPORT"
        assert result.log_size == 5

    def test_returns_none_when_log_grew(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("hello")
        write_snapshot(tmp_path, "R", log)
        log.write_text("hello world")
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_when_log_shrank(self, tmp_path: Path) -> None:
        """Truncated / rotated log -> snapshot is stale even though
        mtime advanced. Both freshness keys must match."""
        log = tmp_path / "log"
        log.write_text("hello world")
        write_snapshot(tmp_path, "R", log)
        log.write_text("hi")  # smaller; mtime may also advance
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text("{not valid json")
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_on_schema_mismatch(self, tmp_path: Path) -> None:
        """A snapshot written by a future devbench (different
        schema_version) returns None so the caller rebuilds via the
        live path."""
        log = tmp_path / "log"
        log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 999,  # future version this code can't read
                    "log_mtime_ns": log.stat().st_mtime_ns,
                    "log_size": log.stat().st_size,
                    "report_text": "R",
                }
            )
        )
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_on_missing_required_field(self, tmp_path: Path) -> None:
        """A snapshot missing a required field is treated as corrupt and
        returns None. Defensive: a partial write that survived a crash
        should not poison the read path."""
        log = tmp_path / "log"
        log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps({"schema_version": 1, "log_mtime_ns": 0})  # missing log_size + report_text
        )
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_on_wrong_field_type(self, tmp_path: Path) -> None:
        """log_mtime_ns must be int. A stringy value from a hand-edited
        snapshot returns None."""
        log = tmp_path / "log"
        log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 1,
                    "log_mtime_ns": "not-an-int",
                    "log_size": 1,
                    "report_text": "R",
                }
            )
        )
        assert read_snapshot(tmp_path, log) is None

    def test_returns_none_when_report_text_is_wrong_type(self, tmp_path: Path) -> None:
        """report_text must be a string. Defensive check guards against a
        hand-edited snapshot whose schema-version + freshness keys match
        but whose payload field has the wrong shape."""
        log = tmp_path / "log"
        log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 1,
                    "log_mtime_ns": log.stat().st_mtime_ns,
                    "log_size": log.stat().st_size,
                    "report_text": ["not", "a", "string"],
                }
            )
        )
        assert read_snapshot(tmp_path, log) is None


class TestRoundTrip:
    def test_write_then_read_returns_same_text(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("source bytes")
        original = "  Multiline\n  report text\n  with formatting\n"
        write_snapshot(tmp_path, original, log)
        result = read_snapshot(tmp_path, log)
        assert result is not None
        assert result.report_text == original


class TestShardedFreshnessKey:
    """Issue #168: snapshot schema v2 includes the sharded-tree mtime
    aggregate so any shard mutation invalidates the snapshot."""

    def _seed_workspace_with_shard(self, tmp_path: Path) -> Path:
        """Build a workspace with one shard + an empty live log."""
        live_log = tmp_path / "logs" / "orchestrator.log"
        live_log.parent.mkdir(parents=True)
        live_log.write_text("")
        shard = tmp_path / "logs" / "2026-05" / "E0-F1-S1-T1.jsonl"
        shard.parent.mkdir(parents=True)
        shard.write_text("2026-05-04T10:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'done'\n")
        return live_log

    def test_freshness_key_covers_sharded_tree(self, tmp_path: Path) -> None:
        """Touching a shard's mtime invalidates an existing snapshot."""

        live_log = self._seed_workspace_with_shard(tmp_path)
        write_snapshot(tmp_path, "REPORT", live_log)
        assert read_snapshot(tmp_path, live_log) is not None

        # Mutate the shard file: append a new line so size advances.
        # mtime alone may have nanosecond-precision collisions; combining
        # mtime + size makes the freshness check reliable.
        shard = tmp_path / "logs" / "2026-05" / "E0-F1-S1-T1.jsonl"
        with shard.open("a") as fh:
            fh.write("2026-05-04T11:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n")

        assert read_snapshot(tmp_path, live_log) is None

    def test_v1_snapshot_invalidates_after_schema_bump(self, tmp_path: Path) -> None:
        """A v1 payload (pre-#168) reads as stale so the caller rebuilds."""
        live_log = tmp_path / "log"
        live_log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 1,  # old version
                    "log_mtime_ns": live_log.stat().st_mtime_ns,
                    "log_size": live_log.stat().st_size,
                    "report_text": "R",
                }
            )
        )
        assert read_snapshot(tmp_path, live_log) is None

    def test_freshness_key_invalidates_when_new_shard_appears(self, tmp_path: Path) -> None:
        """A snapshot written before any shards exist reads as stale once
        a sharded layout appears (the shard list went from [] to [...])."""
        live_log = tmp_path / "logs" / "orchestrator.log"
        live_log.parent.mkdir(parents=True)
        live_log.write_text("seed")
        write_snapshot(tmp_path, "REPORT", live_log)
        assert read_snapshot(tmp_path, live_log) is not None

        # Create a sharded layout post-write.
        shard = tmp_path / "logs" / "2026-05" / "E0-F1-S1-T1.jsonl"
        shard.parent.mkdir(parents=True)
        shard.write_text("2026-05-04T10:00:00Z [devbench.cli] INFO event\n")

        assert read_snapshot(tmp_path, live_log) is None

    def test_freshness_key_includes_shard_list_in_payload(self, tmp_path: Path) -> None:
        """Inspect the persisted payload to confirm shard mtimes are recorded."""
        live_log = self._seed_workspace_with_shard(tmp_path)
        write_snapshot(tmp_path, "REPORT", live_log)
        payload = _json.loads(snapshot_path(tmp_path).read_text())
        assert payload["schema_version"] == 2
        assert isinstance(payload["shard_keys"], list)
        assert len(payload["shard_keys"]) == 1
        # Each shard key is [mtime_ns, size]; both ints, both non-zero.
        shard_mtime, shard_size = payload["shard_keys"][0]
        assert isinstance(shard_mtime, int)
        assert isinstance(shard_size, int)
        assert shard_mtime > 0
        assert shard_size > 0

    def test_payload_with_corrupt_shard_key_returns_none(self, tmp_path: Path) -> None:
        """A v2 payload whose shard_keys entries have the wrong shape
        (e.g. a string instead of [int, int]) returns None defensively."""
        live_log = tmp_path / "log"
        live_log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 2,
                    "log_mtime_ns": live_log.stat().st_mtime_ns,
                    "log_size": live_log.stat().st_size,
                    "shard_keys": ["not-a-pair"],
                    "report_text": "R",
                }
            )
        )
        assert read_snapshot(tmp_path, live_log) is None

    def test_payload_with_missing_shard_keys_field_returns_none(self, tmp_path: Path) -> None:
        """A v2 payload missing the shard_keys field is invalid (the
        field is required by schema v2)."""
        live_log = tmp_path / "log"
        live_log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 2,
                    "log_mtime_ns": live_log.stat().st_mtime_ns,
                    "log_size": live_log.stat().st_size,
                    "report_text": "R",
                    # shard_keys missing
                }
            )
        )
        assert read_snapshot(tmp_path, live_log) is None

    def test_payload_with_non_int_shard_key_value_returns_none(self, tmp_path: Path) -> None:
        """Shard-key int validation: a stringy mtime returns None."""
        live_log = tmp_path / "log"
        live_log.write_text("a")
        snapshot_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        snapshot_path(tmp_path).write_text(
            _json.dumps(
                {
                    "schema_version": 2,
                    "log_mtime_ns": live_log.stat().st_mtime_ns,
                    "log_size": live_log.stat().st_size,
                    "shard_keys": [["not-an-int", 0]],
                    "report_text": "R",
                }
            )
        )
        assert read_snapshot(tmp_path, live_log) is None
