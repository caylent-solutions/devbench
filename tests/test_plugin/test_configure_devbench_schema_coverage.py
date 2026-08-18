"""Schema-coverage and interview-structure pins for E2-F8-S1-T1 (spec
`integration-reality-gates-hardening.md` section 4.15; D-16, G12; AC-28,
AC-29; AC-E2-F8-S1-T1-1 through -6).

Decision D-16 requires the `configure-devbench` skill to interview the
operator about EVERY setting in `src/devbench/config-schema.json` -- every
existing section and the `gates:` block (added by E2-F1) alike -- with one
interactive choice menu per setting: the recommended value marked as such,
every alternative, and a free-form "enter your own" path.

This module is the anti-drift mechanism the task description calls for: it
walks `config-schema.json` recursively (`walk_schema_settings`), building the
dotted-path inventory of every property including every `gates.*` key, and
asserts (`assert_skill_names_every_setting`) that the SKILL text names every
one -- failing with the missing property paths when it does not
(AC-E2-F8-S1-T1-1). A companion structural pin
(`assert_interview_blocks_complete`) parses each `#### \\`dotted.path\\`` block
the rewritten SKILL carries for every STATIC leaf setting (a setting that is
neither a container nor reached through a dynamic per-instance map such as
`repos.<org/repo>.*`) and asserts it carries all three required elements --
the `**Recommended:**` marker, the `**Alternatives:**` marker, and the
`**Free-form:**` marker -- failing with the setting name and the missing
element when one is absent (AC-E2-F8-S1-T1-3). A third check
(`assert_output_contract_validates_before_success`) pins the AC-29 output
contract structurally: the SKILL must instruct `load_runtime_config`
validation of the authored yaml strictly before the point where it writes the
file and reports `[CONFIGURE_DEVBENCH_DONE]` success (AC-E2-F8-S1-T1-4).

Every assertion helper is exercised against BOTH the real, shipped SKILL.md
(the regression guard, green from the moment this module lands because the
SKILL was rewritten in the same task) and synthetic seeded-violation fixtures
built entirely in-memory (never mutating the real schema or SKILL files),
proving each helper is genuinely falsifiable rather than vacuously true.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from devbench.constants import GATE_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]

CONFIG_SCHEMA_PATH = REPO_ROOT / "src" / "devbench" / "config-schema.json"
SKILL_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "configure-devbench" / "SKILL.md"
DOC_PATH = REPO_ROOT / "docs" / "skills" / "configure-devbench.md"
ONBOARDING_DOC_PATH = REPO_ROOT / "docs" / "onboarding.md"

_REQUIRED_BLOCK_MARKERS: tuple[str, ...] = ("**Recommended:**", "**Alternatives:**", "**Free-form:**")

# The literal sentence the rewritten SKILL.md carries in its Step 21 output
# contract, and the two markers that must follow it in file order (AC-29):
# the assembled config must be validated, THEN written, THEN reported done.
_OUTPUT_CONTRACT_ANCHOR = "MUST be validated by `load_runtime_config` before this skill reports success"
_WRITE_MARKER = "Write backlog/config/devbench.yaml"
_SUCCESS_MARKER = "CONFIGURE_DEVBENCH_DONE"

# The two literal phrases the rewritten SKILL.md's every-invocation contract
# (AC-E2-F8-S1-T1-5) carries.
_EVERY_INVOCATION_PHRASE = "runs in full on every invocation"
_NEVER_REUSE_PHRASE = "never silently reuses a prior answer"

# Heading marker every leaf interview block starts with, followed immediately
# by the dotted path in backticks, e.g. "#### `timeouts.gh_api` -- ...".
_BLOCK_HEADING_RE = re.compile(r"^####\s+`([^`]+)`", re.MULTILINE)
# Any heading (## / ### / ####) marks the end of the current block's body.
_ANY_HEADING_RE = re.compile(r"^#{2,4}\s", re.MULTILINE)


# ---------------------------------------------------------------------------
# Shared helper: recursive schema walk (Approach step 7 -- one implementation
# shared by the coverage pin and the companion structural pin).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaSetting:
    """One property discovered by `walk_schema_settings`.

    `dotted_path` uses the literal `<entry>` placeholder segment for any
    ancestor reached through a dynamic per-instance map (JSON Schema
    `patternProperties`, a schema-valued `additionalProperties`, or an array
    `items` schema that itself has fixed `properties`) -- for example
    `repos.<entry>.default_branch` or `gates.repos.<entry>.reachability.enabled`
    -- since the real instance key (an operator's own `org/repo` name or
    model id) is not enumerable from the schema alone.

    `is_container` is true for any node that itself has further children
    (a plain object with `properties`, a dynamic map, or an array of
    objects) -- containers get a section heading in the SKILL, not a
    Recommended/Alternatives/Free-form interview block.

    `in_dynamic_map` is true when `<entry>` appears anywhere in
    `dotted_path`, i.e. this setting is reached only through an
    operator-defined dynamic map and cannot be covered by a literal,
    schema-fixed dotted-path heading.
    """

    dotted_path: str
    is_container: bool
    in_dynamic_map: bool

    @property
    def leaf_name(self) -> str:
        return self.dotted_path.rsplit(".", 1)[-1]

    @property
    def is_static_leaf(self) -> bool:
        """A setting that gets its own full `#### \\`dotted.path\\`` interview
        block: neither a container nor reached through a dynamic map."""
        return not self.is_container and not self.in_dynamic_map


def _is_removed(node: object) -> bool:
    """True for a schema node explicitly marked retired (issue #223's
    `token_cost_per_million_input` etc.): present in the schema so
    `config_loader.py` can name the replacement in its error message, but
    not a real setting an interview should ask about."""
    return isinstance(node, dict) and str(node.get("$comment", "")).startswith("removed-in-")


def _children_kind(node: object, path: str = "<root>") -> tuple[str, dict] | None:
    """Return `(kind, child_schema_map_or_schema)` describing how to recurse
    into `node`, or `None` for a terminal (leaf) node.

    `kind == "properties"`: `node["properties"]` is a fixed name -> schema
    map; recurse once per key, appending the literal key to the path.

    `kind == "entry"`: `node` is a dynamic map (patternProperties /
    schema-valued additionalProperties) or an array of objects (`items`
    with its own `properties`); recurse into the single per-instance schema
    using the `<entry>` placeholder segment instead of a literal key.

    `path` identifies `node`'s own dotted position in the schema (or
    `"<root>"` for the schema root) purely for diagnostic purposes.

    Raises `AssertionError` naming `path` when `node` presents an ambiguous
    or unhandled child shape -- a node carrying BOTH `properties` and
    `patternProperties`, or a node with more than one `patternProperties`
    entry. Earlier revisions of this helper silently narrowed to a single
    branch in both cases, which would let a future schema edit of either
    shape lose interview coverage without CI ever failing -- exactly the
    drift this anti-drift module exists to prevent. Today's
    `config-schema.json` has neither shape, so this never fires against the
    real schema; `TestSeededChildrenKindAmbiguousShapes` below seeds both
    shapes against synthetic nodes to prove the guard is live.
    """
    if not isinstance(node, dict):
        return None
    properties = node.get("properties")
    pattern_properties = node.get("patternProperties")
    if properties and pattern_properties:
        raise AssertionError(
            f"config-schema.json node at {path!r} declares BOTH 'properties' and "
            "'patternProperties'; _children_kind refuses to silently walk only one "
            "branch and drop the other's subtree."
        )
    if properties:
        return ("properties", properties)
    if pattern_properties:
        if len(pattern_properties) > 1:
            raise AssertionError(
                f"config-schema.json node at {path!r} declares "
                f"{len(pattern_properties)} 'patternProperties' entries; "
                "_children_kind only supports exactly one and refuses to "
                "silently discard the rest."
            )
        ((_pattern, sub_schema),) = list(pattern_properties.items())
        return ("entry", sub_schema)
    additional_properties = node.get("additionalProperties")
    if isinstance(additional_properties, dict):
        return ("entry", additional_properties)
    if node.get("type") == "array":
        items = node.get("items")
        if isinstance(items, dict) and items.get("properties"):
            return ("entry", items)
    return None


def walk_schema_settings(schema: dict) -> list[SchemaSetting]:
    """Recursively walk `schema` (a `config-schema.json`-shaped dict, or a
    synthetic fixture of the same shape) and return one `SchemaSetting` per
    property discovered, in schema-declaration order. Retired
    (`$comment: "removed-in-..."`) nodes are skipped entirely (never
    appended, never recursed into)."""
    settings: list[SchemaSetting] = []

    def _walk(node: dict, prefix: str) -> None:
        kind_map = _children_kind(node, prefix or "<root>")
        if kind_map is None:
            return
        kind, child = kind_map
        if kind == "properties":
            for key, sub_schema in child.items():
                if _is_removed(sub_schema):
                    continue
                path = f"{prefix}.{key}" if prefix else key
                in_dynamic_map = "<entry>" in path
                is_container = _children_kind(sub_schema, path) is not None
                settings.append(
                    SchemaSetting(dotted_path=path, is_container=is_container, in_dynamic_map=in_dynamic_map)
                )
                _walk(sub_schema, path)
        else:  # kind == "entry": dynamic map or array-of-objects boundary
            _walk(child, f"{prefix}.<entry>")

    _walk(schema, "")
    return settings


# Placeholder text the shipped SKILL.md prose already uses, verbatim, for
# the per-instance key of a dynamic per-instance map or array -- keyed by
# the dotted-path prefix immediately preceding the `<entry>` placeholder
# segment. For the three operator-NAMED maps (JSON Schema
# `patternProperties` / schema-valued `additionalProperties` keyed by a real
# operator-defined name such as an `org/repo` string or a model id), the
# placeholder is the named token the SKILL prose already uses. Confirmed by
# reading the shipped SKILL.md directly (not assumed):
# `gates.repos.<org/repo>.reachability.enabled` at line 961 and its seven
# `.enabled` siblings, `repos.<org/repo>.merge_strategy` at line 117,
# `repos.<org/repo>.branch_prefix` at line 529.
#
# The two other `<entry>`-bearing nodes in config-schema.json --
# `gates.fixture_consistency.canonical_sources` and `.scan` -- are dynamic
# ARRAYS (JSON Schema `items`), not named maps: an array element has no
# operator-chosen key, so the SKILL prose spells each field's dotted-key
# template with the literal, non-operator-chosen `<item>` placeholder
# instead (mirroring `report.models.<id>.input`'s "full dotted key
# template" pattern already used for the operator-named maps). Confirmed by
# reading the shipped SKILL.md directly: `gates.fixture_consistency.
# canonical_sources.<item>.path` and its six siblings in the
# "dynamic arrays" section.
_DYNAMIC_MAP_PLACEHOLDERS: dict[str, str] = {
    "repos": "<org/repo>",
    "report.models": "<id>",
    "gates.repos": "<org/repo>",
    "gates.fixture_consistency.canonical_sources": "<item>",
    "gates.fixture_consistency.scan": "<item>",
}

# Placeholder table for the small synthetic recursion-shape fixture
# (`_synthetic_schema`, used by `TestSeededSchemaWalkControls` and the two
# `TestSeededCoverageAssertionControls` tests that exercise EVERY setting
# `_synthetic_schema` produces, dynamic maps included). Deliberately kept
# separate from `_DYNAMIC_MAP_PLACEHOLDERS`, which documents only the roots
# that exist in the REAL `config-schema.json` today -- mixing test-only
# fixture roots into that table would make its docstring's "confirmed by
# reading the shipped SKILL.md" claim false. Passed explicitly via
# `required_coverage_token(..., placeholders=...)` /
# `assert_skill_names_every_setting(..., placeholders=...)` wherever those
# two tests need every synthetic dynamic-map leaf to resolve.
_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS: dict[str, str] = {
    "dynamic_map": "<key>",
    "additional_props_map": "<key>",
    "array_of_objects": "<index>",
}


def _dynamic_map_root(dotted_path: str) -> str:
    """Return the dotted-path prefix immediately before the FIRST `<entry>`
    placeholder segment, e.g. `'gates.repos'` for
    `'gates.repos.<entry>.reachability.enabled'`, or `'report.models'` for
    `'report.models.<entry>.input'`."""
    root, _, _ = dotted_path.partition(".<entry>")
    return root


def required_coverage_token(setting: SchemaSetting, *, placeholders: dict[str, str] = _DYNAMIC_MAP_PLACEHOLDERS) -> str:
    """The literal substring `assert_skill_names_every_setting` requires
    present in the SKILL text for `setting` (AC-E2-F8-S1-T1-1).

    For a setting reached through a dynamic per-instance map or array whose
    root is registered in `placeholders` (by default `_DYNAMIC_MAP_PLACEHOLDERS`:
    `repos`, `report.models`, `gates.repos`,
    `gates.fixture_consistency.canonical_sources`,
    `gates.fixture_consistency.scan`), coverage requires the FULL dotted
    path with `<entry>` rendered as the literal instance placeholder the
    SKILL prose already uses for that map or array (e.g.
    `repos.<org/repo>.default_branch`, `gates.repos.<org/repo>.
    reachability.enabled`, `gates.fixture_consistency.canonical_sources.
    <item>.path`) -- never the bare leaf name alone. This is load-bearing:
    `config-schema.json` reuses common words (`enabled`, a gate name,
    `patterns`, `path`) across unrelated settings, and every one of those
    words already appears elsewhere in SKILL.md for unrelated
    project-level reasons, so a bare-leaf-name requirement could never fail
    for those settings even if the entire dynamic-map subsection describing
    them were deleted.

    A dynamic-map setting whose root is NOT registered in `placeholders`
    raises `AssertionError` naming the unregistered root and the offending
    setting's dotted path -- mirroring `_children_kind`'s refusal to
    silently narrow to one branch on an ambiguous schema shape. Earlier
    revisions of this helper fell back to the bare leaf name for an
    unregistered root, which is exactly the vacuous, unfalsifiable check
    this docstring warns against two paragraphs up: register the real
    placeholder token for the new root in `_DYNAMIC_MAP_PLACEHOLDERS`
    instead of reinstating that fallback.

    Every other setting -- including every container and every `gates.*`
    key that is not itself reached through a dynamic map or array -- must
    be named by its FULL dotted path.
    """
    if setting.in_dynamic_map:
        root = _dynamic_map_root(setting.dotted_path)
        placeholder = placeholders.get(root)
        if placeholder is None:
            raise AssertionError(
                f"required_coverage_token: dynamic-map root {root!r} (setting "
                f"{setting.dotted_path!r}) has no registered placeholder; refusing to "
                "silently fall back to the bare leaf name, which could never fail even if "
                "this map's entire SKILL subsection were deleted. Register the real "
                "placeholder token for this root instead of reinstating the fallback."
            )
        return setting.dotted_path.replace("<entry>", placeholder)
    return setting.dotted_path


def assert_skill_names_every_setting(
    skill_text: str, settings: list[SchemaSetting], *, placeholders: dict[str, str] = _DYNAMIC_MAP_PLACEHOLDERS
) -> None:
    """AC-E2-F8-S1-T1-1 / -2: every property `walk_schema_settings` finds
    must be named somewhere in `skill_text`. Raises `AssertionError` naming
    every missing property's full dotted path (not merely a count) so a
    drifted schema addition is immediately actionable. `placeholders` is
    forwarded to `required_coverage_token`; callers exercising the small
    synthetic recursion-shape fixture pass `_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS`
    so its non-production dynamic-map roots resolve instead of raising."""
    missing = [
        s.dotted_path for s in settings if required_coverage_token(s, placeholders=placeholders) not in skill_text
    ]
    if missing:
        raise AssertionError(
            "configure-devbench SKILL.md does not name the following config-schema.json "
            f"propert{'y' if len(missing) == 1 else 'ies'} (including gates.* keys where applicable): "
            f"{missing}"
        )


# ---------------------------------------------------------------------------
# Shared helper: interview-block parser (Approach step 7).
# ---------------------------------------------------------------------------


def parse_interview_blocks(skill_text: str) -> dict[str, str]:
    """Split `skill_text` into `{dotted_path: block_body}` for every
    `#### \\`dotted.path\\`` heading. `block_body` runs from immediately after
    the heading line to the next `##`/`###`/`####` heading (or end of file).
    Later headings for the same path (should never happen in a well-formed
    SKILL, but the parser does not assume uniqueness) overwrite earlier ones.
    """
    headings = list(_BLOCK_HEADING_RE.finditer(skill_text))
    blocks: dict[str, str] = {}
    for match in headings:
        path = match.group(1)
        body_start = match.end()
        next_heading = _ANY_HEADING_RE.search(skill_text, body_start)
        body_end = next_heading.start() if next_heading else len(skill_text)
        blocks[path] = skill_text[body_start:body_end]
    return blocks


def assert_interview_blocks_complete(blocks: dict[str, str], static_leaf_paths: list[str]) -> None:
    """AC-E2-F8-S1-T1-3: every path in `static_leaf_paths` must have a block
    in `blocks` carrying every marker in `_REQUIRED_BLOCK_MARKERS`. Raises
    `AssertionError` naming the setting and the specific missing element (or
    'entire block' when the heading itself is absent) on the FIRST violation
    found, in `static_leaf_paths` order.
    """
    for path in static_leaf_paths:
        body = blocks.get(path)
        if body is None:
            raise AssertionError(
                f"configure-devbench SKILL.md has no '#### `{path}`' interview block at all "
                "(setting, missing element: entire block)"
            )
        missing_markers = [marker for marker in _REQUIRED_BLOCK_MARKERS if marker not in body]
        if missing_markers:
            raise AssertionError(
                f"configure-devbench SKILL.md's interview block for '{path}' is missing: "
                f"{', '.join(missing_markers)} (setting: {path!r}, missing element(s): {missing_markers!r})"
            )


# ---------------------------------------------------------------------------
# Shared helper: AC-29 output-contract structural check.
# ---------------------------------------------------------------------------


def assert_output_contract_validates_before_success(skill_text: str) -> None:
    """AC-E2-F8-S1-T1-4 / AC-29: the SKILL must carry the literal output-
    contract anchor sentence, and that sentence, the file-write instruction,
    and the success report must appear in that strict file order -- proving
    the skill instructs validating the authored yaml via `load_runtime_config`
    BEFORE writing the file and reporting success, not after.
    """
    if _OUTPUT_CONTRACT_ANCHOR not in skill_text:
        raise AssertionError(
            f"configure-devbench SKILL.md must carry the output-contract anchor sentence "
            f"{_OUTPUT_CONTRACT_ANCHOR!r} (AC-29)"
        )
    if _WRITE_MARKER not in skill_text:
        raise AssertionError(
            f"configure-devbench SKILL.md must carry the file-write instruction {_WRITE_MARKER!r} (AC-29)"
        )
    if _SUCCESS_MARKER not in skill_text:
        raise AssertionError(f"configure-devbench SKILL.md must report success via {_SUCCESS_MARKER!r} (AC-29)")
    anchor_pos = skill_text.index(_OUTPUT_CONTRACT_ANCHOR)
    write_pos = skill_text.index(_WRITE_MARKER)
    done_pos = skill_text.index(_SUCCESS_MARKER)
    if not (anchor_pos < write_pos < done_pos):
        raise AssertionError(
            "configure-devbench SKILL.md's output-contract anchor, file-write instruction, and "
            f"success report are out of order (anchor={anchor_pos}, write={write_pos}, done={done_pos}); "
            "the skill must validate via load_runtime_config strictly BEFORE writing the file and "
            "reporting success (AC-29)."
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _schema() -> dict:
    with CONFIG_SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _onboarding_doc_text() -> str:
    return ONBOARDING_DOC_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Real regression pins (green from the moment this module lands, because the
# SKILL.md rewrite lands in the same task).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRealSkillNamesEverySchemaProperty:
    """AC-E2-F8-S1-T1-1, AC-E2-F8-S1-T1-2 (AC-28): the real schema-coverage
    pin against the shipped files."""

    def test_walk_schema_settings_is_nonempty_and_finds_known_paths(self) -> None:
        settings = walk_schema_settings(_schema())
        paths = {s.dotted_path for s in settings}
        assert len(settings) > 100, (
            f"expected the real schema walk to find well over 100 properties, got {len(settings)}"
        )
        for known in ("repos", "timeouts.gh_api", "gates", "gates.reachability", "gates.reachability.enabled"):
            assert known in paths, (
                f"expected {known!r} to be discovered by walk_schema_settings; got paths: {sorted(paths)[:20]}..."
            )

    def test_skill_names_every_schema_property(self) -> None:
        """The real pin: every config-schema.json property, including every
        gates.* key, must be named in the shipped SKILL.md text."""
        settings = walk_schema_settings(_schema())
        assert_skill_names_every_setting(_skill_text(), settings)

    @pytest.mark.parametrize("gate", GATE_NAMES)
    def test_skill_names_every_gate_and_its_enabled_key(self, gate: str) -> None:
        """AC-28's explicit callout: every gates.* key added by E2-F1 must be
        named, checked individually per gate so a single missing gate fails
        with that gate's own name rather than a generic count."""
        text = _skill_text()
        assert f"gates.{gate}" in text, f"SKILL.md must name gate 'gates.{gate}'"
        assert f"gates.{gate}.enabled" in text, f"SKILL.md must name 'gates.{gate}.enabled'"

    def test_removed_report_fields_are_not_required(self) -> None:
        """issue #223's retired report.token_cost_* fields carry a
        '$comment: removed-in-#223' marker and must NOT be required by the
        coverage walk (they are not real settings to interview about)."""
        settings = walk_schema_settings(_schema())
        paths = {s.dotted_path for s in settings}
        removed_fields = (
            "report.token_cost_per_million_input",
            "report.token_cost_per_million_output",
            "report.token_cost_discount",
        )
        for removed in removed_fields:
            assert removed not in paths, (
                f"{removed!r} is marked removed-in-#223 and must be excluded from the coverage walk"
            )

    def test_deleting_gates_repos_subsection_from_real_skill_is_caught(self) -> None:
        """Regression for the round-2 code_review finding: before
        `required_coverage_token` required the full keyed path for dynamic
        maps, it fell back to the bare leaf name ('enabled', a gate name, or
        'patterns') for every `gates.repos.<org/repo>.*` setting -- words
        that already appear elsewhere in SKILL.md for unrelated
        project-level reasons -- so removing every trace of the
        `gates.repos.<org/repo>` per-repo override map from the SKILL text
        left the pin vacuously green.

        Deleting ONLY the dedicated '### `gates.repos` (dynamic per-repo
        override map)' subsection is NOT by itself a sufficient mutation to
        prove this: the shipped SKILL also cross-references the full
        `gates.repos.<org/repo>.<gate>.enabled` path once per gate, in each
        gate's own `**Alternatives:**` bullet (Step 16, e.g. line 961),
        physically outside that subsection -- confirmed by re-running this
        exact narrower mutation, which still passes even after the fix
        (verified manually and recorded in the TDD Cycle Log), because that
        redundant cross-reference text is genuine operator-facing coverage,
        not a vacuous collision. This test therefore also strips every
        remaining literal `gates.repos.<org/repo>` occurrence (the Step-16
        cross-references and the dynamic-map summary in the intro), so the
        mutation genuinely removes ALL trace of `gates.repos.<org/repo>.*`
        coverage -- proving the strengthened pin would catch a SKILL that
        never named the override map anywhere, not merely one that lost its
        dedicated subsection."""
        settings = walk_schema_settings(_schema())
        text = _skill_text()
        heading = "### `gates.repos` (dynamic per-repo override map)"
        assert heading in text, f"fixture assumption broken: {heading!r} not found in the shipped SKILL.md"
        start = text.index(heading)
        end = text.index("## Step 17", start)
        mutated = text[:start] + text[end:]
        assert heading not in mutated, "mutation must actually remove the gates.repos subsection heading"
        cross_reference = "gates.repos.<org/repo>"
        assert cross_reference in mutated, (
            "fixture assumption broken: expected at least one gates.repos.<org/repo> cross-reference "
            "to survive the subsection deletion (e.g. the Step 16 Alternatives bullets)"
        )
        mutated = mutated.replace(cross_reference, "gates.<removed-by-seeded-mutation>")
        assert cross_reference not in mutated
        with pytest.raises(AssertionError) as excinfo:
            assert_skill_names_every_setting(mutated, settings)
        message = str(excinfo.value)
        assert "gates.repos.<entry>.reachability.enabled" in message, (
            f"expected the missing gates.repos.<entry>.* paths to be named in the failure; got: {message}"
        )
        # Control: the real, unmutated SKILL.md must still pass -- proving
        # this is a genuine mutation-triggered failure, not a permanently
        # broken assertion.
        assert_skill_names_every_setting(text, settings)


