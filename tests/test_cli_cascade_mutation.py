"""Unit tests for cascade status mutation: hold/unhold/decline/set-status --cascade.

Covers:
- cascade_status_mutation shared helper: depth-desc traversal order, SKIP lines,
  per-WU audit markers, --reason enforcement for hold/decline.
- cmd_hold --cascade --reason: eligible descendants mutated, ineligible skipped.
- cmd_unhold --cascade --reason: only hold descendants mutated.
- cmd_decline --cascade --reason: eligible descendants mutated.
- cmd_set_status --cascade: descendants set to target status.
- Missing --reason rejected for hold and decline cascade.
- Unknown root ID rejected.

Issue #245 AC-245-1, AC-245a-1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench import cli


def _build_backlog(
    tmp_path: Path,
    rows: list[tuple[str, str, str]],
) -> Path:
    """Materialise BACKLOG.md + per-row work-unit files.

    Each row is ``(id, type, status)``.
    """
    index_lines = [
        "# Backlog\n",
        "## Full Work Unit Index\n",
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
        "|----|-------|------|--------|--------------|------|-----------|",
    ]
    wu_dir = tmp_path / "backlog"
    wu_dir.mkdir(exist_ok=True)
    for unit_id, unit_type, status in rows:
        basename = unit_id.replace("-", "-")
        file_path = f"backlog/{basename}.md"
        index_lines.append(
            f"| {unit_id} | {unit_id} title | {unit_type} | {status} | None"
            f" | caylent-solutions/test-repo | `{file_path}` |"
        )
        wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n\n## Comments\n"
        (wu_dir / f"{basename}.md").write_text(wu_body)
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text("\n".join(index_lines) + "\n")
    return index_path


def _patch_backlog(
    tmp_path: Path,
    index_path: Path,
) -> Any:
    """Return a context manager that patches BACKLOG_ROOT, BACKLOG_INDEX, WORKSPACE_ROOT."""
    return (
        patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
        patch("devbench.cli.BACKLOG_INDEX", index_path),
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
    )


@pytest.mark.unit
class TestCascadeDepthDescOrder:
    """cascade_status_mutation traverses depth-desc (T before S before F before E)."""

    def test_traversal_order_is_depth_desc(self, tmp_path: Path) -> None:
        """Tasks are processed before stories, stories before features, features before epics."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1", "Feature", "in-queue"),
                ("E3-F1-S1", "Story", "in-queue"),
                ("E3-F1-S1-T1", "Task", "in-queue"),
                ("E3-F1-S1-T2", "Task", "in-queue"),
            ],
        )
        mutated_order: list[str] = []

        original_mark_held = cli.BacklogManager.mark_held

        def recording_mark_held(
            self_mgr: Any,
            wu_file: Path,
            backlog_index: Path,
            unit_id: str,
            reason: str,
        ) -> None:
            mutated_order.append(unit_id)
            original_mark_held(self_mgr, wu_file, backlog_index, unit_id, reason)

        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with (
            p1,
            p2,
            p3,
            patch.object(cli.BacklogManager, "mark_held", recording_mark_held),
        ):
            rc = cli.cmd_hold("E3", "--cascade", "--reason", "epic paused")

        assert rc == 0
        assert len(mutated_order) == 5
        task_positions = [mutated_order.index(u) for u in ["E3-F1-S1-T1", "E3-F1-S1-T2"]]
        story_position = mutated_order.index("E3-F1-S1")
        feature_position = mutated_order.index("E3-F1")
        epic_position = mutated_order.index("E3")
        assert max(task_positions) < story_position
        assert story_position < feature_position
        assert feature_position < epic_position


