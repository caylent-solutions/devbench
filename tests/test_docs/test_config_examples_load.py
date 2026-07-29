"""FR-3.8: every shipped example / doc-embedded devbench.yaml round-trips
through ``load_runtime_config``.

Discovers every complete ``devbench.yaml`` example under ``examples/`` plus
the repo's ``sample-config.yaml``, and every fenced ```yaml block in the
FR-3.8-touched docs that represents a complete, independently-loadable
``devbench.yaml`` (identified by a top-level ``repos:`` key -- the JSON
schema's only required field). Each discovered block is round-tripped
through ``load_runtime_config`` so a shipped example or documented snippet
that fails config-load is caught mechanically by this test, rather than
re-discovered by hand for every future doc edit.

A round-trip failure names the offending file path (and, for doc-embedded
blocks, the fenced-block's starting line number) via the pytest
parametrize id AND the failure message, so the next stale example is
diagnosed from the test output alone (AC-E3-F2-S1-T2-4).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from devbench.config_loader import load_runtime_config
from devbench.constants import BEDROCK_AGENT_MODEL_PATTERN, DEFAULT_MODEL_RATES

REPO_ROOT = Path(__file__).resolve().parents[2]

# The FR-3.8 touched docs: every doc repaired by this work unit that could
# plausibly embed a complete, loadable devbench.yaml example.
_TOUCHED_DOC_PATHS: tuple[Path, ...] = (
    REPO_ROOT / "docs" / "devbench-yaml-reference.md",
    REPO_ROOT / "docs" / "architecture.md",
    REPO_ROOT / "docs" / "plugin-architecture.md",
    REPO_ROOT / "docs" / "multi-session-runs.md",
    REPO_ROOT / "docs" / "adr" / "01-claude-agent-sdk-with-plugins.md",
)

_YAML_FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
_TOP_LEVEL_REPOS_RE = re.compile(r"^repos:", re.MULTILINE)


class ConfigBlock(NamedTuple):
    """One discovered devbench.yaml-shaped config, identified by its source location."""

    label: str
    yaml_text: str


def _discover_example_config_files() -> list[ConfigBlock]:
    """Every complete devbench.yaml example under examples/, plus sample-config.yaml."""
    discovered = [
        ConfigBlock(label=str(path.relative_to(REPO_ROOT)), yaml_text=path.read_text(encoding="utf-8"))
        for path in sorted((REPO_ROOT / "examples").rglob("devbench.yaml"))
    ]
    sample_config = REPO_ROOT / "sample-config.yaml"
    discovered.append(
        ConfigBlock(
            label=str(sample_config.relative_to(REPO_ROOT)),
            yaml_text=sample_config.read_text(encoding="utf-8"),
        )
    )
    return discovered


def _discover_doc_config_blocks() -> list[ConfigBlock]:
    """Every fenced ```yaml block in the touched docs that is a complete,
    independently-loadable devbench.yaml.

    A block qualifies when it declares a top-level ``repos:`` key -- the
    JSON schema's only required field (``src/devbench/config-schema.json``).
    Partial snippets (e.g. a bare ``report:`` fragment shown for illustration)
    are not independently loadable and are excluded from this discovery so
    the round-trip check only applies where the doc claims a complete example.
    """
    discovered: list[ConfigBlock] = []
    for doc_path in _TOUCHED_DOC_PATHS:
        text = doc_path.read_text(encoding="utf-8")
        for match in _YAML_FENCE_RE.finditer(text):
            block_text = match.group(1)
            if not _TOP_LEVEL_REPOS_RE.search(block_text):
                continue
            start_line = text[: match.start()].count("\n") + 1
            label = f"{doc_path.relative_to(REPO_ROOT)}:{start_line}"
            discovered.append(ConfigBlock(label=label, yaml_text=block_text))
    return discovered


def _all_config_blocks() -> list[ConfigBlock]:
    return _discover_example_config_files() + _discover_doc_config_blocks()


_CONFIG_BLOCKS = _all_config_blocks()


@pytest.mark.unit
class TestConfigExampleDiscovery:
    """Guards against the parametrized round-trip test below silently collecting
    zero cases (a discovery-regex regression would otherwise pass vacuously)."""

    def test_discovers_at_least_two_example_config_files(self) -> None:
        """
        Given: the examples/ tree and sample-config.yaml
        When: example-config discovery runs
        Then: at least the one shipped brownfield example plus sample-config.yaml
        are found
        """
        example_files = _discover_example_config_files()
        assert len(example_files) >= 2, (
            f"Expected >=2 example config files (examples/**/devbench.yaml + "
            f"sample-config.yaml), found {len(example_files)}: "
            f"{[b.label for b in example_files]}"
        )

    def test_discovers_at_least_one_complete_doc_embedded_block(self) -> None:
        """
        Given: the FR-3.8-touched docs
        When: doc-embedded config-block discovery runs
        Then: at least one complete (repos:-bearing) block is found
        """
        doc_blocks = _discover_doc_config_blocks()
        assert len(doc_blocks) >= 1, (
            f"Expected >=1 complete doc-embedded devbench.yaml block across "
            f"{[str(p.relative_to(REPO_ROOT)) for p in _TOUCHED_DOC_PATHS]}, "
            f"found {len(doc_blocks)}"
        )


@pytest.mark.unit
class TestConfigExamplesRoundTripLoadRuntimeConfig:
    """FR-3.8: every discovered example/doc-embedded config round-trips through
    load_runtime_config. A shipped or documented config example that fails
    config-load is a defect (AC-E3-F2-S1-T2-2, AC-E3-F2-S1-T2-3)."""

    @pytest.mark.parametrize("block", _CONFIG_BLOCKS, ids=[b.label for b in _CONFIG_BLOCKS])
    def test_config_block_loads_successfully(self, block: ConfigBlock, tmp_path: Path) -> None:
        """
        Given: a shipped example config file or a complete doc-embedded
        devbench.yaml block, identified by ``block.label``
        When: the block is written to a temp file and round-tripped through
        load_runtime_config
        Then: it loads without raising -- a shipped/documented example that
        fails config-load is a defect this test catches mechanically
        """
        tmp_config_path = tmp_path / "devbench.yaml"
        tmp_config_path.write_text(block.yaml_text, encoding="utf-8")

        try:
            load_runtime_config(tmp_config_path, {})
        except (ValueError, FileNotFoundError) as exc:
            pytest.fail(f"Config block '{block.label}' failed config-load: {exc}")


# ---------------------------------------------------------------------------
# AC-E3-F2-S1-T2-7: retired report.token_cost_per_million_*/token_cost_discount
# keys must not remain as live YAML guidance in any touched doc or the repaired
# example config. Historical/migration prose (e.g. "were retired in issue
# #223") lives outside a fenced ```yaml block and is explicitly permitted.
# ---------------------------------------------------------------------------

