"""Unit tests for the narrow canonical-ID marker regex in cli.py.

Covers AC-253-3: a WU with ``[BLOCKED_PENDING_PROPOSAL] Amendment`` yields no
``unknown marker target`` skip because the narrowed regex does not capture
non-canonical-ID tokens.

Covers AC-253c-1: the three consumers at lines 668/2062/2387 (relative to the
regex definition) all inherit the narrowed pattern by re-using the single
``_BLOCKED_PENDING_PROPOSAL_MARKER_RE`` constant.

Issue #253c.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_backlog(
    tmp_path: Path,
    rows: list[tuple[str, str, str, str, str, str]],
) -> Path:
    """Materialise BACKLOG.md + per-row work-unit files.

    Each row is ``(id, type, status, deps, basename, comments)`` where
    ``comments`` is appended verbatim to the work-unit Markdown.
    """
    index_lines = [
        "# Backlog\n",
        "## Full Work Unit Index\n",
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
        "|----|-------|------|--------|--------------|------|-----------|",
    ]
    wu_dir = tmp_path / "backlog"
    wu_dir.mkdir(exist_ok=True)
    for unit_id, unit_type, status, deps, basename, comments in rows:
        file_path = f"backlog/{basename}.md"
        index_lines.append(
            f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | caylent-solutions/test-repo | `{file_path}` |"
        )
        wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
        if deps and deps != "None":
            dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
            wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
        if comments:
            wu_body += f"\n{comments}"
        (wu_dir / f"{basename}.md").write_text(wu_body)
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text("\n".join(index_lines) + "\n")
    return index_path


# ---------------------------------------------------------------------------
# AC-253-3: canonical-ID regex does not capture non-canonical tails
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMarkerRegexNarrow:
    """AC-253-3: ``_BLOCKED_PENDING_PROPOSAL_MARKER_RE`` must NOT capture
    non-canonical tokens like ``Amendment``."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            # Canonical full ID is captured.
            (
                "## Comments\n[BLOCKED_PENDING_PROPOSAL] E1-F2-S3-T4\n",
                ["E1-F2-S3-T4"],
            ),
            # Partial canonical IDs are also valid.
            (
                "[BLOCKED_PENDING_PROPOSAL] E1\n",
                ["E1"],
            ),
            (
                "[BLOCKED_PENDING_PROPOSAL] E1-F2\n",
                ["E1-F2"],
            ),
            (
                "[BLOCKED_PENDING_PROPOSAL] E1-F2-S3\n",
                ["E1-F2-S3"],
            ),
            # Non-canonical token: must produce NO match.
            (
                "[BLOCKED_PENDING_PROPOSAL] Amendment\n",
                [],
            ),
            # Non-canonical token: arbitrary word.
            (
                "[BLOCKED_PENDING_PROPOSAL] SomeWord\n",
                [],
            ),
            # Non-canonical token: UUID-like string.
            (
                "[BLOCKED_PENDING_PROPOSAL] abc-123\n",
                [],
            ),
            # Multiple markers: only canonical IDs captured.
            (
                "[BLOCKED_PENDING_PROPOSAL] E1-F2-S3-T4\n[BLOCKED_PENDING_PROPOSAL] Amendment\n",
                ["E1-F2-S3-T4"],
            ),
            # No marker: empty result.
            (
                "## Comments\nSome other text\n",
                [],
            ),
        ],
    )
    def test_regex_captures_only_canonical_ids(self, content: str, expected: list[str]) -> None:
        """The module-level regex must match only canonical ID tails."""
        result = cli._BLOCKED_PENDING_PROPOSAL_MARKER_RE.findall(content)
        assert result == expected


