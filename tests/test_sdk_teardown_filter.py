"""Tests for ``devbench.sdk_teardown_filter`` (issue #232).

The filter intercepts the known ``RuntimeError: Attempted to exit cancel
scope in a different task than it was entered in`` raised by
``claude_agent_sdk._internal.query.Query.close()`` during session
teardown, downgrading it from an asyncio ``ERROR Task exception was
never retrieved`` to a single WARNING line on the ``devbench.sdk``
logger that links to the upstream tracking issue.

Tests cover:

- The match is narrow: only ``RuntimeError`` with the exact substring
  AND a traceback that walks through ``claude_agent_sdk/_internal/*``.
- Non-matching exceptions delegate to the previously-installed handler.
- ``install`` + ``uninstall`` round-trip restores the original handler.
- The ``guard`` async context manager installs on enter and restores
  on exit (including via exception).
- Full traceback is logged at DEBUG when the WARNING fires.
"""

from __future__ import annotations

import asyncio
import logging
import types
from typing import Any

import pytest

from devbench import sdk_teardown_filter
from devbench.sdk_teardown_filter import (
    SDKTeardownExceptionHandler,
    guard,
    install,
    uninstall,
)


def _raise_with_filename(filename: str) -> None:
    """Raise a ``RuntimeError`` whose traceback frame's ``co_filename`` is ``filename``.

    Uses ``CodeType.replace(co_filename=...)`` (Python 3.8+) to rewrite
    the raiser function's code object so its frame reports the desired
    filename. Avoids ``exec`` (bandit S102) and avoids on-disk fixture
    files.
    """

    def _inner() -> None:
        raise RuntimeError("marker")

    _inner.__code__ = _inner.__code__.replace(co_filename=filename)
    _inner()


def _build_traceback_through(filenames: list[str]) -> types.TracebackType | None:
    """Build a fake ``TracebackType`` walking through the named filenames.

    Constructs a chain of traceback objects by raising
    ``RuntimeError("marker")`` from a helper whose ``co_filename`` has
    been rewritten per ``filenames`` and linking the captured tracebacks
    head-to-tail in input order. The first entry in ``filenames``
    becomes the outermost frame; the last entry becomes the innermost.
    """
    tb: types.TracebackType | None = None
    for filename in reversed(filenames):
        try:
            _raise_with_filename(filename)
        except RuntimeError as exc:
            new_tb = exc.__traceback__
            if tb is not None and new_tb is not None:
                walker = new_tb
                while walker.tb_next is not None:
                    walker = walker.tb_next
                walker.tb_next = tb
            tb = new_tb
    return tb


def _make_matching_exception() -> RuntimeError:
    """Return a RuntimeError whose message + traceback match the SDK signature."""
    tb = _build_traceback_through(
        [
            "/site-packages/anyio/_backends/_asyncio.py",
            "/site-packages/claude_agent_sdk/_internal/query.py",
            "/site-packages/claude_agent_sdk/_internal/client.py",
        ]
    )
    exc = RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")
    exc.__traceback__ = tb
    return exc


def _make_non_matching_runtime_error(message: str = "different message") -> RuntimeError:
    """Return a RuntimeError that should NOT match (wrong message)."""
    tb = _build_traceback_through(
        [
            "/site-packages/claude_agent_sdk/_internal/query.py",
        ]
    )
    exc = RuntimeError(message)
    exc.__traceback__ = tb
    return exc


def _make_matching_message_but_no_sdk_frame() -> RuntimeError:
    """Matching message but traceback does not walk through the SDK."""
    tb = _build_traceback_through(
        [
            "/some/unrelated/library.py",
            "/anyio/_backends/_asyncio.py",
        ]
    )
    exc = RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")
    exc.__traceback__ = tb
    return exc


@pytest.mark.unit
class TestMatching:
    """The narrow match logic: must satisfy all three conditions."""

    def test_matching_runtime_error_matches(self) -> None:
        exc = _make_matching_exception()
        assert SDKTeardownExceptionHandler._matches(exc) is True

    def test_value_error_does_not_match(self) -> None:
        exc = ValueError("Attempted to exit cancel scope in a different task than it was entered in")
        # Set a traceback so the second/third conditions don't short-circuit before the class check.
        tb = _build_traceback_through(["/site-packages/claude_agent_sdk/_internal/query.py"])
        exc.__traceback__ = tb
        assert SDKTeardownExceptionHandler._matches(exc) is False

    def test_wrong_message_does_not_match(self) -> None:
        exc = _make_non_matching_runtime_error("something else entirely")
        assert SDKTeardownExceptionHandler._matches(exc) is False

    def test_matching_message_no_sdk_frame_does_not_match(self) -> None:
        exc = _make_matching_message_but_no_sdk_frame()
        assert SDKTeardownExceptionHandler._matches(exc) is False

    def test_none_does_not_match(self) -> None:
        assert SDKTeardownExceptionHandler._matches(None) is False