@pytest.mark.unit
class TestRealSkillInterviewBlocksComplete:
    """AC-E2-F8-S1-T1-3 (AC-28): the companion structural pin against the
    shipped SKILL.md."""

    def test_every_static_leaf_has_a_complete_interview_block(self) -> None:
        settings = walk_schema_settings(_schema())
        static_leaf_paths = [s.dotted_path for s in settings if s.is_static_leaf]
        assert len(static_leaf_paths) > 50, (
            f"expected well over 50 static leaf settings in the real schema, got {len(static_leaf_paths)}"
        )
        blocks = parse_interview_blocks(_skill_text())
        assert_interview_blocks_complete(blocks, static_leaf_paths)

    def test_parsed_block_count_matches_heading_count(self) -> None:
        """Sanity: the parser finds at least one block per static leaf (a
        vacuous parser that found zero blocks would let the completeness
        check above pass trivially on an empty list, which the previous test
        already rules out via its len() assertion, but this pins the parser
        itself is doing real work on the real file)."""
        blocks = parse_interview_blocks(_skill_text())
        assert len(blocks) > 50, (
            f"expected well over 50 parsed interview blocks in the real SKILL.md, got {len(blocks)}"
        )


@pytest.mark.unit
class TestRealSkillOutputContractAndEveryInvocation:
    """AC-E2-F8-S1-T1-4 (AC-29) and AC-E2-F8-S1-T1-5 against the shipped
    SKILL.md."""

    def test_output_contract_validates_before_success(self) -> None:
        assert_output_contract_validates_before_success(_skill_text())

    def test_every_invocation_contract_is_stated(self) -> None:
        text = _skill_text()
        assert _EVERY_INVOCATION_PHRASE in text, (
            f"SKILL.md must state the interview {_EVERY_INVOCATION_PHRASE!r} (AC-E2-F8-S1-T1-5)"
        )
        assert _NEVER_REUSE_PHRASE in text, f"SKILL.md must state it {_NEVER_REUSE_PHRASE!r} (AC-E2-F8-S1-T1-5)"

    def test_prior_values_shown_as_current(self) -> None:
        text = _skill_text()
        assert "CURRENT VALUE" in text or "current value" in text, (
            "SKILL.md must show prior config values as the current value in every menu (AC-E2-F8-S1-T1-5)"
        )


