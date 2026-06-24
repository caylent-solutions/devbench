"""CLI-layer tests for cmd_reconcile_backlog_md (issue #243 AC-243-1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli

_FULL_INDEX_HEADER = (
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
)

_CORRECT_SUMMARY = (
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
    "|------|-------|------|-------------|----------|---------|----------|-------|\n"
    "| E1 | Example Epic | 0 | 0 | 1 | 0 | 0 | 0 |\n"
    "\n"
)

_DRIFT_SUMMARY = (
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
    "|------|-------|------|-------------|----------|---------|----------|-------|\n"
    "| E1 | Example Epic | 0 | 0 | 0 | 0 | 0 | 0 |\n"
    "\n"
)

_INDEX_ROWS = (
    "| E1 | Example Epic | Epic | in-queue | None | example/repo | `backlog/E1.md` |\n"
    "| E1-F1-S1-T1 | Task One | Task | in-queue | None | example/repo | `backlog/E1-F1-S1-T1.md` |\n"
)


def _build_backlog(tmp_path: Path, *, with_drift: bool) -> Path:
    summary = _DRIFT_SUMMARY if with_drift else _CORRECT_SUMMARY
    content = "# Backlog\n\n" + summary + "## Full Work Unit Index\n\n" + _FULL_INDEX_HEADER + _INDEX_ROWS
    path = tmp_path / "BACKLOG.md"
    path.write_text(content, encoding="utf-8")
    return path


def _build_backlog_no_index_section(tmp_path: Path) -> Path:
    """Build a BACKLOG.md that has no '## Full Work Unit Index' section."""
    content = "# Backlog\n\n" + _DRIFT_SUMMARY
    path = tmp_path / "BACKLOG.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.unit
class TestMutuallyExclusiveFlags:
    """--check-only and --force together must error with rc 2."""

    def test_check_only_and_force_together_returns_rc2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--check-only --force together: ERROR message on stderr and rc 2."""
        _build_backlog(tmp_path, with_drift=False)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md("--check-only", "--force")
        assert rc == 2
        captured = capsys.readouterr()
        assert "--check-only and --force are mutually exclusive" in captured.err

    def test_force_and_check_only_order_irrelevant(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--force --check-only (reversed order) also errors with rc 2."""
        _build_backlog(tmp_path, with_drift=False)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md("--force", "--check-only")
        assert rc == 2
        captured = capsys.readouterr()
        assert "--check-only and --force are mutually exclusive" in captured.err


@pytest.mark.unit
class TestNoFlagMode:
    """No flag: prints a mismatch report and returns rc 0 (no write)."""

    def test_no_drift_prints_ok_and_returns_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """No drift: prints ok message and returns 0."""
        backlog_md = _build_backlog(tmp_path, with_drift=False)
        mtime_before = backlog_md.stat().st_mtime_ns
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md()
        assert rc == 0
        assert backlog_md.stat().st_mtime_ns == mtime_before
        captured = capsys.readouterr()
        assert "consistent" in captured.out.lower()

    def test_drift_prints_mismatch_report_and_returns_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Drift: prints mismatch details and returns 0 (no write)."""
        backlog_md = _build_backlog(tmp_path, with_drift=True)
        mtime_before = backlog_md.stat().st_mtime_ns
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md()
        assert rc == 0
        captured = capsys.readouterr()
        assert "drift" in captured.out.lower() or "mismatch" in captured.out.lower()
        assert backlog_md.stat().st_mtime_ns == mtime_before


@pytest.mark.unit
class TestCheckOnlyMode:
    """--check-only: returns rc 1 on drift, rc 0 on no drift, no writes."""

    def test_drift_returns_rc1_no_write(self, tmp_path: Path) -> None:
        """Drift detected: returns 1 and does not write."""
        backlog_md = _build_backlog(tmp_path, with_drift=True)
        mtime_before = backlog_md.stat().st_mtime_ns
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md("--check-only")
        assert rc == 1
        assert backlog_md.stat().st_mtime_ns == mtime_before

    def test_no_drift_returns_rc0(self, tmp_path: Path) -> None:
        """No drift: returns 0."""
        _build_backlog(tmp_path, with_drift=False)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md("--check-only")
        assert rc == 0


@pytest.mark.unit
class TestForceMode:
    """--force: rewrites the index region atomically and returns rc 0."""

    def test_force_rewrites_and_returns_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--force on drifted backlog rewrites and returns 0."""
        backlog_md = _build_backlog(tmp_path, with_drift=True)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md("--force")
        assert rc == 0
        updated = backlog_md.read_text(encoding="utf-8")
        assert "| E1 | Example Epic | 0 | 0 | 1 | 0 | 0 | 0 |" in updated

    def test_force_no_drift_returns_0(self, tmp_path: Path) -> None:
        """--force on already-correct backlog returns 0."""
        _build_backlog(tmp_path, with_drift=False)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md("--force")
        assert rc == 0

    def test_force_write_error_prints_error_and_returns_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--force: when the atomic rewrite fails, prints an error to stderr and returns 2."""
        _build_backlog(tmp_path, with_drift=True)
        tmp_path.chmod(0o555)
        try:
            with (
                patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
                patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            ):
                rc = cli.cmd_reconcile_backlog_md("--force")
        finally:
            tmp_path.chmod(0o755)
        assert rc == 2
        captured = capsys.readouterr()
        assert "ERROR" in captured.err
        assert "BACKLOG.md" in captured.err


@pytest.mark.unit
class TestNoFlagModeNoIndexSection:
    """No-flag mode when BACKLOG.md has no '## Full Work Unit Index' section."""

    def test_no_index_section_prints_drift_and_returns_0(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No '## Full Work Unit Index': drift is detected; prints report and returns 0 (no write)."""
        backlog_md = _build_backlog_no_index_section(tmp_path)
        mtime_before = backlog_md.stat().st_mtime_ns
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reconcile_backlog_md()
        assert rc == 0
        assert backlog_md.stat().st_mtime_ns == mtime_before
        captured = capsys.readouterr()
        assert "drift" in captured.out.lower() or "consistent" in captured.out.lower()
