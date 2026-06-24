"""Tests confirming sdk_teardown_filter workaround has been removed (issue #255).

These tests verify:

- ``sdk_teardown_filter`` is not imported anywhere in cli.py (AC-255-1).
- ``_sdk_teardown_guard`` is not referenced anywhere in cli.py (AC-255a-1).
- The orchestrate loop in cmd_start runs without the guard wrap: the mock
  SDK query is driven to completion with no intervention from the teardown
  filter (AC-255a-1).
- ``src/devbench/sdk_teardown_filter.py`` does not exist on the filesystem
  (AC-255-1).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli


def _make_mock_sdk(messages: list[object]) -> types.ModuleType:
    """Return a minimal fake claude_agent_sdk module.

    The fake exposes the surface ``cmd_start._run`` depends on: a
    ``ClaudeAgentOptions`` placeholder, a module-level ``query`` async
    generator (retained for any direct reference), and a ``ClaudeSDKClient``
    streaming client.  ``ClaudeSDKClient`` is an async context manager whose
    ``receive_response()`` async-yields the test's fake messages and whose
    ``query()`` is a no-op, matching how ``_run`` drives the live SDK::

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                ...

    Args:
        messages: Sequence of objects the fake ``receive_response`` async
            generator yields on each call.

    Returns:
        A module-like object with ``ClaudeAgentOptions``, ``query``, and
        ``ClaudeSDKClient`` attributes suitable for injection via
        ``sys.modules``.
    """
    mock_sdk: types.ModuleType = types.ModuleType("claude_agent_sdk")
    sdk_any: Any = mock_sdk
    sdk_any.ClaudeAgentOptions = MagicMock()

    async def mock_query(**kwargs: object) -> Any:
        for msg in messages:
            yield msg

    sdk_any.query = mock_query

    class FakeClaudeSDKClient:
        """Async-context-manager double for ``claude_agent_sdk.ClaudeSDKClient``.

        Yields the captured *messages* from ``receive_response`` and treats
        ``query`` as a no-op so the orchestrate loop in ``cmd_start`` can be
        driven to completion under test.
        """

        def __init__(self, options: object | None = None) -> None:
            self.options = options

        async def __aenter__(self) -> FakeClaudeSDKClient:
            return self

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

        async def query(self, *args: object, **kwargs: object) -> None:
            return None

        async def receive_response(self) -> Any:
            for msg in messages:
                yield msg

    sdk_any.ClaudeSDKClient = FakeClaudeSDKClient
    return mock_sdk


@pytest.mark.unit
class TestSdkTeardownFilterNotPresent:
    """The sdk_teardown_filter module and all references to it are gone."""

    def test_sdk_teardown_filter_module_file_does_not_exist(self) -> None:
        """AC-255-1: src/devbench/sdk_teardown_filter.py must not exist."""
        repo_root = Path(__file__).parent.parent
        module_path = repo_root / "src" / "devbench" / "sdk_teardown_filter.py"
        assert not module_path.exists(), (
            f"sdk_teardown_filter.py still exists at {module_path}; "
            "it must be deleted as part of the SDK upgrade (issue #255)."
        )

    def test_sdk_teardown_filter_not_importable(self) -> None:
        """AC-255-1: importing devbench.sdk_teardown_filter must raise ModuleNotFoundError."""
        sys.modules.pop("devbench.sdk_teardown_filter", None)
        spec = importlib.util.find_spec("devbench.sdk_teardown_filter")
        assert spec is None, (
            "devbench.sdk_teardown_filter is still findable by the import system; the module file must be deleted."
        )

    def test_cli_source_has_no_sdk_teardown_filter_import(self) -> None:
        """AC-255-1: cli.py must contain no reference to sdk_teardown_filter."""
        cli_source = Path(cli.__file__)
        content = cli_source.read_text(encoding="utf-8")
        assert "sdk_teardown_filter" not in content, (
            "cli.py still references sdk_teardown_filter; "
            "the import at cli.py:6511 must be removed (issue #255, AC-255-1)."
        )

    def test_cli_source_has_no_sdk_teardown_guard_reference(self) -> None:
        """AC-255a-1: cli.py must contain no reference to _sdk_teardown_guard."""
        cli_source = Path(cli.__file__)
        content = cli_source.read_text(encoding="utf-8")
        assert "_sdk_teardown_guard" not in content, (
            "cli.py still references _sdk_teardown_guard; "
            "the async-with guard wrap must be removed (issue #255, AC-255a-1)."
        )


@pytest.mark.unit
class TestOrchestateLoopRunsWithoutGuard:
    """The orchestrate loop in cmd_start drives the SDK query directly."""

    def _make_mock_sdk(self, messages: list[object]) -> types.ModuleType:
        """Return a minimal fake claude_agent_sdk module.

        The fake exposes the surface ``cmd_start._run`` depends on: a
        ``ClaudeAgentOptions`` placeholder, a module-level ``query`` async
        generator (retained for any direct reference), and a
        ``ClaudeSDKClient`` streaming client.  ``ClaudeSDKClient`` is an async
        context manager whose ``receive_response()`` async-yields the test's
        fake messages and whose ``query()`` is a no-op, matching how
        ``_run`` drives the live SDK (``async with ClaudeSDKClient(...) as
        client: await client.query(...); ... client.receive_response()``).

        Args:
            messages: Sequence of objects the fake ``receive_response`` async
                generator yields on each call.

        Returns:
            A module-like object with ``ClaudeAgentOptions``, ``query``, and
            ``ClaudeSDKClient`` attributes suitable for injection via
            ``sys.modules``.
        """
        return _make_mock_sdk(messages)

    @pytest.mark.unit
    def test_cmd_start_succeeds_without_teardown_guard(self, tmp_path: Path) -> None:
        """AC-255a-1: cmd_start drives the SDK loop to completion without the guard wrap."""
        mock_sdk = self._make_mock_sdk(["result message"])
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            result = cli.cmd_start()

        assert result == 0

    @pytest.mark.unit
    def test_cmd_start_processes_all_messages_without_guard(self, tmp_path: Path) -> None:
        """AC-255a-1: every message yielded by the mock SDK is processed when no guard is present."""
        processed_messages: list[object] = []
        original_extract = cli._extract_sdk_result_text

        def capturing_extract(message: object) -> str | None:
            processed_messages.append(message)
            return original_extract(message)

        mock_sdk = self._make_mock_sdk(["msg1", "msg2", "msg3"])
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._extract_sdk_result_text", side_effect=capturing_extract),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            result = cli.cmd_start()

        assert result == 0
        assert processed_messages == ["msg1", "msg2", "msg3"]
