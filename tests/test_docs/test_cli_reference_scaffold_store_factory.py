"""Structural pins for the ``scaffold-store-factory`` verb in docs/cli-reference.md
(E9-F1-S1-T2, spec `integration-reality-gates-hardening.md` section 4.9(b), AC-15).

Verifies that docs/cli-reference.md documents, under the `## Gates` section:
- The `### \\`scaffold-store-factory\\`` subsection (Contents entry: the
  `## Gates` bullet already links here -- matching every other `### <verb>`
  subsection in this file, e.g. `### wire-gate`, `### check-write-path`).
- The usage string, matched VERBATIM against
  `cli._COMMANDS["scaffold-store-factory"]`'s own description (single
  source of truth for `--help`, spec Section 14).
- The `<unit-id>` argument and the `--out` flag.
- Exit codes 0, 1 and 2, including the refuse-to-overwrite exit-1 case.
- That `--force` is absent by design.
- A worked example.

Every assertion below is bounded to the `### \\`scaffold-store-factory\\`\''
section (stopped at the NEXT `##`/`###` heading, never running to
end-of-document) and checked against a HAND-TYPED literal of the expected
wording, never against text derived from the thing being checked -- so a
pin cannot be satisfied by content relocated under a different heading.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench import cli

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

_HEADING_RE = re.compile(r"^#{2,3} ", re.MULTILINE)
_SECTION_HEADING = "### `scaffold-store-factory`"


def _doc_text() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _section() -> str:
    """Return only the ``scaffold-store-factory`` section, bounded at the
    NEXT ``##``/``###`` heading (never end-of-document)."""
    text = _doc_text()
    start = text.index(_SECTION_HEADING)
    after_heading = start + len(_SECTION_HEADING)
    match = _HEADING_RE.search(text, after_heading)
    end = match.start() if match is not None else len(text)
    return text[start:end]


@pytest.mark.unit
class TestScaffoldStoreFactorySectionExists:
    def test_section_heading_present(self) -> None:
        assert _SECTION_HEADING in _doc_text(), (
            "docs/cli-reference.md must contain a '### `scaffold-store-factory`' section (spec 4.9(b), AC-15)."
        )

    def test_section_lives_under_gates(self) -> None:
        text = _doc_text()
        gates_idx = text.index("## Gates")
        scaffold_idx = text.index(_SECTION_HEADING)
        next_h2 = text.index("## Backlog write")
        assert gates_idx < scaffold_idx < next_h2, (
            "'### `scaffold-store-factory`' must live inside the '## Gates' "
            "section (spec 4.9(b)), between '## Gates' and the next H2 heading."
        )

    def test_section_is_nonempty(self) -> None:
        section = _section()
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "'### `scaffold-store-factory`' must contain substantive documentation, not just a heading (spec 4.9(b))."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryContentsEntry:
    """The `## Contents` table links only to top-level `##` sections (never
    per-command `###` subsections -- every other gate verb, e.g.
    `wire-gate`/`check-write-path`, is documented the same way), so the
    pre-existing `[Gates](#gates)` bullet IS the Contents entry for
    `scaffold-store-factory` too."""

    def test_contents_links_to_gates_section(self) -> None:
        text = _doc_text()
        contents_idx = text.index("## Contents")
        next_section = text.index("\n---", contents_idx)
        contents_block = text[contents_idx:next_section]
        assert "[Gates](#gates)" in contents_block, (
            "docs/cli-reference.md Contents table must link to '## Gates' "
            "(spec 4.9(b)): 'scaffold-store-factory' lives inside that "
            "section, so the section-level Contents bullet is its entry."
        )