@pytest.mark.unit
class TestCascadeHold:
    """hold E3 --cascade --reason '...' mutates eligible descendants, skips ineligible."""

    def test_hold_cascade_mutates_eligible_and_skips_ineligible(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Eligible descendants get [HOLD] [CASCADE_FROM E3] marker; done descendants are skipped."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1", "Feature", "in-queue"),
                ("E3-F1-S1", "Story", "in-queue"),
                ("E3-F1-S1-T1", "Task", "in-queue"),
                ("E3-F1-S1-T2", "Task", "done"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_hold("E3", "--cascade", "--reason", "epic paused")

        assert rc == 0
        out = capsys.readouterr().out

        assert "SKIP E3-F1-S1-T2:" in out
        assert "not eligible for hold" in out

        for unit_id in ["E3", "E3-F1", "E3-F1-S1", "E3-F1-S1-T1"]:
            wu_file = tmp_path / "backlog" / f"{unit_id}.md"
            content = wu_file.read_text()
            assert "## Status: hold" in content, f"{unit_id} should be on hold"
            assert "[HOLD]" in content, f"{unit_id} should have [HOLD] audit"
            assert "[CASCADE_FROM E3]" in content, f"{unit_id} should have cascade marker"
            assert "epic paused" in content, f"{unit_id} should include reason"

    def test_hold_cascade_skips_already_held(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A descendant already on hold is skipped with the SKIP line."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "hold"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_hold("E3", "--cascade", "--reason", "paused")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIP E3-F1-S1-T1:" in out
        assert "not eligible for hold" in out

    def test_hold_cascade_skips_declined(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A declined descendant is skipped with the SKIP line."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "declined"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_hold("E3", "--cascade", "--reason", "paused")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIP E3-F1-S1-T1:" in out
        assert "not eligible for hold" in out

    def test_hold_cascade_requires_reason(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """hold --cascade without --reason is rejected with rc=1."""
        rc = cli.cmd_hold("E3", "--cascade")
        assert rc == 1
        assert "reason" in capsys.readouterr().err.lower()

    def test_hold_cascade_unknown_root_returns_1(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An unknown root ID returns rc=1 and an error message."""
        index = _build_backlog(tmp_path, rows=[("E3-F1-S1-T1", "Task", "in-queue")])
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_hold("E99", "--cascade", "--reason", "n/a")
        assert rc == 1
        assert "E99" in capsys.readouterr().err


@pytest.mark.unit
class TestCascadeUnhold:
    """unhold --cascade only returns held descendants to in-queue."""

    def test_unhold_cascade_mutates_held_skips_others(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Only descendants in hold status are unheld; others are skipped."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "hold"),
                ("E3-F1", "Feature", "hold"),
                ("E3-F1-S1-T1", "Task", "hold"),
                ("E3-F1-S1-T2", "Task", "in-queue"),
                ("E3-F1-S1-T3", "Task", "done"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_unhold("E3", "--cascade", "--reason", "resumed")

        assert rc == 0
        out = capsys.readouterr().out

        assert "SKIP E3-F1-S1-T2:" in out
        assert "SKIP E3-F1-S1-T3:" in out

        for unit_id in ["E3", "E3-F1", "E3-F1-S1-T1"]:
            wu_file = tmp_path / "backlog" / f"{unit_id}.md"
            content = wu_file.read_text()
            assert "## Status: in-queue" in content, f"{unit_id} should be in-queue"
            assert "[UNHOLD]" in content, f"{unit_id} should have [UNHOLD] audit"
            assert "[CASCADE_FROM E3]" in content, f"{unit_id} should have cascade marker"

    def test_unhold_cascade_requires_no_reason_arg_but_accepts_it(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """unhold --cascade accepts --reason and uses it in the audit."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "hold"),
                ("E3-F1-S1-T1", "Task", "hold"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_unhold("E3", "--cascade", "--reason", "unblocked")

        assert rc == 0
        wu_file = tmp_path / "backlog" / "E3-F1-S1-T1.md"
        assert "[CASCADE_FROM E3]" in wu_file.read_text()

    def test_unhold_cascade_unknown_root_returns_1(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An unknown root ID returns rc=1."""
        index = _build_backlog(tmp_path, rows=[("E3-F1-S1-T1", "Task", "hold")])
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_unhold("E99", "--cascade", "--reason", "n/a")
        assert rc == 1
        assert "E99" in capsys.readouterr().err


@pytest.mark.unit
class TestCascadeDecline:
    """decline --cascade declines eligible descendants."""

    def test_decline_cascade_mutates_eligible_skips_terminal(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Eligible descendants are declined; done/declined are skipped."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1", "Feature", "in-queue"),
                ("E3-F1-S1-T1", "Task", "in-queue"),
                ("E3-F1-S1-T2", "Task", "done"),
                ("E3-F1-S1-T3", "Task", "declined"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_decline("E3", "--cascade", "--reason", "project cancelled")

        assert rc == 0
        out = capsys.readouterr().out

        assert "SKIP E3-F1-S1-T2:" in out
        assert "not eligible for decline" in out
        assert "SKIP E3-F1-S1-T3:" in out

        for unit_id in ["E3", "E3-F1", "E3-F1-S1-T1"]:
            wu_file = tmp_path / "backlog" / f"{unit_id}.md"
            content = wu_file.read_text()
            assert "## Status: declined" in content, f"{unit_id} should be declined"
            assert "[DECLINED]" in content, f"{unit_id} should have [DECLINED] audit"
            assert "[CASCADE_FROM E3]" in content, f"{unit_id} should have cascade marker"
            assert "project cancelled" in content

    def test_decline_cascade_requires_reason(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """decline --cascade without --reason is rejected with rc=1."""
        rc = cli.cmd_decline("E3", "--cascade")
        assert rc == 1
        assert "reason" in capsys.readouterr().err.lower()

    def test_decline_cascade_unknown_root_returns_1(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An unknown root ID returns rc=1."""
        index = _build_backlog(tmp_path, rows=[("E3-F1-S1-T1", "Task", "in-queue")])
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_decline("E99", "--cascade", "--reason", "n/a")
        assert rc == 1
        assert "E99" in capsys.readouterr().err


@pytest.mark.unit
class TestCascadeSetStatus:
    """set-status <id> <status> --cascade sets all non-terminal descendants."""

    def test_set_status_cascade_mutates_non_terminal(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """All non-terminal descendants are set to the target status."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1", "Feature", "in-queue"),
                ("E3-F1-S1-T1", "Task", "in-queue"),
                ("E3-F1-S1-T2", "Task", "done"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_set_status("E3", "blocked", "--cascade")

        assert rc == 0
        out = capsys.readouterr().out

        assert "SKIP E3-F1-S1-T2:" in out
        assert "not eligible for set-status:blocked" in out

        for unit_id in ["E3", "E3-F1", "E3-F1-S1-T1"]:
            wu_file = tmp_path / "backlog" / f"{unit_id}.md"
            content = wu_file.read_text()
            assert "## Status: blocked" in content, f"{unit_id} should be blocked"

    def test_set_status_cascade_invalid_status_returns_1(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An invalid status returns rc=1 before touching any files."""
        index = _build_backlog(tmp_path, rows=[("E3-F1-S1-T1", "Task", "in-queue")])
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_set_status("E3", "not-a-status", "--cascade")
        assert rc == 1
        assert "Invalid status" in capsys.readouterr().err

    def test_set_status_cascade_unknown_root_returns_1(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unknown root ID returns rc=1."""
        index = _build_backlog(tmp_path, rows=[("E3-F1-S1-T1", "Task", "in-queue")])
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_set_status("E99", "in-queue", "--cascade")
        assert rc == 1
        assert "E99" in capsys.readouterr().err

    def test_set_status_cascade_audit_marker_written(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Each mutated WU gets a [SET-STATUS:<status>] [CASCADE_FROM <root>] audit marker."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "in-queue"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_set_status("E3", "blocked", "--cascade")

        assert rc == 0
        for unit_id in ["E3", "E3-F1-S1-T1"]:
            wu_file = tmp_path / "backlog" / f"{unit_id}.md"
            content = wu_file.read_text()
            assert "[SET-STATUS:blocked]" in content
            assert "[CASCADE_FROM E3]" in content


@pytest.mark.unit
class TestSkipLineShape:
    """SKIP line must be verbatim: 'SKIP <id>: <current-status> not eligible for <op>'."""

    def test_skip_line_hold_on_done_descendant(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """hold cascade emits 'SKIP <id>: done not eligible for hold' for done descendants."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "done"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_hold("E3", "--cascade", "--reason", "r")
        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIP E3-F1-S1-T1: done not eligible for hold" in out

    def test_skip_line_decline_on_done_descendant(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """decline cascade emits 'SKIP <id>: done not eligible for decline' for done descendants."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "done"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_decline("E3", "--cascade", "--reason", "r")
        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIP E3-F1-S1-T1: done not eligible for decline" in out

    def test_skip_line_set_status_on_done_descendant(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """set-status cascade emits 'SKIP <id>: done not eligible for set-status:<status>'."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "done"),
            ],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_set_status("E3", "in-queue", "--cascade")
        assert rc == 0
        out = capsys.readouterr().out
        assert "SKIP E3-F1-S1-T1: done not eligible for set-status:in-queue" in out


@pytest.mark.unit
class TestCascadeReasonEnforcement:
    """hold and decline cascade require --reason; unhold and set-status do not."""

    @pytest.mark.parametrize(
        "cmd_func,cmd_args",
        [
            (cli.cmd_hold, ["E3", "--cascade"]),
            (cli.cmd_decline, ["E3", "--cascade"]),
        ],
    )
    def test_missing_reason_returns_1(
        self,
        capsys: pytest.CaptureFixture[str],
        cmd_func: Any,
        cmd_args: list[str],
    ) -> None:
        """hold and decline cascade without --reason return rc=1."""
        rc = cmd_func(*cmd_args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "reason" in err.lower()


@pytest.mark.unit
class TestParseIdAndReasonCascade:
    """Branch coverage for _parse_id_and_reason_cascade helper."""

    def test_reason_without_value_returns_1(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--reason at end of args (no following value) returns rc=1."""
        rc = cli.cmd_hold("E3", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_missing_task_id_returns_1(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No positional ID at all returns rc=1."""
        rc = cli.cmd_hold("--cascade", "--reason", "some reason")
        assert rc == 1
        assert "requires" in capsys.readouterr().err.lower()

    def test_em_dash_in_reason_rejected(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Em-dash in reason rejected before cascade expansion."""
        rc = cli.cmd_hold("E3", "--cascade", "--reason", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_unhold_without_reason_non_cascade_returns_1(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """unhold without --reason in non-cascade mode returns rc=1."""
        rc = cli.cmd_unhold("E3-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err.lower()

    def test_extra_positional_args_ignored(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Second positional arg after task_id is ignored; task_id is first positional."""
        index = _build_backlog(
            tmp_path,
            rows=[("E3-F1-S1-T1", "Task", "in-queue")],
        )
        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with p1, p2, p3:
            rc = cli.cmd_hold("E3-F1-S1-T1", "extra-ignored", "--reason", "test")
        assert rc == 0

    def test_empty_string_arg_skipped_in_parser(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty-string arguments are skipped silently by the parser."""
        rc = cli.cmd_hold("", "--reason", "r")
        assert rc == 1
        assert "requires" in capsys.readouterr().err.lower()


@pytest.mark.unit
class TestCascadeMutationFileError:
    """cascade_status_mutation returns rc=1 when a wu_file cannot be resolved."""

    def test_hold_single_returns_1_when_wu_file_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Non-cascade cmd_hold returns rc=1 when _resolve_unit_file returns None."""
        index = _build_backlog(
            tmp_path,
            rows=[("E3-F1-S1-T1", "Task", "in-queue")],
        )
        original_resolve = cli._resolve_unit_file

        def patched_resolve(unit: Any) -> Any:
            if unit.id == "E3-F1-S1-T1":
                return None
            return original_resolve(unit)

        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with (
            p1,
            p2,
            p3,
            patch("devbench.cli._resolve_unit_file", patched_resolve),
        ):
            rc = cli.cmd_hold("E3-F1-S1-T1", "--reason", "test")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_unhold_single_returns_1_when_wu_file_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_cmd_unhold_single returns rc=1 when _resolve_unit_file returns None."""
        index = _build_backlog(
            tmp_path,
            rows=[("E3-F1-S1-T1", "Task", "hold")],
        )
        original_resolve = cli._resolve_unit_file

        def patched_resolve(unit: Any) -> Any:
            if unit.id == "E3-F1-S1-T1":
                return None
            return original_resolve(unit)

        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with (
            p1,
            p2,
            p3,
            patch("devbench.cli._resolve_unit_file", patched_resolve),
        ):
            rc = cli.cmd_unhold("E3-F1-S1-T1", "--reason", "test")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_cascade_returns_1_when_wu_file_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When _resolve_unit_file returns None for a descendant, cascade returns rc=1."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E3", "Epic", "in-queue"),
                ("E3-F1-S1-T1", "Task", "in-queue"),
            ],
        )
        original_resolve = cli._resolve_unit_file

        def patched_resolve(unit: Any) -> Any:
            if unit.id == "E3-F1-S1-T1":
                return None
            return original_resolve(unit)

        p1, p2, p3 = _patch_backlog(tmp_path, index)
        with (
            p1,
            p2,
            p3,
            patch("devbench.cli._resolve_unit_file", patched_resolve),
        ):
            rc = cli.cmd_hold("E3", "--cascade", "--reason", "test")
        assert rc == 1
        assert "not found" in capsys.readouterr().err
