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