_DOC_CASES = (
    (_doc_text, "docs/skills/configure-devbench.md"),
    (_onboarding_doc_text, "docs/onboarding.md Step 3"),
)


@pytest.mark.unit
class TestRealDocDescribesInterviewContractNotSuperseded:
    """AC-E2-F8-S1-T1-6: `docs/skills/configure-devbench.md` and
    `docs/onboarding.md` Step 3 both describe the rewritten
    every-invocation interview contract and no longer describe the
    superseded flow. Without this pin a silent revert of either doc back
    to the pre-rewrite wording would leave the rest of this module green
    (it only reads SKILL.md), which is exactly the drift this test class
    closes. Parametrized over `(doc_text_fn, doc_label)` per this module's
    own PARAMETRIZE rule -- `docs/onboarding.md`'s pin (test_review
    advisory) is a straight repeat of `docs/skills/configure-devbench.md`'s
    pin against a second file, not an independent check."""

    @pytest.mark.parametrize(("doc_text_fn", "doc_label"), _DOC_CASES)
    def test_doc_states_the_every_invocation_contract(self, doc_text_fn, doc_label: str) -> None:
        text = doc_text_fn()
        assert "runs in full on every invocation" in text, (
            f"{doc_label} must state the every-invocation contract (AC-E2-F8-S1-T1-6)"
        )

    @pytest.mark.parametrize(("doc_text_fn", "doc_label"), _DOC_CASES)
    def test_doc_no_longer_describes_the_superseded_flow(self, doc_text_fn, doc_label: str) -> None:
        text = doc_text_fn()
        assert "16 sections" not in text, (
            f"{doc_label} must not describe the superseded 16-section flow (AC-E2-F8-S1-T1-6)"
        )
        assert "pre-populate" not in text, (
            f"{doc_label} must not describe the superseded pre-populate-on-existing behaviour (AC-E2-F8-S1-T1-6)"
        )


