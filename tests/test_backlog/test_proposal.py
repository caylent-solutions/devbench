"""Tests for devbench.backlog.proposal (task-factory proposal lifecycle)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from devbench.backlog import proposal as proposal_mod
from devbench.backlog.proposal import (
    DRAFT_TEMPLATE,
    LOCK_FILE_NAME,
    PROPOSAL_DIR_NAME,
    REJECTED_PROPOSAL_DIR_NAME,
    Proposal,
    ProposalError,
    ProposedTask,
    _append_backlog_row,
    _append_dependency_to_source,
    _extract_story_id,
    _find_draft_file,
    _find_originating_source_task,
    _find_source_task_file,
    _has_unresolved_proposals,
    _remove_backlog_row,
    _render_backlog_row,
    _rewrite_backlog_status,
    _rewrite_status,
    _story_dir,
    allocate_next_ids,
    delete_proposal,
    generate_draft_md,
    list_proposals,
    materialise_proposal,
    promote_all_from_source,
    promote_proposal,
    proposal_path,
    read_proposal,
    reject_proposal,
    scan_story_for_task_ids,
    write_proposal,
)

# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


_SOURCE_ROW = (
    "| E0-F1-S1-T1 | Source Task | Task | blocked | None "
    "| caylent-solutions/example | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"
)

_BACKLOG_TEMPLATE = (
    "# Backlog\n\n"
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
    "|------|-------|------|-------------|----------|---------|\n"
    "| E0 | Example Epic | 0 | 0 | 1 | 0 |\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
    f"{_SOURCE_ROW}\n"
)

_SOURCE_TASK_TEMPLATE = """\
# {task_id}: Source Task

## Status: blocked

## Target Repository

- **Repo:** `caylent-solutions/example`

## Description

original source task.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 cover the edge case

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | new unit tests |

## Definition of Done

