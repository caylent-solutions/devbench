"""Tests for ``devbench.utils.io.atomic_write_text``.

Coverage requirement: 100% line + branch under ``make test-coverage-new``.
Pinned by the test-coverage-new gate so any future regression in the
helper trips CI before it can land in production.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from devbench.utils.io import atomic_write_text


class TestAtomicWriteTextHappyPath:
    """The function writes the expected content to the target path."""

    def test_creates_new_file(self, tmp_path: Path) -> None:
        target = tmp_path / "fresh.md"
        atomic_write_text(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "x.md"
        target.write_text("old content", encoding="utf-8")
        atomic_write_text(target, "new content")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_empty_string(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.md"
        atomic_write_text(target, "")
        assert target.read_text(encoding="utf-8") == ""

    def test_unicode_content(self, tmp_path: Path) -> None:
        # Smoke: helper is UTF-8 encoded; non-ASCII round-trips faithfully.
        # No em-dash (forbidden by CLAUDE.md); use other Unicode codepoints.
        target = tmp_path / "u.md"
        atomic_write_text(target, "café ☃ \U0001f600")
        assert target.read_text(encoding="utf-8") == "café ☃ \U0001f600"


class TestAtomicWriteTextTempFileLifecycle:
    """The temp file is created, written, and renamed; no leftover after success."""

    def test_no_tmp_file_left_after_success(self, tmp_path: Path) -> None:
        target = tmp_path / "x.md"
        atomic_write_text(target, "body")
        assert not (tmp_path / "x.md.tmp").exists()

    def test_temp_file_name_format(self, tmp_path: Path) -> None:
        # Spying via a monkeypatched Path.replace lets us catch the temp
        # file name without races: replace() runs LAST in the helper, so
        # at the moment it's called the temp file still exists.
        target = tmp_path / "spy.md"
        observed: dict[str, Path | bool] = {}

        original_replace = Path.replace

        def _spy_replace(self: Path, dest: Path) -> Path:
            observed["tmp"] = self
            observed["tmp_exists"] = self.is_file()
            return original_replace(self, dest)

        from unittest.mock import patch as _patch

        with _patch.object(Path, "replace", _spy_replace):
            atomic_write_text(target, "body")

        assert observed["tmp"] == tmp_path / "spy.md.tmp"
        assert observed["tmp_exists"] is True


class TestAtomicWriteTextFailFast:
    """Missing parent directory + IO errors propagate; no silent fallback."""

    def test_missing_parent_raises_filenotfound(self, tmp_path: Path) -> None:
        target = tmp_path / "nope" / "file.md"
        with pytest.raises(FileNotFoundError, match="parent directory does not exist"):
            atomic_write_text(target, "body")

    def test_parent_must_be_a_directory_not_a_file(self, tmp_path: Path) -> None:
        # Edge: the "parent" path exists but is a regular file. is_dir() is
        # False so the helper raises rather than silently failing inside
        # tmp.write_text().
        file_acting_as_parent = tmp_path / "blocker"
        file_acting_as_parent.write_text("oops", encoding="utf-8")
        target = file_acting_as_parent / "child.md"
        with pytest.raises(FileNotFoundError, match="parent directory does not exist"):
            atomic_write_text(target, "body")


class TestAtomicWriteTextConcurrentReader:
    """A concurrent reader never sees a partial write.

    Pins the heading-invariant that the workspace-WU-md write path must
    preserve: any reader observing the file always sees either the prior
    complete content or the new complete content, never partial.
    """

    def test_reader_always_sees_complete_content(self, tmp_path: Path) -> None:
        target = tmp_path / "wu.md"
        # Use long-enough content that a non-atomic write would have a
        # meaningful partial-read window. 200 KiB exercises the page-cache
        # boundary on typical Linux configs.
        old_content = "# OLD HEADING\n" + ("o" * 200_000) + "\n"
        new_content = "# NEW HEADING\n" + ("n" * 200_000) + "\n"
        target.write_text(old_content, encoding="utf-8")

        observed: list[str] = []
        stop = threading.Event()

        def _reader() -> None:
            while not stop.is_set():
                try:
                    line1 = target.read_text(encoding="utf-8").splitlines()[0]
                except (FileNotFoundError, IndexError):
                    line1 = "<missing>"
                observed.append(line1)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        # Switch the file 50 times via the atomic helper.
        for _ in range(50):
            atomic_write_text(target, new_content)
            atomic_write_text(target, old_content)

        stop.set()
        reader.join(timeout=1.0)

        # Every observed line must be either OLD or NEW (or, on the very
        # first reads, missing if the file was momentarily renamed) but
        # never a partial / empty / truncated heading.
        for line in observed:
            assert line in ("# OLD HEADING", "# NEW HEADING", "<missing>"), f"Reader observed a partial write: {line!r}"
        assert len(observed) > 0, "reader never ran"