@pytest.mark.unit
class TestRealSkillNoEmDash:
    def test_skill_has_no_em_dash(self) -> None:
        text = _skill_text()
        assert "\u2014" not in text, (
            "configure-devbench SKILL.md must not contain em-dash characters (U+2014); use '--'"
        )


# ---------------------------------------------------------------------------
# Seeded controls: synthetic schema/skill fixtures built entirely in-memory,
# proving every shared assertion helper is genuinely falsifiable (never
# mutates the real schema or SKILL.md file).
# ---------------------------------------------------------------------------


def _synthetic_schema() -> dict:
    """A small, self-contained config-schema.json-shaped fixture exercising
    every recursion path walk_schema_settings must handle: a plain scalar
    leaf, a nested container, a gates-like block, a patternProperties
    dynamic map, a schema-valued additionalProperties dynamic map, an array
    of objects, and a removed-in-# field."""
    return {
        "properties": {
            "alpha": {"type": "string", "description": "a static scalar leaf"},
            "beta": {
                "type": "object",
                "properties": {
                    "gamma": {"type": "boolean"},
                    "delta": {"type": "integer"},
                },
            },
            "gates_like": {
                "type": "object",
                "properties": {
                    "widget_one": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                    },
                    "widget_two": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                    },
                },
            },
            "dynamic_map": {
                "type": "object",
                "patternProperties": {
                    "^[^/]+/[^/]+$": {
                        "type": "object",
                        "properties": {"nested_field": {"type": "string"}},
                    }
                },
            },
            "additional_props_map": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {"rate": {"type": "number"}},
                },
            },
            "array_of_objects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"item_field": {"type": "string"}},
                },
            },
            "retired_field": {
                "description": "REMOVED in a synthetic issue.",
                "$comment": "removed-in-#999",
            },
        }
    }


