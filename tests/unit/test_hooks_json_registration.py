"""Issue #138 regression: the Stop event must register exactly one hook
(``continue-orchestration.sh``).

The Stop event used to register two hooks -- ``hook-logger.sh`` AND
``continue-orchestration.sh`` -- and the dual-hook registration was a
candidate root cause for the recurring Stop-hook self-termination class:
``hook-logger.sh`` prints nothing on stdout, which Claude Code may
interpret as an implicit "approve" vote that drowns out
``continue-orchestration.sh``'s ``decision: block`` vote.

This test pins the single-hook registration so a future config drift
re-introducing ``hook-logger.sh`` on the Stop event fails CI before merge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HOOKS_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate" / "hooks" / "hooks.json"
)


@pytest.fixture(scope="module")
def hooks_json() -> dict:
    return json.loads(HOOKS_JSON_PATH.read_text(encoding="utf-8"))


class TestStopEventSingleHookRegistration:
    """The Stop event must have exactly one registered hook."""

    def test_hooks_json_file_exists(self) -> None:
        assert HOOKS_JSON_PATH.is_file(), f"hooks.json missing at {HOOKS_JSON_PATH}"

    def test_stop_event_registered(self, hooks_json: dict) -> None:
        assert "Stop" in hooks_json["hooks"], "Stop event entirely missing from hooks.json"

    def test_stop_event_has_single_matcher_block(self, hooks_json: dict) -> None:
        stop_blocks = hooks_json["hooks"]["Stop"]
        assert len(stop_blocks) == 1, (
            f"Stop event must have a single matcher block; got {len(stop_blocks)} blocks (issue #138)."
        )

    def test_stop_event_registers_exactly_one_hook(self, hooks_json: dict) -> None:
        stop_hooks = hooks_json["hooks"]["Stop"][0]["hooks"]
        assert len(stop_hooks) == 1, (
            "Stop event must register exactly one hook to prevent the "
            "empty-stdout-vote-vs-block-decision dispatcher ambiguity "
            f"(issue #138). Got {len(stop_hooks)} hooks: {stop_hooks!r}"
        )

    def test_stop_hook_is_continue_orchestration_only(self, hooks_json: dict) -> None:
        stop_hooks = hooks_json["hooks"]["Stop"][0]["hooks"]
        sole_hook = stop_hooks[0]
        assert "continue-orchestration.sh" in sole_hook["command"], (
            f"The single Stop hook must be continue-orchestration.sh; got command={sole_hook['command']!r}"
        )
        assert "hook-logger.sh" not in sole_hook["command"], (
            "hook-logger.sh must NOT be registered on the Stop event "
            "(issue #138 -- empty-stdout vote drowned the block decision). "
            "If you need Stop events logged, continue-orchestration.sh "
            "calls 'uv run devbench log' directly."
        )
