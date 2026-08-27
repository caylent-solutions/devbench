r"""Structural pins for docs/cli-reference.md's `check-shared-file-impact` entry
(E5-F3-S1-T1; spec Section 8 -- PR #318 shipped this verb with no cli-reference.md
entry at all; 318-D15 is the separate done-path `[GATE_PASS]` requirement this
module does not pin).

Verifies that docs/cli-reference.md documents:
- A `### \`check-shared-file-impact\`` section carrying the usage line and an
  exit-code table (AC-6).
- The exact spec-4.1 disabled status line,
  `{"gate": "shared_file_impact", "status": "disabled"}`, and exit 0 for the
  disabled case.
- The enabled status-line JSON field enumeration (`gate`, `tier`, `status`,
  `findings`, `scope_hash`).
- The `[GATE_PASS shared_file_impact]` record persisted by a passing run, and
  that `devbench.gate_records.compose_gate_pass_record` is the sole authorized
  builder of that marker text.
- The `mark-done` requirement and its exact remediation command,
  `devbench check-shared-file-impact <unit-id>`.
- The stale-record refusal wording.
- The operator-attributed `[GATE_WAIVER shared_file_impact]` bypass.

Spec source: spec/integration-reality-gates-hardening.md sections 4.1, 4.2, 4.6, 5.2, 5.3.
Source work unit: E5-F3-S1-T1 (src/devbench/cli.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

_CHECK_SHARED_FILE_IMPACT_HEADING = "### `check-shared-file-impact`"


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


@pytest.fixture
def section() -> str:
    text = _read_doc()
    found = _extract_section(text, _CHECK_SHARED_FILE_IMPACT_HEADING)
    assert found, f"{_CHECK_SHARED_FILE_IMPACT_HEADING} section must exist in docs/cli-reference.md"
    return found


@pytest.mark.unit
class TestCheckSharedFileImpactSectionExists:
    """AC-6: the verb has its own documented entry (spec Section 8)."""

    def test_cli_reference_doc_exists(self) -> None:
        assert CLI_REFERENCE_DOC.is_file(), f"docs/cli-reference.md missing at {CLI_REFERENCE_DOC}"

    def test_usage_line_documented(self, section: str) -> None:
        assert "uv run devbench check-shared-file-impact <id>" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' must document the usage line "
            "'uv run devbench check-shared-file-impact <id>'."
        )

    def test_exit_code_table_present(self, section: str) -> None:
        assert "| 0 |" in section and "| 1 |" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' must contain an exit-code table "
            "with a 0 row and a 1 row."
        )


@pytest.mark.unit
class TestCheckSharedFileImpactStatusLines:
    """AC-1 (spec 4.1, AC-4) and AC-2 (spec 5.2): the disabled and enabled status-line shapes."""

    def test_disabled_status_line_documented_exactly(self, section: str) -> None:
        assert '{"gate": "shared_file_impact", "status": "disabled"}' in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' must document the exact spec-4.1 "
            'disabled status line: {"gate": "shared_file_impact", "status": "disabled"}.'
        )

    def test_disabled_exits_zero(self, section: str) -> None:
        lower = section.lower()
        assert "disabled" in lower and "exit 0" in lower, (
            "docs/cli-reference.md '### `check-shared-file-impact`' must state that a disabled gate exits 0."
        )

    def test_enabled_status_line_field_enumeration(self, section: str) -> None:
        for field in ('"gate"', '"tier"', '"status"', '"findings"', '"scope_hash"'):
            assert field in section, (
                f"docs/cli-reference.md '### `check-shared-file-impact`' enabled status-line JSON "
                f"enumeration must include the {field} field (spec 5.2)."
            )

    def test_findings_field_documented_as_first_stdout_line(self, section: str) -> None:
        lower = section.lower()
        assert "first stdout line" in lower, (
            "docs/cli-reference.md '### `check-shared-file-impact`' must state that the gate status "
            "line is printed as the FIRST stdout line (spec 5.2)."
        )


@pytest.mark.unit
class TestCheckSharedFileImpactGatePassRecord:
    """AC-3 (spec 4.2, 5.3): the persisted [GATE_PASS shared_file_impact] record."""

    def test_gate_pass_marker_documented(self, section: str) -> None:
        assert "[GATE_PASS shared_file_impact]" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must document the "
            "[GATE_PASS shared_file_impact] marker persisted by a passing enabled run."
        )

    def test_sole_authorized_builder_documented(self, section: str) -> None:
        assert "devbench.gate_records.compose_gate_pass_record" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must name "
            "devbench.gate_records.compose_gate_pass_record as the sole authorized builder of the "
            "[GATE_PASS shared_file_impact] marker text -- the record is written by the command, "
            "never by agent prose."
        )

    def test_blocking_run_writes_no_record(self, section: str) -> None:
        lower = section.lower()
        assert "writes no record" in lower or "persist no record" in lower or "persists no record" in lower, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must state that a "
            "blocking run, or a disabled gate, writes no [GATE_PASS shared_file_impact] record."
        )


@pytest.mark.unit
class TestMarkDoneSharedFileImpactRequirement:
    """AC-4 (spec 4.2, AC-6, AC-16): the mark-done requirement and its remediation."""

    def test_remediation_command_documented(self, section: str) -> None:
        assert "uv run devbench check-shared-file-impact E9-F1-S1-T1" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must document the exact "
            "mark-done remediation command 'uv run devbench check-shared-file-impact <unit-id>'."
        )

    def test_gate_pass_requirement_named(self, section: str) -> None:
        lower = section.lower()
        assert "mark-done" in lower and "refuses" in lower, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must state that "
            "mark-done refuses when the gate is enabled and no fresh record or operator waiver "
            "exists."
        )


@pytest.mark.unit
class TestCheckSharedFileImpactStaleRecordAndWaiver:
    """AC-5 (spec 4.2, AC-7): stale-record refusal; spec 3.6: the operator-waiver bypass."""

    def test_stale_record_wording_documented(self, section: str) -> None:
        assert "gate 'shared_file_impact' record is stale (scope changed since it ran)" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must document the exact "
            "spec-4.2 stale-record refusal wording."
        )

    def test_operator_waiver_bypass_documented(self, section: str) -> None:
        assert "[GATE_WAIVER shared_file_impact]" in section, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must document the "
            "operator-attributed [GATE_WAIVER shared_file_impact] marker as satisfying the mark-done "
            "requirement in place of a record."
        )
        lower = section.lower()
        assert "executor-attributed waiver alone" in lower or "executor-attributed" in lower, (
            "docs/cli-reference.md '### `check-shared-file-impact`' section must state that an "
            "executor-attributed waiver alone is never sufficient for this machine-blocking gate "
            "(spec Section 3.6)."
        )