def _synthetic_schema_with_known_map_collision() -> dict:
    """A synthetic schema reusing the REAL `repos` dynamic-map root name (so
    `required_coverage_token` renders the real `repos.<org/repo>` placeholder
    from `_DYNAMIC_MAP_PLACEHOLDERS`) whose only leaf is named `enabled` --
    a name deliberately chosen to collide with an unrelated word that a
    realistic SKILL fixture would already contain for a completely
    different reason. Proves the strengthened coverage check requires the
    full keyed path (`repos.<org/repo>.enabled`) rather than being silently
    satisfied by an unrelated occurrence of the bare word `enabled`."""
    return {
        "properties": {
            "repos": {
                "type": "object",
                "patternProperties": {
                    "^[^/]+/[^/]+$": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                    }
                },
            },
        }
    }


def _render_synthetic_block(
    path: str,
    *,
    with_recommended: bool = True,
    with_alternatives: bool = True,
    with_freeform: bool = True,
) -> str:
    lines = [f"#### `{path}` -- Synthetic setting"]
    lines.append("")
    lines.append("A synthetic explanation for a seeded-control fixture.")
    lines.append("")
    if with_recommended:
        lines.append("- **Recommended:** `x` -- because.")
    if with_alternatives:
        lines.append("- **Alternatives:** `y` (consequence)")
    if with_freeform:
        lines.append("- **Free-form:** type your own value.")
    lines.append("")
    return "\n".join(lines)


