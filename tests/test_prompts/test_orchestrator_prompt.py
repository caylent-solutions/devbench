"""Content-integrity tests for orchestrator-prompt.md.

These tests assert that specific instructions are present in the prompt file.
They are NOT behavioral tests — they are guardrails against accidental removal
of critical orchestrator guidance during future prompt edits.

If a test here fails because you intentionally changed the prompt, verify that
the removed instruction was deliberately replaced or is no longer needed before
updating or removing the assertion.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "orchestrator-prompt.md"


def _prompt_text() -> str:
    return PROMPT_PATH.read_text()


def test_prompt_contains_set_status_in_progress() -> None:
    # The orchestrator must call `set-status <unit-id> in-progress` at the start
    # of Step 3, immediately after logging — before any implementation work begins.
    # This prevents BACKLOG.md from showing 'in-queue' while a work unit is actively
    # being modified, which causes status mismatch warnings on every subsequent CLI call.
    # If this assertion fails after a prompt refactor, confirm the set-status call was
    # preserved (possibly with different syntax) rather than simply removed.
    prompt = _prompt_text()
    assert "set-status" in prompt and "in-progress" in prompt, (
        "Step 3 must contain a 'set-status <unit-id> in-progress' call "
        "to transition the work unit before implementation begins"
    )


def test_set_status_precedes_execution_sequence() -> None:
    # The set-status call must appear BEFORE the numbered execution sequence so the
    # agent cannot begin reading files or writing code before claiming the work unit.
    # If this assertion fails, the set-status call may have been moved below the
    # numbered list — restore it to immediately after the initial log line.
    prompt = _prompt_text()

    set_status_idx = prompt.find("set-status")
    follow_sequence_idx = prompt.find("Follow this execution sequence:")

    assert set_status_idx != -1, "orchestrator-prompt.md must contain a set-status call in Step 3"
    assert follow_sequence_idx != -1, (
        "orchestrator-prompt.md must contain the 'Follow this execution sequence:' marker"
    )
    assert set_status_idx < follow_sequence_idx, (
        "set-status call must appear before 'Follow this execution sequence:' — "
        "the work unit must be claimed before implementation begins"
    )


def test_prompt_contains_pre_review_self_check() -> None:
    # Step 3 must include a pre-review self-check step between "Verify all work" and
    # "Update the work unit status to in-review". Without this gate, common judge failures
    # (missing docstrings, call-count-only assertions, untested edge cases) are not caught
    # before the full judge round-trip. If this assertion fails after a prompt refactor,
    # confirm that a self-check step with checklist items was preserved, not just removed.
    prompt = _prompt_text()
    assert "Pre-review self-check" in prompt, (
        "Step 3 must contain a 'Pre-review self-check' step before the in-review transition"
    )


def test_pre_review_checklist_contains_all_five_items() -> None:
    # All five checklist items must be present verbatim. Each item targets a specific
    # class of judge failures observed during E6: missing docstrings, call-count assertions,
    # untested validation logic, mock-only functional tests, and manifest drift.
    # If this assertion fails, restore the missing item(s) — partial checklists miss the
    # failure modes they were designed to catch.
    prompt = _prompt_text()
    expected_items = [
        "Docstrings describe every new code path added, not just the happy path",
        "Every new branch or conditional has a corresponding test assertion (not just call count)",
        "All validation logic (regex, guard clauses, type checks) has tests for valid and invalid inputs",
        "Functional tests assert observable behaviour, not only that mocks were called",
        "`git diff --name-only --cached` matches the Changes Manifest exactly",
    ]
    for item in expected_items:
        assert item in prompt, (
            f"Pre-review self-check is missing required item: {item!r}"
        )


def test_in_review_transition_is_step_10() -> None:
    # After inserting the pre-review self-check as step 9, the in-review transition
    # must be renumbered to step 10. If this assertion fails, the step numbering was
    # not updated — either the new step was not inserted or the downstream steps were
    # not renumbered consistently.
    prompt = _prompt_text()
    assert "10. **Update the work unit status** to `in-review`" in prompt, (
        "In-review transition must be numbered step 10 after the pre-review self-check is inserted as step 9"
    )


def test_log_actions_is_step_11() -> None:
    # After inserting the pre-review self-check as step 9, the log-actions step must
    # be renumbered to step 11. If this assertion fails, the step was not renumbered —
    # restore the correct numbering to keep the sequence consistent.
    prompt = _prompt_text()
    assert "11. **Log all actions**" in prompt, (
        "Log-actions step must be numbered step 11 after pre-review self-check is inserted as step 9"
    )


def test_prompt_red_requires_failure_output_logged() -> None:
    # The RED phase must require pasting actual test runner output into the TDD Cycle Log,
    # not just claiming "test fails as expected". Without this requirement an agent can
    # fabricate the RED phase without ever running the test — making the TDD cycle
    # unverifiable. If this assertion fails after a prompt refactor, confirm the RED
    # bullet still requires logging real failure output, not just confirming the failure.
    prompt = _prompt_text()
    assert "paste the actual failure output" in prompt, (
        "RED bullet must require pasting actual test runner failure output into the TDD Cycle Log"
    )


def test_prompt_red_contains_do_not_proceed_gate() -> None:
    # The RED phase must include an explicit gate preventing the agent from moving to GREEN
    # before failure output has been logged. This closes the loophole where an agent
    # writes a test, skips running it, and proceeds directly to implementation.
    # If this assertion fails after a prompt refactor, restore the gate phrase or an
    # equivalent instruction that blocks the GREEN phase until RED output is recorded.
    prompt = _prompt_text()
    assert "Do not proceed to GREEN until failure output is logged" in prompt, (
        "RED bullet must contain the gate phrase blocking GREEN until failure output is logged"
    )


def test_prompt_contains_branch_precedence_rule() -> None:
    # Step 5A must state the two-rule precedence for branch naming so the agent
    # never invents ad-hoc names. The rules are: (1) use the branch from the work
    # unit's Target Repository section if specified, (2) otherwise fall back to
    # backlog/<unit-id-lowercase>. If this assertion fails after a prompt refactor,
    # confirm both rules are still present and the fallback default is documented.
    prompt = _prompt_text()
    assert "Target Repository" in prompt and "backlog/<unit-id-lowercase>" in prompt, (
        "Step 5A must contain the two-rule branch naming precedence: "
        "Target Repository branch if specified, otherwise backlog/<unit-id-lowercase>"
    )


def test_prompt_does_not_hardcode_backlog_branch_as_command() -> None:
    # Step 5A must use the generic <resolved-branch> placeholder in git commands,
    # not the literal string backlog/<unit-id-lowercase>. Hardcoding the fallback
    # name in the command prevents the agent from using the work unit's specified
    # branch, which causes branch naming drift across work units.
    # If this assertion fails, replace `backlog/<unit-id-lowercase>` in git checkout
    # and git push lines with `<resolved-branch>` and document the rule above them.
    prompt = _prompt_text()
    assert "git checkout -b <resolved-branch>" in prompt, (
        "Step 5A git checkout command must use <resolved-branch>, not a hardcoded backlog/ name"
    )
    assert "git push -u origin <resolved-branch>" in prompt, (
        "Step 5A git push command must use <resolved-branch>, not a hardcoded backlog/ name"
    )
