"""Tests for the sanctioned already-satisfied completion path (tracked issue 014).

A *verification-only* work unit -- one whose ``## Changes Manifest`` is made up
entirely of sentinel rows (e.g. ``<verification-only>``) and therefore authors
no source file -- can have its deliverable already present in the repo (committed
under a non-attributable commit, by a dependency, or by an operator's direct
fix). Such a unit produces no staged/unstaged diff, so ``get-diff`` returns
GET_DIFF_NO_ATTRIBUTABLE (45) and the review pipeline can never run; the unit is
stuck even though its acceptance criteria verifiably pass.

``BacklogManager.mark_done_already_satisfied`` is the narrow, evidence-gated,
audited completion path for exactly this case. It is gated on THREE conditions
that together make it impossible to use as a way to skip real work:

1. the unit must be verification-only (its Manifest owns NO real-file deliverable);
2. the unit must declare a ``## Verification`` contract; and
3. every executable Acceptance Criterion must already have a tool-captured
   exit-0 record in the latest ``verify-ac`` evidence ledger.

On success it writes a ``[WU_ALREADY_SATISFIED]`` audit comment carrying the
evidence summary, then advances the unit to Done. The standard judge-pass gate
(``_last_round_all_passed``) is intentionally NOT consulted -- the review
pipeline cannot run without a diff, so the deterministic, non-forgeable
verify-ac evidence gate stands in its place.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import verification
from devbench.backlog.manager import BacklogManager
from devbench.config_loader import DoneGateConfig, RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

_UNIT_ID = "E0-F1-S1-T1"
_REPO = "caylent-solutions/devbench"

_VERIFICATION_ONLY_MANIFEST = "| File | Change |\n|------|--------|\n| `<verification-only>` | live verify only |\n"
_REAL_MANIFEST = "| File | Change |\n|------|--------|\n| `src/devbench/x.py` | new module |\n"
_VERIFICATION_BLOCK = "- VERIFY AC-1 | type=command | cmd=`make tf-output` | expect-exit=0"


def _make_index(tmp_path: Path) -> Path:
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n"
        f"| {_UNIT_ID} | Task | Task | in-review | none | {_REPO} | `backlog/{_UNIT_ID}.md` |\n",
        encoding="utf-8",
    )
    return idx


def _make_unit(tmp_path: Path, *, manifest: str, verification_block: str) -> Path:
    backlog = tmp_path / "backlog"
    backlog.mkdir(exist_ok=True)
    section = f"## Verification\n\n{verification_block}\n\n" if verification_block else ""
    wu = backlog / f"{_UNIT_ID}.md"
    wu.write_text(
        f"# {_UNIT_ID}: Task\n\n"
        "## Status: in-review\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-1: the output value is present\n\n"
        "## Changes Manifest\n\n"
        f"{manifest}\n"
        f"{section}"
        "## Comments\n\n",
        encoding="utf-8",
    )
    return wu


def _write_evidence(tmp_path: Path, *, exit_code: int, ac: str = "AC-1") -> None:
    attempt = verification.next_attempt_number(tmp_path, _UNIT_ID)
    rec = verification.EvidenceRecord(
        ac_ids=[ac],
        vtype="command",
        command="make tf-output",
        exit_code=exit_code,
        tool="terraform",
    )
    verification.write_evidence_ledger(tmp_path, _UNIT_ID, attempt, [rec])


def _runtime_config(*, allow_deferred: bool = False) -> RuntimeConfig:
    return RuntimeConfig(
        repos={_REPO: RepoConfig(checkout_directory="repo")},
        done_gate=DoneGateConfig(allow_deferred_evidence=allow_deferred),
    )


def _is_done(idx: Path) -> bool:
    return any(_UNIT_ID in line and "done" in line.lower() for line in idx.read_text().splitlines())


class TestAlreadySatisfiedAllows:
    """A verification-only unit with complete exit-0 evidence reaches Done via the path."""

    def test_verification_only_with_evidence_reaches_done(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_VERIFICATION_ONLY_MANIFEST, verification_block=_VERIFICATION_BLOCK)
        _write_evidence(tmp_path, exit_code=0)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        assert "## Status: done" in wu.read_text()
        assert _is_done(idx)

    def test_audit_record_written(self, tmp_path: Path) -> None:
        """The completion records an auditable [WU_ALREADY_SATISFIED] marker."""
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_VERIFICATION_ONLY_MANIFEST, verification_block=_VERIFICATION_BLOCK)
        _write_evidence(tmp_path, exit_code=0)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        content = wu.read_text()
        assert "[WU_ALREADY_SATISFIED]" in content

    def test_completion_does_not_require_judge_passes(self, tmp_path: Path) -> None:
        """No canonical REVIEW_PASS lines exist, yet the evidence-gated path still completes.

        This proves the judge-pass gate is intentionally waived (it can never run
        without a diff) and the verify-ac evidence gate stands in its place.
        """
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_VERIFICATION_ONLY_MANIFEST, verification_block=_VERIFICATION_BLOCK)
        assert not BacklogManager()._last_round_all_passed(wu)  # no judge passes present
        _write_evidence(tmp_path, exit_code=0)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        assert _is_done(idx)


class TestAlreadySatisfiedRefusesNonVerificationOnly:
    """A unit with a real-file deliverable cannot use the already-satisfied path."""

    def test_real_manifest_refused(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_REAL_MANIFEST, verification_block=_VERIFICATION_BLOCK)
        _write_evidence(tmp_path, exit_code=0)  # even with green evidence
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            with pytest.raises(RuntimeError, match="verification-only"):
                mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        assert "## Status: in-review" in wu.read_text()
        assert not _is_done(idx)


class TestAlreadySatisfiedRequiresVerificationContract:
    """A verification-only unit with NO ## Verification section is refused (no proof)."""

    def test_no_verification_section_refused(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_VERIFICATION_ONLY_MANIFEST, verification_block="")
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            with pytest.raises(RuntimeError, match="Verification"):
                mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        assert not _is_done(idx)


class TestAlreadySatisfiedRequiresGreenEvidence:
    """A verification-only unit without complete exit-0 evidence is refused."""

    def test_missing_evidence_refused(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_VERIFICATION_ONLY_MANIFEST, verification_block=_VERIFICATION_BLOCK)
        # No evidence ledger written.
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            with pytest.raises(RuntimeError, match="evidence incomplete"):
                mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        assert not _is_done(idx)

    def test_nonzero_evidence_refused(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, manifest=_VERIFICATION_ONLY_MANIFEST, verification_block=_VERIFICATION_BLOCK)
        _write_evidence(tmp_path, exit_code=2)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            with pytest.raises(RuntimeError, match="evidence incomplete"):
                mgr.mark_done_already_satisfied(wu, idx, _UNIT_ID)
        assert not _is_done(idx)