@pytest.mark.unit
class TestSeededSchemaWalkControls:
    """Proves `walk_schema_settings` handles every recursion boundary and
    excludes retired fields, using the synthetic fixture only."""

    def _paths(self) -> dict[str, SchemaSetting]:
        return {s.dotted_path: s for s in walk_schema_settings(_synthetic_schema())}

    def test_static_leaf_scalar_is_found(self) -> None:
        settings = self._paths()
        assert "alpha" in settings
        assert settings["alpha"].is_static_leaf

    def test_nested_container_leaf_is_found_with_full_path(self) -> None:
        settings = self._paths()
        assert "beta.gamma" in settings
        assert settings["beta.gamma"].is_static_leaf
        assert settings["beta"].is_container

    def test_gates_like_names_are_distinct_and_static(self) -> None:
        settings = self._paths()
        assert "gates_like.widget_one.enabled" in settings
        assert "gates_like.widget_two.enabled" in settings
        assert settings["gates_like.widget_one.enabled"].is_static_leaf
        assert settings["gates_like.widget_two.enabled"].is_static_leaf

    def test_pattern_properties_dynamic_map_uses_entry_placeholder(self) -> None:
        settings = self._paths()
        assert "dynamic_map.<entry>.nested_field" in settings
        setting = settings["dynamic_map.<entry>.nested_field"]
        assert setting.in_dynamic_map
        assert not setting.is_static_leaf

    def test_additional_properties_schema_map_uses_entry_placeholder(self) -> None:
        settings = self._paths()
        assert "additional_props_map.<entry>.rate" in settings
        assert settings["additional_props_map.<entry>.rate"].in_dynamic_map

    def test_array_of_objects_uses_entry_placeholder(self) -> None:
        settings = self._paths()
        assert "array_of_objects.<entry>.item_field" in settings
        assert settings["array_of_objects.<entry>.item_field"].in_dynamic_map

    def test_retired_field_is_excluded_entirely(self) -> None:
        settings = self._paths()
        assert "retired_field" not in settings


@pytest.mark.unit
class TestSeededChildrenKindAmbiguousShapes:
    """Proves `_children_kind` fails loudly (AssertionError naming the
    offending node path) rather than silently narrowing to one branch, for
    the two ambiguous shapes `config-schema.json` does not currently
    contain but a future edit could introduce: a node with BOTH
    `properties` and `patternProperties`, and a node with more than one
    `patternProperties` entry."""

    def test_properties_and_pattern_properties_together_raises(self) -> None:
        node = {
            "type": "object",
            "properties": {"top_level_only_key": {"type": "string"}},
            "patternProperties": {
                "^[^/]+/[^/]+$": {
                    "type": "object",
                    "properties": {"per_repo_only_key": {"type": "string"}},
                }
            },
        }
        with pytest.raises(AssertionError) as excinfo:
            _children_kind(node, "ambiguous_both_shapes")
        message = str(excinfo.value)
        assert "ambiguous_both_shapes" in message
        assert "properties" in message
        assert "patternProperties" in message

    def test_two_pattern_properties_entries_raises(self) -> None:
        node = {
            "type": "object",
            "patternProperties": {
                "^first-.*$": {
                    "type": "object",
                    "properties": {"first_pattern_key": {"type": "string"}},
                },
                "^second-.*$": {
                    "type": "object",
                    "properties": {"second_pattern_key": {"type": "string"}},
                },
            },
        }
        with pytest.raises(AssertionError) as excinfo:
            _children_kind(node, "ambiguous_two_patterns")
        message = str(excinfo.value)
        assert "ambiguous_two_patterns" in message
        assert "2" in message

    def test_unambiguous_shapes_still_resolve_without_raising(self) -> None:
        """Control: a node with only `properties`, or only a single
        `patternProperties` entry, must not raise -- the guard only fires
        on the two genuinely ambiguous shapes above."""
        only_properties = {"type": "object", "properties": {"a": {"type": "string"}}}
        only_one_pattern = {
            "type": "object",
            "patternProperties": {"^[^/]+/[^/]+$": {"type": "object", "properties": {"b": {"type": "string"}}}},
        }
        assert _children_kind(only_properties, "unambiguous_properties") is not None
        assert _children_kind(only_one_pattern, "unambiguous_single_pattern") is not None


