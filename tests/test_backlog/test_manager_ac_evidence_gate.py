"""Tests for the deterministic AC evidence gate in mark_done (Workstream B, ADR-27).

Covers ``BacklogManager._ac_evidence_complete`` and its wiring into ``mark_done``:

- a unit with NO ``## Verification`` section behaves exactly as before (allowed);
- a unit whose executable AC has NO evidence is BLOCKED;
- a unit whose executable AC has a NON-ZERO exit record is BLOCKED;
- a unit whose executable AC has an exit-0 record is ALLOWED;
- a deferred AC BLOCKS by default and is ALLOWED only when
  ``done_gate.allow_deferred_evidence`` is true;
- the latest attempt's ledger is the one consulted.

The done-gate's prior judge-pass check is always satisfied here (all five required
judges have a canonical REVIEW_PASS in the most recent round) so each test isolates
the evidence gate.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import verification
from devbench.backlog.manager import BacklogManager
from devbench.config_loader import DoneGateConfig, RepoConfig, RuntimeConfig
from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

pytestmark = pytest.mark.unit

_UNIT_ID = "E0-F1-S1-T1"
_REPO = "caylent-solutions/devbench"


def _pass_block() -> str:
    ts = "2026-06-07 17:43 UTC"
    return "".join(f"[{ts}] [judge/{j}] [REVIEW_PASS] ok\n" for j in sorted(ALL_REQUIRED_JUDGE_NAMES))


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


def _make_unit(tmp_path: Path, verification_block: str) -> Path:
    backlog = tmp_path / "backlog"
    backlog.mkdir(exist_ok=True)
    section = f"## Verification\n\n{verification_block}\n\n" if verification_block else ""
    wu = backlog / f"{_UNIT_ID}.md"
    wu.write_text(
        f"# {_UNIT_ID}: Task\n\n"
        "## Status: in-review\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-1: a real apply succeeds\n\n"
        f"{section}"
        "## Comments\n\n" + _pass_block(),
        encoding="utf-8",
    )
    return wu


def _write_evidence(tmp_path: Path, *, exit_code: int, ac: str = "AC-1", vtype: str = "command") -> None:
    attempt = verification.next_attempt_number(tmp_path, _UNIT_ID)
    rec = verification.EvidenceRecord(
        ac_ids=[ac],
        vtype=vtype,
        command="make tf-apply",
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


class TestNoVerificationSectionBackCompat:
    """A unit with no ## Verification section is unaffected by the evidence gate."""

    def test_mark_done_allowed_without_verification_section(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, "")  # no verification block
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            mgr.mark_done(wu, idx, _UNIT_ID)
        assert "## Status: done" in wu.read_text()
        assert _is_done(idx)


class TestEvidenceGateBlocks:
    """An executable AC without satisfying evidence blocks mark_done."""

    def test_missing_evidence_blocks(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, "- VERIFY AC-1 | type=apply | cmd=`make tf-apply` | expect-exit=0")
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            with pytest.raises(RuntimeError, match="evidence incomplete"):
                mgr.mark_done(wu, idx, _UNIT_ID)
        # Status NOT advanced to done.
        assert "## Status: in-review" in wu.read_text()
        assert not _is_done(idx)

    def test_nonzero_exit_evidence_blocks(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, "- VERIFY AC-1 | type=apply | cmd=`make tf-apply` | expect-exit=0")
        _write_evidence(tmp_path, exit_code=2)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            with pytest.raises(RuntimeError, match="non-zero exit recorded"):
                mgr.mark_done(wu, idx, _UNIT_ID)
        assert not _is_done(idx)


class TestEvidenceGateAllows:
    """An executable AC with a satisfying exit-0 record allows mark_done."""

    def test_exit_zero_evidence_allows(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, "- VERIFY AC-1 | type=apply | cmd=`make tf-apply` | expect-exit=0")
        _write_evidence(tmp_path, exit_code=0)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            mgr.mark_done(wu, idx, _UNIT_ID)
        assert "## Status: done" in wu.read_text()
        assert _is_done(idx)

    def test_latest_attempt_is_consulted(self, tmp_path: Path) -> None:
        """A failing attempt 1 followed by a passing attempt 2 is allowed."""
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, "- VERIFY AC-1 | type=apply | cmd=`make tf-apply` | expect-exit=0")
        _write_evidence(tmp_path, exit_code=1)  # attempt 1 fails
        _write_evidence(tmp_path, exit_code=0)  # attempt 2 passes -> latest
        assert verification.latest_attempt_number(tmp_path, _UNIT_ID) == 2
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            mgr.mark_done(wu, idx, _UNIT_ID)
        assert _is_done(idx)


class TestDeferredAcGate:
    """Deferred ACs block by default; allowed only when the operator opts in."""

    _DEFERRED = '- VERIFY AC-1 | type=deferred | owner=operator | reason="prod apply is operator-only"'

    def test_deferred_blocks_by_default(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, self._DEFERRED)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config(allow_deferred=False)):
            with pytest.raises(RuntimeError, match="deferred"):
                mgr.mark_done(wu, idx, _UNIT_ID)
        assert not _is_done(idx)

    def test_deferred_allowed_when_opted_in(self, tmp_path: Path) -> None:
        idx = _make_index(tmp_path)
        wu = _make_unit(tmp_path, self._DEFERRED)
        mgr = BacklogManager()
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config(allow_deferred=True)):
            mgr.mark_done(wu, idx, _UNIT_ID)
        assert _is_done(idx)


class TestEvidenceCompleteHelper:
    """Direct unit tests for _ac_evidence_complete (independent of mark_done)."""

    def test_complete_when_exit_zero_present(self, tmp_path: Path) -> None:
        wu = _make_unit(tmp_path, "- VERIFY AC-1 | type=apply | cmd=`make tf-apply` | expect-exit=0")
        _write_evidence(tmp_path, exit_code=0)
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            result = BacklogManager._ac_evidence_complete(wu, tmp_path, _UNIT_ID)
        assert result.complete is True
        assert result.missing == []

    def test_incomplete_when_missing(self, tmp_path: Path) -> None:
        wu = _make_unit(tmp_path, "- VERIFY AC-1 | type=apply | cmd=`make tf-apply` | expect-exit=0")
        with patch("devbench.config.RUNTIME_CONFIG", _runtime_config()):
            result = BacklogManager._ac_evidence_complete(wu, tmp_path, _UNIT_ID)
        assert result.complete is False
        assert "AC-1" in result.missing
