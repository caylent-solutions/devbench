"""End-to-end synthetic quota cycle covering journeys J-1..J-3 (E2-F5-S1-T1).

Drives the REAL orchestrate entry point (``cli.cmd_start``) through a
synthetic quota-exhaustion-then-recover cycle, faking only the two external
I/O boundaries: the ``claude_agent_sdk`` module (injected via
``sys.modules``) and the network-bound recovery probe
(``devbench.cli.recovery_probe``). Everything in between -- quota
detection, checkpoint persistence, the wait engine, audit-comment
appending, and the in-process resume loop -- runs unmocked so the marker
sequence and journeys are proven against production code, not a stand-in.

Journeys (spec S10.4, AC-31):

- J-1 (unattended overnight): quota hits, the orchestrator waits, recovers,
  and completes with rc=0 and no operator intervention.
- J-2 (operator interrupts): ``devbench stop`` (a real SIGTERM) delivered
  mid-wait stops the process promptly, proving no ``asyncio.shield`` is used
  (decision D-9) -- the checkpoint on disk is what survives, not a shielded
  coroutine.
- J-3 (operator inspects): ``quota-watcher`` and ``status`` both reflect the
  pause while it is in progress.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import sys
import threading
import time
import types
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench import log_setup as log_setup_mod
from devbench.config_loader import QuotaHandlingConfig
from devbench.quota import load_checkpoint

_UNIT_ID = "E1-F1-S1-T1"
_READINESS_POLL_INCREMENT_SECONDS = 0.02
_READINESS_TIMEOUT_SECONDS = 10.0


def _build_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write a minimal real backlog with a single in-progress work unit.

    Returns ``(workspace_root, backlog_root, backlog_index)`` -- the three
    paths that must all be patched together (``WORKSPACE_ROOT``,
    ``BACKLOG_ROOT``, ``BACKLOG_INDEX``) so the real ``_handle_quota_pause``
    -> ``_append_quota_audit_comment`` path and the SIGTERM handler's
    ``_find_in_flight_wu`` both resolve the same on-disk work unit instead
    of the real production backlog.
    """
    backlog_root = tmp_path / "backlog"
    backlog_root.mkdir(parents=True, exist_ok=True)
    wu_path = backlog_root / f"{_UNIT_ID}.md"
    wu_path.write_text(
        f"# {_UNIT_ID}: Synthetic quota-cycle task\n\n## Status: in-progress\n\n## Comments\n",
        encoding="utf-8",
    )
    backlog_index = tmp_path / "BACKLOG.md"
    index_row = (
        f"| {_UNIT_ID} | Synthetic quota-cycle task | Task | in-progress | None "
        f"| git-repo | `backlog/{_UNIT_ID}.md` |\n"
    )
    backlog_index.write_text(
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n" + index_row,
        encoding="utf-8",
    )
    return tmp_path, backlog_root, backlog_index


_FROZEN_NOW = datetime(2026, 1, 1, 0, 0, 59, tzinfo=UTC)
_KNOWN_RESET_AT_TEXT = "resets 12:01am (UTC)"

_QUOTA_SIGNAL_SHAPES = (
    pytest.param(None, id="reset_at-unknown"),
    pytest.param(_KNOWN_RESET_AT_TEXT, id="reset_at-known"),
)


def _quota_signal_clock_patch(reset_at_text: str | None) -> ExitStack:
    """Freeze ``devbench.quota``'s clock for the reset_at-known case only.

    ``_parse_reset_at_from_text`` (detection time) and ``_wait_toward_reset``
    (the real-time wait engine, spec S1.6) both read
    ``devbench.quota._get_current_utc``. Freezing it to ``_FROZEN_NOW``
    (one second before the ``_KNOWN_RESET_AT_TEXT`` minute boundary) keeps
    the resulting real-time wait window a deterministic ~1 second instead of
    depending on wall-clock alignment to the next minute boundary, which
    could otherwise require waiting up to 60 real seconds for a test run.
    No patch is applied for the reset_at-unknown case: no text is parsed
    and ``_wait_toward_reset`` no-ops on a ``None`` reset_at, so there is no
    reset-directed sleep to bound.
    """
    stack = ExitStack()
    if reset_at_text is not None:
        stack.enter_context(patch("devbench.quota._get_current_utc", return_value=_FROZEN_NOW))
    return stack