@pytest.mark.unit
class TestSeededCoverageAssertionControls:
    """Proves `assert_skill_names_every_setting` is falsifiable: passes when
    every required token is present, fails naming the missing property when
    one is absent."""

    def test_passes_when_every_token_present(self) -> None:
        settings = walk_schema_settings(_synthetic_schema())
        text = " ".join(required_coverage_token(s, placeholders=_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS) for s in settings)
        assert_skill_names_every_setting(
            text, settings, placeholders=_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS
        )  # must not raise

    def test_fails_naming_missing_static_property(self) -> None:
        settings = walk_schema_settings(_synthetic_schema())
        excluded = "gates_like.widget_two.enabled"
        text = " ".join(
            required_coverage_token(s, placeholders=_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS)
            for s in settings
            if s.dotted_path != excluded
        )
        with pytest.raises(AssertionError, match=re.escape(excluded)):
            assert_skill_names_every_setting(text, settings, placeholders=_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS)

    def test_fails_naming_missing_dynamic_map_leaf(self) -> None:
        settings = walk_schema_settings(_synthetic_schema())
        text = " ".join(
            required_coverage_token(s, placeholders=_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS)
            for s in settings
            if s.dotted_path != "dynamic_map.<entry>.nested_field"
        )
        with pytest.raises(AssertionError, match=re.escape("dynamic_map.<entry>.nested_field")):
            assert_skill_names_every_setting(text, settings, placeholders=_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS)

    def test_dynamic_map_leaf_collision_is_not_satisfied_by_the_bare_word(self) -> None:
        """The seeded control the round-2 code_review finding said was
        missing: a dynamic-map leaf whose bare name ('enabled') collides
        with an unrelated word already present in the SKILL text must NOT
        be satisfied by that unrelated occurrence."""
        settings = walk_schema_settings(_synthetic_schema_with_known_map_collision())
        setting = next(s for s in settings if s.dotted_path == "repos.<entry>.enabled")
        assert required_coverage_token(setting) == "repos.<org/repo>.enabled", (
            "required_coverage_token must render the real repos.<org/repo> placeholder, "
            f"got {required_coverage_token(setting)!r}"
        )
        # 'enabled' appears in this text for an entirely unrelated reason (a
        # sentence about some other boolean toggle), but the full keyed
        # path 'repos.<org/repo>.enabled' never does -- before this fix,
        # required_coverage_token fell back to the bare leaf name 'enabled'
        # for every dynamic-map setting, so this text would have vacuously
        # satisfied coverage.
        colliding_text = "Some unrelated_toggle is enabled by default for a completely different feature."
        with pytest.raises(AssertionError, match=re.escape("repos.<entry>.enabled")):
            assert_skill_names_every_setting(colliding_text, settings)

    def test_dynamic_map_leaf_collision_passes_when_full_keyed_path_present(self) -> None:
        """Control for the previous test: the same colliding bare word
        elsewhere in the text does not prevent a PASS once the full keyed
        path is also genuinely present."""
        settings = walk_schema_settings(_synthetic_schema_with_known_map_collision())
        text = "Ask about repos.<org/repo>.enabled directly, plus the unrelated word enabled elsewhere too."
        assert_skill_names_every_setting(text, settings)  # must not raise


@pytest.mark.unit
class TestSeededUnregisteredDynamicMapRootGuard:
    """Round-5 code_review FAIL_FAST: `required_coverage_token` used to fall
    back to the bare leaf name for a dynamic-map root absent from
    `_DYNAMIC_MAP_PLACEHOLDERS` -- exactly the vacuous, unfalsifiable check
    this module's own docstrings warn against. This class proves the
    strengthened guard is genuinely falsifiable: it raises loudly for an
    UNREGISTERED root (never silently degrading), and it does NOT raise for
    any of the five roots the real `config-schema.json` actually uses
    today."""

    def test_unregistered_dynamic_map_root_raises(self) -> None:
        """`_synthetic_schema()`'s `dynamic_map` root is deliberately absent
        from the PRODUCTION `_DYNAMIC_MAP_PLACEHOLDERS` table (it only
        exists in the test-local `_SYNTHETIC_DYNAMIC_MAP_PLACEHOLDERS`
        override). Calling `required_coverage_token` with the default
        (production) table -- i.e. exactly how `assert_skill_names_every_setting`
        calls it for the real SKILL.md check -- must raise `AssertionError`
        naming the unregistered root and the offending setting path, not
        silently return the bare leaf name 'nested_field'."""
        settings = walk_schema_settings(_synthetic_schema())
        setting = next(s for s in settings if s.dotted_path == "dynamic_map.<entry>.nested_field")
        with pytest.raises(AssertionError) as excinfo:
            required_coverage_token(setting)  # default (production) placeholders -- no override
        message = str(excinfo.value)
        assert "dynamic_map" in message, f"expected the unregistered root 'dynamic_map' in the message: {message}"
        assert "dynamic_map.<entry>.nested_field" in message, (
            f"expected the offending setting path in the message: {message}"
        )

    def test_all_real_schema_dynamic_map_roots_are_registered_and_resolve(self) -> None:
        """Non-raising control: every dynamic-map root the REAL
        `config-schema.json` contains today must already be registered in
        `_DYNAMIC_MAP_PLACEHOLDERS`, so `required_coverage_token` resolves
        each one without raising and without leaving a literal unresolved
        `<entry>` segment in the returned token."""
        settings = walk_schema_settings(_schema())
        dynamic_settings = [s for s in settings if s.in_dynamic_map]
        assert dynamic_settings, "fixture assumption broken: the real schema has no dynamic-map settings"
        real_roots = {_dynamic_map_root(s.dotted_path) for s in dynamic_settings}
        assert real_roots == set(_DYNAMIC_MAP_PLACEHOLDERS), (
            "the real config-schema.json's dynamic-map roots must exactly match "
            f"_DYNAMIC_MAP_PLACEHOLDERS; schema roots={sorted(real_roots)}, "
            f"registered={sorted(_DYNAMIC_MAP_PLACEHOLDERS)}. If a real root is missing, "
            "add it to the table; never reinstate the bare-leaf-name fallback."
        )
        for setting in dynamic_settings:
            token = required_coverage_token(setting)  # must not raise for any real root
            assert "<entry>" not in token, f"expected <entry> fully substituted, got {token!r}"


