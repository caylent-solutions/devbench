"""Supplementary tests for ``devbench.reporting.event_index`` that pin
error / edge-path branches not exercised by the main test suite. The
goal is 100% line coverage on the module; these tests are surgical and
target one branch each.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.reporting.event_index import (
    EventIndex,
    _iso_to_epoch_us,
    _role_from_attribution,
    _tokens_from_usage,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".devbench").mkdir()
    return tmp_path


def _write_orch_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, entries: list[object]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


class TestCorruptDbConnectionClosePath:
    """Lines 267-269: when sqlite3.DatabaseError fires AFTER a successful
    ``_open_connection`` call, the existing connection must be closed
    before the rebuild.  Patch ``executescript`` to raise on the first
    invocation so we hit this branch with a real connection in hand.
    """

    def test_open_rebuild_closes_existing_connection_on_corruption(self, workspace: Path) -> None:
        from devbench.reporting import event_index as ei_mod

        class _FailingExecuteScriptConn:
            """Delegates all attribute access to the wrapped connection
            except ``executescript``, which raises DatabaseError once."""

            def __init__(self, conn: sqlite3.Connection) -> None:
                self._conn = conn
                self.close_called = False
                self.script_raised = False

            def executescript(self, sql: str) -> object:
                if not self.script_raised:
                    self.script_raised = True
                    raise sqlite3.DatabaseError("simulated schema corruption")
                return self._conn.executescript(sql)

            def close(self) -> None:
                self.close_called = True
                self._conn.close()

            def __getattr__(self, name: str) -> object:
                return getattr(self._conn, name)

        # Pre-create the cache DB at the EXPECTED schema version so the
        # ``open()`` method enters the else-branch (line 261-263) which calls
        # ``executescript`` defensively -- that's where our wrapper raises.
        cache_dir = workspace / ".devbench" / "report-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db = cache_dir / "events.sqlite"
        with sqlite3.connect(str(db)) as seed_conn:
            seed_conn.executescript(ei_mod._INIT_SQL)
            seed_conn.execute(f"PRAGMA user_version = {ei_mod._SCHEMA_VERSION}")

        real_open_connection = ei_mod._open_connection
        call_count = {"n": 0}
        first_wrapper: list[_FailingExecuteScriptConn] = []

        def open_wrapped(db_path: Path) -> sqlite3.Connection:
            conn = real_open_connection(db_path)
            call_count["n"] += 1
            if call_count["n"] == 1:
                wrapped = _FailingExecuteScriptConn(conn)
                first_wrapper.append(wrapped)
                return wrapped  # type: ignore[return-value]
            return conn

        with patch.object(ei_mod, "_open_connection", side_effect=open_wrapped):
            idx = EventIndex.open(workspace)
        try:
            # The rebuild succeeded: schema-version pragma is set.
            (version,) = idx._conn.execute("PRAGMA user_version").fetchone()
            assert version == ei_mod._SCHEMA_VERSION
            # The first connection was closed via the except-branch
            # contextlib.suppress block.
            assert first_wrapper[0].close_called
        finally:
            idx.close()


class TestInvalidationOnRotation:
    """Lines 318-319, 323-324: rotation/truncation invalidation for hook
    log + transcript files.  Tested by writing a file, indexing it, then
    truncating it and re-indexing.
    """

    def test_hook_log_rotation_invalidates_cache(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            hook_log = workspace / "hook.jsonl"
            entry = {
                "timestamp": "2025-01-01T00:00:01Z",
                "input": {
                    "tool_response": {"totalDurationMs": 5},
                    "transcript_path": "/some/path.jsonl",
                },
            }
            _write_jsonl(hook_log, [entry, entry, entry])
            idx.refresh_hook_log(hook_log)
            # Truncate: rotation detected via size < cached_size.
            hook_log.write_text("", encoding="utf-8")
            idx.refresh_hook_log(hook_log)
            # New entry after rotation.
            _write_jsonl(hook_log, [entry])
            idx.refresh_hook_log(hook_log)
            # Only one row remains; the original three were invalidated.
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM hook_entries").fetchone()
            assert count == 1
        finally:
            idx.close()

    def test_transcript_rotation_invalidates_cache(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            tdir = workspace / "transcripts"
            tdir.mkdir()
            tfile = tdir / "a.jsonl"
            entry = {
                "timestamp": "2025-01-01T00:00:01Z",
                "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5}},
            }
            _write_jsonl(tfile, [entry, entry])
            idx.refresh_transcripts(tdir)
            # Truncate: triggers invalidation branch.
            tfile.write_text("", encoding="utf-8")
            idx.refresh_transcripts(tdir)
            _write_jsonl(tfile, [entry])
            idx.refresh_transcripts(tdir)
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM transcript_entries").fetchone()
            assert count == 1
        finally:
            idx.close()


class TestScanLinesOSError:
    """Lines 337-338: ``_scan_lines_with_offsets`` swallows OSError on open
    and yields nothing.  Hard to test via the public API because the
    caller checks ``is_file()`` first; we patch ``Path.open`` to force
    the OSError path inside the iterator.
    """

    def test_oserror_on_open_yields_nothing(self, workspace: Path, tmp_path: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            log = tmp_path / "raise-on-open.log"
            log.write_text("garbage", encoding="utf-8")
            with patch.object(Path, "open", side_effect=OSError("denied")):
                items = list(idx._scan_lines_with_offsets(log, 0))
            assert items == []
        finally:
            idx.close()


class TestOrchLogParseErrors:
    """Lines 420-421 (no regex match) + 425-426 (unparseable timestamp).
    Mix valid + invalid lines and assert the invalid ones are skipped.
    """

    def test_skips_lines_without_log_prefix_and_with_bad_timestamp(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            log = workspace / "orch.log"
            _write_orch_log(
                log,
                [
                    "garbage line that does not match the [logger] pattern",
                    # Valid prefix, but timestamp portion is bogus.
                    "9999-99-99T99:99:99Z [judges.executor] INFO doing X",
                    # Valid line.
                    "2025-01-01T00:00:01Z [judges.executor] INFO transition E1-F1-S1-T1 -> done",
                ],
            )
            idx.refresh_orchestrator_log(log)
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM orch_log_events").fetchone()
            # Only the valid line was inserted.
            assert count == 1
        finally:
            idx.close()


class TestHookLogParseErrors:
    """Lines 473, 483, 494: hook-log entries that fail one of the
    isinstance / type checks are skipped or have NULL timestamps.
    """

    def test_non_dict_entry_is_skipped(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            hook_log = workspace / "hook.jsonl"
            # JSON top-level not a dict (a bare list).  Should be skipped.
            _write_jsonl(hook_log, [["not", "a", "dict"]])
            idx.refresh_hook_log(hook_log)
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM hook_entries").fetchone()
            assert count == 0
        finally:
            idx.close()

    def test_missing_timestamp_yields_null_ts(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            hook_log = workspace / "hook.jsonl"
            entry = {"input": {"tool_response": {"totalDurationMs": 7}}}  # no timestamp
            _write_jsonl(hook_log, [entry])
            idx.refresh_hook_log(hook_log)
            row = idx._conn.execute("SELECT ts_epoch_us FROM hook_entries").fetchone()
            assert row[0] is None
        finally:
            idx.close()

    def test_tool_response_not_dict_treated_as_empty(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            hook_log = workspace / "hook.jsonl"
            entry = {
                "timestamp": "2025-01-01T00:00:01Z",
                "input": {"tool_response": "not-a-dict"},  # forces line 494
            }
            _write_jsonl(hook_log, [entry])
            idx.refresh_hook_log(hook_log)
            row = idx._conn.execute("SELECT duration_ms FROM hook_entries").fetchone()
            assert row[0] == 0
        finally:
            idx.close()


class TestTranscriptParseErrors:
    """Lines 554-557, 564-581: transcript edge cases."""

    def test_unchanged_file_skips_reparse(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            tdir = workspace / "transcripts"
            tdir.mkdir()
            tfile = tdir / "a.jsonl"
            entry = {
                "timestamp": "2025-01-01T00:00:01Z",
                "message": {"id": "m1", "usage": {"input_tokens": 10}},
            }
            _write_jsonl(tfile, [entry])
            idx.refresh_transcripts(tdir)
            (count1,) = idx._conn.execute("SELECT COUNT(*) FROM transcript_entries").fetchone()
            # Second call without changing the file -- hits the
            # "unchanged" early-return branch.
            idx.refresh_transcripts(tdir)
            (count2,) = idx._conn.execute("SELECT COUNT(*) FROM transcript_entries").fetchone()
            assert count1 == count2 == 1
        finally:
            idx.close()

    def test_blank_and_malformed_transcript_lines_are_skipped(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            tdir = workspace / "transcripts"
            tdir.mkdir()
            tfile = tdir / "a.jsonl"
            # Blank line, malformed JSON, non-dict top-level, missing message,
            # bad timestamp, valid entry.
            tfile.write_text(
                "\n"
                "{not valid json}\n"
                "[1, 2, 3]\n"
                '{"timestamp": "2025-01-01T00:00:01Z"}\n'
                '{"timestamp": "not-a-date", "message": {"usage": {"input_tokens": 1}, "id": "x"}}\n'
                '{"timestamp": "2025-01-01T00:00:02Z", "message": {"usage": {"input_tokens": 5}, "id": "y"}}\n',
                encoding="utf-8",
            )
            idx.refresh_transcripts(tdir)
            rows = idx._conn.execute("SELECT message_id, ts_epoch_us FROM transcript_entries ORDER BY rowid").fetchall()
            # First insert from the bad-timestamp entry (ts=NULL because
            # parsing failed); second from the valid entry.
            assert len(rows) == 2
            assert rows[0][1] is None
            assert rows[1][1] is not None
        finally:
            idx.close()


class TestPathQueriesForMissingFile:
    """Lines 782-789, 801: ``all_log_timestamps`` and
    ``non_noise_log_timestamps`` exercise both the file_id-None early-return
    and the populated-file happy path.
    """

    def test_all_log_timestamps_returns_empty_for_unknown_file(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            assert idx.all_log_timestamps(workspace / "unknown.log") == []
        finally:
            idx.close()

    def test_all_log_timestamps_returns_sorted_timestamps(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            log = workspace / "orch.log"
            _write_orch_log(
                log,
                [
                    "2025-01-01T00:00:02Z [judges.executor] INFO second",
                    "2025-01-01T00:00:01Z [judges.executor] INFO first",
                ],
            )
            idx.refresh_orchestrator_log(log)
            result = idx.all_log_timestamps(log)
            assert len(result) == 2
            assert result[0] < result[1]
        finally:
            idx.close()

    def test_non_noise_log_timestamps_returns_empty_for_unknown_file(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            assert idx.non_noise_log_timestamps(workspace / "unknown.log", "noise") == []
        finally:
            idx.close()


class TestSnapshotEdgeCases:
    """Lines 931, 951, 956-957: snapshot read/write fallback paths."""

    def test_write_snapshot_noops_when_log_missing(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            missing = workspace / "no-such.log"
            idx.write_snapshot(missing, {"key": "val"})
            assert idx.read_snapshot(missing) is None
        finally:
            idx.close()

    def test_read_snapshot_returns_none_when_no_row(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            log = workspace / "orch.log"
            log.write_text("data\n", encoding="utf-8")
            # No write_snapshot call -- the row simply does not exist.
            assert idx.read_snapshot(log) is None
        finally:
            idx.close()

    def test_read_snapshot_returns_none_on_corrupted_payload(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            log = workspace / "orch.log"
            log.write_text("data\n", encoding="utf-8")
            idx.write_snapshot(log, {"k": "v"})
            # Manually corrupt the stored JSON payload.
            idx._conn.execute(
                "UPDATE report_snapshot SET payload_json = ? WHERE snapshot_id = 1",
                ("{not-valid-json",),
            )
            assert idx.read_snapshot(log) is None
        finally:
            idx.close()


class TestRoleFromAttribution:
    """Lines 1025-1026: ``_role_from_attribution`` colon-split + suffix
    normalisation branches.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("agent:code-reviewer", "code_review"),
            ("plain-string", "plain_string"),
            ("agent:test-reviewer", "test_review"),
        ],
    )
    def test_attribution_string_normalised(self, raw: str, expected: str) -> None:
        assert _role_from_attribution(raw) == expected

    def test_empty_string_falls_back_to_orchestrator(self) -> None:
        assert _role_from_attribution("") == "orchestrator"

    def test_non_string_falls_back_to_orchestrator(self) -> None:
        assert _role_from_attribution(12345) == "orchestrator"


