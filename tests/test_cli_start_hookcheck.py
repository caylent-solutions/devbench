"""Tests for cmd_start guard-hook self-check (spec AC-H4-1, AC-H4-2, E8.F4.S1).

Covers:
- AC-H4-1: start without guard hooks exits 1 with the verbatim error message;
  with them loaded, start proceeds (rc 0).
- AC-H4-2: mark_done gate + force_status done-refusal enforce even with hooks
  disabled (library-level enforcement, independent of hook state).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.manager import BacklogManager

#: Verbatim fail-closed message required by spec AC-H4-1.
_VERBATIM_ERROR: str = (
    "ERROR: devbench guard hooks not loaded; refusing to run"
    " (done-integrity cannot be enforced)."
    " Launch via the devbench-orchestrate plugin."
)


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


# ---------------------------------------------------------------------------
# AC-H4-1: hook presence/absence gate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartHookCheck:
    """AC-H4-1: cmd_start fails closed when guard hooks are absent."""

    def test_start_without_hooks_exits_1(self, tmp_path: Path, capsys: Any) -> None:
        """cmd_start returns rc 1 when the plugin has no hooks.json."""
        plugin_path = tmp_path / "plugin-no-hooks"
        plugin_path.mkdir()
        # No hooks/ directory or hooks.json -- hooks are absent.
        # Patch the SDK import so the test does not hang if the guard check
        # is inadvertently bypassed during development.
        mock_sdk = _make_mock_sdk([])
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli._resolve_plugin_path", return_value=plugin_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_start()
        assert result == 1, f"expected rc 1 when hooks absent, got {result}"

    def test_start_without_hooks_prints_verbatim_message(self, tmp_path: Path, capsys: Any) -> None:
        """cmd_start prints the verbatim fail-closed message to stderr when hooks absent."""
        plugin_path = tmp_path / "plugin-no-hooks"
        plugin_path.mkdir()
        mock_sdk = _make_mock_sdk([])
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli._resolve_plugin_path", return_value=plugin_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_start()
        captured = capsys.readouterr()
        assert _VERBATIM_ERROR in captured.err, (
            f"verbatim error message not found in stderr.\n"
            f"Expected substring: {_VERBATIM_ERROR!r}\n"
            f"Got stderr: {captured.err!r}"
        )

    def test_start_with_empty_hooks_dir_exits_1(self, tmp_path: Path, capsys: Any) -> None:
        """cmd_start returns rc 1 when the hooks/ directory exists but hooks.json is absent."""
        plugin_path = tmp_path / "plugin-empty-hooks"
        plugin_path.mkdir()
        (plugin_path / "hooks").mkdir()
        # hooks/ directory exists but no hooks.json inside it.
        mock_sdk = _make_mock_sdk([])
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli._resolve_plugin_path", return_value=plugin_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_start()
        assert result == 1

    def test_start_with_hooks_proceeds(self, tmp_path: Path) -> None:
        """AC-H4-1: cmd_start proceeds (rc 0) when hooks.json is present."""
        plugin_path = tmp_path / "plugin-with-hooks"
        plugin_path.mkdir()
        hooks_dir = plugin_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text('{"hooks": {}}', encoding="utf-8")

        mock_sdk = _make_mock_sdk([])
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli._resolve_plugin_path", return_value=plugin_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            result = cli.cmd_start()
        assert result == 0, f"expected rc 0 when hooks present, got {result}"


# ---------------------------------------------------------------------------
# AC-H4-2: library-level done gates hold regardless of hook state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLibraryDoneGatesHoldWithoutHooks:
    """AC-H4-2: mark_done gate + force_status done-refusal hold with hooks disabled."""

    def _make_work_unit_file(self, tmp_path: Path, status: str = "in-progress") -> Path:
        """Create a minimal work-unit .md file for testing.

        Args:
            tmp_path: Temporary directory for the file.
            status: The ``## Status:`` value to write.

        Returns:
            Path to the created work-unit file.
        """
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            f"# E0-F1-S1-T1: Test Work Unit\n\n## Status: {status}\n\n## Comments\n",
            encoding="utf-8",
        )
        return wu_file

    def _make_backlog_index(self, tmp_path: Path, unit_id: str, status: str) -> Path:
        """Create a minimal BACKLOG.md index file.

        Args:
            tmp_path: Temporary directory for the file.
            unit_id: The work-unit identifier.
            status: The status for the index row.

        Returns:
            Path to the created BACKLOG.md file.
        """
        index_file = tmp_path / "BACKLOG.md"
        index_file.write_text(
            f"# BACKLOG\n\n| ID | Title | Status |\n"
            f"|-------|-------|--------|\n"
            f"| {unit_id} | Test Work Unit | {status} |\n",
            encoding="utf-8",
        )
        return index_file

    def test_force_status_done_refusal_holds(self, tmp_path: Path) -> None:
        """AC-H4-2: force_status refuses 'done' regardless of hook state.

        This is a library-level gate -- it raises ValueError without any hook
        involvement, so hooks being absent or disabled cannot bypass it.
        """
        wu_file = self._make_work_unit_file(tmp_path)
        index_file = self._make_backlog_index(tmp_path, "E0-F1-S1-T1", "in-progress")
        mgr = BacklogManager()
        with pytest.raises(ValueError, match="force_status must not write 'done'"):
            mgr.force_status(wu_file, index_file, "E0-F1-S1-T1", "done")

    def test_mark_done_gate_refuses_without_passing_verdicts(self, tmp_path: Path) -> None:
        """AC-H4-2: mark_done refuses when not all judges passed, independent of hook state.

        The ``_last_round_all_passed`` check is purely file-based (reads the
        work-unit .md comments) and does not consult any hook-registered guard.
        """
        wu_file = self._make_work_unit_file(tmp_path, status="in-review")
        index_file = self._make_backlog_index(tmp_path, "E0-F1-S1-T1", "in-review")
        mgr = BacklogManager()
        with pytest.raises(RuntimeError, match="not all required judges passed"):
            mgr.mark_done(wu_file, index_file, "E0-F1-S1-T1")

    @pytest.mark.parametrize(
        "attempted_status",
        [
            "done",
            "Done",
            "DONE",
        ],
    )
    def test_force_status_done_refusal_is_case_insensitive(self, tmp_path: Path, attempted_status: str) -> None:
        """AC-H4-2: force_status done-refusal applies regardless of case."""
        wu_file = self._make_work_unit_file(tmp_path)
        index_file = self._make_backlog_index(tmp_path, "E0-F1-S1-T1", "in-progress")
        mgr = BacklogManager()
        with pytest.raises(ValueError, match="force_status must not write 'done'"):
            mgr.force_status(wu_file, index_file, "E0-F1-S1-T1", attempted_status)
