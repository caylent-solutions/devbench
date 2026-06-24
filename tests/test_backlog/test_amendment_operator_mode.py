"""Tests for operator-mode fields and validation in AmendmentRequest.

AC-242a-1: from_dict validates the seven new fields with per-field error strings.

The seven new fields (operator_mode is the bypass flag; the other seven are patch
fields for operator amendments):

  operator_mode        bool, default False
  files_to_remove      list[str], default []
  target_repository    str, default ""
  description_patch    str, default ""
  approach_patch       str, default ""
  title_patch          str, default ""
  dod_patch            str, default ""
  section_patches      dict[str, str], default {}

Each field has a specific error string when the payload has the wrong type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from devbench.backlog.amendment import (
    AmendmentError,
    AmendmentRequest,
    PreFilter,
)
from devbench.config_loader import AmendmentConfig

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | {status} | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""


WORK_UNIT_TEMPLATE = """\
# {task_id}: Sample Task

## Status: {status}

## Description

Test task description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 new test asserts something
- [ ] AC-FUNC-001 something works end-to-end

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | add new tests |

## Definition of Done

- [ ] All AC checked

## Comments
"""


@pytest.fixture()
def tmp_backlog(tmp_path: Path) -> Path:
    """Build a minimal in-progress backlog; return path to BACKLOG.md."""
    index = tmp_path / "BACKLOG.md"
    index.write_text(BACKLOG_INDEX_TEMPLATE.format(status="in-progress"))
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "EX-F1-S1-T1.md").write_text(
        WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="in-progress"),
        encoding="utf-8",
    )
    return index


def _base_payload(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid payload dict (new fields omitted -> defaults)."""
    base: dict[str, Any] = {
        "task_id": "EX-F1-S1-T1",
        "requested_at": "2026-06-07T00:00:00+00:00",
        "reason": "tdd_green_production_fix",
        "justification": "Required for AC-TEST-001.",
        "files_to_add": [{"path": "src/example/parser.py", "change": "utf-8-sig codec"}],
        "linked_acs": ["AC-TEST-001"],
    }
    base.update(overrides)
    return base


def _default_config(**overrides: Any) -> AmendmentConfig:
    defaults: dict[str, Any] = {
        "enabled": True,
        "allowed_reasons": frozenset({"tdd_green_production_fix"}),
        "max_requests_per_execution": 1,
    }
    defaults.update(overrides)
    return AmendmentConfig(**defaults)


class TestOperatorModeField:
    """operator_mode defaults to False; truthy JSON bool sets it True."""

    def test_defaults_to_false_when_absent(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.operator_mode is False

    def test_true_is_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(operator_mode=True))
        assert req.operator_mode is True

    def test_false_is_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(operator_mode=False))
        assert req.operator_mode is False

    def test_non_bool_rejects(self) -> None:
        with pytest.raises(ValueError, match="operator_mode must be a bool"):
            AmendmentRequest.from_dict(_base_payload(operator_mode="yes"))

    def test_integer_rejects(self) -> None:
        with pytest.raises(ValueError, match="operator_mode must be a bool"):
            AmendmentRequest.from_dict(_base_payload(operator_mode=1))


class TestFilesToRemoveField:
    """files_to_remove: list[str], default []."""

    def test_defaults_to_empty_list(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.files_to_remove == []

    def test_valid_list_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(files_to_remove=["src/old.py"]))
        assert req.files_to_remove == ["src/old.py"]

    def test_non_list_rejects(self) -> None:
        with pytest.raises(ValueError, match="files_to_remove must be a list"):
            AmendmentRequest.from_dict(_base_payload(files_to_remove="src/old.py"))

    def test_non_string_entry_rejects(self) -> None:
        with pytest.raises(ValueError, match="files_to_remove entries must be strings"):
            AmendmentRequest.from_dict(_base_payload(files_to_remove=[{"path": "x"}]))

    def test_empty_string_entry_rejects(self) -> None:
        with pytest.raises(ValueError, match="files_to_remove entries must be non-empty strings"):
            AmendmentRequest.from_dict(_base_payload(files_to_remove=[""]))


class TestTargetRepositoryField:
    """target_repository: str, default ""."""

    def test_defaults_to_empty_string(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.target_repository == ""

    def test_valid_value_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(target_repository="org/new-repo"))
        assert req.target_repository == "org/new-repo"

    def test_non_string_rejects(self) -> None:
        with pytest.raises(ValueError, match="target_repository must be a string"):
            AmendmentRequest.from_dict(_base_payload(target_repository=123))

    def test_list_rejects(self) -> None:
        with pytest.raises(ValueError, match="target_repository must be a string"):
            AmendmentRequest.from_dict(_base_payload(target_repository=["org/repo"]))


