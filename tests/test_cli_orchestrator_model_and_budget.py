"""Regression tests for the orchestrator model-pin, fatal-error fail-fast, and
the bounded turn-end continuation budget.

These cover the three compounding defects behind the 2026-06-13 runaway:
  1. the orchestrate SDK session inherited the interactive Claude Code model
     because ``_run`` omitted ``model=`` (now pinned from ``orchestrate.model``);
  2. a hard ``model_not_found`` was fed into the continuation loop instead of
     failing fast (now ``detect_fatal_sdk_error`` exits on occurrence 1);
  3. the continuation budget was unreachable because ``stall_count`` reset on
     every non-ResultMessage (now it resets only on genuine progress -- a claim).
"""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.constants import (
    ORCHESTRATOR_FATAL_ERROR_EXIT_CODE,
    ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE,
)

# ---------------------------------------------------------------------------
# Duck-typed SDK message fakes -- no SDK import, mirrors production duck typing
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeResultMsg:
    """A ResultMessage-like turn boundary (has subtype + num_turns + result)."""

    subtype: str = "success"
    num_turns: int = 1
    result: str = ""


@dataclasses.dataclass
class _FakeAssistantMsg:
    """An AssistantMessage-like message; ``error`` carries a fatal code when set."""

    error: str | None = None
    content: Any = None


@dataclasses.dataclass
class _FakeToolUseBlock:
    """A ToolUseBlock-like content block (the model actively invoking a tool)."""

    name: str = "Bash"
    input: Any = dataclasses.field(default_factory=lambda: {"command": "terraform apply"})


class TaskProgressMessage:
    """Sub-agent Task activity message -- class name is what _is_genuine_progress checks."""


def _make_scripted_fake_sdk(turns: list[list[Any]]) -> Any:
    """Fake claude_agent_sdk yielding ``turns[i]`` on the i-th receive_response()
    call, then exhausting so the loop exits cleanly."""
    idx = [0]

    class _FakeClient:
        def __init__(self, options: Any = None, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def query(self, prompt: str, session_id: str = "default") -> None:
            pass

        async def receive_response(self) -> Any:
            i = idx[0]
            idx[0] += 1
            if i < len(turns):
                for msg in turns[i]:
                    yield msg

    fake_sdk: Any = types.ModuleType("claude_agent_sdk")
    fake_sdk.ClaudeAgentOptions = MagicMock()
    fake_sdk.ClaudeSDKClient = _FakeClient
    return fake_sdk


def _make_repeating_fake_sdk(messages_per_turn: list[Any]) -> Any:
    """Fake claude_agent_sdk whose client yields ``messages_per_turn`` on EVERY
    ``receive_response()`` call (a fresh generator each turn).

    This simulates a session that ends every turn the same way -- the condition
    the continuation budget must bound. ``ClaudeAgentOptions`` is a MagicMock so
    tests can assert the ``model=`` kwarg.
    """

    class _FakeClient:
        def __init__(self, options: Any = None, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def query(self, prompt: str, session_id: str = "default") -> None:
            pass

        async def receive_response(self) -> Any:
            for msg in messages_per_turn:
                yield msg

    fake_sdk: Any = types.ModuleType("claude_agent_sdk")
    fake_sdk.ClaudeAgentOptions = MagicMock()
    fake_sdk.ClaudeSDKClient = _FakeClient
    return fake_sdk


def _run_cmd_start(fake_sdk: Any, tmp_path: Path) -> int:
    with (
        patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}),
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        patch("devbench.cli._fire_orchestrator_stop_notification", lambda reason: None),
    ):
        return cli.cmd_start()


# ---------------------------------------------------------------------------
# detect_fatal_sdk_error -- pure helper
# ---------------------------------------------------------------------------


class TestDetectFatalSdkError:
    @pytest.mark.parametrize(
        "err,expected",
        [
            ("model_not_found", "model_not_found"),
            ("MODEL_NOT_FOUND", "model_not_found"),
            ("authentication_error", "authentication_error"),
            ("permission_error", "permission_error"),
            ("invalid_request_error", "invalid_request_error"),
            ("not_found_error", "not_found_error"),
        ],
    )
    def test_fatal_codes_detected(self, err: str, expected: str) -> None:
        assert cli.detect_fatal_sdk_error(_FakeAssistantMsg(error=err)) == expected

    @pytest.mark.parametrize("err", ["rate_limit_error", "overloaded_error", "", None])
    def test_quota_and_empty_not_fatal(self, err: str | None) -> None:
        # Quota / rate-limit must route to the quota path, NOT the fatal path.
        assert cli.detect_fatal_sdk_error(_FakeAssistantMsg(error=err)) is None

    def test_message_without_error_attr_is_none(self) -> None:
        assert cli.detect_fatal_sdk_error(_FakeResultMsg(result="x")) is None


