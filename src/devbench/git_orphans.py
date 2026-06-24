"""Detection and cleanup of tracked files that should be gitignored.

Build/state artifacts -- terraform state files, terragrunt-cache,
provider binaries, Python pycache, coverage data -- frequently get
accidentally committed by agents whose Changes Manifests don't
enumerate them. Those files then live in git history, expand the repo
size (a single AWS provider binary is ~600 MB), and trip security
review on every subsequent diff.

This module classifies tracked files against a stable list of
ignore-worthy fnmatch patterns and offers two operations:

- ``detect_tracked_orphans``: read-only enumeration. Used by
  ``cmd_git_ops`` to refuse polluting commits and by
  ``cmd_cleanup_tracked_orphans`` to plan the fix.
- ``cleanup_tracked_orphans``: writes a devbench-managed block to the
  repo's root ``.gitignore`` and runs ``git rm --cached`` on each
  detected entry (preserving the file on disk).

The pattern list is the operator-tunable surface and may be widened or
narrowed per backlog via ``DEVBENCH_ORPHAN_IGNORE_PATTERNS`` (a
comma-separated list of fnmatch globs replacing the default).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_ORPHAN_PATTERNS: tuple[str, ...] = (
    "**/*.tfstate",
    "**/*.tfstate.backup",
    "**/*.tfstate.lock.info",
    "**/.terraform/**",
    "**/.terraform.lock.hcl",
    "**/.terragrunt-cache/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    "**/.coverage*",
    "**/htmlcov/**",
    "**/node_modules/**",
    "**/.DS_Store",
)

_DEFAULT_GITIGNORE_ENTRIES: tuple[str, ...] = (
    "# Terraform state and module cache",
    "*.tfstate",
    "*.tfstate.backup",
    "*.tfstate.lock.info",
    ".terraform/",
    ".terraform.lock.hcl",
    ".terragrunt-cache/",
    "",
    "# Python build / test caches",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "",
    "# Coverage. ``.coverage*`` (no separator) catches the canonical",
    "# ``.coverage`` file plus pytest-cov's ``.coverage.<ext>`` and the",
    "# stray ``.coverage (1)`` form that appears when the canonical file",
    "# is already locked by another process. Git's gitignore globber",
    "# treats the trailing ``*`` as a no-slash wildcard, so all variants",
    "# in the same directory match.",
    ".coverage*",
    "htmlcov/",
    "",
    "# Node",
    "node_modules/",
    "",
    "# macOS",
    ".DS_Store",
)

DEVBENCH_GITIGNORE_HEADER = "# devbench-managed: tracked-orphan cleanup defaults"


@dataclass(frozen=True)
class OrphanReport:
    """Outcome of one orphan-cleanup pass against a single git repo."""

    repo_path: Path
    detected: list[str]
    removed: list[str]
    gitignore_path: Path
    gitignore_updated: bool
    dry_run: bool


def configured_patterns() -> tuple[str, ...]:
    """Return the active orphan patterns, honoring the override env var.

    ``DEVBENCH_ORPHAN_IGNORE_PATTERNS`` is a comma-separated list; an
    empty / unset value yields :data:`_DEFAULT_ORPHAN_PATTERNS`.
    """
    override = os.environ.get("DEVBENCH_ORPHAN_IGNORE_PATTERNS", "").strip()
    if not override:
        return _DEFAULT_ORPHAN_PATTERNS
    return tuple(p.strip() for p in override.split(",") if p.strip())


def _pattern_to_regex(pattern: str) -> str:
    """Convert a fnmatch-style glob with ``**`` semantics to a Python regex.

    Globstar semantics (matches ``git`` / standard ``.gitignore`` rules):

    - ``**/`` matches zero or more directory segments (including the empty
      prefix, so ``**/.coverage`` matches both ``.coverage`` and
      ``dir/.coverage``).
    - ``/**`` matches zero or more trailing path segments.
    - ``**`` standalone (not adjacent to ``/``) matches any sequence of
      characters including ``/``.
    - ``*`` matches any sequence of non-``/`` characters within a segment.
    - ``?`` matches a single non-``/`` character.
    - All other characters are matched literally.
    """
    return (
        re.escape(pattern)
        .replace(r"\*\*/", "(?:.*/)?")
        .replace(r"/\*\*", "(?:/.*)?")
        .replace(r"\*\*", ".*")
        .replace(r"\*", "[^/]*")
        .replace(r"\?", "[^/]")
    )


