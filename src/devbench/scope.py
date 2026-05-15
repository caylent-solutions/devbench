"""Scope filter module for devbench -- printer-pages-style --include/--exclude selectors.

Provides ``ScopeFilter``: a dataclass that holds raw include/exclude token lists and
the pre-expanded set of matching work-unit IDs.  Supports O(1) membership checks via
``allows()``.

See spec section 4.2.1 and acceptance criteria AC-190-1 through AC-190-9.

Raises:
    InvalidScopeError: when a range token is reversed (end < start on the final segment),
        or when a token is structurally malformed (leading, trailing, or consecutive hyphens
        producing empty segments).
"""

from __future__ import annotations

import contextlib
import getpass
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Subdirectory under the workspace root where devbench state files live.
_DEVBENCH_SUBDIR = ".devbench"
_SCOPE_FILENAME = "scope.json"


class InvalidScopeError(ValueError):
    """Raised when a scope token is syntactically invalid.

    Raised for reverse ranges (e.g. ``E3-E1`` or ``E1-F1-S1-T3-T1``) and
    structurally malformed tokens (e.g. ``-E1``, ``E1-``, ``E1--E3``).
    The error message includes the offending token and the expected order so the
    caller can surface an actionable diagnostic.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scope_file_path(workspace_root: Path) -> Path:
    """Return the canonical path of scope.json for ``workspace_root``."""
    return workspace_root / _DEVBENCH_SUBDIR / _SCOPE_FILENAME


def _tokenise(raw: str) -> list[str]:
    """Split ``raw`` on commas and strip whitespace, discarding empty tokens.

    Args:
        raw: Comma-separated string of scope tokens (may be empty).

    Returns:
        List of non-empty, whitespace-stripped token strings.
    """
    return [t.strip() for t in raw.split(",") if t.strip()]


def _validate_token(token: str) -> None:
    """Raise ``InvalidScopeError`` if ``token`` is structurally malformed.

    A token is malformed when any hyphen-delimited segment is empty, which
    happens with leading hyphens (``-E1``), trailing hyphens (``E1-``), or
    consecutive hyphens (``E1--E3``).  These are syntactic errors, not
    out-of-range conditions, and must be rejected immediately (fail-fast).

    Args:
        token: A single, whitespace-stripped scope token.

    Raises:
        InvalidScopeError: If ``token`` contains an empty segment.
    """
    segments = token.split("-")
    if any(seg == "" for seg in segments):
        raise InvalidScopeError(
            f"Malformed scope token '{token}': each hyphen-delimited segment must be"
            f" non-empty. Leading hyphens, trailing hyphens, and consecutive hyphens"
            f" are not valid. Example of valid tokens: 'E1', 'E1-F2', 'E1-E3'."
        )


def _expand_token(token: str, backlog_ids: list[str]) -> set[str]:
    """Expand a single scope token against ``backlog_ids``.

    A token is either:
    - A single-ID prefix (e.g. ``E1``, ``E1-F2``, ``E1-F1-S1-T3``):
      matches the token itself and every ``backlog_ids`` entry that starts
      with ``<token>-`` (i.e. all descendants).
    - A range token (e.g. ``E1-E3``, ``E1-F1-S1-T2-T5``):
      Two adjacent segments of the same type at the same level differ in
      the final segment only.  The final segment carries a numeric suffix.
      The range is expanded inclusively on that numeric suffix, then each
      concrete endpoint prefix is expanded like a single-ID prefix.

    Range detection rule: split on ``-``.  Walk from the right.  When two
    consecutive segments share the same letter prefix (``E``, ``F``, ``S``,
    ``T``) but differ in the trailing integer, treat them as a range whose
    parent is the common prefix of everything before those two segments.

    Args:
        token: A single scope token (already stripped of whitespace).
        backlog_ids: Full list of work-unit IDs from the backlog.

    Returns:
        Set of IDs from ``backlog_ids`` matched by this token.

    Raises:
        InvalidScopeError: If the token is a reversed range (end < start) or
            structurally malformed (empty segment from leading/trailing/consecutive
            hyphens).
    """
    _validate_token(token)
    parts = token.split("-")
    # Detect range: last two parts share the same letter prefix (E/F/S/T)
    # but differ in their integer value.
    if len(parts) >= 2:
        last = parts[-1]
        second_last = parts[-2]
        last_letter = _letter_prefix(last)
        second_last_letter = _letter_prefix(second_last)
        if last_letter and last_letter == second_last_letter:
            # Both segments are the same type -- this is a range token.
            start_num = _numeric_suffix(second_last)
            end_num = _numeric_suffix(last)
            if start_num is None or end_num is None:
                # Not numeric; treat as single-ID
                pass
            else:
                if end_num < start_num:
                    raise InvalidScopeError(
                        f"Reverse range in token '{token}': "
                        f"'{second_last}' (={start_num}) > '{last}' (={end_num}). "
                        f"Ranges must be specified in ascending order."
                    )
                parent_parts = parts[:-2]
                parent_prefix = "-".join(parent_parts) + "-" if parent_parts else ""
                matched: set[str] = set()
                for num in range(start_num, end_num + 1):
                    segment = f"{last_letter}{num}"
                    prefix = f"{parent_prefix}{segment}"
                    matched |= _expand_prefix(prefix, backlog_ids)
                if not matched:
                    logger.warning(
                        "Scope token '%s' matched no work units in the backlog; verify the token is correct.",
                        token,
                    )
                return matched

    # Single-ID token (no range detected or only one segment).
    result = _expand_prefix(token, backlog_ids)
    if not result:
        logger.warning(
            "Scope token '%s' matched no work units in the backlog; verify the token is correct.",
            token,
        )
    return result


def _expand_prefix(prefix: str, backlog_ids: list[str]) -> set[str]:
    """Return all IDs in ``backlog_ids`` equal to or descended from ``prefix``.

    An ID ``x`` is a descendant of ``prefix`` when ``x`` starts with ``prefix + '-'``
    (not just ``prefix``; avoids ``E1`` matching ``E10``).

    Args:
        prefix: The exact ID or ancestor prefix to match.
        backlog_ids: Candidate work-unit IDs.

    Returns:
        Set of matching IDs (may be empty).
    """
    needle = prefix + "-"
    return {wid for wid in backlog_ids if wid == prefix or wid.startswith(needle)}


def _letter_prefix(segment: str) -> str | None:
    """Return the leading letter(s) of a segment, or ``None`` if purely numeric.

    For example: ``"E1"`` -> ``"E"``, ``"F12"`` -> ``"F"``, ``"abc"`` -> ``None``
    (since ``"abc"`` has no trailing digit).

    Args:
        segment: A hyphen-delimited token segment.

    Returns:
        The alphabetic prefix string, or ``None`` if the segment has no
        trailing integer.
    """
    i = 0
    while i < len(segment) and segment[i].isalpha():
        i += 1
    if i == 0 or i == len(segment):
        # All letters (no trailing number) or no letters at all
        return None
    return segment[:i]


def _numeric_suffix(segment: str) -> int | None:
    """Return the trailing integer of a segment, or ``None`` if absent.

    Args:
        segment: A hyphen-delimited token segment such as ``"E3"`` or ``"T12"``.

    Returns:
        Integer value of the trailing digits, or ``None`` if the segment has
        no trailing integer component.
    """
    i = len(segment) - 1
    while i >= 0 and segment[i].isdigit():
        i -= 1
    tail = segment[i + 1 :]
    if not tail:
        return None
    return int(tail)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ScopeFilter:
    """Compiled scope filter ready for membership checks.

    Fields:
        include: Raw include tokens as supplied to ``parse()``.
        exclude: Raw exclude tokens as supplied to ``parse()``.
        expanded_ids: Pre-expanded set of work-unit IDs that pass the filter.
            Membership checks against this set are O(1).

    Use ``parse()`` to construct instances from raw CLI strings.
    Use ``to_file()`` / ``from_file()`` / ``clear()`` for persistence.
    """

    include: list[str]
    exclude: list[str]
    expanded_ids: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, include_str: str, exclude_str: str, backlog_ids: list[str]) -> ScopeFilter:
        """Build a ``ScopeFilter`` from raw CLI include/exclude strings.

        Tokenises by comma (whitespace-tolerant).  Each token is either a
        single-ID prefix or a range (two adjacent same-type segments at the
        end of the token, e.g. ``E1-E3``).

        When ``include_str`` is empty, all ``backlog_ids`` are included before
        any exclusions are applied.

        Args:
            include_str: Comma-separated include tokens, or empty for "all".
            exclude_str: Comma-separated exclude tokens, or empty for "none".
            backlog_ids: Complete list of work-unit IDs from the backlog.

        Returns:
            A fully populated ``ScopeFilter`` with ``expanded_ids`` ready for
            ``allows()`` calls.

        Raises:
            InvalidScopeError: If any token is a reversed range or structurally
                malformed (empty segment from leading/trailing/consecutive hyphens).
        """
        include_tokens = _tokenise(include_str)
        exclude_tokens = _tokenise(exclude_str)

        # Build the include set
        if include_tokens:
            include_set: set[str] = set()
            for tok in include_tokens:
                include_set |= _expand_token(tok, backlog_ids)
        else:
            # Empty include means "all"
            include_set = set(backlog_ids)

        # Build the exclude set and subtract
        exclude_set: set[str] = set()
        for tok in exclude_tokens:
            exclude_set |= _expand_token(tok, backlog_ids)

        expanded = include_set - exclude_set

        return cls(
            include=include_tokens,
            exclude=exclude_tokens,
            expanded_ids=expanded,
        )

    # ------------------------------------------------------------------
    # Membership check
    # ------------------------------------------------------------------

    def allows(self, unit_id: str) -> bool:
        """Return ``True`` when ``unit_id`` is within this scope.

        O(1) set-membership check against ``expanded_ids``.

        Args:
            unit_id: The work-unit ID to test.

        Returns:
            ``True`` iff ``unit_id`` is in ``expanded_ids``.
        """
        return unit_id in self.expanded_ids

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_file(self, workspace_root: Path) -> Path:
        """Write this filter to ``<workspace_root>/.devbench/scope.json``.

        Creates the ``.devbench`` directory if it does not exist.
        The file is written atomically (write to a sibling temp path, then
        ``rename()``).

        The JSON schema written is::

            {
              "include": [...],
              "exclude": [...],
              "expanded_ids": [...],
              "started_at": "<ISO-8601 UTC>",
              "started_by": "<username>"
            }

        Args:
            workspace_root: Path to the workspace root directory.

        Returns:
            Path to the written scope.json file.

        Raises:
            OSError: If the file cannot be written (permissions, disk full, etc.).
        """
        scope_path = _scope_file_path(workspace_root)
        scope_path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, object] = {
            "include": self.include,
            "exclude": self.exclude,
            "expanded_ids": sorted(self.expanded_ids),
            "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "started_by": _current_user(),
        }
        tmp_path = scope_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2))
        tmp_path.replace(scope_path)
        return scope_path

    @classmethod
    def from_file(cls, workspace_root: Path) -> ScopeFilter:
        """Read a ``ScopeFilter`` from ``<workspace_root>/.devbench/scope.json``.

        Args:
            workspace_root: Path to the workspace root directory.

        Returns:
            A ``ScopeFilter`` instance reconstructed from the file.

        Raises:
            FileNotFoundError: If ``scope.json`` does not exist.
            json.JSONDecodeError: If the file contains invalid JSON.
            KeyError: If required keys are missing from the JSON payload.
        """
        scope_path = _scope_file_path(workspace_root)
        if not scope_path.exists():
            raise FileNotFoundError(
                f"scope.json not found at '{scope_path}'. Run 'devbench start --include ...' to create one."
            )
        data = json.loads(scope_path.read_text())
        return cls(
            include=data["include"],
            exclude=data["exclude"],
            expanded_ids=set(data["expanded_ids"]),
        )

    @classmethod
    def clear(cls, workspace_root: Path) -> None:
        """Delete ``<workspace_root>/.devbench/scope.json`` if it exists.

        Idempotent: does not raise if the file is already absent.

        Args:
            workspace_root: Path to the workspace root directory.

        Raises:
            OSError: If the file exists but cannot be deleted (permissions, etc.).
        """
        scope_path = _scope_file_path(workspace_root)
        with contextlib.suppress(FileNotFoundError):
            scope_path.unlink()


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _current_user() -> str:
    """Return the current OS username for audit metadata.

    Falls back to the ``USER`` environment variable, then ``"unknown"`` if
    neither is available.

    Returns:
        The current username string.
    """
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")