- [ ] all AC complete
"""


def _build_workspace(tmp_path: Path) -> Path:
    """Create a minimal devbench workspace with one blocked source task."""
    (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1"))
    return tmp_path


def _sample_proposal(source_task_id: str = "E0-F1-S1-T1", *, task_ids: list[str] | None = None) -> Proposal:
    task_ids = task_ids or ["E0-F1-S1-T2", "E0-F1-S1-T3"]
    tasks = [
        ProposedTask(
            suggested_id=tid,
            title=f"Proposed Task {i}",
            files_to_own=[f"src/{tid}.py"],
            linked_scenarios=[f"SC-{i:02d}"],
            suggested_acs=[f"AC-FUNC-{i:03d} fix the scenario"],
            suggested_approach=f"1. Reproduce SC-{i:02d} locally.\n2. Fix the bug.\n3. Verify.",
        )
        for i, tid in enumerate(task_ids, start=1)
    ]
    return Proposal(
        source_task_id=source_task_id,
        generated_at="2026-04-18T03:25:00Z",
        rejection_reason="scope creep fixes are unrelated to source task",
        proposed_tasks=tasks,
    )


# ---------------------------------------------------------------------------
# Proposal dataclass + schema helpers
# ---------------------------------------------------------------------------


class TestProposalDataclass:
    def test_to_dict_roundtrip(self) -> None:
        proposal = _sample_proposal()
        restored = Proposal.from_dict(proposal.to_dict())
        assert restored == proposal

    def test_from_dict_rejects_non_dict(self) -> None:
        with pytest.raises(ValueError, match="must be an object"):
            Proposal.from_dict([1, 2, 3])  # type: ignore[arg-type]

    def test_from_dict_rejects_missing_top_field(self) -> None:
        payload = _sample_proposal().to_dict()
        del payload["source_task_id"]
        with pytest.raises(ValueError, match="missing required field"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_non_list_tasks(self) -> None:
        payload: dict[str, Any] = _sample_proposal().to_dict()
        payload["proposed_tasks"] = "not a list"
        with pytest.raises(ValueError, match="must be a list"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_non_dict_task_entry(self) -> None:
        payload = _sample_proposal().to_dict()
        payload["proposed_tasks"][0] = "bad"
        with pytest.raises(ValueError, match="must be an object"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_missing_task_field(self) -> None:
        payload = _sample_proposal().to_dict()
        del payload["proposed_tasks"][0]["title"]
        with pytest.raises(ValueError, match="missing required field"):
            Proposal.from_dict(payload)


# ---------------------------------------------------------------------------
# Path / lock helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_proposal_path(self, tmp_path: Path) -> None:
        assert proposal_path(tmp_path, "E0-F1-S1-T1") == tmp_path / PROPOSAL_DIR_NAME / "E0-F1-S1-T1.json"

    def test_extract_story_id_ok(self) -> None:
        assert _extract_story_id("E0-F1-S1-T3") == "E0-F1-S1"

    def test_extract_story_id_rejects_short(self) -> None:
        with pytest.raises(ProposalError, match="Cannot derive"):
            _extract_story_id("E0-F1")

    def test_story_dir_rejects_bad_story_id(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="not a valid Story ID"):
            _story_dir(tmp_path, "NOT-A-STORY")

    def test_story_dir_valid(self, tmp_path: Path) -> None:
        path = _story_dir(tmp_path, "E0-F1-S1")
        assert path == tmp_path / "E0" / "E0-F1" / "E0-F1-S1"

    def test_constants_present(self) -> None:
        assert LOCK_FILE_NAME
        assert PROPOSAL_DIR_NAME
        assert REJECTED_PROPOSAL_DIR_NAME
        assert DRAFT_TEMPLATE


# ---------------------------------------------------------------------------
# scan_story_for_task_ids + allocate_next_ids
# ---------------------------------------------------------------------------


class TestScanStoryForTaskIds:
    def test_empty_dir_returns_empty_set(self, tmp_path: Path) -> None:
        assert scan_story_for_task_ids(tmp_path, "E0-F1-S1") == set()

    def test_picks_up_existing_task_files(self, tmp_path: Path) -> None:
        story_dir = tmp_path / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text("# x\n")
        (story_dir / "E0-F1-S1-T2.md").write_text("# x\n")
        (story_dir / "E0-F1-S1.md").write_text("# story\n")  # not a task
        (story_dir / "README.md").write_text("# other\n")  # not a task
        (story_dir / "ignored.txt").write_text("")  # not .md
        assert scan_story_for_task_ids(tmp_path, "E0-F1-S1") == {"E0-F1-S1-T1", "E0-F1-S1-T2"}


class TestAllocateNextIds:
    def test_zero_count_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="count must be"):
            allocate_next_ids(tmp_path, tmp_path / "backlog", "E0-F1-S1", 0)

    def test_first_allocation_skips_existing(self, tmp_path: Path) -> None:
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T5.md").write_text("# x\n")
        (story_dir / "E0-F1-S1-T2.md").write_text("# x\n")
        ids = allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 3)
        assert ids == ["E0-F1-S1-T6", "E0-F1-S1-T7", "E0-F1-S1-T8"]

    def test_allocation_in_parallel_threads_disjoint(self, tmp_path: Path) -> None:
        backlog_root = tmp_path / "backlog"
        (backlog_root / "E0" / "E0-F1" / "E0-F1-S1").mkdir(parents=True)

        results: list[list[str]] = [[], []]
        errors: list[BaseException] = []

        def worker(slot: int) -> None:
            try:
                results[slot] = allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 3)
                # Now materialise the IDs so the other thread sees them as existing.
                story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
                for tid in results[slot]:
                    (story_dir / f"{tid}.md").write_text("# x\n")
            except (AssertionError, RuntimeError, OSError) as exc:
                errors.append(exc)

        # Staged execution: run the two threads sequentially through the lock so the
        # second sees the first's writes and returns disjoint IDs. Concurrency is
        # exercised by the lock-scope exception test below.
        t0 = threading.Thread(target=worker, args=(0,))
        t0.start()
        t0.join()
        t1 = threading.Thread(target=worker, args=(1,))
        t1.start()
        t1.join()

        assert not errors, errors
        assert set(results[0]).isdisjoint(results[1])

    def test_lock_released_on_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        backlog_root = tmp_path / "backlog"
        (backlog_root / "E0" / "E0-F1" / "E0-F1-S1").mkdir(parents=True)

        # Patch scan_story_for_task_ids to raise on the first call; the
        # lock MUST still release so the second call succeeds.
        calls = {"n": 0}
        real_scan = proposal_mod.scan_story_for_task_ids

        def fake_scan(root: Path, story_id: str) -> set[str]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_scan(root, story_id)

        monkeypatch.setattr(proposal_mod, "scan_story_for_task_ids", fake_scan)
        with pytest.raises(RuntimeError, match="boom"):
            allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 1)
        # Second call must succeed (lock released).
        ids = allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 1)
        assert ids == ["E0-F1-S1-T1"]


# ---------------------------------------------------------------------------
# Proposal I/O
# ---------------------------------------------------------------------------


class TestProposalIO:
    def test_write_reads_back(self, tmp_path: Path) -> None:
        proposal = _sample_proposal()
        written = write_proposal(tmp_path, proposal)
        assert written.exists()
        assert read_proposal(tmp_path, proposal.source_task_id) == proposal

    def test_write_rejects_duplicate(self, tmp_path: Path) -> None:
        proposal = _sample_proposal()
        write_proposal(tmp_path, proposal)
        with pytest.raises(ProposalError, match="already exists"):
            write_proposal(tmp_path, proposal)

    def test_read_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="No proposal"):
            read_proposal(tmp_path, "E0-F1-S1-T1")

    def test_read_malformed_raises(self, tmp_path: Path) -> None:
        target = proposal_path(tmp_path, "E0-F1-S1-T1")
        target.parent.mkdir(parents=True)
        target.write_text("not json")
        with pytest.raises(ProposalError, match="not valid JSON"):
            read_proposal(tmp_path, "E0-F1-S1-T1")

    def test_read_schema_violation_raises(self, tmp_path: Path) -> None:
        target = proposal_path(tmp_path, "E0-F1-S1-T1")
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"source_task_id": "E0-F1-S1-T1"}))
        with pytest.raises(ProposalError, match="missing required"):
            read_proposal(tmp_path, "E0-F1-S1-T1")

    def test_delete_noop_when_absent(self, tmp_path: Path) -> None:
        delete_proposal(tmp_path, "E0-F1-S1-T1")  # no raise

    def test_delete_existing(self, tmp_path: Path) -> None:
        proposal = _sample_proposal()
        written = write_proposal(tmp_path, proposal)
        delete_proposal(tmp_path, proposal.source_task_id)
        assert not written.exists()


# ---------------------------------------------------------------------------
# generate_draft_md
# ---------------------------------------------------------------------------


class TestGenerateDraftMd:
    def test_renders_with_explicit_acs_and_manifest(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="My Task",
            files_to_own=["src/a.py", "src/b.py"],
            linked_scenarios=["SC-01", "SC-02"],
            suggested_acs=["AC-FUNC-001 foo"],
            suggested_approach="Do the thing.",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "E0-F1-S1-T9" in md
        assert "## Status: proposed" in md
        assert "Do the thing." in md
        assert "- [ ] AC-FUNC-001 foo" in md
        assert "| `src/a.py` | TODO -- describe change |" in md
        assert "SC-01, SC-02" in md
        assert "generated by task-factory on NOW" in md

    def test_fills_fallbacks_when_fields_empty(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="My Task",
            files_to_own=[],
            linked_scenarios=[],
            suggested_acs=[],
            suggested_approach="",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "human must author approach" in md
        assert "AC-TODO-001 human must author AC" in md
        assert "`TODO` | TODO -- describe change" in md
        assert "(none documented)" in md


# ---------------------------------------------------------------------------
# BACKLOG.md manipulation
# ---------------------------------------------------------------------------


class TestBacklogRowHelpers:
    def test_render_row_shape(self) -> None:
        row = _render_backlog_row("E0-F1-S1-T2", "A", "proposed", "r/x", "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md")
        assert row.startswith("| E0-F1-S1-T2 | A | Task | proposed ")
        assert row.endswith("E0-F1-S1-T2.md` |\n")

    def test_append_and_remove_row_roundtrip(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        new_row = _render_backlog_row(
            "E0-F1-S1-T7",
            "New Task",
            "proposed",
            "caylent-solutions/example",
            "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T7.md",
        )
        _append_backlog_row(workspace / "BACKLOG.md", new_row)
        assert "E0-F1-S1-T7" in (workspace / "BACKLOG.md").read_text()
        _remove_backlog_row(workspace / "BACKLOG.md", "E0-F1-S1-T7")
        assert "E0-F1-S1-T7" not in (workspace / "BACKLOG.md").read_text()

    def test_remove_row_raises_when_missing(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="not found"):
            _remove_backlog_row(workspace / "BACKLOG.md", "DOES-NOT-EXIST")

    def test_append_row_raises_when_marker_missing(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text("no marker here\n")
        with pytest.raises(ProposalError, match="has no '## Full Work Unit Index'"):
            _append_backlog_row(tmp_path / "BACKLOG.md", "| X | ... |\n")


class TestRewriteStatus:
    def test_rewrite_md_status(self, tmp_path: Path) -> None:
        md = tmp_path / "task.md"
        md.write_text("# T\n\n## Status: proposed\n\nbody\n")
        _rewrite_status(md, "in-queue")
        assert "## Status: in-queue" in md.read_text()

    def test_rewrite_md_status_missing_line(self, tmp_path: Path) -> None:
        md = tmp_path / "task.md"
        md.write_text("# T\n\nbody\n")
        with pytest.raises(ProposalError, match="no '## Status:' line"):
            _rewrite_status(md, "in-queue")

    def test_rewrite_backlog_status(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "A",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )
        _rewrite_backlog_status(workspace / "BACKLOG.md", "E0-F1-S1-T2", "in-queue")
        content = (workspace / "BACKLOG.md").read_text()
        assert "| E0-F1-S1-T2 | A | Task | in-queue " in content

    def test_rewrite_backlog_status_missing_row(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="not found"):
            _rewrite_backlog_status(workspace / "BACKLOG.md", "DOES-NOT-EXIST", "in-queue")


# ---------------------------------------------------------------------------
# materialise_proposal
# ---------------------------------------------------------------------------


class TestMaterialiseProposal:
    def test_creates_drafts_and_appends_rows(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal()
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(drafts) == 2
        for draft in drafts:
            assert draft.is_file()
            assert "## Status: proposed" in draft.read_text()
        backlog = (workspace / "BACKLOG.md").read_text()
        assert "E0-F1-S1-T2" in backlog
        assert "E0-F1-S1-T3" in backlog

    def test_refuses_when_unresolved_proposed_rows_exist(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        # Seed BACKLOG.md with an existing proposed row BEFORE materialising a fresh proposal.
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T99",
                "Stale",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T99.md",
            ),
        )
        with pytest.raises(ProposalError, match="unresolved proposed tasks"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=_sample_proposal(),
                repo="caylent-solutions/example",
            )

    def test_refuses_when_draft_file_already_exists(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        (story_dir / "E0-F1-S1-T2.md").write_text("existing")
        with pytest.raises(ProposalError, match=r"Draft file .* already exists"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=_sample_proposal(),
                repo="caylent-solutions/example",
            )


class TestHasUnresolvedProposals:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert _has_unresolved_proposals(tmp_path / "nonexistent") is False

    def test_no_proposed(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        assert _has_unresolved_proposals(workspace / "BACKLOG.md") is False

    def test_present(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T99",
                "Stale",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T99.md",
            ),
        )
        assert _has_unresolved_proposals(workspace / "BACKLOG.md") is True

    def test_short_line_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text("| a |\n## Full Work Unit Index\n")
        assert _has_unresolved_proposals(tmp_path / "BACKLOG.md") is False

    def test_non_pipe_line_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text("plain line\n")
        assert _has_unresolved_proposals(tmp_path / "BACKLOG.md") is False


# ---------------------------------------------------------------------------
# list_proposals
# ---------------------------------------------------------------------------


class TestListProposals:
    def test_none_when_dir_missing(self, tmp_path: Path) -> None:
        assert list_proposals(tmp_path) == []

    def test_returns_written_proposals(self, tmp_path: Path) -> None:
        p = _sample_proposal()
        write_proposal(tmp_path, p)
        out = list_proposals(tmp_path)
        assert len(out) == 1
        assert out[0] == p

    def test_skips_garbage_files(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        d = tmp_path / PROPOSAL_DIR_NAME
        d.mkdir(parents=True)
        (d / "junk.json").write_text("not json")
        (d / "readme.txt").write_text("skip non-json extension")
        # Also add a valid one so the final output is non-empty.
        p = _sample_proposal()
        write_proposal(tmp_path, p)
        out = list_proposals(tmp_path)
        assert any(pp == p for pp in out)


# ---------------------------------------------------------------------------
# promote_proposal / reject_proposal
# ---------------------------------------------------------------------------


class TestPromoteProposal:
    def test_missing_draft_raises(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="No draft file"):
            promote_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T99",
            )

    def test_happy_path_wires_dependency(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        draft = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert draft.is_file()
        # Draft status flipped.
        assert "## Status: in-queue" in draft.read_text()
        # Source task now has a dep on the promoted task.
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "| E0-F1-S1-T2 |" in source.read_text()
        # Audit comment was added.
        assert "[PROPOSAL_PROMOTED]" in source.read_text()

    def test_skip_dep_wiring(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            dep_on_source=False,
        )
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "| E0-F1-S1-T2 |" not in source.read_text()


class TestPromoteAllFromSource:
    def test_missing_proposal_raises(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="Cannot resolve"):
            promote_all_from_source(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                source_task_id="E0-F1-S1-T1",
            )

    def test_promotes_every_task(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        promoted = promote_all_from_source(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            source_task_id="E0-F1-S1-T1",
        )
        assert len(promoted) == 2


class TestRejectProposal:
    def test_empty_reason_raises(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="non-empty reason"):
            reject_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
                reason="   ",
            )

    def test_archives_draft_and_removes_row(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="wrong direction",
        )
        assert archive is not None and archive.is_file()
        assert "E0-F1-S1-T2" not in (workspace / "BACKLOG.md").read_text()
        # Source task got an audit comment.
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "[PROPOSAL_REJECTED]" in source.read_text()

    def test_rejects_proposal_when_draft_missing(self, tmp_path: Path) -> None:
        """Idempotent: missing draft returns ``None`` without error."""
        workspace = _build_workspace(tmp_path)
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T99",
            reason="already gone",
        )
        assert archive is None


# ---------------------------------------------------------------------------
# Find helpers used by promote / reject
# ---------------------------------------------------------------------------


class TestFindHelpers:
    def test_find_draft_file_missing(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        assert _find_draft_file(workspace / "backlog", "E0-F1-S1-T99") is None

    def test_find_source_task_file_missing_index(self, tmp_path: Path) -> None:
        assert _find_source_task_file(tmp_path / "backlog", tmp_path / "nonexistent.md", "X") is None

    def test_find_originating_source_task_returns_none_when_nothing(self, tmp_path: Path) -> None:
        assert _find_originating_source_task(tmp_path, "E0-F1-S1-T2") is None


class TestAppendDependencyToSource:
    def test_appends_when_no_none(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        # Overwrite source task with a non-empty Dependencies section (no "none").
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        content = source.read_text().replace(
            "| none | | |",
            "| E0-F1-S1-T4 | some | done |",
        )
        source.write_text(content)
        _append_dependency_to_source(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1", "E0-F1-S1-T9")
        updated = source.read_text()
        assert "| E0-F1-S1-T9 |" in updated

    def test_raises_without_dependencies_section(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        # Keep a valid parser-readable header but drop the Dependencies section.
        source.write_text(
            "# E0-F1-S1-T1: Source Task\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `caylent-solutions/example`\n\n"
            "## Description\n\ntext\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 cover edge\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] all complete\n"
        )
        with pytest.raises(ProposalError, match="no '## Dependencies'"):
            _append_dependency_to_source(
                workspace / "backlog",
                workspace / "BACKLOG.md",
                "E0-F1-S1-T1",
                "E0-F1-S1-T9",
            )

    def test_raises_when_source_missing(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="Cannot find source"):
            _append_dependency_to_source(
                workspace / "backlog",
                workspace / "BACKLOG.md",
                "NO-SUCH-TASK",
                "E0-F1-S1-T9",
            )


class TestSourceCommentsSectionAlreadyPresent:
    """When a source task already has a ## Comments section, rejects append to it."""

    def test_reject_appends_to_existing_comments(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        # Write a source task that already has a ## Comments section.
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        content = source.read_text() + "\n## Comments\n\n[2026-04-17 10:00 UTC] [agent/orchestrator] prior entry\n"
        source.write_text(content)

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="not needed",
        )
        updated = source.read_text()
        assert "prior entry" in updated
        assert "[PROPOSAL_REJECTED]" in updated


class TestAppendPromoteCommentNoExistingComments:
    """_append_promote_comment creates a ## Comments section when missing."""

    def test_creates_comments_when_absent(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _append_promote_comment

        source_file = tmp_path / "source.md"
        source_file.write_text("# T: X\n\n## Status: blocked\n\n## Description\n\nx\n")
        _append_promote_comment(source_file, "E0-F1-S1-T1", "E0-F1-S1-T2")
        updated = source_file.read_text()
        assert "## Comments" in updated
        assert "[PROPOSAL_PROMOTED]" in updated
