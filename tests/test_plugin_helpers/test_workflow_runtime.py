"""Tests for ``devbench.plugin_helpers.workflow_runtime``.

E12-F1-S1-T1: Workflow detection, chunked fan-out configuration, and
single-agent fallback when the Workflow tool is absent.

Spec Section 4 E12-F1-S1 AC-1, AC-2, AC-3.
"""

from __future__ import annotations

import pytest

from devbench.plugin_helpers.workflow_runtime import (
    FanOutDecision,
    FanOutMode,
    chunk_tasks,
    decide_fan_out,
    should_fan_out,
)

# ---------------------------------------------------------------------------
# FanOutMode -- enum values
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFanOutMode:
    """FanOutMode enum has exactly the expected members."""

    def test_workflow_mode_value(self) -> None:
        assert FanOutMode.WORKFLOW == "workflow"

    def test_fallback_mode_value(self) -> None:
        assert FanOutMode.FALLBACK == "fallback"


# ---------------------------------------------------------------------------
# chunk_tasks -- AC-3: chunk size never exceeds configured value
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChunkTasks:
    """chunk_tasks splits an iterable into chunks of at most chunk_size items."""

    def test_exact_multiple(self) -> None:
        """Six tasks with chunk_size 3 produces two full chunks."""
        tasks = list(range(6))
        result = chunk_tasks(tasks, chunk_size=3)
        assert result == [[0, 1, 2], [3, 4, 5]]

    def test_partial_last_chunk(self) -> None:
        """Seven tasks with chunk_size 3 produces two full chunks and one partial."""
        tasks = list(range(7))
        result = chunk_tasks(tasks, chunk_size=3)
        assert result == [[0, 1, 2], [3, 4, 5], [6]]

    def test_single_chunk_when_fewer_than_chunk_size(self) -> None:
        """Two tasks with chunk_size 4 produces one chunk."""
        tasks = ["a", "b"]
        result = chunk_tasks(tasks, chunk_size=4)
        assert result == [["a", "b"]]

    def test_empty_input_returns_empty(self) -> None:
        """Empty task list yields empty list of chunks."""
        result = chunk_tasks([], chunk_size=3)
        assert result == []

    def test_chunk_size_one_every_task_separate(self) -> None:
        """chunk_size=1 produces one chunk per task."""
        tasks = [10, 20, 30]
        result = chunk_tasks(tasks, chunk_size=1)
        assert result == [[10], [20], [30]]

    @pytest.mark.parametrize("chunk_size", [0, -1, -5])
    def test_invalid_chunk_size_raises(self, chunk_size: int) -> None:
        """Non-positive chunk_size must raise ValueError with an actionable message."""
        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            chunk_tasks([1, 2, 3], chunk_size=chunk_size)

    def test_no_chunk_exceeds_configured_size(self) -> None:
        """AC-3: every chunk in the result has length <= chunk_size."""
        tasks = list(range(20))
        chunk_size = 4
        result = chunk_tasks(tasks, chunk_size=chunk_size)
        for chunk in result:
            assert len(chunk) <= chunk_size, f"chunk {chunk!r} exceeds size {chunk_size}"


# ---------------------------------------------------------------------------
# should_fan_out -- threshold predicate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShouldFanOut:
    """should_fan_out returns True when task_count strictly exceeds threshold."""

    @pytest.mark.parametrize(
        "task_count,threshold,expected",
        [
            (11, 10, True),
            (10, 10, False),
            (9, 10, False),
            (1, 0, True),
            (0, 0, False),
        ],
    )
    def test_threshold_boundary(self, task_count: int, threshold: int, expected: bool) -> None:
        assert should_fan_out(task_count, threshold) is expected


# ---------------------------------------------------------------------------
# decide_fan_out -- AC-1, AC-2: main decision function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecideFanOut:
    """decide_fan_out returns the correct FanOutDecision for all combinations."""

    def test_returns_fallback_when_use_workflow_false(self) -> None:
        """AC-2: when use_workflow is False, always return FALLBACK regardless of task count."""
        decision = decide_fan_out(
            task_count=100,
            use_workflow=False,
            workflow_chunk_size=4,
            fan_out_threshold=5,
        )
        assert decision.mode is FanOutMode.FALLBACK
        assert decision.chunks == []

    def test_returns_fallback_below_threshold_even_when_use_workflow_true(self) -> None:
        """When use_workflow is True but task_count does not exceed threshold, return FALLBACK."""
        decision = decide_fan_out(
            task_count=5,
            use_workflow=True,
            workflow_chunk_size=3,
            fan_out_threshold=10,
        )
        assert decision.mode is FanOutMode.FALLBACK
        assert decision.chunks == []

    def test_returns_workflow_above_threshold_with_use_workflow_true(self) -> None:
        """AC-1: above threshold with use_workflow True, return WORKFLOW mode with chunk list."""
        decision = decide_fan_out(
            task_count=12,
            use_workflow=True,
            workflow_chunk_size=4,
            fan_out_threshold=10,
        )
        assert decision.mode is FanOutMode.WORKFLOW
        # chunk_count = ceil(12 / 4) = 3
        assert len(decision.chunks) == 3
        # All chunks together cover all 12 indices
        all_items = [item for chunk in decision.chunks for item in chunk]
        assert sorted(all_items) == list(range(12))

    def test_chunk_size_respected_in_workflow_decision(self) -> None:
        """AC-3: no chunk in the decision exceeds workflow_chunk_size."""
        decision = decide_fan_out(
            task_count=10,
            use_workflow=True,
            workflow_chunk_size=3,
            fan_out_threshold=5,
        )
        assert decision.mode is FanOutMode.WORKFLOW
        for chunk in decision.chunks:
            assert len(chunk) <= 3

    def test_fallback_at_exact_threshold_with_use_workflow_true(self) -> None:
        """Exactly at threshold, fan-out does not trigger (strictly greater than)."""
        decision = decide_fan_out(
            task_count=10,
            use_workflow=True,
            workflow_chunk_size=4,
            fan_out_threshold=10,
        )
        assert decision.mode is FanOutMode.FALLBACK

    def test_invalid_chunk_size_raises(self) -> None:
        """A zero or negative workflow_chunk_size must raise ValueError."""
        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            decide_fan_out(
                task_count=20,
                use_workflow=True,
                workflow_chunk_size=0,
                fan_out_threshold=5,
            )

    @pytest.mark.parametrize("task_count", [0, 1, 5])
    def test_zero_or_small_task_count_with_use_workflow_false(self, task_count: int) -> None:
        """AC-2: any task_count with use_workflow=False returns FALLBACK."""
        decision = decide_fan_out(
            task_count=task_count,
            use_workflow=False,
            workflow_chunk_size=4,
            fan_out_threshold=10,
        )
        assert decision.mode is FanOutMode.FALLBACK


# ---------------------------------------------------------------------------
# FanOutDecision -- dataclass contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFanOutDecision:
    """FanOutDecision carries mode and chunks attributes."""

    def test_fallback_decision_has_empty_chunks(self) -> None:
        d = FanOutDecision(mode=FanOutMode.FALLBACK, chunks=[])
        assert d.mode is FanOutMode.FALLBACK
        assert d.chunks == []

    def test_workflow_decision_carries_chunk_list(self) -> None:
        chunks = [[0, 1], [2, 3]]
        d = FanOutDecision(mode=FanOutMode.WORKFLOW, chunks=chunks)
        assert d.mode is FanOutMode.WORKFLOW
        assert d.chunks == chunks
