"""Structural and machine-verified pins for
``docs/release-notes/live-smoke-evidence.md`` (AC-TEST-001 through
AC-TEST-004; E12-F1-S2-T1, widened by E12-F1-S2-T2).

This closes a demonstrated coverage gap, not a theorised one:
``docs/release-notes/live-smoke-evidence.md`` is a 12-step operator
checklist with a 12-row evidence table, and before this module the only
assertion touching the file was that its path resolves
(``test_config_examples_load.py``'s doc-discovery walk does not even
qualify it -- the fenced ``gates:`` fragment is a partial snippet, not a
complete ``devbench.yaml``). Replacing the whole document with a
three-line ``TODO.`` stub left the full test suite green. Across review,
doc_review and code_review each independently found several substantive
defects in the document that never failed a test: a config fragment
placed where the schema rejects it at load time, a step whose expected
refusal was preempted by an earlier invariant, a step whose expected exit
code was unreachable, a step describing command output that did not
exist, and a commit step that could not have produced a commit.

Two verification strategies are used here, deliberately:

1. Structural pins (``TestStructuralInvariants``) read the real document
   and assert presence/shape invariants -- the traditional doc-pin
   pattern used throughout ``tests/test_docs/``.
2. Machine verification (``TestMachineVerification``) is the check that
   would actually have caught the round-one defects: the fenced
   ``gates:`` fragment is loaded through the REAL config loader
   (``devbench.config_loader.load_runtime_config``) and asserted to
   validate against the shipped JSON Schema, and every CLI verb named in
   an ``uv run devbench <verb>`` invocation ANYWHERE in the document
   (E12-F1-S2-T2 widened this from "inside fenced ```bash blocks only" to
   the whole document, keyed on the literal invocation prefix) is
   resolved against the real CLI dispatch table
   (``devbench.cli._COMMANDS``) rather than a hand-maintained list this
   module would have to keep in sync by hand.

``TestStubMutationControls`` demonstrates, permanently, that the
structural extraction helpers actually fail against a gutted copy of the
document (the same three-line ``TODO.`` stub that slipped through the
prior state) -- this is the RED evidence AC-TEST-001 requires, kept alive
as a regression control rather than only a one-time TDD-log demonstration.

``TestCliVerbExtractionWidening`` (E12-F1-S2-T2) pins the widened
extraction's four load-bearing guarantees: it still excludes the
``check-<name-with-hyphens>`` placeholder (which collapses to the real
verb ``check`` under a naive widening -- a trap, not a false positive to
shrug off) and the ``devbench process's`` prose mention; it now discovers
``unhold`` and ``validate-backlog``, both previously unpinned; and it
discovers the checklist's hard-wrapped ``set-status`` invocation, the
audit-sensitive verb the operator-gating section explicitly warns against
misusing, which was unreachable by the extraction at any prior scope
width because the verb fell on the line after ``uv run devbench``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from devbench.cli import _COMMANDS
from devbench.config_loader import RuntimeConfig, load_runtime_config

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_SMOKE_DOC = REPO_ROOT / "docs" / "release-notes" / "live-smoke-evidence.md"

_GATE_ENABLEMENT_HEADING = "## Gate enablement"
_CHECKLIST_HEADING = "## The checklist"
_EVIDENCE_HEADING = "## Evidence table"
_CROSS_REFERENCES_HEADING = "## Cross-references"
_OPERATOR_GATED_HEADING = "## Operator-gated: automation must not touch this run"
_PRECONDITIONS_HEADING = "## Preconditions"

_YAML_FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)

# Keys on the literal ``uv run devbench`` invocation prefix (not a bare
# ``devbench <word>`` scan), so prose mentions of the word "devbench" that
# are not actually invocations -- e.g. "the devbench process's current
# working directory" -- never match: no "uv run" immediately precedes
# "process's". The whitespace between the prefix and the verb is bounded
# to AT MOST one line break: either same-line spaces/tabs
# (``[ \t]+``), or an optional line break with optional leading/trailing
# indentation (``[ \t]*\n[ \t]*``). This is what makes the match hard-wrap
# tolerant -- an invocation split across exactly two lines (the
# checklist's `` `uv run devbench\n  set-status ...` `` mention) is still
# discovered -- without also bridging a full blank line: text that ends a
# paragraph on the words "uv run devbench" followed by a blank line and
# then unrelated prose does NOT capture the next paragraph's first word,
# because the pattern only ever consumes one literal ``\n``.
# The captured group is intentionally permissive (``[a-z0-9-]+``, allowed
# to end in a hyphen) so ``_is_well_formed_verb_token`` below can detect,
# and reject, a token truncated mid-word by a non-verb character. One
# known, accepted consequence of that permissiveness: a verb hyphenated
# across a line break (e.g. ``set-\nstatus``) is indistinguishable from a
# truncated fragment and is silently dropped rather than discovered. This
# does not occur anywhere in the shipped document today, and dropping a
# verb is the fail-closed direction relative to the false positive this
# guard exists to prevent, so it is accepted rather than special-cased.
_CLI_INVOCATION_RE = re.compile(r"uv run devbench(?:[ \t]+|[ \t]*\n[ \t]*)([a-z0-9-]+)")
_NUMBERED_STEP_RE = re.compile(r"^\d+\.\s+\*\*", re.MULTILINE)
_EVIDENCE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|(.*)$", re.MULTILINE)

_FIXTURE_HOLD_TASK_ID = "E12-F1-S1-T2"

# The three-line stub that actually slipped through review with the full
# suite green (see module docstring). Reused verbatim by
# TestStubMutationControls so the mutation control reproduces the exact
# historical gap.
_GUTTED_STUB_DOC = "# Live Smoke Evidence: Integration-Reality Gates\n\nTODO.\n"

# Anchors the widened extraction's regression guard to explicit document
# facts instead of a hand-tuned magic integer (see
# test_discovered_document_cli_verbs_include_required_set). Each member is
# individually justified: 'report' and 'git-ops-finalize' were already
# reachable at E12-F1-S2-T1's fenced-block-only scope and are kept here as
# a non-regression anchor; 'unhold' and 'validate-backlog' are the two
# previously-unpinned prose mentions E12-F1-S2-T2's widening added
# coverage for; 'set-status' is the hard-wrapped, audit-sensitive verb
# AC-TEST-002 requires -- the one verb this document most needs pinned,
# since the operator-gating section explicitly warns against misusing it.
_REQUIRED_DOCUMENT_CLI_VERBS = frozenset({"report", "git-ops-finalize", "unhold", "validate-backlog", "set-status"})


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str, next_heading: str) -> str:
    """Return the slice of *text* from *heading* up to (excluding) *next_heading*.

    Raises:
        AssertionError: when either heading is absent, naming which one.
    """
    start = text.find(heading)
    if start == -1:
        raise AssertionError(f"expected heading {heading!r} not found in document")
    end = text.find(next_heading, start)
    if end == -1:
        raise AssertionError(f"expected heading {next_heading!r} not found after {heading!r}")
    return text[start:end]


def _extract_gates_yaml_fragment(text: str) -> str:
    """Return the raw YAML body of the fenced ```yaml block under
    ``## Gate enablement``.

    Raises:
        AssertionError: when the section or the fenced block is absent.
    """
    section = _extract_section(text, _GATE_ENABLEMENT_HEADING, _CHECKLIST_HEADING)
    match = _YAML_FENCE_RE.search(section)
    if match is None:
        raise AssertionError(f"no fenced ```yaml block found under {_GATE_ENABLEMENT_HEADING!r}")
    return match.group(1)


