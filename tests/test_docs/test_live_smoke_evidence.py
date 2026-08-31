"""Structural and machine-verified pins for
``docs/release-notes/live-smoke-evidence.md`` (AC-TEST-001 through
AC-TEST-004; E12-F1-S2-T1).

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
   the checklist's runnable command blocks is resolved against the real
   CLI dispatch table (``devbench.cli._COMMANDS``) rather than a
   hand-maintained list this module would have to keep in sync by hand.

``TestStubMutationControls`` demonstrates, permanently, that the
structural extraction helpers actually fail against a gutted copy of the
document (the same three-line ``TODO.`` stub that slipped through the
prior state) -- this is the RED evidence AC-TEST-001 requires, kept alive
as a regression control rather than only a one-time TDD-log demonstration.
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
_BASH_FENCE_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_CLI_VERB_RE = re.compile(r"\bdevbench ([a-z][a-z0-9-]*[a-z0-9])")
_NUMBERED_STEP_RE = re.compile(r"^\d+\.\s+\*\*", re.MULTILINE)
_EVIDENCE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|(.*)$", re.MULTILINE)

_FIXTURE_HOLD_TASK_ID = "E12-F1-S1-T2"

# The three-line stub that actually slipped through review with the full
# suite green (see module docstring). Reused verbatim by
# TestStubMutationControls so the mutation control reproduces the exact
# historical gap.
_GUTTED_STUB_DOC = "# Live Smoke Evidence: Integration-Reality Gates\n\nTODO.\n"


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


def _checklist_cli_verbs(checklist_section: str) -> set[str]:
    """Every ``devbench <verb>`` token found in the checklist's runnable
    ```bash command blocks (not prose mentions elsewhere in the document,
    and not the ``check-<name-with-hyphens>`` placeholder in step 3's
    prose, which lives outside any ```bash block).
    """
    verbs: set[str] = set()
    for block in _BASH_FENCE_RE.findall(checklist_section):
        verbs.update(_CLI_VERB_RE.findall(block))
    return verbs


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


def _discovered_checklist_verbs() -> list[str]:
    """Every ``devbench <verb>`` token discovered in the real checklist's
    runnable command blocks, module-scoped so the parametrize list below
    is built once from the real document rather than re-derived per test."""
    text = _read_text(LIVE_SMOKE_DOC)
    checklist_section = _extract_checklist_section(text)
    return sorted(_checklist_cli_verbs(checklist_section))


_DISCOVERED_CHECKLIST_VERBS: list[str] = _discovered_checklist_verbs()


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
        verbs = _checklist_cli_verbs(checklist_section)
        assert "report" in verbs, (
            "the checklist must include at least one runnable 'uv run devbench report' step "
            f"(steps 7 and 9 capture the baseline/final report evidence); discovered verbs: {sorted(verbs)}"
        )

    def test_finalize_step_present(self) -> None:
        text = _read_text(LIVE_SMOKE_DOC)
        checklist_section = _extract_checklist_section(text)
        verbs = _checklist_cli_verbs(checklist_section)
        assert "git-ops-finalize" in verbs, (
            "the checklist must include a runnable 'uv run devbench git-ops-finalize' step "
            f"(step 12 captures the finalize PR-body evidence); discovered verbs: {sorted(verbs)}"
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

    def test_discovers_at_least_five_checklist_cli_verbs(self) -> None:
        """Guards against the verb-discovery regex silently collecting zero
        (or too few) cases, which would make the parametrized resolution
        test below pass vacuously."""
        text = _read_text(LIVE_SMOKE_DOC)
        checklist_section = _extract_checklist_section(text)
        verbs = _checklist_cli_verbs(checklist_section)
        assert len(verbs) >= 5, f"expected >=5 distinct CLI verbs discovered in the checklist, found: {sorted(verbs)}"

    @pytest.mark.parametrize("verb", _DISCOVERED_CHECKLIST_VERBS)
    def test_checklist_cli_verb_exists_in_dispatch_table(self, verb: str) -> None:
        assert verb in _COMMANDS, (
            f"docs/release-notes/live-smoke-evidence.md's checklist names 'devbench {verb}' "
            f"in a runnable command block, but '{verb}' is not registered in "
            "devbench.cli._COMMANDS. Available commands: "
            f"{sorted(_COMMANDS)}"
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
