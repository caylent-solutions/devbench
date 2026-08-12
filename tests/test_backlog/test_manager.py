"""Tests for judges.backlog_manager module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.backlog.work_unit import WorkUnitType
from devbench.config_loader import RepoConfig, RuntimeConfig, ValidateConfig
from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_INDEX_CELL_COUNT,
    REVIEW_JUDGE_NAMES,
    SECURITY_JUDGE_NAMES,
    STATUS_DRAFT,
    VALID_STATUSES,
)
from devbench.scope import ScopeFilter


@pytest.fixture
def backlog_index_titlecase(tmp_path: Path) -> Path:
    """Create a BACKLOG.md with title-case status values."""
    content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Create Makefile | Task | In Queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Lint Targets | Task | In Queue | E0-F1-S1-T1 | git-repo | `backlog/E0-F1-S1-T2.md` |
| E0-F1-S1-T3 | Test Targets | Task | Done | None | git-repo | `backlog/E0-F1-S1-T3.md` |
"""
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(content)
    return index_path


@pytest.fixture
def backlog_index_lowercase(tmp_path: Path) -> Path:
    """Create a BACKLOG.md with lowercase status values (as the real backlog uses)."""
    content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Create Makefile | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Lint Targets | Task | in-review | E0-F1-S1-T1 | git-repo | `backlog/E0-F1-S1-T2.md` |
"""
    index_path = tmp_path / "BACKLOG-lower.md"
    index_path.write_text(content)
    return index_path


@pytest.fixture
def backlog_with_hierarchy(tmp_path: Path, backlog_dir: Path) -> tuple[Path, Path, Path, Path]:
    """Create BACKLOG.md with story + tasks, plus work unit files for all."""
    content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Create Makefile | Task | Done | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Lint Targets | Task | in-queue | E0-F1-S1-T1 | git-repo | `backlog/E0-F1-S1-T2.md` |
| E0-F1-S1 | Makefile Story | Story | in-queue | E0-F1 | git-repo | `backlog/E0-F1-S1.md` |
| E0-F1 | git-repo Tooling | Feature | in-queue | None | git-repo | `backlog/E0-F1.md` |
"""
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(content)

    t2_file = backlog_dir / "E0-F1-S1-T2.md"
    t2_file.write_text("# E0-F1-S1-T2\n\n## Status: in-queue\n")

    story_file = backlog_dir / "E0-F1-S1.md"
    story_file.write_text("# E0-F1-S1\n\n## Status: in-queue\n")

    feature_file = backlog_dir / "E0-F1.md"
    feature_file.write_text("# E0-F1\n\n## Status: in-queue\n")

    return index_path, t2_file, story_file, feature_file


class TestForceStatus:
    """Test force_status updates both files without enforcing the done-gate."""

    def test_updates_both_files(self, tmp_work_unit_file: Path, backlog_index_titlecase: Path) -> None:
        judge = BacklogManager()
        judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "in-progress")

        wu_content = tmp_work_unit_file.read_text()
        assert "## Status: in-progress" in wu_content

        index_content = backlog_index_titlecase.read_text()
        for line in index_content.splitlines():
            if "E0-F1-S1-T1" in line:
                assert "in-progress" in line
                break
        else:
            pytest.fail("E0-F1-S1-T1 not found in BACKLOG.md")

    def test_allows_done_without_judge_comments(self, tmp_work_unit_file: Path, backlog_index_titlecase: Path) -> None:
        """force_status bypasses the done-gate -- no judge comments required."""
        judge = BacklogManager()
        judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "done")

        assert "## Status: done" in tmp_work_unit_file.read_text()

    def test_updates_lowercase_statuses_in_backlog(
        self,
        tmp_work_unit_file: Path,
        backlog_index_lowercase: Path,
    ) -> None:
        judge = BacklogManager()
        judge.force_status(tmp_work_unit_file, backlog_index_lowercase, "E0-F1-S1-T1", "done")

        index_content = backlog_index_lowercase.read_text()
        for line in index_content.splitlines():
            if "E0-F1-S1-T1" in line:
                assert "done" in line
                break
        else:
            pytest.fail("E0-F1-S1-T1 not found in BACKLOG.md")

    def test_accepts_all_valid_statuses(self, tmp_work_unit_file: Path, backlog_index_titlecase: Path) -> None:
        judge = BacklogManager()
        for cli_status, canonical in VALID_STATUSES.items():
            backlog_index_titlecase.write_text(
                backlog_index_titlecase.read_text()
                .replace("in-progress", "In Queue")
                .replace("in-review", "In Queue")
                .replace("done", "In Queue")
                .replace("blocked", "In Queue")
                .replace("in-queue", "In Queue")
                .replace("In Progress", "In Queue")
                .replace("In Review", "In Queue")
                .replace("Done", "In Queue")
                .replace("Blocked", "In Queue")
            )
            tmp_work_unit_file.write_text(
                tmp_work_unit_file.read_text().replace(
                    "## Status: " + tmp_work_unit_file.read_text().split("## Status: ")[1].split("\n")[0],
                    "## Status: in-queue",
                )
            )

            judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", cli_status)
            wu_content = tmp_work_unit_file.read_text()
            assert f"## Status: {canonical}" in wu_content

    def test_rejects_invalid_status(self, tmp_work_unit_file: Path, backlog_index_titlecase: Path) -> None:
        judge = BacklogManager()
        with pytest.raises(ValueError, match="Invalid status"):
            judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "invalid")

    def test_raises_file_not_found_for_work_unit(self, tmp_path: Path, backlog_index_titlecase: Path) -> None:
        judge = BacklogManager()
        with pytest.raises(FileNotFoundError):
            judge.force_status(tmp_path / "missing.md", backlog_index_titlecase, "E0-F1-S1-T1", "done")

    def test_raises_file_not_found_for_backlog(self, tmp_work_unit_file: Path, tmp_path: Path) -> None:
        judge = BacklogManager()
        with pytest.raises(FileNotFoundError):
            judge.force_status(tmp_work_unit_file, tmp_path / "missing.md", "E0-F1-S1-T1", "done")

    def test_force_status_with_session_name_stamps_wu_claimed_comment(
        self, tmp_work_unit_file: Path, backlog_index_titlecase: Path
    ) -> None:
        """force_status with session_name appends 'session=<name>' in WU_CLAIMED audit comment."""
        judge = BacklogManager()
        judge.force_status(
            tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "in-progress", session_name="alpha"
        )
        content = tmp_work_unit_file.read_text()
        assert "[WU_CLAIMED] Set E0-F1-S1-T1 to 'in-progress' session=alpha" in content

    def test_force_status_without_session_name_omits_session_in_wu_claimed_comment(
        self, tmp_work_unit_file: Path, backlog_index_titlecase: Path
    ) -> None:
        """force_status without session_name uses the bare WU_CLAIMED format (no session= suffix)."""
        judge = BacklogManager()
        judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "in-progress")
        content = tmp_work_unit_file.read_text()
        assert "[WU_CLAIMED] Set E0-F1-S1-T1 to 'in-progress'" in content
        assert "session=" not in content

    def test_force_status_session_name_ignored_for_non_in_progress_transition(
        self, tmp_work_unit_file: Path, backlog_index_titlecase: Path
    ) -> None:
        """session_name is accepted but has no effect when the new status is not in-progress."""
        judge = BacklogManager()
        judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "in-review", session_name="beta")
        content = tmp_work_unit_file.read_text()
        assert "session=beta" not in content


def _judge_comment(judge_name: str, action: str, msg: str = "ok") -> str:
    """Return a single formatted judge comment line (no trailing newline)."""
    return f"[2024-01-01 00:00 UTC] [judge/{judge_name}] [{action}] {msg}"


def _all_judges_pass_block() -> str:
    """Return comment lines for all four required judges passing."""
    return "\n".join(_judge_comment(j, "REVIEW_PASS") for j in sorted(REVIEW_JUDGE_NAMES)) + "\n"


def _all_five_judges_pass_block() -> str:
    """Return comment lines for all five required judges (4 review_team + security_review) passing."""
    return "\n".join(_judge_comment(j, "REVIEW_PASS") for j in sorted(ALL_REQUIRED_JUDGE_NAMES)) + "\n"


_ALL_JUDGES_PASSED_COMMENTS = _all_five_judges_pass_block()


