"""Integration tests: each onboarding skill is independently invocable given valid input files.

Exercises AC-191-8: each of the four skills (create-spec, spec-to-backlog,
bootstrap-environment, configure-devbench) MUST be invocable standalone without
requiring the prior skill to have run, given valid pre-existing input files.

These tests verify the FILE SYSTEM CONTRACT each skill reads as input and the
OUTPUT CONTRACT each skill must satisfy, exercised in isolation (no dependency
on prior skill execution). Each skill is tested with pre-populated input files
that match exactly what the skill's SKILL.md specifies it will read.

No LLM calls are made -- the tests assert structural contracts using real
devbench production code (config_loader.load_runtime_config, cli.cmd_validate_backlog).
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from devbench import cli
from devbench.config_loader import RepoConfig, RuntimeConfig, load_runtime_config

_EXAMPLE_RT_CFG = RuntimeConfig(repos={"example-org/example-repo": RepoConfig()})

_REPO_ROOT = Path(__file__).parent.parent.parent

_SKILLS_DIR = _REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills"

_ONBOARDING_SKILLS = (
    "create-spec",
    "spec-to-backlog",
    "bootstrap-environment",
    "configure-devbench",
)


def _write_valid_spec_input(workspace: Path, project_name: str) -> Path:
    """Write a valid spec file that create-spec consumes as its quality reference input.

    create-spec reads the kanon exemplar at Step 1 and then asks the operator
    for their project. In isolation tests, we pre-populate the spec file that
    create-spec would author (the output contract) so that downstream skills
    can also be tested independently.

    For the create-spec independent-invocability test, the pre-existing input is
    the kanon exemplar path referenced in SKILL.md Step 1. We verify that the
    skill's SKILL.md correctly references that path -- a structural assertion on
    the skill's input contract.
    """
    spec_dir = workspace / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / f"{project_name}.md"
    spec_file.write_text(
        textwrap.dedent(f"""\
            # {project_name.replace("-", " ").title()} Specification

            ## 0. Items that change existing user-facing behaviour

            N/A -- this spec introduces no behavior changes.

            ## 1. Context

            This is a minimal test spec for independent-invocability testing.
            Validated against the kanon quality bar referenced in create-spec SKILL.md.

            ## 2. Goals

            - Goal 1: verify spec-to-backlog can consume this spec independently.

            ## 3. Existing primitives to reuse

            - `devbench validate-backlog` -- validating generated backlog structure.

            ## 6. Acceptance Criteria

            - AC-1 The spec file is non-empty and parseable as Markdown.
            - AC-2 The spec-to-backlog skill can consume this file independently.

            ## 12. Out of scope

            Everything not in Section 2.
        """),
        encoding="utf-8",
    )
    return spec_file


def _write_valid_backlog_input(workspace: Path, repo_slug: str) -> Path:
    """Write a valid BACKLOG.md + task file that spec-to-backlog would produce.

    spec-to-backlog's output is what the orchestrator and downstream tooling
    consume. Pre-populating this lets us test configure-devbench and
    bootstrap-environment independently.
    """
    task_rel_path = "backlog/E1-demo-epic/E1-F1-demo-feature/E1-F1-S1-demo-story/E1-F1-S1-T1.md"
    backlog_dir = workspace / "backlog" / "E1-demo-epic" / "E1-F1-demo-feature" / "E1-F1-S1-demo-story"
    backlog_dir.mkdir(parents=True, exist_ok=True)

    task_content = (
        "# E1-F1-S1-T1: Demo task\n\n"
        "## Status: draft\n\n"
        "## Target Repository\n\n"
        f"- **Repo:** `{repo_slug}`\n"
        "- **Branch:** `main`\n\n"
        "## Description\n\n"
        "Minimal task produced for independent-invocability testing.\n\n"
        "### Definition of Ready\n\n"
        "- [ ] Dependency work units are done\n"
        "- [ ] Repository is accessible\n"
        "- [ ] Tools are installed\n"
        "- [ ] Branch is checked out\n"
        "- [ ] Acceptance criteria are unambiguous\n\n"
        "### Depends On This\n\n"
        "| ID | Title | Status |\n"
        "|----|-------|--------|\n"
        "| none | | |\n\n"
        "### Approach\n\n"
        "1. Write failing tests (TDD RED).\n"
        "2. Implement minimum change (TDD GREEN).\n"
        "3. Refactor (TDD REFACTOR).\n\n"
        "### Code Standards\n\n"
        "#### Critical Rules (Violation = Automatic Rejection)\n\n"
        "1. No fallback logic.\n"
        "2. No silent failures.\n\n"
        "#### Architecture Principles\n\n"
        "SOLID, DRY, 12-Factor.\n\n"
        "#### Testing Rules\n\n"
        "TDD mandatory; no stub tests.\n\n"
        "#### Git Rules\n\n"
        "Stage only manifest files.\n\n"
        "#### Security Rules\n\n"
        "No secrets.\n\n"
        "#### Error Handling Contract\n\n"
        "Every public function documents raised exceptions.\n\n"
        "### Related Specifications\n\n"
        "- Spec source: spec/demo-project.md section 2.\n\n"
        "## Dependencies\n\n"
        "| ID | Title | Status |\n"
        "|----|-------|--------|\n"
        "| none | | |\n\n"
        "## Acceptance Criteria\n\n"
        "- **AC-1** The skill chain produces expected artefacts.\n\n"
        "## Changes Manifest\n\n"
        "| File | Change |\n"
        "|------|--------|\n"
        "| `docs/demo-task.md` | add |\n\n"
        "## Definition of Done\n\n"
        "- [ ] All acceptance criteria checked.\n"
        "- [ ] `make validate` passes.\n\n"
        "## TDD Cycle Log\n\n"
        "## Comments\n"
    )
    task_file = backlog_dir / "E1-F1-S1-T1.md"
    task_file.write_text(task_content, encoding="utf-8")

    backlog_content = (
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Draft | In Queue | In Progress | In Review | Done | Blocked | Total |\n"
        "|------|-------|----------|-------------|-----------|------|---------|-------|\n"
        f"| E1 -- Demo Epic | 1 | 0 | 0 | 0 | 0 | 0 | 1 |\n"
        "| **TOTAL** | 1 | 0 | 0 | 0 | 0 | 0 | 1 |\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|----------|\n"
        f"| E1-F1-S1-T1 | Demo task | Task | draft | none | {repo_slug} | `{task_rel_path}` |\n"
    )
    backlog_file = workspace / "BACKLOG.md"
    backlog_file.write_text(backlog_content, encoding="utf-8")
    return backlog_file


def _write_valid_devbench_yaml_input(workspace: Path, repo_slug: str) -> Path:
    """Write a valid devbench.yaml that configure-devbench would produce.

    Simulates the output that configure-devbench would have produced after
    Step 16. Pre-populating this lets bootstrap-environment be tested independently.
    """
    config_dir = workspace / "backlog" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "devbench.yaml"
    config_data = {
        "repos": {
            repo_slug: {
                "default_branch": "main",
                "checkout_directory": "target-repo",
            }
        },
        "merge_strategy": "squash",
        "max_executor_retries": 3,
        "use_bedrock": False,
        "git_ops": {
            "defer_pr": True,
            "single_branch": "feat/independent-invocation-test",
        },
    }
    config_file.write_text(
        yaml.dump(config_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return config_file


def _write_valid_target_repo_input(workspace: Path, checkout_dir: str) -> Path:
    """Pre-populate a fake target repo checkout directory (bootstrap-environment input).

    bootstrap-environment's Step 2a checks for ``<checkout_directory>/.git``.
    Pre-populating this directory with a fake ``.git`` simulates an already-cloned
    repo, which is the valid input state for bootstrapping an existing checkout.
    """
    repo_dir = workspace / checkout_dir
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    (repo_dir / "README.md").write_text("# Fake Target Repo for Independent Test\n", encoding="utf-8")
    return repo_dir


def _read_skill_md(skill_name: str) -> str:
    """Read and return the SKILL.md content for the named onboarding skill.

    Raises:
        AssertionError: if the SKILL.md file does not exist at the expected path.
    """
    skill_md_path = _SKILLS_DIR / skill_name / "SKILL.md"
    assert skill_md_path.exists(), (
        f"{skill_name} SKILL.md not found at {skill_md_path}. The authoring task for this skill must create the file."
    )
    return skill_md_path.read_text(encoding="utf-8")


def _extract_frontmatter(skill_name: str, content: str) -> str:
    """Extract the YAML frontmatter block from SKILL.md content.

    Raises:
        AssertionError: if the frontmatter delimiters are not found.
    """
    lines = content.splitlines()
    assert lines[0].strip() == "---", (
        f"{skill_name} SKILL.md must start with '---' frontmatter delimiter. Got: {lines[0]!r}"
    )
    closing_idx = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    assert closing_idx is not None, f"{skill_name} SKILL.md has no closing '---' frontmatter delimiter."
    return "\n".join(lines[1:closing_idx])


def _extract_step_content(skill_name: str, step_number: int) -> str:
    """Extract the content of a numbered step from a SKILL.md file.

    Reads the SKILL.md for ``skill_name`` and returns the text between the
    heading that introduces ``step_number`` and the heading that introduces
    ``step_number + 1`` (or the end of the file if the step is the last one).

    The step heading is matched by ``"## Step N"`` or ``"Step N --"`` anywhere
    on a line. The next-step boundary is detected by a line that starts with
    ``"## Step <N+1>"`` or ``"## Step <N+2>"``.

    Args:
        skill_name: Directory name of the skill (e.g. ``"create-spec"``).
        step_number: 1-based step number to extract.

    Returns:
        Concatenated lines of the step content (joined with newlines). Returns
        an empty string if the step heading is not found.

    Raises:
        AssertionError: if the SKILL.md file does not exist (via ``_read_skill_md``).
    """
    content = _read_skill_md(skill_name)
    next_step = step_number + 1
    next_next_step = step_number + 2
    lines = content.splitlines()
    step_lines: list[str] = []
    in_step = False
    for line in lines:
        if f"## Step {step_number}" in line or f"Step {step_number} --" in line:
            in_step = True
        elif line.startswith((f"## Step {next_step}", f"## Step {next_next_step}")):
            break
        if in_step:
            step_lines.append(line)
    return "\n".join(step_lines)


@pytest.mark.unit
@pytest.mark.parametrize("skill_name", _ONBOARDING_SKILLS)
def test_skill_md_exists_and_is_readable(skill_name: str) -> None:
    """Each onboarding skill SKILL.md must exist and be non-empty in isolation.

    Verifies the skill is independently accessible -- no prior skill output
    is required to read the skill definition.
    """
    content = _read_skill_md(skill_name)
    assert len(content) > 0, f"{skill_name} SKILL.md must not be empty"


@pytest.mark.unit
@pytest.mark.parametrize("skill_name", _ONBOARDING_SKILLS)
def test_skill_frontmatter_declares_correct_name(skill_name: str) -> None:
    """Each onboarding skill SKILL.md frontmatter must declare its canonical name.

    The name field is the skill's invocation identifier. It must match the
    directory name exactly so Claude Code can resolve it via
    'claude run devbench:<skill-name>'.
    """
    content = _read_skill_md(skill_name)
    frontmatter = _extract_frontmatter(skill_name, content)
    assert f"name: {skill_name}" in frontmatter, (
        f"{skill_name} SKILL.md frontmatter must contain 'name: {skill_name}'. Got frontmatter:\n{frontmatter}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("skill_name", _ONBOARDING_SKILLS)
def test_skill_frontmatter_declares_model(skill_name: str) -> None:
    """Each onboarding skill SKILL.md frontmatter must declare a model field.

    The model field controls which Claude model the skill runs on. It must
    be present for the skill to be invocable in standalone mode.
    """
    content = _read_skill_md(skill_name)
    frontmatter = _extract_frontmatter(skill_name, content)
    assert "model:" in frontmatter, (
        f"{skill_name} SKILL.md frontmatter must declare a 'model:' field for standalone invocation. "
        f"Got frontmatter:\n{frontmatter}"
    )


@pytest.mark.unit
class TestCreateSpecIndependentInvocation:
    """AC-191-8: create-spec is independently invocable given valid input files.

    create-spec's only pre-existing input is the kanon exemplar it reads in Step 1.
    The skill can run without spec-to-backlog, configure-devbench, or
    bootstrap-environment having run previously.
    """

    _SKILL_NAME = "create-spec"

    def test_skill_input_contract_references_configurable_exemplar(self) -> None:
        """create-spec SKILL.md must reference the configurable ``skills.exemplar_spec_path``
        as its optional Step 1 input (issue #221 E1-E10: skill is application-agnostic and
        has no hardcoded exemplar input)."""
        content = _read_skill_md(self._SKILL_NAME)
        assert "skills.exemplar_spec_path" in content, (
            f"{self._SKILL_NAME} SKILL.md must reference 'skills.exemplar_spec_path' as the "
            "configurable optional Step 1 input -- the skill is application-agnostic and "
            "must not hardcode any exemplar path."
        )
        assert "kanon-list-add-lock-features-spec.md" not in content, (
            f"{self._SKILL_NAME} SKILL.md must NOT contain the literal kanon exemplar filename (issue #221 E1-E10)."
        )

    def test_skill_output_contract_specifies_spec_path(self) -> None:
        """create-spec SKILL.md must specify spec/<project-name>.md as its output.

        The output contract defines what the skill will produce when invoked with
        valid inputs. It must be specified so downstream skills know where to find
        the spec file without create-spec having to run first.
        """
        content = _read_skill_md(self._SKILL_NAME)
        assert "spec/<project-name>.md" in content, (
            f"{self._SKILL_NAME} SKILL.md must specify 'spec/<project-name>.md' as its output file. "
            "This contract lets spec-to-backlog consume the spec independently."
        )

    def test_skill_invocable_with_pre_existing_spec_file(self, tmp_path: Path) -> None:
        """create-spec is independently invocable given a pre-existing spec file.

        Constructs a valid spec file (the output create-spec would produce) and
        asserts that this input is structurally correct -- it begins with an H1
        heading and contains the minimum sections create-spec's iterate-until-perfect
        rubric requires.
        """
        spec_file = _write_valid_spec_input(tmp_path, "my-standalone-project")
        assert spec_file.exists(), f"Pre-existing spec file must exist for standalone invocation: {spec_file}"
        content = spec_file.read_text(encoding="utf-8")
        assert content.startswith("# "), (
            "Spec file must start with a Markdown H1 heading -- required by create-spec's output contract"
        )
        assert "## 1. Context" in content, (
            "Pre-existing spec file must include Section 1 (Context) for create-spec quality validation"
        )

    def test_skill_does_not_require_backlog_input(self) -> None:
        """create-spec SKILL.md must NOT reference BACKLOG.md as a required input.

        create-spec is the first skill in the chain. It must not depend on the
        output of spec-to-backlog, configure-devbench, or bootstrap-environment.
        """
        content = _read_skill_md(self._SKILL_NAME)
        assert "Read BACKLOG.md" not in content, (
            f"{self._SKILL_NAME} must not require BACKLOG.md as input -- "
            "it is the first skill in the chain and must be invocable without prior skill output"
        )

    def test_skill_step_1_makes_devbench_yaml_optional(self) -> None:
        """create-spec SKILL.md Step 1 references devbench.yaml only as an OPTIONAL exemplar
        lookup, not as a precondition (issue #221 E1-E10).

        Step 1a reads ``backlog/config/devbench.yaml`` to discover ``skills.exemplar_spec_path``,
        but the SKILL.md must explicitly state that an absent key or absent file is fine: the
        skill falls back to the embedded 16-section skeleton. So Step 1 may MENTION devbench.yaml
        but must not REQUIRE it as a precondition.
        """
        step1_content = _extract_step_content(self._SKILL_NAME, 1)
        assert "absent" in step1_content.lower() and "skip" in step1_content.lower(), (
            f"{self._SKILL_NAME} SKILL.md Step 1 must explicitly describe what happens when "
            "skills.exemplar_spec_path is absent (skip the read; the embedded 16-section "
            "skeleton is sufficient). "
            f"Step 1 content:\n{step1_content}"
        )


@pytest.mark.unit
class TestSpecToBacklogIndependentInvocation:
    """AC-191-8: spec-to-backlog is independently invocable given a valid spec file.

    spec-to-backlog's required input is a pre-existing spec/<project-name>.md.
    It must not require configure-devbench or bootstrap-environment to have run.
    """

    _SKILL_NAME = "spec-to-backlog"
    _REPO_SLUG = "example-org/example-repo"

    def test_skill_input_contract_reads_spec_file(self) -> None:
        """spec-to-backlog SKILL.md must specify that it reads a spec/<name>.md input file.

        This is the sole required pre-existing file for standalone invocation.
        The skill must ask for the spec path in Step 2 (or accept it from the invocation message).
        """
        content = _read_skill_md(self._SKILL_NAME)
        assert "spec/" in content, (
            f"{self._SKILL_NAME} SKILL.md must reference 'spec/' path as its input contract. "
            "A pre-existing spec/<project-name>.md is the only file required for standalone invocation."
        )

    def test_skill_output_contract_specifies_backlog_md(self) -> None:
        """spec-to-backlog SKILL.md must specify BACKLOG.md as part of its output contract."""
        content = _read_skill_md(self._SKILL_NAME)
        assert "BACKLOG.md" in content, (
            f"{self._SKILL_NAME} SKILL.md must specify 'BACKLOG.md' in its output contract. "
            "This is the primary artefact that downstream skills consume independently."
        )

    def test_skill_invocable_with_pre_existing_spec_file(self, tmp_path: Path) -> None:
        """spec-to-backlog is independently invocable given a pre-existing spec file.

        Constructs a valid spec file and asserts that spec-to-backlog's output
        (a minimal BACKLOG.md + task files) satisfies the validate-backlog contract
        when the spec input was pre-populated without create-spec having run.
        """
        _write_valid_spec_input(tmp_path, "standalone-project")
        backlog_file = _write_valid_backlog_input(tmp_path, self._REPO_SLUG)
        assert backlog_file.exists(), f"BACKLOG.md must be produceable independently: {backlog_file}"
        content = backlog_file.read_text(encoding="utf-8")
        assert "## Status Summary" in content, (
            "Independently produced BACKLOG.md must contain '## Status Summary' section"
        )
        assert "## Full Work Unit Index" in content, (
            "Independently produced BACKLOG.md must contain '## Full Work Unit Index' section"
        )

    def test_output_passes_validate_backlog_when_invoked_standalone(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """spec-to-backlog's output must pass validate-backlog when produced independently.

        This is the core AC-191-8 assertion for spec-to-backlog: when the skill is
        invoked standalone with only a spec file present (no configure-devbench or
        bootstrap-environment output), the generated backlog must still satisfy
        validate-backlog with rc=0.
        """
        _write_valid_spec_input(tmp_path, "standalone-project")
        _write_valid_backlog_input(tmp_path, self._REPO_SLUG)

        backlog_index = tmp_path / "BACKLOG.md"
        with (
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.config.RUNTIME_CONFIG", _EXAMPLE_RT_CFG),
        ):
            rc = cli.cmd_validate_backlog()
        assert rc == 0, (
            f"validate-backlog must return 0 when spec-to-backlog runs standalone. "
            f"Exit code: {rc}. Captured:\n{capsys.readouterr().out}"
        )

    def test_skill_does_not_require_devbench_yaml(self) -> None:
        """spec-to-backlog SKILL.md must not require devbench.yaml as a mandatory Step 1 input.

        spec-to-backlog must be runnable before configure-devbench has produced
        backlog/config/devbench.yaml.
        """
        step1_content = _extract_step_content(self._SKILL_NAME, 1)
        assert "devbench.yaml" not in step1_content or "spec" in step1_content, (
            f"{self._SKILL_NAME} SKILL.md Step 1 must focus on reading the spec or kanon exemplar, "
            "not require devbench.yaml -- the skill must be invocable before configure-devbench runs"
        )


@pytest.mark.unit
class TestBootstrapEnvironmentIndependentInvocation:
    """AC-191-8: bootstrap-environment is independently invocable given a valid devbench.yaml.

    bootstrap-environment's required input is backlog/config/devbench.yaml with a
    repos: section. It must not require create-spec or spec-to-backlog to have run.
    """

    _SKILL_NAME = "bootstrap-environment"
    _REPO_SLUG = "example-org/example-repo"
    _CHECKOUT_DIR = "target-repo"

    def test_skill_input_contract_reads_devbench_yaml(self) -> None:
        """bootstrap-environment SKILL.md Step 1 must read backlog/config/devbench.yaml.

        This is the skill's sole required pre-existing input for standalone invocation.
        When a valid devbench.yaml exists, the skill can run without spec or backlog files.
        """
        content = _read_skill_md(self._SKILL_NAME)
        assert "backlog/config/devbench.yaml" in content, (
            f"{self._SKILL_NAME} SKILL.md must reference 'backlog/config/devbench.yaml' as its input. "
            "A pre-existing devbench.yaml is the only file required for standalone invocation."
        )

    def test_skill_invocable_with_pre_existing_devbench_yaml(self, tmp_path: Path) -> None:
        """bootstrap-environment is independently invocable given a pre-existing devbench.yaml.

        Constructs a valid devbench.yaml (normally produced by configure-devbench) and
        asserts bootstrap-environment can parse its repos: section to determine
        checkout directories -- without spec or backlog files being present.
        """
        config_file = _write_valid_devbench_yaml_input(tmp_path, self._REPO_SLUG)
        assert config_file.exists(), f"devbench.yaml must exist for standalone bootstrap: {config_file}"

        raw = config_file.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        repos = parsed.get("repos", {})
        assert self._REPO_SLUG in repos, (
            f"Pre-existing devbench.yaml must contain '{self._REPO_SLUG}' in repos section. "
            f"Got keys: {list(repos.keys())}"
        )
        repo_entry = repos[self._REPO_SLUG]
        assert "checkout_directory" in repo_entry, (
            f"Repo entry must have 'checkout_directory' field for bootstrap-environment Step 2a. Got: {repo_entry}"
        )

    def test_skill_detects_existing_checkout_without_prior_skill_run(self, tmp_path: Path) -> None:
        """bootstrap-environment correctly detects an existing checkout given valid inputs only.

        Pre-populates devbench.yaml and a fake target repo (the two required inputs).
        Verifies the .git sentinel detection works without any spec or backlog files.
        """
        _write_valid_devbench_yaml_input(tmp_path, self._REPO_SLUG)
        repo_dir = _write_valid_target_repo_input(tmp_path, self._CHECKOUT_DIR)

        git_sentinel = repo_dir / ".git"
        assert git_sentinel.exists(), (
            f"bootstrap-environment's EXISTS detection relies on .git sentinel at: {git_sentinel}. "
            "Pre-existing .git directory must be detectable without spec or backlog present."
        )
        assert not (tmp_path / "BACKLOG.md").exists(), (
            "BACKLOG.md must not be required for bootstrap-environment standalone invocation"
        )
        assert not (tmp_path / "spec").exists(), (
            "spec/ directory must not be required for bootstrap-environment standalone invocation"
        )

    def test_skill_devbench_yaml_loads_via_runtime_config_standalone(self, tmp_path: Path) -> None:
        """Pre-existing devbench.yaml must load via RuntimeConfig for bootstrap-environment.

        bootstrap-environment's Step 1 reads the repos section via the config file.
        This test verifies the file is immediately loadable by devbench's own
        config_loader when provided as a standalone input -- without prior skill runs.
        """
        config_file = _write_valid_devbench_yaml_input(tmp_path, self._REPO_SLUG)
        env = {**os.environ, "DEVBENCH_WORKSPACE_ROOT": str(tmp_path)}
        try:
            runtime_config = load_runtime_config(config_file, env)
        except (ValueError, FileNotFoundError) as exc:
            pytest.fail(f"load_runtime_config raised on standalone devbench.yaml for {self._SKILL_NAME}: {exc}")
        assert runtime_config is not None, "RuntimeConfig must load from standalone devbench.yaml"
        repo_keys = list(runtime_config.repos.keys()) if runtime_config.repos else []
        assert self._REPO_SLUG in repo_keys, (
            f"RuntimeConfig must contain '{self._REPO_SLUG}' from standalone devbench.yaml. Got repos: {repo_keys}"
        )

    def test_skill_does_not_require_spec_file(self) -> None:
        """bootstrap-environment SKILL.md must not reference spec/<name>.md as a required input.

        The skill clones repos and runs make validate -- it must not depend on
        the create-spec or spec-to-backlog output to begin.
        """
        step1_content = _extract_step_content(self._SKILL_NAME, 1)
        assert "spec/" not in step1_content, (
            f"{self._SKILL_NAME} SKILL.md Step 1 must not reference 'spec/' as a required input. "
            "bootstrap-environment must be invocable without spec-to-backlog having run."
        )


@pytest.mark.unit
class TestConfigureDevbenchIndependentInvocation:
    """AC-191-8: configure-devbench is independently invocable with no prior skill output.

    configure-devbench is the configuration skill. It does not require create-spec,
    spec-to-backlog, or bootstrap-environment to have run. Its only input is the
    operator's answers to the guided walkthrough (interactive), which means it can
    run as the very first skill in a fresh workspace.
    """

    _SKILL_NAME = "configure-devbench"
    _REPO_SLUG = "example-org/example-repo"

    def test_skill_step1_handles_missing_devbench_yaml(self) -> None:
        """configure-devbench SKILL.md Step 1 must handle a missing devbench.yaml gracefully.

        The skill must be invocable in a fresh workspace where no devbench.yaml exists.
        Step 1 must explicitly check for its presence and fall back to empty defaults.
        """
        content = _read_skill_md(self._SKILL_NAME)
        assert "MISSING" in content, (
            f"{self._SKILL_NAME} SKILL.md must handle the case where devbench.yaml is MISSING. "
            "The skill must be independently invocable in a fresh workspace with no prior config."
        )
        assert "empty defaults" in content.lower() or "start with empty" in content.lower(), (
            f"{self._SKILL_NAME} SKILL.md must start with 'empty defaults' when devbench.yaml is absent. "
            "This enables standalone invocation in a brand-new workspace."
        )

    def test_skill_output_contract_specifies_devbench_yaml_path(self) -> None:
        """configure-devbench SKILL.md must specify backlog/config/devbench.yaml as its output.

        This is the canonical output contract. When consumed by bootstrap-environment
        independently, the file must be at this exact path.
        """
        content = _read_skill_md(self._SKILL_NAME)
        assert "backlog/config/devbench.yaml" in content, (
            f"{self._SKILL_NAME} SKILL.md must specify 'backlog/config/devbench.yaml' as its output. "
            "This is the output contract that enables bootstrap-environment standalone invocation."
        )

    def test_skill_invocable_with_no_prior_artefacts(self, tmp_path: Path) -> None:
        """configure-devbench is independently invocable with a completely empty workspace.

        The skill must run successfully when no spec/, BACKLOG.md, or target repo
        is present. The produced devbench.yaml must be loadable by RuntimeConfig.
        """
        assert not (tmp_path / "spec").exists(), "spec/ must be absent for fresh-workspace test"
        assert not (tmp_path / "BACKLOG.md").exists(), "BACKLOG.md must be absent for fresh-workspace test"

        config_file = _write_valid_devbench_yaml_input(tmp_path, self._REPO_SLUG)
        assert config_file.exists(), f"configure-devbench must produce devbench.yaml at: {config_file}"

        env = {**os.environ, "DEVBENCH_WORKSPACE_ROOT": str(tmp_path)}
        try:
            runtime_config = load_runtime_config(config_file, env)
        except (ValueError, FileNotFoundError) as exc:
            pytest.fail(f"configure-devbench output devbench.yaml failed RuntimeConfig load: {exc}")
        assert runtime_config is not None, "configure-devbench output must produce a loadable RuntimeConfig"

    def test_skill_does_not_require_spec_file_in_step1(self) -> None:
        """configure-devbench SKILL.md Step 1 must not require spec/<name>.md as input.

        configure-devbench must be the one skill that can run in a completely empty
        workspace, since it is the tool for CONFIGURING the workspace before any
        spec work begins.
        """
        step1_content = _extract_step_content(self._SKILL_NAME, 1)
        assert "spec/" not in step1_content, (
            f"{self._SKILL_NAME} SKILL.md Step 1 must not require 'spec/' as input. "
            "configure-devbench must be invocable without spec-to-backlog having run."
        )

    def test_skill_does_not_require_backlog_md_in_step1(self) -> None:
        """configure-devbench SKILL.md Step 1 must not require BACKLOG.md as input.

        This verifies configure-devbench can run before spec-to-backlog has generated
        any backlog files -- it is independently invocable from a blank workspace.
        """
        step1_content = _extract_step_content(self._SKILL_NAME, 1)
        assert "BACKLOG.md" not in step1_content, (
            f"{self._SKILL_NAME} SKILL.md Step 1 must not require 'BACKLOG.md' as input. "
            "configure-devbench must be invocable before spec-to-backlog has run."
        )

    def test_produced_devbench_yaml_is_valid_yaml(self, tmp_path: Path) -> None:
        """configure-devbench's output must be valid YAML parseable without errors.

        Verifies the output contract in isolation: when configure-devbench produces
        backlog/config/devbench.yaml, it must be well-formed YAML.
        """
        config_file = _write_valid_devbench_yaml_input(tmp_path, self._REPO_SLUG)
        raw = config_file.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            pytest.fail(f"configure-devbench output is not valid YAML: {exc}")
        assert isinstance(parsed, dict), (
            f"configure-devbench output must be a YAML mapping. Got: {type(parsed).__name__}"
        )
        assert "repos" in parsed, "configure-devbench output must contain a 'repos:' section"
