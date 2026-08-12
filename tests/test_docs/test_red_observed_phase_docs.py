"""Structural pins for the RED_OBSERVED phase documentation sync.

Verifies that the RED_OBSERVED orchestrator-only TDD phase (added to
src/devbench/constants.py and src/devbench/cli.py by E4-F3-S1-T1) is
reflected in the two doc surfaces this task owns:

- docs/plugin-architecture.md: the ``log-tdd`` row of the CLI Commands table
  must name RED_OBSERVED as a fourth phase that the agent-facing verb
  rejects with exit 1 because it is orchestrator-only.
- docs/backlog-contract.md: the Populated-by column of the ``## TDD Cycle
  Log`` row in the Required Sections -- Task Files table must distinguish
  agent-writable entries (RED, GREEN, REFACTOR via ``devbench log-tdd``)
  from the orchestrator-only RED_OBSERVED record written through
  ``write_red_observed_entry``.

Spec source: AC-E4-F3-S1-T1-5, AC-54. Task: E4-F3-S1-T3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PLUGIN_ARCHITECTURE_DOC = REPO_ROOT / "docs" / "plugin-architecture.md"
BACKLOG_CONTRACT_DOC = REPO_ROOT / "docs" / "backlog-contract.md"

# The pre-change rows this task replaces. Asserting their exact absence pins
# that the old three-phase-only wording has actually been superseded, not
# merely that new text was appended somewhere else in the file.
_OLD_PLUGIN_ARCHITECTURE_LOG_TDD_ROW = (
    "| `devbench log-tdd <id> <RED\\|GREEN\\|REFACTOR> <message>` | Append TDD phase entry to work unit |"
)
_OLD_BACKLOG_CONTRACT_TDD_CYCLE_LOG_ROW = (
    "| `## TDD Cycle Log` | Yes (may be empty) | `devbench log-tdd` during implementation |"
)


def _read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_row(text: str, prefix: str) -> str:
    """Return the first line of ``text`` starting with ``prefix``, or empty string."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return ""


@pytest.mark.unit
class TestPluginArchitectureLogTddRow:
    """docs/plugin-architecture.md log-tdd row must document RED_OBSERVED (AC-E4-F3-S1-T3-1)."""

    def test_log_tdd_row_names_red_observed(self) -> None:
        """The log-tdd row must name RED_OBSERVED as a distinct phase."""
        text = _read_doc(PLUGIN_ARCHITECTURE_DOC)
        row = _find_row(text, "| `devbench log-tdd")
        assert row, "docs/plugin-architecture.md must contain a '| `devbench log-tdd' CLI Commands table row"
        assert "RED_OBSERVED" in row, (
            "docs/plugin-architecture.md log-tdd row must name RED_OBSERVED as a fourth "
            "TDD phase (AC-E4-F3-S1-T1-5, AC-54)."
        )

    def test_log_tdd_row_states_orchestrator_only(self) -> None:
        """The row must state RED_OBSERVED is orchestrator-only."""
        text = _read_doc(PLUGIN_ARCHITECTURE_DOC)
        row = _find_row(text, "| `devbench log-tdd")
        assert row, "docs/plugin-architecture.md must contain a '| `devbench log-tdd' CLI Commands table row"
        assert "orchestrator-only" in row.lower(), (
            "docs/plugin-architecture.md log-tdd row must state that RED_OBSERVED is "
            "orchestrator-only (AC-E4-F3-S1-T1-5, AC-54)."
        )

    def test_log_tdd_row_states_rejected_with_exit_1(self) -> None:
        """The row must state that an agent invocation naming RED_OBSERVED is rejected with exit 1."""
        text = _read_doc(PLUGIN_ARCHITECTURE_DOC)
        row = _find_row(text, "| `devbench log-tdd")
        assert row, "docs/plugin-architecture.md must contain a '| `devbench log-tdd' CLI Commands table row"
        lower = row.lower()
        assert "exit 1" in lower or "exit code 1" in lower, (
            "docs/plugin-architecture.md log-tdd row must state that the agent-facing "
            "log-tdd verb rejects a RED_OBSERVED invocation with exit 1 (AC-E4-F3-S1-T1-5, AC-54)."
        )

    def test_pre_change_three_phase_only_row_is_gone(self) -> None:
        """The exact pre-change three-phase-only row text must no longer be present verbatim."""
        text = _read_doc(PLUGIN_ARCHITECTURE_DOC)
        assert _OLD_PLUGIN_ARCHITECTURE_LOG_TDD_ROW not in text, (
            "docs/plugin-architecture.md must no longer contain the pre-change log-tdd row "
            "that omits RED_OBSERVED; it must be updated to name the fourth, orchestrator-only "
            "phase (AC-E4-F3-S1-T1-5, AC-54)."
        )


@pytest.mark.unit
class TestBacklogContractTddCycleLogPopulatedBy:
    """docs/backlog-contract.md TDD Cycle Log Populated-by column must distinguish write paths (AC-E4-F3-S1-T3-2)."""

    def test_populated_by_names_agent_writable_phases_via_log_tdd(self) -> None:
        """The row must name RED, GREEN, REFACTOR as written via devbench log-tdd."""
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        row = _find_row(text, "| `## TDD Cycle Log`")
        assert row, "docs/backlog-contract.md must contain a '| `## TDD Cycle Log`' Required Sections table row"
        assert "RED" in row and "GREEN" in row and "REFACTOR" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must name the agent-writable "
            "RED, GREEN, REFACTOR phases (AC-E4-F3-S1-T1-5, AC-54)."
        )
        assert "log-tdd" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must attribute RED/GREEN/REFACTOR "
            "entries to `devbench log-tdd` (AC-E4-F3-S1-T1-5, AC-54)."
        )

    def test_populated_by_names_orchestrator_only_red_observed_via_write_red_observed_entry(self) -> None:
        """The row must name the orchestrator-only RED_OBSERVED write path."""
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        row = _find_row(text, "| `## TDD Cycle Log`")
        assert row, "docs/backlog-contract.md must contain a '| `## TDD Cycle Log`' Required Sections table row"
        assert "RED_OBSERVED" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must name the orchestrator-only "
            "RED_OBSERVED record (AC-E4-F3-S1-T1-5, AC-54)."
        )
        assert "write_red_observed_entry" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must name write_red_observed_entry "
            "as the write path for RED_OBSERVED (AC-E4-F3-S1-T1-5, AC-54)."
        )
        assert "orchestrator-only" in row.lower() or "orchestrator only" in row.lower(), (
            "docs/backlog-contract.md TDD Cycle Log row must state that RED_OBSERVED is "
            "orchestrator-only (AC-E4-F3-S1-T1-5, AC-54)."
        )

    def test_pre_change_undifferentiated_row_is_gone(self) -> None:
        """The exact pre-change row that doesn't distinguish write paths must no longer be present verbatim."""
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        assert _OLD_BACKLOG_CONTRACT_TDD_CYCLE_LOG_ROW not in text, (
            "docs/backlog-contract.md must no longer contain the pre-change TDD Cycle Log "
            "row that attributes all entries to `devbench log-tdd` without distinguishing "
            "the orchestrator-only RED_OBSERVED write path (AC-E4-F3-S1-T1-5, AC-54)."
        )