def _extract_checklist_section(text: str) -> str:
    return _extract_section(text, _CHECKLIST_HEADING, _EVIDENCE_HEADING)


def _extract_evidence_section(text: str) -> str:
    return _extract_section(text, _EVIDENCE_HEADING, _CROSS_REFERENCES_HEADING)


def _extract_operator_gated_section(text: str) -> str:
    return _extract_section(text, _OPERATOR_GATED_HEADING, _PRECONDITIONS_HEADING)


def _numbered_step_count(checklist_section: str) -> int:
    """Count of top-level numbered checklist steps (``1. **...**`` style)."""
    return len(_NUMBERED_STEP_RE.findall(checklist_section))


def _evidence_data_rows(evidence_section: str) -> list[str]:
    """Every numbered data row of the evidence table (header/separator excluded)."""
    return [m.group(0) for m in _EVIDENCE_ROW_RE.finditer(evidence_section)]


def _operator_evidence_cells(evidence_section: str) -> list[str]:
    """The last (Operator evidence) column's content for every data row."""
    cells: list[str] = []
    for row in _evidence_data_rows(evidence_section):
        parts = row.strip().strip("|").split("|")
        cells.append(parts[-1].strip())
    return cells


def _is_well_formed_verb_token(token: str) -> bool:
    """A raw ``_CLI_INVOCATION_RE`` capture is only a genuine CLI verb if it
    starts with a lowercase letter AND ends in an alphanumeric character --
    the same shape the superseded ``_CLI_VERB_RE`` (``[a-z][a-z0-9-]*[a-z0-9]``)
    required, restored here as an explicit two-sided check rather than
    folded back into the capture regex.

    The trailing check is the load-bearing one for the document as
    shipped: a capture ending in a hyphen means the greedy ``[a-z0-9-]+``
    character class ran into a non-verb character (e.g. the literal ``<``
    that opens the ``check-<name-with-hyphens>`` placeholder in step 3's
    prose) partway through the token, leaving a truncated fragment
    (``check-``) behind. Discarding that fragment outright -- rather than
    trimming its trailing hyphen and keeping the remainder -- is the
    load-bearing choice: trimming would silently resolve the placeholder
    to ``check``, which IS a real registered verb, turning an inert
    placeholder into a false positive.

    The leading check has no live trigger in the document today, but is
    not decorative: without it this helper returns ``True`` for captures
    such as ``--help``, ``--version``, ``-x`` or ``2`` (``_CLI_INVOCATION_RE``'s
    capture group permits digits and hyphens in the first position), none
    of which is a well-formed CLI verb name.
    """
    return bool(token) and token[0].isalpha() and token[-1] != "-"