class TestHookLogEarlyReturns:
    """Lines 447, 457, 467, 470-471: refresh_hook_log early-return paths."""

    def test_returns_when_file_does_not_exist(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            # File does not exist -- refresh_hook_log returns at line 447.
            idx.refresh_hook_log(workspace / "no-such.jsonl")
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM hook_entries").fetchone()
            assert count == 0
        finally:
            idx.close()

    def test_unchanged_file_skips_reparse(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            hook_log = workspace / "hook.jsonl"
            entry = {"timestamp": "2025-01-01T00:00:01Z", "input": {}}
            _write_jsonl(hook_log, [entry])
            idx.refresh_hook_log(hook_log)
            # Second call without modification hits the unchanged-file
            # early return at line 457.
            idx.refresh_hook_log(hook_log)
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM hook_entries").fetchone()
            assert count == 1
        finally:
            idx.close()

    def test_blank_and_malformed_lines_are_skipped(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            hook_log = workspace / "hook.jsonl"
            # Blank line + malformed JSON + valid entry.
            hook_log.write_text(
                '\n{not valid json}\n{"timestamp": "2025-01-01T00:00:01Z", "input": {}}\n',
                encoding="utf-8",
            )
            idx.refresh_hook_log(hook_log)
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM hook_entries").fetchone()
            assert count == 1
        finally:
            idx.close()


class TestTranscriptDirEarlyReturn:
    """Line 541: refresh_transcripts returns when transcript_dir is None or
    not a directory.
    """

    def test_returns_when_dir_not_a_directory(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            # Path to a regular file, not a directory.
            f = workspace / "file.txt"
            f.write_text("x", encoding="utf-8")
            idx.refresh_transcripts(f)
            (count,) = idx._conn.execute("SELECT COUNT(*) FROM transcript_entries").fetchone()
            assert count == 0
        finally:
            idx.close()


class TestTranscriptUnchangedFile:
    """Line 573: transcript with unparseable timestamp inside otherwise-valid
    entry (ts_epoch_us assignment via ValueError catch on _iso_to_epoch_us).
    Already exercised through ``test_blank_and_malformed_transcript_lines_are_skipped``
    but explicit second test pins the branch behaviour.
    """

    def test_bad_timestamp_yields_null_ts(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            tdir = workspace / "transcripts"
            tdir.mkdir()
            tfile = tdir / "a.jsonl"
            entry = {
                "timestamp": "not-a-date",
                "message": {"usage": {"input_tokens": 1}, "id": "z"},
            }
            _write_jsonl(tfile, [entry])
            idx.refresh_transcripts(tdir)
            row = idx._conn.execute("SELECT ts_epoch_us FROM transcript_entries").fetchone()
            assert row[0] is None
        finally:
            idx.close()

    def test_missing_timestamp_yields_null_ts(self, workspace: Path) -> None:
        """Entry without a ``timestamp`` field hits the line-573 branch
        where ``ts_str`` is None / non-string.
        """
        idx = EventIndex.open(workspace)
        try:
            tdir = workspace / "transcripts"
            tdir.mkdir()
            tfile = tdir / "a.jsonl"
            entry = {"message": {"usage": {"input_tokens": 1}, "id": "x"}}  # no timestamp
            _write_jsonl(tfile, [entry])
            idx.refresh_transcripts(tdir)
            row = idx._conn.execute("SELECT ts_epoch_us FROM transcript_entries").fetchone()
            assert row[0] is None
        finally:
            idx.close()


class TestAggregateForMissingFile:
    """Line 818, 905: aggregate_hook_window and first_hook_transcript_path
    return their empty/None defaults when the file isn't indexed.
    """

    def test_aggregate_hook_window_empty_for_unknown_file(self, workspace: Path) -> None:
        from datetime import UTC, datetime

        idx = EventIndex.open(workspace)
        try:
            res = idx.aggregate_hook_window(workspace / "no-such.jsonl", datetime(2020, 1, 1, tzinfo=UTC))
            assert isinstance(res, dict)
            assert all(v == 0 for v in res.values())
        finally:
            idx.close()

    def test_first_hook_transcript_path_returns_none_for_unknown_file(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            assert idx.first_hook_transcript_path(workspace / "no-such.jsonl") is None
        finally:
            idx.close()


class TestCacheCreationDictBranch:
    """Lines 994-995: ``cache_creation`` is a dict, so 5m/1h fields are read
    from inside it rather than from the legacy flat ``cache_creation_input_tokens``.
    """

    def test_cache_creation_dict_uses_5m_and_1h_fields(self) -> None:
        usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 100,
                "ephemeral_1h_input_tokens": 200,
            },
        }
        tokens = _tokens_from_usage(usage)
        assert tokens.cache_write_5m_tokens == 100
        assert tokens.cache_write_1h_tokens == 200


class TestIsoTimezone:
    """Line 1045: naive ISO timestamp (no offset) is treated as UTC."""

    def test_naive_iso_treated_as_utc(self) -> None:
        from datetime import UTC, datetime

        result_us = _iso_to_epoch_us("2025-01-01T00:00:00")
        expected = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1_000_000)
        assert result_us == expected
