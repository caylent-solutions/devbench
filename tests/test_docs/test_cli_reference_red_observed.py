"""Structural pins for the RED_OBSERVED ``log-tdd`` section of cli-reference.md.

Verifies that ``docs/cli-reference.md``'s ``### `log-tdd``` section (the only
section this task owns -- ``docs/plugin-architecture.md`` and
``docs/backlog-contract.md`` are owned by sibling task E4-F3-S1-T3) reflects
the four-phase TDD vocabulary added by E4-F3-S1-T1:

- The full four-phase vocabulary, with the agent-facing verb naming only the
  three agent-writable phases (RED, GREEN, REFACTOR).
- The RED_OBSERVED rejection path: exit code 1, the orchestrator-only
  message (rendered from ``devbench.constants.TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE``
  rather than a hand-typed duplicate), and that nothing is written to the
  TDD Cycle Log on rejection.
- The internal ``write_red_observed_entry`` write path and every member of
  ``devbench.constants.RED_OBSERVED_RECORD_FIELDS``.
- The stale unqualified pre-change sentence ("Enforces the phase token (must
  be RED, GREEN, or REFACTOR, case-insensitive)") is gone.

Doc/constants agreement is asserted by importing the real constants and
rendering the exact message the doc must quote, rather than hardcoding a
second, independently-maintained literal (AC-E4-F3-S1-T5-6).

Spec source: AC-E4-F3-S1-T1-5, AC-54, AC-55. Task: E4-F3-S1-T5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.constants import (
    AGENT_WRITABLE_TDD_PHASES,
    RED_OBSERVED_RECORD_FIELDS,
    RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE,
    TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE,
    TDD_PHASE_RED_OBSERVED,
)

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

# The pre-change sentence this task's GREEN step replaces. Asserting its
# exact absence pins that the stale three-phase-only wording has actually
# been superseded, not merely that new text was appended alongside it.
_OLD_LOG_TDD_ENFORCEMENT_SENTENCE = (
    "Enforces the phase token (must be `RED`, `GREEN`, or `REFACTOR`, case-insensitive)."
)

# The exact orchestrator-only rejection message docs/cli-reference.md must
# quote, rendered from the real constants rather than duplicated as a
# hand-typed literal (AC-E4-F3-S1-T5-6).
_RENDERED_ORCHESTRATOR_ONLY_MESSAGE = TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE.format(
    phase=TDD_PHASE_RED_OBSERVED,
    agent_phases=", ".join(sorted(AGENT_WRITABLE_TDD_PHASES)),
)

# The exact missing-field rejection message docs/cli-reference.md must quote,
# rendered from the real constants (using the doc's own placeholder token
# '<field>') rather than pinned by a weakened substring check that any prose
# containing the word "missing" would satisfy (AC-E4-F3-S1-T5-6).
_RENDERED_MISSING_FIELD_MESSAGE = RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE.format(field="<field>")


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])  # count leading '#'
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


def _log_tdd_section() -> str:
    text = _read_doc()
    section = _extract_section(text, "### `log-tdd`")
    assert section, "docs/cli-reference.md must contain a '### `log-tdd`' section"
    return section


@pytest.mark.unit
class TestLogTddSectionExists:
    """The log-tdd section must exist and contain substantive content."""

    def test_log_tdd_section_exists(self) -> None:
        text = _read_doc()
        assert "### `log-tdd`" in text, (
            "docs/cli-reference.md must contain a '### `log-tdd`' section (AC-E4-F3-S1-T5-1)."
        )

    def test_log_tdd_section_is_nonempty(self) -> None:
        section = _log_tdd_section()
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "docs/cli-reference.md '### `log-tdd`' section must contain substantive documentation, not just a heading."
        )


@pytest.mark.unit
class TestFourPhaseVocabulary:
    """AC-E4-F3-S1-T5-1: full four-phase vocabulary, agent-facing subset named explicitly."""

    def test_section_names_red_observed(self) -> None:
        section = _log_tdd_section()
        assert "RED_OBSERVED" in section, (
            "docs/cli-reference.md log-tdd section must name RED_OBSERVED as the fourth TDD phase (AC-E4-F3-S1-T5-1)."
        )

    def test_section_names_agent_writable_phases(self) -> None:
        """Assert the backticked tokens, not a bare substring.

        A bare ``"RED" in section`` check is trivially satisfied by the
        substring embedded inside ``RED_OBSERVED`` and can never fail
        independently of that unrelated phase name. The doc renders every
        phase token in backticks (see the raw section text), so pinning on
        the backticked form makes this assertion independently failable.
        """
        section = _log_tdd_section()
        assert "`RED`" in section and "`GREEN`" in section and "`REFACTOR`" in section, (
            "docs/cli-reference.md log-tdd section must name the three agent-writable "
            "phases as backticked tokens `RED`, `GREEN`, `REFACTOR` (AC-E4-F3-S1-T5-1)."
        )

    def test_section_states_agent_verb_accepts_only_three_phases(self) -> None:
        section = _log_tdd_section()
        lower = section.lower()
        assert "agent-writable" in lower or "agent writable" in lower, (
            "docs/cli-reference.md log-tdd section must state that the agent-facing verb "
            "accepts only RED, GREEN and REFACTOR (AC-E4-F3-S1-T5-1)."
        )

    def test_pre_change_enforcement_sentence_is_gone(self) -> None:
        """AC-E4-F3-S1-T5-4: the stale unqualified three-phase sentence must be gone."""
        text = _read_doc()
        assert _OLD_LOG_TDD_ENFORCEMENT_SENTENCE not in text, (
            "docs/cli-reference.md must no longer contain the pre-change 'Enforces the "
            "phase token (must be RED, GREEN, or REFACTOR, case-insensitive)' sentence "
            "unqualified anywhere in the document (AC-E4-F3-S1-T5-4)."
        )


@pytest.mark.unit
class TestRedObservedRejectionPath:
    """AC-E4-F3-S1-T5-2: RED_OBSERVED rejection -- exit 1, orchestrator-only message, no write."""

    def test_section_states_exit_code_1(self) -> None:
        section = _log_tdd_section()
        lower = section.lower()
        assert "exit 1" in lower or "exit code 1" in lower or "exits 1" in lower, (
            "docs/cli-reference.md log-tdd section must state that an agent invocation "
            "naming RED_OBSERVED is rejected with exit code 1 (AC-E4-F3-S1-T5-2, AC-54)."
        )

    def test_section_quotes_orchestrator_only_message_from_constants(self) -> None:
        """The doc must quote the real, constants-rendered message, not a paraphrase."""
        section = _log_tdd_section()
        assert _RENDERED_ORCHESTRATOR_ONLY_MESSAGE in section, (
            "docs/cli-reference.md log-tdd section must quote the exact orchestrator-only "
            "rejection message rendered from devbench.constants."
            "TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE, not a hand-typed duplicate "
            f"(AC-E4-F3-S1-T5-2, AC-E4-F3-S1-T5-6, AC-54). Expected substring: "
            f"{_RENDERED_ORCHESTRATOR_ONLY_MESSAGE!r}"
        )

    def test_section_states_nothing_written_on_rejection(self) -> None:
        section = _log_tdd_section()
        lower = section.lower()
        has_no_write = "writes nothing" in lower or "nothing is written" in lower or "no entry is written" in lower
        assert has_no_write, (
            "docs/cli-reference.md log-tdd section must state that no entry is written to "
            "the TDD Cycle Log when a RED_OBSERVED invocation is rejected (AC-E4-F3-S1-T5-2, AC-54)."
        )

    def test_section_states_orchestrator_only(self) -> None:
        section = _log_tdd_section()
        assert "orchestrator-only" in section.lower(), (
            "docs/cli-reference.md log-tdd section must state that RED_OBSERVED is "
            "orchestrator-only (AC-E4-F3-S1-T5-2, AC-54)."
        )


@pytest.mark.unit
class TestWriteRedObservedEntryPath:
    """AC-E4-F3-S1-T5-3: internal write path and the three required record fields."""

    def test_section_names_write_red_observed_entry(self) -> None:
        section = _log_tdd_section()
        assert "write_red_observed_entry" in section, (
            "docs/cli-reference.md log-tdd section must name write_red_observed_entry as "
            "the internal orchestrator write path for RED_OBSERVED entries (AC-E4-F3-S1-T5-3)."
        )

    @pytest.mark.parametrize("field_name", RED_OBSERVED_RECORD_FIELDS)
    def test_section_names_each_required_record_field(self, field_name: str) -> None:
        """Every member of devbench.constants.RED_OBSERVED_RECORD_FIELDS must be named."""
        section = _log_tdd_section()
        assert field_name in section, (
            f"docs/cli-reference.md log-tdd section must name the RED_OBSERVED record "
            f"field {field_name!r} (one of devbench.constants.RED_OBSERVED_RECORD_FIELDS; "
            f"AC-E4-F3-S1-T5-3)."
        )

    def test_section_describes_missing_field_rejection(self) -> None:
        """The doc must quote the real, constants-rendered missing-field message.

        The missing-field message is the one literal in this section not
        otherwise derived from a constant already asserted elsewhere, so it
        must be pinned by rendering the real template rather than by a
        substring check any prose containing the word "missing" would
        satisfy.
        """
        section = _log_tdd_section()
        assert _RENDERED_MISSING_FIELD_MESSAGE in section, (
            "docs/cli-reference.md log-tdd section must quote the exact missing-field "
            "rejection message rendered from devbench.constants."
            "RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE, not a hand-typed duplicate "
            f"(AC-E4-F3-S1-T5-3, AC-E4-F3-S1-T5-6). Expected substring: "
            f"{_RENDERED_MISSING_FIELD_MESSAGE!r}"
        )


@pytest.mark.unit
class TestContentsTableUnaffected:
    """The pre-existing Contents table entry for the parent section must still resolve.

    ``### `log-tdd``` is a subsection of ``## Orchestrator helpers (invoked by
    agents)``; the Contents table links top-level ``##`` sections only (it
    has no per-subcommand entries, matching the existing convention for
    every other ``###`` subcommand in this document), so this pins that the
    parent link survived the edit rather than asserting a subsection entry
    that would deviate from that convention.
    """

    def test_contents_references_orchestrator_helpers_section(self) -> None:
        text = _read_doc()
        contents_idx = text.find("## Contents")
        assert contents_idx != -1, "docs/cli-reference.md must contain a '## Contents' table"
        next_section = text.find("\n---", contents_idx)
        if next_section == -1:
            next_section = contents_idx + 2000
        contents_block = text[contents_idx:next_section]
        assert "orchestrator-helpers-invoked-by-agents" in contents_block, (
            "docs/cli-reference.md Contents table must still link to the "
            "'Orchestrator helpers (invoked by agents)' section that contains log-tdd."
        )