def _document_cli_verbs(text: str) -> set[str]:
    """Every ``uv run devbench <verb>`` invocation discovered anywhere in
    *text*, keyed on the literal ``uv run devbench`` invocation prefix
    rather than on fenced ```bash blocks (E12-F1-S2-T2 widening).

    This is intentionally NOT scoped to runnable command blocks: the
    checklist's hard-wrapped ``set-status`` mention and its ``unhold`` /
    ``validate-backlog`` mentions all live in prose, outside any ```bash
    fence, and each is a genuine ``uv run devbench <verb>`` invocation the
    operator is meant to notice. The prefix anchor alone already excludes
    the one false positive fenced-block-scoping used to dodge (``the
    devbench process's current working directory`` is not preceded by "uv
    run"), and ``_is_well_formed_verb_token`` excludes the other (the
    ``check-<name-with-hyphens>`` placeholder).
    """
    verbs: set[str] = set()
    for match in _CLI_INVOCATION_RE.finditer(text):
        token = match.group(1)
        if _is_well_formed_verb_token(token):
            verbs.add(token)
    return verbs


def _assert_verb_registered_in_dispatch_table(verb: str, context: str) -> None:
    """Assert *verb* is a real, registered CLI command name.

    Resolves against the REAL CLI dispatch table
    (``devbench.cli._COMMANDS``) rather than a hand-maintained list, so
    this check cannot drift from what ``uv run devbench <verb>`` would
    actually invoke. Shared between the parametrized resolution test
    (real, document-discovered verbs) and
    ``TestCliVerbExtractionWidening``'s mutation control (an injected
    bogus verb), so both exercise the identical assertion path.

    *context* is a caller-supplied string naming where *verb* came from
    (e.g. the document path and how it was discovered, or "synthetic
    mutation-control fixture"), threaded into the failure message so a
    future failure names the source to fix rather than only the verb.

    Raises:
        AssertionError: when *verb* is not a key in ``_COMMANDS``.
    """
    assert verb in _COMMANDS, (
        f"'{verb}' ({context}) is not registered in devbench.cli._COMMANDS. Available commands: {sorted(_COMMANDS)}"
    )


