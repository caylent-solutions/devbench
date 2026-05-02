"""Tests for judges.backlog_manager module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig
from devbench.constants import ALL_REQUIRED_JUDGE_NAMES, REVIEW_JUDGE_NAMES, SECURITY_JUDGE_NAMES, VALID_STATUSES


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
        bad_file.write_text("# No status here\nJust content.\n" + _ALL_JUDGES_PASSED_COMMENTS)

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
        wu = self._make_wu(tmp_path, _all_five_judges_pass_block())
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
        content = (
            f"# {unit_id}\n\n"
            f"## Status: {status}\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nTest work unit.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Placeholder\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
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
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
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


def _unit_body(unit_id: str, status: str, *, deps: list[str] | None = None, comments: str = "") -> str:
    """Build a minimal well-formed work-unit file body for the scan tests.

    Produces the sections the parser and scan require (``Status``, ``Dependencies``,
    ``Comments``) plus the content sections ``validate-backlog`` demands for
    task-level files (``Description``, ``Acceptance Criteria``, ``Changes Manifest``,
    ``Definition of Done``). Keeps every fixture self-contained so a test
    failure points at the scan logic rather than a malformed fixture.
    """
    dep_rows = "| none | | |" if not deps else "\n".join(f"| {d} | (auto) | proposed |" for d in deps)
    return (
        f"# {unit_id}: Test Task\n\n"
        f"## Status: {status}\n\n"
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
        dep_file = _unit_body("E0-F1-S1-T2", "in-review", comments=review_passes + "\n")
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
        dep_file = _unit_body("E0-F1-S1-T2", "in-review", comments=review_passes + "\n")

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
    ) -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(
            f"# {unit_id}\n\n"
            f"## Status: {status}\n\n"
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
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(index, errors)
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
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(index, errors)
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
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(index, errors)
        assert errors == []

    def test_self_dep_reported(self, tmp_path: Path) -> None:
        """A 1-node cycle (self-dep) is also caught."""
        index = self._index_with_deps(tmp_path, {"E0-F1-S1-T1": "E0-F1-S1-T1"})
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(index, errors)
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
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(index, errors)
        cycle_errors = [e for e in errors if "cycle" in e]
        assert len(cycle_errors) == 2

    def test_missing_index_no_crash(self, tmp_path: Path) -> None:
        """The cycle check on a missing BACKLOG.md returns silently."""
        errors: list[str] = []
        BacklogManager()._check_dep_cycles(tmp_path / "missing.md", errors)
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
