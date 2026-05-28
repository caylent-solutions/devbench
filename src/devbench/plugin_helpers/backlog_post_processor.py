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

# Canonical regex matching any work-unit ID at any of the four levels.
_WORK_UNIT_ID_RE = re.compile(r"^E\d+(?:-F\d+(?:-S\d+(?:-T\d+)?)?)?$")

# Canonical regex matching an Epic ID specifically (level 1).
_EPIC_ID_RE = re.compile(r"^E\d+$")


class BacklogAppendCollisionError(RuntimeError):
    """Raised when ``regenerate_backlog_index`` detects a colliding epic ID.

    The collision means the on-disk scope path contains an epic ID that
    already exists in the BACKLOG.md Full Work Unit Index with a
    different file path. Per the fail-fast policy the pass writes
    nothing and asks the operator to resolve the collision (re-number
    the new epic or rename the existing directory) before retrying.
    """


def regenerate_backlog_index(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    workspace_root: Path | None = None,
) -> int:
    """Append newly-authored epic + leaf-task rows to ``BACKLOG.md`` (#225).

    Materialising a new spec on top of an existing backlog must preserve
    every existing E1...E16 row verbatim and only APPEND new rows for
    the epics this materialisation produced. Today the skill's Step 6
    re-writes BACKLOG.md from scratch, so the operator had to merge
    the new content into the existing file by hand.

    This pass implements append-first semantics:

    1. If ``BACKLOG.md`` does not exist at ``workspace_root / BACKLOG.md``
       (or ``workspace_root`` is ``None``), the pass returns 0 -- the
       skill falls back to its existing greenfield write path.
    2. If ``BACKLOG.md`` exists, parses the existing Status Summary and
       Full Work Unit Index. For each scope path, walks the task files
       and appends ROWS NOT ALREADY IN THE INDEX. Existing rows are
       byte-for-byte preserved.
    3. If any new epic ID collides with an existing index ID
       (different file path), raises ``BacklogAppendCollisionError``
       and writes nothing.

    Args:
        backlog_dir: Root of the backlog tree (used for scope walks).
        scope_paths: Optional iterable of epic directories to walk.
            When ``None``, walks the full ``backlog_dir`` tree.
        workspace_root: Root of the workspace -- the directory holding
            ``BACKLOG.md``. When ``None``, the pass is a no-op (the
            skill must supply the workspace root explicitly).

    The terminal-status guard does not apply to this pass: existing
    rows in BACKLOG.md are byte-for-byte preserved regardless of
    status, so there is nothing for ``force_terminal`` to override.

    Returns:
        ``1`` when ``BACKLOG.md`` was modified, ``0`` otherwise.

    Raises:
        BacklogAppendCollisionError: when a new epic ID collides with
            an existing index row that references a different file
            path.
    """
    if workspace_root is None:
        return 0
    backlog_md = workspace_root / "BACKLOG.md"

    materialised_scope: list[Path] | None = list(scope_paths) if scope_paths is not None else None

    # Gather (id, type, status, file_path) tuples for every work-unit
    # file under the requested scope. ``_TASK_ID_RE`` and friends
    # classify the level from the filename stem.
    new_rows = _collect_work_unit_rows(backlog_dir, materialised_scope, workspace_root)

    if not backlog_md.exists():
        # Greenfield: skip this pass; the skill's existing write path
        # handles greenfield invocations.
        return 0

    existing_text = backlog_md.read_text(encoding="utf-8")
    existing_ids = _parse_existing_index_ids(existing_text)

    # Filter new rows to the ones not already in the existing index.
    rows_to_append = []
    for row in new_rows:
        if row["id"] in existing_ids:
            # Collision: the same ID appears in the existing index but
            # may reference a different file. Compare paths.
            if existing_ids[row["id"]] != row["path"]:
                raise BacklogAppendCollisionError(
                    f"Cannot append {row['id']}: existing index row references "
                    f"{existing_ids[row['id']]!r} but new work-unit file is "
                    f"{row['path']!r}. Resolve by re-numbering the new epic or "
                    f"renaming the existing directory."
                )
            # Same ID + same path: already present, no append needed.
            continue
        rows_to_append.append(row)

    if not rows_to_append:
        return 0

    new_text = _append_rows_to_backlog_index(existing_text, rows_to_append)
    if new_text == existing_text:
        return 0

    backlog_md.write_text(new_text, encoding="utf-8")
    return 1