_EXAMPLE_CONFIG_PATH = (
    REPO_ROOT / "examples/backlogs/brownfield/multi-repo_single-pr_no-merge/before/backlog/config/devbench.yaml"
)

_RETIRED_REPORT_KEYS: tuple[str, ...] = (
    "token_cost_per_million_input",
    "token_cost_per_million_output",
    "token_cost_discount",
)
_RETIRED_KEY_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(key) for key in _RETIRED_REPORT_KEYS) + r")\s*:",
    re.MULTILINE,
)


def _yaml_fence_blocks(text: str) -> list[str]:
    """Every fenced ```yaml block's raw content, regardless of shape."""
    return [match.group(1) for match in _YAML_FENCE_RE.finditer(text)]


@pytest.mark.unit
class TestRetiredReportKeysAreNotLiveGuidance:
    """AC-E3-F2-S1-T2-7: the retired report.* keys must not appear as a live YAML
    key inside a fenced config example in any touched doc, nor in the repaired
    example config. A doc that shows the retired keys as a config example
    (rather than mentioning them only in migration prose outside a yaml fence)
    is a defect this test catches mechanically."""

    @pytest.mark.parametrize(
        "doc_path",
        _TOUCHED_DOC_PATHS,
        ids=[str(p.relative_to(REPO_ROOT)) for p in _TOUCHED_DOC_PATHS],
    )
    def test_touched_doc_has_no_retired_report_key_in_yaml_fence(self, doc_path: Path) -> None:
        text = doc_path.read_text(encoding="utf-8")
        for block in _yaml_fence_blocks(text):
            match = _RETIRED_KEY_RE.search(block)
            assert match is None, (
                f"{doc_path.relative_to(REPO_ROOT)} still shows the retired key "
                f"'{match.group(1) if match else ''}' as live YAML guidance inside a "
                "fenced config example (AC-E3-F2-S1-T2-7); only prose migration notes "
                "outside a yaml fence are permitted."
            )

    def test_example_config_has_no_retired_report_key(self) -> None:
        text = _EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")
        match = _RETIRED_KEY_RE.search(text)
        assert match is None, (
            f"{_EXAMPLE_CONFIG_PATH.relative_to(REPO_ROOT)} still sets the retired key "
            f"'{match.group(1) if match else ''}' (AC-E3-F2-S1-T2-7)."
        )


