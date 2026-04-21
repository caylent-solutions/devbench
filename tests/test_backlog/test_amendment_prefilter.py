"""Unit tests for the Layer 1 PreFilter in devbench.backlog.amendment.

Each deterministic rule has at least one passing and one failing test so the
coverage report demonstrates every rule is exercised independently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from devbench.backlog.amendment import (
    AmendmentError,
    AmendmentRequest,
    PreFilter,
    _extract_ac_id,
)
from devbench.config_loader import AmendmentConfig

# ---------------------------------------------------------------------------
# Fixture content
# ---------------------------------------------------------------------------

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
"""

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | {status} | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""


@pytest.fixture
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


def _sample_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "task_id": "EX-F1-S1-T1",
        "requested_at": "2026-04-18T00:00:00+00:00",
        "reason": "tdd_green_production_fix",
        "justification": "Required for AC-TEST-001 per TDD GREEN.",
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


def _make_request(**overrides: Any) -> AmendmentRequest:
    return AmendmentRequest.from_dict(_sample_payload(**overrides))


# ---------------------------------------------------------------------------
# check_enabled
# ---------------------------------------------------------------------------


class TestCheckEnabled:
    def test_enabled_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config(enabled=True))
        pf.check_enabled()  # no raise

    def test_disabled_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config(enabled=False))
        with pytest.raises(AmendmentError, match="disabled for this backlog"):
            pf.check_enabled()


# ---------------------------------------------------------------------------
# check_reason_allowed
# ---------------------------------------------------------------------------


class TestCheckReasonAllowed:
    def test_allowed_reason_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        pf.check_reason_allowed(_make_request())

    def test_disallowed_reason_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        req = _make_request(reason="some_other_reason")
        with pytest.raises(AmendmentError, match="not in allowed reasons"):
            pf.check_reason_allowed(req)


# ---------------------------------------------------------------------------
# check_rate_limit
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    def test_zero_prior_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config(max_requests_per_execution=1))
        pf.check_rate_limit(0)

    def test_at_limit_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config(max_requests_per_execution=1))
        with pytest.raises(AmendmentError, match="rate limit exceeded"):
            pf.check_rate_limit(1)

    def test_over_limit_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config(max_requests_per_execution=2))
        with pytest.raises(AmendmentError, match="rate limit exceeded"):
            pf.check_rate_limit(3)


# ---------------------------------------------------------------------------
# check_task_exists_and_in_progress
# ---------------------------------------------------------------------------


class TestCheckTaskExistsAndInProgress:
    def test_in_progress_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        assert unit.id == "EX-F1-S1-T1"

    def test_unknown_task_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        req = _make_request(task_id="UNKNOWN-ID")
        with pytest.raises(AmendmentError, match="not found in backlog"):
            pf.check_task_exists_and_in_progress(req)

    def test_non_in_progress_rejects(self, tmp_path: Path) -> None:
        # Rebuild the backlog with a non-in-progress status.
        index = tmp_path / "BACKLOG.md"
        index.write_text(BACKLOG_INDEX_TEMPLATE.format(status="in-queue"))
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / "EX-F1-S1-T1.md").write_text(
            WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="in-queue"),
            encoding="utf-8",
        )
        pf = PreFilter(index, _default_config())
        with pytest.raises(AmendmentError, match="not in-progress"):
            pf.check_task_exists_and_in_progress(_make_request())

    def test_missing_backlog_rejects(self, tmp_path: Path) -> None:
        pf = PreFilter(tmp_path / "nonexistent-BACKLOG.md", _default_config())
        with pytest.raises(AmendmentError, match="Cannot read backlog index"):
            pf.check_task_exists_and_in_progress(_make_request())


# ---------------------------------------------------------------------------
# check_linked_acs_exist
# ---------------------------------------------------------------------------


