"""Tests for the executor-facing manifest-row-superseded amendment path
(reason=manifest_row_superseded).

A judge-gated, config-gated amendment path that lets the EXECUTOR self-remove a
Changes Manifest row whose file a DONE sibling renamed/deleted -- without an
operator stop-window. Deterministic guards require, before any row is removed:

  (a) the row's file is ABSENT on disk in the target repo,
  (b) every cited unit is status ``done`` in the backlog index, and
  (c) the staged diff does not touch the removed path.

Never removes a row whose file still exists. Mirrors the design of the
verification-directive amendment (``reason=verification_directive_defect``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from devbench.backlog.amendment import (
    ALLOWED_AMENDMENT_REASONS,
    MANIFEST_ROW_REMOVED_ACTION,
    REASON_MANIFEST_ROW_SUPERSEDED,
    AmendmentError,
    AmendmentRequest,
    ManifestRowSupersededClaim,
    PreFilter,
    _apply_manifest_row_superseded,
    _check_cited_units_done,
    apply_amendment,
    request_path,
    write_request,
)
from devbench.backlog.manifest import parse_manifest
from devbench.config_loader import AmendmentConfig

TASK_ID = "EX-F1-S1-T1"
DONE_ID = "EX-F1-S1-T0"
STALE_ROW = "terragrunt/terragrunt.hcl"
SURVIVING_ROW = "terragrunt/root.hcl"

WORK_UNIT_TEMPLATE = """\
# {task_id}: Sample Task

## Status: in-progress

## Description

Test task description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 the version floor is bumped

## Changes Manifest

| File | Change |
|------|--------|
| `terragrunt/terragrunt.hcl` | modify version floor |
| `terragrunt/root.hcl` | modify version floor |

## Definition of Done

- [ ] All AC checked

## Comments
"""

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 1 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T0 | Done Sibling | Task | done | None | caylent-solutions/example | `backlog/EX-F1-S1-T0.md` |
| EX-F1-S1-T1 | Sample Task | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""

DONE_UNIT = """\
# EX-F1-S1-T0: Done Sibling

## Status: done

## Description

Landed work that renamed terragrunt/terragrunt.hcl to terragrunt/root.hcl.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-1 landed the rename

## Changes Manifest

| File | Change |
|------|--------|
| `terragrunt/root.hcl` | rename from terragrunt.hcl |

## Definition of Done

- [ ] All AC checked
"""

NOT_DONE_INDEX = BACKLOG_INDEX_TEMPLATE.replace(
    "| EX-F1-S1-T0 | Done Sibling | Task | done |",
    "| EX-F1-S1-T0 | Done Sibling | Task | in-queue |",
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Minimal workspace: BACKLOG.md + one in-progress unit + one done sibling.

    Also creates a fake target repo working tree under ``repo/`` where the
    surviving row's file exists but the stale row's file is absent (the DONE
    sibling renamed it away).
    """
    (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / f"{TASK_ID}.md").write_text(WORK_UNIT_TEMPLATE.format(task_id=TASK_ID))
    (backlog_dir / f"{DONE_ID}.md").write_text(DONE_UNIT)
    repo = tmp_path / "repo"
    (repo / "terragrunt").mkdir(parents=True)
    (repo / SURVIVING_ROW).write_text("// root.hcl\n", encoding="utf-8")
    return tmp_path


def _repo_path(workspace: Path) -> Path:
    return workspace / "repo"


def _claim(
    row_path: str = STALE_ROW,
    cited: list[str] | None = None,
    evidence: str = "git log shows EX-F1-S1-T0 renamed terragrunt.hcl -> root.hcl; file absent on disk",
) -> ManifestRowSupersededClaim:
    return ManifestRowSupersededClaim(
        row_path=row_path,
        cited_done_units=cited if cited is not None else [DONE_ID],
        evidence=evidence,
    )


def _request(
    claims: list[ManifestRowSupersededClaim] | None = None,
    reason: str = REASON_MANIFEST_ROW_SUPERSEDED,
    files_to_add: list[dict[str, str]] | None = None,
) -> AmendmentRequest:
    data: dict[str, Any] = {
        "task_id": TASK_ID,
        "requested_at": "2026-06-14T15:00:00Z",
        "reason": reason,
        "justification": "Row superseded by DONE sibling's landed rename; file absent on disk.",
        "files_to_add": files_to_add if files_to_add is not None else [],
        "linked_acs": ["AC-TEST-001"],
        "manifest_row_superseded_claims": [
            c.__dict__ if isinstance(c, ManifestRowSupersededClaim) else c
            for c in (claims if claims is not None else [_claim()])
        ],
    }
    return AmendmentRequest.from_dict(data)


