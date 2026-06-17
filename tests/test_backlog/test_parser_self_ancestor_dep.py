"""TDI-001: a Task that depends on one of its own ancestor containers must not self-block.

``BacklogParser._deps_satisfied`` treats a non-Task (Epic/Feature/Story)
dependency as satisfied only when EVERY descendant Task of that container is
terminal. When the depending Task is itself a descendant of the container it
lists as a dependency, the old logic required the Task to already be terminal
before it could start -- an unsatisfiable self-block that ``next`` reports as
``awaiting-dep`` rather than a cycle.

These tests pin the corrected semantics: the depending Task (and its own
subtree) is excluded from the ancestor's descendant set, so:

- a 1-Task story whose only Task depends on the parent story -> vacuously
  satisfied (the Task can start immediately);
- a multi-Task story -> the self-ancestor dep collapses to "wait for my
  siblings" with no self-deadlock.
"""

from __future__ import annotations

from pathlib import Path

from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

_NULL = Path("/dev/null")


def _unit(
    unit_id: str,
    unit_type: WorkUnitType,
    status: WorkUnitStatus,
    dependencies: list[str] | None = None,
) -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        title=unit_id,
        status=status,
        unit_type=unit_type,
        file_path=_NULL,
        repo="r",
        dependencies=dependencies or [],
    )


class TestSelfAncestorDepIsNotASelfBlock:
    def test_single_task_story_depending_on_parent_story_is_satisfied(self) -> None:
        """1-Task story: the only Task depends on its parent story -> satisfied.

        The story's only descendant Task is the depending Task itself; excluding
        it leaves an empty descendant set, so the dependency is vacuously
        satisfied and the Task is immediately actionable.
        """
        story = _unit("E0-F1-S1", WorkUnitType.STORY, WorkUnitStatus.IN_QUEUE)
        task = _unit(
            "E0-F1-S1-T1",
            WorkUnitType.TASK,
            WorkUnitStatus.IN_QUEUE,
            dependencies=["E0-F1-S1"],
        )
        units_by_id = {story.id: story, task.id: task}

        assert BacklogParser._deps_satisfied(task, units_by_id) is True

    def test_task_depending_on_parent_feature_is_satisfied(self) -> None:
        """Same self-block, one level up: Task depends on its ancestor Feature."""
        feature = _unit("E0-F1", WorkUnitType.FEATURE, WorkUnitStatus.IN_QUEUE)
        story = _unit("E0-F1-S1", WorkUnitType.STORY, WorkUnitStatus.IN_QUEUE)
        task = _unit(
            "E0-F1-S1-T1",
            WorkUnitType.TASK,
            WorkUnitStatus.IN_QUEUE,
            dependencies=["E0-F1"],
        )
        units_by_id = {feature.id: feature, story.id: story, task.id: task}

        assert BacklogParser._deps_satisfied(task, units_by_id) is True

    def test_task_depending_on_parent_epic_is_satisfied(self) -> None:
        """Top-level self-ancestor: Task depends on its ancestor Epic."""
        epic = _unit("E0", WorkUnitType.EPIC, WorkUnitStatus.IN_QUEUE)
        task = _unit(
            "E0-F1-S1-T1",
            WorkUnitType.TASK,
            WorkUnitStatus.IN_QUEUE,
            dependencies=["E0"],
        )
        units_by_id = {epic.id: epic, task.id: task}

        assert BacklogParser._deps_satisfied(task, units_by_id) is True

    def test_multi_task_story_self_ancestor_waits_only_on_siblings(self) -> None:
        """Multi-Task story: self-ancestor dep means "wait for my siblings".

        T1 depends on its parent story which also contains T2 (still in-queue).
        The depending Task (T1) is excluded from the descendant set but its
        sibling T2 is not, so the dep is NOT satisfied until T2 is terminal.
        """
        story = _unit("E0-F1-S1", WorkUnitType.STORY, WorkUnitStatus.IN_QUEUE)
        t1 = _unit(
            "E0-F1-S1-T1",
            WorkUnitType.TASK,
            WorkUnitStatus.IN_QUEUE,
            dependencies=["E0-F1-S1"],
        )
        t2 = _unit("E0-F1-S1-T2", WorkUnitType.TASK, WorkUnitStatus.IN_QUEUE)
        units_by_id = {story.id: story, t1.id: t1, t2.id: t2}

        assert BacklogParser._deps_satisfied(t1, units_by_id) is False

    def test_multi_task_story_self_ancestor_satisfied_once_sibling_done(self) -> None:
        """The same multi-Task story is satisfied once the sibling is terminal."""
        story = _unit("E0-F1-S1", WorkUnitType.STORY, WorkUnitStatus.IN_QUEUE)
        t1 = _unit(
            "E0-F1-S1-T1",
            WorkUnitType.TASK,
            WorkUnitStatus.IN_QUEUE,
            dependencies=["E0-F1-S1"],
        )
        t2 = _unit("E0-F1-S1-T2", WorkUnitType.TASK, WorkUnitStatus.DONE)
        units_by_id = {story.id: story, t1.id: t1, t2.id: t2}

        assert BacklogParser._deps_satisfied(t1, units_by_id) is True

    def test_genuine_cross_container_dep_still_blocks(self) -> None:
        """Regression guard: a dep on a DIFFERENT (non-ancestor) container is unchanged.

        The depending Task is not a descendant of the dep container, so the
        full descendant set is evaluated and an in-queue descendant still blocks.
        """
        other_story = _unit("E0-F2-S1", WorkUnitType.STORY, WorkUnitStatus.IN_QUEUE)
        other_task = _unit("E0-F2-S1-T1", WorkUnitType.TASK, WorkUnitStatus.IN_QUEUE)
        task = _unit(
            "E0-F1-S1-T1",
            WorkUnitType.TASK,
            WorkUnitStatus.IN_QUEUE,
            dependencies=["E0-F2-S1"],
        )
        units_by_id = {other_story.id: other_story, other_task.id: other_task, task.id: task}

        assert BacklogParser._deps_satisfied(task, units_by_id) is False