class TestMarkDone:
    """Test mark_done delegates to set_status and updates both files."""

    def test_mark_done_updates_both_files(self, tmp_work_unit_file: Path, backlog_index_titlecase: Path) -> None:
        # Append required judge pass entries so the done-gate check passes.
        # Declare an exempt Task Type (E4-F4-S1-T2 round 3: mark_done() now
        # enforces the FR-4.5/FR-4.6 task-type invariant directly, so an
        # undeclared type would default to the strictest gated type and
        # this test -- which is about the judges-pass gate, not the
        # task-type gate -- would fail for an unrelated reason).
        content = tmp_work_unit_file.read_text(encoding="utf-8")
        content = content.replace("## Status: in-queue\n", "## Status: in-queue\n\n## Task Type: chore\n", 1)
        tmp_work_unit_file.write_text(content + _ALL_JUDGES_PASSED_COMMENTS, encoding="utf-8")

        judge = BacklogManager()
        judge.mark_done(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1")

        wu_content = tmp_work_unit_file.read_text()
        assert "## Status: done" in wu_content

        index_content = backlog_index_titlecase.read_text()
        for line in index_content.splitlines():
            if "E0-F1-S1-T1" in line:
                assert "done" in line
                break

    def test_mark_done_raises_file_not_found(self, tmp_path: Path, backlog_index_titlecase: Path) -> None:
        judge = BacklogManager()
        with pytest.raises(FileNotFoundError):
            judge.mark_done(tmp_path / "nonexistent.md", backlog_index_titlecase, "E0-F1-S1-T1")

    def test_mark_done_raises_when_no_status_line(self, tmp_path: Path, backlog_index_titlecase: Path) -> None:
        bad_file = tmp_path / "bad.md"
        # Declare an exempt Task Type so this test exercises only the
        # missing-status-line ValueError, not the task-type invariant that
        # mark_done() now also enforces (E4-F4-S1-T2 round 3).
        bad_file.write_text("# No status here\n\n## Task Type: chore\n\nJust content.\n" + _ALL_JUDGES_PASSED_COMMENTS)

        judge = BacklogManager()
        with pytest.raises(ValueError, match="Could not find"):
            judge.mark_done(bad_file, backlog_index_titlecase, "E0-F1-S1-T1")


class TestMarkBlocked:
    """Test mark_blocked updates both files and appends comment."""

    def test_mark_blocked_updates_both_files_and_adds_comment(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
    ) -> None:
        judge = BacklogManager()
        judge.mark_blocked(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "Dependency not met")

        wu_content = tmp_work_unit_file.read_text()
        assert "## Status: blocked" in wu_content
        assert "Dependency not met" in wu_content
        assert "[BLOCKED]" in wu_content

        index_content = backlog_index_titlecase.read_text()
        for line in index_content.splitlines():
            if "E0-F1-S1-T1" in line:
                assert "blocked" in line
                break


class TestMarkHeldAndUnheld:
    """E222: mark_held and unmark_held lifecycle methods."""

    def test_mark_held_updates_both_files_and_writes_audit(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
    ) -> None:
        manager = BacklogManager()
        manager.mark_held(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "awaiting product input")

        wu_content = tmp_work_unit_file.read_text()
        assert "## Status: hold" in wu_content
        assert "[HOLD]" in wu_content
        assert "awaiting product input" in wu_content

        index_content = backlog_index_titlecase.read_text()
        for line in index_content.splitlines():
            if "E0-F1-S1-T1" in line:
                assert "hold" in line
                break
        else:
            pytest.fail("E0-F1-S1-T1 row not found in BACKLOG.md after mark_held")

    def test_unmark_held_returns_unit_to_in_queue_with_audit(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
    ) -> None:
        manager = BacklogManager()
        manager.mark_held(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "deferred")
        manager.unmark_held(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "input received")

        wu_content = tmp_work_unit_file.read_text()
        assert "## Status: in-queue" in wu_content
        # Both audit markers must be present so the lifecycle is reconstructible.
        assert "[HOLD]" in wu_content
        assert "[UNHOLD]" in wu_content
        assert "deferred" in wu_content
        assert "input received" in wu_content


class TestRemoveUnit:
    """db-303 (E12-F1-S2-T1): the managed ``remove`` verb (spec 4.A, FR-16).

    ``remove_unit`` deletes the work-unit file and its BACKLOG.md index row
    under a single ``flock_backlog``, re-rolls the Status Summary, and
    appends a ``[WU_REMOVED] <id> -- <reason>`` line to the workspace audit
    log. An unknown id fails fast with ``ValueError`` before any file is
    touched.
    """

    def test_remove_unit_deletes_file_and_index_row_under_flock(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
        tmp_path: Path,
    ) -> None:
        audit_log_path = tmp_path / "logs" / "removals.log"
        manager = BacklogManager()
        manager.remove_unit(
            tmp_work_unit_file,
            backlog_index_titlecase,
            "E0-F1-S1-T1",
            "superseded by E0-F1-S1-T9",
            audit_log_path,
        )

        # (1) the work-unit file is deleted.
        assert not tmp_work_unit_file.exists()

        # (2) the BACKLOG.md index row for the removed unit is gone, while
        # sibling rows are untouched. Matched on the row's own ID cell (not
        # a bare substring check) since E0-F1-S1-T1 also appears in T2's
        # Dependencies column.
        index_content = backlog_index_titlecase.read_text()
        assert not any(line.strip().startswith("| E0-F1-S1-T1 |") for line in index_content.splitlines())
        assert any(line.strip().startswith("| E0-F1-S1-T2 |") for line in index_content.splitlines())
        assert any(line.strip().startswith("| E0-F1-S1-T3 |") for line in index_content.splitlines())

        # (3) the Status Summary section was re-rolled.
        assert "## Status Summary" in index_content

        # (4) the workspace audit log has the [WU_REMOVED] line.
        audit_content = audit_log_path.read_text()
        assert "[WU_REMOVED] E0-F1-S1-T1 -- superseded by E0-F1-S1-T9" in audit_content

    def test_remove_unit_unknown_id_raises(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
        tmp_path: Path,
    ) -> None:
        audit_log_path = tmp_path / "logs" / "removals.log"
        manager = BacklogManager()

        with pytest.raises(ValueError, match=r"remove: work unit 'NO-SUCH-ID' not found in BACKLOG\.md"):
            manager.remove_unit(
                tmp_work_unit_file,
                backlog_index_titlecase,
                "NO-SUCH-ID",
                "typo",
                audit_log_path,
            )

        # Nothing was deleted: the WU file and its index row survive, and no
        # audit line was ever written.
        assert tmp_work_unit_file.exists()
        assert "E0-F1-S1-T1" in backlog_index_titlecase.read_text()
        assert not audit_log_path.exists()

    def test_remove_unit_creates_audit_log_parent_directories(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
        tmp_path: Path,
    ) -> None:
        audit_log_path = tmp_path / "nested" / "does" / "not" / "exist" / "removals.log"
        manager = BacklogManager()
        manager.remove_unit(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "cleanup", audit_log_path)

        assert audit_log_path.exists()
        assert "[WU_REMOVED] E0-F1-S1-T1 -- cleanup" in audit_log_path.read_text()

    def test_remove_unit_appends_to_existing_audit_log(
        self,
        tmp_work_unit_file: Path,
        backlog_index_titlecase: Path,
        tmp_path: Path,
    ) -> None:
        audit_log_path = tmp_path / "logs" / "removals.log"
        audit_log_path.parent.mkdir(parents=True)
        audit_log_path.write_text("[2020-01-01 00:00 UTC] [WU_REMOVED] E9-F1-S1-T1 -- earlier removal\n")

        manager = BacklogManager()
        manager.remove_unit(
            tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "second removal", audit_log_path
        )

        content = audit_log_path.read_text()
        assert "E9-F1-S1-T1 -- earlier removal" in content
        assert "[WU_REMOVED] E0-F1-S1-T1 -- second removal" in content

    def test_remove_backlog_index_row_raises_when_index_missing(self, tmp_path: Path) -> None:
        manager = BacklogManager()
        missing_index = tmp_path / "does-not-exist" / "BACKLOG.md"
        with pytest.raises(FileNotFoundError, match="Backlog index not found"):
            manager._remove_backlog_index_row(missing_index, "E0-F1-S1-T1")


class TestRollupParentStatus:
    """Test that marking the last child Done rolls up to parent."""

    def test_story_marked_done_when_all_tasks_done(
        self,
        backlog_with_hierarchy: tuple[Path, Path, Path, Path],
    ) -> None:
        index_path, t2_file, story_file, feature_file = backlog_with_hierarchy

        judge = BacklogManager()
        # T1 is already Done in the fixture. Mark T2 Done -- should roll up S1.
        judge.force_status(t2_file, index_path, "E0-F1-S1-T2", "done")

        # Story should now be done in both files
        story_content = story_file.read_text()
        assert "## Status: done" in story_content

        index_content = index_path.read_text()
        for line in index_content.splitlines():
            if "| E0-F1-S1 |" in line and "Story" in line:
                assert " done " in line
                break
        else:
            pytest.fail("E0-F1-S1 story row not found in BACKLOG.md")

    def test_no_rollup_when_siblings_not_done(
        self,
        backlog_with_hierarchy: tuple[Path, Path, Path, Path],
    ) -> None:
        index_path, t2_file, story_file, feature_file = backlog_with_hierarchy

        judge = BacklogManager()
        # Mark T2 as in-progress -- T1 is Done but T2 is not, so story stays
        judge.force_status(t2_file, index_path, "E0-F1-S1-T2", "in-progress")

        story_content = story_file.read_text()
        assert "## Status: in-queue" in story_content

    def test_rollup_treats_declined_children_as_complete(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Parent rolls to done when every child is either Done or Declined."""
        content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Task A | Task | done | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Task B | Task | declined | None | git-repo | `backlog/E0-F1-S1-T2.md` |
| E0-F1-S1-T3 | Task C | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T3.md` |
| E0-F1-S1 | Story A | Story | in-queue | None | git-repo | `backlog/E0-F1-S1.md` |
"""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(content)

        t3_file = backlog_dir / "E0-F1-S1-T3.md"
        t3_file.write_text("# E0-F1-S1-T3\n\n## Status: in-queue\n")
        story_file = backlog_dir / "E0-F1-S1.md"
        story_file.write_text("# E0-F1-S1\n\n## Status: in-queue\n")

        judge = BacklogManager()
        # Mark the final in-queue task Done. The other sibling is Declined.
        # Rollup should succeed because declined children are terminal-complete.
        judge.force_status(t3_file, index_path, "E0-F1-S1-T3", "done")

        assert "## Status: done" in story_file.read_text()

    def test_cascades_to_feature_when_all_stories_done(self, tmp_path: Path, backlog_dir: Path) -> None:
        content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Task A | Task | Done | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1 | Story A | Story | Done | None | git-repo | `backlog/E0-F1-S1.md` |
| E0-F1-S2-T1 | Task B | Task | in-queue | None | git-repo | `backlog/E0-F1-S2-T1.md` |
| E0-F1-S2 | Story B | Story | in-queue | None | git-repo | `backlog/E0-F1-S2.md` |
| E0-F1 | Feature | Feature | in-queue | None | git-repo | `backlog/E0-F1.md` |
"""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(content)

        t_file = backlog_dir / "E0-F1-S2-T1.md"
        t_file.write_text("# E0-F1-S2-T1\n\n## Status: in-queue\n")
        s2_file = backlog_dir / "E0-F1-S2.md"
        s2_file.write_text("# E0-F1-S2\n\n## Status: in-queue\n")
        feature_file = backlog_dir / "E0-F1.md"
        feature_file.write_text("# E0-F1\n\n## Status: in-queue\n")

        judge = BacklogManager()
        # Mark last task Done → story rolls up → feature rolls up
        judge.force_status(t_file, index_path, "E0-F1-S2-T1", "done")

        # S2 should be done
        assert "## Status: done" in s2_file.read_text()

        # Feature should be done (both stories now done)
        assert "## Status: done" in feature_file.read_text()

        # Verify BACKLOG.md
        final_content = index_path.read_text()
        for line in final_content.splitlines():
            if "| E0-F1 |" in line and "Feature" in line:
                assert " done " in line
                break
        else:
            pytest.fail("E0-F1 feature row not found")


class TestLogToTraceabilityMatrix:
    """Test traceability matrix logging."""

    def test_creates_matrix_file_if_absent(self, tmp_path: Path) -> None:
        matrix = tmp_path / "traceability" / "matrix.md"

        judge = BacklogManager()
        judge.log_to_traceability_matrix(matrix, "AC-FUNC-001", "test_feature")

        assert matrix.exists()
        content = matrix.read_text()
        assert "Spec Ref" in content
        assert "AC-FUNC-001" in content
        assert "test_feature" in content

    def test_appends_to_existing_matrix(self, tmp_path: Path) -> None:
        matrix = tmp_path / "matrix.md"
        matrix.write_text("| Spec Ref | Test Ref | Verified At |\n| --- | --- | --- |\n")

        judge = BacklogManager()
        judge.log_to_traceability_matrix(matrix, "AC-01", "test_a")
        judge.log_to_traceability_matrix(matrix, "AC-02", "test_b")

        content = matrix.read_text()
        assert "AC-01" in content
        assert "AC-02" in content
        lines = [line for line in content.strip().splitlines() if line.startswith("|")]
        assert len(lines) >= 4


class TestLastRoundAllPassed:
    """Tests for _last_round_all_passed() done-gate check."""

    def _make_wu_with_comments(self, tmp_path: Path, comments: str) -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            f"# E0-F1-S1-T1\n\n## Status: in-review\n\n## Comments\n\n{comments}",
            encoding="utf-8",
        )
        return wu

    def test_returns_true_when_all_required_judges_passed(self, tmp_path: Path) -> None:
        wu = self._make_wu_with_comments(tmp_path, _all_five_judges_pass_block())
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is True

    def test_returns_false_when_judge_missing(self, tmp_path: Path) -> None:
        comments = (
            "\n".join(
                [
                    _judge_comment("code_review", "REVIEW_PASS"),
                    _judge_comment("test_review", "REVIEW_PASS"),
                    _judge_comment("doc_review", "REVIEW_PASS"),
                    # changes_manifest missing
                ]
            )
            + "\n"
        )
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False

    def test_returns_false_when_followed_by_review_rejected(self, tmp_path: Path) -> None:
        """All 4 judges passed in round 1, but then REVIEW_REJECTED -- round 2 has no passes."""
        comments = (
            # Round 1 passes (older, before REVIEW_REJECTED)
            _all_judges_pass_block() + "[2024-01-01 00:04 UTC] [orchestrator] [REVIEW_REJECTED] attempt 1 rejected\n"
            # Round 2 has no REVIEW_PASS entries yet (only rejection so far)
        )
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False

    def test_returns_true_when_round2_passes_after_rejection(self, tmp_path: Path) -> None:
        """Round 2 passes after a prior round was rejected."""
        comments = (
            # Round 1 -- rejected
            _judge_comment("code_review", "REVIEW_PASS")
            + "\n"
            + "[2024-01-01 00:01 UTC] [orchestrator] [REVIEW_REJECTED] attempt 1 rejected\n"
            # Round 2 -- all five judges pass
            + _all_five_judges_pass_block()
        )
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is True

    def test_returns_false_when_no_comments(self, tmp_path: Path) -> None:
        wu = self._make_wu_with_comments(tmp_path, "")
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False


class TestSecurityGate:
    """Tests for the security_review gate in _last_round_all_passed (AC-4, AC-5)."""

    def _make_wu_with_comments(self, tmp_path: Path, comments: str) -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            f"# E0-F1-S1-T1\n\n## Status: in-review\n\n## Comments\n\n{comments}",
            encoding="utf-8",
        )
        return wu

    def test_constants_security_judge_names_exists(self) -> None:
        """AC-3: SECURITY_JUDGE_NAMES must be a frozenset containing 'security_review'."""
        assert "security_review" in SECURITY_JUDGE_NAMES
        assert isinstance(SECURITY_JUDGE_NAMES, frozenset)

    def test_constants_all_required_judge_names_includes_security(self) -> None:
        """AC-3: ALL_REQUIRED_JUDGE_NAMES must include both REVIEW_JUDGE_NAMES and SECURITY_JUDGE_NAMES."""
        assert ALL_REQUIRED_JUDGE_NAMES >= REVIEW_JUDGE_NAMES
        assert ALL_REQUIRED_JUDGE_NAMES >= SECURITY_JUDGE_NAMES
        assert "security_review" in ALL_REQUIRED_JUDGE_NAMES
        assert isinstance(ALL_REQUIRED_JUDGE_NAMES, frozenset)

    def test_last_round_all_passed_false_when_only_review_team_passes(self, tmp_path: Path) -> None:
        """AC-4: Returns False when all 4 review_team judges pass but security_review is absent."""
        comments = _all_judges_pass_block()  # only the 4 review_team judges
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False

    def test_last_round_all_passed_requires_security_review(self, tmp_path: Path) -> None:
        """AC-4: Returns False when security_review REVIEW_PASS is absent."""
        comments = (
            "\n".join(
                [
                    _judge_comment("code_review", "REVIEW_PASS"),
                    _judge_comment("test_review", "REVIEW_PASS"),
                    _judge_comment("doc_review", "REVIEW_PASS"),
                    _judge_comment("changes_manifest", "REVIEW_PASS"),
                    # security_review deliberately absent
                ]
            )
            + "\n"
        )
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False

    def test_last_round_all_passed_true_when_all_five_judges_pass(self, tmp_path: Path) -> None:
        """AC-5: Returns True only when all 5 judges (4 review_team + security_review) have REVIEW_PASS."""
        comments = _all_five_judges_pass_block()
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is True


class TestMarkDoneGate:
    """Test that mark_done enforces the done-gate check."""

    def _make_wu(
        self,
        tmp_path: Path,
        comments: str = "",
        *,
        task_type: str | None = "chore",
        tdd_cycle_log: str = "",
    ) -> Path:
        # Defaults to an exempt Task Type ("chore") so these judges-gate
        # tests exercise only the judges-passed check, not the FR-4.5/FR-4.6
        # task-type invariant mark_done() now also enforces directly
        # (E4-F4-S1-T2 round 3). Tests targeting the task-type invariant
        # itself pass ``task_type`` explicitly.
        task_type_section = f"## Task Type: {task_type}\n\n" if task_type is not None else ""
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            f"# E0-F1-S1-T1\n\n## Status: in-review\n\n{task_type_section}"
            f"## TDD Cycle Log\n\n{tdd_cycle_log}\n## Comments\n\n{comments}",
            encoding="utf-8",
        )
        return wu

    def _make_index(self, tmp_path: Path) -> Path:
        content = (
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Task | Task | in-review | None | repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        idx = tmp_path / "BACKLOG.md"
        idx.write_text(content, encoding="utf-8")
        return idx

    def test_mark_done_raises_when_judges_not_all_passed(self, tmp_path: Path) -> None:
        wu = self._make_wu(tmp_path, _judge_comment("code_review", "REVIEW_PASS") + "\n")
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        with pytest.raises(RuntimeError, match="not all required judges passed"):
            judge.mark_done(wu, idx, "E0-F1-S1-T1")

    def test_mark_done_succeeds_when_all_judges_passed(self, tmp_path: Path) -> None:
        wu = self._make_wu(tmp_path, _all_five_judges_pass_block())
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        judge.mark_done(wu, idx, "E0-F1-S1-T1")
        assert "## Status: done" in wu.read_text(encoding="utf-8")

    def test_mark_done_raises_when_gated_default_type_missing_red_observed(self, tmp_path: Path) -> None:
        """code_review FAIL round 3 regression: BacklogManager.mark_done() itself
        must enforce the RED_OBSERVED invariant for a gated task, not merely
        cli.py's cmd_mark_done wrapper -- so every caller (including
        _check_merge_handle_merged / check-merge) inherits the block."""
        # No ``## Task Type:`` section declared at all -> defaults to the
        # strictest gated type (DEFAULT_TASK_TYPE), same fail-closed
        # precedent as _check_task_type_taxonomy.
        wu = self._make_wu(tmp_path, _all_five_judges_pass_block(), task_type=None)
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        with pytest.raises(RuntimeError, match="no RED_OBSERVED record found"):
            judge.mark_done(wu, idx, "E0-F1-S1-T1")
        assert "## Status: in-review" in wu.read_text(encoding="utf-8")
        assert "## Status: done" not in wu.read_text(encoding="utf-8")

    def test_mark_done_succeeds_when_gated_type_has_red_observed(self, tmp_path: Path) -> None:
        red_observed = (
            "- [RED_OBSERVED] 2026-01-01T00:00:00+00:00 -- exit_code=1 "
            "test_node_id=tests/test_x.py::test_y "
            f"failure_digest={'a' * 64}\n"
        )
        wu = self._make_wu(
            tmp_path,
            _all_five_judges_pass_block(),
            task_type="behavior-fix",
            tdd_cycle_log=red_observed,
        )
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        judge.mark_done(wu, idx, "E0-F1-S1-T1")
        assert "## Status: done" in wu.read_text(encoding="utf-8")

    def test_mark_done_raises_when_refactor_missing_green_green_observed(self, tmp_path: Path) -> None:
        """code_review FAIL round 3 regression: a refactor task with all judges
        passed but no machine-observed GREEN_GREEN_OBSERVED record must be
        blocked by BacklogManager.mark_done() itself."""
        wu = self._make_wu(tmp_path, _all_five_judges_pass_block(), task_type="refactor")
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        with pytest.raises(RuntimeError, match="no GREEN_GREEN_OBSERVED record found"):
            judge.mark_done(wu, idx, "E0-F1-S1-T1")
        assert "## Status: in-review" in wu.read_text(encoding="utf-8")
        assert "## Status: done" not in wu.read_text(encoding="utf-8")

    def test_mark_done_succeeds_when_refactor_has_green_green_observed(self, tmp_path: Path) -> None:
        green_green_observed = (
            "- [GREEN_GREEN_OBSERVED] 2026-01-01T00:00:00+00:00 -- test_node_ids=tests/test_x.py::test_y\n"
        )
        wu = self._make_wu(
            tmp_path,
            _all_five_judges_pass_block(),
            task_type="refactor",
            tdd_cycle_log=green_green_observed,
        )
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        judge.mark_done(wu, idx, "E0-F1-S1-T1")
        assert "## Status: done" in wu.read_text(encoding="utf-8")


def _extract_summary_lines(content: str) -> list[str]:
    """Extract table data lines from the Status Summary section only."""
    in_summary = False
    lines = []
    for line in content.splitlines():
        if line.strip() == "## Status Summary":
            in_summary = True
            continue
        if in_summary and line.startswith("##"):
            break
        if in_summary and line.strip().startswith("|"):
            cells = [c.strip() for c in line.split("|")]
            # Skip the header row (contains 'Epic' as first cell) and separator
            if len(cells) > 1 and cells[1] not in ("Epic", "------", "---"):
                lines.append(line)
    return lines


class TestValidate:
    """Tests for BacklogManager.validate() backlog integrity checks."""

    def _make_index(self, tmp_path: Path, rows: str) -> Path:
        idx = tmp_path / "BACKLOG.md"
        # Include a valid Status Summary section to pass AC-4 check.
        # Use a known epic prefix pattern that won't clash with test data.
        idx.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n" + rows,
            encoding="utf-8",
        )
        return idx

    def _make_wu(self, backlog_dir: Path, unit_id: str, status: str = "in-queue") -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        # Include all required sections so content validation passes for task IDs.
        # Manifest carries a production-source row paired with its test entry so
        # the default ("behavior-fix") task type's production-source invariant
        # (FR-4.1) and Rule 14 (source-test atomicity) both pass.
        content = (
            f"# {unit_id}\n\n"
            f"## Status: {status}\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nTest work unit.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Placeholder\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `src/f.py` | New |\n| `tests/unit/test_f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] All ACs checked\n\n"
            "## TDD Cycle Log\n\n## Comments\n"
        )
        wu.write_text(content, encoding="utf-8")
        return wu

    def test_valid_backlog_returns_no_errors(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "done")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | done | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert errors == []

    def test_missing_work_unit_file_is_reported(self, tmp_path: Path) -> None:
        # Index references a file that doesn't exist
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "missing" in e.lower() for e in errors)

    def test_status_mismatch_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Index says "in-queue" but file says "done"
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "done")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "status" in e.lower() for e in errors)

    def test_orphaned_work_unit_file_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        # Extra file not in index
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-queue")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T2" in e and "orphan" in e.lower() for e in errors)

    def test_orphaned_nested_work_unit_file_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        # Nested orphan in a subdirectory
        nested_dir = backlog_dir / "E0-epic"
        nested_dir.mkdir()
        nested_wu = nested_dir / "E0-F1-S1-T3.md"
        nested_wu.write_text("# E0-F1-S1-T3\n\n## Status: in-queue\n", encoding="utf-8")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T3" in e and "orphan" in e.lower() for e in errors)

    def test_indexed_nested_work_unit_not_orphaned(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Nested file that IS in the index should not be flagged
        nested_dir = backlog_dir / "E0-epic"
        nested_dir.mkdir()
        nested_wu = nested_dir / "E0-F1-S1-T1.md"
        nested_wu.write_text("# E0-F1-S1-T1\n\n## Status: in-queue\n", encoding="utf-8")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-epic/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert not any("orphan" in e.lower() for e in errors)

    def test_invalid_dependency_id_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-queue")
        # T2 depends on T1 but T1 is not in the index
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T2 | Task 2 | Task | in-queue | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "depend" in e.lower() for e in errors)

    def test_work_unit_file_missing_status_line_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Bug fix: work unit file with no ## Status: line must produce an error, not be silently skipped."""
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text("# E0-F1-S1-T1: Task\n\n## Description\nNo status line here.\n", encoding="utf-8")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "status" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Content quality validation (checks 6-10)
# ---------------------------------------------------------------------------


class TestValidateContent:
    """Tests for work unit content quality validation (checks 6-10)."""

    def _make_index(self, tmp_path: Path, rows: str) -> Path:
        idx = tmp_path / "BACKLOG.md"
        idx.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n" + rows,
            encoding="utf-8",
        )
        return idx

    def _make_task(self, backlog_dir: Path, unit_id: str, content: str) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(content, encoding="utf-8")
        return wu

    def test_task_missing_description_section_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n- **Repo:** `org/repo`\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "Description" in e for e in errors)

    def test_task_empty_description_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\n## Dependencies\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "Description" in e and "empty" in e.lower() for e in errors)

    def test_task_missing_acceptance_criteria_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\nDo something.\n\n## Dependencies\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "Acceptance Criteria" in e for e in errors)

    def test_task_acceptance_criteria_without_ac_items_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\nDo something.\n\n"
            "## Acceptance Criteria\n\nNo checklist items here.\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "AC-" in e for e in errors)

    def test_task_missing_changes_manifest_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\nDo something.\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Something\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "Changes Manifest" in e for e in errors)

    def test_task_missing_definition_of_done_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\nDo something.\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Something\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "Definition of Done" in e for e in errors)

    def test_task_with_em_dash_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\nDo something \u2014 with em-dash.\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Something\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] All ACs checked\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "em-dash" in e.lower() for e in errors)

    def test_valid_task_passes_content_checks(self, tmp_path: Path, backlog_dir: Path) -> None:
        self._make_task(
            backlog_dir,
            "E0-F1-S1-T1",
            "# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Repo:** `org/repo`\n\n## Description\n\nDo something real.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Something testable\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `src/f.py` | New |\n| `tests/unit/test_f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] All ACs checked\n\n"
            "## TDD Cycle Log\n\n## Comments\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert errors == []

    def test_epic_skips_content_checks(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Non-task files (Epic, Feature, Story) skip content quality checks."""
        self._make_task(
            backlog_dir,
            "E0",
            "# E0\n\n## Status: in-queue\n\n## Description\n\nEpic summary.\n",
        )
        idx = self._make_index(
            tmp_path,
            "| E0 | Epic | Epic | in-queue | none | repo | `backlog/E0.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        # Epic should NOT be flagged for missing AC, Changes Manifest, etc.
        assert not any("Acceptance Criteria" in e for e in errors)
        assert not any("Changes Manifest" in e for e in errors)
        assert not any("Definition of Done" in e for e in errors)


# ---------------------------------------------------------------------------
# Check 11: Changes Manifest paths must be repo-relative (no checkout_directory prefix)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateManifestPathPrefix:
    """Tests for check 11 -- reject Changes Manifest paths that begin with
    a ``checkout_directory/`` prefix. ``assert_staged_matches_manifest``
    compares against repo-relative ``git diff --name-only`` output, so a
    ``checkout_directory`` prefix on a manifest path is a guaranteed miss
    at git-ops time. Surfacing it at ``validate-backlog`` time catches the
    drafting defect before any executor cycle spends work on it."""

    def _make_index(self, tmp_path: Path, rows: str) -> Path:
        idx = tmp_path / "BACKLOG.md"
        idx.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n" + rows,
            encoding="utf-8",
        )
        return idx

    def _make_task(
        self,
        backlog_dir: Path,
        unit_id: str,
        repo: str,
        manifest_rows: str,
    ) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(
            f"# {unit_id}\n\n"
            f"## Status: in-queue\n\n"
            f"## Target Repository\n\n"
            f"- **Repo:** `{repo}`\n\n"
            f"## Description\n\nTest task.\n\n"
            f"## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            f"## Changes Manifest\n\n"
            f"| File | Change |\n"
            f"|------|--------|\n"
            f"{manifest_rows}\n"
            f"## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        return wu

    def _run_validate(
        self,
        tmp_path: Path,
        runtime_config: RuntimeConfig,
    ) -> list[str]:
        idx = tmp_path / "BACKLOG.md"
        with patch("devbench.config.RUNTIME_CONFIG", runtime_config):
            return BacklogManager().validate(idx, tmp_path)

    def test_manifest_without_checkout_prefix_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Repo-relative manifest path with configured checkout_directory: no error."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `README.md` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        assert not any("Changes Manifest path" in e and "begins with" in e for e in errors)

    def test_manifest_with_checkout_prefix_fails(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Manifest path starting with checkout_directory prefix: one error,
        message quotes the offending path, the prefix, and names the doc."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `example-repo/README.md` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        prefix_errors = [e for e in errors if "Changes Manifest path" in e and "begins with" in e]
        assert len(prefix_errors) == 1
        assert "EX-F1-S1-T1" in prefix_errors[0]
        assert "'example-repo/README.md'" in prefix_errors[0]
        assert "'example-repo/'" in prefix_errors[0]
        assert "docs/backlog-contract.md" in prefix_errors[0]

    def test_check_skipped_when_checkout_directory_unset(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Repo without checkout_directory: the check does not apply, regardless of manifest path."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `anything/goes/here.py` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory=None)})
        errors = self._run_validate(tmp_path, rt_cfg)
        assert not any("Changes Manifest path" in e and "begins with" in e for e in errors)

    def test_multiple_repos_different_checkout_dirs(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Each WU checked against its own repo's prefix; no cross-repo contamination."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "org-a/repo-a",
            "| `repo-a/file.py` | update |\n",
        )
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T2",
            "org-b/repo-b",
            "| `repo-a/file.py` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | org-a/repo-a | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | Task | Task | in-queue | none | org-b/repo-b | `backlog/EX-F1-S1-T2.md` |\n",
        )
        rt_cfg = RuntimeConfig(
            repos={
                "org-a/repo-a": RepoConfig(checkout_directory="repo-a"),
                "org-b/repo-b": RepoConfig(checkout_directory="repo-b"),
            }
        )
        errors = self._run_validate(tmp_path, rt_cfg)
        prefix_errors = [e for e in errors if "Changes Manifest path" in e and "begins with" in e]
        # WU1's path is `repo-a/file.py`, matches its own prefix -> flagged.
        # WU2's path is also `repo-a/file.py` but WU2's prefix is `repo-b/` -> NOT flagged.
        assert len(prefix_errors) == 1
        assert "EX-F1-S1-T1" in prefix_errors[0]
        assert "EX-F1-S1-T2" not in prefix_errors[0]

    def test_nested_prefix_path_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Deeply nested paths under the prefix are also flagged."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `example-repo/src/nested/deeper/file.py` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        prefix_errors = [e for e in errors if "Changes Manifest path" in e and "begins with" in e]
        assert len(prefix_errors) == 1
        assert "'example-repo/src/nested/deeper/file.py'" in prefix_errors[0]

    def test_empty_manifest_section_does_not_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Work unit with only the Changes Manifest header + blank body: check does not error
        (other checks handle missing-entries)."""
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `example-org/example-repo`\n\n"
            "## Description\n\nTask.\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            "## Changes Manifest\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        assert not any("Changes Manifest path" in e and "begins with" in e for e in errors)

    def test_work_unit_for_unknown_repo_skipped(self, tmp_path: Path, backlog_dir: Path) -> None:
        """If the WU's repo isn't in runtime_config.repos, the check skips it."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "unknown-org/unknown-repo",
            "| `unknown-repo/file.py` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | unknown-org/unknown-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        assert not any("Changes Manifest path" in e and "begins with" in e for e in errors)

    def test_error_message_is_actionable(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Error string must name the WU id, quote the offending path and the prefix,
        and point at the doc that explains the rule."""
        self._make_task(
            backlog_dir,
            "EX-F2-S3-T4",
            "example-org/example-repo",
            "| `example-repo/CONTRIBUTING.md` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F2-S3-T4 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F2-S3-T4.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        prefix_errors = [e for e in errors if "Changes Manifest path" in e and "begins with" in e]
        assert len(prefix_errors) == 1
        err = prefix_errors[0]
        assert "EX-F2-S3-T4" in err
        assert "'example-repo/CONTRIBUTING.md'" in err
        assert "'example-repo/'" in err
        assert "repo-relative" in err
        assert "docs/backlog-contract.md" in err

    def test_check_runs_on_every_work_unit_file(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Three WUs on disk, only the middle one has the defect; errors list has exactly
        one entry and it names only WU2."""
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `README.md` | update |\n",
        )
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T2",
            "example-org/example-repo",
            "| `example-repo/docs/bad.md` | update |\n",
        )
        self._make_task(
            backlog_dir,
            "EX-F1-S1-T3",
            "example-org/example-repo",
            "| `src/good.py` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | "
            "`backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | Task | Task | in-queue | none | example-org/example-repo | "
            "`backlog/EX-F1-S1-T2.md` |\n"
            "| EX-F1-S1-T3 | Task | Task | in-queue | none | example-org/example-repo | "
            "`backlog/EX-F1-S1-T3.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        errors = self._run_validate(tmp_path, rt_cfg)
        prefix_errors = [e for e in errors if "Changes Manifest path" in e and "begins with" in e]
        assert len(prefix_errors) == 1
        assert "EX-F1-S1-T2" in prefix_errors[0]
        assert "EX-F1-S1-T1" not in prefix_errors[0]
        assert "EX-F1-S1-T3" not in prefix_errors[0]


# ---------------------------------------------------------------------------
# validate() --fix mode (auto-correct rule-10 and rule-11 violations)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateFix:
    """Tests for BacklogManager.validate(fix=True) auto-correction mode.

    Four contract pins:
    1. fix=True strips checkout_directory prefix from manifest paths (rule-11).
    2. fix=True replaces em-dash characters with '--' (rule-10).
    3. fix=True appends an audit comment with a timestamp to the corrected file.
    4. fix=False (default) leaves the file unchanged (read-only).
    """

    _INDEX_HEADER = (
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n"
        "\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
    )

    def _make_index(self, tmp_path: Path, rows: str) -> Path:
        idx = tmp_path / "BACKLOG.md"
        idx.write_text(self._INDEX_HEADER + rows, encoding="utf-8")
        return idx

    def _make_task(
        self,
        backlog_dir: Path,
        unit_id: str,
        repo: str,
        manifest_rows: str,
        description: str = "Test task.",
    ) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(
            f"# {unit_id}\n\n"
            f"## Status: in-queue\n\n"
            f"## Target Repository\n\n"
            f"- **Repo:** `{repo}`\n\n"
            f"## Description\n\n{description}\n\n"
            f"## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            f"## Changes Manifest\n\n"
            f"| File | Change |\n"
            f"|------|--------|\n"
            f"{manifest_rows}\n"
            f"## Definition of Done\n\n- [ ] Done\n\n"
            f"## Comments\n",
            encoding="utf-8",
        )
        return wu

    def test_fix_strips_checkout_directory_prefix_from_manifest_paths(self, tmp_path: Path, backlog_dir: Path) -> None:
        """fix=True removes the checkout_directory prefix from manifest paths (rule-11)."""
        wu = self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `example-repo/src/foo.py` | update |\n",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        idx = tmp_path / "BACKLOG.md"
        with patch("devbench.config.RUNTIME_CONFIG", rt_cfg):
            errors = BacklogManager().validate(idx, tmp_path, fix=True)

        prefix_errors = [e for e in errors if "Changes Manifest path" in e and "begins with" in e]
        assert prefix_errors == [], f"Expected no prefix errors after fix, got: {prefix_errors}"

        content = wu.read_text(encoding="utf-8")
        assert "`example-repo/src/foo.py`" not in content
        assert "`src/foo.py`" in content

    def test_fix_replaces_em_dash_characters(self, tmp_path: Path, backlog_dir: Path) -> None:
        """fix=True replaces U+2014 em-dash characters with '--' (rule-10)."""
        wu = self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `src/foo.py` | update |\n",
            description="Do something — with em-dash.",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        idx = tmp_path / "BACKLOG.md"
        errors = BacklogManager().validate(idx, tmp_path, fix=True)

        em_errors = [e for e in errors if "em-dash" in e.lower()]
        assert em_errors == [], f"Expected no em-dash errors after fix, got: {em_errors}"

        content = wu.read_text(encoding="utf-8")
        assert "—" not in content, "U+2014 em-dash must be replaced after fix"
        assert "Do something -- with em-dash." in content

    def test_fix_appends_audit_comment_with_timestamp(self, tmp_path: Path, backlog_dir: Path) -> None:
        """fix=True appends a [VALIDATE_FIX] audit comment with a timestamp."""
        wu = self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `src/foo.py` | update |\n",
            description="Fix — this em-dash.",
        )
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        idx = tmp_path / "BACKLOG.md"
        BacklogManager().validate(idx, tmp_path, fix=True)

        content = wu.read_text(encoding="utf-8")
        assert "[VALIDATE_FIX]" in content, "Audit comment must be appended after fix"
        assert "rule-10" in content, "Audit comment must name the corrected rule"
        import re

        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", content), (
            "Audit comment must include a timestamp in YYYY-MM-DD HH:MM UTC format"
        )

    def test_validate_without_fix_is_read_only(self, tmp_path: Path, backlog_dir: Path) -> None:
        """fix=False (default) does not modify work-unit files -- validate remains read-only."""
        wu = self._make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "example-org/example-repo",
            "| `src/foo.py` | update |\n",
            description="Keep — the em-dash.",
        )
        original_content = wu.read_text(encoding="utf-8")
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        idx = tmp_path / "BACKLOG.md"
        errors = BacklogManager().validate(idx, tmp_path)

        assert any("em-dash" in e.lower() for e in errors), "em-dash violation must be reported without --fix"

        assert wu.read_text(encoding="utf-8") == original_content, (
            "validate() without fix=True must not modify any work-unit files"
        )

    def test_fix_skips_missing_work_unit_file(self, tmp_path: Path, backlog_dir: Path) -> None:
        """fix=True silently skips rows whose work-unit files do not exist on disk."""
        self._make_index(
            tmp_path,
            "| EX-F1-S1-T1 | Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        idx = tmp_path / "BACKLOG.md"
        errors = BacklogManager().validate(idx, tmp_path, fix=True)
        assert any("missing" in e.lower() for e in errors), "Missing file must still be reported even when fix=True"

    def test_fix_append_fix_audit_no_op_when_no_audit_lines(self) -> None:
        """_append_fix_audit returns content unchanged when audit_lines is empty."""
        content = "# Task\n\n## Status: in-queue\n"
        result = BacklogManager._append_fix_audit(content, "2026-01-01 00:00 UTC", [])
        assert result == content

    def test_fix_append_fix_audit_creates_comments_section_if_absent(self) -> None:
        """_append_fix_audit creates '## Comments' section when it is not already present."""
        content = "# Task\n\n## Status: in-queue\n"
        result = BacklogManager._append_fix_audit(content, "2026-01-01 00:00 UTC", ["[VALIDATE_FIX] rule-10"])
        assert "## Comments" in result
        assert "[VALIDATE_FIX] rule-10" in result

    def test_fix_manifest_prefixes_no_op_when_checkout_dir_unset(self) -> None:
        """_fix_manifest_prefixes returns unchanged content when checkout_directory is None."""
        content = "## Target Repository\n\n- **Repo:** `org/repo`\n"
        rt_cfg = RuntimeConfig(repos={"org/repo": RepoConfig(checkout_directory=None)})
        with patch("devbench.config.RUNTIME_CONFIG", rt_cfg):
            result, count = BacklogManager._fix_manifest_prefixes(content, [])
        assert result == content
        assert count == 0

    def test_fix_manifest_prefixes_no_op_when_no_prefix_match(self) -> None:
        """_fix_manifest_prefixes returns unchanged content when no manifest paths carry the prefix."""
        content = (
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Changes Manifest\n\n| File | Change |\n|---|---|\n| `src/foo.py` | update |\n"
        )
        rt_cfg = RuntimeConfig(repos={"org/repo": RepoConfig(checkout_directory="repo")})
        with patch("devbench.config.RUNTIME_CONFIG", rt_cfg):
            result, count = BacklogManager._fix_manifest_prefixes(content, [])
        assert result == content
        assert count == 0

    def test_fix_manifest_prefixes_no_op_when_parse_raises(self) -> None:
        """_fix_manifest_prefixes returns unchanged content when parse_manifest raises
        (e.g. the Changes Manifest section is absent from the file)."""
        content = "## Target Repository\n\n- **Repo:** `org/repo`\n\n## Description\n\nno manifest here\n"
        rt_cfg = RuntimeConfig(repos={"org/repo": RepoConfig(checkout_directory="repo")})
        with patch("devbench.config.RUNTIME_CONFIG", rt_cfg):
            result, count = BacklogManager._fix_manifest_prefixes(content, [])
        assert result == content
        assert count == 0


# ---------------------------------------------------------------------------
# E4-F1-S1-T1: Rename BacklogManagerJudge -> BacklogManager
# ---------------------------------------------------------------------------


class TestBacklogManagerRename:
    """AC tests for E4-F1-S1-T1: class rename and reference cleanup."""

    def test_backlog_manager_symbol_exists(self) -> None:
        """AC-1: BacklogManager class is importable with expected public interface."""
        import inspect

        from devbench.backlog.manager import BacklogManager

        assert inspect.isclass(BacklogManager)
        assert callable(getattr(BacklogManager, "force_status", None)), "force_status must be present"
        assert callable(getattr(BacklogManager, "validate", None)), "validate must be present"

    def test_no_backlog_manager_judge_symbol_in_src(self) -> None:
        """AC-5: No BacklogManagerJudge references in the changed source files."""
        import importlib.util
        from pathlib import Path

        old_name = "BacklogManagerJudge"
        spec = importlib.util.find_spec("devbench")
        assert spec is not None and spec.origin is not None
        src_root = Path(spec.origin).parent
        checked = [
            src_root / "backlog" / "manager.py",
            src_root / "cli.py",
        ]
        matches = [str(p) for p in checked if old_name in p.read_text(encoding="utf-8")]
        assert not matches, f"{old_name} still found in: {matches}"

    def test_cli_imports_backlog_manager(self) -> None:
        """AC-3: cli.py source contains BacklogManager import, not BacklogManagerJudge."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("devbench")
        assert spec is not None and spec.origin is not None
        src = (Path(spec.origin).parent / "cli.py").read_text(encoding="utf-8")
        assert "BacklogManager" in src, "cli.py must import BacklogManager"
        assert "BacklogManagerJudge" not in src, "cli.py must not reference BacklogManagerJudge"

    def test_backlog_manager_set_status_behavior_unchanged(self, tmp_path: Path) -> None:
        """AC-6: force_status writes exact status to both files after rename."""
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text("# Task\n\n## Status: in-progress\n")
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "| ID | Title | Type | Status | Deps | Repo | File Path |\n"
            "|---|---|---|---|---|---|---|\n"
            "| E0-F1-S1-T1 | Task 1 | Task | in-progress | none | repo | `E0-F1-S1-T1.md` |\n"
        )
        mgr = BacklogManager()
        mgr.force_status(wu, index, "E0-F1-S1-T1", "in-review")
        assert "## Status: in-review" in wu.read_text(), "work-unit status line must be updated"
        assert "in-review" in index.read_text(), "backlog index row must be updated"
        # invalid status still raises
        with pytest.raises(ValueError, match="Invalid status"):
            mgr.force_status(wu, index, "E0-F1-S1-T1", "not-a-status")

    def test_backlog_manager_validate_behavior_unchanged(self, tmp_path: Path) -> None:
        """AC-6: validate returns error for missing work-unit file after rename."""
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "| ID | Title | Type | Status | Deps | Repo | File Path |\n"
            "|---|---|---|---|---|---|---|\n"
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/missing.md` |\n"
        )
        mgr = BacklogManager()
        errors = mgr.validate(index, tmp_path)
        assert errors, "validate must return at least one error for a missing file"
        assert any("E0-F1-S1-T1" in e for e in errors), f"error must mention the unit ID; got: {errors}"

    def test_backlog_manager_logger_injectable(self) -> None:
        """BacklogManager accepts an injected logger instead of creating its own."""
        import logging

        custom_logger = logging.getLogger("test.custom")
        mgr = BacklogManager(logger=custom_logger)
        assert mgr.logger is custom_logger, "injected logger must be used, not the default"

    def test_backlog_manager_default_logger(self) -> None:
        """BacklogManager creates a default logger when none is injected."""
        import logging

        mgr = BacklogManager()
        assert isinstance(mgr.logger, logging.Logger), "default logger must be a logging.Logger"
        assert mgr.logger.name == "devbench.backlog_manager", f"default logger name wrong: {mgr.logger.name}"

    def test_backlog_manager_has_no_evaluate_method(self) -> None:
        """BacklogManager must not expose evaluate() -- judge interface removed."""
        assert not hasattr(BacklogManager, "evaluate"), (
            "BacklogManager must not have evaluate(); judge interface was intentionally removed"
        )


# ---------------------------------------------------------------------------
# E226-F1-S1-T1: Status Summary table and _update_status_summary
# ---------------------------------------------------------------------------

_BACKLOG_WITH_SUMMARY_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
{summary_rows}
## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
{index_rows}
"""

_BACKLOG_WITHOUT_SUMMARY = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E0 | Tooling | Epic | in-queue | None | repo | `backlog/E0.md` |
| E0-F1-S1-T1 | Task 1 | Task | done | None | repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Task 2 | Task | in-queue | None | repo | `backlog/E0-F1-S1-T2.md` |
"""


def _make_backlog_with_epics(tmp_path: Path, backlog_dir: Path) -> tuple[Path, dict[str, Path]]:
    """Create a BACKLOG.md with a known epic/task structure and corresponding WU files.

    Returns (index_path, {unit_id: wu_file_path}).
    """
    content = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|----------|\n"
        "| E1 | Epic One | Epic | in-queue | None | repo | `backlog/E1.md` |\n"
        "| E1-F1-S1-T1 | Task A | Task | done | None | repo | `backlog/E1-F1-S1-T1.md` |\n"
        "| E1-F1-S1-T2 | Task B | Task | in-progress | None | repo | `backlog/E1-F1-S1-T2.md` |\n"
        "| E1-F1-S1-T3 | Task C | Task | in-queue | None | repo | `backlog/E1-F1-S1-T3.md` |\n"
        "| E2 | Epic Two | Epic | in-queue | None | repo | `backlog/E2.md` |\n"
        "| E2-F1-S1-T1 | Task D | Task | blocked | None | repo | `backlog/E2-F1-S1-T1.md` |\n"
    )
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(content, encoding="utf-8")

    files: dict[str, Path] = {}
    units = [
        ("E1", "in-queue"),
        ("E1-F1-S1-T1", "done"),
        ("E1-F1-S1-T2", "in-progress"),
        ("E1-F1-S1-T3", "in-queue"),
        ("E2", "in-queue"),
        ("E2-F1-S1-T1", "blocked"),
    ]
    for uid, status in units:
        wu = backlog_dir / f"{uid}.md"
        wu.write_text(f"# {uid}\n\n## Status: {status}\n", encoding="utf-8")
        files[uid] = wu

    return index_path, files


class TestUpdateStatusSummary:
    """Tests for BacklogManager._update_status_summary() -- AC-2."""

    def test_method_exists(self) -> None:
        """AC-2: _update_status_summary method must exist on BacklogManager."""
        assert hasattr(BacklogManager, "_update_status_summary"), (
            "BacklogManager must have _update_status_summary method"
        )
        assert callable(BacklogManager._update_status_summary)

    def test_rewrites_summary_table_when_already_present(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-2: _update_status_summary rewrites the Status Summary section in place."""
        index_path, _ = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()

        # Inject a stale summary section
        content = index_path.read_text(encoding="utf-8")
        stale_summary = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|----------|\n"
            "| E1 | OLD | 0 | 0 | 0 | 0 |\n\n"
        )
        index_path.write_text(stale_summary + content, encoding="utf-8")

        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        # Summary section must be present
        assert "## Status Summary" in result
        # Extract only the summary section rows
        summary_rows = _extract_summary_lines(result)
        e1_lines = [l for l in summary_rows if "| E1 |" in l]
        e2_lines = [l for l in summary_rows if "| E2 |" in l]
        assert len(e1_lines) == 1, f"Expected exactly 1 E1 summary row, got: {e1_lines}"
        assert len(e2_lines) == 1, f"Expected exactly 1 E2 summary row, got: {e2_lines}"
        e1_line = e1_lines[0]
        cells = [c.strip() for c in e1_line.split("|")]
        # cells[0]='', cells[1]=epic, cells[2]=title
        # cells[3]=done, cells[4]=in-prog, cells[5]=in-queue, cells[6]=blocked
        assert cells[3] == "1", f"E1 done count wrong: {e1_line}"
        assert cells[4] == "1", f"E1 in-progress count wrong: {e1_line}"
        assert cells[5] == "1", f"E1 in-queue count wrong: {e1_line}"
        assert cells[6] == "0", f"E1 blocked count wrong: {e1_line}"

    def test_creates_summary_section_when_absent(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-2: _update_status_summary inserts the Status Summary section when not present."""
        index_path, _ = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()

        # No summary section in the index
        assert "## Status Summary" not in index_path.read_text(encoding="utf-8")

        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        assert "## Status Summary" in result
        assert "| E1 |" in result
        assert "| E2 |" in result

    def test_counts_only_descendant_rows_not_epic_itself(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-2: counts reflect descendant rows; the epic-level row itself is not double-counted."""
        index_path, _ = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()
        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        summary_rows = _extract_summary_lines(result)
        e2_lines = [l for l in summary_rows if "| E2 |" in l]
        assert len(e2_lines) == 1, f"Expected exactly 1 E2 summary row, got: {e2_lines}"
        e2_line = e2_lines[0]
        cells = [c.strip() for c in e2_line.split("|")]
        # E2 has only one descendant: E2-F1-S1-T1 with status blocked
        assert cells[6] == "1", f"E2 blocked count wrong: {e2_line}"
        # Done, in-progress, in-queue should be 0
        assert cells[3] == "0", f"E2 done count should be 0: {e2_line}"


class TestSetStatusCallsUpdateStatusSummary:
    """Tests that _set_status calls _update_status_summary -- AC-3."""

    def test_set_status_updates_summary_table(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-3: After force_status, the Status Summary table reflects the new status."""
        index_path, files = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()

        # Prime the summary by calling it once
        mgr._update_status_summary(index_path)

        # Now change T3 from in-queue to done via force_status
        mgr.force_status(files["E1-F1-S1-T3"], index_path, "E1-F1-S1-T3", "done")

        result = index_path.read_text(encoding="utf-8")
        summary_rows = _extract_summary_lines(result)
        e1_lines = [l for l in summary_rows if "| E1 |" in l]
        assert len(e1_lines) == 1, "Summary table must have exactly one E1 row"
        e1_line = e1_lines[0]
        cells = [c.strip() for c in e1_line.split("|")]
        # E1 should now have 2 done (T1 + T3), 1 in-progress (T2), 0 in-queue, 0 blocked
        assert cells[3] == "2", f"E1 done should be 2 after marking T3 done: {e1_line}"
        assert cells[5] == "0", f"E1 in-queue should be 0 after marking T3 done: {e1_line}"


class TestValidateBacklogSummary:
    """Tests for validate-backlog Status Summary checks -- AC-4."""

    def _make_backlog_no_summary(self, tmp_path: Path, backlog_dir: Path) -> tuple[Path, dict[str, Path]]:
        """Return an index without a Status Summary section."""
        return _make_backlog_with_epics(tmp_path, backlog_dir)

    def _make_backlog_with_correct_summary(self, tmp_path: Path, backlog_dir: Path) -> tuple[Path, dict[str, Path]]:
        """Return an index with a correct Status Summary section."""
        index_path, files = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()
        mgr._update_status_summary(index_path)
        return index_path, files

    def _make_backlog_with_wrong_summary(self, tmp_path: Path, backlog_dir: Path) -> tuple[Path, dict[str, Path]]:
        """Return an index with a Status Summary section that has wrong counts."""
        index_path, files = _make_backlog_with_epics(tmp_path, backlog_dir)
        content = index_path.read_text(encoding="utf-8")
        wrong_summary = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|----------|\n"
            "| E1 | Epic One | 99 | 99 | 99 | 99 |\n"
            "| E2 | Epic Two | 99 | 99 | 99 | 99 |\n\n"
        )
        index_path.write_text(wrong_summary + content, encoding="utf-8")
        return index_path, files

    def test_validate_fails_when_summary_table_missing(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-4: validate reports an error when ## Status Summary section is absent."""
        index_path, _ = self._make_backlog_no_summary(tmp_path, backlog_dir)
        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)
        assert any("status summary" in e.lower() for e in errors), f"Expected status summary error, got: {errors}"

    def test_validate_fails_when_summary_counts_mismatch(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-4: validate reports an error when summary counts don't match index."""
        index_path, _ = self._make_backlog_with_wrong_summary(tmp_path, backlog_dir)
        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)
        assert any("status summary" in e.lower() or "mismatch" in e.lower() for e in errors), (
            f"Expected status summary mismatch error, got: {errors}"
        )

    def test_validate_passes_when_summary_matches_index(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-4: validate returns no summary errors when counts are correct."""
        index_path, _ = self._make_backlog_with_correct_summary(tmp_path, backlog_dir)
        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)
        summary_errors = [e for e in errors if "status summary" in e.lower()]
        assert not summary_errors, f"Unexpected status summary errors: {summary_errors}"


# ---------------------------------------------------------------------------
# E230-F1-S1-T1: _append_agent_comment and _resolve_unit_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAppendAgentComment:
    """AC-6: BacklogManager._append_agent_comment writes COMMENT_AGENT_TEMPLATE format."""

    def test_append_agent_comment_writes_agent_template_format(self, tmp_path: Path) -> None:
        """_append_agent_comment appends '[timestamp] [agent/<name>] <message>' to Comments."""
        wu = tmp_path / "E230-F1-S1-T1.md"
        wu.write_text(
            "# E230-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n",
            encoding="utf-8",
        )
        mgr = BacklogManager()
        mgr._append_agent_comment(wu, "git_ops", "[PR_CREATED] https://github.com/org/repo/pull/42")

        content = wu.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content
        assert "[PR_CREATED] https://github.com/org/repo/pull/42" in content

    def test_append_agent_comment_creates_comments_section_when_absent(self, tmp_path: Path) -> None:
        """_append_agent_comment creates ## Comments section when not present."""
        wu = tmp_path / "E230-F1-S1-T1.md"
        wu.write_text("# E230-F1-S1-T1\n\n## Status: in-progress\n", encoding="utf-8")
        mgr = BacklogManager()
        mgr._append_agent_comment(wu, "orchestrator", "[DONE] Work unit E230-F1-S1-T1 completed")

        content = wu.read_text(encoding="utf-8")
        assert "## Comments" in content
        assert "[agent/orchestrator]" in content
        assert "[DONE] Work unit E230-F1-S1-T1 completed" in content

    def test_append_agent_comment_contains_no_review_token(self, tmp_path: Path) -> None:
        """AC-5: Event entries contain no [REVIEW_PASS] or [REVIEW_FAIL] token."""
        wu = tmp_path / "E230-F1-S1-T1.md"
        wu.write_text("# E230-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")
        mgr = BacklogManager()
        mgr._append_agent_comment(wu, "git_ops", "[PR_MERGED] https://github.com/org/repo/pull/42")

        content = wu.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" not in content
        assert "[REVIEW_FAIL]" not in content

    def test_append_agent_comment_includes_timestamp(self, tmp_path: Path) -> None:
        """_append_agent_comment includes a UTC timestamp in the entry."""
        wu = tmp_path / "E230-F1-S1-T1.md"
        wu.write_text("# E230-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")
        mgr = BacklogManager()
        mgr._append_agent_comment(wu, "git_ops", "[PR_CREATED] https://github.com/org/repo/pull/1")

        content = wu.read_text(encoding="utf-8")
        # Timestamp format: [YYYY-MM-DD HH:MM UTC]
        import re

        assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\]", content), f"No UTC timestamp found in: {content}"


# ---------------------------------------------------------------------------
# E231-F2-S1-T1: _append_tdd_entry and log-tdd command
# ---------------------------------------------------------------------------

_WORK_UNIT_WITH_TDD_LOG = """\
# E231-F2-S1-T1: TDD Test

## Status: in-progress

## Comments

## TDD Cycle Log
"""


@pytest.mark.unit
class TestAppendTddEntry:
    """Tests for BacklogManager._append_tdd_entry() -- AC-1 through AC-6."""

    def _make_wu_with_tdd_section(self, tmp_path: Path, extra: str = "") -> Path:
        """Create a work unit file with both ## Comments and ## TDD Cycle Log sections."""
        wu = tmp_path / "E231-F2-S1-T1.md"
        wu.write_text(_WORK_UNIT_WITH_TDD_LOG + extra, encoding="utf-8")
        return wu

    def _make_wu_without_tdd_section(self, tmp_path: Path) -> Path:
        """Create a work unit file WITHOUT ## TDD Cycle Log section."""
        wu = tmp_path / "E231-F2-S1-T1-no-tdd.md"
        wu.write_text(
            "# E231-F2-S1-T1\n\n## Status: in-progress\n\n## Comments\n",
            encoding="utf-8",
        )
        return wu

    def test_log_tdd_red_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-1: RED entry appears in ## TDD Cycle Log section."""
        wu = self._make_wu_with_tdd_section(tmp_path)
        mgr = BacklogManager()
        mgr._append_tdd_entry(
            wu,
            "RED",
            "Tests: tests/test_foo.py. Command: make test-unit. Exit: 1. Failures: 2 failed.",
        )

        content = wu.read_text(encoding="utf-8")
        tdd_section_start = content.find("## TDD Cycle Log")
        assert tdd_section_start != -1, "## TDD Cycle Log section must exist"
        tdd_section = content[tdd_section_start:]
        assert "[RED]" in tdd_section, f"[RED] tag not found in TDD Cycle Log: {tdd_section}"
        assert "make test-unit" in tdd_section

    def test_log_tdd_green_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-2: GREEN entry appears in ## TDD Cycle Log section."""
        wu = self._make_wu_with_tdd_section(tmp_path)
        mgr = BacklogManager()
        mgr._append_tdd_entry(
            wu,
            "GREEN",
            "Command: make test-unit. Result: 5 passed, 0 failed. Files changed: src/foo.py",
        )

        content = wu.read_text(encoding="utf-8")
        tdd_section_start = content.find("## TDD Cycle Log")
        tdd_section = content[tdd_section_start:]
        assert "[GREEN]" in tdd_section, f"[GREEN] tag not found in TDD Cycle Log: {tdd_section}"

    def test_log_tdd_refactor_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-3: REFACTOR entry appears in ## TDD Cycle Log section."""
        wu = self._make_wu_with_tdd_section(tmp_path)
        mgr = BacklogManager()
        mgr._append_tdd_entry(wu, "REFACTOR", "No refactor needed. Tests: 5 passed, 0 failed")

        content = wu.read_text(encoding="utf-8")
        tdd_section_start = content.find("## TDD Cycle Log")
        tdd_section = content[tdd_section_start:]
        assert "[REFACTOR]" in tdd_section, f"[REFACTOR] tag not found in TDD Cycle Log: {tdd_section}"

    def test_entry_format_matches_spec(self, tmp_path: Path) -> None:
        """AC-5: Entry matches '- [<PHASE>] <ISO-8601 timestamp> -- <message>'."""
        import re

        wu = self._make_wu_with_tdd_section(tmp_path)
        mgr = BacklogManager()
        mgr._append_tdd_entry(wu, "RED", "some test message")

        content = wu.read_text(encoding="utf-8")
        # Pattern: - [RED] <ISO-8601 datetime> -- <message>
        # Double-hyphen separator required: em-dash is rejected by validate-backlog.
        pattern = r"- \[RED\] \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^ ]* -- some test message"
        assert re.search(pattern, content), f"Entry format does not match expected pattern in: {content}"

    def test_entry_appears_in_tdd_section_not_comments(self, tmp_path: Path) -> None:
        """AC-11: TDD entries do not appear in ## Comments section."""
        wu = self._make_wu_with_tdd_section(tmp_path)
        mgr = BacklogManager()
        mgr._append_tdd_entry(wu, "RED", "unique-tdd-marker-123")

        content = wu.read_text(encoding="utf-8")
        comments_start = content.find("## Comments")
        tdd_start = content.find("## TDD Cycle Log")

        # Extract the Comments section (between ## Comments and ## TDD Cycle Log)
        comments_section = content[comments_start:tdd_start] if tdd_start > comments_start else content[comments_start:]
        assert "unique-tdd-marker-123" not in comments_section, (
            f"TDD entry leaked into ## Comments section: {comments_section}"
        )

    def test_missing_tdd_section_raises_value_error(self, tmp_path: Path) -> None:
        """AC-6: Raises ValueError when ## TDD Cycle Log section does not exist."""
        wu = self._make_wu_without_tdd_section(tmp_path)
        mgr = BacklogManager()
        with pytest.raises(ValueError, match="TDD Cycle Log"):
            mgr._append_tdd_entry(wu, "RED", "some message")

    def test_multiple_entries_are_appended_in_order(self, tmp_path: Path) -> None:
        """Multiple TDD entries are appended sequentially within the section."""
        wu = self._make_wu_with_tdd_section(tmp_path)
        mgr = BacklogManager()
        mgr._append_tdd_entry(wu, "RED", "first-red-entry")
        mgr._append_tdd_entry(wu, "GREEN", "second-green-entry")

        content = wu.read_text(encoding="utf-8")
        red_pos = content.find("[RED]")
        green_pos = content.find("[GREEN]")
        assert red_pos < green_pos, "RED entry must appear before GREEN entry"
        assert "first-red-entry" in content
        assert "second-green-entry" in content

    def test_tdd_entry_has_no_emdash(self, tmp_path: Path) -> None:
        """Regression guard: TDD_ENTRY_TEMPLATE must not emit em-dash into work unit files.

        The validate-backlog check (manager.py Check 10) rejects work unit files
        containing U+2014, so a writer that produces em-dash blocks the
        orchestrator loop on the next validate-backlog run.
        """
        wu = self._make_wu_with_tdd_section(tmp_path)
        BacklogManager()._append_tdd_entry(wu, "RED", "some failure detail")

        assert "\u2014" not in wu.read_text(encoding="utf-8")

    def test_append_tdd_entry_lands_inside_tdd_cycle_log_when_comments_follows(self, tmp_path: Path) -> None:
        """AC-FUNC-001/AC-FUNC-003/AC-CYCLE-001: Entries land between TDD Log and Comments.

        Builds a fixture with canonical section order (## TDD Cycle Log then
        ## Comments), calls _append_tdd_entry three times, and asserts:
        (a) all three entries appear between ## TDD Cycle Log and ## Comments,
        (b) ## Comments content is byte-for-byte unchanged,
        (c) no extra blank lines are introduced between successive entries.
        """
        # Canonical order: ## TDD Cycle Log comes before ## Comments.
        fixture = (
            "# E6-F1-S1-T1: Section-aware TDD Test\n\n"
            "## Status: in-progress\n\n"
            "## TDD Cycle Log\n\n"
            "<!-- entries go here -->\n\n"
            "## Comments\n\n"
            "<!-- comments go here -->\n"
        )
        wu = tmp_path / "E6-F1-S1-T1.md"
        wu.write_text(fixture, encoding="utf-8")

        comments_before = fixture[fixture.find("## Comments") :]

        mgr = BacklogManager()
        mgr._append_tdd_entry(wu, "RED", "red-marker-abc")
        mgr._append_tdd_entry(wu, "GREEN", "green-marker-def")
        mgr._append_tdd_entry(wu, "REFACTOR", "refactor-marker-ghi")

        content = wu.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        comments_start = content.find("## Comments")
        assert tdd_start != -1, "## TDD Cycle Log section must exist"
        assert comments_start != -1, "## Comments section must exist"
        assert tdd_start < comments_start, "## TDD Cycle Log must precede ## Comments"

        tdd_section = content[tdd_start:comments_start]
        assert "red-marker-abc" in tdd_section, f"RED entry not in TDD section: {tdd_section!r}"
        assert "green-marker-def" in tdd_section, f"GREEN entry not in TDD section: {tdd_section!r}"
        assert "refactor-marker-ghi" in tdd_section, f"REFACTOR entry not in TDD section: {tdd_section!r}"

        comments_after = content[comments_start:]
        assert comments_after == comments_before, (
            f"## Comments section was mutated.\nBefore: {comments_before!r}\nAfter: {comments_after!r}"
        )

        # No extra blank lines between successive entries: entry lines are separated
        # by at most one blank line (i.e., no triple-newline runs within the TDD section).
        assert "\n\n\n" not in tdd_section, f"Extra blank lines found in TDD section: {tdd_section!r}"

    def test_append_tdd_entry_appends_to_eof_when_tdd_log_is_last_section(self, tmp_path: Path) -> None:
        """AC-FUNC-002: Falls back to EOF append when ## TDD Cycle Log is the last section."""
        fixture = (
            "# E6-F1-S1-T1: EOF Fallback Test\n\n"
            "## Status: in-progress\n\n"
            "## TDD Cycle Log\n\n"
            "<!-- no sections after this -->\n"
        )
        wu = tmp_path / "E6-F1-S1-T1-eof.md"
        wu.write_text(fixture, encoding="utf-8")

        mgr = BacklogManager()
        mgr._append_tdd_entry(wu, "RED", "eof-marker-xyz")

        content = wu.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        entry_pos = content.find("eof-marker-xyz")
        assert entry_pos > tdd_start, "Entry must appear after ## TDD Cycle Log heading"
        # Must be at or near EOF -- no ## heading appears after the entry.
        trailing = content[entry_pos:]
        assert not any(line.startswith("## ") for line in trailing.splitlines()), (
            f"Unexpected ## heading found after EOF-appended entry: {trailing!r}"
        )

    def test_append_tdd_entry_validate_backlog_roundtrip_parity(self, tmp_path: Path) -> None:
        """AC-FUNC-004: validate() returns no errors after N _append_tdd_entry calls."""
        # Build a minimum-viable backlog workspace that validate() accepts.
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()

        unit_id = "E0-F1-S1-T1"
        wu_content = (
            f"# {unit_id}: Roundtrip Test\n\n"
            f"## Status: in-progress\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nRoundtrip fixture.\n\n"
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            "| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 fixture\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `src/f.py` | fixture |\n| `tests/unit/test_f.py` | fixture |\n\n"
            "## Definition of Done\n\n- [ ] All ACs checked\n\n"
            "## TDD Cycle Log\n\n"
            "<!-- entries go here -->\n\n"
            "## Comments\n\n"
            "<!-- comments go here -->\n"
        )
        wu_path = backlog_dir / f"{unit_id}.md"
        wu_path.write_text(wu_content, encoding="utf-8")

        index_content = (
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
            "|------|-------|------|-------------|----------|---------|----------|\n"
            "| E0 | Example | 0 | 1 | 0 | 0 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0 | Example | Epic | in-queue | None | org/repo | `backlog/E0.md` |\n"
            f"| {unit_id} | Roundtrip Test | Task | in-progress | none | org/repo | `backlog/{unit_id}.md` |\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(index_content, encoding="utf-8")

        # Epic stub required by validate().
        (backlog_dir / "E0.md").write_text("# E0: Example\n\n## Status: in-queue\n", encoding="utf-8")

        mgr = BacklogManager()
        # Append three TDD entries -- RED, GREEN, REFACTOR.
        mgr._append_tdd_entry(wu_path, "RED", "roundtrip-red")
        mgr._append_tdd_entry(wu_path, "GREEN", "roundtrip-green")
        mgr._append_tdd_entry(wu_path, "REFACTOR", "No refactor needed.")

        errors = mgr.validate(index_path, tmp_path)
        assert errors == [], f"validate() reported errors after TDD entries: {errors}"


class TestRollupParentStatusEdgeCases:
    """Test edge cases in _rollup_parent_status."""

    def test_rollup_returns_early_for_top_level_id(self, tmp_path: Path) -> None:
        """Line 319: early return when unit_id has no parent (no hyphen)."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0 | Epic | Epic | done | None | git-repo | `backlog/E0.md` |\n"
        )
        mgr = BacklogManager()
        # Should return without error for a single-segment ID
        mgr._rollup_parent_status(index_path, "E0")

    def test_rollup_warns_when_parent_file_missing(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Lines 329-330: warns and skips rollup when parent file is missing."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1 | Feature | Feature | in-queue | None | git-repo | `backlog/E0-F1.md` |\n"
            "| E0-F1-S1 | Story | Story | done | None | git-repo | `backlog/E0-F1-S1.md` |\n"
        )
        # Only create child file, not parent file
        child_file = backlog_dir / "E0-F1-S1.md"
        child_file.write_text("# E0-F1-S1\n\n## Status: done\n")
        # Parent file E0-F1.md does NOT exist

        mgr = BacklogManager()
        # Should warn but not raise
        mgr._rollup_parent_status(index_path, "E0-F1-S1")

    def test_rollup_appends_comment_when_comments_section_exists(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Line 347: when parent already has ## Comments, the rollup comment is appended
        to the existing section (no duplicate header created)."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1 | Feature | Feature | in-queue | None | git-repo | `backlog/E0-F1.md` |\n"
            "| E0-F1-S1 | Story | Story | done | None | git-repo | `backlog/E0-F1-S1.md` |\n"
        )
        # Parent file already contains ## Comments section
        parent_file = backlog_dir / "E0-F1.md"
        parent_file.write_text(
            "# E0-F1: Feature\n\n## Status: in-queue\n\n## Comments\n\n"
            "[2026-01-01 00:00 UTC] [agent/orchestrator] Previous comment\n"
        )
        child_file = backlog_dir / "E0-F1-S1.md"
        child_file.write_text("# E0-F1-S1\n\n## Status: done\n")

        mgr = BacklogManager()
        mgr._rollup_parent_status(index_path, "E0-F1-S1")

        result = parent_file.read_text(encoding="utf-8")
        # Should contain exactly one ## Comments header (not duplicated)
        assert result.count("## Comments") == 1
        # Should contain the auto-rollup comment
        assert "Auto-rolled to done" in result
        # Should still contain the previous comment
        assert "Previous comment" in result

    def test_rollup_comment_has_no_emdash(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Regression guard: the auto-rollup comment must not contain U+2014.

        The validate-backlog check (manager.py Check 10) rejects work unit files
        that contain em-dash, so any writer emitting em-dash into a work unit
        file is a self-inflicted block of the orchestrator loop. Keep writers
        using '--' instead.
        """
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1 | Feature | Feature | in-queue | None | git-repo | `backlog/E0-F1.md` |\n"
            "| E0-F1-S1 | Story | Story | done | None | git-repo | `backlog/E0-F1-S1.md` |\n"
        )
        parent_file = backlog_dir / "E0-F1.md"
        parent_file.write_text("# E0-F1: Feature\n\n## Status: in-queue\n")
        child_file = backlog_dir / "E0-F1-S1.md"
        child_file.write_text("# E0-F1-S1\n\n## Status: done\n")

        BacklogManager()._rollup_parent_status(index_path, "E0-F1-S1")

        assert "\u2014" not in parent_file.read_text(encoding="utf-8")


class TestParseBacklogRowsEdgeCases:
    """Test _parse_backlog_rows with edge-case input."""

    def test_skips_rows_with_fewer_than_5_cells(self, tmp_path: Path) -> None:
        """Line 347: rows with < 5 cells are skipped."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "| Only | Two |\n"
            "| E0-F1-S1-T1 | Create Makefile | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        mgr = BacklogManager()
        rows = mgr._parse_backlog_rows(index_path)
        # Only the valid row should be parsed
        ids = [r[0] for r in rows]
        assert "E0-F1-S1-T1" in ids


class TestAllChildrenDoneEdgeCases:
    """Test _all_children_done edge cases."""

    def test_returns_false_when_parent_already_done(self) -> None:
        """Line 372: returns False if parent is already Done."""
        rows = [
            ("E0-F1", "done", "backlog/E0-F1.md"),
            ("E0-F1-S1", "done", "backlog/E0-F1-S1.md"),
        ]
        mgr = BacklogManager()
        assert mgr._all_children_done(rows, "E0-F1") is False


class TestFindWorkUnitFileEdgeCases:
    """Test _find_work_unit_file edge cases."""

    def test_returns_none_when_file_does_not_exist(self, tmp_path: Path) -> None:
        """Line 394: returns None when candidate file doesn't exist."""
        rows = [
            ("E0-F1-S1-T1", "in-queue", "backlog/E0-F1-S1-T1.md"),
        ]
        mgr = BacklogManager()
        result = mgr._find_work_unit_file(rows, "E0-F1-S1-T1", tmp_path)
        assert result is None

    def test_returns_none_when_id_not_in_rows(self, tmp_path: Path) -> None:
        """Line 394: returns None when unit_id not found in rows."""
        rows = [
            ("E0-F1-S1-T1", "in-queue", "backlog/E0-F1-S1-T1.md"),
        ]
        mgr = BacklogManager()
        result = mgr._find_work_unit_file(rows, "NONEXISTENT", tmp_path)
        assert result is None


class TestUpdateBacklogIndexNotFound:
    """Test _update_backlog_index raises when unit not found."""

    def test_raises_when_unit_not_in_index(self, tmp_path: Path) -> None:
        """Line 439: raises ValueError when the unit ID is not found in BACKLOG.md."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1-S1-T1 | Task | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        mgr = BacklogManager()
        with pytest.raises(ValueError, match="Could not find unit"):
            mgr._update_backlog_index(index_path, "NONEXISTENT", "done")


class TestAppendCommentNoSection:
    """Test _append_comment when Comments section is absent."""

    def test_creates_comments_section_when_missing(self, tmp_path: Path) -> None:
        """Line 460: appends Comments header when section doesn't exist."""
        wu = tmp_path / "unit.md"
        wu.write_text("# Unit\n\n## Status: in-queue\n")

        mgr = BacklogManager()
        mgr._append_comment(wu, "TEST_ACTION", "test message")

        content = wu.read_text(encoding="utf-8")
        assert "## Comments" in content
        assert "[TEST_ACTION]" in content
        assert "test message" in content


class TestParseEpicTitlesEdgeCases:
    """Test _parse_epic_titles edge cases."""

    def test_skips_rows_with_fewer_than_4_cells(self, tmp_path: Path) -> None:
        """Line 566: rows with < 4 cells are skipped."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n| Short |\n| E0 | Epic Zero | Epic | in-queue | None | git-repo | `backlog/E0.md` |\n"
        )
        mgr = BacklogManager()
        titles = mgr._parse_epic_titles(index_path)
        assert "E0" in titles


class TestParseSummaryTableEdgeCases:
    """Test _parse_summary_table edge cases."""

    def test_skips_rows_with_fewer_than_7_cells(self) -> None:
        """Line 675: rows with < 7 cells in summary table are skipped."""
        content = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| Short |\n"
            "| E0 | Epic | 1 | 2 | 3 | 0 |\n"
            "\n## Full Work Unit Index\n"
        )
        mgr = BacklogManager()
        result = mgr._parse_summary_table(content)
        assert "E0" in result

    def test_skips_rows_with_non_integer_counts(self) -> None:
        """Lines 686-687: rows with non-integer count values are skipped."""
        content = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Epic | NaN | two | 3 | 0 |\n"
            "| E1 | Other | 1 | 2 | 3 | 0 |\n"
            "\n## Full Work Unit Index\n"
        )
        mgr = BacklogManager()
        result = mgr._parse_summary_table(content)
        # E0 should be skipped due to ValueError, E1 should be parsed
        assert "E0" not in result
        assert "E1" in result


# ---------------------------------------------------------------------------
# Auto-requeue cascade -- the sideways counterpart to parent rollup.
#
# Fixture layout: each test builds a tmp workspace with a BACKLOG.md and two
# or three work-unit files under ``backlog/``. Fixtures intentionally mirror
# the on-disk shape of a real workspace (``workspace/BACKLOG.md`` +
# ``workspace/backlog/<subpath>/<id>.md``) because ``_auto_requeue_marker_dependents``
# resolves paths via ``backlog_index.parent / file_path`` exactly the way the
# production CLI does.
# ---------------------------------------------------------------------------


def _unit_body(
    unit_id: str,
    status: str,
    *,
    deps: list[str] | None = None,
    comments: str = "",
    task_type: str | None = None,
) -> str:
    """Build a minimal well-formed work-unit file body for the scan tests.

    Produces the sections the parser and scan require (``Status``, ``Dependencies``,
    ``Comments``) plus the content sections ``validate-backlog`` demands for
    task-level files (``Description``, ``Acceptance Criteria``, ``Changes Manifest``,
    ``Definition of Done``). Keeps every fixture self-contained so a test
    failure points at the scan logic rather than a malformed fixture.

    ``task_type``, when provided, inserts a ``## Task Type:`` section so
    callers that drive a real ``BacklogManager.mark_done()`` can declare an
    exempt type (e.g. ``"chore"``) and avoid tripping the FR-4.5/FR-4.6
    task-type invariant mark_done() enforces directly (E4-F4-S1-T2 round 3)
    when the fixture's concern is unrelated (status transitions, auto-requeue
    cascades).
    """
    dep_rows = "| none | | |" if not deps else "\n".join(f"| {d} | (auto) | proposed |" for d in deps)
    task_type_section = f"## Task Type: {task_type}\n\n" if task_type is not None else ""
    return (
        f"# {unit_id}: Test Task\n\n"
        f"## Status: {status}\n\n"
        f"{task_type_section}"
        "## Description\n\nAuto-requeue test fixture.\n\n"
        "## Dependencies\n\n"
        "| ID | Title | Status |\n"
        "|----|-------|--------|\n"
        f"{dep_rows}\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-TEST-001 fixture\n\n"
        "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `test.py` | fixture |\n\n"
        "## Definition of Done\n\n- [ ] AC complete\n\n"
        f"## Comments\n\n{comments}"
    )


def _backlog_index_body(rows: list[tuple[str, str, str]]) -> str:
    """Build a BACKLOG.md with Summary + Full Index for the given rows."""
    index_rows = "\n".join(
        f"| {unit_id} | {title} | Task | {status} | None | example/repo | `backlog/{unit_id}.md` |"
        for unit_id, title, status in rows
    )
    # Status Summary needs an epic-row count. All fixture IDs start with E0-,
    # so group them under a single E0 epic. Counts are synthesised from the
    # rows' statuses; other columns zero for simplicity.
    done = sum(1 for _, _, s in rows if s == "done")
    in_progress = sum(1 for _, _, s in rows if s == "in-progress")
    in_queue = sum(1 for _, _, s in rows if s == "in-queue")
    blocked = sum(1 for _, _, s in rows if s == "blocked")
    declined = sum(1 for _, _, s in rows if s == "declined")
    return (
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
        "|------|-------|------|-------------|----------|---------|----------|\n"
        f"| E0 | Example | {done} | {in_progress} | {in_queue} | {blocked} | {declined} |\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
        "| E0 | Example | Epic | in-queue | None | example/repo | `backlog/E0.md` |\n"
        f"{index_rows}\n"
    )


def _write_workspace(
    tmp_path: Path,
    *,
    rows: list[tuple[str, str, str]],
    files: dict[str, str],
) -> Path:
    """Materialise BACKLOG.md + the set of work-unit files under ``tmp_path``.

    Returns the BACKLOG.md path (used as ``backlog_index`` in the scan).
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(_backlog_index_body(rows), encoding="utf-8")
    # Minimal Epic file so parser's validate path doesn't trip if invoked.
    (backlog_dir / "E0.md").write_text("# E0: Example\n\n## Status: in-queue\n", encoding="utf-8")
    for unit_id, body in files.items():
        (backlog_dir / f"{unit_id}.md").write_text(body, encoding="utf-8")
    return index_path


class TestExtractPendingProposalMarkers:
    """The marker extractor reads only the Comments section."""

    def test_empty_comments_returns_empty_set(self, tmp_path: Path) -> None:
        wu = tmp_path / "t.md"
        wu.write_text(_unit_body("E0-F1-S1-T1", "blocked", comments=""), encoding="utf-8")
        assert BacklogManager()._extract_pending_proposal_markers(wu) == set()

    def test_single_marker(self, tmp_path: Path) -> None:
        wu = tmp_path / "t.md"
        wu.write_text(
            _unit_body(
                "E0-F1-S1-T1",
                "blocked",
                comments="[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
            ),
            encoding="utf-8",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == {"E0-F1-S1-T2"}

    def test_multiple_markers(self, tmp_path: Path) -> None:
        wu = tmp_path / "t.md"
        wu.write_text(
            _unit_body(
                "E0-F1-S1-T1",
                "blocked",
                comments=(
                    "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n\n"
                    "[2026-04-19 14:01 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n"
                ),
            ),
            encoding="utf-8",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == {"E0-F1-S1-T2", "E0-F1-S1-T3"}

    def test_marker_quoted_in_description_is_ignored(self, tmp_path: Path) -> None:
        """Markers outside the Comments section must not trigger the scan.

        A Description section that happens to contain the literal text
        ``[BLOCKED_PENDING_PROPOSAL] SOMETHING`` (e.g. explaining the workflow
        in prose) would falsely activate auto-requeue if the extractor were
        not scoped to ``## Comments``.
        """
        wu = tmp_path / "t.md"
        # Hand-craft: put the marker in Description, leave Comments clean.
        body = (
            "# E0-F1-S1-T1\n\n"
            "## Status: blocked\n\n"
            "## Description\n\n"
            "Quoting prior incident text: '[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99'\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `x.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] AC\n\n"
            "## Comments\n\n"
        )
        wu.write_text(body, encoding="utf-8")
        assert BacklogManager()._extract_pending_proposal_markers(wu) == set()

    def test_nonexistent_file_returns_empty_set(self, tmp_path: Path) -> None:
        assert BacklogManager()._extract_pending_proposal_markers(tmp_path / "nope.md") == set()

    def test_file_without_comments_section_returns_empty_set(self, tmp_path: Path) -> None:
        wu = tmp_path / "t.md"
        wu.write_text("# Task\n\n## Status: blocked\n", encoding="utf-8")
        assert BacklogManager()._extract_pending_proposal_markers(wu) == set()


class TestAutoRequeueMarkerDependents:
    """Marker-based auto-requeue on parent's ``mark_done``.

    Every test builds a tmp workspace with a blocked source task, runs the
    scan directly (``_auto_requeue_marker_dependents``), and asserts on
    the resulting status and audit comment. The direct-call form is used
    for most cases to keep fixtures focused; one integration-level test
    drives the same path through ``_set_status`` to prove the hook-up.
    """

    def test_blocked_source_with_all_markers_terminal_is_requeued(self, tmp_path: Path) -> None:
        """Happy path: one blocked source, one promoted dep, dep is done -> requeue."""
        marker_comment = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=marker_comment,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "PromotedDep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[AUTO_UNBLOCKED]" in src
        assert "E0-F1-S1-T2" in src  # audit comment names the dep

    def test_partial_completion_keeps_source_blocked(self, tmp_path: Path) -> None:
        """Two markers, only one dep is done -> source stays blocked."""
        markers = (
            "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n\n"
            "[2026-04-19 14:01 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2", "E0-F1-S1-T3"],
            comments=markers,
        )
        dep_done = _unit_body("E0-F1-S1-T2", "done")
        dep_queued = _unit_body("E0-F1-S1-T3", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep1", "done"),
                ("E0-F1-S1-T3", "Dep2", "in-queue"),
            ],
            files={
                "E0-F1-S1-T1": src_file,
                "E0-F1-S1-T2": dep_done,
                "E0-F1-S1-T3": dep_queued,
            },
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src
        assert "[AUTO_UNBLOCKED]" not in src

    def test_declined_dep_counts_as_terminal(self, tmp_path: Path) -> None:
        """Declined is terminal for auto-requeue (mirrors parent-rollup semantics)."""
        markers = (
            "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n\n"
            "[2026-04-19 14:01 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2", "E0-F1-S1-T3"],
            comments=markers,
        )
        dep_done = _unit_body("E0-F1-S1-T2", "done")
        dep_declined = _unit_body("E0-F1-S1-T3", "declined")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep1", "done"),
                ("E0-F1-S1-T3", "Dep2", "declined"),
            ],
            files={
                "E0-F1-S1-T1": src_file,
                "E0-F1-S1-T2": dep_done,
                "E0-F1-S1-T3": dep_declined,
            },
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[AUTO_UNBLOCKED]" in src

    def test_blocked_without_markers_is_untouched(self, tmp_path: Path) -> None:
        """Blocks from non-proposal causes (review fail, git-ops, operator) stay manual."""
        src_file = _unit_body("E0-F1-S1-T1", "blocked", deps=["E0-F1-S1-T2"], comments="")
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src
        assert "[AUTO_UNBLOCKED]" not in src

    @pytest.mark.parametrize("candidate_status", ["in-queue", "in-progress", "in-review", "done", "declined"])
    def test_non_blocked_candidate_is_untouched(self, tmp_path: Path, candidate_status: str) -> None:
        """The scan only transitions candidates whose status is blocked."""
        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            candidate_status,
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", candidate_status),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert f"## Status: {candidate_status}" in src
        assert "[AUTO_UNBLOCKED]" not in src

    def test_newly_done_not_in_declared_deps_is_skipped(self, tmp_path: Path) -> None:
        """Must be a declared dep; the scan does not scan all blocked tasks globally."""
        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n"
        # Source declares T3 as dep; T2 (the newly-done) is NOT in its deps.
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T3"],
            comments=markers,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        dep3_file = _unit_body("E0-F1-S1-T3", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "UnrelatedDone", "done"),
                ("E0-F1-S1-T3", "Dep", "done"),
            ],
            files={
                "E0-F1-S1-T1": src_file,
                "E0-F1-S1-T2": dep_file,
                "E0-F1-S1-T3": dep3_file,
            },
        )
        # Fire with T2 (not in T1's deps) -> no auto-requeue, even though
        # the marker dep T3 is also done.
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src

    def test_unknown_marker_id_treated_as_non_terminal(self, tmp_path: Path) -> None:
        """Rejected / archived drafts whose IDs no longer exist in the index never terminate.

        This prevents spurious auto-requeue when a promoted proposal was
        later rejected (and its index row removed).
        """
        # Marker names a task ID that has no row in BACKLOG.md.
        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src

    def test_audit_comment_names_all_marker_ids_sorted(self, tmp_path: Path) -> None:
        """The [AUTO_UNBLOCKED] comment sorts the marker IDs for deterministic output."""
        markers = (
            "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n\n"
            "[2026-04-19 14:01 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2", "E0-F1-S1-T3"],
            comments=markers,
        )
        dep2 = _unit_body("E0-F1-S1-T2", "done")
        dep3 = _unit_body("E0-F1-S1-T3", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep1", "done"),
                ("E0-F1-S1-T3", "Dep2", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep2, "E0-F1-S1-T3": dep3},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "[AUTO_UNBLOCKED]" in src
        # Sorted order: T2 before T3.
        unblocked_idx = src.index("[AUTO_UNBLOCKED]")
        tail = src[unblocked_idx:]
        assert tail.index("E0-F1-S1-T2") < tail.index("E0-F1-S1-T3")

    def test_mark_done_integration_triggers_requeue(self, tmp_path: Path) -> None:
        """End-to-end: calling mark_done drives the cascade through _set_status."""
        from devbench.constants import (
            ALL_REQUIRED_JUDGE_NAMES as JUDGES,
        )

        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        # Dep must pass the done-gate before mark_done accepts it: write
        # [REVIEW_PASS] lines for every required judge to simulate a
        # completed review round. Dep starts as in-review so mark_done is
        # the transition under test.
        review_passes = "\n".join(f"[2026-04-19 14:05 UTC] [judge/{judge}] [REVIEW_PASS] ok" for judge in JUDGES)
        dep_file = _unit_body("E0-F1-S1-T2", "in-review", comments=review_passes + "\n", task_type="chore")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep", "in-review"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )

        mgr = BacklogManager()
        dep_path = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        mgr.mark_done(dep_path, index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[AUTO_UNBLOCKED]" in src

    def test_auto_requeue_runs_before_parent_rollup(self, tmp_path: Path) -> None:
        """Ordering invariant: the sideways unblock fires BEFORE parent rollup.

        Scenario: source T1 is blocked with a marker on T2; parent Story S1
        would otherwise roll to done (both children T1 and T2 are terminal
        in the strict sense -- blocked is not terminal for rollup, but let's
        verify the order matters). When T2 is marked done:
          1. _auto_requeue_marker_dependents flips T1 blocked -> in-queue.
          2. _rollup_parent_status then sees T1 as in-queue and correctly
             does NOT roll S1 to done.
        """
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES as JUDGES

        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        review_passes = "\n".join(f"[2026-04-19 14:05 UTC] [judge/{judge}] [REVIEW_PASS] ok" for judge in JUDGES)
        dep_file = _unit_body("E0-F1-S1-T2", "in-review", comments=review_passes + "\n", task_type="chore")

        # Use a richer BACKLOG.md that includes the Story parent so rollup
        # has something to consider.
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
            "|------|-------|------|-------------|----------|---------|----------|\n"
            "| E0 | Example | 0 | 0 | 0 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0 | Example | Epic | in-queue | None | example/repo | `backlog/E0.md` |\n"
            "| E0-F1 | Feature | Feature | in-queue | None | example/repo | `backlog/E0-F1.md` |\n"
            "| E0-F1-S1 | Story | Story | in-queue | None | example/repo | `backlog/E0-F1-S1.md` |\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | example/repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Dep | Task | in-review | None | example/repo | `backlog/E0-F1-S1-T2.md` |\n",
            encoding="utf-8",
        )
        (backlog_dir / "E0.md").write_text("# E0\n\n## Status: in-queue\n", encoding="utf-8")
        (backlog_dir / "E0-F1.md").write_text("# E0-F1\n\n## Status: in-queue\n", encoding="utf-8")
        (backlog_dir / "E0-F1-S1.md").write_text("# E0-F1-S1\n\n## Status: in-queue\n", encoding="utf-8")
        (backlog_dir / "E0-F1-S1-T1.md").write_text(src_file, encoding="utf-8")
        (backlog_dir / "E0-F1-S1-T2.md").write_text(dep_file, encoding="utf-8")

        mgr = BacklogManager()
        mgr.mark_done(backlog_dir / "E0-F1-S1-T2.md", index_path, "E0-F1-S1-T2")

        # T1 was requeued.
        t1 = (backlog_dir / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in t1

        # Parent Story S1 was NOT auto-rolled to done because T1 is now
        # in-queue (not terminal).
        s1 = (backlog_dir / "E0-F1-S1.md").read_text()
        assert "## Status: in-queue" in s1

    def test_candidate_file_missing_logs_and_continues(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A missing candidate file produces a warning but does not crash the scan."""
        import logging

        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep", "done"),
                ("E0-F1-S1-T3", "Ghost", "blocked"),  # row exists but file won't
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        # Ghost row has a file path in BACKLOG.md but the file is not on
        # disk. The scan must log + continue, not crash.
        caplog.set_level(logging.WARNING, logger="devbench.backlog_manager")
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        # T1 was still requeued (ghost didn't prevent the scan).
        t1 = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in t1
        # A warning was logged about the missing ghost file.
        assert any("candidate file missing" in rec.message.lower() for rec in caplog.records)

    def test_scan_skipped_when_backlog_unparseable(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A missing / unreadable BACKLOG.md logs a warning and returns cleanly."""
        import logging

        # Do NOT create BACKLOG.md; point the scan at a missing path.
        missing = tmp_path / "BACKLOG.md"
        caplog.set_level(logging.WARNING, logger="devbench.backlog_manager")
        BacklogManager()._auto_requeue_marker_dependents(missing, "E0-F1-S1-T2")

        assert any("auto-requeue scan skipped" in rec.message.lower() for rec in caplog.records)

    def test_blocked_row_without_file_path_is_skipped(self, tmp_path: Path) -> None:
        """A blocked row missing the file_path cell cannot be resolved; scan skips it cleanly."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index_path = tmp_path / "BACKLOG.md"
        # Craft a row whose File Path cell is empty (no backtick path).
        # `_parse_backlog_rows` will still yield it with file_path="" and
        # the scan must skip rather than crash on workspace / "".
        index_path.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
            "|------|-------|------|-------------|----------|---------|----------|\n"
            "| E0 | Example | 0 | 0 | 0 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0 | Example | Epic | in-queue | None | example/repo | `backlog/E0.md` |\n"
            "| E0-F1-S1-T1 | Orphan | Task | blocked | None | example/repo |  |\n"
            "| E0-F1-S1-T2 | Dep | Task | done | None | example/repo | `backlog/E0-F1-S1-T2.md` |\n",
            encoding="utf-8",
        )
        (backlog_dir / "E0.md").write_text("# E0\n## Status: in-queue\n", encoding="utf-8")
        (backlog_dir / "E0-F1-S1-T2.md").write_text(_unit_body("E0-F1-S1-T2", "done"), encoding="utf-8")

        # Must not raise.
        BacklogManager()._auto_requeue_marker_dependents(index_path, "E0-F1-S1-T2")


class TestAutoRequeueRegularDepDependents:
    """Cascade for blocked tasks with regular task-level deps and NO marker (#208).

    The marker-cascade (``_auto_requeue_marker_dependents``) only handles
    blocked tasks whose Comments section carries a ``[BLOCKED_PENDING_PROPOSAL]``
    marker.  Tasks that landed in ``blocked`` via ``cmd_sync_blocked`` (regular
    Dependencies table only, no marker) were ignored — they stayed blocked
    forever even after the dep transitioned to ``done``.  This class pins
    the regular-dep cascade that closes that gap.
    """

    def test_blocked_with_regular_dep_done_is_requeued(self, tmp_path: Path) -> None:
        """Happy path: T1 deps on T2 (no marker), T2 -> done, scan flips T1 -> in-queue."""
        sync_blocked_audit = (
            "[2026-05-11 20:13 UTC] [backlog_manager] "
            "[BLOCKED] sync-blocked: dependency 'E0-F1-S1-T2' not yet terminal\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=sync_blocked_audit,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Dependent", "blocked"),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_regular_dep_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[UNBLOCKED]" in src
        assert "[CASCADE_RESOLVED]" in src
        assert "E0-F1-S1-T2" in src

    def test_blocked_with_marker_is_left_to_marker_cascade(self, tmp_path: Path) -> None:
        """Tasks WITH a ``[BLOCKED_PENDING_PROPOSAL]`` marker are owned by the
        marker cascade; the regular-dep cascade must not touch them to avoid
        double-handling and conflicting audit comments."""
        marker = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=marker,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Dependent", "blocked"),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_regular_dep_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        # The regular-dep cascade leaves marker-bearing tasks alone.
        assert "## Status: blocked" in src
        assert "[UNBLOCKED]" not in src

    def test_partial_completion_keeps_blocked(self, tmp_path: Path) -> None:
        """T1 deps on T2 and T3; only T2 is done; scan must NOT requeue T1."""
        sync_blocked_audit = (
            "[2026-05-11 20:13 UTC] [backlog_manager] "
            "[BLOCKED] sync-blocked: dependency 'E0-F1-S1-T2' not yet terminal\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2", "E0-F1-S1-T3"],
            comments=sync_blocked_audit,
        )
        dep_done = _unit_body("E0-F1-S1-T2", "done")
        dep_queued = _unit_body("E0-F1-S1-T3", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Dependent", "blocked"),
                ("E0-F1-S1-T2", "Dep1", "done"),
                ("E0-F1-S1-T3", "Dep2", "in-queue"),
            ],
            files={
                "E0-F1-S1-T1": src_file,
                "E0-F1-S1-T2": dep_done,
                "E0-F1-S1-T3": dep_queued,
            },
        )
        BacklogManager()._auto_requeue_regular_dep_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src
        assert "[UNBLOCKED]" not in src

    def test_dep_not_referenced_by_candidate_is_left_untouched(self, tmp_path: Path) -> None:
        """If newly_done_id is not in T1's Dependencies table, T1 stays blocked."""
        sync_blocked_audit = (
            "[2026-05-11 20:13 UTC] [backlog_manager] "
            "[BLOCKED] sync-blocked: dependency 'E0-F1-S1-T9' not yet terminal\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T9"],
            comments=sync_blocked_audit,
        )
        unrelated = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Dependent", "blocked"),
                ("E0-F1-S1-T2", "Unrelated", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": unrelated},
        )
        BacklogManager()._auto_requeue_regular_dep_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src

    def test_in_queue_task_is_left_alone(self, tmp_path: Path) -> None:
        """Only blocked tasks are candidates -- a task already in-queue is ignored."""
        src_file = _unit_body("E0-F1-S1-T1", "in-queue", deps=["E0-F1-S1-T2"])
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Dependent", "in-queue"),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_regular_dep_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[UNBLOCKED]" not in src

    def test_mark_done_integration_triggers_regular_dep_cascade(self, tmp_path: Path) -> None:
        """End-to-end: mark_done(B) flips A (regular-dep blocked, no marker) to in-queue.

        Mirrors the marker-cascade integration test but exercises the
        regular-dep path that was missing pre-fix (#208).
        """
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES as JUDGES

        sync_blocked_audit = (
            "[2026-05-11 20:13 UTC] [backlog_manager] "
            "[BLOCKED] sync-blocked: dependency 'E0-F1-S1-T2' not yet terminal\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=sync_blocked_audit,
        )
        review_passes = "\n".join(f"[2026-04-19 14:05 UTC] [judge/{judge}] [REVIEW_PASS] ok" for judge in JUDGES)
        dep_file = _unit_body("E0-F1-S1-T2", "in-review", comments=review_passes + "\n", task_type="chore")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Dependent", "blocked"),
                ("E0-F1-S1-T2", "Dep", "in-review"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )

        mgr = BacklogManager()
        dep_path = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        mgr.mark_done(dep_path, index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[UNBLOCKED]" in src
        assert "[CASCADE_RESOLVED]" in src


class TestParseCandidateDependencies:
    """Coverage of the inline dependency-table parser used by the auto-requeue scan."""

    def test_dependency_row_with_fewer_than_four_cells_is_skipped(self) -> None:
        """Malformed rows with <4 split cells must be skipped, not crash or mis-parse.

        ``| oops |`` splits into ``["", " oops ", ""]`` -- three cells. The
        parser's ``len(cells) < 4`` guard discards it; valid four-cell rows
        around it are unaffected. Regression-pin for the guard branch.
        """
        content = (
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            "| oops |\n"  # 3 cells after split -> the <4 branch fires
            "| E0-F1-S1-T2 | Good | proposed |\n"
        )
        deps = BacklogManager._parse_candidate_dependencies(content)
        # The short row is skipped; the valid row is collected.
        assert deps == ["E0-F1-S1-T2"]

    def test_dependency_row_with_empty_first_cell_is_skipped(self) -> None:
        """A row like ``|  | title | status |`` has an empty first cell -- skip it."""
        content = (
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            "|  | missing-id | proposed |\n"
            "| E0-F1-S1-T2 | Good | proposed |\n"
        )
        deps = BacklogManager._parse_candidate_dependencies(content)
        assert deps == ["E0-F1-S1-T2"]


class TestCheckDependenciesCellCountMismatch:
    """Coverage for the Status-Summary-vs-Index cell-count guard in ``_check_dependencies``."""

    def test_row_with_wrong_cell_count_is_skipped(self, tmp_path: Path) -> None:
        """Full-Index rows whose cell count differs from BACKLOG_INDEX_CELL_COUNT are skipped.

        Guards against the collision between Status Summary rows (8 cells) and
        Full Work Unit Index rows (9 cells after the Declined column landed).
        """
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Valid | Task | in-queue | E0-F1-S1-T2 | example/repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Short | Task |\n"  # too few cells -> should be skipped without error
            "| E0-F1-S1-T2 | Valid | Task | done | None | example/repo | `backlog/E0-F1-S1-T2.md` |\n",
            encoding="utf-8",
        )
        errors: list[str] = []
        BacklogManager()._check_dependencies(index_path, {"E0-F1-S1-T1", "E0-F1-S1-T2"}, errors)
        # The malformed row is silently skipped; valid rows still produce no
        # errors because their deps resolve.
        assert errors == []


# ---------------------------------------------------------------------------
# Post-Backlog-A Tier 3 rules: tests for _check_manifest_conflicts,
# _check_language_ac_alignment, and _check_source_test_pairs. Each rule is
# tested in isolation using the existing backlog_dir fixture. The helper
# methods (_classify_manifest_tier, _is_production_source,
# _is_real_manifest_path, _matching_test_basenames) are tested directly via
# unit-style assertions on the BacklogManager class.
# ---------------------------------------------------------------------------


class TestClassifyManifestTier:
    """Direct tests for the language-tier classifier helper."""

    def test_pure_python_returns_python(self) -> None:
        assert BacklogManager._classify_manifest_tier(["src/foo.py", "tests/test_foo.py"]) == "Python"

    def test_pure_hcl_returns_hcl(self) -> None:
        assert BacklogManager._classify_manifest_tier(["infra/main.tf", "infra/vars.tfvars"]) == "HCL"

    def test_pure_yaml_returns_yaml(self) -> None:
        assert BacklogManager._classify_manifest_tier(["infra/properties/common.yaml"]) == "YAML"

    def test_python_plus_yaml_returns_python(self) -> None:
        # Python is dominant per docs/acceptance-criteria-canonical.md
        assert BacklogManager._classify_manifest_tier(["src/foo.py", "config.yaml"]) == "Python"

    def test_yaml_plus_json_returns_mixed(self) -> None:
        assert BacklogManager._classify_manifest_tier(["a.yaml", "b.json"]) == "Mixed"

    def test_empty_returns_empty_string(self) -> None:
        assert BacklogManager._classify_manifest_tier([]) == ""


class TestIsProductionSource:
    """Direct tests for the source-vs-test classifier helper."""

    def test_src_python_is_source(self) -> None:
        assert BacklogManager._is_production_source("src/foo/bar.py") is True

    def test_infra_scripts_python_is_source(self) -> None:
        assert BacklogManager._is_production_source("infra/scripts/migrate.py") is True

    def test_services_src_nested_is_source(self) -> None:
        assert BacklogManager._is_production_source("services/api/src/handler.py") is True

    def test_test_path_is_not_source(self) -> None:
        assert BacklogManager._is_production_source("tests/unit/test_foo.py") is False

    def test_nested_test_path_is_not_source(self) -> None:
        assert BacklogManager._is_production_source("services/api/tests/unit/test_foo.py") is False

    def test_init_py_is_not_source(self) -> None:
        assert BacklogManager._is_production_source("src/foo/__init__.py") is False

    def test_yaml_is_not_python_source(self) -> None:
        assert BacklogManager._is_production_source("config.yaml") is False

    def test_top_level_python_outside_src_is_not_source(self) -> None:
        # Random top-level .py is not classified as production source
        assert BacklogManager._is_production_source("setup.py") is False


class TestIsRealManifestPath:
    """Direct tests for the placeholder-string filter."""

    def test_real_path_passes(self) -> None:
        assert BacklogManager._is_real_manifest_path("src/foo.py") is True

    def test_none_placeholder_filtered(self) -> None:
        assert BacklogManager._is_real_manifest_path("(none)") is False

    def test_no_file_changes_placeholder_filtered(self) -> None:
        assert BacklogManager._is_real_manifest_path("(no file changes; verification-only)") is False

    def test_empty_string_filtered(self) -> None:
        assert BacklogManager._is_real_manifest_path("") is False


class TestSourceStemForPairMatch:
    """Direct tests for the source-stem helper used in pair matching."""

    def test_simple_module_returns_basename_stem(self) -> None:
        assert BacklogManager._source_stem_for_pair_match("src/foo.py") == "foo"

    def test_nested_module_uses_basename_only(self) -> None:
        assert BacklogManager._source_stem_for_pair_match("services/api/src/handler.py") == "handler"

    def test_init_py_returns_empty(self) -> None:
        assert BacklogManager._source_stem_for_pair_match("src/foo/__init__.py") == ""

    def test_non_python_returns_empty(self) -> None:
        assert BacklogManager._source_stem_for_pair_match("infra/main.tf") == ""


class TestTestFilenamePairsWithStem:
    """Direct tests for the test-file pair predicate used by the rule."""

    def test_exact_test_prefix_match(self) -> None:
        assert BacklogManager._test_filename_pairs_with_stem("tests/unit/test_event.py", "event")

    def test_namespaced_test_prefix_match(self) -> None:
        # Project convention: `test_telemetry_<basename>.py` for
        # `<basename>.py` -- the stem appears as a substring of the
        # underscore-tokenised inner test name.
        assert BacklogManager._test_filename_pairs_with_stem("tests/unit/test_telemetry_event.py", "event")

    def test_suffix_test_form_match(self) -> None:
        # Some projects use `<basename>_test.py` instead of `test_<basename>.py`.
        assert BacklogManager._test_filename_pairs_with_stem("tests/unit/event_test.py", "event")

    def test_unrelated_test_does_not_match(self) -> None:
        assert not BacklogManager._test_filename_pairs_with_stem("tests/unit/test_handler.py", "models")

    def test_non_test_filename_rejected(self) -> None:
        # Source files that are not in a test_*.py / *_test.py shape do not
        # satisfy the pair predicate, even if their basename contains the stem.
        assert not BacklogManager._test_filename_pairs_with_stem("src/event.py", "event")

    def test_init_py_rejected(self) -> None:
        assert not BacklogManager._test_filename_pairs_with_stem("tests/__init__.py", "event")


class TestExtractDepIdsEdgeCases:
    """Cover the degenerate-row branches in `_extract_dep_ids`."""

    def test_empty_token_between_commas_is_skipped(self) -> None:
        # Cell value "E1-F1-S1-T1, , E2-F1-S1-T1" includes an empty
        # token after the first comma; the loop must skip it without
        # adding anything for that slot.
        content = "## Dependencies\n| E1-F1-S1-T1, , E2-F1-S1-T1 |\n"
        deps = BacklogManager._extract_dep_ids(content)
        assert deps == {"E1-F1-S1-T1", "E2-F1-S1-T1"}


class TestTasksFormDepChainEdgeCases:
    """Cover the trivial-input early return in `_tasks_form_dep_chain`."""

    def test_single_id_is_trivially_a_chain(self) -> None:
        assert BacklogManager._tasks_form_dep_chain(["E1-F1-S1-T1"], {}) is True

    def test_empty_id_list_is_trivially_a_chain(self) -> None:
        assert BacklogManager._tasks_form_dep_chain([], {}) is True

    def test_chain_through_non_claimant_intermediate(self) -> None:
        """FR-5 (db-311): C -> B(non-claimant) -> A is a chain even though B
        is not itself one of the claimant ids being compared.
        """
        assert BacklogManager._tasks_form_dep_chain(["A", "C"], {"C": {"B"}, "B": {"A"}}) is True


class TestSourceTestPairsDefensiveStemGuard:
    """Cover the `if not source_stem: continue` guard in `_check_source_test_pairs`.

    Today's `_PYTHON_EXTS = (".py",)` makes the guard unreachable through
    `_is_production_source`'s public contract -- any path that passes the
    production-source filter has a non-empty stem. The guard exists to
    keep the rule safe if `_PYTHON_EXTS` is expanded to include a
    non-`.py` extension (where `_source_stem_for_pair_match` would
    legitimately return ""). Patch `_source_stem_for_pair_match` to
    simulate that future-state and confirm the rule swallows the entry
    without raising or emitting an error.
    """

    def test_empty_stem_is_swallowed_without_emitting_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wu_path = tmp_path / "wu.md"
        wu_path.write_text(
            "# Title\n\n## Changes Manifest\n\n| File | Action |\n|------|--------|\n| src/devbench/foo.py | new |\n",
            encoding="utf-8",
        )
        rows = [("E1-F1-S1-T1", "in-queue", str(wu_path))]
        errors: list[str] = []
        monkeypatch.setattr(BacklogManager, "_source_stem_for_pair_match", staticmethod(lambda _p: ""))
        manager = BacklogManager()
        manager._check_source_test_pairs(rows, tmp_path, errors)
        assert errors == []


class _ValidateRuleHarness:
    """Reusable harness for the new validate-rule tests.

    Builds a minimal BACKLOG.md + work-unit files in a tmp_path. Each test
    crafts the specific Manifest content under test, then runs validate()
    and asserts on the relevant error subset.
    """

    INDEX_HEADER: str = (
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n"
        "\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
    )

    @staticmethod
    def make_index(tmp_path: Path, rows: str) -> Path:
        idx = tmp_path / "BACKLOG.md"
        idx.write_text(_ValidateRuleHarness.INDEX_HEADER + rows, encoding="utf-8")
        return idx

    @staticmethod
    def make_task(
        backlog_dir: Path,
        unit_id: str,
        repo: str,
        manifest_rows: str,
        ac_block: str = "- [ ] AC-TEST-001",
        deps_rows: str = "| none | | |",
        status: str = "in-queue",
        task_type: str | None = None,
    ) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        task_type_section = f"## Task Type: {task_type}\n\n" if task_type is not None else ""
        wu.write_text(
            f"# {unit_id}\n\n"
            f"## Status: {status}\n\n"
            f"{task_type_section}"
            f"## Target Repository\n\n"
            f"- **Repo:** `{repo}`\n\n"
            f"## Description\n\nTest task.\n\n"
            f"## Dependencies\n\n"
            f"| ID | Title | Status |\n"
            f"|----|-------|--------|\n"
            f"{deps_rows}\n\n"
            f"## Acceptance Criteria\n\n{ac_block}\n\n"
            f"## Changes Manifest\n\n"
            f"| File | Change |\n"
            f"|------|--------|\n"
            f"{manifest_rows}\n"
            f"## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        return wu


class TestValidateManifestConflicts:
    """Tests for _check_manifest_conflicts (Manifest Conflict Rule)."""

    H = _ValidateRuleHarness

    def test_two_in_queue_tasks_same_path_same_repo_no_dep_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", repo, "| `shared.yaml` | new |\n")
        self.H.make_task(backlog_dir, "EX-F1-S1-T2", repo, "| `shared.yaml` | edit |\n")
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n"
            f"| EX-F1-S1-T2 | T2 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        conflict = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
        assert len(conflict) == 1
        assert "EX-F1-S1-T1" in conflict[0]
        assert "EX-F1-S1-T2" in conflict[0]
        assert "docs/backlog-contract.md" in conflict[0]

    def test_two_tasks_different_repos_same_path_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        # The conflict check is scoped by (repo, path); two different repos
        # legitimately can list the same path.
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", "ex/repo-a", "| `shared.yaml` | new |\n")
        self.H.make_task(backlog_dir, "EX-F2-S1-T1", "ex/repo-b", "| `shared.yaml` | new |\n")
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/repo-a | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F2-S1-T1 | T1 | Task | in-queue | none | ex/repo-b | `backlog/EX-F2-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("Manifest conflict" in e for e in errors)

    def test_two_tasks_with_explicit_dep_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        # When the later Task lists the earlier in its Dependencies, the
        # ownership conflict resolves into a sequential ordering. Use the
        # production-shape E\d+ IDs so the dep-extraction regex matches.
        repo = "ex/foo"
        self.H.make_task(backlog_dir, "E9-F1-S1-T1", repo, "| `shared.yaml` | new |\n")
        self.H.make_task(
            backlog_dir,
            "E9-F1-S1-T2",
            repo,
            "| `shared.yaml` | edit |\n",
            deps_rows="| E9-F1-S1-T1 | dep | proposed |",
        )
        self.H.make_index(
            tmp_path,
            f"| E9-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/E9-F1-S1-T1.md` |\n"
            f"| E9-F1-S1-T2 | T2 | Task | in-queue | E9-F1-S1-T1 | {repo} | `backlog/E9-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("Manifest conflict" in e for e in errors)

    def test_placeholder_strings_filtered(self, tmp_path: Path, backlog_dir: Path) -> None:
        # "(none)" / "(no file changes; ...)" placeholders must NOT trigger conflicts.
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", "ex/foo", "| `(none)` | n/a |\n")
        self.H.make_task(backlog_dir, "EX-F2-S1-T1", "ex/foo", "| `(none)` | n/a |\n")
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F2-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F2-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("Manifest conflict" in e for e in errors)

    def test_in_progress_plus_in_queue_same_path_no_dep_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """FR-3 / AC-8 (db-313): an actively-executing (in-progress) claimant
        is a HARD claimant -- it collides with an in-queue claimant of the
        same path exactly like two in-queue claimants would.
        """
        repo = "ex/foo"
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status="in-progress")
        self.H.make_task(backlog_dir, "EX-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status="in-queue")
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-progress | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n"
            f"| EX-F1-S1-T2 | T2 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        conflict = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
        assert len(conflict) == 1
        assert "EX-F1-S1-T1" in conflict[0]
        assert "EX-F1-S1-T2" in conflict[0]

    def test_in_progress_conflict_survives_flip_to_blocked(self, tmp_path: Path, backlog_dir: Path) -> None:
        """FR-3 / AC-9 (db-313): the verdict (and the emitted error text,
        which embeds only ids, never status) is invariant across a
        HARD-status flip -- in-progress and blocked are both HARD claimants.
        """
        repo = "ex/foo"

        def _run(status_a: str) -> list[str]:
            self.H.make_task(backlog_dir, "EX-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=status_a)
            self.H.make_task(backlog_dir, "EX-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status="in-queue")
            self.H.make_index(
                tmp_path,
                f"| EX-F1-S1-T1 | T1 | Task | {status_a} | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n"
                f"| EX-F1-S1-T2 | T2 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T2.md` |\n",
            )
            errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
            return [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]

        in_progress_conflict = _run("in-progress")
        blocked_conflict = _run("blocked")
        assert len(in_progress_conflict) == 1
        assert len(blocked_conflict) == 1
        assert in_progress_conflict[0] == blocked_conflict[0]

    def test_two_draft_claimants_error_only_under_strict(self, tmp_path: Path, backlog_dir: Path) -> None:
        """FR-4 / AC-10 (db-267): two draft claimants of one path are silent
        under the default run (preserving the all-draft rc=0 authoring gate)
        but emit the new draft/hold ERROR once ``strict=True`` folds SOFT
        claimants into the count.
        """
        repo = "ex/foo"
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status="draft")
        self.H.make_task(backlog_dir, "EX-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status="draft")
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | draft | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n"
            f"| EX-F1-S1-T2 | T2 | Task | draft | none | {repo} | `backlog/EX-F1-S1-T2.md` |\n",
        )
        default_errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("Manifest conflict" in e for e in default_errors)

        strict_errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path, strict=True)
        conflict = [e for e in strict_errors if "Manifest conflict (draft/hold)" in e]
        assert len(conflict) == 1
        assert "EX-F1-S1-T1" in conflict[0]
        assert "EX-F1-S1-T2" in conflict[0]
        assert "shared.yaml" in conflict[0]

    def test_two_draft_claimants_with_explicit_dep_no_error_under_strict(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """FR-4 (db-267): an explicit Dependency between two draft claimants
        resolves the ordering, so ``--strict`` stays silent -- mirrors
        ``test_two_tasks_with_explicit_dep_no_error`` for the SOFT half.
        """
        repo = "ex/foo"
        self.H.make_task(backlog_dir, "E9-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status="draft")
        self.H.make_task(
            backlog_dir,
            "E9-F1-S1-T2",
            repo,
            "| `shared.yaml` | edit |\n",
            status="draft",
            deps_rows="| E9-F1-S1-T1 | dep | draft |",
        )
        self.H.make_index(
            tmp_path,
            f"| E9-F1-S1-T1 | T1 | Task | draft | none | {repo} | `backlog/E9-F1-S1-T1.md` |\n"
            f"| E9-F1-S1-T2 | T2 | Task | draft | E9-F1-S1-T1 | {repo} | `backlog/E9-F1-S1-T2.md` |\n",
        )
        strict_errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path, strict=True)
        assert not any("Manifest conflict" in e for e in strict_errors)


class TestValidateManifestConflictsTransitiveChain:
    """Issue #145 regression: a clean N-1 dep chain among N claimants of the
    same Manifest path satisfies the Manifest Conflict Rule. Pairs do NOT
    need direct dep edges -- transitive reachability is sufficient.
    """

    H = _ValidateRuleHarness
    REPO = "ex/foo"

    def _seed_chain(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        edges: dict[str, list[str]],
        ids: list[str],
    ) -> list[str]:
        """Seed a backlog where every id in ``ids`` claims pyproject.toml,
        with the provided dep edges (mapping blocked_id -> [blocker_ids]).
        Returns the validate() error list.
        """
        for tid in ids:
            blockers = edges.get(tid, [])
            deps_rows = "\n".join(f"| {bid} | dep | proposed |" for bid in blockers) if blockers else "| none | | |"
            self.H.make_task(
                backlog_dir,
                tid,
                self.REPO,
                "| `pyproject.toml` | edit |\n",
                deps_rows=deps_rows,
            )
        rows = "".join(
            f"| {tid} | T | Task | in-queue | "
            f"{','.join(edges.get(tid, [])) or 'none'} | "
            f"{self.REPO} | `backlog/{tid}.md` |\n"
            for tid in ids
        )
        self.H.make_index(tmp_path, rows)
        return BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)

    def test_clean_n_minus_1_chain_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        """5 claimants wired as A <- B <- C <- D <- E (only N-1 = 4 edges).
        Pre-#145 this failed because (A, C), (A, D), (A, E), (B, D), (B, E),
        (C, E) lacked direct edges; transitive reachability accepts.
        """
        ids = ["E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F1-S1-T3", "E1-F1-S1-T4", "E1-F1-S1-T5"]
        edges = {
            ids[1]: [ids[0]],
            ids[2]: [ids[1]],
            ids[3]: [ids[2]],
            ids[4]: [ids[3]],
        }
        errors = self._seed_chain(tmp_path, backlog_dir, edges, ids)
        assert not any("Manifest conflict" in e and "pyproject.toml" in e for e in errors), errors

    def test_chain_with_shortcuts_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Chain backbone A <- B <- C <- D <- E plus extra shortcut edges
        (D <- A, C <- A). Denser than minimum; every pair still comparable
        via the chain backbone.
        """
        ids = ["E2-F1-S1-T1", "E2-F1-S1-T2", "E2-F1-S1-T3", "E2-F1-S1-T4", "E2-F1-S1-T5"]
        edges = {
            ids[1]: [ids[0]],
            ids[2]: [ids[1], ids[0]],  # shortcut: T3 also directly deps on T1
            ids[3]: [ids[2], ids[0]],  # shortcut: T4 also directly deps on T1
            ids[4]: [ids[3]],
        }
        errors = self._seed_chain(tmp_path, backlog_dir, edges, ids)
        assert not any("Manifest conflict" in e and "pyproject.toml" in e for e in errors), errors

    def test_partial_chain_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """3 claimants, only A <- B wired; C is isolated -> rejected."""
        ids = ["E3-F1-S1-T1", "E3-F1-S1-T2", "E3-F1-S1-T3"]
        edges = {ids[1]: [ids[0]]}
        errors = self._seed_chain(tmp_path, backlog_dir, edges, ids)
        conflict = [e for e in errors if "Manifest conflict" in e and "pyproject.toml" in e]
        assert len(conflict) == 1
        for tid in ids:
            assert tid in conflict[0]

    def test_unrelated_claimants_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """3 claimants, zero edges -> rejected."""
        ids = ["E4-F1-S1-T1", "E4-F1-S1-T2", "E4-F1-S1-T3"]
        errors = self._seed_chain(tmp_path, backlog_dir, {}, ids)
        conflict = [e for e in errors if "Manifest conflict" in e and "pyproject.toml" in e]
        assert len(conflict) == 1

    def test_full_pairwise_still_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Backwards compat: backlogs that already wire N*(N-1)/2 direct
        edges keep passing.
        """
        ids = ["E5-F1-S1-T1", "E5-F1-S1-T2", "E5-F1-S1-T3", "E5-F1-S1-T4"]
        edges = {ids[i]: ids[:i] for i in range(1, len(ids))}
        errors = self._seed_chain(tmp_path, backlog_dir, edges, ids)
        assert not any("Manifest conflict" in e and "pyproject.toml" in e for e in errors), errors

    def test_error_message_suggests_chain(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Conflict error message includes a suggested N-1 chain in
        lexical-sort order (operator hint).
        """
        ids = ["E6-F1-S1-T1", "E6-F1-S1-T2", "E6-F1-S1-T3"]
        errors = self._seed_chain(tmp_path, backlog_dir, {}, ids)
        conflict = next(e for e in errors if "Manifest conflict" in e and "pyproject.toml" in e)
        assert "Wire a serial dep chain:" in conflict
        assert "uv run devbench add-dep E6-F1-S1-T2 E6-F1-S1-T1" in conflict
        assert "uv run devbench add-dep E6-F1-S1-T3 E6-F1-S1-T2" in conflict
        assert "or any other DAG that totally orders the set" in conflict


class TestValidateLanguageAcAlignment:
    """Tests for _check_language_ac_alignment (canonical-AC Applicability)."""

    H = _ValidateRuleHarness

    AC_BLOCK_PYTHON_TIER_NO_NA: str = (
        "- [ ] AC-FINAL-001 Every AC-TEST and AC-CYCLE passes.\n"
        "- [ ] AC-FINAL-002 `ruff check src tests` exits zero.\n"
        "- [ ] AC-FINAL-005 `pytest tests/unit -v` exits zero.\n"
        "- [ ] AC-FINAL-014 Coverage 100%.\n"
    )

    AC_BLOCK_PYTHON_TIER_WITH_NA: str = (
        "- [ ] AC-FINAL-001 Every AC-TEST and AC-CYCLE passes.\n"
        "- [ ] AC-FINAL-002 `ruff check src tests` exits zero -- N/A for HCL Tasks (no Python source authored)\n"
        "- [ ] AC-FINAL-005 `pytest tests/unit -v` exits zero -- N/A for HCL Tasks (no Python source authored)\n"
        "- [ ] AC-FINAL-014 Coverage 100% -- N/A for HCL Tasks (no Python source authored)\n"
    )

    def test_hcl_only_task_without_na_suffix_emits_warnings(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `infra/main.tf` | new |\n",
            ac_block=self.AC_BLOCK_PYTHON_TIER_NO_NA,
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        ac_errors = [e for e in errors if "EX-F1-S1-T1" in e and "requires the N/A suffix" in e]
        # AC-FINAL-002, 005, 014 each expected to fire (3 errors)
        assert len(ac_errors) == 3
        assert any("AC-FINAL-002" in e for e in ac_errors)
        assert any("AC-FINAL-005" in e for e in ac_errors)
        assert any("AC-FINAL-014" in e for e in ac_errors)
        assert all("HCL" in e for e in ac_errors)

    def test_hcl_task_with_na_suffix_no_warnings(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `infra/main.tf` | new |\n",
            ac_block=self.AC_BLOCK_PYTHON_TIER_WITH_NA,
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("requires the N/A suffix" in e for e in errors)

    def test_python_task_without_na_suffix_no_warnings(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Python-tier task should NOT have the N/A suffix; rule must skip.
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            ac_block=self.AC_BLOCK_PYTHON_TIER_NO_NA,
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("requires the N/A suffix" in e for e in errors)


class TestValidateSourceTestPairs:
    """Tests for _check_source_test_pairs (source-test atomicity)."""

    H = _ValidateRuleHarness

    def test_python_source_without_test_pair_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `infra/scripts/migrate.py` | new |\n",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        pair_errors = [e for e in errors if "EX-F1-S1-T1" in e and "no matching test in the same Manifest" in e]
        assert len(pair_errors) == 1
        assert "infra/scripts/migrate.py" in pair_errors[0]
        assert "'migrate'" in pair_errors[0]
        assert "docs/source-test-atomicity.md" in pair_errors[0]

    def test_python_source_with_test_pair_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `infra/scripts/migrate.py` | new |\n| `tests/unit/test_migrate.py` | new |\n",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("no matching test in the same Manifest" in e for e in errors)

    def test_namespaced_test_filename_satisfies_pair(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Project convention: tests live under tests/unit/ named like
        # test_<feature>_<basename>.py. The rule accepts these as valid
        # pairs because the source basename appears in the test filename.
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/kanon_cli/telemetry/event.py` | new |\n| `tests/unit/test_telemetry_event.py` | new |\n",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("no matching test in the same Manifest" in e for e in errors)

    def test_init_py_does_not_require_test_pair(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/pkg/__init__.py` | new |\n",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("no matching test in the same Manifest" in e for e in errors)

    def test_test_path_under_services_satisfies_pair(self, tmp_path: Path, backlog_dir: Path) -> None:
        # The rule accepts any test path ending in /test_<basename>; the
        # services/<name>/tests/... convention is honored.
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `services/api/src/handler.py` | new |\n| `services/api/tests/unit/test_handler.py` | new |\n",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("no matching test in the same Manifest" in e for e in errors)


class TestIsTestSourcePath:
    """Direct tests for the shared test-path predicate (FR-4.1 / AC-47).

    ``_is_test_source_path`` is extracted out of ``_is_production_source``
    so the FR-4.1 task-type row classifier and the Rule 14 source-test
    pairing check share a single test/production boundary decision.
    """

    def test_python_under_tests_dir_is_test(self) -> None:
        assert BacklogManager._is_test_source_path("tests/unit/test_foo.py") is True

    def test_python_nested_tests_dir_is_test(self) -> None:
        assert BacklogManager._is_test_source_path("services/api/tests/unit/test_foo.py") is True

    def test_python_outside_tests_dir_is_not_test(self) -> None:
        assert BacklogManager._is_test_source_path("src/foo/bar.py") is False

    def test_non_python_under_tests_dir_is_not_test(self) -> None:
        # The predicate is Python-scoped, matching Rule 14's own scope.
        assert BacklogManager._is_test_source_path("tests/fixtures/data.json") is False

    def test_is_production_source_still_excludes_test_paths(self) -> None:
        """Regression: extracting the helper must not change _is_production_source's behavior."""
        assert BacklogManager._is_production_source("tests/unit/test_foo.py") is False
        assert BacklogManager._is_production_source("services/api/tests/unit/test_foo.py") is False
        assert BacklogManager._is_production_source("src/foo/bar.py") is True


class TestIsDocumentationPath:
    """Direct boundary tests for the ``docs`` task-type predicate (FR-4.1).

    Mirrors ``TestIsTestSourcePath``: ``_is_documentation_path`` is
    deliberately extension-based (``.md`` only), so these cases pin the
    exact boundary rather than inferring it indirectly through
    ``validate()`` scenarios.
    """

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("docs/architecture.md", True),
            ("README.md", True),
            ("docs/guide.MD", True),
            ("docs/guide.rst", False),
            ("docs/guide.txt", False),
            ("src/foo.py", False),
            ("pyproject.toml", False),
        ],
    )
    def test_documentation_path_boundary(self, path: str, expected: bool) -> None:
        assert BacklogManager._is_documentation_path(path) is expected


class TestIsChorePath:
    """Direct boundary tests for the ``chore`` task-type predicate (FR-4.1).

    Mirrors ``TestIsTestSourcePath``: ``_is_chore_path`` unions the
    non-Markdown ``_NON_PY_EXTS_TO_TIER`` tiers with the chore-only
    lockfile/legacy-config extensions in ``_CHORE_EXTRA_EXTS``. These
    cases pin the exact boundary, including the deliberate Markdown
    exclusion (Markdown belongs to the ``docs`` type, not ``chore``).
    """

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("pyproject.toml", True),
            ("uv.lock", True),
            ("setup.cfg", True),
            ("tox.ini", True),
            ("requirements.txt", True),
            ("config.yaml", True),
            ("config.yml", True),
            ("package.json", True),
            ("infra/main.tf", True),
            ("docs/guide.md", False),
            ("README.md", False),
            ("src/foo.py", False),
        ],
    )
    def test_chore_path_boundary(self, path: str, expected: bool) -> None:
        assert BacklogManager._is_chore_path(path) is expected


class TestExtractTaskType:
    """Direct tests for the ``## Task Type:`` line extractor (FR-4.1 / AC-45)."""

    def test_missing_section_returns_none(self) -> None:
        content = "# EX-F1-S1-T1\n\n## Status: in-queue\n\n## Description\n\nbody\n"
        assert BacklogManager._extract_task_type(content) is None

    @pytest.mark.parametrize(
        "task_type",
        ["behavior-fix", "feature", "test-only", "refactor", "docs", "chore"],
    )
    def test_present_section_returns_lowercased_value(self, task_type: str) -> None:
        content = f"# EX-F1-S1-T1\n\n## Status: in-queue\n\n## Task Type: {task_type}\n\n## Description\n\nbody\n"
        assert BacklogManager._extract_task_type(content) == task_type

    def test_present_section_is_case_and_whitespace_normalized(self) -> None:
        content = "# EX-F1-S1-T1\n\n## Status: in-queue\n\n##  Task Type:   Test-Only  \n\n## Description\n\nx\n"
        assert BacklogManager._extract_task_type(content) == "test-only"


class TestValidateTaskTypeTaxonomy:
    """FR-4.1 (AC-45, AC-46, AC-47): six-type taxonomy parsing and per-type
    Changes Manifest invariants enforced by ``validate-backlog``."""

    H = _ValidateRuleHarness

    @pytest.mark.parametrize(
        "task_type,manifest_rows",
        [
            ("behavior-fix", "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n"),
            ("feature", "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n"),
            ("test-only", "| `tests/unit/test_foo.py` | new |\n"),
            ("refactor", "| `src/foo.py` | modify |\n| `tests/unit/test_foo.py` | modify |\n"),
            ("docs", "| `docs/architecture.md` | new |\n"),
            ("chore", "| `pyproject.toml` | modify |\n"),
        ],
    )
    def test_all_six_types_parse_without_unrecognized_error(
        self, tmp_path: Path, backlog_dir: Path, task_type: str, manifest_rows: str
    ) -> None:
        """AC-45: each of the six declared types is recognized (no unrecognized-type error)."""
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", "ex/foo", manifest_rows, task_type=task_type)
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("unrecognized '## Task Type:'" in e for e in errors), (
            f"Type {task_type!r} should be recognized; got errors: {errors}"
        )

    def test_unknown_type_fails_naming_allowed_set(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-45: an unrecognized type fails validate-backlog naming the full allowed set."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            task_type="performance-tuning",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "performance-tuning" in e]
        assert matching, f"Expected an unrecognized-type error; got: {errors}"
        for allowed in ("behavior-fix", "feature", "test-only", "refactor", "docs", "chore"):
            assert allowed in matching[0], f"Allowed-set message missing {allowed!r}: {matching[0]}"

    def test_missing_task_type_defaults_to_behavior_fix(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A missing '## Task Type:' section defaults to behavior-fix (the strictest
        type), never to an exempt type. Proven by the fact that a manifest with zero
        production-source rows, which WOULD be rejected for an explicit behavior-fix
        declaration, is ALSO rejected when the section is absent."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `tests/unit/test_foo.py` | new |\n",
            task_type=None,
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "behavior-fix" in e and "production-source" in e]
        assert matching, f"Expected a behavior-fix zero-production-source error by default; got: {errors}"

    def test_docs_task_touching_src_is_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-46: a docs task touching src/ is rejected."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `docs/guide.md` | new |\n| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            task_type="docs",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "src/foo.py" in e and "docs" in e]
        assert matching, f"Expected a docs-invariant violation naming src/foo.py; got: {errors}"

    def test_docs_task_with_only_markdown_rows_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `docs/guide.md` | new |\n| `README.md` | modify |\n",
            task_type="docs",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("task-type invariant" in e for e in errors), f"Unexpected invariant error: {errors}"

    def test_test_only_task_touching_production_is_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-46: a test-only task touching production source is rejected."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            task_type="test-only",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "src/foo.py" in e and "test-only" in e]
        assert matching, f"Expected a test-only-invariant violation naming src/foo.py; got: {errors}"

    def test_test_only_task_with_only_test_rows_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `tests/unit/test_foo.py` | new |\n",
            task_type="test-only",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("task-type invariant" in e for e in errors), f"Unexpected invariant error: {errors}"

    def test_chore_task_touching_src_is_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-46: a chore task touching src/ is rejected."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `pyproject.toml` | modify |\n| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            task_type="chore",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "src/foo.py" in e and "chore" in e]
        assert matching, f"Expected a chore-invariant violation naming src/foo.py; got: {errors}"

    def test_chore_task_with_only_lockfile_rows_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `pyproject.toml` | modify |\n| `uv.lock` | modify |\n",
            task_type="chore",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("task-type invariant" in e for e in errors), f"Unexpected invariant error: {errors}"

    @pytest.mark.parametrize("task_type", ["behavior-fix", "feature"])
    def test_gated_task_with_zero_production_rows_is_rejected(
        self, tmp_path: Path, backlog_dir: Path, task_type: str
    ) -> None:
        """AC-46: a gated task (behavior-fix or feature) with zero production-source rows is rejected."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `tests/unit/test_foo.py` | new |\n",
            task_type=task_type,
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and task_type in e and "production-source" in e]
        assert matching, f"Expected a zero-production-source error for {task_type}; got: {errors}"

    @pytest.mark.parametrize("task_type", ["behavior-fix", "feature"])
    def test_gated_task_with_production_row_no_error(self, tmp_path: Path, backlog_dir: Path, task_type: str) -> None:
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            task_type=task_type,
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("production-source" in e and "EX-F1-S1-T1" in e for e in errors), (
            f"Unexpected zero-production-source error: {errors}"
        )

    def test_refactor_task_manifest_rows_not_invariant_checked(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Approach step 6: refactor's manifest rows validate here (type parses); the
        green-green runtime check is delivered separately by E4-F4-S1-T2, so no
        per-row invariant fires for a refactor task regardless of row shape."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/foo.py` | modify |\n| `docs/guide.md` | modify |\n| `pyproject.toml` | modify |\n",
            task_type="refactor",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("task-type invariant" in e or "production-source" in e for e in errors), (
            f"Unexpected task-type error for refactor: {errors}"
        )

    def test_rejection_message_names_row_type_and_invariant(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-E4-F2-S1-T1-5: every invariant rejection names the offending manifest
        row, the declared type, and the violated invariant."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `docs/guide.md` | new |\n| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            task_type="docs",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "src/foo.py" in e]
        assert matching, f"Expected an invariant error; got: {errors}"
        message = matching[0]
        assert "EX-F1-S1-T1" in message  # the row
        assert "docs" in message  # the declared type
        assert "task-type invariant" in message  # the violated invariant is named

    @pytest.mark.parametrize(
        "status,expect_error",
        [
            ("done", False),
            ("declined", False),
            ("in-queue", True),
        ],
    )
    def test_terminal_status_skip_is_scoped_not_a_blanket_bypass(
        self, tmp_path: Path, backlog_dir: Path, status: str, expect_error: bool
    ) -> None:
        """The terminal-status skip (manager.py:2427 'if row_status in
        _TERMINAL_CHILD_STATUSES: continue') exempts only done/declined
        Tasks from rule 21, not every Task. A done or declined Task with no
        '## Task Type:' section and zero production-source rows produces NO
        rule-21 error, while an otherwise identical in-queue Task DOES --
        proving the skip is scoped to terminal status rather than being a
        universal escape hatch (mutation-verified: deleting the skip
        condition must flip the done/declined cases below from pass to
        fail)."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `tests/unit/test_foo.py` | new |\n",
            task_type=None,
            status=status,
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | {status} | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "behavior-fix" in e and "production-source" in e]
        if expect_error:
            assert matching, (
                f"Expected a behavior-fix zero-production-source error for "
                f"non-terminal status={status!r}; got: {errors}"
            )
        else:
            assert not matching, (
                f"Terminal status={status!r} should be skipped by rule 21 entirely; got matching errors: {matching}"
            )

    def test_malformed_manifest_skips_taxonomy_check_without_crashing(self, tmp_path: Path, backlog_dir: Path) -> None:
        """The ManifestParseError swallow (manager.py 'except
        ManifestParseError: continue' inside ``_check_task_type_taxonomy``)
        mirrors the established pattern used by every other
        Manifest-consuming validate rule (the manifest-conflict check, the
        AC-language-tier check, the no-glob check and the Rule 14
        source-test pair check): when a Task's '## Changes Manifest' table
        cannot be parsed -- here a row with 3 columns instead of the
        required 2 -- rule 21 skips enforcement for that Task instead of
        crashing validate() or deriving a task-type-invariant error from a
        partially/incorrectly parsed Manifest. The malformed row is not
        silently accepted as valid: ``parse_manifest`` genuinely raises
        ``ManifestParseError`` for it (verified directly below).

        This test does NOT claim some other rule catches the malformed
        table on the author's behalf -- it doesn't, for a repo with no
        configured ``checkout_directory`` (verified below: the full
        validate() pipeline returns zero errors for this row_id). That is
        a pre-existing gap shared by all six ManifestParseError call sites
        in manager.py, predating this task; fixing it repo-wide is out of
        this task's scope (AC-E4-F2-S1-T1-1..6 concern only the taxonomy).
        What this test pins is narrower and genuinely rule-21-specific:
        rule 21 does not crash and does not manufacture a task-type
        diagnostic out of a table it could not fully parse.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        malformed_content = (
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/foo.py` | new | extra column |\n"
        )
        with pytest.raises(ManifestParseError):
            parse_manifest(malformed_content)

        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `src/foo.py` | new | extra column |\n",
            task_type="behavior-fix",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        taxonomy_errors = [
            e for e in errors if "EX-F1-S1-T1" in e and ("task-type invariant" in e or "production-source" in e)
        ]
        assert not taxonomy_errors, (
            f"A malformed Changes Manifest must not surface a rule-21 "
            f"error derived from a partially parsed table; got: {taxonomy_errors}"
        )
        # Documents (does not merely assume) that no OTHER rule catches this
        # malformed row for a repo without a configured checkout_directory
        # either: the whole pipeline is silent on it today. This is the
        # pre-existing, repo-wide gap named in the docstring above, pinned
        # here so a future fix to any of the six sites is a deliberate,
        # visible change to this assertion rather than an unnoticed drift.
        all_errors_for_row = [e for e in errors if "EX-F1-S1-T1" in e]
        assert not all_errors_for_row, (
            f"Known pre-existing gap: no validate() rule currently surfaces "
            f"a malformed Changes Manifest table for a repo with no "
            f"configured checkout_directory. If this assertion starts "
            f"failing, some rule now catches it -- update this test's "
            f"docstring to name that rule rather than reintroducing the "
            f"'not it, but something else' claim this test replaced; "
            f"got: {all_errors_for_row}"
        )

    def test_docs_task_with_markdown_and_doc_pin_test_rows_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """db-300 / AC-17: a docs task legitimately owning a
        documentation-pinning test row (e.g. ``tests/test_docs/test_guide_pin.py``)
        is not a false-positive rejection -- the docs OR-list also accepts
        test-source rows."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `docs/guide.md` | new |\n| `tests/test_docs/test_guide_pin.py` | new |\n",
            task_type="docs",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("task-type invariant" in e for e in errors), f"Unexpected invariant error: {errors}"

    def test_chore_task_with_config_and_changelog_rows_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """db-300 / AC-18: a chore task legitimately owning ``CHANGELOG.md``
        is not a false-positive rejection -- the chore OR-list also accepts
        documentation/markdown rows."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `pyproject.toml` | modify |\n| `CHANGELOG.md` | modify |\n",
            task_type="chore",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("task-type invariant" in e for e in errors), f"Unexpected invariant error: {errors}"

    def test_docs_task_with_production_source_row_still_fails(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-19: the docs OR-list widening never lets production Python
        source under src/ through -- every named classifier rejects it."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `docs/guide.md` | new |\n| `src/devbench/foo.py` | new |\n",
            task_type="docs",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "src/devbench/foo.py" in e and "docs" in e]
        assert matching, f"Expected a docs-invariant violation naming src/devbench/foo.py; got: {errors}"

    def test_chore_task_with_production_source_row_still_fails(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-19: the chore OR-list widening never lets production Python
        source under src/ through -- every named classifier rejects it."""
        self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `pyproject.toml` | modify |\n| `src/devbench/foo.py` | new |\n",
            task_type="chore",
        )
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [e for e in errors if "EX-F1-S1-T1" in e and "src/devbench/foo.py" in e and "chore" in e]
        assert matching, f"Expected a chore-invariant violation naming src/devbench/foo.py; got: {errors}"

    @pytest.mark.parametrize(
        "task_type,manifest_rows,expect_error",
        [
            ("behavior-fix", "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n", False),
            ("behavior-fix", "| `tests/unit/test_foo.py` | new |\n", True),
            ("feature", "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n", False),
            ("feature", "| `tests/unit/test_foo.py` | new |\n", True),
            ("test-only", "| `tests/unit/test_foo.py` | new |\n", False),
            ("test-only", "| `src/foo.py` | new |\n", True),
            ("refactor", "| `src/foo.py` | modify |\n| `docs/guide.md` | modify |\n", False),
            ("docs", "| `docs/guide.md` | new |\n", False),
            ("docs", "| `tests/test_docs/test_guide_pin.py` | new |\n", False),
            ("docs", "| `src/foo.py` | new |\n", True),
            ("chore", "| `pyproject.toml` | modify |\n", False),
            ("chore", "| `CHANGELOG.md` | modify |\n", False),
            ("chore", "| `src/foo.py` | new |\n", True),
        ],
    )
    def test_task_type_row_invariant_matrix_unchanged(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        task_type: str,
        manifest_rows: str,
        expect_error: bool,
    ) -> None:
        """db-300: pins the accept/reject behavior of all six task types
        across the OR-list widening. Proves the docs/chore widening only
        ADDS acceptance of documentation-pinning-test / markdown rows
        respectively -- it does not alter behavior-fix, feature, test-only,
        or refactor behavior at all, and production-source rows under src/
        are still rejected for docs/chore/test-only."""
        self.H.make_task(backlog_dir, "EX-F1-S1-T1", "ex/foo", manifest_rows, task_type=task_type)
        self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        matching = [
            e for e in errors if "EX-F1-S1-T1" in e and ("task-type invariant" in e or "production-source" in e)
        ]
        if expect_error:
            assert matching, f"Expected a task-type-invariant error for {task_type}; got: {errors}"
        else:
            assert not matching, f"Unexpected task-type-invariant error for {task_type}; got: {matching}"


class TestValidateRequiredSections:
    """E209: every Task work-unit must declare Status, Dependencies, and Changes Manifest."""

    def test_missing_dependencies_section_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nTask body.\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("missing required section '## Dependencies'" in e and "EX-F1-S1-T1" in e for e in errors)

    def test_complete_task_emits_no_required_section_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        _ValidateRuleHarness.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `f.py` | new |\n| `tests/unit/test_f.py` | new |\n",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("missing required section" in e for e in errors)


class TestValidateStatusEnum:
    """E209: every parsed ``## Status:`` value must be in VALID_STATUSES."""

    def test_unknown_status_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: pending-review\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("invalid '## Status:' value 'pending-review'" in e and "EX-F1-S1-T1" in e for e in errors)

    def test_hold_status_is_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        _ValidateRuleHarness.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `f.py` | new |\n| `tests/unit/test_f.py` | new |\n",
            status="hold",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | hold | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("invalid '## Status:'" in e for e in errors)

    def test_draft_status_accepted_for_task(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-2: draft is a valid status for Task work units."""
        _ValidateRuleHarness.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `f.py` | new |\n| `tests/unit/test_f.py` | new |\n",
            status=STATUS_DRAFT,
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | draft | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any(STATUS_DRAFT in e and "EX-F1-S1-T1" in e for e in errors), (
            f"Task with draft status should not produce an error; got: {errors}"
        )

    @pytest.mark.parametrize(
        "unit_id,unit_type,index_row",
        [
            (
                "EX",
                WorkUnitType.EPIC.value,
                "| EX | An Epic | Epic | draft | none | ex/foo | `backlog/EX.md` |\n",
            ),
            (
                "EX-F1",
                WorkUnitType.FEATURE.value,
                "| EX-F1 | A Feature | Feature | draft | none | ex/foo | `backlog/EX-F1.md` |\n",
            ),
            (
                "EX-F1-S1",
                WorkUnitType.STORY.value,
                "| EX-F1-S1 | A Story | Story | draft | none | ex/foo | `backlog/EX-F1-S1.md` |\n",
            ),
        ],
    )
    def test_draft_status_rejected_for_non_task(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        unit_id: str,
        unit_type: str,
        index_row: str,
    ) -> None:
        """AC-189-10: draft status is rejected for Epic, Feature, and Story work units."""
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(
            f"# {unit_id}\n\n## Status: draft\n\n## Description\n\nSummary.\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(tmp_path, index_row)
        errors = BacklogManager().validate(idx, tmp_path)
        assert any(
            f'Status "{STATUS_DRAFT}" is only valid for Task work units' in e and unit_id in e and unit_type in e
            for e in errors
        ), f"Expected draft-for-non-task error for {unit_id} ({unit_type}); got: {errors}"

    def test_draft_rejected_for_epic_error_message_format(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-10: error message for draft Epic names the type explicitly."""
        wu = backlog_dir / "EX.md"
        wu.write_text(
            "# EX\n\n## Status: draft\n\n## Description\n\nEpic summary.\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX | An Epic | Epic | draft | none | ex/foo | `backlog/EX.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        matching = [e for e in errors if "EX" in e and STATUS_DRAFT in e]
        assert matching, f"Expected at least one error mentioning EX and {STATUS_DRAFT!r}; got: {errors}"
        expected_msg = (
            f'EX: Status "{STATUS_DRAFT}" is only valid for Task work units; EX is type {WorkUnitType.EPIC.value}.'
        )
        assert any(e == expected_msg for e in matching), f"Error message format mismatch; got: {matching}"


class TestValidateDepIdFormat:
    """E209: dep-row IDs in '## Dependencies' must match the canonical regex."""

    def test_malformed_dep_id_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| TASK-123 | Task | dep |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | TASK-123 | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("dependency ID 'TASK-123' does not match" in e and "EX-F1-S1-T1" in e for e in errors)

    def test_canonical_dep_id_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        _ValidateRuleHarness.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `f1.py` | new |\n| `tests/unit/test_f1.py` | new |\n",
        )
        wu = backlog_dir / "EX-F1-S1-T2.md"
        wu.write_text(
            "# EX-F1-S1-T2\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n"
            "| ID | Type | Reason |\n|----|------|--------|\n"
            "| EX-F1-S1-T1 | Task | dep |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n"
            "| `f2.py` | New |\n| `tests/unit/test_f2.py` | new |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | T2 | Task | in-queue | EX-F1-S1-T1 | ex/foo | `backlog/EX-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("does not match the" in e for e in errors)


class TestValidateBranchUniqueness:
    """E219: no two Tasks may derive the same branch name."""

    def test_collision_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Both tasks override the branch to the same explicit name.
        for unit_id in ("EX-F1-S1-T1", "EX-F1-S1-T2"):
            wu = backlog_dir / f"{unit_id}.md"
            wu.write_text(
                f"# {unit_id}\n\n"
                "## Status: in-queue\n\n"
                "- **Branch:** `feat/shared`\n\n"
                "## Description\n\nx\n\n"
                "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
                "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
                "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
                f"| `{unit_id}.py` | new |\n| `tests/unit/test_{unit_id.lower()}.py` | new |\n\n"
                "## Definition of Done\n\n- [ ] Done\n",
                encoding="utf-8",
            )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | T2 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any(
            "Branch collision on 'feat/shared'" in e and "EX-F1-S1-T1" in e and "EX-F1-S1-T2" in e for e in errors
        )

    def test_canonical_branches_unique_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        for unit_id in ("EX-F1-S1-T1", "EX-F1-S1-T2"):
            _ValidateRuleHarness.make_task(
                backlog_dir,
                unit_id,
                "ex/foo",
                f"| `{unit_id}.py` | new |\n| `tests/unit/test_{unit_id.lower()}.py` | new |\n",
            )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | T2 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("Branch collision" in e for e in errors)

    def test_single_branch_mode_skips_check(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Both tasks override to the same branch; under single-PR mode
        # this is legitimate (the configured single_branch is shared).
        for unit_id in ("EX-F1-S1-T1", "EX-F1-S1-T2"):
            wu = backlog_dir / f"{unit_id}.md"
            wu.write_text(
                f"# {unit_id}\n\n"
                "## Status: in-queue\n\n"
                "- **Branch:** `feat/single-pr`\n\n"
                "## Description\n\nx\n\n"
                "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
                "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
                "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
                f"| `{unit_id}.py` | new |\n| `tests/unit/test_{unit_id.lower()}.py` | new |\n\n"
                "## Definition of Done\n\n- [ ] Done\n",
                encoding="utf-8",
            )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | T2 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T2.md` |\n",
        )
        # Patch the runtime config so single_branch is set; the rule
        # must short-circuit and emit no collision error.
        from unittest.mock import patch as _patch

        from devbench.config_loader import GitOpsConfig, RuntimeConfig

        runtime = RuntimeConfig(git_ops=GitOpsConfig(single_branch="feat/single-pr"))
        with _patch("devbench.config.RUNTIME_CONFIG", runtime):
            errors = BacklogManager().validate(idx, tmp_path)
        assert not any("Branch collision" in e for e in errors)

    def test_configured_branch_prefix_used_for_canonical_derivation(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Canonical (non-explicit-Branch:) tasks derive the namespaced branch
        name when git_ops.branch_prefix is configured -- matching what
        cmd_git_ops/BacklogParser actually push to, so this check can't pass
        while a real push would still collide across two devbench workspaces."""
        for unit_id in ("EX-F1-S1-T1", "EX-F1-S1-T2"):
            _ValidateRuleHarness.make_task(
                backlog_dir,
                unit_id,
                "ex/foo",
                f"| `{unit_id}.py` | new |\n| `tests/unit/test_{unit_id.lower()}.py` | new |\n",
            )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n"
            "| EX-F1-S1-T2 | T2 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T2.md` |\n",
        )
        from unittest.mock import patch as _patch

        from devbench.config_loader import GitOpsConfig, RuntimeConfig

        assert (
            BacklogManager._derive_branch_for_row("EX-F1-S1-T1", "", tmp_path, "wg_004") == "backlog/wg_004/ex-f1-s1-t1"
        )

        runtime = RuntimeConfig(git_ops=GitOpsConfig(branch_prefix="wg_004"))
        with _patch("devbench.config.RUNTIME_CONFIG", runtime):
            errors = BacklogManager().validate(idx, tmp_path)
        assert not any("Branch collision" in e for e in errors)


class TestRequiredSectionsRowDefensiveSkips:
    """Cover the empty-file-path-string defensive skip in `_check_required_sections`.

    `_parse_backlog_rows` never returns rows with empty file_path strings
    today, but the rule is hardened against that future-state to avoid
    a crash when reading a directory path. Hit the branch directly via
    a hand-crafted row tuple."""

    def test_empty_file_path_string_is_skipped(self, tmp_path: Path) -> None:
        manager = BacklogManager()
        errors: list[str] = []
        rows = [("EX-F1-S1-T1", "in-queue", "")]
        manager._check_required_sections(rows, tmp_path, errors)
        assert errors == []


class TestValidateNoPlaceholderManifestRows:
    """Issue #117: reject TBD placeholder rows in active-Task Manifests."""

    def test_tbd_first_cell_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n"
            "| TBD | Executor agent: replace this row with the actual files |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("Changes Manifest still has placeholder row 'TBD'" in e and "EX-F1-S1-T1" in e for e in errors)

    def test_tbd_lowercase_also_caught(self, tmp_path: Path, backlog_dir: Path) -> None:
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| tbd | placeholder |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any("placeholder row 'tbd'" in e for e in errors)

    def test_real_manifest_emits_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        _ValidateRuleHarness.make_task(
            backlog_dir,
            "EX-F1-S1-T1",
            "ex/foo",
            "| `f.py` | new |\n| `tests/unit/test_f.py` | new |\n",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("placeholder row" in e for e in errors)

    def test_done_status_skips_check(self, tmp_path: Path, backlog_dir: Path) -> None:
        # Done tasks are terminal; the placeholder rule does not apply.
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: done\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| TBD | placeholder |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        idx = _ValidateRuleHarness.make_index(
            tmp_path,
            "| EX-F1-S1-T1 | T1 | Task | done | none | ex/foo | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("placeholder row" in e for e in errors)

    def test_empty_file_path_string_is_skipped(self, tmp_path: Path) -> None:
        """Defensive guard: rows with empty file_path should not crash the rule.

        Production rows always have a file_path; cover the branch directly
        for 100% coverage. Belongs to TestValidateNoPlaceholderManifestRows
        but appended at module-tail because the file was reformatted.
        """
        manager = BacklogManager()
        errors: list[str] = []
        rows = [("EX-F1-S1-T1", "in-queue", "")]
        manager._check_no_placeholder_manifest_rows(rows, tmp_path, errors)
        assert errors == []


class TestValidateNoOrphanPathTokens:
    """Rule 20: backtick-quoted path-shaped tokens in AC / DoD must appear in
    the Task's Changes Manifest after normalisation, OR be marked read-only
    via a trailing ``(ref)`` suffix. Gated by
    ``RUNTIME_CONFIG.validate.check_orphan_path_tokens``; defaults ON (set
    ``false`` to opt out).
    """

    H = _ValidateRuleHarness

    @staticmethod
    def _runtime_with_rule_on(repo: str, checkout_directory: str | None) -> RuntimeConfig:
        return RuntimeConfig(
            repos={repo: RepoConfig(checkout_directory=checkout_directory)},
            validate=ValidateConfig(check_orphan_path_tokens=True),
        )

    @staticmethod
    def _make_task_with_sections(
        backlog_dir: Path,
        unit_id: str,
        repo: str,
        manifest_rows: str,
        ac_block: str,
        dod_block: str = "- [ ] Done",
        description: str = "Test task.",
    ) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(
            f"# {unit_id}\n\n"
            f"## Status: in-queue\n\n"
            f"## Target Repository\n\n"
            f"- **Repo:** `{repo}`\n\n"
            f"## Description\n\n{description}\n\n"
            f"## Dependencies\n\n"
            f"| ID | Title | Status |\n"
            f"|----|-------|--------|\n"
            f"| none | | |\n\n"
            f"## Acceptance Criteria\n\n{ac_block}\n\n"
            f"## Changes Manifest\n\n"
            f"| File | Change |\n"
            f"|------|--------|\n"
            f"{manifest_rows}\n"
            f"## Definition of Done\n\n{dod_block}\n",
            encoding="utf-8",
        )
        return wu

    def _validate(self, tmp_path: Path, runtime_config: RuntimeConfig) -> list[str]:
        with patch("devbench.config.RUNTIME_CONFIG", runtime_config):
            return BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)

    def _orphan_errors(self, errors: list[str], unit_id: str) -> list[str]:
        return [e for e in errors if e.startswith(f"{unit_id}: orphan path ")]

    # ---------------------------------------------------------------------
    # Must-fire cases
    # ---------------------------------------------------------------------

    def test_orphan_path_in_ac_fires(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/real.py` | new |\n| `tests/unit/test_real.py` | new |\n",
            "- [ ] AC-FUNC-001: `src/imaginary.py` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        orphans = self._orphan_errors(errors, "EX-F1-S1-T1")
        assert len(orphans) == 1
        assert "src/imaginary.py" in orphans[0]
        assert "Acceptance Criteria" in orphans[0]

    def test_orphan_path_in_dod_fires(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/real.py` | new |\n| `tests/unit/test_real.py` | new |\n",
            "- [ ] AC-FUNC-001: behavioural check.",
            dod_block="- [ ] Entry committed to `docs/release-notes.md`.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        orphans = self._orphan_errors(errors, "EX-F1-S1-T1")
        assert len(orphans) == 1
        assert "docs/release-notes.md" in orphans[0]
        assert "Definition of Done" in orphans[0]

    def test_task_id_placeholder_does_not_match_resolved_manifest(self, tmp_path: Path, backlog_dir: Path) -> None:
        """The original caylent-telemetry-teardown bug: AC says <TASK-ID>, manifest has resolved ID."""
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `destroy-log/EX-F1-S1-T1.md` | new |\n",
            "- [ ] AC-DOC-001: `destroy-log/<TASK-ID>.md` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        orphans = self._orphan_errors(errors, "EX-F1-S1-T1")
        assert len(orphans) == 1
        assert "destroy-log/<TASK-ID>.md" in orphans[0]

    def test_case_mismatch_fires(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Linux paths are case-sensitive; ``Foo.py`` is not the same as ``foo.py``."""
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: `src/Foo.py` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert any("src/Foo.py" in e for e in self._orphan_errors(errors, "EX-F1-S1-T1"))

    # ---------------------------------------------------------------------
    # Must-NOT-fire cases
    # ---------------------------------------------------------------------

    def test_path_matching_manifest_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: `src/foo.py` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_checkout_directory_prefix_normalises(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC writes ``<checkout_dir>/path`` while manifest holds ``path`` (post-rule-11 strip)."""
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: `my-repo/src/foo.py` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, "my-repo"))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_canonical_pytest_command_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-FINAL-005 / 006 / 007 use multi-token backtick blocks; rule must not fire."""
        repo = "ex/foo"
        ac = (
            "- [ ] AC-FUNC-001: feature is delivered.\n"
            "- [ ] AC-FINAL-005: `pytest tests/unit -v` exits zero.\n"
            "- [ ] AC-FINAL-006: `pytest tests/integration -v` exits zero.\n"
            "- [ ] AC-FINAL-007: `pytest tests/functional -v` exits zero.\n"
        )
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            ac,
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_url_token_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: response payload references `https://api.example.com/v1/foo`.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_shell_flag_token_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: command runs with `--cov=src`.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_glob_token_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: every `*.py` file in the manifest is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_trailing_slash_normalised(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC writes ``foo/`` while manifest holds ``foo`` -- normalised, the strings match."""
        repo = "ex/foo"
        # Use a path-shaped token without an extension so the manifest-prefix
        # match path drives the assertion (extension would short-circuit).
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: contents at `src/foo.py/` are correct.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_path_in_description_section_does_not_fire(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Description / Approach prose is out of scope; only AC + DoD are checked."""
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: behavioural.",
            description=(
                "### Approach\n\nThe task reads `src/legacy/auth.py` (a path that does not appear in the manifest)."
            ),
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_inline_ref_marker_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: behaviour matches the format described in `src/legacy/auth.py` (ref).",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_story_unit_short_circuits(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Stories have no Manifest; the rule must not fire on Story IDs."""
        repo = "ex/foo"
        story = backlog_dir / "EX-F1-S1.md"
        story.write_text(
            "# EX-F1-S1\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nA story spec mentions `src/anywhere.py` in prose.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n",
            encoding="utf-8",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1 | S1 | Story | in-queue | none | {repo} | `backlog/EX-F1-S1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1") == []

    def test_flag_off_does_nothing(self, tmp_path: Path, backlog_dir: Path) -> None:
        """With the rule disabled, even a clear orphan path is silent."""
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            "- [ ] AC-FUNC-001: `src/imaginary.py` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        rt_off = RuntimeConfig(
            repos={repo: RepoConfig(checkout_directory=None)},
            validate=ValidateConfig(check_orphan_path_tokens=False),
        )
        errors = self._validate(tmp_path, rt_off)
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    # ---------------------------------------------------------------------
    # Coverage corner cases (low-traffic branches in the orphan-check helpers)
    # ---------------------------------------------------------------------

    def test_path_shaped_via_manifest_dir_prefix_match(self, tmp_path: Path, backlog_dir: Path) -> None:
        """``_is_path_shaped`` final ``return`` branch: a token that has
        no known extension and no built-in directory prefix, but whose
        first segment matches a directory observed in the Task's parsed
        Manifest, must be treated as path-shaped and trigger the rule
        when it isn't itself in the Manifest.
        """
        repo = "ex/foo"
        # Manifest entry under ``custom/`` -- not in _ORPHAN_KNOWN_PREFIXES
        # (which has src/, tests/, infra/, docs/, backlog/, config/) and
        # not carrying a known extension. The orphan token uses the same
        # ``custom/`` dir prefix but names a file that isn't in the
        # manifest, so the manifest-dir-prefix branch fires.
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `custom/real_data` | new |\n",
            "- [ ] AC-FUNC-001: `custom/imaginary_data` is created.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        orphans = self._orphan_errors(errors, "EX-F1-S1-T1")
        assert len(orphans) == 1
        assert "custom/imaginary_data" in orphans[0]

    def test_dot_slash_prefix_normalised_to_match(self, tmp_path: Path, backlog_dir: Path) -> None:
        """``_normalise_orphan_path`` ``./`` strip branch: an AC token
        written as ``./src/real.py`` must normalise to ``src/real.py``
        and match a Manifest entry of ``src/real.py``.
        """
        repo = "ex/foo"
        self._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/real.py` | new |\n| `tests/unit/test_real.py` | new |\n",
            "- [ ] AC-FUNC-001: `./src/real.py` exposes the new API.",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        # The ``./`` prefix is stripped before matching so the token is
        # recognised as in-Manifest -- no orphan error should fire.
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_missing_work_unit_file_is_skipped_silently(self, tmp_path: Path, backlog_dir: Path) -> None:
        """``_check_no_orphan_path_tokens`` skips Tasks whose work-unit
        file does not exist on disk. The BACKLOG.md row points at a path
        that was never written -- the rule must short-circuit at the
        ``not wu_path.is_file()`` guard rather than crash.
        """
        repo = "ex/foo"
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        # Note: NO write of backlog/EX-F1-S1-T1.md -- the row is a stale
        # pointer (the kind of state that arises mid-rename or mid-
        # refactor). Validation must remain silent on this Task for the
        # orphan rule (other rules may flag elsewhere).
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_malformed_manifest_skipped_silently(self, tmp_path: Path, backlog_dir: Path) -> None:
        """``_check_one_task_orphan_paths`` swallows ``ManifestParseError``
        and returns early -- another rule already reports the malformed
        manifest, so this rule must not double-report.
        """
        repo = "ex/foo"
        # Hand-write a work-unit whose Changes Manifest has a pipe-
        # prefixed row with the wrong column count, which ``parse_manifest``
        # rejects with ManifestParseError (see manifest.py:201). The
        # well-formed header + separator above the bad row keep the
        # parser past the section-detect step so it reaches the body
        # walk and raises on the malformed data row.
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            f"- **Repo:** `{repo}`\n\n"
            "## Description\n\nTest task.\n\n"
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            "| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001: `src/foo.py` exists.\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n"
            "|------|--------|\n"
            "| `src/foo.py` | new | extra-column |\n"  # 3 cells, parser rejects
            "\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        # Manifest parse failure is reported by another rule; the orphan
        # rule must produce ZERO orphan-prefixed errors for this Task.
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []

    def test_missing_definition_of_done_section_is_skipped(self, tmp_path: Path, backlog_dir: Path) -> None:
        """``_check_one_task_orphan_paths`` skips a section whose extracted
        body is empty (the inner ``if not body: continue`` branch). When
        a Task work-unit lacks the ``## Definition of Done`` heading
        entirely, ``sections.get("Definition of Done", "")`` returns ``""``
        and the per-section walk must short-circuit cleanly.
        """
        repo = "ex/foo"
        # Hand-build a work-unit that has Acceptance Criteria but does
        # NOT have a Definition of Done header at all. Other validation
        # rules may complain about the missing section; this test only
        # asserts that the orphan-path rule does not raise on it.
        wu = backlog_dir / "EX-F1-S1-T1.md"
        wu.write_text(
            "# EX-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            f"- **Repo:** `{repo}`\n\n"
            "## Description\n\nTest task.\n\n"
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            "| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001: behaviour check.\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n"
            "|------|--------|\n"
            "| `src/foo.py` | new |\n"
            "| `tests/unit/test_foo.py` | new |\n",
            # Note: NO ``## Definition of Done`` section follows.
            encoding="utf-8",
        )
        self.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = self._validate(tmp_path, self._runtime_with_rule_on(repo, None))
        # The orphan rule must produce ZERO orphan-prefixed errors --
        # the missing DoD section is silently skipped at the empty-body
        # guard. A separate required-sections rule may emit its own
        # error for the same Task, but that error does not start with
        # the orphan-rule prefix.
        assert self._orphan_errors(errors, "EX-F1-S1-T1") == []


class TestIsValidCitation:
    """BacklogManager.is_valid_citation (FR-4.5): commit hash or task id shape.

    A citation is either a git commit hash (7-40 lowercase hex characters,
    covering both abbreviated and full SHA forms) or a canonical work-unit
    ID matching the same shape enforced by Check 17's dependency-ID format
    rule. Free text is never a valid citation -- it must be independently
    verifiable.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "abc1234",
            "0123456789abcdef0123456789abcdef01234567",
            "deadbeef",
            "E4-F4-S1-T2",
            "E4",
            "E4-F4",
            "E4-F4-S1",
        ],
        ids=[
            "commit-min-length",
            "commit-40-char",
            "commit-hex-word",
            "task-id-full",
            "task-id-epic-only",
            "task-id-epic-feature",
            "task-id-epic-feature-story",
        ],
    )
    def test_valid_citations_accepted(self, value: str) -> None:
        assert BacklogManager.is_valid_citation(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "abc12",  # too short for a commit hash
            "not-a-commit-hash-or-task-id",
            "ABCDEF1",  # uppercase hex is never a git-reported hash
            "closed by a prior task",
            "1234567890abcdef1234567890abcdef123456789",  # 43 chars, too long
        ],
        ids=[
            "empty",
            "whitespace-only",
            "too-short-hash",
            "free-text",
            "uppercase-hex",
            "prose-sentence",
            "too-long-hash",
        ],
    )
    def test_invalid_citations_rejected(self, value: str) -> None:
        assert BacklogManager.is_valid_citation(value) is False

    def test_citation_with_surrounding_whitespace_is_trimmed(self) -> None:
        assert BacklogManager.is_valid_citation("  abc1234  ") is True


class TestValidateAlreadySatisfiedDeclineCitation:
    """Check 22: an already-satisfied decline must cite a commit or task id (FR-4.5).

    An uncited already-satisfied decline is rejected by validate-backlog's
    comment-format checks; a cited decline is accepted and the citation is
    preserved verbatim in the Comments section.
    """

    H = _ValidateRuleHarness

    @staticmethod
    def _append_declined_comment(wu_path: Path, message: str) -> None:
        content = wu_path.read_text(encoding="utf-8")
        entry = f"[2026-01-01 00:00 UTC] [backlog_manager] [DECLINED] {message}\n"
        content += ("\n## Comments\n\n" + entry) if "## Comments" not in content else ("\n" + entry)
        wu_path.write_text(content, encoding="utf-8")

    def _errors_for(self, tmp_path: Path, backlog_dir: Path, row_id: str, declined_message: str) -> list[str]:
        wu = self.H.make_task(
            backlog_dir,
            row_id,
            "ex/foo",
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            status="declined",
        )
        self._append_declined_comment(wu, declined_message)
        idx = self.H.make_index(
            tmp_path,
            f"| {row_id} | T | Task | declined | none | ex/foo | `backlog/{row_id}.md` |\n",
        )
        return BacklogManager().validate(idx, tmp_path)

    def test_uncited_already_satisfied_decline_is_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        errors = self._errors_for(tmp_path, backlog_dir, "EX-F1-S1-T1", "already-satisfied")
        matches = [e for e in errors if "EX-F1-S1-T1" in e and "already-satisfied" in e]
        assert len(matches) == 1
        assert "citation" in matches[0]

    def test_cited_already_satisfied_decline_with_commit_hash_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        errors = self._errors_for(tmp_path, backlog_dir, "EX-F1-S1-T2", "already-satisfied (citing abc1234)")
        assert not any("EX-F1-S1-T2" in e and "already-satisfied" in e for e in errors)

    def test_cited_already_satisfied_decline_with_task_id_accepted(self, tmp_path: Path, backlog_dir: Path) -> None:
        errors = self._errors_for(tmp_path, backlog_dir, "EX-F1-S1-T3", "already-satisfied (citing E1-F1-S1-T9)")
        assert not any("EX-F1-S1-T3" in e and "already-satisfied" in e for e in errors)

    def test_declined_for_unrelated_reason_without_citation_unaffected(self, tmp_path: Path, backlog_dir: Path) -> None:
        errors = self._errors_for(tmp_path, backlog_dir, "EX-F1-S1-T4", "duplicate of E1-F1-S1-T2")
        assert not any("EX-F1-S1-T4" in e and "citation" in e for e in errors)

    def test_non_declined_task_unaffected_even_with_already_satisfied_text(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        wu = self.H.make_task(
            backlog_dir,
            "EX-F1-S1-T5",
            "ex/foo",
            "| `src/foo.py` | new |\n| `tests/unit/test_foo.py` | new |\n",
            status="in-queue",
        )
        self._append_declined_comment(wu, "already-satisfied")
        idx = self.H.make_index(
            tmp_path,
            "| EX-F1-S1-T5 | T | Task | in-queue | none | ex/foo | `backlog/EX-F1-S1-T5.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert not any("EX-F1-S1-T5" in e and "citation" in e for e in errors)

    def test_missing_file_path_row_skipped(self, tmp_path: Path) -> None:
        """Defensive guard: rows with empty file_path should not crash the rule."""
        manager = BacklogManager()
        errors: list[str] = []
        rows = [("EX-F1-S1-T1", "declined", "")]
        manager._check_already_satisfied_decline_citation(rows, tmp_path, errors)
        assert errors == []


class TestAutoRequeueOnDeclineTransition:
    """Issue #147: ``_set_status`` fires the auto-requeue cascade for every
    terminal transition (``done`` AND ``declined``), not just ``mark_done``.

    Each test wires a blocked source whose ``[BLOCKED_PENDING_PROPOSAL]``
    marker references a single dep, drives the dep into the terminal status
    under test through the corresponding public API, and asserts the source
    flipped to ``in-queue`` with the cascade audit comment.
    """

    def _build(self, tmp_path: Path, dep_status: str) -> tuple[Path, Path]:
        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        dep_file = _unit_body("E0-F1-S1-T2", dep_status)
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep", dep_status),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        dep_path = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        return index, dep_path

    def test_mark_declined_cascades_requeue(self, tmp_path: Path) -> None:
        """``mark_declined`` is a terminal transition and must fire the cascade."""
        index, dep_path = self._build(tmp_path, "in-queue")
        BacklogManager().mark_declined(dep_path, index, "E0-F1-S1-T2", "scope changed")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[CASCADE_RESOLVED]" in src

    def test_set_status_to_declined_cascades(self, tmp_path: Path) -> None:
        """``force_status -> declined`` exercises the same code path as decline."""
        index, dep_path = self._build(tmp_path, "in-queue")
        BacklogManager().force_status(dep_path, index, "E0-F1-S1-T2", "declined")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[CASCADE_RESOLVED]" in src

    def test_force_status_to_done_cascades(self, tmp_path: Path) -> None:
        """``force_status -> done`` (legacy path) must continue to cascade."""
        index, dep_path = self._build(tmp_path, "in-queue")
        BacklogManager().force_status(dep_path, index, "E0-F1-S1-T2", "done")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src
        assert "[CASCADE_RESOLVED]" in src

    def test_cascade_idempotent_under_repeat_set_status(self, tmp_path: Path) -> None:
        """A second ``_set_status`` call to the same terminal target does NOT
        re-run the cascade. The audit comment count stays at 1."""
        index, dep_path = self._build(tmp_path, "in-queue")
        mgr = BacklogManager()
        mgr.force_status(dep_path, index, "E0-F1-S1-T2", "declined")
        # First fire updated T1 to in-queue. Calling _set_status again with
        # the same terminal status must be a no-op cascade-wise even though
        # the rest of the writes happen.
        mgr._set_status(dep_path, index, "E0-F1-S1-T2", "declined")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert src.count("[CASCADE_RESOLVED]") == 1

    def test_non_terminal_transition_does_not_cascade(self, tmp_path: Path) -> None:
        """A transition to ``in-progress`` must not fire the cascade."""
        index, dep_path = self._build(tmp_path, "in-queue")
        BacklogManager().force_status(dep_path, index, "E0-F1-S1-T2", "in-progress")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        # T1 stays blocked; cascade did not run.
        assert "## Status: blocked" in src
        assert "[CASCADE_RESOLVED]" not in src


class TestSetStatusWritesWuClaimedAudit:
    """Issue #185(b): every Task transition into ``in-progress`` must
    append a ``[WU_CLAIMED]`` audit-comment row to the work-unit file so
    the status-timer fallback can recover the claim timestamp after the
    orchestrator log has rotated. Stories / Features / Epics are skipped
    (their status is auto-rolled from children and never user-claimed).
    """

    def test_in_progress_transition_writes_wu_claimed_audit(self, tmp_path: Path) -> None:
        wu_body = _unit_body("E0-F1-S1-T1", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-queue")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-progress")
        content = wu_path.read_text(encoding="utf-8")
        # The audit row must include both the [WU_CLAIMED] marker and
        # the canonical `Set <id> to 'in-progress'` phrase so the audit
        # regex in cli._latest_audit_in_progress_ts matches.
        assert "[WU_CLAIMED]" in content
        assert "Set E0-F1-S1-T1 to 'in-progress'" in content
        assert "[agent/orchestrator]" in content

    def test_non_in_progress_transition_does_not_write_audit(self, tmp_path: Path) -> None:
        """A transition to ``blocked`` / ``done`` / ``in-queue`` MUST NOT
        write a ``[WU_CLAIMED]`` row. Only ``in-progress`` is the claim
        event we want to record."""
        wu_body = _unit_body("E0-F1-S1-T1", "in-progress")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-progress")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-queue")
        content = wu_path.read_text(encoding="utf-8")
        assert "[WU_CLAIMED]" not in content

    def test_story_transition_does_not_write_audit(self, tmp_path: Path) -> None:
        """IDs without ``-T`` (Story / Feature / Epic) are auto-rolled
        from children; the ``[WU_CLAIMED]`` write is skipped."""
        wu_body = _unit_body("E0-F1-S1", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1", "Story", "in-queue")],
            files={"E0-F1-S1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1", "in-progress")
        content = wu_path.read_text(encoding="utf-8")
        assert "[WU_CLAIMED]" not in content


class TestWuClaimedSessionSuffix:
    """Spec section 4.4.7 / AC-192-6: [WU_CLAIMED] audit format extension.

    When ``DEVBENCH_SESSION_NAME`` is set the audit comment becomes
    ``[WU_CLAIMED] Set <id> to 'in-progress' session=<name>``.
    When the env var is absent the legacy format is unchanged.
    """

    def test_set_status_with_session_name_appends_session_suffix(self, tmp_path: Path) -> None:
        """_set_status with session_name produces 'session=<name>' in [WU_CLAIMED] row."""
        wu_body = _unit_body("E0-F1-S1-T1", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-queue")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-progress", session_name="prod-01")
        content = wu_path.read_text(encoding="utf-8")
        assert "[WU_CLAIMED] Set E0-F1-S1-T1 to 'in-progress' session=prod-01" in content

    def test_set_status_without_session_name_omits_session_suffix(self, tmp_path: Path) -> None:
        """_set_status without session_name produces the bare [WU_CLAIMED] format (legacy)."""
        wu_body = _unit_body("E0-F1-S1-T1", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-queue")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-progress")
        content = wu_path.read_text(encoding="utf-8")
        assert "[WU_CLAIMED] Set E0-F1-S1-T1 to 'in-progress'" in content
        assert "session=" not in content

    def test_set_status_session_name_none_omits_session_suffix(self, tmp_path: Path) -> None:
        """_set_status with explicit session_name=None uses the bare [WU_CLAIMED] format."""
        wu_body = _unit_body("E0-F1-S1-T1", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-queue")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-progress", session_name=None)
        content = wu_path.read_text(encoding="utf-8")
        assert "[WU_CLAIMED] Set E0-F1-S1-T1 to 'in-progress'" in content
        assert "session=" not in content

    @pytest.mark.parametrize(
        "session_name",
        ["alpha", "beta", "my-session-01", "prod"],
        ids=["alpha", "beta", "hyphenated", "prod"],
    )
    def test_set_status_session_name_parametrized(self, tmp_path: Path, session_name: str) -> None:
        """session_name value is reproduced verbatim in the [WU_CLAIMED] audit row."""
        wu_body = _unit_body("E0-F1-S1-T1", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-queue")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-progress", session_name=session_name)
        content = wu_path.read_text(encoding="utf-8")
        assert f"session={session_name}" in content

    def test_set_status_session_name_ignored_for_non_in_progress(self, tmp_path: Path) -> None:
        """session_name has no effect when the target status is not 'in-progress'."""
        wu_body = _unit_body("E0-F1-S1-T1", "in-progress")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Task", "in-progress")],
            files={"E0-F1-S1-T1": wu_body},
        )
        wu_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        BacklogManager()._set_status(wu_path, index, "E0-F1-S1-T1", "in-queue", session_name="ignored-session")
        content = wu_path.read_text(encoding="utf-8")
        assert "session=ignored-session" not in content


class TestSetStatusWritesUnblockedAudit:
    """Issue #153: when the cascade flips ``blocked -> in-queue`` it writes
    a ``[CASCADE_RESOLVED]`` audit; sync-blocked separately writes
    ``[UNBLOCKED] deps satisfied``. Both markers feed the panel renderer
    supersession filter.
    """

    def test_cascade_audit_uses_cascade_resolved_tag(self, tmp_path: Path) -> None:
        markers = "[2026-04-19 14:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=["E0-F1-S1-T2"],
            comments=markers,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "Dep", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "[CASCADE_RESOLVED]" in src
        assert "[AUTO_UNBLOCKED]" in src  # backward-compatibility tag


class TestValidateDepCycle4Node:
    """Issue #151: validate-backlog rejects N-node dependency cycles via
    DFS-with-recursion-stack. The 4-node case T1->T2->T3->T4->T1 is the
    canonical regression: a naive 'has any dep' check would miss it.
    """

    def _index_with_deps(self, tmp_path: Path, dep_map: dict[str, str]) -> Path:
        """Build a minimal BACKLOG.md whose Full Index lists the given dep map."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        rows = []
        for unit_id, deps in dep_map.items():
            rows.append(f"| {unit_id} | T | Task | in-queue | {deps} | example/repo | `backlog/{unit_id}.md` |")
            (backlog_dir / f"{unit_id}.md").write_text(
                f"# {unit_id}\n\n## Status: in-queue\n",
                encoding="utf-8",
            )
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
        return index

    @staticmethod
    def _run_check(manager: BacklogManager, index: Path, tmp_path: Path) -> list[str]:
        """FR-1: `_check_dep_cycles` now takes `rows` + `workspace_root` too.

        The fixture work-unit files here carry no `## Dependencies` table and
        no `## Comments` section, so `rows`/`workspace_root` contribute zero
        extra edges -- these regression cases stay pinned to index-only
        behavior while exercising the new signature.
        """
        errors: list[str] = []
        rows = manager._parse_backlog_rows(index)
        manager._check_dep_cycles(index, rows, tmp_path, errors)
        return errors

    def test_4_node_cycle_rejected(self, tmp_path: Path) -> None:
        index = self._index_with_deps(
            tmp_path,
            {
                "E0-F1-S1-T1": "E0-F1-S1-T4",
                "E0-F1-S1-T2": "E0-F1-S1-T1",
                "E0-F1-S1-T3": "E0-F1-S1-T2",
                "E0-F1-S1-T4": "E0-F1-S1-T3",
            },
        )
        errors = self._run_check(BacklogManager(), index, tmp_path)
        assert any("cycle" in e for e in errors)

    def test_5_node_cycle_rejected(self, tmp_path: Path) -> None:
        index = self._index_with_deps(
            tmp_path,
            {
                "E0-F1-S1-T1": "E0-F1-S1-T5",
                "E0-F1-S1-T2": "E0-F1-S1-T1",
                "E0-F1-S1-T3": "E0-F1-S1-T2",
                "E0-F1-S1-T4": "E0-F1-S1-T3",
                "E0-F1-S1-T5": "E0-F1-S1-T4",
            },
        )
        errors = self._run_check(BacklogManager(), index, tmp_path)
        assert any("cycle" in e for e in errors)

    def test_4_node_dag_accepted(self, tmp_path: Path) -> None:
        """T4->T3->T2->T1 (no back edge) must NOT trigger a cycle error."""
        index = self._index_with_deps(
            tmp_path,
            {
                "E0-F1-S1-T1": "None",
                "E0-F1-S1-T2": "E0-F1-S1-T1",
                "E0-F1-S1-T3": "E0-F1-S1-T2",
                "E0-F1-S1-T4": "E0-F1-S1-T3",
            },
        )
        errors = self._run_check(BacklogManager(), index, tmp_path)
        assert errors == []

    def test_self_dep_reported(self, tmp_path: Path) -> None:
        """A 1-node cycle (self-dep) is also caught."""
        index = self._index_with_deps(tmp_path, {"E0-F1-S1-T1": "E0-F1-S1-T1"})
        errors = self._run_check(BacklogManager(), index, tmp_path)
        assert any("cycle" in e for e in errors)

    def test_disjoint_cycle_reported_once(self, tmp_path: Path) -> None:
        """Two disjoint cycles each report exactly once (no duplicate from re-traversal)."""
        index = self._index_with_deps(
            tmp_path,
            {
                "E0-F1-S1-T1": "E0-F1-S1-T2",
                "E0-F1-S1-T2": "E0-F1-S1-T1",
                "E0-F1-S1-T3": "E0-F1-S1-T4",
                "E0-F1-S1-T4": "E0-F1-S1-T3",
            },
        )
        errors = self._run_check(BacklogManager(), index, tmp_path)
        cycle_errors = [e for e in errors if "cycle" in e]
        assert len(cycle_errors) == 2

    def test_missing_index_no_crash(self, tmp_path: Path) -> None:
        """The cycle check on a missing BACKLOG.md returns silently."""
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(tmp_path / "missing.md", [], tmp_path, errors)
        assert errors == []

    def test_summary_row_skipped_when_cell_count_mismatches(self, tmp_path: Path) -> None:
        """Status Summary rows have a different cell count and must NOT be
        confused with Full Index rows when building the dependency graph.
        """
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| short | row | with | wrong-cell-count |\n"
            "| E0-F1-S1-T1 | T | Task | in-queue | None | r | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        graph = BacklogManager()._build_dependency_graph(index)
        assert "E0-F1-S1-T1" in graph
        assert "short" not in graph


class TestCheckDepCyclesUnionOfChannels:
    """FR-1 (db-253 Gap 1, db-299 Defect 2): `_check_dep_cycles` unions the
    index `Dependencies` column with each non-terminal Task's own
    `## Dependencies` table and `[BLOCKED_PENDING_PROPOSAL]` markers. Before
    the fix, `validate()` was blind to a cycle that existed only in the
    table/marker channels because the index cells stayed `none`.
    """

    def test_marker_only_cycle_detected(self, tmp_path: Path) -> None:
        """Two non-terminal tasks with MUTUAL markers and index cells 'none'."""
        marker_to_t2 = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        marker_to_t1 = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T1\n"
        t1 = _unit_body("E0-F1-S1-T1", "in-queue", comments=marker_to_t2)
        t2 = _unit_body("E0-F1-S1-T2", "in-queue", comments=marker_to_t1)
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "T1", "in-queue"), ("E0-F1-S1-T2", "T2", "in-queue")],
            files={"E0-F1-S1-T1": t1, "E0-F1-S1-T2": t2},
        )
        errors = BacklogManager().validate(index, tmp_path)
        assert any("dependency cycle detected" in e for e in errors), errors

    def test_work_unit_table_cycle_detected(self, tmp_path: Path) -> None:
        """Two non-terminal tasks with MUTUAL `## Dependencies` table rows and
        index cells 'none'.
        """
        t1 = _unit_body("E0-F1-S1-T1", "in-queue", deps=["E0-F1-S1-T2"])
        t2 = _unit_body("E0-F1-S1-T2", "in-queue", deps=["E0-F1-S1-T1"])
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "T1", "in-queue"), ("E0-F1-S1-T2", "T2", "in-queue")],
            files={"E0-F1-S1-T1": t1, "E0-F1-S1-T2": t2},
        )
        errors = BacklogManager().validate(index, tmp_path)
        assert any("dependency cycle detected" in e for e in errors), errors

    def test_add_dep_x2_makes_validate_report_cycle(self, tmp_path: Path) -> None:
        """AC-3: `add_dep(T1,T4)` then `add_dep(T4,T1)` makes `validate()`
        report a cycle end-to-end, because the union reads the
        `## Dependencies` tables and markers that `add_dep` writes.
        """
        from devbench.backlog.proposal import add_dep

        t1 = _unit_body("E0-F1-S1-T1", "in-queue")
        t4 = _unit_body("E0-F1-S1-T4", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "T1", "in-queue"), ("E0-F1-S1-T4", "T4", "in-queue")],
            files={"E0-F1-S1-T1": t1, "E0-F1-S1-T4": t4},
        )
        add_dep(
            backlog_root=tmp_path / "backlog",
            backlog_index=index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T4",
        )
        add_dep(
            backlog_root=tmp_path / "backlog",
            backlog_index=index,
            blocked_task_id="E0-F1-S1-T4",
            blocker_task_id="E0-F1-S1-T1",
        )
        errors = BacklogManager().validate(index, tmp_path)
        assert any("dependency cycle detected" in e for e in errors), errors

    def test_terminal_unit_stale_marker_not_a_cycle(self, tmp_path: Path) -> None:
        """AC-4: a done/declined task's stale marker contributes no edge, so
        it cannot resurrect a historical cycle even though the OTHER side of
        the pair is a live, non-terminal marker edge.
        """
        stale_marker = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        live_marker = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T1\n"
        t1 = _unit_body("E0-F1-S1-T1", "declined", comments=stale_marker)
        t2 = _unit_body("E0-F1-S1-T2", "blocked", comments=live_marker)
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "T1", "declined"), ("E0-F1-S1-T2", "T2", "blocked")],
            files={"E0-F1-S1-T1": t1, "E0-F1-S1-T2": t2},
        )
        errors = BacklogManager().validate(index, tmp_path)
        assert not any("dependency cycle detected" in e for e in errors), errors


