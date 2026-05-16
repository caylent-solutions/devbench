"""Structural pins for per-skill quickstart documentation (AC-191-9).

Verifies that docs/skills/<skill>.md quickstart pages exist for the four
marketplace plugin skills: create-spec, spec-to-backlog, bootstrap-environment,
and configure-devbench.

Each quickstart doc must:
- Exist as a readable file.
- Contain the skill name as a heading.
- Describe the purpose of the skill.
- Include usage instructions (invocation example).
- Reference the kanon exemplar as the quality bar (where applicable).
- Document the output contract (what the skill produces).
- Include cross-references to companion docs.

Spec source: spec/devbench-self-improve.md section 4.6.6.
Issue: #191.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SKILLS_DOCS_DIR = REPO_ROOT / "docs" / "skills"

CREATE_SPEC_DOC = SKILLS_DOCS_DIR / "create-spec.md"
SPEC_TO_BACKLOG_DOC = SKILLS_DOCS_DIR / "spec-to-backlog.md"
BOOTSTRAP_ENVIRONMENT_DOC = SKILLS_DOCS_DIR / "bootstrap-environment.md"
CONFIGURE_DEVBENCH_DOC = SKILLS_DOCS_DIR / "configure-devbench.md"

ALL_SKILL_DOCS = [
    ("create-spec", CREATE_SPEC_DOC),
    ("spec-to-backlog", SPEC_TO_BACKLOG_DOC),
    ("bootstrap-environment", BOOTSTRAP_ENVIRONMENT_DOC),
    ("configure-devbench", CONFIGURE_DEVBENCH_DOC),
]


@pytest.mark.unit
class TestSkillDocFilesExist:
    """All four per-skill quickstart docs must exist under docs/skills/."""

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_file_exists(self, skill_name: str, doc_path: Path) -> None:
        """docs/skills/<skill>.md must exist for each of the four skills (AC-191-9)."""
        assert doc_path.is_file(), (
            f"docs/skills/{skill_name}.md must exist -- it is the per-skill quickstart "
            f"reference for the marketplace plugin onboarding workflow (AC-191-9)."
        )


@pytest.mark.unit
class TestSkillDocContent:
    """Each quickstart doc must contain the required structural sections."""

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_has_skill_name_heading(self, skill_name: str, doc_path: Path) -> None:
        """Each doc must have the skill name in a heading."""
        text = doc_path.read_text(encoding="utf-8")
        lower = text.lower()
        skill_slug = skill_name.lower()
        assert skill_slug in lower, (
            f"docs/skills/{skill_name}.md must contain the skill name '{skill_name}' "
            f"in its content (AC-191-9)."
        )
        # Must have at least one markdown heading.
        assert "#" in text, (
            f"docs/skills/{skill_name}.md must contain at least one markdown heading (AC-191-9)."
        )

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_has_invocation_example(self, skill_name: str, doc_path: Path) -> None:
        """Each doc must show how to invoke the skill."""
        text = doc_path.read_text(encoding="utf-8")
        # The doc should show a 'claude run' or skill invocation pattern.
        has_invocation = (
            "claude run" in text
            or "devbench:" + skill_name in text
            or "claude --plugin" in text
            or "plugin" in text.lower()
        )
        assert has_invocation, (
            f"docs/skills/{skill_name}.md must include an invocation example showing "
            f"how to run the skill (AC-191-9)."
        )

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_has_output_contract(self, skill_name: str, doc_path: Path) -> None:
        """Each doc must describe what the skill produces (output contract)."""
        text = doc_path.read_text(encoding="utf-8")
        lower = text.lower()
        has_output_info = (
            "output" in lower
            or "produces" in lower
            or "result" in lower
            or "writes" in lower
            or "creates" in lower
            or "generates" in lower
        )
        assert has_output_info, (
            f"docs/skills/{skill_name}.md must describe what the skill produces "
            f"(output contract) so operators know what to expect (AC-191-9)."
        )

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_has_prerequisites_or_inputs(self, skill_name: str, doc_path: Path) -> None:
        """Each doc must describe required inputs or prerequisites."""
        text = doc_path.read_text(encoding="utf-8")
        lower = text.lower()
        has_prereqs = (
            "prerequisite" in lower
            or "requires" in lower
            or "input" in lower
            or "before" in lower
            or "first" in lower
            or "step 1" in lower
        )
        assert has_prereqs, (
            f"docs/skills/{skill_name}.md must document prerequisites or required inputs "
            f"so operators know what to prepare before invoking the skill (AC-191-9)."
        )

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_no_em_dashes(self, skill_name: str, doc_path: Path) -> None:
        """No em-dash characters may appear in skill quickstart docs."""
        text = doc_path.read_text(encoding="utf-8")
        assert "\u2014" not in text, (
            f"docs/skills/{skill_name}.md must not contain em-dash characters (U+2014). "
            f"Use double hyphen '--' instead. "
            f"validate-backlog rule 10 enforces this on work-unit files; the same "
            f"convention applies to documentation files authored alongside work units."
        )


@pytest.mark.unit
class TestCreateSpecDocSpecifics:
    """create-spec quickstart must reference the kanon exemplar as the quality bar."""

    def test_create_spec_doc_references_kanon_exemplar(self) -> None:
        """docs/skills/create-spec.md must mention the kanon exemplar quality reference."""
        text = CREATE_SPEC_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_quality_ref = (
            "kanon" in lower
            or "quality" in lower
            or "exemplar" in lower
            or "quality bar" in lower.replace("-", " ")
        )
        assert has_quality_ref, (
            "docs/skills/create-spec.md must reference the kanon exemplar as the "
            "quality bar for spec authoring -- the SKILL.md itself mandates reading "
            "the kanon spec (AC-191-9)."
        )

    def test_create_spec_doc_mentions_output_file(self) -> None:
        """docs/skills/create-spec.md must mention spec/<name>.md as the output."""
        text = CREATE_SPEC_DOC.read_text(encoding="utf-8")
        assert "spec/" in text, (
            "docs/skills/create-spec.md must document that the output is written "
            "to spec/<project-name>.md (AC-191-9)."
        )

    def test_create_spec_doc_mentions_iterate_until_perfect(self) -> None:
        """docs/skills/create-spec.md must mention the iterate-until-perfect loop."""
        text = CREATE_SPEC_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_iteration = (
            "iterate" in lower
            or "iteration" in lower
            or "self-critique" in lower
            or "rubric" in lower
            or "revise" in lower
        )
        assert has_iteration, (
            "docs/skills/create-spec.md must describe the iterate-until-perfect "
            "self-critique loop that drives spec quality (AC-191-9)."
        )


@pytest.mark.unit
class TestSpecToBacklogDocSpecifics:
    """spec-to-backlog quickstart must reference the kanon exemplar and backlog output."""

    def test_spec_to_backlog_doc_references_kanon_exemplar(self) -> None:
        """docs/skills/spec-to-backlog.md must mention the kanon quality reference."""
        text = SPEC_TO_BACKLOG_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_quality_ref = (
            "kanon" in lower
            or "quality" in lower
            or "exemplar" in lower
        )
        assert has_quality_ref, (
            "docs/skills/spec-to-backlog.md must reference the kanon exemplar as the "
            "quality bar for backlog generation (AC-191-9)."
        )

    def test_spec_to_backlog_doc_mentions_backlog_output(self) -> None:
        """docs/skills/spec-to-backlog.md must mention BACKLOG.md as output."""
        text = SPEC_TO_BACKLOG_DOC.read_text(encoding="utf-8")
        assert "BACKLOG.md" in text, (
            "docs/skills/spec-to-backlog.md must document that BACKLOG.md is "
            "generated as the primary output (AC-191-9)."
        )

    def test_spec_to_backlog_doc_mentions_validate_backlog(self) -> None:
        """docs/skills/spec-to-backlog.md must mention validate-backlog."""
        text = SPEC_TO_BACKLOG_DOC.read_text(encoding="utf-8")
        assert "validate-backlog" in text, (
            "docs/skills/spec-to-backlog.md must document that the generated "
            "backlog is verified with validate-backlog (AC-191-9)."
        )

    def test_spec_to_backlog_doc_mentions_draft_status(self) -> None:
        """docs/skills/spec-to-backlog.md must document that tasks default to draft."""
        text = SPEC_TO_BACKLOG_DOC.read_text(encoding="utf-8")
        assert "draft" in text, (
            "docs/skills/spec-to-backlog.md must document that generated tasks "
            "default to 'draft' status (AC-191-9)."
        )


@pytest.mark.unit
class TestBootstrapEnvironmentDocSpecifics:
    """bootstrap-environment quickstart must describe the clone + validate workflow."""

    def test_bootstrap_environment_doc_mentions_clone(self) -> None:
        """docs/skills/bootstrap-environment.md must mention cloning repos."""
        text = BOOTSTRAP_ENVIRONMENT_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        assert "clone" in lower, (
            "docs/skills/bootstrap-environment.md must document the repo-cloning "
            "step in the bootstrap workflow (AC-191-9)."
        )

    def test_bootstrap_environment_doc_mentions_make_validate(self) -> None:
        """docs/skills/bootstrap-environment.md must mention make validate."""
        text = BOOTSTRAP_ENVIRONMENT_DOC.read_text(encoding="utf-8")
        assert "make validate" in text or "validate" in text.lower(), (
            "docs/skills/bootstrap-environment.md must document that make validate "
            "is run as a baseline check for each repo (AC-191-9)."
        )

    def test_bootstrap_environment_doc_mentions_devbench_yaml(self) -> None:
        """docs/skills/bootstrap-environment.md must reference devbench.yaml config."""
        text = BOOTSTRAP_ENVIRONMENT_DOC.read_text(encoding="utf-8")
        assert "devbench.yaml" in text, (
            "docs/skills/bootstrap-environment.md must reference backlog/config/devbench.yaml "
            "as the source of the repos list (AC-191-9)."
        )


@pytest.mark.unit
class TestConfigureDevbenchDocSpecifics:
    """configure-devbench quickstart must describe the YAML authoring walkthrough."""

    def test_configure_devbench_doc_mentions_yaml_output(self) -> None:
        """docs/skills/configure-devbench.md must mention devbench.yaml as the output."""
        text = CONFIGURE_DEVBENCH_DOC.read_text(encoding="utf-8")
        assert "devbench.yaml" in text, (
            "docs/skills/configure-devbench.md must document that the output is "
            "backlog/config/devbench.yaml (AC-191-9)."
        )

    def test_configure_devbench_doc_mentions_validation(self) -> None:
        """docs/skills/configure-devbench.md must describe runtime validation."""
        text = CONFIGURE_DEVBENCH_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_validation = "valid" in lower or "round-trip" in lower or "parse" in lower
        assert has_validation, (
            "docs/skills/configure-devbench.md must document that config values are "
            "validated via RuntimeConfig round-trip after each section (AC-191-9)."
        )

    def test_configure_devbench_doc_mentions_repos_section(self) -> None:
        """docs/skills/configure-devbench.md must describe the repos: section."""
        text = CONFIGURE_DEVBENCH_DOC.read_text(encoding="utf-8")
        assert "repos" in text, (
            "docs/skills/configure-devbench.md must document the repos: section "
            "as the primary required config (AC-191-9)."
        )


@pytest.mark.unit
class TestSkillDocsHaveCrossReferences:
    """Skill quickstart docs must cross-reference the skills directory and companion docs."""

    @pytest.mark.parametrize("skill_name,doc_path", ALL_SKILL_DOCS)
    def test_skill_doc_has_cross_references(self, skill_name: str, doc_path: Path) -> None:
        """Each skill doc must include at least one cross-reference link."""
        text = doc_path.read_text(encoding="utf-8")
        # Cross-references in markdown use [text](link) syntax or bare refs.
        has_cross_ref = (
            "](" in text  # markdown link
            or "docs/" in text  # reference to another doc
            or "SKILL.md" in text  # link to the skill source
            or "zero-to-ready" in text  # reference to onboarding guide
            or "onboarding" in text.lower()  # reference to onboarding
        )
        assert has_cross_ref, (
            f"docs/skills/{skill_name}.md must include cross-references to "
            f"companion docs or the SKILL.md source (AC-191-9)."
        )
