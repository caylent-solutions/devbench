"""Doc-pin for docs/newly-reachable-paths.md syncing to the shipped `log-newly-reachable` verb.

Source task E2-F4-S1-T2 shipped `cli.cmd_log_newly_reachable` / `cli.compose_newly_reachable_record`,
whose CHANGELOG.md and docs/cli-reference.md already declare the old
`log-comment`-into-`## Comments` `[NEWLY_REACHABLE]` prose convention superseded. This module pins
that docs/newly-reachable-paths.md (the workflow-rationale doc, distinct from the CLI arg reference)
has been synced to match:

- (a) the '## The audit trail' section documents `log-newly-reachable <id> --path <p> --method <m>
  --result <r>` writing a `[NEWLY_REACHABLE] <path> <method> <result>` marker into `## TDD Cycle Log`.
- (b) the doc no longer presents the `log-comment`-into-`## Comments` convention as the LIVE audit
  trail (the literal old invocation string must not appear anywhere in the doc).
- (c) every `--method`/`--result` token the doc shows for the verb (in a fenced code block that
  actually invokes `log-newly-reachable`) is a member of the shipped enumerations imported directly
  from `src/devbench/cli.py` -- so a stale example using `n/a` or `"none -- ..."` fails this pin.
- (d) no em-dash (U+2014) is present anywhere in the doc.

It also pins that the no-newly-reachable-paths case is documented via a supported mechanism (never
by invoking `log-newly-reachable` with a fabricated placeholder path/method/result), that the
'## Where this is enforced' bullet checks the marker in `## TDD Cycle Log` (not Comments/Agent Log),
and that '## Known limitations / follow-ups' records the structured marker as shipped rather than a
hypothetical future follow-up.

Task: E2-F4-S1-T4 (AC-FIX-001 through AC-FIX-007).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.cli import NEWLY_REACHABLE_METHODS, NEWLY_REACHABLE_RESULTS

REPO_ROOT = Path(__file__).parent.parent.parent
DOC_PATH = REPO_ROOT / "docs" / "newly-reachable-paths.md"

# The exact legacy invocation prefix the doc previously instructed as "the" audit trail. Its
# presence anywhere in the doc, in this exact present-tense-invocation shape, means the old
# convention is still being presented as live.
LEGACY_INVOCATION_PREFIX = 'log-comment executor <task-id> "[NEWLY_REACHABLE]'

# The shipped verb's usage line (spec section 4.9(a)) and marker grammar (spec section 5.3).
SHIPPED_USAGE_LINE = "log-newly-reachable <task-id> --path <p> --method <m> --result <r>"
SHIPPED_MARKER_GRAMMAR = "[NEWLY_REACHABLE] <path> <method> <result>"

_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9]*)\n(.*?)```", re.DOTALL)
_FLAG_TOKEN_RE = re.compile(r"--(method|result)\s+(\S+)")


def _read_doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading`` up to the next same-level heading."""
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


def _fenced_blocks(text: str) -> list[str]:
    return _FENCE_RE.findall(text)


def _clean_token(token: str) -> str:
    return token.strip("`\"',.()")


@pytest.mark.unit
class TestAuditTrailSectionDocumentsShippedVerb:
    """AC-FIX-001 / AC-FIX-006(a): the audit trail documents the shipped verb and marker grammar."""

    def test_audit_trail_section_exists(self) -> None:
        text = _read_doc()
        assert "## The audit trail" in text, "docs/newly-reachable-paths.md must retain a '## The audit trail' section."

    def test_audit_trail_documents_log_newly_reachable_invocation(self) -> None:
        section = _extract_section(_read_doc(), "## The audit trail")
        assert section, "'## The audit trail' section must exist"
        assert SHIPPED_USAGE_LINE in section, (
            f"'## The audit trail' must document the exact '{SHIPPED_USAGE_LINE}' invocation (spec 4.9(a))."
        )

    def test_audit_trail_documents_marker_grammar(self) -> None:
        section = _extract_section(_read_doc(), "## The audit trail")
        assert section, "'## The audit trail' section must exist"
        assert SHIPPED_MARKER_GRAMMAR in section, (
            "'## The audit trail' must document the exact "
            f"'{SHIPPED_MARKER_GRAMMAR}' marker grammar (spec 5.3 field order)."
        )

    def test_audit_trail_names_tdd_cycle_log_target(self) -> None:
        section = _extract_section(_read_doc(), "## The audit trail")
        assert section, "'## The audit trail' section must exist"
        assert "## TDD Cycle Log" in section, (
            "'## The audit trail' must name '## TDD Cycle Log' as the marker's insertion point."
        )


