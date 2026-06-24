"""Integration tests: cmd_git_ops_finalize CI-watch paths (E7-F2-S1-T1).

Covers all four CIResult branches plus the cascade-cap interaction using a
fixture workspace so no real ``gh`` calls are made.

Each test builds a minimal fixture workspace, patches GitOpsService so that
``wait_for_checks_and_classify`` returns a specific CIResult value, invokes
``cmd_git_ops_finalize``, and asserts:

- The returned exit code.
- The audit comments written to the work-unit file (where applicable).
- The task status transition (where applicable).
- The on-disk proposal JSON (where applicable).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task
from devbench.github.git_ops import CIResult

_REPO = "caylent-solutions/devbench"
_BRANCH = "feature/combined"
_PR_URL = "https://github.com/caylent-solutions/devbench/pull/77"
_TASK_ID = "E7-F2-S1-T1"
_KNOWN_TASK_ID = "E7-F1-S1-T1"


def _build_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a minimal fixture workspace with two in-review tasks.

    Returns (workspace_root, backlog_root, backlog_index).

    The BACKLOG.md uses the seven-column format required by BacklogParser:
    ID | Title | Type | Status | Dependencies | Repo | File Path
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backlog_root = workspace / "backlog"
    backlog_root.mkdir()

    backlog_index = workspace / "BACKLOG.md"
    backlog_index.write_text(
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n"
        f"| {_KNOWN_TASK_ID} | Known Task | Task | in-review | None | {_REPO} |"
        f" `backlog/{_KNOWN_TASK_ID}.md` |\n"
        f"| {_TASK_ID} | Current Task | Task | in-review | None | {_REPO} |"
        f" `backlog/{_TASK_ID}.md` |\n",
        encoding="utf-8",
    )

    for tid in (_KNOWN_TASK_ID, _TASK_ID):
        wu_file = backlog_root / f"{tid}.md"
        wu_file.write_text(
            f"# {tid}: Sample Task\n\n"
            f"## Status: in-review\n\n"
            f"## Target Repository\n\n"
            f"- **Repo:** `{_REPO}`\n\n"
            "## Comments\n\n",
            encoding="utf-8",
        )

    return workspace, backlog_root, backlog_index


def _make_mock_ops(ci_result: object) -> MagicMock:
    """Return a GitOpsService mock whose CI helper returns *ci_result*."""
    mock_ops = MagicMock()
    mock_ops.create_pr.return_value = _PR_URL
    mock_ops.wait_for_checks_and_classify.return_value = ci_result
    mock_ops.get_latest_failing_run_id.return_value = None
    return mock_ops


@pytest.mark.integration
class TestFinalizeGreen:
    """AC-FUNC-002 / AC-CYCLE-001: GREEN result returns rc=0 and logs [CI_GREEN]."""

    def test_green_returns_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_git_ops_finalize returns 0 when CI reports GREEN."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.GREEN)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops_finalize(_REPO)

        assert result == 0
        mock_ops.wait_for_checks_and_classify.assert_called_once_with(_PR_URL, repo_path)

    def test_green_logs_ci_green_audit(self, tmp_path: Path) -> None:
        """GREEN result writes [CI_GREEN] audit comment to the most recent in-review task."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.GREEN)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        wu_file = backlog_root / f"{_TASK_ID}.md"
        content = wu_file.read_text(encoding="utf-8")
        assert "[CI_GREEN]" in content

    def test_green_does_not_merge(self, tmp_path: Path) -> None:
        """GREEN result does NOT call merge_pr -- PR stays open for human merge."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.GREEN)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        mock_ops.merge_pr.assert_not_called()


@pytest.mark.integration
class TestFinalizeFailedKnownTask:
    """AC-FUNC-003 / AC-CYCLE-001: FAILED_KNOWN_TASK writes proposal, blocks task, rc=2."""

    def test_failed_known_task_returns_two(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK result returns rc=2."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_KNOWN_TASK(_KNOWN_TASK_ID))

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 5),
        ):
            result = cli.cmd_git_ops_finalize(_REPO)

        assert result == 2

    def test_failed_known_task_writes_proposal_json(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK writes a recovery-proposal JSON blamed on the named task."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_KNOWN_TASK(_KNOWN_TASK_ID))

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 5),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        proposal_path = workspace / ".devbench" / "proposals" / f"{_KNOWN_TASK_ID}.json"
        assert proposal_path.is_file(), f"Expected proposal at {proposal_path}"
        payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        assert payload["source_task_id"] == _KNOWN_TASK_ID

    def test_failed_known_task_transitions_to_blocked(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK transitions the named task to blocked status."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_KNOWN_TASK(_KNOWN_TASK_ID))

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 5),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        wu_file = backlog_root / f"{_KNOWN_TASK_ID}.md"
        content = wu_file.read_text(encoding="utf-8")
        assert "## Status: blocked" in content
        assert "[CI_FAILED_BATCH_PR]" in content


@pytest.mark.integration
class TestFinalizeFailedUnknown:
    """AC-FUNC-004 / AC-CYCLE-001: FAILED_UNKNOWN transitions most-recent in-review/done task."""

    def test_failed_unknown_returns_two(self, tmp_path: Path) -> None:
        """FAILED_UNKNOWN result returns rc=2."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_UNKNOWN)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops_finalize(_REPO)

        assert result == 2

    def test_failed_unknown_blocks_most_recent_task(self, tmp_path: Path) -> None:
        """FAILED_UNKNOWN transitions the most-recent in-review/done task to blocked."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_UNKNOWN)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        wu_file = backlog_root / f"{_TASK_ID}.md"
        content = wu_file.read_text(encoding="utf-8")
        assert "## Status: blocked" in content
        assert "[CI_FAILED_BATCH_PR]" in content


@pytest.mark.integration
class TestFinalizeTimeout:
    """AC-FUNC-005 / AC-CYCLE-001: TIMEOUT returns rc=2 without changing task statuses."""

    def test_timeout_returns_two(self, tmp_path: Path) -> None:
        """TIMEOUT result returns rc=2."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.TIMEOUT)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops_finalize(_REPO)

        assert result == 2

    def test_timeout_does_not_change_task_statuses(self, tmp_path: Path) -> None:
        """TIMEOUT does not transition any task status."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.TIMEOUT)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        for tid in (_KNOWN_TASK_ID, _TASK_ID):
            wu_file = backlog_root / f"{tid}.md"
            content = wu_file.read_text(encoding="utf-8")
            assert "## Status: in-review" in content
            assert "blocked" not in content.lower().replace("## Status: in-review", "")

    def test_timeout_logs_ci_watch_timeout_audit(self, tmp_path: Path) -> None:
        """TIMEOUT writes [CI_WATCH_TIMEOUT] audit to the most recent in-review task."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.TIMEOUT)

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        wu_file = backlog_root / f"{_TASK_ID}.md"
        content = wu_file.read_text(encoding="utf-8")
        assert "[CI_WATCH_TIMEOUT]" in content


