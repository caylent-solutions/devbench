"""Unit tests for _effective_local_only helper in git_ops.py.

Covers AC-FIX-001 through AC-FIX-005:
- Per-repo local_only overrides top-level git_ops.local_only (both directions).
- Falls back to git_ops.local_only when repo is not in RUNTIME_CONFIG.repos.
- Falls back to git_ops.local_only when RUNTIME_CONFIG.repos is None.
- ensure_branch uses _effective_local_only so per-repo precedence is honoured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_runtime_config(
    *,
    git_ops_local_only: bool,
    repos: dict[str, bool] | None,
) -> MagicMock:
    """Build a minimal mock RUNTIME_CONFIG.

    Args:
        git_ops_local_only: Value for RUNTIME_CONFIG.git_ops.local_only.
        repos: Mapping of repo key -> local_only value to populate
            RUNTIME_CONFIG.repos.  ``None`` means RUNTIME_CONFIG.repos is falsy
            (the None path that collapses the conditional in _effective_local_only).
    """
    mock_config = MagicMock()
    mock_config.git_ops.local_only = git_ops_local_only

    if repos is None:
        mock_config.repos = None
    else:
        repo_mocks: dict[str, MagicMock] = {}
        for name, lo in repos.items():
            repo_mock = MagicMock()
            repo_mock.local_only = lo
            repo_mocks[name] = repo_mock
        repos_mock = MagicMock()
        repos_mock.get = lambda key, default=None: repo_mocks.get(key, default)
        repos_mock.__bool__ = lambda self: True
        mock_config.repos = repos_mock

    return mock_config


@pytest.mark.unit
class TestEffectiveLocalOnlyHelper:
    """Parametrized coverage of _effective_local_only precedence rules."""

    @pytest.mark.parametrize(
        "repos, git_ops_local_only, repo_key, expected",
        [
            pytest.param(
                {"workspace-local": True},
                False,
                "workspace-local",
                True,
                id="per_repo_true_overrides_top_level_false",
            ),
            pytest.param(
                {"remote-repo": False},
                True,
                "remote-repo",
                False,
                id="per_repo_false_overrides_top_level_true",
            ),
            pytest.param(
                {},
                True,
                "unknown-repo",
                True,
                id="repo_absent_from_repos_fallback_to_top_level_true",
            ),
            pytest.param(
                {},
                False,
                "unknown-repo",
                False,
                id="repo_absent_from_repos_fallback_to_top_level_false",
            ),
            pytest.param(
                None,
                True,
                "any-repo",
                True,
                id="repos_none_fallback_to_top_level_true",
            ),
            pytest.param(
                None,
                False,
                "any-repo",
                False,
                id="repos_none_fallback_to_top_level_false",
            ),
        ],
    )
    def test_effective_local_only(
        self,
        repos: dict[str, bool] | None,
        git_ops_local_only: bool,
        repo_key: str,
        expected: bool,
    ) -> None:
        """_effective_local_only returns the correct effective value for each scenario."""
        from devbench.github.git_ops import _effective_local_only

        mock_config = _make_runtime_config(
            git_ops_local_only=git_ops_local_only,
            repos=repos,
        )

        with patch("devbench.github.git_ops.RUNTIME_CONFIG", mock_config):
            result = _effective_local_only(repo_key)

        assert result is expected, (
            f"Expected _effective_local_only({repo_key!r}) to be {expected} "
            f"(repos={repos!r}, git_ops.local_only={git_ops_local_only}), got {result}"
        )


@pytest.mark.unit
class TestEnsureBranchUsesEffectiveLocalOnly:
    """ensure_branch must call _effective_local_only(repo) to decide fetch behaviour."""

    def _fake_run_command(self, cmd: list[str], cwd: object = None) -> tuple[int, str, str]:
        """Stub run_command so no real git subprocess is launched."""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "some-other-branch\n", ""
        if cmd[:2] == ["git", "status"]:
            return 0, "", ""
        if cmd[:2] == ["git", "show-ref"]:
            return 1, "", ""
        return 0, "main\n", ""

    def test_ensure_branch_skips_fetch_when_effective_local_only_true(self, tmp_path: Path) -> None:
        """When _effective_local_only returns True, ensure_branch must not call
        git fetch origin.

        This validates AC-FIX-004 by patching _effective_local_only directly so
        the test is decoupled from config resolution details already covered by
        TestEffectiveLocalOnlyHelper.
        """
        from devbench.github.git_ops import GitOpsService

        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        git_mock = MagicMock(return_value=(0, "main\n", ""))
        default_branch_mock = MagicMock(return_value="main")

        with (
            patch("devbench.github.git_ops.run_command", side_effect=self._fake_run_command),
            patch("devbench.github.git_ops._effective_local_only", return_value=True),
            patch("devbench.github.git_ops.validate_repo"),
        ):
            ops: Any = GitOpsService.__new__(GitOpsService)
            ops.logger = MagicMock()
            ops._git = git_mock
            ops._get_default_branch = default_branch_mock

            ops.ensure_branch("workspace-local", repo_path, "feat/work")

        for call in git_mock.call_args_list:
            args = call[0][0] if call[0] else []
            assert args[:2] != ["fetch", "origin"], (
                f"ensure_branch must not call git fetch origin when local_only is True; got {call}"
            )

    def test_ensure_branch_calls_fetch_when_effective_local_only_false(self, tmp_path: Path) -> None:
        """When _effective_local_only returns False, ensure_branch must call
        git fetch origin to create a branch from origin/<default_branch>.
        """
        from devbench.github.git_ops import GitOpsService

        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        fetch_calls: list[list[str]] = []

        def _tracking_git(cmd: list[str], cwd: object) -> tuple[int, str, str]:
            if cmd[:2] == ["fetch", "origin"]:
                fetch_calls.append(cmd)
            return 0, "main\n", ""

        with (
            patch("devbench.github.git_ops.run_command", side_effect=self._fake_run_command),
            patch("devbench.github.git_ops._effective_local_only", return_value=False),
            patch("devbench.github.git_ops.validate_repo"),
        ):
            ops: Any = GitOpsService.__new__(GitOpsService)
            ops.logger = MagicMock()
            ops._git = MagicMock(side_effect=_tracking_git)
            ops._get_default_branch = MagicMock(return_value="main")

            ops.ensure_branch("remote-repo", repo_path, "feat/work")

        assert fetch_calls, "ensure_branch must call git fetch origin when effective local_only is False"
