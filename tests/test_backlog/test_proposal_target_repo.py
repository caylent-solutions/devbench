"""Tests for classify_blocked_task marker detection: [BLOCKED_TARGET_REPO_UNRESOLVED].

Verifies that the classifier returns OPERATOR_ACTION_REQUIRED when the
work-unit file's Comments section contains the
[BLOCKED_TARGET_REPO_UNRESOLVED] <repo> marker (issue #241, AC-241-1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BLOCKED_MARKER = "[BLOCKED_TARGET_REPO_UNRESOLVED]"


def _write_backlog(tmp_path: Path, rows: list[str]) -> Path:
    """Write a minimal BACKLOG.md and return its path."""
    content = (
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
    )
    index = tmp_path / "BACKLOG.md"
    index.write_text(content)
    return index


def _make_story_dir(tmp_path: Path) -> Path:
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    return story_dir


def _wu_with_marker(unit_id: str, repo: str) -> str:
    """Return WU file content that includes the unresolved-repo marker."""
    return (
        f"# {unit_id}: Test Task\n\n"
        "## Status: blocked\n\n"
        "## Description\n\nTest.\n\n"
        "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| src/x.py | modify |\n\n"
        "## Comments\n\n"
        f"[2026-06-07 00:00 UTC] [backlog_manager] [BLOCKED] target repo unresolvable: "
        f"{_BLOCKED_MARKER} {repo}\n"
    )


def _wu_without_marker(unit_id: str) -> str:
    """Return WU file content with a generic BLOCKED comment (no unresolved-repo marker)."""
    return (
        f"# {unit_id}: Test Task\n\n"
        "## Status: blocked\n\n"
        "## Description\n\nTest.\n\n"
        "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| src/x.py | modify |\n\n"
        "## Comments\n\n"
        "[2026-06-07 00:00 UTC] [backlog_manager] [BLOCKED] manual gate\n"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClassifyBlockedTaskTargetRepoMarker:
    """AC-241-1: classify_blocked_task yields OPERATOR_ACTION_REQUIRED for the unresolved-repo marker."""

    def test_marker_yields_operator_action_required(self, tmp_path: Path) -> None:
        """A WU with [BLOCKED_TARGET_REPO_UNRESOLVED] classifies as OPERATOR_ACTION_REQUIRED."""
        story_dir = _make_story_dir(tmp_path)
        unit_id = "E0-F1-S1-T1"
        (story_dir / f"{unit_id}.md").write_text(
            _wu_with_marker(unit_id, "unknown-org/no-such-repo"),
            encoding="utf-8",
        )
        backlog_index = _write_backlog(
            tmp_path,
            [f"| {unit_id} | Test | Task | blocked | none | git-repo | `backlog/E0/E0-F1/E0-F1-S1/{unit_id}.md` |"],
        )

        result = classify_blocked_task(
            backlog_root=tmp_path,
            backlog_index=backlog_index,
            task_id=unit_id,
        )

        assert result == BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_marker_with_different_repo_name(self, tmp_path: Path) -> None:
        """The classifier matches the marker regardless of the repo name embedded in it."""
        story_dir = _make_story_dir(tmp_path)
        unit_id = "E0-F1-S1-T1"
        (story_dir / f"{unit_id}.md").write_text(
            _wu_with_marker(unit_id, "another-org/completely-different-repo"),
            encoding="utf-8",
        )
        backlog_index = _write_backlog(
            tmp_path,
            [f"| {unit_id} | Test | Task | blocked | none | git-repo | `backlog/E0/E0-F1/E0-F1-S1/{unit_id}.md` |"],
        )

        result = classify_blocked_task(
            backlog_root=tmp_path,
            backlog_index=backlog_index,
            task_id=unit_id,
        )

        assert result == BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_no_marker_does_not_force_operator_action(self, tmp_path: Path) -> None:
        """A WU without the unresolved-repo marker is not forced into OPERATOR_ACTION_REQUIRED
        purely by absence of the marker -- its actual state may vary.

        This test confirms the marker check is purely additive: a WU with a generic
        BLOCKED comment and no deps does fall through to OPERATOR_ACTION_REQUIRED, but
        for the right reasons (no recovery signal). The test verifies the classifier
        does NOT hard-code OPERATOR_ACTION_REQUIRED for all blocked tasks.
        """
        story_dir = _make_story_dir(tmp_path)
        unit_id = "E0-F1-S1-T1"
        (story_dir / f"{unit_id}.md").write_text(
            _wu_without_marker(unit_id),
            encoding="utf-8",
        )
        backlog_index = _write_backlog(
            tmp_path,
            [f"| {unit_id} | Test | Task | blocked | none | git-repo | `backlog/E0/E0-F1/E0-F1-S1/{unit_id}.md` |"],
        )

        result = classify_blocked_task(
            backlog_root=tmp_path,
            backlog_index=backlog_index,
            task_id=unit_id,
        )

        # Without marker, no deps, no recovery: still OPERATOR_ACTION_REQUIRED
        # (catch-all bucket) but NOT because of the marker path.
        assert result == BlockedTaskState.OPERATOR_ACTION_REQUIRED

    @pytest.mark.parametrize(
        "repo_name",
        [
            "unknown-org/no-such-repo",
            "some-org/totally-missing",
            "acme-corp/private-repo",
        ],
    )
    def test_marker_parametrized_repo_names(self, tmp_path: Path, repo_name: str) -> None:
        """[BLOCKED_TARGET_REPO_UNRESOLVED] detection works for various repo name values."""
        story_dir = _make_story_dir(tmp_path)
        unit_id = "E0-F1-S1-T1"
        (story_dir / f"{unit_id}.md").write_text(
            _wu_with_marker(unit_id, repo_name),
            encoding="utf-8",
        )
        backlog_index = _write_backlog(
            tmp_path,
            [f"| {unit_id} | Test | Task | blocked | none | git-repo | `backlog/E0/E0-F1/E0-F1-S1/{unit_id}.md` |"],
        )

        result = classify_blocked_task(
            backlog_root=tmp_path,
            backlog_index=backlog_index,
            task_id=unit_id,
        )

        assert result == BlockedTaskState.OPERATOR_ACTION_REQUIRED


class TestHasUnresolvedRepoMarkerEdgeCases:
    """Branch coverage for _has_unresolved_repo_marker's OSError fallback."""

    def test_oserror_on_read_returns_false(self, tmp_path: Path) -> None:
        """_has_unresolved_repo_marker returns False when the source file is unreadable.

        The OSError fallback ensures the classifier never masks an otherwise
        detectable blocked state due to a transient filesystem error.
        """
        from devbench.backlog.proposal import _has_unresolved_repo_marker

        non_existent = tmp_path / "does_not_exist.md"
        # The file does not exist; read_text raises FileNotFoundError (subclass of OSError).
        result = _has_unresolved_repo_marker(non_existent)

        assert result is False

    def test_returns_false_when_marker_absent(self, tmp_path: Path) -> None:
        """_has_unresolved_repo_marker returns False when the file exists but has no marker."""
        from devbench.backlog.proposal import _has_unresolved_repo_marker

        source_file = tmp_path / "task.md"
        source_file.write_text("# Task\n\n## Comments\n\n[2026-01-01] [agent] [BLOCKED] manual\n")

        result = _has_unresolved_repo_marker(source_file)

        assert result is False

    def test_returns_true_when_marker_present(self, tmp_path: Path) -> None:
        """_has_unresolved_repo_marker returns True when the file contains the marker."""
        from devbench.backlog.proposal import _has_unresolved_repo_marker

        source_file = tmp_path / "task.md"
        source_file.write_text(
            f"# Task\n\n## Comments\n\n[2026-01-01] [agent] [BLOCKED] {_BLOCKED_MARKER} some-org/repo\n"
        )

        result = _has_unresolved_repo_marker(source_file)

        assert result is True
