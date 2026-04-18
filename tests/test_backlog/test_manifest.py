"""Tests for devbench.backlog.manifest module."""

from __future__ import annotations

import pytest

from devbench.backlog.manifest import (
    EM_DASH,
    MANIFEST_HEADER,
    ManifestParseError,
    ManifestRow,
    append_rows,
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
            row.file = "other"  # type: ignore[misc]

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