def _gates_fragment_mapping(yaml_fragment: str) -> dict:
    """Parse the fenced fragment's YAML text into a plain mapping.

    Raises:
        AssertionError: when the fragment does not parse to a mapping with
            a top-level ``gates`` key.
    """
    parsed = yaml.safe_load(yaml_fragment)
    if not isinstance(parsed, dict) or "gates" not in parsed:
        raise AssertionError(f"gates fragment did not parse to a mapping with a top-level 'gates' key: {parsed!r}")
    return parsed


def _discovered_document_verbs() -> list[str]:
    """Every ``uv run devbench <verb>`` invocation discovered anywhere in
    the real document (not scoped to the checklist section or to fenced
    ```bash blocks -- E12-F1-S2-T2 widening), module-scoped so the
    parametrize list below is built once from the real document rather
    than re-derived per test."""
    text = _read_text(LIVE_SMOKE_DOC)
    return sorted(_document_cli_verbs(text))


_DISCOVERED_DOCUMENT_VERBS: list[str] = _discovered_document_verbs()


def _load_fragment_through_real_config_loader(yaml_fragment: str, tmp_path: Path) -> RuntimeConfig:
    """Merge *yaml_fragment* into a minimal, otherwise-valid ``devbench.yaml``
    (one throwaway ``repos:`` entry -- the schema's only other required
    field) and round-trip it through the real ``load_runtime_config``, so
    the fragment's CONTENT is machine-verified against the shipped JSON
    Schema: an unknown gate key or a non-boolean ``enabled`` value each
    raise the real loader's ``ValueError``. This helper always hoists the
    parsed ``gates`` mapping to the top level of the synthetic config
    before calling the loader, so it does not exercise fragment
    PLACEMENT -- that guarantee comes separately from
    ``_gates_fragment_mapping``'s own assertion that the parsed fragment
    has a top-level ``gates`` key.
    """
    gates_mapping = _gates_fragment_mapping(yaml_fragment)
    full_config: dict = {"repos": {"example-org/example-repo": {}}}
    full_config.update(gates_mapping)
    config_path = tmp_path / "devbench.yaml"
    config_path.write_text(yaml.safe_dump(full_config), encoding="utf-8")
    return load_runtime_config(config_path, {})


@pytest.mark.unit
class TestDocumentExists:
    """Pre-condition: the live-smoke checklist document must exist."""

    def test_live_smoke_doc_exists(self) -> None:
        assert LIVE_SMOKE_DOC.is_file(), (
            "docs/release-notes/live-smoke-evidence.md must exist -- it is the operator's "
            "live-smoke checklist (spec integration-reality-gates-hardening.md Section 10)."
        )


