"""Workflow-tool detection and chunked fan-out gating for the bundled skills.

Issue #266 E12-F1-S1: the ``create-spec`` and ``spec-to-backlog`` skills
can optionally fan out through the Claude Code Workflow tool when
``skills.use_workflow`` is true and the unit count exceeds the configured
threshold. This module exposes the pure decision logic so the SKILL.md
prompt files can import it without embedding orchestration logic here.

When the Workflow tool is absent (``use_workflow=False`` in config) this
module returns the fallback decision -- identical to the Agent / single-agent
behavior that existed before E12-F1-S1 -- so no operator action is needed
to preserve existing behavior (spec Section 4 E12-F1-S1 AC-2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FanOutMode(StrEnum):
    """The execution path chosen by ``decide_fan_out``.

    Attributes:
        WORKFLOW: Fan out through the Claude Code Workflow tool, chunked
            to at most ``workflow_chunk_size`` tasks per invocation.
        FALLBACK: Use the existing Agent / single-agent execution path
            without any change to behavior.
    """

    WORKFLOW = "workflow"
    FALLBACK = "fallback"


@dataclass
class FanOutDecision:
    """Result returned by ``decide_fan_out``.

    Attributes:
        mode: The chosen execution path.
        chunks: When ``mode`` is ``FanOutMode.WORKFLOW``, a list of
            non-empty sub-lists, each containing integer indices into
            the caller's task sequence. The union of all chunks covers
            ``range(task_count)`` exactly and no chunk exceeds
            ``workflow_chunk_size``. When ``mode`` is
            ``FanOutMode.FALLBACK``, this list is always empty.
    """

    mode: FanOutMode
    chunks: list[list[int]] = field(default_factory=list)


def chunk_tasks(tasks: list, chunk_size: int) -> list[list]:
    """Split *tasks* into consecutive sub-lists of at most *chunk_size* items.

    Args:
        tasks: The ordered sequence of tasks to partition.
        chunk_size: Maximum length of each chunk. Must be >= 1; zero or
            negative values raise ``ValueError`` immediately (fail-fast
            per spec Section 4 E12-F1-S1 error handling contract).

    Returns:
        A list of sub-lists. Every chunk except possibly the last has
        exactly ``chunk_size`` items; the last chunk contains the
        remainder. When ``tasks`` is empty the return value is ``[]``.
        No chunk in the result ever exceeds ``chunk_size`` items
        (AC-3 invariant).

    Raises:
        ValueError: When ``chunk_size`` is less than 1. The message
            names the key and the remedy so operators can fix it
            immediately.
    """
    if chunk_size < 1:
        raise ValueError(
            f"chunk_size must be >= 1; got {chunk_size!r}. "
            "Set skills.workflow_chunk_size to 3 or 4 to stay under provider rate limits."
        )
    if not tasks:
        return []
    return [tasks[i : i + chunk_size] for i in range(0, len(tasks), chunk_size)]


def should_fan_out(task_count: int, threshold: int) -> bool:
    """Return ``True`` when *task_count* strictly exceeds *threshold*.

    This is the threshold predicate shared by both authoring skills and
    reusable by the E12-F2 and E12-F3 fan-out phases. The comparison is
    strictly greater-than so a threshold of 10 means fan-out starts at
    task_count == 11.

    Args:
        task_count: Total number of tasks to be executed.
        threshold: The fan-out trigger level configured by the operator.

    Returns:
        ``True`` if and only if ``task_count > threshold``.
    """
    return task_count > threshold


def decide_fan_out(
    *,
    task_count: int,
    use_workflow: bool,
    workflow_chunk_size: int,
    fan_out_threshold: int,
) -> FanOutDecision:
    """Decide whether to use the Workflow tool and produce the chunk layout.

    This is the single entry-point the SKILL.md prompt files call. It
    encapsulates the full gating logic in one place so neither skill
    duplicates the threshold comparison or the chunk-clamp invariant.

    Behavior summary:

    - When ``use_workflow`` is ``False``: always return
      ``FanOutDecision(mode=FALLBACK, chunks=[])`` regardless of
      ``task_count``. This preserves byte-for-byte the existing Agent /
      single-agent behavior (spec Section 4 E12-F1-S1 AC-2).
    - When ``use_workflow`` is ``True`` but ``task_count`` does not
      strictly exceed ``fan_out_threshold``: return
      ``FanOutDecision(mode=FALLBACK, chunks=[])``.
    - When ``use_workflow`` is ``True`` and ``task_count`` strictly
      exceeds ``fan_out_threshold``: return
      ``FanOutDecision(mode=WORKFLOW, chunks=<chunked index list>)``
      where every chunk has at most ``workflow_chunk_size`` items
      (AC-3 invariant).

    Args:
        task_count: Total number of tasks the caller wants to execute.
        use_workflow: Value of ``skills.use_workflow`` after env/YAML
            resolution. When ``False`` the Workflow path is never taken.
        workflow_chunk_size: Maximum tasks per Workflow chunk. Forwarded
            to ``chunk_tasks``; must be >= 1 (fail-fast).
        fan_out_threshold: The ``skills.fan_out_threshold`` value after
            env/YAML resolution. Fan-out triggers only when
            ``task_count > fan_out_threshold``.

    Returns:
        A ``FanOutDecision`` whose ``mode`` and ``chunks`` the caller
        uses to branch between the Workflow and fallback code paths.

    Raises:
        ValueError: When ``workflow_chunk_size`` is less than 1, raised
            immediately via ``chunk_tasks`` with an actionable message.
    """
    if not use_workflow or not should_fan_out(task_count, fan_out_threshold):
        return FanOutDecision(mode=FanOutMode.FALLBACK, chunks=[])
    index_chunks = chunk_tasks(list(range(task_count)), chunk_size=workflow_chunk_size)
    return FanOutDecision(mode=FanOutMode.WORKFLOW, chunks=index_chunks)
