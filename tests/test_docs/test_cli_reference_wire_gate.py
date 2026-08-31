r"""Structural pins for the `wire-gate` verb addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The `### \`wire-gate\`` section under `## Gates` (AC-DOC-001).
- The usage string: `wire-gate <gate-task-id> --blocks-roots`.
- The `--blocks-roots` flag.
- The exit codes: 0 (wired), 1 (missing / inconsistently-wired gate task or
  root), 2 (usage error).
- A worked example.

Spec source: spec/integration-reality-gates-hardening.md sections 4.5
(317-D23), 4.9, and 8 (AC-15). Work unit E4-F1-S1-T2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.cli import _ANCESTRY_GATE_TASK_TITLE_MARKER, _COMMANDS

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
class TestWireGateSectionExists:
    """The `wire-gate` section must exist in cli-reference.md under `## Gates`."""

    def test_wire_gate_section_exists(self) -> None:
        text = _read_doc()
        assert "### `wire-gate`" in text, (
            "docs/cli-reference.md must contain a '### `wire-gate`' section documenting "
            "the ancestry-gate fan-in verb (spec 4.5, 317-D23; AC-DOC-001)."
        )

    def test_wire_gate_section_is_nonempty(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "docs/cli-reference.md '### `wire-gate`' section must contain substantive "
            "documentation, not just a heading."
        )

    def test_wire_gate_section_lives_under_gates(self) -> None:
        text = _read_doc()
        gates_idx = text.find("## Gates")
        wire_gate_idx = text.find("### `wire-gate`")
        backlog_write_idx = text.find("## Backlog write")
        assert gates_idx != -1, "'## Gates' section not found in cli-reference.md"
        assert wire_gate_idx != -1, "'### `wire-gate`' section not found in cli-reference.md"
        assert gates_idx < wire_gate_idx, "'### `wire-gate`' must live under the '## Gates' section (AC-DOC-001)"
        if backlog_write_idx != -1:
            assert wire_gate_idx < backlog_write_idx, (
                "'### `wire-gate`' must appear before the next top-level section after '## Gates'"
            )

    def test_gates_section_intro_names_wire_gate(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "## Gates")
        assert section, "'## Gates' section must exist"
        assert "`wire-gate`" in section, (
            "docs/cli-reference.md '## Gates' section intro must name 'wire-gate' among the verbs it documents."
        )


@pytest.mark.unit
class TestWireGateUsageString:
    """The usage string must be documented (spec 4.5)."""

    def test_usage_string_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        assert "wire-gate <gate-task-id> --blocks-roots" in section, (
            "docs/cli-reference.md '### `wire-gate`' section must document the usage string "
            "'wire-gate <gate-task-id> --blocks-roots'."
        )


@pytest.mark.unit
class TestWireGateBlocksRootsFlag:
    """The `--blocks-roots` flag must be documented (spec 4.9)."""

    def test_blocks_roots_flag_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        assert "--blocks-roots" in section, (
            "docs/cli-reference.md '### `wire-gate`' section must document the '--blocks-roots' flag."
        )

    def test_blocks_roots_documented_as_required(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        lower = section.lower()
        assert "required" in lower, (
            "docs/cli-reference.md '### `wire-gate`' section must document that '--blocks-roots' "
            "is required (spec 4.9: omitting it is a usage error)."
        )


@pytest.mark.unit
class TestWireGateExitCodes:
    """The exit codes 0/1/2 must be documented (spec 4.9)."""

    def test_exit_code_table_present(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        assert "| Exit code | Meaning |" in section, (
            "docs/cli-reference.md '### `wire-gate`' section must include an exit-code table."
        )

    def test_exit_0_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert "| 0 |" in section

    def test_exit_1_names_missing_and_conflicting_gate_task(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert "| 1 |" in section
        assert "already wired to a different" in section.lower()
        assert "not found" in section.lower()

    def test_exit_1_documents_the_ancestry_gate_recognition_rule(self) -> None:
        """The exit-1 row must state how a conflicting gate task is recognised.

        `_is_ancestry_gate_task` in `src/devbench/cli.py` keys off a title
        substring, `_ANCESTRY_GATE_TASK_TITLE_MARKER`. A reader must be able
        to infer that recognition rule from the doc alone, so the doc must
        name the live marker string verbatim rather than a hand-typed copy
        that could drift from the implementation.
        """
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert "| 1 |" in section
        assert f"`{_ANCESTRY_GATE_TASK_TITLE_MARKER}`" in section, (
            "docs/cli-reference.md '### `wire-gate`' exit-1 row must name the live "
            f"_ANCESTRY_GATE_TASK_TITLE_MARKER value ({_ANCESTRY_GATE_TASK_TITLE_MARKER!r}) "
            "verbatim so the recognition rule cannot drift from the implementation."
        )
        assert "title suffix" in section.lower() or "recognised" in section.lower(), (
            "docs/cli-reference.md '### `wire-gate`' exit-1 row must explain that an "
            "ancestry-gate task is recognised by its generated title suffix."
        )

    def test_exit_2_is_a_usage_error(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert "| 2 |" in section
        assert "usage error" in section.lower()


@pytest.mark.unit
class TestWireGateIdempotency:
    """The idempotency contract must be documented (AC-WIRE-002)."""

    def test_idempotent_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        lower = section.lower()
        assert "idempotent" in lower, (
            "docs/cli-reference.md '### `wire-gate`' section must document that re-running the "
            "verb is idempotent (AC-WIRE-002)."
        )


@pytest.mark.unit
class TestWireGateWorkedExample:
    """The section must include at least one worked example."""

    def test_example_present(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        assert "```" in section, (
            "docs/cli-reference.md '### `wire-gate`' section must include at least one code "
            "block with a worked example."
        )

    def test_example_shows_json_output(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert '"gate_task"' in section
        assert '"wired_roots"' in section


@pytest.mark.unit
class TestWireGateCommandsDescriptionMatchesDoc:
    """AC-WIRE-005: the ``_COMMANDS`` ``--help`` description matches the doc.

    Mirrors the stronger cross-check convention used by
    ``tests/test_docs/test_cli_reference_log_waiver.py::``
    ``test_documented_usage_matches_commands_description_exactly`` so the
    ``_COMMANDS['wire-gate']`` description and the documented usage string
    in ``docs/cli-reference.md`` can never silently drift apart.
    """

    def test_documented_usage_matches_commands_description_exactly(self) -> None:
        _, _, description = _COMMANDS["wire-gate"]
        text = _read_doc()
        section = _extract_section(text, "### `wire-gate`")
        assert section, "### `wire-gate` section must exist"
        assert description in section, (
            "docs/cli-reference.md '### `wire-gate`' section must contain the exact "
            f"_COMMANDS['wire-gate'] description string {description!r} verbatim, so the "
            "documented usage can never drift from the single-sourced --help text "
            "(spec Section 14 snapshot)."
        )

    def test_min_args_is_zero(self) -> None:
        _, min_args, _ = _COMMANDS["wire-gate"]
        assert min_args == 0, (
            "wire-gate is registered in _VARIADIC_COMMANDS and owns all of its own usage "
            "validation via _parse_wire_gate_argv (AC-WIRE-004); a nonzero min_args would let "
            "main()'s pre-dispatch arity check reject a short invocation with exit 1 before "
            "cmd_wire_gate ever runs, contradicting the exit-2 usage contract documented above."
        )


@pytest.mark.unit
class TestWireGateDocPinReadsArtifactOffDisk:
    """The pin module reads its artifact off disk (no frozen copy)."""

    def test_cli_reference_doc_exists(self) -> None:
        assert CLI_REFERENCE_DOC.is_file(), f"docs/cli-reference.md missing at {CLI_REFERENCE_DOC}"
