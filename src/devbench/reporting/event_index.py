"""Persistent event-index cache for the report subsystem (issue #162).

This module is the read-side cache layer that replaces the full-file
re-parse of three log sources every ``devbench report`` invocation:

* The orchestrator's text-format ``logs/orchestrator.log`` -- regex
  scanned for ``Set <id> to '<status>'`` events and the per-line
  ``[logger.name]`` tag (used by the session-boundary detector).
* The plugin's ``hook-logs.jsonl`` -- JSON-per-line entries with
  per-token-type usage data.
* The Claude Code per-session transcripts under
  ``~/.claude/projects/<slug>/`` -- JSON-per-line messages with
  ``message.usage`` data and per-agent attribution.

The cache lives in a single SQLite database at
``<workspace>/.devbench/report-cache/events.sqlite`` (sqlite3 is
stdlib, satisfying issue #162's "no new mandatory runtime
dependencies" rule). Each source file is tracked by mtime+size+the
byte offset of the last parsed line. On every ``refresh_*`` call the
cache validates the source against its row in the ``source_files``
table:

* If mtime + size are unchanged, the cache is a perfect hit and no
  IO is performed against the source file.
* If size grew (and mtime advanced) the orchestrator's append-only
  contract holds; the cache reads only the bytes from
  ``parsed_offset`` to current size, parses the new lines, inserts
  them, and updates the ``source_files`` row.
* If size shrank or mtime regressed the source was rotated /
  truncated / hand-edited; the cache invalidates every event for
  that source and re-parses the whole file.

Every timestamp is stored as ``ts_epoch_us INTEGER`` (microseconds
since the Unix epoch, UTC). Storing a single normalised type avoids
the pitfall of comparing ``YYYY-MM-DDTHH:MM:SSZ`` against
``YYYY-MM-DDTHH:MM:SS.ffffff+00:00`` lexicographically -- ``Z`` and
``+`` sort before ``.``, which would silently misdrop entries near
the window boundary. Indexes on the integer column give correct
range-scan semantics for free.

The cached data is a deterministic, lossless transformation of the
source files. The JSONL log is still the audit-friendly source of
truth; if the cache is deleted the next report invocation rebuilds it
from scratch.
"""

from __future__ import annotations

import contextlib
import json as _json
import logging
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger("devbench.reporting.event_index")


# Schema version. Bumped whenever the on-disk schema changes in a way
# that requires a rebuild (column added, index changed, etc.). On open
# the cache reads ``PRAGMA user_version`` and rebuilds from scratch
# when the value differs from ``_SCHEMA_VERSION``.
#
# Version 2: ``ts_epoch_us`` on ``hook_entries`` + ``transcript_entries``
# is now nullable. NULL means "timestamp present in the source but
# unparseable" -- the legacy ``_entry_in_window`` includes these in
# every window aggregate (fail-soft so a malformed entry doesn't drop
# the cost data attached to it). The query ``WHERE ts_epoch_us IS NULL
# OR ts_epoch_us >= ?`` mirrors that semantic.
#
# Version 3 (issue #169): ``transcript_entries`` gains a ``message_id``
# column with a partial UNIQUE index (``WHERE message_id IS NOT NULL``).
# Resumed Claude Code sessions copy prior assistant messages forward
# into new transcript files, so the same logical message can appear in
# multiple ``*.jsonl`` files. The unique index lets ``INSERT OR IGNORE``
# discard the duplicate at ingest time so the aggregate ``SUM`` stays
# correct without a query-side ``DISTINCT``. Entries without a stable
# ``message.id`` continue to insert -- the partial predicate excludes
# NULLs from the uniqueness check.
#
# Version 4 (issue #223): ``hook_entries`` + ``transcript_entries`` both
# gain a ``model TEXT`` column carrying the Claude model id (the literal
# ``message.model`` from the transcript envelope, e.g.
# ``claude-opus-4-7``).  NULL means "no model attribution available"
# and aggregates under the ``"<unknown>"`` bucket priced against
# ``REPORT_DEFAULT_MODEL_RATES``.  Pre-v4 caches are dropped + rebuilt by
# the open-time version-mismatch handler (rebuild is lossless because
# every row is a deterministic transformation of the source files).
_SCHEMA_VERSION = 4


_KIND_ORCH_LOG = "orchestrator_log"
_KIND_HOOK_LOG = "hook_log"
_KIND_TRANSCRIPT = "transcript"


# Match a log line of the form "YYYY-MM-DDTHH:MM:SSZ [logger.name] LEVEL ...",
# capturing the ISO-8601 timestamp (group 1) and the logger name (group 2).
# Same regex shape as the existing one in report.py; kept private here so
# the index can advance one line at a time during incremental reads.
_LOG_LINE_RE = re.compile(rb"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z \[([^\]]+)\]")
# Issue #329 FR-1b (ingestion-side hardening). Mirrors the existing
# ``_DONE_RE`` / ``_PROGRESS_RE`` regexes in report.py: task IDs must start
# with ``E`` (the backlog work-unit-ID convention) to be counted as
# transition events.
#
# The leading `` [A-Z]+ `` matches the level token (e.g. ``INFO``) that
# ``_LOG_LINE_RE`` does not itself capture. This pattern is applied with
# ``.match(raw_line, pos)`` where ``pos`` is the offset immediately after
# the ``_LOG_LINE_RE`` match (i.e. right after the closing ``]`` of the
# logger tag) -- ``.match()`` with an explicit ``pos`` requires the match to
# START at that exact byte offset, so a transition phrase quoted later in
# the same line (e.g. inside an echoed SDK ``ToolResultBlock`` payload that
# reproduces a prior audit comment) can never match. Only the line's OWN
# ``LEVEL Set <id> to '<status>'`` record -- the text the logging framework
# itself wrote immediately after the prefix -- can match. Previously this
# was `` _TASK_TRANSITION_RE.search(raw_line)``, which scanned the entire
# line and let a later, unrelated quote win.
_TASK_TRANSITION_RE = re.compile(rb" [A-Z]+ Set (E\S+) to '([^']+)'")