# ---------------------------------------------------------------------------
# AC-E3-F2-S1-T2-5: docs/plugin-architecture.md's Model Per Role table must
# match the model value shipped in each agent's own frontmatter.
# ---------------------------------------------------------------------------

_AGENTS_DIR = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents"
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
_FRONTMATTER_MODEL_RE = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE)
_MODEL_ROLE_TABLE_ROW_RE = re.compile(r"^\|\s*((?:`[\w.-]+\.md`(?:,\s*)?)+)\s*\|\s*(\w+)\s*\|", re.MULTILINE)
_AGENT_FILENAME_RE = re.compile(r"`([\w.-]+\.md)`")


def _shipped_agent_models() -> dict[str, str]:
    """Agent filename -> model value, read directly from each agent's own
    frontmatter under plugin/devbench-orchestrate/agents/**/*.md."""
    models: dict[str, str] = {}
    for agent_path in sorted(_AGENTS_DIR.rglob("*.md")):
        frontmatter_match = _FRONTMATTER_RE.match(agent_path.read_text(encoding="utf-8"))
        assert frontmatter_match is not None, f"{agent_path} has no leading YAML frontmatter block"
        model_match = _FRONTMATTER_MODEL_RE.search(frontmatter_match.group(1))
        assert model_match is not None, f"{agent_path} frontmatter has no 'model:' field"
        models[agent_path.name] = model_match.group(1)
    return models


def _model_per_role_table_rows() -> list[tuple[str, str]]:
    """(agent filename, documented model) pairs parsed from the Model Per Role
    table in docs/plugin-architecture.md."""
    text = (REPO_ROOT / "docs" / "plugin-architecture.md").read_text(encoding="utf-8")
    heading_index = text.index("## Model Per Role")
    next_heading_index = text.index("\n## ", heading_index + 1)
    table_text = text[heading_index:next_heading_index]
    pairs: list[tuple[str, str]] = []
    for row_match in _MODEL_ROLE_TABLE_ROW_RE.finditer(table_text):
        agents_cell, model = row_match.group(1), row_match.group(2)
        for agent_name in _AGENT_FILENAME_RE.findall(agents_cell):
            pairs.append((agent_name, model))
    return pairs


_MODEL_ROLE_ROWS = _model_per_role_table_rows()


@pytest.mark.unit
class TestPluginArchitectureModelPerRoleTableMatchesShippedFrontmatter:
    """AC-E3-F2-S1-T2-1, AC-E3-F2-S1-T2-5: docs/plugin-architecture.md's Model
    Per Role table must match the model value actually shipped in each agent's
    own frontmatter -- a stale table entry (e.g. the removed Haiku-judge rows,
    or a mischaracterized role like a claimed 'fan-out coordinator' for a
    non-spawning aggregator) is a defect."""

    def test_table_has_at_least_five_documented_agents(self) -> None:
        assert len(_MODEL_ROLE_ROWS) >= 5, (
            f"Expected >=5 documented agent rows, parsed {len(_MODEL_ROLE_ROWS)}: {_MODEL_ROLE_ROWS}"
        )

    @pytest.mark.parametrize(
        "agent_name,documented_model",
        _MODEL_ROLE_ROWS,
        ids=[f"{name}={model}" for name, model in _MODEL_ROLE_ROWS],
    )
    def test_documented_model_matches_shipped_frontmatter(self, agent_name: str, documented_model: str) -> None:
        shipped_models = _shipped_agent_models()
        assert agent_name in shipped_models, (
            f"docs/plugin-architecture.md documents '{agent_name}' but no matching agent "
            f"file was found under {_AGENTS_DIR.relative_to(REPO_ROOT)}"
        )
        assert shipped_models[agent_name] == documented_model, (
            f"docs/plugin-architecture.md's Model Per Role table says '{agent_name}' uses "
            f"model '{documented_model}', but the shipped frontmatter sets "
            f"model: {shipped_models[agent_name]}"
        )


