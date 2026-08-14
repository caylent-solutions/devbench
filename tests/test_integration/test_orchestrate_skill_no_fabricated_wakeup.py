"""Regression: orchestrate SKILL must deny background execution and git writes.

Background (observed 2026-08-14T14:44): the orchestrator delegated work unit
E2-F5-S1-T1 to the executor, then attempted ``git add CHANGELOG.md`` itself.
Orchestrator-issued git writes are out of scope -- staging belongs to the
executor and committing to ``devbench git-ops`` -- so a guard hook correctly
denied it. Instead of emitting the right devbench command, the model ended its
turn on this claim:

    "The executor agent is running in the background for work unit
    E2-F5-S1-T1. I've scheduled a fallback check-in, but the primary trigger
    will be the agent's completion notification, at which point I'll continue
    the orchestrate loop (steps 4a-9) automatically."

No such mechanism exists anywhere in devbench: Agent calls are synchronous,
there are no background tasks, no completion callbacks, and nothing that
resumes a wound-down turn. The SDK session ended and the orchestrator process
exited with 105 work units unfinished.

Two SKILL rules close the prompt half of that failure (the harness half is the
bounded premature-turn-end restart in ``cmd_start``): no fabricated wakeup
mechanism, and no orchestrator-issued git writes. Pinned by-content, following
``test_orchestrate_skill_no_recap_anti_pattern.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "plugin"
    / "devbench-orchestrate"
    / "skills"
    / "orchestrate"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.mark.integration
class TestNoFabricatedWakeupMechanism:
    """The SKILL must state plainly that nothing will ever resume the model."""

    def test_states_agent_calls_are_synchronous(self, skill_text: str) -> None:
        assert "Agent tool calls are SYNCHRONOUS" in skill_text

    @pytest.mark.parametrize(
        "denied_mechanism",
        [
            "background tasks",
            "completion notifications",
            "callbacks",
            "schedulers",
            "wakeup timers",
            "fallback check-ins",
        ],
    )
    def test_names_each_nonexistent_mechanism(self, skill_text: str, denied_mechanism: str) -> None:
        """Naming them individually matters: the observed failure invented two
        at once (a background agent AND a scheduled check-in)."""
        assert denied_mechanism in skill_text

    def test_declares_no_such_feature_exists(self, skill_text: str) -> None:
        assert "No such feature exists anywhere in devbench" in skill_text

    @pytest.mark.parametrize(
        "fabricated_claim",
        [
            "the agent is running in the background",
            "I've scheduled a check-in",
            "I'll be notified when it completes",
            "I'll continue automatically",
        ],
    )
    def test_quotes_the_forbidden_claim_shapes(self, skill_text: str, fabricated_claim: str) -> None:
        """The rule quotes the exact phrasings so the model can pattern-match
        its own output against them before ending a turn."""
        assert fabricated_claim in skill_text

    def test_labels_the_claim_a_fabrication_that_kills_the_run(self, skill_text: str) -> None:
        assert "FABRICATION" in skill_text
        assert "nothing is listening" in skill_text

    def test_directs_the_model_to_act_on_the_returned_result(self, skill_text: str) -> None:
        """A returned Agent call is already finished: the correct action is the
        next tool call, not a turn ending."""
        assert "its work is already finished" in skill_text


@pytest.mark.integration
class TestNoOrchestratorGitWrites:
    """Git state changes belong to the executor and ``devbench git-ops``."""

    @pytest.mark.parametrize(
        "forbidden_command",
        ["git add", "git commit", "git push", "git restore", "git stash"],
    )
    def test_forbids_each_state_changing_git_command(self, skill_text: str, forbidden_command: str) -> None:
        assert f"`{forbidden_command}`" in skill_text

    def test_names_the_correct_owners(self, skill_text: str) -> None:
        assert "Staging belongs to the executor" in skill_text
        assert "uv run devbench git-ops <id>" in skill_text

    def test_permits_read_only_inspection(self, skill_text: str) -> None:
        """Diagnosis must stay available; only writes are forbidden."""
        assert "Read-only inspection" in skill_text

    def test_says_a_guard_denial_is_correct_and_must_not_end_the_turn(self, skill_text: str) -> None:
        """The observed run treated a correct denial as a reason to stop."""
        assert "that denial is CORRECT" in skill_text
        assert "do not end your turn over it" in skill_text
