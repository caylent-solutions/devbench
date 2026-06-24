"""CLI-layer integration tests for cmd_validate_backlog --strict / --include-draft.

Verifies that:
- Default run on an all-draft conflict fixture prints a WARNING line and
  returns rc 0 (no hard error).
- validate-backlog --strict returns non-zero with an ERROR line on the
  same fixture.
- validate-backlog --include-draft (alias) behaves identically to --strict.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.constants import BACKLOG_SUBDIR

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

_TASK_TEMPLATE = """\
# {unit_id}

## Status: {status}

## Target Repository

- **Repo:** `{repo}`

## Description

Test task.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001

## Changes Manifest

| File | Change |
|------|--------|
| `{manifest_path}` | new |

## Definition of Done

- [ ] Done
"""

_TEST_REPO = "caylent-solutions/devbench"

_TEST_MANIFEST_PATH = "docs/config-reference.md"


def _build_draft_conflict_backlog(tmp_path: Path) -> Path:
    """Create a backlog where two draft tasks own the same (repo, path)."""
    backlog_dir = tmp_path / BACKLOG_SUBDIR
    backlog_dir.mkdir(exist_ok=True)

    for unit_id in ("E9-F1-S1-T1", "E9-F1-S1-T2"):
        task_file = backlog_dir / f"{unit_id}.md"
        task_file.write_text(
            _TASK_TEMPLATE.format(
                unit_id=unit_id,
                status="draft",
                repo=_TEST_REPO,
                manifest_path=_TEST_MANIFEST_PATH,
            ),
            encoding="utf-8",
        )

    index_rows = (
        f"| E9-F1-S1-T1 | T1 | Task | draft | none | {_TEST_REPO} | `{BACKLOG_SUBDIR}/E9-F1-S1-T1.md` |\n"
        f"| E9-F1-S1-T2 | T2 | Task | draft | none | {_TEST_REPO} | `{BACKLOG_SUBDIR}/E9-F1-S1-T2.md` |\n"
    )
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(_INDEX_HEADER + index_rows, encoding="utf-8")
    return index_path


@pytest.mark.unit
class TestValidateBacklogStrictFlag:
    """cmd_validate_backlog --strict / --include-draft controls draft conflict severity."""

    def test_default_run_on_draft_conflict_returns_rc0_with_warning(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Default (no strict flag): draft conflict emits WARNING and returns 0."""
        index_path = _build_draft_conflict_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_validate_backlog()
        assert rc == 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "WARNING" in combined
        assert "draft/hold conflict" in combined
        assert _TEST_MANIFEST_PATH in combined

    @pytest.mark.parametrize("flag", ["--strict", "--include-draft"])
    def test_strict_flag_on_draft_conflict_returns_nonzero_with_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        flag: str,
    ) -> None:
        """--strict / --include-draft: draft conflict escalates to ERROR and rc != 0."""
        index_path = _build_draft_conflict_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_validate_backlog(flag)
        assert rc != 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "ERROR" in combined
        assert "draft/hold conflict" in combined
        assert _TEST_MANIFEST_PATH in combined

    def test_inflight_conflict_always_errors_regardless_of_strict(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """in-queue conflict always produces ERROR even without --strict."""
        backlog_dir = tmp_path / BACKLOG_SUBDIR
        backlog_dir.mkdir(exist_ok=True)

        for unit_id in ("E9-F2-S1-T1", "E9-F2-S1-T2"):
            task_file = backlog_dir / f"{unit_id}.md"
            task_file.write_text(
                _TASK_TEMPLATE.format(
                    unit_id=unit_id,
                    status="in-queue",
                    repo=_TEST_REPO,
                    manifest_path=_TEST_MANIFEST_PATH,
                ),
                encoding="utf-8",
            )

        index_rows = (
            f"| E9-F2-S1-T1 | T1 | Task | in-queue | none | {_TEST_REPO} | `{BACKLOG_SUBDIR}/E9-F2-S1-T1.md` |\n"
            f"| E9-F2-S1-T2 | T2 | Task | in-queue | none | {_TEST_REPO} | `{BACKLOG_SUBDIR}/E9-F2-S1-T2.md` |\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(_INDEX_HEADER + index_rows, encoding="utf-8")

        with (
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_validate_backlog()
        assert rc != 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "ERROR" in combined
        assert "Manifest conflict" in combined
