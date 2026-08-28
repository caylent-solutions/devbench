"""Structural pins for the ``check-write-path`` verb in docs/cli-reference.md
(E7-F1-S1-T1, spec `integration-reality-gates-hardening.md` section 4.8, AC-WP-009).

Verifies that docs/cli-reference.md documents, under the `## Gates` section:
- The `### check-write-path` subsection (Contents entry: the `## Gates`
  bullet already links here -- AC-WP-009 does not require a second,
  per-command Contents bullet, matching every other `### <verb>` subsection
  in this file, e.g. `### gates`, `### log-waiver`, `### wire-gate`).
- The usage string, matched VERBATIM against `cli._COMMANDS["check-write-path"]`'s
  own description (single source of truth for `--help`).
- Exit codes 0/1/2 and the exact disabled status line.

Every assertion below is bounded to the `### check-write-path` section
(stopped at the NEXT `##`/`###` heading, never running to end-of-document)
and checked against a HAND-TYPED literal of the expected wording, never
against text derived from the thing being checked -- so a pin cannot be
satisfied by content relocated under a different heading.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench import cli

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

_HEADING_RE = re.compile(r"^#{2,3} ", re.MULTILINE)


def _doc_text() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _section() -> str:
    """Return only the ``check-write-path`` section, bounded at the NEXT
    ``##``/``###`` heading (never end-of-document)."""
    text = _doc_text()
    start = text.index("### `check-write-path`")
    after_heading = start + len("### `check-write-path`")
    match = _HEADING_RE.search(text, after_heading)
    end = match.start() if match is not None else len(text)
    return text[start:end]


@pytest.mark.unit
class TestCheckWritePathSectionExists:
    def test_section_heading_present(self) -> None:
        assert "### `check-write-path`" in _doc_text(), (
            "docs/cli-reference.md must contain a '### `check-write-path`' section (spec 4.8, AC-WP-009)."
        )

    def test_section_lives_under_gates(self) -> None:
        text = _doc_text()
        gates_idx = text.index("## Gates")
        check_write_path_idx = text.index("### `check-write-path`")
        next_h2 = text.index("## Backlog write")
        assert gates_idx < check_write_path_idx < next_h2, (
            "'### `check-write-path`' must live inside the '## Gates' section "
            "(spec 4.8, AC-WP-009), between '## Gates' and the next H2 heading."
        )

    def test_section_is_nonempty(self) -> None:
        section = _section()
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "'### `check-write-path`' must contain substantive documentation, not just a heading (spec 4.8)."
        )


@pytest.mark.unit
class TestCheckWritePathContentsEntry:
    """AC-WP-009's Contents entry: this file's `## Contents` table links only
    to top-level `##` sections (never per-command `###` subsections -- every
    other gate verb, e.g. `wire-gate`/`log-waiver`, is documented the same
    way), so the pre-existing `[Gates](#gates)` bullet IS the Contents entry
    for every command living inside `## Gates`, `check-write-path` included."""

    def test_contents_links_to_gates_section(self) -> None:
        text = _doc_text()
        contents_idx = text.index("## Contents")
        next_section = text.index("\n---", contents_idx)
        contents_block = text[contents_idx:next_section]
        assert "[Gates](#gates)" in contents_block, (
            "docs/cli-reference.md Contents table must link to '## Gates' "
            "(spec 4.8, AC-WP-009): 'check-write-path' lives inside that "
            "section, so the section-level Contents bullet is its entry."
        )


@pytest.mark.unit
class TestCheckWritePathUsageStringMatchesCommandsVerbatim:
    """The usage string documented in cli-reference.md must be VERBATIM the
    same text as `cli._COMMANDS["check-write-path"]`'s own description --
    single source of truth for `--help` (spec Section 14)."""

    def test_usage_string_matches_commands_description(self) -> None:
        _func, _min_args, description = cli._COMMANDS["check-write-path"]
        section = _section()
        assert description in section, (
            "'### `check-write-path`' must reproduce cli._COMMANDS "
            f"['check-write-path']'s description verbatim: {description!r}"
        )

    def test_usage_string_is_the_spec_section_14_string(self) -> None:
        _func, _min_args, description = cli._COMMANDS["check-write-path"]
        assert description == "Write-path audit: check-write-path <id> --flag <name>", (
            "cli._COMMANDS['check-write-path']'s description must be the exact spec Section 14 usage string."
        )

    def test_bare_command_usage_documented(self) -> None:
        section = _section()
        assert "check-write-path <id> --flag <name>" in section, (
            "'### `check-write-path`' must document the bare usage "
            "'check-write-path <id> --flag <name>' (spec Section 14)."
        )


@pytest.mark.unit
class TestCheckWritePathDisabledStatusLine:
    def test_disabled_status_line_documented_exactly(self) -> None:
        """Pinned against the EMITTED bytes (`_gate_disabled_line`, which
        `json.dumps` with default separators, i.e. a space after each `:`
        and `,`), not a hand-typed compact literal the verb never prints --
        code_review round 1 (E7-F1-S1-T1 Blocking 2) caught a prior version
        of this pin asserting the compact form, which actively blocked the
        doc correction it was meant to guard."""
        section = _section()
        assert '{"gate": "write_path_audit", "status": "disabled"}' in section, (
            "'### `check-write-path`' must document the exact disabled status line (spec 4.1, AC-WP-006)."
        )


@pytest.mark.unit
class TestCheckWritePathExitCodes:
    def test_exit_code_zero_documented(self) -> None:
        section = _section()
        assert "`0` -- the gate is disabled/unconfigured" in section, (
            "'### `check-write-path`' must document exit code 0 (spec Section 7)."
        )

    def test_exit_code_one_documented(self) -> None:
        section = _section()
        assert "`1` -- the unit id is unknown" in section, (
            "'### `check-write-path`' must document exit code 1 (spec Section 7)."
        )

    def test_exit_code_two_documented(self) -> None:
        section = _section()
        assert "`2` -- a usage error naming the offending argument" in section, (
            "'### `check-write-path`' must document exit code 2 (spec Section 7)."
        )


@pytest.mark.unit
class TestCheckWritePathVerdictVocabulary:
    def test_live_verdict_documented(self) -> None:
        section = _section()
        assert '`live` is `status: "pass"`' in section, (
            "'### `check-write-path`' must document the `live` verdict's status mapping (spec 4.8, AC-WP-003)."
        )

    def test_indeterminate_never_blocks_documented(self) -> None:
        section = _section()
        lower = section.lower()
        assert "indeterminate" in lower and "never block" in lower, (
            "'### `check-write-path`' must document that `indeterminate` never blocks (spec 4.8, AC-WP-005)."
        )

    def test_default_verdict_documented(self) -> None:
        section = _section()
        assert "`default`, `no_write_path` and `not_found` are each" in section, (
            "'### `check-write-path`' must document that `default` (and the "
            "other non-live verdicts) is a genuine finding (spec 4.8)."
        )


@pytest.mark.unit
class TestCheckWritePathWorkedExample:
    def test_worked_example_present(self) -> None:
        section = _section()
        assert "```" in section, "'### `check-write-path`' must include at least one worked-example code block."

    def test_worked_example_shows_the_5_2_status_line_shape(self) -> None:
        section = _section()
        assert '"tier": "judge-evidence"' in section, (
            "'### `check-write-path`' worked example must show the spec 5.2 "
            "status line carrying the judge-evidence tier."
        )
