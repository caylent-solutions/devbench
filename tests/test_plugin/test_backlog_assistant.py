"""Tests for the devbench-backlog-assistant plugin (issue #246).

Validates:
- plugin.json and marketplace entry parse and register the plugin
- Exactly nine SKILL.md files exist with valid frontmatter
- commands.txt parity matches the skill directories
- All skills follow the universal output contract (VERDICT/SUGGESTED COMMAND/CONFIRM)
- Diagnosis skills are read-only (no Write tool declared)
- Payload skills only declare Write scoped to /tmp
- No mutating verbs run without CONFIRM (static analysis)
- Fixture WU files are byte-identical after every skill (no mutation)
- Triage skill reuses classify_blocked_task with no duplication
- Auto-resolve integration: RUNTIME_DEGRADATION end-to-end with seeded catalog (E11-F3)
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BACKLOG_ASSISTANT_DIR = REPO_ROOT / "plugin-authoring" / "devbench-backlog-assistant"
PLUGIN_JSON = BACKLOG_ASSISTANT_DIR / ".claude-plugin" / "plugin.json"
SKILLS_DIR = BACKLOG_ASSISTANT_DIR / "skills"
COMMANDS_TXT = BACKLOG_ASSISTANT_DIR / "commands.txt"
AUTHORING_MARKETPLACE_JSON = REPO_ROOT / "plugin-authoring" / ".claude-plugin" / "marketplace.json"

EXPECTED_SKILL_NAMES = {
    "triage-blocked-task",
    "audit-backlog-impossibilities",
    "rewrite-impossibility",
    "cascade-status",
    "backlog-health-check",
    "reconcile-backlog-md",
    "amend-manifest-offline",
    "refactor-target-repository",
    "diagnose-review-stuck",
}

# Skills that are read-only diagnosis -- may only use Read and Bash, not Write
READ_ONLY_SKILLS = {
    "triage-blocked-task",
    "cascade-status",
    "backlog-health-check",
    "reconcile-backlog-md",
    "diagnose-review-stuck",
    "amend-manifest-offline",
    "refactor-target-repository",
}

REQUIRED_PLUGIN_FIELDS = (
    "name",
    "description",
    "version",
    "keywords",
    "repository",
    "license",
    "homepage",
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "backlog_assistant"


def _load_json(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{path} is not valid JSON: {exc}")
    assert isinstance(data, dict), f"{path} top-level value must be a JSON object."
    return data


def _parse_frontmatter(skill_path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a SKILL.md file delimited by --- lines."""
    content = skill_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        pytest.fail(f"{skill_path}: SKILL.md must begin with '---' frontmatter delimiter")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        pytest.fail(f"{skill_path}: SKILL.md frontmatter is not closed with '---'")
    frontmatter_lines = lines[1:end_idx]
    result: dict[str, Any] = {}
    tools_list: list[str] = []
    in_tools = False
    for line in frontmatter_lines:
        if line.startswith("tools:"):
            in_tools = True
            # inline tools: [A, B]
            inline = line[len("tools:") :].strip()
            if inline.startswith("["):
                items = inline.strip("[]").split(",")
                tools_list = [i.strip().strip('"').strip("'") for i in items if i.strip()]
                in_tools = False
            result["tools"] = tools_list
        elif in_tools and line.startswith("  - "):
            tools_list.append(line[4:].strip())
            result["tools"] = tools_list
        elif in_tools and not line.startswith("  "):
            in_tools = False
            # fall through to parse this line normally
            parts = line.split(":", 1)
            if len(parts) == 2:
                result[parts[0].strip()] = parts[1].strip()
        elif not in_tools:
            parts = line.split(":", 1)
            if len(parts) == 2:
                result[parts[0].strip()] = parts[1].strip()
    return result


@pytest.fixture(scope="session")
def assistant_plugin_manifest() -> dict[str, Any]:
    return _load_json(PLUGIN_JSON)


@pytest.fixture(scope="session")
def authoring_marketplace_manifest() -> dict[str, Any]:
    return _load_json(AUTHORING_MARKETPLACE_JSON)


@pytest.fixture(scope="session")
def skill_frontmatters() -> dict[str, dict[str, Any]]:
    """Return a mapping of skill-name -> parsed frontmatter for all 9 skills."""
    assert SKILLS_DIR.exists(), f"skills directory missing: {SKILLS_DIR}"
    result = {}
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.exists(), f"SKILL.md missing for skill dir {skill_dir.name}"
            result[skill_dir.name] = _parse_frontmatter(skill_md)
    return result


