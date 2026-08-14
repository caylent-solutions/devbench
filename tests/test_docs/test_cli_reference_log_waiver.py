"""Structural pins for the `log-waiver` CLI verb addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- A ``### `log-waiver`` `` subsection living under the top-level ``## Gates``
  section (spec `integration-reality-gates-hardening.md` Section 8: new
  verbs documented under ``## Gates``; per-command doc-pin module required).
- The documented usage line matches ``_COMMANDS["log-waiver"]`` EXACTLY, so
  the doc can never drift from the single-sourced ``--help`` text (spec
  Section 14 snapshot).
- Every argument (``<judge>``, ``<id>``, ``--gate``, ``--target``,
  ``--reason``, ``--operator``) and every exit code (0/1/2) from spec
  section 4.9 is documented.

Spec source: spec/integration-reality-gates-hardening.md section 4.9, 5.3,
Section 8, Section 14, G7. Task: E2-F4-S1-T1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.cli import _COMMANDS

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


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
class TestLogWaiverSectionExists:
    """The `### log-waiver` subsection must exist under `## Gates` (spec Section 8)."""

    def test_log_waiver_subsection_exists(self) -> None:
        text = _read_doc()
        assert "### `log-waiver`" in text, (
            "docs/cli-reference.md must contain a '### `log-waiver`' section documenting "
            "the structured gate-waiver verb (spec Section 8, 4.9)."
        )

    def test_log_waiver_lives_under_gates_section(self) -> None:
        text = _read_doc()
        gates_section = _extract_section(text, "## Gates")
        assert "### `log-waiver`" in gates_section, (
            "'### `log-waiver`' must live inside the top-level '## Gates' section "
            "(spec Section 8: every gate-related verb is homed there)."
        )

    def test_log_waiver_subsection_is_nonempty(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "docs/cli-reference.md '### `log-waiver`' section must contain substantive "
            "documentation, not just a heading."
        )


@pytest.mark.unit
class TestLogWaiverUsageMatchesRegistry:
    """The documented usage line must match _COMMANDS['log-waiver'] EXACTLY (single source of truth)."""

    def test_log_waiver_registered_in_commands(self) -> None:
        assert "log-waiver" in _COMMANDS, "devbench.cli._COMMANDS must register a 'log-waiver' entry"

    def test_log_waiver_takes_two_required_positional_arguments(self) -> None:
        _, min_args, _ = _COMMANDS["log-waiver"]
        assert min_args == 2, "'log-waiver' must require two positional arguments (<judge> and <id>)"

    def test_documented_usage_matches_commands_description_exactly(self) -> None:
        _, _, description = _COMMANDS["log-waiver"]
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert description in section, (
            "docs/cli-reference.md '### `log-waiver`' section must contain the exact "
            f"_COMMANDS['log-waiver'] description string {description!r} verbatim, so the "
            "documented usage can never drift from the single-sourced --help text "
            "(spec Section 14 snapshot)."
        )

    def test_commands_description_matches_section_14_snapshot(self) -> None:
        _, _, description = _COMMANDS["log-waiver"]
        assert description == (
            "Record a structured gate waiver: log-waiver <judge> <id> --gate <g> --target <t> --reason <r> [--operator]"
        ), "devbench.cli._COMMANDS['log-waiver'] description must match the spec Section 14 --help snapshot wording."

    def test_bare_usage_line_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert "log-waiver <judge> <id> --gate <g> --target <t> --reason <r> [--operator]" in section, (
            "docs/cli-reference.md '### `log-waiver`' section must document the full usage line."
        )


@pytest.mark.unit
class TestLogWaiverArgumentsDocumented:
    """Every argument from spec 4.9 must be documented."""

    @pytest.mark.parametrize(
        "token",
        ["<judge>", "<id>", "--gate", "--target", "--reason", "--operator"],
    )
    def test_argument_documented(self, token: str) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert token in section, (
            f"docs/cli-reference.md '### `log-waiver`' section must document the '{token}' argument."
        )

    def test_judge_vocabulary_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        for judge in ("code_review", "test_review", "doc_review", "changes_manifest", "security_review"):
            assert judge in section, f"docs/cli-reference.md '### `log-waiver`' section must name judge '{judge}'."

    def test_machine_blocking_operator_requirement_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        lower = section.lower()
        assert "machine-blocking" in lower, (
            "docs/cli-reference.md '### `log-waiver`' section must document the machine-blocking "
            "gate operator-attribution requirement (spec Section 3.6)."
        )


@pytest.mark.unit
class TestLogWaiverExitCodesDocumented:
    """The 0/1/2 exit-code contract from spec section 4.9 must be documented."""

    def test_exit_0_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert "`0`" in section, "docs/cli-reference.md '### `log-waiver`' section must document exit code 0."

    def test_exit_1_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert "`1`" in section, "docs/cli-reference.md '### `log-waiver`' section must document exit code 1."

    def test_exit_2_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert "`2`" in section, "docs/cli-reference.md '### `log-waiver`' section must document exit code 2."


@pytest.mark.unit
class TestLogWaiverConsumersDocumented:
    """validate-backlog and report must be documented as marker consumers (spec 4.9)."""

    def test_validate_backlog_grammar_rule_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert "validate-backlog" in section, (
            "docs/cli-reference.md '### `log-waiver`' section must document the validate-backlog "
            "grammar rule that rejects a malformed marker."
        )

    def test_report_count_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert "report" in section, (
            "docs/cli-reference.md '### `log-waiver`' section must document that 'report' counts outstanding waivers."
        )


@pytest.mark.unit
class TestLogWaiverWorkedExample:
    """The log-waiver section must include a worked example (spec G7)."""

    def test_example_code_block_present(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert section.count("```") >= 4, (
            "docs/cli-reference.md '### `log-waiver`' section must include at least two code "
            "blocks (bare usage + worked example, spec G7)."
        )

    def test_example_shows_gate_waiver_json_output(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-waiver`")
        assert section, "### `log-waiver` section must exist"
        assert '"attribution"' in section, (
            "docs/cli-reference.md '### `log-waiver`' section's worked example must show the "
            "JSON output's 'attribution' field."
        )