def _apply(workspace: Path, staged: frozenset[str] | None = frozenset()) -> None:
    apply_amendment(
        workspace,
        workspace / "BACKLOG.md",
        TASK_ID,
        repo_path=_repo_path(workspace),
        staged_files=staged,
    )


class TestManifestRowSupersededParsing:
    def test_reason_constant_registered(self) -> None:
        assert REASON_MANIFEST_ROW_SUPERSEDED == "manifest_row_superseded"
        assert REASON_MANIFEST_ROW_SUPERSEDED in ALLOWED_AMENDMENT_REASONS

    def test_round_trip_through_dict(self) -> None:
        req = _request()
        rebuilt = AmendmentRequest.from_dict(req.to_dict())
        assert rebuilt.manifest_row_superseded_claims == req.manifest_row_superseded_claims
        assert rebuilt.reason == REASON_MANIFEST_ROW_SUPERSEDED

    def test_from_dict_defaults_to_empty_claims(self) -> None:
        req = AmendmentRequest(
            task_id=TASK_ID,
            requested_at="2026-06-14T15:00:00Z",
            reason="tdd_green_production_fix",
            justification="x",
            files_to_add=[],
            linked_acs=[],
        )
        rebuilt = AmendmentRequest.from_dict(req.to_dict())
        assert rebuilt.manifest_row_superseded_claims == []

    @pytest.mark.parametrize("field", ["row_path", "evidence"])
    def test_empty_required_field_rejected(self, field: str) -> None:
        kwargs: dict[str, Any] = {"row_path": STALE_ROW, "cited_done_units": [DONE_ID], "evidence": "e"}
        kwargs[field] = "   "
        with pytest.raises(ValueError):
            ManifestRowSupersededClaim(**kwargs)

    def test_empty_cited_units_rejected(self) -> None:
        with pytest.raises(ValueError):
            ManifestRowSupersededClaim(row_path=STALE_ROW, cited_done_units=[], evidence="e")

    def test_from_dict_rejects_non_list_claims(self) -> None:
        data = _request().to_dict()
        data["manifest_row_superseded_claims"] = {"row_path": "x"}
        with pytest.raises(ValueError):
            AmendmentRequest.from_dict(data)


class TestPreFilterGating:
    def test_gate_defaults_to_on(self, workspace: Path) -> None:
        assert AmendmentConfig().allow_manifest_row_superseded_amendments is True
        pf = PreFilter(workspace / "BACKLOG.md", AmendmentConfig())
        pf.run_all(_request(), prior_applied_count=0)

    def test_rejected_when_config_gate_off(self, workspace: Path) -> None:
        cfg = AmendmentConfig(allow_manifest_row_superseded_amendments=False)
        pf = PreFilter(workspace / "BACKLOG.md", cfg)
        with pytest.raises(AmendmentError, match="allow_manifest_row_superseded_amendments"):
            pf.run_all(_request(), prior_applied_count=0)

    def test_rejected_without_claims(self, workspace: Path) -> None:
        pf = PreFilter(workspace / "BACKLOG.md", AmendmentConfig())
        with pytest.raises(AmendmentError, match="manifest_row_superseded_claims"):
            pf.run_all(_request(claims=[]), prior_applied_count=0)

    def test_rejected_when_files_to_add_present(self, workspace: Path) -> None:
        pf = PreFilter(workspace / "BACKLOG.md", AmendmentConfig())
        req = _request(files_to_add=[{"path": "x.py", "change": "add"}])
        with pytest.raises(AmendmentError, match="files_to_add"):
            pf.run_all(req, prior_applied_count=0)

    def test_claims_rejected_with_other_reason(self, workspace: Path) -> None:
        pf = PreFilter(workspace / "BACKLOG.md", AmendmentConfig())
        req = _request(reason="tdd_green_production_fix")
        with pytest.raises(AmendmentError, match="manifest_row_superseded_claims"):
            pf.run_all(req, prior_applied_count=0)


