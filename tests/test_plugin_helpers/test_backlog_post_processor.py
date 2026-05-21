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
            "suffix_ref_on_orphan_paths": 0,
            "suffix_na_on_non_python_tasks": 0,
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
            "suffix_ref_on_orphan_paths": 0,
            "suffix_na_on_non_python_tasks": 0,
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
