"""Structural pins for the `log-newly-reachable` CLI verb addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- A ``### `log-newly-reachable`` `` subsection living under the top-level ``## Gates``
  section (spec `integration-reality-gates-hardening.md` Section 8: new verbs
  documented under ``## Gates``; per-command doc-pin module required).
- The documented usage line matches ``_COMMANDS["log-newly-reachable"]`` EXACTLY, so
  the doc can never drift from the single-sourced ``--help`` text (spec Section 14
  snapshot).
- Every argument (``<id>``, ``--path``, ``--method``, ``--result``) and every exit
  code (0/1/2) from spec section 4.9(a) is documented.

Spec source: spec/integration-reality-gates-hardening.md section 4.9(a), 5.3,
Section 8, Section 14. Task: E2-F4-S1-T2.
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
class TestLogNewlyReachableSectionExists:
    """The `### log-newly-reachable` subsection must exist under `## Gates` (spec Section 8)."""

    def test_log_newly_reachable_subsection_exists(self) -> None:
        text = _read_doc()
        assert "### `log-newly-reachable`" in text, (
            "docs/cli-reference.md must contain a '### `log-newly-reachable`' section documenting "
            "the structured newly-reachable-path verb (spec Section 8, 4.9(a))."
        )

    def test_log_newly_reachable_lives_under_gates_section(self) -> None:
        text = _read_doc()
        gates_section = _extract_section(text, "## Gates")
        assert "### `log-newly-reachable`" in gates_section, (
            "'### `log-newly-reachable`' must live inside the top-level '## Gates' section "
            "(spec Section 8: every gate-related verb is homed there)."
        )

    def test_log_newly_reachable_subsection_is_nonempty(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "docs/cli-reference.md '### `log-newly-reachable`' section must contain substantive "
            "documentation, not just a heading."
        )


@pytest.mark.unit
class TestLogNewlyReachableUsageMatchesRegistry:
    """The documented usage line must match _COMMANDS['log-newly-reachable'] EXACTLY (single source of truth)."""

    def test_log_newly_reachable_registered_in_commands(self) -> None:
        assert "log-newly-reachable" in _COMMANDS, "devbench.cli._COMMANDS must register a 'log-newly-reachable' entry"

    def test_log_newly_reachable_takes_one_required_positional_argument(self) -> None:
        _, min_args, _ = _COMMANDS["log-newly-reachable"]
        assert min_args == 1, "'log-newly-reachable' must require one positional argument (<id>)"

    def test_documented_usage_matches_commands_description_exactly(self) -> None:
        _, _, description = _COMMANDS["log-newly-reachable"]
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert description in section, (
            "docs/cli-reference.md '### `log-newly-reachable`' section must contain the exact "
            f"_COMMANDS['log-newly-reachable'] description string {description!r} verbatim, so the "
            "documented usage can never drift from the single-sourced --help text "
            "(spec Section 14 snapshot)."
        )

    def test_commands_description_matches_section_14_snapshot(self) -> None:
        _, _, description = _COMMANDS["log-newly-reachable"]
        assert description == (
            "Record a newly-reachable-path verification: log-newly-reachable <id> --path <p> --method <m> --result <r>"
        ), (
            "devbench.cli._COMMANDS['log-newly-reachable'] description must match the spec Section 14 "
            "--help snapshot wording."
        )

    def test_bare_usage_line_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert "log-newly-reachable <id> --path <p> --method <m> --result <r>" in section, (
            "docs/cli-reference.md '### `log-newly-reachable`' section must document the full usage line."
        )


@pytest.mark.unit
class TestLogNewlyReachableArgumentsDocumented:
    """Every argument from spec 4.9(a) must be documented."""

    @pytest.mark.parametrize("token", ["<id>", "--path", "--method", "--result"])
    def test_argument_documented(self, token: str) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert token in section, (
            f"docs/cli-reference.md '### `log-newly-reachable`' section must document the '{token}' argument."
        )

    @pytest.mark.parametrize(
        "method",
        ["manual", "unit_test", "integration_test", "functional_test"],
    )
    def test_method_vocabulary_documented(self, method: str) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert method in section, (
            f"docs/cli-reference.md '### `log-newly-reachable`' section must name method '{method}'."
        )

    @pytest.mark.parametrize("result_value", ["verified", "broken"])
    def test_result_vocabulary_documented(self, result_value: str) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert result_value in section, (
            f"docs/cli-reference.md '### `log-newly-reachable`' section must name result '{result_value}'."
        )


@pytest.mark.unit
class TestLogNewlyReachableExitCodesDocumented:
    """The 0/1/2 exit-code contract from spec section 4.9(a) must be documented."""

    def test_exit_0_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert "`0`" in section, "docs/cli-reference.md '### `log-newly-reachable`' section must document exit code 0."

    def test_exit_1_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert "`1`" in section, "docs/cli-reference.md '### `log-newly-reachable`' section must document exit code 1."

    def test_exit_2_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert "`2`" in section, "docs/cli-reference.md '### `log-newly-reachable`' section must document exit code 2."


@pytest.mark.unit
class TestLogNewlyReachableEvidenceHorizonDocumented:
    """The TDD Cycle Log / Evidence-fetch survival property (AC-21) must be documented."""

    def test_tdd_cycle_log_section_named(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert "TDD Cycle Log" in section, (
            "docs/cli-reference.md '### `log-newly-reachable`' section must name the '## TDD Cycle Log' "
            "audit surface the marker is written into."
        )

    def test_strip_comments_evidence_fetch_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert "read-unit --strip-comments" in section, (
            "docs/cli-reference.md '### `log-newly-reachable`' section must document that the marker "
            "survives the 'read-unit --strip-comments' Evidence fetch (AC-21)."
        )


@pytest.mark.unit
class TestLogNewlyReachableWorkedExample:
    """The log-newly-reachable section must include a worked example."""

    def test_example_code_block_present(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert section.count("```") >= 4, (
            "docs/cli-reference.md '### `log-newly-reachable`' section must include at least two code "
            "blocks (bare usage + worked example)."
        )

    def test_example_shows_newly_reachable_json_output(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `log-newly-reachable`")
        assert section, "### `log-newly-reachable` section must exist"
        assert '"result"' in section, (
            "docs/cli-reference.md '### `log-newly-reachable`' section's worked example must show the "
            "JSON output's 'result' field."
        )
