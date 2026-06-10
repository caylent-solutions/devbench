"""Tests for BacklogManager._check_verification_contract (Workstream A).

Covers:
- executable AC with no VERIFY directive -> warning (default) / error (strict)
- executable AC covered by an executable VERIFY directive -> no finding
- executable AC covered by type=deferred -> no finding
- executable AC covered only by type=judge -> still flagged
- DoD item asserting a runnable outcome with no AC reference -> warning/error
- DoD item asserting a runnable outcome that cites an existing AC -> no finding
- non-executable AC with no VERIFY -> no finding
- malformed VERIFY directive -> always error (even non-strict)
- clean unit -> no verification-contract findings
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

_REPO = "caylent-solutions/devbench"


def _make_index(tmp_path: Path, unit_id: str) -> Path:
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
        f"| {unit_id} | Task Title | Task | in-queue | none | {_REPO} | `backlog/{unit_id}.md` |\n",
        encoding="utf-8",
    )
    return idx


def _make_task(
    backlog_dir: Path,
    unit_id: str,
    *,
    ac_block: str,
    dod_block: str,
    verification_block: str = "",
) -> None:
    verification = f"## Verification\n\n{verification_block}\n\n" if verification_block else ""
    (backlog_dir / f"{unit_id}.md").write_text(
        f"# {unit_id}: Task Title\n\n"
        f"## Status: in-queue\n\n"
        f"## Target Repository\n\n- **Repo:** `{_REPO}`\n\n"
        f"## Description\n\nTest task.\n\n"
        f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        f"## Acceptance Criteria\n\n{ac_block}\n\n"
        f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/f.py` | modify |\n\n"
        f"## Definition of Done\n\n{dod_block}\n\n"
        f"{verification}"
        f"## TDD Cycle Log\n\n## Comments\n",
        encoding="utf-8",
    )


def _validate(tmp_path: Path, *, strict: bool = False) -> tuple[list[str], list[str]]:
    idx = tmp_path / "BACKLOG.md"
    cfg = RuntimeConfig(repos={_REPO: RepoConfig()})
    with patch("devbench.config.RUNTIME_CONFIG", cfg):
        return BacklogManager().validate_with_warnings(idx, tmp_path, strict=strict)


def _contract_findings(items: list[str]) -> list[str]:
    return [i for i in items if "Verification Contract" in i or "malformed '## Verification'" in i]


# ---------------------------------------------------------------------------
# Finding 1: executable-AC coverage
# ---------------------------------------------------------------------------


def test_executable_ac_without_verify_warns_then_errors_under_strict(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: a real terragrunt apply succeeds and creates the bucket",
        dod_block="- [ ] All ACs checked",
    )
    errors, warnings = _validate(tmp_path)
    assert _contract_findings(errors) == []
    assert any("executable Acceptance Criterion AC-1" in w for w in warnings)

    errors_s, warnings_s = _validate(tmp_path, strict=True)
    assert any("executable Acceptance Criterion AC-1" in e for e in errors_s)
    assert _contract_findings(warnings_s) == []


def test_executable_ac_with_matching_verify_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: a real terragrunt apply succeeds and creates the bucket",
        dod_block="- [ ] All ACs checked",
        verification_block="- VERIFY AC-1 | type=terratest | cmd=`make tf-test UNIT=x` | expect-exit=0",
    )
    errors, warnings = _validate(tmp_path, strict=True)
    assert _contract_findings(errors) == []
    assert _contract_findings(warnings) == []


def test_executable_ac_covered_by_deferred_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: a real terraform apply provisions prod",
        dod_block="- [ ] All ACs checked",
        verification_block='- VERIFY AC-1 | type=deferred | owner=operator | reason="prod is operator-only"',
    )
    errors, _ = _validate(tmp_path, strict=True)
    assert _contract_findings(errors) == []


def test_executable_ac_covered_only_by_judge_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: a real terraform apply provisions prod",
        dod_block="- [ ] All ACs checked",
        verification_block="- VERIFY AC-1 | type=judge",
    )
    errors, _ = _validate(tmp_path, strict=True)
    assert any("executable Acceptance Criterion AC-1" in e for e in errors)


def test_non_executable_ac_without_verify_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the module follows SOLID and is documented in the README",
        dod_block="- [ ] All ACs checked",
    )
    errors, warnings = _validate(tmp_path, strict=True)
    assert _contract_findings(errors) == []
    assert _contract_findings(warnings) == []


# ---------------------------------------------------------------------------
# Finding 2: DoD/AC agreement
# ---------------------------------------------------------------------------


def test_dod_runnable_claim_without_ac_reference_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the module follows SOLID",
        dod_block="- [ ] make tf-test passes for the unit",
    )
    errors, warnings = _validate(tmp_path)
    assert any("Definition-of-Done item asserts a runnable outcome" in w for w in warnings)
    errors_s, _ = _validate(tmp_path, strict=True)
    assert any("Definition-of-Done item asserts a runnable outcome" in e for e in errors_s)


def test_dod_runnable_claim_citing_existing_ac_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: a real terraform apply succeeds",
        dod_block="- [ ] AC-1 verified: terraform apply succeeds",
        verification_block="- VERIFY AC-1 | type=apply | cmd=`terraform apply` | expect-exit=0",
    )
    errors, warnings = _validate(tmp_path, strict=True)
    assert _contract_findings(errors) == []
    assert _contract_findings(warnings) == []


# ---------------------------------------------------------------------------
# Malformed directive + clean unit
# ---------------------------------------------------------------------------


def test_malformed_verify_directive_is_always_error(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the module follows SOLID",
        dod_block="- [ ] All ACs checked",
        verification_block="- VERIFY AC-1 | type=bogus",
    )
    errors, _ = _validate(tmp_path)  # non-strict -- still an error
    assert any("malformed '## Verification' directive" in e for e in errors)


def test_clean_unit_has_no_contract_findings(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: variables are documented and naming is idiomatic",
        dod_block="- [ ] All ACs checked\n- [ ] Lint and format clean",
    )
    errors, warnings = _validate(tmp_path, strict=True)
    assert _contract_findings(errors) == []
    assert _contract_findings(warnings) == []


@pytest.mark.parametrize(
    "suggested_acs",
    [
        pytest.param([], id="no-suggested-acs"),
        pytest.param(["AC-FUNC-001 the resolver returns the canonical URL"], id="qualitative-suggested-ac"),
    ],
)
def test_auto_generated_draft_passes_verification_contract(
    tmp_path: Path, backlog_dir: Path, suggested_acs: list[str]
) -> None:
    """The proposal.py ## Verification stub must not trip the contract for drafts.

    The auto-generated draft seeds a ``type=judge`` directive for its first AC, so a
    freshly materialised draft is contract-clean even under ``--strict``.
    """
    from devbench.backlog.proposal import ProposedTask, generate_draft_md

    _make_index(tmp_path, "E1-F1-S1-T1")
    draft = generate_draft_md(
        ProposedTask(
            suggested_id="E1-F1-S1-T1",
            title="Task Title",
            files_to_own=["src/f.py"],
            linked_scenarios=[],
            suggested_acs=suggested_acs,
            suggested_approach="Do the thing.",
        ),
        repo=_REPO,
        source_task_id="E1-F1-S1-T0",
        generated_at="NOW",
    )
    (backlog_dir / "E1-F1-S1-T1.md").write_text(draft, encoding="utf-8")

    errors, warnings = _validate(tmp_path, strict=True)
    assert _contract_findings(errors) == []
    assert _contract_findings(warnings) == []


def test_promoted_proposal_draft_with_files_and_executable_ac_validates_clean(
    tmp_path: Path, backlog_dir: Path
) -> None:
    """TDI-008 AC-1/2/3: a materialised draft with files_to_own + an executable AC,
    promoted to in-queue, has concrete add rows (no TODO), a type=command
    directive per executable AC, and introduces no validate-backlog errors."""
    from devbench.backlog.proposal import ProposedTask, generate_draft_md

    _make_index(tmp_path, "E1-F1-S1-T1")
    draft = generate_draft_md(
        ProposedTask(
            suggested_id="E1-F1-S1-T1",
            title="Task Title",
            files_to_own=["src/resolver.py", "tests/test_resolver.py"],
            linked_scenarios=[],
            suggested_acs=["AC-1 a real terraform validate of the module passes", "AC-2 the resolver is documented"],
            suggested_approach="Wire the resolver and validate the module.",
        ),
        repo=_REPO,
        source_task_id="E1-F1-S1-T0",
        generated_at="NOW",
        status="in-queue",
    )
    # AC-1: concrete add rows, no TODO cell.
    assert "| `src/resolver.py` | add |" in draft
    assert "TODO" not in draft
    # AC-3: executable AC gets a type=command directive; qualitative AC gets type=judge.
    assert "- VERIFY AC-1 | type=command" in draft
    assert "- VERIFY AC-2 | type=judge" in draft

    (backlog_dir / "E1-F1-S1-T1.md").write_text(draft, encoding="utf-8")

    # AC-2: promoting (status in-queue) introduces no validate-backlog ERRORs.
    errors, _ = _validate(tmp_path, strict=True)
    assert errors == [], f"unexpected validate errors on promoted draft: {errors}"