def _build_quota_signal(reset_at_text: str | None) -> SimpleNamespace:
    """A quota-shaped SDK message matching ``detect_quota_error`` Rule 7
    (``AssistantMessage.error == "rate_limit"``, source=claude-code-cli).

    The message shape is parameter-driven (spec AC-E2-F5-S1-T1-8): when
    *reset_at_text* is ``None`` the message carries no content block, so
    ``_extract_reset_at_from_content`` finds nothing and the downstream
    checkpoint/markers/quota-watcher all report the ``unknown`` sentinel.
    When *reset_at_text* is a provider-shaped string such as
    ``_KNOWN_RESET_AT_TEXT`` (``"resets 12:01am (UTC)"``),
    ``_parse_reset_at_from_text`` parses a concrete ``reset_at`` from the
    embedded content block, exercising the
    reset-time-bearing path AC-E2-F5-S1-T1-4 requires quota-watcher and the
    ``[QUOTA_WAITING]`` marker to report.
    """
    content = None if reset_at_text is None else [SimpleNamespace(text=reset_at_text)]
    return SimpleNamespace(error="rate_limit", content=content, status_code=None, body={})


def _terminal_message(result: str) -> SimpleNamespace:
    return SimpleNamespace(result=result)


def _assert_reset_at_marker(text: str, *, expect_known: bool) -> None:
    """Assert a ``reset_at=<value>`` token in *text* matches the expected shape.

    When *expect_known* is False, the literal ``reset_at=unknown`` sentinel
    must appear. When True, a parseable ISO-8601 UTC datetime must appear in
    its place (never the ``unknown`` sentinel), proving the reset-time path
    was actually exercised end-to-end rather than asserting its own literal.
    """
    match = re.search(r"reset_at=(\S+)", text)
    assert match is not None, f"no reset_at=<value> token found in: {text!r}"
    value = match.group(1)
    if not expect_known:
        assert value == "unknown", f"expected reset_at=unknown, got reset_at={value}"
        return
    assert value != "unknown", "expected a concrete reset time, got the unknown sentinel"
    datetime.fromisoformat(value)


def _install_fake_sdk(message_batches: list[list[object]]) -> tuple[Any, dict[str, int]]:
    """Install a fake ``claude_agent_sdk`` module whose ``query()`` yields the
    i-th batch of *message_batches* on its i-th invocation.

    Returns the fake module and a mutable call-count dict (key ``"n"``) so
    callers can assert how many fresh SDK sessions were opened.
    """
    mock_sdk: Any = types.ModuleType("claude_agent_sdk")
    mock_sdk.ClaudeAgentOptions = MagicMock()
    call_count = {"n": 0}

    async def mock_query(**kwargs: object) -> object:
        idx = call_count["n"]
        call_count["n"] += 1
        for message in message_batches[idx]:
            yield message

    mock_sdk.query = mock_query
    return mock_sdk, call_count


def _reset_logging(log_file: Path) -> ExitStack:
    """Reset the log_setup singleton and point DEVBENCH_LOG_FILE at *log_file*.

    Returns an ExitStack whose close() undoes both the env patch and the
    handler/singleton state, matching ``TestSetupLogging.setup_method`` in
    ``tests/test_log_setup.py``.
    """
    stack = ExitStack()
    log_setup_mod._state[0] = False
    logging.getLogger().handlers.clear()
    stack.enter_context(patch.dict(os.environ, {"DEVBENCH_LOG_FILE": str(log_file)}))
    log_setup_mod.setup_logging()

    def _cleanup() -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    stack.callback(_cleanup)
    return stack


