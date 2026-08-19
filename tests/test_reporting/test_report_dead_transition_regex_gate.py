"""Dead-transition-regex removal gate (issue #329 FR-3, AC-14, AC-E13-F2-S1-T1-1).

``_DONE_RE`` / ``_PROGRESS_RE`` used to be defined in ``report.py`` but were
never referenced anywhere else in that module -- every transition-time
consumer reads through ``EventIndex``'s logger-anchored queries instead
(``task_transition_times`` / ``task_transition_time_series_for_workspace``,
both gated on ``_TRANSITION_LOGGER``). Both classes below are end-state
pinning tests: they assert facts that are true only *after* the two
constants are deleted, so they intentionally fail against the pre-deletion
state of ``report.py``.

They live in their own module, separate from ``test_event_index.py`` and
``test_report.py``, so that the file-scoped before/after parity check run by
the green-green gate can still observe those two files fully green on the
before-change (pre-deletion) state: every other test in those two files
already passes regardless of whether the deletion has landed, and mixing an
end-state pinning assertion into either file would make the whole file fail
pre-change, which the gate does not tolerate.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root: tests/test_reporting/test_report_dead_transition_regex_gate.py ->
# parents[2] is the ``devbench`` checkout root (matches the convention
# already used by tests/test_docs/test_config_examples_load.py and
# tests/test_integration/test_tdd_red_gate_e2e.py).
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


class TestNoDeadTransitionRegex:
    """Issue #329 FR-3 (AC-14): ``_DONE_RE`` / ``_PROGRESS_RE`` used to be
    defined in ``report.py`` but were never referenced anywhere else in that
    module -- every transition-time consumer reads through ``EventIndex``'s
    logger-anchored queries instead (``task_transition_times`` /
    ``task_transition_time_series_for_workspace``, both gated on
    ``_TRANSITION_LOGGER``). Keeping the dead pair around restated the
    transition-parsing contract a second time, with the SAME unanchored
    ``.* Set ... to '...'`` shape that caused the #329 Defect A echo bug in
    the (already-fixed) indexed path -- exactly the kind of drift risk the
    "Complete Replacement of Superseded Code" standard exists to prevent.

    This is the grep gate: it fails the moment either name reappears
    anywhere under ``src/``, whether as a resurrected definition, a
    resurrected consumer, or a stale comment/docstring mention of the
    deleted names.
    """

    def test_no_references_to_deleted_regexes_under_src(self) -> None:
        pattern = re.compile(r"\b_DONE_RE\b|\b_PROGRESS_RE\b")
        offending: list[str] = []
        for path in sorted(_SRC_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offending.append(str(path.relative_to(_SRC_ROOT)))
        assert offending == [], (
            "Found live references to the deleted _DONE_RE / _PROGRESS_RE "
            f"transition regexes under src/: {offending}. Every transition-time "
            "consumer must read through EventIndex.task_transition_times / "
            "task_transition_time_series_for_workspace instead."
        )


class TestDeadTransitionRegexesRemoved:
    """Issue #329 FR-3 (AC-14, AC-E13-F2-S1-T1-1): ``_DONE_RE`` and
    ``_PROGRESS_RE`` used to restate the transition-parsing contract a
    second time in ``report.py``, even though every anchor consumer already
    reads through ``EventIndex``. Complete removal is pinned at the
    Python-attribute level here; the src-tree-wide grep gate lives
    alongside it, above, in ``TestNoDeadTransitionRegex``.
    """

    def test_module_no_longer_defines_done_re(self) -> None:
        from devbench.reporting import report

        assert not hasattr(report, "_DONE_RE"), (
            "report.py must not define _DONE_RE -- every 'done' transition "
            "timestamp is read through EventIndex.task_transition_times "
            "instead (issue #329 FR-3)"
        )

    def test_module_no_longer_defines_progress_re(self) -> None:
        from devbench.reporting import report

        assert not hasattr(report, "_PROGRESS_RE"), (
            "report.py must not define _PROGRESS_RE -- every 'in-progress' "
            "claim timestamp is read through "
            "EventIndex.task_transition_time_series_for_workspace instead "
            "(issue #329 FR-3)"
        )
