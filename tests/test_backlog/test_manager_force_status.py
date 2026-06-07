"""Tests for force_status done-refusal (E8-F1-S1-T1).

Verifies that force_status raises ValueError with the exact prescribed
message when the resolved status is 'done', so that the only writers of
done are mark_done (gated) and _rollup_parent_status (structural).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.constants import STATUS_DONE

# ---------------------------------------------------------------------------
# Minimal fixture helpers
# ---------------------------------------------------------------------------

_INDEX_HEADER = (
    "# Backlog\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|-----|-------|------|--------|-------------|------|-----------|\n"
)


def _make_index(tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
    """Return a BACKLOG.md with a single row for unit_id."""
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        _INDEX_HEADER + f"| {unit_id} | Test Task | Task | {status} | None | git-repo | `backlog/{unit_id}.md` |\n"
    )
    return index


def _make_wu(tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
    """Return a minimal work-unit .md file."""
    wu = tmp_path / f"{unit_id}.md"
    wu.write_text(f"# {unit_id}: Test Task\n\n## Status: {status}\n\n## Comments\n\n")
    return wu


# ---------------------------------------------------------------------------
# AC-H1-1: force_status raises the verbatim ValueError on done
# ---------------------------------------------------------------------------


_VERBATIM_FORCE_STATUS_ERROR = "force_status must not write 'done'; use mark_done (done-gate enforced)"
# Regex-safe fragment for pytest.raises(match=...) -- parentheses are re-escaped.
_FORCE_STATUS_ERROR_MATCH = r"force_status must not write 'done'; use mark_done \(done-gate enforced\)"


@pytest.mark.unit
class TestForceStatusRefusesDone:
    """force_status raises ValueError when the resolved status is done."""

    def test_raises_on_done_literal(self, tmp_path: Path) -> None:
        """Passing 'done' raises ValueError with the verbatim message."""
        wu = _make_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_index(tmp_path, "E0-F1-S1-T1")

        mgr = BacklogManager()
        with pytest.raises(ValueError, match=_FORCE_STATUS_ERROR_MATCH):
            mgr.force_status(wu, index, "E0-F1-S1-T1", "done")

    def test_raises_on_status_done_constant(self, tmp_path: Path) -> None:
        """Passing STATUS_DONE raises ValueError -- constant is the same value."""
        wu = _make_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_index(tmp_path, "E0-F1-S1-T1")

        mgr = BacklogManager()
        with pytest.raises(ValueError, match=_FORCE_STATUS_ERROR_MATCH):
            mgr.force_status(wu, index, "E0-F1-S1-T1", STATUS_DONE)

    def test_raises_on_done_titlecase(self, tmp_path: Path) -> None:
        """Title-case 'Done' resolves to done and is also refused."""
        wu = _make_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_index(tmp_path, "E0-F1-S1-T1")

        mgr = BacklogManager()
        with pytest.raises(ValueError, match=_FORCE_STATUS_ERROR_MATCH):
            mgr.force_status(wu, index, "E0-F1-S1-T1", "Done")

    def test_no_file_written_on_done(self, tmp_path: Path) -> None:
        """When force_status raises on done, neither file is modified."""
        original_wu = "# E0-F1-S1-T1: Test Task\n\n## Status: in-queue\n\n## Comments\n\n"
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(original_wu)
        index = _make_index(tmp_path, "E0-F1-S1-T1")
        original_index = index.read_text()

        mgr = BacklogManager()
        with pytest.raises(ValueError):
            mgr.force_status(wu, index, "E0-F1-S1-T1", "done")

        assert wu.read_text() == original_wu
        assert index.read_text() == original_index

    @pytest.mark.parametrize(
        "non_done_status",
        ["in-queue", "in-progress", "in-review", "blocked", "declined"],
    )
    def test_non_done_statuses_are_still_accepted(self, tmp_path: Path, non_done_status: str) -> None:
        """force_status must still accept all non-done statuses without raising."""
        wu = _make_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_index(tmp_path, "E0-F1-S1-T1")

        mgr = BacklogManager()
        # Must not raise
        mgr.force_status(wu, index, "E0-F1-S1-T1", non_done_status)
        assert f"## Status: {non_done_status}" in wu.read_text()