# ---------------------------------------------------------------------------
# AC-E3-F2-S1-T2-1, AC-E3-F2-S1-T2-5: every inline frontmatter example block
# embedded in docs/plugin-architecture.md and
# docs/adr/01-claude-agent-sdk-with-plugins.md (a fenced ```markdown block
# whose content opens with a '---' YAML frontmatter declaring both name: and
# model:) must match the shipped agent's own frontmatter model, wherever the
# example's name: matches a shipped agent filename. One shared helper covers
# both doc targets (DRY) so neither doc's illustrative frontmatter snippet
# can go stale (e.g. reverting to a removed haiku judge example) with the
# Model Per Role table check above -- which only covers the table, not these
# inline examples -- still green.
# ---------------------------------------------------------------------------

_MARKDOWN_FENCE_RE = re.compile(r"```markdown\n(.*?)```", re.DOTALL)
_FRONTMATTER_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


class FrontmatterExample(NamedTuple):
    """One inline frontmatter example discovered in a doc, identified by
    source location, the agent name it claims to illustrate, and the model
    it documents for that agent."""

    doc_label: str
    agent_name: str
    documented_model: str


def _frontmatter_examples_in_doc(doc_path: Path) -> list[FrontmatterExample]:
    """Every embedded frontmatter example in doc_path: a fenced ```markdown
    block whose content opens with a '---' YAML frontmatter block declaring
    both name: and model:. Reuses the same _FRONTMATTER_RE / _FRONTMATTER_MODEL_RE
    helpers that parse the real shipped agent files, so both sides of the
    comparison are extracted identically."""
    text = doc_path.read_text(encoding="utf-8")
    examples: list[FrontmatterExample] = []
    for fence_match in _MARKDOWN_FENCE_RE.finditer(text):
        block = fence_match.group(1)
        frontmatter_match = _FRONTMATTER_RE.match(block)
        if frontmatter_match is None:
            continue
        frontmatter_text = frontmatter_match.group(1)
        name_match = _FRONTMATTER_NAME_RE.search(frontmatter_text)
        model_match = _FRONTMATTER_MODEL_RE.search(frontmatter_text)
        if name_match is None or model_match is None:
            continue
        start_line = text[: fence_match.start()].count("\n") + 1
        examples.append(
            FrontmatterExample(
                doc_label=f"{doc_path.relative_to(REPO_ROOT)}:{start_line}",
                agent_name=name_match.group(1),
                documented_model=model_match.group(1),
            )
        )
    return examples


_FRONTMATTER_EXAMPLE_DOCS: tuple[Path, ...] = (
    REPO_ROOT / "docs" / "plugin-architecture.md",
    REPO_ROOT / "docs" / "adr" / "01-claude-agent-sdk-with-plugins.md",
)
_FRONTMATTER_EXAMPLES: list[FrontmatterExample] = [
    example for doc_path in _FRONTMATTER_EXAMPLE_DOCS for example in _frontmatter_examples_in_doc(doc_path)
]


@pytest.mark.unit
class TestFrontmatterExamplesMatchShippedAgents:
    """AC-E3-F2-S1-T2-1, AC-E3-F2-S1-T2-5: every inline frontmatter example
    embedded in docs/plugin-architecture.md and
    docs/adr/01-claude-agent-sdk-with-plugins.md must declare the same model
    as the matching shipped agent's own frontmatter -- a stale example (e.g.
    a reverted haiku judge example, or a routing narrative that no longer
    matches shipped reality) is a defect this test catches mechanically."""

    def test_discovers_at_least_two_frontmatter_examples(self) -> None:
        assert len(_FRONTMATTER_EXAMPLES) >= 2, (
            "Expected >=2 embedded frontmatter examples across "
            f"{[str(p.relative_to(REPO_ROOT)) for p in _FRONTMATTER_EXAMPLE_DOCS]}, "
            f"found {len(_FRONTMATTER_EXAMPLES)}"
        )

    @pytest.mark.parametrize(
        "example",
        _FRONTMATTER_EXAMPLES,
        ids=[f"{e.doc_label}:{e.agent_name}={e.documented_model}" for e in _FRONTMATTER_EXAMPLES],
    )
    def test_frontmatter_example_matches_shipped_agent_model(self, example: FrontmatterExample) -> None:
        shipped_models = _shipped_agent_models()
        agent_filename = f"{example.agent_name}.md"
        assert agent_filename in shipped_models, (
            f"{example.doc_label} shows a frontmatter example for '{example.agent_name}' but no "
            f"matching agent file was found under {_AGENTS_DIR.relative_to(REPO_ROOT)}"
        )
        assert shipped_models[agent_filename] == example.documented_model, (
            f"{example.doc_label} shows a frontmatter example for '{example.agent_name}' with "
            f"model '{example.documented_model}', but the shipped frontmatter sets "
            f"model: {shipped_models[agent_filename]}"
        )