class TestValidateManifestConflictsThroughNonClaimant:
    """FR-5 (db-311): `_tasks_form_dep_chain` traverses through a non-claimant
    intermediate so a correctly-ordered chain is not a false Manifest
    conflict, while genuinely unordered claimants (even considering
    non-claimants) still conflict.
    """

    H = _ValidateRuleHarness
    REPO = "ex/foo"

    def test_chain_through_non_claimant_intermediate_no_conflict(self, tmp_path: Path, backlog_dir: Path) -> None:
        """T_b -> T_x(non-claimant) -> T_a resolves the ownership conflict
        even though T_x never appears in the ownership map for the shared
        path (db-311): T_x's Manifest claims a different file.
        """
        self.H.make_task(backlog_dir, "E7-F1-S1-T1", self.REPO, "| `pyproject.toml` | edit |\n")  # T_a
        self.H.make_task(
            backlog_dir,
            "E7-F1-S1-T2",  # T_x, non-claimant intermediate
            self.REPO,
            "| `other.yaml` | edit |\n",
            deps_rows="| E7-F1-S1-T1 | dep | proposed |",
        )
        self.H.make_task(
            backlog_dir,
            "E7-F1-S1-T3",  # T_b
            self.REPO,
            "| `pyproject.toml` | edit |\n",
            deps_rows="| E7-F1-S1-T2 | dep | proposed |",
        )
        self.H.make_index(
            tmp_path,
            "| E7-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/E7-F1-S1-T1.md` |\n"
            "| E7-F1-S1-T2 | T2 | Task | in-queue | E7-F1-S1-T1 | ex/foo | `backlog/E7-F1-S1-T2.md` |\n"
            "| E7-F1-S1-T3 | T3 | Task | in-queue | E7-F1-S1-T2 | ex/foo | `backlog/E7-F1-S1-T3.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("Manifest conflict" in e and "pyproject.toml" in e for e in errors), errors

    def test_genuinely_unordered_claimants_still_conflict(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-13: two claimants with no path even through non-claimants still
        conflict; a bystander non-claimant task (unrelated dep-free) must not
        change the verdict.
        """
        self.H.make_task(backlog_dir, "E8-F1-S1-T1", self.REPO, "| `pyproject.toml` | edit |\n")
        self.H.make_task(backlog_dir, "E8-F1-S1-T2", self.REPO, "| `pyproject.toml` | edit |\n")
        self.H.make_task(backlog_dir, "E8-F1-S1-T3", self.REPO, "| `other.yaml` | edit |\n")
        self.H.make_index(
            tmp_path,
            "| E8-F1-S1-T1 | T1 | Task | in-queue | none | ex/foo | `backlog/E8-F1-S1-T1.md` |\n"
            "| E8-F1-S1-T2 | T2 | Task | in-queue | none | ex/foo | `backlog/E8-F1-S1-T2.md` |\n"
            "| E8-F1-S1-T3 | T3 | Task | in-queue | none | ex/foo | `backlog/E8-F1-S1-T3.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        conflict = [e for e in errors if "Manifest conflict" in e and "pyproject.toml" in e]
        assert len(conflict) == 1


class TestCheckDanglingMarkers:
    """FR-7 (db-253 Gap 2b): a well-formed marker referencing a WU-ID absent
    from the index is a `validate()` error, instead of silently surviving
    until `reconcile-cascade` trips on it.
    """

    def test_marker_referencing_absent_id_is_error(self, tmp_path: Path) -> None:
        marker = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99\n"
        t1 = _unit_body("E0-F1-S1-T1", "blocked", comments=marker)
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Source", "blocked")],
            files={"E0-F1-S1-T1": t1},
        )
        errors = BacklogManager().validate(index, tmp_path)
        expected = (
            "work unit E0-F1-S1-T1: [BLOCKED_PENDING_PROPOSAL] marker references "
            "unknown task 'E0-F1-S1-T99' -- the referenced task is not in the "
            "index; remove the marker or fix the reference (blocks reconcile-cascade)."
        )
        assert expected in errors

    def test_marker_referencing_known_id_is_not_dangling(self, tmp_path: Path) -> None:
        marker = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        t1 = _unit_body("E0-F1-S1-T1", "blocked", comments=marker)
        t2 = _unit_body("E0-F1-S1-T2", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Source", "blocked"), ("E0-F1-S1-T2", "Target", "in-queue")],
            files={"E0-F1-S1-T1": t1, "E0-F1-S1-T2": t2},
        )
        errors = BacklogManager().validate(index, tmp_path)
        assert not any("marker references unknown task" in e for e in errors), errors

    def test_terminal_unit_dangling_marker_not_reported(self, tmp_path: Path) -> None:
        """A done Task's dangling marker is not reported: terminal units are
        skipped, matching the FR-1 cycle-union gating.
        """
        marker = "[2026-06-01 00:00 UTC] [agent/operator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99\n"
        t1 = _unit_body("E0-F1-S1-T1", "done", comments=marker)
        index = _write_workspace(
            tmp_path,
            rows=[("E0-F1-S1-T1", "Source", "done")],
            files={"E0-F1-S1-T1": t1},
        )
        errors = BacklogManager().validate(index, tmp_path)
        assert not any("marker references unknown task" in e for e in errors), errors


class TestValidateBrokenAndCanonicalBacklogFormat:
    """Tests for the zero-row integrity check added to BacklogManager.validate().

    AC-FUNC-001: validate() returns the new integrity error when zero work-unit
    rows are parsed from ## Full Work Unit Index.
    AC-FUNC-002: the new error is the FIRST entry in the error list (fires before
    rule-3 orphan checks).
    AC-FUNC-005/006: broken and canonical fixture workspaces exist on disk.
    AC-FUNC-007: all three tests pass.
    """

    _FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "backlog_format"

    def test_validate_rejects_broken_backlog_format(self) -> None:
        """Broken fixture uses a 4-column table under a non-canonical heading -- validator must reject it.

        The first error must be the zero-row integrity message (AC-FUNC-002).
        """
        broken_root = self._FIXTURES_ROOT / "broken"
        backlog_index = broken_root / "BACKLOG.md"

        manager = BacklogManager()
        errors = manager.validate(backlog_index, broken_root)

        assert errors, "Expected at least one error from the broken fixture"
        # Issue #174: the broken fixture has no '## Full Work Unit Index'
        # heading at all, so the "header text row not found" error is the
        # first (most-actionable) entry; the zero-row error remains below
        # it for backward-compatibility.
        assert errors[0].startswith("No '## Full Work Unit Index' header text row found"), (
            f"Expected missing-header error as first entry, got: {errors[0]!r}"
        )
        assert any(e.startswith("No work-unit rows parsed from") for e in errors), (
            f"Expected zero-row integrity error to also fire, got: {errors!r}"
        )
        assert "ID | Title | Type | Status | Dependencies | Repo | File Path" in errors[0]

    def test_validate_accepts_canonical_backlog_format(self) -> None:
        """Canonical fixture uses 7-column / ## Full Work Unit Index -- validator must accept it."""
        canonical_root = self._FIXTURES_ROOT / "canonical"
        backlog_index = canonical_root / "BACKLOG.md"

        manager = BacklogManager()
        errors = manager.validate(backlog_index, canonical_root)

        zero_row_errors = [e for e in errors if "No work-unit rows parsed from" in e]
        assert zero_row_errors == [], f"Canonical fixture must not trigger the zero-row error; got: {zero_row_errors}"

    def test_validate_existing_canonical_fixture_still_passes(self) -> None:
        """Regression: tests/fixtures/activity/BACKLOG.md must not trigger the zero-row error."""
        activity_root = Path(__file__).parent.parent / "fixtures" / "activity"
        backlog_index = activity_root / "BACKLOG.md"

        manager = BacklogManager()
        errors = manager.validate(backlog_index, activity_root)

        zero_row_errors = [e for e in errors if "No work-unit rows parsed from" in e]
        assert zero_row_errors == [], f"Activity fixture must not trigger the zero-row error; got: {zero_row_errors}"

    def test_validate_skips_full_index_rows_with_wrong_cell_count(self, tmp_path: Path) -> None:
        """Cover the cell-count-mismatch continue branch in _check_full_index_has_rows.

        A row inside `## Full Work Unit Index` whose split-by-pipe cell
        count does not equal BACKLOG_INDEX_CELL_COUNT is skipped (the
        ``continue`` at manager.py:332). If it is the only row, the
        zero-row integrity error fires downstream.
        """
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E1 | only-four | cells |\n"
        )
        manager = BacklogManager()
        errors = manager.validate(backlog_index, tmp_path)
        assert errors
        assert any("No work-unit rows parsed from" in e for e in errors)

    def test_validate_rejects_reordered_header_columns(self, tmp_path: Path) -> None:
        """Issue #174: a header with the right columns in the wrong order is rejected.

        The header below swaps Title and Type. ``validate-backlog`` MUST
        flag this with a column-order error so the operator can fix it
        before ``devbench report`` crashes on the same file.
        """
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Type | Title | Status | Dependencies | Repo | File Path |\n"
            "|----|------|-------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Task | T | in-queue | None | r | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        manager = BacklogManager()
        errors = manager.validate(backlog_index, tmp_path)
        assert any("does not match the canonical column order/spelling" in e for e in errors), (
            f"Expected column-order error, got: {errors!r}"
        )

    def test_validate_rejects_renamed_header_column(self, tmp_path: Path) -> None:
        """Issue #174: a header where a column name is renamed is rejected.

        Renames ``Dependencies`` -> ``Deps``. validate-backlog MUST reject.
        """
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Deps | Repo | File Path |\n"
            "|----|-------|------|--------|------|------|-----------|\n"
            "| E0-F1-S1-T1 | T | Task | in-queue | None | r | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        manager = BacklogManager()
        errors = manager.validate(backlog_index, tmp_path)
        assert any("does not match the canonical column order/spelling" in e for e in errors), (
            f"Expected column-spelling error, got: {errors!r}"
        )

    def test_validate_rejects_header_with_wrong_cell_count(self, tmp_path: Path) -> None:
        """Issue #174: a header with fewer/more than 7 columns is rejected with a cell-count error."""
        backlog_index = tmp_path / "BACKLOG.md"
        # 6 columns instead of 7 (Repo column dropped)
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | File Path |\n"
            "|----|-------|------|--------|--------------|-----------|\n"
            "| E0-F1-S1-T1 | T | Task | in-queue | None | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        manager = BacklogManager()
        errors = manager.validate(backlog_index, tmp_path)
        assert any("header row" in e and "cells; expected" in e for e in errors), (
            f"Expected header cell-count error, got: {errors!r}"
        )

    def test_scan_skips_stray_data_row_with_blank_id(self, tmp_path: Path) -> None:
        """Coverage for the ``continue`` branch in ``_scan_full_index_rows``
        when a 9-cell pipe-row follows the header/separator but the ID
        cell is blank (or contains the literal "id" / a leading "-").
        Such rows are silently skipped instead of counted as work units.
        """
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            # 9-cell row with a blank ID (e.g. accidental separator-style
            # row left over from a hand edit). Must be skipped.
            "|  | placeholder | Task | in-queue | None | r | `backlog/x.md` |\n"
            "| E0-F1-S1-T1 | T | Task | in-queue | None | r | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        header, separator, count = BacklogManager._scan_full_index_rows(backlog_index)
        assert header is not None and len(header) == BACKLOG_INDEX_CELL_COUNT
        assert separator is not None
        # Only the real row is counted; the blank-ID row was skipped.
        assert count == 1

    def test_validate_rejects_missing_separator_row(self, tmp_path: Path) -> None:
        """Issue #174: canonical header followed by data rows without a separator is rejected."""
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "| E0-F1-S1-T1 | T | Task | in-queue | None | r | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        manager = BacklogManager()
        errors = manager.validate(backlog_index, tmp_path)
        assert any("separator row" in e and "is missing" in e for e in errors), (
            f"Expected missing-separator error, got: {errors!r}"
        )


# ---------------------------------------------------------------------------
# GREEN_CHECK emoji constant used in tick-checkbox tests (U+2705)
# ---------------------------------------------------------------------------
_GREEN_CHECK = "\u2705"
_EM_DASH = "\u2014"


class TestTickCompletionCheckboxes:
    """Tests for BacklogManager._tick_completion_checkboxes (AC-FUNC-001 through AC-FUNC-007).

    E12-F1-S1-T1: helper rewrites unchecked / legacy-checked lines in
    ## Acceptance Criteria and ## Definition of Done sections to
    ``- [x] <content> \u2705`` (U+2705 green check, NOT U+2014 em-dash).
    Lines outside those two sections are never modified.
    """

    # ------------------------------------------------------------------
    # AC-FUNC-001: basic rewrite under ## Acceptance Criteria
    # ------------------------------------------------------------------

    def test_tick_completion_checkboxes_basic_ac(self, tmp_path: Path) -> None:
        """Unchecked AC line is rewritten to checked + green-check (U+2705)."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n## Status: done\n\n## Acceptance Criteria\n\n- [ ] AC-FUNC-001: foo\n\n## Comments\n",
            encoding="utf-8",
        )

        BacklogManager()._tick_completion_checkboxes(wu_file)

        result = wu_file.read_text(encoding="utf-8")
        assert f"- [x] AC-FUNC-001: foo {_GREEN_CHECK}" in result

    # ------------------------------------------------------------------
    # AC-FUNC-001: basic rewrite under ## Definition of Done
    # ------------------------------------------------------------------

    def test_tick_completion_checkboxes_basic_dod(self, tmp_path: Path) -> None:
        """Unchecked DoD line is rewritten to checked + green-check (U+2705)."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n"
            "## Status: done\n\n"
            "## Definition of Done\n\n"
            "- [ ] All acceptance criteria are checked.\n\n"
            "## Comments\n",
            encoding="utf-8",
        )

        BacklogManager()._tick_completion_checkboxes(wu_file)

        result = wu_file.read_text(encoding="utf-8")
        assert f"- [x] All acceptance criteria are checked. {_GREEN_CHECK}" in result

    # ------------------------------------------------------------------
    # AC-FUNC-003: N/A suffix preserved verbatim; green-check at end of line
    # ------------------------------------------------------------------

    def test_tick_completion_checkboxes_preserves_na_suffix(self, tmp_path: Path) -> None:
        """N/A suffix is preserved verbatim; green-check appends at end of the full line."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        na_line = "- [ ] AC-FINAL-002: ruff check ... -- N/A for Markdown Tasks (no Python source authored)"
        wu_file.write_text(
            f"# E0-F1-S1-T1\n\n## Acceptance Criteria\n\n{na_line}\n\n## Comments\n",
            encoding="utf-8",
        )

        BacklogManager()._tick_completion_checkboxes(wu_file)

        result = wu_file.read_text(encoding="utf-8")
        expected = (
            f"- [x] AC-FINAL-002: ruff check ... -- N/A for Markdown Tasks (no Python source authored) {_GREEN_CHECK}"
        )
        assert expected in result

    # ------------------------------------------------------------------
    # AC-FUNC-004: idempotency -- second call produces zero file changes
    # ------------------------------------------------------------------

    def test_tick_completion_checkboxes_idempotent(self, tmp_path: Path) -> None:
        """Running the helper twice leaves the file unchanged on the second call (mtime stable)."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-FUNC-001: foo\n\n"
            "## Definition of Done\n\n"
            "- [ ] All ACs checked.\n\n"
            "## Comments\n",
            encoding="utf-8",
        )

        manager = BacklogManager()
        manager._tick_completion_checkboxes(wu_file)
        mtime_after_first = wu_file.stat().st_mtime_ns

        manager._tick_completion_checkboxes(wu_file)
        mtime_after_second = wu_file.stat().st_mtime_ns

        assert mtime_after_first == mtime_after_second, "Second call should be a no-op: file mtime must not change"

    # ------------------------------------------------------------------
    # AC-FUNC-007: byte-level assertion -- no em-dash (U+2014) in output
    # ------------------------------------------------------------------

    def test_helper_output_has_no_em_dash(self, tmp_path: Path) -> None:
        """Helper output contains U+2705 (green check) and zero U+2014 (em-dash)."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-FUNC-001: verify something\n\n"
            "## Definition of Done\n\n"
            "- [ ] All criteria met.\n",
            encoding="utf-8",
        )

        BacklogManager()._tick_completion_checkboxes(wu_file)

        raw = wu_file.read_bytes()
        assert _GREEN_CHECK.encode("utf-8") in raw, "U+2705 green-check must appear in output"
        assert _EM_DASH.encode("utf-8") not in raw, "U+2014 em-dash must NOT appear in output"

    # ------------------------------------------------------------------
    # Lines outside the two target sections must not be modified
    # ------------------------------------------------------------------

    def test_tick_does_not_modify_lines_outside_target_sections(self, tmp_path: Path) -> None:
        """Checkboxes outside AC and DoD sections are never touched."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n"
            "## Description\n\n"
            "- [ ] some item in description\n\n"
            "## Approach\n\n"
            "- [ ] step one\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-FUNC-001: real criterion\n\n"
            "## Comments\n\n"
            "- [ ] comment checkbox (should not be touched)\n",
            encoding="utf-8",
        )

        BacklogManager()._tick_completion_checkboxes(wu_file)

        result = wu_file.read_text(encoding="utf-8")
        # The AC line should be ticked
        assert f"- [x] AC-FUNC-001: real criterion {_GREEN_CHECK}" in result
        # Lines outside the sections must remain unchanged
        assert "- [ ] some item in description" in result
        assert "- [ ] step one" in result
        assert "- [ ] comment checkbox (should not be touched)" in result

    # ------------------------------------------------------------------
    # Legacy ticked-but-no-emoji lines get the green-check appended
    # ------------------------------------------------------------------

    def test_tick_adds_emoji_to_legacy_ticked_line(self, tmp_path: Path) -> None:
        """A ``- [x] ...`` line without green-check gets the emoji appended."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n## Acceptance Criteria\n\n- [x] AC-FUNC-001: already ticked but no emoji\n",
            encoding="utf-8",
        )

        BacklogManager()._tick_completion_checkboxes(wu_file)

        result = wu_file.read_text(encoding="utf-8")
        assert f"- [x] AC-FUNC-001: already ticked but no emoji {_GREEN_CHECK}" in result


class TestSetStatusDoneTicks:
    """Integration tests: _set_status with STATUS_DONE triggers _tick_completion_checkboxes.

    AC-FUNC-002, AC-CYCLE-001, AC-CYCLE-003.
    """

    def _make_index(self, tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
        """Create a minimal BACKLOG.md with a single task row."""
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            f"| {unit_id} | Task | Task | {status} | None | git-repo | `backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )
        return index

    def _make_work_unit(self, directory: Path, unit_id: str) -> Path:
        """Create a minimal work-unit file with AC and DoD sections."""
        wu_file = directory / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}\n\n"
            "## Status: in-queue\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-FUNC-001: something\n\n"
            "## Definition of Done\n\n"
            "- [ ] All ACs checked.\n\n"
            "## Comments\n",
            encoding="utf-8",
        )
        return wu_file

    def test_set_status_done_ticks_acs_and_dod(self, tmp_path: Path) -> None:
        """_set_status with STATUS_DONE ticks both AC and DoD checkboxes."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-queue")
        wu_file = self._make_work_unit(backlog_dir, "E0-F1-S1-T1")

        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", "done")

        result = wu_file.read_text(encoding="utf-8")
        assert f"- [x] AC-FUNC-001: something {_GREEN_CHECK}" in result
        assert f"- [x] All ACs checked. {_GREEN_CHECK}" in result

    @pytest.mark.parametrize(
        "non_done_status",
        ["blocked", "declined", "hold", "in-queue", "in-progress", "in-review"],
    )
    def test_set_status_non_done_does_not_tick(self, tmp_path: Path, non_done_status: str) -> None:
        """Non-DONE transitions leave checkboxes unchanged (AC-FUNC-006, AC-CYCLE-003)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-queue")
        wu_file = self._make_work_unit(backlog_dir, "E0-F1-S1-T1")

        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", non_done_status)

        result = wu_file.read_text(encoding="utf-8")
        # Unchecked checkboxes must remain unchecked
        assert "- [ ] AC-FUNC-001: something" in result
        assert "- [ ] All ACs checked." in result
        # No green-check must appear
        assert _GREEN_CHECK not in result


class TestRollupParentTicksCheckboxes:
    """Rollup-driven done (AC-FUNC-005, AC-CYCLE-002): parent's AC and DoD lines tick on rollup."""

    def test_rollup_parent_ticks_parent_acs_and_dod(self, tmp_path: Path) -> None:
        """When all children are done, the parent's AC and DoD lines get ticked + green-check."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()

        # Create BACKLOG.md with T1 done and T2 in-queue, plus the story
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Task A | Task | done | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task B | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T2.md` |\n"
            "| E0-F1-S1 | Story | Story | in-queue | None | git-repo | `backlog/E0-F1-S1.md` |\n",
            encoding="utf-8",
        )

        # T2 work-unit file (we will mark this done)
        t2_file = backlog_dir / "E0-F1-S1-T2.md"
        t2_file.write_text("# E0-F1-S1-T2\n\n## Status: in-queue\n", encoding="utf-8")

        # Story file with AC and DoD checkboxes
        story_file = backlog_dir / "E0-F1-S1.md"
        story_file.write_text(
            "# E0-F1-S1\n\n"
            "## Status: in-queue\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-FUNC-001: story criterion\n\n"
            "## Definition of Done\n\n"
            "- [ ] All tasks complete.\n\n"
            "## Comments\n",
            encoding="utf-8",
        )

        # Mark T2 done -- should roll up the story, which should tick story's AC and DoD
        BacklogManager()._set_status(t2_file, index, "E0-F1-S1-T2", "done")

        story_result = story_file.read_text(encoding="utf-8")
        assert "## Status: done" in story_result
        assert f"- [x] AC-FUNC-001: story criterion {_GREEN_CHECK}" in story_result
        assert f"- [x] All tasks complete. {_GREEN_CHECK}" in story_result


class TestSetStatusAcceptsDraft:
    """AC-189-2: _set_status accepts STATUS_DRAFT as a valid transition target.

    Verifies that draft is present in VALID_STATUSES and that calling
    _set_status with 'draft' writes the status to both the work-unit file
    and BACKLOG.md without raising an exception.
    """

    def _make_index(self, tmp_path: Path, unit_id: str, initial_status: str) -> Path:
        """Create a minimal BACKLOG.md with a single task row."""
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            f"| {unit_id} | Task | Task | {initial_status} | None | git-repo |"
            f" `backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )
        return index

    def _make_work_unit(self, directory: Path, unit_id: str, initial_status: str) -> Path:
        """Create a minimal work-unit file with a Status line and Comments section."""
        wu_file = directory / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}\n\n## Status: {initial_status}\n\n## Comments\n",
            encoding="utf-8",
        )
        return wu_file

    def test_status_draft_present_in_valid_statuses(self) -> None:
        """STATUS_DRAFT constant is present in VALID_STATUSES lookup (AC-189-2)."""
        assert STATUS_DRAFT in VALID_STATUSES, f"STATUS_DRAFT ('{STATUS_DRAFT}') must be a key in VALID_STATUSES"
        assert VALID_STATUSES[STATUS_DRAFT] == STATUS_DRAFT, (
            "VALID_STATUSES[STATUS_DRAFT] must normalise to STATUS_DRAFT itself"
        )

    def test_set_status_draft_updates_work_unit_file(self, tmp_path: Path) -> None:
        """_set_status('draft') writes '## Status: draft' to the work-unit file (AC-189-2)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-queue")
        wu_file = self._make_work_unit(backlog_dir, "E0-F1-S1-T1", "in-queue")

        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", STATUS_DRAFT)

        wu_text = wu_file.read_text(encoding="utf-8")
        assert "## Status: draft" in wu_text, (
            f"Work-unit file must contain '## Status: draft' after transition; got:\n{wu_text}"
        )

    def test_set_status_draft_updates_backlog_index(self, tmp_path: Path) -> None:
        """_set_status('draft') updates the BACKLOG.md status cell for the unit (AC-189-2)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-queue")
        wu_file = self._make_work_unit(backlog_dir, "E0-F1-S1-T1", "in-queue")

        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", STATUS_DRAFT)

        index_text = index.read_text(encoding="utf-8")
        assert "draft" in index_text, (
            f"BACKLOG.md must contain 'draft' after _set_status transition; got:\n{index_text}"
        )

    def test_set_status_draft_does_not_raise(self, tmp_path: Path) -> None:
        """_set_status('draft') completes without raising ValueError (AC-189-2)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-progress")
        wu_file = self._make_work_unit(backlog_dir, "E0-F1-S1-T1", "in-progress")

        # Must not raise -- draft is a valid transition target
        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", STATUS_DRAFT)

    def test_set_status_draft_does_not_write_wu_claimed_audit(self, tmp_path: Path) -> None:
        """draft transition does not append a [WU_CLAIMED] audit comment (AC-189-2, spec 4.1.2)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-queue")
        wu_file = self._make_work_unit(backlog_dir, "E0-F1-S1-T1", "in-queue")

        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", STATUS_DRAFT)

        wu_text = wu_file.read_text(encoding="utf-8")
        assert "[WU_CLAIMED]" not in wu_text, "draft transitions must not produce a [WU_CLAIMED] audit comment"

    def test_set_status_draft_does_not_tick_checkboxes(self, tmp_path: Path) -> None:
        """draft transition does not tick AC/DoD checkboxes (only 'done' triggers ticking)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index = self._make_index(tmp_path, "E0-F1-S1-T1", "in-queue")
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1\n\n"
            "## Status: in-queue\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-001: some criterion\n\n"
            "## Definition of Done\n\n"
            "- [ ] All done.\n\n"
            "## Comments\n",
            encoding="utf-8",
        )

        BacklogManager()._set_status(wu_file, index, "E0-F1-S1-T1", STATUS_DRAFT)

        wu_text = wu_file.read_text(encoding="utf-8")
        assert "- [ ] AC-001: some criterion" in wu_text, "draft transition must leave AC checkboxes unchecked"
        assert "- [ ] All done." in wu_text, "draft transition must leave DoD checkboxes unchecked"


