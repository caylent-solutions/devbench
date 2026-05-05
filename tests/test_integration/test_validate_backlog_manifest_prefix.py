"""Functional tests for the validate-backlog ``checkout_directory`` path-prefix check.

Exercises the full ``cmd_validate_backlog`` CLI entry point against real
workspaces on disk. Complements the unit-level coverage in
``tests/test_backlog/test_manager.py::TestValidateManifestPathPrefix`` by
pinning the CLI exit code + stdout / stderr contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.config_loader import RepoConfig, RuntimeConfig

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 0 | 1 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | in-queue | none | example-org/example-repo | `backlog/EX-F1-S1-T1.md` |
"""


def _make_work_unit(backlog_dir: Path, manifest_path: str) -> Path:
    wu = backlog_dir / "EX-F1-S1-T1.md"
    wu.write_text(
        "# EX-F1-S1-T1\n\n"
        "## Status: in-queue\n\n"
        "## Target Repository\n\n"
        "- **Repo:** `example-org/example-repo`\n\n"
        "## Description\n\nFunctional-test task.\n\n"
        "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
        "## Changes Manifest\n\n"
        "| File | Change |\n"
        "|------|--------|\n"
        f"| `{manifest_path}` | update |\n\n"
        "## Definition of Done\n\n- [ ] Done\n",
        encoding="utf-8",
    )
    return wu


def _build_workspace(tmp_path: Path, manifest_path: str) -> Path:
    (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE, encoding="utf-8")
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    _make_work_unit(backlog_dir, manifest_path)
    return tmp_path


@pytest.mark.functional
class TestValidateBacklogManifestPrefixFunctional:
    """End-to-end: invoke cli.cmd_validate_backlog against a real workspace."""

    def test_cli_validate_backlog_exits_nonzero_on_prefix_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A workspace whose only WU has a checkout_directory-prefixed manifest path
        must cause cmd_validate_backlog to exit non-zero with a message naming the
        offending path and the prefix."""
        workspace = _build_workspace(tmp_path, manifest_path="example-repo/README.md")
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        with (
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.config.RUNTIME_CONFIG", rt_cfg),
        ):
            rc = cli.cmd_validate_backlog()

        assert rc == 1
        captured = capsys.readouterr()
        assert "FAILED" in captured.out
        assert "EX-F1-S1-T1" in captured.out
        assert "'example-repo/README.md'" in captured.out
        assert "'example-repo/'" in captured.out
        assert "docs/backlog-contract.md" in captured.out

    def test_cli_validate_backlog_exits_zero_on_correct_paths(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Same workspace with a repo-relative manifest path: cmd_validate_backlog
        exits zero and prints the standard pass message."""
        workspace = _build_workspace(tmp_path, manifest_path="README.md")
        rt_cfg = RuntimeConfig(repos={"example-org/example-repo": RepoConfig(checkout_directory="example-repo")})
        with (
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.config.RUNTIME_CONFIG", rt_cfg),
        ):
            rc = cli.cmd_validate_backlog()

        assert rc == 0
        captured = capsys.readouterr()
        assert "passed" in captured.out.lower()
