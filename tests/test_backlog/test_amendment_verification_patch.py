"""Tests for the verification-directive amendment path (reason=verification_directive_defect).

A judge-gated, config-gated amendment path that lets the pipeline repair an
objectively-defective ``## Verification`` directive (stale assertion superseded
by a DONE unit, syntactic regex/quoting bug, or identifier rename landed by a
DONE sibling) without an operator stop-window -- while deterministic guards
ensure the rewritten directive can never be weaker than the original (same AC
ids, same type, same expect-exit).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from devbench.backlog.amendment import (
    ALLOWED_AMENDMENT_REASONS,
    REASON_VERIFICATION_DIRECTIVE_DEFECT,
    AmendmentError,
    AmendmentRequest,
    PreFilter,
    VerificationPatch,
    apply_amendment,
    read_request,
    request_path,
    write_request,
)
from devbench.config_loader import AmendmentConfig

TASK_ID = "EX-F1-S1-T1"
DONE_ID = "EX-F1-S1-T0"

DEFECTIVE_DIRECTIVE = (
    "- VERIFY AC-5 | type=command | "
    'cmd=`test "$(grep -cE "^variable \\"[a-z_]+_source\\"" variables.tf)" = "6"` | expect-exit=0'
)
FIXED_DIRECTIVE = (
    "- VERIFY AC-5 | type=command | "
    'cmd=`test "$(grep -cE "^variable \\"[a-z0-9_]+_source\\"" variables.tf)" = "6"` | expect-exit=0'
)

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

- [ ] AC-5 the variable count matches

## Changes Manifest

| File | Change |
|------|--------|
| `variables.tf` | modify |

## Definition of Done

- [ ] All AC checked

## Verification

{directive}

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

Landed work that renamed/removed things the directive asserted.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-1 landed

## Changes Manifest

| File | Change |
|------|--------|
| `variables.tf` | modify |

## Definition of Done

- [ ] All AC checked
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Minimal workspace: BACKLOG.md + one in-progress unit with a defective directive + one done sibling."""
    (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    unit_md = WORK_UNIT_TEMPLATE.format(task_id=TASK_ID, directive=DEFECTIVE_DIRECTIVE)
    (backlog_dir / f"{TASK_ID}.md").write_text(unit_md)
    (backlog_dir / f"{DONE_ID}.md").write_text(DONE_UNIT)
    return tmp_path


def _patch(
    before: str = DEFECTIVE_DIRECTIVE,
    after: str = FIXED_DIRECTIVE,
    cited: list[str] | None = None,
    evidence: str = "grep -cE with [a-z_] cannot match route53_record_source; digit-blind regex",
) -> VerificationPatch:
    return VerificationPatch(
        before=before,
        after=after,
        cited_done_units=cited if cited is not None else [],
        evidence=evidence,
    )


def _request(
    patches: list[VerificationPatch] | None = None,
    reason: str = REASON_VERIFICATION_DIRECTIVE_DEFECT,
) -> AmendmentRequest:
    return AmendmentRequest(
        task_id=TASK_ID,
        requested_at="2026-06-12T15:00:00Z",
        reason=reason,
        justification="VERIFY AC-5 regex is digit-blind; fix preserves AC ids, type, and expect-exit",
        files_to_add=[],
        linked_acs=["AC-5"],
        verification_patches=patches if patches is not None else [_patch()],
    )


def _gated_config(allow: bool) -> AmendmentConfig:
    return AmendmentConfig(allow_verification_directive_amendments=allow)


class TestVerificationPatchParsing:
    def test_reason_constant_registered(self) -> None:
        assert REASON_VERIFICATION_DIRECTIVE_DEFECT == "verification_directive_defect"
        assert REASON_VERIFICATION_DIRECTIVE_DEFECT in ALLOWED_AMENDMENT_REASONS

    def test_round_trip_through_dict(self) -> None:
        req = _request(patches=[_patch(cited=[DONE_ID])])
        rebuilt = AmendmentRequest.from_dict(req.to_dict())
        assert rebuilt.verification_patches == req.verification_patches
        assert rebuilt.reason == REASON_VERIFICATION_DIRECTIVE_DEFECT

    def test_from_dict_defaults_to_empty_patches(self) -> None:
        req = AmendmentRequest(
            task_id=TASK_ID,
            requested_at="2026-06-12T15:00:00Z",
            reason="tdd_green_production_fix",
            justification="x",
            files_to_add=[],
            linked_acs=[],
        )
        rebuilt = AmendmentRequest.from_dict(req.to_dict())
        assert rebuilt.verification_patches == []

    @pytest.mark.parametrize(
        "field,value",
        [
            ("before", ""),
            ("after", ""),
            ("evidence", ""),
        ],
    )
    def test_empty_required_patch_field_rejected(self, field: str, value: str) -> None:
        kwargs: dict[str, Any] = {
            "before": DEFECTIVE_DIRECTIVE,
            "after": FIXED_DIRECTIVE,
            "cited_done_units": [],
            "evidence": "e",
        }
        kwargs[field] = value
        with pytest.raises(ValueError):
            VerificationPatch(**kwargs)

    def test_from_dict_rejects_non_list_patches(self) -> None:
        data = _request().to_dict()
        data["verification_patches"] = {"before": "x"}
        with pytest.raises(ValueError):
            AmendmentRequest.from_dict(data)

    def test_identical_before_after_rejected(self) -> None:
        with pytest.raises(ValueError):
            _patch(after=DEFECTIVE_DIRECTIVE)


class TestPreFilterGating:
    def test_gate_defaults_to_on(self, workspace: Path) -> None:
        """allow_verification_directive_amendments defaults to True (operator decision 2026-06-12)."""
        assert AmendmentConfig().allow_verification_directive_amendments is True
        pf = PreFilter(workspace / "BACKLOG.md", AmendmentConfig())
        pf.run_all(_request(), prior_applied_count=0)

    def test_rejected_when_config_gate_off(self, workspace: Path) -> None:
        pf = PreFilter(workspace / "BACKLOG.md", _gated_config(allow=False))
        with pytest.raises(AmendmentError, match="allow_verification_directive_amendments"):
            pf.run_all(_request(), prior_applied_count=0)

    def test_accepted_when_config_gate_on(self, workspace: Path) -> None:
        pf = PreFilter(workspace / "BACKLOG.md", _gated_config(allow=True))
        pf.run_all(_request(), prior_applied_count=0)

    def test_rejected_without_patches(self, workspace: Path) -> None:
        pf = PreFilter(workspace / "BACKLOG.md", _gated_config(allow=True))
        with pytest.raises(AmendmentError, match="verification_patches"):
            pf.run_all(_request(patches=[]), prior_applied_count=0)

    def test_rejected_when_manifest_rows_mixed_in(self, workspace: Path) -> None:
        from devbench.backlog.amendment import AmendmentFileEntry

        mixed = AmendmentRequest(
            task_id=TASK_ID,
            requested_at="2026-06-12T15:00:00Z",
            reason=REASON_VERIFICATION_DIRECTIVE_DEFECT,
            justification="x",
            files_to_add=[AmendmentFileEntry(path="extra.py", change="add")],
            linked_acs=["AC-5"],
            verification_patches=[_patch()],
        )
        pf = PreFilter(workspace / "BACKLOG.md", _gated_config(allow=True))
        with pytest.raises(AmendmentError, match="files_to_add"):
            pf.run_all(mixed, prior_applied_count=0)

    def test_other_reasons_must_not_carry_patches(self, workspace: Path) -> None:
        req = AmendmentRequest(
            task_id=TASK_ID,
            requested_at="2026-06-12T15:00:00Z",
            reason="tdd_green_production_fix",
            justification="x",
            files_to_add=[],
            linked_acs=["AC-5"],
            verification_patches=[_patch()],
        )
        pf = PreFilter(workspace / "BACKLOG.md", _gated_config(allow=True))
        with pytest.raises(AmendmentError, match="verification_patches"):
            pf.run_all(req, prior_applied_count=0)


class TestApplyVerificationAmendment:
    def _write_and_apply(self, workspace: Path, req: AmendmentRequest) -> None:
        write_request(workspace, req)
        apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_happy_path_rewrites_directive(self, workspace: Path) -> None:
        self._write_and_apply(workspace, _request(patches=[_patch(cited=[DONE_ID])]))
        content = (workspace / "backlog" / f"{TASK_ID}.md").read_text()
        verification_section = content.split("## Verification")[1].split("## Comments")[0]
        assert FIXED_DIRECTIVE in verification_section
        assert DEFECTIVE_DIRECTIVE not in verification_section

    def test_happy_path_appends_audit_comment(self, workspace: Path) -> None:
        self._write_and_apply(workspace, _request(patches=[_patch(cited=[DONE_ID])]))
        content = (workspace / "backlog" / f"{TASK_ID}.md").read_text()
        assert "[VERIFICATION_AMENDMENT]" in content
        assert DONE_ID in content.split("[VERIFICATION_AMENDMENT]")[1]

    def test_happy_path_deletes_request(self, workspace: Path) -> None:
        self._write_and_apply(workspace, _request())
        assert not request_path(workspace, TASK_ID).exists()

    def test_before_line_not_found_rejected_and_unchanged(self, workspace: Path) -> None:
        ghost = DEFECTIVE_DIRECTIVE.replace("AC-5", "AC-99")
        req = _request(patches=[_patch(before=ghost, after=ghost.replace("[a-z_]", "[a-z0-9_]"))])
        write_request(workspace, req)
        original = (workspace / "backlog" / f"{TASK_ID}.md").read_text()
        with pytest.raises(AmendmentError, match="not found"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)
        assert (workspace / "backlog" / f"{TASK_ID}.md").read_text() == original

    def test_ac_id_change_rejected(self, workspace: Path) -> None:
        req = _request(patches=[_patch(after=FIXED_DIRECTIVE.replace("AC-5", "AC-6"))])
        write_request(workspace, req)
        with pytest.raises(AmendmentError, match="AC id"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_type_weakening_rejected(self, workspace: Path) -> None:
        deferred = '- VERIFY AC-5 | type=deferred | owner=operator | reason="cannot run"'
        req = _request(patches=[_patch(after=deferred)])
        write_request(workspace, req)
        with pytest.raises(AmendmentError, match="type"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_expect_exit_change_rejected(self, workspace: Path) -> None:
        req = _request(patches=[_patch(after=FIXED_DIRECTIVE.replace("expect-exit=0", "expect-exit=1"))])
        write_request(workspace, req)
        with pytest.raises(AmendmentError, match="expect-exit"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_citation_of_non_done_unit_rejected(self, workspace: Path) -> None:
        req = _request(patches=[_patch(cited=[TASK_ID])])
        write_request(workspace, req)
        with pytest.raises(AmendmentError, match="done"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_citation_of_unknown_unit_rejected(self, workspace: Path) -> None:
        req = _request(patches=[_patch(cited=["EX-F9-S9-T9"])])
        write_request(workspace, req)
        with pytest.raises(AmendmentError, match="EX-F9-S9-T9"):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_after_with_em_dash_restores_original(self, workspace: Path) -> None:
        em = chr(0x2014)
        bad_after = FIXED_DIRECTIVE.replace("variables.tf", f"variables{em}.tf")
        req = _request(patches=[_patch(after=bad_after)])
        write_request(workspace, req)
        original = (workspace / "backlog" / f"{TASK_ID}.md").read_text()
        with pytest.raises(AmendmentError):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)
        assert (workspace / "backlog" / f"{TASK_ID}.md").read_text() == original

    def test_unparseable_after_rejected(self, workspace: Path) -> None:
        req = _request(patches=[_patch(after="- VERIFY AC-5 this is not a directive")])
        write_request(workspace, req)
        with pytest.raises(AmendmentError):
            apply_amendment(workspace, workspace / "BACKLOG.md", TASK_ID)

    def test_request_survives_write_read_round_trip(self, workspace: Path) -> None:
        req = _request(patches=[_patch(cited=[DONE_ID])])
        write_request(workspace, req)
        loaded = read_request(workspace, TASK_ID)
        assert loaded.verification_patches == req.verification_patches
