"""Tests for devbench.backlog.manifest module."""

from __future__ import annotations

import subprocess as _subprocess
from pathlib import Path as _Path
from typing import Any, cast
from unittest.mock import patch as _patch

import pytest

from devbench.backlog.manifest import (
    EM_DASH,
    MANIFEST_HEADER,
    ManifestParseError,
    ManifestRow,
    append_rows,
    assert_staged_matches_manifest,
    assert_worktree_scoped_to_manifest,
    list_changed_files,
    list_staged_files,
    parse_manifest,
    render_manifest_rows,
)

# ---------------------------------------------------------------------------
# Sample work-unit content used across tests
# ---------------------------------------------------------------------------

SAMPLE_ONE_ROW = """\
# T1: Sample Task

## Status: in-queue

## Changes Manifest

| File | Change |
|------|--------|
| `src/example/parser.py` | add feature |

## Definition of Done
"""

SAMPLE_MANY_ROWS = """\
# T1: Sample Task

## Changes Manifest

| File | Change |
|------|--------|
| `src/example/parser.py` | add feature |
| `tests/test_example.py` | cover feature |
| `docs/example.md` | document feature |

## Definition of Done
"""

SAMPLE_EMPTY_MANIFEST = """\
# T1: Sample Task

## Changes Manifest

| File | Change |
|------|--------|

## Definition of Done
"""

SAMPLE_NO_MANIFEST = """\
# T1: Sample Task

## Status: in-queue

## Definition of Done
"""

SAMPLE_SECTION_AT_END = """\
# T1: Sample Task

## Changes Manifest

| File | Change |
|------|--------|
| `src/example/parser.py` | add feature |
"""


# ---------------------------------------------------------------------------
# ManifestRow validation tests
# ---------------------------------------------------------------------------


class TestManifestRow:
    """ManifestRow construction validates its fields."""

    def test_valid_row(self) -> None:
        row = ManifestRow(file="src/example/parser.py", change="Fix BOM handling")
        assert row.file == "src/example/parser.py"
        assert row.change == "Fix BOM handling"

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            ManifestRow(file="", change="x")

    def test_whitespace_file_rejected(self) -> None:
        with pytest.raises(ValueError, match="leading/trailing whitespace"):
            ManifestRow(file=" src/a.py ", change="x")

    def test_empty_change_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            ManifestRow(file="src/a.py", change="")

    def test_whitespace_only_change_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be non-empty"):
            ManifestRow(file="src/a.py", change="   ")

    def test_em_dash_in_file_rejected(self) -> None:
        with pytest.raises(ValueError, match="em-dash"):
            ManifestRow(file=f"src/a{EM_DASH}.py", change="x")

    def test_em_dash_in_change_rejected(self) -> None:
        with pytest.raises(ValueError, match="em-dash"):
            ManifestRow(file="src/a.py", change=f"foo{EM_DASH}bar")

    def test_frozen(self) -> None:
        row = ManifestRow(file="src/a.py", change="x")
        with pytest.raises(AttributeError):
            cast(Any, row).file = "other"

    def test_equality(self) -> None:
        assert ManifestRow(file="a", change="b") == ManifestRow(file="a", change="b")
        assert ManifestRow(file="a", change="b") != ManifestRow(file="a", change="c")


# ---------------------------------------------------------------------------
# parse_manifest tests
# ---------------------------------------------------------------------------