class TestDescriptionPatchField:
    """description_patch: str, default ""."""

    def test_defaults_to_empty_string(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.description_patch == ""

    def test_valid_value_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(description_patch="New description text."))
        assert req.description_patch == "New description text."

    def test_non_string_rejects(self) -> None:
        with pytest.raises(ValueError, match="description_patch must be a string"):
            AmendmentRequest.from_dict(_base_payload(description_patch=42))

    def test_list_rejects(self) -> None:
        with pytest.raises(ValueError, match="description_patch must be a string"):
            AmendmentRequest.from_dict(_base_payload(description_patch=["line"]))


class TestApproachPatchField:
    """approach_patch: str, default ""."""

    def test_defaults_to_empty_string(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.approach_patch == ""

    def test_valid_value_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(approach_patch="1. RED. 2. GREEN."))
        assert req.approach_patch == "1. RED. 2. GREEN."

    def test_non_string_rejects(self) -> None:
        with pytest.raises(ValueError, match="approach_patch must be a string"):
            AmendmentRequest.from_dict(_base_payload(approach_patch=99))


class TestTitlePatchField:
    """title_patch: str, default ""."""

    def test_defaults_to_empty_string(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.title_patch == ""

    def test_valid_value_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(title_patch="New Task Title"))
        assert req.title_patch == "New Task Title"

    def test_non_string_rejects(self) -> None:
        with pytest.raises(ValueError, match="title_patch must be a string"):
            AmendmentRequest.from_dict(_base_payload(title_patch=True))

    def test_list_rejects(self) -> None:
        with pytest.raises(ValueError, match="title_patch must be a string"):
            AmendmentRequest.from_dict(_base_payload(title_patch=["Title"]))


class TestDodPatchField:
    """dod_patch: str, default ""."""

    def test_defaults_to_empty_string(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.dod_patch == ""

    def test_valid_value_accepted(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload(dod_patch="- [ ] All AC checked.\n- [ ] ruff passes."))
        assert req.dod_patch == "- [ ] All AC checked.\n- [ ] ruff passes."

    def test_non_string_rejects(self) -> None:
        with pytest.raises(ValueError, match="dod_patch must be a string"):
            AmendmentRequest.from_dict(_base_payload(dod_patch={"key": "val"}))


class TestSectionPatchesField:
    """section_patches: dict[str, str], default {}."""

    def test_defaults_to_empty_dict(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        assert req.section_patches == {}

    def test_valid_dict_accepted(self) -> None:
        patches = {"## Related Specifications": "- Spec Section 4 E7.F1.S1."}
        req = AmendmentRequest.from_dict(_base_payload(section_patches=patches))
        assert req.section_patches == patches

    def test_non_dict_rejects(self) -> None:
        with pytest.raises(ValueError, match="section_patches must be a dict"):
            AmendmentRequest.from_dict(_base_payload(section_patches=["key: val"]))

    def test_non_string_key_rejects(self) -> None:
        with pytest.raises(ValueError, match="section_patches keys must be strings"):
            AmendmentRequest.from_dict(_base_payload(section_patches={1: "value"}))

    def test_non_string_value_rejects(self) -> None:
        with pytest.raises(ValueError, match="section_patches values must be strings"):
            AmendmentRequest.from_dict(_base_payload(section_patches={"## Sec": 42}))


class TestRoundTrip:
    """to_dict / from_dict must be invertible for the new fields."""

    def test_roundtrip_with_operator_fields(self) -> None:
        original = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_remove=["src/old.py"],
                target_repository="new-org/new-repo",
                description_patch="Updated description.",
                approach_patch="Updated approach.",
                title_patch="Updated Title",
                dod_patch="- [ ] Updated DoD.",
                section_patches={"## Related Specifications": "- AC-242-1."},
            )
        )
        rebuilt = AmendmentRequest.from_dict(original.to_dict())
        assert rebuilt.operator_mode is True
        assert rebuilt.files_to_remove == ["src/old.py"]
        assert rebuilt.target_repository == "new-org/new-repo"
        assert rebuilt.description_patch == "Updated description."
        assert rebuilt.approach_patch == "Updated approach."
        assert rebuilt.title_patch == "Updated Title"
        assert rebuilt.dod_patch == "- [ ] Updated DoD."
        assert rebuilt.section_patches == {"## Related Specifications": "- AC-242-1."}

    def test_roundtrip_defaults(self) -> None:
        req = AmendmentRequest.from_dict(_base_payload())
        rebuilt = AmendmentRequest.from_dict(req.to_dict())
        assert rebuilt.operator_mode is False
        assert rebuilt.files_to_remove == []
        assert rebuilt.target_repository == ""
        assert rebuilt.description_patch == ""
        assert rebuilt.approach_patch == ""
        assert rebuilt.title_patch == ""
        assert rebuilt.dod_patch == ""
        assert rebuilt.section_patches == {}