class TestUnitTypeLabel:
    """Unit tests for BacklogManager._unit_type_label static method."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "unit_id,expected_label",
        [
            ("EX-F1-S1-T1", WorkUnitType.TASK.value),
            ("EX-F1-S1", WorkUnitType.STORY.value),
            ("EX-F1", WorkUnitType.FEATURE.value),
            ("EX", WorkUnitType.EPIC.value),
        ],
    )
    def test_unit_type_label_returns_correct_label(self, unit_id: str, expected_label: str) -> None:
        """_unit_type_label derives the hierarchy level from the ID structure."""
        assert BacklogManager._unit_type_label(unit_id) == expected_label, (
            f"Expected _unit_type_label({unit_id!r}) == {expected_label!r}"
        )

    @pytest.mark.unit
    def test_unit_type_label_task_delegates_to_is_task_id(self) -> None:
        """_unit_type_label('EX-F1-S1-T1') returns WorkUnitType.TASK.value via _is_task_id delegation."""
        result = BacklogManager._unit_type_label("EX-F1-S1-T1")
        assert result == WorkUnitType.TASK.value, f"Expected {WorkUnitType.TASK.value!r}, got {result!r}"

    @pytest.mark.unit
    def test_unit_type_label_raises_for_unrecognized_id(self) -> None:
        """_unit_type_label raises ValueError for an ID that does not match any known hierarchy shape."""
        with pytest.raises(ValueError, match="Unrecognized work-unit ID shape"):
            BacklogManager._unit_type_label("MALFORMED-X99")


# ---------------------------------------------------------------------------
# E1-F2-S3-T1: _check_status_summary + _update_status_summary include Draft column
# AC-189-6, AC-189-7
# ---------------------------------------------------------------------------


def _make_backlog_with_draft(tmp_path: Path, backlog_dir: Path) -> tuple[Path, dict[str, Path]]:
    """Create a BACKLOG.md with a draft task and corresponding WU files.

    Returns (index_path, {unit_id: wu_file_path}).
    """
    content = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|----------|\n"
        "| E1 | Epic One | Epic | in-queue | None | repo | `backlog/E1.md` |\n"
        "| E1-F1-S1-T1 | Task A | Task | done | None | repo | `backlog/E1-F1-S1-T1.md` |\n"
        "| E1-F1-S1-T2 | Task B | Task | draft | None | repo | `backlog/E1-F1-S1-T2.md` |\n"
        "| E1-F1-S1-T3 | Task C | Task | in-queue | None | repo | `backlog/E1-F1-S1-T3.md` |\n"
        "| E2 | Epic Two | Epic | in-queue | None | repo | `backlog/E2.md` |\n"
        "| E2-F1-S1-T1 | Task D | Task | draft | None | repo | `backlog/E2-F1-S1-T1.md` |\n"
        "| E2-F1-S1-T2 | Task E | Task | draft | None | repo | `backlog/E2-F1-S1-T2.md` |\n"
    )
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(content, encoding="utf-8")

    files: dict[str, Path] = {}
    units = [
        ("E1", "in-queue"),
        ("E1-F1-S1-T1", "done"),
        ("E1-F1-S1-T2", "draft"),
        ("E1-F1-S1-T3", "in-queue"),
        ("E2", "in-queue"),
        ("E2-F1-S1-T1", "draft"),
        ("E2-F1-S1-T2", "draft"),
    ]
    for uid, status in units:
        wu = backlog_dir / f"{uid}.md"
        wu.write_text(f"# {uid}\n\n## Status: {status}\n", encoding="utf-8")
        files[uid] = wu

    return index_path, files


@pytest.mark.unit
class TestStatusSummaryDraftColumn:
    """AC-189-7: Status Summary per-epic table includes a Draft column.

    Tests that _update_status_summary writes the Draft column and
    _check_status_summary validates it.
    """

    def test_update_status_summary_includes_draft_header(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-7: _update_status_summary writes a Draft column header in the table."""
        index_path, _ = _make_backlog_with_draft(tmp_path, backlog_dir)
        mgr = BacklogManager()
        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        assert "Draft" in result, (
            "Status Summary table must include a 'Draft' column header after _update_status_summary"
        )

    def test_update_status_summary_counts_draft_tasks(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-7: _update_status_summary correctly counts draft tasks per epic."""
        index_path, _ = _make_backlog_with_draft(tmp_path, backlog_dir)
        mgr = BacklogManager()
        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        summary_rows = _extract_summary_lines(result)

        e1_lines = [line for line in summary_rows if "| E1 |" in line]
        e2_lines = [line for line in summary_rows if "| E2 |" in line]
        assert len(e1_lines) == 1, f"Expected exactly 1 E1 row, got: {e1_lines}"
        assert len(e2_lines) == 1, f"Expected exactly 1 E2 row, got: {e2_lines}"

        # E1 has: 1 done, 1 draft, 1 in-queue
        # E2 has: 2 draft
        # Parse cells: '' | epic | title | done | in-progress | in-queue | blocked | declined | draft | ...
        # The Draft column position depends on the header order
        e1_cells = [c.strip() for c in e1_lines[0].split("|")]
        e2_cells = [c.strip() for c in e2_lines[0].split("|")]

        # Find the Draft column index by parsing the header row
        header_line = next(
            (line for line in result.splitlines() if line.strip().startswith("| Epic") and "Draft" in line),
            None,
        )
        assert header_line is not None, "Status Summary table must have a header row containing 'Draft'"

        header_cells = [c.strip() for c in header_line.split("|")]
        draft_col = next(
            (i for i, h in enumerate(header_cells) if h.strip() == "Draft"),
            None,
        )
        assert draft_col is not None, f"No 'Draft' column found in header: {header_line}"

        assert e1_cells[draft_col] == "1", (
            f"E1 draft count should be 1 (one draft task), got: {e1_cells[draft_col]!r}. Row: {e1_lines[0]}"
        )
        assert e2_cells[draft_col] == "2", (
            f"E2 draft count should be 2 (two draft tasks), got: {e2_cells[draft_col]!r}. Row: {e2_lines[0]}"
        )

    def test_update_status_summary_zero_draft_when_no_draft_tasks(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-7: _update_status_summary writes 0 in Draft column when no draft tasks exist."""
        index_path, _ = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()
        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        summary_rows = _extract_summary_lines(result)
        assert len(summary_rows) >= 1, "Expected at least one summary row"

        header_line = next(
            (line for line in result.splitlines() if line.strip().startswith("| Epic") and "Draft" in line),
            None,
        )
        assert header_line is not None, "Status Summary table must have a header row containing 'Draft'"

        header_cells = [c.strip() for c in header_line.split("|")]
        draft_col = next(
            (i for i, h in enumerate(header_cells) if h.strip() == "Draft"),
            None,
        )
        assert draft_col is not None, f"No 'Draft' column found in header: {header_line}"

        for row in summary_rows:
            cells = [c.strip() for c in row.split("|")]
            assert cells[draft_col] == "0", (
                f"Draft count should be 0 for row with no draft tasks, got: {cells[draft_col]!r}. Row: {row}"
            )

    def test_check_status_summary_includes_draft_in_validation(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-7: _check_status_summary validates Draft counts match the index."""
        index_path, _ = _make_backlog_with_draft(tmp_path, backlog_dir)
        mgr = BacklogManager()
        # First write a correct summary
        mgr._update_status_summary(index_path)

        # Validate that no draft mismatch errors are reported
        errors: list[str] = []
        rows = mgr._parse_backlog_rows(index_path)
        mgr._check_status_summary(index_path, rows, errors)
        draft_errors = [e for e in errors if "draft" in e.lower()]
        assert not draft_errors, f"Unexpected draft mismatch errors: {draft_errors}"

    def test_check_status_summary_reports_draft_mismatch(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-7: _check_status_summary reports an error when Draft count is wrong."""
        index_path, _ = _make_backlog_with_draft(tmp_path, backlog_dir)
        mgr = BacklogManager()

        # Write a deliberately wrong summary with Draft count 0 where it should be > 0
        wrong_summary = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
            "|------|-------|------|-------------|----------|---------|----------|-------|\n"
            "| E1 | Epic One | 1 | 0 | 1 | 0 | 0 | 0 |\n"
            "| E2 | Epic Two | 0 | 0 | 0 | 0 | 0 | 0 |\n\n"
        )
        existing = index_path.read_text(encoding="utf-8")
        index_path.write_text(wrong_summary + existing, encoding="utf-8")

        errors: list[str] = []
        rows = mgr._parse_backlog_rows(index_path)
        mgr._check_status_summary(index_path, rows, errors)

        assert any("draft" in e.lower() or "mismatch" in e.lower() for e in errors), (
            f"Expected a draft mismatch error but got: {errors}"
        )

    def test_parse_summary_table_includes_draft_column(self) -> None:
        """AC-189-7: _parse_summary_table correctly parses the Draft column."""
        content = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
            "|------|-------|------|-------------|----------|---------|----------|-------|\n"
            "| E1 | Epic One | 1 | 0 | 2 | 0 | 0 | 3 |\n"
            "\n## Full Work Unit Index\n"
        )
        mgr = BacklogManager()
        result = mgr._parse_summary_table(content)

        assert "E1" in result, f"E1 epic row must be parsed; got: {result}"
        from devbench.constants import STATUS_DRAFT

        assert result["E1"][STATUS_DRAFT] == 3, f"Draft count for E1 must be 3; got: {result['E1'].get(STATUS_DRAFT)}"

    def test_compute_epic_counts_includes_draft(self, tmp_path: Path, backlog_dir: Path) -> None:
        """AC-189-7: _compute_epic_counts includes draft tasks in per-epic counts."""
        index_path, _ = _make_backlog_with_draft(tmp_path, backlog_dir)
        mgr = BacklogManager()
        rows = mgr._parse_backlog_rows(index_path)
        counts = mgr._compute_epic_counts(rows)

        from devbench.constants import STATUS_DRAFT

        assert "E1" in counts, "E1 must be present in epic counts"
        assert "E2" in counts, "E2 must be present in epic counts"
        assert counts["E1"][STATUS_DRAFT] == 1, f"E1 must have 1 draft task; got: {counts['E1'].get(STATUS_DRAFT)}"
        assert counts["E2"][STATUS_DRAFT] == 2, f"E2 must have 2 draft tasks; got: {counts['E2'].get(STATUS_DRAFT)}"

    def test_migration_legacy_table_without_draft_does_not_error_on_check(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-189-7: Legacy BACKLOG.md without Draft column triggers rewrite on _update_status_summary.

        After calling _update_status_summary once on a legacy file, the resulting
        table must include the Draft column and _check_status_summary must report
        no errors.
        """
        index_path, _ = _make_backlog_with_draft(tmp_path, backlog_dir)
        # Write a legacy summary without Draft column
        legacy_summary = (
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
            "|------|-------|------|-------------|----------|---------|----------|\n"
            "| E1 | Epic One | 0 | 0 | 0 | 0 | 0 |\n"
            "| E2 | Epic Two | 0 | 0 | 0 | 0 | 0 |\n\n"
        )
        existing = index_path.read_text(encoding="utf-8")
        index_path.write_text(legacy_summary + existing, encoding="utf-8")

        mgr = BacklogManager()
        # _update_status_summary must rewrite the table including Draft column
        mgr._update_status_summary(index_path)

        result = index_path.read_text(encoding="utf-8")
        assert "Draft" in result, "After _update_status_summary, Draft column must appear in Status Summary"

        errors: list[str] = []
        rows = mgr._parse_backlog_rows(index_path)
        mgr._check_status_summary(index_path, rows, errors)
        assert not errors, f"After rewrite, _check_status_summary must report no errors; got: {errors}"


class TestValidateBacklogIgnoresScope:
    """AC-190-14: validate-backlog validates the ENTIRE backlog regardless of active scope.

    Scope is a claim-side filter only. Even when a scope.json restricts the
    orchestrator to a single epic, ``BacklogManager.validate()`` must inspect
    every work unit in BACKLOG.md -- not just the scoped subset.
    """

    @staticmethod
    def _build_two_epic_workspace(
        tmp_path: Path,
        backlog_dir: Path,
        *,
        e2_file_status: str = "in-queue",
        e2_index_status: str = "in-queue",
    ) -> Path:
        """Build a minimal two-epic workspace and return the BACKLOG.md path.

        E1 contains one well-formed task. E2 contains one task whose file
        status and index status can be set independently to introduce a
        deliberate mismatch for detection by validate().

        Args:
            tmp_path: Temporary directory (pytest fixture).
            backlog_dir: The ``backlog/`` subdirectory under ``tmp_path``.
            e2_file_status: Status written inside E2-F1-S1-T1.md.
            e2_index_status: Status recorded in the BACKLOG.md index row.

        Returns:
            Path to the created BACKLOG.md.
        """
        index_content = (
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|----------|\n"
            f"| E1 | Epic One | Epic | in-queue | None | repo | `backlog/E1.md` |\n"
            f"| E1-F1-S1-T1 | Task Alpha | Task | done | None | repo | `backlog/E1-F1-S1-T1.md` |\n"
            f"| E2 | Epic Two | Epic | in-queue | None | repo | `backlog/E2.md` |\n"
            f"| E2-F1-S1-T1 | Task Beta | Task | {e2_index_status} | None | repo | `backlog/E2-F1-S1-T1.md` |\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(index_content, encoding="utf-8")

        units = [
            ("E1", "in-queue"),
            ("E1-F1-S1-T1", "done"),
            ("E2", "in-queue"),
            ("E2-F1-S1-T1", e2_file_status),
        ]
        for uid, status in units:
            wu = backlog_dir / f"{uid}.md"
            wu.write_text(f"# {uid}\n\n## Status: {status}\n", encoding="utf-8")

        return index_path

    def test_validate_detects_out_of_scope_errors_with_scope_json_present(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """Regression: validate() catches an E2 status mismatch even when scope restricts to E1.

        Steps:
        1. Build a two-epic workspace where E2-F1-S1-T1 has a deliberate
           status mismatch (file says 'done', index says 'in-queue').
        2. Write a scope.json restricting to E1 only.
        3. Run validate() and confirm it reports the E2 mismatch error.
        """
        index_path = self._build_two_epic_workspace(
            tmp_path,
            backlog_dir,
            e2_file_status="done",
            e2_index_status="in-queue",
        )

        all_ids = ["E1", "E1-F1-S1-T1", "E2", "E2-F1-S1-T1"]
        scope = ScopeFilter.parse("E1", "", all_ids)
        assert scope.allows("E1-F1-S1-T1"), "Scope must include E1 tasks"
        assert not scope.allows("E2-F1-S1-T1"), "Scope must exclude E2 tasks"
        scope.to_file(tmp_path)
        assert (tmp_path / ".devbench" / "scope.json").exists(), "scope.json must exist"

        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)

        e2_mismatch_errors = [e for e in errors if "E2-F1-S1-T1" in e and "status" in e.lower()]
        assert e2_mismatch_errors, (
            f"validate() must detect the E2-F1-S1-T1 status mismatch even with E1-only scope active; "
            f"errors returned: {errors}"
        )

    def test_validate_inspects_all_units_without_scope(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Baseline: validate() detects E2 mismatch when no scope.json exists.

        This test is the non-scoped control for the regression test above.
        If this fails, the production code has a pre-existing bug unrelated to scope.
        """
        index_path = self._build_two_epic_workspace(
            tmp_path,
            backlog_dir,
            e2_file_status="done",
            e2_index_status="in-queue",
        )

        assert not (tmp_path / ".devbench" / "scope.json").exists()

        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)

        e2_mismatch_errors = [e for e in errors if "E2-F1-S1-T1" in e and "status" in e.lower()]
        assert e2_mismatch_errors, f"validate() must detect the E2-F1-S1-T1 status mismatch; errors returned: {errors}"

    def test_validate_clean_backlog_passes_with_scope_present(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A valid backlog remains valid when scope.json is present.

        Ensures scope.json does not introduce false positives into validate().
        """
        index_path = self._build_two_epic_workspace(
            tmp_path,
            backlog_dir,
            e2_file_status="in-queue",
            e2_index_status="in-queue",
        )

        all_ids = ["E1", "E1-F1-S1-T1", "E2", "E2-F1-S1-T1"]
        scope = ScopeFilter.parse("E1", "", all_ids)
        scope.to_file(tmp_path)

        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)

        status_mismatch_errors = [e for e in errors if "status" in e.lower() and "mismatch" in e.lower()]
        assert not status_mismatch_errors, (
            f"A consistent backlog must not produce status-mismatch errors even with scope.json present; "
            f"got: {status_mismatch_errors}"
        )


class TestBulkSetStatus:
    """Tests for BacklogManager.bulk_set_status -- spec section 4.7.2, AC-194-5/6/7.

    AC-194-5: All writes acquire flock(BACKLOG.lock) once before any per-WU _set_status call.
    AC-194-6: Per-WU updates go through BacklogManager._set_status so audit + rollup fire.
    AC-194-7: A workspace-level [BULK_STATUS_UPDATE] audit row is written per invocation.
    """

    # ------------------------------------------------------------------
    # Fixture helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_backlog(
        tmp_path: Path,
        unit_specs: list[tuple[str, str]],
    ) -> tuple[Path, Path, dict[str, Path]]:
        """Build BACKLOG.md + per-WU files for a list of (unit_id, status) pairs.

        Returns:
            (backlog_index_path, backlog_dir, {unit_id: wu_file_path})
        """
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(parents=True, exist_ok=True)

        rows = "\n".join(
            f"| {uid} | Title {uid} | Task | {status} | None | repo | `backlog/{uid}.md` |"
            for uid, status in unit_specs
        )
        index_content = (
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|----------|\n"
            f"{rows}\n\n"
            "## Status Summary\n\n"
            "| Status | Count |\n"
            "|--------|-------|\n"
            f"| in-queue | {sum(1 for _, s in unit_specs if s == 'in-queue')} |\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(index_content, encoding="utf-8")

        wu_paths: dict[str, Path] = {}
        for uid, status in unit_specs:
            wu_file = backlog_dir / f"{uid}.md"
            wu_file.write_text(
                f"# {uid}: Title {uid}\n\n## Status: {status}\n\n## Comments\n",
                encoding="utf-8",
            )
            wu_paths[uid] = wu_file

        return index_path, backlog_dir, wu_paths

    # ------------------------------------------------------------------
    # AC-194-5: Happy path -- all WUs updated, flock acquired once
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_bulk_set_status_updates_all_work_units(self, tmp_path: Path) -> None:
        """bulk_set_status writes the new status to every WU file listed in unit_ids."""
        unit_specs = [
            ("E7-F1-S1-T1", "in-queue"),
            ("E7-F1-S1-T2", "in-queue"),
            ("E7-F1-S1-T3", "in-queue"),
        ]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]
        count = mgr.bulk_set_status(
            pairs,
            "done",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7"',
        )

        assert count == 3, f"Expected 3 updated, got {count}"
        for uid, wu_path in pairs:
            content = wu_path.read_text(encoding="utf-8")
            assert "## Status: done" in content, f"{uid} file should have 'done' status; got:\n{content}"

    @pytest.mark.unit
    def test_bulk_set_status_updates_backlog_index(self, tmp_path: Path) -> None:
        """bulk_set_status updates the BACKLOG.md index row for every WU."""
        unit_specs = [("E7-F2-S1-T1", "in-queue"), ("E7-F2-S1-T2", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]
        mgr.bulk_set_status(
            pairs,
            "blocked",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F2"',
        )

        index_content = index_path.read_text(encoding="utf-8")
        assert "blocked" in index_content, f"BACKLOG.md should contain 'blocked' after bulk update:\n{index_content}"
        assert "in-queue" not in index_content.split("## Full Work Unit Index")[1].split("##")[0], (
            "No 'in-queue' rows should remain in the index after bulk update to 'blocked'"
        )

    @pytest.mark.unit
    def test_bulk_set_status_returns_count(self, tmp_path: Path) -> None:
        """bulk_set_status returns the exact count of WUs it processed."""
        unit_specs = [
            ("E7-F3-T1", "in-queue"),
            ("E7-F3-T2", "in-queue"),
            ("E7-F3-T3", "in-queue"),
            ("E7-F3-T4", "in-queue"),
            ("E7-F3-T5", "in-queue"),
        ]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]
        count = mgr.bulk_set_status(
            pairs,
            "hold",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F3"',
        )

        assert count == 5

    # ------------------------------------------------------------------
    # AC-194-5: flock acquired exactly once around the entire batch
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_bulk_set_status_acquires_flock_once(self, tmp_path: Path) -> None:
        """flock_backlog context manager is entered exactly once for the batch."""
        from unittest.mock import patch

        unit_specs = [("E7-F4-T1", "in-queue"), ("E7-F4-T2", "in-queue"), ("E7-F4-T3", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        enter_count = 0

        import contextlib

        @contextlib.contextmanager
        def fake_flock(workspace_root, timeout_seconds=30):
            nonlocal enter_count
            enter_count += 1
            yield None

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]

        with patch("devbench.backlog.manager.flock_backlog", fake_flock):
            mgr.bulk_set_status(
                pairs,
                "in-progress",
                backlog_index=index_path,
                audit_log_path=audit_log,
                audit_meta='--include="E7-F4"',
            )

        assert enter_count == 1, f"flock_backlog should be entered exactly once for the batch, got {enter_count}"

    @pytest.mark.unit
    def test_bulk_set_status_releases_flock_on_exception(self, tmp_path: Path) -> None:
        """flock is released even when _set_status raises mid-batch."""
        from unittest.mock import patch

        unit_specs = [("E7-F5-T1", "in-queue"), ("E7-F5-T2", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        released = {"flag": False}

        import contextlib

        @contextlib.contextmanager
        def fake_flock(workspace_root, timeout_seconds=30):
            try:
                yield None
            finally:
                released["flag"] = True

        call_count = {"n": 0}
        original_set_status = BacklogManager._set_status

        def failing_set_status(self, work_unit_path, backlog_index, unit_id, new_status, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("Simulated failure on second WU")
            original_set_status(self, work_unit_path, backlog_index, unit_id, new_status, **kwargs)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]

        with (
            patch("devbench.backlog.manager.flock_backlog", fake_flock),
            patch.object(BacklogManager, "_set_status", failing_set_status),
        ):
            with pytest.raises(RuntimeError, match="Simulated failure"):
                mgr.bulk_set_status(
                    pairs,
                    "blocked",
                    backlog_index=index_path,
                    audit_log_path=audit_log,
                    audit_meta='--include="E7-F5"',
                )

        assert released["flag"] is True, "flock must be released even when an exception is raised"

    # ------------------------------------------------------------------
    # AC-194-6: per-WU _set_status is called so audit + rollup fire
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_bulk_set_status_calls_set_status_per_wu(self, tmp_path: Path) -> None:
        """_set_status is invoked once per WU in unit_ids (not batched)."""
        from unittest.mock import patch

        unit_specs = [
            ("E7-F6-T1", "in-queue"),
            ("E7-F6-T2", "in-queue"),
            ("E7-F6-T3", "in-queue"),
        ]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        called_with: list[tuple[str, str]] = []
        original_set_status = BacklogManager._set_status

        def recording_set_status(self, work_unit_path, backlog_index, unit_id, new_status, **kwargs):
            called_with.append((unit_id, new_status))
            original_set_status(self, work_unit_path, backlog_index, unit_id, new_status, **kwargs)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]

        with patch.object(BacklogManager, "_set_status", recording_set_status):
            mgr.bulk_set_status(
                pairs,
                "declined",
                backlog_index=index_path,
                audit_log_path=audit_log,
                audit_meta='--include="E7-F6"',
            )

        assert len(called_with) == 3, f"Expected 3 _set_status calls, got {len(called_with)}"
        for uid, _ in unit_specs:
            matched = [(i, s) for i, s in called_with if i == uid]
            assert len(matched) == 1, f"_set_status not called for {uid}"
            assert matched[0][1] == "declined", f"Expected 'declined', got {matched[0][1]}"

    # ------------------------------------------------------------------
    # AC-194-7: workspace-level [BULK_STATUS_UPDATE] audit row is written
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_bulk_set_status_writes_audit_row(self, tmp_path: Path) -> None:
        """A [BULK_STATUS_UPDATE] row is appended to the audit_log_path file."""
        unit_specs = [("E7-F7-T1", "in-queue"), ("E7-F7-T2", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]
        mgr.bulk_set_status(
            pairs,
            "hold",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F7" --exclude="E7-F7-T3"',
        )

        assert audit_log.exists(), "audit_log_path file must be created by bulk_set_status"
        audit_content = audit_log.read_text(encoding="utf-8")
        assert "[BULK_STATUS_UPDATE]" in audit_content, (
            f"Audit log must contain '[BULK_STATUS_UPDATE]'; got:\n{audit_content}"
        )
        assert "2 WUs" in audit_content, f"Audit row must include count '2 WUs'; got:\n{audit_content}"
        assert "hold" in audit_content, f"Audit row must include target status 'hold'; got:\n{audit_content}"
        assert '--include="E7-F7"' in audit_content, f"Audit row must include audit_meta; got:\n{audit_content}"

    @pytest.mark.unit
    def test_bulk_set_status_audit_row_format(self, tmp_path: Path) -> None:
        """[BULK_STATUS_UPDATE] row matches expected format with count, status, and meta."""
        unit_specs = [
            ("E7-F8-T1", "in-queue"),
            ("E7-F8-T2", "in-queue"),
            ("E7-F8-T3", "in-queue"),
        ]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk-updates.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]
        mgr.bulk_set_status(
            pairs,
            "in-queue",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F8"',
        )

        audit_content = audit_log.read_text(encoding="utf-8")
        # Must match: [BULK_STATUS_UPDATE] 3 WUs set to 'in-queue' by --include="E7-F8"
        import re

        pattern = r"\[BULK_STATUS_UPDATE\] 3 WUs set to 'in-queue' by --include=\"E7-F8\""
        assert re.search(pattern, audit_content), (
            f"Audit row does not match expected format.\nExpected pattern: {pattern}\nGot:\n{audit_content}"
        )

    @pytest.mark.unit
    def test_bulk_set_status_creates_audit_log_parent_dirs(self, tmp_path: Path) -> None:
        """bulk_set_status creates parent directories for audit_log_path if absent."""
        unit_specs = [("E7-F9-T1", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "deep" / "nested" / "logs" / "bulk.log"

        assert not audit_log.parent.exists(), "Pre-condition: parent dirs must not exist"

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]
        mgr.bulk_set_status(
            pairs,
            "in-queue",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F9"',
        )

        assert audit_log.exists(), "audit_log_path must be created including parent directories"

    @pytest.mark.unit
    def test_bulk_set_status_appends_multiple_runs(self, tmp_path: Path) -> None:
        """Successive bulk_set_status calls append rows; they do not overwrite."""
        unit_specs = [("E7-F10-T1", "in-queue"), ("E7-F10-T2", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()

        # First run: update T1 to done
        mgr.bulk_set_status(
            [("E7-F10-T1", wu_paths["E7-F10-T1"])],
            "done",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F10-T1"',
        )

        # Revert index/file state for second run (write T2 update)
        mgr.bulk_set_status(
            [("E7-F10-T2", wu_paths["E7-F10-T2"])],
            "blocked",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E7-F10-T2"',
        )

        audit_content = audit_log.read_text(encoding="utf-8")
        assert audit_content.count("[BULK_STATUS_UPDATE]") == 2, (
            f"Expected exactly 2 [BULK_STATUS_UPDATE] rows, got:\n{audit_content}"
        )

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_bulk_set_status_empty_list_writes_audit_row(self, tmp_path: Path) -> None:
        """Empty unit_ids list writes a 0 WUs audit row and returns 0."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|----------|\n",
            encoding="utf-8",
        )
        audit_log = tmp_path / "logs" / "bulk.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        count = mgr.bulk_set_status(
            [],
            "in-queue",
            backlog_index=index_path,
            audit_log_path=audit_log,
            audit_meta='--include="E99"',
        )

        assert count == 0
        audit_content = audit_log.read_text(encoding="utf-8")
        assert "[BULK_STATUS_UPDATE]" in audit_content
        assert "0 WUs" in audit_content

    @pytest.mark.unit
    def test_bulk_set_status_invalid_status_raises(self, tmp_path: Path) -> None:
        """Invalid new_status raises ValueError before any file is written."""
        unit_specs = [("E7-F11-T1", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]

        with pytest.raises(ValueError, match="Invalid status"):
            mgr.bulk_set_status(
                pairs,
                "not-a-real-status",
                backlog_index=index_path,
                audit_log_path=audit_log,
                audit_meta='--include="E7-F11"',
            )

        # WU file must NOT have been modified
        content = wu_paths["E7-F11-T1"].read_text(encoding="utf-8")
        assert "## Status: in-queue" in content, "WU file should remain unchanged when invalid status is provided"

    @pytest.mark.unit
    def test_bulk_set_status_flock_workspace_root_is_backlog_index_parent(self, tmp_path: Path) -> None:
        """flock_backlog is called with workspace_root = backlog_index.parent."""
        import contextlib
        from unittest.mock import patch

        unit_specs = [("E7-F12-T1", "in-queue")]
        index_path, _, wu_paths = self._make_backlog(tmp_path, unit_specs)
        audit_log = tmp_path / "logs" / "bulk.log"
        audit_log.parent.mkdir(parents=True, exist_ok=True)

        called_with_root: list[Path] = []

        @contextlib.contextmanager
        def capturing_flock(workspace_root, timeout_seconds=30):
            called_with_root.append(workspace_root)
            yield None

        mgr = BacklogManager()
        pairs = [(uid, wu_paths[uid]) for uid, _ in unit_specs]

        with patch("devbench.backlog.manager.flock_backlog", capturing_flock):
            mgr.bulk_set_status(
                pairs,
                "in-queue",
                backlog_index=index_path,
                audit_log_path=audit_log,
                audit_meta='--include="E7-F12"',
            )

        assert len(called_with_root) == 1
        assert called_with_root[0] == index_path.parent, (
            f"Expected workspace_root={index_path.parent}, got {called_with_root[0]}"
        )


# ---------------------------------------------------------------------------
# Issue #200 / AC-200-2: cascade fires even when the newly-done ID is only
# referenced by a [BLOCKED_PENDING_PROPOSAL] marker, NOT in the dep table.
# Before the fix, condition 2 in _auto_requeue_marker_dependents required
# newly_done_id to be in _parse_candidate_dependencies (the Dependencies
# table), which was never true for marker-only references.
# ---------------------------------------------------------------------------


class TestAutoRequeueMarkerOnlyNoDep:
    """AC-200-2: cascade fires when marker target is not in the dep table.

    The fix must relax condition 2 so that ``newly_done_id`` appearing as a
    marker ID (in the Comments section) is sufficient to trigger the cascade,
    even when the Dependencies section contains ``| none | | |``.
    """

    def test_cascade_fires_when_marker_target_done_but_not_in_dep_table(self, tmp_path: Path) -> None:
        """AC-200-2 happy path: marker-only reference triggers auto-requeue."""
        marker_comment = "[2026-05-16 01:57 UTC] [agent/agent/orchestrator] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=None,  # No dep table entry -- marker only
            comments=marker_comment,
        )
        dep_file = _unit_body("E0-F1-S1-T2", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "MarkerTarget", "done"),
            ],
            files={"E0-F1-S1-T1": src_file, "E0-F1-S1-T2": dep_file},
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in src, "Source should be re-queued when its marker target is done"
        assert "[AUTO_UNBLOCKED]" in src
        assert "[CASCADE_RESOLVED]" in src

    def test_cascade_does_not_fire_when_other_marker_still_non_terminal(self, tmp_path: Path) -> None:
        """Two markers with no dep table: cascade stays blocked until both are done."""
        marker_comment = (
            "[2026-05-16 01:57 UTC] [agent/agent/orchestrator] [BLOCKED_PENDING_PROPOSAL]"
            " E0-F1-S1-T2\n"
            "[2026-05-16 01:58 UTC] [agent/agent/orchestrator] [BLOCKED_PENDING_PROPOSAL]"
            " E0-F1-S1-T3\n"
        )
        src_file = _unit_body(
            "E0-F1-S1-T1",
            "blocked",
            deps=None,
            comments=marker_comment,
        )
        dep_done = _unit_body("E0-F1-S1-T2", "done")
        dep_queued = _unit_body("E0-F1-S1-T3", "in-queue")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Source", "blocked"),
                ("E0-F1-S1-T2", "MarkerTarget1", "done"),
                ("E0-F1-S1-T3", "MarkerTarget2", "in-queue"),
            ],
            files={
                "E0-F1-S1-T1": src_file,
                "E0-F1-S1-T2": dep_done,
                "E0-F1-S1-T3": dep_queued,
            },
        )
        BacklogManager()._auto_requeue_marker_dependents(index, "E0-F1-S1-T2")

        src = (tmp_path / "backlog" / "E0-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in src, "Should stay blocked -- T3 not yet done"
        assert "[AUTO_UNBLOCKED]" not in src


# ---------------------------------------------------------------------------
# Issue #200 / AC-200-3: regression test for the captured E5-F3-S1-T1 +
# E5-F3-S1-T4 scenario from 2026-05-16 02:32 UTC. Before the fix, T1
# remained blocked after T4 completed because the cascade required T4 to
# be in T1's Dependencies table (it was, but the classifier still returned
# OPERATOR_ACTION_REQUIRED for satisfied markers).
# ---------------------------------------------------------------------------


class TestAutoRequeueRegressionE5Scenario:
    """AC-200-3: regression test for the E5-F3-S1-T1 + E5-F3-S1-T4 scenario.

    Reproduces the exact audit-log content from 2026-05-16 02:32 UTC and
    asserts that within one _auto_requeue_marker_dependents sweep tick,
    T1 auto-clears with [AUTO_UNBLOCKED] [CASCADE_RESOLVED] and status
    in-queue -- without operator intervention.
    """

    # Exact audit-log lines from the 2026-05-16 02:32 UTC capture for T1.
    _T1_AUDIT_COMMENTS = (
        "[2026-05-16 01:56 UTC] [agent/task_factory] [PROPOSAL_PROMOTED]"
        " E5-F3-S1-T4 promoted and wired as dependency of E5-F3-S1-T1."
        " (auto-accepted via task_factory.auto_accept_proposals=true at"
        " write-proposal time) [BLOCKED_PENDING_PROPOSAL] E5-F3-S1-T4\n"
        "[2026-05-16 01:57 UTC] [agent/agent/orchestrator]"
        " [BLOCKED_PENDING_PROPOSAL] Amendment rejected"
        " (source-test atomicity on constants.py). Blocker-resolver proposed"
        " E5-F3-S1-T4 to own constants, schema, and sample-config changes."
        " Task will auto-requeue once E5-F3-S1-T4 completes.\n"
    )

    def test_regression_e5_f3_s1_t1_auto_clears_after_t4_done(self, tmp_path: Path) -> None:
        """T4 completing must trigger T1 auto-unblock in one sweep tick.

        The fixture preserves the exact audit-log text so future audit-format
        changes do not silently break the regression. The cascade must write
        [AUTO_UNBLOCKED] [CASCADE_RESOLVED] and flip T1 to in-queue without
        operator intervention.
        """
        src_file = _unit_body(
            "E5-F3-S1-T1",
            "blocked",
            deps=["E5-F3-S1-T4"],  # dep table entry as it existed at capture time
            comments=self._T1_AUDIT_COMMENTS,
        )
        dep_file = _unit_body("E5-F3-S1-T4", "done")
        index = _write_workspace(
            tmp_path,
            rows=[
                ("E5-F3-S1-T1", "AddSampleConfig", "blocked"),
                ("E5-F3-S1-T4", "SampleConfigConstants", "done"),
            ],
            files={"E5-F3-S1-T1": src_file, "E5-F3-S1-T4": dep_file},
        )

        # Single sweep tick: T4 just completed.
        BacklogManager()._auto_requeue_marker_dependents(index, "E5-F3-S1-T4")

        t1_content = (tmp_path / "backlog" / "E5-F3-S1-T1.md").read_text()
        assert "## Status: in-queue" in t1_content, "T1 must flip to in-queue in one sweep tick"
        assert "[AUTO_UNBLOCKED]" in t1_content
        assert "[CASCADE_RESOLVED]" in t1_content
        assert "E5-F3-S1-T4" in t1_content  # audit names the marker ID
        # Verify no operator intervention line was present before the fix:
        # the [REJECTION_FEEDBACK_RESOLVED] line from the captured log was
        # an operator action, which should no longer be necessary.
        assert (
            "[REJECTION_FEEDBACK_RESOLVED]" not in t1_content
            or "operator" not in t1_content.split("[REJECTION_FEEDBACK_RESOLVED]")[0].split("\n")[-1]
        ), "Auto-unblock must precede any operator action"


# ---------------------------------------------------------------------------
# Issue #221 B2: bare ``.md`` extension in AC prose MUST NOT be flagged
# ---------------------------------------------------------------------------


class TestBareMdExtensionNotOrphan:
    """Issue #221 B2: bare ``.md`` (the extension only) in prose isn't a path.

    Prose like "only ``.md`` files modified" or "the work-unit ``.md``
    file" backticks the extension itself for emphasis. The orphan-path
    rule must not flag this 3-char string as a path; only tokens with
    a real filename stem or a directory separator qualify.
    """

    H = _ValidateRuleHarness

    @staticmethod
    def _runtime_with_rule_on(repo: str) -> RuntimeConfig:
        return RuntimeConfig(
            repos={repo: RepoConfig(checkout_directory=None)},
            validate=ValidateConfig(check_orphan_path_tokens=True),
        )

    def test_bare_md_in_ac_not_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        TestValidateNoOrphanPathTokens._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/real.py` | new |\n| `tests/unit/test_real.py` | new |\n",
            "- [ ] AC-FUNC-001: only `.md` files modified in this task.",
        )
        TestBareMdExtensionNotOrphan.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        with patch("devbench.config.RUNTIME_CONFIG", self._runtime_with_rule_on(repo)):
            errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        orphans = [e for e in errors if "orphan path" in e]
        assert not orphans, f"bare `.md` should not be flagged; got: {orphans}"

    def test_real_md_path_still_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        TestValidateNoOrphanPathTokens._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/real.py` | new |\n| `tests/unit/test_real.py` | new |\n",
            "- [ ] AC-FUNC-001: `docs/imaginary.md` updated.",
        )
        TestBareMdExtensionNotOrphan.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        with patch("devbench.config.RUNTIME_CONFIG", self._runtime_with_rule_on(repo)):
            errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        orphans = [e for e in errors if "orphan path" in e and "docs/imaginary.md" in e]
        assert len(orphans) == 1