# ---------------------------------------------------------------------------
# AC-253-3: no ``unknown marker target`` skip for non-canonical marker tail
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNonCanonicalMarkerNoSkip:
    """AC-253-3: A WU file with ``[BLOCKED_PENDING_PROPOSAL] Amendment`` must
    not produce an ``unknown marker target`` skip in cmd_reconcile_cascade."""

    def test_amendment_marker_tail_does_not_cause_unknown_target_skip(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A ``[BLOCKED_PENDING_PROPOSAL] Amendment`` line is ignored by the
        narrowed regex, so no skip is emitted for an unknown marker target."""
        # T1 is done; T2 is blocked but its WU file contains the non-canonical
        # ``[BLOCKED_PENDING_PROPOSAL] Amendment`` line (emitted by some
        # automation before the fix).  With the broad ``\\S+`` regex T2 would
        # have been skipped as ``unknown marker target Amendment``.  With the
        # narrowed regex the line is ignored and T2 flips normally.
        comments_t2 = "\n## Comments\n[BLOCKED_PENDING_PROPOSAL] Amendment\n"
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    comments_t2,
                ),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        skipped_ids = {item["unit_id"] for item in envelope.get("skipped", [])}
        assert "E0-F1-S1-T2" not in skipped_ids, (
            "T2 must NOT appear in skipped; non-canonical marker tail must be ignored"
        )
        flipped_ids = {item["unit_id"] for item in envelope.get("flipped", [])}
        assert "E0-F1-S1-T2" in flipped_ids, "T2 must flip to in-queue because its only dep (T1) is done"


# ---------------------------------------------------------------------------
# AC-253c-1: all three consumers inherit the narrowed pattern
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConsumersInheritNarrowedPattern:
    """AC-253c-1: The three consumers at lines 668/2062/2387 each call
    ``_BLOCKED_PENDING_PROPOSAL_MARKER_RE.findall()``.  Confirm they all see
    the narrowed pattern by exercising each consumer path with a non-canonical
    marker tail and verifying the consumer does not treat it as a live target.
    """

    def _make_units_by_id(self, unit_id: str, status: WorkUnitStatus) -> dict[str, WorkUnit]:
        """Build a minimal units_by_id mapping for a single WU."""
        wu = WorkUnit(
            id=unit_id,
            title=unit_id,
            unit_type=WorkUnitType.TASK,
            status=status,
            dependencies=[],
            repo="caylent-solutions/test-repo",
            file_path=Path(f"backlog/{unit_id}.md"),
        )
        return {unit_id: wu}

    def test_has_open_proposal_marker_ignores_non_canonical_tail(self) -> None:
        """Consumer at the ``_has_open_proposal_marker`` path: a non-canonical
        tail does not match, so the function returns False (no open markers)."""
        content = "[BLOCKED_PENDING_PROPOSAL] Amendment\n"
        units_by_id: dict[str, WorkUnit] = {}
        result = cli._has_open_proposal_marker(content, units_by_id)
        assert result is False, "_has_open_proposal_marker must return False for non-canonical tail"

    def test_has_open_proposal_marker_canonical_id_open(self) -> None:
        """Consumer at the ``_has_open_proposal_marker`` path: a canonical ID
        pointing to an in-progress task is treated as open (True)."""
        unit_id = "E0-F1-S1-T9"
        content = f"[BLOCKED_PENDING_PROPOSAL] {unit_id}\n"
        units_by_id = self._make_units_by_id(unit_id, WorkUnitStatus.IN_PROGRESS)
        result = cli._has_open_proposal_marker(content, units_by_id)
        assert result is True, "_has_open_proposal_marker must return True for an open canonical-ID marker"

    def test_has_open_proposal_marker_canonical_id_done(self) -> None:
        """Consumer at the ``_has_open_proposal_marker`` path: a canonical ID
        whose target is done is treated as satisfied (False)."""
        unit_id = "E0-F1-S1-T9"
        content = f"[BLOCKED_PENDING_PROPOSAL] {unit_id}\n"
        units_by_id = self._make_units_by_id(unit_id, WorkUnitStatus.DONE)
        result = cli._has_open_proposal_marker(content, units_by_id)
        assert result is False, "_has_open_proposal_marker must return False when the canonical target is done"

    def test_print_blocked_row_consumer_ignores_non_canonical_tail(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Consumer at ``_print_blocked_row`` (line 668 path): a non-canonical
        tail is not surfaced as an open marker; the printed output contains no
        ``Amendment`` marker note."""
        unit_id = "E0-F1-S1-T2"
        wu_file = tmp_path / "E0-F1-S1-T2.md"
        content = f"# {unit_id}: Test\n\n## Status: blocked\n\n## Comments\n[BLOCKED_PENDING_PROPOSAL] Amendment\n"
        wu_file.write_text(content)

        wu = WorkUnit(
            id=unit_id,
            title=unit_id,
            unit_type=WorkUnitType.TASK,
            status=WorkUnitStatus.BLOCKED,
            dependencies=[],
            repo="caylent-solutions/test-repo",
            file_path=wu_file,
        )
        units_by_id: dict[str, WorkUnit] = {unit_id: wu}

        with patch("devbench.cli._resolve_unit_file", return_value=wu_file):
            cli._print_blocked_row(wu, units_by_id)

        out = capsys.readouterr().out
        assert "Amendment" not in out, "Non-canonical tail 'Amendment' must not appear in _print_blocked_row output"
