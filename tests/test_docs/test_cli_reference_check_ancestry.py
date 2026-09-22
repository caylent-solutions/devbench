r"""Structural pins syncing docs/cli-reference.md's `check-ancestry` and `mark-done`
sections with the shipped gate-record and mark-done behaviour (E4-F2-S1-T1).

Verifies that docs/cli-reference.md documents:
- The `### \`check-ancestry\`` status-line JSON enumeration includes `scope_hash`, and
  that it is the empty string on the BLOCKED (`status: "fail"`) line (AC-DOC-001).
- The `[GATE_PASS ancestry]` marker and its `[GATE_ANCESTRY_TARGET_REF]` companion
  marker, written together in a single atomic write, persisted only by a passing
  enabled run (AC-DOC-002).
- The exit-code table's `rc=1` row enumerates the record-write failure causes, and the
  surrounding prose states a record-write failure occurs AFTER the terminal pass
  decision, not only before one (AC-DOC-003).
- The `mark-done` gate-record-invariant paragraph states that the ancestry gate's
  scope-hash recompute additionally folds in the resolved target-ref sha and a defined
  absent-file scope marker, distinct from the generic file-blob-hash-only formula
  (AC-DOC-004).
- That same paragraph documents the ancestry-specific remediation string
  (`check-ancestry <unit-id> <dependency-ref>`) and the new `mark-done` refusal when the
  `[GATE_ANCESTRY_TARGET_REF]` companion marker is absent or malformed (AC-DOC-005).
- The withdrawn "Manifest row naming an absent file causes rc=1" claim is NOT present
  (AC-DOC-006).

Spec source: spec/integration-reality-gates-hardening.md sections 4.2, 4.3, 4.5, 5.2.
Source work unit: E4-F2-S1-T1 (src/devbench/cli.py, src/devbench/backlog/manager.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

_CHECK_ANCESTRY_HEADING = "### `check-ancestry`"
_MARK_DONE_HEADING = "### `mark-done`"


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])  # count leading '#'
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


@pytest.mark.unit
class TestCheckAncestryStatusLineScopeHash:
    """AC-DOC-001: the status-line JSON enumeration includes ``scope_hash``."""

    def test_scope_hash_field_in_status_line_enumeration(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        assert '"scope_hash"' in section, (
            "docs/cli-reference.md '### `check-ancestry`' section's status-line JSON "
            "enumeration must include the 'scope_hash' field this unit's predecessor "
            "added to `_ancestry_status_line` (cli.py)."
        )

    def test_scope_hash_documented_as_empty_string_on_blocked_line(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        lower = section.lower()
        assert "empty string" in lower and "blocked" in lower, (
            "docs/cli-reference.md '### `check-ancestry`' section must state that "
            "'scope_hash' is the empty string on the BLOCKED (status: \"fail\") line, "
            'which persists no record (cli.py:5310 prints _ancestry_status_line(..., "")).'
        )


@pytest.mark.unit
class TestCheckAncestryGatePassRecord:
    """AC-DOC-002: the [GATE_PASS ancestry] record and its companion marker."""

    def test_gate_pass_marker_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        assert "[GATE_PASS ancestry]" in section, (
            "docs/cli-reference.md '### `check-ancestry`' section must document the "
            "[GATE_PASS ancestry] marker persisted by a passing enabled run."
        )

    def test_target_ref_companion_marker_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        assert "[GATE_ANCESTRY_TARGET_REF]" in section, (
            "docs/cli-reference.md '### `check-ancestry`' section must document the "
            "[GATE_ANCESTRY_TARGET_REF] companion marker "
            "(_write_ancestry_gate_pass_record, cli.py:5030-5113)."
        )

    def test_markers_written_in_single_atomic_write(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        lower = section.lower()
        assert "atomic" in lower, (
            "docs/cli-reference.md '### `check-ancestry`' section must state that the "
            "[GATE_PASS ancestry] record and its [GATE_ANCESTRY_TARGET_REF] companion "
            "marker are written together in a SINGLE atomic write."
        )

    def test_failing_error_or_disabled_runs_persist_no_record(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        lower = section.lower()
        assert "persists neither" in lower or "persist neither" in lower or "persists no record" in lower, (
            "docs/cli-reference.md '### `check-ancestry`' section must state that a "
            "failing, error, or disabled run persists neither marker."
        )


@pytest.mark.unit
class TestCheckAncestryRecordWriteFailureExitCode:
    """AC-DOC-003: rc=1 record-write failure causes and their timing relative to the pass decision."""

    def test_exit_1_row_enumerates_record_write_failure_causes(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        assert "| 1 |" in section, "docs/cli-reference.md '### `check-ancestry`' must contain an exit-code table"
        lower = section.lower()
        for cause in (
            "unreadable",
            "unwritable",
            "changes manifest",
            "hash-object",
            "rev-parse",
        ):
            assert cause in lower, (
                f"docs/cli-reference.md '### `check-ancestry`' exit-code table must enumerate "
                f"the record-write failure cause {cause!r} in its rc=1 row "
                "(_write_ancestry_gate_pass_record's failure modes, cli.py:5188-5194)."
            )

    def test_record_write_failure_occurs_after_terminal_pass_decision(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        lower = section.lower()
        assert "after the terminal pass decision" in lower or "after the terminal" in lower, (
            "docs/cli-reference.md '### `check-ancestry`' section must state that a "
            "record-write failure occurs AFTER the terminal pass decision, not only "
            "before a terminal probe decision (cli.py's cmd_check_ancestry docstring, "
            "cli.py:5192-5197)."
        )


@pytest.mark.unit
class TestCheckAncestryWithdrawnClaimAbsent:
    """AC-DOC-006 (negative assertion): the withdrawn absent-Manifest-file claim is gone."""

    def test_scope_hash_documented_and_withdrawn_claim_absent(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _CHECK_ANCESTRY_HEADING)
        assert section, f"{_CHECK_ANCESTRY_HEADING} section must exist"
        # Paired with a genuinely-failing-pre-fix assertion (scope_hash) so this test
        # method exercises real RED->GREEN behaviour, not just a trivially-true guard:
        # the withdrawn claim below was never landed in docs/cli-reference.md (the
        # source work unit's docs sync was skipped across all three review rounds), so
        # its absence alone cannot distinguish pre-fix from post-fix text.
        assert '"scope_hash"' in section, (
            "docs/cli-reference.md '### `check-ancestry`' section must include 'scope_hash' "
            "in the status-line JSON enumeration."
        )
        lower = section.lower()
        assert "causes check-ancestry to exit 1" not in lower, (
            "docs/cli-reference.md '### `check-ancestry`' section must NOT contain the "
            "withdrawn claim that a Changes-Manifest row naming a file absent from the "
            "checkout causes check-ancestry to exit 1: doc_review withdrew that item in "
            "round 3 -- the shipped code folds an absent declared file into the scope-hash "
            "digest via _ABSENT_MANIFEST_FILE_SCOPE_MARKER instead of failing "
            "(_compute_ancestry_scope_hash, cli.py:4944-5028)."
        )


@pytest.mark.unit
class TestMarkDoneAncestryScopeHashFormulaDistinctFromGeneric:
    """AC-DOC-004: the ancestry gate's scope-hash recompute is distinct from the generic formula."""

    def test_target_ref_sha_fold_in_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _MARK_DONE_HEADING)
        assert section, f"{_MARK_DONE_HEADING} section must exist"
        lower = section.lower()
        assert "target-ref" in lower or "target ref" in lower, (
            "docs/cli-reference.md '### `mark-done`' gate-record-invariant paragraph must "
            "state that the ancestry gate's scope-hash recompute additionally folds in the "
            "resolved target-ref sha, read back from the [GATE_ANCESTRY_TARGET_REF] marker "
            "(BacklogManager._resolve_ancestry_target_ref, manager.py:861-914)."
        )

    def test_absent_file_scope_marker_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _MARK_DONE_HEADING)
        assert section, f"{_MARK_DONE_HEADING} section must exist"
        lower = section.lower()
        assert "absent-file" in lower or "absent file" in lower, (
            "docs/cli-reference.md '### `mark-done`' gate-record-invariant paragraph must "
            "state that the ancestry gate's recompute folds a defined absent-file scope "
            "marker into the digest for any declared Manifest path not present in the "
            "checkout (_compute_ancestry_scope_hash, cli.py:4944-5028)."
        )

    def test_ancestry_formula_distinguished_from_generic_blob_hash_formula(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _MARK_DONE_HEADING)
        assert section, f"{_MARK_DONE_HEADING} section must exist"
        lower = section.lower()
        assert "distinct" in lower, (
            "docs/cli-reference.md '### `mark-done`' gate-record-invariant paragraph must "
            "state that the ancestry gate's recompute is distinct from the generic "
            "file-blob-hash-only formula documented for the other machine-blocking gates."
        )


