"""Tests for _check_manifest_conflicts with draft/hold status scoping.

Verifies that:
- Two draft tasks owning the same (repo, path) without a serial dep emit
  a WARNING (default) or escalate to ERROR when strict=True.
- The existing in-queue/proposed/blocked ERROR path is unchanged.
- Tasks wired with a serial dep are exempt regardless of status.
- done/declined/in-progress remain out of scope (never flagged).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager

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


def _make_index(tmp_path: Path, rows: str) -> Path:
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(INDEX_HEADER + rows, encoding="utf-8")
    return idx


def _make_task(
    backlog_dir: Path,
    unit_id: str,
    repo: str,
    manifest_rows: str,
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
        f"## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
        f"## Changes Manifest\n\n"
        f"| File | Change |\n"
        f"|------|--------|\n"
        f"{manifest_rows}\n"
        f"## Definition of Done\n\n- [ ] Done\n",
        encoding="utf-8",
    )
    return wu


@pytest.mark.unit
@pytest.mark.parametrize("task_status", ["draft", "hold"])
def test_two_draft_or_hold_no_dep_emits_warning(
    tmp_path: Path,
    backlog_dir: Path,
    task_status: str,
) -> None:
    """Two draft/hold tasks sharing (repo, path) with no dep -> WARNING (default)."""
    repo = "ex/foo"
    _make_task(backlog_dir, "E1-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=task_status)
    _make_task(backlog_dir, "E1-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status=task_status)
    _make_index(
        tmp_path,
        f"| E1-F1-S1-T1 | T1 | Task | {task_status} | none | {repo} | `backlog/E1-F1-S1-T1.md` |\n"
        f"| E1-F1-S1-T2 | T2 | Task | {task_status} | none | {repo} | `backlog/E1-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 0, f"Unexpected error in default mode: {conflict_errors}"
    conflict_warnings = [w for w in warnings if "draft/hold conflict" in w and "shared.yaml" in w]
    assert len(conflict_warnings) == 1
    assert "E1-F1-S1-T1" in conflict_warnings[0]
    assert "E1-F1-S1-T2" in conflict_warnings[0]


@pytest.mark.unit
@pytest.mark.parametrize("task_status", ["draft", "hold"])
def test_two_draft_or_hold_no_dep_strict_emits_error(
    tmp_path: Path,
    backlog_dir: Path,
    task_status: str,
) -> None:
    """Two draft/hold tasks sharing (repo, path) with no dep -> ERROR under strict."""
    repo = "ex/foo"
    _make_task(backlog_dir, "E1-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=task_status)
    _make_task(backlog_dir, "E1-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status=task_status)
    _make_index(
        tmp_path,
        f"| E1-F1-S1-T1 | T1 | Task | {task_status} | none | {repo} | `backlog/E1-F1-S1-T1.md` |\n"
        f"| E1-F1-S1-T2 | T2 | Task | {task_status} | none | {repo} | `backlog/E1-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path, strict=True)
    conflict_errors = [e for e in errors if "draft/hold conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1
    assert "E1-F1-S1-T1" in conflict_errors[0]
    assert "E1-F1-S1-T2" in conflict_errors[0]
    conflict_warnings = [w for w in warnings if "draft/hold conflict" in w and "shared.yaml" in w]
    assert len(conflict_warnings) == 0


@pytest.mark.unit
@pytest.mark.parametrize("inflight_status", ["in-queue", "proposed", "blocked"])
def test_inflight_conflict_always_error_strict_false(
    tmp_path: Path,
    backlog_dir: Path,
    inflight_status: str,
) -> None:
    """in-queue/proposed/blocked conflict -> ERROR even without strict flag (unchanged behavior)."""
    repo = "ex/foo"
    _make_task(backlog_dir, "E2-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=inflight_status)
    _make_task(backlog_dir, "E2-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status=inflight_status)
    _make_index(
        tmp_path,
        f"| E2-F1-S1-T1 | T1 | Task | {inflight_status} | none | {repo} | `backlog/E2-F1-S1-T1.md` |\n"
        f"| E2-F1-S1-T2 | T2 | Task | {inflight_status} | none | {repo} | `backlog/E2-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, _warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1
    assert "E2-F1-S1-T1" in conflict_errors[0]
    assert "E2-F1-S1-T2" in conflict_errors[0]
    assert "docs/backlog-contract.md" in conflict_errors[0]


@pytest.mark.unit
@pytest.mark.parametrize("task_status", ["draft", "hold"])
def test_draft_or_hold_with_serial_dep_no_finding(
    tmp_path: Path,
    backlog_dir: Path,
    task_status: str,
) -> None:
    """Draft/hold tasks wired with a serial dep are exempt (no conflict)."""
    repo = "ex/foo"
    _make_task(backlog_dir, "E3-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=task_status)
    _make_task(
        backlog_dir,
        "E3-F1-S1-T2",
        repo,
        "| `shared.yaml` | edit |\n",
        deps_rows="| E3-F1-S1-T1 | dep | draft |",
        status=task_status,
    )
    _make_index(
        tmp_path,
        f"| E3-F1-S1-T1 | T1 | Task | {task_status} | none | {repo} | `backlog/E3-F1-S1-T1.md` |\n"
        f"| E3-F1-S1-T2 | T2 | Task | {task_status} | E3-F1-S1-T1 | {repo} | `backlog/E3-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "conflict" in e.lower() and "shared.yaml" in e]
    conflict_warnings = [w for w in warnings if "conflict" in w.lower() and "shared.yaml" in w]
    assert len(conflict_errors) == 0, f"Unexpected error: {conflict_errors}"
    assert len(conflict_warnings) == 0, f"Unexpected warning: {conflict_warnings}"


@pytest.mark.unit
@pytest.mark.parametrize("task_status", ["draft", "hold"])
def test_draft_or_hold_with_serial_dep_strict_no_finding(
    tmp_path: Path,
    backlog_dir: Path,
    task_status: str,
) -> None:
    """Draft/hold tasks wired with serial dep are exempt even under strict mode."""
    repo = "ex/foo"
    _make_task(backlog_dir, "E3-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=task_status)
    _make_task(
        backlog_dir,
        "E3-F1-S1-T2",
        repo,
        "| `shared.yaml` | edit |\n",
        deps_rows="| E3-F1-S1-T1 | dep | draft |",
        status=task_status,
    )
    _make_index(
        tmp_path,
        f"| E3-F1-S1-T1 | T1 | Task | {task_status} | none | {repo} | `backlog/E3-F1-S1-T1.md` |\n"
        f"| E3-F1-S1-T2 | T2 | Task | {task_status} | E3-F1-S1-T1 | {repo} | `backlog/E3-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path, strict=True)
    conflict_errors = [e for e in errors if "conflict" in e.lower() and "shared.yaml" in e]
    conflict_warnings = [w for w in warnings if "conflict" in w.lower() and "shared.yaml" in w]
    assert len(conflict_errors) == 0, f"Unexpected error under strict: {conflict_errors}"
    assert len(conflict_warnings) == 0, f"Unexpected warning under strict: {conflict_warnings}"


@pytest.mark.unit
@pytest.mark.parametrize("task_status", ["done", "declined", "in-progress"])
def test_out_of_scope_statuses_never_flagged(
    tmp_path: Path,
    backlog_dir: Path,
    task_status: str,
) -> None:
    """done/declined/in-progress owners are never flagged for manifest conflicts."""
    repo = "ex/foo"
    _make_task(backlog_dir, "E4-F1-S1-T1", repo, "| `shared.yaml` | new |\n", status=task_status)
    _make_task(backlog_dir, "E4-F1-S1-T2", repo, "| `shared.yaml` | edit |\n", status=task_status)
    _make_index(
        tmp_path,
        f"| E4-F1-S1-T1 | T1 | Task | {task_status} | none | {repo} | `backlog/E4-F1-S1-T1.md` |\n"
        f"| E4-F1-S1-T2 | T2 | Task | {task_status} | none | {repo} | `backlog/E4-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path, strict=True)
    conflict_errors = [e for e in errors if "conflict" in e.lower() and "shared.yaml" in e]
    conflict_warnings = [w for w in warnings if "conflict" in w.lower() and "shared.yaml" in w]
    assert len(conflict_errors) == 0, f"Unexpected error for {task_status}: {conflict_errors}"
    assert len(conflict_warnings) == 0, f"Unexpected warning for {task_status}: {conflict_warnings}"


@pytest.mark.unit
def test_draft_tasks_different_repos_same_path_no_finding(tmp_path: Path, backlog_dir: Path) -> None:
    """Two draft tasks in different repos with same path are not in conflict."""
    _make_task(backlog_dir, "E5-F1-S1-T1", "ex/repo-a", "| `shared.yaml` | new |\n", status="draft")
    _make_task(backlog_dir, "E5-F1-S1-T2", "ex/repo-b", "| `shared.yaml` | new |\n", status="draft")
    _make_index(
        tmp_path,
        "| E5-F1-S1-T1 | T1 | Task | draft | none | ex/repo-a | `backlog/E5-F1-S1-T1.md` |\n"
        "| E5-F1-S1-T2 | T2 | Task | draft | none | ex/repo-b | `backlog/E5-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "conflict" in e.lower() and "shared.yaml" in e]
    conflict_warnings = [w for w in warnings if "conflict" in w.lower() and "shared.yaml" in w]
    assert len(conflict_errors) == 0
    assert len(conflict_warnings) == 0


@pytest.mark.unit
@pytest.mark.parametrize("edit_verb", ["modify", "delete", "update", "remove"])
def test_adder_recommended_before_modifier(
    tmp_path: Path,
    backlog_dir: Path,
    edit_verb: str,
) -> None:
    """A unit that ``add``s the shared path must be the chain root.

    The adder (E6-F1-S1-T2) sorts AFTER the modifier (E6-F1-S1-T1)
    lexicographically, so a naive ``sorted(ids)`` chain would (wrongly)
    recommend the adder depend on the modifier. The verb-aware ordering must
    instead recommend the modifier depend on the adder: ``add-dep T1 T2``.
    """
    repo = "ex/foo"
    _make_task(backlog_dir, "E6-F1-S1-T1", repo, f"| `shared.yaml` | {edit_verb} |\n", status="in-queue")
    _make_task(backlog_dir, "E6-F1-S1-T2", repo, "| `shared.yaml` | add |\n", status="in-queue")
    _make_index(
        tmp_path,
        f"| E6-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/E6-F1-S1-T1.md` |\n"
        f"| E6-F1-S1-T2 | T2 | Task | in-queue | none | {repo} | `backlog/E6-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, _warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1, f"Expected exactly one conflict error; got: {errors}"
    message = conflict_errors[0]
    assert "uv run devbench add-dep E6-F1-S1-T1 E6-F1-S1-T2" in message, (
        f"Expected modifier-depends-on-adder recommendation; got:\n{message}"
    )
    assert "uv run devbench add-dep E6-F1-S1-T2 E6-F1-S1-T1" not in message
    assert "claimed by E6-F1-S1-T1, E6-F1-S1-T2" in message


@pytest.mark.unit
def test_all_modify_falls_back_to_lexicographic(
    tmp_path: Path,
    backlog_dir: Path,
) -> None:
    """When no single ``add``er disambiguates, the chain stays lexicographic.

    Both claimants modify the shared path, so the recommendation must preserve
    the prior behaviour: the lexicographically LATER id depends on the EARLIER
    one (``add-dep T2 T1``).
    """
    repo = "ex/foo"
    _make_task(backlog_dir, "E7-F1-S1-T1", repo, "| `shared.yaml` | modify |\n", status="in-queue")
    _make_task(backlog_dir, "E7-F1-S1-T2", repo, "| `shared.yaml` | modify |\n", status="in-queue")
    _make_index(
        tmp_path,
        f"| E7-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/E7-F1-S1-T1.md` |\n"
        f"| E7-F1-S1-T2 | T2 | Task | in-queue | none | {repo} | `backlog/E7-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, _warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1, f"Expected exactly one conflict error; got: {errors}"
    message = conflict_errors[0]
    assert "uv run devbench add-dep E7-F1-S1-T2 E7-F1-S1-T1" in message, (
        f"Expected lexicographic fallback recommendation; got:\n{message}"
    )
    assert "uv run devbench add-dep E7-F1-S1-T1 E7-F1-S1-T2" not in message


@pytest.mark.unit
def test_two_adders_falls_back_to_lexicographic(
    tmp_path: Path,
    backlog_dir: Path,
) -> None:
    """When MORE than one claimant adds the path, verbs do not disambiguate.

    Two adders mean there is no single creator the others can depend on, so the
    recommendation falls back to the lexicographic chain.
    """
    repo = "ex/foo"
    _make_task(backlog_dir, "E8-F1-S1-T1", repo, "| `shared.yaml` | add |\n", status="in-queue")
    _make_task(backlog_dir, "E8-F1-S1-T2", repo, "| `shared.yaml` | add |\n", status="in-queue")
    _make_index(
        tmp_path,
        f"| E8-F1-S1-T1 | T1 | Task | in-queue | none | {repo} | `backlog/E8-F1-S1-T1.md` |\n"
        f"| E8-F1-S1-T2 | T2 | Task | in-queue | none | {repo} | `backlog/E8-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, _warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path)
    conflict_errors = [e for e in errors if "Manifest conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1, f"Expected exactly one conflict error; got: {errors}"
    message = conflict_errors[0]
    assert "uv run devbench add-dep E8-F1-S1-T2 E8-F1-S1-T1" in message, (
        f"Expected lexicographic fallback for two-adder case; got:\n{message}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("edit_verb", ["modify", "delete", "update", "remove"])
def test_draft_strict_adder_recommended_before_modifier(
    tmp_path: Path,
    backlog_dir: Path,
    edit_verb: str,
) -> None:
    """On the all-draft output under --strict, the adder must be the chain root.

    The adder (E9-F1-S1-T2) sorts AFTER the modifier (E9-F1-S1-T1)
    lexicographically; the verb-aware ordering must recommend the modifier
    depend on the adder (``add-dep T1 T2``), not the reverse -- mirroring AC-4
    and the validator's in-queue verb-aware test on the draft/hold strict path.
    """
    repo = "ex/foo"
    _make_task(backlog_dir, "E9-F1-S1-T1", repo, f"| `shared.yaml` | {edit_verb} |\n", status="draft")
    _make_task(backlog_dir, "E9-F1-S1-T2", repo, "| `shared.yaml` | add |\n", status="draft")
    _make_index(
        tmp_path,
        f"| E9-F1-S1-T1 | T1 | Task | draft | none | {repo} | `backlog/E9-F1-S1-T1.md` |\n"
        f"| E9-F1-S1-T2 | T2 | Task | draft | none | {repo} | `backlog/E9-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path, strict=True)
    conflict_errors = [e for e in errors if "draft/hold conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1, f"Expected exactly one strict draft/hold conflict; got: {errors}"
    message = conflict_errors[0]
    assert "uv run devbench add-dep E9-F1-S1-T1 E9-F1-S1-T2" in message, (
        f"Expected modifier-depends-on-adder recommendation on the strict draft path; got:\n{message}"
    )
    assert "uv run devbench add-dep E9-F1-S1-T2 E9-F1-S1-T1" not in message
    conflict_warnings = [w for w in warnings if "draft/hold conflict" in w and "shared.yaml" in w]
    assert len(conflict_warnings) == 0


@pytest.mark.unit
def test_draft_strict_all_modify_falls_back_to_lexicographic(
    tmp_path: Path,
    backlog_dir: Path,
) -> None:
    """On the strict draft path, an all-modify conflict stays lexicographic.

    With no single adder to disambiguate, the recommendation preserves the
    deterministic positional fallback: the lexicographically later id depends
    on the earlier one (``add-dep T2 T1``).
    """
    repo = "ex/foo"
    _make_task(backlog_dir, "E10-F1-S1-T1", repo, "| `shared.yaml` | modify |\n", status="draft")
    _make_task(backlog_dir, "E10-F1-S1-T2", repo, "| `shared.yaml` | modify |\n", status="draft")
    _make_index(
        tmp_path,
        f"| E10-F1-S1-T1 | T1 | Task | draft | none | {repo} | `backlog/E10-F1-S1-T1.md` |\n"
        f"| E10-F1-S1-T2 | T2 | Task | draft | none | {repo} | `backlog/E10-F1-S1-T2.md` |\n",
    )
    mgr = BacklogManager()
    errors, _warnings = mgr.validate_with_warnings(tmp_path / "BACKLOG.md", tmp_path, strict=True)
    conflict_errors = [e for e in errors if "draft/hold conflict" in e and "shared.yaml" in e]
    assert len(conflict_errors) == 1, f"Expected exactly one strict draft/hold conflict; got: {errors}"
    message = conflict_errors[0]
    assert "uv run devbench add-dep E10-F1-S1-T2 E10-F1-S1-T1" in message, (
        f"Expected lexicographic fallback on the strict draft path; got:\n{message}"
    )
    assert "uv run devbench add-dep E10-F1-S1-T1 E10-F1-S1-T2" not in message