def path_matches_orphan(path: str, patterns: Iterable[str] | None = None) -> bool:
    """Return True when *path* matches any orphan pattern."""
    pats = tuple(patterns) if patterns is not None else configured_patterns()
    return any(re.fullmatch(_pattern_to_regex(pattern), path) for pattern in pats)


def detect_tracked_orphans(repo_path: Path, patterns: Iterable[str] | None = None) -> list[str]:
    """Return the sorted list of git-tracked paths matching orphan patterns."""
    pats = tuple(patterns) if patterns is not None else configured_patterns()
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = [line for line in result.stdout.splitlines() if line and path_matches_orphan(line, pats)]
    return sorted(matches)


def detect_staged_orphans(repo_path: Path, patterns: Iterable[str] | None = None) -> list[str]:
    """Return the sorted list of staged paths (added or modified) that match orphan patterns.

    Used by ``cmd_git_ops`` as a pre-commit gate so a polluting commit
    never lands in the first place.
    """
    pats = tuple(patterns) if patterns is not None else configured_patterns()
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--cached", "--name-only", "--diff-filter=AM"],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = [line for line in result.stdout.splitlines() if line and path_matches_orphan(line, pats)]
    return sorted(matches)


def _write_or_extend_gitignore(repo_path: Path) -> tuple[Path, bool]:
    """Append the canonical orphan-ignore block to ``.gitignore`` if absent.

    Returns ``(path, updated)`` where ``updated`` is ``True`` iff the
    file content changed on this call. Idempotent: re-running on a repo
    whose ``.gitignore`` already contains
    :data:`DEVBENCH_GITIGNORE_HEADER` is a no-op.
    """
    gitignore = repo_path / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if DEVBENCH_GITIGNORE_HEADER in existing:
        return gitignore, False
    block_lines = [DEVBENCH_GITIGNORE_HEADER, *_DEFAULT_GITIGNORE_ENTRIES]
    block = "\n".join(block_lines) + "\n"
    new_content = (existing.rstrip("\n") + "\n\n" + block) if existing.strip() else block
    gitignore.write_text(new_content, encoding="utf-8")
    return gitignore, True


def cleanup_tracked_orphans(
    repo_path: Path,
    dry_run: bool = False,
    patterns: Iterable[str] | None = None,
) -> OrphanReport:
    """Detect tracked orphans, untrack them, and write/extend ``.gitignore``.

    Uses ``subprocess`` (not the Bash agent tool) so the PreToolUse
    ``guard-destructive-git`` hook -- which scopes only to Bash tool
    invocations from agents -- does not interfere.

    Args:
        repo_path: filesystem path to the git repo root.
        dry_run: when True, returns the detection without modifying state.
        patterns: optional override of the default pattern list.

    Returns:
        :class:`OrphanReport` summarizing detection and any actions taken.

    Raises:
        FileNotFoundError: when *repo_path* is not a git repo.
    """
    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(f"not a git repo: {repo_path}")
    detected = detect_tracked_orphans(repo_path, patterns)
    gitignore_path = repo_path / ".gitignore"
    if dry_run:
        return OrphanReport(
            repo_path=repo_path,
            detected=detected,
            removed=[],
            gitignore_path=gitignore_path,
            gitignore_updated=False,
            dry_run=True,
        )
    removed: list[str] = []
    for path in detected:
        subprocess.run(
            ["git", "-C", str(repo_path), "rm", "--cached", "--quiet", "--", path],
            check=True,
        )
        removed.append(path)
    _, gitignore_updated = _write_or_extend_gitignore(repo_path)
    return OrphanReport(
        repo_path=repo_path,
        detected=detected,
        removed=removed,
        gitignore_path=gitignore_path,
        gitignore_updated=gitignore_updated,
        dry_run=False,
    )
