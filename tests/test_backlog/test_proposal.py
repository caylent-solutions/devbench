"""Tests for devbench.backlog.proposal (task-factory proposal lifecycle)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

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
            suggested_approach=(
                f"Context: Scenario SC-{i:02d} failed against the current implementation. "
                f"Scope: One production file and its companion unit test. "
                f"TDD approach: 1. RED -- Reproduce SC-{i:02d} locally in a unit test. "
                "2. GREEN -- Apply the minimal fix in the production module. "
                "3. REFACTOR -- Clean up without changing behaviour. "
                "Verify: make lint && make format-check && make test-unit && make test-integration "
                "all exit zero."
            ),
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
            Proposal.from_dict(cast(Any, [1, 2, 3]))

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

    def test_source_dep_direction_default_is_empty(self) -> None:
        # Default Proposal omits the field; to_dict + from_dict roundtrip
        # must preserve the empty string and not surface as a schema change.
        proposal = _sample_proposal()
        assert proposal.source_dep_direction == ""
        payload = proposal.to_dict()
        assert payload["source_dep_direction"] == ""
        restored = Proposal.from_dict(payload)
        assert restored.source_dep_direction == ""

    def test_source_dep_direction_test_validates_source_roundtrips(self) -> None:
        # The only non-default value the schema accepts is the explicit
        # string "test_validates_source"; cmd_promote_proposal reads this
        # to auto-apply --no-dep-on-source.
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-18T03:25:00Z",
            rejection_reason="r",
            proposed_tasks=[],
            source_dep_direction="test_validates_source",
        )
        restored = Proposal.from_dict(proposal.to_dict())
        assert restored.source_dep_direction == "test_validates_source"

    def test_source_dep_direction_rejects_unknown_value(self) -> None:
        # Any string outside the {"", "test_validates_source"} allowlist
        # must raise ValueError so typos surface at promotion time.
        payload = _sample_proposal().to_dict()
        payload["source_dep_direction"] = "source_validates_test"
        with pytest.raises(ValueError, match="must be empty or 'test_validates_source'"):
            Proposal.from_dict(payload)

    def test_source_dep_direction_missing_in_json_defaults_to_empty(self) -> None:
        # Older proposal JSON files written before the field existed must
        # continue to load as "" (no behavior change).
        payload = _sample_proposal().to_dict()
        del payload["source_dep_direction"]
        restored = Proposal.from_dict(payload)
        assert restored.source_dep_direction == ""


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

    def test_story_dir_resolves_existing_slug_directories(self, tmp_path: Path) -> None:
        """spec-to-backlog names dirs ``<id>-<slug>``; materialise must reuse them."""
        slug = tmp_path / "E0-epic-slug" / "E0-F1-feature-slug" / "E0-F1-S1-story-slug"
        slug.mkdir(parents=True)
        assert _story_dir(tmp_path, "E0-F1-S1") == slug

    def test_story_dir_prefers_directory_holding_the_story_file(self, tmp_path: Path) -> None:
        """When a bare orphan tree and the canonical slug tree both exist, pick the canonical one."""
        orphan = tmp_path / "E0" / "E0-F1" / "E0-F1-S1"
        orphan.mkdir(parents=True)
        canonical = tmp_path / "E0-epic-slug" / "E0-F1-feature-slug" / "E0-F1-S1-story-slug"
        canonical.mkdir(parents=True)
        (canonical / "E0-F1-S1.md").write_text("# E0-F1-S1: Story\n")
        assert _story_dir(tmp_path, "E0-F1-S1") == canonical

    def test_story_dir_does_not_match_a_longer_story_number(self, tmp_path: Path) -> None:
        """``E0-F1-S10-*`` must not satisfy a lookup for ``E0-F1-S1``."""
        (tmp_path / "E0-epic" / "E0-F1-feat" / "E0-F1-S10-story").mkdir(parents=True)
        assert _story_dir(tmp_path, "E0-F1-S1") == tmp_path / "E0" / "E0-F1" / "E0-F1-S1"

    def test_scan_story_sees_tasks_inside_slug_directory(self, tmp_path: Path) -> None:
        slug = tmp_path / "E0-epic-slug" / "E0-F1-feature-slug" / "E0-F1-S1-story-slug"
        slug.mkdir(parents=True)
        for tid in ("E0-F1-S1-T1", "E0-F1-S1-T2"):
            (slug / f"{tid}.md").write_text(f"# {tid}\n")
        assert scan_story_for_task_ids(tmp_path, "E0-F1-S1") == {"E0-F1-S1-T1", "E0-F1-S1-T2"}

    def test_allocate_next_ids_does_not_collide_with_slug_directory_tasks(self, tmp_path: Path) -> None:
        """Allocating blind to the slug dir would return T1 and overwrite an existing task."""
        slug = tmp_path / "backlog" / "E0-epic-slug" / "E0-F1-feature-slug" / "E0-F1-S1-story-slug"
        slug.mkdir(parents=True)
        for tid in ("E0-F1-S1-T1", "E0-F1-S1-T2"):
            (slug / f"{tid}.md").write_text(f"# {tid}\n")
        assert allocate_next_ids(tmp_path, tmp_path / "backlog", "E0-F1-S1", 2) == [
            "E0-F1-S1-T3",
            "E0-F1-S1-T4",
        ]

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

    def test_branch_line_uses_canonical_template_by_default(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="My Task",
            files_to_own=[],
            linked_scenarios=[],
            suggested_acs=[],
            suggested_approach="",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "- **Branch:** `backlog/e0-f1-s1-t9`" in md

    def test_fix_titled_task_gets_newly_reachable_paths_dod_item(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="Fix the exporter crash",
            files_to_own=["src/a.py"],
            linked_scenarios=[],
            suggested_acs=["AC-FIX-001 exporter no longer crashes"],
            suggested_approach="Do the fix.",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "Newly-reachable code paths enumerated and live-verified" in md
        assert "[NEWLY_REACHABLE]" in md
        assert "docs/newly-reachable-paths.md" in md

    def test_fix_title_is_case_insensitive(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="FIX the exporter crash",
            files_to_own=[],
            linked_scenarios=[],
            suggested_acs=[],
            suggested_approach="",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "Newly-reachable code paths enumerated and live-verified" in md

    def test_non_fix_titled_task_omits_newly_reachable_paths_dod_item(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="Add exporter caching",
            files_to_own=["src/a.py"],
            linked_scenarios=[],
            suggested_acs=["AC-ADD-001 caching added"],
            suggested_approach="Do the thing.",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "Newly-reachable code paths enumerated and live-verified" not in md
        assert "[NEWLY_REACHABLE]" not in md

    def test_base_dod_items_always_present(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="Fix the exporter crash",
            files_to_own=[],
            linked_scenarios=[],
            suggested_acs=[],
            suggested_approach="",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "- [ ] All acceptance criteria checked" in md
        assert "- [ ] Tests green" in md
        assert "- [ ] Lint and format clean" in md
        assert "- [ ] Only files in Changes Manifest are staged with `git add`" in md

    def test_branch_line_namespaced_by_configured_branch_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The generated draft's Branch: line must match the branch devbench
        will actually push to -- so it stays namespaced when git_ops.branch_prefix
        (or a per-repo override) is configured, same as BacklogParser's fallback."""
        import devbench.config as config_module
        from devbench.config_loader import GitOpsConfig, RepoConfig, RuntimeConfig

        monkeypatch.setattr(
            config_module,
            "RUNTIME_CONFIG",
            RuntimeConfig(
                repos={"acme/example": RepoConfig(branch_prefix="wg_004")},
                git_ops=GitOpsConfig(),
            ),
        )
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="My Task",
            files_to_own=[],
            linked_scenarios=[],
            suggested_acs=[],
            suggested_approach="",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "- **Branch:** `backlog/wg_004/e0-f1-s1-t9`" in md


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
    def test_creates_drafts_and_appends_rows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_config = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_config, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)

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
            assert "## Status: in-queue" in draft.read_text()
        backlog = (workspace / "BACKLOG.md").read_text()
        assert "E0-F1-S1-T2" in backlog
        assert "E0-F1-S1-T3" in backlog

    def test_promote_all_deletes_the_proposal_json_once_consumed(self, tmp_path: Path) -> None:
        """A consumed proposal must not linger as a permanent AWAITING_AMENDMENT_RECOVERY signal.

        Left on disk it classifies its source task as auto-recovering forever
        -- the classifier keeps promising a task-factory sweep that will never
        run again -- and ``write_proposal`` then refuses to emit a replacement,
        so the task can neither recover nor be re-proposed.
        """
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
        assert proposal_path(workspace, "E0-F1-S1-T1").is_file()

        promote_all_from_source(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            source_task_id="E0-F1-S1-T1",
        )

        assert not proposal_path(workspace, "E0-F1-S1-T1").exists()
        # And the blocker-resolver can now emit a replacement -- this would
        # raise ProposalError("Proposal already exists ...") otherwise.
        write_proposal(workspace, _sample_proposal(task_ids=["E0-F1-S1-T4"]))

    def test_promote_all_wires_every_task_before_deleting(self, tmp_path: Path) -> None:
        """Deleting mid-loop would strip the JSON that later promotions read for wiring."""
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
        promote_all_from_source(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            source_task_id="E0-F1-S1-T1",
        )

        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert source_text.count("[BLOCKED_PENDING_PROPOSAL]") == 2

    def test_consumed_check_is_a_noop_without_a_proposal(self, tmp_path: Path) -> None:
        """A draft with no originating proposal must not trip the consumed check."""
        workspace = _build_workspace(tmp_path)
        proposal_mod.delete_proposal_if_consumed(workspace, workspace / "backlog", None)

    def test_consumed_check_deletes_once_the_last_draft_is_declined(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2", "E0-F1-S1-T3"])
        write_proposal(workspace, proposal)
        _seed_draft(backlog_root, "E0-F1-S1-T2", "in-queue")
        _seed_draft(backlog_root, "E0-F1-S1-T3", "declined")

        proposal_mod.delete_proposal_if_consumed(workspace, backlog_root, proposal)

        assert not proposal_path(workspace, "E0-F1-S1-T1").exists()

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

    def test_skips_task_when_draft_file_already_exists(self, tmp_path: Path) -> None:
        """ADR-09: materialise is idempotent. A pre-existing draft is left alone."""
        workspace = _build_workspace(tmp_path)
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        existing_path = story_dir / "E0-F1-S1-T2.md"
        existing_path.write_text("# E0-F1-S1-T2: X\n\n## Status: proposed\n\nexisting\n")

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        # No draft was created -- the existing file classifies as PROPOSED and is skipped.
        assert drafts == []
        # Existing file content preserved (no overwrite).
        assert "existing" in existing_path.read_text()


class TestMaterialiseProposalIdempotent:
    """ADR-09: materialise_proposal is classify-aware and idempotent."""

    def _two_task_proposal(self) -> Proposal:
        return _sample_proposal(task_ids=["E0-F1-S1-T2", "E0-F1-S1-T3"])

    def test_skips_rejected_task_from_archive(self, tmp_path: Path) -> None:
        """Rejected archive -> classifier returns REJECTED -> materialise does NOT recreate."""
        from devbench.backlog.proposal import REJECTED_PROPOSAL_DIR_NAME

        workspace = _build_workspace(tmp_path)
        # Seed a per-draft reject archive for T2 at the canonical location.
        archive_dir = workspace / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "E0-F1-S1-T2-20260419T000000Z.md").write_text("archived draft body")

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert drafts == []
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        assert not (story_dir / "E0-F1-S1-T2.md").exists(), "rejected draft must not be resurrected"
        # BACKLOG.md must not have gained a row for the skipped task.
        assert "E0-F1-S1-T2" not in (workspace / "BACKLOG.md").read_text()

    @pytest.mark.parametrize("status_value", ["in-queue", "in-progress", "in-review", "blocked", "done", "declined"])
    def test_skips_promoted_done_declined_states(self, tmp_path: Path, status_value: str) -> None:
        """Any non-PROPOSED / non-UNMATERIALISED state -> skip, no recreation."""
        workspace = _build_workspace(tmp_path)
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        draft = story_dir / "E0-F1-S1-T2.md"
        draft.write_text(f"# E0-F1-S1-T2: X\n\n## Status: {status_value}\n\nbody\n")
        original_body = draft.read_text()

        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=_sample_proposal(task_ids=["E0-F1-S1-T2"]),
            repo="caylent-solutions/example",
        )
        assert drafts == []
        assert draft.read_text() == original_body

    def test_creates_remaining_tasks_when_others_skipped(self, tmp_path: Path) -> None:
        """Partial state: one task rejected (archive), the other materialises normally."""
        from devbench.backlog.proposal import REJECTED_PROPOSAL_DIR_NAME

        workspace = _build_workspace(tmp_path)
        # Reject T2 via archive; T3 should still materialise on the same call.
        archive_dir = workspace / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "E0-F1-S1-T2-20260419T000000Z.md").write_text("archived")

        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=self._two_task_proposal(),
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 1, "only T3 should be materialised; T2 stays rejected"
        assert drafts[0].name == "E0-F1-S1-T3.md"
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        assert not (story_dir / "E0-F1-S1-T2.md").exists()
        assert (story_dir / "E0-F1-S1-T3.md").exists()

    def test_double_call_is_noop(self, tmp_path: Path) -> None:
        """Calling materialise twice on the same fresh JSON: second call creates nothing."""
        workspace = _build_workspace(tmp_path)
        proposal = self._two_task_proposal()

        first = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(first) == 2

        # Second call: every task now classifies as PROPOSED (draft file has
        # Status: proposed). materialise must skip both and return empty.
        second = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert second == []

        # BACKLOG.md has exactly one row per task (no duplicates). Each row
        # mentions the id twice (id cell + file-path cell); count the
        # row-start form to measure row count.
        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert backlog_text.count("| E0-F1-S1-T2 |") == 1
        assert backlog_text.count("| E0-F1-S1-T3 |") == 1

    def test_rejected_draft_does_not_resurrect_on_re_materialise(self, tmp_path: Path) -> None:
        """End-to-end: reject a materialised draft, then re-materialise. Archive stays; no recreation."""
        workspace = _build_workspace(tmp_path)
        proposal = self._two_task_proposal()
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
            reason="superseded",
        )
        # T2's draft is archived now. Re-materialise the same JSON.
        result = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert result == [], "rejected draft must not be resurrected on re-materialise"
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        assert not (story_dir / "E0-F1-S1-T2.md").exists()