def _quota_handling_config(*, poll_interval_seconds: int = 60, max_wait_seconds: int = 18000) -> QuotaHandlingConfig:
    return QuotaHandlingConfig(
        enabled=True,
        on_exhaustion="wait",
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
        on_exhaustion_timeout="drain",
        resume_strategy="continue_current_wu",
        audit_comment_on_wait=True,
        audit_comment_on_resume=True,
        log_structured_events=True,
    )


def _patched_orchestrator_environment(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    quota_cfg: QuotaHandlingConfig,
    recovery_probe_fn: Any,
    mock_sdk: Any,
) -> ExitStack:
    """Build the shared patch set every journey in this module needs.

    Patches the three workspace-location globals together (WORKSPACE_ROOT,
    BACKLOG_ROOT, BACKLOG_INDEX) so the real ``_handle_quota_pause`` audit
    comment and the SIGTERM handler's in-flight WU lookup both resolve to
    this test's synthetic backlog rather than the real one; patches
    ``RUNTIME_CONFIG.quota_handling`` (leaving the rest of RUNTIME_CONFIG
    untouched since cmd_start exercises other fields too); patches
    ``recovery_probe`` (the only network-bound call in the wait path); and
    injects the fake SDK module.
    """
    stack = ExitStack()
    stack.enter_context(patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}))
    stack.enter_context(patch("devbench.cli.WORKSPACE_ROOT", workspace_root))
    stack.enter_context(patch("devbench.cli.BACKLOG_ROOT", backlog_root))
    stack.enter_context(patch("devbench.cli.BACKLOG_INDEX", backlog_index))
    stack.enter_context(patch.object(cli.RUNTIME_CONFIG, "quota_handling", quota_cfg))
    stack.enter_context(patch("devbench.cli.recovery_probe", recovery_probe_fn))
    return stack