class TestPreFilterOperatorMode:
    """run_all with operator_mode=True skips check_task_exists_and_in_progress."""

    def test_non_operator_mode_blocks_non_in_progress(self, tmp_backlog: Path) -> None:
        """Without operator_mode, a blocked task is rejected."""
        index_path = tmp_backlog.parent / "BACKLOG.md"
        index_path.write_text(BACKLOG_INDEX_TEMPLATE.format(status="blocked"))
        wu_path = tmp_backlog.parent / "backlog" / "EX-F1-S1-T1.md"
        wu_path.write_text(
            WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="blocked"),
            encoding="utf-8",
        )
        pf = PreFilter(tmp_backlog, _default_config())
        req = AmendmentRequest.from_dict(_base_payload())
        with pytest.raises(AmendmentError, match="not in-progress"):
            pf.run_all(req)

    def test_operator_mode_bypasses_in_progress_gate(self, tmp_backlog: Path) -> None:
        """With operator_mode=True, a blocked task is NOT rejected by the status gate."""
        index_path = tmp_backlog.parent / "BACKLOG.md"
        index_path.write_text(BACKLOG_INDEX_TEMPLATE.format(status="blocked"))
        wu_path = tmp_backlog.parent / "backlog" / "EX-F1-S1-T1.md"
        wu_path.write_text(
            WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="blocked"),
            encoding="utf-8",
        )
        pf = PreFilter(tmp_backlog, _default_config())
        req = AmendmentRequest.from_dict(_base_payload(operator_mode=True, linked_acs=[]))
        pf.run_all(req, operator_mode=True)

    def test_operator_mode_still_enforces_check_enabled(self, tmp_backlog: Path) -> None:
        """operator_mode does not bypass the enabled check."""
        pf = PreFilter(tmp_backlog, _default_config(enabled=False))
        req = AmendmentRequest.from_dict(_base_payload(operator_mode=True))
        with pytest.raises(AmendmentError, match="disabled for this backlog"):
            pf.run_all(req, operator_mode=True)


class TestApplyOperatorAmendmentErrorPaths:
    """Coverage for the two exception paths in apply_operator_amendment."""

    def test_missing_manifest_section_raises_amendment_error(self, tmp_path: Path) -> None:
        """ManifestParseError from append_rows is re-raised as AmendmentError."""
        from devbench.backlog.amendment import apply_operator_amendment

        index = tmp_path / "BACKLOG.md"
        index.write_text(BACKLOG_INDEX_TEMPLATE.format(status="blocked"))
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_content = """\
# EX-F1-S1-T1: Sample Task

## Status: blocked

## Description

No manifest section here.

## Comments
"""
        (backlog_dir / "EX-F1-S1-T1.md").write_text(wu_content, encoding="utf-8")
        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[{"path": "src/new.py", "change": "add new module"}],
                linked_acs=[],
            )
        )
        with pytest.raises(AmendmentError, match="Cannot apply operator amendment"):
            apply_operator_amendment(index, "EX-F1-S1-T1", req)

    def test_manifest_row_with_em_dash_raises_amendment_error(self, tmp_path: Path) -> None:
        """ValueError from ManifestRow (em-dash in path) is re-raised as AmendmentError."""
        from unittest.mock import patch

        from devbench.backlog.amendment import apply_operator_amendment
        from devbench.backlog.manifest import ManifestRow

        index = tmp_path / "BACKLOG.md"
        index.write_text(BACKLOG_INDEX_TEMPLATE.format(status="blocked"))
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / "EX-F1-S1-T1.md").write_text(
            WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="blocked"),
            encoding="utf-8",
        )
        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[{"path": "src/new.py", "change": "add new module"}],
                linked_acs=[],
            )
        )

        def _raise_value_error(file: str, change: str) -> ManifestRow:
            raise ValueError("ManifestRow mock error")

        with patch("devbench.backlog.amendment.ManifestRow", side_effect=_raise_value_error):
            with pytest.raises(AmendmentError, match="Operator amendment contains invalid manifest row"):
                apply_operator_amendment(index, "EX-F1-S1-T1", req)


TWO_ROW_WORK_UNIT_TEMPLATE = """\
# {task_id}: Sample Task

## Status: {status}

## Description

Test task description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 new test asserts something
- [ ] AC-FUNC-001 something works end-to-end

## Changes Manifest

| File | Change |
|------|--------|
| `terragrunt/terragrunt.hcl` | modify version floor |
| `terragrunt/root.hcl` | modify version floor |

## Definition of Done

- [ ] All AC checked

## Comments
"""