# ---------------------------------------------------------------------------
# _resolve_orchestrator_model -- fail-fast, no fallback
# ---------------------------------------------------------------------------


class TestResolveOrchestratorModel:
    def test_returns_configured_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "model", "claude-opus-4-8")
        assert cli._resolve_orchestrator_model() == "claude-opus-4-8"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "model", "  sonnet  ")
        assert cli._resolve_orchestrator_model() == "sonnet"

    @pytest.mark.parametrize("bad", [None, "", "   "])
    def test_unset_raises(self, monkeypatch: pytest.MonkeyPatch, bad: str | None) -> None:
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "model", bad)
        with pytest.raises(cli._OrchestratorModelUnsetError, match=r"orchestrate\.model"):
            cli._resolve_orchestrator_model()


# ---------------------------------------------------------------------------
# cmd_start: model pinned into ClaudeAgentOptions + fail-fast when unset
# ---------------------------------------------------------------------------


class TestCmdStartModelPin:
    def test_options_built_with_configured_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "model", "claude-opus-4-8")
        fake_sdk = _make_repeating_fake_sdk([])  # empty turn -> loop exhausts, clean exit
        _run_cmd_start(fake_sdk, tmp_path)
        # The orchestrate session must be constructed with the configured model,
        # never inheriting the interactive Claude Code selection.
        kwargs = fake_sdk.ClaudeAgentOptions.call_args.kwargs
        assert kwargs.get("model") == "claude-opus-4-8", f"options model= not pinned: {kwargs!r}"

    def test_fail_fast_when_model_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "model", None)
        fake_sdk = _make_repeating_fake_sdk([])
        rc = _run_cmd_start(fake_sdk, tmp_path)
        assert rc == 1, "cmd_start must fail fast (rc=1) when orchestrate.model is unset"
        # And it must NOT have constructed the SDK session.
        assert fake_sdk.ClaudeAgentOptions.call_count == 0


# ---------------------------------------------------------------------------
# Bounded continuation budget + fatal-error fail-fast (the runaway fixes)
# ---------------------------------------------------------------------------