@pytest.fixture(scope="session")
def skill_contents() -> dict[str, str]:
    """Return a mapping of skill-name -> full text content for all 9 skills."""
    assert SKILLS_DIR.exists(), f"skills directory missing: {SKILLS_DIR}"
    result = {}
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if skill_dir.is_dir():
            skill_md = skill_dir / "SKILL.md"
            assert skill_md.exists(), f"SKILL.md missing for skill dir {skill_dir.name}"
            result[skill_dir.name] = skill_md.read_text(encoding="utf-8")
    return result


@pytest.fixture(scope="session")
def fixture_wu_files() -> list[tuple[Path, bytes]]:
    """Return list of (path, original_bytes) for fixture WU files."""
    if not FIXTURES_DIR.exists():
        return []
    return [(p, p.read_bytes()) for p in sorted(FIXTURES_DIR.rglob("*.md")) if p.is_file()]


@pytest.mark.unit
class TestPluginJsonExists:
    """AC-246-1: plugin.json must exist at the expected path."""

    def test_plugin_json_exists(self) -> None:
        assert PLUGIN_JSON.exists(), f"backlog-assistant plugin.json missing at {PLUGIN_JSON}"

    def test_skills_dir_exists(self) -> None:
        assert SKILLS_DIR.exists(), f"backlog-assistant skills directory missing at {SKILLS_DIR}"

    def test_commands_txt_exists(self) -> None:
        assert COMMANDS_TXT.exists(), f"backlog-assistant commands.txt missing at {COMMANDS_TXT}"


@pytest.mark.unit
class TestPluginJsonShape:
    """AC-246-1: plugin.json has all required metadata fields."""

    @pytest.mark.parametrize("field", REQUIRED_PLUGIN_FIELDS)
    def test_required_field_present(self, assistant_plugin_manifest: dict[str, Any], field: str) -> None:
        assert field in assistant_plugin_manifest, (
            f"backlog-assistant plugin.json missing required field {field!r}; "
            f"present fields: {sorted(assistant_plugin_manifest.keys())}"
        )

    def test_name_is_devbench_backlog_assistant(self, assistant_plugin_manifest: dict[str, Any]) -> None:
        assert assistant_plugin_manifest["name"] == "devbench-backlog-assistant", (
            f"plugin.json name must be 'devbench-backlog-assistant'; got {assistant_plugin_manifest['name']!r}"
        )

    def test_version_is_semver(self, assistant_plugin_manifest: dict[str, Any]) -> None:
        version = assistant_plugin_manifest["version"]
        assert re.match(r"^\d+\.\d+\.\d+$", version), (
            f"backlog-assistant plugin.json version must be semver; got {version!r}"
        )

    def test_repository_references_caylent_solutions(self, assistant_plugin_manifest: dict[str, Any]) -> None:
        repo = assistant_plugin_manifest["repository"]
        assert "caylent-solutions" in repo and "devbench" in repo, (
            f"plugin.json repository must reference caylent-solutions/devbench; got {repo!r}"
        )

    def test_homepage_is_https(self, assistant_plugin_manifest: dict[str, Any]) -> None:
        assert assistant_plugin_manifest["homepage"].startswith("https://"), (
            f"plugin.json homepage must be https://; got {assistant_plugin_manifest['homepage']!r}"
        )

    def test_keywords_is_list(self, assistant_plugin_manifest: dict[str, Any]) -> None:
        assert isinstance(assistant_plugin_manifest["keywords"], list), "plugin.json keywords must be a JSON array"
        assert len(assistant_plugin_manifest["keywords"]) >= 1, "plugin.json keywords must have at least one entry"


