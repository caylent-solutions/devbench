"""Deterministic post-processing passes for backlog work-unit files.

The bundled ``spec-to-backlog`` skill is LLM-driven and can produce
backlog files that pass the per-task rubric but still trip
``devbench validate-backlog`` because of mechanical issues a deterministic
pass can fix without changing the spec semantics. Each function in this
module fixes one such class of issue. Functions are pure with respect to
the filesystem inputs:

- Each takes a ``backlog_dir`` ``Path``.
- Each returns ``int`` -- the number of files modified.
- Each is idempotent: re-running on the same backlog after a fix should
  modify zero additional files.

Issue #221 A8-A17 enumerated nine candidate passes; this module
implements the three most-frequently-tripped ones (A11, A12, A13) and
provides the package scaffold for the rest.

The skill's Step 5 invokes these helpers via the Bash tool, e.g.::

    uv run python -c "from devbench.plugin_helpers import \\
        backlog_post_processor as bpp; \\
        import pathlib; \\
        bpp.run_all(pathlib.Path('backlog'))"

The returned counts are also useful for the audit-comment surface.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

# Regex matching the start of a Changes Manifest section in a work-unit file.
_MANIFEST_HEADER_RE = re.compile(r"^##\s+Changes Manifest\s*$", re.MULTILINE)

# Regex matching the start of the NEXT level-2 heading after Changes Manifest.
_NEXT_H2_RE = re.compile(r"^##\s+", re.MULTILINE)

# Regex matching a single Manifest table row (excluding header + separator).
# The Manifest is a 2-column markdown table: ``| <path> | <annotation> |``.
_MANIFEST_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")

# Regex matching a path-shaped backtick token used in AC / DoD prose.
# Matches ``foo/bar.py`` or ``foo/bar`` or ``.github/workflows/x.yml`` -- any
# backtick-quoted token that contains a ``/``. Used by suffix_ref_on_orphan_paths
# to identify candidates needing a trailing ``(ref)`` suffix.
_BACKTICK_PATH_RE = re.compile(r"`([^`\s]+/[^`\s]+)`")


def _iter_work_unit_files(backlog_dir: Path) -> Iterable[Path]:
    """Yield every work-unit ``.md`` file under *backlog_dir*.

    Excludes ``BACKLOG.md`` (the index, not a work unit) and any file under
    a ``config/`` subdirectory (operator config, not work-unit content).
    Sorted for deterministic ordering across runs.
    """
    for path in sorted(backlog_dir.rglob("*.md")):
        if path.name == "BACKLOG.md":
            continue
        if "config" in path.parts:
            continue
        yield path


def _split_manifest_section(text: str) -> tuple[str, str, str] | None:
    """Return ``(before, manifest_block, after)`` or ``None`` if no Manifest.

    The ``manifest_block`` includes the ``## Changes Manifest`` header line
    itself and everything up to (but not including) the next ``## `` heading
    or end-of-file. Callers can reassemble the file by concatenating the
    three pieces.
    """
    header_match = _MANIFEST_HEADER_RE.search(text)
    if not header_match:
        return None
    block_start = header_match.start()
    after_search_start = header_match.end()
    next_h2 = _NEXT_H2_RE.search(text, after_search_start)
    block_end = next_h2.start() if next_h2 else len(text)
    return text[:block_start], text[block_start:block_end], text[block_end:]


def sanitize_markdown_pipes_in_manifest(backlog_dir: Path) -> int:
    """Escape unescaped ``|`` characters inside Manifest cells (issue #221 A12).

    The validator's manifest parser now honours markdown-escaped pipes
    (``\\|``) inside cells (issue #221 B1), but skills sometimes emit raw
    ``|`` inside the *annotation* column when narrating a shell-pipeline
    example (``run cmd | grep -v debug``). That raw pipe makes the row
    look like a 3-column entry and trips ``ManifestParseError``.

    This pass scans every WU file's Manifest, and for any row containing
    more than the required 3 ``|`` characters (leading + separator +
    trailing) rewrites the extra pipes as ``\\|`` so the row parses as
    2 columns.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir):
        text = path.read_text(encoding="utf-8")
        parts = _split_manifest_section(text)
        if parts is None:
            continue
        before, block, after = parts
        new_lines: list[str] = []
        changed = False
        for line in block.splitlines(keepends=True):
            stripped = line.rstrip("\n")
            if not stripped.startswith("|") or stripped.startswith("|-"):
                new_lines.append(line)
                continue
            # Header line (``| File | Change |``) is 3 pipes; rows are 3 pipes.
            # Anything more is an extra in-cell ``|`` we need to escape.
            pipe_count = stripped.count("|") - stripped.count("\\|")
            if pipe_count <= 3:
                new_lines.append(line)
                continue
            # Walk the line and escape every pipe except the first, the second
            # (column separator), and the trailing pipe.
            new_line = _escape_inner_pipes(stripped) + ("\n" if line.endswith("\n") else "")
            new_lines.append(new_line)
            changed = True
        if changed:
            path.write_text(before + "".join(new_lines) + after, encoding="utf-8")
            modified += 1
    return modified


def _escape_inner_pipes(row: str) -> str:
    """Escape ``|`` characters that appear inside a Manifest row's cells.

    Preserves the three structural pipes (leading, column-separator,
    trailing) and escapes every other unescaped pipe. Input is a single
    Manifest row stripped of its trailing newline.
    """
    # Find the positions of unescaped pipes.
    positions: list[int] = []
    for idx, char in enumerate(row):
        if char != "|":
            continue
        if idx > 0 and row[idx - 1] == "\\":
            continue
        positions.append(idx)
    if len(positions) <= 3:
        return row
    # Preserve the first (leading), the second (column separator), and the last
    # (trailing). Escape everything between the 2nd and the last.
    preserve = {positions[0], positions[1], positions[-1]}
    result: list[str] = []
    for idx, char in enumerate(row):
        if idx in preserve or char != "|":
            result.append(char)
            continue
        if idx > 0 and row[idx - 1] == "\\":
            result.append(char)
            continue
        result.append("\\|")
    return "".join(result)


def dedupe_manifest_rows(backlog_dir: Path) -> int:
    """Remove duplicate rows from each Manifest section (issue #221 A13).

    When the spec-to-backlog skill produces a Manifest with two identical
    rows (same path, same annotation), the validator's
    ``_check_manifest_conflicts`` flags the duplication as a Manifest
    Conflict Rule violation within a single Task. This pass collapses
    identical adjacent or non-adjacent rows down to one entry, preserving
    first-occurrence order.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir):
        text = path.read_text(encoding="utf-8")
        parts = _split_manifest_section(text)
        if parts is None:
            continue
        before, block, after = parts
        new_block, changed = _dedupe_block_rows(block)
        if changed:
            path.write_text(before + new_block + after, encoding="utf-8")
            modified += 1
    return modified


def _dedupe_block_rows(block: str) -> tuple[str, bool]:
    """Dedupe Manifest data rows within a Manifest block; preserve header + separator.

    The block starts with ``## Changes Manifest`` and contains a 2-column
    markdown table. Header rows (``| File | Change |``) and the ``|---|---|``
    separator are kept verbatim. Subsequent data rows are de-duplicated by
    the ``(path, annotation)`` pair.
    """
    seen: set[tuple[str, str]] = set()
    output: list[str] = []
    changed = False
    saw_separator = False
    for line in block.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.startswith(("|-", "| -")):
            saw_separator = True
            output.append(line)
            continue
        if not saw_separator or not stripped.startswith("|"):
            output.append(line)
            continue
        match = _MANIFEST_ROW_RE.match(stripped)
        if match is None:
            output.append(line)
            continue
        key = (match.group(1).strip(), match.group(2).strip())
        if key in seen:
            changed = True
            continue
        seen.add(key)
        output.append(line)
    return "".join(output), changed


def suffix_ref_on_orphan_paths(backlog_dir: Path, manifest_paths: dict[Path, set[str]] | None = None) -> int:
    """Suffix ``(ref)`` on backtick-quoted path tokens in AC / DoD prose
    that do NOT appear in the same Task's Manifest (issue #221 A11).

    Validator Rule 20 (orphan path tokens) flags any backtick-quoted
    path-shaped token in a Task's Acceptance Criteria or Definition of
    Done sections that is not in the same Task's Changes Manifest, unless
    the token is suffixed ``(ref)`` to declare it a read-only reference.
    The spec-to-backlog skill often emits such tokens when it cites a
    pre-existing file; this pass adds the missing ``(ref)`` suffix
    automatically.

    Args:
        backlog_dir: Root of the backlog tree to scan.
        manifest_paths: Optional pre-computed ``{file_path: {manifest_paths}}``
            map. When ``None``, each file's own Manifest is parsed to derive
            the set of in-Manifest paths.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir):
        text = path.read_text(encoding="utf-8")
        in_manifest = manifest_paths.get(path) if manifest_paths else _extract_manifest_paths(text)
        if not in_manifest:
            continue
        new_text = _suffix_orphans_in_text(text, in_manifest)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            modified += 1
    return modified