class TestParseManifest:
    """parse_manifest extracts typed rows from work-unit Markdown content."""

    def test_one_row(self) -> None:
        rows = parse_manifest(SAMPLE_ONE_ROW)
        assert rows == [ManifestRow(file="src/example/parser.py", change="add feature")]

    def test_many_rows(self) -> None:
        rows = parse_manifest(SAMPLE_MANY_ROWS)
        assert rows == [
            ManifestRow(file="src/example/parser.py", change="add feature"),
            ManifestRow(file="tests/test_example.py", change="cover feature"),
            ManifestRow(file="docs/example.md", change="document feature"),
        ]

    def test_empty_manifest_returns_empty_list(self) -> None:
        assert parse_manifest(SAMPLE_EMPTY_MANIFEST) == []

    def test_missing_section_raises(self) -> None:
        with pytest.raises(ManifestParseError, match=MANIFEST_HEADER):
            parse_manifest(SAMPLE_NO_MANIFEST)

    def test_file_without_backticks(self) -> None:
        content = SAMPLE_ONE_ROW.replace("`src/example/parser.py`", "src/example/parser.py")
        rows = parse_manifest(content)
        assert rows == [ManifestRow(file="src/example/parser.py", change="add feature")]

    def test_single_column_row_raises(self) -> None:
        content = """\
# T: x

## Changes Manifest

| File | Change |
|------|--------|
| only_one_column |

## Foo
"""
        with pytest.raises(ManifestParseError, match="2 columns"):
            parse_manifest(content)

    def test_three_column_row_raises(self) -> None:
        content = """\
# T: x

## Changes Manifest

| File | Change |
|------|--------|
| `src/a.py` | change | extra |

## Foo
"""
        with pytest.raises(ManifestParseError, match="2 columns"):
            parse_manifest(content)

    def test_em_dash_in_cell_raises(self) -> None:
        content = f"""\
# T: x

## Changes Manifest

| File | Change |
|------|--------|
| `src/a.py` | foo{EM_DASH}bar |

## Foo
"""
        with pytest.raises(ManifestParseError, match="em-dash"):
            parse_manifest(content)

    def test_empty_file_cell_raises(self) -> None:
        content = """\
# T: x

## Changes Manifest

| File | Change |
|------|--------|
| `` | something |

## Foo
"""
        with pytest.raises(ManifestParseError, match="non-empty"):
            parse_manifest(content)

    def test_empty_change_cell_raises(self) -> None:
        content = """\
# T: x

## Changes Manifest

| File | Change |
|------|--------|
| `src/a.py` |   |

## Foo
"""
        with pytest.raises(ManifestParseError, match="non-empty"):
            parse_manifest(content)

    def test_ignores_non_table_lines_in_section(self) -> None:
        content = """\
# T: x

## Changes Manifest

This is introductory prose that should be ignored.

| File | Change |
|------|--------|
| `src/a.py` | thing |

More trailing prose.

## Foo
"""
        rows = parse_manifest(content)
        assert rows == [ManifestRow(file="src/a.py", change="thing")]

    def test_section_at_end_of_file(self) -> None:
        rows = parse_manifest(SAMPLE_SECTION_AT_END)
        assert rows == [ManifestRow(file="src/example/parser.py", change="add feature")]


# ---------------------------------------------------------------------------
# render_manifest_rows tests
# ---------------------------------------------------------------------------


class TestRenderManifestRows:
    """render_manifest_rows produces a stable Markdown table."""

    def test_empty_list_returns_header_only(self) -> None:
        assert render_manifest_rows([]) == "| File | Change |\n|------|--------|\n"

    def test_one_row(self) -> None:
        out = render_manifest_rows([ManifestRow(file="src/a.py", change="x")])
        assert out == "| File | Change |\n|------|--------|\n| `src/a.py` | x |\n"

    def test_many_rows(self) -> None:
        rows = [
            ManifestRow(file="src/a.py", change="alpha"),
            ManifestRow(file="tests/b.py", change="beta"),
        ]
        out = render_manifest_rows(rows)
        assert "| `src/a.py` | alpha |" in out
        assert "| `tests/b.py` | beta |" in out
        assert out.endswith("\n")

    def test_round_trip_idempotent(self) -> None:
        original = [
            ManifestRow(file="src/a.py", change="alpha"),
            ManifestRow(file="tests/b.py", change="beta"),
        ]
        rendered = render_manifest_rows(original)
        wrapped = f"# T: x\n\n## Changes Manifest\n\n{rendered}\n## Foo\n"
        first_pass = parse_manifest(wrapped)
        assert first_pass == original

        second_render = render_manifest_rows(first_pass)
        second_wrapped = f"# T: x\n\n## Changes Manifest\n\n{second_render}\n## Foo\n"
        second_pass = parse_manifest(second_wrapped)
        assert second_pass == original


# ---------------------------------------------------------------------------
# append_rows tests
# ---------------------------------------------------------------------------


