"""Tests for ``devbench.reporting.archive`` (issue #162 Phase 7, ADR-21).

Pins the round-trip JSONL <-> Parquet parity, the missing-dep
``ArchiveDependencyMissingError`` error path, and the destination layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from devbench.reporting.archive import (
    LEGACY_DIR_NAME,
    ArchiveDependencyMissingError,
    archive_path,
    archive_session,
    read_archived_session,
)


class TestArchivePath:
    def test_destination_under_logs_legacy(self, tmp_path: Path) -> None:
        path = archive_path(tmp_path, "session-abc")
        assert path == tmp_path / LEGACY_DIR_NAME / "session-abc.parquet"

    def test_filename_carries_session_id(self, tmp_path: Path) -> None:
        path = archive_path(tmp_path, "abc-123")
        assert path.name == "abc-123.parquet"


class TestArchiveSession:
    def test_raises_when_log_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            archive_session(tmp_path, "session-id", tmp_path / "nope.log")

    def test_creates_legacy_directory_if_missing(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text('{"event": "set-status", "id": "E0-F1-S1-T1"}\n{"event": "set-status", "id": "E0-F1-S1-T2"}\n')
        archive_session(tmp_path, "s1", log)
        assert (tmp_path / LEGACY_DIR_NAME).is_dir()

    def test_writes_parquet_at_canonical_path(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text('{"event": "x"}\n')
        out = archive_session(tmp_path, "session-abc", log)
        assert out == archive_path(tmp_path, "session-abc")
        assert out.is_file()
        assert out.read_bytes()[:4] == b"PAR1"

    def test_round_trip_preserves_raw_lines(self, tmp_path: Path) -> None:
        """Source-of-truth contract: JSONL -> Parquet -> JSONL must be
        byte-faithful at the line level."""
        log = tmp_path / "log"
        original_lines = [
            '{"event": "set-status", "id": "E0-F1-S1-T1", "status": "in-progress"}',
            '{"event": "set-status", "id": "E0-F1-S1-T1", "status": "done"}',
            "plain text line that is not json",
        ]
        log.write_text("\n".join(original_lines) + "\n")

        out = archive_session(tmp_path, "session-abc", log)
        recovered = list(read_archived_session(out))
        assert recovered == original_lines


class TestReadArchivedSession:
    def test_raises_when_archive_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            list(read_archived_session(tmp_path / "nope.parquet"))

    def test_yields_in_source_order(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        original_lines = [f"line-{i}" for i in range(5)]
        log.write_text("\n".join(original_lines) + "\n")

        archive_session(tmp_path, "s1", log)
        recovered = list(read_archived_session(archive_path(tmp_path, "s1")))
        assert recovered == original_lines


class TestArchiveDependencyMissingError:
    """When ``pyarrow`` isn't installed, every public function raises a
    structured error pointing at the install command."""

    def test_archive_session_raises_when_pyarrow_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log = tmp_path / "log"
        log.write_text('{"event": "x"}\n')

        for module_name in list(sys.modules):
            if module_name.startswith("pyarrow"):
                monkeypatch.delitem(sys.modules, module_name)

        class _BlockPyarrow:
            def find_spec(self, name: str, path: object | None = None, target: object | None = None) -> object:
                if name.startswith("pyarrow"):
                    raise ImportError(f"simulated missing {name}")
                return None

        monkeypatch.setattr(sys, "meta_path", [_BlockPyarrow(), *sys.meta_path])

        with pytest.raises(ArchiveDependencyMissingError) as exc_info:
            archive_session(tmp_path, "s1", log)
        assert "pip install devbench[archive]" in str(exc_info.value)

    def test_read_archived_session_raises_when_pyarrow_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "log"
        log.write_text('{"event": "x"}\n')
        out = archive_session(tmp_path, "s1", log)

        for module_name in list(sys.modules):
            if module_name.startswith("pyarrow"):
                monkeypatch.delitem(sys.modules, module_name)

        class _BlockPyarrow:
            def find_spec(self, name: str, path: object | None = None, target: object | None = None) -> object:
                if name.startswith("pyarrow"):
                    raise ImportError(f"simulated missing {name}")
                return None

        monkeypatch.setattr(sys, "meta_path", [_BlockPyarrow(), *sys.meta_path])

        with pytest.raises(ArchiveDependencyMissingError) as exc_info:
            list(read_archived_session(out))
        assert "pip install devbench[archive]" in str(exc_info.value)

    def test_error_message_names_operation(self) -> None:
        """The ArchiveDependencyMissingError message includes the install
        command verbatim so operators can paste the fix from a CI log."""
        err = ArchiveDependencyMissingError("test-op")
        assert "test-op" in str(err)
        assert "pip install devbench[archive]" in str(err)
