"""Tests for AC-260-1: configure-devbench emits defaults equal to loader defaults.

Field-by-field equality is asserted for the sections whose defaults are
code-defined (not env-derived): task_factory, manifest_amendment, validate,
stop_hook, backlog, skills, quota_handling.

The "generated config" YAML is built from the annotated default values listed
in configure-devbench SKILL.md Steps 8-14. An empty YAML (only a repos entry)
produces the same loader defaults via code-path defaults. Both must be equal.
"""

from __future__ import annotations

import dataclasses
import textwrap
from pathlib import Path

import pytest

from devbench.config_loader import (
    AmendmentConfig,
    BacklogConfig,
    QuotaHandlingConfig,
    RuntimeConfig,
    SkillsConfig,
    StopHookConfig,
    TaskFactoryConfig,
    ValidateConfig,
    load_runtime_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_REPOS_YAML = textwrap.dedent("""\
    repos:
      example-org/example-repo:
        default_branch: main
        checkout_directory: example-repo
""")

# YAML block that mirrors every annotated default shown in configure-devbench
# SKILL.md for the sections with code-defined defaults: task_factory,
# manifest_amendment, validate, stop_hook, backlog, skills, quota_handling.
# Sections whose fields are env-derived (hook_tail, debug, orchestrate) are
# excluded because their loader fields default to None, not a fixed value.
_GENERATED_DEFAULTS_YAML = textwrap.dedent("""\
    repos:
      example-org/example-repo:
        default_branch: main
        checkout_directory: example-repo

    task_factory:
      enabled: true
      auto_accept_proposals: true

    manifest_amendment:
      enabled: true
      allowed_reasons:
        - tdd_green_production_fix
      max_requests_per_execution: 1

    validate:
      check_orphan_path_tokens: true

    stop_hook:
      max_blocks: 5
      window_seconds: 180
      stale_task_minutes: 120

    backlog:
      default_status_for_new_work_units: in-queue
      bulk_update_confirm_threshold: 10
      bulk_update_audit_path: logs/bulk-updates.log
      cascade_requeue_max_cycles: 3

    skills:
      fan_out_threshold: 10
      max_iterations: 5

    quota_handling:
      enabled: true
      on_exhaustion: wait
      poll_interval_seconds: 60
      max_wait_seconds: 18000
      on_exhaustion_timeout: drain
      resume_strategy: continue_current_wu
      audit_comment_on_wait: true
      audit_comment_on_resume: true
      log_structured_events: true
""")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _load(path: Path) -> RuntimeConfig:
    return load_runtime_config(path, {})


# ---------------------------------------------------------------------------
# AC-260-1: field-by-field equality for all relevant sections
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigLoaderDefaultsEquality:
    """AC-260-1: relevant config sections are field-by-field equal between
    an empty-config load and a configure-devbench-generated-config load.
    """

    @pytest.mark.parametrize(
        "section_name",
        [
            "task_factory",
            "manifest_amendment",
            "validate",
            "stop_hook",
            "backlog",
            "skills",
            "quota_handling",
        ],
    )
    def test_section_equals_empty_config_load(
        self,
        tmp_path: Path,
        section_name: str,
    ) -> None:
        """Each generated-config section must equal the empty-config section.

        Given: a YAML with only the required repos entry (empty config)
        And:   a YAML that mirrors every annotated default from SKILL.md
        When:  both are loaded via load_runtime_config
        Then:  the section dataclasses are field-by-field equal
        """
        empty_cfg = _load(_write(tmp_path, "empty.yaml", _MINIMAL_REPOS_YAML))
        generated_cfg = _load(_write(tmp_path, "generated.yaml", _GENERATED_DEFAULTS_YAML))

        empty_section = getattr(empty_cfg, section_name)
        generated_section = getattr(generated_cfg, section_name)

        # Use dataclasses.asdict for deep equality that surfaces field names.
        empty_dict = dataclasses.asdict(empty_section)
        generated_dict = dataclasses.asdict(generated_section)

        assert generated_dict == empty_dict, (
            f"Section '{section_name}' differs between empty-config load and "
            f"generated-config load.\n"
            f"  empty-config:    {empty_dict}\n"
            f"  generated-config: {generated_dict}"
        )

    def test_task_factory_enabled_default_is_true(self, tmp_path: Path) -> None:
        """task_factory.enabled loader default is True (not False).

        Verifies the dependency E5-F1-S1-T1 default flip has landed:
        an empty config must parse to task_factory.enabled == True.
        """
        cfg = _load(_write(tmp_path, "empty.yaml", _MINIMAL_REPOS_YAML))
        assert cfg.task_factory.enabled is True, (
            f"Expected task_factory.enabled=True (loader default), "
            f"got {cfg.task_factory.enabled!r}. "
            "The configure-devbench SKILL.md must annotate this default as 'true'."
        )

    def test_generated_config_task_factory_enabled_true(self, tmp_path: Path) -> None:
        """The generated YAML must emit task_factory.enabled: true.

        If SKILL.md annotates the wrong default (false), this YAML would
        encode false and the round-trip equality test above would catch it
        only indirectly. This dedicated test pins the value explicitly.
        """
        cfg = _load(_write(tmp_path, "generated.yaml", _GENERATED_DEFAULTS_YAML))
        assert cfg.task_factory.enabled is True, (
            f"Generated YAML task_factory.enabled must be True, got {cfg.task_factory.enabled!r}."
        )

    def test_all_relevant_sections_present_and_equal(self, tmp_path: Path) -> None:
        """Composite: every relevant section in generated config equals its empty-config peer.

        Combines all sections into one assertion so a single test run reveals
        ALL deviating sections, not just the first one.
        """
        empty_cfg = _load(_write(tmp_path, "empty.yaml", _MINIMAL_REPOS_YAML))
        generated_cfg = _load(_write(tmp_path, "generated.yaml", _GENERATED_DEFAULTS_YAML))

        section_names = [
            "task_factory",
            "manifest_amendment",
            "validate",
            "stop_hook",
            "backlog",
            "skills",
            "quota_handling",
        ]
        mismatches: list[str] = []
        for name in section_names:
            empty_dict = dataclasses.asdict(getattr(empty_cfg, name))
            generated_dict = dataclasses.asdict(getattr(generated_cfg, name))
            if generated_dict != empty_dict:
                mismatches.append(f"  {name}: empty={empty_dict!r} vs generated={generated_dict!r}")

        assert not mismatches, "Sections differ between empty-config and generated-config loads:\n" + "\n".join(
            mismatches
        )

    @pytest.mark.parametrize(
        ("section_name", "expected_type"),
        [
            ("task_factory", TaskFactoryConfig),
            ("manifest_amendment", AmendmentConfig),
            ("validate", ValidateConfig),
            ("stop_hook", StopHookConfig),
            ("backlog", BacklogConfig),
            ("skills", SkillsConfig),
            ("quota_handling", QuotaHandlingConfig),
        ],
    )
    def test_section_is_correct_type(
        self,
        tmp_path: Path,
        section_name: str,
        expected_type: type,
    ) -> None:
        """Generated-config sections are instances of the expected dataclass types."""
        cfg = _load(_write(tmp_path, "generated.yaml", _GENERATED_DEFAULTS_YAML))
        section = getattr(cfg, section_name)
        assert isinstance(section, expected_type), (
            f"Expected {section_name} to be {expected_type.__name__}, got {type(section).__name__}."
        )
