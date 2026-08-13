"""End-to-end proof that a fully-terminal backlog reaches finalize (#332 FR-5).

Regression coverage for issue #332: before ``E17-F1-S1-T1`` landed,
``_rollup_parent_status`` only ran on the ``done`` transition, so a parent
whose sole remaining child resolved via ``Declined`` (never ``Done``) never
triggered the promotion check at all. The story -- and every ancestor above
it -- stayed stranded in a non-terminal status forever, which meant the
orchestrate SKILL.md step-11 signal ("when all work units are done ... run
``git-ops-finalize``") never fired either. Nothing in the test suite drove
that whole path end-to-end, so the defect was invisible until an operator
hit it live.

This module builds a small four-level backlog (Epic -> Feature -> Story ->
two Tasks, one already Done and one transitioned to Declined by the test
body), drives the declined transition through the real ``BacklogManager``
API exactly as ``devbench set-status``/``devbench decline`` would, and
asserts:

1. The rollup reaches the epic (spec AC-13, first half).
2. ``BacklogParser`` reports the epic Done and zero remaining actionable
   Task candidates -- the concrete signal the orchestrate loop uses to know
   the backlog is drained.
3. ``cmd_git_ops_finalize`` -- the command SKILL.md step 11 invokes once
   that signal fires -- is reached against this drained backlog and
   actually attempts a push (spec AC-13, second half).

No production code is expected to change here (the Approach's GREEN step is
a no-op): this is a validation gate over behaviour that ``E17-F1-S1-T1`` and
``E17-F2-S1-T1`` already delivered.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType
from devbench.github.git_ops import CIResult

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

_REPO = "caylent-solutions/devbench"
_BRANCH = "feature/drained-backlog-e2e"
_PR_URL = "https://github.com/caylent-solutions/devbench/pull/332"

_EPIC_ID = "E90"
_FEATURE_ID = "E90-F1"
_STORY_ID = "E90-F1-S1"
_TASK_DONE_ID = "E90-F1-S1-T1"
_TASK_DECLINED_ID = "E90-F1-S1-T2"

_DECLINE_REASON = "duplicate of Task A -- superseded, closing without further work"


def _build_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a minimal 4-level fixture backlog: Epic -> Feature -> Story -> 2 Tasks.

    ``_TASK_DONE_ID`` starts already Done; ``_TASK_DECLINED_ID`` starts In
    Queue so the test body can transition it to Declined -- the exact
    transition the #332 rollup fix targets. Returns
    ``(workspace_root, backlog_root, backlog_index)``.
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
        f"| {_TASK_DONE_ID} | Task A | Task | done | None | {_REPO} |"
        f" `backlog/{_TASK_DONE_ID}.md` |\n"
        f"| {_TASK_DECLINED_ID} | Task B | Task | in-queue | None | {_REPO} |"
        f" `backlog/{_TASK_DECLINED_ID}.md` |\n"
        f"| {_STORY_ID} | Story | Story | in-queue | None | {_REPO} |"
        f" `backlog/{_STORY_ID}.md` |\n"
        f"| {_FEATURE_ID} | Feature | Feature | in-queue | None | {_REPO} |"
        f" `backlog/{_FEATURE_ID}.md` |\n"
        f"| {_EPIC_ID} | Epic | Epic | in-queue | None | {_REPO} |"
        f" `backlog/{_EPIC_ID}.md` |\n",
        encoding="utf-8",
    )

    def _write_unit(unit_id: str, title: str, status: str) -> None:
        (backlog_root / f"{unit_id}.md").write_text(
            f"# {unit_id}: {title}\n\n"
            f"## Status: {status}\n\n"
            "## Target Repository\n\n"
            f"- **Repo:** `{_REPO}`\n\n"
            "## Comments\n\n",
            encoding="utf-8",
        )

    _write_unit(_TASK_DONE_ID, "Task A", "done")
    _write_unit(_TASK_DECLINED_ID, "Task B", "in-queue")
    _write_unit(_STORY_ID, "Story", "in-queue")
    _write_unit(_FEATURE_ID, "Feature", "in-queue")
    _write_unit(_EPIC_ID, "Epic", "in-queue")

    return workspace, backlog_root, backlog_index


def _decline_last_task(backlog_root: Path, backlog_index: Path) -> None:
    """Transition ``_TASK_DECLINED_ID`` to Declined via the real ``BacklogManager`` API."""
    BacklogManager().mark_declined(
        backlog_root / f"{_TASK_DECLINED_ID}.md",
        backlog_index,
        _TASK_DECLINED_ID,
        _DECLINE_REASON,
    )


def _make_mock_ops(ci_result: object) -> MagicMock:
    """Return a ``GitOpsService`` mock whose CI helper returns *ci_result*."""
    mock_ops = MagicMock()
    mock_ops.create_pr.return_value = _PR_URL
    mock_ops.wait_for_checks_and_classify.return_value = ci_result
    mock_ops.get_latest_failing_run_id.return_value = None
    return mock_ops


@pytest.mark.integration
class TestDrainedBacklogReachesFinalize:
    """AC-E17-F2-S2-T1-1 / spec AC-13: a drained backlog (declined leaf
    included) rolls up to the epic and reaches ``git-ops-finalize``, which
    attempts a push.
    """

    def test_declined_leaf_rolls_up_to_epic(self, tmp_path: Path) -> None:
        """Declining the only remaining open task rolls Story, Feature and
        Epic all the way to Done -- the #332 FR-1 rollup fix exercised
        end-to-end (rather than unit-tested directly against
        ``_rollup_parent_status``).
        """
        _workspace, backlog_root, backlog_index = _build_workspace(tmp_path)

        _decline_last_task(backlog_root, backlog_index)

        story_content = (backlog_root / f"{_STORY_ID}.md").read_text(encoding="utf-8")
        feature_content = (backlog_root / f"{_FEATURE_ID}.md").read_text(encoding="utf-8")
        epic_content = (backlog_root / f"{_EPIC_ID}.md").read_text(encoding="utf-8")

        assert "## Status: done" in story_content, "Story must roll up once its declined child is terminal"
        assert "## Status: done" in feature_content, "Feature must cascade from the story rollup"
        assert "## Status: done" in epic_content, "Epic must cascade all the way from the declined leaf"

        declined_content = (backlog_root / f"{_TASK_DECLINED_ID}.md").read_text(encoding="utf-8")
        assert "## Status: declined" in declined_content, "The leaf itself stays Declined, not Done"

    def test_declined_leaf_leaves_no_actionable_candidates(self, tmp_path: Path) -> None:
        """Once the rollup completes, ``BacklogParser`` reports the epic
        Done and no remaining actionable Task candidates -- the concrete
        signal the orchestrator loop (SKILL.md steps 1/10/11) uses to know
        it is time to advance to ``git-ops-finalize``.
        """
        _workspace, backlog_root, backlog_index = _build_workspace(tmp_path)

        _decline_last_task(backlog_root, backlog_index)

        parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
        units = parser.parse_index()
        candidates = parser.get_parallel_candidates(units)

        assert candidates == [], "No Task should remain actionable once every leaf is terminal"

        epic_unit = next(u for u in units if u.id == _EPIC_ID)
        assert epic_unit.unit_type is WorkUnitType.EPIC
        assert epic_unit.status is WorkUnitStatus.DONE, "Epic must have rolled up to Done"

    def test_drained_backlog_reaches_git_ops_finalize_and_attempts_push(self, tmp_path: Path) -> None:
        """After the rollup drains the backlog, ``cmd_git_ops_finalize`` --
        the command the orchestrate SKILL.md invokes at step 11 -- is reached
        and attempts a push of the accumulated single branch.
        """
        workspace, backlog_root, backlog_index = _build_workspace(tmp_path)

        _decline_last_task(backlog_root, backlog_index)

        # Precondition (not the thing under test in this method): the
        # backlog is actually drained before we even attempt finalize.
        parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
        epic_unit = next(u for u in parser.parse_index() if u.id == _EPIC_ID)
        assert epic_unit.status is WorkUnitStatus.DONE, "Precondition: the backlog must actually be drained"

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

        assert result == 0, "GREEN CI result must return rc=0 once the branch is pushed"

        mock_ops.commit_and_push.assert_called_once()
        push_call = mock_ops.commit_and_push.call_args
        assert push_call.args[0] == _REPO, "Push must target the repo the drained backlog belongs to"
        assert push_call.args[1] == repo_path
        assert push_call.args[2] == _BRANCH, "Push must use the configured single branch"
        assert push_call.kwargs.get("stage_all") is True, "Finalize stages the whole accumulated tree"

        mock_ops.create_pr.assert_called_once()
        mock_ops.wait_for_checks_and_classify.assert_called_once_with(_PR_URL, repo_path)

    def test_non_terminal_sibling_blocks_reaching_finalize(self, tmp_path: Path) -> None:
        """Regression guard: if a sibling Task is still open, the epic must
        NOT roll up, and there must still be an actionable candidate --
        i.e. the orchestrator has not yet reached the point where it would
        call ``git-ops-finalize``. Without this guard, a test that merely
        asserts "finalize can be called" could pass trivially even if the
        rollup never actually gated on backlog completion.
        """
        _workspace, backlog_root, backlog_index = _build_workspace(tmp_path)

        # Add a third, still-open sibling task under the same story so the
        # story cannot be considered fully terminal even after Task B is
        # declined.
        open_task_id = "E90-F1-S1-T3"
        index_path = backlog_root.parent / "BACKLOG.md"
        content = index_path.read_text(encoding="utf-8")
        content = content.replace(
            f"| {_STORY_ID} | Story | Story |",
            f"| {open_task_id} | Task C | Task | in-queue | None | {_REPO} |"
            f" `backlog/{open_task_id}.md` |\n| {_STORY_ID} | Story | Story |",
        )
        index_path.write_text(content, encoding="utf-8")
        (backlog_root / f"{open_task_id}.md").write_text(
            f"# {open_task_id}: Task C\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            f"- **Repo:** `{_REPO}`\n\n"
            "## Comments\n\n",
            encoding="utf-8",
        )

        _decline_last_task(backlog_root, backlog_index)

        story_content = (backlog_root / f"{_STORY_ID}.md").read_text(encoding="utf-8")
        epic_content = (backlog_root / f"{_EPIC_ID}.md").read_text(encoding="utf-8")
        assert "## Status: in-queue" in story_content, "An open sibling must block the rollup"
        assert "## Status: in-queue" in epic_content, "The epic must not cascade while a sibling is open"

        parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
        units = parser.parse_index()
        candidates = parser.get_parallel_candidates(units)
        assert any(u.id == open_task_id for u in candidates), (
            "Task C must still be an actionable candidate -- the backlog is not drained"
        )
