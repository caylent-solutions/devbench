"""Structural pins for the round-3 done-gate and RED/GREEN_GREEN vocabulary sync.

E4-F4-S1-T2 (FR-4.5/FR-4.6) moved the task-type completion invariant into
``BacklogManager.mark_done`` (``_check_task_type_done_invariant``), added the
``devbench green-green-check`` command and the orchestrator-only
``GREEN_GREEN_OBSERVED`` TDD phase, and added the ``devbench decline
--citation`` flag -- but left ``SKILL.md``, ``docs/cli-reference.md``,
``docs/backlog-contract.md``, and ``docs/plugin-architecture.md`` describing
the pre-round-3, four-phase / single-gate architecture. This test pins that
all four doc surfaces were updated in the same change:

- ``SKILL.md`` step 4d.b explicitly invokes ``devbench green-green-check`` as
  part of the refactor-task done flow (AC-DOC-001).
- ``docs/cli-reference.md`` documents ``green-green-check``, ``decline
  --citation``, and the ``mark-done`` / ``check-merge`` gate changes
  (AC-DOC-002).
- ``docs/backlog-contract.md``'s numbered ``validate()`` rule list reaches
  check 22 (AC-DOC-003).
- Zero remaining references to the pre-round-3 architecture claim that the
  task-type gate lives only in ``cmd_mark_done`` rather than
  ``BacklogManager.mark_done`` (AC-DOC-004).
- Every phase-set enumeration across the three docs correctly states five
  ``VALID_TDD_PHASES`` with two orchestrator-only phases (``RED_OBSERVED``,
  ``GREEN_GREEN_OBSERVED``), naming their producers (AC-DOC-005).

Spec source: AC-DOC-001..005. Task: E4-F4-S1-T3.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.constants import (
    AGENT_WRITABLE_TDD_PHASES,
    ORCHESTRATOR_ONLY_TDD_PHASES,
    TDD_PHASE_GREEN_GREEN_OBSERVED,
    TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE,
    TDD_PHASE_RED_OBSERVED,
    VALID_TDD_PHASES,
)

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"
BACKLOG_CONTRACT_DOC = REPO_ROOT / "docs" / "backlog-contract.md"
PLUGIN_ARCHITECTURE_DOC = REPO_ROOT / "docs" / "plugin-architecture.md"
SKILL_DOC = REPO_ROOT / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"

# The pre-round-3 architecture claim this task must remove: the task-type
# done-gate logic used to live only in cli.cmd_mark_done, before E4-F4-S1-T2
# round 3 moved it into BacklogManager.mark_done so every caller (mark-done
# AND check-merge) inherits it identically.
_PRE_ROUND3_ARCHITECTURE_PHRASE = "only in cmd_mark_done"

# The exact orchestrator-only rejection message an agent invocation naming
# GREEN_GREEN_OBSERVED via log-tdd must be rejected with -- rendered from the
# real constants template rather than a hand-typed duplicate.
_RENDERED_GREEN_GREEN_ORCHESTRATOR_ONLY_MESSAGE = TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE.format(
    phase=TDD_PHASE_GREEN_GREEN_OBSERVED,
    agent_phases=", ".join(sorted(AGENT_WRITABLE_TDD_PHASES)),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


def _find_row(text: str, prefix: str) -> str:
    """Return the first line of ``text`` starting with ``prefix``, or empty string."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return ""


_STEP_4D_HEADING = "4d. **Machine-observed RED gate"


def _step_4d_b(skill_text: str) -> str:
    """Return SKILL.md step 4d.b's own text (up to the start of step 4d.c)."""
    anchor = skill_text.find(_STEP_4D_HEADING)
    assert anchor != -1, "SKILL.md must contain the '4d. **Machine-observed RED gate' step heading"
    idx_b = skill_text.find("\n    b. ", anchor)
    assert idx_b != -1, "SKILL.md step 4d must contain a 'b.' substep"
    idx_c = skill_text.find("\n    c. ", idx_b)
    end = idx_c if idx_c != -1 else skill_text.find("\n\n", idx_b)
    assert end != -1, "SKILL.md step 4d.b must be followed by step 4d.c or a blank line"
    return skill_text[idx_b:end]