@pytest.fixture()
def tmp_backlog_two_rows(tmp_path: Path) -> Path:
    """Build an in-progress backlog whose task has a two-row Changes Manifest."""
    index = tmp_path / "BACKLOG.md"
    index.write_text(BACKLOG_INDEX_TEMPLATE.format(status="in-progress"))
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "EX-F1-S1-T1.md").write_text(
        TWO_ROW_WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="in-progress"),
        encoding="utf-8",
    )
    return index


class TestApplyOperatorAmendmentFilesToRemove:
    """AC-1: an operator-mode amendment with files_to_remove removes those rows."""

    def test_removes_named_row(self, tmp_backlog_two_rows: Path) -> None:
        from devbench.backlog.amendment import apply_operator_amendment
        from devbench.backlog.manifest import parse_manifest

        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[],
                files_to_remove=["terragrunt/terragrunt.hcl"],
                linked_acs=[],
            )
        )
        rc = apply_operator_amendment(tmp_backlog_two_rows, "EX-F1-S1-T1", req)
        assert rc == 0

        wu_file = tmp_backlog_two_rows.parent / "backlog" / "EX-F1-S1-T1.md"
        updated = wu_file.read_text(encoding="utf-8")
        rows = parse_manifest(updated)
        assert [r.file for r in rows] == ["terragrunt/root.hcl"]

    def test_writes_row_removed_audit_comment(self, tmp_backlog_two_rows: Path) -> None:
        from devbench.backlog.amendment import (
            MANIFEST_ROW_REMOVED_ACTION,
            apply_operator_amendment,
        )

        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[],
                files_to_remove=["terragrunt/terragrunt.hcl"],
                linked_acs=[],
            )
        )
        apply_operator_amendment(tmp_backlog_two_rows, "EX-F1-S1-T1", req)

        wu_file = tmp_backlog_two_rows.parent / "backlog" / "EX-F1-S1-T1.md"
        updated = wu_file.read_text(encoding="utf-8")
        assert MANIFEST_ROW_REMOVED_ACTION in updated
        assert "terragrunt/terragrunt.hcl" in updated

    def test_add_and_remove_in_one_request(self, tmp_backlog_two_rows: Path) -> None:
        from devbench.backlog.amendment import apply_operator_amendment
        from devbench.backlog.manifest import parse_manifest

        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[{"path": "terragrunt/env.hcl", "change": "add env config"}],
                files_to_remove=["terragrunt/terragrunt.hcl"],
                linked_acs=[],
            )
        )
        rc = apply_operator_amendment(tmp_backlog_two_rows, "EX-F1-S1-T1", req)
        assert rc == 0

        wu_file = tmp_backlog_two_rows.parent / "backlog" / "EX-F1-S1-T1.md"
        rows = parse_manifest(wu_file.read_text(encoding="utf-8"))
        files = {r.file for r in rows}
        assert "terragrunt/terragrunt.hcl" not in files
        assert "terragrunt/root.hcl" in files
        assert "terragrunt/env.hcl" in files

    def test_absent_row_raises_and_restores_file(self, tmp_backlog_two_rows: Path) -> None:
        from devbench.backlog.amendment import apply_operator_amendment

        wu_file = tmp_backlog_two_rows.parent / "backlog" / "EX-F1-S1-T1.md"
        before = wu_file.read_text(encoding="utf-8")

        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[],
                files_to_remove=["terragrunt/does-not-exist.hcl"],
                linked_acs=[],
            )
        )
        with pytest.raises(AmendmentError) as exc:
            apply_operator_amendment(tmp_backlog_two_rows, "EX-F1-S1-T1", req)
        assert "terragrunt/does-not-exist.hcl" in str(exc.value)

        assert wu_file.read_text(encoding="utf-8") == before

    def test_post_check_rollback_on_integrity_violation(self, tmp_backlog_two_rows: Path) -> None:
        from devbench.backlog.amendment import apply_operator_amendment

        index = tmp_backlog_two_rows
        damaged = index.read_text().replace(
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | None |",
            "| EX-F1-S1-T1 | Sample Task | Task | in-progress | NONEXISTENT-ID |",
        )
        index.write_text(damaged)

        wu_file = index.parent / "backlog" / "EX-F1-S1-T1.md"
        before = wu_file.read_text(encoding="utf-8")

        req = AmendmentRequest.from_dict(
            _base_payload(
                operator_mode=True,
                files_to_add=[],
                files_to_remove=["terragrunt/terragrunt.hcl"],
                linked_acs=[],
            )
        )
        with pytest.raises(AmendmentError, match="Layer-3 post-check failed"):
            apply_operator_amendment(index, "EX-F1-S1-T1", req)

        assert wu_file.read_text(encoding="utf-8") == before