@pytest.mark.unit
class TestSeededInterviewBlockControls:
    """Proves `parse_interview_blocks` + `assert_interview_blocks_complete`
    are falsifiable: passes on a well-formed synthetic block set, fails
    naming the setting and the specific missing element for each of the
    three required markers, and fails naming the setting when the whole
    block is absent."""

    _STATIC_PATHS: ClassVar[list[str]] = ["alpha", "beta.gamma", "beta.delta"]

    def test_passes_when_every_block_is_complete(self) -> None:
        text = "\n".join(_render_synthetic_block(p) for p in self._STATIC_PATHS)
        blocks = parse_interview_blocks(text)
        assert_interview_blocks_complete(blocks, self._STATIC_PATHS)  # must not raise

    def test_fails_naming_setting_when_block_entirely_missing(self) -> None:
        text = "\n".join(_render_synthetic_block(p) for p in self._STATIC_PATHS if p != "beta.delta")
        blocks = parse_interview_blocks(text)
        with pytest.raises(AssertionError, match=re.escape("beta.delta")):
            assert_interview_blocks_complete(blocks, self._STATIC_PATHS)

    def test_fails_naming_setting_and_missing_recommended_marker(self) -> None:
        text = "\n".join(_render_synthetic_block(p, with_recommended=(p != "beta.gamma")) for p in self._STATIC_PATHS)
        blocks = parse_interview_blocks(text)
        with pytest.raises(AssertionError, match=re.escape("beta.gamma")) as exc_info:
            assert_interview_blocks_complete(blocks, self._STATIC_PATHS)
        assert "**Recommended:**" in str(exc_info.value)

    def test_fails_naming_setting_and_missing_alternatives_marker(self) -> None:
        text = "\n".join(_render_synthetic_block(p, with_alternatives=(p != "alpha")) for p in self._STATIC_PATHS)
        blocks = parse_interview_blocks(text)
        with pytest.raises(AssertionError, match=re.escape("alpha")) as exc_info:
            assert_interview_blocks_complete(blocks, self._STATIC_PATHS)
        assert "**Alternatives:**" in str(exc_info.value)

    def test_fails_naming_setting_and_missing_freeform_marker(self) -> None:
        text = "\n".join(_render_synthetic_block(p, with_freeform=(p != "beta.delta")) for p in self._STATIC_PATHS)
        blocks = parse_interview_blocks(text)
        with pytest.raises(AssertionError, match=re.escape("beta.delta")) as exc_info:
            assert_interview_blocks_complete(blocks, self._STATIC_PATHS)
        assert "**Free-form:**" in str(exc_info.value)

    def test_block_body_stops_at_next_heading_not_bleeding_into_next_block(self) -> None:
        """A block missing a marker must not be rescued by a marker that
        actually belongs to the NEXT block -- proves the parser's body-end
        boundary (next ##/###/#### heading) is respected."""
        text = _render_synthetic_block("alpha", with_freeform=False) + "\n" + _render_synthetic_block("beta.gamma")
        blocks = parse_interview_blocks(text)
        assert "**Free-form:**" not in blocks["alpha"]
        with pytest.raises(AssertionError, match=re.escape("alpha")):
            assert_interview_blocks_complete(blocks, ["alpha", "beta.gamma"])


@pytest.mark.unit
class TestSeededOutputContractControls:
    """Proves `assert_output_contract_validates_before_success` is
    falsifiable: passes on the correct ordering, fails when the anchor is
    absent, and fails when the ordering is reversed."""

    def test_passes_on_correct_order(self) -> None:
        text = f"intro\n{_OUTPUT_CONTRACT_ANCHOR}\nmore text\n{_WRITE_MARKER}\neven more\n{_SUCCESS_MARKER}\n"
        assert_output_contract_validates_before_success(text)  # must not raise

    def test_fails_when_anchor_sentence_absent(self) -> None:
        text = f"intro\n{_WRITE_MARKER}\neven more\n{_SUCCESS_MARKER}\n"
        with pytest.raises(AssertionError, match="output-contract anchor"):
            assert_output_contract_validates_before_success(text)

    def test_fails_when_success_reported_before_validation(self) -> None:
        text = f"intro\n{_SUCCESS_MARKER}\nmore text\n{_WRITE_MARKER}\neven more\n{_OUTPUT_CONTRACT_ANCHOR}\n"
        with pytest.raises(AssertionError, match="out of order"):
            assert_output_contract_validates_before_success(text)

    def test_fails_when_write_marker_absent(self) -> None:
        text = f"intro\n{_OUTPUT_CONTRACT_ANCHOR}\nmore text\n{_SUCCESS_MARKER}\n"
        with pytest.raises(AssertionError, match="file-write instruction"):
            assert_output_contract_validates_before_success(text)
