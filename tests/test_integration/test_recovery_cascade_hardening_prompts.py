"""Issues #141, #142, #143, #144 regression: pin the agent-prompt + skill
rules added by the recovery-cascade hardening commit so a future revert
fails CI.

Mirrors the existing prompt-pinning patterns in
``test_security_review_scope.py`` /
``test_executor_review_pass_terminality.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.constants import DEFAULT_MAX_CASCADE_DEPTH

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate"
BLOCKER_RESOLVER = PLUGIN_ROOT / "agents" / "blocker-resolver.md"
MANIFEST_AMENDER = PLUGIN_ROOT / "agents" / "manifest-amender.md"
TASK_FACTORY = PLUGIN_ROOT / "agents" / "task-factory.md"


@pytest.fixture(scope="module")
def blocker_resolver_text() -> str:
    return BLOCKER_RESOLVER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest_amender_text() -> str:
    return MANIFEST_AMENDER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def task_factory_text() -> str:
    return TASK_FACTORY.read_text(encoding="utf-8")


@pytest.mark.integration
class TestBlockerResolverDedupContract:
    """Issue #141: dedup-contract section in blocker-resolver.md."""

    def test_dedup_section_present(self, blocker_resolver_text: str) -> None:
        assert "## Dedup contract (issue #141)" in blocker_resolver_text

    @pytest.mark.parametrize(
        "fragment",
        [
            "stable `fix_signature` hash",
            "scans `.devbench/proposals/*.json`",
            "auto-wires the new source task as an additional dep edge",
            '`"recovery_reused": true`',
            "`[RECOVERY_REUSED] reusing existing recovery task",
            "STOP after logging the verdict",
            "test_blocker_resolver_dedup.py",
        ],
    )
    def test_each_protective_fragment_present(self, blocker_resolver_text: str, fragment: str) -> None:
        assert fragment in blocker_resolver_text


@pytest.mark.integration
class TestManifestAmenderAutoDep:
    """Issue #142: auto-dep wiring on terminal-state Manifest conflicts."""

    def test_auto_dep_rule_present(self, manifest_amender_text: str) -> None:
        assert "auto-wire the dep (issue #142)" in manifest_amender_text

    @pytest.mark.parametrize(
        "fragment",
        [
            "auto-wire the dep",
            "uv run devbench add-dep <source-task-id> <conflict-task-id>",
            "[CONFLICT_AUTODEP]",
            "[CONFLICT_AUTODEP_FAILED]",
            "test_manifest_amender_auto_dep.py",
        ],
    )
    def test_each_protective_fragment_present(self, manifest_amender_text: str, fragment: str) -> None:
        assert fragment in manifest_amender_text


@pytest.mark.integration
class TestTaskFactoryCascadeDepthAndPlaceholderRules:
    """Issues #143 + #144: cascade-depth limit + placeholder rejection
    sections in task-factory.md."""

    def test_cascade_depth_section_present(self, task_factory_text: str) -> None:
        assert "## Cascade-depth limit (issue #144)" in task_factory_text

    def test_placeholder_section_present(self, task_factory_text: str) -> None:
        assert "## Materialise-time placeholder rejection (issue #143)" in task_factory_text

    @pytest.mark.parametrize(
        "fragment",
        [
            "orchestrate.max_cascade_depth",
            "DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH",
            "NEEDS_OPERATOR_ATTENTION",
            "parent_depth + 1",
            "test_cascade_depth_limit.py",
        ],
    )
    def test_each_cascade_depth_fragment_present(self, task_factory_text: str, fragment: str) -> None:
        assert fragment in task_factory_text

    @pytest.mark.parametrize(
        "fragment",
        [
            "scans `proposed_tasks[*].suggested_approach`",
            "empty / TODO / TBD placeholder values",
            "rejects the materialisation",
            "test_task_factory_todo_reject.py",
        ],
    )
    def test_each_placeholder_fragment_present(self, task_factory_text: str, fragment: str) -> None:
        assert fragment in task_factory_text


@pytest.mark.integration
class TestCascadeDepthDefaultValue:
    """Issue #144 (E8): pin the default cascade-depth cap to 2 so a future
    revert of the E8 change fails CI at this assertion."""

    def test_default_max_cascade_depth_is_2(self) -> None:
        assert DEFAULT_MAX_CASCADE_DEPTH == 2, (
            f"DEFAULT_MAX_CASCADE_DEPTH must be 2 (depth-2 escalation cap), got {DEFAULT_MAX_CASCADE_DEPTH}"
        )

    def test_task_factory_mentions_default_2(self, task_factory_text: str) -> None:
        assert "default 2" in task_factory_text, (
            "task-factory.md must document the cascade-depth cap default as 'default 2'"
        )