class TestAppendRows:
    """append_rows splices new rows into the Changes Manifest atomically."""

    def test_append_to_existing_single_row(self) -> None:
        new_rows = [ManifestRow(file="src/new.py", change="new fix")]
        out = append_rows(SAMPLE_ONE_ROW, new_rows)
        parsed = parse_manifest(out)
        assert parsed == [
            ManifestRow(file="src/example/parser.py", change="add feature"),
            ManifestRow(file="src/new.py", change="new fix"),
        ]

    def test_append_multiple_rows(self) -> None:
        new_rows = [
            ManifestRow(file="src/new_one.py", change="fix one"),
            ManifestRow(file="src/new_two.py", change="fix two"),
        ]
        out = append_rows(SAMPLE_ONE_ROW, new_rows)
        parsed = parse_manifest(out)
        assert len(parsed) == 3
        assert parsed[1].file == "src/new_one.py"
        assert parsed[2].file == "src/new_two.py"

    def test_append_to_empty_manifest(self) -> None:
        new_rows = [ManifestRow(file="src/new.py", change="new fix")]
        out = append_rows(SAMPLE_EMPTY_MANIFEST, new_rows)
        assert parse_manifest(out) == new_rows

    def test_append_nothing_is_noop(self) -> None:
        out = append_rows(SAMPLE_ONE_ROW, [])
        assert out == SAMPLE_ONE_ROW

    def test_missing_section_raises(self) -> None:
        with pytest.raises(ManifestParseError, match=MANIFEST_HEADER):
            append_rows(SAMPLE_NO_MANIFEST, [ManifestRow(file="a", change="b")])

    def test_preserves_content_before_section(self) -> None:
        new_rows = [ManifestRow(file="src/new.py", change="new")]
        out = append_rows(SAMPLE_ONE_ROW, new_rows)
        # Everything up to "## Changes Manifest" should be byte-identical
        prefix = SAMPLE_ONE_ROW.split("## Changes Manifest", 1)[0]
        assert out.startswith(prefix)

    def test_preserves_content_after_section(self) -> None:
        new_rows = [ManifestRow(file="src/new.py", change="new")]
        out = append_rows(SAMPLE_ONE_ROW, new_rows)
        # Everything from "## Definition of Done" to the end should be byte-identical
        suffix = "## Definition of Done" + SAMPLE_ONE_ROW.split("## Definition of Done", 1)[1]
        assert suffix in out
        assert out.endswith(suffix)

    def test_append_when_section_at_end_of_file(self) -> None:
        new_rows = [ManifestRow(file="src/new.py", change="new fix")]
        out = append_rows(SAMPLE_SECTION_AT_END, new_rows)
        parsed = parse_manifest(out)
        assert parsed == [
            ManifestRow(file="src/example/parser.py", change="add feature"),
            ManifestRow(file="src/new.py", change="new fix"),
        ]

    def test_append_rejects_malformed_existing_manifest(self) -> None:
        bad = """\
# T: x

## Changes Manifest

| File | Change |
|------|--------|
| three | col | row |

## Foo
"""
        with pytest.raises(ManifestParseError, match="2 columns"):
            append_rows(bad, [ManifestRow(file="a", change="b")])


# ---------------------------------------------------------------------------
# Slice 3b: list_staged_files + assert_staged_matches_manifest
# ---------------------------------------------------------------------------


def _init_repo_with_file(path: _Path, filename: str, contents: str = "x") -> None:
    _subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    _subprocess.run(["git", "config", "user.email", "t@ex.com"], cwd=path, check=True, capture_output=True)
    _subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / filename).parent.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(contents)