# ---------------------------------------------------------------------------
# materialise_proposal -- config-driven default status (AC-189-8)
# ---------------------------------------------------------------------------


class TestMaterialiseProposalDefaultStatus:
    """AC-189-8: materialise_proposal respects backlog.default_status_for_new_work_units."""

    def test_default_status_proposed_when_config_in_queue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When RUNTIME_CONFIG.backlog.default_status_for_new_work_units == 'in-queue',
        the draft file must have '## Status: in-queue' and the BACKLOG.md row must
        carry 'in-queue' (not 'proposed').
        """
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_config = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_config, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)

        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 1
        draft_text = drafts[0].read_text()
        assert "## Status: in-queue" in draft_text, "draft must carry in-queue status"
        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert "| in-queue |" in backlog_text, "BACKLOG.md row must carry in-queue status in a pipe-delimited cell"

    def test_default_status_draft_when_config_draft(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When RUNTIME_CONFIG.backlog.default_status_for_new_work_units == 'draft',
        the draft file must have '## Status: draft' and the BACKLOG.md row must
        carry 'draft'.
        """
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_config = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_config, "backlog", BacklogConfig(default_status_for_new_work_units="draft"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)

        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 1
        draft_text = drafts[0].read_text()
        assert "## Status: draft" in draft_text, "draft must carry draft status"
        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert "| draft |" in backlog_text, "BACKLOG.md row must carry draft status in a pipe-delimited cell"

    def test_backlog_index_row_status_matches_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The BACKLOG.md row's Status cell (column 4) matches the configured default status."""
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_config = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_config, "backlog", BacklogConfig(default_status_for_new_work_units="draft"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)

        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        backlog_text = (workspace / "BACKLOG.md").read_text()
        # Find the row for E0-F1-S1-T2 and check its Status cell (index 4 in pipe-split).
        for line in backlog_text.splitlines():
            if "E0-F1-S1-T2" in line and line.strip().startswith("|"):
                cells = [c.strip() for c in line.split("|")]
                if cells[1] == "E0-F1-S1-T2":
                    assert cells[4] == "draft", f"Expected Status cell to be 'draft', got {cells[4]!r}"
                    break
        else:
            pytest.fail("Row for E0-F1-S1-T2 not found in BACKLOG.md")

    @pytest.mark.parametrize("configured_status", ["in-queue", "draft"])
    def test_parametrized_status_values_written_correctly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        configured_status: str,
    ) -> None:
        """Both allowed config values produce the correct status in draft and BACKLOG.md."""
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_config = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(
            fake_config,
            "backlog",
            BacklogConfig(default_status_for_new_work_units=configured_status),
        )
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)

        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 1
        assert f"## Status: {configured_status}" in drafts[0].read_text()
        backlog_text = (workspace / "BACKLOG.md").read_text()
        for line in backlog_text.splitlines():
            if "E0-F1-S1-T2" in line and line.strip().startswith("|"):
                cells = [c.strip() for c in line.split("|")]
                if cells[1] == "E0-F1-S1-T2":
                    assert cells[4] == configured_status
                    break
        else:
            pytest.fail("Row for E0-F1-S1-T2 not found in BACKLOG.md")

    def test_invalid_default_status_raises_proposal_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When default_status_for_new_work_units has an invalid value, materialise_proposal
        raises ProposalError with an actionable message before writing any files.
        """
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_config = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(
            fake_config,
            "backlog",
            BacklogConfig(default_status_for_new_work_units="in_queue"),  # underscore -- invalid
        )
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)

        workspace = _build_workspace(tmp_path)
        prop = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        with pytest.raises(ProposalError, match=r"invalid value.*in_queue"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=prop,
                repo="caylent-solutions/example",
            )
        # No draft files should have been written before the error was raised.
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        draft_file = story_dir / "E0-F1-S1-T2.md"
        assert not draft_file.exists(), "No draft file should be written when config is invalid"


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

    def test_exclude_task_ids_filters_own_proposed_rows(self, tmp_path: Path) -> None:
        """ADR-09: re-materialising a proposal must not see its own proposed rows as blockers."""
        workspace = _build_workspace(tmp_path)
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "Mine",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )
        # Without the exclude set, this row makes the guard fire.
        assert _has_unresolved_proposals(workspace / "BACKLOG.md") is True
        # Excluding the same id returns False -- the row is ignored.
        assert _has_unresolved_proposals(workspace / "BACKLOG.md", exclude_task_ids=frozenset({"E0-F1-S1-T2"})) is False


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
        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.draft_path.is_file()
        # Draft status flipped.
        assert "## Status: in-queue" in result.draft_path.read_text()
        # Source task was the sole wired target (no affected_task_ids in fixture).
        assert result.wired_targets == ["E0-F1-S1-T1"]
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

    def test_promote_writes_blocked_pending_proposal_marker(self, tmp_path: Path) -> None:
        """The wiring write must include the ``[BLOCKED_PENDING_PROPOSAL]`` marker.

        The marker is what ``BacklogManager._auto_requeue_marker_dependents``
        reads on the source task to decide whether a subsequent ``mark_done``
        of the promoted dep should auto-unblock the source. Regressions here
        silently break the auto-requeue cascade without any CI signal.
        """
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
        )
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        source_text = source.read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in source_text
        # Both markers should be on the same audit comment line.
        for line in source_text.splitlines():
            if "[PROPOSAL_PROMOTED] E0-F1-S1-T2" in line:
                assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in line
                break
        else:
            raise AssertionError("did not find [PROPOSAL_PROMOTED] audit line")

    def test_promote_with_no_dep_on_source_skips_marker(self, tmp_path: Path) -> None:
        """``--no-dep-on-source`` implies no auto-requeue wiring; marker MUST be absent."""
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
        assert "[BLOCKED_PENDING_PROPOSAL]" not in source.read_text()


class TestPromoteCommentAuditSuffix:
    """ADR-11: _append_promote_comment optional audit_suffix kwarg."""

    def test_append_promote_comment_has_no_suffix_when_not_supplied(self, tmp_path: Path) -> None:
        """Back-compat pin: today's byte-identical marker line."""
        from devbench.backlog.proposal import _append_promote_comment

        source = tmp_path / "src.md"
        source.write_text("# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nx\n")
        _append_promote_comment(source, "E0-F1-S1-T1", "E0-F1-S1-T2")
        text = source.read_text()
        assert (
            "[PROPOSAL_PROMOTED] E0-F1-S1-T2 promoted and wired as dependency of E0-F1-S1-T1. "
            "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2"
        ) in text
        assert "auto-accepted" not in text

    def test_append_promote_comment_appends_audit_suffix_when_supplied(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _append_promote_comment

        source = tmp_path / "src.md"
        source.write_text("# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nx\n")
        _append_promote_comment(
            source,
            "E0-F1-S1-T1",
            "E0-F1-S1-T2",
            audit_suffix="(auto-accepted via task_factory.auto_accept_proposals=true)",
        )
        text = source.read_text()
        assert (
            "[PROPOSAL_PROMOTED] E0-F1-S1-T2 promoted and wired as dependency of E0-F1-S1-T1."
            " (auto-accepted via task_factory.auto_accept_proposals=true) "
            "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2"
        ) in text


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


class TestProposalAffectedTaskIds:
    """ADR-10: Proposal.affected_task_ids schema."""

    def _base_payload(self) -> dict:
        return {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-04-20T00:00:00Z",
            "rejection_reason": "test fixture",
            "proposed_tasks": [],
        }

    def test_from_dict_accepts_missing_field_defaults_empty(self) -> None:
        payload = self._base_payload()
        assert "affected_task_ids" not in payload
        p = Proposal.from_dict(payload)
        assert p.affected_task_ids == []

    def test_from_dict_accepts_empty_list(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": []}
        assert Proposal.from_dict(payload).affected_task_ids == []

    def test_from_dict_accepts_single_entry(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F1-S1-T9"]}
        assert Proposal.from_dict(payload).affected_task_ids == ["E0-F1-S1-T9"]

    def test_from_dict_accepts_multiple_entries(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F2-S1-T1", "E0-F3-S1-T1"]}
        assert Proposal.from_dict(payload).affected_task_ids == ["E0-F2-S1-T1", "E0-F3-S1-T1"]

    def test_from_dict_rejects_non_list(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": "E0-F1-S1-T9"}
        with pytest.raises(ValueError, match="must be a list"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_non_string_entry(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": [123]}
        with pytest.raises(ValueError, match="must be a string"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_empty_string_entry(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": [""]}
        with pytest.raises(ValueError, match="empty entry"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_duplicate_entries(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F2-S1-T1", "E0-F2-S1-T1"]}
        with pytest.raises(ValueError, match="duplicate entry"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_source_task_id_duplicated_in_affected(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F1-S1-T1"]}
        with pytest.raises(ValueError, match="duplicates source_task_id"):
            Proposal.from_dict(payload)

    def test_from_dict_strips_whitespace(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["  E0-F2-S1-T1  "]}
        assert Proposal.from_dict(payload).affected_task_ids == ["E0-F2-S1-T1"]

    def test_to_dict_round_trip_preserves_order(self) -> None:
        original = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="t",
            rejection_reason="r",
            proposed_tasks=[],
            affected_task_ids=["E0-F3-S1-T1", "E0-F2-S1-T1"],
        )
        reloaded = Proposal.from_dict(original.to_dict())
        assert reloaded.affected_task_ids == ["E0-F3-S1-T1", "E0-F2-S1-T1"]

    def test_to_dict_always_emits_key_even_when_empty(self) -> None:
        p = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="t",
            rejection_reason="r",
            proposed_tasks=[],
        )
        out = p.to_dict()
        assert "affected_task_ids" in out
        assert out["affected_task_ids"] == []


class TestPromoteProposalAffectedWiring:
    """ADR-10: promote_proposal wires [source] + affected_task_ids; fail-fast on missing targets."""

    def _build_workspace_with_peers(self, tmp_path: Path, peer_ids: list[str]) -> Path:
        """Build a workspace with source task T1 + one peer task per id in peer_ids."""
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1"))
        for peer_id in peer_ids:
            (story_dir / f"{peer_id}.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id=peer_id))
            _append_backlog_row(
                tmp_path / "BACKLOG.md",
                _render_backlog_row(
                    peer_id,
                    f"Peer {peer_id}",
                    "blocked",
                    "caylent-solutions/example",
                    f"backlog/E0/E0-F1/E0-F1-S1/{peer_id}.md",
                ),
            )
        return tmp_path

    def _materialise_fixture(self, workspace: Path, peer_ids: list[str]) -> None:
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-20T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach=(
                        "Context: ADR-10 multi-target wiring unit-test fixture. "
                        "Scope: src/x.py and a matching unit test. "
                        "TDD approach: 1. RED -- write failing assertion. "
                        "2. GREEN -- minimal fix. 3. REFACTOR -- no behaviour change. "
                        "Verify: make lint && make test-unit both exit zero."
                    ),
                )
            ],
            affected_task_ids=peer_ids,
        )
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

    def test_promote_wires_source_only_when_affected_empty(self, tmp_path: Path) -> None:
        """Back-compat: empty affected_task_ids wires only the source task."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=[])
        self._materialise_fixture(workspace, peer_ids=[])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == ["E0-F1-S1-T1"]

    def test_promote_wires_source_plus_affected_when_populated(self, tmp_path: Path) -> None:
        """ADR-10 primary behaviour: marker + dep land on source + every affected entry."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=["E0-F1-S1-T3", "E0-F1-S1-T4"])
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T3", "E0-F1-S1-T4"])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == ["E0-F1-S1-T1", "E0-F1-S1-T3", "E0-F1-S1-T4"]
        # Marker lands on every target's Comments.
        for tid in result.wired_targets:
            text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / f"{tid}.md").read_text()
            assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in text

    def test_promote_fails_fast_when_affected_target_missing_from_backlog(self, tmp_path: Path) -> None:
        """Missing peer ID raises before any write so the source is never half-wired."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=[])
        # peer T99 is in affected_task_ids but does NOT exist in backlog.
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T99"])

        with pytest.raises(ProposalError, match=r"E0-F1-S1-T99.*not found in backlog"):
            promote_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
            )
        # Source task must NOT have been partially wired -- no marker written.
        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" not in source_text

    def test_promote_no_dep_on_source_still_wires_affected_deps(self, tmp_path: Path) -> None:
        """`--no-dep-on-source` skips only the source; affected entries still get their row + marker."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=["E0-F1-S1-T3"])
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T3"])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            dep_on_source=False,
        )
        assert result.wired_targets == ["E0-F1-S1-T3"]
        # Source did NOT get the marker.
        source = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL]" not in source
        # Peer DID get the marker.
        peer = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T3.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in peer

    def test_promote_wires_in_declared_order(self, tmp_path: Path) -> None:
        """Target list preserves declared order so audit reads naturally."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=["E0-F1-S1-T4", "E0-F1-S1-T3"])
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T4", "E0-F1-S1-T3"])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        # Source first, then affected in declared order.
        assert result.wired_targets == ["E0-F1-S1-T1", "E0-F1-S1-T4", "E0-F1-S1-T3"]


class TestAddDepCoreHelper:
    """ADR-10: add_dep() core helper (called by cmd_add_dep)."""

    def _workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1"))
        (story_dir / "E0-F1-S1-T2.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T2"))
        _append_backlog_row(
            tmp_path / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "Fix",
                "in-queue",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )
        return tmp_path

    def test_add_dep_writes_row_and_marker_when_absent(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        wrote = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
            reason="ADR-10 smoke test",
        )
        assert wrote is True
        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "| E0-F1-S1-T2 |" in source_text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in source_text
        assert "[WU_WIRED]" in source_text
        assert "[agent/operator]" in source_text
        assert "ADR-10 smoke test" in source_text

    def test_add_dep_is_idempotent_on_repeated_call(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        first = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        second = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        assert first is True
        assert second is False
        # Marker appears exactly once.
        text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert text.count("[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2") == 1

    def test_add_dep_fails_fast_when_blocker_missing_from_backlog(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        with pytest.raises(ProposalError, match="blocker task 'E0-F1-S1-T99' not found"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T99",
            )

    def test_add_dep_rejects_self_wire(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        with pytest.raises(ProposalError, match="cannot be the same task"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T1",
            )

    def test_add_dep_fails_fast_when_blocker_is_done(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        # Flip T2 to done manually.
        t2 = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T2.md"
        t2.write_text(t2.read_text().replace("## Status: blocked", "## Status: done"))
        idx = workspace / "BACKLOG.md"
        idx.write_text(
            idx.read_text().replace(
                "| E0-F1-S1-T2 | Fix | Task | in-queue |",
                "| E0-F1-S1-T2 | Fix | Task | done |",
            )
        )
        with pytest.raises(ProposalError, match="already terminal"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T2",
            )


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


# ---------------------------------------------------------------------------
# Proposal-lifecycle observability + cleanup (ADR-08 slices A / E / F / H).
# ---------------------------------------------------------------------------


def _draft_body(status: str, task_id: str = "E0-F1-S1-T1") -> str:
    """Return a minimal draft-file body with the given ## Status value.

    The heading uses ``task_id`` so ``BacklogParser.parse_work_unit_file``
    recovers the same ID the enclosing test is using.
    """
    return f"# {task_id}: X\n\n## Status: {status}\n\n## Description\n\nx\n"


def _seed_draft(backlog_root: Path, task_id: str, status: str) -> Path:
    """Create a draft .md for ``task_id`` with the given status under a story dir."""
    from devbench.backlog.proposal import _extract_story_id, _story_dir

    story_dir = _story_dir(backlog_root, _extract_story_id(task_id))
    story_dir.mkdir(parents=True, exist_ok=True)
    draft = story_dir / f"{task_id}.md"
    draft.write_text(_draft_body(status, task_id=task_id))
    return draft


class TestClassifyProposedTask:
    """Every ``ProposalTaskState`` value has a deterministic fixture scenario."""

    def test_unmaterialised_when_no_draft_and_no_archive(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            ProposalTaskState,
            classify_proposed_task,
        )

        (tmp_path / "backlog").mkdir()
        state = classify_proposed_task(tmp_path / "backlog", tmp_path, "E0-F1-S1-T9")
        assert state is ProposalTaskState.UNMATERIALISED

    def test_rejected_when_archive_entry_exists(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            REJECTED_PROPOSAL_DIR_NAME,
            ProposalTaskState,
            classify_proposed_task,
        )

        (tmp_path / "backlog").mkdir()
        archive_dir = tmp_path / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T9-20260101T000000Z.md").write_text("archived")
        state = classify_proposed_task(tmp_path / "backlog", tmp_path, "E0-F1-S1-T9")
        assert state is ProposalTaskState.REJECTED

    @pytest.mark.parametrize(
        "status,expected_state",
        [
            ("proposed", "PROPOSED"),
            ("done", "DONE"),
            ("declined", "DECLINED"),
            ("in-queue", "PROMOTED"),
            ("in-progress", "PROMOTED"),
            ("in-review", "PROMOTED"),
            ("blocked", "PROMOTED"),
        ],
    )
    def test_draft_status_maps_to_state(self, tmp_path: Path, status: str, expected_state: str) -> None:
        from devbench.backlog.proposal import (
            ProposalTaskState,
            classify_proposed_task,
        )

        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir()
        _seed_draft(backlog_root, "E0-F1-S1-T2", status)
        state = classify_proposed_task(backlog_root, tmp_path, "E0-F1-S1-T2")
        assert state is ProposalTaskState[expected_state]

    def test_malformed_draft_with_no_status_line_treated_as_promoted(self, tmp_path: Path) -> None:
        """Defensive: a draft missing its Status line is still classified (as PROMOTED)."""
        from devbench.backlog.proposal import (
            ProposalTaskState,
            _extract_story_id,
            _story_dir,
            classify_proposed_task,
        )

        backlog_root = tmp_path / "backlog"
        story_dir = _story_dir(backlog_root, _extract_story_id("E0-F1-S1-T2"))
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T2.md").write_text("# header only\n")
        state = classify_proposed_task(backlog_root, tmp_path, "E0-F1-S1-T2")
        assert state is ProposalTaskState.PROMOTED


class TestClassifyBlockedTask:
    """ADR-10: classify_blocked_task returns one of the six blocked-task states."""

    def _workspace_with_markers(self, tmp_path: Path, marker_target_status_pairs: list[tuple[str, str]]) -> Path:
        """Build a workspace where E0-F1-S1-T1 carries markers for each (id, status) pair.

        Every marker target gets a corresponding row in BACKLOG.md with the
        given status. The blocked-source task itself (T1) has ``blocked``
        status + one comment line per marker target.
        """
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        # Source task with the markers in its Comments section.
        comments_block = "\n## Comments\n\n" + "\n".join(
            f"[2026-04-20 00:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] {tid} "
            f"promoted and wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] {tid}"
            for tid, _ in marker_target_status_pairs
        )
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n" + comments_block
        )

        # Marker target files and BACKLOG.md rows.
        rows = ["| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"]
        for tid, status in marker_target_status_pairs:
            (story_dir / f"{tid}.md").write_text(f"# {tid}: X\n\n## Status: {status}\n")
            rows.append(f"| {tid} | Marker | Task | {status} | None | r | `backlog/E0/E0-F1/E0-F1-S1/{tid}.md` |")

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
        )
        return tmp_path

    def test_task_with_all_active_markers_is_auto_clearing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(
            tmp_path, [("E0-F1-S1-T2", "in-queue"), ("E0-F1-S1-T3", "in-progress")]
        )
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_task_with_mixed_markers_is_auto_clearing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "done"), ("E0-F1-S1-T3", "in-queue")])
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_task_with_all_terminal_markers_is_auto_clearing(self, tmp_path: Path) -> None:
        """Issue #200 / AC-200-1: all-terminal markers must return AUTO_CLEARING_VIA_PROPOSAL.

        Before the fix this returned OPERATOR_ACTION_REQUIRED via fallthrough.
        The fix: _classify_with_markers always returns AUTO_CLEARING_VIA_PROPOSAL
        for any non-empty, non-unknown, non-HOLD marker set. The orchestrator's
        cascade (_auto_requeue_marker_dependents) is responsible for actually
        flipping the task to in-queue once all markers are terminal.
        """
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "done"), ("E0-F1-S1-T3", "declined")])
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_task_with_unknown_marker_id_is_operator_action_required(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "in-queue")])
        # Add a second marker pointing at an ID with no backlog row.
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        source_file.write_text(
            source_file.read_text()
            + "\n[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99\n"
        )
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_task_with_no_markers_is_operator_action_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reach the empty-markers branch without depending on BacklogParser internals."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        source = tmp_path / "fake.md"
        source.write_text("# fake\n\n## Status: blocked\n")
        monkeypatch.setattr(proposal_mod, "_find_source_task_file", lambda *a, **kw: source)

        class _NoMarkers:
            def _extract_pending_proposal_markers(self, _p):
                return set()

            def _parse_backlog_rows(self, _path):
                return []

        monkeypatch.setattr(proposal_mod, "BacklogManager", _NoMarkers)
        state = classify_blocked_task(tmp_path / "backlog", tmp_path / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_task_not_in_backlog_is_operator_action_required(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
        )
        state = classify_blocked_task(tmp_path / "backlog", tmp_path / "BACKLOG.md", "E0-F1-S1-T99")
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_missing_backlog_index_is_operator_action_required(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-10: if _parse_backlog_rows raises FileNotFoundError, classifier defaults to operator-action-required."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source = story_dir / "E0-F1-S1-T1.md"
        source.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        )
        monkeypatch.setattr(proposal_mod, "_find_source_task_file", lambda *a, **kw: source)

        class _Exploding:
            def _extract_pending_proposal_markers(self, _p):
                return {"E0-F1-S1-T2"}

            def _parse_backlog_rows(self, _path):
                raise FileNotFoundError("forced")

        monkeypatch.setattr(proposal_mod, "BacklogManager", _Exploding)
        state = classify_blocked_task(backlog_dir, tmp_path / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_task_with_hold_marker_target_is_blocked_on_held(self, tmp_path: Path) -> None:
        """HOLD-target marker -> BLOCKED_ON_HELD (not OPERATOR_ACTION_REQUIRED).

        HOLD is non-terminal so the cascade cannot fire; HOLD will not
        clear without operator action so AUTO_CLEARING_VIA_PROPOSAL is
        a misclassification. The 6-state classifier surfaces this as
        BLOCKED_ON_HELD so operators see the dependency is on a held unit.
        """
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "hold")])
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.BLOCKED_ON_HELD

    def test_task_with_hold_target_among_active_targets_is_blocked_on_held(self, tmp_path: Path) -> None:
        """HOLD precedence: any HOLD target wins over in-queue / in-progress siblings.

        Even though one of the marker targets is in-queue (would normally
        return AUTO_CLEARING_VIA_PROPOSAL), the presence of a HOLD target
        forces BLOCKED_ON_HELD because the cascade is gated on every
        target reaching terminal and HOLD is non-terminal.
        """
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "in-queue"), ("E0-F1-S1-T3", "hold")])
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.BLOCKED_ON_HELD


class TestAddDepBlockedMissing:
    def test_add_dep_raises_when_blocked_task_missing_from_backlog(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T2 | Fix | Task | in-queue | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        (story / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Fix\n\n## Status: in-queue\n\n## Description\n\nx\n")
        with pytest.raises(ProposalError, match=r"blocked task 'E0-F1-S1-T99' not found"):
            add_dep(
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T99",
                blocker_task_id="E0-F1-S1-T2",
            )


class TestManualDepCommentNoCommentsSection:
    def test_append_manual_dep_creates_comments_section_when_missing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _append_manual_dep_comment

        f = tmp_path / "x.md"
        f.write_text("# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nno comments section yet\n")
        _append_manual_dep_comment(f, "E0-F1-S1-T1", "E0-F1-S1-T2", "slice I coverage")
        text = f.read_text()
        assert "## Comments" in text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in text

    def test_append_manual_dep_appends_to_existing_comments_section(self, tmp_path: Path) -> None:
        """Line 1015: when the file already has ## Comments, the entry is appended to it."""
        from devbench.backlog.proposal import _append_manual_dep_comment

        f = tmp_path / "y.md"
        f.write_text(
            "# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nx\n\n"
            "## Comments\n\n[2026-04-19 09:00 UTC] [agent/other] prior entry\n"
        )
        _append_manual_dep_comment(f, "E0-F1-S1-T1", "E0-F1-S1-T2", "")
        text = f.read_text()
        # Prior entry still there + the new marker.
        assert "prior entry" in text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in text
        # Only ONE "## Comments" header (we did not append a duplicate section).
        assert text.count("## Comments") == 1


class TestFindOriginatingProposalMiss:
    def test_find_originating_proposal_returns_none_when_no_match(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _find_originating_proposal

        assert _find_originating_proposal(tmp_path, "E0-F1-S1-T99") is None


class TestPromoteProposalSourceFileMissing:
    def test_promote_loops_through_missing_source_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Continue-branch coverage when _find_source_task_file returns None during the wire loop."""
        from devbench.backlog import proposal as proposal_mod

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
        # Single-task proposal means the fail-fast loop skips _find_source_task_file
        # (targets[1:] is empty); the wire loop then calls it once for the source.
        # Patch it to return None so the continue branch is exercised.
        monkeypatch.setattr(proposal_mod, "_find_source_task_file", lambda *a, **kw: None)
        result = proposal_mod.promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == []


class TestMaterialiseProposalThinApproachRefusal:
    """ADR-08 slice H: materialise refuses thin ``suggested_approach`` fail-fast."""

    def _mini_workspace(self, tmp_path: Path, approach: str) -> tuple[Path, Proposal]:
        """Build a tmp workspace with a single proposal carrying ``approach``."""
        from devbench.backlog.proposal import (
            Proposal,
            ProposedTask,
        )

        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(_draft_body("blocked"))
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="scope",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix",
                    files_to_own=["src/a.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach=approach,
                )
            ],
        )
        return tmp_path, proposal

    def test_empty_approach_raises_proposal_error(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, materialise_proposal

        workspace, proposal = self._mini_workspace(tmp_path, approach="")
        with pytest.raises(ProposalError, match="suggested_approach too terse"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="r",
            )

    def test_below_threshold_approach_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, materialise_proposal

        workspace, proposal = self._mini_workspace(tmp_path, approach="RED. GREEN. Verify.")
        with pytest.raises(ProposalError, match="suggested_approach too terse"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="r",
            )

    def test_at_threshold_passes(self, tmp_path: Path) -> None:
        """A single-char-above-threshold approach materialises successfully."""
        from devbench.backlog.proposal import (
            _SUGGESTED_APPROACH_MIN_CHARS,
            materialise_proposal,
        )

        padded = "x" * (_SUGGESTED_APPROACH_MIN_CHARS + 1)
        workspace, proposal = self._mini_workspace(tmp_path, approach=padded)
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="r",
        )
        assert len(drafts) == 1

    def test_thin_approach_error_names_task_id(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, materialise_proposal

        workspace, proposal = self._mini_workspace(tmp_path, approach="nope")
        with pytest.raises(ProposalError, match="E0-F1-S1-T2"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="r",
            )


class TestRejectProposalArgumentForms:
    """ADR-08 slice E: the two mutually-exclusive rejection forms."""

    def test_neither_form_supplied_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        with pytest.raises(ProposalError, match="exactly one of"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                reason="r",
            )

    def test_both_forms_supplied_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        with pytest.raises(ProposalError, match="not both"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
                unmaterialised_source_id="E0-F1-S1-T1",
                reason="r",
            )

    def test_empty_reason_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        with pytest.raises(ProposalError, match="non-empty reason"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
                reason="",
            )


class TestRejectUnmaterialisedProposal:
    """The new ``--unmaterialised <source-id>`` form."""

    def _mini_workspace(self, tmp_path: Path) -> Path:
        """Build a workspace with a source task + one proposal JSON (un-materialised drafts)."""
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            Proposal,
            ProposedTask,
            write_proposal,
        )

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(_draft_body("blocked", task_id="E0-F1-S1-T1"))
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        (tmp_path / PROPOSAL_DIR_NAME).mkdir(parents=True, exist_ok=True)
        write_proposal(
            tmp_path,
            Proposal(
                source_task_id="E0-F1-S1-T1",
                generated_at="2026-04-19T00:00:00Z",
                rejection_reason="scope",
                proposed_tasks=[
                    ProposedTask(
                        suggested_id="E0-F1-S1-T2",
                        title="Fix",
                        files_to_own=["src/a.py"],
                        linked_scenarios=["SC-01"],
                        suggested_acs=["AC-001 fix"],
                        suggested_approach=(
                            "Context: unit test. Scope: src/a.py. "
                            "TDD approach: 1. RED. 2. GREEN. 3. REFACTOR no-op. "
                            "Verify: make lint && make test-unit exit zero."
                        ),
                    )
                ],
            ),
        )
        return tmp_path

    def test_happy_path_archives_json_and_audits_source(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            REJECTED_PROPOSAL_DIR_NAME,
            reject_proposal,
        )

        workspace = self._mini_workspace(tmp_path)
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            unmaterialised_source_id="E0-F1-S1-T1",
            reason="redundant with T3",
        )

        # Archive created with the '-unmaterialised-' infix.
        assert archive is not None
        assert archive.is_file()
        assert archive.parent == workspace / REJECTED_PROPOSAL_DIR_NAME
        assert "unmaterialised" in archive.name

        # Live JSON removed.
        assert not (workspace / PROPOSAL_DIR_NAME / "E0-F1-S1-T1.json").exists()

        # Source task has the audit comment.
        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "[PROPOSAL_JSON_REJECTED]" in source_text
        assert "redundant with T3" in source_text

    def test_refuses_when_any_task_already_materialised(self, tmp_path: Path) -> None:
        """If any proposed task has a draft .md, per-source rejection must refuse."""
        from devbench.backlog.proposal import ProposalError, reject_proposal

        workspace = self._mini_workspace(tmp_path)
        # Materialise the T2 draft out-of-band so it's in PROPOSED state.
        _seed_draft(workspace / "backlog", "E0-F1-S1-T2", "proposed")

        with pytest.raises(ProposalError, match="already in state"):
            reject_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                unmaterialised_source_id="E0-F1-S1-T1",
                reason="r",
            )

    def test_refuses_when_json_missing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        (tmp_path / "backlog").mkdir()
        (tmp_path / "BACKLOG.md").write_text("# Backlog\n")
        with pytest.raises(ProposalError, match="No proposal JSON found"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                unmaterialised_source_id="E0-F1-S1-T1",
                reason="r",
            )

    def test_happy_path_appends_to_existing_comments_section(self, tmp_path: Path) -> None:
        """When the source already has ``## Comments``, the audit line is appended to it."""
        from devbench.backlog.proposal import REJECTED_PROPOSAL_DIR_NAME, reject_proposal

        workspace = self._mini_workspace(tmp_path)
        source_md = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        existing = source_md.read_text() + "\n## Comments\n\n[2026-04-18 14:00 UTC] [agent/test] prior\n"
        source_md.write_text(existing)

        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            unmaterialised_source_id="E0-F1-S1-T1",
            reason="appended",
        )
        assert archive is not None
        assert archive.parent == workspace / REJECTED_PROPOSAL_DIR_NAME
        final_text = source_md.read_text()
        assert "prior" in final_text
        assert "[PROPOSAL_JSON_REJECTED]" in final_text
        assert "appended" in final_text


class TestRejectProposalMarkerStrip:
    """ADR-08 slice F: per-draft rejection strips the marker + invokes cascade."""

    def _workspace_with_marker(
        self,
        tmp_path: Path,
        *,
        second_draft_status: str = "done",
    ) -> tuple[Path, Path]:
        """Build a workspace where source T1 has markers for T2 + T3.

        T2 is the draft we'll reject; T3 is seeded with ``second_draft_status``.
        Returns (workspace, source_file).
        """
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            Proposal,
            ProposedTask,
            write_proposal,
        )

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n"
            "| ID | Title | Status |\n|----|-------|--------|\n"
            "| E0-F1-S1-T2 | (auto) | proposed |\n"
            "| E0-F1-S1-T3 | (auto) | proposed |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 x\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `x.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] AC complete\n\n"
            "## Comments\n\n"
            "[2026-04-19 14:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T2 "
            "promoted and wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n\n"
            "[2026-04-19 14:01 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T3 "
            "promoted and wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n"
        )
        _seed_draft(backlog_dir, "E0-F1-S1-T2", "in-queue")
        _seed_draft(backlog_dir, "E0-F1-S1-T3", second_draft_status)
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
            "|------|-------|------|-------------|----------|---------|----------|\n"
            "| E0 | Ex | 0 | 0 | 1 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0 | Ex | Epic | in-queue | None | r | `backlog/E0.md` |\n"
            "| E0-F1-S1-T1 | Src | Task | blocked | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Dep1 | Task | in-queue | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
            f"| E0-F1-S1-T3 | Dep2 | Task | {second_draft_status} | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T3.md` |\n"
        )
        (backlog_dir / "E0.md").write_text("# E0: Ex\n\n## Status: in-queue\n")
        # Seed a proposal JSON so _find_originating_source_task resolves T2's source.
        (tmp_path / PROPOSAL_DIR_NAME).mkdir(parents=True, exist_ok=True)
        write_proposal(
            tmp_path,
            Proposal(
                source_task_id="E0-F1-S1-T1",
                generated_at="2026-04-19T00:00:00Z",
                rejection_reason="scope",
                proposed_tasks=[
                    ProposedTask(
                        suggested_id="E0-F1-S1-T2",
                        title="Dep1",
                        files_to_own=["src/a.py"],
                        linked_scenarios=["SC-01"],
                        suggested_acs=["AC-001 fix"],
                        suggested_approach=(
                            "Context: unit test fixture. Scope: a. "
                            "TDD: 1 RED 2 GREEN 3 REFACTOR no-op. "
                            "Verify: make lint && make test-unit all exit zero."
                        ),
                    ),
                    ProposedTask(
                        suggested_id="E0-F1-S1-T3",
                        title="Dep2",
                        files_to_own=["src/b.py"],
                        linked_scenarios=["SC-02"],
                        suggested_acs=["AC-002 fix"],
                        suggested_approach=(
                            "Context: unit test fixture. Scope: b. "
                            "TDD: 1 RED 2 GREEN 3 REFACTOR no-op. "
                            "Verify: make lint && make test-unit all exit zero."
                        ),
                    ),
                ],
            ),
        )
        return tmp_path, source_file

    def test_rejecting_draft_strips_marker_and_cascade_fires(self, tmp_path: Path) -> None:
        """T3 is already done; reject T2 -> cascade sees T1's remaining markers all terminal -> requeue."""
        from devbench.backlog.proposal import reject_proposal

        workspace, source_file = self._workspace_with_marker(tmp_path, second_draft_status="done")

        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="redundant",
        )

        updated = source_file.read_text()
        # T2 marker stripped.
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" not in updated
        # T3 marker preserved.
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3" in updated
        # Cascade fired: source auto-flipped because remaining marker (T3) is terminal.
        assert "## Status: in-queue" in updated
        assert "[AUTO_UNBLOCKED]" in updated

    def test_rejecting_draft_when_other_marker_still_active_keeps_blocked(self, tmp_path: Path) -> None:
        """T3 is still in-queue (non-terminal); reject T2 -> cascade abstains."""
        from devbench.backlog.proposal import reject_proposal

        workspace, source_file = self._workspace_with_marker(tmp_path, second_draft_status="in-queue")

        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="redundant",
        )

        updated = source_file.read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" not in updated
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3" in updated
        # T3 still non-terminal -> source stays blocked.
        assert "## Status: blocked" in updated
        assert "[AUTO_UNBLOCKED]" not in updated

    def test_strip_marker_on_source_with_no_matching_marker_is_noop(self, tmp_path: Path) -> None:
        """Calling reject for a task whose marker was never on the source is safe."""
        from devbench.backlog.proposal import _strip_pending_proposal_marker

        source_file = tmp_path / "src.md"
        original = "# x\n\n## Comments\n\n[some comment]\n"
        source_file.write_text(original)
        _strip_pending_proposal_marker(source_file, "E0-F1-S1-T99")
        assert source_file.read_text() == original


class TestClassifyBlockedTaskAwaitingRecovery:
    """6-state classifier: AWAITING_AMENDMENT_RECOVERY signals.

    No ``[BLOCKED_PENDING_PROPOSAL]`` marker is present, but the
    orchestrator's loop has left a recovery artefact on disk. The
    task should classify as ``AWAITING_AMENDMENT_RECOVERY`` -- not
    ``OPERATOR_ACTION_REQUIRED`` -- so the operator does not get
    paged on a transient state devbench will resolve itself.
    """

    def _workspace_no_marker(self, tmp_path: Path) -> Path:
        """Source task with no markers; recovery signals supplied by tests."""
        from datetime import UTC, datetime

        del datetime, UTC
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n"
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        return tmp_path

    def test_pending_proposal_json_classifies_recovery(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        proposals_dir = workspace / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text("{}")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_rejected_amendment_archive_classifies_recovery(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        archive_dir = workspace / ".devbench" / "rejected-requests"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-20260501T120000Z.json").write_text("{}")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_recent_recovery_audit_comment_classifies_recovery(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        # Append a recent recovery-shaped [BLOCKED] line to the source file.
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        source_file.write_text(
            source_file.read_text()
            + f"\n[{ts}] [agent/orchestrator] [BLOCKED] amendment-reject for T1 -- emitting fix proposal\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_stale_audit_comment_falls_through_to_operator_action_required(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime, timedelta

        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        old_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M UTC")
        source_file.write_text(
            source_file.read_text() + f"\n[{old_ts}] [agent/orchestrator] [BLOCKED] amendment-reject (long ago)\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_audit_from_non_recovery_agent_falls_through_to_operator_action_required(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        source_file.write_text(
            source_file.read_text() + f"\n[{ts}] [agent/some-other-agent] [BLOCKED] amendment-reject\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_no_signal_classifies_operator_action_required(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_no_workspace_root_falls_back_to_operator_action_required(self, tmp_path: Path) -> None:
        # Older callers that pass only backlog_root + backlog_index still
        # get operator-action-required (formerly the two-state fallback).
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
        )

        workspace = self._workspace_no_marker(tmp_path)
        proposals_dir = workspace / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text("{}")
        # Without workspace_root, recovery signals are invisible.
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED


class TestRecoverySignalForTask:
    """``recovery_signal_for_task`` names the AWAITING_AMENDMENT_RECOVERY signal source for the report renderer."""

    def test_pending_proposal_named(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import recovery_signal_for_task

        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-T1.json").write_text("{}")
        signal = recovery_signal_for_task(tmp_path, "E0-T1")
        assert "pending proposal" in signal
        assert ".devbench/proposals/E0-T1.json" in signal

    def test_rejected_archive_named(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import recovery_signal_for_task

        archive_dir = tmp_path / ".devbench" / "rejected-requests"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-T1-20260501T000000Z.json").write_text("{}")
        signal = recovery_signal_for_task(tmp_path, "E0-T1")
        assert "rejected-requests" in signal

    def test_audit_comment_when_neither_artefact_present(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import recovery_signal_for_task

        signal = recovery_signal_for_task(tmp_path, "E0-T1")
        # No artefact on disk, so the function returns the audit-comment
        # fallback string. The classifier itself is responsible for
        # deciding whether the audit comment actually qualifies; this
        # helper just supplies the human-facing label.
        assert "audit comment" in signal


class TestRecentRecoveryAuditCommentEdgeCases:
    """Direct coverage for ``_recent_recovery_audit_comment`` branches."""

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _recent_recovery_audit_comment

        assert _recent_recovery_audit_comment(tmp_path / "absent.md", datetime.now(UTC), 300) is False

    def test_blocked_pending_proposal_lines_are_skipped(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _recent_recovery_audit_comment

        wu = tmp_path / "wu.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(f"## Comments\n\n[{ts}] [agent/orchestrator] [BLOCKED_PENDING_PROPOSAL] E0-T9\n")
        # Only marker-style line; helper must return False (cascade state, not recovery).
        assert _recent_recovery_audit_comment(wu, now, 300) is False

    def test_malformed_timestamp_is_skipped(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _recent_recovery_audit_comment

        wu = tmp_path / "wu.md"
        # Regex matches the shape but the values are not a valid
        # calendar date, so strptime raises ValueError. The helper
        # must continue past it rather than crashing.
        wu.write_text("## Comments\n\n[9999-99-99 99:99 UTC] [agent/orchestrator] [BLOCKED] amendment-reject\n")
        assert _recent_recovery_audit_comment(wu, datetime(2026, 5, 1, 12, 0, tzinfo=UTC), 300) is False


# ---------------------------------------------------------------------------
# Issue #211: agent-tag hyphen vs underscore parity
# ---------------------------------------------------------------------------


class TestRecoveryAgentTagHyphenUnderscoreParity:
    """Issue #211: ``_recent_recovery_audit_comment`` must accept both
    hyphen-form (``agent/manifest-amender``) and underscore-form
    (``agent/manifest_amender``) audit-row agent tags.

    ``_RECOVERY_AGENT_TAGS`` enumerates the canonical underscore form,
    but ``amendment.py::AMENDER_AGENT_ID = "agent/manifest-amender"``
    and other writers emit the hyphen form. Before the fix the hyphen
    form silently failed the frozenset membership check, causing
    ``classify_blocked_task`` to fall through to
    ``OPERATOR_ACTION_REQUIRED`` for rejected-amendment audits it
    should have classified as ``AWAITING_AMENDMENT_RECOVERY``.
    """

    @pytest.mark.parametrize(
        "agent_tag",
        [
            pytest.param("agent/orchestrator", id="orchestrator-no-dashes"),
            pytest.param("agent/blocker_resolver", id="blocker_resolver-underscore"),
            pytest.param("agent/blocker-resolver", id="blocker-resolver-hyphen"),
            pytest.param("agent/manifest_amender", id="manifest_amender-underscore"),
            pytest.param("agent/manifest-amender", id="manifest-amender-hyphen"),
            pytest.param("agent/backlog_manager", id="backlog_manager-underscore"),
            pytest.param("agent/backlog-manager", id="backlog-manager-hyphen"),
        ],
    )
    def test_recovery_audit_helper_accepts_both_forms(self, tmp_path: Path, agent_tag: str) -> None:
        """Direct unit test on ``_recent_recovery_audit_comment``: both
        hyphen and underscore forms of each recovery-agent tag return True
        when the body matches a recovery cause.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _recent_recovery_audit_comment

        wu = tmp_path / "wu.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(
            f"## Comments\n\n[{ts}] [{agent_tag}] [BLOCKED] Amendment rejected -- emitting fix proposal\n",
        )
        assert _recent_recovery_audit_comment(wu, now, 300) is True, (
            f"recovery helper should accept agent tag {agent_tag!r} regardless of hyphen/underscore form"
        )

    @pytest.mark.parametrize(
        "agent_tag",
        [
            pytest.param("agent/manifest-amender", id="manifest-amender-hyphen-structured-tag"),
            pytest.param("agent/manifest_amender", id="manifest_amender-underscore-structured-tag"),
        ],
    )
    def test_recovery_audit_helper_accepts_both_forms_for_structured_rejection_tag(
        self, tmp_path: Path, agent_tag: str
    ) -> None:
        """The ``[AMENDMENT_REJECTED]`` structured-tag path (issue #200)
        also passes through ``_recent_recovery_audit_comment``; verify the
        hyphen/underscore parity holds on that path too -- this is the
        exact reproducer named in the issue body.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _recent_recovery_audit_comment

        wu = tmp_path / "wu.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(
            f"## Comments\n\n[{ts}] [{agent_tag}] [BLOCKED] [AMENDMENT_REJECTED] "
            f"tdd_green_production_fix; rejected: APPROACH_AUTH: secret leaked into log\n",
        )
        assert _recent_recovery_audit_comment(wu, now, 300) is True

    @pytest.mark.parametrize(
        "agent_tag",
        [
            pytest.param("agent/code-reviewer", id="non-recovery-hyphen"),
            pytest.param("agent/executor", id="non-recovery-no-dashes"),
            pytest.param("operator/manifest-amender", id="non-agent-prefix"),
        ],
    )
    def test_recovery_audit_helper_still_rejects_non_recovery_agents(self, tmp_path: Path, agent_tag: str) -> None:
        """The normalisation must NOT widen the agent allowlist beyond the
        four canonical recovery agents. Tags like ``agent/code-reviewer``
        (review-team agent, not a recovery agent) and ``operator/...``
        must still return False.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _recent_recovery_audit_comment

        wu = tmp_path / "wu.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(
            f"## Comments\n\n[{ts}] [{agent_tag}] [BLOCKED] Amendment rejected -- emitting fix proposal\n",
        )
        assert _recent_recovery_audit_comment(wu, now, 300) is False

    def test_normalize_agent_tag_is_idempotent_on_canonical_form(self) -> None:
        """The normaliser is a no-op when the input is already in the
        canonical underscore form -- guards against accidental double-
        normalisation regressions.
        """
        from devbench.backlog.proposal import _normalize_agent_tag

        assert _normalize_agent_tag("agent/manifest_amender") == "agent/manifest_amender"
        assert _normalize_agent_tag("agent/manifest-amender") == "agent/manifest_amender"
        assert _normalize_agent_tag("operator/manifest-amender") == "operator/manifest-amender"


# ---------------------------------------------------------------------------
# Issue #211 end-to-end: classify_blocked_task on a hyphen-form amender audit
# ---------------------------------------------------------------------------


class TestClassifyBlockedTaskHyphenFormAmenderAudit:
    """Issue #211 reproducer: a work unit whose Comments section carries
    only a ``[BLOCKED] [AMENDMENT_REJECTED]`` audit from
    ``agent/manifest-amender`` (hyphen form, as written by
    ``amendment.py::AMENDER_AGENT_ID``), and no proposal / archive on
    disk, must classify as ``AWAITING_AMENDMENT_RECOVERY``.

    Before the issue #211 fix this returned ``OPERATOR_ACTION_REQUIRED``
    because the hyphen-form agent tag failed the frozenset membership
    check in ``_recent_recovery_audit_comment``.
    """

    def test_hyphen_form_amender_rejection_classifies_recovery(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        wu = story_dir / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            f"[{ts}] [agent/manifest-amender] [BLOCKED] [AMENDMENT_REJECTED] "
            "tdd_green_production_fix; rejected: APPROACH_AUTH: secret leaked into log\n",
        )
        (tmp_path / "BACKLOG.md").write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Test | Task | blocked | None | r "
            "| `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
        )
        state = classify_blocked_task(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=tmp_path,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY


# ---------------------------------------------------------------------------
# E8-F1-S1-T1 / issue #195: _RECOVERY_BODY_RE must match English forms
# and auto-requeue phrase
# ---------------------------------------------------------------------------


class TestRecoveryBodyRegex:
    """Parametrised regression tests for ``_RECOVERY_BODY_RE``.

    Issue #195: the original regex only matched kebab-case
    ``amendment-reject``, missing the natural-English ``Amendment rejected``
    and the orchestrator's stock ``will auto-requeue when ...`` phrase.
    """

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("amendment-reject", id="kebab-case-base"),
            pytest.param("Amendment-reject", id="kebab-case-capitalised"),
            pytest.param("AMENDMENT-REJECT", id="kebab-case-upper"),
            pytest.param("amendment-rejected", id="kebab-case-past-tense"),
            pytest.param("amendment reject", id="space-separated"),
            pytest.param("amendment rejected", id="english-past-tense"),
            pytest.param("Amendment rejected", id="english-capitalised-past-tense"),
            pytest.param("AMENDMENT REJECTED", id="english-upper-case"),
            pytest.param(
                "will auto-requeue when constants.py ownership clears",
                id="auto-requeue-full-phrase",
            ),
            pytest.param("will auto-requeue when", id="auto-requeue-bare"),
            pytest.param(
                "[BLOCKED] will auto-requeue when dep X clears",
                id="auto-requeue-in-blocked-audit",
            ),
            pytest.param("out-of-scope", id="out-of-scope"),
            pytest.param("Out-Of-Scope", id="out-of-scope-title-case"),
            pytest.param("ALL_REVIEWS_FAILED", id="all-reviews-failed"),
            pytest.param("REVIEW_REJECTED", id="review-rejected"),
            pytest.param(
                "dependency E0-T1 not yet terminal",
                id="dependency-not-terminal",
            ),
            pytest.param("dep E0-T1 not yet terminal", id="dep-short-form"),
        ],
    )
    def test_positive_match(self, body: str) -> None:
        from devbench.backlog.proposal import _RECOVERY_BODY_RE

        assert _RECOVERY_BODY_RE.search(body), f"_RECOVERY_BODY_RE should match: {body!r}"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("unrelated [BLOCKED] reason", id="unrelated-blocked"),
            pytest.param(
                "not in any recovery scenario",
                id="no-recovery-keywords",
            ),
            pytest.param("task is stuck on an unknown issue", id="generic-stuck"),
            pytest.param("amend this code", id="partial-amend-no-match"),
            pytest.param(
                "rejected by the operator",
                id="rejected-but-not-amendment",
            ),
            pytest.param("auto-requeue", id="auto-requeue-without-will-when"),
            pytest.param("amendment", id="amendment-bare-no-reject"),
        ],
    )
    def test_negative_no_match(self, body: str) -> None:
        from devbench.backlog.proposal import _RECOVERY_BODY_RE

        assert not _RECOVERY_BODY_RE.search(body), f"_RECOVERY_BODY_RE should NOT match: {body!r}"


class TestRecoveryBodyRegexIntegration:
    """End-to-end: synthetic work-unit with English-form [BLOCKED] audit
    classifies as AWAITING_AMENDMENT_RECOVERY via ``classify_blocked_task``.
    """

    def _workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        index = workspace / "BACKLOG.md"
        index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Test task | Task | blocked | None | r "
            "| `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        return workspace

    def test_amendment_rejected_english_classifies_recovery(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace(tmp_path)
        wu = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            f"[{ts}] [agent/orchestrator] [BLOCKED] Amendment rejected -- emitting fix proposal\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_auto_requeue_phrase_classifies_recovery(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace(tmp_path)
        wu = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        wu.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            f"[{ts}] [agent/orchestrator] [BLOCKED] Task will auto-requeue when constants.py ownership clears\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY


# ---------------------------------------------------------------------------
# E8-F1-S1-T3 / issue #195: classify_blocked_task end-to-end integration
# with 'Amendment rejected' audit -- false-positive loophole regression
# ---------------------------------------------------------------------------


class TestClassifyBlockedTaskAmendmentRejectedEndToEnd:
    """Issue #195 false-positive loophole: a task blocked with a recent
    ``[BLOCKED] Amendment rejected ...`` audit comment, no
    ``[BLOCKED_PENDING_PROPOSAL]`` marker, no unsatisfied regular deps,
    and ``workspace_root`` supplied MUST classify as
    ``AWAITING_AMENDMENT_RECOVERY`` -- not ``OPERATOR_ACTION_REQUIRED``.

    Before the regex fix in E8-F1-S1-T1, the natural-English phrase
    ``Amendment rejected`` was not matched by ``_RECOVERY_BODY_RE``
    (which only accepted kebab-case ``amendment-reject``).  This caused
    the classifier to fall through the recovery-signal branch and
    incorrectly report ``OPERATOR_ACTION_REQUIRED``, generating
    false operator-attention alerts during the 2026-05-15 autonomous run.
    """

    @staticmethod
    def _build_workspace(
        tmp_path: Path,
        *,
        audit_body: str,
        include_marker: bool = False,
        dep_status: str = "done",
    ) -> Path:
        """Build a minimal synthetic workspace for ``classify_blocked_task``.

        Parameters
        ----------
        tmp_path:
            pytest-provided temporary directory.
        audit_body:
            The ``[BLOCKED]`` audit comment body text.
        include_marker:
            If ``True``, add a ``[BLOCKED_PENDING_PROPOSAL]`` marker row
            to the work-unit file.
        dep_status:
            Status of the dependency task (``done`` means satisfied).
        """
        from datetime import UTC, datetime

        workspace = tmp_path / "ws"
        workspace.mkdir()
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        now = datetime(2026, 5, 15, 14, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")

        dep_section = "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        marker_section = ""
        if include_marker:
            marker_section = "## Blocked Pending Proposals\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99\n\n"

        wu = story_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: Test task\n\n"
            "## Status: blocked\n\n"
            + marker_section
            + dep_section
            + "## Comments\n\n"
            + f"[{ts}] [agent/orchestrator] [BLOCKED] {audit_body}\n"
        )

        index = workspace / "BACKLOG.md"
        index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Test task | Task | blocked | None | r "
            "| `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )

        return workspace

    @pytest.mark.parametrize(
        "audit_body",
        [
            pytest.param(
                "Amendment rejected -- emitting fix proposal",
                id="english-capitalised-past-tense",
            ),
            pytest.param(
                "amendment rejected for scope violation",
                id="english-lowercase-past-tense",
            ),
            pytest.param(
                "amendment-reject -- scope mismatch",
                id="kebab-case-original",
            ),
        ],
    )
    def test_recovery_signal_fires_with_workspace_root(self, tmp_path: Path, audit_body: str) -> None:
        """When workspace_root is supplied and the recent audit comment
        contains an amendment-rejected phrase, the classifier MUST return
        AWAITING_AMENDMENT_RECOVERY (not OPERATOR_ACTION_REQUIRED).
        This is the core regression for issue #195.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._build_workspace(tmp_path, audit_body=audit_body)
        now = datetime(2026, 5, 15, 14, 0, tzinfo=UTC)

        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )

        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_falls_through_without_workspace_root(self, tmp_path: Path) -> None:
        """Without workspace_root the recovery-signal branch is skipped and
        the classifier returns OPERATOR_ACTION_REQUIRED -- proving that
        the workspace_root parameter is load-bearing for the recovery path.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._build_workspace(
            tmp_path,
            audit_body="Amendment rejected -- emitting fix proposal",
        )
        now = datetime(2026, 5, 15, 14, 0, tzinfo=UTC)

        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            # workspace_root deliberately omitted
            now=now,
            recovery_window_seconds=300,
        )

        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_stale_audit_outside_window_falls_through(self, tmp_path: Path) -> None:
        """An amendment-rejected audit older than the recovery window does NOT
        classify as AWAITING_AMENDMENT_RECOVERY -- the timestamp check is
        load-bearing.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._build_workspace(
            tmp_path,
            audit_body="Amendment rejected -- emitting fix proposal",
        )
        # Set now to 10 minutes after the audit timestamp (window is 300s / 5min)
        now = datetime(2026, 5, 15, 14, 10, tzinfo=UTC)

        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )

        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED


# ---------------------------------------------------------------------------
# E2-F1-S1-T1 / issue #183(d): BlockedTaskState classifier tests
# ---------------------------------------------------------------------------


class TestBlockedTaskStateCanonicalValues:
    """The classifier enum must carry exactly the documented set of values.

    Issue #183(d) added ``RUNTIME_DEGRADATION`` to the original 6
    canonical buckets, so the asserted set is now 7 entries.
    """

    def test_has_exactly_canonical_values(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState

        names = {s.name for s in BlockedTaskState}
        assert names == {
            "AUTO_CLEARING_VIA_PROPOSAL",
            "AWAITING_AMENDMENT_RECOVERY",
            "AWAITING_DEPENDENCY",
            "HELD",
            "BLOCKED_ON_HELD",
            "OPERATOR_ACTION_REQUIRED",
            "RUNTIME_DEGRADATION",
        }

    def test_old_values_removed(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState

        names = {s.name for s in BlockedTaskState}
        assert "AWAITING_AUTO_RECOVERY" not in names
        assert "NEEDS_OPERATOR_ATTENTION" not in names


class TestHasRuntimeDegradationSignal:
    """Direct unit tests for ``_has_runtime_degradation_signal`` covering
    the defensive branches the higher-level classifier tests don't reach.
    """

    def test_returns_false_when_source_file_missing(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _has_runtime_degradation_signal

        missing = tmp_path / "nope.md"
        assert _has_runtime_degradation_signal(missing, datetime(2026, 5, 1, tzinfo=UTC)) is False

    def test_returns_false_on_unreadable_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError on read (e.g. permission denied) -> False, not a crash."""
        from datetime import UTC, datetime
        from pathlib import Path as _Path

        from devbench.backlog.proposal import _has_runtime_degradation_signal

        source = tmp_path / "wu.md"
        source.write_text("# wu\n", encoding="utf-8")

        original_read_text = _Path.read_text

        def _raise(self: _Path, *args: Any, **kwargs: Any) -> str:
            if self == source:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "read_text", _raise)
        assert _has_runtime_degradation_signal(source, datetime(2026, 5, 1, tzinfo=UTC)) is False

    def test_skips_audit_with_invalid_timestamp(self, tmp_path: Path) -> None:
        """A [BLOCKED] audit whose timestamp fails strptime is silently
        skipped; if nothing else matches the helper returns False.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _has_runtime_degradation_signal

        source = tmp_path / "wu.md"
        source.write_text(
            "## Comments\n[9999-99-99 99:99 UTC] [agent/review-supervisor] [BLOCKED] agent-tool-unavailable\n",
            encoding="utf-8",
        )
        assert _has_runtime_degradation_signal(source, datetime(2026, 5, 1, tzinfo=UTC)) is False


class TestRuntimeDegradationCommentRegressionSemantics:
    """AC-E4-F1-S1-T2-67: post-flatten (ADR-33) review-supervisor no longer
    runs a Step 0 self-check, so a healthy run must never emit the
    ``agent-tool-unavailable`` / ``review-supervisor...only Bash`` payload
    this classifier bucket matches on. The source comment and docstring
    must describe a firing match as a topology REGRESSION signal, not a
    transient condition an operator restart can clear -- while the regex
    pattern itself (and its matching behaviour) stays byte-for-byte
    unchanged.
    """

    def test_comment_documents_regression_not_transient_degradation(self) -> None:
        """The module-level comment above ``_RUNTIME_DEGRADATION_BODY_RE``
        must describe a match as a regression signal (something that
        should not happen post-flatten), not as an expected transient
        degradation an operator restart routinely clears.
        """
        import inspect

        lines = inspect.getsource(proposal_mod).splitlines()
        marker_line = next(i for i, line in enumerate(lines) if line.startswith("_RUNTIME_DEGRADATION_BODY_RE"))
        comment_lines: list[str] = []
        cursor = marker_line - 1
        while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
            comment_lines.insert(0, lines[cursor])
            cursor -= 1
        comment_text = "\n".join(comment_lines).lower()
        assert "regression" in comment_text, (
            "the comment above _RUNTIME_DEGRADATION_BODY_RE must document that a match "
            "signals a topology regression post-flatten (ADR-33), not routine transient "
            "degradation"
        )

    def test_docstring_documents_regression_not_transient_degradation(self) -> None:
        """``_has_runtime_degradation_signal``'s docstring must be updated
        to the same regression-signal semantics as the comment above the
        pattern it matches against.
        """
        from devbench.backlog.proposal import _has_runtime_degradation_signal

        docstring = (_has_runtime_degradation_signal.__doc__ or "").lower()
        assert "regression" in docstring, (
            "_has_runtime_degradation_signal's docstring must document that a match "
            "signals a topology regression post-flatten (ADR-33), not review-supervisor's "
            "(now-removed) Step 0 self-check"
        )

    def test_pattern_source_unchanged_still_matches_historical_self_check_phrasing(self) -> None:
        """The regex pattern's source string must be byte-for-byte
        unchanged: only the surrounding comment/docstring prose may
        change, per the work unit's scope boundary. Historical
        self-check phrasing (from before the flatten) must still match,
        since old audit rows already on disk must still classify.
        """
        from devbench.backlog.proposal import _RUNTIME_DEGRADATION_BODY_RE

        assert _RUNTIME_DEGRADATION_BODY_RE.pattern == r"agent-tool-unavailable|review-supervisor[^\n]*only\s+Bash"
        assert _RUNTIME_DEGRADATION_BODY_RE.search("agent-tool-unavailable") is not None
        assert _RUNTIME_DEGRADATION_BODY_RE.search("review-supervisor has only Bash available") is not None

    def test_pattern_does_not_fire_on_unrelated_blocked_body(self, tmp_path: Path) -> None:
        """A [BLOCKED] audit unrelated to the (now-removed) self-check
        payload must not match -- the pattern's scope must stay narrow
        even though its documented meaning changed.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import _has_runtime_degradation_signal

        source = tmp_path / "wu.md"
        source.write_text(
            "## Comments\n[2026-05-01 00:00 UTC] [agent/executor] [BLOCKED] waiting on dependency E1-F1-S1-T9\n",
            encoding="utf-8",
        )
        assert _has_runtime_degradation_signal(source, datetime(2026, 5, 1, 1, tzinfo=UTC)) is False

    def test_enum_member_comment_documents_regression_not_transient_degradation(self) -> None:
        """The comment above ``BlockedTaskState.RUNTIME_DEGRADATION`` itself
        (not just the comment above the regex further down the module)
        must describe a match as a topology regression, not a transient
        runtime condition a ``make start`` restart routinely clears.
        """
        import inspect

        from devbench.backlog.proposal import BlockedTaskState

        lines = inspect.getsource(BlockedTaskState).splitlines()
        marker_line = next(i for i, line in enumerate(lines) if line.strip().startswith("RUNTIME_DEGRADATION"))
        comment_lines: list[str] = []
        cursor = marker_line - 1
        while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
            comment_lines.insert(0, lines[cursor])
            cursor -= 1
        comment_text = "\n".join(comment_lines).lower()
        assert "regression" in comment_text, (
            "the comment above BlockedTaskState.RUNTIME_DEGRADATION must document that a "
            "match signals a topology regression post-flatten (ADR-33), not routine "
            "transient degradation a restart clears"
        )

    def test_classify_blocked_task_docstring_documents_regression_semantics(self) -> None:
        """``classify_blocked_task``'s priority-0 docstring bullet must be
        updated to the same regression-signal semantics as the module
        comment and ``_has_runtime_degradation_signal``'s docstring.
        """
        from devbench.backlog.proposal import classify_blocked_task

        docstring = (classify_blocked_task.__doc__ or "").lower()
        priority_0_start = docstring.index("0. ``runtime_degradation``")
        priority_1_start = docstring.index("1. ``held``")
        priority_0_bullet = docstring[priority_0_start:priority_1_start]
        assert "regression" in priority_0_bullet, (
            "classify_blocked_task's priority-0 docstring bullet must document that a "
            "match signals a topology regression post-flatten (ADR-33), not a transient "
            "runtime condition a make start restart resolves"
        )


class TestClassifyBlockedTaskRuntimeDegradation:
    """Issue #183(d): tasks with a recent agent-tool-unavailable [BLOCKED]
    audit comment must bucket as ``RUNTIME_DEGRADATION`` so the operator
    sees that a ``make start`` restart -- not a code fix -- is what
    recovers the work.
    """

    def _workspace(self, tmp_path: Path, comments: str) -> Path:
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nfixture\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            f"## Comments\n\n{comments}",
            encoding="utf-8",
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_recent_agent_tool_unavailable_returns_runtime_degradation(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"[{ts}] [agent/review-supervisor] [BLOCKED] agent-tool-unavailable: "
            "orchestrator review-supervisor lost Agent tool access in this session; "
            "operator restart of `make start` required\n",
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is BlockedTaskState.RUNTIME_DEGRADATION

    def test_stale_agent_tool_unavailable_does_not_trigger(self, tmp_path: Path) -> None:
        """A 25-hour-old payload is past the 24h window: classifier
        falls through to other buckets (here: OPERATOR_ACTION_REQUIRED)."""
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
        ts = (now - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"[{ts}] [agent/review-supervisor] [BLOCKED] agent-tool-unavailable: stale alert\n",
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is not BlockedTaskState.RUNTIME_DEGRADATION

    def test_unrelated_blocked_audit_does_not_trigger(self, tmp_path: Path) -> None:
        """A recent [BLOCKED] audit naming an unrelated cause (e.g.
        amendment-reject) must NOT trigger RUNTIME_DEGRADATION."""
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"[{ts}] [agent/backlog_manager] [BLOCKED] amendment-reject: scope drift\n",
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is not BlockedTaskState.RUNTIME_DEGRADATION

    def test_audit_older_than_restart_marker_does_not_trigger(self, tmp_path: Path) -> None:
        """Issue #215: an agent-tool-unavailable audit row older than the
        workspace's last-restart marker must NOT keep the task classified as
        RUNTIME_DEGRADATION.  The operator-driven restart resets the
        degradation context; only audit rows emitted by the new orchestrator
        instance should bucket as RUNTIME_DEGRADATION.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task
        from devbench.constants import LAST_RESTART_MARKER_PATH

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        # Audit row was emitted 30 minutes ago by the OLD instance.
        old_audit_ts = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"[{old_audit_ts}] [agent/review-supervisor] [BLOCKED] agent-tool-unavailable: "
            "session subprocess dropped Agent tool\n",
        )
        # Operator restarted 5 minutes ago -- AFTER the audit row.
        marker = workspace / LAST_RESTART_MARKER_PATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text((now - timedelta(minutes=5)).isoformat(), encoding="utf-8")

        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is not BlockedTaskState.RUNTIME_DEGRADATION, (
            "Audit row predating the last-restart marker must NOT keep RUNTIME_DEGRADATION classification (#215)"
        )

    def test_audit_after_restart_marker_still_triggers(self, tmp_path: Path) -> None:
        """Issue #215: a fresh agent-tool-unavailable audit row emitted AFTER
        the most recent restart marker is still observable; the new instance
        has independently entered a degraded state and the classifier must
        surface it.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task
        from devbench.constants import LAST_RESTART_MARKER_PATH

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        # Operator restarted 30 minutes ago.
        # Audit row was emitted 5 minutes ago by the NEW instance.
        new_audit_ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"[{new_audit_ts}] [agent/review-supervisor] [BLOCKED] agent-tool-unavailable: "
            "session subprocess dropped Agent tool (post-restart)\n",
        )
        marker = workspace / LAST_RESTART_MARKER_PATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text((now - timedelta(minutes=30)).isoformat(), encoding="utf-8")

        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is BlockedTaskState.RUNTIME_DEGRADATION, (
            "Audit row emitted AFTER the last-restart marker must still trigger RUNTIME_DEGRADATION (#215)"
        )

    def test_missing_restart_marker_falls_back_to_24h_window(self, tmp_path: Path) -> None:
        """Issue #215: when no restart marker is present (cold-boot /
        never-restarted workspace), the classifier must use the existing
        24h window so behaviour is a strict superset of the pre-fix
        implementation.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"[{ts}] [agent/review-supervisor] [BLOCKED] agent-tool-unavailable: cold-boot\n",
        )
        # No marker written.
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is BlockedTaskState.RUNTIME_DEGRADATION, (
            "With no restart marker, classifier must use 24h window and still find the recent audit row (#215)"
        )


class TestClassifyBlockedTaskHeld:
    """AC-FUNC-004: HELD state for a unit whose own status is HOLD."""

    def _workspace_hold_task(self, tmp_path: Path) -> Path:
        """Build a minimal workspace where E0-F1-S1-T1 has status HOLD."""
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: hold\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n"
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | hold | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        return tmp_path

    def test_task_with_hold_status_returns_held(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_hold_task(tmp_path)
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.HELD

    def test_task_status_is_hold_returns_false_when_task_id_not_in_index(self, tmp_path: Path) -> None:
        """Cover the fall-through `return False` in _task_status_is_hold.

        ``parse_index()`` succeeds and returns one unit (E0-F1-S1-T1);
        the task_id we ask about (E0-F1-S1-T99) is NOT that unit, so the
        for-loop exhausts and we hit the final ``return False`` at
        proposal.py:342.
        """
        from devbench.backlog.proposal import _task_status_is_hold

        workspace = self._workspace_hold_task(tmp_path)
        result = _task_status_is_hold(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T99",
        )
        assert result is False


class TestClassifyBlockedTaskBlockedOnHeld:
    """AC-FUNC-005: BLOCKED_ON_HELD state when a marker target is in HOLD."""

    def _workspace_blocked_on_hold(self, tmp_path: Path) -> Path:
        """Build a workspace where E0-F1-S1-T1 is blocked with a marker pointing to a HOLD target."""
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T2 "
            "promoted and wired as dependency. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        )
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Fix\n\n## Status: hold\n")
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Fix | Task | hold | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        return tmp_path

    def test_blocked_with_hold_marker_returns_blocked_on_held(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_blocked_on_hold(tmp_path)
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.BLOCKED_ON_HELD


class TestClassifyBlockedTaskAwaitingDependency:
    """AC-FUNC-003: AWAITING_DEPENDENCY when Dependencies-table row points at non-terminal task."""

    def _workspace_with_dep(self, tmp_path: Path, dep_status: str) -> Path:
        """Build workspace where E0-F1-S1-T1 has a regular dependency on E0-F1-S1-T2 (no marker)."""
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            f"| E0-F1-S1-T2 | Fix | {dep_status} |\n\n"
            "## Comments\n"
        )
        (story_dir / "E0-F1-S1-T2.md").write_text(f"# E0-F1-S1-T2: Fix\n\n## Status: {dep_status}\n")
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | E0-F1-S1-T2 | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            f"| E0-F1-S1-T2 | Fix | Task | {dep_status} | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        return tmp_path

    def test_non_terminal_dep_returns_awaiting_dependency(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_dep(tmp_path, "in-queue")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.AWAITING_DEPENDENCY

    def test_non_terminal_dep_in_progress_returns_awaiting_dependency(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_dep(tmp_path, "in-progress")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.AWAITING_DEPENDENCY

    def test_stale_terminal_markers_with_unsatisfied_dep_returns_awaiting_dependency(self, tmp_path: Path) -> None:
        """Issue #186: a task with stale ``[BLOCKED_PENDING_PROPOSAL]``
        markers (all targets terminal) AND an unsatisfied regular
        Dependencies-table row MUST classify as AWAITING_DEPENDENCY.

        Prior to the fix, ``_classify_with_markers`` short-circuited to
        OPERATOR_ACTION_REQUIRED the moment any marker rows were
        present, even when the marker rows were stale and the *real*
        blocker was the regular dep.
        """
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        # Build the workspace: T1 has a stale marker on terminal T2 AND
        # a regular dep on still-in-queue T3.
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nfixture\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            "| E0-F1-S1-T3 | Real blocker | in-queue |\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
            encoding="utf-8",
        )
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Stale\n\n## Status: done\n")
        (story_dir / "E0-F1-S1-T3.md").write_text("# E0-F1-S1-T3: Real blocker\n\n## Status: in-queue\n")
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | E0-F1-S1-T3 | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Stale | Task | done | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
            "| E0-F1-S1-T3 | Real blocker | Task | in-queue | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T3.md` |\n",
            encoding="utf-8",
        )
        state = classify_blocked_task(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=tmp_path,
        )
        assert state is BlockedTaskState.AWAITING_DEPENDENCY

    def test_stale_terminal_markers_no_dep_no_recovery_is_auto_clearing(self, tmp_path: Path) -> None:
        """Issue #200 / AC-200-1: when all markers are terminal AND there is no
        unsatisfied regular dep AND no recovery signal, the classifier MUST return
        AUTO_CLEARING_VIA_PROPOSAL (not OPERATOR_ACTION_REQUIRED).

        Before the fix this fell through to OPERATOR_ACTION_REQUIRED; the new
        behaviour signals that the cascade (_auto_requeue_marker_dependents)
        should flip the task to in-queue. The operator does not need to intervene.
        """
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nfixture\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
            encoding="utf-8",
        )
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Stale\n\n## Status: done\n")
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Stale | Task | done | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n",
            encoding="utf-8",
        )
        state = classify_blocked_task(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=tmp_path,
        )
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL


class TestClassifyBlockedTaskAwaitingAmendmentRecovery:
    """AC-FUNC-002: AWAITING_AMENDMENT_RECOVERY for sync-blocked comments from backlog_manager."""

    def _workspace_no_marker_no_dep(self, tmp_path: Path) -> Path:
        """Build workspace with E0-F1-S1-T1 blocked, no markers, no regular deps."""
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n"
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        return tmp_path

    def test_sync_blocked_comment_from_backlog_manager_returns_amendment_recovery(self, tmp_path: Path) -> None:
        """AC-FUNC-002 regression test: backlog_manager sync-blocked comment must classify
        as AWAITING_AMENDMENT_RECOVERY, not OPERATOR_ACTION_REQUIRED."""
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_no_marker_no_dep(tmp_path)
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        source_file.write_text(
            source_file.read_text()
            + f"\n[{ts}] [agent/backlog_manager] [BLOCKED] sync-blocked: dependency 'E0-F1-S1-T2' not yet terminal\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_dep_not_yet_terminal_pattern_matches_amendment_recovery(self, tmp_path: Path) -> None:
        """dep .* not yet terminal also matches the AWAITING_AMENDMENT_RECOVERY path."""
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_no_marker_no_dep(tmp_path)
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        source_file.write_text(
            source_file.read_text() + f"\n[{ts}] [agent/backlog_manager] [BLOCKED] dep E0-F1-S1-T2 not yet terminal\n"
        )
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=300,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_pending_proposal_json_returns_amendment_recovery(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_no_marker_no_dep(tmp_path)
        proposals_dir = workspace / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text("{}")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_rejected_amendment_archive_returns_amendment_recovery(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_no_marker_no_dep(tmp_path)
        archive_dir = workspace / ".devbench" / "rejected-requests"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-20260501T120000Z.json").write_text("{}")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_no_signal_returns_operator_action_required(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_no_marker_no_dep(tmp_path)
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED


class TestPanelSymbolsRemoved:
    """AC-FUNC-006: panel3_annotation and _panel3_classify_markers must not exist."""

    def test_panel3_annotation_not_importable(self) -> None:
        import importlib

        mod = importlib.import_module("devbench.backlog.proposal")
        assert not hasattr(mod, "panel3_annotation"), "panel3_annotation must be removed from proposal.py"

    def test_panel3_classify_markers_not_importable(self) -> None:
        import importlib

        mod = importlib.import_module("devbench.backlog.proposal")
        assert not hasattr(mod, "_panel3_classify_markers"), "_panel3_classify_markers must be removed from proposal.py"


class TestClassifyBlockedTaskEndToEnd:
    """AC-CYCLE-001: end-to-end exercise of the new 6-state classifier."""

    def test_all_six_states_reachable_via_classify_blocked_task(self, tmp_path: Path) -> None:
        """Build one fixture per state and verify the observed outcome matches the spec.

        Exercises the full call path through classify_blocked_task for each
        of the six canonical states in the order the spec defines them:
        HELD -> BLOCKED_ON_HELD -> AUTO_CLEARING_VIA_PROPOSAL ->
        AWAITING_DEPENDENCY -> AWAITING_AMENDMENT_RECOVERY ->
        OPERATOR_ACTION_REQUIRED.
        """
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        # Task T10: HOLD status -> HELD
        (story_dir / "E0-F1-S1-T10.md").write_text(
            "# E0-F1-S1-T10: Hold Task\n\n## Status: hold\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
        )
        # Task T11: blocked, marker pointing at T10 (HOLD) -> BLOCKED_ON_HELD
        (story_dir / "E0-F1-S1-T11.md").write_text(
            "# E0-F1-S1-T11: Blocked On Hold\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T10\n"
        )
        # Task T12: blocked, marker pointing at in-queue target -> AUTO_CLEARING_VIA_PROPOSAL
        (story_dir / "E0-F1-S1-T12.md").write_text(
            "# E0-F1-S1-T12: Auto Clearing\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T13\n"
        )
        (story_dir / "E0-F1-S1-T13.md").write_text("# E0-F1-S1-T13: In Queue\n\n## Status: in-queue\n")
        # Task T14: blocked, regular dep row pointing at in-queue T15, no marker
        (story_dir / "E0-F1-S1-T14.md").write_text(
            "# E0-F1-S1-T14: Awaiting Dep\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            "| E0-F1-S1-T15 | Dep | in-queue |\n\n"
            "## Comments\n"
        )
        (story_dir / "E0-F1-S1-T15.md").write_text("# E0-F1-S1-T15: Dep\n\n## Status: in-queue\n")
        # Task T16: blocked, no marker, no dep, recent recovery comment
        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        (story_dir / "E0-F1-S1-T16.md").write_text(
            "# E0-F1-S1-T16: Awaiting Amendment\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            f"## Comments\n\n[{ts}] [agent/backlog_manager] [BLOCKED] "
            "dependency 'E0-F1-S1-T15' not yet terminal\n"
        )
        # Task T17: blocked, no marker, no dep, no recovery signal
        (story_dir / "E0-F1-S1-T17.md").write_text(
            "# E0-F1-S1-T17: Operator Required\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n"
        )

        _bp = "backlog/E0/E0-F1/E0-F1-S1"
        rows = [
            f"| E0-F1-S1-T10 | Hold | Task | hold | None | r | `{_bp}/E0-F1-S1-T10.md` |",
            f"| E0-F1-S1-T11 | BlockedOnHold | Task | blocked | None | r | `{_bp}/E0-F1-S1-T11.md` |",
            f"| E0-F1-S1-T12 | AutoClearing | Task | blocked | None | r | `{_bp}/E0-F1-S1-T12.md` |",
            f"| E0-F1-S1-T13 | InQueue | Task | in-queue | None | r | `{_bp}/E0-F1-S1-T13.md` |",
            f"| E0-F1-S1-T14 | AwaitingDep | Task | blocked | E0-F1-S1-T15 | r | `{_bp}/E0-F1-S1-T14.md` |",
            f"| E0-F1-S1-T15 | Dep | Task | in-queue | None | r | `{_bp}/E0-F1-S1-T15.md` |",
            f"| E0-F1-S1-T16 | AwaitingAmendment | Task | blocked | None | r | `{_bp}/E0-F1-S1-T16.md` |",
            f"| E0-F1-S1-T17 | Operator | Task | blocked | None | r | `{_bp}/E0-F1-S1-T17.md` |",
        ]
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
        )

        cases: list[tuple[str, BlockedTaskState]] = [
            ("E0-F1-S1-T10", BlockedTaskState.HELD),
            ("E0-F1-S1-T11", BlockedTaskState.BLOCKED_ON_HELD),
            ("E0-F1-S1-T12", BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL),
            ("E0-F1-S1-T14", BlockedTaskState.AWAITING_DEPENDENCY),
            ("E0-F1-S1-T16", BlockedTaskState.AWAITING_AMENDMENT_RECOVERY),
            ("E0-F1-S1-T17", BlockedTaskState.OPERATOR_ACTION_REQUIRED),
        ]
        for task_id, expected in cases:
            state = classify_blocked_task(
                backlog_dir,
                tmp_path / "BACKLOG.md",
                task_id,
                workspace_root=tmp_path,
                now=now,
                recovery_window_seconds=300,
            )
            assert state is expected, f"{task_id}: expected {expected.name}, got {state.name}"


# ---------------------------------------------------------------------------
# Issue #200 / AC-200-1: classifier returns AUTO_CLEARING_VIA_PROPOSAL
# even when ALL [BLOCKED_PENDING_PROPOSAL] marker targets are terminal.
# Before the fix, _classify_with_markers returned None for all-terminal
# markers, causing a fall-through to OPERATOR_ACTION_REQUIRED.
# ---------------------------------------------------------------------------


class TestClassifyBlockedTaskSatisfiedMarkers:
    """AC-200-1: classify_blocked_task on satisfied (terminal) markers.

    Parametrised tests cover the four sub-cases in the acceptance criteria:
    - single satisfied marker
    - multiple satisfied markers
    - mixed satisfied + unsatisfied (unsatisfied wins: AUTO_CLEARING_VIA_PROPOSAL)
    - satisfied marker + operator-attention audit (operator wins:
      OPERATOR_ACTION_REQUIRED when no recovery signals exist)
    """

    def _workspace(
        self,
        tmp_path: Path,
        marker_target_status_pairs: list[tuple[str, str]],
        comments_extra: str = "",
        dep_ids: list[str] | None = None,
    ) -> Path:
        """Build a workspace where E0-F1-S1-T1 has [BLOCKED_PENDING_PROPOSAL] markers."""
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        marker_lines = "\n".join(
            f"[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] {tid}"
            for tid, _ in marker_target_status_pairs
        )
        dep_rows = "| none | | |"
        if dep_ids:
            dep_rows = "\n".join(f"| {d} | (auto) | proposed |" for d in dep_ids)

        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nfixture\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            f"{dep_rows}\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `t.py` | fixture |\n\n"
            "## Definition of Done\n\n- [ ] AC complete\n\n"
            f"## Comments\n\n{marker_lines}\n{comments_extra}"
        )

        rows = ["| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"]
        for tid, status in marker_target_status_pairs:
            (story_dir / f"{tid}.md").write_text(f"# {tid}: X\n\n## Status: {status}\n")
            rows.append(f"| {tid} | Marker | Task | {status} | None | r | `backlog/E0/E0-F1/E0-F1-S1/{tid}.md` |")

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
        )
        return tmp_path

    @pytest.mark.parametrize(
        "pairs,expected_state",
        [
            pytest.param(
                [("E0-F1-S1-T2", "done")],
                "AUTO_CLEARING_VIA_PROPOSAL",
                id="single-satisfied-marker-done",
            ),
            pytest.param(
                [("E0-F1-S1-T2", "declined")],
                "AUTO_CLEARING_VIA_PROPOSAL",
                id="single-satisfied-marker-declined",
            ),
            pytest.param(
                [("E0-F1-S1-T2", "done"), ("E0-F1-S1-T3", "declined")],
                "AUTO_CLEARING_VIA_PROPOSAL",
                id="multiple-satisfied-markers",
            ),
            pytest.param(
                [("E0-F1-S1-T2", "done"), ("E0-F1-S1-T3", "in-queue")],
                "AUTO_CLEARING_VIA_PROPOSAL",
                id="mixed-satisfied-unsatisfied-unsatisfied-wins",
            ),
        ],
    )
    def test_satisfied_markers_return_auto_clearing(
        self, tmp_path: Path, pairs: list[tuple[str, str]], expected_state: str
    ) -> None:
        """AC-200-1: all-terminal and mixed-terminal markers both produce AUTO_CLEARING_VIA_PROPOSAL."""
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace(tmp_path, pairs)
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
        )
        assert state is getattr(BlockedTaskState, expected_state), (
            f"Expected {expected_state}, got {state.name}. Pairs: {pairs}"
        )


# ---------------------------------------------------------------------------
# Issue #200 / AC-200-4: _REJECTION_TAG_RE matches [AMENDMENT_REJECTED]
# structured-tag audits and causes AWAITING_AMENDMENT_RECOVERY classification.
# ---------------------------------------------------------------------------


class TestRejectionTagRegex:
    """AC-200-4: separate _REJECTION_TAG_RE for structured [AMENDMENT_REJECTED] tags.

    The classifier's AWAITING_AMENDMENT_RECOVERY path must recognise
    structured-tag audits like
    ``[AMENDMENT_REJECTED] tdd_green_production_fix; rejected: POST_CHECK: ...``
    even though that body text does not contain the prose ``amendment reject``
    that ``_RECOVERY_BODY_RE`` matches.
    """

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(
                "[AMENDMENT_REJECTED] tdd_green_production_fix; rejected: POST_CHECK: scope violation",
                id="structured-tag-with-reason",
            ),
            pytest.param(
                "[AMENDMENT_REJECTED]",
                id="structured-tag-bare",
            ),
            pytest.param(
                "[AMENDMENT_REJECTED] out-of-scope: constants.py not in manifest",
                id="structured-tag-with-out-of-scope",
            ),
        ],
    )
    def test_positive_match(self, body: str) -> None:
        """_REJECTION_TAG_RE must match [AMENDMENT_REJECTED] structured tags."""
        from devbench.backlog.proposal import _REJECTION_TAG_RE

        assert _REJECTION_TAG_RE.search(body), f"_REJECTION_TAG_RE should match: {body!r}"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("unrelated blocked reason", id="unrelated"),
            pytest.param("AMENDMENT REJECTED", id="prose-form-no-brackets"),
            pytest.param("amendment rejected", id="lowercase-prose-no-brackets"),
        ],
    )
    def test_negative_no_match(self, body: str) -> None:
        """_REJECTION_TAG_RE must NOT match prose forms without brackets."""
        from devbench.backlog.proposal import _REJECTION_TAG_RE

        assert not _REJECTION_TAG_RE.search(body), f"_REJECTION_TAG_RE should NOT match: {body!r}"

    def _workspace_no_marker(self, tmp_path: Path) -> Path:
        """Build workspace with E0-F1-S1-T1 blocked, no markers, no regular deps."""
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n"
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r"
            " | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        return tmp_path

    @pytest.mark.parametrize(
        "audit_body",
        [
            pytest.param(
                "[AMENDMENT_REJECTED] tdd_green_production_fix; rejected: POST_CHECK: scope",
                id="amendment-rejected-tag-with-reason",
            ),
            pytest.param(
                "[AMENDMENT_REJECTED]",
                id="amendment-rejected-tag-bare",
            ),
        ],
    )
    def test_amendment_rejected_tag_classifies_awaiting_amendment_recovery(
        self, tmp_path: Path, audit_body: str
    ) -> None:
        """AC-200-4: [AMENDMENT_REJECTED] structured-tag audit triggers AWAITING_AMENDMENT_RECOVERY."""
        from datetime import UTC, datetime

        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_no_marker(tmp_path)
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        now = datetime(2026, 5, 16, 2, 32, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        source_file.write_text(source_file.read_text() + f"\n[{ts}] [agent/manifest_amender] [BLOCKED] {audit_body}\n")
        state = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=3600,
        )
        assert state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY, (
            f"Expected AWAITING_AMENDMENT_RECOVERY for body {audit_body!r}, got {state.name}"
        )


class TestClassifyBlockedTaskRetryBudgetExhausted:
    """Issue #248: a spent retry budget is operator work, not recovery.

    The orchestrate skill writes its retry-exhaustion row under
    ``agent/orchestrator`` and names the failing checks, so it matched both
    the recovery agent-tag allowlist and ``_RECOVERY_BODY_RE`` (which
    includes ``ALL_REVIEWS_FAILED`` / ``REVIEW_REJECTED``). The unit was
    therefore reported as ``AWAITING_AMENDMENT_RECOVERY``, whose contract is
    "operator does nothing", while no further executor run was coming. The
    run stalled with no alert.
    """

    def _workspace(self, tmp_path: Path, comments: str) -> Path:
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n" + comments
        )
        (tmp_path / "BACKLOG.md").write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        return tmp_path

    @staticmethod
    def _classify(workspace: Path, now: datetime, window: int = 300) -> object:
        from devbench.backlog.proposal import classify_blocked_task

        return classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
            recovery_window_seconds=window,
        )

    def test_exhausted_budget_classifies_operator_action_required(self, tmp_path: Path) -> None:
        """The exact row shape that stalled a real run, now correctly classified."""
        from devbench.backlog.proposal import BlockedTaskState

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"\n[{ts}] [agent/orchestrator] [BLOCKED] [RETRY_BUDGET_EXHAUSTED] "
            "test_review REVIEW_REJECTED after 10 consecutive failures\n",
        )
        assert self._classify(workspace, now) is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_same_row_without_the_tag_is_still_treated_as_recovery(self, tmp_path: Path) -> None:
        """Guards the boundary: an ordinary REVIEW_REJECTED with retries left must not page the operator."""
        from devbench.backlog.proposal import BlockedTaskState

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"\n[{ts}] [agent/orchestrator] [BLOCKED] test_review REVIEW_REJECTED, retrying\n",
        )
        assert self._classify(workspace, now) is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_exhausted_budget_beats_a_pending_proposal_json(self, tmp_path: Path) -> None:
        """A leftover recovery artefact must not mask an exhausted budget."""
        from devbench.backlog.proposal import BlockedTaskState

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"\n[{ts}] [agent/orchestrator] [BLOCKED] [RETRY_BUDGET_EXHAUSTED] budget spent\n",
        )
        proposals_dir = workspace / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text("{}")
        assert self._classify(workspace, now) is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_lowercase_prose_mention_of_the_tag_does_not_trigger(self, tmp_path: Path) -> None:
        """Prose quoting the tag is not the tag, matching _REJECTION_TAG_RE's convention."""
        from devbench.backlog.proposal import BlockedTaskState

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"\n[{ts}] [agent/orchestrator] [BLOCKED] ALL_REVIEWS_FAILED; "
            "if this repeats we will see [retry_budget_exhausted]\n",
        )
        assert self._classify(workspace, now) is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY

    def test_marker_driven_cascade_still_wins_over_the_tag(self, tmp_path: Path) -> None:
        """A live cascade genuinely will clear the unit, so it must not be downgraded to operator work."""
        from devbench.backlog.proposal import BlockedTaskState

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        workspace = self._workspace(
            tmp_path,
            f"\n[{ts}] [agent/orchestrator] [BLOCKED] [RETRY_BUDGET_EXHAUSTED] budget spent "
            "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
        )
        # T2 exists and is non-terminal, so the ADR-07 cascade is genuinely in flight.
        index = workspace / "BACKLOG.md"
        index.write_text(
            index.read_text()
            + "| E0-F1-S1-T2 | Recovery | Task | in-queue | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T2.md").write_text(
            "# E0-F1-S1-T2: Recovery\n\n## Status: in-queue\n\n## Comments\n"
        )
        assert self._classify(workspace, now) is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL


class TestHasRetryExhaustedSignalReadFailure:
    """An unreadable work-unit file must not manufacture an operator alert (issue #248)."""

    def test_unreadable_source_file_returns_false(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from devbench.backlog.proposal import _has_retry_exhausted_signal

        source = tmp_path / "E0-F1-S1-T1.md"
        source.write_text("[RETRY_BUDGET_EXHAUSTED] present but unreadable", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            assert _has_retry_exhausted_signal(source) is False


class TestFindDraftFileResolvesAnywhere:
    """Issue #302: a work-unit ID identifies one unit wherever its file sits.

    The resolver previously looked in exactly one computed story directory.
    A unit living elsewhere read as absent, so ``classify_proposed_task``
    reported UNMATERIALISED and the orchestrate loop's opening
    ``sweep-proposals`` created it again in the canonical directory. That
    produced two files and two index rows under one ID, on every start.
    """

    @staticmethod
    def _write(root: Path, rel: str, status: str = "done") -> Path:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {target.stem}: T\n\n## Status: {status}\n", encoding="utf-8")
        return target

    def test_finds_a_unit_outside_the_canonical_story_directory(self, tmp_path: Path) -> None:
        """The exact shape that caused the repeated duplication."""
        from devbench.backlog.proposal import _find_draft_file

        root = tmp_path / "backlog"
        # Bare-ID tree, not the <id>-<slug> layout the resolver used to assume.
        expected = self._write(root, "E2/E2-F4/E2-F4-S3/E2-F4-S3-T2.md")
        assert _find_draft_file(root, "E2-F4-S3-T2") == expected

    def test_finds_a_unit_in_the_slug_directory(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _find_draft_file

        root = tmp_path / "backlog"
        expected = self._write(root, "E2-quota/E2-F4-orch/E2-F4-S3-loop/E2-F4-S3-T2.md")
        assert _find_draft_file(root, "E2-F4-S3-T2") == expected

    def test_absent_unit_still_returns_none(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _find_draft_file

        (tmp_path / "backlog").mkdir()
        assert _find_draft_file(tmp_path / "backlog", "E9-F9-S9-T9") is None

    def test_existing_unit_is_not_classified_unmaterialised(self, tmp_path: Path) -> None:
        """The consequence that mattered: sweep-proposals re-creating a live unit."""
        from devbench.backlog.proposal import ProposalTaskState, classify_proposed_task

        root = tmp_path / "backlog"
        self._write(root, "E2/E2-F4/E2-F4-S3/E2-F4-S3-T2.md", status="done")
        state = classify_proposed_task(root, tmp_path, "E2-F4-S3-T2")
        assert state is ProposalTaskState.DONE
        assert state is not ProposalTaskState.UNMATERIALISED

    def test_duplicate_files_are_refused_rather_than_picked_arbitrarily(self, tmp_path: Path) -> None:
        """Returning either would make downstream decisions depend on walk order."""
        from devbench.backlog.proposal import ProposalError, _find_draft_file

        root = tmp_path / "backlog"
        self._write(root, "E2/E2-F4/E2-F4-S3/E2-F4-S3-T2.md", status="done")
        self._write(root, "E2-quota/E2-F4-orch/E2-F4-S3-loop/E2-F4-S3-T2.md", status="declined")
        with pytest.raises(ProposalError) as exc:
            _find_draft_file(root, "E2-F4-S3-T2")
        msg = str(exc.value)
        assert "duplicate work-unit file" in msg
        assert "2 files carry this ID" in msg
        assert "validate-backlog" in msg