@pytest.mark.unit
class TestOldConventionNoLongerPresentedAsLive:
    """AC-FIX-001 / AC-FIX-006(b): the log-comment/Comments convention is superseded, not live."""

    def test_legacy_invocation_absent_from_whole_doc(self) -> None:
        text = _read_doc()
        assert LEGACY_INVOCATION_PREFIX not in text, (
            "docs/newly-reachable-paths.md must not instruct running "
            f"'{LEGACY_INVOCATION_PREFIX} ...' anywhere -- that convention is superseded by "
            "log-newly-reachable (E2-F4-S1-T2)."
        )

    def test_migration_note_marks_old_convention_superseded(self) -> None:
        section = _extract_section(_read_doc(), "## The audit trail")
        assert section, "'## The audit trail' section must exist"
        assert "superseded" in section.lower(), (
            "'## The audit trail' must contain a migration note marking the old "
            "log-comment/`## Comments` convention as superseded."
        )

    def test_migration_note_explains_comments_is_stripped(self) -> None:
        section = _extract_section(_read_doc(), "## The audit trail")
        assert section, "'## The audit trail' section must exist"
        assert "strip-comments" in section, (
            "The migration note must explain that '## Comments' is stripped by the "
            "'read-unit --strip-comments' Evidence fetch, hence invisible to judges."
        )


@pytest.mark.unit
class TestMethodResultVocabularyPin:
    """AC-FIX-002 / AC-FIX-006(c): every concrete --method/--result token shown for the verb is valid."""

    def test_enumerations_are_nonempty(self) -> None:
        # Guards against a vacuously-true pin if the production constants ever became empty.
        assert NEWLY_REACHABLE_METHODS, "cli.NEWLY_REACHABLE_METHODS must be non-empty"
        assert NEWLY_REACHABLE_RESULTS, "cli.NEWLY_REACHABLE_RESULTS must be non-empty"

    def test_no_invocation_uses_an_out_of_enum_method_or_result_token(self) -> None:
        text = _read_doc()
        offenders: list[str] = []
        for block in _fenced_blocks(text):
            if "log-newly-reachable" not in block:
                continue
            for flag, raw_token in _FLAG_TOKEN_RE.findall(block):
                token = _clean_token(raw_token)
                if token.startswith("<"):
                    continue  # placeholder like <m>/<r>, not a concrete example value
                valid = NEWLY_REACHABLE_METHODS if flag == "method" else NEWLY_REACHABLE_RESULTS
                if token not in valid:
                    offenders.append(f"--{flag} {token!r}")
        assert not offenders, (
            "docs/newly-reachable-paths.md shows an invocation of log-newly-reachable using a "
            f"value outside the shipped enumeration: {offenders}. Valid methods: "
            f"{sorted(NEWLY_REACHABLE_METHODS)}. Valid results: {sorted(NEWLY_REACHABLE_RESULTS)}."
        )

    def test_no_method_n_slash_a_literal_anywhere(self) -> None:
        text = _read_doc()
        assert "--method n/a" not in text, (
            "docs/newly-reachable-paths.md must not show '--method n/a' -- 'n/a' is not a member "
            "of cli.NEWLY_REACHABLE_METHODS and the verb rejects it with exit 2."
        )

    def test_no_result_none_placeholder_literal_anywhere(self) -> None:
        text = _read_doc()
        assert '--result "none' not in text, (
            "docs/newly-reachable-paths.md must not show '--result \"none ...'; 'none' is not "
            "a member of cli.NEWLY_REACHABLE_RESULTS and the verb rejects it with exit 2."
        )

    @pytest.mark.parametrize("method", sorted(NEWLY_REACHABLE_METHODS))
    def test_every_shipped_method_is_documented_somewhere(self, method: str) -> None:
        assert method in _read_doc(), (
            f"docs/newly-reachable-paths.md must document the shipped --method value {method!r}."
        )

    @pytest.mark.parametrize("result_value", sorted(NEWLY_REACHABLE_RESULTS))
    def test_every_shipped_result_is_documented_somewhere(self, result_value: str) -> None:
        assert result_value in _read_doc(), (
            f"docs/newly-reachable-paths.md must document the shipped --result value {result_value!r}."
        )


