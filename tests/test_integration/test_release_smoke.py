"""V-next release smoke: rc=0/1/2/3 paths + inline cleanup chore commit + pause-before-merge.

Exercises the orchestrator's git-ops surface end-to-end against a tmp
git repo + tmp workspace YAML. Mocks ``GitOpsService._gh`` /
``GitOpsService._git`` and ``BacklogParser.parse_index`` so no real
GitHub or filesystem-walking interactions occur. The intent is to
verify the v-next release's headline behaviours hold in an
integration-style invocation that goes through ``cli.cmd_git_ops``
end-to-end (parser -> service -> manifest assertion -> dispatch ->
exit-code), which unit tests cover only in isolation.

Scenarios covered:
- Standard rc=0 happy path (CI green, no orphans, no review bots).
- Inline orphan-cleanup chore commit lands before the task's commit
  when build/state orphan paths are detected.
- rc=2 CI-failure retry signal with ``ci_failure_retry: true``.
- rc=3 PR review-comment polling signal with
  ``pr_review_resolution.enabled: true`` and a bot in the allowlist.
- rc=0 + in-review transition with ``pause_before_merge: true``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


def _make_unit(unit_id: str = "E0-F1-S1-T1") -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        title="Smoke task",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        branch=f"backlog/{unit_id.lower()}",
        dependencies=[],
    )


def _seed_repo(tmp_path: Path, with_orphan: bool = False) -> Path:
    """Initialise a tmp git repo with a single committed file."""
    repo = tmp_path / "devbench"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "smoke@test.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "smoke"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("smoke", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", repo.as_posix()],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if with_orphan:
        # Tracked orphan: matches the standard "build/state" pattern set.
        (repo / ".coverage (1)").write_text("data", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "leak orphan"], cwd=repo, check=True, capture_output=True)
    return repo


def _make_mock_ops(
    *,
    create_pr_url: str = "https://github.com/caylent-solutions/devbench/pull/42",
    wait_for_checks_returns: bool = True,
) -> MagicMock:
    from devbench.github.git_ops import CIResult

    ops = MagicMock()
    ops.create_pr.return_value = create_pr_url
    ops.wait_for_checks_and_classify.return_value = (
        CIResult.GREEN if wait_for_checks_returns else CIResult.FAILED_UNKNOWN
    )
    return ops


@pytest.mark.integration
class TestReleaseSmoke:
    """End-to-end smoke covering every v-next exit-code path."""

    def test_rc_0_standard_happy_path(self, tmp_path: Path) -> None:
        """CI green, no orphans, no review bots, no pause: rc=0 + merge."""
        repo = _seed_repo(tmp_path, with_orphan=False)
        unit = _make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        ops = _make_mock_ops()
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_git_ops("E0-F1-S1-T1")
        assert rc == 0
        ops.merge_pr.assert_called_once()

    def test_rc_2_ci_retry(self, tmp_path: Path) -> None:
        """CI fails, retry budget allows: rc=2 + executor retry signal."""
        repo = _seed_repo(tmp_path)
        unit = _make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        ops = _make_mock_ops(wait_for_checks_returns=False)
        ops.get_latest_failing_run_id.return_value = "999"
        ops.fetch_run_log.return_value = "ruff E501\n"
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
        ):
            rc = cli.cmd_git_ops("E0-F1-S1-T1")
        assert rc == 2
        ops.merge_pr.assert_not_called()

    def test_rc_3_pr_review_polling(self, tmp_path: Path) -> None:
        """PR review polling fires: rc=3 + executor retry signal."""
        from devbench.github.git_ops import ReviewResolution

        repo = _seed_repo(tmp_path)
        unit = _make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        ops = _make_mock_ops()
        ops.poll_pr_review_resolution.return_value = ReviewResolution(
            resolved=False,
            review_decision="CHANGES_REQUESTED",
            unresolved_reviews=[{"reviewer": "github-copilot[bot]", "state": "CHANGES_REQUESTED", "body": "fix"}],
        )
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ("github-copilot[bot]",)),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
        ):
            rc = cli.cmd_git_ops("E0-F1-S1-T1")
        assert rc == 3
        ops.merge_pr.assert_not_called()

    def test_rc_0_pause_before_merge(self, tmp_path: Path) -> None:
        """pause_before_merge: True transitions to in-review without merging."""
        repo = _seed_repo(tmp_path)
        unit = _make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        ops = _make_mock_ops()
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
            patch("devbench.config.PAUSE_BEFORE_MERGE", True),
        ):
            rc = cli.cmd_git_ops("E0-F1-S1-T1")
        assert rc == 0
        ops.merge_pr.assert_not_called()

    def test_inline_orphan_cleanup_lands_chore_commit(self, tmp_path: Path) -> None:
        """Tracked .coverage (1) orphan -> chore commit lands before task commit."""
        from devbench.constants import DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        repo = _seed_repo(tmp_path, with_orphan=True)
        # Re-stage some executor work alongside the orphan so the inline path
        # has a real "preserve executor staging" job to do.
        (repo / "feature.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True, capture_output=True)

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=repo,
            detected=[".coverage (1)"],
        )
        assert result is False
        log = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert log == DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE
        # Orphan removed from index.
        ls = subprocess.run(
            ["git", "-C", str(repo), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage (1)" not in ls
        # Executor's intended file remains staged.
        staged = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "feature.py" in staged

    def test_check_merge_promotes_done_on_external_merge(self, tmp_path: Path) -> None:
        """pause_before_merge: True + external merge -> cmd_check_merge transitions to done."""
        unit = _make_unit()
        unit_in_review = WorkUnit(
            id=unit.id,
            title=unit.title,
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=unit.unit_type,
            file_path=unit.file_path,
            repo=unit.repo,
            branch=unit.branch,
            dependencies=unit.dependencies,
        )
        ops = MagicMock()
        ops._gh.return_value = (
            0,
            json.dumps([{"number": 42, "state": "MERGED", "merged": True, "url": "u"}]),
            "",
        )
        mgr = MagicMock()
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch(
                "devbench.cli._resolve_git_ops_context",
                return_value=(unit_in_review, "caylent-solutions/devbench", tmp_path),
            ),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_done.assert_called_once()
