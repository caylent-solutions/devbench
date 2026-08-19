"""Regression test for #330 FR-3 / AC-8: the Manifest Conflict Rule's printed
remedy command is executed and proven to resolve the conflict it names.

Prior to this test, nothing ever ran the ``uv run devbench add-dep <a> <b>``
line the Manifest Conflict Rule prints (docs/backlog-contract.md "Manifest
Conflict Rule"). The rule named a command, the command reported success, and
no test closed the loop between the two.

This test builds a real two-Task Manifest conflict via
``BacklogManager.validate()``, parses the remedy command out of the
validator's OWN error text with a regex (never hardcoded -- spec D-3:
hardcoding the command would let the printed message and the actual CLI
behavior drift apart again without anything noticing), executes the parsed
command through the real CLI dispatcher (``cli.main()``), and asserts
``validate-backlog`` subsequently returns rc=0.

See spec/dep-remedy-and-dependency-currency.md Section 4 FR-3, Section 10,
Section 13 D-3, AC-8.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli

# Matches only the exact shape the Manifest Conflict Rule prints under
# "Wire a serial dep chain:" (manager.py `_hard_manifest_conflict_error`):
# a "uv run devbench add-dep <blocked> <blocker>" line indented under that
# header. Deliberately narrow -- if the subcommand is ever renamed, the
# argument order changes, or the header text drifts, this regex stops
# matching, which IS the failure mode #330 FR-3 exists to catch (spec D-3).
_REMEDY_COMMAND_RE = re.compile(r"Wire a serial dep chain:\n\s*(uv run devbench add-dep \S+ \S+)")


def _parse_remedy_command(validator_output: str) -> str | None:
    """Return the first remedy command line found in *validator_output*, or ``None``.

    The one and only place the command text is derived from -- the real,
    captured stdout of ``validate-backlog``. Nothing here re-derives the
    command from the task ids independently, so a change to the printed
    message is the only thing that can make this return ``None``.
    """
    match = _REMEDY_COMMAND_RE.search(validator_output)
    return match.group(1) if match else None


_CONFLICT_TASK_TEMPLATE = """\
# {unit_id}: {title}

## Status: in-queue

## Task Type: docs

## Target Repository

- **Repo:** `{repo}`

## Description

Regression fixture for #330 FR-3: two Tasks intentionally claim the same
Changes Manifest path so the Manifest Conflict Rule fires.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 Fixture task; not itself under review.

## Changes Manifest

| File | Change |
|------|--------|
| `{manifest_path}` | edit |

## Definition of Done

- [ ] Fixture only.

## Comments
"""


def _write_conflicting_backlog(tmp_path: Path, blocked_id: str, blocker_id: str) -> Path:
    """Build a minimal backlog where *blocked_id* and *blocker_id* both claim
    the same Changes Manifest path in the same repo, with no ordering
    dependency between them -- a real HARD Manifest Conflict Rule collision
    (manager.py ``_check_manifest_conflicts``). Returns the ``BACKLOG.md`` path.
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    repo = "caylent-solutions/git-repo"
    manifest_path = "docs/fixture-conflict.md"
    titles = {blocked_id: "First conflict claimant", blocker_id: "Second conflict claimant"}
    for tid in (blocked_id, blocker_id):
        (backlog_dir / f"{tid}.md").write_text(
            _CONFLICT_TASK_TEMPLATE.format(unit_id=tid, title=titles[tid], repo=repo, manifest_path=manifest_path),
            encoding="utf-8",
        )
    rows = "".join(
        f"| {tid} | {titles[tid]} | Task | in-queue | none | {repo} | `backlog/{tid}.md` |\n"
        for tid in (blocked_id, blocker_id)
    )
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n" + rows,
        encoding="utf-8",
    )
    return index_path


class TestManifestConflictRemedyIsExecutable:
    """#330 FR-3 / AC-8: closes the loop between the printed remedy and behavior."""

    BLOCKED_ID = "E0-F9-S1-T2"
    BLOCKER_ID = "E0-F9-S1-T1"

    def test_parsed_remedy_command_resolves_the_conflict(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        index_path = _write_conflicting_backlog(tmp_path, self.BLOCKED_ID, self.BLOCKER_ID)
        backlog_dir = index_path.parent / "backlog"

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
        ):
            rc_before = cli.cmd_validate_backlog()
            output_before = capsys.readouterr().out
            assert rc_before == 1, "fixture must reproduce a real Manifest conflict before the remedy runs"
            assert "Manifest conflict" in output_before, output_before

            command = _parse_remedy_command(output_before)
            assert command is not None, (
                "remedy command not found in validate-backlog output -- the "
                "'Wire a serial dep chain:' message shape changed"
            )

            argv = command.split()
            assert argv[:3] == ["uv", "run", "devbench"], argv
            with patch.object(sys, "argv", ["devbench", *argv[3:]]):
                rc_remedy = cli.main()
            capsys.readouterr()
            assert rc_remedy == 0, f"the parsed remedy command {command!r} itself must exit 0"

            rc_after = cli.cmd_validate_backlog()
            output_after = capsys.readouterr().out

        assert rc_after == 0, f"validate-backlog still failing after the parsed remedy ran: {output_after}"

    def test_remedy_parser_rejects_a_drifted_message_shape(self) -> None:
        """AC-8 / spec D-3 non-vacuity: the SAME parser used above must fail
        to find a command when the message shape drifts (renamed
        subcommand, missing 'Wire a serial dep chain:' header). Proves the
        parser is a real filter, not a pass-through that would accept any
        text -- so a future rename of the remedy verb is caught here rather
        than silently producing a command that is never actually executed.
        """
        drifted = (
            "Manifest conflict on 'docs/x.md': claimed by E0-F1-S1-T1, E0-F1-S1-T2. "
            "Run devbench wire-dep E0-F1-S1-T2 E0-F1-S1-T1 to fix it."
        )
        assert _parse_remedy_command(drifted) is None

    def test_remedy_parser_accepts_only_the_documented_shape(self) -> None:
        """Positive-side companion to the drift test: confirms the parser
        extracts exactly the command text on the line the rule actually
        prints, with no re-derivation from the task ids themselves.
        """
        text = (
            "Manifest conflict on 'x.md' in repo r: claimed by A, B. Wire a serial dep chain:\n"
            "    uv run devbench add-dep B A\n"
            "  -- or any other DAG that totally orders the set. See docs/backlog-contract.md "
            "'Manifest Conflict Rule'."
        )
        assert _parse_remedy_command(text) == "uv run devbench add-dep B A"
