"""Regression tests guarding against stale hard-coded values drifting back
into ``devbench.reporting.report`` comments.

Context (issue #233 / E3-F1-S1-T1): the fast-mode token-volume field-group
comment in ``HookLogTotals`` used to restate ``DEFAULT_FAST_MODE_MULTIPLIER``
as a literal (``6.0``), and the per-role fallback-pricing comment referenced
the retired "Opus 4.7" default. Both comments drifted the moment the
underlying constants changed because they duplicated values instead of
naming the constant that owns them (a DRY violation). These tests read
``report.py``'s source text directly from disk -- rather than importing and
inspecting symbols -- so the guard also catches an accidental reintroduction
of a hard-coded literal even if the constant it should reference still
resolves to the same runtime value.
"""

from __future__ import annotations

import re
from pathlib import Path

import devbench.reporting.report as report_module

_REPORT_SOURCE_PATH = Path(report_module.__file__)

_STALE_MODEL_LABEL = "Opus 4.7"
_CURRENT_CONSTANT_NAME = "DEFAULT_FAST_MODE_MULTIPLIER"
_FAST_MODE_FIELD_DECLARATION = "fast_input_tokens:"
_HARD_CODED_DEFAULT_PATTERN = re.compile(r"default\s+\d")


def _read_report_source() -> str:
    """Read the report.py module's source text directly from disk."""
    return _REPORT_SOURCE_PATH.read_text(encoding="utf-8")


def _fast_mode_comment_block(source_text: str) -> str:
    """Extract the fast-mode token-volume field-group comment block.

    The block is the contiguous run of ``#``-prefixed comment lines
    immediately preceding the ``fast_input_tokens`` field declaration in
    ``HookLogTotals``. Returns the block as a single newline-joined string,
    or an empty string if no such field declaration is found.
    """
    lines = source_text.splitlines()
    field_index = next(
        (i for i, line in enumerate(lines) if line.strip().startswith(_FAST_MODE_FIELD_DECLARATION)),
        None,
    )
    if field_index is None:
        return ""
    start = field_index
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1
    return "\n".join(lines[start:field_index])


def test_no_opus_4_7_reference_remains() -> None:
    """The retired 'Opus 4.7' model label must not appear anywhere in report.py.

    Guards AC-E3-F1-S1-T2-2: the per-role fallback-pricing comment must cite
    the current Opus 5 list rates, not the superseded Opus 4.7 default.
    """
    source_text = _read_report_source()
    assert _STALE_MODEL_LABEL not in source_text, (
        f"{_STALE_MODEL_LABEL!r} still referenced in {_REPORT_SOURCE_PATH}; "
        "the per-role fallback-pricing comment must cite Opus 5 list rates instead"
    )


def test_fast_mode_comment_has_no_hard_coded_multiplier_literal() -> None:
    """The fast-mode field-group comment names the constant, not a literal.

    Guards AC-E3-F1-S1-T2-1: the comment preceding ``fast_input_tokens``
    must point at ``DEFAULT_FAST_MODE_MULTIPLIER`` in
    ``src/devbench/constants.py`` as the single source of truth for the
    default, rather than restating the numeric value inline (which drifts
    every time the constant's value changes).
    """
    source_text = _read_report_source()
    comment_block = _fast_mode_comment_block(source_text)
    assert comment_block, "expected a comment block preceding fast_input_tokens"
    assert not _HARD_CODED_DEFAULT_PATTERN.search(comment_block), (
        f"fast-mode comment block still hard-codes a numeric default value: {comment_block!r}"
    )
    assert _CURRENT_CONSTANT_NAME in comment_block, (
        f"fast-mode comment block does not name {_CURRENT_CONSTANT_NAME} as the source of truth: {comment_block!r}"
    )