class TestCheckCitedUnitsDone:
    """Direct coverage of the shared cited-units guard's error branches."""

    def test_empty_cited_is_noop(self, tmp_path: Path) -> None:
        _check_cited_units_done(set(), tmp_path / "MISSING.md", context="x")

    def test_missing_index_raises(self, tmp_path: Path) -> None:
        with pytest.raises(AmendmentError, match="Cannot read backlog index"):
            _check_cited_units_done({DONE_ID}, tmp_path / "MISSING.md", context="Manifest-row-superseded claim")

    def test_unknown_cited_unit_raises(self, workspace: Path) -> None:
        with pytest.raises(AmendmentError, match="does not exist in the backlog index"):
            _check_cited_units_done({"E9-F9-S9-T9"}, workspace / "BACKLOG.md", context="Manifest-row-superseded claim")


class TestApplyManifestRowSupersededDirect:
    """Direct coverage of ``_apply_manifest_row_superseded`` error branches."""

    def test_malformed_manifest_raises(self, workspace: Path) -> None:
        req = _request()
        content_without_manifest = "# EX-F1-S1-T1\n\n## Description\n\nno manifest here\n"
        with pytest.raises(AmendmentError, match="manifest_row_superseded"):
            _apply_manifest_row_superseded(
                content_without_manifest,
                req,
                workspace / "BACKLOG.md",
                repo_path=_repo_path(workspace),
                staged_files=frozenset(),
            )


class TestApplyManifestRowSuperseded:
    def test_removes_absent_row_with_done_citation(self, workspace: Path) -> None:
        wu_file = workspace / "backlog" / f"{TASK_ID}.md"
        write_request(workspace, _request())
        _apply(workspace)
        rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
        assert [r.file for r in rows] == [SURVIVING_ROW]
        updated = wu_file.read_text(encoding="utf-8")
        assert MANIFEST_ROW_REMOVED_ACTION in updated
        assert STALE_ROW in updated
        assert not request_path(workspace, TASK_ID).exists()

    def test_rejected_when_file_still_exists(self, workspace: Path) -> None:
        wu_file = workspace / "backlog" / f"{TASK_ID}.md"
        before = wu_file.read_text(encoding="utf-8")
        write_request(workspace, _request(claims=[_claim(row_path=SURVIVING_ROW)]))
        with pytest.raises(AmendmentError, match="still exists"):
            _apply(workspace)
        assert wu_file.read_text(encoding="utf-8") == before

    def test_rejected_when_cited_unit_not_done(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(NOT_DONE_INDEX)
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / f"{TASK_ID}.md").write_text(WORK_UNIT_TEMPLATE.format(task_id=TASK_ID))
        done_unit_in_queue = DONE_UNIT.replace("## Status: done", "## Status: in-queue")
        (backlog_dir / f"{DONE_ID}.md").write_text(done_unit_in_queue)
        repo = tmp_path / "repo"
        (repo / "terragrunt").mkdir(parents=True)
        (repo / SURVIVING_ROW).write_text("// root.hcl\n", encoding="utf-8")
        wu_file = backlog_dir / f"{TASK_ID}.md"
        before = wu_file.read_text(encoding="utf-8")
        write_request(tmp_path, _request())
        with pytest.raises(AmendmentError, match="status"):
            apply_amendment(
                tmp_path,
                tmp_path / "BACKLOG.md",
                TASK_ID,
                repo_path=repo,
                staged_files=frozenset(),
            )
        assert wu_file.read_text(encoding="utf-8") == before

    def test_rejected_when_staged_diff_touches_removed_path(self, workspace: Path) -> None:
        wu_file = workspace / "backlog" / f"{TASK_ID}.md"
        before = wu_file.read_text(encoding="utf-8")
        write_request(workspace, _request())
        with pytest.raises(AmendmentError, match="staged diff"):
            _apply(workspace, staged=frozenset({STALE_ROW}))
        assert wu_file.read_text(encoding="utf-8") == before

    def test_post_check_rollback_on_integrity_violation(self, workspace: Path) -> None:
        wu_file = workspace / "backlog" / f"{TASK_ID}.md"
        before = wu_file.read_text(encoding="utf-8")
        write_request(workspace, _request())
        backlog_md = workspace / "BACKLOG.md"
        damaged = backlog_md.read_text(encoding="utf-8").replace(
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | None |",
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | NONEXISTENT-ID |",
        )
        backlog_md.write_text(damaged, encoding="utf-8")
        with pytest.raises(AmendmentError, match="Post-check"):
            _apply(workspace)
        assert wu_file.read_text(encoding="utf-8") == before

    def test_row_not_in_manifest_raises(self, workspace: Path) -> None:
        wu_file = workspace / "backlog" / f"{TASK_ID}.md"
        before = wu_file.read_text(encoding="utf-8")
        ghost = "terragrunt/ghost.hcl"
        write_request(workspace, _request(claims=[_claim(row_path=ghost)]))
        with pytest.raises(AmendmentError, match=r"ghost\.hcl"):
            _apply(workspace)
        assert wu_file.read_text(encoding="utf-8") == before

    def test_requires_repo_path(self, workspace: Path) -> None:
        write_request(workspace, _request())
        with pytest.raises(AmendmentError, match="repo"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID, repo_path=None, staged_files=frozenset())

    def test_requires_staged_files(self, workspace: Path) -> None:
        write_request(workspace, _request())
        with pytest.raises(AmendmentError, match="staged"):
            apply_amendment(
                workspace, workspace / "BACKLOG.md", TASK_ID, repo_path=_repo_path(workspace), staged_files=None
            )