def _wait_for_condition(predicate: Any, timeout_seconds: float = _READINESS_TIMEOUT_SECONDS) -> None:
    """Bounded readiness-polling loop (no sleep-based synchronization, spec AC-E2-F5-S1-T1-8)."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(_READINESS_POLL_INCREMENT_SECONDS)
    raise AssertionError(f"condition not met within {timeout_seconds}s")


class TestSyntheticQuotaCycleMarkerSequence:
    """AC-E2-F5-S1-T1-1: every FR-2.10 marker the synthetic quota-then-recover
    cycle emits appears in ``logs/orchestrator.log`` at its specified point,
    in order, including the ``[QUOTA_POLLING]`` heartbeat reaching the root
    handler (spec AC-26)."""

    @pytest.mark.integration
    @pytest.mark.parametrize("reset_at_text", _QUOTA_SIGNAL_SHAPES)
    def test_full_marker_sequence_in_orchestrator_log(self, tmp_path: Path, reset_at_text: str | None) -> None:
        workspace_root, backlog_root, backlog_index = _build_workspace(tmp_path)
        log_file = tmp_path / "logs" / "orchestrator.log"
        mock_sdk, call_count = _install_fake_sdk(
            [
                [_build_quota_signal(reset_at_text)],
                [_terminal_message("ALL_DONE")],
            ]
        )
        probe_calls = {"n": 0}

        def fake_recovery_probe(*, timeout_seconds: float, request_size_tokens: int, source: str = "") -> bool:
            probe_calls["n"] += 1
            return True

        with (
            _quota_signal_clock_patch(reset_at_text),
            _reset_logging(log_file),
            _patched_orchestrator_environment(
                workspace_root=workspace_root,
                backlog_root=backlog_root,
                backlog_index=backlog_index,
                quota_cfg=_quota_handling_config(poll_interval_seconds=30),
                recovery_probe_fn=fake_recovery_probe,
                mock_sdk=mock_sdk,
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert call_count["n"] == 2, "expected exactly one fresh SDK session per side of the resume"
        assert probe_calls["n"] == 1, "probe should recover on the very first call"

        log_text = log_file.read_text(encoding="utf-8")

        def _marker_line_index(marker: str) -> int:
            for idx, line in enumerate(log_text.splitlines()):
                if marker in line:
                    return idx
            raise AssertionError(f"marker {marker!r} not found in orchestrator log:\n{log_text}")

        waiting_idx = _marker_line_index("[QUOTA_WAITING] reason=claude-code-cli reset_at=")
        polling_idx = _marker_line_index("[QUOTA_POLLING] elapsed=")
        resumed_idx = _marker_line_index("[QUOTA_RESUMED] waited_seconds=")
        orchestrator_resume_idx = _marker_line_index("[ORCHESTRATOR_QUOTA_RESUME] resume=1 max=")

        assert waiting_idx < polling_idx < resumed_idx < orchestrator_resume_idx, (
            "expected [QUOTA_WAITING] -> [QUOTA_POLLING] -> [QUOTA_RESUMED] -> "
            f"[ORCHESTRATOR_QUOTA_RESUME] in order; got line indices "
            f"{waiting_idx}, {polling_idx}, {resumed_idx}, {orchestrator_resume_idx}"
        )
        # When reset_at is known, _wait_toward_reset emits its own leading
        # probe=0 heartbeat while stepping toward the reset time, before the
        # probe loop's probe=1 heartbeat -- both satisfy "at least one
        # [QUOTA_POLLING] heartbeat reaches the root handler" (spec AC-26),
        # so search all lines between [QUOTA_WAITING] and [QUOTA_RESUMED]
        # for the probe=1 heartbeat rather than assuming it is the first one.
        probe_confirmed_lines = log_text.splitlines()[waiting_idx:resumed_idx]
        assert any("[QUOTA_POLLING]" in line and "probe=1" in line for line in probe_confirmed_lines), (
            f"expected a [QUOTA_POLLING] probe=1 heartbeat between [QUOTA_WAITING] and "
            f"[QUOTA_RESUMED]; got: {probe_confirmed_lines!r}"
        )
        _assert_reset_at_marker(log_text.splitlines()[waiting_idx], expect_known=reset_at_text is not None)


class TestJourneyJ1UnattendedOvernight:
    """AC-E2-F5-S1-T1-2: quota hits, the orchestrator waits, recovers, and
    completes unattended with rc=0 -- no operator action of any kind."""

    @pytest.mark.integration
    @pytest.mark.parametrize("reset_at_text", _QUOTA_SIGNAL_SHAPES)
    def test_unattended_overnight_completes_with_rc_zero(self, tmp_path: Path, reset_at_text: str | None) -> None:
        workspace_root, backlog_root, backlog_index = _build_workspace(tmp_path)
        log_file = tmp_path / "logs" / "orchestrator.log"
        mock_sdk, call_count = _install_fake_sdk(
            [
                [_build_quota_signal(reset_at_text)],
                [_terminal_message("NO_ACTIONABLE -- 1/1 done, 0 blocked")],
            ]
        )

        def fake_recovery_probe(*, timeout_seconds: float, request_size_tokens: int, source: str = "") -> bool:
            return True

        with (
            _quota_signal_clock_patch(reset_at_text),
            _reset_logging(log_file),
            _patched_orchestrator_environment(
                workspace_root=workspace_root,
                backlog_root=backlog_root,
                backlog_index=backlog_index,
                quota_cfg=_quota_handling_config(poll_interval_seconds=30),
                recovery_probe_fn=fake_recovery_probe,
                mock_sdk=mock_sdk,
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert call_count["n"] == 2, "the orchestrator must open a fresh second session after recovery (D-6)"

        checkpoint = load_checkpoint(workspace_root)
        assert checkpoint is None, "a completed cycle must not leave a stale pause checkpoint behind"

        log_text = log_file.read_text(encoding="utf-8")
        assert "[QUOTA_WAITING]" in log_text
        assert "[QUOTA_RESUMED]" in log_text
        assert "[ORCHESTRATOR_QUOTA_RESUME] resume=1" in log_text
        waiting_line = next(line for line in log_text.splitlines() if "[QUOTA_WAITING]" in line)
        _assert_reset_at_marker(waiting_line, expect_known=reset_at_text is not None)


class TestJourneyJ2OperatorInterrupts:
    """AC-E2-F5-S1-T1-3: a real SIGTERM delivered mid-wait stops the process
    promptly (proving no asyncio.shield, D-9) and the checkpoint survives on
    disk for a subsequent restart to pick up."""

    @pytest.mark.integration
    @pytest.mark.parametrize("reset_at_text", _QUOTA_SIGNAL_SHAPES)
    def test_sigterm_mid_wait_is_prompt_and_checkpoint_survives(
        self, tmp_path: Path, reset_at_text: str | None
    ) -> None:
        workspace_root, backlog_root, backlog_index = _build_workspace(tmp_path)
        log_file = tmp_path / "logs" / "orchestrator.log"
        mock_sdk, _call_count = _install_fake_sdk([[_build_quota_signal(reset_at_text)]])

        # The probe never confirms recovery on this journey -- the operator
        # interrupts before any recovery would occur, so wait_for_reset must
        # take the real asyncio.sleep() branch (poll_interval_seconds kept
        # small so the sleep window is short but still long enough for the
        # background thread below to detect the checkpoint and deliver the
        # signal well before the sleep would otherwise elapse).
        def fake_recovery_probe(*, timeout_seconds: float, request_size_tokens: int, source: str = "") -> bool:
            return False

        pid = os.getpid()
        sigterm_sent_at: dict[str, float] = {}

        def _send_sigterm_once_checkpoint_exists() -> None:
            _wait_for_condition(lambda: load_checkpoint(workspace_root) is not None)
            sigterm_sent_at["t"] = time.monotonic()
            os.kill(pid, signal.SIGTERM)

        interrupter = threading.Thread(target=_send_sigterm_once_checkpoint_exists, daemon=True)

        with (
            _quota_signal_clock_patch(reset_at_text),
            _reset_logging(log_file),
            _patched_orchestrator_environment(
                workspace_root=workspace_root,
                backlog_root=backlog_root,
                backlog_index=backlog_index,
                quota_cfg=_quota_handling_config(poll_interval_seconds=2, max_wait_seconds=18000),
                recovery_probe_fn=fake_recovery_probe,
                mock_sdk=mock_sdk,
            ),
        ):
            interrupter.start()
            start = time.monotonic()
            with pytest.raises(SystemExit) as excinfo:
                cli.cmd_start()
            caught_at = time.monotonic()

        interrupter.join(timeout=_READINESS_TIMEOUT_SECONDS)
        assert not interrupter.is_alive(), "interrupter thread failed to complete"

        assert excinfo.value.code == 0

        # Promptness: the wait must not have blocked for anything close to
        # the full poll interval once the signal was sent -- an
        # asyncio.shield-based implementation would defer the interrupt
        # until the shielded sleep completed instead of raising immediately.
        assert "t" in sigterm_sent_at, "background thread never observed the checkpoint"
        interrupt_latency = caught_at - sigterm_sent_at["t"]
        assert interrupt_latency < 1.0, f"SIGTERM took {interrupt_latency:.3f}s to interrupt the wait -- not prompt"
        assert (caught_at - start) < 10.0, "cmd_start took too long to raise SystemExit after SIGTERM"

        # Durability (D-6/D-9): the checkpoint written before the wait began
        # is still on disk after the process death, proving durability (not
        # shielding) is what survives the interrupt.
        checkpoint = load_checkpoint(workspace_root)
        assert checkpoint is not None, "checkpoint must survive a SIGTERM delivered mid-wait"
        assert checkpoint.reason == "claude-code-cli"
        if reset_at_text is None:
            assert checkpoint.reset_at is None
        else:
            assert checkpoint.reset_at is not None

        # The SIGTERM handler also force-blocks the in-flight WU -- expected
        # behaviour of the handler's own contract, not a bug in this journey.
        wu_content = (backlog_root / f"{_UNIT_ID}.md").read_text(encoding="utf-8")
        assert "## Status: blocked" in wu_content
        assert "[FORCED_BLOCKED_ON_STOP]" in wu_content


class TestJourneyJ3OperatorInspects:
    """AC-E2-F5-S1-T1-4: while a quota pause is in progress, ``quota-watcher``
    reports rc=0 with the reset time and ``status`` still shows the paused
    work unit as active."""

    @pytest.mark.integration
    @pytest.mark.parametrize("reset_at_text", _QUOTA_SIGNAL_SHAPES)
    def test_watcher_and_status_reflect_the_pause(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], reset_at_text: str | None
    ) -> None:
        workspace_root, backlog_root, backlog_index = _build_workspace(tmp_path)
        log_file = tmp_path / "logs" / "orchestrator.log"
        mock_sdk, call_count = _install_fake_sdk(
            [
                [_build_quota_signal(reset_at_text)],
                [_terminal_message("ALL_DONE")],
            ]
        )

        # The probe blocks synchronously until the inspecting thread has
        # captured its snapshot, then releases -- this fully removes any
        # timing race between "pause is visible" and "probe recovers"
        # instead of relying on a fixed sleep window.
        release_probe = threading.Event()
        inspection_done = threading.Event()
        captured: dict[str, str] = {}

        def fake_recovery_probe(*, timeout_seconds: float, request_size_tokens: int, source: str = "") -> bool:
            release_probe.wait(timeout=_READINESS_TIMEOUT_SECONDS)
            return True

        def _inspect_once_checkpoint_exists() -> None:
            # WORKSPACE_ROOT / BACKLOG_ROOT / BACKLOG_INDEX are already
            # patched process-wide by the enclosing
            # _patched_orchestrator_environment context (module globals are
            # shared across threads), so no additional patching is needed
            # here.
            #
            # DEVBENCH_SESSION_NAME, however, must be cleared for the
            # duration of this inspection. In real usage ``devbench status``
            # / ``devbench quota-watcher`` run as a SEPARATE OS process from
            # the running ``devbench start`` and never inherit its
            # temporarily-mutated env var. Running in-process (as this test
            # does, to observe the pause synchronously) would otherwise leak
            # that mutation onto the same interpreter's os.environ, routing
            # the scope-banner read at a per-session scope.json path that
            # ``_write_session_state_files`` already owns for an unrelated
            # purpose (the session's own scope-id record) -- an artifact of
            # in-process testing, not a real operator-facing collision.
            _wait_for_condition(lambda: load_checkpoint(workspace_root) is not None)
            prev_session_name = os.environ.pop("DEVBENCH_SESSION_NAME", None)
            try:
                watcher_rc = cli.cmd_quota_watcher()
                captured["watcher_stdout"] = capsys.readouterr().out
                captured["watcher_rc"] = str(watcher_rc)
                status_rc = cli.cmd_status()
                captured["status_stdout"] = capsys.readouterr().out
                captured["status_rc"] = str(status_rc)
            finally:
                if prev_session_name is not None:
                    os.environ["DEVBENCH_SESSION_NAME"] = prev_session_name
            inspection_done.set()
            release_probe.set()

        inspector = threading.Thread(target=_inspect_once_checkpoint_exists, daemon=True)

        with (
            _quota_signal_clock_patch(reset_at_text),
            _reset_logging(log_file),
            _patched_orchestrator_environment(
                workspace_root=workspace_root,
                backlog_root=backlog_root,
                backlog_index=backlog_index,
                quota_cfg=_quota_handling_config(poll_interval_seconds=30),
                recovery_probe_fn=fake_recovery_probe,
                mock_sdk=mock_sdk,
            ),
        ):
            inspector.start()
            rc = cli.cmd_start()

        inspector.join(timeout=_READINESS_TIMEOUT_SECONDS)
        assert inspection_done.is_set(), "inspector thread never completed its snapshot"
        assert rc == 0
        assert call_count["n"] == 2

        assert captured["watcher_rc"] == "0"
        assert "[QUOTA_WAITING] reason=claude-code-cli reset_at=" in captured["watcher_stdout"]
        _assert_reset_at_marker(captured["watcher_stdout"], expect_known=reset_at_text is not None)

        assert captured["status_rc"] == "0"
        assert "Active work units:" in captured["status_stdout"]
        assert _UNIT_ID in captured["status_stdout"]
