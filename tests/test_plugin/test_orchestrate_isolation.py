"""Issue #224 AC-10: orchestrate plugin loads + functions without the authoring plugin.

The split landed two marketplaces in the same repo:

- ``plugin/`` (orchestrate marketplace, ships ``devbench-orchestrate``)
- ``plugin-authoring/`` (authoring marketplace, ships ``devbench-authoring``)

This test pins that the orchestrate side is SELF-CONTAINED: every skill,
agent, hook, and script that orchestrate references resolves under
``plugin/devbench-orchestrate/`` alone -- no cross-marketplace path lookups,
no string references to authoring-side artefacts that would fail when
``plugin-authoring/`` is not registered as a marketplace.

The regression failure mode this catches: a future refactor that
accidentally moves a guard script or agent file out of the orchestrate
plugin breaks the isolation property silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ORCHESTRATE_PLUGIN_ROOT = REPO_ROOT / "plugin" / "devbench-orchestrate"
ORCHESTRATE_MARKETPLACE = REPO_ROOT / "plugin" / ".claude-plugin" / "marketplace.json"


@pytest.mark.unit
class TestOrchestratePluginSelfContained:
    """Every artefact the orchestrate marketplace references must resolve
    under ``plugin/devbench-orchestrate/``.
    """

    def test_marketplace_source_points_to_orchestrate_dir(self) -> None:
        manifest = json.loads(ORCHESTRATE_MARKETPLACE.read_text(encoding="utf-8"))
        plugins = manifest["plugins"]
        assert len(plugins) == 1
        source = plugins[0]["source"].rstrip("/")
        # Claude Code resolves the source path relative to the marketplace
        # ROOT directory (the parent of .claude-plugin/), not the
        # .claude-plugin/ subdir itself.
        marketplace_root = ORCHESTRATE_MARKETPLACE.parent.parent
        resolved = (marketplace_root / source).resolve()
        assert resolved == ORCHESTRATE_PLUGIN_ROOT.resolve(), (
            f"orchestrate marketplace's plugin source must resolve to {ORCHESTRATE_PLUGIN_ROOT}; got {resolved}"
        )

    def test_orchestrate_skill_present(self) -> None:
        assert (ORCHESTRATE_PLUGIN_ROOT / "skills" / "orchestrate" / "SKILL.md").is_file()

    def test_authoring_skills_absent_from_orchestrate_plugin(self) -> None:
        """The four authoring skills must NOT live inside the orchestrate
        plugin -- they ship from devbench-authoring instead (issue #224).
        """
        for skill in ("spec-to-backlog", "create-spec", "configure-devbench", "bootstrap-environment"):
            assert not (ORCHESTRATE_PLUGIN_ROOT / "skills" / skill).exists(), (
                f"authoring skill {skill!r} must NOT live in the orchestrate plugin"
            )

    def test_every_agent_md_present(self) -> None:
        """The orchestrate plugin must ship every agent the orchestrate
        skill expects to spawn.  Read agent names from the SKILL.md and
        assert each one's .md file exists under agents/.
        """
        skill_text = (ORCHESTRATE_PLUGIN_ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
        # Match every Skill("devbench-orchestrate:<agent>", ...) invocation.
        invocations = set(re.findall(r"devbench-orchestrate:([a-z][a-z_-]+)", skill_text))
        # Map review-team underscored shortnames to their actual file
        # locations (review_team subdir + hyphenated filename).
        review_team_map = {
            "code_review": "review_team/code-reviewer.md",
            "test_review": "review_team/test-reviewer.md",
            "doc_review": "review_team/doc-reviewer.md",
            "changes_manifest": "review_team/changes-manifest.md",
        }
        for invocation in invocations:
            if invocation == "orchestrate":
                continue  # the skill itself, not an agent
            if invocation in review_team_map:
                agent_path = ORCHESTRATE_PLUGIN_ROOT / "agents" / review_team_map[invocation]
            else:
                # Direct mapping: kebab-case agent name -> agents/<name>.md
                agent_path = ORCHESTRATE_PLUGIN_ROOT / "agents" / f"{invocation}.md"
            assert agent_path.is_file(), (
                f"orchestrate plugin must contain agent file for invocation {invocation!r} at {agent_path}"
            )

    def test_every_hook_script_present(self) -> None:
        """Every script named in hooks.json must exist under scripts/."""
        hooks = json.loads((ORCHESTRATE_PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        script_refs: set[str] = set()
        for matcher_block in hooks["hooks"].get("PreToolUse", []) + hooks["hooks"].get("PostToolUse", []):
            for hook in matcher_block["hooks"]:
                cmd = hook["command"]
                m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([a-z_-]+\.sh)", cmd)
                if m:
                    script_refs.add(m.group(1))
        for ref in script_refs:
            script_path = ORCHESTRATE_PLUGIN_ROOT / "scripts" / ref
            assert script_path.is_file(), f"hooks.json references {ref}; must exist at {script_path}"

    def test_no_stale_devbench_only_prefix_in_orchestrate(self) -> None:
        """After the rename, no orchestrate-side file may reference the
        OLD bare ``devbench:<agent>`` prefix.  Every sub-agent invocation
        must use the new ``devbench-orchestrate:<agent>`` prefix.
        """
        for md_path in ORCHESTRATE_PLUGIN_ROOT.rglob("*.md"):
            content = md_path.read_text(encoding="utf-8")
            stale = re.findall(
                r"\bdevbench:(executor|blocker-resolver|task-factory|manifest-amender|"
                r"security-reviewer|review-supervisor|code_review|test_review|doc_review|"
                r"changes_manifest|orchestrate)\b",
                content,
            )
            assert not stale, (
                f"{md_path.relative_to(REPO_ROOT)} contains stale 'devbench:<agent>' refs: {stale}. "
                "Issue #224 requires every orchestrate-side sub-agent invocation to use 'devbench-orchestrate:<agent>'."
            )
        for sh_path in (ORCHESTRATE_PLUGIN_ROOT / "scripts").glob("*.sh"):
            content = sh_path.read_text(encoding="utf-8")
            stale = re.findall(
                r"\bdevbench:(executor|blocker-resolver|task-factory|manifest-amender|"
                r"security-reviewer|review-supervisor|code_review|test_review|doc_review|"
                r"changes_manifest|orchestrate)\b",
                content,
            )
            assert not stale, (
                f"{sh_path.relative_to(REPO_ROOT)} contains stale 'devbench:<agent>' refs: {stale}. "
                "Issue #224 requires every guard script to match against 'devbench-orchestrate:<agent>'."
            )