@pytest.mark.integration
class TestFinalizeCascadeCap:
    """AC-FUNC-006 / AC-CYCLE-001: cascade-cap interaction.

    When the offending task is already at cascade depth N-1, the proposal
    is skipped and the task transitions to OPERATOR_ACTION_REQUIRED.
    """

    def test_cascade_cap_skips_proposal(self, tmp_path: Path) -> None:
        """When cascade depth >= MAX_CASCADE_DEPTH, no proposal JSON is written."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_KNOWN_TASK(_KNOWN_TASK_ID))

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 1),
        ):
            result = cli.cmd_git_ops_finalize(_REPO)

        proposal_path = workspace / ".devbench" / "proposals" / f"{_KNOWN_TASK_ID}.json"
        assert not proposal_path.exists(), "Proposal should not be written when cascade cap reached"
        assert result == 2

    def test_cascade_cap_adds_ci_failed_cascade_capped_audit(self, tmp_path: Path) -> None:
        """Cascade-cap writes [CI_FAILED_CASCADE_CAPPED] audit to the named task."""
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_KNOWN_TASK(_KNOWN_TASK_ID))

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 1),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        wu_file = backlog_root / f"{_KNOWN_TASK_ID}.md"
        content = wu_file.read_text(encoding="utf-8")
        assert "[CI_FAILED_CASCADE_CAPPED]" in content

    def test_cascade_cap_classifier_returns_operator_action_required(self, tmp_path: Path) -> None:
        """After cascade-cap, classify_blocked_task returns OPERATOR_ACTION_REQUIRED.

        AC-FUNC-006: the task is transitioned to blocked with [CI_FAILED_CASCADE_CAPPED]
        audit marker; the BlockedTaskState classifier resolves this to
        OPERATOR_ACTION_REQUIRED because no proposal marker, no pending
        dependency, and no recovery signal is present.
        """
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        mock_ops = _make_mock_ops(CIResult.FAILED_KNOWN_TASK(_KNOWN_TASK_ID))

        with (
            patch("devbench.config.SINGLE_BRANCH", _BRANCH),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 1),
        ):
            cli.cmd_git_ops_finalize(_REPO)

        state = classify_blocked_task(backlog_root, backlog_index, _KNOWN_TASK_ID)
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED, (
            f"Expected OPERATOR_ACTION_REQUIRED for cascade-capped task, got {state}"
        )
