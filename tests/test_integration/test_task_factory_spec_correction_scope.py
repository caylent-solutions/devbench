"""Issue #136 regression: task-factory spec-correction recovery tasks must
list ONLY the work-unit markdown file in their Changes Manifest.

Background: when the auto-recovery cascade (blocker-resolver -> task-factory)
materialises a recovery task whose job is to remove or modify rows in
another work-unit's Manifest table, the recovery task is editing a
**markdown document**, not the source files referenced inside that
document's table. Listing those source files in the recovery task's
Manifest re-introduces the same Manifest Conflict the recovery task was
created to resolve.

Live evidence (2026-05-02): E2-F3-S2-T5 was materialised to remove
``pyproject.toml`` + ``Makefile`` rows from E2-F3-S2-T1's Manifest. The
factory wrote those source-file paths into T5's own Manifest. Result:
the next ``validate-backlog`` run reported the same Manifest Conflict on
``pyproject.toml`` (now claimed by 5 tasks including T5) and ``Makefile``
(now claimed by T1 + T5). The cascade's "fix" reintroduced the bug.

Fix: ``plugin/devbench-orchestrate/agents/task-factory.md`` adds an explicit
"spec-correction recovery tasks must list ONLY the work-unit markdown"
section. This test pins the rule by-content via the existing
``test_security_review_scope.py`` pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate" / "agents" / "task-factory.md"
)


@pytest.fixture(scope="module")
def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


@pytest.mark.integration
class TestTaskFactorySpecCorrectionScope:
    """Pin the issue #136 rule so the recovery cascade cannot re-create
    the very Manifest Conflict it was supposed to resolve."""

    def test_prompt_file_exists(self) -> None:
        assert PROMPT_PATH.is_file(), f"task-factory prompt missing at {PROMPT_PATH}"

    def test_critical_section_header_present(self, prompt_text: str) -> None:
        assert (
            "spec-correction recovery tasks must list ONLY the work-unit markdown file they edit (issue #136)"
            in prompt_text
        ), (
            "task-factory.md is missing the issue #136 section header. The "
            "header anchors the rule that prevents recovery-cascade-driven "
            "Manifest re-conflicts."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            "the draft's OWN Changes Manifest contains a single row pointing at",
            "draft MUST NOT list the source files referenced inside that table",
            "re-introduces the very Manifest Conflict the recovery task was created to resolve",
            "Self-correcting heuristic",
            "remove the X row",
            "test_task_factory_spec_correction_scope.py",
        ],
    )
    def test_each_protective_fragment_present(self, prompt_text: str, fragment: str) -> None:
        assert fragment in prompt_text, (
            f"task-factory.md is missing protective fragment: {fragment!r}. "
            "All fragments must be present so issue #136 cannot return."
        )
