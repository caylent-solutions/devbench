"""Structural pin for E3-F2-S1-T11 (`E3-F2-S1-T1-doc_review-3.json`,
finding ``API_DOCS_STALE``): `code-reviewer.md`'s REACHABILITY rubric
(rule 57) must stay in sync with the shipped `[WAIVED]` / `[GATE_PASS
reachability]` behaviour introduced by E3-F2-S1-T1 (commit `50bcf97`).

Four staleness items were found against the pre-fix rubric text:

1. Rule 57 enumerated `[OK]`, `[POTENTIALLY UNREACHABLE]` (including the
   orphan-chain variant) and `[LOAD_ERROR]`, but never the `[WAIVED]
   <target> -- <reason>` block or the widened `Summary: ..., N waived.`
   line (`cli.py::_reachability_scan_candidates`,
   `cli.py::_reachability_waived_block`).
2. Rule 57c told the judge that an `<operator|executor>`-attributed
   `[GATE_WAIVER reachability]` marker "exempts" a file. Under the shipped
   code (`cli._REACHABILITY_WAIVER_REQUIRED_ATTRIBUTION`,
   `manager.BacklogManager._check_gate_pass_done_invariant`) only an
   OPERATOR-attributed marker clears anything; an executor-attributed one
   exempts nothing and `mark-done` still refuses the unit.
3. The reason-quality control ("a vague waiver reason is itself a
   finding") hung entirely off the `[POTENTIALLY UNREACHABLE]` branch, a
   branch an operator-waived target can no longer reach (the shipped scan
   renders a waived candidate `[WAIVED]`, never `[POTENTIALLY
   UNREACHABLE]`) -- leaving the control with no live rubric path.
4. The Evidence line (~lines 20-21) called `check-reachability`
   "heuristic candidates only", although a clean enabled run now mutates
   the work-unit file by appending a `[GATE_PASS reachability]` record
   (`cli.py`, persisted-machine-record block; `docs/cli-reference.md`
   "Persisted machine record" paragraph, already in sync).

This module reads `code-reviewer.md` and `docs/cli-reference.md` (the
already-verified source of truth, per the passing `doc_review` round for
E3-F2-S1-T1) directly off disk rather than hand-copying either text, so a
future rewording of either file cannot silently drift this pin out of
sync with what it is meant to guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CODE_REVIEWER_PATH = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "code-reviewer.md"
CLI_REFERENCE_PATH = REPO_ROOT / "docs" / "cli-reference.md"

_RULE_57_RE = re.compile(r"^57\.\s.*?(?=\n## OUT OF SCOPE FOR FINDINGS)", re.DOTALL | re.MULTILINE)
_EVIDENCE_LINE_RE = re.compile(r"^Reachability evidence \(.*\):$", re.MULTILINE)

# Pulled from the shipped, already-verified docs/cli-reference.md "Persisted
# machine record" paragraph rather than hand-copied, so this pin cannot
# drift independently of the source-of-truth doc it cross-checks against.
_GATE_PASS_RECORD_TOKEN_RE = re.compile(r"`\[GATE_PASS reachability\][^`]*`")


def extract_rule_57(code_reviewer_text: str) -> str:
    """Return rule 57's full text block (heading through its last lettered
    sub-item), verbatim from the shipped `code-reviewer.md`."""
    match = _RULE_57_RE.search(code_reviewer_text)
    assert match, "rule 57 (REACHABILITY) not found in code-reviewer.md -- has it been renumbered or removed?"
    return match.group(0)


def extract_evidence_line(code_reviewer_text: str) -> str:
    """Return the `Reachability evidence (...)` Evidence-block line verbatim."""
    match = _EVIDENCE_LINE_RE.search(code_reviewer_text)
    assert match, "'Reachability evidence (...)' Evidence line not found in code-reviewer.md"
    return match.group(0)


def extract_gate_pass_record_token(cli_reference_text: str) -> str:
    """Pull the literal `[GATE_PASS reachability] ...` marker shape straight
    out of `docs/cli-reference.md`'s already-verified "Persisted machine
    record" paragraph, so the doc-consistency assertion below never
    hand-restates the marker shape."""
    match = _GATE_PASS_RECORD_TOKEN_RE.search(cli_reference_text)
    assert match, "no `[GATE_PASS reachability] ...` marker token found in docs/cli-reference.md"
    return match.group(0).strip("`")


@pytest.fixture(scope="module")
def code_reviewer_text() -> str:
    assert CODE_REVIEWER_PATH.is_file(), f"code-reviewer.md missing at {CODE_REVIEWER_PATH}"
    return CODE_REVIEWER_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cli_reference_text() -> str:
    assert CLI_REFERENCE_PATH.is_file(), f"cli-reference.md missing at {CLI_REFERENCE_PATH}"
    return CLI_REFERENCE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rule_57_text(code_reviewer_text: str) -> str:
    return extract_rule_57(code_reviewer_text)


@pytest.fixture(scope="module")
def evidence_line(code_reviewer_text: str) -> str:
    return extract_evidence_line(code_reviewer_text)


@pytest.mark.unit
class TestRule57DocumentsWaivedBlockAndWidenedSummary:
    """AC-TEST-001 / AC-CODE-001."""

    def test_rule_57_mentions_the_waived_block_token(self, rule_57_text: str) -> None:
        assert "[WAIVED]" in rule_57_text, (
            "rule 57 does not mention the '[WAIVED]' output token that "
            "cli.py::_reachability_waived_block renders for an "
            "operator-waived candidate"
        )

    def test_rule_57_mentions_the_widened_summary_suffix(self, rule_57_text: str) -> None:
        assert ", N waived." in rule_57_text or re.search(r",\s*<?N>?\s*waived\.", rule_57_text), (
            "rule 57 does not document the widened 'Summary: ..., N waived.' "
            "suffix that cli.py::_reachability_scan_candidates now appends"
        )

    def test_rule_57_excludes_waived_from_findings_count(self, rule_57_text: str) -> None:
        assert "excluded" in rule_57_text and "findings" in rule_57_text, (
            "rule 57 does not state that a waived target is excluded from the blocking findings count"
        )


@pytest.mark.unit
class TestRule57cScopedToOperatorAttributionOnly:
    """AC-TEST-002 / AC-CODE-002."""

    def test_no_operator_or_executor_wording_survives(self, rule_57_text: str) -> None:
        assert "<operator|executor>" not in rule_57_text, (
            "rule 57 still tells the judge an '<operator|executor>'-attributed "
            "marker exempts a file, contradicting the shipped operator-only "
            "waiver rule (cli._REACHABILITY_WAIVER_REQUIRED_ATTRIBUTION)"
        )

    def test_waiver_acceptance_language_is_operator_scoped(self, rule_57_text: str) -> None:
        assert "OPERATOR" in rule_57_text or "operator-attributed" in rule_57_text, (
            "rule 57 does not scope waiver acceptance to operator attribution"
        )
        assert "executor-attributed" in rule_57_text and "exempts nothing" in rule_57_text, (
            "rule 57 does not state that an executor-attributed waiver "
            "exempts nothing (it must remain internally consistent with "
            "the operator-only mark-done rule)"
        )


@pytest.mark.unit
class TestEvidenceLineDocumentsGatePassMutation:
    """AC-TEST-003 / AC-CODE-003."""

    def test_evidence_line_no_longer_says_heuristic_candidates_only(self, evidence_line: str) -> None:
        assert "heuristic candidates only" not in evidence_line, (
            "Evidence line still describes check-reachability output as "
            "'heuristic candidates only', but a clean enabled run mutates "
            "the work-unit file by appending a [GATE_PASS reachability] "
            "record"
        )

    def test_evidence_line_names_the_gate_pass_record(self, evidence_line: str, cli_reference_text: str) -> None:
        gate_pass_token = extract_gate_pass_record_token(cli_reference_text)
        assert "GATE_PASS" in evidence_line, (
            "Evidence line does not mention that check-reachability "
            f"persists a {gate_pass_token!r} record on a clean enabled run"
        )


@pytest.mark.unit
class TestVagueWaiverReasonControlHasALiveRubricPath:
    """AC-TEST-004 / AC-CODE-004: the reason-quality control must be
    reachable for a candidate that a rubric reader is actually evaluating
    -- i.e. co-located with the `[WAIVED]` branch, not solely attached to
    the `[POTENTIALLY UNREACHABLE]` branch an operator-waived target can no
    longer reach."""

    def test_reason_quality_control_is_reachable_from_a_waived_candidate(self, rule_57_text: str) -> None:
        # Split rule 57 into lettered sub-items (`    a. ...`, `    b. ...`,
        # etc.) and confirm at least one sub-item mentions BOTH the
        # `[WAIVED]` token and the reason-quality control together, so a
        # judge evaluating a `[WAIVED]` block still has rubric text to
        # apply the control to.
        sub_items = re.split(r"\n    [a-z]\. ", rule_57_text)
        reason_quality_phrase = "is itself a finding"
        assert any("[WAIVED]" in item and reason_quality_phrase in item for item in sub_items), (
            "no sub-item of rule 57 attaches the waiver reason-quality "
            "control ('...is itself a finding') to a branch a [WAIVED] "
            "candidate can actually reach"
        )