# ---------------------------------------------------------------------------
# AC-E3-F2-S1-T2-5: docs/plugin-architecture.md must retain the #198 ban
# citation and its unconditional wording. The Model Per Role table check
# above only covers the table; it enforces nothing about this separate prose
# paragraph, which round-2 review proved could be deleted outright, or have
# its unconditional wording softened, with the full suite still green.
# ---------------------------------------------------------------------------

_PLUGIN_ARCHITECTURE_PATH = REPO_ROOT / "docs" / "plugin-architecture.md"
_HAIKU_BAN_ISSUE_CITATION = "caylent-solutions/devbench#198"


def _normalize_whitespace(text: str) -> str:
    """Collapse all runs of whitespace (including newlines from markdown
    line-wrapping) to a single space, so a prose assertion can match text
    that spans a hard line-wrap without being sensitive to exactly where the
    wrap falls."""
    return re.sub(r"\s+", " ", text)


@pytest.mark.unit
class TestPluginArchitectureRetainsHaikuBanRationale:
    """AC-E3-F2-S1-T2-5: the #198 ban citation and its unconditional wording
    must remain present in docs/plugin-architecture.md. Softening the
    rationale or deleting the citation is a defect distinct from (and not
    caught by) the Model Per Role table or frontmatter-example checks above."""

    def test_haiku_ban_issue_citation_present(self) -> None:
        text = _PLUGIN_ARCHITECTURE_PATH.read_text(encoding="utf-8")
        assert _HAIKU_BAN_ISSUE_CITATION in text, (
            f"docs/plugin-architecture.md no longer cites '{_HAIKU_BAN_ISSUE_CITATION}' for the "
            "Haiku ban (AC-E3-F2-S1-T2-5)."
        )

    def test_haiku_ban_is_stated_unconditional(self) -> None:
        text = _normalize_whitespace(_PLUGIN_ARCHITECTURE_PATH.read_text(encoding="utf-8"))
        assert "this ban is unconditional" in text.lower(), (
            "docs/plugin-architecture.md no longer states the Haiku ban is unconditional "
            "(AC-E3-F2-S1-T2-5); the ban rationale must not be softened."
        )


# ---------------------------------------------------------------------------
# AC-E3-F2-S1-T2-6: every DEVBENCH_CLAUDE_MODEL exported in
# docs/multi-session-runs.md must be a real id accepted by the Bedrock
# model-id pattern (spec S1.7) AND must name a currently-published model
# (BEDROCK_AGENT_MODEL_PATTERN validates shape only -- a syntactically valid
# but nonexistent id like the retired sonnet-4-7 example still matches it, so
# shape-match alone cannot catch that regression class).
# ---------------------------------------------------------------------------

_MODEL_EXPORT_RE = re.compile(r"export DEVBENCH_CLAUDE_MODEL=(\S+)")
_BEDROCK_ID_SHORT_NAME_RE = re.compile(r"^us\.anthropic\.(claude-[a-z0-9-]+)-v[0-9]+$")


def _multi_session_runs_model_exports() -> list[str]:
    text = (REPO_ROOT / "docs" / "multi-session-runs.md").read_text(encoding="utf-8")
    return _MODEL_EXPORT_RE.findall(text)


_MULTI_SESSION_MODEL_EXPORTS = _multi_session_runs_model_exports()