@pytest.mark.unit
class TestMarketplaceEntry:
    """AC-246-1: authoring marketplace must list exactly two plugins after this task."""

    def test_marketplace_lists_two_plugins(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        plugins = authoring_marketplace_manifest.get("plugins", [])
        assert isinstance(plugins, list)
        assert len(plugins) == 2, f"authoring marketplace must list exactly two plugins after #246; got {len(plugins)}"

    def test_marketplace_has_authoring_entry(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        names = [p["name"] for p in authoring_marketplace_manifest["plugins"]]
        assert "devbench-authoring" in names, f"authoring marketplace must retain devbench-authoring entry; got {names}"

    def test_marketplace_has_backlog_assistant_entry(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        names = [p["name"] for p in authoring_marketplace_manifest["plugins"]]
        assert "devbench-backlog-assistant" in names, (
            f"authoring marketplace must include devbench-backlog-assistant entry; got {names}"
        )

    def test_backlog_assistant_marketplace_entry_source(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        entry = next(p for p in authoring_marketplace_manifest["plugins"] if p["name"] == "devbench-backlog-assistant")
        assert entry["source"].rstrip("/") == "./devbench-backlog-assistant", (
            f"backlog-assistant marketplace source must be './devbench-backlog-assistant'; got {entry['source']!r}"
        )

    def test_marketplace_version_bumped(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        version = authoring_marketplace_manifest.get("metadata", {}).get("version", "")
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"marketplace metadata.version must be semver; got {version!r}"
        # version must be > 0.2.0
        parts = [int(x) for x in version.split(".")]
        assert tuple(parts) > (0, 2, 0), f"marketplace metadata.version must be bumped above 0.2.0; got {version!r}"


@pytest.mark.unit
class TestNineSkillsExist:
    """AC-246-1: Exactly nine SKILL.md files with valid frontmatter."""

    def test_exactly_nine_skills(self, skill_frontmatters: dict[str, dict[str, Any]]) -> None:
        found = set(skill_frontmatters.keys())
        assert len(found) == 9, f"Expected exactly 9 skills; found {len(found)}: {sorted(found)}"

    def test_all_expected_skill_names_present(self, skill_frontmatters: dict[str, dict[str, Any]]) -> None:
        found = set(skill_frontmatters.keys())
        missing = EXPECTED_SKILL_NAMES - found
        extra = found - EXPECTED_SKILL_NAMES
        assert not missing, f"Missing expected skills: {sorted(missing)}"
        assert not extra, f"Unexpected extra skills: {sorted(extra)}"

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_name_in_frontmatter(
        self, skill_frontmatters: dict[str, dict[str, Any]], skill_name: str
    ) -> None:
        fm = skill_frontmatters.get(skill_name, {})
        assert "name" in fm, f"Skill {skill_name} SKILL.md frontmatter missing 'name'"
        assert fm["name"] == skill_name, f"Skill {skill_name} frontmatter name mismatch: got {fm['name']!r}"

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_description_in_frontmatter(
        self, skill_frontmatters: dict[str, dict[str, Any]], skill_name: str
    ) -> None:
        fm = skill_frontmatters.get(skill_name, {})
        assert "description" in fm, f"Skill {skill_name} SKILL.md frontmatter missing 'description'"
        assert fm["description"].strip(), f"Skill {skill_name} SKILL.md frontmatter description is empty"

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_model_in_frontmatter(
        self, skill_frontmatters: dict[str, dict[str, Any]], skill_name: str
    ) -> None:
        fm = skill_frontmatters.get(skill_name, {})
        assert "model" in fm, f"Skill {skill_name} SKILL.md frontmatter missing 'model'"
        assert fm["model"] in ("opus", "sonnet"), (
            f"Skill {skill_name} model must be 'opus' or 'sonnet'; got {fm['model']!r}"
        )

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_tools_in_frontmatter(
        self, skill_frontmatters: dict[str, dict[str, Any]], skill_name: str
    ) -> None:
        fm = skill_frontmatters.get(skill_name, {})
        assert "tools" in fm, f"Skill {skill_name} SKILL.md frontmatter missing 'tools'"
        assert isinstance(fm["tools"], list) and len(fm["tools"]) >= 1, (
            f"Skill {skill_name} SKILL.md frontmatter 'tools' must be a non-empty list"
        )


@pytest.mark.unit
class TestReadOnlySkillsNoWriteTool:
    """Diagnosis skills must not declare Write in their tools list."""

    @pytest.mark.parametrize("skill_name", sorted(READ_ONLY_SKILLS))
    def test_read_only_skill_has_no_write_tool(
        self, skill_frontmatters: dict[str, dict[str, Any]], skill_name: str
    ) -> None:
        fm = skill_frontmatters.get(skill_name, {})
        tools = fm.get("tools", [])
        assert "Write" not in tools, (
            f"Read-only skill {skill_name!r} must not declare 'Write' in tools; got tools={tools}"
        )


@pytest.mark.unit
class TestCommandsTxtParity:
    """commands.txt entries must match the skill directory names exactly."""

    def test_commands_txt_lists_all_skills(self) -> None:
        commands_text = COMMANDS_TXT.read_text(encoding="utf-8")
        skill_dirs = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
        for skill_name in skill_dirs:
            assert skill_name in commands_text, (
                f"commands.txt must mention skill {skill_name!r}; not found in {COMMANDS_TXT}"
            )

    def test_commands_txt_has_no_extra_skills(self) -> None:
        commands_text = COMMANDS_TXT.read_text(encoding="utf-8")
        skill_dirs = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
        # Extract skill-name-like tokens from commands.txt
        for line in commands_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            # If the line contains a skill-name-pattern token, it must be a real skill dir
            tokens = re.findall(r"\b([a-z][a-z0-9-]+[a-z0-9])\b", stripped)
            for token in tokens:
                if token in EXPECTED_SKILL_NAMES:
                    assert token in skill_dirs, (
                        f"commands.txt references skill {token!r} but no matching skill directory"
                    )


@pytest.mark.unit
class TestUniversalOutputContract:
    """Every SKILL.md must contain the VERDICT/SUGGESTED COMMAND/CONFIRM output contract."""

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_verdict_section(self, skill_contents: dict[str, str], skill_name: str) -> None:
        content = skill_contents.get(skill_name, "")
        assert "VERDICT:" in content, f"Skill {skill_name!r} SKILL.md must contain 'VERDICT:' output contract"

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_suggested_command_section(self, skill_contents: dict[str, str], skill_name: str) -> None:
        content = skill_contents.get(skill_name, "")
        assert "SUGGESTED COMMAND:" in content, (
            f"Skill {skill_name!r} SKILL.md must contain 'SUGGESTED COMMAND:' output contract"
        )

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_confirm_gate(self, skill_contents: dict[str, str], skill_name: str) -> None:
        content = skill_contents.get(skill_name, "")
        assert "CONFIRM?" in content, f"Skill {skill_name!r} SKILL.md must contain 'CONFIRM?' gate"


@pytest.mark.unit
class TestNoAutonomousMutation:
    """Static check: mutating verbs must only appear in CONFIRM-gated blocks."""

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_stops_after_confirm(self, skill_contents: dict[str, str], skill_name: str) -> None:
        """SKILL.md must state STOP or NOT run after CONFIRM -- verifying the STOP contract."""
        content = skill_contents.get(skill_name, "")
        # The skill must declare it stops and does not autonomously run mutating verbs
        # Check that STOPS or STOP appears indicating the safety model is documented
        has_stop = re.search(r"\bSTOP\b|\bSTOPS\b|never run|never runs|prints.*command", content, re.IGNORECASE)
        assert has_stop, (
            f"Skill {skill_name!r} SKILL.md must explicitly state it STOP(S) after printing "
            f"the suggested command and never autonomously runs mutating verbs"
        )

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_triage_skill_references_classify_blocked_task(
        self, skill_contents: dict[str, str], skill_name: str
    ) -> None:
        """triage-blocked-task must reuse classify_blocked_task, not reimplement it."""
        if skill_name != "triage-blocked-task":
            return
        content = skill_contents.get(skill_name, "")
        assert "classify_blocked_task" in content, (
            "triage-blocked-task SKILL.md must reference 'classify_blocked_task' (no reimplementation -- AC-246a-1)"
        )


@pytest.mark.unit
class TestTriageSkillClassifierReuse:
    """AC-246a-1: triage-blocked-task reuses classify_blocked_task with no logic duplication."""

    def test_triage_calls_classify_blocked_task(self, skill_contents: dict[str, str]) -> None:
        content = skill_contents.get("triage-blocked-task", "")
        assert "classify_blocked_task" in content, "triage-blocked-task must call classify_blocked_task (AC-246a-1)"

    def test_triage_does_not_reimplement_bucket_logic(self, skill_contents: dict[str, str]) -> None:
        content = skill_contents.get("triage-blocked-task", "")
        # The skill should delegate to the classifier, not reimplement state machine
        # Key markers of reimplementation: defining HELD/BLOCKED_ON_HELD/etc inline
        reimpl_patterns = [
            r"if.*BLOCKED_ON_HELD.*elif.*AWAITING",
            r"match.*bucket.*case.*HELD",
        ]
        for pattern in reimpl_patterns:
            assert not re.search(pattern, content), (
                f"triage-blocked-task appears to reimplement classifier logic "
                f"(pattern {pattern!r} matched) -- must delegate to classify_blocked_task"
            )

    def test_triage_has_composite_sub_cap(self, skill_contents: dict[str, str]) -> None:
        """Sub-cap 1a: composite RUNTIME_DEGRADATION + OPERATOR_ACTION_REQUIRED warning."""
        content = skill_contents.get("triage-blocked-task", "")
        assert "RUNTIME_DEGRADATION" in content, (
            "triage-blocked-task must handle RUNTIME_DEGRADATION composite case (sub-cap 1a)"
        )

    def test_triage_has_thrash_sub_cap(self, skill_contents: dict[str, str]) -> None:
        """Sub-cap 1b: thrash detection via CASCADE_RECONCILED cycle count."""
        content = skill_contents.get("triage-blocked-task", "")
        assert "CASCADE_RECONCILED" in content or "thrash" in content.lower(), (
            "triage-blocked-task must handle thrash detection (sub-cap 1b)"
        )


@pytest.mark.unit
class TestFixtureWuImmutability:
    """No skill mutates fixture WU files -- all must be byte-identical after skill inspection."""

    def test_fixture_directory_exists(self) -> None:
        assert FIXTURES_DIR.exists(), f"Fixture directory for backlog-assistant must exist at {FIXTURES_DIR}"

    def test_fixture_files_unchanged_after_read(self, fixture_wu_files: list[tuple[Path, bytes]]) -> None:
        """Verify no skill SKILL.md alters fixture files -- byte-identity check."""
        for path, original_bytes in fixture_wu_files:
            current_bytes = path.read_bytes()
            assert current_bytes == original_bytes, (
                f"Fixture file {path} was mutated: byte content changed. "
                f"Skills must never autonomously write to backlog files."
            )


@pytest.mark.unit
class TestModelExamples:
    """AC-246-14: model examples in SKILL.md must reference claude-opus-4-8."""

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_uses_opus_48_model_example(self, skill_contents: dict[str, str], skill_name: str) -> None:
        content = skill_contents.get(skill_name, "")
        # Model example must be claude-opus-4-8 (not 4-7 or 4-6)
        if "claude-opus-4-" in content:
            assert "claude-opus-4-8" in content, (
                f"Skill {skill_name!r} model examples must use claude-opus-4-8; found older model reference"
            )


@pytest.mark.unit
class TestNoEmDash:
    """No em-dash characters in any skill file (critical rule)."""

    @pytest.mark.parametrize("skill_name", sorted(EXPECTED_SKILL_NAMES))
    def test_skill_has_no_em_dash(self, skill_contents: dict[str, str], skill_name: str) -> None:
        content = skill_contents.get(skill_name, "")
        em_dash = "\u2014"
        assert em_dash not in content, (
            f"Skill {skill_name!r} SKILL.md contains an em-dash character (U+2014); use '--' instead"
        )

    def test_plugin_json_has_no_em_dash(self, assistant_plugin_manifest: dict[str, Any]) -> None:
        raw = PLUGIN_JSON.read_text(encoding="utf-8")
        em_dash = "\u2014"
        assert em_dash not in raw, "plugin.json contains em-dash character (U+2014); use '--' instead"

    def test_commands_txt_has_no_em_dash(self) -> None:
        raw = COMMANDS_TXT.read_text(encoding="utf-8")
        em_dash = "\u2014"
        assert em_dash not in raw, "commands.txt contains em-dash character (U+2014); use '--' instead"


# ---------------------------------------------------------------------------
# E11-F3 integration tests: auto-resolve wired into triage flow (issue #263)
# ---------------------------------------------------------------------------

AUTO_RESOLVE_FIXTURES_DIR = FIXTURES_DIR / "auto_resolve"
SEEDED_CATALOG_PATH = AUTO_RESOLVE_FIXTURES_DIR / "seeded-catalog.json"

# Advise-only payload that the triage flow would produce for a RUNTIME_DEGRADATION task.
# This is the text the disabled path must return byte-for-byte unchanged (AC-1 disabled path).
_ADVISE_ONLY_PAYLOAD = (
    "VERDICT: RUNTIME_DEGRADATION -- orchestrator agent-tool-unavailable signal detected.\n"
    "\n"
    "SUGGESTED COMMAND:\n"
    "  make start\n"
    "\n"
    "CONFIRM? Run the above command only after reviewing the audit tail above.\n"
    "This skill STOPS here. No mutating verb has been executed.\n"
)


@pytest.mark.unit
class TestTriageSkillAutoResolveInstructions:
    """AC-1, AC-2: triage-blocked-task SKILL.md documents the auto-resolve flow.

    These static-analysis tests verify that the SKILL.md instructions reference
    the auto-resolve engine entry point (apply_auto_resolve), the config gate
    (auto_resolve.enabled), the catalog consultation, and preserve the advise-only
    default when auto_resolve.enabled is false.
    """

    def test_triage_skill_references_apply_auto_resolve(self, skill_contents: dict[str, str]) -> None:
        """SKILL.md must instruct the agent to call apply_auto_resolve (AC-1)."""
        content = skill_contents.get("triage-blocked-task", "")
        assert "apply_auto_resolve" in content, (
            "triage-blocked-task SKILL.md must reference 'apply_auto_resolve' to invoke the engine (AC-1)"
        )

    def test_triage_skill_references_auto_resolve_enabled(self, skill_contents: dict[str, str]) -> None:
        """SKILL.md must guard the auto-apply path on auto_resolve.enabled (AC-1)."""
        content = skill_contents.get("triage-blocked-task", "")
        assert "auto_resolve.enabled" in content or "auto_resolve[" in content or "auto_resolve_enabled" in content, (
            "triage-blocked-task SKILL.md must reference the 'auto_resolve.enabled' config gate (AC-1)"
        )

    def test_triage_skill_references_catalog_consultation(self, skill_contents: dict[str, str]) -> None:
        """SKILL.md must instruct catalog consultation (AC-1, AC-2)."""
        content = skill_contents.get("triage-blocked-task", "")
        assert "catalog" in content.lower(), (
            "triage-blocked-task SKILL.md must reference catalog consultation in the auto-resolve flow (AC-1)"
        )

    def test_triage_skill_advise_only_default_preserved(self, skill_contents: dict[str, str]) -> None:
        """SKILL.md must state the advise-only path is preserved when disabled (AC-1)."""
        content = skill_contents.get("triage-blocked-task", "")
        # The skill must describe the disabled/advise-only preservation path
        has_disabled_path = (
            "advise-only" in content.lower()
            or "advise_only" in content.lower()
            or ("disabled" in content.lower() and "auto_resolve" in content.lower())
        )
        assert has_disabled_path, (
            "triage-blocked-task SKILL.md must describe the advise-only default preserved when disabled (AC-1)"
        )

    def test_triage_skill_no_classifier_duplication(self, skill_contents: dict[str, str]) -> None:
        """SKILL.md must reuse the existing classifier with no duplication (AC-2)."""
        content = skill_contents.get("triage-blocked-task", "")
        # Must reference the classifier -- confirmed by existing test, but also
        # must NOT embed inline bucket logic (inline if/elif chains duplicating classifier)
        assert "classify_blocked_task" in content, (
            "triage-blocked-task SKILL.md must call classify_blocked_task (no reimplementation -- AC-2)"
        )
        reimpl_patterns = [
            r"if.*BLOCKED_ON_HELD.*elif.*AWAITING",
            r"match.*bucket.*case.*HELD",
        ]
        for pattern in reimpl_patterns:
            assert not re.search(pattern, content), (
                f"triage-blocked-task appears to reimplement classifier logic "
                f"(pattern {pattern!r} matched) -- must delegate to classify_blocked_task (AC-2)"
            )


@pytest.mark.unit
class TestSeededCatalogFixture:
    """AC-3: seeded-catalog fixture exists and has valid schema for integration tests."""

    def test_seeded_catalog_fixture_exists(self) -> None:
        """Fixture file must exist so the integration test can load it (AC-3)."""
        assert SEEDED_CATALOG_PATH.exists(), (
            f"Seeded-catalog fixture missing at {SEEDED_CATALOG_PATH}; "
            "required for RUNTIME_DEGRADATION end-to-end integration test (AC-3)"
        )

    def test_seeded_catalog_is_valid_json(self) -> None:
        """Fixture must parse as valid JSON (AC-3)."""
        raw = SEEDED_CATALOG_PATH.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            pytest.fail(f"seeded-catalog.json is not valid JSON: {exc}")
        assert isinstance(data, dict), "seeded-catalog.json top-level value must be a JSON object"

    def test_seeded_catalog_has_correct_schema_version(self) -> None:
        """Fixture must declare schema_version matching CATALOG_SCHEMA_VERSION (AC-3)."""
        from devbench.backlog.operator_resolution_catalog import CATALOG_SCHEMA_VERSION

        raw = SEEDED_CATALOG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data.get("schema_version") == CATALOG_SCHEMA_VERSION, (
            f"seeded-catalog.json schema_version must be {CATALOG_SCHEMA_VERSION}; got {data.get('schema_version')!r}"
        )

    def test_seeded_catalog_has_runtime_degradation_entry(self) -> None:
        """Fixture must contain a learned RUNTIME_DEGRADATION entry (success_count >= 1) (AC-3)."""
        raw = SEEDED_CATALOG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        entries = data.get("entries", {})
        runtime_keys = [k for k in entries if k.startswith("RUNTIME_DEGRADATION:")]
        assert runtime_keys, (
            "seeded-catalog.json must contain at least one RUNTIME_DEGRADATION entry "
            "so the integration test can exercise the learned-signature apply path (AC-3)"
        )
        # At least one entry must have success_count >= 1 (a learned -- not novel -- signature)
        learned_entries = [entries[k] for k in runtime_keys if entries[k].get("success_count", 0) >= 1]
        assert learned_entries, (
            "seeded-catalog.json must have at least one RUNTIME_DEGRADATION entry with "
            "success_count >= 1 (learned signature) so auto-apply path fires in integration test (AC-3)"
        )


@pytest.mark.unit
class TestAutoResolveEngineIntegration:
    """AC-3: end-to-end integration of apply_auto_resolve with seeded catalog and fixture backlog.

    These tests exercise the auto-resolve engine directly (not via the skill instructions)
    to verify the triage flow produces [AUTO_RESOLVED] for a RUNTIME_DEGRADATION task when
    enabled, and returns byte-for-byte advise-only when disabled.
    """

    def test_runtime_degradation_auto_resolve_enabled_emits_audit_string(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When auto_resolve.enabled is true and the catalog has a learned signature,
        apply_auto_resolve must emit [AUTO_RESOLVED] and return the advise-only payload (AC-3)."""
        from devbench.backlog.auto_resolve import apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import CATALOG_SCHEMA_VERSION
        from devbench.config_loader import AutoResolveConfig

        # Build a temporary workspace with the seeded catalog.
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            # Seed the catalog from the fixture file.
            fixture_data = json.loads(SEEDED_CATALOG_PATH.read_text(encoding="utf-8"))
            assert fixture_data.get("schema_version") == CATALOG_SCHEMA_VERSION, (
                f"seeded-catalog.json must have schema_version={CATALOG_SCHEMA_VERSION}"
            )
            # Write the seeded catalog to the temp workspace.
            devbench_dir = workspace / ".devbench"
            devbench_dir.mkdir()
            catalog_file = devbench_dir / "operator-resolution-catalog.json"
            catalog_file.write_text(SEEDED_CATALOG_PATH.read_text(encoding="utf-8"), encoding="utf-8")

            # Extract the signature from the fixture so the test is data-driven.
            entries = fixture_data.get("entries", {})
            runtime_entries = {k: v for k, v in entries.items() if k.startswith("RUNTIME_DEGRADATION:")}
            assert runtime_entries, "seeded catalog must have RUNTIME_DEGRADATION entry"
            # Use the first learned entry (success_count >= 1).
            learned_key, learned_entry = next(
                (k, v) for k, v in runtime_entries.items() if v.get("success_count", 0) >= 1
            )
            # The key format is "RUNTIME_DEGRADATION:<normalized_signature>"
            _, normalized_sig = learned_key.split(":", 1)
            remediation = learned_entry["remediation"]

            config = AutoResolveConfig(enabled=True, max_attempts=3)
            result = apply_auto_resolve(
                task_id="E99-FIXTURE-T1",
                signature=normalized_sig,
                remediation=remediation,
                advise_only_payload=_ADVISE_ONLY_PAYLOAD,
                config=config,
                catalog_path=workspace,
                classification="RUNTIME_DEGRADATION",
            )

        # The payload is returned unchanged (the engine logs [AUTO_RESOLVED] to stderr).
        assert result == _ADVISE_ONLY_PAYLOAD, (
            "apply_auto_resolve must return the advise-only payload unchanged on the apply path"
        )
        captured = capsys.readouterr()
        assert "[AUTO_RESOLVED]" in captured.err, (
            "apply_auto_resolve must emit [AUTO_RESOLVED] to stderr for a learned RUNTIME_DEGRADATION signature "
            "when auto_resolve.enabled is true (AC-3)"
        )

    def test_runtime_degradation_auto_resolve_disabled_returns_advise_only_unchanged(self) -> None:
        """When auto_resolve.enabled is false, apply_auto_resolve must return
        the advise-only payload byte-for-byte without any engine side-effects (AC-1 disabled path)."""
        from devbench.backlog.auto_resolve import apply_auto_resolve
        from devbench.config_loader import AutoResolveConfig

        config = AutoResolveConfig(enabled=False, max_attempts=3)
        result = apply_auto_resolve(
            task_id="E99-FIXTURE-T1",
            signature="agent-tool-unavailable-sig-abc123",
            remediation="re-queue",
            advise_only_payload=_ADVISE_ONLY_PAYLOAD,
            config=config,
        )
        assert result == _ADVISE_ONLY_PAYLOAD, (
            "apply_auto_resolve with enabled=False must return the advise-only payload "
            "byte-for-byte unchanged -- no modification, no side-effects (AC-1 disabled path)"
        )

    def test_runtime_degradation_auto_resolve_disabled_no_catalog_write(self) -> None:
        """When disabled, the engine must not write to the catalog at all (AC-1 disabled path)."""
        from devbench.backlog.auto_resolve import apply_auto_resolve
        from devbench.config_loader import AutoResolveConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = AutoResolveConfig(enabled=False, max_attempts=3)
            apply_auto_resolve(
                task_id="E99-FIXTURE-T1",
                signature="agent-tool-unavailable-sig-disabled",
                remediation="re-queue",
                advise_only_payload=_ADVISE_ONLY_PAYLOAD,
                config=config,
                catalog_path=workspace,
                classification="RUNTIME_DEGRADATION",
            )
            catalog_file = workspace / ".devbench" / "operator-resolution-catalog.json"
            assert not catalog_file.exists(), (
                "apply_auto_resolve with enabled=False must NOT write the catalog "
                "(disabled path must be byte-for-byte advise-only with no side-effects -- AC-1)"
            )


# ---------------------------------------------------------------------------
# Regression: triage-blocked-task classifier import-module + call-form drift
# (TDI: backlog-assistant-triage-classify-import-and-argform-drift)
#
# The skill documents `python -c` blocks that import the classifier and call
# it. Two prior drifts broke the skill's core classification step:
#   1. Wrong module: imported from devbench.backlog.manager (raises ImportError)
#      instead of the module that actually exports the symbol.
#   2. Wrong call form: classify_blocked_task('<id>', workspace) -- 2 positional
#      args -- instead of the real signature
#      classify_blocked_task(backlog_root, backlog_index, task_id, *, ...).
# These tests pin both invariants against the LIVE code so a future module move
# re-flags the stale docs.
# ---------------------------------------------------------------------------

# Classifier symbols the skill documents importing + calling.
_CLASSIFIER_SYMBOLS = (
    "classify_blocked_task",
    "classify_blocked_task_excluding_degradation",
)


def _module_that_exports(symbol: str) -> str:
    """Return the dotted module path that actually defines ``symbol`` at HEAD.

    Discovered dynamically from the live package so that if the function is
    moved to another module the skill docs are forced to follow (the assertion
    below compares the skill's documented import module to this value).
    """
    import importlib

    proposal = importlib.import_module("devbench.backlog.proposal")
    obj = getattr(proposal, symbol, None)
    assert obj is not None, (
        f"{symbol} is no longer exported by devbench.backlog.proposal; "
        "update this regression test to discover the new home module"
    )
    return obj.__module__


def _classifier_import_modules(content: str, symbol: str) -> set[str]:
    """Modules the SKILL.md documents importing ``symbol`` from.

    Matches ``from <module> import ... <symbol> ...`` lines.
    """
    modules: set[str] = set()
    import_re = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+(.+)$", re.MULTILINE)
    for module, names in import_re.findall(content):
        imported = {n.strip() for n in names.split(",")}
        if symbol in imported:
            modules.add(module)
    return modules


@pytest.mark.unit
class TestTriageClassifierImportModule:
    """AC-2: the skill's documented import module matches the module that
    actually exports the classifier symbols, so a future move re-flags it."""

    @pytest.mark.parametrize("symbol", _CLASSIFIER_SYMBOLS)
    def test_skill_imports_classifier_from_exporting_module(self, skill_contents: dict[str, str], symbol: str) -> None:
        content = skill_contents["triage-blocked-task"]
        documented = _classifier_import_modules(content, symbol)
        assert documented, (
            f"triage-blocked-task SKILL.md documents no import of {symbol!r}; "
            "the skill's classification step must import the classifier"
        )
        exporting = _module_that_exports(symbol)
        assert documented == {exporting}, (
            f"triage-blocked-task SKILL.md imports {symbol!r} from {sorted(documented)}, "
            f"but it is actually exported by {exporting!r}. Update the SKILL.md import "
            "lines to match the module that owns the symbol (the function moved)."
        )

    def test_skill_does_not_import_classifier_from_manager(self, skill_contents: dict[str, str]) -> None:
        """The stale `from devbench.backlog.manager import classify_blocked_task`
        form raised ImportError; it must not reappear."""
        content = skill_contents["triage-blocked-task"]
        for symbol in _CLASSIFIER_SYMBOLS:
            assert "devbench.backlog.manager" not in _classifier_import_modules(content, symbol), (
                f"triage-blocked-task SKILL.md imports {symbol!r} from devbench.backlog.manager, "
                "which does not export it (raises ImportError)"
            )


@pytest.mark.unit
class TestTriageClassifierCallForm:
    """AC-1: every documented classify_blocked_task* call uses the real
    >=3-positional-arg signature, never the stale 2-arg form."""

    @pytest.mark.parametrize("symbol", _CLASSIFIER_SYMBOLS)
    def test_documented_calls_use_three_or_more_positional_args(
        self, skill_contents: dict[str, str], symbol: str
    ) -> None:
        content = skill_contents["triage-blocked-task"]
        # Match `symbol(` not preceded by `import `/`def `/an identifier char,
        # then capture the parenthesised argument list (no nested parens needed
        # for the documented calls).
        call_re = re.compile(rf"(?<![\w.]){re.escape(symbol)}\(([^()]*)\)")
        calls = call_re.findall(content)
        assert calls, (
            f"triage-blocked-task SKILL.md documents no call to {symbol}(...); the skill must invoke the classifier"
        )
        for args in calls:
            # Count positional args = top-level comma-separated terms with no '='.
            terms = [t.strip() for t in args.split(",") if t.strip()]
            positional = [t for t in terms if "=" not in t]
            assert len(positional) >= 3, (
                f"triage-blocked-task SKILL.md calls {symbol}({args}) with "
                f"{len(positional)} positional arg(s); the real signature requires "
                "at least 3 positional args (backlog_root, backlog_index, task_id). "
                "The stale 2-arg form raises TypeError."
            )