# ---------------------------------------------------------------------------
# Issue #221 B3: sentinel values are exempt from path-based rules
# ---------------------------------------------------------------------------


class TestSentinelManifestExemption:
    """Issue #221 B3: ``<verification-only>`` etc. are NOT real Manifest paths.

    Two tasks both claiming ``<verification-only>`` must NOT trigger
    the Manifest Conflict Rule. A decision-only task whose Manifest is
    ``<decision-only>`` must NOT trigger source-test atomicity.
    """

    def test_sentinel_not_real_manifest_path(self) -> None:
        from devbench.backlog.manager import BacklogManager

        assert BacklogManager._is_real_manifest_path("<verification-only>") is False
        assert BacklogManager._is_real_manifest_path("<decision-only>") is False
        assert BacklogManager._is_real_manifest_path("<no-op>") is False
        assert BacklogManager._is_real_manifest_path("<no changes>") is False
        assert BacklogManager._is_real_manifest_path("<verification-only:E15-F5-S1-T2>") is False
        # Real path with angle brackets in surrounding prose-style is still real.
        assert BacklogManager._is_real_manifest_path("src/foo.py") is True
        # Sentinel-shaped but with leading/trailing whitespace still recognised.
        assert BacklogManager._is_real_manifest_path("  <verification-only>  ") is False

    def test_placeholder_paren_form_still_filtered(self) -> None:
        from devbench.backlog.manager import BacklogManager

        assert BacklogManager._is_real_manifest_path("(none)") is False
        assert BacklogManager._is_real_manifest_path("(no file changes; documentation only)") is False