def _classify_work_unit_level(stem: str) -> str | None:
    """Return ``Epic`` / ``Feature`` / ``Story`` / ``Task`` from the file stem, or None."""
    if re.fullmatch(r"E\d+", stem):
        return "Epic"
    if re.fullmatch(r"E\d+-F\d+", stem):
        return "Feature"
    if re.fullmatch(r"E\d+-F\d+-S\d+", stem):
        return "Story"
    if re.fullmatch(r"E\d+-F\d+-S\d+-T\d+", stem):
        return "Task"
    return None


def _collect_work_unit_rows(
    backlog_dir: Path,
    scope_paths: Iterable[Path] | None,
    workspace_root: Path,
) -> list[dict[str, str]]:
    """Return a list of row dicts for each work-unit file under scope.

    Each dict has keys ``id``, ``type``, ``status``, ``title``, ``path``
    (path is relative to ``workspace_root`` so the BACKLOG.md row
    matches the canonical ``backlog/...`` shape).
    """
    rows: list[dict[str, str]] = []
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        level = _classify_work_unit_level(path.stem)
        if level is None:
            continue
        text = path.read_text(encoding="utf-8")
        status_match = _STATUS_LINE_RE.search(text)
        status = status_match.group(1).strip().lower() if status_match else ""
        title_match = re.search(r"^#\s+([^\n]+)\n", text)
        title = title_match.group(1).strip() if title_match else path.stem
        # Strip the ID prefix from the title if the H1 is ``# <ID>: <title>``.
        if ":" in title and title.split(":", 1)[0].strip() == path.stem:
            title = title.split(":", 1)[1].strip()
        try:
            rel_path = path.relative_to(workspace_root).as_posix()
        except ValueError:
            # File is outside workspace_root; record the absolute path.
            rel_path = str(path)
        rows.append(
            {
                "id": path.stem,
                "type": level,
                "status": status,
                "title": title,
                "path": rel_path,
            }
        )
    return rows


def _parse_existing_index_ids(content: str) -> dict[str, str]:
    """Return ``{id: file_path}`` for every row in the existing Full Work Unit Index.

    Only rows whose first cell matches the canonical work-unit-ID regex
    are included. The Status Summary table's rows have their own format
    (the ID column is the epic ID, not a path), so this parser focuses
    on the Full Work Unit Index where the row carries a path.
    """
    ids: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _split_row_cells(stripped)
        if cells is None or len(cells) < 2:
            continue
        first = cells[0].strip()
        if not _WORK_UNIT_ID_RE.match(first):
            continue
        # Find the file path in the row: look for a backtick-wrapped
        # ``backlog/.../*.md`` token in any cell.
        file_path = ""
        for cell in cells:
            token = cell.strip().strip("`")
            if token.startswith("backlog/") and token.endswith(".md"):
                file_path = token
                break
        ids[first] = file_path
    return ids


