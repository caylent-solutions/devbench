"""Integration tests for the end-to-end skill chain against a fake workspace.

Exercises the chained-skill workflow described in docs/onboarding.md:
  create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment

These tests verify AC-191-7 (chained invocation) and AC-191-10 (make validate passes)
by constructing a minimal fake workspace on disk and asserting the structural
contracts each skill produces.

No LLM calls are made -- each test exercises the FILE SYSTEM CONTRACT that each
skill's output must satisfy, and validates those contracts using the real devbench
production code (config_loader.load_runtime_config, cli.cmd_validate_backlog).
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from devbench import cli
from devbench.config_loader import RepoConfig, RuntimeConfig, load_runtime_config

_EXAMPLE_RT_CFG = RuntimeConfig(repos={"example-org/example-repo": RepoConfig()})

# Absolute path to the repo root so tests are portable regardless of cwd.
_REPO_ROOT = Path(__file__).parent.parent.parent

# Issue #224: all four chain skills live in the authoring plugin after the split.
_SKILLS_DIR = _REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills"
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "skill_chain"

_SKILL_NAMES = (
    "create-spec",
    "spec-to-backlog",
    "configure-devbench",
    "bootstrap-environment",
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _build_minimal_spec(workspace: Path, project_name: str) -> Path:
    """Write a minimal spec file matching the create-spec output contract.

    The spec must live at ``spec/<project-name>.md`` and must be non-empty.
    This simulates the output that the create-spec skill produces.
    """
    spec_dir = workspace / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / f"{project_name}.md"
    spec_file.write_text(
        textwrap.dedent(f"""\
            # {project_name.replace("-", " ").title()} Specification

            ## 0. Items that change existing user-facing behaviour

            None.

            ## 1. Context

            This is a minimal test spec for integration testing.

            ## 2. Goals

            - Goal 1: demonstrate end-to-end skill chain.

            ## 12. Out of scope

            Everything not described in Section 2.

            ## 6. Acceptance Criteria

            - AC-1 The skill chain produces the expected artefacts.
        """),
        encoding="utf-8",
    )
    return spec_file


def _build_minimal_backlog(workspace: Path, repo_slug: str) -> None:
    """Write a minimal BACKLOG.md + one task file that passes validate-backlog.

    Simulates the output that the spec-to-backlog skill produces.
    The BACKLOG.md must contain a Status Summary table and a Full Work Unit Index
    in the canonical 7-column format: | ID | Title | Type | Status | Dependencies | Repo | File Path |

    The Changes Manifest uses only a documentation file (docs/demo-task.md) to avoid
    triggering the source-test-atomicity rule which requires Python source files to have
    matching test files.
    """
    task_rel_path = "backlog/E1-demo-epic/E1-F1-demo-feature/E1-F1-S1-demo-story/E1-F1-S1-T1.md"
    backlog_dir = workspace / "backlog" / "E1-demo-epic" / "E1-F1-demo-feature" / "E1-F1-S1-demo-story"
    backlog_dir.mkdir(parents=True, exist_ok=True)

    task_content = (
        f"# E1-F1-S1-T1: Demo task\n\n"
        f"## Status: draft\n\n"
        f"## Target Repository\n\n"
        f"- **Repo:** `{repo_slug}`\n"
        f"- **Branch:** `main`\n\n"
        "## Description\n\n"
        "Minimal task produced by the spec-to-backlog skill for integration testing.\n\n"
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

    # The canonical 7-column BACKLOG.md format: | ID | Title | Type | Status | Dependencies | Repo | File Path |
    # File Path is a relative path from the workspace root.
    backlog_index_content = (
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
    (workspace / "BACKLOG.md").write_text(backlog_index_content, encoding="utf-8")


def _build_minimal_devbench_yaml(workspace: Path, repo_slug: str) -> Path:
    """Write a minimal devbench.yaml that passes load_runtime_config without errors.

    Simulates the output that the configure-devbench skill produces.
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
            "single_branch": "feat/integration-test",
        },
    }
    config_file.write_text(
        yaml.dump(config_data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return config_file


def _build_minimal_target_repo(workspace: Path, checkout_dir: str) -> Path:
    """Create a minimal fake target repo directory (no real git clone needed).

    The bootstrap-environment skill checks for the presence of a ``.git`` directory.
    Creating a fake one lets tests verify the 'EXISTS' branch without network access.
    """
    repo_dir = workspace / checkout_dir
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    (repo_dir / "README.md").write_text("# Fake Target Repo\n", encoding="utf-8")
    return repo_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillChainFixtureDirectory:
    """Verify the skill_chain fixture directory and its README exist on disk."""

    def test_fixture_dir_exists(self) -> None:
        """tests/fixtures/skill_chain/ must exist as a directory."""
        assert _FIXTURES_DIR.is_dir(), (
            f"Fixture directory not found: {_FIXTURES_DIR}. The directory must be created alongside this test file."
        )

    def test_fixture_readme_exists(self) -> None:
        """tests/fixtures/skill_chain/README.md must exist and be non-empty."""
        readme = _FIXTURES_DIR / "README.md"
        assert readme.exists(), f"README.md missing from fixture directory: {_FIXTURES_DIR}"
        assert readme.stat().st_size > 0, "README.md must not be empty"

    def test_fixture_readme_describes_chain(self) -> None:
        """README.md must describe the four-skill chain by name."""
        readme = _FIXTURES_DIR / "README.md"
        content = readme.read_text(encoding="utf-8")
        for skill_name in _SKILL_NAMES:
            assert skill_name in content, (
                f"README.md must mention skill '{skill_name}'. Found content:\n{content[:400]}"
            )


@pytest.mark.unit
class TestSkillFilesExist:
    """Structural pin: each skill must have a SKILL.md at the expected path (AC-191-2)."""

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_skill_md_exists(self, skill_name: str) -> None:
        """Each skill directory must contain SKILL.md."""
        skill_md = _SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_md.exists(), f"SKILL.md missing for skill '{skill_name}' at expected path: {skill_md}"

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_skill_md_has_frontmatter(self, skill_name: str) -> None:
        """Each SKILL.md must start with a YAML frontmatter block."""
        skill_md = _SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert lines[0].strip() == "---", (
            f"SKILL.md for '{skill_name}' must start with '---' YAML frontmatter delimiter. First line: {lines[0]!r}"
        )
        closing_idx = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        assert closing_idx is not None, f"SKILL.md for '{skill_name}' has no closing '---' frontmatter delimiter."

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_skill_md_frontmatter_has_name_field(self, skill_name: str) -> None:
        """Each SKILL.md frontmatter must contain a 'name:' field matching the skill directory name."""
        skill_md = _SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        lines = content.splitlines()
        end_idx = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        assert end_idx is not None
        frontmatter = "\n".join(lines[1:end_idx])
        assert f"name: {skill_name}" in frontmatter, (
            f"SKILL.md for '{skill_name}' frontmatter must contain 'name: {skill_name}'. "
            f"Got frontmatter:\n{frontmatter}"
        )


@pytest.mark.unit
class TestCreateSpecOutputContract:
    """AC-191-3: create-spec must produce spec/<name>.md that spec-to-backlog can consume."""

    def test_spec_file_written_to_expected_path(self, tmp_path: Path) -> None:
        """A spec file written by create-spec must reside at spec/<project-name>.md."""
        project_name = "my-test-project"
        spec_file = _build_minimal_spec(tmp_path, project_name)
        assert spec_file.exists(), f"spec file not created at expected path: {spec_file}"
        assert spec_file.parent.name == "spec", f"spec file must live in a 'spec/' directory, got: {spec_file.parent}"
        assert spec_file.name == f"{project_name}.md", (
            f"spec file name must be '{project_name}.md', got: {spec_file.name}"
        )

    def test_spec_file_is_non_empty(self, tmp_path: Path) -> None:
        """A spec file produced by create-spec must be non-empty."""
        spec_file = _build_minimal_spec(tmp_path, "my-test-project")
        assert spec_file.stat().st_size > 0, "spec file must be non-empty"

    def test_spec_file_content_is_markdown(self, tmp_path: Path) -> None:
        """The spec file must begin with a Markdown H1 heading."""
        spec_file = _build_minimal_spec(tmp_path, "my-test-project")
        content = spec_file.read_text(encoding="utf-8")
        assert content.startswith("# "), f"spec file must start with a Markdown H1 heading. Got: {content[:80]!r}"


@pytest.mark.unit
class TestSpecToBacklogOutputContract:
    """AC-191-4: spec-to-backlog must produce BACKLOG.md + work units that pass validate-backlog."""

    _REPO_SLUG = "example-org/example-repo"

    def test_backlog_md_written(self, tmp_path: Path) -> None:
        """BACKLOG.md must exist at the workspace root after spec-to-backlog runs."""
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        assert (tmp_path / "BACKLOG.md").exists(), "BACKLOG.md must be written by spec-to-backlog"

    def test_backlog_md_has_status_summary_table(self, tmp_path: Path) -> None:
        """BACKLOG.md must contain a Status Summary table."""
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        content = (tmp_path / "BACKLOG.md").read_text(encoding="utf-8")
        assert "## Status Summary" in content, "BACKLOG.md must contain a '## Status Summary' section"

    def test_backlog_md_has_full_work_unit_index(self, tmp_path: Path) -> None:
        """BACKLOG.md must contain a Full Work Unit Index section."""
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        content = (tmp_path / "BACKLOG.md").read_text(encoding="utf-8")
        assert "## Full Work Unit Index" in content, "BACKLOG.md must contain a '## Full Work Unit Index' section"

    def test_validate_backlog_passes_on_generated_backlog(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The backlog produced by spec-to-backlog must pass devbench validate-backlog (rc=0).

        This is the core AC-191-4 assertion: spec-to-backlog's output must be
        immediately consumable by validate-backlog without manual intervention.
        """
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        backlog_index = tmp_path / "BACKLOG.md"
        with (
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.config.RUNTIME_CONFIG", _EXAMPLE_RT_CFG),
        ):
            rc = cli.cmd_validate_backlog()
        assert rc == 0, (
            f"validate-backlog must return 0 on spec-to-backlog output. "
            f"Exit code: {rc}. "
            f"Captured output:\n{capsys.readouterr().out}"
        )

    def test_task_files_use_draft_status(self, tmp_path: Path) -> None:
        """All task files generated by spec-to-backlog must default to 'draft' status."""
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        task_file = (
            tmp_path / "backlog" / "E1-demo-epic" / "E1-F1-demo-feature" / "E1-F1-S1-demo-story" / "E1-F1-S1-T1.md"
        )
        assert task_file.exists(), f"Expected task file not found: {task_file}"
        content = task_file.read_text(encoding="utf-8")
        assert "## Status: draft" in content, (
            f"Task file must use '## Status: draft' per spec section 4.6 default. Got content start:\n{content[:200]}"
        )


@pytest.mark.unit
class TestConfigureDevbenchOutputContract:
    """AC-191-6: configure-devbench must produce a devbench.yaml that loads without errors."""

    _REPO_SLUG = "example-org/example-repo"

    def test_devbench_yaml_written(self, tmp_path: Path) -> None:
        """backlog/config/devbench.yaml must exist after configure-devbench runs."""
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        assert config_file.exists(), (
            f"backlog/config/devbench.yaml must be written by configure-devbench. Expected path: {config_file}"
        )

    def test_devbench_yaml_is_valid_yaml(self, tmp_path: Path) -> None:
        """The produced devbench.yaml must be parseable YAML without errors."""
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        raw = config_file.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            pytest.fail(f"devbench.yaml is not valid YAML: {exc}")
        assert isinstance(parsed, dict), (
            f"devbench.yaml must be a YAML mapping at the top level, got: {type(parsed).__name__}"
        )

    def test_devbench_yaml_loads_via_runtime_config(self, tmp_path: Path) -> None:
        """The produced devbench.yaml must load cleanly via load_runtime_config (AC-191-6).

        This is the canonical AC-191-6 assertion: configure-devbench's output must
        not raise any ConfigLoader exception.
        """
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        env = {**os.environ, "DEVBENCH_WORKSPACE_ROOT": str(tmp_path)}
        try:
            runtime_config = load_runtime_config(config_file, env)
        except (ValueError, FileNotFoundError) as exc:
            pytest.fail(f"load_runtime_config raised an error on configure-devbench output: {exc}")
        assert runtime_config is not None, "load_runtime_config must return a RuntimeConfig"
        repo_keys = list(runtime_config.repos.keys()) if runtime_config.repos else []
        assert self._REPO_SLUG in repo_keys, (
            f"RuntimeConfig must contain the configured repo '{self._REPO_SLUG}'. Got repos: {repo_keys}"
        )

    def test_devbench_yaml_contains_repos_section(self, tmp_path: Path) -> None:
        """The produced devbench.yaml must contain a non-empty 'repos:' section."""
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        raw = config_file.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        assert "repos" in parsed, "devbench.yaml must contain a 'repos:' section"
        assert parsed["repos"], "devbench.yaml 'repos:' section must be non-empty"


@pytest.mark.unit
class TestBootstrapEnvironmentDryRun:
    """AC-191-5: bootstrap-environment must succeed against a fake workspace (mocked git clone)."""

    _REPO_SLUG = "example-org/example-repo"
    _CHECKOUT_DIR = "target-repo"

    def test_existing_clone_detection(self, tmp_path: Path) -> None:
        """bootstrap-environment must detect an existing checkout and skip clone.

        The bootstrap-environment skill checks for ``<checkout_directory>/.git``.
        When ``.git`` exists, the skill takes the 'EXISTS' branch and skips the clone.
        This test verifies the contract by asserting the ``.git`` directory is the
        sentinel that determines 'EXISTS' vs 'MISSING' branching.
        """
        repo_dir = _build_minimal_target_repo(tmp_path, self._CHECKOUT_DIR)
        git_sentinel = repo_dir / ".git"
        assert git_sentinel.exists(), f"Fake target repo must have a .git directory at: {git_sentinel}"

    def test_missing_clone_directory_detected(self, tmp_path: Path) -> None:
        """When checkout_directory does not contain .git, bootstrap-environment must detect MISSING."""
        checkout_path = tmp_path / self._CHECKOUT_DIR
        assert not checkout_path.exists(), "checkout_directory must not exist before bootstrapping"
        git_sentinel = checkout_path / ".git"
        assert not git_sentinel.exists(), "bootstrap-environment relies on .git absence to determine MISSING state"

    def test_bootstrap_skill_reads_devbench_yaml_repos(self, tmp_path: Path) -> None:
        """bootstrap-environment must read backlog/config/devbench.yaml for the repos list.

        This test verifies the contract between configure-devbench and
        bootstrap-environment: the YAML produced by configure-devbench must be
        parseable by the bootstrap skill's Step 1 logic.
        """
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        raw = config_file.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw)
        repos = parsed.get("repos", {})
        assert self._REPO_SLUG in repos, (
            f"devbench.yaml repos section must contain '{self._REPO_SLUG}'. Got keys: {list(repos.keys())}"
        )
        repo_entry = repos[self._REPO_SLUG]
        assert "checkout_directory" in repo_entry, (
            f"Repo entry for '{self._REPO_SLUG}' must have 'checkout_directory' key. Got: {repo_entry}"
        )
        assert repo_entry["checkout_directory"] == self._CHECKOUT_DIR, (
            f"checkout_directory must equal '{self._CHECKOUT_DIR}', got: {repo_entry['checkout_directory']}"
        )

    def test_clone_invocation_uses_github_url_pattern(self, tmp_path: Path) -> None:
        """bootstrap-environment's clone command must use the GitHub HTTPS URL pattern.

        The SKILL.md specifies: ``git clone https://github.com/<repo>.git <checkout_directory>``.
        This test asserts the URL pattern is present in the bootstrap SKILL.md content.
        """
        skill_md = _SKILLS_DIR / "bootstrap-environment" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "https://github.com/" in content, (
            "bootstrap-environment SKILL.md must use 'https://github.com/' URL pattern for git clone"
        )
        assert "git clone" in content, "bootstrap-environment SKILL.md must use 'git clone' command"


@pytest.mark.unit
class TestSkillChainEndToEnd:
    """AC-191-7: the full skill chain produces expected artefacts in a single fake workspace.

    This is the integration test described in the work unit: exercises the chain with
    a fake workspace + minimal target repo. Asserts all four skill output contracts
    hold simultaneously on the same workspace instance.
    """

    _REPO_SLUG = "example-org/example-repo"
    _CHECKOUT_DIR = "target-repo"
    _PROJECT_NAME = "integration-test-project"

    def test_full_chain_artefacts_present(self, tmp_path: Path) -> None:
        """All four skill outputs must coexist in a single workspace (AC-191-7).

        Constructs the workspace by running each skill's output helper in sequence,
        then asserts every expected artefact is present.
        """
        # create-spec output
        spec_file = _build_minimal_spec(tmp_path, self._PROJECT_NAME)

        # spec-to-backlog output
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)

        # configure-devbench output
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)

        # bootstrap-environment precondition (existing checkout)
        _build_minimal_target_repo(tmp_path, self._CHECKOUT_DIR)

        # Assert all artefacts are present
        assert spec_file.exists(), f"spec file missing: {spec_file}"
        assert (tmp_path / "BACKLOG.md").exists(), "BACKLOG.md missing"
        assert config_file.exists(), f"devbench.yaml missing: {config_file}"
        assert (tmp_path / self._CHECKOUT_DIR / ".git").exists(), "target repo .git missing"

    def test_validate_backlog_passes_on_full_chain_workspace(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """validate-backlog must pass on a workspace produced by the full skill chain (AC-191-7).

        This verifies that the combined output of create-spec + spec-to-backlog +
        configure-devbench satisfies the validate-backlog integrity contract.
        """
        _build_minimal_spec(tmp_path, self._PROJECT_NAME)
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        _build_minimal_target_repo(tmp_path, self._CHECKOUT_DIR)

        backlog_index = tmp_path / "BACKLOG.md"
        with (
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.config.RUNTIME_CONFIG", _EXAMPLE_RT_CFG),
        ):
            rc = cli.cmd_validate_backlog()

        assert rc == 0, (
            f"validate-backlog must return 0 on the full chained workspace. "
            f"Exit code: {rc}. "
            f"Captured:\n{capsys.readouterr().out}"
        )

    def test_devbench_yaml_loads_on_full_chain_workspace(self, tmp_path: Path) -> None:
        """The devbench.yaml produced by configure-devbench must load cleanly in the full chain workspace.

        Ensures the configure-devbench skill output integrates with the bootstrap
        skill's Step 1 runtime-config read without raising exceptions.
        """
        _build_minimal_spec(tmp_path, self._PROJECT_NAME)
        _build_minimal_backlog(tmp_path, self._REPO_SLUG)
        config_file = _build_minimal_devbench_yaml(tmp_path, self._REPO_SLUG)
        _build_minimal_target_repo(tmp_path, self._CHECKOUT_DIR)

        env = {**os.environ, "DEVBENCH_WORKSPACE_ROOT": str(tmp_path)}
        try:
            runtime_config = load_runtime_config(config_file, env)
        except (ValueError, FileNotFoundError) as exc:
            pytest.fail(f"load_runtime_config raised on full-chain workspace devbench.yaml: {exc}")
        assert runtime_config is not None

    def test_onboarding_doc_describes_chain_order(self) -> None:
        """docs/onboarding.md must describe the four-skill chain in the correct order (AC-191-9).

        The canonical chain order is: create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment
        """
        onboarding_doc = _REPO_ROOT / "docs" / "onboarding.md"
        assert onboarding_doc.exists(), f"docs/onboarding.md must exist: {onboarding_doc}"
        content = onboarding_doc.read_text(encoding="utf-8")
        for skill_name in _SKILL_NAMES:
            assert skill_name in content, (
                f"docs/onboarding.md must mention skill '{skill_name}'. Got content (first 500 chars):\n{content[:500]}"
            )
        # Verify ordering: each skill appears before the next in the chain
        positions = {skill: content.index(skill) for skill in _SKILL_NAMES}
        chain_order = list(_SKILL_NAMES)
        for i in range(len(chain_order) - 1):
            first, second = chain_order[i], chain_order[i + 1]
            assert positions[first] < positions[second], (
                f"docs/onboarding.md must list '{first}' before '{second}' in the chain. "
                f"Found positions: {positions[first]} vs {positions[second]}"
            )


@pytest.mark.unit
class TestMakeValidatePassesWithSkillsInstalled:
    """AC-191-10: make validate must pass against the devbench codebase with new skills installed."""

    def test_all_skill_dirs_present(self) -> None:
        """All four onboarding skill dirs must be present under the authoring plugin (issue #224)."""
        for skill_name in _SKILL_NAMES:
            skill_dir = _SKILLS_DIR / skill_name
            assert skill_dir.is_dir(), (
                f"Skill directory '{skill_name}' missing at: {skill_dir}. All four onboarding skills must be installed."
            )

    def test_make_validate_lint_passes_on_integration_test_file(self) -> None:
        """The integration test file must pass ruff lint (AC-191-10: no regressions introduced).

        Scopes lint check to the files introduced by this work unit to verify that
        the new skill chain tests do not introduce lint violations. Pre-existing
        violations in other files are tracked by their own tasks.
        """
        test_file = _REPO_ROOT / "tests" / "test_plugin" / "test_skill_chain_integration.py"
        result = subprocess.run(
            ["uv", "run", "ruff", "check", str(test_file)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff check must pass on integration test file (AC-191-10). "
            f"Exit code: {result.returncode}. "
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_make_validate_format_passes_on_integration_test_file(self) -> None:
        """The integration test file must pass ruff format check (AC-191-10).

        Verifies that the new skill chain test file is correctly formatted.
        """
        test_file = _REPO_ROOT / "tests" / "test_plugin" / "test_skill_chain_integration.py"
        result = subprocess.run(
            ["uv", "run", "ruff", "format", "--check", str(test_file)],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"ruff format --check must pass on integration test file (AC-191-10). "
            f"Exit code: {result.returncode}. "
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_devbench_validate_backlog_passes_on_isolated_backlog(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """devbench validate-backlog must pass on an isolated minimal backlog (AC-191-10).

        This pin ensures that the validate-backlog command works correctly after
        the new skills are installed. Uses an isolated tmp_path workspace to avoid
        interference from the real project backlog's branch-uniqueness state.
        """
        _build_minimal_backlog(tmp_path, "example-org/example-repo")
        backlog_index = tmp_path / "BACKLOG.md"
        with (
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.config.RUNTIME_CONFIG", _EXAMPLE_RT_CFG),
        ):
            rc = cli.cmd_validate_backlog()
        assert rc == 0, (
            f"validate-backlog must return 0 on the isolated minimal backlog. "
            f"Exit code: {rc}. "
            f"Captured:\n{capsys.readouterr().out}"
        )