class TestCheckLinkedAcsExist:
    def test_known_ac_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        pf.check_linked_acs_exist(_make_request(linked_acs=["AC-TEST-001"]), unit)

    def test_multiple_known_acs_pass(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        pf.check_linked_acs_exist(_make_request(linked_acs=["AC-TEST-001", "AC-FUNC-001"]), unit)

    def test_unknown_ac_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        with pytest.raises(AmendmentError, match="not found"):
            pf.check_linked_acs_exist(_make_request(linked_acs=["AC-DOES-NOT-EXIST"]), unit)


# ---------------------------------------------------------------------------
# check_files_not_already_in_manifest
# ---------------------------------------------------------------------------


class TestCheckFilesNotAlreadyInManifest:
    def test_new_file_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        pf.check_files_not_already_in_manifest(_make_request(), unit)  # src/example/parser.py not in manifest

    def test_duplicate_file_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        # The template's manifest already lists tests/test_example.py
        req = _make_request(files_to_add=[{"path": "tests/test_example.py", "change": "dup"}])
        with pytest.raises(AmendmentError, match="already declared in Changes Manifest"):
            pf.check_files_not_already_in_manifest(req, unit)

    def test_malformed_manifest_raises_amendment_error(self, tmp_path: Path) -> None:
        # Build a work unit whose Changes Manifest is malformed
        index = tmp_path / "BACKLOG.md"
        index.write_text(BACKLOG_INDEX_TEMPLATE.format(status="in-progress"))
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        content = WORK_UNIT_TEMPLATE.format(task_id="EX-F1-S1-T1", status="in-progress")
        # Inject a three-column row into the manifest
        content = content.replace(
            "| `tests/test_example.py` | add new tests |",
            "| `tests/test_example.py` | add | bad |",
        )
        (backlog_dir / "EX-F1-S1-T1.md").write_text(content, encoding="utf-8")
        pf = PreFilter(index, _default_config())
        unit = pf.check_task_exists_and_in_progress(_make_request())
        with pytest.raises(AmendmentError, match="Cannot read current Changes Manifest"):
            pf.check_files_not_already_in_manifest(_make_request(), unit)


# ---------------------------------------------------------------------------
# check_files_in_staged_diff
# ---------------------------------------------------------------------------


class TestCheckFilesInStagedDiff:
    def test_all_files_present_passes(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        pf.check_files_in_staged_diff(_make_request(), frozenset({"src/example/parser.py"}))

    def test_missing_file_rejects(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        with pytest.raises(AmendmentError, match="not in the staged diff"):
            pf.check_files_in_staged_diff(_make_request(), frozenset({"other/file.py"}))


# ---------------------------------------------------------------------------
# run_all integration
# ---------------------------------------------------------------------------


class TestRunAll:
    def test_all_pass_end_to_end(self, tmp_backlog: Path) -> None:
        pf = PreFilter(tmp_backlog, _default_config())
        pf.run_all(
            _make_request(),
            staged_files=frozenset({"src/example/parser.py"}),
            prior_applied_count=0,
        )

    def test_short_circuits_on_first_failure(self, tmp_backlog: Path) -> None:
        # Config says enabled=False; the very first check (enabled) must fail
        # without needing any of the later context.
        pf = PreFilter(tmp_backlog, _default_config(enabled=False))
        with pytest.raises(AmendmentError, match="disabled"):
            pf.run_all(_make_request())

    def test_run_all_skips_staged_check_when_none(self, tmp_backlog: Path) -> None:
        # Passing staged_files=None means the check is skipped; everything else passes.
        pf = PreFilter(tmp_backlog, _default_config())
        pf.run_all(_make_request(), staged_files=None, prior_applied_count=0)


# ---------------------------------------------------------------------------
# _extract_ac_id helper
# ---------------------------------------------------------------------------


class TestExtractAcId:
    def test_typical_line(self) -> None:
        assert _extract_ac_id("AC-TEST-003 UTF-8 BOM prefix is stripped") == "AC-TEST-003"

    def test_leading_whitespace(self) -> None:
        assert _extract_ac_id("   AC-FUNC-001 something") == "AC-FUNC-001"

    def test_empty_line_returns_empty_string(self) -> None:
        assert _extract_ac_id("") == ""

    def test_whitespace_only_returns_empty_string(self) -> None:
        assert _extract_ac_id("   \t\n") == ""

    def test_single_token(self) -> None:
        assert _extract_ac_id("AC-ONLY-ID") == "AC-ONLY-ID"
