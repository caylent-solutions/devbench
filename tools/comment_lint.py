#!/usr/bin/env python3
"""comment_lint: detect or remove explanatory Python comments.

A focused linter whose only job is to enforce that Python source carries no
explanatory ``#`` comments.  Functional directives that change tooling
behaviour are preserved: shebang lines, PEP 263 encoding declarations,
``# type:`` (mypy) and ``# pragma:`` (coverage).  Docstrings and every other
string literal are never touched -- the tool locates comments via the
:mod:`tokenize` module, which emits ``COMMENT`` tokens distinct from
``STRING`` tokens, so a ``#`` that appears inside a string literal is never
mistaken for a comment.

Removal is proven non-destructive by an abstract-syntax-tree equality guard.
A file is rewritten only when ``ast.dump(ast.parse(before))`` equals
``ast.dump(ast.parse(after))``.  Comments are absent from the AST while
docstrings and code are present, so an equal dump proves that only comments
were removed and no code or docstring changed.  Removal is atomic: if any
file would fail the guard, nothing is written.

Usage:
    comment_lint.py [--check] PATH [PATH ...] [--exclude GLOB ...]
    comment_lint.py --fix     PATH [PATH ...] [--exclude GLOB ...]

Exit codes:
    0  --check found no disallowed comments, or --fix succeeded
    1  --check found disallowed comments
    2  fatal error (unparseable file, AST-equality violation, ruff failure)
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

EXIT_OK = 0
EXIT_FOUND = 1
EXIT_FATAL = 2

ENCODING_RE = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
DIRECTIVE_RE = re.compile(
    r"^#\s*(noqa|nosec|type:|pragma:|pylint:|mypy:|ruff:|isort:|flake8:|fmt:|yapf:)",
)

SKIP_DIR_PARTS = frozenset({"__pycache__", ".venv", ".git"})

Formatter = Callable[[str, Path], str]


class CommentLintError(Exception):
    """Fatal, user-facing error mapped to exit code 2."""


@dataclass(frozen=True)
class Violation:
    """A single disallowed comment located in a source file."""

    path: Path
    row: int
    col: int
    text: str

    def render(self) -> str:
        """Render as ``path:line:col: disallowed comment: <text>``."""
        return f"{self.path.as_posix()}:{self.row}:{self.col}: disallowed comment: {self.text}"


def is_preserved(text: str, row: int, col: int) -> bool:
    """Return True when a comment is a functional directive that must be kept.

    Preserved directives are: a shebang on the very first line; a PEP 263
    encoding declaration on line 1 or 2; and a tool/lint directive that
    changes how a tool treats the code -- ``# type:`` (mypy), ``# pragma:``
    (coverage), ``# noqa``/``# nosec`` (linter/security suppressions),
    ``# fmt:`` (formatter), and the equivalent ``pylint:``/``ruff:``/etc.
    families.  Everything else is an explanatory comment and is removable.
    """
    stripped = text.strip()
    if row == 1 and col == 0 and stripped.startswith("#!"):
        return True
    if row in (1, 2) and ENCODING_RE.match(stripped):
        return True
    return bool(DIRECTIVE_RE.match(stripped))


def _comment_tokens(source: str) -> list[tokenize.TokenInfo]:
    readline = io.StringIO(source).readline
    try:
        tokens = list(tokenize.generate_tokens(readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise CommentLintError(f"could not tokenize source: {exc}") from exc
    return [tok for tok in tokens if tok.type == tokenize.COMMENT]


def _ast_dump(source: str, path: Path) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CommentLintError(f"{path.as_posix()}: could not parse: {exc}") from exc
    return ast.dump(tree)


def find_disallowed(path: Path, source: str) -> list[Violation]:
    """Return every disallowed comment in ``source`` (parse errors are fatal)."""
    _ast_dump(source, path)
    violations: list[Violation] = []
    for tok in _comment_tokens(source):
        row, col = tok.start
        if not is_preserved(tok.string, row, col):
            violations.append(Violation(path, row, col, tok.string))
    return violations


def strip_comments(source: str, removable: dict[int, int]) -> str:
    """Remove the comments at the given ``{row: col}`` positions.

    A comment that occupies a whole line (only whitespace precedes the ``#``)
    drops the entire physical line; a trailing comment is sliced off and the
    remaining code is right-stripped.  The original line ending is preserved.
    """
    lines = source.splitlines(keepends=True)
    out: list[str] = []
    for index, line in enumerate(lines, start=1):
        col = removable.get(index)
        if col is None:
            out.append(line)
            continue
        prefix = line[:col]
        if prefix.strip() == "":
            continue
        if line.endswith("\r\n"):
            eol = "\r\n"
        elif line.endswith("\n"):
            eol = "\n"
        elif line.endswith("\r"):
            eol = "\r"
        else:
            eol = ""
        out.append(prefix.rstrip() + eol)
    return "".join(out)


def _ruff(args: list[str], source: str, path: Path, *, ok_codes: tuple[int, ...]) -> str:
    proc = subprocess.run(
        ["ruff", *args, "--stdin-filename", str(path), "-"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in ok_codes or not proc.stdout:
        raise CommentLintError(f"{path.as_posix()}: ruff {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout


def _ruff_normalize(source: str, path: Path) -> str:
    """Normalise import blocks (isort) then formatting, both behaviour-preserving.

    Removing comments that sit inside or around an import block leaves blank-line
    residue the import rule (``I``) rejects; ``ruff format`` does not fix that, so
    the import-fix pass runs first and ``ruff format`` produces the final canonical
    layout -- the same fixpoint ``make format`` reaches.  Any genuine import
    re-ordering this triggers is caught by the AST-equality guard in
    :func:`fix_file`.
    """
    sorted_source = _ruff(["check", "--fix", "--select", "I"], source, path, ok_codes=(0, 1))
    return _ruff(["format"], sorted_source, path, ok_codes=(0,))


def fix_file(path: Path, source: str, formatter: Formatter) -> str | None:
    """Return the rewritten source for ``path``, or None if it is unchanged.

    Raises :class:`CommentLintError` if removal would alter the program's
    abstract syntax tree (docstrings/code), so the caller can abort before
    writing anything.
    """
    before = _ast_dump(source, path)
    violations = find_disallowed(path, source)
    if not violations:
        return None
    stripped = strip_comments(source, {v.row: v.col for v in violations})
    formatted = formatter(stripped, path)
    after = _ast_dump(formatted, path)
    if before != after:
        raise CommentLintError(
            f"{path.as_posix()}: refusing to write -- comment removal changed the syntax tree "
            "(this is a comment_lint bug; no files were modified)"
        )
    if formatted == source:
        return None
    return formatted


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIR_PARTS for part in path.parts)


def discover(paths: Sequence[str], excludes: Sequence[str]) -> list[Path]:
    """Resolve path arguments to a deduplicated, ordered list of .py files."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates = sorted(path.rglob("*.py"))
        elif path.is_file():
            candidates = [path]
        else:
            raise CommentLintError(f"path does not exist: {raw}")
        for candidate in candidates:
            if _is_skipped(candidate):
                continue
            rel = candidate.as_posix()
            if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
                continue
            found.append(candidate)
    return list(dict.fromkeys(found))


def run_check(files: Sequence[Path]) -> int:
    violations: list[Violation] = []
    for path in files:
        violations.extend(find_disallowed(path, path.read_text(encoding="utf-8")))
    for violation in violations:
        print(violation.render())
    if violations:
        print(
            f"ERROR: {len(violations)} disallowed comment(s) found; run 'make fix-comments' to remove them.",
            file=sys.stderr,
        )
        return EXIT_FOUND
    return EXIT_OK


def run_fix(files: Sequence[Path], formatter: Formatter) -> int:
    rewrites: list[tuple[Path, str]] = []
    for path in files:
        rewritten = fix_file(path, path.read_text(encoding="utf-8"), formatter)
        if rewritten is not None:
            rewrites.append((path, rewritten))
    for path, source in rewrites:
        path.write_text(source, encoding="utf-8")
    print(f"comment_lint: rewrote {len(rewrites)} file(s)")
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comment_lint",
        description="Detect or remove explanatory Python comments.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report disallowed comments (default).")
    mode.add_argument("--fix", action="store_true", help="Remove disallowed comments in place.")
    parser.add_argument("paths", nargs="+", help="Files or directories to scan.")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="fnmatch glob (matched against the posix path) to exclude; repeatable.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, formatter: Formatter = _ruff_normalize) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        files = discover(args.paths, args.exclude)
        if args.fix:
            return run_fix(files, formatter)
        return run_check(files)
    except CommentLintError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
