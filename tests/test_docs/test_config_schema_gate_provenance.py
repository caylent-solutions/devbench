"""Pin `src/devbench/config-schema.json` gate descriptions against
`docs/issue-provenance.md` (spec `integration-reality-gates-hardening.md`
section 4.12; work unit E2-F7-S1-T5).

`src/devbench/config-schema.json` cites, in each `gates.properties.<gate>.description`,
the `caylent-solutions/devbench-internal-backlog` issue that gate came from.
`docs/issue-provenance.md` (E2-F7-S1-T2) is the authoritative map of the same
relationship, and E11's closure work units read that map verbatim. Nothing
previously held the two in agreement, which is how the swapped
`newly_reachable_paths` / `layout_geometry` citations E2-F7-S1-T4 corrects
survived unnoticed through eight source pull requests.

`test_schema_descriptions_match_provenance_map_for_every_gate` is the
regression guard: it parses both sides for every gate in
`devbench.constants.GATE_NAMES` and asserts they agree (AC-FUNC-001). It is
green from the moment this module is added because E2-F7-S1-T4 already
corrected the schema side.

The map side is parsed with `parse_provenance_map`, imported from
`tests/test_docs/test_issue_provenance.py` rather than re-implemented here
(AC-FUNC-002), so the five-column table shape has exactly one authority.
`tests/test_integration/test_make_targets.py` already establishes the
precedent of importing one `tests/` module from another via the `tests`
`pythonpath` entry (`pyproject.toml`'s `[tool.pytest.ini_options]`); this
module follows that same convention.

Because the real files agree by construction, the module also carries seeded-
mutation controls (AC-FUNC-003) and seeded-omission controls (AC-FUNC-004)
that prove the shared assertion (`assert_schema_matches_map`) is falsifiable
rather than vacuous: they build synthetic schema/map fixtures under
`tmp_path`, mutate or drop one gate on one side, and assert the shared
assertion raises naming the gate and (for the mutation controls) both cited
issue numbers. The real production files are never mutated by this module.

Source: E2-F7-S1-T5. Depends on E2-F7-S1-T4 (schema correction) and
E2-F7-S1-T2 (`docs/issue-provenance.md`). AC-FUNC-001 through -004.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_docs.test_issue_provenance import ProvenanceRow, parse_provenance_map

from devbench.constants import GATE_NAMES

REPO_ROOT = Path(__file__).parent.parent.parent

CONFIG_SCHEMA_PATH = REPO_ROOT / "src" / "devbench" / "config-schema.json"
PROVENANCE_MAP_PATH = REPO_ROOT / "docs" / "issue-provenance.md"

# The qualified internal-backlog citation form config-schema.json's gate
# descriptions carry, e.g. "caylent-solutions/devbench-internal-backlog#10".
# Mirrors `test_issue_provenance._QUALIFIED_ISSUE_RE`'s qualified branch, but
# is not imported from it: that module's regex also matches the unqualified
# `devbench-internal-backlog#<n>` form used for prose citations, while this
# module only ever needs to extract the one canonical citation a schema
# description carries, so a locally scoped constant is clearer than importing
# a private regex tuned for a different surface.
_SCHEMA_ISSUE_RE = re.compile(r"caylent-solutions/devbench-internal-backlog#(\d+)")

# Synthetic per-gate internal-backlog issue numbers used only by the seeded
# controls below (AC-FUNC-003, AC-FUNC-004). Deliberately outside the real
# #10-#17 range so a synthetic fixture can never be mistaken for production
# data and this module's controls never depend on the real map's numbering.
_SYNTHETIC_GATE_ISSUE_NUMBERS: dict[str, int] = {gate: 900 + index for index, gate in enumerate(GATE_NAMES)}


def extract_gate_issue_citations(schema: dict) -> dict[str, str]:
    """Return `{gate: 'caylent-solutions/devbench-internal-backlog#<n>'}` for
    every `gates.properties.<gate>.description` in `schema` whose gate name is
    in `GATE_NAMES` (AC-FUNC-001).

    Raises `ValueError` naming the gate when a present gate's description
    carries zero or more than one qualified internal-backlog citation, since
    either shape means the description can no longer be trusted to name a
    single source issue.
    """
    gates_schema = schema["properties"]["gates"]["properties"]
    citations: dict[str, str] = {}
    for gate_name, gate_spec in gates_schema.items():
        if gate_name not in GATE_NAMES:
            continue
        description = gate_spec.get("description", "")
        matches = _SCHEMA_ISSUE_RE.findall(description)
        if len(matches) != 1:
            raise ValueError(
                f"gates.properties.{gate_name}.description carries {len(matches)} "
                f"internal-backlog citation(s), need exactly 1: {description!r}"
            )
        citations[gate_name] = f"caylent-solutions/devbench-internal-backlog#{matches[0]}"
    return citations


def map_gate_issue_citations(rows: list[ProvenanceRow]) -> dict[str, str]:
    """Return `{gate: internal_issue}` for every `docs/issue-provenance.md` row
    whose gate name is in `GATE_NAMES` (AC-FUNC-001)."""
    return {row.gate: row.internal_issue for row in rows if row.gate in GATE_NAMES}


def assert_schema_matches_map(schema_citations: dict[str, str], map_citations: dict[str, str]) -> None:
    """The shared assertion both the real regression test and the seeded
    controls call (AC-FUNC-001, AC-FUNC-003, AC-FUNC-004): every gate in
    `GATE_NAMES` must be present on both sides with matching internal-issue
    citations.

    Raises `AssertionError` naming the gate and, when both sides name the
    gate, both cited issue values; when only one side names the gate, the
    message instead names which side is missing the gate so the pin cannot
    silently narrow to fewer than all eight gates.
    """
    for gate in GATE_NAMES:
        schema_value = schema_citations.get(gate)
        map_value = map_citations.get(gate)
        assert schema_value is not None, f"gate {gate!r} missing from config-schema.json gate descriptions"
        assert map_value is not None, f"gate {gate!r} missing from docs/issue-provenance.md provenance map"
        assert schema_value == map_value, (
            f"gate {gate!r}: config-schema.json cites {schema_value!r} but docs/issue-provenance.md cites {map_value!r}"
        )


def _build_synthetic_schema(issue_numbers: dict[str, int]) -> dict:
    """Build a minimal `config-schema.json`-shaped dict carrying one
    `gates.properties.<gate>.description` per entry in `issue_numbers`, used
    only by the seeded controls (never written over the real schema file)."""
    return {
        "properties": {
            "gates": {
                "properties": {
                    gate: {
                        "description": (
                            f"{gate} gate (caylent-solutions/devbench-internal-backlog#{number}). "
                            "Synthetic seeded-control fixture."
                        )
                    }
                    for gate, number in issue_numbers.items()
                }
            }
        }
    }


def _build_synthetic_map_text(issue_numbers: dict[str, int]) -> str:
    """Build a minimal `docs/issue-provenance.md`-shaped five-column table
    carrying one row per entry in `issue_numbers`, in the exact shape
    `parse_provenance_map` expects, used only by the seeded controls."""
    lines = [
        "| Gate | Internal Issue | Source PR | Devbench Issues | Spec Section |",
        "|------|-----------------|-----------|------------------|--------------|",
    ]
    for gate, number in issue_numbers.items():
        lines.append(
            f"| `{gate}` | `caylent-solutions/devbench-internal-backlog#{number}` | "
            "`caylent-solutions/devbench#900` | none | `9.9` |"
        )
    return "\n".join(lines) + "\n"


def _schema() -> dict:
    return json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))


def _map_rows() -> list[ProvenanceRow]:
    return parse_provenance_map(PROVENANCE_MAP_PATH.read_text(encoding="utf-8"))


@pytest.mark.unit
class TestConfigSchemaGateProvenance:
    """AC-FUNC-001, AC-FUNC-002: the real regression guard."""

    def test_schema_descriptions_match_provenance_map_for_every_gate(self) -> None:
        """The real pin: `src/devbench/config-schema.json` and
        `docs/issue-provenance.md` must cite the same internal-backlog issue
        for every gate in `GATE_NAMES`. Green from the moment this module is
        added because E2-F7-S1-T4 already corrected the schema side."""
        schema_citations = extract_gate_issue_citations(_schema())
        map_citations = map_gate_issue_citations(_map_rows())
        assert_schema_matches_map(schema_citations, map_citations)

    def test_extract_gate_issue_citations_covers_every_gate_name(self) -> None:
        """Guards against a future gate silently missing a description
        citation altogether: every name in `GATE_NAMES` must resolve to
        exactly one qualified citation in the real schema."""
        schema_citations = extract_gate_issue_citations(_schema())
        assert sorted(schema_citations) == sorted(GATE_NAMES)

    def test_map_gate_issue_citations_covers_every_gate_name(self) -> None:
        """Mirrors the schema-side coverage check for the map side."""
        map_citations = map_gate_issue_citations(_map_rows())
        assert sorted(map_citations) == sorted(GATE_NAMES)


@pytest.mark.unit
class TestSeededMutationControls:
    """AC-FUNC-003: seeded-mutation controls proving `assert_schema_matches_map`
    is falsifiable, using synthetic fixtures under `tmp_path` rather than the
    real files."""

    def test_synthetic_baseline_fixture_is_internally_consistent(self) -> None:
        """Positive control: the synthetic fixture data itself agrees on both
        sides before any mutation, so the mutation controls below are known to
        be exercising the assertion rather than a pre-broken fixture."""
        schema_citations = extract_gate_issue_citations(_build_synthetic_schema(_SYNTHETIC_GATE_ISSUE_NUMBERS))
        map_text = _build_synthetic_map_text(_SYNTHETIC_GATE_ISSUE_NUMBERS)
        map_citations = map_gate_issue_citations(parse_provenance_map(map_text))
        assert_schema_matches_map(schema_citations, map_citations)

    def test_seeded_mutation_on_schema_side_is_caught(self, tmp_path: Path) -> None:
        """Mutating one gate's citation on the schema side only must fail,
        naming the mutated gate and both the (now-disagreeing) schema and map
        issue numbers."""
        target_gate = GATE_NAMES[0]
        mutated_numbers = dict(_SYNTHETIC_GATE_ISSUE_NUMBERS)
        mutated_numbers[target_gate] = mutated_numbers[target_gate] + 1

        schema_path = tmp_path / "synthetic-config-schema.json"
        schema_path.write_text(json.dumps(_build_synthetic_schema(mutated_numbers)), encoding="utf-8")
        map_path = tmp_path / "synthetic-issue-provenance.md"
        map_path.write_text(_build_synthetic_map_text(_SYNTHETIC_GATE_ISSUE_NUMBERS), encoding="utf-8")

        schema_citations = extract_gate_issue_citations(json.loads(schema_path.read_text(encoding="utf-8")))
        map_citations = map_gate_issue_citations(parse_provenance_map(map_path.read_text(encoding="utf-8")))

        with pytest.raises(AssertionError) as exc_info:
            assert_schema_matches_map(schema_citations, map_citations)

        message = str(exc_info.value)
        assert target_gate in message, f"expected mutated gate {target_gate!r} named in: {message}"
        assert str(mutated_numbers[target_gate]) in message, f"expected mutated schema issue number in: {message}"
        assert str(_SYNTHETIC_GATE_ISSUE_NUMBERS[target_gate]) in message, (
            f"expected original map issue number in: {message}"
        )

    def test_seeded_mutation_on_map_side_is_caught(self, tmp_path: Path) -> None:
        """Mutating one gate's citation on the map side only must fail, naming
        the mutated gate and both disagreeing issue numbers -- proves the
        assertion catches a map-side regression, not only a schema-side one."""
        target_gate = GATE_NAMES[-1]
        mutated_numbers = dict(_SYNTHETIC_GATE_ISSUE_NUMBERS)
        mutated_numbers[target_gate] = mutated_numbers[target_gate] + 1

        schema_path = tmp_path / "synthetic-config-schema.json"
        schema_path.write_text(json.dumps(_build_synthetic_schema(_SYNTHETIC_GATE_ISSUE_NUMBERS)), encoding="utf-8")
        map_path = tmp_path / "synthetic-issue-provenance.md"
        map_path.write_text(_build_synthetic_map_text(mutated_numbers), encoding="utf-8")

        schema_citations = extract_gate_issue_citations(json.loads(schema_path.read_text(encoding="utf-8")))
        map_citations = map_gate_issue_citations(parse_provenance_map(map_path.read_text(encoding="utf-8")))

        with pytest.raises(AssertionError) as exc_info:
            assert_schema_matches_map(schema_citations, map_citations)

        message = str(exc_info.value)
        assert target_gate in message, f"expected mutated gate {target_gate!r} named in: {message}"
        assert str(mutated_numbers[target_gate]) in message, f"expected mutated map issue number in: {message}"
        assert str(_SYNTHETIC_GATE_ISSUE_NUMBERS[target_gate]) in message, (
            f"expected original schema issue number in: {message}"
        )


@pytest.mark.unit
class TestSeededOmissionControls:
    """AC-FUNC-004: a gate present in `GATE_NAMES` but absent from either the
    schema or the map fails naming the missing gate, using synthetic fixtures
    under `tmp_path` rather than the real files, so the pin cannot silently
    narrow to fewer than all eight gates."""

    def test_gate_missing_from_schema_side_fails_naming_the_gate(self, tmp_path: Path) -> None:
        missing_gate = GATE_NAMES[0]
        numbers_without_gate = {
            gate: number for gate, number in _SYNTHETIC_GATE_ISSUE_NUMBERS.items() if gate != missing_gate
        }

        schema_path = tmp_path / "synthetic-config-schema.json"
        schema_path.write_text(json.dumps(_build_synthetic_schema(numbers_without_gate)), encoding="utf-8")
        map_path = tmp_path / "synthetic-issue-provenance.md"
        map_path.write_text(_build_synthetic_map_text(_SYNTHETIC_GATE_ISSUE_NUMBERS), encoding="utf-8")

        schema_citations = extract_gate_issue_citations(json.loads(schema_path.read_text(encoding="utf-8")))
        map_citations = map_gate_issue_citations(parse_provenance_map(map_path.read_text(encoding="utf-8")))

        assert missing_gate not in schema_citations
        with pytest.raises(AssertionError, match=re.escape(f"gate {missing_gate!r} missing from config-schema.json")):
            assert_schema_matches_map(schema_citations, map_citations)

    def test_gate_missing_from_map_side_fails_naming_the_gate(self, tmp_path: Path) -> None:
        missing_gate = GATE_NAMES[-1]
        numbers_without_gate = {
            gate: number for gate, number in _SYNTHETIC_GATE_ISSUE_NUMBERS.items() if gate != missing_gate
        }

        schema_path = tmp_path / "synthetic-config-schema.json"
        schema_path.write_text(json.dumps(_build_synthetic_schema(_SYNTHETIC_GATE_ISSUE_NUMBERS)), encoding="utf-8")
        map_path = tmp_path / "synthetic-issue-provenance.md"
        map_path.write_text(_build_synthetic_map_text(numbers_without_gate), encoding="utf-8")

        schema_citations = extract_gate_issue_citations(json.loads(schema_path.read_text(encoding="utf-8")))
        map_citations = map_gate_issue_citations(parse_provenance_map(map_path.read_text(encoding="utf-8")))

        assert missing_gate not in map_citations
        with pytest.raises(
            AssertionError, match=re.escape(f"gate {missing_gate!r} missing from docs/issue-provenance.md")
        ):
            assert_schema_matches_map(schema_citations, map_citations)