# ---------------------------------------------------------------------------
# Issue #221 B4: Manifest entries with glob patterns are rejected
# ---------------------------------------------------------------------------


class TestManifestGlobRejection:
    """Issue #221 B4: globs (``*``, ``**``) in Manifest paths emit a clear error."""

    H = _ValidateRuleHarness

    def test_glob_in_manifest_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        TestValidateNoOrphanPathTokens._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/**/*.py` | Update conditional |\n",
            "- [ ] AC-FUNC-001: drift fixed.",
        )
        TestManifestGlobRejection.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        glob_errors = [e for e in errors if "glob pattern" in e]
        assert len(glob_errors) == 1
        assert "src/**/*.py" in glob_errors[0]
        assert "sentinel" in glob_errors[0]
        assert "manifest_amendment" in glob_errors[0]

    def test_no_glob_no_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        repo = "ex/foo"
        TestValidateNoOrphanPathTokens._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `src/real.py` | new |\n| `tests/unit/test_real.py` | new |\n",
            "- [ ] AC-FUNC-001: done.",
        )
        TestManifestGlobRejection.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("glob pattern" in e for e in errors)

    def test_sentinel_with_no_glob_passes(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Sentinels are exempt from glob rejection -- they're not paths."""
        repo = "ex/foo"
        TestValidateNoOrphanPathTokens._make_task_with_sections(
            backlog_dir,
            "EX-F1-S1-T1",
            repo,
            "| `<source-drift-fix-targets-determined-at-execution>` | run-time list |\n",
            "- [ ] AC-FUNC-001: done.",
        )
        TestManifestGlobRejection.H.make_index(
            tmp_path,
            f"| EX-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/EX-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(tmp_path / "BACKLOG.md", tmp_path)
        assert not any("glob pattern" in e for e in errors)


class TestValidateBacklogUniqueIds:
    """Check 21: one work-unit ID must map to exactly one index row.

    A unit written into two directory trees yields two index rows under one
    ID. Every other check still passes, because both files exist, each
    matches its own row's status, and neither is orphaned. The backlog is
    nonetheless incoherent: the rows can disagree about status, and a
    dependency on that ID resolves against whichever row is reached first.
    """

    @staticmethod
    def _build_workspace(tmp_path: Path, backlog_dir: Path, *, duplicate: bool) -> Path:
        """Write a minimal backlog, optionally indexing one unit under two paths."""
        rows = [
            ("E1", "Epic One", "Epic", "in-queue", "backlog/E1.md"),
            ("E1-F1-S1-T1", "Task Alpha", "Task", "done", "backlog/E1-F1-S1-T1.md"),
        ]
        if duplicate:
            rows.append(("E1-F1-S1-T1", "Task Alpha", "Task", "declined", "backlog/dup/E1-F1-S1-T1.md"))

        header = (
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|----------|\n"
        )
        body = "".join(
            f"| {rid} | {title} | {typ} | {st} | None | repo | `{fp}` |\n" for rid, title, typ, st, fp in rows
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(header + body, encoding="utf-8")

        (backlog_dir / "E1.md").write_text("# E1\n\n## Status: in-queue\n", encoding="utf-8")
        (backlog_dir / "E1-F1-S1-T1.md").write_text("# E1-F1-S1-T1\n\n## Status: done\n", encoding="utf-8")
        if duplicate:
            dup_dir = backlog_dir / "dup"
            dup_dir.mkdir(parents=True, exist_ok=True)
            (dup_dir / "E1-F1-S1-T1.md").write_text("# E1-F1-S1-T1\n\n## Status: declined\n", encoding="utf-8")
        return index_path

    def test_unique_ids_produce_no_duplicate_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        index_path = self._build_workspace(tmp_path, backlog_dir, duplicate=False)
        errors = BacklogManager().validate(index_path, tmp_path)
        assert not [e for e in errors if "duplicate work unit ID" in e]

    def test_duplicate_id_is_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        """The exact shape that previously passed: same ID, conflicting statuses, both files present."""
        index_path = self._build_workspace(tmp_path, backlog_dir, duplicate=True)
        errors = BacklogManager().validate(index_path, tmp_path)
        duplicates = [e for e in errors if "duplicate work unit ID" in e]
        assert len(duplicates) == 1
        msg = duplicates[0]
        assert msg.startswith("E1-F1-S1-T1:")
        assert "2 index rows share this ID" in msg
        # Both conflicting statuses and both paths are named so the operator can act.
        assert "status 'done' at backlog/E1-F1-S1-T1.md" in msg
        assert "status 'declined' at backlog/dup/E1-F1-S1-T1.md" in msg

    def test_duplicate_id_is_not_masked_by_the_other_checks_passing(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Regression: file-existence, status-match and orphan checks all pass here."""
        index_path = self._build_workspace(tmp_path, backlog_dir, duplicate=True)
        errors = BacklogManager().validate(index_path, tmp_path)
        assert not [e for e in errors if "work unit file missing" in e]
        assert not [e for e in errors if "status mismatch" in e]
        assert not [e for e in errors if "orphaned work unit file" in e]
        assert [e for e in errors if "duplicate work unit ID" in e]

    def test_three_rows_under_one_id_are_all_reported(self, tmp_path: Path, backlog_dir: Path) -> None:
        index_path = self._build_workspace(tmp_path, backlog_dir, duplicate=True)
        extra_dir = backlog_dir / "dup2"
        extra_dir.mkdir(parents=True, exist_ok=True)
        (extra_dir / "E1-F1-S1-T1.md").write_text("# E1-F1-S1-T1\n\n## Status: hold\n", encoding="utf-8")
        index_path.write_text(
            index_path.read_text(encoding="utf-8")
            + "| E1-F1-S1-T1 | Task Alpha | Task | hold | None | repo | `backlog/dup2/E1-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        errors = BacklogManager().validate(index_path, tmp_path)
        duplicates = [e for e in errors if "duplicate work unit ID" in e]
        assert len(duplicates) == 1
        assert "3 index rows share this ID" in duplicates[0]


