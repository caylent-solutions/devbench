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
- Each accepts the optional ``scope_paths`` and ``force_terminal`` arguments
  documented below; passes default to skipping terminal-status files so a
  fresh materialisation cannot retroactively mutate already-done work
  (issue #226).

Issue #221 A8-A17 enumerated nine candidate passes; this module
implements the three most-frequently-tripped ones (A11, A12, A13) and
provides the package scaffold for the rest.

The skill's Step 5 invokes these helpers via the Bash tool, e.g.::

    uv run python -c "from devbench.plugin_helpers import \\
        backlog_post_processor as bpp; \\
        import pathlib; \\
        bpp.run_all(pathlib.Path('backlog'), \\
            scope_paths=[pathlib.Path('backlog/E17-...')])"

The returned counts are also useful for the audit-comment surface.

Scope and terminal-status guards (issue #226)
=============================================

Every pass accepts two optional arguments that control which work-unit
files it touches:

- ``scope_paths``: an iterable of ``Path`` objects naming the epic
  directories the pass may walk. ``None`` (the default) walks the full
  ``backlog_dir`` tree. When supplied, only files under those scope
  paths are considered candidates.
- ``force_terminal``: when ``False`` (the default), files whose
  ``## Status:`` line is one of the terminal states in
  ``_TERMINAL_STATUSES`` are skipped even when otherwise in scope. Set to
  ``True`` to override the guard (one-time mass migrations of an old
  backlog under a new convention).

Together, the two guards make the post-processor safe to run from a
``spec-to-backlog`` invocation that adds new epics on top of an existing
populated backlog: the operator-supplied ``scope_paths`` lists only the
newly-authored epics, and the terminal-status guard catches any stray
done / declined file the scope happened to include.
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

# Regex extracting the ``## Status:`` value from a work-unit file. Used by
# ``_is_terminal_status`` to decide whether to skip a file. Mirrors the
# canonical status-line shape every work unit ships with.
_STATUS_LINE_RE = re.compile(r"^##\s+Status:\s*(\S+)", re.MULTILINE)

# Statuses considered terminal: passes never modify a file in one of these
# states unless ``force_terminal=True`` is passed. ``declined`` is included
# alongside ``done`` because a declined work unit is also frozen --
# retroactive mechanical edits would reopen settled decisions.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "declined"})


def _iter_work_unit_files(
    backlog_dir: Path,
    scope_paths: Iterable[Path] | None = None,
) -> Iterable[Path]:
    """Yield every work-unit ``.md`` file under *backlog_dir* (or *scope_paths*).

    Excludes ``BACKLOG.md`` (the index, not a work unit) and any file under
    a ``config/`` subdirectory (operator config, not work-unit content).
    Sorted for deterministic ordering across runs.

    Args:
        backlog_dir: Root of the backlog tree. Used as the walk root when
            ``scope_paths`` is ``None``.
        scope_paths: Optional iterable of directories to walk instead of
            ``backlog_dir`` (issue #226). When supplied, the walk yields
            only files under one of the supplied directories. Duplicate
            files (the same path reachable from multiple scope roots) are
            yielded exactly once.

    Raises:
        FileNotFoundError: when a supplied ``scope_paths`` entry does not
            exist. The error is explicit per the fail-fast policy; an
            operator-supplied typo in the scope list is a defect, not
            something the helper should silently absorb.
    """
    if scope_paths is None:
        walk_roots = [backlog_dir]
    else:
        walk_roots = [Path(p) for p in scope_paths]
        for root in walk_roots:
            if not root.exists():
                raise FileNotFoundError(
                    f"scope_paths entry does not exist: {root}. "
                    "Check the caller's scope_paths list against the on-disk "
                    "epic directories."
                )
    seen: set[Path] = set()
    for root in walk_roots:
        for path in sorted(root.rglob("*.md")):
            if path.name == "BACKLOG.md":
                continue
            if "config" in path.parts:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def _is_terminal_status(text: str) -> bool:
    """Return ``True`` when the work-unit ``text`` carries a terminal status.

    Terminal statuses (``done``, ``declined``) freeze the work unit from
    further mechanical mutation. Files lacking a ``## Status:`` line return
    ``False`` so the passes still operate on freshly-authored files where
    the status line has not yet been written.
    """
    match = _STATUS_LINE_RE.search(text)
    if match is None:
        return False
    return match.group(1).strip().lower() in _TERMINAL_STATUSES


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


def normalize_manifest_column_count(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> int:
    """Collapse N-column Manifest tables (3+) to the canonical 2-column form (#227).

    The validator's ``parse_manifest`` enforces exactly two columns
    (``| File | Change |``). LLM authors -- including sub-agents the
    skill spawns via fan-out -- sometimes emit 3-column variants
    (``| Repo | Path | Action |`` for multi-repo work) or 4-column
    variants (``| Repo | Path | Action | Notes |``). The validator
    fail-fasts on those with ``ManifestParseError: Manifest row must
    have exactly 2 columns``, blocking the entire backlog.

    This pass rewrites N-column Manifests losslessly:

    - ``| Repo | Path | Action |`` (header[0] is ``Repo``): File cell
      becomes ``<repo> -- <path>``; Change cell is the third column.
    - Anything else with ``N >= 3`` columns: File cell is the first
      column; Change cell joins the remaining columns with `` -- ``
      so no information is dropped.

    Already-canonical 2-column tables are skipped (no mutation).

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit the
            walk to (issue #226).
        force_terminal: When ``True``, also rewrite files with terminal
            status. Default ``False`` skips them.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
        parts = _split_manifest_section(text)
        if parts is None:
            continue
        before, block, after = parts
        new_block, changed = _collapse_manifest_block(block)
        if changed:
            path.write_text(before + new_block + after, encoding="utf-8")
            modified += 1
    return modified


def _split_row_cells(stripped: str) -> list[str] | None:
    """Split a Markdown table row into cell contents.

    ``stripped`` is the row with leading / trailing whitespace removed.
    Returns ``None`` when the line is not a well-formed table row
    (missing leading or trailing ``|``).

    Honours backslash-escaped pipes (``\\|``) inside cells: the escaped
    sequence is treated as part of the cell content, NOT as a cell
    separator. Mirrors the parser's escape handling in
    ``src/devbench/backlog/manifest.py``.
    """
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    inner = stripped[1:-1]
    cells: list[str] = []
    buf: list[str] = []
    idx = 0
    while idx < len(inner):
        char = inner[idx]
        if char == "\\" and idx + 1 < len(inner) and inner[idx + 1] == "|":
            buf.append("|")
            idx += 2
            continue
        if char == "|":
            cells.append("".join(buf).strip())
            buf = []
            idx += 1
            continue
        buf.append(char)
        idx += 1
    cells.append("".join(buf).strip())
    return cells


def _collapse_manifest_block(block: str) -> tuple[str, bool]:
    """Collapse an N-column Manifest table block to the canonical 2-column form.

    Returns ``(new_block, changed)``. ``changed`` is ``False`` when the
    block is already in canonical 2-column form or when no table can be
    located inside the block.
    """
    lines = block.splitlines(keepends=True)

    # Locate the header row (first non-separator pipe line).
    header_idx: int | None = None
    header_cells: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n").strip()
        if not stripped.startswith("|") or stripped.startswith("|-"):
            continue
        cells = _split_row_cells(stripped)
        if cells is None:
            continue
        header_idx = i
        header_cells = cells
        break

    if header_idx is None or len(header_cells) <= 2:
        return block, False

    # Locate the separator row (first ``|---|---|`` line after the header).
    sep_idx: int | None = None
    for i in range(header_idx + 1, len(lines)):
        if lines[i].rstrip("\n").strip().startswith("|-"):
            sep_idx = i
            break

    # When the header is ``| Repo | Path | Action |`` we collapse the
    # first two columns into the File cell; otherwise we treat the
    # first column as File and join the rest into Change with `` -- ``.
    repo_first = header_cells[0].lower() == "repo"

    new_lines: list[str] = []
    new_lines.extend(lines[:header_idx])
    new_lines.append("| File | Change |\n")
    new_lines.append("|------|--------|\n")

    data_start = (sep_idx + 1) if sep_idx is not None else (header_idx + 1)
    for line in lines[data_start:]:
        stripped = line.rstrip("\n").strip()
        if not stripped.startswith("|"):
            new_lines.append(line)
            continue
        cells = _split_row_cells(stripped)
        if cells is None or len(cells) <= 2:
            new_lines.append(line)
            continue
        if repo_first and len(cells) >= 3:
            repo_value = cells[0].strip().strip("`").strip()
            path_value = cells[1].strip().strip("`").strip()
            file_cell = f"{repo_value} -- {path_value}"
            change_cell = " -- ".join(c.strip() for c in cells[2:] if c.strip())
        else:
            file_cell = cells[0].strip().strip("`").strip()
            change_cell = " -- ".join(c.strip() for c in cells[1:] if c.strip())
        if not change_cell:
            # An N-column row whose tail cells were all empty collapses to
            # a single-column row, which is malformed. Skip the rewrite for
            # that row so the validator surfaces the underlying defect
            # instead of silently absorbing it.
            new_lines.append(line)
            continue
        new_lines.append(f"| `{file_cell}` | {change_cell} |\n")

    return "".join(new_lines), True


def sanitize_markdown_pipes_in_manifest(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> int:
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

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit the
            walk to (issue #226). ``None`` walks the full tree.
        force_terminal: When ``True``, also rewrite files with terminal
            status (``done`` / ``declined``). Default ``False`` skips
            terminal-status files so already-frozen work is preserved.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
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


def dedupe_manifest_rows(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> int:
    """Remove duplicate rows from each Manifest section (issue #221 A13).

    When the spec-to-backlog skill produces a Manifest with two identical
    rows (same path, same annotation), the validator's
    ``_check_manifest_conflicts`` flags the duplication as a Manifest
    Conflict Rule violation within a single Task. This pass collapses
    identical adjacent or non-adjacent rows down to one entry, preserving
    first-occurrence order.

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit the
            walk to (issue #226).
        force_terminal: When ``True``, also dedupe files with terminal
            status. Default ``False`` skips them.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
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


def suffix_ref_on_orphan_paths(
    backlog_dir: Path,
    manifest_paths: dict[Path, set[str]] | None = None,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> int:
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
        scope_paths: Optional iterable of epic directories to limit the
            walk to (issue #226).
        force_terminal: When ``True``, also suffix files with terminal
            status. Default ``False`` skips them so already-frozen work
            is not retroactively mutated.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
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


# Canonical regex matching one task ID -- ``E<digits>-F<digits>-S<digits>-T<digits>``.
# Used by ``suffix_na_on_non_python_tasks`` to detect Task work units (skipping
# Epic / Feature / Story files, which never carry AC-FINAL lines).
_TASK_ID_RE = re.compile(r"^E\d+-F\d+-S\d+-T\d+$")

# Regex matching an Acceptance Criteria checkbox row. Captures the AC ID so
# the pass can decide whether the row is one of the Python-tooling
# AC-FINAL-* lines.
_AC_CHECKBOX_RE = re.compile(r"^(\s*- \[[ xX]\] )(AC-FINAL-\d{3})\b(.*)$")


# Regex matching the canonical task-ID form ``E<n>[-F<n>][-S<n>][-T<n>]``.
# Used by ``normalize_dep_ids`` to strip slug-suffix material after the
# canonical prefix when the operator wrote ``E16-test-cleanup`` instead
# of the bare canonical ``E16``.
_CANONICAL_ID_PREFIX_RE = re.compile(r"^(E\d+(?:-F\d+(?:-S\d+(?:-T\d+)?)?)?)")


def normalize_dep_ids(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> int:
    """Rewrite slug-form dep IDs to canonical regex form (#229).

    The validator's ``_check_dep_id_format`` rule enforces the canonical
    task-ID regex ``^E\\d+(-F\\d+)?(-S\\d+)?(-T\\d+)?$`` on the first
    column of every row in ``## Dependencies`` and ``### Depends On
    This``. When an author cites an existing-backlog epic by its
    directory slug (e.g., ``E16-test-cleanup``), the validator fails
    with ``dependency ID '<slug>' does not match the canonical
    task-ID regex``. This pass strips the slug suffix to leave the
    canonical prefix (``E16``).

    Walks the two dependency-table sections in each work-unit file:

    - ``## Dependencies`` (level-2 heading, upstream deps)
    - ``### Depends On This`` (level-3 heading, downstream deps)

    For each table row whose first cell matches ``E<n>-<lowercase>``,
    the cell is replaced with the canonical prefix. Cells already in
    canonical form, the ``none`` placeholder, header rows, and
    separator rows are left alone.

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit the
            walk to (issue #226).
        force_terminal: When ``True``, also rewrite files with terminal
            status. Default ``False`` skips them.

    Returns the number of files modified.
    """
    modified = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
        new_text, changed = _normalize_dep_ids_in_text(text)
        if changed:
            path.write_text(new_text, encoding="utf-8")
            modified += 1
    return modified


def _normalize_dep_ids_in_text(text: str) -> tuple[str, bool]:
    """Rewrite slug-form dep IDs inside ``## Dependencies`` and ``### Depends On This``.

    Returns ``(new_text, changed)``. ``changed`` is ``False`` when no
    rewrites were necessary.
    """
    # Find both dep-table sections and rewrite each in place. The two
    # sections live at different heading levels; we handle both.
    sections = (
        ("## Dependencies", "## "),
        ("### Depends On This", "### "),
    )
    new_text = text
    changed = False
    for header, _ in sections:
        bounds = _find_dep_section_bounds(new_text, header)
        if bounds is None:
            continue
        start, end = bounds
        body = new_text[start:end]
        rewritten = _rewrite_dep_table_first_cells(body)
        if rewritten != body:
            new_text = new_text[:start] + rewritten + new_text[end:]
            changed = True
    return new_text, changed


def _find_dep_section_bounds(text: str, header: str) -> tuple[int, int] | None:
    """Return ``(body_start, body_end)`` for ``header`` or ``None``.

    The body starts immediately after the header line and ends at the
    next heading of the same-or-higher level (``## `` for level-2,
    ``### `` or ``## `` for level-3) or end-of-file.
    """
    idx = text.find(header)
    if idx < 0:
        return None
    body_start = idx + len(header)
    # Advance past the header's trailing newline.
    nl = text.find("\n", body_start)
    if nl < 0:
        return None
    body_start = nl + 1
    if header.startswith("### "):
        # Level-3 header: stops at next level-2 or level-3 heading.
        next_heading = re.compile(r"^(##|###)\s+", re.MULTILINE)
    else:
        # Level-2 header: stops at next level-2 heading.
        next_heading = re.compile(r"^##\s+", re.MULTILINE)
    match = next_heading.search(text, body_start)
    body_end = match.start() if match else len(text)
    return body_start, body_end


def _rewrite_dep_table_first_cells(body: str) -> str:
    """Rewrite the first cell of each dep-table row to canonical-ID form."""
    new_lines: list[str] = []
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n").strip()
        # Skip non-table lines and header / separator rows.
        if not stripped.startswith("|") or stripped.startswith("|-"):
            new_lines.append(line)
            continue
        cells = _split_row_cells(stripped)
        if cells is None or not cells:
            new_lines.append(line)
            continue
        first = cells[0].strip()
        # Skip the table header ``| ID | Title | Status |`` and the
        # ``| none | | |`` sentinel.
        if first.lower() in ("id", "none", ""):
            new_lines.append(line)
            continue
        match = _CANONICAL_ID_PREFIX_RE.match(first)
        if match is None:
            new_lines.append(line)
            continue
        canonical = match.group(1)
        if canonical == first:
            # Already canonical; nothing to rewrite.
            new_lines.append(line)
            continue
        # Replace the slug-form ID with the canonical prefix. Preserve
        # the cell padding the original row used (the leading column is
        # ``| <ID> |``).
        old_cell = f"| {first} |"
        new_cell = f"| {canonical} |"
        if old_cell in line:
            new_lines.append(line.replace(old_cell, new_cell, 1))
            continue
        # Fall back to a regex replace tolerant of variable whitespace.
        new_lines.append(re.sub(rf"\|\s*{re.escape(first)}\s*\|", new_cell, line, count=1))
    return "".join(new_lines)


def suffix_na_on_non_python_tasks(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> int:
    """Append the canonical N/A tier suffix to Python-tooling AC-FINAL lines
    on non-Python task files (#228).

    Validator Rule 13 (``_check_language_ac_alignment`` in
    ``devbench.backlog.manager``) requires AC-FINAL-002 (ruff format),
    003 (ruff check), 004 (mypy), 005 (pytest tier), 006 (pytest other
    tier), 008 (bandit), and 014 (coverage) to carry the explicit
    suffix ``-- N/A for <Tier> Tasks (no Python source authored)``
    whenever the task's Changes Manifest contains zero ``.py`` paths.
    The skill prompt's Step 5b rubric now mandates this, but tasks
    authored before the rubric update fail on first validate. This
    pass adds the missing suffix deterministically.

    Tier is derived from the task's Manifest paths via the same
    classifier the validator uses
    (``BacklogManager._classify_manifest_tier``): one of ``YAML``,
    ``Markdown``, ``TOML``, ``HCL``, ``JSON``, ``XML``, or ``Mixed``.
    Python-tier and Mixed-tier tasks are skipped (Mixed includes at
    least one ``.py`` file so the Python ACs apply to that subset).

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit the
            walk to (issue #226).
        force_terminal: When ``True``, also rewrite files with terminal
            status. Default ``False`` skips them.

    Returns the number of files modified.
    """
    # Imported here so the module has no import-time dependency on the
    # validator class (and the post-processor remains usable from
    # standalone scripts even if the validator side-effects change).
    from devbench.backlog.manager import BacklogManager
    from devbench.backlog.manifest import ManifestParseError, parse_manifest

    ac_final_ids: frozenset[str] = BacklogManager._AC_FINAL_LANGUAGE_TIER_IDS

    modified = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        if not _TASK_ID_RE.match(path.stem):
            continue
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
        try:
            manifest_rows = parse_manifest(text)
        except ManifestParseError:
            # Other validator rules surface the underlying defect; this
            # pass cannot reason about an unparseable Manifest.
            continue
        paths = [row.file for row in manifest_rows]
        tier = BacklogManager._classify_manifest_tier(paths)
        if tier in ("", "Python", "Mixed"):
            continue
        suffix = f" -- N/A for {tier} Tasks (no Python source authored)"

        new_lines: list[str] = []
        changed = False
        for line in text.splitlines(keepends=True):
            match = _AC_CHECKBOX_RE.match(line.rstrip("\n"))
            if match is None:
                new_lines.append(line)
                continue
            ac_id = match.group(2)
            if ac_id not in ac_final_ids:
                new_lines.append(line)
                continue
            tail = match.group(3)
            if "-- N/A" in tail:
                new_lines.append(line)
                continue
            # Append the suffix at the end of the line (preserving the
            # trailing newline if present).
            ending = "\n" if line.endswith("\n") else ""
            new_lines.append(f"{match.group(1)}{ac_id}{tail}{suffix}{ending}")
            changed = True
        if changed:
            path.write_text("".join(new_lines), encoding="utf-8")
            modified += 1
    return modified


def run_all(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
) -> dict[str, int]:
    """Run every available post-processing pass over *backlog_dir*.

    Returns a ``{pass_name: files_modified}`` mapping the skill can log
    as audit output. The passes are run in a deterministic order so the
    audit row is stable across invocations.

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit the
            walk to. ``None`` walks the full tree. Recommended for any
            ``spec-to-backlog`` invocation that adds new epics on top of
            an existing populated backlog (issue #226).
        force_terminal: When ``True``, also rewrite files with terminal
            status. Default ``False`` skips ``done`` / ``declined`` work
            so already-frozen tasks are not retroactively mutated.

    Scope paths are accepted as keyword-only arguments so the legacy
    single-arg call form ``run_all(backlog_dir)`` keeps working; the
    only behavioural change for legacy callers is that terminal-status
    files are now skipped by default (this is the issue #226 fix).
    """
    # The scope list is materialised once so each pass walks the same
    # set of files. Without this, an iterable would be exhausted by the
    # first pass and subsequent passes would silently no-op.
    materialised_scope = list(scope_paths) if scope_paths is not None else None
    return {
        "normalize_manifest_column_count": normalize_manifest_column_count(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
        ),
        "sanitize_markdown_pipes_in_manifest": sanitize_markdown_pipes_in_manifest(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
        ),
        "dedupe_manifest_rows": dedupe_manifest_rows(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
        ),
        "normalize_dep_ids": normalize_dep_ids(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
        ),
        "suffix_ref_on_orphan_paths": suffix_ref_on_orphan_paths(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
        ),
        "suffix_na_on_non_python_tasks": suffix_na_on_non_python_tasks(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
        ),
    }
