"""Unit tests for BacklogManager.reconcile_backlog_md (issue #243 AC-243-1, AC-243a-1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.constants import STATUS_SUMMARY_TABLE_HEADER

_FULL_INDEX_HEADER = (
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
)

_DRIFT_SUMMARY = (
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
    "|------|-------|------|-------------|----------|---------|----------|-------|\n"
    "| E1 | Example Epic | 0 | 0 | 0 | 0 | 0 | 0 |\n"
    "\n"
)


def _build_backlog_md(tmp_path: Path, *, with_drift: bool = False) -> Path:
    """Build a minimal BACKLOG.md with one E1 epic row.

    When ``with_drift`` is True the Status Summary contains stale counts
    (all zeros) instead of the correct counts derived from the index rows.
    """
    index_rows = (
        "| E1 | Example Epic | Epic | in-queue | None | example/repo | `backlog/E1.md` |\n"
        "| E1-F1-S1-T1 | Task One | Task | in-queue | None | example/repo | `backlog/E1-F1-S1-T1.md` |\n"
    )
    correct_summary = (
        "## Status Summary\n\n" + STATUS_SUMMARY_TABLE_HEADER + "| E1 | Example Epic | 0 | 0 | 1 | 0 | 0 | 0 |\n" + "\n"
    )
    summary = _DRIFT_SUMMARY if with_drift else correct_summary
    content = "# Backlog\n\n" + summary + "## Full Work Unit Index\n\n" + _FULL_INDEX_HEADER + index_rows
    path = tmp_path / "BACKLOG.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.unit
class TestReconcileReturnCodes:
    """reconcile_backlog_md returns 0 (ok), 1 (drift), 2 (write-error)."""

    def test_returns_0_when_no_drift(self, tmp_path: Path) -> None:
        """Return 0 when the Status Summary matches the index."""
        _build_backlog_md(tmp_path, with_drift=False)
        mgr = BacklogManager()
        rc, _content = mgr.reconcile_backlog_md(tmp_path, force=False, check_only=False)
        assert rc == 0

    def test_returns_1_on_drift_check_only(self, tmp_path: Path) -> None:
        """Return 1 when drift is detected with check_only=True (no write)."""
        backlog_md = _build_backlog_md(tmp_path, with_drift=True)
        mtime_before = backlog_md.stat().st_mtime_ns
        mgr = BacklogManager()
        rc, _content = mgr.reconcile_backlog_md(tmp_path, force=False, check_only=True)
        assert rc == 1
        assert backlog_md.stat().st_mtime_ns == mtime_before

    def test_returns_0_on_no_drift_check_only(self, tmp_path: Path) -> None:
        """Return 0 when no drift is detected with check_only=True."""
        _build_backlog_md(tmp_path, with_drift=False)
        mgr = BacklogManager()
        rc, _content = mgr.reconcile_backlog_md(tmp_path, force=False, check_only=True)
        assert rc == 0

    def test_returns_2_on_write_error(self, tmp_path: Path) -> None:
        """Return 2 when the atomic rewrite fails (write error)."""
        _build_backlog_md(tmp_path, with_drift=True)
        tmp_path.chmod(0o555)
        try:
            mgr = BacklogManager()
            rc, _content = mgr.reconcile_backlog_md(tmp_path, force=True, check_only=False)
            assert rc == 2
        finally:
            tmp_path.chmod(0o755)

    def test_force_rewrites_index_region_atomically(self, tmp_path: Path) -> None:
        """--force rewrites the Status Summary region atomically and returns 0."""
        backlog_md = _build_backlog_md(tmp_path, with_drift=True)
        mgr = BacklogManager()
        rc, _content = mgr.reconcile_backlog_md(tmp_path, force=True, check_only=False)
        assert rc == 0
        updated = backlog_md.read_text(encoding="utf-8")
        assert "| E1 | Example Epic | 0 | 0 | 1 | 0 | 0 | 0 |" in updated

    def test_force_preserves_full_index(self, tmp_path: Path) -> None:
        """--force must not modify the Full Work Unit Index rows."""
        backlog_md = _build_backlog_md(tmp_path, with_drift=True)
        mgr = BacklogManager()
        mgr.reconcile_backlog_md(tmp_path, force=True, check_only=False)
        updated = backlog_md.read_text(encoding="utf-8")
        assert "| E1-F1-S1-T1 | Task One | Task | in-queue |" in updated

    def test_no_flag_returns_0_no_write_when_drift_present(self, tmp_path: Path) -> None:
        """No flag: returns 0 even when drift is present (report only, no write)."""
        backlog_md = _build_backlog_md(tmp_path, with_drift=True)
        mtime_before = backlog_md.stat().st_mtime_ns
        mgr = BacklogManager()
        rc, _content = mgr.reconcile_backlog_md(tmp_path, force=False, check_only=False)
        assert rc == 0
        assert backlog_md.stat().st_mtime_ns == mtime_before

    def test_raises_on_force_and_check_only_together(self, tmp_path: Path) -> None:
        """Passing both force=True and check_only=True raises ValueError."""
        _build_backlog_md(tmp_path, with_drift=False)
        mgr = BacklogManager()
        with pytest.raises(ValueError, match="mutually exclusive"):
            mgr.reconcile_backlog_md(tmp_path, force=True, check_only=True)


@pytest.mark.unit
class TestIndexRegionReuse:
    """reconcile_backlog_md reuses region detection and touches only the Status Summary."""

    def test_force_leaves_content_outside_summary_unchanged(self, tmp_path: Path) -> None:
        """Content before the Status Summary and the Full Work Unit Index are untouched."""
        backlog_md = _build_backlog_md(tmp_path, with_drift=True)
        original_lines = backlog_md.read_text(encoding="utf-8").splitlines()
        full_index_line = next(line for line in original_lines if line.startswith("## Full Work Unit Index"))
        mgr = BacklogManager()
        mgr.reconcile_backlog_md(tmp_path, force=True, check_only=False)
        updated = backlog_md.read_text(encoding="utf-8")
        assert full_index_line in updated

    def test_force_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        """Atomic rewrite: no .tmp file remains after a successful write."""
        _build_backlog_md(tmp_path, with_drift=True)
        mgr = BacklogManager()
        mgr.reconcile_backlog_md(tmp_path, force=True, check_only=False)
        assert not (tmp_path / "BACKLOG.md.tmp").exists()

    @pytest.mark.parametrize("with_drift", [True, False])
    def test_check_only_never_writes(self, tmp_path: Path, with_drift: bool) -> None:
        """check_only=True never modifies BACKLOG.md regardless of drift state."""
        backlog_md = _build_backlog_md(tmp_path, with_drift=with_drift)
        content_before = backlog_md.read_text(encoding="utf-8")
        mgr = BacklogManager()
        mgr.reconcile_backlog_md(tmp_path, force=False, check_only=True)
        assert backlog_md.read_text(encoding="utf-8") == content_before

    def test_no_full_index_section_appends_summary(self, tmp_path: Path) -> None:
        """When BACKLOG.md has no '## Full Work Unit Index', the reconciled content appends the summary."""
        content = "# Backlog\n\n## Status Summary\n\n(stale content)\n"
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(content, encoding="utf-8")
        mgr = BacklogManager()
        rc, reconciled = mgr.reconcile_backlog_md(tmp_path, force=False, check_only=False)
        assert rc == 0
        assert "## Status Summary" in reconciled
        assert reconciled != content
