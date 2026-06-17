"""TDI-001 validator gate: reject a Task that depends on one of its own ancestors.

A Task whose ``## Dependencies`` table lists one of its own ancestor containers
(``E<e>`` epic, ``E<e>-F<f>`` feature, or ``E<e>-F<f>-S<s>`` story) is always
either a no-op (1-task story) or a self-block (the depending Task cannot start
until it is itself terminal). Neither is ever a valid authored dependency, and
``_check_dep_cycles`` never flags it because a self-ancestor edge is not a graph
cycle. ``validate-backlog`` must therefore reject it at authoring time so the
backlog cannot reach a "launch-ready" state with a silently non-executable Task.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

_KNOWN_REPO = "caylent-solutions/devbench"


def _make_runtime_config(repo: str = _KNOWN_REPO) -> RuntimeConfig:
    return RuntimeConfig(repos={repo: RepoConfig()})


def _write_index(tmp_path: Path, rows: str) -> Path:
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


def _write_container(backlog_dir: Path, unit_id: str, title: str) -> None:
    """Write a minimal container (story/feature/epic) work-unit file on disk."""
    (backlog_dir / f"{unit_id}.md").write_text(
        f"# {unit_id}: {title}\n\n## Status: in-queue\n\n## Description\n\nContainer.\n",
        encoding="utf-8",
    )


def _write_task(backlog_dir: Path, unit_id: str, dep_rows: str, repo: str = _KNOWN_REPO) -> None:
    """Write a minimal, otherwise-valid Task work-unit file with the given dep rows."""
    (backlog_dir / f"{unit_id}.md").write_text(
        f"# {unit_id}: Task Title\n\n"
        f"## Status: in-queue\n\n"
        f"## Target Repository\n\n- **Repo:** `{repo}`\n\n"
        f"## Description\n\nTest task.\n\n"
        f"## Dependencies\n\n"
        f"| ID | Title | Status |\n"
        f"|----|-------|--------|\n"
        f"{dep_rows}\n"
        f"## Acceptance Criteria\n\n- [ ] AC-TEST-001 placeholder\n\n"
        f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/f.py` | modify |\n\n"
        f"## Definition of Done\n\n- [ ] All ACs checked\n\n"
        f"## TDD Cycle Log\n\n## Comments\n",
        encoding="utf-8",
    )


def _run_validate(tmp_path: Path, rt_cfg: RuntimeConfig) -> list[str]:
    idx = tmp_path / "BACKLOG.md"
    with patch("devbench.config.RUNTIME_CONFIG", rt_cfg):
        return BacklogManager().validate(idx, tmp_path)


def _self_ancestor_errors(errors: list[str], task_id: str) -> list[str]:
    return [e for e in errors if task_id in e and "ancestor" in e.lower()]


class TestSelfAncestorDepValidator:
    def test_task_depending_on_parent_story_is_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A Task that lists its parent story is rejected with an explicit error."""
        _write_container(backlog_dir, "E1-F1-S1", "Story")
        _write_task(backlog_dir, "E1-F1-S1-T1", dep_rows="| E1-F1-S1 | Story | in-queue |\n")
        _write_index(
            tmp_path,
            "| E1-F1-S1 | Story | Story | in-queue | none | "
            f"{_KNOWN_REPO} | `backlog/E1-F1-S1.md` |\n"
            "| E1-F1-S1-T1 | Task Title | Task | in-queue | E1-F1-S1 | "
            f"{_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        errors = _run_validate(tmp_path, _make_runtime_config())
        matches = _self_ancestor_errors(errors, "E1-F1-S1-T1")
        assert matches, f"Expected a self-ancestor dependency error; got: {errors}"
        assert "E1-F1-S1" in matches[0]

    def test_task_depending_on_parent_feature_is_rejected(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A Task that lists its parent feature is also rejected."""
        _write_container(backlog_dir, "E1-F1", "Feature")
        _write_task(backlog_dir, "E1-F1-S1-T1", dep_rows="| E1-F1 | Feature | in-queue |\n")
        _write_index(
            tmp_path,
            "| E1-F1 | Feature | Feature | in-queue | none | "
            f"{_KNOWN_REPO} | `backlog/E1-F1.md` |\n"
            "| E1-F1-S1-T1 | Task Title | Task | in-queue | E1-F1 | "
            f"{_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        errors = _run_validate(tmp_path, _make_runtime_config())
        assert _self_ancestor_errors(errors, "E1-F1-S1-T1"), f"Expected self-ancestor error; got: {errors}"

    def test_dep_on_non_ancestor_container_is_not_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Regression guard: depending on a DIFFERENT container is a legitimate edge.

        ``E1-F1-S1-T1`` depending on the unrelated story ``E1-F2-S1`` is a
        normal cross-container dependency and must NOT trip the self-ancestor
        gate (the dep id is not a prefix-ancestor of the task id).
        """
        _write_container(backlog_dir, "E1-F2-S1", "Other Story")
        _write_task(backlog_dir, "E1-F2-S1-T1", dep_rows="| none | | |\n")
        _write_task(backlog_dir, "E1-F1-S1-T1", dep_rows="| E1-F2-S1 | Other Story | in-queue |\n")
        _write_index(
            tmp_path,
            "| E1-F2-S1 | Other Story | Story | in-queue | none | "
            f"{_KNOWN_REPO} | `backlog/E1-F2-S1.md` |\n"
            "| E1-F2-S1-T1 | Task Title | Task | in-queue | none | "
            f"{_KNOWN_REPO} | `backlog/E1-F2-S1-T1.md` |\n"
            "| E1-F1-S1-T1 | Task Title | Task | in-queue | E1-F2-S1 | "
            f"{_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        errors = _run_validate(tmp_path, _make_runtime_config())
        assert not _self_ancestor_errors(errors, "E1-F1-S1-T1"), (
            f"Cross-container dep must not be flagged as self-ancestor; got: {errors}"
        )

    def test_clean_task_dep_is_not_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A plain Task->Task dependency never trips the self-ancestor gate."""
        _write_task(backlog_dir, "E1-F1-S1-T1", dep_rows="| none | | |\n")
        _write_task(backlog_dir, "E1-F1-S1-T2", dep_rows="| E1-F1-S1-T1 | Task Title | in-queue |\n")
        _write_index(
            tmp_path,
            "| E1-F1-S1-T1 | Task Title | Task | in-queue | none | "
            f"{_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n"
            "| E1-F1-S1-T2 | Task Title | Task | in-queue | E1-F1-S1-T1 | "
            f"{_KNOWN_REPO} | `backlog/E1-F1-S1-T2.md` |\n",
        )
        errors = _run_validate(tmp_path, _make_runtime_config())
        assert not _self_ancestor_errors(errors, "E1-F1-S1-T2"), f"Task->Task dep must not be flagged; got: {errors}"
