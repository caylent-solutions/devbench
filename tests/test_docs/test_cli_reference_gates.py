"""Structural pins for the `gates` CLI verb addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- A top-level ``## Gates`` section (spec `integration-reality-gates-hardening.md`
  Section 8: new top-level section, Contents entry required).
- A Contents entry linking to that section.
- A ``### `gates`` `` subsection whose documented usage line matches the
  ``_COMMANDS["gates"]`` description string in ``devbench.cli`` EXACTLY, so
  the doc can never drift from the single-sourced ``--help`` text
  (AC-E2-F1-S2-T1-5, AC-E2-F1-S2-T1-6).
- The worked example table's four columns (gate/status/repos/provenance).

Spec source: spec/integration-reality-gates-hardening.md G2, Section 8,
Section 14. Task: E2-F1-S2-T1.
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
class TestGatesSectionExists:
    """The top-level ## Gates section must exist (spec Section 8)."""

    def test_gates_top_level_section_exists(self) -> None:
        text = _read_doc()
        assert "\n## Gates\n" in text, (
            "docs/cli-reference.md must contain a top-level '## Gates' section "
            "(spec integration-reality-gates-hardening.md Section 8) that will host "
            "every gate-related verb."
        )

    def test_gates_subcommand_section_exists(self) -> None:
        text = _read_doc()
        assert "### `gates`" in text, (
            "docs/cli-reference.md must contain a '### `gates`' section documenting "
            "the read-only gates overview verb (spec G2, section 4.1)."
        )

    def test_gates_subcommand_section_is_nonempty(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "docs/cli-reference.md '### `gates`' section must contain substantive documentation, not just a heading."
        )


@pytest.mark.unit
class TestGatesContentsEntry:
    """The Contents table must reference the Gates section (spec Section 8)."""

    def test_contents_includes_gates_entry(self) -> None:
        text = _read_doc()
        contents_idx = text.find("## Contents")
        assert contents_idx != -1, "docs/cli-reference.md must have a '## Contents' table"
        next_section = text.find("\n---", contents_idx)
        if next_section == -1:
            next_section = contents_idx + 1000
        contents_block = text[contents_idx:next_section]
        assert "[Gates](#gates)" in contents_block, (
            "docs/cli-reference.md Contents table must include a '[Gates](#gates)' entry "
            "linking to the ## Gates section (spec Section 8)."
        )


@pytest.mark.unit
class TestGatesUsageMatchesRegistry:
    """The documented usage line must match _COMMANDS['gates'] EXACTLY (single source of truth)."""

    def test_gates_registered_in_commands(self) -> None:
        assert "gates" in _COMMANDS, "devbench.cli._COMMANDS must register a 'gates' entry"

    def test_gates_takes_zero_required_positional_arguments(self) -> None:
        _, min_args, _ = _COMMANDS["gates"]
        assert min_args == 0, "'gates' must require zero positional arguments (it is a global overview verb)"

    def test_documented_usage_matches_commands_description_exactly(self) -> None:
        _, _, description = _COMMANDS["gates"]
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        assert description in section, (
            "docs/cli-reference.md '### `gates`' section must contain the exact "
            f"_COMMANDS['gates'] description string {description!r} verbatim, so the "
            "documented usage can never drift from the single-sourced --help text "
            "(spec Section 14 snapshot)."
        )

    def test_commands_description_matches_section_14_snapshot(self) -> None:
        _, _, description = _COMMANDS["gates"]
        assert description == "Show every gate's tier, status and repo overrides", (
            "devbench.cli._COMMANDS['gates'] description must match the spec Section 14 "
            "--help snapshot wording exactly."
        )

    def test_bare_gates_usage_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        has_bare = "devbench gates" in section or "uv run devbench gates" in section
        assert has_bare, "docs/cli-reference.md '### `gates`' section must document the bare 'devbench gates' usage."


@pytest.mark.unit
class TestGatesTableColumns:
    """The gates section must document all four rendered table columns (AC-E2-F1-S2-T1-1..3)."""

    @pytest.mark.parametrize("column", ["gate", "status", "repos", "provenance"])
    def test_column_documented(self, column: str) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        assert column in section, f"docs/cli-reference.md '### `gates`' section must document the '{column}' column."

    def test_no_override_placeholder_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        assert "`-`" in section, (
            "docs/cli-reference.md '### `gates`' section must document the '-' "
            "no-override placeholder shown when a gate has no repo override (AC-E2-F1-S2-T1-2)."
        )

    def test_provenance_layer_labels_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        for label in ("builtin", "project", "repo", "env"):
            assert label in section, (
                f"docs/cli-reference.md '### `gates`' section must document the '{label}' "
                "provenance layer label (AC-E2-F1-S2-T1-3)."
            )


@pytest.mark.unit
class TestGatesErrorPathDocumented:
    """The config-load-failure error path must be documented (spec Section 7; AC-E2-F1-S2-T1-4)."""

    def test_exit_1_on_config_load_failure_documented(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        lower = section.lower()
        assert "exits 1" in lower or "exit 1" in lower, (
            "docs/cli-reference.md '### `gates`' section must document that a config load "
            "failure exits 1 (spec Section 7, AC-E2-F1-S2-T1-4)."
        )
        assert "loader" in lower, (
            "docs/cli-reference.md '### `gates`' section must document that the printed "
            "error is the loader's own fail-fast message (AC-E2-F1-S2-T1-4)."
        )


@pytest.mark.unit
class TestGatesWorkedExample:
    """The gates section must include a worked example (spec G2)."""

    def test_example_code_block_present(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        assert section.count("```") >= 4, (
            "docs/cli-reference.md '### `gates`' section must include at least two code "
            "blocks (bare usage + worked example output, spec G2)."
        )

    def test_example_shows_eight_declared_gates(self) -> None:
        text = _read_doc()
        section = _extract_section(text, "### `gates`")
        assert section, "### `gates` section must exist"
        for gate in (
            "reachability",
            "ancestry",
            "shared_file_impact",
            "fixture_consistency",
            "write_path_audit",
            "newly_reachable_paths",
            "composition_root",
            "layout_geometry",
        ):
            assert gate in section, f"docs/cli-reference.md '### `gates`' section must name the '{gate}' gate."
