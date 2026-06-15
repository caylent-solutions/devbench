"""Exit taxonomy: classify a supervised-session outcome to an exit (AC-2, FR-13).

Spec Section 4.6: the supervisor classifies its own outcome into clean (exit 0),
fault (classified non-zero), or quota (NOT an exit -- a holding state). These
tests pin the mapping for every row of the Section 4.6 table so a regression in
the classifier surfaces immediately.
"""

from __future__ import annotations

import pytest

from devbench.supervise import (
    SUPERVISE_FAULT_EXIT_CODE,
    SuperviseOutcome,
    classify_supervise_outcome,
)


@pytest.mark.unit
class TestCleanOutcomes:
    """ALL_DONE / NO_ACTIONABLE-with-only-operator-holds -> exit 0 (Section 4.6)."""

    def test_all_done_is_clean_exit_zero(self) -> None:
        outcome = classify_supervise_outcome(marker="ALL_DONE", child_exitstatus=0)
        assert outcome.is_clean is True
        assert outcome.exit_code == 0
        assert outcome.exit_reason == "all-done"

    def test_no_actionable_operator_holds_is_clean(self) -> None:
        outcome = classify_supervise_outcome(marker="NO_ACTIONABLE", child_exitstatus=0)
        assert outcome.is_clean is True
        assert outcome.exit_code == 0
        assert outcome.exit_reason == "no-actionable"

    def test_terminal_exit_log_marker_is_clean(self) -> None:
        outcome = classify_supervise_outcome(marker="[ORCHESTRATOR_TERMINAL_EXIT]", child_exitstatus=0)
        assert outcome.is_clean is True
        assert outcome.exit_code == 0

    def test_bare_clean_exit_no_marker_is_clean(self) -> None:
        # A child that exits 0 with NO terminal sentinel on the PTY is a bare clean
        # process exit -> clean "all-done" (Section 4.6: 0 = clean). This is the
        # fallthrough after the quota / non-zero-exit / clean-marker / fault-marker
        # rules, exercised when the CLI exits silently.
        outcome = classify_supervise_outcome(marker=None, child_exitstatus=0)
        assert outcome.is_clean is True
        assert outcome.exit_code == 0
        assert outcome.exit_reason == "all-done"

    def test_clean_exit_unknown_exitstatus_is_clean(self) -> None:
        # The child EOF was observed before the OS reaped it (exitstatus None) and no
        # marker is present: still a bare clean exit (not a fault).
        outcome = classify_supervise_outcome(marker=None, child_exitstatus=None)
        assert outcome.is_clean is True
        assert outcome.exit_reason == "all-done"


@pytest.mark.unit
class TestFaultOutcomes:
    """Every fault row maps to a classified NON-ZERO exit (Section 4.6, FR-13)."""

    def test_claude_crash_nonzero_exit(self) -> None:
        outcome = classify_supervise_outcome(marker=None, child_exitstatus=3)
        assert outcome.is_clean is False
        assert outcome.is_quota is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "claude-exit-3"

    def test_circuit_breaker_is_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="circuit_breaker", child_exitstatus=0)
        assert outcome.is_clean is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "circuit-breaker"

    def test_harness_self_edit_block_is_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="harness_block", child_exitstatus=0)
        assert outcome.is_clean is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "harness-self-edit-block"

    def test_stop_reason_premature_is_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="stop_reason", child_exitstatus=0, stop_reason="premature-turn-end")
        assert outcome.is_clean is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "stop-reason-premature-turn-end"

    def test_prompt_timeout_is_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="prompt_timeout", child_exitstatus=None, phase="ready")
        assert outcome.is_clean is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "prompt-timeout-ready"

    def test_restart_cap_exhausted_is_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="restart_cap_exhausted", child_exitstatus=None)
        assert outcome.is_clean is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "restart-cap-exhausted"

    def test_quota_resume_cap_exhausted_is_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="quota_resume_cap_exhausted", child_exitstatus=None)
        assert outcome.is_clean is False
        assert outcome.is_quota is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "quota-resume-cap-exhausted"

    def test_clean_marker_but_nonzero_child_exit_is_fault(self) -> None:
        # ALL_DONE marker but the child still exited non-zero: a clean sentinel
        # cannot launder a non-zero process exit (defense in depth).
        outcome = classify_supervise_outcome(marker="ALL_DONE", child_exitstatus=5)
        assert outcome.is_clean is False
        assert outcome.exit_code == SUPERVISE_FAULT_EXIT_CODE
        assert outcome.exit_reason == "claude-exit-5"


@pytest.mark.unit
class TestQuotaIsNotAnExit:
    """Quota exhaustion is a holding state, NEVER an exit (FR-13, Section 4.6)."""

    def test_quota_outcome_is_not_clean_and_not_fault(self) -> None:
        outcome = classify_supervise_outcome(marker="quota_limit", child_exitstatus=None)
        assert outcome.is_quota is True
        assert outcome.is_clean is False
        # exit_code is None: quota never exits (the caller transitions to
        # quota-waiting instead of returning an exit code).
        assert outcome.exit_code is None
        assert outcome.exit_reason == "quota-waiting"


@pytest.mark.unit
class TestOutcomeIsValueObject:
    """SuperviseOutcome is a value object: same inputs classify to equal outcomes."""

    def test_same_inputs_are_equal(self) -> None:
        # A frozen dataclass derives value equality; two clean ALL_DONE outcomes
        # compare equal, proving the classifier is deterministic and the result is
        # a comparable value object (not an identity-based holder).
        first = classify_supervise_outcome(marker="ALL_DONE", child_exitstatus=0)
        second = classify_supervise_outcome(marker="ALL_DONE", child_exitstatus=0)
        assert first == second
        assert isinstance(first, SuperviseOutcome)

    def test_different_dispositions_are_not_equal(self) -> None:
        clean = classify_supervise_outcome(marker="ALL_DONE", child_exitstatus=0)
        fault = classify_supervise_outcome(marker=None, child_exitstatus=3)
        assert clean != fault