@pytest.mark.unit
class TestGatesSectionIntroNamesScaffoldStoreFactory:
    """doc_review round 2 (E9-F1-S1-T2, Blocking): the `## Gates` section's
    own intro sentence enumerates every verb it documents ("today it
    documents `gates`, `log-waiver`, ..."); `wire-gate` and
    `check-write-path` were both swept into that enumeration when their
    subsections landed, so `scaffold-store-factory` must be too, and the
    section's "Read-only introspection" framing must be widened to cover a
    verb that writes a file to `--out`."""

    def _gates_intro_paragraph(self) -> str:
        text = _doc_text()
        heading = "## Gates\n"
        heading_idx = text.index(heading)
        para_start = heading_idx + len(heading)
        para_end = text.index("\n\n", para_start)
        return text[para_start:para_end]

    def test_gates_intro_names_scaffold_store_factory(self) -> None:
        intro = self._gates_intro_paragraph()
        assert "`scaffold-store-factory`" in intro, (
            "The '## Gates' section intro must enumerate 'scaffold-store-factory' "
            "among the verbs it documents (doc_review round 2, E9-F1-S1-T2)."
        )

    def test_gates_intro_is_not_read_only_only(self) -> None:
        intro = self._gates_intro_paragraph()
        assert "Read-only introspection and structured-waiver tooling for the eight" not in intro, (
            "The stale, unwidened 'Read-only introspection and structured-waiver "
            "tooling' framing must be replaced to acknowledge the new "
            "file-generating verb."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryUsageStringMatchesCommandsVerbatim:
    """The usage string documented in cli-reference.md must be VERBATIM the
    same text as `cli._COMMANDS["scaffold-store-factory"]`'s own
    description -- single source of truth for `--help` (spec Section 14)."""

    def test_usage_string_matches_commands_description(self) -> None:
        _func, _min_args, description = cli._COMMANDS["scaffold-store-factory"]
        section = _section()
        assert description in section, (
            "'### `scaffold-store-factory`' must reproduce cli._COMMANDS "
            f"['scaffold-store-factory']'s description verbatim: {description!r}"
        )

    def test_usage_string_is_the_expected_summary(self) -> None:
        _func, _min_args, description = cli._COMMANDS["scaffold-store-factory"]
        assert description == (
            "Emit a composition-root store-factory test skeleton: scaffold-store-factory <id> --out <path>"
        ), "cli._COMMANDS['scaffold-store-factory']'s description must be the exact Section 14 usage summary."

    def test_bare_command_usage_documented(self) -> None:
        section = _section()
        assert "scaffold-store-factory <id> --out <path>" in section, (
            "'### `scaffold-store-factory`' must document the bare usage "
            "'scaffold-store-factory <id> --out <path>' (spec Section 14)."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryArguments:
    def test_unit_id_argument_documented(self) -> None:
        section = _section()
        assert "<id>" in section and "work-unit id" in section, (
            "'### `scaffold-store-factory`' must document that '<id>' is a work-unit id (spec 4.9(b))."
        )

    def test_out_flag_documented(self) -> None:
        section = _section()
        assert "--out <path>" in section, (
            "'### `scaffold-store-factory`' must document the '--out <path>' flag (spec 4.9(b))."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryForceAbsent:
    def test_force_flag_absent_by_design_documented(self) -> None:
        section = _section()
        assert "--force" in section, (
            "'### `scaffold-store-factory`' must mention '--force' when documenting its absence (spec 4.9)."
        )
        lower = section.lower()
        assert "does not exist on this verb" in lower or "absent by design" in lower, (
            "'### `scaffold-store-factory`' must state that '--force' is absent by design (spec 4.9)."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryExitCodes:
    def test_exit_code_zero_documented(self) -> None:
        section = _section()
        assert "| 0 |" in section, "'### `scaffold-store-factory`' must document exit code 0 (spec Section 7)."

    def test_exit_code_one_documented(self) -> None:
        section = _section()
        assert "| 1 |" in section, "'### `scaffold-store-factory`' must document exit code 1 (spec Section 7)."

    def test_exit_code_two_documented(self) -> None:
        section = _section()
        assert "| 2 |" in section, "'### `scaffold-store-factory`' must document exit code 2 (spec Section 7)."

    def test_refuse_to_overwrite_exit_one_documented(self) -> None:
        section = _section()
        assert "already exists" in section and "nothing written" in section, (
            "'### `scaffold-store-factory`' must document the refuse-to-overwrite exit-1 "
            "case (spec 4.9): an existing '--out' path is refused and nothing is written."
        )

    def test_usage_error_exit_two_documented(self) -> None:
        section = _section()
        assert "usage error naming the offending argument" in section, (
            "'### `scaffold-store-factory`' must document that exit 2 names the offending argument (spec Section 7)."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryUndetectableShape:
    def test_no_placeholder_skeleton_documented(self) -> None:
        section = _section()
        lower = section.lower()
        assert "no placeholder skeleton is ever written" in lower, (
            "'### `scaffold-store-factory`' must document that no placeholder/generic "
            "skeleton is ever emitted for an undetectable store shape (spec 4.9(b))."
        )

    def test_files_scanned_named_on_failure_documented(self) -> None:
        section = _section()
        lower = section.lower()
        assert "files it scanned" in lower or "files scanned" in lower, (
            "'### `scaffold-store-factory`' must document that an undetectable-shape "
            "failure names the files scanned (spec 4.9(b))."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryCompositionRootDocLink:
    def test_links_to_composition_root_testing_doc(self) -> None:
        section = _section()
        assert "composition-root-testing.md" in section, (
            "'### `scaffold-store-factory`' must link to docs/composition-root-testing.md (spec 4.9(b))."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryWorkedExample:
    def test_worked_example_present(self) -> None:
        section = _section()
        assert "```" in section, (
            "'### `scaffold-store-factory`' must include at least one code block with "
            "worked examples (spec Section 14)."
        )

    def test_worked_example_shows_command_and_output(self) -> None:
        section = _section()
        assert "uv run devbench scaffold-store-factory" in section, (
            "'### `scaffold-store-factory`' worked example must show the actual "
            "'uv run devbench scaffold-store-factory ...' invocation."
        )
        assert "Wrote " in section, (
            "'### `scaffold-store-factory`' worked example must show the 'Wrote <path> "
            "(detected store shape: <shape>)' success output."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryDoesNotClaimToSatisfyTheAC:
    """code_review + doc_review round 1 (E9-F1-S1-T2, Blocking): this section
    must NOT claim the emitted skeleton satisfies the composition-root
    acceptance criterion -- docs/composition-root-testing.md (shipped in the
    same change) states normatively that it does NOT, by itself, satisfy
    the AC. Pinned here so a future edit cannot silently reintroduce the
    contradiction."""

    def test_does_not_claim_the_skeleton_satisfies_the_ac(self) -> None:
        section = _section()
        assert "how the emitted skeleton satisfies" not in section, (
            "'### `scaffold-store-factory`' must not claim the emitted skeleton "
            "'satisfies' the composition-root acceptance criterion (contradicts "
            "docs/composition-root-testing.md)."
        )

    def test_states_the_skeleton_does_not_by_itself_satisfy_the_ac(self) -> None:
        section = _section()
        lower = section.lower()
        assert "does not by itself satisfy" in lower, (
            "'### `scaffold-store-factory`' must state the emitted skeleton relates "
            "to, but does NOT by itself satisfy, the composition-root acceptance "
            "criterion, matching docs/composition-root-testing.md's wording."
        )


@pytest.mark.unit
class TestScaffoldStoreFactoryCitesCurrentHeadingText:
    """code_review round 1 (E9-F1-S1-T2, Blocking): this section must cite
    docs/composition-root-testing.md's CURRENT heading text, not the
    pre-rename 'Recommended companion convention' title this same change
    renamed away from."""

    def test_does_not_cite_the_stale_heading_title(self) -> None:
        section = _section()
        assert '"Recommended companion convention" section' not in section, (
            "'### `scaffold-store-factory`' must not cite the pre-rename "
            "'Recommended companion convention' section title."
        )

    def test_cites_the_current_heading_text(self) -> None:
        section = _section()
        assert "Store-factory convention (v2): recommended companion convention plus a generator" in section, (
            "'### `scaffold-store-factory`' must cite docs/composition-root-testing.md's CURRENT heading text."
        )
