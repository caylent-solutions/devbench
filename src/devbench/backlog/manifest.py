"""Changes Manifest parser and writer for work-unit Markdown files.

The Changes Manifest is a declarative table in each work-unit file that lists
the files the executor is authorized to stage and the reason for each change.
The ``changes_manifest`` judge enforces that the staged file set matches this
manifest exactly.

This module provides:

- ``ManifestRow`` -- typed row with ``file`` and ``change`` fields.
- ``parse_manifest`` -- extract rows from a work-unit Markdown string.
- ``render_manifest_rows`` -- format rows as a stable Markdown table.
- ``append_rows`` -- splice additional rows into an existing work-unit
  Markdown string without modifying anything outside the Changes Manifest
  section body.
- ``list_staged_files`` / ``list_changed_files`` -- read-only git queries for
  the staged set and for the full changed set (staged, unstaged, untracked).
- ``assert_staged_matches_manifest`` -- commit-time guard: nothing outside
  the manifest may be staged.

The writer is used by the manifest amendment workflow to add production-fix
rows to a work-unit's manifest at runtime after a judge has approved the
amendment. It is never used to remove rows.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

EM_DASH = "\u2014"
MANIFEST_HEADER = "Changes Manifest"

# Bounded timeout (seconds) for the read-only ``git diff --cached --name-only``
# call used by ``assert_staged_matches_manifest``. Kept generous so a slow
# checkout still completes; far below the global command timeout so a hung
# git process surfaces fast.
_GIT_DIFF_TIMEOUT: int = 30

_SECTION_RE = re.compile(
    rf"^(##\s+{re.escape(MANIFEST_HEADER)}\s*\n)(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


class ManifestParseError(ValueError):
    """Raised when the Changes Manifest section cannot be parsed."""


@dataclass(frozen=True)
class ManifestRow:
    """One row of the Changes Manifest table.

    ``file`` is the path of the staged file, relative to the repo root.
    ``change`` is a one-line description of what the executor changed.
    Both values are validated on construction.
    """

    file: str
    change: str

    def __post_init__(self) -> None:
        if not self.file:
            raise ValueError("ManifestRow.file must be non-empty")
        if self.file != self.file.strip():
            raise ValueError(f"ManifestRow.file must not have leading/trailing whitespace: {self.file!r}")
        if not self.change or not self.change.strip():
            raise ValueError("ManifestRow.change must be non-empty")
        if EM_DASH in self.file or EM_DASH in self.change:
            raise ValueError(
                "ManifestRow must not contain em-dash (U+2014). Use '--' instead. "
                f"file={self.file!r} change={self.change!r}"
            )


def parse_manifest(content: str) -> list[ManifestRow]:
    """Return the rows of the Changes Manifest table parsed from ``content``.

    Returns an empty list when the section exists but contains only a header
    and separator (no data rows).

    Raises ``ManifestParseError`` if the ``## Changes Manifest`` section is
    missing, if a row does not have exactly two columns, or if a cell
    contains content that violates ``ManifestRow`` invariants (empty value,
    em-dash, leading/trailing whitespace in the file path).
    """
    match = _SECTION_RE.search(content)
    if match is None:
        raise ManifestParseError(f"'## {MANIFEST_HEADER}' section not found in work-unit content")
    return _parse_body(match.group(2))


def render_manifest_rows(rows: list[ManifestRow]) -> str:
    """Render ``rows`` as a Markdown table.

    The output always has a ``| File | Change |`` header and a separator row.
    When ``rows`` is empty the header and separator are returned with no
    data rows. The output ends with a single newline so it can be spliced
    directly into a Markdown document.
    """
    lines = ["| File | Change |", "|------|--------|"]
    for row in rows:
        lines.append(f"| `{row.file}` | {row.change} |")
    return "\n".join(lines) + "\n"


def _read_git_paths(repo_path: Path, args: list[str]) -> list[str]:
    """Run a read-only, NUL-delimited git path query and return the paths.

    Shared by :func:`list_staged_files` and :func:`list_changed_files` so the
    subprocess invocation, timeout, and failure handling exist once. ``args``
    must be a git subcommand that emits NUL-separated paths (``-z``), which
    keeps paths containing spaces or newlines intact instead of relying on
    git's quoting rules.

    Args:
        repo_path: Local repo root the query runs against.
        args: Git subcommand and flags, without the leading ``git -C <path>``.

    Returns:
        Paths relative to ``repo_path``, in the order git emitted them.

    Raises:
        RuntimeError: If git times out or exits non-zero. An empty result set
            is not an error; it returns an empty list.
    """
    cmd = ["git", "-C", str(repo_path), *args]
    printable = " ".join(args)
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_DIFF_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {printable} timed out in {repo_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"git {printable} failed in {repo_path} (exit {result.returncode}): {result.stderr.strip()}")
    return [path for path in result.stdout.split("\0") if path.strip()]


def list_staged_files(repo_path: Path) -> list[str]:
    """Return the staged file set for ``repo_path`` via ``git diff --cached``.

    Pure read-only operation. Bounded by ``_GIT_DIFF_TIMEOUT``. Returns paths
    relative to ``repo_path`` -- the same shape that appears in the Changes
    Manifest. Raises ``RuntimeError`` if git itself fails (not when the diff
    is empty -- that's an empty list).
    """
    return _read_git_paths(repo_path, ["diff", "--cached", "--name-only", "-z"])


def list_changed_files(repo_path: Path) -> list[str]:
    """Return every path in ``repo_path`` that differs from the last commit.

    The union of three read-only queries, so nothing a work unit could have
    left behind is missed:

    - ``git diff --cached`` -- staged against HEAD.
    - ``git diff`` -- unstaged, tracked modifications.
    - ``git ls-files --others --exclude-standard`` -- untracked files that
      are not gitignored.

    :func:`list_staged_files` sees only the first of the three, which is why
    it cannot serve as the claim-time guard: a work unit that blocked before
    staging leaves unstaged and untracked residue that a staged-only query
    reports as a clean tree.

    Args:
        repo_path: Local repo root to inspect.

    Returns:
        Sorted, de-duplicated paths relative to ``repo_path``.

    Raises:
        RuntimeError: If any of the three git queries times out or fails.
    """
    staged = _read_git_paths(repo_path, ["diff", "--cached", "--name-only", "-z"])
    unstaged = _read_git_paths(repo_path, ["diff", "--name-only", "-z"])
    untracked = _read_git_paths(repo_path, ["ls-files", "--others", "--exclude-standard", "-z"])
    return sorted({*staged, *unstaged, *untracked})


def assert_staged_matches_manifest(repo_path: Path, manifest_files: list[str]) -> None:
    """Verify every staged path appears in ``manifest_files``; raise otherwise.

    Called from every commit path so a TRACE_FILE / dst/ / fixture-pollution
    style scope violation cannot reach the commit. Pure deterministic check;
    no LLM involvement.

    Args:
        repo_path: Local repo root the diff is computed against.
        manifest_files: The exact file-path list from the work unit's
            ``## Changes Manifest`` (parsed via :func:`parse_manifest`).

    Raises:
        RuntimeError: when one or more staged paths are NOT in
            ``manifest_files``. The error message lists every offending
            path so the operator (or executor) can act precisely.
    """
    staged = list_staged_files(repo_path)
    allowed = set(manifest_files)
    out_of_scope = [p for p in staged if p not in allowed]
    if out_of_scope:
        raise RuntimeError(
            f"Manifest scope violation in {repo_path}: "
            f"{len(out_of_scope)} staged file(s) not in Changes Manifest: {out_of_scope}. "
            f"Manifest declares: {sorted(allowed)}. Unstage the offending files or "
            "amend the work unit's Changes Manifest before committing."
        )


def append_rows(content: str, new_rows: list[ManifestRow]) -> str:
    """Return ``content`` with ``new_rows`` appended to the Changes Manifest.

    Existing manifest rows are preserved; new rows are appended in the
    order given. Content outside the Changes Manifest section is left
    byte-for-byte identical. Within the section, rows are re-rendered with
    the canonical formatter so the resulting table is internally consistent.

    Returns ``content`` unchanged when ``new_rows`` is empty. Raises
    ``ManifestParseError`` if the section is missing or malformed.
    """
    if not new_rows:
        return content

    match = _SECTION_RE.search(content)
    if match is None:
        raise ManifestParseError(f"'## {MANIFEST_HEADER}' section not found in work-unit content")

    existing = _parse_body(match.group(2))
    combined = existing + list(new_rows)
    rendered = render_manifest_rows(combined)

    replacement = match.group(1) + "\n" + rendered + "\n"
    return content[: match.start()] + replacement + content[match.end() :]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_body(body: str) -> list[ManifestRow]:
    """Parse the body of the Changes Manifest section into typed rows.

    Honours markdown-style pipe escapes inside cells: a ``\\|`` sequence
    is treated as a literal pipe within a cell, not as a cell separator.
    This lets Manifest descriptions reference command-line examples like
    ``my-cmd output \\| grep -v debug`` (or any prose that names a
    pipe) without breaking the parser's 2-column invariant.
    """
    rows: list[ManifestRow] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("|"):
            continue
        cells = _split_cells_honouring_escapes(line)
        if _is_header_row(cells) or _is_separator_row(cells):
            continue
        if len(cells) != 2:
            raise ManifestParseError(f"Manifest row must have exactly 2 columns, got {len(cells)}: {line!r}")
        raw_file = cells[0]
        # Handle repo-prefixed manifest format: `<repo>` -- `<path>`
        if "` -- `" in raw_file:
            file_cell = raw_file.split("` -- `", 1)[1].strip("`").strip()
        else:
            file_cell = raw_file.strip("`").strip()
        change_cell = cells[1]
        try:
            rows.append(ManifestRow(file=file_cell, change=change_cell))
        except ValueError as exc:
            raise ManifestParseError(f"Invalid manifest row {line!r}: {exc}") from exc
    return rows


# Sentinel byte that cannot occur naturally in Markdown source. Used by
# ``_split_cells_honouring_escapes`` to temporarily replace markdown-escaped
# pipes (``\\|``) so the line can be split on unescaped pipes via
# ``str.split`` without a regex-based tokenizer.
_ESC_PIPE_SENTINEL = "\x00ESC_PIPE\x00"


def _split_cells_honouring_escapes(line: str) -> list[str]:
    """Split a Manifest table row on unescaped pipes; preserve ``\\|`` literal.

    The line is split on ``|`` as separators, but any backslash-escaped
    pipe (``\\|``) inside a cell is treated as a literal pipe and does
    NOT split. After splitting, each cell's literal pipes are restored.
    """
    protected = line.replace("\\|", _ESC_PIPE_SENTINEL)
    raw_cells = protected.strip("|").split("|")
    return [c.strip().replace(_ESC_PIPE_SENTINEL, "|") for c in raw_cells]


def _is_header_row(cells: list[str]) -> bool:
    """Return ``True`` if ``cells`` is the ``| File | Change |`` header row."""
    if len(cells) < 2:
        return False
    return cells[0].lower() == "file" and cells[1].lower() == "change"


def _is_separator_row(cells: list[str]) -> bool:
    """Return ``True`` if every cell in ``cells`` is an all-dashes separator."""
    for cell in cells:
        stripped = cell.replace(":", "").strip()
        if not stripped or any(c != "-" for c in stripped):
            return False
    return bool(cells)