@pytest.mark.unit
class TestHandlerCallable:
    """The handler dispatches to log-warning or to the previous handler."""

    def test_matching_exception_logs_warning_not_error(self, caplog: pytest.LogCaptureFixture) -> None:
        previous_calls: list[dict[str, Any]] = []

        def previous(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            previous_calls.append(context)

        handler = SDKTeardownExceptionHandler(previous)
        loop = asyncio.new_event_loop()
        try:
            with caplog.at_level(logging.WARNING, logger="devbench.sdk"):
                handler(loop, {"exception": _make_matching_exception(), "message": "ignored"})
            # WARNING (not ERROR) emitted on devbench.sdk.
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "devbench.sdk"]
            assert len(warnings) == 1
            assert "claude-agent-sdk Query.close() raised a known teardown race" in warnings[0].getMessage()
            assert "https://github.com/caylent-solutions/devbench/issues/231" in warnings[0].getMessage()
            # Previous handler NOT called -- matched cases short-circuit.
            assert previous_calls == []
            # No ERROR records emitted by this handler.
            errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
            assert errors == []
        finally:
            loop.close()

    def test_non_matching_exception_delegates_to_previous(self) -> None:
        previous_calls: list[dict[str, Any]] = []

        def previous(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            previous_calls.append(context)

        handler = SDKTeardownExceptionHandler(previous)
        loop = asyncio.new_event_loop()
        try:
            context = {"exception": _make_non_matching_runtime_error()}
            handler(loop, context)
            assert previous_calls == [context]
        finally:
            loop.close()

    def test_value_error_delegates_to_previous(self) -> None:
        previous_calls: list[dict[str, Any]] = []

        def previous(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            previous_calls.append(context)

        handler = SDKTeardownExceptionHandler(previous)
        loop = asyncio.new_event_loop()
        try:
            context = {"exception": ValueError("any")}
            handler(loop, context)
            assert previous_calls == [context]
        finally:
            loop.close()

    def test_none_previous_falls_back_to_default_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handler = SDKTeardownExceptionHandler(None)
        loop = asyncio.new_event_loop()
        defaults: list[dict[str, Any]] = []
        # monkeypatch the loop's default_exception_handler so we can
        # observe the delegation. monkeypatch.setattr handles bound-method
        # restoration on test teardown and satisfies mypy's
        # method-assign check.
        monkeypatch.setattr(loop, "default_exception_handler", defaults.append)
        try:
            context = {"exception": _make_non_matching_runtime_error()}
            handler(loop, context)
            assert defaults == [context]
        finally:
            loop.close()

    def test_full_traceback_logged_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        handler = SDKTeardownExceptionHandler(None)
        loop = asyncio.new_event_loop()
        try:
            with caplog.at_level(logging.DEBUG, logger="devbench.sdk"):
                handler(loop, {"exception": _make_matching_exception()})
            debugs = [r for r in caplog.records if r.levelno == logging.DEBUG and r.name == "devbench.sdk"]
            assert len(debugs) == 1
            assert "SDK teardown race full traceback" in debugs[0].getMessage()
            assert "RuntimeError" in debugs[0].getMessage()
        finally:
            loop.close()


@pytest.mark.unit
class TestInstallUninstall:
    """install/uninstall round-trips the loop's exception handler."""

    def test_install_returns_handler_with_previous_recorded(self) -> None:
        loop = asyncio.new_event_loop()

        def sentinel(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            pass

        try:
            loop.set_exception_handler(sentinel)
            handler = install(loop)
            assert isinstance(handler, SDKTeardownExceptionHandler)
            assert handler.previous is sentinel
            # The loop's handler is now our handler.
            assert loop.get_exception_handler() is handler
        finally:
            loop.close()

    def test_uninstall_restores_previous_handler(self) -> None:
        loop = asyncio.new_event_loop()

        def sentinel(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            pass

        try:
            loop.set_exception_handler(sentinel)
            handler = install(loop)
            uninstall(loop, handler)
            assert loop.get_exception_handler() is sentinel
        finally:
            loop.close()

    def test_install_with_no_previous_uninstalls_to_none(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            handler = install(loop)
            assert handler.previous is None
            uninstall(loop, handler)
            assert loop.get_exception_handler() is None
        finally:
            loop.close()


@pytest.mark.unit
class TestGuardContextManager:
    """The async context manager installs on enter and restores on exit."""

    def test_guard_installs_and_restores_on_clean_exit(self) -> None:
        async def run() -> tuple[Any, Any, Any]:
            loop = asyncio.get_event_loop()
            before = loop.get_exception_handler()
            async with guard() as handler:
                during = loop.get_exception_handler()
                assert during is handler
            after = loop.get_exception_handler()
            return before, during, after

        before, during, after = asyncio.run(run())
        assert before is None
        assert isinstance(during, SDKTeardownExceptionHandler)
        assert after is None

    def test_guard_restores_on_exception(self) -> None:
        async def run() -> Any:
            loop = asyncio.get_event_loop()
            before = loop.get_exception_handler()
            try:
                async with guard():
                    raise ValueError("inside guard")
            except ValueError:
                pass
            after = loop.get_exception_handler()
            return before, after

        before, after = asyncio.run(run())
        assert before is None
        assert after is None

    def test_guard_chains_previous_handler(self) -> None:
        async def run() -> Any:
            loop = asyncio.get_event_loop()

            def sentinel(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
                pass

            loop.set_exception_handler(sentinel)
            async with guard() as handler:
                assert handler.previous is sentinel
            return loop.get_exception_handler()

        restored = asyncio.run(run())
        assert callable(restored)


@pytest.mark.unit
class TestModuleConstants:
    """The module exports the tracking-issue URL and marker substring."""

    def test_tracking_issue_url_targets_devbench_231(self) -> None:
        assert sdk_teardown_filter._TRACKING_ISSUE_URL == ("https://github.com/caylent-solutions/devbench/issues/231")

    def test_teardown_marker_matches_anyio_message(self) -> None:
        # The marker substring must be a verbatim slice of the upstream
        # error message; spelling drift breaks the match logic.
        assert (
            sdk_teardown_filter._TEARDOWN_MARKER
            == "Attempted to exit cancel scope in a different task than it was entered in"
        )

    def test_sdk_frame_markers_cover_internal_modules(self) -> None:
        # Both internal modules where the upstream stack walks are listed.
        assert "claude_agent_sdk/_internal/query" in sdk_teardown_filter._SDK_FRAME_MARKERS
        assert "claude_agent_sdk/_internal/client" in sdk_teardown_filter._SDK_FRAME_MARKERS
