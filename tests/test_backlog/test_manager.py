"""Tests for judges.backlog_manager module."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager
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


def _all_five_judges_pass_block() -> str:
    """Return comment lines for all five required judges (4 review_team + security_review) passing."""
    return "\n".join(
        _judge_comment(j, "REVIEW_PASS") for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
    ) + "\n"


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
            # Round 2 — all five judges pass
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
        comments = "\n".join([
            _judge_comment("code_review", "REVIEW_PASS"),
            _judge_comment("test_review", "REVIEW_PASS"),
            _judge_comment("doc_review", "REVIEW_PASS"),
            _judge_comment("changes_manifest", "REVIEW_PASS"),
            # security_review deliberately absent
        ]) + "\n"
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
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            + rows,
            encoding="utf-8",
        )
        return idx

    def _make_wu(self, backlog_dir: Path, unit_id: str, status: str = "in-queue") -> Path:
        wu = backlog_dir / f"{unit_id}.md"
        wu.write_text(f"# {unit_id}\n\n## Status: {status}\n", encoding="utf-8")
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
    """Tests for BacklogManager._update_status_summary() — AC-2."""

    def test_method_exists(self) -> None:
        """AC-2: _update_status_summary method must exist on BacklogManager."""
        assert hasattr(BacklogManager, "_update_status_summary"), (
            "BacklogManager must have _update_status_summary method"
        )
        assert callable(BacklogManager._update_status_summary)

    def test_rewrites_summary_table_when_already_present(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
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

    def test_creates_summary_section_when_absent(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
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

    def test_counts_only_descendant_rows_not_epic_itself(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
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
    """Tests that _set_status calls _update_status_summary — AC-3."""

    def test_set_status_updates_summary_table(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
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
    """Tests for validate-backlog Status Summary checks — AC-4."""

    def _make_backlog_no_summary(self, tmp_path: Path, backlog_dir: Path) -> tuple[Path, dict[str, Path]]:
        """Return an index without a Status Summary section."""
        return _make_backlog_with_epics(tmp_path, backlog_dir)

    def _make_backlog_with_correct_summary(
        self, tmp_path: Path, backlog_dir: Path
    ) -> tuple[Path, dict[str, Path]]:
        """Return an index with a correct Status Summary section."""
        index_path, files = _make_backlog_with_epics(tmp_path, backlog_dir)
        mgr = BacklogManager()
        mgr._update_status_summary(index_path)
        return index_path, files

    def _make_backlog_with_wrong_summary(
        self, tmp_path: Path, backlog_dir: Path
    ) -> tuple[Path, dict[str, Path]]:
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

    def test_validate_fails_when_summary_table_missing(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: validate reports an error when ## Status Summary section is absent."""
        index_path, _ = self._make_backlog_no_summary(tmp_path, backlog_dir)
        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)
        assert any("status summary" in e.lower() for e in errors), (
            f"Expected status summary error, got: {errors}"
        )

    def test_validate_fails_when_summary_counts_mismatch(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: validate reports an error when summary counts don't match index."""
        index_path, _ = self._make_backlog_with_wrong_summary(tmp_path, backlog_dir)
        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)
        assert any("status summary" in e.lower() or "mismatch" in e.lower() for e in errors), (
            f"Expected status summary mismatch error, got: {errors}"
        )

    def test_validate_passes_when_summary_matches_index(
        self, tmp_path: Path, backlog_dir: Path
    ) -> None:
        """AC-4: validate returns no summary errors when counts are correct."""
        index_path, _ = self._make_backlog_with_correct_summary(tmp_path, backlog_dir)
        mgr = BacklogManager()
        errors = mgr.validate(index_path, tmp_path)
        summary_errors = [e for e in errors if "status summary" in e.lower()]
        assert not summary_errors, (
            f"Unexpected status summary errors: {summary_errors}"
        )


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
        assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\]", content), (
            f"No UTC timestamp found in: {content}"
        )
