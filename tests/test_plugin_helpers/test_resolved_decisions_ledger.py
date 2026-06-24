"""Tests for ``devbench.plugin_helpers.resolved_decisions_ledger``.

Issue #264 E12-F2-S2: the resolved-decisions ledger that ``create-spec``
maintains during the adversarial hardening loop. The ledger lives at
``spec/<name>-resolved-decisions.md`` and records each confirmed
cross-section or cross-file contradiction resolution as a ``D<N>`` entry.

AC-1: appending a new decision writes a ``D<N>`` entry with the next
      sequential index; reading returns all entries in order.
AC-1 (defer): a re-resolution of an already-recorded contradiction defers
      to the existing entry verbatim rather than appending a duplicate.
AC-atomicity: writes use the atomic tmp-plus-replace pattern so a failed
      write leaves the prior ledger file intact.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.plugin_helpers.resolved_decisions_ledger import (
    DecisionEntry,
    DuplicateResolutionError,
    LedgerEntry,
    append_decision,
    next_index,
    read_ledger,
)


def _spec_dir(tmp_path: Path) -> Path:
    spec = tmp_path / "spec"
    spec.mkdir(parents=True, exist_ok=True)
    return spec


def _ledger_path(tmp_path: Path, name: str = "myproject") -> Path:
    return _spec_dir(tmp_path) / f"{name}-resolved-decisions.md"


@pytest.mark.unit
class TestDecisionEntry:
    """DecisionEntry holds the structured fields of a single ledger row."""

    def test_fields_accessible(self) -> None:
        entry = DecisionEntry(
            index=1,
            contradiction="Section 2 vs Section 4 conflict on timeout value",
            resolution="Use 30 s from Section 4; Section 2 prose updated to match",
            rationale="Section 4 provides the authoritative numeric contract",
        )
        assert entry.index == 1
        assert "timeout" in entry.contradiction
        assert "30 s" in entry.resolution
        assert "authoritative" in entry.rationale

    def test_equality_is_value_based(self) -> None:
        a = DecisionEntry(
            index=2,
            contradiction="c",
            resolution="r",
            rationale="rat",
        )
        b = DecisionEntry(
            index=2,
            contradiction="c",
            resolution="r",
            rationale="rat",
        )
        assert a == b

    def test_repr_contains_index(self) -> None:
        entry = DecisionEntry(index=5, contradiction="c", resolution="r", rationale="rat")
        assert "5" in repr(entry)


@pytest.mark.unit
class TestLedgerEntry:
    """LedgerEntry carries the raw markdown text alongside the parsed index."""

    def test_fields_accessible(self) -> None:
        entry = LedgerEntry(index=3, raw="## D3\n\n**Contradiction:** c\n")
        assert entry.index == 3
        assert "D3" in entry.raw


@pytest.mark.unit
class TestNextIndex:
    """next_index returns the next sequential D<N> integer."""

    def test_returns_one_for_empty_ledger(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        assert next_index(path) == 1

    def test_returns_one_for_empty_file(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        path.write_text("", encoding="utf-8")
        assert next_index(path) == 1

    def test_returns_n_plus_one_after_existing_entries(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        path.write_text(
            "# Resolved Decisions\n\n## D1\n\ntext\n\n## D2\n\ntext\n",
            encoding="utf-8",
        )
        assert next_index(path) == 3

    def test_index_is_max_plus_one_even_with_gaps(self, tmp_path: Path) -> None:
        """If entries are D1 and D3 (gap at D2), next index is 4."""
        path = _ledger_path(tmp_path)
        path.write_text(
            "# Resolved Decisions\n\n## D1\n\ntext\n\n## D3\n\ntext\n",
            encoding="utf-8",
        )
        assert next_index(path) == 4


@pytest.mark.unit
class TestReadLedger:
    """read_ledger returns all D<N> entries in ascending index order."""

    def test_empty_ledger_returns_empty_list(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        assert read_ledger(path) == []

    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        assert not path.exists()
        assert read_ledger(path) == []

    def test_returns_entries_in_index_order(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        path.write_text(
            "# Resolved Decisions\n\n## D2\n\ntext two\n\n## D1\n\ntext one\n",
            encoding="utf-8",
        )
        entries = read_ledger(path)
        assert len(entries) == 2
        assert entries[0].index == 1
        assert entries[1].index == 2

    def test_raw_contains_section_body(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        path.write_text(
            "# Resolved Decisions\n\n## D1\n\n**Contradiction:** foo vs bar\n",
            encoding="utf-8",
        )
        entries = read_ledger(path)
        assert len(entries) == 1
        assert "foo vs bar" in entries[0].raw


@pytest.mark.unit
class TestAppendDecision:
    """append_decision writes a new D<N> entry atomically."""

    def test_creates_ledger_with_first_entry(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        decision = DecisionEntry(
            index=0,
            contradiction="Section 2 vs Section 4 on retry count",
            resolution="Use retry_count=3 from Section 4",
            rationale="Section 4 is the authoritative error-handling contract",
        )
        entry = append_decision(path, decision)
        assert path.exists()
        assert entry.index == 1
        content = path.read_text(encoding="utf-8")
        assert "## D1" in content
        assert "retry_count=3" in content

    def test_sequential_appends_produce_increasing_indices(self, tmp_path: Path) -> None:
        path = _ledger_path(tmp_path)
        d1 = DecisionEntry(index=0, contradiction="c1", resolution="r1", rationale="rat1")
        d2 = DecisionEntry(index=0, contradiction="c2", resolution="r2", rationale="rat2")
        e1 = append_decision(path, d1)
        e2 = append_decision(path, d2)
        assert e1.index == 1
        assert e2.index == 2
        entries = read_ledger(path)
        assert len(entries) == 2
        assert entries[0].index == 1
        assert entries[1].index == 2

    def test_duplicate_contradiction_raises_and_does_not_modify_ledger(self, tmp_path: Path) -> None:
        """A re-resolution of an already-recorded contradiction raises DuplicateResolutionError."""
        path = _ledger_path(tmp_path)
        d1 = DecisionEntry(
            index=0,
            contradiction="Section 2 vs Section 4 on timeout",
            resolution="Use 30 s from Section 4",
            rationale="authoritative",
        )
        append_decision(path, d1)
        original_content = path.read_text(encoding="utf-8")

        d_dup = DecisionEntry(
            index=0,
            contradiction="Section 2 vs Section 4 on timeout",
            resolution="Use 60 s instead",
            rationale="different rationale",
        )
        with pytest.raises(DuplicateResolutionError) as exc_info:
            append_decision(path, d_dup)

        assert path.read_text(encoding="utf-8") == original_content
        assert "D1" in str(exc_info.value)

    def test_write_is_atomic_tmp_plus_replace(self, tmp_path: Path) -> None:
        """The write must use a .tmp file then replace atomically."""
        path = _ledger_path(tmp_path)
        tmp_path_for_ledger = path.parent / (path.name + ".tmp")

        tmp_existed_during_write: list[bool] = []
        original_replace = Path.replace

        def spy_replace(self: Path, target: Path) -> Path:
            tmp_existed_during_write.append(tmp_path_for_ledger.exists())
            return original_replace(self, target)

        decision = DecisionEntry(index=0, contradiction="c", resolution="r", rationale="rat")
        with patch.object(Path, "replace", spy_replace):
            append_decision(path, decision)

        assert any(tmp_existed_during_write), "No atomic rename observed during write"
        assert not tmp_path_for_ledger.exists()

    def test_partial_write_failure_leaves_prior_ledger_intact(self, tmp_path: Path) -> None:
        """If the tmp-write fails, the original ledger file is untouched."""
        path = _ledger_path(tmp_path)
        d1 = DecisionEntry(index=0, contradiction="c1", resolution="r1", rationale="rat1")
        append_decision(path, d1)
        original_content = path.read_text(encoding="utf-8")

        original_write_text = Path.write_text

        def failing_write_text(self: Path, data: str, **kwargs: str | None) -> int:
            if str(self).endswith(".tmp"):
                raise OSError("Simulated disk error")
            return original_write_text(self, data, **kwargs)

        d2 = DecisionEntry(index=0, contradiction="c2", resolution="r2", rationale="rat2")
        with patch.object(Path, "write_text", failing_write_text):
            with pytest.raises(OSError, match="Simulated disk error"):
                append_decision(path, d2)

        assert path.read_text(encoding="utf-8") == original_content

    def test_ledger_file_name_uses_project_name(self, tmp_path: Path) -> None:
        """The ledger path follows the spec/<name>-resolved-decisions.md convention."""
        path = _ledger_path(tmp_path, name="my-feature")
        assert path.name == "my-feature-resolved-decisions.md"
        assert path.parent.name == "spec"

    @pytest.mark.parametrize(
        "contradiction,resolution,rationale",
        [
            (
                "Section 3 vs Section 7 on config key name",
                "Use DEVBENCH_TIMEOUT_SECONDS from Section 7",
                "Section 7 is the authoritative config reference",
            ),
            (
                "FR-5 vs AC-12 on output format",
                "Emit JSON as specified in FR-5",
                "FR-5 is machine-readable; AC-12 was ambiguous prose",
            ),
            (
                "Section 0 vs Section 4 on error exit code",
                "Exit code 2 per Section 0 behavior-change table",
                "Section 0 takes precedence as it documents user-visible behavior",
            ),
        ],
    )
    def test_parametrised_decisions_round_trip(
        self,
        tmp_path: Path,
        contradiction: str,
        resolution: str,
        rationale: str,
    ) -> None:
        """append_decision followed by read_ledger recovers the exact text."""
        path = _ledger_path(tmp_path)
        decision = DecisionEntry(index=0, contradiction=contradiction, resolution=resolution, rationale=rationale)
        entry = append_decision(path, decision)
        assert entry.index == 1

        entries = read_ledger(path)
        assert len(entries) == 1
        assert contradiction in entries[0].raw
        assert resolution in entries[0].raw


@pytest.mark.unit
class TestDuplicateResolutionError:
    """DuplicateResolutionError carries the existing D<N> index and contradiction text."""

    def test_str_mentions_existing_index(self) -> None:
        err = DuplicateResolutionError(
            existing_index=7,
            contradiction="some contradiction text",
        )
        assert "D7" in str(err)
        assert "some contradiction text" in str(err)

    def test_is_value_error(self) -> None:
        err = DuplicateResolutionError(existing_index=1, contradiction="c")
        assert isinstance(err, ValueError)