# ---------------------------------------------------------------------------
# AC-DOC-001: SKILL.md step 4d.b invokes 'devbench green-green-check'.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillStep4dbInvokesGreenGreenCheck:
    def test_skill_doc_exists(self) -> None:
        assert SKILL_DOC.is_file(), f"orchestrate SKILL.md missing at {SKILL_DOC}"

    def test_step_4d_b_names_green_green_check_command(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "devbench green-green-check" in step, (
            "SKILL.md step 4d.b must explicitly invoke 'devbench green-green-check' as "
            "part of the refactor-task done flow (AC-DOC-001)."
        )

    def test_step_4d_b_names_refactor_type(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "`refactor`" in step, (
            "SKILL.md step 4d.b must name the 'refactor' task type as the trigger for "
            "the green-green-check invocation (AC-DOC-001)."
        )

    def test_step_4d_b_names_green_green_observed_record(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "GREEN_GREEN_OBSERVED" in step, (
            "SKILL.md step 4d.b must name the orchestrator-only GREEN_GREEN_OBSERVED "
            "record the check writes on success (AC-DOC-001)."
        )

    def test_step_4d_b_still_skips_test_only_docs_chore(self) -> None:
        """The three still-fully-exempt types must remain named in step 4d.b."""
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        for exempt_type in ("`test-only`", "`docs`", "`chore`"):
            assert exempt_type in step, (
                f"SKILL.md step 4d.b must still name {exempt_type} as a type exempt from "
                "the RED gate entirely (AC-DOC-001)."
            )


# ---------------------------------------------------------------------------
# AC-DOC-002: cli-reference.md documents green-green-check, decline
# --citation, and mark-done/check-merge gate changes.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCliReferenceGreenGreenCheckSection:
    def test_green_green_check_section_exists(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        assert "### `green-green-check`" in text, (
            "docs/cli-reference.md must contain a '### `green-green-check`' section (AC-DOC-002)."
        )

    def test_section_documents_usage_line(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "green-green-check <id> <test_node_id>" in section, (
            "docs/cli-reference.md green-green-check section must document the usage line "
            "'green-green-check <id> <test_node_id> [...]' (AC-DOC-002)."
        )

    def test_section_names_green_green_observed_write(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "GREEN_GREEN_OBSERVED" in section, (
            "docs/cli-reference.md green-green-check section must name the "
            "GREEN_GREEN_OBSERVED record it writes on success (AC-DOC-002)."
        )

    def test_section_states_before_after_semantics(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        lower = section.lower()
        assert "before" in lower and "after" in lower, (
            "docs/cli-reference.md green-green-check section must describe the "
            "before/after test-run semantics (AC-DOC-002)."
        )


@pytest.mark.unit
class TestCliReferenceDeclineCitationFlag:
    def test_decline_section_documents_citation_flag(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `decline`")
        assert section, "docs/cli-reference.md must contain a '### `decline`' section"
        assert "--citation" in section, (
            "docs/cli-reference.md decline section must document the '--citation' flag "
            "added by E4-F4-S1-T2 (AC-DOC-002)."
        )

    def test_decline_section_states_citation_required_for_already_satisfied(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `decline`")
        assert "already-satisfied" in section, (
            "docs/cli-reference.md decline section must state that --citation is required "
            "when the reason names 'already-satisfied' (AC-DOC-002)."
        )


@pytest.mark.unit
class TestCliReferenceMarkDoneGateChanges:
    def test_mark_done_section_names_backlog_manager_mark_done(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `mark-done`")
        assert section, "docs/cli-reference.md must contain a '### `mark-done`' section"
        assert "BacklogManager.mark_done" in section, (
            "docs/cli-reference.md mark-done section must attribute the task-type done-gate "
            "invariant to BacklogManager.mark_done, the round-3 architecture (AC-DOC-002, AC-DOC-004)."
        )

    def test_mark_done_section_names_both_gate_records(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `mark-done`")
        assert "RED_OBSERVED" in section, (
            "docs/cli-reference.md mark-done section must name the RED_OBSERVED "
            "requirement for gated task types (AC-DOC-002)."
        )
        assert "GREEN_GREEN_OBSERVED" in section, (
            "docs/cli-reference.md mark-done section must name the GREEN_GREEN_OBSERVED "
            "requirement for refactor task types (AC-DOC-002)."
        )


@pytest.mark.unit
class TestCliReferenceCheckMergeGateChanges:
    def test_check_merge_section_names_backlog_manager_mark_done(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `check-merge`")
        assert section, "docs/cli-reference.md must contain a '### `check-merge`' section"
        assert "BacklogManager.mark_done" in section, (
            "docs/cli-reference.md check-merge section must state that a merged PR is "
            "promoted through the same BacklogManager.mark_done invariant as mark-done, "
            "so a merged-but-ungated task is refused identically (AC-DOC-002, AC-DOC-004)."
        )


@pytest.mark.unit
class TestCliReferenceLogTddFivePhases:
    def test_log_tdd_section_names_five_phases(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `log-tdd`")
        assert "five phases" in section, (
            "docs/cli-reference.md log-tdd section must state VALID_TDD_PHASES names "
            "five phases, not four (AC-DOC-002, AC-DOC-005)."
        )
        assert "four phases" not in section, (
            "docs/cli-reference.md log-tdd section must no longer claim VALID_TDD_PHASES "
            "names only four phases (AC-DOC-005)."
        )

    def test_log_tdd_section_names_green_green_observed(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `log-tdd`")
        assert "GREEN_GREEN_OBSERVED" in section, (
            "docs/cli-reference.md log-tdd section must name GREEN_GREEN_OBSERVED as one "
            "of the five VALID_TDD_PHASES (AC-DOC-002, AC-DOC-005)."
        )

    def test_log_tdd_section_states_both_orchestrator_only_phases(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `log-tdd`")
        assert "ORCHESTRATOR_ONLY_TDD_PHASES" in section
        assert "RED_OBSERVED" in section and "GREEN_GREEN_OBSERVED" in section

    def test_log_tdd_section_quotes_green_green_orchestrator_only_rejection(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `log-tdd`")
        assert _RENDERED_GREEN_GREEN_ORCHESTRATOR_ONLY_MESSAGE in section, (
            "docs/cli-reference.md log-tdd section must quote the exact orchestrator-only "
            "rejection message for a GREEN_GREEN_OBSERVED invocation, rendered from "
            "devbench.constants.TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE "
            f"(AC-DOC-005). Expected substring: {_RENDERED_GREEN_GREEN_ORCHESTRATOR_ONLY_MESSAGE!r}"
        )

    def test_log_tdd_section_names_green_green_check_producer(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `log-tdd`")
        assert "devbench green-green-check" in section, (
            "docs/cli-reference.md log-tdd section must name 'devbench green-green-check' "
            "as the producer of the GREEN_GREEN_OBSERVED entry (AC-DOC-005)."
        )


# ---------------------------------------------------------------------------
# AC-DOC-003: backlog-contract.md's validate() rule list extended through
# check 22.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBacklogContractRuleListReachesCheck22:
    def test_rule_list_contains_numbered_rule_22(self) -> None:
        text = _read(BACKLOG_CONTRACT_DOC)
        assert "\n22. " in text, (
            "docs/backlog-contract.md validate-backlog rule list must be extended through check 22 (AC-DOC-003)."
        )

    def test_rule_22_describes_already_satisfied_decline_citation(self) -> None:
        text = _read(BACKLOG_CONTRACT_DOC)
        idx = text.find("\n22. ")
        assert idx != -1, "docs/backlog-contract.md must contain a numbered rule 22"
        line_end = text.find("\n", idx + 1)
        rule_22_line = text[idx + 1 : line_end]
        assert "already-satisfied" in rule_22_line.lower(), (
            "docs/backlog-contract.md rule 22 must describe the already-satisfied decline "
            "citation requirement (FR-4.5, AC-DOC-003)."
        )


# ---------------------------------------------------------------------------
# AC-DOC-004: zero remaining references to the pre-round-3 architecture.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoPreRound3ArchitectureReferences:
    @pytest.mark.parametrize(
        "doc_path",
        [CLI_REFERENCE_DOC, BACKLOG_CONTRACT_DOC, SKILL_DOC],
        ids=["cli-reference.md", "backlog-contract.md", "SKILL.md"],
    )
    def test_doc_does_not_claim_gate_lives_only_in_cmd_mark_done(self, doc_path: Path) -> None:
        text = _read(doc_path)
        assert _PRE_ROUND3_ARCHITECTURE_PHRASE not in text, (
            f"{doc_path} must not describe the task-type done-gate as living "
            f"{_PRE_ROUND3_ARCHITECTURE_PHRASE!r} -- as of E4-F4-S1-T2 round 3 it lives in "
            "BacklogManager.mark_done, inherited by every caller (AC-DOC-004)."
        )


# ---------------------------------------------------------------------------
# AC-DOC-005: five VALID_TDD_PHASES, two orchestrator-only, naming producers.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFivePhaseTwoOrchestratorOnlyInvariant:
    def test_constants_shape_is_five_phases_two_orchestrator_only(self) -> None:
        """Sanity-pin the constants themselves so the doc assertions below test real values."""
        assert len(VALID_TDD_PHASES) == 5
        assert len(ORCHESTRATOR_ONLY_TDD_PHASES) == 2
        assert {TDD_PHASE_RED_OBSERVED, TDD_PHASE_GREEN_GREEN_OBSERVED} == ORCHESTRATOR_ONLY_TDD_PHASES


@pytest.mark.unit
class TestPluginArchitecturePhaseEnumeration:
    def test_log_tdd_row_names_both_orchestrator_only_phases(self) -> None:
        text = _read(PLUGIN_ARCHITECTURE_DOC)
        row = _find_row(text, "| `devbench log-tdd")
        assert row, "docs/plugin-architecture.md must contain a '| `devbench log-tdd' CLI Commands table row"
        assert "RED_OBSERVED" in row and "GREEN_GREEN_OBSERVED" in row, (
            "docs/plugin-architecture.md log-tdd row must name both orchestrator-only "
            "phases, RED_OBSERVED and GREEN_GREEN_OBSERVED (AC-DOC-005)."
        )

    def test_log_tdd_row_names_both_producers(self) -> None:
        text = _read(PLUGIN_ARCHITECTURE_DOC)
        row = _find_row(text, "| `devbench log-tdd")
        assert row, "docs/plugin-architecture.md must contain a '| `devbench log-tdd' CLI Commands table row"
        assert "write_red_observed_entry" in row, (
            "docs/plugin-architecture.md log-tdd row must name write_red_observed_entry as "
            "the RED_OBSERVED producer (AC-DOC-005)."
        )
        assert "green-green-check" in row, (
            "docs/plugin-architecture.md log-tdd row must name 'devbench green-green-check' "
            "as the GREEN_GREEN_OBSERVED producer (AC-DOC-005)."
        )

    def test_fourth_phase_ordinal_is_gone(self) -> None:
        """The stale 'RED_OBSERVED is a fourth phase' ordinal wording must be gone."""
        text = _read(PLUGIN_ARCHITECTURE_DOC)
        row = _find_row(text, "| `devbench log-tdd")
        assert "fourth phase" not in row, (
            "docs/plugin-architecture.md log-tdd row must drop the 'fourth phase' ordinal "
            "now that there are five phases and two orchestrator-only phases (AC-DOC-005)."
        )


# ---------------------------------------------------------------------------
# doc_review round-2 fix: green-green-check <test_node_id> shape must be
# documented, with a defined node-id source, and the fifth rejection
# condition (no uncommitted production-source change to stash) must appear
# in cli-reference.md alongside the other four.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGreenGreenCheckNodeIdShapeDocumented:
    def test_skill_step_4d_b_states_node_id_shape(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "<path>.py::<test_name>" in step, (
            "SKILL.md step 4d.b must state the required fully-qualified pytest node id "
            "shape '<path>.py::<test_name>' -- a bare test file path is rejected by "
            "green-green-check with 'could not collect test' (doc_review round-2 fix)."
        )

    def test_skill_step_4d_b_gives_a_defined_node_id_source(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "Changes Manifest" in step, (
            "SKILL.md step 4d.b must name a defined source for the test node id(s) -- "
            "the tests covering the Changes Manifest production-source rows -- rather "
            "than pointing only at the executor's [GREEN] log entry, whose contracted "
            "format never carries node ids (doc_review round-2 fix)."
        )

    def test_cli_reference_green_green_check_states_node_id_shape(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "<path>.py::<test_name>" in section, (
            "docs/cli-reference.md green-green-check section must document the required "
            "'<path>.py::<test_name>' node id shape (doc_review round-2 fix)."
        )
        assert "could not collect test" in section, (
            "docs/cli-reference.md green-green-check section must state the exact "
            "rejection message a bare file path receives (doc_review round-2 fix)."
        )


# ---------------------------------------------------------------------------
# doc_review round-3 fix: the round-2 node-id shape ('<path>.py::<test_name>'
# only) excludes the class-nested node ids pytest actually prints for 127 of
# 131 test files in this repo (tests defined inside `class Test*`), and
# SKILL.md 4d.b's 'enumerate every test_* function' derivation drops the
# class segment. Both surfaces must document the full node-id grammar and a
# mechanical, class-segment-preserving derivation.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGreenGreenCheckNodeIdShapeIncludesClassSegment:
    def test_skill_step_4d_b_states_class_nested_shape(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "<path>.py::<Class>::<test_name>" in step, (
            "SKILL.md step 4d.b must state the class-nested node id shape "
            "'<path>.py::<Class>::<test_name>' -- 127 of 131 test files in this repo "
            "nest tests inside a class, so the two-segment shape alone excludes the "
            "real node ids pytest reports (doc_review round-3 fix)."
        )

    def test_skill_step_4d_b_states_parametrized_shape(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "[<param>]" in step, (
            "SKILL.md step 4d.b must state the parametrized node id shape "
            "'<path>.py::<Class>::<test_name>[<param>]' (doc_review round-3 fix)."
        )

    def test_skill_step_4d_b_derives_ids_via_collect_only(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "--collect-only" in step, (
            "SKILL.md step 4d.b must replace the 'enumerate every test_* function' "
            "derivation with a mechanical step that preserves the class segment: run "
            "'pytest <file> --collect-only -q' on the Changes-Manifest test files and "
            "use the emitted node ids verbatim (doc_review round-3 fix)."
        )

    def test_skill_step_4d_b_no_longer_enumerates_bare_function_names(self) -> None:
        text = _read(SKILL_DOC)
        step = _step_4d_b(text)
        assert "enumerate every" not in step, (
            "SKILL.md step 4d.b must not derive node ids by reading `test_*` function "
            "names out of the file -- that derivation drops the class segment for "
            "class-nested tests, guaranteeing a 'could not collect test' rejection "
            "(doc_review round-3 fix)."
        )

    def test_cli_reference_states_class_nested_shape(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "<path>.py::<Class>::<test_name>" in section, (
            "docs/cli-reference.md green-green-check section must document the "
            "class-nested node id shape '<path>.py::<Class>::<test_name>' "
            "(doc_review round-3 fix)."
        )

    def test_cli_reference_states_parametrized_shape(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "[<param>]" in section, (
            "docs/cli-reference.md green-green-check section must document the "
            "parametrized node id shape '<path>.py::<Class>::<test_name>[<param>]' "
            "(doc_review round-3 fix)."
        )

    def test_cli_reference_states_authoritative_source_is_pytest_ra_output(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "-rA" in section and "PASSED" in section, (
            "docs/cli-reference.md green-green-check section must state that the "
            "authoritative node id is exactly what pytest prints on its '-rA' "
            "'PASSED' summary line, not a hand-derived shape (doc_review round-3 fix)."
        )


@pytest.mark.unit
class TestCliReferenceGreenGreenCheckFiveRejectionConditions:
    def test_section_names_no_uncommitted_change_rejection(self) -> None:
        text = _read(CLI_REFERENCE_DOC)
        section = _extract_section(text, "### `green-green-check`")
        assert "no uncommitted production-source change" in section, (
            "docs/cli-reference.md green-green-check section must list the fifth "
            "rejection condition -- the stash push finding no uncommitted production-"
            "source change to reconstruct a 'before' state from -- matching SKILL.md "
            "step 4d.b's five-condition list (doc_review round-2 fix)."
        )


# ---------------------------------------------------------------------------
# code_review round-2 fix: rule-count cross-references must not lag behind
# backlog-contract.md's numbered validate() rule list.
# ---------------------------------------------------------------------------

ONBOARDING_DOC = REPO_ROOT / "docs" / "onboarding.md"
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"
SPEC_TO_BACKLOG_DOC = REPO_ROOT / "docs" / "skills" / "spec-to-backlog.md"


def _highest_numbered_rule(text: str) -> int:
    """Return the highest N for a '\\nN. ' numbered rule line in backlog-contract.md's rule list."""
    matches = re.findall(r"\n(\d+)\. ", text)
    assert matches, "docs/backlog-contract.md must contain a numbered rule list"
    return max(int(n) for n in matches)


@pytest.mark.unit
class TestRuleCountCrossReferencesSync:
    @pytest.mark.parametrize(
        "doc_path",
        [ONBOARDING_DOC, ZERO_TO_READY_DOC, SPEC_TO_BACKLOG_DOC],
        ids=["onboarding.md", "zero-to-ready.md", "spec-to-backlog.md"],
    )
    def test_doc_does_not_state_a_stale_rule_count(self, doc_path: Path) -> None:
        current_rule_count = _highest_numbered_rule(_read(BACKLOG_CONTRACT_DOC))
        text = _read(doc_path)
        for n in range(1, current_rule_count):
            stale_token = f"({n} rules"
            assert stale_token not in text, (
                f"{doc_path} states a stale rule count ({stale_token!r}); "
                f"docs/backlog-contract.md's numbered rule list currently reaches "
                f"{current_rule_count} (code_review round-2 fix)."
            )

    @pytest.mark.parametrize(
        "doc_path",
        [ONBOARDING_DOC, ZERO_TO_READY_DOC, SPEC_TO_BACKLOG_DOC],
        ids=["onboarding.md", "zero-to-ready.md", "spec-to-backlog.md"],
    )
    def test_doc_states_the_current_rule_count(self, doc_path: Path) -> None:
        current_rule_count = _highest_numbered_rule(_read(BACKLOG_CONTRACT_DOC))
        text = _read(doc_path)
        assert f"{current_rule_count} rules" in text, (
            f"{doc_path} must state the current rule count ({current_rule_count} rules) "
            "so it stays in sync with docs/backlog-contract.md (code_review round-2 fix)."
        )


@pytest.mark.unit
class TestBacklogContractTddCycleLogTwoOrchestratorOnlyPhases:
    def test_tdd_cycle_log_row_names_both_orchestrator_only_phases(self) -> None:
        text = _read(BACKLOG_CONTRACT_DOC)
        row = _find_row(text, "| `## TDD Cycle Log`")
        assert row, "docs/backlog-contract.md must contain a '| `## TDD Cycle Log`' Required Sections table row"
        assert "RED_OBSERVED" in row and "GREEN_GREEN_OBSERVED" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must name both orchestrator-only "
            "phases, RED_OBSERVED and GREEN_GREEN_OBSERVED (AC-DOC-005)."
        )

    def test_tdd_cycle_log_row_names_green_green_check_producer(self) -> None:
        text = _read(BACKLOG_CONTRACT_DOC)
        row = _find_row(text, "| `## TDD Cycle Log`")
        assert row, "docs/backlog-contract.md must contain a '| `## TDD Cycle Log`' Required Sections table row"
        assert "green-green-check" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must name 'devbench green-green-check' "
            "as the GREEN_GREEN_OBSERVED producer (AC-DOC-005)."
        )
        assert "write_red_observed_entry" in row, (
            "docs/backlog-contract.md TDD Cycle Log row must still name write_red_observed_entry "
            "as the RED_OBSERVED producer (AC-DOC-005)."
        )

    def test_tdd_cycle_log_row_names_refactor_precondition(self) -> None:
        text = _read(BACKLOG_CONTRACT_DOC)
        row = _find_row(text, "| `## TDD Cycle Log`")
        assert row, "docs/backlog-contract.md must contain a '| `## TDD Cycle Log`' Required Sections table row"
        assert "refactor" in row.lower(), (
            "docs/backlog-contract.md TDD Cycle Log row must note GREEN_GREEN_OBSERVED is a "
            "refactor done-gate precondition (AC-DOC-005)."
        )