def _extract_manifest_paths(text: str) -> set[str]:
    """Return the set of paths appearing as the first column of any Manifest row."""
    parts = _split_manifest_section(text)
    if parts is None:
        return set()
    _, block, _ = parts
    paths: set[str] = set()
    for line in block.splitlines():
        if not line.startswith("|") or line.startswith("|-"):
            continue
        match = _MANIFEST_ROW_RE.match(line)
        if match is None:
            continue
        cell = match.group(1).strip().strip("`")
        if cell.lower() == "file":
            continue
        paths.add(cell)
    return paths


def _suffix_orphans_in_text(text: str, in_manifest: set[str]) -> str:
    """Append ``(ref)`` after backtick-quoted path tokens that are not in the Manifest.

    Only operates inside the Acceptance Criteria and Definition of Done
    sections (the two sections Rule 20 scans). Tokens already followed by
    ``(ref)`` are left alone.
    """
    ac_section = _find_section_bounds(text, "## Acceptance Criteria")
    dod_section = _find_section_bounds(text, "## Definition of Done")
    if not ac_section and not dod_section:
        return text

    # Operate on the two scopes individually; build a fresh string.
    def _substitute(match: re.Match[str]) -> str:
        return _maybe_suffix(match, in_manifest)

    result = text
    for start, end in sorted(filter(None, [ac_section, dod_section]), reverse=True):
        scope = result[start:end]
        new_scope = _BACKTICK_PATH_RE.sub(_substitute, scope)
        result = result[:start] + new_scope + result[end:]
    return result


