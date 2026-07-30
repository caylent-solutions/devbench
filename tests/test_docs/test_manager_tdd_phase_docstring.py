"""Structural pins for the ``_append_tdd_entry`` docstring's TDD-phase vocabulary.

Verifies that ``BacklogManager._append_tdd_entry``'s docstring (in
``src/devbench/backlog/manager.py``) documents the ``phase`` argument against
the current four-member ``devbench.constants.VALID_TDD_PHASES`` vocabulary
(RED, GREEN, REFACTOR, RED_OBSERVED) added by E4-F3-S1-T1, rather than the
stale three-phase-only wording ("must be one of ``RED``, ``GREEN``, or
``REFACTOR``"). It also pins that the docstring attributes the
agent-writable versus orchestrator-only authorization boundary to the
caller (``cmd_log_tdd`` checking ``AGENT_WRITABLE_TDD_PHASES``), since
``_append_tdd_entry`` itself performs no phase validation.

The expected phase set is derived from ``devbench.constants.VALID_TDD_PHASES``
rather than hardcoded, so a future fifth phase fails this test instead of
silently passing.

Spec source: AC-E4-F3-S1-T1-5, AC-E4-F3-S1-T1-1. Task: E4-F3-S1-T4.
"""

from __future__ import annotations

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.constants import VALID_TDD_PHASES

STALE_THREE_PHASE_SENTENCE = "must be one of ``RED``, ``GREEN``, or ``REFACTOR``"


def _docstring() -> str:
    """Return ``_append_tdd_entry``'s docstring, failing fast if absent."""
    doc = BacklogManager._append_tdd_entry.__doc__
    assert doc is not None, (
        "BacklogManager._append_tdd_entry must have a docstring (AC-E4-F3-S1-T4-1, AC-E4-F3-S1-T4-2)."
    )
    return doc


@pytest.mark.unit
class TestAppendTddEntryDocstringPhaseVocabulary:
    """Pins the docstring's phase Args: paragraph against VALID_TDD_PHASES."""

    @pytest.mark.parametrize("phase", sorted(VALID_TDD_PHASES))
    def test_docstring_names_every_valid_phase(self, phase: str) -> None:
        doc = _docstring()
        assert phase in doc, (
            f"BacklogManager._append_tdd_entry docstring must name the '{phase}' "
            "phase from devbench.constants.VALID_TDD_PHASES "
            "(AC-E4-F3-S1-T4-1, AC-E4-F3-S1-T4-3)."
        )

    def test_docstring_does_not_contain_stale_three_phase_sentence(self) -> None:
        doc = _docstring()
        assert STALE_THREE_PHASE_SENTENCE not in doc, (
            "BacklogManager._append_tdd_entry docstring must not contain the "
            "stale three-phase-only sentence superseded by VALID_TDD_PHASES "
            "(AC-E4-F3-S1-T4-1, AC-E4-F3-S1-T4-3)."
        )

    def test_docstring_references_valid_tdd_phases_by_name(self) -> None:
        doc = _docstring()
        assert "VALID_TDD_PHASES" in doc, (
            "BacklogManager._append_tdd_entry docstring must reference "
            "devbench.constants.VALID_TDD_PHASES as the source of truth for "
            "the phase argument (AC-E4-F3-S1-T4-1)."
        )


@pytest.mark.unit
class TestAppendTddEntryDocstringAuthorizationBoundary:
    """Pins the docstring's caller-enforced authorization boundary language."""

    def test_docstring_attributes_authorization_to_the_caller(self) -> None:
        doc = _docstring()
        assert "cmd_log_tdd" in doc, (
            "BacklogManager._append_tdd_entry docstring must name cmd_log_tdd "
            "as the caller responsible for enforcing the agent-writable versus "
            "orchestrator-only phase boundary (AC-E4-F3-S1-T4-2)."
        )
        assert "AGENT_WRITABLE_TDD_PHASES" in doc, (
            "BacklogManager._append_tdd_entry docstring must name "
            "AGENT_WRITABLE_TDD_PHASES as the set cmd_log_tdd checks the phase "
            "argument against (AC-E4-F3-S1-T4-2)."
        )