@pytest.mark.unit
class TestMultiSessionRunsModelIdsAreRealBedrockIds:
    """AC-E3-F2-S1-T2-6: every DEVBENCH_CLAUDE_MODEL exported in
    docs/multi-session-runs.md must satisfy BEDROCK_AGENT_MODEL_PATTERN AND
    name a currently-published model id (a member of constants.DEFAULT_MODEL_RATES,
    the source of truth for the current lineup); a nonexistent id (like the
    retired sonnet-4-7 example, which is shape-valid but was never published)
    is a defect."""

    def test_at_least_one_model_export_found(self) -> None:
        assert len(_MULTI_SESSION_MODEL_EXPORTS) >= 1, (
            "Expected at least one 'export DEVBENCH_CLAUDE_MODEL=' line in docs/multi-session-runs.md"
        )

    @pytest.mark.parametrize(
        "model_id",
        _MULTI_SESSION_MODEL_EXPORTS,
        ids=_MULTI_SESSION_MODEL_EXPORTS,
    )
    def test_model_export_matches_bedrock_pattern(self, model_id: str) -> None:
        assert BEDROCK_AGENT_MODEL_PATTERN.match(model_id), (
            f"docs/multi-session-runs.md exports DEVBENCH_CLAUDE_MODEL={model_id!r}, which "
            "does not match constants.BEDROCK_AGENT_MODEL_PATTERN -- this id is not even "
            "shape-valid for the current Bedrock lineup."
        )

    @pytest.mark.parametrize(
        "model_id",
        _MULTI_SESSION_MODEL_EXPORTS,
        ids=_MULTI_SESSION_MODEL_EXPORTS,
    )
    def test_model_export_names_a_currently_published_model(self, model_id: str) -> None:
        short_name_match = _BEDROCK_ID_SHORT_NAME_RE.match(model_id)
        assert short_name_match is not None, (
            f"docs/multi-session-runs.md exports DEVBENCH_CLAUDE_MODEL={model_id!r}, which "
            "does not have the expected 'us.anthropic.claude-<name>-v<N>' shape."
        )
        short_name = short_name_match.group(1)
        assert short_name in DEFAULT_MODEL_RATES, (
            f"docs/multi-session-runs.md exports DEVBENCH_CLAUDE_MODEL={model_id!r} (short "
            f"name '{short_name}'), which is not a currently-published model in "
            "constants.DEFAULT_MODEL_RATES -- this id does not exist in the current lineup."
        )


# ---------------------------------------------------------------------------
# AC-E3-F2-S1-T2-7 (mutation-check gap closure): bare `report:` fragments
# (partial snippets excluded from the complete-config discovery above because
# they have no top-level `repos:` key) are merged onto a minimal complete
# base config and round-tripped through load_runtime_config, so the retired
# keys can no longer be reintroduced into e.g.
# docs/devbench-yaml-reference.md's report: block with zero test signal.
# ---------------------------------------------------------------------------

_TOP_LEVEL_REPORT_RE = re.compile(r"^report:", re.MULTILINE)
_MINIMAL_BASE_CONFIG = "repos:\n  example-org/example-repo: {}\n"


def _discover_report_fragment_blocks() -> list[ConfigBlock]:
    discovered: list[ConfigBlock] = []
    for doc_path in _TOUCHED_DOC_PATHS:
        text = doc_path.read_text(encoding="utf-8")
        for match in _YAML_FENCE_RE.finditer(text):
            block_text = match.group(1)
            if _TOP_LEVEL_REPOS_RE.search(block_text):
                continue  # already a complete config, covered by _CONFIG_BLOCKS
            if not _TOP_LEVEL_REPORT_RE.search(block_text):
                continue
            start_line = text[: match.start()].count("\n") + 1
            label = f"{doc_path.relative_to(REPO_ROOT)}:{start_line}"
            discovered.append(ConfigBlock(label=label, yaml_text=_MINIMAL_BASE_CONFIG + block_text))
    return discovered


_REPORT_FRAGMENT_BLOCKS = _discover_report_fragment_blocks()


@pytest.mark.unit
class TestReportFragmentBlocksRoundTripWhenMergedOntoMinimalConfig:
    """AC-E3-F2-S1-T2-7: bare `report:` fragment doc examples, merged onto a
    minimal complete `repos:` base, still round-trip through
    load_runtime_config."""

    def test_discovers_at_least_one_report_fragment(self) -> None:
        assert len(_REPORT_FRAGMENT_BLOCKS) >= 1, (
            "Expected >=1 bare 'report:' fragment across "
            f"{[str(p.relative_to(REPO_ROOT)) for p in _TOUCHED_DOC_PATHS]}, "
            f"found {len(_REPORT_FRAGMENT_BLOCKS)}"
        )

    @pytest.mark.parametrize(
        "block",
        _REPORT_FRAGMENT_BLOCKS,
        ids=[b.label for b in _REPORT_FRAGMENT_BLOCKS],
    )
    def test_report_fragment_loads_when_merged_onto_minimal_config(self, block: ConfigBlock, tmp_path: Path) -> None:
        tmp_config_path = tmp_path / "devbench.yaml"
        tmp_config_path.write_text(block.yaml_text, encoding="utf-8")
        try:
            load_runtime_config(tmp_config_path, {})
        except (ValueError, FileNotFoundError) as exc:
            pytest.fail(
                f"report: fragment '{block.label}' failed config-load when merged onto a minimal base config: {exc}"
            )