def _append_rows_to_backlog_index(existing_text: str, rows: list[dict[str, str]]) -> str:
    """Append ``rows`` to the existing BACKLOG.md tables.

    For each row, two operations:
    - Status Summary table: append one row per NEW epic ID with the
      computed per-status counts of its children (sourced from the
      ``rows`` list itself; existing epics in the table are left
      alone).
    - Full Work Unit Index table: append one row per work unit (any
      level) whose ID is not already in the index.

    Existing rows in both tables are byte-for-byte preserved.
    """
    # Group new rows by epic for Status Summary aggregation.
    by_epic: dict[str, list[dict[str, str]]] = {}
    new_epics: list[str] = []
    new_epic_titles: dict[str, str] = {}
    for row in rows:
        epic_id = row["id"].split("-", 1)[0]
        if row["id"] == epic_id and epic_id not in new_epic_titles:
            new_epic_titles[epic_id] = row["title"]
        by_epic.setdefault(epic_id, []).append(row)
    for row in rows:
        epic_id = row["id"].split("-", 1)[0]
        if epic_id not in new_epics and epic_id in new_epic_titles:
            new_epics.append(epic_id)

    with_summary = _append_status_summary_rows(existing_text, new_epics, new_epic_titles, by_epic)
    return _append_full_index_rows(with_summary, rows)


def _table_body_end(text: str, after_header: int) -> int | None:
    """Return the offset at which to append new rows to a Markdown table.

    Walks forward from ``after_header`` (the position immediately after
    the table's header row's terminator) through the separator row and
    all subsequent pipe-prefixed data rows. Returns the offset just
    AFTER the last data row's trailing newline -- the position at which
    a caller can splice new rows.

    Returns ``None`` when no separator row is found, indicating the
    text does not contain a valid Markdown table starting at this
    header.
    """
    # ``after_header`` points to the position of the header row's
    # trailing newline character (or end-of-string). Advance past it.
    pos = after_header
    if pos < len(text) and text[pos] == "\n":
        pos += 1
    # The separator row follows. Match it explicitly. The character
    # class explicitly excludes ``\n`` so the greedy ``+`` cannot
    # overflow into the next data row -- ``\s`` would include ``\n``
    # and an overflowing greedy match plus backtrack would land the
    # match end one char inside the data row.
    sep_match = re.match(r"\|[-| \t]+\|[ \t]*\n?", text[pos:])
    if sep_match is None:
        return None
    pos += sep_match.end()
    # Walk forward through every consecutive pipe-prefixed line.
    while pos < len(text):
        line_end = text.find("\n", pos)
        if line_end < 0:
            # Last line without trailing newline.
            line = text[pos:]
            if line.startswith("|"):
                pos = len(text)
            break
        line = text[pos : line_end + 1]
        if not line.startswith("|"):
            break
        pos = line_end + 1
    return pos


def _append_status_summary_rows(
    text: str,
    new_epics: list[str],
    epic_titles: dict[str, str],
    by_epic: dict[str, list[dict[str, str]]],
) -> str:
    """Append new epic rows to the Status Summary table.

    Counts EXCLUDE the epic file itself, matching the validator's
    ``_compute_epic_counts`` semantics (issue #229 fix). The table's
    column order is preserved verbatim from the existing header.
    """
    # Locate the Status Summary table (header row that starts ``| Epic |``).
    header_match = re.search(r"^\| Epic \| [^\n]+ \|\s*$", text, re.MULTILINE)
    if header_match is None:
        return text
    body_end = _table_body_end(text, header_match.end())
    if body_end is None:
        return text

    # Parse the header to learn column order.
    header_cells = _split_row_cells(text[header_match.start() : header_match.end()].strip())
    if header_cells is None:
        return text
    # Build new rows matching the column order.
    status_columns = [c.strip().lower() for c in header_cells]
    new_rows_text = ""
    for epic_id in new_epics:
        children = [r for r in by_epic.get(epic_id, []) if r["id"] != epic_id]
        counts = {
            "done": sum(1 for c in children if c["status"] == "done"),
            "in progress": sum(1 for c in children if c["status"] == "in-progress"),
            "in queue": sum(1 for c in children if c["status"] == "in-queue"),
            "in review": sum(1 for c in children if c["status"] == "in-review"),
            "blocked": sum(1 for c in children if c["status"] == "blocked"),
            "declined": sum(1 for c in children if c["status"] == "declined"),
            "draft": sum(1 for c in children if c["status"] == "draft"),
            "hold": sum(1 for c in children if c["status"] == "hold"),
        }
        cells = []
        for col in status_columns:
            if col == "epic":
                cells.append(epic_id)
            elif col in ("title",):
                cells.append(epic_titles.get(epic_id, ""))
            elif col == "total":
                cells.append(str(len(children)))
            else:
                cells.append(str(counts.get(col, 0)))
        new_rows_text += "| " + " | ".join(cells) + " |\n"

    if not new_rows_text:
        return text

    return text[:body_end] + new_rows_text + text[body_end:]


