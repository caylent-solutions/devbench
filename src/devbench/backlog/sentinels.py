"""Sentinel Manifest values that the validator exempts from path-based rules.

A Changes Manifest entry that names a "real" file path is checked against
the Manifest Conflict Rule (no two tasks claim the same file), the
source-test atomicity rule (every production ``.py`` has a paired
``test_*.py``), and the orphan-path-token rule (every path token in AC /
DoD must be in the same Manifest unless suffixed ``(ref)``). Some Manifest
rows are deliberately not real paths -- they are sentinel strings that
document the task as documentation-only, verification-only, or
decision-only. The validator MUST exempt these from path-based rules so
that:

- Multiple verification-only tasks across different epics don't trigger
  a spurious Manifest Conflict Rule violation.
- A decision-only task whose Manifest contains only ``<decision-only>``
  doesn't trigger Rule 14 (production source without paired test).
- An AC line that references ``<verification-only>`` in prose doesn't
  trigger Rule 20 (orphan path token).

The set ``BACKLOG_SENTINEL_VALUES`` below is the explicit allowlist; the
regex ``SENTINEL_PATTERN`` accepts any token shaped as ``<name>`` so
operators can introduce task-specific variants like
``<verification-only:E15-F5-S1-T2>`` without round-tripping through this
module.
"""

from __future__ import annotations

import re

# Explicit allowlist of sentinel Manifest values recognised by the validator.
# Adding a sentinel here is documentation -- the regex below already covers it.
BACKLOG_SENTINEL_VALUES: frozenset[str] = frozenset(
    {
        "<verification-only>",
        "<decision-only>",
        "<no changes>",
        "<no-op>",
        "<source-drift-fix-targets-determined-at-execution>",
    }
)

# Regex form: any token shaped as ``<name>`` is treated as a sentinel. This
# covers operator-defined per-task variants without requiring the validator
# to know about each one. The pattern is anchored to require the entire
# value (not just a substring) to be of the sentinel shape.
SENTINEL_PATTERN: re.Pattern[str] = re.compile(r"^<[^>]+>$")


def is_sentinel_manifest_value(path: str) -> bool:
    """Return ``True`` if ``path`` is a Manifest sentinel value.

    A sentinel value is either explicitly listed in
    ``BACKLOG_SENTINEL_VALUES`` or matches ``SENTINEL_PATTERN``. Sentinel
    values are exempt from the Manifest Conflict Rule, source-test
    atomicity rule, and orphan-path rule.

    Args:
        path: A Changes Manifest entry's file cell, as read from the
            work-unit Markdown.

    Returns:
        ``True`` when the value is a sentinel and should be exempt from
        path-based validator rules; ``False`` for real file paths and
        empty input.
    """
    if not path:
        return False
    stripped = path.strip()
    if stripped in BACKLOG_SENTINEL_VALUES:
        return True
    return bool(SENTINEL_PATTERN.fullmatch(stripped))
