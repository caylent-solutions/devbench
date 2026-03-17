"""Tests for judges.backlog_manager module."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.constants import REVIEW_JUDGE_NAMES, VALID_STATUSES


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

    def test_allows_done_without_judge_comments(
        self, tmp_work_unit_file: Path, backlog_index_titlecase: Path
    ) -> None:
        """force_status bypasses the done-gate — no judge comments required."""
        judge = BacklogManager()
        judge.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "done")

        assert "## Status: done" in tmp_work_unit_file.read_text()

    def test_updates_lowercase_statuses_in_backlog(
        self, tmp_work_unit_file: Path, backlog_index_lowercase: Path,
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


def _judge_comment(judge_name: str, action: str, msg: str = "ok") -> str:
    """Return a single formatted judge comment line (no trailing newline)."""
    return f"[2024-01-01 00:00 UTC] [judge/{judge_name}] [{action}] {msg}"


def _all_judges_pass_block() -> str:
    """Return comment lines for all four required judges passing."""
    return "\n".join(
        _judge_comment(j, "REVIEW_PASS") for j in sorted(REVIEW_JUDGE_NAMES)
    ) + "\n"


_ALL_JUDGES_PASSED_COMMENTS = _all_judges_pass_block()


class TestMarkDone:
    """Test mark_done delegates to set_status and updates both files."""

    def test_mark_done_updates_both_files(self, tmp_work_unit_file: Path, backlog_index_titlecase: Path) -> None:
        # Append required judge pass entries so the done-gate check passes
        content = tmp_work_unit_file.read_text(encoding="utf-8")
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
        bad_file.write_text(
            "# No status here\nJust content.\n" + _ALL_JUDGES_PASSED_COMMENTS
        )

        judge = BacklogManager()
        with pytest.raises(ValueError, match="Could not find"):
            judge.mark_done(bad_file, backlog_index_titlecase, "E0-F1-S1-T1")


class TestMarkBlocked:
    """Test mark_blocked updates both files and appends comment."""

    def test_mark_blocked_updates_both_files_and_adds_comment(
        self, tmp_work_unit_file: Path, backlog_index_titlecase: Path,
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


class TestRollupParentStatus:
    """Test that marking the last child Done rolls up to parent."""

    def test_story_marked_done_when_all_tasks_done(
        self, backlog_with_hierarchy: tuple[Path, Path, Path, Path],
    ) -> None:
        index_path, t2_file, story_file, feature_file = backlog_with_hierarchy

        judge = BacklogManager()
        # T1 is already Done in the fixture. Mark T2 Done — should roll up S1.
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
        self, backlog_with_hierarchy: tuple[Path, Path, Path, Path],
    ) -> None:
        index_path, t2_file, story_file, feature_file = backlog_with_hierarchy

        judge = BacklogManager()
        # Mark T2 as in-progress — T1 is Done but T2 is not, so story stays
        judge.force_status(t2_file, index_path, "E0-F1-S1-T2", "in-progress")

        story_content = story_file.read_text()
        assert "## Status: in-queue" in story_content

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


class TestRollupMissingParent:
    """Tests for _rollup_parent_status() when parent ID is absent from the backlog index.

    The crash scenario: a parent row exists in BACKLOG.md but with no recognized status
    cell (e.g. an epic-level row like ``| E6 | Epic Title | | | | | backlog/E6.md |``).
    ``_all_children_done()`` sees the row (parent_found=True, status=""), treats it as
    not-yet-done, and returns True once all children are done.  ``_find_work_unit_file``
    then resolves the parent file.  ``_set_status`` calls ``_update_backlog_index``, which
    finds the row by ID but cannot locate a recognized status cell → raises ``ValueError``.

    The fix: in ``_rollup_parent_status()``, check whether the parent ID has a recognized
    status entry in the parsed rows before calling ``_set_status()``.  If absent, log
    DEBUG and return without raising.
    """

    def _make_index_with_statusless_parent(
        self, tmp_path: Path, backlog_dir: Path
    ) -> tuple[Path, Path, Path]:
        """Build a BACKLOG.md where the parent row has no recognized status cell.

        E0-F1-S1-T1 and E0-F1-S1-T2 are both children (T2 is already Done).
        E0-F1-S1 is the parent row, present in the index but with an empty Status
        column so ``_update_backlog_index`` cannot update it.
        """
        content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Task A | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Task B | Task | Done | None | git-repo | `backlog/E0-F1-S1-T2.md` |
| E0-F1-S1 | Story A | Story | | None | git-repo | `backlog/E0-F1-S1.md` |
"""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(content)

        t1_file = backlog_dir / "E0-F1-S1-T1.md"
        t1_file.write_text("# E0-F1-S1-T1\n\n## Status: in-queue\n")
        story_file = backlog_dir / "E0-F1-S1.md"
        story_file.write_text("# E0-F1-S1\n\n## Status: in-queue\n")

        return index_path, t1_file, story_file

    def test_set_status_completes_when_parent_absent_from_index(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: set_status on a child completes successfully when the parent has no updatable index row.

        Given: A backlog index where the parent row (E0-F1-S1) has no recognized status cell
        When: force_status is called on the last child (E0-F1-S1-T1) with status 'done'
        Then: No exception is raised and the child status is written correctly
        Spec: manager.py:_rollup_parent_status
        """
        index_path, t1_file, _ = self._make_index_with_statusless_parent(tmp_path, backlog_dir)

        manager = BacklogManager()
        # Must not raise even though parent E0-F1-S1 has no recognized status cell
        manager.force_status(t1_file, index_path, "E0-F1-S1-T1", "done")

        # Child status is persisted correctly
        assert "## Status: done" in t1_file.read_text(encoding="utf-8")

    def test_debug_logged_when_rollup_skipped(
        self, tmp_path: Path, backlog_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-5: a DEBUG message is emitted when rollup is skipped for a missing parent.

        Given: A backlog index where the parent row (E0-F1-S1) has no recognized status cell
        When: force_status marks the last child done and rollup would fire
        Then: A DEBUG log message is emitted that identifies the missing parent ID
        Spec: manager.py:_rollup_parent_status
        """
        import logging

        index_path, t1_file, _ = self._make_index_with_statusless_parent(tmp_path, backlog_dir)

        manager = BacklogManager()
        with caplog.at_level(logging.DEBUG, logger="devbench.backlog_manager"):
            manager.force_status(t1_file, index_path, "E0-F1-S1-T1", "done")

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("E0-F1-S1" in msg for msg in debug_messages), (
            f"Expected DEBUG message mentioning 'E0-F1-S1' but got: {debug_messages}"
        )

    def test_rollup_proceeds_normally_when_parent_present(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-3: rollup behaviour is unchanged when the parent ID is present with a recognized status.

        Given: A backlog index where both child and parent have rows with recognized statuses
        When: force_status marks the last child done
        Then: The parent status is rolled up to 'done' as before
        Spec: manager.py:_rollup_parent_status
        """
        content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Task A | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Task B | Task | Done | None | git-repo | `backlog/E0-F1-S1-T2.md` |
| E0-F1-S1 | Story A | Story | in-queue | None | git-repo | `backlog/E0-F1-S1.md` |
"""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(content)

        t1_file = backlog_dir / "E0-F1-S1-T1.md"
        t1_file.write_text("# E0-F1-S1-T1\n\n## Status: in-queue\n")
        story_file = backlog_dir / "E0-F1-S1.md"
        story_file.write_text("# E0-F1-S1\n\n## Status: in-queue\n")

        manager = BacklogManager()
        manager.force_status(t1_file, index_path, "E0-F1-S1-T1", "done")

        assert "## Status: done" in story_file.read_text(encoding="utf-8"), (
            "Parent story should be rolled up to done when all children are done"
        )
        index_content = index_path.read_text()
        for line in index_content.splitlines():
            if "| E0-F1-S1 |" in line and "Story" in line:
                assert " done " in line
                break
        else:
            pytest.fail("E0-F1-S1 story row not found in BACKLOG.md")


class TestUpdateBacklogIndexStatusSummary:
    """Tests for _update_backlog_index() when a Status Summary row precedes the index row.

    The crash scenario: BACKLOG.md contains a Status Summary section (e.g.
    ``| E24 | Git Ops Branch Hygiene | 1 | 1 | 2 | 5 | 0 | 0 | 0 | 5 |``) that
    appears before the Full Work Unit Index row (e.g.
    ``| E24 | ... | Epic | in-queue | ... |``).

    ``_update_backlog_index`` scans lines for a row whose first ID cell matches
    the target ID.  The Status Summary row matches the ID but contains only
    integer count cells — none of which are recognized statuses.  The old code
    unconditionally wrote the (unmodified) line back and broke out of the loop,
    leaving ``updated=False`` and raising ``ValueError``.

    The fix: only ``break`` from the scan when a recognized status cell was
    actually found and replaced (i.e., ``updated=True``).  If the matching row
    has no recognized status cell, continue scanning.
    """

    def _make_index_with_status_summary(self, tmp_path: Path, backlog_dir: Path) -> tuple[Path, Path]:
        """Build a BACKLOG.md where a Status Summary row for E9 precedes the index row.

        The Status Summary row has integer cells only (no recognized status value).
        The Full Work Unit Index row has the real ``in-queue`` status that must be updated.
        """
        content = """\
# Backlog

## Status Summary

| Epic | Title | Features | Stories | Tasks | Total | Done | In Progress | In Review | In Queue |
|------|-------|----------|---------|-------|-------|------|-------------|-----------|----------|
| E9 | Config Schema | 1 | 2 | 8 | 12 | 7 | 0 | 0 | 5 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E9 | Config Schema | Epic | in-queue | None | devbench | `backlog/E9.md` |
"""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(content)

        epic_file = backlog_dir / "E9.md"
        epic_file.write_text("# E9\n\n## Status: in-queue\n")

        return index_path, epic_file

    def test_update_backlog_index_skips_status_summary_row(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """_update_backlog_index must skip the Status Summary row and update the index row.

        Given: A BACKLOG.md with a Status Summary row for E9 appearing before the
               Full Work Unit Index row for E9
        When: _update_backlog_index is called to set E9 status to 'done'
        Then: The Full Work Unit Index row is updated to 'done'
              The Status Summary row is left unchanged (integers intact)
              No ValueError is raised
        Spec: manager.py:_update_backlog_index
        """
        index_path, _ = self._make_index_with_status_summary(tmp_path, backlog_dir)

        manager = BacklogManager()
        # Must not raise even though the Status Summary row matches E9 first
        manager._update_backlog_index(index_path, "E9", "done")

        updated = index_path.read_text(encoding="utf-8")
        lines = updated.splitlines()

        # The Full Work Unit Index row must now show 'done'
        index_row = next(
            (l for l in lines if "| E9 |" in l and "Epic" in l),
            None,
        )
        assert index_row is not None, "Full Work Unit Index row for E9 not found"
        assert " done " in index_row, (
            f"Expected 'done' in index row but got: {index_row!r}"
        )

        # The Status Summary row must be unchanged (integers, no status keyword injected)
        summary_row = next(
            (l for l in lines if "| E9 |" in l and "Config Schema" in l and "Epic" not in l),
            None,
        )
        assert summary_row is not None, "Status Summary row for E9 not found"
        assert "done" not in summary_row, (
            f"Status Summary row must not be modified but got: {summary_row!r}"
        )

    def test_update_backlog_index_no_status_summary_still_works(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """_update_backlog_index works correctly when no Status Summary section exists.

        Given: A BACKLOG.md with only a Full Work Unit Index (no Status Summary)
        When: _update_backlog_index is called
        Then: The index row is updated correctly and no exception is raised
        Spec: manager.py:_update_backlog_index
        """
        content = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E9 | Config Schema | Epic | in-queue | None | devbench | `backlog/E9.md` |
"""
        index_path = tmp_path / "BACKLOG2.md"
        index_path.write_text(content)

        manager = BacklogManager()
        manager._update_backlog_index(index_path, "E9", "done")

        updated = index_path.read_text(encoding="utf-8")
        assert " done " in updated, "Index row must be updated to 'done'"


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
        wu = self._make_wu_with_comments(tmp_path, _all_judges_pass_block())
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is True

    def test_returns_false_when_judge_missing(self, tmp_path: Path) -> None:
        comments = "\n".join([
            _judge_comment("code_review", "REVIEW_PASS"),
            _judge_comment("test_review", "REVIEW_PASS"),
            _judge_comment("doc_review", "REVIEW_PASS"),
            # changes_manifest missing
        ]) + "\n"
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False

    def test_returns_false_when_followed_by_review_rejected(self, tmp_path: Path) -> None:
        """All 4 judges passed in round 1, but then REVIEW_REJECTED — round 2 has no passes."""
        comments = (
            # Round 1 passes (older, before REVIEW_REJECTED)
            _all_judges_pass_block()
            + "[2024-01-01 00:04 UTC] [orchestrator] [REVIEW_REJECTED] attempt 1 rejected\n"
            # Round 2 has no REVIEW_PASS entries yet (only rejection so far)
        )
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False

    def test_returns_true_when_round2_passes_after_rejection(self, tmp_path: Path) -> None:
        """Round 2 passes after a prior round was rejected."""
        comments = (
            # Round 1 — rejected
            _judge_comment("code_review", "REVIEW_PASS") + "\n"
            + "[2024-01-01 00:01 UTC] [orchestrator] [REVIEW_REJECTED] attempt 1 rejected\n"
            # Round 2 — all pass
            + _all_judges_pass_block()
        )
        wu = self._make_wu_with_comments(tmp_path, comments)
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is True

    def test_returns_false_when_no_comments(self, tmp_path: Path) -> None:
        wu = self._make_wu_with_comments(tmp_path, "")
        judge = BacklogManager()
        assert judge._last_round_all_passed(wu) is False


class TestMarkDoneGate:
    """Test that mark_done enforces the done-gate check."""

    def _make_wu(self, tmp_path: Path, comments: str = "") -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            f"# E0-F1-S1-T1\n\n## Status: in-review\n\n## Comments\n\n{comments}",
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
        wu = self._make_wu(tmp_path, _all_judges_pass_block())
        idx = self._make_index(tmp_path)
        judge = BacklogManager()
        judge.mark_done(wu, idx, "E0-F1-S1-T1")
        assert "## Status: done" in wu.read_text(encoding="utf-8")


class TestValidate:
    """Tests for BacklogManager.validate() backlog integrity checks."""

    def _make_index(self, tmp_path: Path, rows: str, summary: str = "") -> Path:
        idx = tmp_path / "BACKLOG.md"
        summary_section = (
            "## Status Summary\n\n"
            + summary
            + "\n---\n\n"
            if summary
            else ""
        )
        idx.write_text(
            "# Backlog\n\n"
            + summary_section
            + "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            + rows,
            encoding="utf-8",
        )
        return idx

    def _make_wu(self, backlog_dir: Path, unit_id: str, status: str = "in-queue", with_comments: bool = True) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        comments = "\n## Comments\n" if with_comments else ""
        wu.write_text(f"# {unit_id}\n\n## Status: {status}\n{comments}", encoding="utf-8")
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
        assert errors == [], f"Expected no errors but got: {errors}"

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

    # ------------------------------------------------------------------
    # AC-1: Status Summary count drift detection
    # ------------------------------------------------------------------

    def test_validate_backlog_detects_status_summary_count_drift(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: validate reports an error when Status Summary counts don't match index counts."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-queue")
        # Summary claims 1 in-queue but index has 2
        summary = (
            "| Epic | Title | In Queue | Done |\n"
            "|------|-------|----------|------|\n"
            "| E0 | Fixes | 1 | 0 |\n"
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T2.md` |\n",
            summary=summary,
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("summary" in e.lower() or "count" in e.lower() for e in errors), (
            f"Expected count drift error but got: {errors}"
        )

    def test_validate_backlog_no_summary_count_errors_when_counts_match(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: validate reports no summary errors when counts match the index."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "done")
        summary = (
            "| Epic | Title | In Queue | Done |\n"
            "|------|-------|----------|------|\n"
            "| E0 | Fixes | 1 | 1 |\n"
        )
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | done | none | repo | `backlog/E0-F1-S1-T2.md` |\n",
            summary=summary,
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        summary_errors = [e for e in errors if "summary" in e.lower() or "count" in e.lower()]
        assert summary_errors == [], f"Expected no count errors but got: {summary_errors}"

    def test_validate_backlog_no_summary_errors_when_no_summary_section(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: validate skips count check gracefully when no Status Summary section is present."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        summary_errors = [e for e in errors if "summary" in e.lower() or "count" in e.lower()]
        assert summary_errors == [], f"Expected no count errors but got: {summary_errors}"

    # ------------------------------------------------------------------
    # AC-2: blocked status recognized in validation
    # ------------------------------------------------------------------

    def test_validate_backlog_accepts_blocked_status(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-2: validate accepts blocked status in both index and work-unit file without spurious errors."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "blocked")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | blocked | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        status_errors = [e for e in errors if "E0-F1-S1-T1" in e and "status" in e.lower()]
        assert status_errors == [], (
            f"blocked status should not produce a status error but got: {status_errors}"
        )

    def test_validate_backlog_reports_mismatch_when_blocked_vs_other(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-2: validate reports status mismatch when index says blocked but file says in-queue."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | blocked | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "status" in e.lower() for e in errors), (
            f"Expected mismatch error for blocked vs in-queue but got: {errors}"
        )

    # ------------------------------------------------------------------
    # AC-3: required section headers in task files
    # ------------------------------------------------------------------

    def test_validate_backlog_reports_missing_comments_header(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-3: validate reports an error when a task file has no ## Comments section."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue", with_comments=False)
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        assert any("E0-F1-S1-T1" in e and "comments" in e.lower() for e in errors), (
            f"Expected missing comments header error but got: {errors}"
        )

    def test_validate_backlog_no_comments_error_when_header_present(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-3: validate does not report a comments error when ## Comments section is present."""
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue", with_comments=True)
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        judge = BacklogManager()
        errors = judge.validate(idx, tmp_path)
        comments_errors = [e for e in errors if "comments" in e.lower()]
        assert comments_errors == [], f"Expected no comments errors but got: {comments_errors}"

    # ------------------------------------------------------------------
    # AC-5: existing checks remain intact
    # ------------------------------------------------------------------

    def test_validate_backlog_keeps_existing_integrity_checks(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-5: all four original integrity checks still function after new checks are added."""
        # Check 1: missing file
        idx1 = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
        )
        errors1 = BacklogManager().validate(idx1, tmp_path)
        assert any("E0-F1-S1-T1" in e and "missing" in e.lower() for e in errors1), (
            f"file existence check broken: {errors1}"
        )

        # Check 2: status mismatch
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "done")
        idx2 = self._make_index(
            tmp_path,
            "| E0-F1-S1-T2 | Task 2 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors2 = BacklogManager().validate(idx2, tmp_path)
        assert any("E0-F1-S1-T2" in e and "status" in e.lower() for e in errors2), (
            f"status mismatch check broken: {errors2}"
        )

        # Check 3: orphan detection
        self._make_wu(backlog_dir, "E0-F1-S1-T3", "in-queue")
        idx3 = self._make_index(
            tmp_path,
            "| E0-F1-S1-T2 | Task 2 | Task | done | none | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors3 = BacklogManager().validate(idx3, tmp_path)
        assert any("orphan" in e.lower() for e in errors3), (
            f"orphan detection check broken: {errors3}"
        )

        # Check 4: broken dependency
        self._make_wu(backlog_dir, "E0-F1-S1-T4", "in-queue")
        idx4 = self._make_index(
            tmp_path,
            "| E0-F1-S1-T4 | Task 4 | Task | in-queue | E0-NONEXISTENT | repo | `backlog/E0-F1-S1-T4.md` |\n",
        )
        errors4 = BacklogManager().validate(idx4, tmp_path)
        assert any("E0-NONEXISTENT" in e and "depend" in e.lower() for e in errors4), (
            f"dependency check broken: {errors4}"
        )

    # ------------------------------------------------------------------
    # Check 7: dep-status violation detection (E15-F2-S1-T2)
    # ------------------------------------------------------------------

    def test_validate_reports_in_queue_unit_with_incomplete_dep(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: validate reports an error when an in-queue unit has an in-queue dep.

        Given: T2 is in-queue and depends on T1 which is also in-queue
        When: validate is called
        Then: An error is emitted for T2 indicating T1 is 'in-queue'
        Spec: E15-F2-S1-T2 AC-1
        """
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-queue")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | in-queue | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any(
            "E0-F1-S1-T2" in e
            and "E0-F1-S1-T1" in e
            and "in-queue" in e
            for e in errors
        ), f"Expected dep-status error for T2 but got: {errors}"

    def test_validate_reports_in_progress_unit_with_blocked_dep(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-2: validate reports an error when an in-progress unit has a blocked dep.

        Given: T2 is in-progress and depends on T1 which is blocked
        When: validate is called
        Then: An error is emitted for T2 indicating T1 is 'blocked'
        Spec: E15-F2-S1-T2 AC-2
        """
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "blocked")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-progress")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | blocked | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | in-progress | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        assert any(
            "E0-F1-S1-T2" in e
            and "E0-F1-S1-T1" in e
            and "blocked" in e
            for e in errors
        ), f"Expected dep-status error for T2 with blocked dep but got: {errors}"

    def test_validate_passes_when_all_deps_done(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-3: validate does not report an error when all deps of an in-queue unit are done.

        Given: T2 is in-queue and depends on T1 which is done
        When: validate is called
        Then: No dep-status error is emitted
        Spec: E15-F2-S1-T2 AC-3
        """
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "done")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-queue")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | done | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | in-queue | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        dep_status_errors = [
            e for e in errors
            if "E0-F1-S1-T2" in e and "E0-F1-S1-T1" in e and ("is '" in e or "but unit" in e)
        ]
        assert dep_status_errors == [], (
            f"Expected no dep-status errors but got: {dep_status_errors}"
        )

    def test_validate_does_not_flag_blocked_units(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: validate does not report dep-status errors for blocked units.

        Given: T2 is blocked and depends on T1 which is in-queue
        When: validate is called
        Then: No dep-status error is emitted for T2
        Spec: E15-F2-S1-T2 AC-4
        """
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-queue")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "blocked")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | blocked | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        dep_status_errors = [
            e for e in errors
            if "E0-F1-S1-T2" in e and ("is '" in e or "but unit" in e)
        ]
        assert dep_status_errors == [], (
            f"blocked units must not be flagged for dep-status but got: {dep_status_errors}"
        )

    def test_validate_error_includes_dep_status(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-5: error message includes dep ID and its current status.

        Given: T2 is in-queue with dep T1 that is in-review
        When: validate is called
        Then: Error contains T1's ID and status 'in-review', and T2's status 'in-queue'
        Spec: E15-F2-S1-T2 AC-5
        """
        self._make_wu(backlog_dir, "E0-F1-S1-T1", "in-review")
        self._make_wu(backlog_dir, "E0-F1-S1-T2", "in-queue")
        idx = self._make_index(
            tmp_path,
            "| E0-F1-S1-T1 | Task 1 | Task | in-review | none | repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Task 2 | Task | in-queue | E0-F1-S1-T1 | repo | `backlog/E0-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        # Error must match: "<unit-id>: dep '<dep-id>' is '<dep-status>' but unit is '<unit-status>'"
        matching = [
            e for e in errors
            if "E0-F1-S1-T2" in e
            and "E0-F1-S1-T1" in e
            and "in-review" in e
            and "in-queue" in e
        ]
        assert matching, (
            f"Expected error with dep ID 'E0-F1-S1-T1' and status 'in-review' but got: {errors}"
        )

    # ------------------------------------------------------------------
    # E19-F1-S1-T1: Check 8 — unique task branch names
    # ------------------------------------------------------------------

    def test_check_8_duplicate_branch_names_reported(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: Two Task files sharing a branch name → error listing both task IDs.

        Given: T1 and T2 are both Task rows whose work-unit files declare
               the same **Branch:** value
        When: validate is called
        Then: An error is returned that contains both task IDs and the shared
              branch name
        Spec: E19-F1-S1-T1 AC-1
        """
        t1 = backlog_dir / "E1-F1-S1-T1.md"
        t1.write_text(
            "# E1-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Branch:** `feature/shared-branch`\n\n## Comments\n",
            encoding="utf-8",
        )
        t2 = backlog_dir / "E1-F1-S1-T2.md"
        t2.write_text(
            "# E1-F1-S1-T2\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Branch:** `feature/shared-branch`\n\n## Comments\n",
            encoding="utf-8",
        )
        idx = self._make_index(
            tmp_path,
            "| E1-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E1-F1-S1-T1.md` |\n"
            "| E1-F1-S1-T2 | Task 2 | Task | in-queue | none | repo | `backlog/E1-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        duplicate_errors = [e for e in errors if "shared-branch" in e]
        assert duplicate_errors, f"Expected duplicate-branch error but got: {errors}"
        assert any("E1-F1-S1-T1" in e for e in duplicate_errors)
        assert any("E1-F1-S1-T2" in e for e in duplicate_errors)

    def test_check_8_unique_branch_names_pass(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-2: Two Task files with different branches → no error.

        Given: T1 declares branch 'feature/branch-a' and T2 declares 'feature/branch-b'
        When: validate is called
        Then: No duplicate-branch error is emitted
        Spec: E19-F1-S1-T1 AC-2
        """
        t1 = backlog_dir / "E1-F1-S1-T1.md"
        t1.write_text(
            "# E1-F1-S1-T1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Branch:** `feature/branch-a`\n\n## Comments\n",
            encoding="utf-8",
        )
        t2 = backlog_dir / "E1-F1-S1-T2.md"
        t2.write_text(
            "# E1-F1-S1-T2\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Branch:** `feature/branch-b`\n\n## Comments\n",
            encoding="utf-8",
        )
        idx = self._make_index(
            tmp_path,
            "| E1-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E1-F1-S1-T1.md` |\n"
            "| E1-F1-S1-T2 | Task 2 | Task | in-queue | none | repo | `backlog/E1-F1-S1-T2.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        branch_errors = [e for e in errors if "duplicate branch" in e]
        assert branch_errors == [], f"Expected no duplicate-branch errors but got: {branch_errors}"

    def test_check_8_non_task_files_ignored(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-3: Story/Feature files sharing a branch value → not flagged.

        Given: A Story row and a Feature row both declare the same **Branch:** value
        When: validate is called
        Then: No duplicate-branch error is emitted (only Task rows are checked)
        Spec: E19-F1-S1-T1 AC-3
        """
        story = backlog_dir / "E1-F1-S1.md"
        story.write_text(
            "# E1-F1-S1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Branch:** `feature/shared-non-task`\n\n## Comments\n",
            encoding="utf-8",
        )
        feature = backlog_dir / "E1-F1.md"
        feature.write_text(
            "# E1-F1\n\n## Status: in-queue\n\n## Target Repository\n\n"
            "- **Branch:** `feature/shared-non-task`\n\n## Comments\n",
            encoding="utf-8",
        )
        idx = self._make_index(
            tmp_path,
            "| E1-F1-S1 | Story 1 | Story | in-queue | none | repo | `backlog/E1-F1-S1.md` |\n"
            "| E1-F1 | Feature 1 | Feature | in-queue | none | repo | `backlog/E1-F1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        branch_errors = [e for e in errors if "duplicate branch" in e]
        assert branch_errors == [], (
            f"Non-Task rows must not be checked for branch uniqueness but got: {branch_errors}"
        )

    def test_check_8_task_without_branch_field_skipped(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: Task file with no **Branch:** field → not flagged.

        Given: A Task work-unit file contains no '**Branch:**' line
        When: validate is called
        Then: No error is emitted for that task (graceful skip)
        Spec: E19-F1-S1-T1 AC-4
        """
        t1 = backlog_dir / "E1-F1-S1-T1.md"
        t1.write_text(
            "# E1-F1-S1-T1\n\n## Status: in-queue\n\n## Comments\n",
            encoding="utf-8",
        )
        idx = self._make_index(
            tmp_path,
            "| E1-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E1-F1-S1-T1.md` |\n",
        )
        errors = BacklogManager().validate(idx, tmp_path)
        branch_errors = [e for e in errors if "duplicate branch" in e]
        assert branch_errors == [], (
            f"Task with no Branch field must not generate errors but got: {branch_errors}"
        )


# ---------------------------------------------------------------------------
# E4-F1-S1-T1: Rename BacklogManagerJudge → BacklogManager
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
        """AC-5: No BacklogManagerJudge references in the three changed source files."""
        import importlib.util
        from pathlib import Path

        old_name = "BacklogManagerJudge"
        spec = importlib.util.find_spec("devbench")
        assert spec is not None and spec.origin is not None
        src_root = Path(spec.origin).parent
        checked = [
            src_root / "backlog" / "manager.py",
            src_root / "cli.py",
            src_root / "execution" / "orchestrator.py",
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

    def test_orchestrator_imports_backlog_manager(self) -> None:
        """AC-4: orchestrator.py source contains BacklogManager import, not BacklogManagerJudge."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("devbench")
        assert spec is not None and spec.origin is not None
        src = (Path(spec.origin).parent / "execution" / "orchestrator.py").read_text(encoding="utf-8")
        assert "BacklogManager" in src, "orchestrator.py must import BacklogManager"
        assert "BacklogManagerJudge" not in src, "orchestrator.py must not reference BacklogManagerJudge"

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
        assert any("E0-F1-S1-T1" in e for e in errors), (
            f"error must mention the unit ID; got: {errors}"
        )

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
        assert mgr.logger.name == "devbench.backlog_manager", (
            f"default logger name wrong: {mgr.logger.name}"
        )

    def test_backlog_manager_has_no_evaluate_method(self) -> None:
        """BacklogManager must not expose evaluate() — judge interface removed."""
        assert not hasattr(BacklogManager, "evaluate"), (
            "BacklogManager must not have evaluate(); judge interface was intentionally removed"
        )


# ---------------------------------------------------------------------------
# E26-F1-S1-T1: _update_status_summary keeps Status Summary in sync
# ---------------------------------------------------------------------------


class TestUpdateStatusSummary:
    """AC tests for E26-F1-S1-T1: _set_status keeps Status Summary table in sync."""

    def _make_index_with_summary(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        *,
        e26_t1_status: str = "in-queue",
    ) -> tuple[Path, Path]:
        """Build a minimal BACKLOG.md with a Status Summary and a Full Work Unit Index.

        Returns (index_path, work_unit_file for E26-F1-S1-T1).

        Two tasks exist for E26-F1-S1 so that marking T1 done does NOT trigger
        rollup (T2 remains in-queue).  This keeps the test focused on the
        summary update logic rather than the rollup logic.
        """
        content = f"""\
# Backlog

## Status Summary

| Epic | Title | Features | Stories | Tasks | Total | Done | In Progress | In Review | In Queue |
|------|-------|----------|---------|-------|-------|------|-------------|-----------|----------|
| E26 | Backlog Status Summary Sync | 1 | 1 | 2 | 5 | 0 | 0 | 0 | 5 |
| **Total** | | **1** | **1** | **2** | **5** | **0** | **0** | **0** | **5** |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E26-F1-S1-T1 | Update Status Summary | Task | {e26_t1_status} | None | devbench | `backlog/E26-F1-S1-T1.md` |
| E26-F1-S1-T2 | Another Task | Task | in-queue | None | devbench | `backlog/E26-F1-S1-T2.md` |
| E26-F1-S1 | Update Summary Story | Story | in-queue | None | devbench | `backlog/E26-F1-S1.md` |
| E26-F1 | Sync Feature | Feature | in-queue | None | devbench | `backlog/E26-F1.md` |
| E26 | Backlog Status Summary Sync | Epic | in-queue | None | devbench | `backlog/E26.md` |
"""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(content, encoding="utf-8")

        wu_file = backlog_dir / "E26-F1-S1-T1.md"
        wu_file.write_text(
            f"# E26-F1-S1-T1\n\n## Status: {e26_t1_status}\n\n## Comments\n",
            encoding="utf-8",
        )
        for unit_id in ("E26-F1-S1-T2", "E26-F1-S1", "E26-F1", "E26"):
            (backlog_dir / f"{unit_id}.md").write_text(
                f"# {unit_id}\n\n## Status: in-queue\n\n## Comments\n",
                encoding="utf-8",
            )

        return index_path, wu_file

    def test_set_status_updates_status_summary_done_count(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: Calling _set_status updates Status Summary done/in-queue counts.

        Given: A BACKLOG.md with Status Summary showing E26 has 0 done, 4 in-queue
               and a Full Work Unit Index where E26-F1-S1-T1 is in-queue
        When: force_status sets E26-F1-S1-T1 to done
        Then: The Status Summary row for E26 shows 1 done and 3 in-queue
        Spec: AC-1
        """
        index_path, wu_file = self._make_index_with_summary(tmp_path, backlog_dir)

        manager = BacklogManager()
        manager.force_status(wu_file, index_path, "E26-F1-S1-T1", "done")

        content = index_path.read_text(encoding="utf-8")
        summary_lines = [
            ln for ln in content.splitlines()
            if "E26" in ln and "Backlog Status Summary Sync" in ln
            and "Epic" not in ln
        ]
        assert summary_lines, f"E26 Status Summary row not found in:\n{content}"
        summary_row = summary_lines[0]
        # Strip leading/trailing pipe and split; preserve all cells including empty ones
        cells = [c.strip() for c in summary_row.strip().strip("|").split("|")]
        # columns (0-indexed after stripping outer pipes):
        # Epic, Title, Features, Stories, Tasks, Total, Done, In Progress, In Review, In Queue
        done_idx = 6
        in_queue_idx = 9
        assert cells[done_idx] == "1", (
            f"Done count must be 1 after marking T1 done, got {cells[done_idx]!r} in row: {summary_row!r}"
        )
        assert cells[in_queue_idx] == "4", (
            f"In Queue count must be 4 after marking T1 done (T2+S1+F1+E26 remain), "
            f"got {cells[in_queue_idx]!r} in row: {summary_row!r}"
        )

    def test_set_status_updates_status_summary_total_row(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-2: The **Total** row in the Status Summary reflects aggregate counts after each write.

        Given: A BACKLOG.md with a **Total** row showing 0 done, 4 in-queue
        When: force_status sets E26-F1-S1-T1 to done
        Then: The **Total** row shows 1 done and 3 in-queue
        Spec: AC-2
        """
        index_path, wu_file = self._make_index_with_summary(tmp_path, backlog_dir)

        manager = BacklogManager()
        manager.force_status(wu_file, index_path, "E26-F1-S1-T1", "done")

        content = index_path.read_text(encoding="utf-8")
        total_lines = [
            ln for ln in content.splitlines()
            if "**Total**" in ln
        ]
        assert total_lines, f"**Total** row not found in Status Summary:\n{content}"
        total_row = total_lines[0]
        # Split preserving all cells including empty ones; strip leading/trailing pipes
        cells = [c.strip() for c in total_row.strip().strip("|").split("|")]
        # columns (0-indexed): Epic/Total, Title, Features, Stories, Tasks,
        # Total-count, Done, In Progress, In Review, In Queue
        done_idx = 6
        in_queue_idx = 9
        done_val = cells[done_idx].strip("*")
        in_queue_val = cells[in_queue_idx].strip("*")
        assert done_val == "1", (
            f"Total done count must be 1 after marking T1 done, got {done_val!r} in row: {total_row!r}"
        )
        assert in_queue_val == "4", (
            f"Total in-queue count must be 4 after marking T1 done (T2+S1+F1+E26 remain), "
            f"got {in_queue_val!r} in row: {total_row!r}"
        )

    def test_validate_passes_after_set_status(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-3: validate-backlog passes immediately after any status change via _set_status.

        Given: A BACKLOG.md with a consistent Status Summary and Full Work Unit Index
        When: force_status changes E26-F1-S1-T1 to in-progress
        Then: validate() returns no Status Summary count errors
        Spec: AC-3
        """
        index_path, wu_file = self._make_index_with_summary(tmp_path, backlog_dir)

        manager = BacklogManager()
        manager.force_status(wu_file, index_path, "E26-F1-S1-T1", "in-progress")

        errors = manager.validate(index_path, tmp_path)
        summary_errors = [e for e in errors if "Status Summary" in e]
        assert summary_errors == [], (
            f"Expected no Status Summary errors after set_status, got: {summary_errors}"
        )

    def test_set_status_still_writes_index_row(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: Existing behaviour of _set_status (work-unit file write, index row write) is unchanged.

        Given: A BACKLOG.md with E26-F1-S1-T1 in-queue
        When: force_status sets E26-F1-S1-T1 to in-progress
        Then: The work-unit file is updated to in-progress
              The Full Work Unit Index row is updated to in-progress
        Spec: AC-4
        """
        index_path, wu_file = self._make_index_with_summary(tmp_path, backlog_dir)

        manager = BacklogManager()
        manager.force_status(wu_file, index_path, "E26-F1-S1-T1", "in-progress")

        wu_content = wu_file.read_text(encoding="utf-8")
        assert "## Status: in-progress" in wu_content, (
            "Work-unit file must be updated to in-progress"
        )

        index_content = index_path.read_text(encoding="utf-8")
        index_rows = [
            ln for ln in index_content.splitlines()
            if "E26-F1-S1-T1" in ln and "Task" in ln
        ]
        assert index_rows, "E26-F1-S1-T1 Task row not found in index"
        assert "in-progress" in index_rows[0], (
            f"Index row must show in-progress, got: {index_rows[0]!r}"
        )


class TestHoldStatus:
    """Tests for the 'hold' status value.

    AC-1: 'hold' is a recognized status (no validation error).
    AC-2: 'hold' units are never returned by get_parallel_candidates().
    AC-4: 'hold' units are not treated as 'blocked' or 'in-queue'.
    """

    def test_hold_is_valid_status(self) -> None:
        """AC-1: 'hold' is a recognized status value in VALID_STATUSES.

        Given: The VALID_STATUSES mapping in constants
        When: 'hold' is looked up
        Then: It is present as a valid status key
        Spec: AC-1
        """
        assert "hold" in VALID_STATUSES

    def test_hold_constant_exists(self) -> None:
        """AC-1: STATUS_HOLD constant exists in constants module.

        Given: The constants module
        When: STATUS_HOLD is imported
        Then: Its value is 'hold'
        Spec: AC-1
        """
        from devbench.constants import STATUS_HOLD
        assert STATUS_HOLD == "hold"

    def test_validate_does_not_report_hold_as_unknown_status(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-1: validate() does not report 'hold' as an unknown status.

        Given: A BACKLOG.md with a unit whose status is 'hold'
              and a corresponding work-unit file with '## Status: hold'
        When: BacklogManager.validate() is called
        Then: No error about unknown/invalid status is returned for the unit
        Spec: AC-1
        """
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: hold\n\n## Comments\n")

        index_content = (
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            "| E0-F1-S1-T1 | Hold Task | Task | hold | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(index_content)

        manager = BacklogManager()
        errors = manager.validate(index_path, tmp_path)

        status_errors = [e for e in errors if "E0-F1-S1-T1" in e and "status" in e.lower()]
        assert not status_errors, f"Unexpected status errors for hold unit: {status_errors}"

    def test_hold_not_treated_as_blocked_or_in_queue(self) -> None:
        """AC-4: 'hold' is distinct from 'blocked' and 'in-queue' in VALID_STATUSES.

        Given: The VALID_STATUSES mapping
        When: The canonical form of 'hold' is inspected
        Then: It is 'hold', not 'blocked' and not 'in-queue'
        Spec: AC-4
        """
        assert VALID_STATUSES["hold"] == "hold"
        assert VALID_STATUSES["hold"] != "blocked"
        assert VALID_STATUSES["hold"] != "in-queue"

    def test_hold_excluded_from_dep_status_check(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: validate() does not flag a 'hold' unit for dep-status violations.

        Given: A BACKLOG.md where a 'hold' unit has an unsatisfied dependency
        When: BacklogManager.validate() is called
        Then: No dep-status error is reported for the 'hold' unit
        Spec: AC-4
        """
        dep_file = backlog_dir / "E0-F1-S1-T1.md"
        dep_file.write_text("# E0-F1-S1-T1\n\n## Status: in-queue\n\n## Comments\n")

        hold_file = backlog_dir / "E0-F1-S1-T2.md"
        hold_file.write_text("# E0-F1-S1-T2\n\n## Status: hold\n\n## Comments\n")

        index_content = (
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            "| E0-F1-S1-T1 | Dep Task | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Hold Task | Task | hold | E0-F1-S1-T1 | git-repo | `backlog/E0-F1-S1-T2.md` |\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(index_content)

        manager = BacklogManager()
        errors = manager.validate(index_path, tmp_path)

        dep_status_errors = [
            e for e in errors
            if "E0-F1-S1-T2" in e and "dep" in e.lower()
        ]
        assert not dep_status_errors, (
            f"'hold' unit must not be flagged for dep-status violations: {dep_status_errors}"
        )

    def test_force_status_accepts_hold(
        self, tmp_work_unit_file: Path, backlog_index_titlecase: Path
    ) -> None:
        """AC-3: force_status accepts 'hold' as a valid target status.

        Given: A work-unit file and a BACKLOG.md index
        When: force_status is called with 'hold'
        Then: The work-unit file is updated to '## Status: hold'
              No ValueError is raised
        Spec: AC-3
        """
        manager = BacklogManager()
        manager.force_status(tmp_work_unit_file, backlog_index_titlecase, "E0-F1-S1-T1", "hold")

        content = tmp_work_unit_file.read_text()
        assert "## Status: hold" in content