class TestMarkerScannerIgnoresQuotedMarkers:
    """Issue #304: quoting a marker in prose must not create one.

    Both writers emit ``[BLOCKED_PENDING_PROPOSAL] <id>`` as the final token
    of an audit row, so end-anchoring admits every marker devbench writes
    while excluding prose that merely mentions one. Before this, an operator
    audit comment recording that a marker had been removed, quoting the
    removed line verbatim, silently re-blocked the unit on the quoted ID.
    """

    @staticmethod
    def _wu(tmp_path: Path, comments: str) -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(f"# E0-F1-S1-T1: T\n\n## Status: blocked\n\n## Comments\n{comments}", encoding="utf-8")
        return wu

    def test_marker_at_end_of_an_audit_row_is_live(self, tmp_path: Path) -> None:
        wu = self._wu(
            tmp_path,
            "\n[2026-05-01 12:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T9 promoted and "
            "wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == {"E0-F1-S1-T9"}

    def test_add_dep_marker_shape_is_live(self, tmp_path: Path) -> None:
        wu = self._wu(
            tmp_path,
            "\n[2026-05-01 12:00 UTC] [agent/operator] [WU_WIRED] E0-F1-S1-T1 manually blocked on "
            "E0-F1-S1-T9 via `devbench add-dep`. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == {"E0-F1-S1-T9"}

    def test_quoted_marker_mid_sentence_creates_no_marker(self, tmp_path: Path) -> None:
        """The exact operator-audit shape that silently re-blocked a unit."""
        wu = self._wu(
            tmp_path,
            "\n[2026-05-01 12:05 UTC] [agent/operator] [CYCLE_BROKEN] The inverted marker line was removed. "
            "It read verbatim: '[2026-05-01 12:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T9 "
            "promoted. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9' and the edge now runs the other way.\n",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == set()

    def test_live_marker_survives_alongside_a_quoted_one(self, tmp_path: Path) -> None:
        """Removing one marker while quoting another must not disturb the survivor."""
        wu = self._wu(
            tmp_path,
            "\n[2026-05-01 12:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T8 promoted. "
            "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T8\n"
            "\n[2026-05-01 12:05 UTC] [agent/operator] [CYCLE_BROKEN] removed the row reading "
            "'[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9' because it was inverted.\n",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == {"E0-F1-S1-T8"}

    def test_trailing_whitespace_after_a_marker_is_tolerated(self, tmp_path: Path) -> None:
        wu = self._wu(
            tmp_path,
            "\n[2026-05-01 12:00 UTC] [agent/operator] [WU_WIRED] x. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9   \n",
        )
        assert BacklogManager()._extract_pending_proposal_markers(wu) == {"E0-F1-S1-T9"}
