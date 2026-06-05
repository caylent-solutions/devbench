"""Regression test for zero asyncio teardown errors (issue #231).

Verifies that under a cleanly-closing fake SDK a full orchestrate run via
``cmd_start`` produces zero asyncio error records matching the pattern
``Task exception was never retrieved``.

The capture uses ``loop.set_exception_handler`` wired through a custom
``loop_factory`` injected into ``asyncio.run``.  Combined with ``caplog``
at ERROR level this ensures any rogue unhandled-exception callbacks are
surfaced and asserted absent (AC-231-1, AC-231a-1).

Reference: issue #231 (closed by PR #255).
"""

from __future__ import annotations

import asyncio
import re
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Pattern the asyncio loop logs when a Task's exception is never retrieved.
# The removal of the sdk_teardown_filter workaround (PR #255) means this
# pattern must NOT appear under a cleanly-closing fake SDK.
# ---------------------------------------------------------------------------
_TEARDOWN_PATTERN = re.compile(r"Task exception was never retrieved", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Fake SDK factory
# ---------------------------------------------------------------------------


def _make_clean_sdk(messages: list[object]) -> types.ModuleType:
    """Return a duck-typed fake ``claude_agent_sdk`` module.

    The ``query`` async generator yields each object in ``messages`` and then
    returns normally -- no exception on close, no lingering background tasks.
    This is the clean-teardown contract that PR #255 relies on.

    Args:
        messages: Sequence of objects the fake ``query`` async generator
            yields before returning.

    Returns:
        A module-like object whose ``query`` attribute is a cleanly-closing
        async generator and whose ``ClaudeAgentOptions`` is a no-op class.
    """
    fake_sdk: types.ModuleType = types.ModuleType("claude_agent_sdk")
    sdk_any: Any = fake_sdk

    class _ClaudeAgentOptions:
        def __init__(self, **kwargs: object) -> None:
            pass

    sdk_any.ClaudeAgentOptions = _ClaudeAgentOptions

    async def _clean_query(**kwargs: object) -> Any:
        for msg in messages:
            yield msg

    sdk_any.query = _clean_query
    return fake_sdk


# ---------------------------------------------------------------------------
# Loop factory that wires the exception handler before the run starts
# ---------------------------------------------------------------------------


def _make_exception_recording_loop(
    captured: list[dict[str, Any]],
) -> asyncio.AbstractEventLoop:
    """Return a new asyncio event loop with a recording exception handler.

    All exceptions routed through ``loop.call_exception_handler`` are
    appended to ``captured``.  The default asyncio handler also fires so
    that standard debug output is not suppressed.

    Args:
        captured: Mutable list to which each exception-context dict is
            appended.

    Returns:
        A configured ``asyncio.DefaultEventLoopPolicy``-compatible loop.
    """
    loop = asyncio.new_event_loop()
    default_handler = loop.get_exception_handler()

    def _recording_handler(
        loop_ref: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        captured.append(dict(context))
        if default_handler is not None:
            default_handler(loop_ref, context)
        else:
            loop_ref.default_exception_handler(context)

    loop.set_exception_handler(_recording_handler)
    return loop


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


@pytest.mark.functional
class TestNoAsyncioTeardownErrors:
    """AC-231-1 / AC-231a-1: zero teardown-pattern records under a clean SDK."""

    def test_zero_teardown_records_on_clean_sdk_close(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A full orchestrate run under a cleanly-closing fake SDK produces
        zero asyncio error records matching ``Task exception was never
        retrieved`` (spec AC-231-1).

        Capture uses ``loop.set_exception_handler`` injected via
        ``loop_factory`` into ``asyncio.run``, plus ``caplog`` at ERROR so
        any asyncio-level unhandled-exception callbacks are surfaced
        (spec Section 4 E1.F1.S2, AC-231a-1).
        """
        from devbench import cli

        exception_contexts: list[dict[str, Any]] = []

        original_asyncio_run = asyncio.run

        def _patched_asyncio_run(
            coro: Any,
            *,
            debug: Any = None,
            loop_factory: Any = None,
        ) -> Any:
            """Wrap ``asyncio.run`` to inject the recording loop factory.

            Any caller-supplied ``loop_factory`` is ignored so that OUR
            recording factory is always installed; the production path in
            ``cmd_start`` does not pass one, so this only affects tests that
            do.
            """

            def recording_factory() -> asyncio.AbstractEventLoop:
                return _make_exception_recording_loop(exception_contexts)

            return original_asyncio_run(
                coro,
                debug=debug,
                loop_factory=recording_factory,
            )

        fake_sdk = _make_clean_sdk(["orchestration complete"])

        with (
            caplog.at_level("ERROR"),
            patch.dict(sys.modules, {"claude_agent_sdk": fake_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("asyncio.run", _patched_asyncio_run),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            result = cli.cmd_start()

        assert result == 0, f"cmd_start returned non-zero exit code: {result}"

        # Check the recording exception handler caught nothing matching the pattern.
        teardown_contexts = [ctx for ctx in exception_contexts if _TEARDOWN_PATTERN.search(str(ctx.get("message", "")))]
        assert teardown_contexts == [], (
            f"Expected zero asyncio 'Task exception was never retrieved' records "
            f"but got {len(teardown_contexts)}: {teardown_contexts!r}. "
            "This indicates the PR #255 teardown-workaround removal introduced a "
            "regression -- a background task's exception is not being retrieved "
            "before the event loop is torn down."
        )

        # Also assert that caplog at ERROR captured no matching records.
        teardown_log_records = [
            record
            for record in caplog.records
            if record.levelno >= 40  # logging.ERROR
            and _TEARDOWN_PATTERN.search(record.getMessage())
        ]
        assert teardown_log_records == [], (
            f"Expected zero ERROR-level log records matching the teardown pattern "
            f"but got {len(teardown_log_records)}: {teardown_log_records!r}."
        )