def _index_row_sort_key(row: dict[str, str]) -> tuple[int, ...]:
    """Sort key that places Epic rows first, then Features, Stories, Tasks.

    Within the same level, sort lexicographically by ID so the resulting
    order matches the natural reading flow operators expect when
    inspecting the index.
    """
    parts = row["id"].split("-")
    nums: list[int] = []
    for part in parts:
        match = re.match(r"[A-Z](\d+)", part)
        nums.append(int(match.group(1)) if match else 0)
    return tuple(nums)


_INDEX_COLUMN_FIELD: dict[str, str] = {
    "id": "id",
    "title": "title",
    "type": "type",
    "status": "status",
}
_INDEX_DEPS_HEADERS: frozenset[str] = frozenset({"dependencies", "depends on"})
_INDEX_PATH_HEADERS: frozenset[str] = frozenset({"file path", "changed files", "file"})


def _cell_for_index_column(column: str, row: dict[str, str]) -> str:
    """Return the cell content for ``column`` in the Full Work Unit Index.

    Maps column-header text (case-insensitive) to the appropriate field
    on ``row``. Unknown columns (e.g., operator-added custom columns)
    are left blank so the surrounding table layout stays intact.
    """
    col_l = column.strip().lower()
    field = _INDEX_COLUMN_FIELD.get(col_l)
    if field is not None:
        return row[field]
    if col_l in _INDEX_DEPS_HEADERS:
        return "None"
    if col_l in _INDEX_PATH_HEADERS:
        return f"`{row['path']}`"
    return ""


def _append_full_index_rows(text: str, rows: list[dict[str, str]]) -> str:
    """Append new work-unit rows to the Full Work Unit Index table.

    The index's column order is detected from the existing header. Each
    new row populates the cells in that order; columns not derivable
    from the row dict (e.g., custom operator-added columns) are left
    blank.
    """
    # Locate the Full Work Unit Index header. Typical shapes:
    #   | ID | Title | Type | Status | Dependencies | Repo | File Path |
    #   | ID | Title | Status | Repo | Branch | Depends On | Changed Files |
    # The header always starts with ``| ID |``.
    header_match = re.search(r"^\| ID \| [^\n]+ \|\s*$", text, re.MULTILINE)
    if header_match is None:
        return text
    body_end = _table_body_end(text, header_match.end())
    if body_end is None:
        return text

    header_cells = _split_row_cells(text[header_match.start() : header_match.end()].strip())
    if header_cells is None:
        return text

    new_rows_text = ""
    for row in sorted(rows, key=_index_row_sort_key):
        cells = [_cell_for_index_column(col, row) for col in header_cells]
        new_rows_text += "| " + " | ".join(cells) + " |\n"

    if not new_rows_text:
        return text

    return text[:body_end] + new_rows_text + text[body_end:]


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