@pytest.mark.unit
class TestStructuralInvariants:
    """AC-TEST-001: structural invariants of the checklist document.

    Gutting the document (replacing it with a short stub) must fail this
    suite; see ``TestStubMutationControls`` for the permanent mutation
    control proving that.
    """

    def test_both_gate_names_present_in_gates_fragment(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        fragment = _extract_gates_yaml_fragment(text)
        for gate_name in ("reachability", "shared_file_impact"):
            assert gate_name in fragment, (
                f"the fenced gates: fragment under {_GATE_ENABLEMENT_HEADING!r} must name "
                f"the '{gate_name}' gate (spec Section 10 enables exactly reachability and "
                f"shared_file_impact). Fragment was:\n{fragment}"
            )

    def test_numbered_step_count_equals_evidence_table_row_count(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        checklist_section = _extract_checklist_section(text)
        evidence_section = _extract_evidence_section(text)
        step_count = _numbered_step_count(checklist_section)
        row_count = len(_evidence_data_rows(evidence_section))
        assert step_count > 0, "no numbered checklist steps discovered under '## The checklist'"
        assert row_count > 0, "no numbered evidence-table data rows discovered under '## Evidence table'"
        assert step_count == row_count, (
            f"checklist declares {step_count} numbered steps but the evidence table carries "
            f"{row_count} data rows -- every checklist step must have exactly one evidence row."
        )

    def test_every_operator_evidence_cell_is_empty(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        evidence_section = _extract_evidence_section(text)
        cells = _operator_evidence_cells(evidence_section)
        assert cells, "no evidence-table data rows discovered -- extraction regression"
        non_empty = [(i + 1, cell) for i, cell in enumerate(cells) if cell]
        assert not non_empty, (
            "docs/release-notes/live-smoke-evidence.md ships as a template with every "
            "'Operator evidence' cell empty; the companion task E12-F1-S1-T2 (hold) is the "
            f"only path that fills them in. Non-empty rows found: {non_empty}"
        )

    def test_operator_gating_statement_names_hold_task(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        section = _extract_operator_gated_section(text)
        assert _FIXTURE_HOLD_TASK_ID in section, (
            f"the '{_OPERATOR_GATED_HEADING}' section must name {_FIXTURE_HOLD_TASK_ID!r} as "
            "the task that fills in the evidence column."
        )
        assert "## Status: hold" in section, (
            f"the '{_OPERATOR_GATED_HEADING}' section must state that "
            f"{_FIXTURE_HOLD_TASK_ID} is released with '## Status: hold'."
        )
        gating_phrase_re = re.compile(
            re.escape(_FIXTURE_HOLD_TASK_ID) + r"`,\s+is released with `## Status: hold`",
        )
        assert gating_phrase_re.search(section), (
            f"expected the exact gating phrase naming {_FIXTURE_HOLD_TASK_ID} as released "
            f"with '## Status: hold' in the '{_OPERATOR_GATED_HEADING}' section; found only "
            "loose mentions of the two facts separately."
        )

    def test_report_step_present(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        checklist_section = _extract_checklist_section(text)
        verbs = _document_cli_verbs(checklist_section)
        assert "report" in verbs, (
            "the checklist section must include at least one 'uv run devbench report' "
            "invocation, fenced or prose (steps 7 and 9 capture the baseline/final report "
            f"evidence); discovered verbs: {sorted(verbs)}"
        )

    def test_finalize_step_present(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        checklist_section = _extract_checklist_section(text)
        verbs = _document_cli_verbs(checklist_section)
        assert "git-ops-finalize" in verbs, (
            "the checklist section must include a 'uv run devbench git-ops-finalize' "
            "invocation, fenced or prose (step 12 captures the finalize PR-body evidence); "
            f"discovered verbs: {sorted(verbs)}"
        )


@pytest.mark.unit
class TestMachineVerification:
    """AC-TEST-002: machine verification rather than prose assertion.

    This is the check that would have caught the round-one defects that
    passed through a green suite: schema validation via the real config
    loader, and CLI-verb resolution via the real dispatch table.
    """

    def test_gates_fragment_validates_against_shipped_schema(self, tmp_path: Path) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        fragment = _extract_gates_yaml_fragment(text)
        try:
            _load_fragment_through_real_config_loader(fragment, tmp_path)
        except ValueError as exc:
            pytest.fail(
                "the fenced gates: fragment under '## Gate enablement' failed to validate "
                f"against the shipped config schema through the real config loader: {exc}"
            )

    def test_gates_fragment_resolves_expected_gate_keys(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        fragment = _extract_gates_yaml_fragment(text)
        mapping = _gates_fragment_mapping(fragment)
        assert set(mapping["gates"].keys()) == {"reachability", "shared_file_impact"}, (
            "the gates: fragment must declare exactly the two gates the spec Section 10 "
            f"live-smoke run enables; parsed keys were {sorted(mapping['gates'].keys())}"
        )

    def test_loaded_config_resolves_both_gates_enabled(self, tmp_path: Path) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        fragment = _extract_gates_yaml_fragment(text)
        runtime_config = _load_fragment_through_real_config_loader(fragment, tmp_path)
        assert runtime_config.gates.reachability.enabled is True, (
            "the loaded RuntimeConfig must resolve gates.reachability.enabled=True from the documented fragment"
        )
        assert runtime_config.gates.shared_file_impact.enabled is True, (
            "the loaded RuntimeConfig must resolve gates.shared_file_impact.enabled=True from the documented fragment"
        )
        assert runtime_config.gates.shared_file_impact.auto_derive_registry is True, (
            "the loaded RuntimeConfig must resolve "
            "gates.shared_file_impact.auto_derive_registry=True from the documented fragment"
        )

    def test_discovers_at_least_ten_document_cli_verbs(self) -> None:
        """Guards against the verb-discovery regex silently collecting zero
        (or too few) cases, which would make the parametrized resolution
        test below pass vacuously. This floor value is honest about what
        it does and does not detect: E12-F1-S2-T1's fenced-block-only
        scope found exactly 9 distinct verbs, so a floor of 10 -- one
        above that count -- DOES bite if the extraction ever regresses to
        that narrower scope, independently of which specific verbs are
        lost, unlike the prior floor of 9 which would have passed
        unchanged under that exact regression. This is a supplementary,
        coarse-grained guard; the precise, name-anchored regression check
        is test_discovered_document_cli_verbs_include_required_set below,
        which does not depend on picking the right integer at all."""
        text = _read_text(LIVE_SMOKE_DOC)
        verbs = _document_cli_verbs(text)
        assert len(verbs) >= 10, f"expected >=10 distinct CLI verbs discovered in the document, found: {sorted(verbs)}"

    def test_discovered_document_cli_verbs_include_required_set(self) -> None:
        """AC-TEST-003: the discovered verb set can never silently collapse
        back to a scope that drops a document-critical verb, verified by
        asserting a superset of an explicitly named, individually
        justified required-verb set (see ``_REQUIRED_DOCUMENT_CLI_VERBS``)
        rather than by a bare cardinality floor. A floor set at any single
        integer only approximates the real invariant and must be
        hand-re-tuned on every document edit; this assertion is anchored
        to document facts instead, so it is self-documenting about why
        each verb matters and bites on exactly the collapse this
        acceptance criterion cares about. Falsifiable: reverting
        ``_document_cli_verbs`` to E12-F1-S2-T1's fenced-block-only scope
        drops 'unhold', 'validate-backlog' and the hard-wrapped
        'set-status' (none of the three lives inside a fenced ```bash
        block), so this assertion goes RED under that exact regression --
        unlike the old floor of 9, which passed under it."""
        text = _read_text(LIVE_SMOKE_DOC)
        verbs = _document_cli_verbs(text)
        missing = sorted(_REQUIRED_DOCUMENT_CLI_VERBS - verbs)
        assert not missing, (
            f"expected the discovered verb set to be a superset of "
            f"{sorted(_REQUIRED_DOCUMENT_CLI_VERBS)}; missing: {missing}. Discovered: {sorted(verbs)}"
        )

    @pytest.mark.parametrize("verb", _DISCOVERED_DOCUMENT_VERBS)
    def test_checklist_cli_verb_exists_in_dispatch_table(self, verb: str) -> None:
        _assert_verb_registered_in_dispatch_table(
            verb, context=f"discovered via 'uv run devbench {verb}' in {LIVE_SMOKE_DOC}"
        )


@pytest.mark.unit
class TestStubMutationControls:
    """Permanent RED-evidence controls (AC-TEST-001): the same three-line
    ``TODO.`` stub that previously slipped through review with a green
    suite must fail every structural extraction helper this module relies
    on. This is the demonstrated failure the TDD Cycle Log cites, kept
    alive as a regression control rather than only a one-time manual step.
    """

    def test_gates_fragment_extraction_fails_on_gutted_doc(self) -> None:
        with pytest.raises(AssertionError, match=re.escape(_GATE_ENABLEMENT_HEADING)):
            _extract_gates_yaml_fragment(_GUTTED_STUB_DOC)

    def test_checklist_section_extraction_fails_on_gutted_doc(self) -> None:
        with pytest.raises(AssertionError, match=re.escape(_CHECKLIST_HEADING)):
            _extract_checklist_section(_GUTTED_STUB_DOC)

    def test_evidence_section_extraction_fails_on_gutted_doc(self) -> None:
        with pytest.raises(AssertionError, match=re.escape(_EVIDENCE_HEADING)):
            _extract_evidence_section(_GUTTED_STUB_DOC)

    def test_operator_gated_section_extraction_fails_on_gutted_doc(self) -> None:
        with pytest.raises(AssertionError, match=re.escape(_OPERATOR_GATED_HEADING)):
            _extract_operator_gated_section(_GUTTED_STUB_DOC)

    def test_step_count_mismatch_detected_on_partially_gutted_doc(self) -> None:
        """A doc that keeps both required headings but drops every numbered
        step (e.g. a stub that preserves structure but truncates content)
        must still be caught: the step-count-vs-row-count equality is the
        real invariant, not merely 'the headings exist'."""
        partially_gutted = (
            "# Live Smoke Evidence: Integration-Reality Gates\n\n"
            f"{_CHECKLIST_HEADING}\n\nTODO.\n\n"
            f"{_EVIDENCE_HEADING}\n\n"
            "| Step | Command | Expected observation | Operator evidence |\n"
            "|------|---------|----------------------|--------------------|\n"
            "| 1 | `uv run devbench gates` | placeholder |  |\n\n"
            f"{_CROSS_REFERENCES_HEADING}\n"
        )
        checklist_section = _extract_checklist_section(partially_gutted)
        evidence_section = _extract_evidence_section(partially_gutted)
        step_count = _numbered_step_count(checklist_section)
        row_count = len(_evidence_data_rows(evidence_section))
        assert step_count == 0
        assert row_count == 1
        assert step_count != row_count, (
            "expected the partially-gutted fixture's step count and evidence-row count to "
            "disagree, proving the equality assertion in TestStructuralInvariants is "
            "falsifiable"
        )


@pytest.mark.unit
class TestCliVerbExtractionWidening:
    """E12-F1-S2-T2 (AC-TEST-001 through AC-TEST-004): the widened
    ``uv run devbench <verb>`` extraction's four load-bearing guarantees,
    each pinned against the REAL document except the mechanism/mutation
    controls, which use synthetic fixtures the same way
    ``TestStubMutationControls`` does above.
    """

    def test_check_placeholder_does_not_collapse_to_registered_verb(self) -> None:
        """AC-TEST-001: the ``check-<name-with-hyphens>`` placeholder in
        step 3's prose must NOT resolve to the real registered verb
        ``check`` -- the trap a naive widening (trim the trailing hyphen
        instead of discarding the truncated token) would fall into."""
        assert "check" in _COMMANDS, (
            "test fixture invariant violated: 'check' must be a real registered CLI command "
            "for this to be a meaningful trap test"
        )
        text = _read_text(LIVE_SMOKE_DOC)
        verbs = _document_cli_verbs(text)
        assert "check" not in verbs, (
            "the check-<name-with-hyphens> placeholder in step 3's prose must not collapse "
            f"to the real verb 'check'; discovered verbs: {sorted(verbs)}"
        )

    def test_devbench_process_prose_does_not_fire(self) -> None:
        """AC-TEST-001: 'the devbench process's current working directory'
        (step 12's prose, no 'uv run' prefix) must never be mistaken for a
        'uv run devbench process' invocation."""
        text = _read_text(LIVE_SMOKE_DOC)
        assert "devbench process's" in text, (
            "test fixture invariant violated: expected prose mention 'devbench process's' "
            "not found in the live document -- update this test if step 12's wording changed"
        )
        verbs = _document_cli_verbs(text)
        assert "process" not in verbs, f"discovered verbs must not include 'process': {sorted(verbs)}"

    def test_widened_pin_discovers_unhold_and_validate_backlog(self) -> None:
        """AC-TEST-001: widening the extraction to the whole document (not
        just fenced ```bash blocks) must newly discover 'unhold' (the
        operator-gated section's release path) and 'validate-backlog'
        (precondition 3), both previously unpinned."""
        text = _read_text(LIVE_SMOKE_DOC)
        verbs = _document_cli_verbs(text)
        for verb in ("unhold", "validate-backlog"):
            assert verb in verbs, f"expected {verb!r} among the widened discovered verbs: {sorted(verbs)}"

    def test_hard_wrapped_set_status_invocation_is_discovered(self) -> None:
        """AC-TEST-002: the checklist hard-wraps its 'set-status' mention
        (`` `uv run devbench`` ends one line, ``  set-status ...` `` starts
        the next). 'set-status' is the audit-sensitive verb the
        operator-gating section explicitly warns against misusing, so it
        is the verb this document most needs pinned; before this widening
        it was unreachable by the extraction at any scope width."""
        text = _read_text(LIVE_SMOKE_DOC)
        assert "uv run devbench\n" in text, (
            "test fixture invariant violated: expected the live document to still hard-wrap "
            "an 'uv run devbench' invocation across a line break -- update this test if the "
            "document's wrapping changed"
        )
        verbs = _document_cli_verbs(text)
        assert "set-status" in verbs, f"expected 'set-status' among the discovered verbs: {sorted(verbs)}"

    def test_extraction_mechanism_tolerates_hard_wrapped_invocation(self) -> None:
        """AC-TEST-002 mechanism control: pins the hard-wrap tolerance
        directly against a fixture shaped like the real document's wrap
        (an invocation prefix ending a line, its verb starting the next),
        independent of the real document's own wrapping. Complements
        ``test_hard_wrapped_set_status_invocation_is_discovered`` above,
        which pins the same guarantee against real content."""
        hard_wrapped_fixture = (
            "A second command, `uv run devbench\n  totally-fake-hardwrap-verb E12-F1-S1-T2 in-queue`, exists.\n"
        )
        verbs = _document_cli_verbs(hard_wrapped_fixture)
        assert "totally-fake-hardwrap-verb" in verbs, (
            f"hard-wrapped invocation was not discovered; extraction found: {sorted(verbs)}"
        )

    def test_dispatch_table_resolution_fails_on_injected_bogus_verb(self) -> None:
        """AC-TEST-001/AC-TEST-004 mutation control: proves the
        dispatch-table resolution check the parametrized
        ``test_checklist_cli_verb_exists_in_dispatch_table`` test performs
        is not vacuous, by showing the SAME shared helper actually raises
        for an injected verb that is not a real CLI command. Mirrors
        ``TestStubMutationControls``'s pattern of proving a pin can fail,
        not merely that it can pass."""
        bogus_verb = "not-a-real-devbench-verb"
        assert bogus_verb not in _COMMANDS, (
            f"test fixture invariant violated: {bogus_verb!r} unexpectedly collides with a "
            "real registered CLI command; choose a different bogus token"
        )
        with pytest.raises(AssertionError, match=re.escape(bogus_verb)):
            _assert_verb_registered_in_dispatch_table(
                bogus_verb, context="synthetic mutation-control fixture, not a real document reference"
            )