class TestCmdApplyAmendmentManifestRowSuperseded:
    """``cmd_apply_amendment`` threads repo context for manifest_row_superseded."""

    def test_cli_apply_removes_row(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench import cli

        write_request(workspace, _request())
        repo = _repo_path(workspace)

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", workspace)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", workspace / "BACKLOG.md")
        monkeypatch.setattr(cli, "_resolve_amendment_repo_context", lambda _uid: (repo, frozenset()))

        rc = cli.cmd_apply_amendment(TASK_ID)
        assert rc == 0
        rows = parse_manifest((workspace / "backlog" / f"{TASK_ID}.md").read_text(encoding="utf-8"))
        assert [r.file for r in rows] == [SURVIVING_ROW]

    def test_reason_needs_repo_context_true_only_for_this_reason(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", workspace)
        write_request(workspace, _request())
        assert cli._amendment_reason_needs_repo_context(TASK_ID) is True

    def test_reason_needs_repo_context_false_for_other_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench import cli

        (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / f"{TASK_ID}.md").write_text(WORK_UNIT_TEMPLATE.format(task_id=TASK_ID))
        tdd_req = AmendmentRequest.from_dict(
            {
                "task_id": TASK_ID,
                "requested_at": "2026-06-14T15:00:00Z",
                "reason": "tdd_green_production_fix",
                "justification": "x",
                "files_to_add": [{"path": "src/x.py", "change": "add"}],
                "linked_acs": ["AC-TEST-001"],
            }
        )
        write_request(tmp_path, tdd_req)
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        assert cli._amendment_reason_needs_repo_context(TASK_ID) is False

    def test_reason_needs_repo_context_false_when_no_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        assert cli._amendment_reason_needs_repo_context("EX-F1-S1-T9") is False

    def test_resolve_repo_context_none_when_unit_unresolvable(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench import cli

        monkeypatch.setattr(cli, "_resolve_unit_file_and_repo_path", lambda _uid: None)
        assert cli._resolve_amendment_repo_context(TASK_ID) == (None, None)

    def test_resolve_repo_context_handles_non_git_repo(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench import cli
        from devbench.backlog import manifest as manifest_mod

        repo = _repo_path(workspace)
        monkeypatch.setattr(
            cli, "_resolve_unit_file_and_repo_path", lambda _uid: (workspace / "backlog" / f"{TASK_ID}.md", repo)
        )

        def _raise(_repo: Path) -> list[str]:
            raise RuntimeError("not a git repo")

        monkeypatch.setattr(manifest_mod, "list_staged_files", _raise)
        resolved_repo, staged = cli._resolve_amendment_repo_context(TASK_ID)
        assert resolved_repo == repo
        assert staged is None

    def test_resolve_repo_context_returns_staged_set(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench import cli
        from devbench.backlog import manifest as manifest_mod

        repo = _repo_path(workspace)
        monkeypatch.setattr(
            cli, "_resolve_unit_file_and_repo_path", lambda _uid: (workspace / "backlog" / f"{TASK_ID}.md", repo)
        )
        monkeypatch.setattr(manifest_mod, "list_staged_files", lambda _repo: ["a.py", "b.py"])
        resolved_repo, staged = cli._resolve_amendment_repo_context(TASK_ID)
        assert resolved_repo == repo
        assert staged == frozenset({"a.py", "b.py"})