def verify_code_standards_canonical(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
    workspace_root: Path | None = None,
) -> int:
    """Report (but do not mutate) tasks whose Code Standards block has drifted (#230).

    Walks every Task work-unit file in scope and compares the contents
    of its ``### Code Standards`` block (excluding the
    ``#### Error Handling Contract`` subsection, which is intentionally
    task-specific) against the canonical body returned by
    ``code_standards_template.canonical_body_excluding_error_contract``.

    This is a CHECK-ONLY pass: it counts drifted task files but never
    rewrites them. The operator decides whether to fix manually or via
    a future regenerate pass; the audit row is the surfacing
    mechanism. Returning a non-zero count is the signal that drift
    exists, not an error condition the pass itself should resolve.

    Args:
        backlog_dir: Root of the backlog tree.
        scope_paths: Optional iterable of epic directories to limit
            the walk to (issue #226).
        force_terminal: When ``True``, also check files with terminal
            status. Default ``False`` skips them so already-frozen
            tasks are not flagged for drift that was acceptable at
            their authoring time.
        workspace_root: Optional workspace directory. When supplied,
            the canonical body is rendered with the workspace's
            ``CLAUDE.md`` path substituted, matching what
            ``emit_code_standards_block`` produces for the same
            workspace. When ``None``, the canonical body keeps the
            ``<WORKSPACE_CLAUDE_MD>`` placeholder verbatim.

    Returns the count of task files whose Code Standards block (excluding
    the Error Handling Contract subsection) differs from the canonical
    body.
    """
    from devbench.plugin_helpers.code_standards_template import (
        canonical_body_excluding_error_contract,
    )

    canonical_trimmed = canonical_body_excluding_error_contract().rstrip("\n")
    if workspace_root is not None:
        canonical_trimmed = canonical_trimmed.replace(
            "<WORKSPACE_CLAUDE_MD>",
            str(workspace_root / "CLAUDE.md"),
        )
    # The carve-outs placeholder is set to its empty-list rendering so
    # tasks that emit no carve-outs match the canonical exactly.
    canonical_trimmed = canonical_trimmed.replace("<REPO_CARVE_OUTS>", "(none)")

    drifted = 0
    for path in _iter_work_unit_files(backlog_dir, scope_paths=scope_paths):
        if not _TASK_ID_RE.match(path.stem):
            continue
        text = path.read_text(encoding="utf-8")
        if not force_terminal and _is_terminal_status(text):
            continue
        block = _extract_code_standards_block_excluding_error_contract(text)
        if block is None:
            continue
        if block.rstrip("\n") != canonical_trimmed:
            drifted += 1
    return drifted


def _extract_code_standards_block_excluding_error_contract(text: str) -> str | None:
    """Return the `### Code Standards` block content trimmed of its Error Handling Contract subsection.

    Returns ``None`` when the file has no ``### Code Standards`` heading.
    The trimmed block starts at the ``### Code Standards`` line and
    ends just before the ``#### Error Handling Contract`` subsection
    (or the next ``###`` / ``##`` heading, whichever comes first).
    Trailing whitespace is preserved to allow exact comparison with
    the canonical body.
    """
    start_marker = "### Code Standards"
    start = text.find(start_marker)
    if start < 0:
        return None
    # Find the section's end: the next ``##``-level heading or end-of-file.
    next_h2 = re.search(r"^##\s+", text[start + len(start_marker) :], re.MULTILINE)
    end = (start + len(start_marker) + next_h2.start()) if next_h2 else len(text)
    section = text[start:end]
    # Trim the Error Handling Contract subsection.
    error_marker = "#### Error Handling Contract"
    error_idx = section.find(error_marker)
    if error_idx >= 0:
        section = section[:error_idx].rstrip() + "\n"
    return section


def run_all(
    backlog_dir: Path,
    *,
    scope_paths: Iterable[Path] | None = None,
    force_terminal: bool = False,
    workspace_root: Path | None = None,
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
        "regenerate_backlog_index": regenerate_backlog_index(
            backlog_dir,
            scope_paths=materialised_scope,
            workspace_root=workspace_root,
        ),
        "verify_code_standards_canonical": verify_code_standards_canonical(
            backlog_dir,
            scope_paths=materialised_scope,
            force_terminal=force_terminal,
            workspace_root=workspace_root,
        ),
    }