class TestContinuationBudgetBounded:
    def test_no_progress_no_sentinel_trips_budget(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A turn that emits an AssistantMessage + a non-terminal ResultMessage
        every time (no claim) must TRIP the budget and exit -- not loop forever.

        On the pre-fix code (stall_count reset on every non-ResultMessage) this
        would never trip and would hang; the bounded budget makes it exit with
        the continuation-exhausted code.
        """
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS", "3")
        # Each turn: a plain assistant message (not a claim, not fatal) then a
        # non-terminal ResultMessage (empty result -> continuation).
        per_turn = [_FakeAssistantMsg(content="thinking, no tool call"), _FakeResultMsg(result="")]
        fake_sdk = _make_repeating_fake_sdk(per_turn)
        rc = _run_cmd_start(fake_sdk, tmp_path)
        assert rc == ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE

    def test_fatal_model_error_exits_on_first_occurrence(self, tmp_path: Path) -> None:
        """A synthetic model_not_found AssistantMessage exits with the fatal-error
        code immediately -- never entering the continuation loop."""
        per_turn = [_FakeAssistantMsg(error="model_not_found"), _FakeResultMsg(result="")]
        fake_sdk = _make_repeating_fake_sdk(per_turn)
        rc = _run_cmd_start(fake_sdk, tmp_path)
        assert rc == ORCHESTRATOR_FATAL_ERROR_EXIT_CODE

    def test_long_run_with_tool_use_does_not_trip_budget(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A legitimately long-running unit -- many turns of tool-use + a
        non-terminal ResultMessage with NO new claim -- must NOT trip the budget,
        because genuine activity (a tool-use) resets the stall counter. Regression
        guard for the over-strict 'reset only on a claim' that would kill a quiet
        sub-agent doing a long terraform apply / make validate.
        """
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS", "3")
        tool_turn = [_FakeAssistantMsg(content=[_FakeToolUseBlock()]), _FakeResultMsg(result="")]
        # 6 working turns (> budget of 3) then a terminal sentinel.
        turns = [tool_turn for _ in range(6)] + [[_FakeResultMsg(result="ALL_DONE")]]
        fake_sdk = _make_scripted_fake_sdk(turns)
        rc = _run_cmd_start(fake_sdk, tmp_path)
        assert rc != ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE
        assert rc != ORCHESTRATOR_FATAL_ERROR_EXIT_CODE

    def test_long_run_with_subagent_activity_does_not_trip_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sub-agent Task activity (TaskProgressMessage) also counts as genuine
        progress and resets the budget, so a quiet sub-agent run survives."""
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS", "3")
        task_turn = [TaskProgressMessage(), _FakeResultMsg(result="")]
        turns = [task_turn for _ in range(6)] + [[_FakeResultMsg(result="ALL_DONE")]]
        fake_sdk = _make_scripted_fake_sdk(turns)
        rc = _run_cmd_start(fake_sdk, tmp_path)
        assert rc != ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE


def _verify_block(unit_id: str) -> Any:
    return _FakeAssistantMsg(content=[_FakeToolUseBlock(input={"command": f"uv run devbench verify-ac {unit_id}"})])


def _claim_block(unit_id: str) -> Any:
    return _FakeAssistantMsg(content=[_FakeToolUseBlock(input={"command": f"uv run devbench claim {unit_id}"})])


class TestWithinClaimConvergenceIntegration:
    """The convergence bound trips inside the real ``_run`` loop (not just the tracker)."""

    def test_repeated_identical_failure_blocks_unit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-1: a claim that repeats the SAME verify-ac failure beyond the cap
        # force-blocks the unit and exits the loop. The budget is set high so it
        # is the CONVERGENCE bound (not the continuation budget) that fires.
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS", "999")
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_WITHIN_CLAIM_ATTEMPTS", "3")
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_WITHIN_CLAIM_CONVERGENCE_CHECK", "true")

        blocked: list[tuple[str, str]] = []
        monkeypatch.setattr(cli, "_block_non_converging_claim", lambda uid, recurring: blocked.append((uid, recurring)))

        # Turn 1 claims; subsequent turns re-run the SAME verify-ac (the failure).
        turns = [
            [_claim_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            [_verify_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            [_verify_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            [_verify_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            # would loop forever on pre-fix code; a terminal sentinel as a safety net.
            [_FakeResultMsg(result="ALL_DONE")],
        ]
        fake_sdk = _make_scripted_fake_sdk(turns)
        _run_cmd_start(fake_sdk, tmp_path)
        assert blocked, "the convergence bound must force-block the non-converging claim"
        uid, recurring = blocked[0]
        assert uid == "E1-F1-S1-T1"
        assert "verify-ac" in recurring and "E1-F1-S1-T1" in recurring

    def test_disabled_check_never_blocks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS", "999")
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_WITHIN_CLAIM_ATTEMPTS", "2")
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_WITHIN_CLAIM_CONVERGENCE_CHECK", "false")
        blocked: list[tuple[str, str]] = []
        monkeypatch.setattr(cli, "_block_non_converging_claim", lambda uid, recurring: blocked.append((uid, recurring)))
        turns = [
            [_claim_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            [_verify_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            [_verify_block("E1-F1-S1-T1"), _FakeResultMsg(result="")],
            [_FakeResultMsg(result="ALL_DONE")],
        ]
        fake_sdk = _make_scripted_fake_sdk(turns)
        _run_cmd_start(fake_sdk, tmp_path)
        assert not blocked, "a disabled convergence check must never block"


# ---------------------------------------------------------------------------
# config_loader: orchestrate.model parsing + validation
# ---------------------------------------------------------------------------


class TestOrchestrateModelConfig:
    def _load(self, body: str) -> Any:
        import os
        import tempfile

        from devbench.config_loader import load_runtime_config

        d = tempfile.mkdtemp()
        cfg = Path(d) / "devbench.yaml"
        cfg.write_text("repos:\n  caylent-solutions/x: {default_branch: main, checkout_directory: x}\n" + body)
        return load_runtime_config(cfg, os.environ)

    def test_model_parsed_when_set(self) -> None:
        assert self._load("orchestrate:\n  model: claude-opus-4-8\n").orchestrate.model == "claude-opus-4-8"

    def test_model_none_when_absent(self) -> None:
        assert self._load("").orchestrate.model is None

    def test_coexists_with_max_cascade_depth(self) -> None:
        rc = self._load("orchestrate:\n  max_cascade_depth: 2\n  model: sonnet\n")
        assert rc.orchestrate.model == "sonnet"
        assert rc.orchestrate.max_cascade_depth == 2

    def test_haiku_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"[Hh]aiku"):
            self._load("orchestrate:\n  model: claude-haiku-4-5\n")

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            self._load('orchestrate:\n  model: ""\n')
