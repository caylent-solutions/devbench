"""Tests for ``devbench.plugin_helpers.backlog_post_processor``.

Issue #221 A11, A12, A13: each post-processing pass must be idempotent
(re-running on already-clean input yields zero modifications) and must
not corrupt files lacking the section it operates on.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from devbench.plugin_helpers import backlog_post_processor as bpp


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _iter_work_unit_files
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIterWorkUnitFiles:
    """The iterator skips BACKLOG.md and any path under config/."""

    def test_yields_only_work_units(self, tmp_path: Path) -> None:
        _write(tmp_path / "BACKLOG.md", "# index\n")
        # A .md file under config/ -- skipped because 'config' is in path.parts.
        _write(tmp_path / "config" / "notes.md", "# config notes\n")
        _write(tmp_path / "E1" / "E1-F1-S1-T1.md", "# task\n")
        _write(tmp_path / "E1" / "E1.md", "# epic\n")
        files = list(bpp._iter_work_unit_files(tmp_path))
        names = {f.name for f in files}
        assert "BACKLOG.md" not in names
        assert "notes.md" not in names
        assert "E1-F1-S1-T1.md" in names
        assert "E1.md" in names


# ---------------------------------------------------------------------------
# sanitize_markdown_pipes_in_manifest -- A12
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSanitizeMarkdownPipes:
    """A12: escape raw ``|`` inside Manifest annotation cells."""

    _BASE = """\
        # E1-F1-S1-T1: Title

        ## Status: draft

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/foo.py` | Add prose with run cmd | grep -v debug shell pipeline. |
        | `tests/test_foo.py` | Add unit tests. |

        ## Next Section

        body.
        """

    def test_escapes_inner_pipes(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "E1-F1-S1-T1.md", self._BASE)
        count = bpp.sanitize_markdown_pipes_in_manifest(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        assert "run cmd \\| grep -v debug" in text

    def test_idempotent(self, tmp_path: Path) -> None:
        _write(tmp_path / "E1-F1-S1-T1.md", self._BASE)
        first = bpp.sanitize_markdown_pipes_in_manifest(tmp_path)
        second = bpp.sanitize_markdown_pipes_in_manifest(tmp_path)
        assert first == 1
        assert second == 0

    def test_skips_files_without_manifest(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "E1.md",
            """\
            # E1: Epic

            ## Status: draft

            (no Manifest section.)
            """,
        )
        assert bpp.sanitize_markdown_pipes_in_manifest(tmp_path) == 0

    def test_leaves_clean_manifest_unchanged(self, tmp_path: Path) -> None:
        wu = _write(
            tmp_path / "E1-F1-S1-T1.md",
            """\
            # E1-F1-S1-T1: Title

            ## Status: draft

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/foo.py` | Add the foo. |

            ## Next
            """,
        )
        before = wu.read_text(encoding="utf-8")
        assert bpp.sanitize_markdown_pipes_in_manifest(tmp_path) == 0
        assert wu.read_text(encoding="utf-8") == before


@pytest.mark.unit
class TestEscapeInnerPipes:
    """Direct tests for the cell-level escape helper."""

    def test_no_inner_pipes_passthrough(self) -> None:
        assert bpp._escape_inner_pipes("| a | b |") == "| a | b |"

    def test_already_escaped_left_alone(self) -> None:
        # The row has 3 unescaped pipes; ``\|`` is escaped already.
        assert bpp._escape_inner_pipes(r"| a | b \| c |") == r"| a | b \| c |"

    def test_extra_inner_pipe_gets_escaped(self) -> None:
        result = bpp._escape_inner_pipes("| a | b | c |")
        # First, second, and last pipes are preserved; the third is escaped.
        assert result == r"| a | b \| c |"

    def test_mixed_escaped_and_unescaped_inner_pipes(self) -> None:
        """Row with an already-escaped pipe AND extra unescaped pipes:
        the escaped pipe is preserved verbatim and unescaped extras get escaped."""
        # 5 unescaped pipes (positions 0, 4, 10, 14, 22) + 1 already-escaped pipe.
        row = r"| a | b \| c | d | e |"
        # Unescaped pipe positions: 0 (lead), 4 (col-sep), 12 (inner), 16 (inner), 21 (trail).
        # _escape_inner_pipes preserves 0/4/last and escapes the two inner ones; the
        # pre-existing ``\|`` stays as-is.
        result = bpp._escape_inner_pipes(row)
        assert r"\| c" in result  # original escape preserved
        assert result.startswith("| a | b ")
        assert result.endswith(" |")
        # The two extra inner pipes (at "| d" and "| e") are now escaped.
        assert r"\| d" in result
        assert r"\| e" in result


# ---------------------------------------------------------------------------
# dedupe_manifest_rows -- A13
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupeManifestRows:
    """A13: collapse duplicate Manifest rows down to one entry."""

    _DUP = """\
        # E1-F1-S1-T1: Title

        ## Status: draft

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/foo.py` | Add foo. |
        | `tests/test_foo.py` | Add tests. |
        | `src/foo.py` | Add foo. |

        ## Next
        """

    def test_dedupes(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._DUP)
        assert bpp.dedupe_manifest_rows(tmp_path) == 1
        text = wu.read_text(encoding="utf-8")
        # First occurrence kept; second collapsed.
        assert text.count("`src/foo.py`") == 1

    def test_idempotent(self, tmp_path: Path) -> None:
        _write(tmp_path / "T.md", self._DUP)
        bpp.dedupe_manifest_rows(tmp_path)
        assert bpp.dedupe_manifest_rows(tmp_path) == 0

    def test_no_dup_no_modification(self, tmp_path: Path) -> None:
        wu = _write(
            tmp_path / "T.md",
            """\
            # E1-F1-S1-T1: Title

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/a.py` | Add a. |
            | `src/b.py` | Add b. |

            ## Next
            """,
        )
        before = wu.read_text(encoding="utf-8")
        assert bpp.dedupe_manifest_rows(tmp_path) == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_skip_files_without_manifest(self, tmp_path: Path) -> None:
        _write(tmp_path / "E.md", "# Epic\n\n## Status: draft\n")
        assert bpp.dedupe_manifest_rows(tmp_path) == 0


@pytest.mark.unit
class TestDedupeBlockRows:
    """Direct tests for the block-level helper."""

    def test_dedupes_data_rows_only(self) -> None:
        block = textwrap.dedent("""\
            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `a.py` | one |
            | `a.py` | one |
            """)
        new, changed = bpp._dedupe_block_rows(block)
        assert changed is True
        assert new.count("`a.py`") == 1

    def test_no_data_rows_no_change(self) -> None:
        block = "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
        new, changed = bpp._dedupe_block_rows(block)
        assert changed is False
        assert new == block

    def test_non_table_lines_left_alone(self) -> None:
        block = textwrap.dedent("""\
            ## Changes Manifest

            Note before the table.

            | File | Change |
            |------|--------|
            | `a.py` | one |
            """)
        new, changed = bpp._dedupe_block_rows(block)
        assert changed is False
        assert "Note before the table." in new

    def test_pre_separator_pipes_preserved(self) -> None:
        """Pipe-shaped lines appearing BEFORE the table separator are left alone.

        Some authors put a leading ``| File | Change |`` header on one
        line and the ``|---|---|`` separator on the next; we must not
        dedupe them.
        """
        block = "## Changes Manifest\n| File | Change |\n| File | Change |\n|---|---|\n"
        new, _changed = bpp._dedupe_block_rows(block)
        # The two pre-separator pipe lines remain.
        assert new.count("| File | Change |\n") == 2

    def test_invalid_row_pipe_format_left_alone(self) -> None:
        """A pipe-shaped line that does NOT match the 2-column regex is preserved."""
        block = textwrap.dedent("""\
            ## Changes Manifest

            | File | Change |
            |------|--------|
            | malformed row missing the second pipe
            """)
        new, changed = bpp._dedupe_block_rows(block)
        assert changed is False
        assert "malformed row" in new


# ---------------------------------------------------------------------------
# suffix_ref_on_orphan_paths -- A11
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuffixRefOnOrphanPaths:
    """A11: add ``(ref)`` to backtick-quoted paths in AC/DoD that are not
    in the Manifest."""

    _BASE = """\
        # E1-F1-S1-T1: Title

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/foo.py` | Add foo. |

        ## Acceptance Criteria

        - AC-1: writes a new module `src/foo.py` exporting bar.
        - AC-2: integrates with the existing `src/legacy.py` interface.

        ## Definition of Done

        - DoD-1: tests in `tests/test_foo.py` pass.

        ## Next
        """

    def test_suffixes_orphan(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._BASE)
        count = bpp.suffix_ref_on_orphan_paths(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        # In-Manifest path stays bare.
        assert "`src/foo.py` exporting" in text
        # Orphan paths get ``(ref)``.
        assert "`src/legacy.py` (ref)" in text
        assert "`tests/test_foo.py` (ref)" in text

    def test_idempotent(self, tmp_path: Path) -> None:
        _write(tmp_path / "T.md", self._BASE)
        bpp.suffix_ref_on_orphan_paths(tmp_path)
        assert bpp.suffix_ref_on_orphan_paths(tmp_path) == 0

    def test_already_suffixed_left_alone(self, tmp_path: Path) -> None:
        already = """\
            # T

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/a.py` | Add. |

            ## Acceptance Criteria

            - AC-1: cite `src/legacy.py` (ref) as the parent.

            ## Next
            """
        wu = _write(tmp_path / "T.md", already)
        count = bpp.suffix_ref_on_orphan_paths(tmp_path)
        before = textwrap.dedent(already).lstrip("\n")
        assert count == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_skips_files_without_ac_or_dod(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "E.md",
            """\
            # E1

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/a.py` | Add. |

            ## Status
            """,
        )
        assert bpp.suffix_ref_on_orphan_paths(tmp_path) == 0

    def test_skip_files_without_manifest(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "X.md",
            """\
            # X

            ## Acceptance Criteria

            - AC-1: see `src/y.py`.
            """,
        )
        assert bpp.suffix_ref_on_orphan_paths(tmp_path) == 0

    def test_pre_supplied_manifest_paths_used(self, tmp_path: Path) -> None:
        """When ``manifest_paths`` is passed, it overrides per-file extraction."""
        wu = _write(tmp_path / "T.md", self._BASE)
        supplied = {wu: {"src/legacy.py", "tests/test_foo.py", "src/foo.py"}}
        count = bpp.suffix_ref_on_orphan_paths(tmp_path, manifest_paths=supplied)
        # With the broader supplied set, every token is in-Manifest -- zero changes.
        assert count == 0


# ---------------------------------------------------------------------------
# run_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunAll:
    """``run_all`` returns a ``{pass_name: count}`` mapping."""

    def test_returns_count_per_pass(self, tmp_path: Path) -> None:
        result = bpp.run_all(tmp_path)
        assert result == {
            "sanitize_markdown_pipes_in_manifest": 0,
            "dedupe_manifest_rows": 0,
            "suffix_ref_on_orphan_paths": 0,
        }


# ---------------------------------------------------------------------------
# Helper functions: _split_manifest_section / _extract_manifest_paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitManifestSection:
    """Section-splitter edge cases."""

    def test_no_manifest_returns_none(self) -> None:
        assert bpp._split_manifest_section("# Title only\n") is None

    def test_manifest_at_eof(self) -> None:
        text = "# T\n\n## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a.py` | one |\n"
        result = bpp._split_manifest_section(text)
        assert result is not None
        before, block, after = result
        assert "## Changes Manifest" in block
        assert after == ""


@pytest.mark.unit
class TestExtractManifestPaths:
    """Manifest-path extractor edge cases."""

    def test_no_manifest_returns_empty_set(self) -> None:
        assert bpp._extract_manifest_paths("# T\n\n## Status: draft\n") == set()

    def test_extracts_paths_skipping_header(self) -> None:
        text = textwrap.dedent("""\
            # T

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/a.py` | one |
            | `src/b.py` | two |
            """)
        assert bpp._extract_manifest_paths(text) == {"src/a.py", "src/b.py"}

    def test_skips_malformed_rows(self) -> None:
        text = textwrap.dedent("""\
            # T

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | malformed
            | `src/a.py` | one |
            """)
        assert bpp._extract_manifest_paths(text) == {"src/a.py"}