@pytest.mark.unit
class TestMarkDoneAncestryRemediationAndTargetRefRefusal:
    """AC-DOC-005: the ancestry remediation string and the new target-ref-marker refusal."""

    def test_ancestry_remediation_string_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _MARK_DONE_HEADING)
        assert section, f"{_MARK_DONE_HEADING} section must exist"
        assert "check-ancestry <unit-id> <dependency-ref>" in section, (
            "docs/cli-reference.md '### `mark-done`' gate-record-invariant paragraph must "
            "document the ancestry gate's remediation string as "
            "'uv run devbench check-ancestry <unit-id> <dependency-ref>' (manager.py:1066-1071), "
            "distinct from the other three machine-blocking gates' bare '<verb> <unit-id>' "
            "worked example."
        )

    def test_missing_or_malformed_target_ref_marker_refusal_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, _MARK_DONE_HEADING)
        assert section, f"{_MARK_DONE_HEADING} section must exist"
        assert "[GATE_ANCESTRY_TARGET_REF]" in section, (
            "docs/cli-reference.md '### `mark-done`' gate-record-invariant paragraph must "
            "name the [GATE_ANCESTRY_TARGET_REF] marker whose absence or malformation "
            "produces a new mark-done refusal."
        )
        lower = section.lower()
        assert "malformed" in lower and ("absent" in lower or "no [gate_ancestry_target_ref]" in lower), (
            "docs/cli-reference.md '### `mark-done`' gate-record-invariant paragraph must "
            "document the new mark-done refusal that fires when the "
            "[GATE_ANCESTRY_TARGET_REF] companion marker is absent or malformed "
            "(_resolve_ancestry_target_ref raises RuntimeError in both cases, "
            "manager.py:894-914)."
        )


@pytest.mark.unit
class TestCheckAncestryDocPinReadsArtifactOffDisk:
    """The pin module reads its artifact off disk (no frozen copy)."""

    def test_cli_reference_doc_exists(self) -> None:
        assert CLI_REFERENCE_DOC.is_file(), f"docs/cli-reference.md missing at {CLI_REFERENCE_DOC}"
