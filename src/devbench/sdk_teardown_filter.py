"""Narrow asyncio-loop exception handler for the known SDK teardown race (#232).

``claude_agent_sdk._internal.query.Query.close()`` raises
``RuntimeError: Attempted to exit cancel scope in a different task than
it was entered in`` during session teardown -- AFTER the final
``ResultMessage`` has been yielded and the orchestration outcome is
already established. The exception is raised in a background task
(asyncio.Task) that the consumer never retrieves, so Python's asyncio
default exception handler logs it as
``[asyncio] ERROR Task exception was never retrieved`` followed by the
full traceback.

Devbench's consumer (cmd_start in cli.py) cannot catch this via a
normal ``try / except`` around the ``async for`` loop -- the exception
fires on a different task. The asyncio-loop exception-handler API
(``loop.set_exception_handler``) is the only intercept point.

This module installs a narrow filter that:

1. Inspects the asyncio context dict.
2. If the exception is a ``RuntimeError`` whose message contains the
   exact known marker substring AND the traceback walks through a
   ``claude_agent_sdk/_internal/`` frame, the filter logs ONE WARNING
   line on the ``devbench.sdk`` logger naming the upstream-tracking
   issue (caylent-solutions/devbench#231) and the full traceback at
   DEBUG so the diagnostic is preserved.
3. Otherwise, delegates to the previously-installed handler so
   unrelated asyncio defects still surface at ERROR.

The match is intentionally narrow (per CLAUDE.md "Fail-Fast
Philosophy"): a future SDK release that changes the message string or
the module layout will fall through to the default handler and
surface as ERROR -- which is correct (it would mean a different,
unknown SDK defect).

Tracking issue (upstream + downstream):

- caylent-solutions/devbench#231 -- stays open until upstream fixes.
- caylent-solutions/devbench#232 -- this workaround; closes when this
  module lands on ``feat/issues-188-193``.
- anthropics/claude-agent-sdk-python#983 -- upstream bug.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

_LOGGER = logging.getLogger("devbench.sdk")

# Exact substring from anyio's cancel-scope guard. Matches verbatim
# against ``str(exception)``. A future anyio / SDK release that changes
# the message text will fall through to the default handler.
_TEARDOWN_MARKER = "Attempted to exit cancel scope in a different task than it was entered in"

# Module-name fragments the traceback must walk through for the match
# to apply. Either fragment is sufficient: the upstream stack hits both
# (client.py during ``process_query``, query.py during ``Query.close``),
# but we accept either in case the SDK refactors one of them.
_SDK_FRAME_MARKERS: tuple[str, ...] = (
    "claude_agent_sdk/_internal/query",
    "claude_agent_sdk/_internal/client",
)

# Operator-facing URL the WARNING line links to so an operator can
# check upstream resolution status without grepping the source tree.
_TRACKING_ISSUE_URL = "https://github.com/caylent-solutions/devbench/issues/231"


class SDKTeardownExceptionHandler:
    """Asyncio exception handler that downgrades the known SDK teardown error.

    Holds a reference to the previously-installed handler so non-matching
    exceptions delegate. Callers MUST install and uninstall via
    :func:`install` and :func:`uninstall` so the previous handler is
    restored on teardown.
    """

    def __init__(self, previous: Any) -> None:
        self.previous = previous

    def __call__(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exception = context.get("exception")
        if self._matches(exception):
            self._log_warning(exception)
            return
        # Non-matching exception: delegate to the previous handler. When
        # there was no previous handler (None), asyncio's documented
        # behaviour is to call ``loop.default_exception_handler``.
        if self.previous is None:
            loop.default_exception_handler(context)
            return
        self.previous(loop, context)

    @staticmethod
    def _matches(exception: BaseException | None) -> bool:
        """Return ``True`` when ``exception`` is the known SDK teardown race."""
        if not isinstance(exception, RuntimeError):
            return False
        if _TEARDOWN_MARKER not in str(exception):
            return False
        return SDKTeardownExceptionHandler._traceback_walks_through_sdk(exception.__traceback__)

    @staticmethod
    def _traceback_walks_through_sdk(tb: TracebackType | None) -> bool:
        """Return ``True`` when any frame in ``tb`` is from the SDK internals."""
        frame = tb
        while frame is not None:
            filename = frame.tb_frame.f_code.co_filename
            for marker in _SDK_FRAME_MARKERS:
                if marker in filename:
                    return True
            frame = frame.tb_next
        return False

    @staticmethod
    def _log_warning(exception: BaseException | None) -> None:
        """Emit the operator-facing WARNING + DEBUG traceback for the match."""
        exc_repr = repr(exception)
        _LOGGER.warning(
            "claude-agent-sdk Query.close() raised a known teardown race (%s). "
            "Orchestration outcome is unaffected. "
            "This will be resolved once the upstream SDK ships a fix; see %s for "
            "the tracking issue and the upstream cross-reference.",
            exc_repr,
            _TRACKING_ISSUE_URL,
        )
        if exception is not None:
            _LOGGER.debug(
                "SDK teardown race full traceback:\n%s",
                "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)),
            )


def install(loop: asyncio.AbstractEventLoop) -> SDKTeardownExceptionHandler:
    """Install the SDK-teardown filter on ``loop`` and return the handler.

    Stores the previously-installed handler (which may be ``None``) on
    the returned handler so :func:`uninstall` can restore it. Callers
    MUST pair every ``install`` with an ``uninstall`` (typically in a
    ``finally`` block) so subsequent invocations of the same code path
    start with a clean event loop.
    """
    previous = loop.get_exception_handler()
    handler = SDKTeardownExceptionHandler(previous)
    loop.set_exception_handler(handler)
    return handler


def uninstall(loop: asyncio.AbstractEventLoop, handler: SDKTeardownExceptionHandler) -> None:
    """Restore the previous asyncio exception handler on ``loop``.

    Idempotent: calling ``uninstall`` twice with the same handler is a
    no-op on the second call (the loop's handler will already be the
    previous handler the first call restored).
    """
    loop.set_exception_handler(handler.previous)


@contextlib.asynccontextmanager
async def guard() -> AsyncIterator[SDKTeardownExceptionHandler]:
    """Async context manager that installs the filter for the scope of a block.

    Usage::

        async with sdk_teardown_filter.guard():
            async for message in query(...):
                ...

    The filter is installed on entry and uninstalled on exit, including
    when the block exits via an exception or via ``return``. Single
    ``async with`` keeps the caller's cyclomatic complexity flat (no
    extra ``try / finally`` branches on the caller's side).
    """
    loop = asyncio.get_event_loop()
    handler = install(loop)
    try:
        yield handler
    finally:
        uninstall(loop, handler)