# Issue #329 FR-1a (query-side, authoritative). The only in-tree emitter of
# the quoted ``Set <id> to '<status>'`` record is
# ``BacklogManager._set_status`` via ``self.logger`` -- defaulted to
# ``logging.getLogger("devbench.backlog_manager")`` at manager.py -- which is
# a code-level invariant of the emitter, not operator configuration, hence a
# module constant rather than a YAML/env key. ``devbench.cli`` logs
# transitions unquoted (``Set %s to %s``, no ``'``) and so never matches
# ``_TASK_TRANSITION_RE`` on its own account; it only leaked prior
# transition rows into the index by echoing other loggers' quoted text
# inside a later payload (issue #329 Defect A). Both transition-query
# methods bind this constant as a ``logger = ?`` predicate so only rows the
# real emitter wrote count as evidence of a transition.
_TRANSITION_LOGGER = "devbench.backlog_manager"


# Issue #329 FR-1a: ``orch_log_events.logger`` is nullable rather than
# ``NOT NULL``. No in-tree ingestion path inserts a NULL ``logger`` today --
# ``_LOG_LINE_RE``'s logger-name capture group requires at least one
# character, so a row is only ever created with a populated logger -- but
# the column stays nullable so the transition-query predicate
# (``logger = _TRANSITION_LOGGER``) is provably defensive against a
# corrupt/unattributable row rather than merely "correct by the ingestion
# code's current behaviour". SQL's three-valued comparison already makes
# ``NULL = 'devbench.backlog_manager'`` evaluate to NULL (never TRUE), so a
# NULL row is excluded by the existing predicate with no extra ``IS NOT
# NULL`` clause needed. This does not require a schema-version bump: it
# widens what MAY be stored, so every row a pre-change cache already holds
# (always non-NULL) remains valid, and no rebuild is triggered.
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS source_files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    parsed_offset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orch_log_events (
    file_id INTEGER NOT NULL,
    line_offset INTEGER NOT NULL,
    ts_epoch_us INTEGER NOT NULL,
    logger TEXT,
    task_id TEXT,
    transition TEXT,
    PRIMARY KEY (file_id, line_offset),
    FOREIGN KEY (file_id) REFERENCES source_files(file_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_orch_log_events_ts ON orch_log_events(ts_epoch_us);
CREATE INDEX IF NOT EXISTS idx_orch_log_events_task
    ON orch_log_events(task_id, transition, ts_epoch_us);
CREATE INDEX IF NOT EXISTS idx_orch_log_events_logger ON orch_log_events(logger, ts_epoch_us);

CREATE TABLE IF NOT EXISTS hook_entries (
    file_id INTEGER NOT NULL,
    line_offset INTEGER NOT NULL,
    -- Nullable: NULL means the source entry's timestamp was unparseable.
    -- The aggregate query treats NULL as "always in window" to match the
    -- legacy ``_entry_in_window`` fail-soft behaviour.
    ts_epoch_us INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_5m_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
    has_usage INTEGER NOT NULL DEFAULT 0,
    is_us_only INTEGER NOT NULL DEFAULT 0,
    is_fast INTEGER NOT NULL DEFAULT 0,
    transcript_path TEXT,
    -- Issue #223: per-call model attribution.  NULL aggregates under the
    -- ``"<unknown>"`` bucket; non-NULL values match a row in
    -- ``REPORT_MODEL_RATES`` or fall back to ``REPORT_DEFAULT_MODEL_RATES``.
    model TEXT,
    PRIMARY KEY (file_id, line_offset),
    FOREIGN KEY (file_id) REFERENCES source_files(file_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_hook_entries_ts ON hook_entries(ts_epoch_us);
CREATE INDEX IF NOT EXISTS idx_hook_entries_model_ts ON hook_entries(model, ts_epoch_us);

CREATE TABLE IF NOT EXISTS transcript_entries (
    file_id INTEGER NOT NULL,
    line_offset INTEGER NOT NULL,
    ts_epoch_us INTEGER,  -- nullable; same NULL-means-always-in-window semantic as hook_entries
    role TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_5m_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
    is_us_only INTEGER NOT NULL DEFAULT 0,
    is_fast INTEGER NOT NULL DEFAULT 0,
    has_usage INTEGER NOT NULL DEFAULT 0,
    message_id TEXT,  -- issue #169: assistant message id for cross-file dedup; NULL allowed
    -- Issue #223: per-call model attribution; same semantic as hook_entries.model.
    model TEXT,
    PRIMARY KEY (file_id, line_offset),
    FOREIGN KEY (file_id) REFERENCES source_files(file_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_transcript_entries_ts ON transcript_entries(ts_epoch_us);
CREATE INDEX IF NOT EXISTS idx_transcript_entries_role_ts
    ON transcript_entries(role, ts_epoch_us);
CREATE INDEX IF NOT EXISTS idx_transcript_entries_model_ts
    ON transcript_entries(model, ts_epoch_us);
-- Issue #169: cross-file dedup. Resumed sessions copy prior assistant
-- messages into new files; the partial unique index lets
-- ``INSERT OR IGNORE`` reject the second insert of the same message_id
-- while still allowing rows whose message_id is NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_transcript_entries_msgid
    ON transcript_entries(message_id) WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS report_snapshot (
    snapshot_id INTEGER PRIMARY KEY CHECK (snapshot_id = 1),
    log_mtime_ns INTEGER NOT NULL,
    log_size_bytes INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    written_at TEXT NOT NULL
);
"""


# In-memory lock to serialise writers within a single Python process.
# SQLite itself serialises across processes via its own locking; this
# extra lock guards against concurrent ``refresh_*`` calls in the same
# interpreter (e.g. from a future watch-mode parallel renderer) racing
# on the parsed_offset bookkeeping.
_INDEX_LOCKS: dict[Path, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


def _index_lock_for(db_path: Path) -> threading.Lock:
    """Return the per-DB-path lock, creating it lazily on first access."""
    with _REGISTRY_LOCK:
        lock = _INDEX_LOCKS.get(db_path)
        if lock is None:
            lock = threading.Lock()
            _INDEX_LOCKS[db_path] = lock
        return lock


def _open_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with the project's standard pragmas applied."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL gives concurrent readers + a single writer with low overhead;
    # ideal for ``devbench report --watch N`` running alongside an
    # active orchestrator that may someday want to feed the same index.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class EventIndex:
    """Read-side cache for orchestrator + hook + transcript event data.

    Construct via :meth:`open` (which opens the SQLite file, validates
    the schema version, and rebuilds the schema from scratch on a
    version mismatch or corruption). Call ``refresh_*`` to bring each
    source's cache up to date with the current file state, then call
    the ``query_*`` methods to read aggregated views.

    Instances are not safe to share across threads; each thread should
    call :meth:`open` independently. Cross-process safety is provided
    by SQLite's WAL journal.
    """

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self._conn = conn
        self._db_path = db_path
        self._lock = _index_lock_for(db_path)

    @classmethod
    def open(cls, workspace_root: Path) -> EventIndex:
        """Open (or create + initialise) the cache for ``workspace_root``.

        Detects schema-version drift and corrupted DB files and rebuilds
        from scratch when either is found. The rebuild is safe because
        every cached row is a deterministic transformation of the source
        files; nothing useful is lost.
        """
        cache_dir = workspace_root / ".devbench" / "report-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db_path = cache_dir / "events.sqlite"
        conn: sqlite3.Connection | None = None
        try:
            conn = _open_connection(db_path)
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            if current_version != _SCHEMA_VERSION:
                conn.close()
                db_path.unlink(missing_ok=True)
                conn = _open_connection(db_path)
                conn.executescript(_INIT_SQL)
                conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            else:
                # Run init defensively in case a partial init left tables missing.
                conn.executescript(_INIT_SQL)
        except sqlite3.DatabaseError:
            # Corrupt SQLite file. Wipe and rebuild: every row is
            # derived from source files, so the rebuild is lossless.
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()
            db_path.unlink(missing_ok=True)
            conn = _open_connection(db_path)
            conn.executescript(_INIT_SQL)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        return cls(conn, db_path)

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._conn.close()

    # ------------------------------------------------------------------
    # Source-file bookkeeping
    # ------------------------------------------------------------------

    def _get_or_create_file_row(self, path: Path, kind: str) -> tuple[int, int, int, int]:
        """Return ``(file_id, mtime_ns, size_bytes, parsed_offset)`` for ``path``.

        Inserts a zero row when the file is being seen for the first
        time. All columns are 0 for the zero row so the caller's
        invalidation logic treats it as "fully unparsed".
        """
        cur = self._conn.execute(
            "SELECT file_id, mtime_ns, size_bytes, parsed_offset FROM source_files WHERE path = ?",
            (str(path),),
        )
        row = cur.fetchone()
        if row is not None:
            return int(row[0]), int(row[1]), int(row[2]), int(row[3])
        cur = self._conn.execute(
            "INSERT INTO source_files (path, kind, mtime_ns, size_bytes, parsed_offset) VALUES (?, ?, 0, 0, 0)",
            (str(path), kind),
        )
        file_id = int(cur.lastrowid or 0)
        return file_id, 0, 0, 0

    def _update_file_row(self, file_id: int, mtime_ns: int, size_bytes: int, parsed_offset: int) -> None:
        self._conn.execute(
            "UPDATE source_files SET mtime_ns = ?, size_bytes = ?, parsed_offset = ? WHERE file_id = ?",
            (mtime_ns, size_bytes, parsed_offset, file_id),
        )

    def _invalidate_orch_log_events_for_file(self, file_id: int) -> None:
        """Delete every cached orchestrator-log row for ``file_id`` (rotation / truncation path)."""
        self._conn.execute("DELETE FROM orch_log_events WHERE file_id = ?", (file_id,))
        self._conn.execute("UPDATE source_files SET parsed_offset = 0 WHERE file_id = ?", (file_id,))

    def _invalidate_hook_entries_for_file(self, file_id: int) -> None:
        """Delete every cached hook-entry row for ``file_id`` (rotation / truncation path)."""
        self._conn.execute("DELETE FROM hook_entries WHERE file_id = ?", (file_id,))
        self._conn.execute("UPDATE source_files SET parsed_offset = 0 WHERE file_id = ?", (file_id,))

    def _invalidate_transcript_entries_for_file(self, file_id: int) -> None:
        """Delete every cached transcript-entry row for ``file_id`` (rotation / truncation path)."""
        self._conn.execute("DELETE FROM transcript_entries WHERE file_id = ?", (file_id,))
        self._conn.execute("UPDATE source_files SET parsed_offset = 0 WHERE file_id = ?", (file_id,))

    def _scan_lines_with_offsets(self, path: Path, start_offset: int) -> Iterator[tuple[int, bytes]]:
        """Yield ``(line_start_offset, raw_line_bytes)`` from ``path`` starting at ``start_offset``.

        Lines are yielded in file order with their starting byte offset
        so the caller can record per-line offsets in the index. Trailing
        partial lines (no final newline) are skipped so a half-written
        line doesn't enter the cache; the next refresh will pick them up
        once the writer flushes the newline.
        """
        try:
            f = path.open("rb")
        except OSError:
            return
        try:
            f.seek(start_offset)
            current_offset = start_offset
            buf = b""
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    line = buf[:nl]
                    yield (current_offset, line)
                    current_offset += nl + 1
                    buf = buf[nl + 1 :]
            # Anything remaining in ``buf`` is a partial unterminated
            # line; explicitly do NOT yield it.
        finally:
            f.close()

    # ------------------------------------------------------------------
    # Orchestrator log
    # ------------------------------------------------------------------

    def refresh_orchestrator_log(self, log_path: Path) -> None:
        """Bring the orchestrator-log cache into sync with ``log_path``.

        No-op when ``log_path`` doesn't exist (e.g. a brand-new
        workspace before the orchestrator has logged anything). The
        report consumer's ``done_times`` / ``progress_times`` queries
        return empty results in that case; existing report code already
        handles "log absent" elsewhere.
        """
        if not log_path.is_file():
            return
        with self._lock:
            self._refresh_orchestrator_log_locked(log_path)

    def refresh_orch_log_sources(self, workspace_root: Path, live_log_path: Path) -> None:
        """Refresh the live flat log + every sharded-tree shard.

        Issue #168: post-Phase-3-migration the historical events live in
        ``logs/<YYYY-MM>/<task>.jsonl`` shards. The live ``logs/orchestrator.log``
        carries any post-migration accumulation. This method refreshes both
        sources so the cache reflects the union; the union-aware query
        helpers (``task_transition_times_for_workspace`` etc.) then aggregate
        events across every relevant ``file_id``.

        When the workspace has no sharded layout (pre-migration or never-
        migrated workspace), this method behaves exactly like
        ``refresh_orchestrator_log(live_log_path)`` -- the shard enumeration
        is a no-op and only the live log is refreshed. Backwards-compatible.
        """
        from devbench.reporting.sharded_log import is_sharded_layout, iter_shard_paths

        with self._lock:
            if live_log_path.is_file():
                self._refresh_orchestrator_log_locked(live_log_path)
            if is_sharded_layout(workspace_root):
                for shard_path in iter_shard_paths(workspace_root):
                    self._refresh_orchestrator_log_locked(shard_path)

    def _refresh_orchestrator_log_locked(self, log_path: Path) -> None:
        stat = log_path.stat()
        mtime_ns = stat.st_mtime_ns
        size_bytes = stat.st_size
        file_id, cached_mtime, cached_size, parsed_offset = self._get_or_create_file_row(log_path, _KIND_ORCH_LOG)
        if cached_mtime == mtime_ns and cached_size == size_bytes:
            return  # perfect hit
        if size_bytes < cached_size or mtime_ns < cached_mtime:
            # Rotation / truncation / hand-edit -- invalidate.
            self._invalidate_orch_log_events_for_file(file_id)
            parsed_offset = 0
        # Append-only path: parse from parsed_offset to size_bytes.
        rows: list[tuple[int, int, int, str, str | None, str | None]] = []
        new_parsed_offset = parsed_offset
        for offset, raw_line in self._scan_lines_with_offsets(log_path, parsed_offset):
            new_parsed_offset = offset + len(raw_line) + 1  # +1 for the consumed newline
            m = _LOG_LINE_RE.match(raw_line)
            if not m:
                continue
            ts_str = m.group(1).decode("ascii")
            try:
                ts_epoch_us = _orch_ts_to_epoch_us(ts_str)
            except ValueError:
                continue
            logger_name = m.group(2).decode("utf-8", errors="replace")
            task_match = _TASK_TRANSITION_RE.match(raw_line, m.end())
            task_id = task_match.group(1).decode("utf-8", errors="replace") if task_match else None
            transition = task_match.group(2).decode("utf-8", errors="replace") if task_match else None
            rows.append((file_id, offset, ts_epoch_us, logger_name, task_id, transition))
        if rows:
            self._conn.executemany(
                "INSERT OR REPLACE INTO orch_log_events "
                "(file_id, line_offset, ts_epoch_us, logger, task_id, transition) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        self._update_file_row(file_id, mtime_ns, size_bytes, new_parsed_offset)

    # ------------------------------------------------------------------
    # Hook log
    # ------------------------------------------------------------------

    def refresh_hook_log(self, hook_log_path: Path) -> None:
        """Bring the hook-log cache into sync with ``hook_log_path``."""
        if not hook_log_path.is_file():
            return
        with self._lock:
            self._refresh_hook_log_locked(hook_log_path)

    def _refresh_hook_log_locked(self, hook_log_path: Path) -> None:
        stat = hook_log_path.stat()
        mtime_ns = stat.st_mtime_ns
        size_bytes = stat.st_size
        file_id, cached_mtime, cached_size, parsed_offset = self._get_or_create_file_row(hook_log_path, _KIND_HOOK_LOG)
        if cached_mtime == mtime_ns and cached_size == size_bytes:
            return
        if size_bytes < cached_size or mtime_ns < cached_mtime:
            self._invalidate_hook_entries_for_file(file_id)
            parsed_offset = 0
        new_parsed_offset = parsed_offset
        rows: list[tuple] = []
        for offset, raw_line in self._scan_lines_with_offsets(hook_log_path, parsed_offset):
            new_parsed_offset = offset + len(raw_line) + 1
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = _json.loads(stripped)
            except _json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            ts_str = entry.get("timestamp")
            if not isinstance(ts_str, str) or not ts_str:
                # Match legacy ``_entry_in_window``: a missing
                # ``timestamp`` field means the entry is dropped
                # (return value defaults True only when the field is
                # ``""`` -- but legacy uses ``entry.get("timestamp", "")``
                # which produces ``""`` for missing too, so the entry
                # was effectively included as "always in window"). Mark
                # ts as NULL so the aggregate query picks it up.
                ts_epoch_us: int | None = None
            else:
                try:
                    ts_epoch_us = _iso_to_epoch_us(ts_str)
                except ValueError:
                    # Unparseable timestamp -- legacy behaviour
                    # includes the entry in every window (the cost
                    # data attached to it would otherwise vanish).
                    ts_epoch_us = None
            tool_resp = (entry.get("input") or {}).get("tool_response") or {}
            if not isinstance(tool_resp, dict):
                tool_resp = {}
            duration_ms = tool_resp.get("totalDurationMs")
            duration_ms_int = int(duration_ms) if isinstance(duration_ms, int) else 0
            usage = tool_resp.get("usage")
            tokens = _tokens_from_usage(usage)
            transcript_path = (entry.get("input") or {}).get("transcript_path")
            transcript_path_str = transcript_path if isinstance(transcript_path, str) else None
            # Issue #223: hook log entries typically carry the model id
            # inside ``tool_response.model`` (Claude Code's PostToolUse
            # envelope).  Some entries fall back to ``entry.model``.
            # When neither is present the row is stored with model=NULL
            # and aggregates under the ``"<unknown>"`` bucket downstream.
            raw_model = tool_resp.get("model") or entry.get("model")
            model_str = raw_model if isinstance(raw_model, str) and raw_model else None
            rows.append(
                (
                    file_id,
                    offset,
                    ts_epoch_us,
                    duration_ms_int,
                    tokens.input_tokens,
                    tokens.output_tokens,
                    tokens.cache_read_tokens,
                    tokens.cache_write_5m_tokens,
                    tokens.cache_write_1h_tokens,
                    tokens.has_usage,
                    tokens.is_us_only,
                    tokens.is_fast,
                    transcript_path_str,
                    model_str,
                )
            )
        if rows:
            self._conn.executemany(
                "INSERT OR REPLACE INTO hook_entries "
                "(file_id, line_offset, ts_epoch_us, duration_ms, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, "
                "has_usage, is_us_only, is_fast, transcript_path, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self._update_file_row(file_id, mtime_ns, size_bytes, new_parsed_offset)

    # ------------------------------------------------------------------
    # Transcripts
    # ------------------------------------------------------------------

    def refresh_transcripts(self, transcript_dir: Path | None) -> None:
        """Bring every transcript file under ``transcript_dir`` into sync.

        ``transcript_dir`` is typically the parent of one Claude Code
        project's session JSONLs (``~/.claude/projects/<slug>``).
        Each file in the directory is cached independently.
        """
        if transcript_dir is None or not transcript_dir.is_dir():
            return
        with self._lock:
            for transcript_file in sorted(transcript_dir.glob("*.jsonl")):
                self._refresh_transcript_locked(transcript_file)

    def _refresh_transcript_locked(self, transcript_path: Path) -> None:
        stat = transcript_path.stat()
        mtime_ns = stat.st_mtime_ns
        size_bytes = stat.st_size
        file_id, cached_mtime, cached_size, parsed_offset = self._get_or_create_file_row(
            transcript_path, _KIND_TRANSCRIPT
        )
        if cached_mtime == mtime_ns and cached_size == size_bytes:
            return
        if size_bytes < cached_size or mtime_ns < cached_mtime:
            self._invalidate_transcript_entries_for_file(file_id)
            parsed_offset = 0
        new_parsed_offset = parsed_offset
        rows: list[tuple] = []
        for offset, raw_line in self._scan_lines_with_offsets(transcript_path, parsed_offset):
            new_parsed_offset = offset + len(raw_line) + 1
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = _json.loads(stripped)
            except _json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            ts_str = entry.get("timestamp")
            if not isinstance(ts_str, str) or not ts_str:
                ts_epoch_us: int | None = None
            else:
                try:
                    ts_epoch_us = _iso_to_epoch_us(ts_str)
                except ValueError:
                    ts_epoch_us = None
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            tokens = _tokens_from_usage(usage)
            if not tokens.has_usage:
                continue
            role = _role_from_attribution(entry.get("attributionAgent"))
            # Issue #169: capture message.id so the partial unique index
            # can dedup carried-forward messages across resumed-session
            # transcript files. Non-string ids fall through as NULL,
            # which the partial predicate excludes from uniqueness.
            raw_id = message.get("id")
            message_id = raw_id if isinstance(raw_id, str) and raw_id else None
            # Issue #223: capture the model id Claude Code records on
            # every ``assistant`` message envelope.  NULL when the field
            # is missing or non-string; the aggregator buckets such rows
            # under ``"<unknown>"`` priced against ``REPORT_DEFAULT_MODEL_RATES``.
            raw_model = message.get("model")
            model_str = raw_model if isinstance(raw_model, str) and raw_model else None
            rows.append(
                (
                    file_id,
                    offset,
                    ts_epoch_us,
                    role,
                    tokens.input_tokens,
                    tokens.output_tokens,
                    tokens.cache_read_tokens,
                    tokens.cache_write_5m_tokens,
                    tokens.cache_write_1h_tokens,
                    tokens.is_us_only,
                    tokens.is_fast,
                    tokens.has_usage,
                    message_id,
                    model_str,
                )
            )
        if rows:
            # OR IGNORE (was OR REPLACE) so a duplicate ``message_id``
            # from a resumed session leaves the original row in place
            # rather than swapping in the carried-forward copy. The
            # carried copy carries identical usage data, so first-write
            # wins is deterministic.
            self._conn.executemany(
                "INSERT OR IGNORE INTO transcript_entries "
                "(file_id, line_offset, ts_epoch_us, role, input_tokens, output_tokens, "
                "cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, "
                "is_us_only, is_fast, has_usage, message_id, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self._update_file_row(file_id, mtime_ns, size_bytes, new_parsed_offset)

    # ------------------------------------------------------------------
    # Query API (consumed by report.py)
    # ------------------------------------------------------------------
    #
    # Every query is scoped to a specific source-file path so the same
    # cache DB can hold events from many log files (multi-session
    # workspaces, side-by-side renamed logs, the cache db being shared
    # by every test in a pytest run) without one source's data leaking
    # into another's aggregates.

    def _file_id_for(self, path: Path) -> int | None:
        row = self._conn.execute("SELECT file_id FROM source_files WHERE path = ?", (str(path),)).fetchone()
        return int(row[0]) if row else None

    def _orch_log_file_ids_for_workspace(self, workspace_root: Path, live_log_path: Path) -> list[int]:
        """Return the list of orch-log ``file_id``s relevant to ``workspace_root``.

        Issue #168: after the Phase 3 migration the orchestrator log's
        historical events live in shards under ``logs/<YYYY-MM>/``. The
        union-aware query helpers fold transitions from every shard plus
        the live flat log so the report sees the same data as before
        migration. When no sharded layout exists, this returns either
        ``[live_id]`` (live log present) or ``[]`` (brand-new workspace).

        Anti-double-count rule: the live log's file_id is included only
        when the file CURRENTLY exists on disk. After migration archives
        the live log to ``logs/legacy/``, the pre-migration row in
        ``source_files`` still exists (we don't garbage-collect it on
        every read), but the same event content now lives under shard
        file_ids -- including BOTH would double-count every event. Same
        rule applies to each shard path: only include shards that exist
        on disk right now.
        """
        from devbench.reporting.sharded_log import is_sharded_layout, iter_shard_paths

        ids: list[int] = []
        if live_log_path.is_file():
            live_id = self._file_id_for(live_log_path)
            if live_id is not None:
                ids.append(live_id)
        if is_sharded_layout(workspace_root):
            for shard_path in iter_shard_paths(workspace_root):
                if shard_path.is_file():
                    shard_id = self._file_id_for(shard_path)
                    if shard_id is not None:
                        ids.append(shard_id)
        return ids

    def task_transition_times_for_workspace(
        self,
        workspace_root: Path,
        live_log_path: Path,
        transition: str,
    ) -> dict[str, datetime]:
        """Workspace-aware variant of ``task_transition_times``.

        Issue #168: aggregates per-task transitions across every orch-log
        ``file_id`` in the workspace (live flat log + sharded-tree
        shards). Reduces to the legacy single-file behaviour when the
        workspace has no shards. ``MAX(ts_epoch_us) GROUP BY task_id``
        preserves the assignment-overwrites-earlier semantic.

        Issue #329 FR-1a: rows are additionally restricted to
        ``logger = _TRANSITION_LOGGER``. A row whose ``logger`` is anything
        else -- including ``NULL``, which SQL's three-valued comparison
        already excludes from an ``=`` predicate -- is not evidence of a
        transition, so it is dropped rather than counted. This is query-side
        because ``logger`` is already persisted on every cached row, so a
        cache populated before this predicate existed is corrected on the
        very next query with no rebuild and no cache-version bump.
        """
        file_ids = self._orch_log_file_ids_for_workspace(workspace_root, live_log_path)
        if not file_ids:
            return {}
        placeholders = ",".join("?" for _ in file_ids)
        # SQL composed via list-join rather than an f-string so the static
        # analyser does not misclassify the variable-arity ``IN`` clause
        # as user-controlled input. ``placeholders`` is a comma-joined
        # string of literal ``?`` characters; values bind through the
        # parameter tuple below.
        sql = "".join(
            [
                "SELECT task_id, MAX(ts_epoch_us) FROM orch_log_events ",
                "WHERE file_id IN (",
                placeholders,
                ") AND transition = ? AND logger = ? AND task_id IS NOT NULL ",
                "GROUP BY task_id",
            ]
        )
        rows = self._conn.execute(sql, (*file_ids, transition, _TRANSITION_LOGGER)).fetchall()
        return {tid: _epoch_us_to_dt(int(ts)) for tid, ts in rows}

    def all_log_timestamps_for_workspace(
        self,
        workspace_root: Path,
        live_log_path: Path,
    ) -> list[datetime]:
        """Workspace-aware variant of ``all_log_timestamps``.

        Issue #168: every ``[logger]`` log-line timestamp from every
        orch-log ``file_id`` in the workspace, ascending.
        """
        file_ids = self._orch_log_file_ids_for_workspace(workspace_root, live_log_path)
        if not file_ids:
            return []
        placeholders = ",".join("?" for _ in file_ids)
        sql = "".join(
            [
                "SELECT ts_epoch_us FROM orch_log_events WHERE file_id IN (",
                placeholders,
                ") ORDER BY ts_epoch_us ASC",
            ]
        )
        rows = self._conn.execute(sql, tuple(file_ids)).fetchall()
        return [_epoch_us_to_dt(int(r[0])) for r in rows]

    def non_noise_log_timestamps_for_workspace(
        self,
        workspace_root: Path,
        live_log_path: Path,
        noise_logger: str,
    ) -> list[datetime]:
        """Workspace-aware variant of ``non_noise_log_timestamps``.

        Issue #168: drops the noise logger's entries across the union of
        orch-log shards + live log; returns the remaining timestamps
        ascending. Used by the session-boundary detector.
        """
        file_ids = self._orch_log_file_ids_for_workspace(workspace_root, live_log_path)
        if not file_ids:
            return []
        placeholders = ",".join("?" for _ in file_ids)
        sql = "".join(
            [
                "SELECT ts_epoch_us FROM orch_log_events WHERE file_id IN (",
                placeholders,
                ") AND logger != ? ORDER BY ts_epoch_us ASC",
            ]
        )
        rows = self._conn.execute(sql, (*file_ids, noise_logger)).fetchall()
        return [_epoch_us_to_dt(int(r[0])) for r in rows]

    def task_transition_times(self, log_path: Path, transition: str) -> dict[str, datetime]:
        """Return the most-recent timestamp per task_id for the given transition in ``log_path``.

        Equivalent to the existing ``_DONE_RE.finditer`` /
        ``_PROGRESS_RE.finditer`` loops in report.py: each task
        contributes its newest matching line within the named log file.
        The ``MAX(ts_epoch_us) GROUP BY task_id`` matches the
        assignment-overwrites-earlier semantic of the original
        dict-build.

        Issue #329 FR-1a: see ``task_transition_times_for_workspace`` for the
        ``logger = _TRANSITION_LOGGER`` predicate rationale; the same
        invariant applies here for the single-file case.
        """
        file_id = self._file_id_for(log_path)
        if file_id is None:
            return {}
        rows = self._conn.execute(
            "SELECT task_id, MAX(ts_epoch_us) FROM orch_log_events "
            "WHERE file_id = ? AND transition = ? AND logger = ? AND task_id IS NOT NULL "
            "GROUP BY task_id",
            (file_id, transition, _TRANSITION_LOGGER),
        ).fetchall()
        return {tid: _epoch_us_to_dt(int(ts)) for tid, ts in rows}

    def all_log_timestamps(self, log_path: Path) -> list[datetime]:
        """Every ``[logger]`` log-line timestamp in ``log_path``, ascending. Window-end source."""
        file_id = self._file_id_for(log_path)
        if file_id is None:
            return []
        rows = self._conn.execute(
            "SELECT ts_epoch_us FROM orch_log_events WHERE file_id = ? ORDER BY ts_epoch_us ASC",
            (file_id,),
        ).fetchall()
        return [_epoch_us_to_dt(int(r[0])) for r in rows]

    def non_noise_log_timestamps(self, log_path: Path, noise_logger: str) -> list[datetime]:
        """Every ``[logger]`` timestamp except the noise-logger ones, ascending.

        Mirrors ``_find_current_session_start``'s walk: drops entries
        from the noise logger that fires on every CLI tick (which would
        otherwise dominate gap detection) and returns the remaining
        timestamps in ascending order.
        """
        file_id = self._file_id_for(log_path)
        if file_id is None:
            return []
        rows = self._conn.execute(
            "SELECT ts_epoch_us FROM orch_log_events WHERE file_id = ? AND logger != ? ORDER BY ts_epoch_us ASC",
            (file_id, noise_logger),
        ).fetchall()
        return [_epoch_us_to_dt(int(r[0])) for r in rows]

    def aggregate_hook_window(self, hook_log_path: Path, window_start: datetime) -> dict[str, int]:
        """Sum hook-entry token + duration columns from ``hook_log_path`` for ``ts >= window_start``.

        Returns a dict matching the ``_empty_totals_acc`` shape so the
        caller can wrap it in ``HookLogTotals`` directly. Equivalent to
        ``_parse_hook_log_metrics`` but served by an indexed range scan
        rather than a full-file re-parse.
        """
        file_id = self._file_id_for(hook_log_path)
        if file_id is None:
            return _row_to_totals_dict([0] * len(_TOTALS_KEYS_ORDERED))
        boundary = _datetime_to_epoch_us(window_start)
        row = self._conn.execute(
            "SELECT "
            "COALESCE(SUM(duration_ms), 0), "
            "COALESCE(SUM(input_tokens), 0), "
            "COALESCE(SUM(output_tokens), 0), "
            "COALESCE(SUM(cache_read_tokens), 0), "
            "COALESCE(SUM(cache_write_5m_tokens), 0), "
            "COALESCE(SUM(cache_write_1h_tokens), 0), "
            "COALESCE(SUM(has_usage), 0), "
            "COALESCE(SUM(is_us_only), 0), "
            "COALESCE(SUM(is_fast), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_1h_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_1h_tokens ELSE 0 END), 0) "
            # NULL ts means the source entry's timestamp was unparseable;
            # mirror legacy ``_entry_in_window`` by including those rows
            # in every window so cost data is not silently dropped.
            "FROM hook_entries WHERE file_id = ? AND (ts_epoch_us IS NULL OR ts_epoch_us >= ?)",
            (file_id, boundary),
        ).fetchone()
        return _row_to_totals_dict(row)

    def aggregate_transcript_window(self, transcript_dir: Path | None, window_start: datetime) -> dict[str, int]:
        """Sum transcript token columns from every cached file under ``transcript_dir``.

        Equivalent to ``_parse_transcript_metrics`` -- a single SQL
        aggregate replacing a directory-walk + JSON-line full re-parse.
        Filters via a subquery JOIN against ``source_files`` whose
        ``path`` lives directly under ``transcript_dir`` (matches the
        ``transcript_dir.glob("*.jsonl")`` selection that the parser
        path uses, so the two implementations agree on the input set).
        Using a subquery rather than an interpolated IN-list keeps the
        SQL a fixed string with only bound parameters, no string
        construction over caller data.
        """
        empty = _row_to_totals_dict([0] * len(_TOTALS_KEYS_ORDERED))
        if transcript_dir is None:
            return empty
        boundary = _datetime_to_epoch_us(window_start)
        row = self._conn.execute(
            "SELECT "
            "0, "  # transcripts contribute no duration_ms
            "COALESCE(SUM(input_tokens), 0), "
            "COALESCE(SUM(output_tokens), 0), "
            "COALESCE(SUM(cache_read_tokens), 0), "
            "COALESCE(SUM(cache_write_5m_tokens), 0), "
            "COALESCE(SUM(cache_write_1h_tokens), 0), "
            "COALESCE(SUM(has_usage), 0), "
            "COALESCE(SUM(is_us_only), 0), "
            "COALESCE(SUM(is_fast), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_1h_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_1h_tokens ELSE 0 END), 0) "
            "FROM transcript_entries "
            "WHERE file_id IN (SELECT file_id FROM source_files WHERE kind = ? AND path LIKE ?) "
            "AND (ts_epoch_us IS NULL OR ts_epoch_us >= ?)",
            (_KIND_TRANSCRIPT, f"{transcript_dir}/%.jsonl", boundary),
        ).fetchone()
        return _row_to_totals_dict(row)

    def aggregate_hook_window_by_model(self, hook_log_path: Path, window_start: datetime) -> dict[str, dict[str, int]]:
        """Per-model variant of :meth:`aggregate_hook_window` (issue #223).

        Returns ``{model_id -> totals_dict}`` where ``totals_dict`` has the
        same shape as the single-bucket ``aggregate_hook_window`` return
        value.  Rows with a NULL ``model`` aggregate under the sentinel
        key ``"<unknown>"`` so legacy data (pre-#223 caches) and any
        future entry that lacks model attribution contribute to a single
        catch-all bucket priced against ``REPORT_DEFAULT_MODEL_RATES``.
        Empty result -> empty dict (no rows in the window).
        """
        file_id = self._file_id_for(hook_log_path)
        if file_id is None:
            return {}
        boundary = _datetime_to_epoch_us(window_start)
        rows = self._conn.execute(
            "SELECT COALESCE(model, ?), "
            "COALESCE(SUM(duration_ms), 0), "
            "COALESCE(SUM(input_tokens), 0), "
            "COALESCE(SUM(output_tokens), 0), "
            "COALESCE(SUM(cache_read_tokens), 0), "
            "COALESCE(SUM(cache_write_5m_tokens), 0), "
            "COALESCE(SUM(cache_write_1h_tokens), 0), "
            "COALESCE(SUM(has_usage), 0), "
            "COALESCE(SUM(is_us_only), 0), "
            "COALESCE(SUM(is_fast), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_1h_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_1h_tokens ELSE 0 END), 0) "
            "FROM hook_entries WHERE file_id = ? AND (ts_epoch_us IS NULL OR ts_epoch_us >= ?) "
            "GROUP BY COALESCE(model, ?)",
            ("<unknown>", file_id, boundary, "<unknown>"),
        ).fetchall()
        return {row[0]: _row_to_totals_dict(row[1:]) for row in rows}

    def aggregate_transcript_window_by_model(
        self, transcript_dir: Path | None, window_start: datetime
    ) -> dict[str, dict[str, int]]:
        """Per-model variant of :meth:`aggregate_transcript_window` (issue #223).

        Returns ``{model_id -> totals_dict}``; NULL model rows aggregate
        under ``"<unknown>"``.  Empty source dir / no rows -> empty dict.
        Transcripts contribute zero duration (the column is hard-coded
        to 0 in the single-bucket aggregator and that semantic is
        preserved here).
        """
        if transcript_dir is None:
            return {}
        boundary = _datetime_to_epoch_us(window_start)
        rows = self._conn.execute(
            "SELECT COALESCE(model, ?), "
            "0, "
            "COALESCE(SUM(input_tokens), 0), "
            "COALESCE(SUM(output_tokens), 0), "
            "COALESCE(SUM(cache_read_tokens), 0), "
            "COALESCE(SUM(cache_write_5m_tokens), 0), "
            "COALESCE(SUM(cache_write_1h_tokens), 0), "
            "COALESCE(SUM(has_usage), 0), "
            "COALESCE(SUM(is_us_only), 0), "
            "COALESCE(SUM(is_fast), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_us_only=1 THEN cache_write_1h_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN input_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN output_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_read_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_5m_tokens ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN is_fast=1 THEN cache_write_1h_tokens ELSE 0 END), 0) "
            "FROM transcript_entries "
            "WHERE file_id IN (SELECT file_id FROM source_files WHERE kind = ? AND path LIKE ?) "
            "AND (ts_epoch_us IS NULL OR ts_epoch_us >= ?) "
            "GROUP BY COALESCE(model, ?)",
            ("<unknown>", _KIND_TRANSCRIPT, f"{transcript_dir}/%.jsonl", boundary, "<unknown>"),
        ).fetchall()
        return {row[0]: _row_to_totals_dict(row[1:]) for row in rows}

    def first_hook_transcript_path(self, hook_log_path: Path) -> str | None:
        """Return the earliest non-null ``transcript_path`` recorded on a hook entry from ``hook_log_path``.

        Mirrors the existing ``_discover_transcript_dir`` walk, which
        returns the parent directory of the first hook entry's
        ``input.transcript_path``. The cache-side equivalent is a
        single SELECT over the indexed table rather than reading the
        whole hook log.
        """
        file_id = self._file_id_for(hook_log_path)
        if file_id is None:
            return None
        # NULLS LAST so a malformed-timestamp entry (ts NULL) doesn't
        # outrank a real one. Within ts_epoch_us ASC the earliest valid
        # timestamp wins -- same first-hit semantic as the legacy
        # ``_discover_transcript_dir`` walk.
        row = self._conn.execute(
            "SELECT transcript_path FROM hook_entries "
            "WHERE file_id = ? AND transcript_path IS NOT NULL "
            "ORDER BY (ts_epoch_us IS NULL), ts_epoch_us ASC LIMIT 1",
            (file_id,),
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Phase 6: render-output snapshot
    # ------------------------------------------------------------------

    def write_snapshot(self, log_path: Path, payload: dict) -> None:
        """Persist a JSON-serialisable rendered-state payload keyed on log mtime+size.

        The snapshot is bound to the orchestrator log's exact byte
        position so a stale snapshot is detected on the next read; if
        the log advances by even one byte the snapshot is treated as
        stale and the caller falls back to the index path.
        """
        if not log_path.is_file():
            return
        stat = log_path.stat()
        body = _json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO report_snapshot "
                "(snapshot_id, log_mtime_ns, log_size_bytes, payload_json, written_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (stat.st_mtime_ns, stat.st_size, body, datetime.now(UTC).isoformat()),
            )

    def read_snapshot(self, log_path: Path) -> dict | None:
        """Return the snapshot payload only when its keys match the log's current state."""
        if not log_path.is_file():
            return None
        stat = log_path.stat()
        row = self._conn.execute(
            "SELECT log_mtime_ns, log_size_bytes, payload_json FROM report_snapshot WHERE snapshot_id = 1"
        ).fetchone()
        if row is None:
            return None
        if int(row[0]) != stat.st_mtime_ns or int(row[1]) != stat.st_size:
            return None
        try:
            payload = _json.loads(row[2])
        except _json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None


# ----------------------------------------------------------------------
# Free-function helpers (shared with the parser path in report.py via
# a thin re-export layer; keeping them module-private here keeps the
# cache schema definition in one file).
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _UsageTokens:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    has_usage: int
    is_us_only: int
    is_fast: int


def _tokens_from_usage(usage: object) -> _UsageTokens:
    """Extract the 5 token columns + per-entry flags from a ``usage`` dict.

    Mirrors ``_extract_usage_totals`` in report.py but returns a
    structured tuple instead of mutating an accumulator. Used at cache
    insertion time to flatten each entry into its column shape.
    """
    if not isinstance(usage, dict):
        return _UsageTokens(0, 0, 0, 0, 0, 0, 0, 0)
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, dict):
        cache_write_5m_tokens = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
        cache_write_1h_tokens = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
    else:
        cache_write_5m_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        cache_write_1h_tokens = 0
    is_us_only = 1 if usage.get("inference_geo") else 0
    is_fast = 1 if usage.get("speed") == "fast" else 0
    return _UsageTokens(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_5m_tokens=cache_write_5m_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        has_usage=1,
        is_us_only=is_us_only,
        is_fast=is_fast,
    )


_ROLE_ORCHESTRATOR = "orchestrator"


def _role_from_attribution(raw: object) -> str:
    """Mirror of ``_role_for_entry`` in report.py.

    Kept private to event_index so the cache module is self-contained;
    the same string-shape is asserted by the parity test that compares
    indexed aggregates against the reference parser output.
    """
    if not isinstance(raw, str) or not raw:
        return _ROLE_ORCHESTRATOR
    base = raw.split(":", 1)[1] if ":" in raw else raw
    return base.replace("-reviewer", "_review").replace("-", "_")


def _orch_ts_to_epoch_us(raw: str) -> int:
    """Parse the orchestrator-log ``YYYY-MM-DDTHH:MM:SSZ`` form -> microseconds."""
    dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    return _datetime_to_epoch_us(dt)


def _iso_to_epoch_us(raw: str) -> int:
    """Parse general ISO-8601 (hooks + transcripts) -> microseconds.

    Supports the trailing-Z form on Python 3.11+ (the project's pinned
    interpreter is 3.12) by replacing it with ``+00:00`` before
    handing to ``fromisoformat``. Naive strings (no offset) are treated
    as UTC for backward-compat with any caller that still emits them.
    """
    aware = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if aware.tzinfo is None:
        aware = aware.replace(tzinfo=UTC)
    return _datetime_to_epoch_us(aware)


def _datetime_to_epoch_us(dt: datetime) -> int:
    """Convert a tz-aware (or naive=UTC) datetime to integer microseconds since epoch."""
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    # ``datetime.timestamp()`` returns a float; round to microseconds.
    return int(aware.timestamp() * 1_000_000)


def _epoch_us_to_dt(epoch_us: int) -> datetime:
    """Convert integer microseconds-since-epoch to a tz-aware UTC datetime."""
    return datetime.fromtimestamp(epoch_us / 1_000_000, tz=UTC).replace(microsecond=epoch_us % 1_000_000)


_TOTALS_KEYS_ORDERED = (
    "total_duration_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "entries_with_usage",
    "entries_us_geo",
    "entries_fast_mode",
    "us_only_input_tokens",
    "us_only_output_tokens",
    "us_only_cache_read_tokens",
    "us_only_cache_write_5m_tokens",
    "us_only_cache_write_1h_tokens",
    "fast_input_tokens",
    "fast_output_tokens",
    "fast_cache_read_tokens",
    "fast_cache_write_5m_tokens",
    "fast_cache_write_1h_tokens",
)


def _row_to_totals_dict(row: Iterable) -> dict[str, int]:
    """Convert a 19-column SQL aggregate row into the standard accumulator shape."""
    values = list(row)
    if len(values) != len(_TOTALS_KEYS_ORDERED):
        raise ValueError(
            f"event_index aggregate row width mismatch: got {len(values)}, expected {len(_TOTALS_KEYS_ORDERED)}"
        )
    return dict(zip(_TOTALS_KEYS_ORDERED, (int(v or 0) for v in values), strict=True))
