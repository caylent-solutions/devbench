"""Tests for ``devbench.plugin_helpers.backlog_post_processor``.

Issue #221 A11, A12, A13: each post-processing pass must be idempotent
(re-running on already-clean input yields zero modifications) and must
not corrupt files lacking the section it operates on.
"""

from __future__ import annotations

import re
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
# normalize_manifest_column_count -- issue #227
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeManifestColumnCount:
    """Issue #227: collapse N-col Manifest tables to canonical 2-col form."""

    _THREE_COL_REPO = """\
        # E1-F1-S1-T1: Title

        ## Status: in-queue

        ## Changes Manifest

        | Repo | Path | Action |
        |------|------|--------|
        | caylent/cpk | .github/workflows/audit.yml | modify |
        | caylent/cpk | docs/contributing.md | modify |

        ## Next Section
        """

    _THREE_COL_FILE_FIRST = """\
        # E1-F1-S1-T1: Title

        ## Status: in-queue

        ## Changes Manifest

        | File | Change | Notes |
        |------|--------|-------|
        | `src/foo.py` | add | new feature |
        | `tests/test_foo.py` | add | covers AC-1 |

        ## Next
        """

    _FOUR_COL = """\
        # E1-F1-S1-T1: Title

        ## Status: in-queue

        ## Changes Manifest

        | Repo | Path | Action | Notes |
        |------|------|--------|-------|
        | caylent/cpk | audit.yml | modify | install pkg fix |

        ## Next
        """

    _CANONICAL = """\
        # E1-F1-S1-T1: Title

        ## Status: in-queue

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/foo.py` | add |

        ## Next
        """

    def test_3col_repo_path_action_to_canonical(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._THREE_COL_REPO)
        count = bpp.normalize_manifest_column_count(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        # Canonical header.
        assert "| File | Change |" in text
        assert "|------|--------|" in text
        # Row preserves repo + path + action.
        assert "| `caylent/cpk -- .github/workflows/audit.yml` | modify |" in text
        assert "| `caylent/cpk -- docs/contributing.md` | modify |" in text
        # Original headers gone.
        assert "| Repo | Path | Action |" not in text

    def test_3col_file_change_notes_to_canonical(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._THREE_COL_FILE_FIRST)
        count = bpp.normalize_manifest_column_count(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        assert "| File | Change |" in text
        # Notes appended to Change with ' -- '.
        assert "| `src/foo.py` | add -- new feature |" in text
        assert "| `tests/test_foo.py` | add -- covers AC-1 |" in text

    def test_4col_collapses_losslessly(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._FOUR_COL)
        count = bpp.normalize_manifest_column_count(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        # Repo + Path merge, then Action -- Notes in Change.
        assert "| `caylent/cpk -- audit.yml` | modify -- install pkg fix |" in text

    def test_canonical_unchanged(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._CANONICAL)
        before = wu.read_text(encoding="utf-8")
        count = bpp.normalize_manifest_column_count(tmp_path)
        assert count == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_idempotent(self, tmp_path: Path) -> None:
        _write(tmp_path / "T.md", self._THREE_COL_REPO)
        first = bpp.normalize_manifest_column_count(tmp_path)
        second = bpp.normalize_manifest_column_count(tmp_path)
        assert first == 1
        assert second == 0

    def test_post_normalize_parse_manifest_succeeds(self, tmp_path: Path) -> None:
        """After normalisation, the public parse_manifest accepts the file."""
        from devbench.backlog.manifest import parse_manifest

        wu = _write(tmp_path / "T.md", self._THREE_COL_REPO)
        bpp.normalize_manifest_column_count(tmp_path)
        rows = parse_manifest(wu.read_text(encoding="utf-8"))
        assert len(rows) == 2
        assert rows[0].file == "caylent/cpk -- .github/workflows/audit.yml"
        assert rows[0].change == "modify"

    def test_skips_files_without_manifest(self, tmp_path: Path) -> None:
        _write(tmp_path / "E.md", "# E1: Epic\n\n## Status: in-queue\n\n(no Manifest.)\n")
        assert bpp.normalize_manifest_column_count(tmp_path) == 0

    def test_terminal_status_skipped_by_default(self, tmp_path: Path) -> None:
        content = self._THREE_COL_REPO.replace("## Status: in-queue", "## Status: done")
        wu = _write(tmp_path / "T.md", content)
        before = wu.read_text(encoding="utf-8")
        assert bpp.normalize_manifest_column_count(tmp_path) == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_scope_paths_honoured(self, tmp_path: Path) -> None:
        in_scope = _write(tmp_path / "E2" / "E2-F1-S1-T1.md", self._THREE_COL_REPO)
        out_of_scope = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", self._THREE_COL_REPO)
        out_before = out_of_scope.read_text(encoding="utf-8")
        count = bpp.normalize_manifest_column_count(tmp_path, scope_paths=[tmp_path / "E2"])
        assert count == 1
        # In-scope file collapsed.
        assert "| File | Change |" in in_scope.read_text(encoding="utf-8")
        # Out-of-scope file unchanged.
        assert out_of_scope.read_text(encoding="utf-8") == out_before


@pytest.mark.unit
class TestSplitRowCells:
    """Direct tests for the cell splitter that honours backslash-escaped pipes."""

    def test_two_cells(self) -> None:
        assert bpp._split_row_cells("| a | b |") == ["a", "b"]

    def test_three_cells(self) -> None:
        assert bpp._split_row_cells("| a | b | c |") == ["a", "b", "c"]

    def test_escaped_pipe_kept_inside_cell(self) -> None:
        assert bpp._split_row_cells(r"| cmd \| grep | desc |") == ["cmd | grep", "desc"]

    def test_missing_leading_pipe_returns_none(self) -> None:
        assert bpp._split_row_cells("a | b |") is None

    def test_missing_trailing_pipe_returns_none(self) -> None:
        assert bpp._split_row_cells("| a | b") is None

    def test_empty_cells_kept(self) -> None:
        assert bpp._split_row_cells("|  |  |") == ["", ""]


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

    def test_quoted_heading_in_prose_does_not_hijack_bounds(self, tmp_path: Path) -> None:
        """Issue #337: heading text QUOTED in another section's prose must not
        move the section bounds. Pre-fix, the unanchored substring search
        anchored 'Acceptance Criteria' at the Description's backtick quotation
        and the pass suffixed a Code Standards path token far outside AC/DoD.
        """
        quoted = """\
            # E1-F1-S1-T1: Title

            ## Description

            The new rule requires every task `## Acceptance Criteria` line to
            carry the tag; an auto-ticked `## Definition of Done` item is never
            accepted as satisfaction.

            ### Code Standards

            See `/workspace/CLAUDE.md` for the full engineering standards.

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/foo.py` | Add foo. |

            ## Acceptance Criteria

            - AC-1: integrates with the existing `src/legacy.py` interface.

            ## Definition of Done

            - DoD-1: tests in `tests/test_foo.py` pass.

            ## Next
            """
        wu = _write(tmp_path / "T.md", quoted)
        count = bpp.suffix_ref_on_orphan_paths(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        # The Code Standards token is OUTSIDE Acceptance Criteria / Definition
        # of Done and must stay bare.
        assert "`/workspace/CLAUDE.md` for the full" in text
        assert "`/workspace/CLAUDE.md` (ref)" not in text
        # Genuine orphans inside the REAL sections still get the suffix.
        assert "`src/legacy.py` (ref)" in text
        assert "`tests/test_foo.py` (ref)" in text

    def test_only_quoted_heading_and_no_real_section_is_untouched(self, tmp_path: Path) -> None:
        """Issue #337: a file that only QUOTES the heading strings has no AC or
        DoD section, so the pass must leave it byte-identical."""
        quoted_only = """\
            # E1-F1-S1-T1: Title

            ## Description

            Discusses the `## Acceptance Criteria` grammar and the
            `## Definition of Done` auto-tick behavior at `src/legacy.py`.

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/foo.py` | Add foo. |

            ## Next
            """
        wu = _write(tmp_path / "T.md", quoted_only)
        before = wu.read_text(encoding="utf-8")
        count = bpp.suffix_ref_on_orphan_paths(tmp_path)
        assert count == 0
        assert wu.read_text(encoding="utf-8") == before

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
# normalize_dep_ids -- issue #229
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeDepIds:
    """Issue #229: rewrite slug-form dep IDs to canonical regex form."""

    _SLUG_DEPS = """\
        # E17-F1-S1-T1: Title

        ## Status: in-queue

        ## Dependencies

        | ID | Title | Status |
        |----|-------|--------|
        | E16-test-cleanup | E16: Test cleanup for `feat/...` (PR #60) | done |
        | E15-impl-cleanup | E15: Implementation cleanup | done |

        ### Depends On This

        | ID | Title | Status |
        |----|-------|--------|
        | E20-F1-S1-T1-verification | E20-F1-S1-T1: Cross-repo gate | in-queue |

        ## Next
        """

    _CANONICAL_DEPS = """\
        # E17-F1-S1-T1: Title

        ## Status: in-queue

        ## Dependencies

        | ID | Title | Status |
        |----|-------|--------|
        | E16 | E16: Test cleanup | done |

        ### Depends On This

        | ID | Title | Status |
        |----|-------|--------|
        | none | | |

        ## Next
        """

    def test_slug_form_rewritten(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._SLUG_DEPS)
        count = bpp.normalize_dep_ids(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        # Slug suffix stripped to canonical prefix.
        assert "| E16 | E16: Test cleanup for `feat/...` (PR #60) | done |" in text
        assert "| E15 | E15: Implementation cleanup | done |" in text
        # ``### Depends On This`` block also rewritten.
        assert "| E20-F1-S1-T1 | E20-F1-S1-T1: Cross-repo gate | in-queue |" in text
        # Original slug strings gone.
        assert "E16-test-cleanup" not in text
        assert "E15-impl-cleanup" not in text
        assert "E20-F1-S1-T1-verification" not in text

    def test_canonical_unchanged(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "T.md", self._CANONICAL_DEPS)
        before = wu.read_text(encoding="utf-8")
        count = bpp.normalize_dep_ids(tmp_path)
        assert count == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_idempotent(self, tmp_path: Path) -> None:
        _write(tmp_path / "T.md", self._SLUG_DEPS)
        first = bpp.normalize_dep_ids(tmp_path)
        second = bpp.normalize_dep_ids(tmp_path)
        assert first == 1
        assert second == 0

    def test_header_and_none_left_alone(self, tmp_path: Path) -> None:
        """``ID`` header row and ``none`` sentinel are not rewritten."""
        wu = _write(tmp_path / "T.md", self._CANONICAL_DEPS)
        bpp.normalize_dep_ids(tmp_path)
        text = wu.read_text(encoding="utf-8")
        assert "| ID | Title | Status |" in text
        assert "| none | | |" in text

    def test_skips_files_without_dep_sections(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "T.md",
            """\
            # T

            ## Status: in-queue

            (no dep sections)
            """,
        )
        assert bpp.normalize_dep_ids(tmp_path) == 0

    def test_terminal_status_skipped_by_default(self, tmp_path: Path) -> None:
        content = self._SLUG_DEPS.replace("## Status: in-queue", "## Status: done")
        wu = _write(tmp_path / "T.md", content)
        before = wu.read_text(encoding="utf-8")
        assert bpp.normalize_dep_ids(tmp_path) == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_scope_paths_honoured(self, tmp_path: Path) -> None:
        in_scope = _write(tmp_path / "E2" / "E2-F1-S1-T1.md", self._SLUG_DEPS)
        out_of_scope = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", self._SLUG_DEPS)
        out_before = out_of_scope.read_text(encoding="utf-8")
        count = bpp.normalize_dep_ids(tmp_path, scope_paths=[tmp_path / "E2"])
        assert count == 1
        assert "E16-test-cleanup" not in in_scope.read_text(encoding="utf-8")
        assert out_of_scope.read_text(encoding="utf-8") == out_before


# ---------------------------------------------------------------------------
# suffix_na_on_non_python_tasks -- issue #228
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuffixNAOnNonPythonTasks:
    """Issue #228: AC-FINAL Python-tooling lines on non-Python tasks get the N/A tier suffix."""

    _YAML_TASK = """\
        # E1-F1-S1-T1: YAML task

        ## Status: in-queue

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `.github/workflows/audit.yml` | modify |

        ## Acceptance Criteria

        - [ ] AC-FUNC-001 the workflow installs kanon-cli.
        - [ ] AC-FINAL-001 every AC-TEST-* test runs and passes.
        - [ ] AC-FINAL-002 ruff format --check exits zero.
        - [ ] AC-FINAL-003 ruff check exits zero.
        - [ ] AC-FINAL-004 mypy src exits zero.
        - [ ] AC-FINAL-008 bandit -r src -ll exits zero.
        - [ ] AC-FINAL-014 coverage gate is met.

        ## Next
        """

    _PYTHON_TASK = """\
        # E1-F1-S1-T1: Python task

        ## Status: in-queue

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/foo.py` | add |

        ## Acceptance Criteria

        - [ ] AC-FINAL-002 ruff format --check exits zero.

        ## Next
        """

    _MARKDOWN_TASK = """\
        # E1-F1-S1-T1: Markdown task

        ## Status: in-queue

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `docs/contributing.md` | modify |

        ## Acceptance Criteria

        - [ ] AC-FINAL-002 ruff format --check exits zero.

        ## Next
        """

    _MIXED_TASK = """\
        # E1-F1-S1-T1: Mixed task (one .py + one .yml)

        ## Status: in-queue

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/foo.py` | add |
        | `.github/workflows/x.yml` | modify |

        ## Acceptance Criteria

        - [ ] AC-FINAL-002 ruff format --check exits zero.

        ## Next
        """

    def test_yaml_tier_gets_suffix(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "E1-F1-S1-T1.md", self._YAML_TASK)
        count = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        # All Python-tooling AC-FINAL rows get the YAML suffix.
        assert "AC-FINAL-002 ruff format --check exits zero. -- N/A for YAML Tasks (no Python source authored)" in text
        assert "AC-FINAL-004 mypy src exits zero. -- N/A for YAML Tasks (no Python source authored)" in text
        assert "AC-FINAL-014 coverage gate is met. -- N/A for YAML Tasks (no Python source authored)" in text
        # AC-FINAL-001 is NOT in the language-tier set; left alone.
        assert "AC-FINAL-001 every AC-TEST-* test runs and passes." in text
        assert "AC-FINAL-001 every AC-TEST-* test runs and passes. -- N/A" not in text
        # AC-FUNC-001 is not an AC-FINAL line; left alone.
        assert "AC-FUNC-001 the workflow installs kanon-cli." in text

    def test_python_task_untouched(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "E1-F1-S1-T1.md", self._PYTHON_TASK)
        before = wu.read_text(encoding="utf-8")
        count = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert count == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_markdown_tier(self, tmp_path: Path) -> None:
        wu = _write(tmp_path / "E1-F1-S1-T1.md", self._MARKDOWN_TASK)
        count = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        assert (
            "AC-FINAL-002 ruff format --check exits zero. -- N/A for Markdown Tasks (no Python source authored)" in text
        )

    def test_mixed_tier_untouched(self, tmp_path: Path) -> None:
        """Mixed-tier tasks (>=1 .py file) are NOT suffixed -- the Python ACs apply."""
        wu = _write(tmp_path / "E1-F1-S1-T1.md", self._MIXED_TASK)
        before = wu.read_text(encoding="utf-8")
        count = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert count == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_already_suffixed_idempotent(self, tmp_path: Path) -> None:
        """AC lines that already carry `-- N/A` are left alone."""
        _write(tmp_path / "E1-F1-S1-T1.md", self._YAML_TASK)
        first = bpp.suffix_na_on_non_python_tasks(tmp_path)
        second = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert first == 1
        assert second == 0

    def test_skips_epic_feature_story(self, tmp_path: Path) -> None:
        """Only Task work units are processed (Epic / Feature / Story files have no AC-FINAL rows)."""
        # Epic file ID 'E1' should be skipped even with AC-FINAL lines in body.
        _write(tmp_path / "E1.md", self._YAML_TASK.replace("E1-F1-S1-T1", "E1"))
        count = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert count == 0

    def test_unparseable_manifest_skipped(self, tmp_path: Path) -> None:
        """A task with a malformed Manifest (3-col header) is skipped silently."""
        # 3-col header would fail parse_manifest; this pass skips so other
        # passes (normalize_manifest_column_count) can fix the root cause.
        bad = """\
            # E1-F1-S1-T1: bad manifest

            ## Status: in-queue

            ## Changes Manifest

            | Repo | Path | Action |
            |------|------|--------|
            | caylent/cpk | foo.yml | modify |

            ## Acceptance Criteria

            - [ ] AC-FINAL-002 ruff format --check exits zero.
            """
        _write(tmp_path / "E1-F1-S1-T1.md", bad)
        count = bpp.suffix_na_on_non_python_tasks(tmp_path)
        assert count == 0

    def test_terminal_status_skipped_by_default(self, tmp_path: Path) -> None:
        content = self._YAML_TASK.replace("## Status: in-queue", "## Status: done")
        wu = _write(tmp_path / "E1-F1-S1-T1.md", content)
        before = wu.read_text(encoding="utf-8")
        assert bpp.suffix_na_on_non_python_tasks(tmp_path) == 0
        assert wu.read_text(encoding="utf-8") == before

    def test_force_terminal_processes_done(self, tmp_path: Path) -> None:
        content = self._YAML_TASK.replace("## Status: in-queue", "## Status: done")
        wu = _write(tmp_path / "E1-F1-S1-T1.md", content)
        count = bpp.suffix_na_on_non_python_tasks(tmp_path, force_terminal=True)
        assert count == 1
        text = wu.read_text(encoding="utf-8")
        assert "-- N/A for YAML Tasks (no Python source authored)" in text

    def test_scope_paths_honoured(self, tmp_path: Path) -> None:
        in_scope = _write(tmp_path / "E2" / "E2-F1-S1-T1.md", self._YAML_TASK.replace("E1-F1-S1-T1", "E2-F1-S1-T1"))
        out_of_scope = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", self._YAML_TASK)
        out_before = out_of_scope.read_text(encoding="utf-8")
        count = bpp.suffix_na_on_non_python_tasks(tmp_path, scope_paths=[tmp_path / "E2"])
        assert count == 1
        assert "-- N/A for YAML" in in_scope.read_text(encoding="utf-8")
        assert out_of_scope.read_text(encoding="utf-8") == out_before


# ---------------------------------------------------------------------------
# regenerate_backlog_index -- issue #225
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegenerateBacklogIndex:
    """Issue #225: BACKLOG.md append mode preserves existing rows."""

    _EXISTING_BACKLOG = """\
        # Backlog

        ## Status Summary

        | Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |
        |------|-------|------|-------------|----------|---------|----------|-------|
        | E1 | First epic | 3 | 0 | 0 | 0 | 0 | 0 |

        ## Full Work Unit Index

        | ID | Title | Type | Status | Dependencies | Repo | File Path |
        |----|-------|------|--------|--------------|------|-----------|
        | E1 | First epic | Epic | done | None | caylent/foo | `backlog/E1-first/E1.md` |
        | E1-F1-S1-T1 | First task | Task | done | None | caylent/foo | `backlog/E1-first/E1-F1-S1-T1.md` |
        """

    def _write_existing_workspace(self, tmp_path: Path) -> Path:
        """Author the existing BACKLOG.md + backlog/ tree under tmp_path. Returns the workspace root."""
        ws = tmp_path
        _write(ws / "BACKLOG.md", self._EXISTING_BACKLOG)
        _write(
            ws / "backlog" / "E1-first" / "E1.md",
            """\
            # E1: First epic

            ## Status: done
            """,
        )
        _write(
            ws / "backlog" / "E1-first" / "E1-F1-S1-T1.md",
            """\
            # E1-F1-S1-T1: First task

            ## Status: done

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/foo.py` | Done. |
            """,
        )
        return ws

    def _write_new_epic(self, ws: Path) -> None:
        """Add a new E2 epic + one Feature + one Story + one Task under ws/backlog/."""
        _write(
            ws / "backlog" / "E2-second" / "E2.md",
            """\
            # E2: Second epic

            ## Status: in-queue
            """,
        )
        _write(
            ws / "backlog" / "E2-second" / "E2-F1-feat" / "E2-F1.md",
            """\
            # E2-F1: Feature

            ## Status: in-queue
            """,
        )
        _write(
            ws / "backlog" / "E2-second" / "E2-F1-feat" / "E2-F1-S1-story" / "E2-F1-S1.md",
            """\
            # E2-F1-S1: Story

            ## Status: in-queue
            """,
        )
        _write(
            ws / "backlog" / "E2-second" / "E2-F1-feat" / "E2-F1-S1-story" / "E2-F1-S1-T1.md",
            """\
            # E2-F1-S1-T1: New task

            ## Status: in-queue

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/bar.py` | Add. |
            """,
        )

    def test_no_workspace_root_is_noop(self, tmp_path: Path) -> None:
        ws = self._write_existing_workspace(tmp_path)
        self._write_new_epic(ws)
        result = bpp.regenerate_backlog_index(ws / "backlog", scope_paths=[ws / "backlog" / "E2-second"])
        assert result == 0  # workspace_root not supplied; no-op.

    def test_missing_backlog_md_is_noop(self, tmp_path: Path) -> None:
        # No BACKLOG.md exists yet -- the skill's greenfield write handles it.
        _write(tmp_path / "backlog" / "E2-second" / "E2.md", "# E2: Second\n\n## Status: in-queue\n")
        result = bpp.regenerate_backlog_index(
            tmp_path / "backlog",
            scope_paths=[tmp_path / "backlog" / "E2-second"],
            workspace_root=tmp_path,
        )
        assert result == 0
        assert not (tmp_path / "BACKLOG.md").exists()

    def test_append_preserves_existing_rows(self, tmp_path: Path) -> None:
        ws = self._write_existing_workspace(tmp_path)
        self._write_new_epic(ws)
        before = (ws / "BACKLOG.md").read_text(encoding="utf-8")
        result = bpp.regenerate_backlog_index(
            ws / "backlog",
            scope_paths=[ws / "backlog" / "E2-second"],
            workspace_root=ws,
        )
        assert result == 1
        after = (ws / "BACKLOG.md").read_text(encoding="utf-8")
        # Every existing row from BEFORE is still in AFTER, byte-for-byte.
        for original_row in before.splitlines():
            if original_row.strip().startswith("| E1"):
                assert original_row in after, f"row lost: {original_row!r}"
        # E2 Status Summary row appended (3 children: 1F + 1S + 1T = 3 in-queue).
        assert "| E2 | Second epic |" in after
        # E2 Full Work Unit Index rows appended.
        assert "| E2 | Second epic | Epic |" in after
        assert "| E2-F1 |" in after
        assert "| E2-F1-S1 |" in after
        assert "| E2-F1-S1-T1 | New task | Task |" in after

    def test_collision_raises(self, tmp_path: Path) -> None:
        ws = self._write_existing_workspace(tmp_path)
        # Author a new E1 epic at a different path -- collision.
        _write(
            ws / "backlog" / "E1-different" / "E1.md",
            "# E1: Collision\n\n## Status: in-queue\n",
        )
        before = (ws / "BACKLOG.md").read_text(encoding="utf-8")
        with pytest.raises(bpp.BacklogAppendCollisionError, match="E1"):
            bpp.regenerate_backlog_index(
                ws / "backlog",
                scope_paths=[ws / "backlog" / "E1-different"],
                workspace_root=ws,
            )
        # File on disk is unchanged after the collision.
        assert (ws / "BACKLOG.md").read_text(encoding="utf-8") == before

    def test_idempotent(self, tmp_path: Path) -> None:
        ws = self._write_existing_workspace(tmp_path)
        self._write_new_epic(ws)
        first = bpp.regenerate_backlog_index(
            ws / "backlog",
            scope_paths=[ws / "backlog" / "E2-second"],
            workspace_root=ws,
        )
        second = bpp.regenerate_backlog_index(
            ws / "backlog",
            scope_paths=[ws / "backlog" / "E2-second"],
            workspace_root=ws,
        )
        assert first == 1
        assert second == 0

    def test_status_summary_counts_exclude_epic(self, tmp_path: Path) -> None:
        """The Epic file itself is NOT counted in its Status Summary row (issue #229)."""
        ws = self._write_existing_workspace(tmp_path)
        self._write_new_epic(ws)
        bpp.regenerate_backlog_index(
            ws / "backlog",
            scope_paths=[ws / "backlog" / "E2-second"],
            workspace_root=ws,
        )
        text = (ws / "BACKLOG.md").read_text(encoding="utf-8")
        # E2 has 1 Feature + 1 Story + 1 Task all in-queue, plus the Epic
        # itself (in-queue but excluded). So the In Queue column should be 3.
        m = re.search(r"^\| E2 \| Second epic \| (\d+) \| (\d+) \| (\d+) \|", text, re.MULTILINE)
        assert m is not None, f"E2 row not found in:\n{text}"
        done, in_progress, in_queue = m.group(1), m.group(2), m.group(3)
        assert done == "0"
        assert in_progress == "0"
        assert in_queue == "3"

    def test_collision_same_path_is_noop(self, tmp_path: Path) -> None:
        """Re-running with the existing E1 in scope is a no-op (same path)."""
        ws = self._write_existing_workspace(tmp_path)
        before = (ws / "BACKLOG.md").read_text(encoding="utf-8")
        result = bpp.regenerate_backlog_index(
            ws / "backlog",
            scope_paths=[ws / "backlog" / "E1-first"],
            workspace_root=ws,
        )
        # No new rows to append; existing rows already cover the scope.
        assert result == 0
        assert (ws / "BACKLOG.md").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# verify_code_standards_canonical -- issue #230
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyCodeStandardsCanonical:
    """Issue #230: drift detector reports without mutating."""

    def _make_task_with_canonical(self, tmp_path: Path) -> Path:
        from devbench.plugin_helpers.code_standards_template import emit_code_standards_block

        block = emit_code_standards_block(tmp_path, task_specific_error_paths=["task-specific error 1"])
        # Compose a minimal Task work-unit file.
        content = (
            textwrap.dedent("""\
            # E1-F1-S1-T1: Title

            ## Status: in-queue

            """)
            + block
            + "\n## Next\n"
        )
        return _write(tmp_path / "E1-F1-S1-T1.md", content)

    def _make_task_with_drift(self, tmp_path: Path) -> Path:
        # Same shape as canonical but Critical Rule 1 is paraphrased.
        drifted_block = textwrap.dedent("""\
            ### Code Standards

            Some paraphrased intro that does not match the canonical wording.

            #### Critical Rules (Violation = Automatic Rejection)

            1. **Different rule** -- paraphrased.

            #### Error Handling Contract

            Generic.

            Task-specific error paths for this work unit:

            (none)
            """)
        content = (
            textwrap.dedent("""\
            # E1-F1-S1-T1: Title

            ## Status: in-queue

            """)
            + drifted_block
            + "\n## Next\n"
        )
        return _write(tmp_path / "E1-F1-S1-T1.md", content)

    def test_canonical_match_returns_zero(self, tmp_path: Path) -> None:
        wu = self._make_task_with_canonical(tmp_path)
        before = wu.read_text(encoding="utf-8")
        count = bpp.verify_code_standards_canonical(tmp_path, workspace_root=tmp_path)
        assert count == 0
        # File is not mutated.
        assert wu.read_text(encoding="utf-8") == before

    def test_drift_detected_but_not_mutated(self, tmp_path: Path) -> None:
        wu = self._make_task_with_drift(tmp_path)
        before = wu.read_text(encoding="utf-8")
        count = bpp.verify_code_standards_canonical(tmp_path)
        assert count == 1
        # File is NOT mutated -- check-only pass.
        assert wu.read_text(encoding="utf-8") == before

    def test_skips_non_task_files(self, tmp_path: Path) -> None:
        """Epic / Feature / Story files are skipped (they may not carry the block)."""
        # Author the drift content but use an Epic-shaped filename.
        self._make_task_with_drift(tmp_path)
        # Rename to Epic shape.
        (tmp_path / "E1-F1-S1-T1.md").rename(tmp_path / "E1.md")
        count = bpp.verify_code_standards_canonical(tmp_path)
        assert count == 0

    def test_terminal_status_skipped_by_default(self, tmp_path: Path) -> None:
        wu = self._make_task_with_drift(tmp_path)
        # Mark the file done.
        content = wu.read_text(encoding="utf-8").replace("## Status: in-queue", "## Status: done")
        wu.write_text(content, encoding="utf-8")
        count = bpp.verify_code_standards_canonical(tmp_path)
        assert count == 0

    def test_force_terminal_includes_done_tasks(self, tmp_path: Path) -> None:
        wu = self._make_task_with_drift(tmp_path)
        content = wu.read_text(encoding="utf-8").replace("## Status: in-queue", "## Status: done")
        wu.write_text(content, encoding="utf-8")
        count = bpp.verify_code_standards_canonical(tmp_path, force_terminal=True)
        assert count == 1

    def test_no_code_standards_section_is_zero(self, tmp_path: Path) -> None:
        _write(tmp_path / "E1-F1-S1-T1.md", "# T\n\n## Status: in-queue\n\n(no code standards block)\n")
        count = bpp.verify_code_standards_canonical(tmp_path)
        assert count == 0

    def test_scope_paths_honoured(self, tmp_path: Path) -> None:
        # Out-of-scope task is drifted but scope only includes E2.
        out_scope_dir = tmp_path / "E1"
        out_scope_dir.mkdir()
        drifted_block = "### Code Standards\n\nbad\n\n#### Error Handling Contract\n\n(none)\n"
        _write(
            out_scope_dir / "E1-F1-S1-T1.md",
            "# T\n\n## Status: in-queue\n\n" + drifted_block,
        )
        in_scope_dir = tmp_path / "E2"
        in_scope_dir.mkdir()
        _write(
            in_scope_dir / "E2-F1-S1-T1.md",
            "# T\n\n## Status: in-queue\n\n" + drifted_block,
        )
        count = bpp.verify_code_standards_canonical(tmp_path, scope_paths=[in_scope_dir])
        # Only the in-scope drift is counted.
        assert count == 1


# ---------------------------------------------------------------------------
# run_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunAll:
    """``run_all`` returns a ``{pass_name: count}`` mapping."""

    def test_returns_count_per_pass(self, tmp_path: Path) -> None:
        result = bpp.run_all(tmp_path)
        assert result == {
            "normalize_manifest_column_count": 0,
            "sanitize_markdown_pipes_in_manifest": 0,
            "dedupe_manifest_rows": 0,
            "normalize_dep_ids": 0,
            "suffix_ref_on_orphan_paths": 0,
            "suffix_na_on_non_python_tasks": 0,
            "regenerate_backlog_index": 0,
            "verify_code_standards_canonical": 0,
        }


# ---------------------------------------------------------------------------
# Scope-awareness + terminal-status guard -- issue #226
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScopeAwareness:
    """Issue #226: passes default-skip done/declined files and honour scope_paths."""

    _DONE_WITH_ORPHAN = """\
        # E1-F1-S1-T1: existing done task

        ## Status: done

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/old.py` | Done. |

        ## Acceptance Criteria

        - AC-1: cite `src/orphan.py` for context.

        ## Next
        """

    _NEW_WITH_ORPHAN = """\
        # E2-F1-S1-T1: new task

        ## Status: in-queue

        ## Changes Manifest

        | File | Change |
        |------|--------|
        | `src/new.py` | Add. |

        ## Acceptance Criteria

        - AC-1: cite `src/orphan.py` for context.

        ## Next
        """

    def test_run_all_respects_scope_paths(self, tmp_path: Path) -> None:
        """When ``scope_paths`` is supplied, files outside scope are untouched."""
        old = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", self._DONE_WITH_ORPHAN)
        new = _write(tmp_path / "E2" / "E2-F1-S1-T1.md", self._NEW_WITH_ORPHAN)
        old_before = old.read_text(encoding="utf-8")
        new_before = new.read_text(encoding="utf-8")

        result = bpp.run_all(tmp_path, scope_paths=[tmp_path / "E2"])

        # E1 (out of scope) is bit-identical.
        assert old.read_text(encoding="utf-8") == old_before
        # E2 (in scope) had its orphan path suffixed.
        new_after = new.read_text(encoding="utf-8")
        assert new_after != new_before
        assert "`src/orphan.py` (ref)" in new_after
        # Only E2 was modified.
        assert result["suffix_ref_on_orphan_paths"] == 1

    def test_run_all_skips_terminal_status_by_default(self, tmp_path: Path) -> None:
        """Default behaviour: done/declined files are skipped even without ``scope_paths``."""
        done = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", self._DONE_WITH_ORPHAN)
        before = done.read_text(encoding="utf-8")

        result = bpp.run_all(tmp_path)

        # Done task is not mutated even though it has an orphan path.
        assert done.read_text(encoding="utf-8") == before
        assert result["suffix_ref_on_orphan_paths"] == 0

    def test_force_terminal_overrides_status_guard(self, tmp_path: Path) -> None:
        """``force_terminal=True`` mutates done/declined files."""
        done = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", self._DONE_WITH_ORPHAN)
        before = done.read_text(encoding="utf-8")

        result = bpp.run_all(tmp_path, force_terminal=True)

        after = done.read_text(encoding="utf-8")
        assert after != before
        assert "`src/orphan.py` (ref)" in after
        assert result["suffix_ref_on_orphan_paths"] == 1

    def test_declined_status_also_skipped_by_default(self, tmp_path: Path) -> None:
        """``declined`` is treated as terminal alongside ``done``."""
        declined_content = self._DONE_WITH_ORPHAN.replace("## Status: done", "## Status: declined")
        declined = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", declined_content)
        before = declined.read_text(encoding="utf-8")

        result = bpp.run_all(tmp_path)

        assert declined.read_text(encoding="utf-8") == before
        assert result["suffix_ref_on_orphan_paths"] == 0

    def test_idempotency_under_scope_paths(self, tmp_path: Path) -> None:
        """Re-running with the same scope returns 0 across all passes."""
        _write(tmp_path / "E2" / "E2-F1-S1-T1.md", self._NEW_WITH_ORPHAN)

        first = bpp.run_all(tmp_path, scope_paths=[tmp_path / "E2"])
        second = bpp.run_all(tmp_path, scope_paths=[tmp_path / "E2"])

        assert first["suffix_ref_on_orphan_paths"] == 1
        assert second == {
            "normalize_manifest_column_count": 0,
            "sanitize_markdown_pipes_in_manifest": 0,
            "dedupe_manifest_rows": 0,
            "normalize_dep_ids": 0,
            "suffix_ref_on_orphan_paths": 0,
            "suffix_na_on_non_python_tasks": 0,
            "regenerate_backlog_index": 0,
            "verify_code_standards_canonical": 0,
        }

    def test_scope_paths_nonexistent_raises(self, tmp_path: Path) -> None:
        """A typo in ``scope_paths`` raises ``FileNotFoundError`` (fail-fast)."""
        with pytest.raises(FileNotFoundError, match="scope_paths entry does not exist"):
            bpp.run_all(tmp_path, scope_paths=[tmp_path / "missing"])

    def test_scope_paths_overlap_dedupes(self, tmp_path: Path) -> None:
        """Overlapping ``scope_paths`` do not double-process the same file."""
        _write(tmp_path / "E2" / "E2-F1-S1-T1.md", self._NEW_WITH_ORPHAN)

        # The parent directory and the explicit E2 directory both contain
        # the same file; the walk yields it once.
        result = bpp.run_all(tmp_path, scope_paths=[tmp_path, tmp_path / "E2"])

        assert result["suffix_ref_on_orphan_paths"] == 1

    def test_sanitize_pipes_skips_done_task_by_default(self, tmp_path: Path) -> None:
        """The pipe-sanitizer also honours the terminal-status guard."""
        content = """\
            # E1-F1-S1-T1: done with bad pipes

            ## Status: done

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/foo.py` | Add prose with run cmd | grep -v debug shell pipeline. |

            ## Next
            """
        wu = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", content)
        before = wu.read_text(encoding="utf-8")

        result = bpp.sanitize_markdown_pipes_in_manifest(tmp_path)

        assert wu.read_text(encoding="utf-8") == before
        assert result == 0

    def test_dedupe_skips_done_task_by_default(self, tmp_path: Path) -> None:
        """The dedupe pass also honours the terminal-status guard."""
        content = """\
            # E1-F1-S1-T1: done with dup rows

            ## Status: done

            ## Changes Manifest

            | File | Change |
            |------|--------|
            | `src/foo.py` | Add foo. |
            | `src/foo.py` | Add foo. |

            ## Next
            """
        wu = _write(tmp_path / "E1" / "E1-F1-S1-T1.md", content)
        before = wu.read_text(encoding="utf-8")

        result = bpp.dedupe_manifest_rows(tmp_path)

        assert wu.read_text(encoding="utf-8") == before
        assert result == 0


@pytest.mark.unit
class TestIsTerminalStatus:
    """Direct tests for the terminal-status helper."""

    def test_done_is_terminal(self) -> None:
        assert bpp._is_terminal_status("## Status: done\n") is True

    def test_declined_is_terminal(self) -> None:
        assert bpp._is_terminal_status("## Status: declined\n") is True

    def test_draft_is_not_terminal(self) -> None:
        assert bpp._is_terminal_status("## Status: draft\n") is False

    def test_in_queue_is_not_terminal(self) -> None:
        assert bpp._is_terminal_status("## Status: in-queue\n") is False

    def test_missing_status_line_returns_false(self) -> None:
        assert bpp._is_terminal_status("# Title only\n") is False

    def test_status_value_is_case_insensitive(self) -> None:
        assert bpp._is_terminal_status("## Status: DONE\n") is True


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