def _maybe_suffix(match: re.Match[str], in_manifest: set[str]) -> str:
    """Decide whether ``match`` needs a trailing ``(ref)`` suffix."""
    token = match.group(1)
    if token in in_manifest:
        return match.group(0)
    # Look one char ahead to see if the operator already wrote ``(ref)``.
    end = match.end()
    suffix_window = match.string[end : end + 6]
    if suffix_window.startswith((" (ref)", "(ref)")):
        return match.group(0)
    return f"`{token}` (ref)"


def _find_section_bounds(text: str, header: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` index range covering one section, or ``None``."""
    idx = text.find(header)
    if idx < 0:
        return None
    section_start = idx + len(header)
    next_h2 = _NEXT_H2_RE.search(text, section_start + 1)
    section_end = next_h2.start() if next_h2 else len(text)
    return section_start, section_end


def run_all(backlog_dir: Path) -> dict[str, int]:
    """Run every available post-processing pass over *backlog_dir*.

    Returns a ``{pass_name: files_modified}`` mapping the skill can log
    as audit output. The passes are run in a deterministic order so the
    audit row is stable across invocations.
    """
    return {
        "sanitize_markdown_pipes_in_manifest": sanitize_markdown_pipes_in_manifest(backlog_dir),
        "dedupe_manifest_rows": dedupe_manifest_rows(backlog_dir),
        "suffix_ref_on_orphan_paths": suffix_ref_on_orphan_paths(backlog_dir),
    }