class TestListStagedFiles:
    def test_empty_staged_returns_empty_list(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "README.md")
        assert list_staged_files(tmp_path) == []

    def test_staged_files_returned_relative(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _init_repo_with_file(tmp_path, "src/b.py")
        _subprocess.run(["git", "add", "src/a.py", "src/b.py"], cwd=tmp_path, check=True, capture_output=True)
        staged = list_staged_files(tmp_path)
        assert sorted(staged) == ["src/a.py", "src/b.py"]

    def test_non_git_dir_raises(self, tmp_path: _Path) -> None:
        with pytest.raises(RuntimeError, match=r"git diff --cached --name-only -z failed"):
            list_staged_files(tmp_path)

    def test_timeout_raises_runtime_error(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "x.txt")
        with (
            _patch(
                "subprocess.run",
                side_effect=_subprocess.TimeoutExpired(cmd="git", timeout=1),
            ),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            list_staged_files(tmp_path)


class TestAssertStagedMatchesManifest:
    def test_empty_staged_passes(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "README.md")
        assert_staged_matches_manifest(tmp_path, ["src/a.py"])  # no raise

    def test_all_staged_in_manifest_passes(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _subprocess.run(["git", "add", "src/a.py"], cwd=tmp_path, check=True, capture_output=True)
        assert_staged_matches_manifest(tmp_path, ["src/a.py", "tests/test_a.py"])

    def test_out_of_manifest_rejected_with_paths(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _init_repo_with_file(tmp_path, "TRACE_FILE")
        _subprocess.run(["git", "add", "src/a.py", "TRACE_FILE"], cwd=tmp_path, check=True, capture_output=True)
        with pytest.raises(RuntimeError) as exc:
            assert_staged_matches_manifest(tmp_path, ["src/a.py"])
        msg = str(exc.value)
        assert "Manifest scope violation" in msg
        assert "TRACE_FILE" in msg
        # src/a.py appears in the "Manifest declares" list but not in the offender list
        assert "staged file(s) not in Changes Manifest: ['TRACE_FILE']" in msg


# ---------------------------------------------------------------------------
# Claim-time checkout scope guard: the shared single-branch checkout must hold
# only paths the claiming work unit is authorized to touch.
# ---------------------------------------------------------------------------


def _commit_baseline(path: _Path) -> None:
    """Stage everything present and commit it, so the tree starts clean."""
    _subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    _subprocess.run(["git", "commit", "-m", "baseline"], cwd=path, check=True, capture_output=True)


class TestListChangedFiles:
    def test_clean_tree_returns_empty_list(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        assert list_changed_files(tmp_path) == []

    def test_staged_change_is_reported(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        (tmp_path / "src/a.py").write_text("changed")
        _subprocess.run(["git", "add", "src/a.py"], cwd=tmp_path, check=True, capture_output=True)
        assert list_changed_files(tmp_path) == ["src/a.py"]

    def test_unstaged_tracked_modification_is_reported(self, tmp_path: _Path) -> None:
        """The case ``list_staged_files`` misses: a unit that blocked before staging."""
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        (tmp_path / "src/a.py").write_text("changed")
        assert list_staged_files(tmp_path) == []
        assert list_changed_files(tmp_path) == ["src/a.py"]

    def test_untracked_file_is_reported(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        (tmp_path / "leftover.py").write_text("residue")
        assert list_changed_files(tmp_path) == ["leftover.py"]

    def test_gitignored_file_is_not_reported(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        (tmp_path / ".gitignore").write_text("build/\n")
        _commit_baseline(tmp_path)
        (tmp_path / "build").mkdir()
        (tmp_path / "build/out.o").write_text("binary")
        assert list_changed_files(tmp_path) == []

    def test_union_is_sorted_and_deduplicated(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/z.py")
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        # src/a.py is both staged AND modified again in the worktree, so it is
        # reported by two of the three underlying queries.
        (tmp_path / "src/a.py").write_text("staged")
        _subprocess.run(["git", "add", "src/a.py"], cwd=tmp_path, check=True, capture_output=True)
        (tmp_path / "src/a.py").write_text("and then modified again")
        (tmp_path / "src/z.py").write_text("unstaged only")
        (tmp_path / "new.py").write_text("untracked")
        assert list_changed_files(tmp_path) == ["new.py", "src/a.py", "src/z.py"]

    def test_non_git_dir_raises(self, tmp_path: _Path) -> None:
        with pytest.raises(RuntimeError, match=r"git diff --cached --name-only -z failed"):
            list_changed_files(tmp_path)


class TestAssertWorktreeScopedToManifest:
    def test_clean_tree_passes(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        assert_worktree_scoped_to_manifest(tmp_path, ["src/a.py"], "E1-F1-S1-T1")

    def test_dirty_within_own_manifest_passes(self, tmp_path: _Path) -> None:
        """Re-claiming an interrupted in-progress unit must remain possible."""
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        (tmp_path / "src/a.py").write_text("own work in progress")
        (tmp_path / "tests/test_a.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests/test_a.py").write_text("own new test")
        assert_worktree_scoped_to_manifest(tmp_path, ["src/a.py", "tests/test_a.py"], "E1-F1-S1-T1")

    def test_foreign_staged_file_is_refused(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _init_repo_with_file(tmp_path, "docs/sibling.md")
        _commit_baseline(tmp_path)
        (tmp_path / "docs/sibling.md").write_text("another unit's staged work")
        _subprocess.run(["git", "add", "docs/sibling.md"], cwd=tmp_path, check=True, capture_output=True)
        with pytest.raises(RuntimeError) as exc:
            assert_worktree_scoped_to_manifest(tmp_path, ["src/a.py"], "E1-F1-S1-T1")
        msg = str(exc.value)
        assert "Checkout scope violation" in msg
        assert "E1-F1-S1-T1" in msg
        assert "docs/sibling.md" in msg

    def test_foreign_unstaged_file_is_refused(self, tmp_path: _Path) -> None:
        """The exact residue shape that made a docs-only unit fail security review."""
        _init_repo_with_file(tmp_path, "docs/own.md")
        _init_repo_with_file(tmp_path, "src/sibling.py")
        _commit_baseline(tmp_path)
        (tmp_path / "src/sibling.py").write_text("another unit's uncommitted source")
        with pytest.raises(RuntimeError) as exc:
            assert_worktree_scoped_to_manifest(tmp_path, ["docs/own.md"], "E4-F3-S1-T3")
        msg = str(exc.value)
        assert "src/sibling.py" in msg
        assert "E4-F3-S1-T3" in msg

    def test_foreign_untracked_file_is_refused(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/a.py")
        _commit_baseline(tmp_path)
        (tmp_path / "tests/test_sibling.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests/test_sibling.py").write_text("another unit's new test module")
        with pytest.raises(RuntimeError) as exc:
            assert_worktree_scoped_to_manifest(tmp_path, ["src/a.py"], "E1-F1-S1-T1")
        assert "tests/test_sibling.py" in str(exc.value)

    def test_error_lists_every_offender_and_the_manifest(self, tmp_path: _Path) -> None:
        _init_repo_with_file(tmp_path, "src/own.py")
        _init_repo_with_file(tmp_path, "src/one.py")
        _init_repo_with_file(tmp_path, "src/two.py")
        _commit_baseline(tmp_path)
        (tmp_path / "src/one.py").write_text("residue one")
        (tmp_path / "src/two.py").write_text("residue two")
        with pytest.raises(RuntimeError) as exc:
            assert_worktree_scoped_to_manifest(tmp_path, ["src/own.py"], "E1-F1-S1-T1")
        msg = str(exc.value)
        assert "2 path(s)" in msg
        assert "['src/one.py', 'src/two.py']" in msg
        assert "Manifest declares: ['src/own.py']" in msg


# ---------------------------------------------------------------------------
# Issue #221 B1: markdown-escaped pipes inside cells are literal pipes
# ---------------------------------------------------------------------------


class TestMarkdownPipeEscapes:
    """Issue #221 B1: parser must honour ``\\|`` inside Manifest cells.

    Manifest descriptions sometimes reference shell pipelines or other
    prose that contains a literal pipe. Authors write such pipes as
    ``\\|`` (markdown-escape form). The parser must split rows on
    unescaped pipes only and restore ``\\|`` to ``|`` in each cell.
    """

    def test_escaped_pipe_in_change_cell_treated_as_literal(self) -> None:
        content = (
            "# T1: Verify completion script\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n"
            "|------|--------|\n"
            "| `tests/fixtures/completion/expected.sh` | "
            "run `my-cmd output \\| grep -v debug` and verify byte-for-byte match |\n\n"
            "## Definition of Done\n"
        )
        rows = parse_manifest(content)
        assert len(rows) == 1
        assert rows[0].file == "tests/fixtures/completion/expected.sh"
        assert "my-cmd output | grep -v debug" in rows[0].change

    def test_multiple_escaped_pipes_in_one_cell(self) -> None:
        content = (
            "## Changes Manifest\n\n"
            "| File | Change |\n"
            "|------|--------|\n"
            "| `a/b.sh` | run a \\| b \\| c three-stage pipeline |\n\n"
            "## End\n"
        )
        rows = parse_manifest(content)
        assert len(rows) == 1
        assert rows[0].change == "run a | b | c three-stage pipeline"

    def test_unescaped_extra_pipe_still_raises(self) -> None:
        content = "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a.py` | one | two |\n\n## End\n"
        with pytest.raises(ManifestParseError) as exc:
            parse_manifest(content)
        assert "exactly 2 columns" in str(exc.value)

    def test_escaped_pipe_in_file_cell_preserves_pipe(self) -> None:
        content = "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a \\| b` | change |\n\n## End\n"
        rows = parse_manifest(content)
        assert len(rows) == 1
        assert rows[0].file == "a | b"

    def test_no_pipe_no_change_in_behaviour(self) -> None:
        rows = parse_manifest(SAMPLE_ONE_ROW)
        assert len(rows) == 1
        assert rows[0].file == "src/example/parser.py"
        assert rows[0].change == "add feature"


class TestRepoPrefixedManifestRows:
    """Multi-repo work units encode the repo in the File cell as `` `<org/repo>` -- <path> ``."""

    def test_repo_prefixed_file_cell_yields_the_bare_path(self) -> None:
        content = (
            "## Changes Manifest\n\n"
            "| File | Change |\n"
            "|------|--------|\n"
            "| `caylent-solutions/devbench` -- `src/devbench/cli.py` | modify |\n\n"
            "## End\n"
        )
        rows = parse_manifest(content)
        assert len(rows) == 1
        assert rows[0].file == "src/devbench/cli.py"
        assert rows[0].change == "modify"