@pytest.mark.unit
class TestNoPathCaseUsesSupportedMechanismOnly:
    """AC-FIX-003: the no-newly-reachable-paths case never invokes the verb with fabricated values."""

    def test_no_path_case_section_exists(self) -> None:
        text = _read_doc()
        assert "no path is newly reachable" in text.lower(), (
            "docs/newly-reachable-paths.md must document the no-newly-reachable-paths case in its own named section."
        )

    def test_no_path_section_does_not_invoke_verb_with_placeholder_path(self) -> None:
        section = _extract_section(_read_doc(), "## When no path is newly reachable")
        assert section, "'## When no path is newly reachable' section must exist"
        for block in _fenced_blocks(section):
            assert "log-newly-reachable" not in block, (
                "The no-path-case section must not show an invocation of log-newly-reachable at "
                f"all (fabricated placeholder values are the exact bug this pin guards): {block!r}"
            )

    def test_no_path_section_names_a_supported_alternative_channel(self) -> None:
        section = _extract_section(_read_doc(), "## When no path is newly reachable")
        assert section, "'## When no path is newly reachable' section must exist"
        assert "log-tdd" in section or "log-comment" in section, (
            "The no-path-case section must name a supported channel (log-tdd or log-comment) for "
            "recording the absence of newly-reachable paths."
        )


@pytest.mark.unit
class TestEnforcementBulletChecksTddCycleLog:
    """AC-FIX-004: the enforcement bullet checks the marker in `## TDD Cycle Log`."""

    def test_enforcement_section_exists(self) -> None:
        assert "## Where this is enforced" in _read_doc()

    def test_code_reviewer_bullet_names_tdd_cycle_log(self) -> None:
        section = _extract_section(_read_doc(), "## Where this is enforced")
        assert section, "'## Where this is enforced' section must exist"
        assert "`## TDD Cycle Log`" in section, (
            "The code-reviewer enforcement bullet must check the '[NEWLY_REACHABLE]' marker in "
            "'## TDD Cycle Log', the Evidence-fetch-retained section."
        )

    def test_code_reviewer_bullet_no_longer_names_comments_agent_log(self) -> None:
        section = _extract_section(_read_doc(), "## Where this is enforced")
        assert section, "'## Where this is enforced' section must exist"
        assert "Comments/Agent Log" not in section, (
            "The code-reviewer enforcement bullet must not claim the marker is checked in "
            "'Comments/Agent Log' -- that surface is stripped by the judge Evidence fetch."
        )


@pytest.mark.unit
class TestKnownLimitationsReflectsShippedMarker:
    """AC-FIX-005: Known limitations records the marker as delivered, not a hypothetical follow-up."""

    def test_known_limitations_section_exists(self) -> None:
        assert "## Known limitations / follow-ups" in _read_doc()

    def test_known_limitations_names_shipped_verb_and_task(self) -> None:
        section = _extract_section(_read_doc(), "## Known limitations / follow-ups")
        assert section, "'## Known limitations / follow-ups' section must exist"
        assert "log-newly-reachable" in section, (
            "'## Known limitations / follow-ups' must name log-newly-reachable as the shipped mechanism."
        )
        assert "E2-F4-S1-T2" in section, (
            "'## Known limitations / follow-ups' must attribute the shipped marker to E2-F4-S1-T2."
        )

    def test_known_limitations_does_not_frame_marker_as_hypothetical(self) -> None:
        section = _extract_section(_read_doc(), "## Known limitations / follow-ups")
        assert section, "'## Known limitations / follow-ups' section must exist"
        assert "delivered" in section.lower(), (
            "'## Known limitations / follow-ups' must state the structured marker is delivered."
        )
        # The old phrasing framed the *marker itself* as a hypothetical future follow-up. That
        # phrase must no longer describe the marker's existence (a residual hypothetical about
        # the *evidence* field, a narrower and still-genuinely-open gap, is fine).
        assert "could require a structured" not in section, (
            "'## Known limitations / follow-ups' must not still frame the "
            "{path, method, result} marker itself as a hypothetical future follow-up."
        )


@pytest.mark.unit
class TestNoEmDash:
    """AC-FIX-006(d) / AC-FIX-007: no em-dash (U+2014) anywhere in the doc."""

    def test_no_em_dash_present(self) -> None:
        text = _read_doc()
        assert "—" not in text, "docs/newly-reachable-paths.md must not contain an em-dash (U+2014)."
