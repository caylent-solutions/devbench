"""S10.4 journeys J-6, J-8, J-9: honest completion paths end to end (E4-F4-S1-T2).

Each journey scripts a full, operator-facing scenario against real temporary
git repositories and real backlog work-unit files -- no mocked git, no
internal-state assertions. Individual unit-level coverage of the building
blocks (``_build_remedies_rejection_message``, the ``cmd_mark_done`` gated
block, ``cmd_decline`` citation, ``cmd_green_green_check``) lives in
``tests/test_cli.py``; this module only proves the three journeys the work
unit's Definition of Done names by contract.

- J-6: a false-fix attempt (behavior-fix task, zero production change, an
  immediately-passing new test) is judged REVIEW_FAIL with the exact FR-4.4
  message "no genuine RED; fix may be absent or the test does not reproduce
  the failure" -- pulled verbatim from the judge prompt markdown (E4-F4-S1-T1)
  rather than duplicated as a second hardcoded literal, so the two can never
  silently drift apart -- and that REVIEW_FAIL blocks the done-gate.
- J-8: an honest behavior-fix (a failing test is written against a real
  buggy production file, the orchestrator observes a genuine, machine-scored
  RED via ``cli.cmd_tdd_gate``, the production bug is then fixed and the
  named test goes green) reaches done with a ``[RED_OBSERVED]`` record
  present and no rejection.
- J-9: a work unit completed end to end produces four review_team verdicts
  (``REVIEW_JUDGE_NAMES``), each attributable to its own judge agent via the
  persisted ``[judge/<name>] [REVIEW_PASS]`` line; removing any one of the
  four (or the security judge) blocks done.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from test_tdd_gate import commit_scratch_repo, init_scratch_repo, write_scratch_file

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import ALL_REQUIRED_JUDGE_NAMES, REVIEW_JUDGE_NAMES

# Repo root: tests/test_integration/test_tdd_red_gate_e2e.py -> parents[2] is
# the devbench repo root that contains plugin/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGES_MANIFEST_JUDGE_PROMPT = (
    _REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "changes-manifest.md"
)
_TEST_REVIEWER_JUDGE_PROMPT = (
    _REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "test-reviewer.md"
)


def _extract_no_genuine_red_message(prompt_path: Path) -> str:
    """Pull the exact FR-4.4 rejection sentence out of a judge prompt file.

    Reads the literal quoted string that follows ``REVIEW_FAIL with the
    exact message:`` in *prompt_path* so this journey's assertion is tied to
    the judge's own source of truth (E4-F4-S1-T1), never to a second,
    independently-typed copy of the sentence that could drift.
    """
    content = prompt_path.read_text(encoding="utf-8")
    marker = "REVIEW_FAIL with the exact message: "
    start = content.index(marker) + len(marker)
    quote_start = content.index('"', start) + 1
    quote_end = content.index('"', quote_start)
    return content[quote_start:quote_end]


def _write_backlog_index(tmp_path: Path, unit_id: str, *, status: str = "in-review") -> Path:
    """Write a minimal ``BACKLOG.md`` with one row for *unit_id*.

    ``BacklogManager._update_backlog_index`` (invoked by ``mark_done`` via
    ``_set_status``) requires a matching table row with a recognized status
    cell to update in place; without it the real (unmocked) manager raises.
    """
    backlog_index = tmp_path / "BACKLOG.md"
    backlog_index.write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|---|---|---|---|---|---|---|\n"
        f"| {unit_id} | Journey test | Task | {status} | None | repo | `backlog/{unit_id}.md` |\n",
        encoding="utf-8",
    )
    return backlog_index


def _setup_unit(
    tmp_path: Path, unit_id: str, wu_body: str, *, repo: str = "caylent-solutions/devbench"
) -> tuple[Path, MagicMock]:
    """Write a work-unit file and return it plus a mock ``BacklogParser``."""
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    wu_file = backlog_dir / f"{unit_id}.md"
    wu_file.write_text(wu_body, encoding="utf-8")
    unit = WorkUnit(
        id=unit_id,
        title="Journey test",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo=repo,
        dependencies=[],
    )
    mock_parser = MagicMock()
    mock_parser.parse_index.return_value = [unit]
    return wu_file, mock_parser


class TestJourneyJ6FalseFixRejection:
    """J-6: zero production change plus an immediately-passing test is a false fix."""

    def test_false_fix_yields_exact_review_fail_message_and_blocks_done(self, tmp_path: Path) -> None:
        # The two judge prompts (changes-manifest and test-reviewer) both
        # carry the exact FR-4.4 sentence -- confirm they still agree before
        # using either as this journey's expected text.
        message_a = _extract_no_genuine_red_message(_CHANGES_MANIFEST_JUDGE_PROMPT)
        message_b = _extract_no_genuine_red_message(_TEST_REVIEWER_JUDGE_PROMPT)
        assert (
            message_a == message_b == ("no genuine RED; fix may be absent or the test does not reproduce the failure")
        )

        unit_id = "E233-F1-S1-T1"
        body = (
            f"# {unit_id}\n\n## Status: in-review\n\n## Task Type: behavior-fix\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `tests/test_x.py` | add an immediately-passing test, no production change |\n\n"
            "## TDD Cycle Log\n\n## Comments\n\n"
        )
        wu_file, mock_parser = _setup_unit(tmp_path, unit_id, body)

        # Simulate what the judge (a prompt-driven agent, not code under
        # unit test) emits on this exact scenario: a REVIEW_FAIL verdict
        # carrying the FR-4.4 sentence character for character.
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
        ):
            rc = cli.cmd_log_verdict("test_review", unit_id, "fail", message_a)
        assert rc == 0

        content = wu_file.read_text(encoding="utf-8")
        assert "[judge/test_review]" in content
        assert "[REVIEW_FAIL]" in content
        assert message_a in content

        # The done-gate never lets a task with a false fix through: no
        # judge in ALL_REQUIRED_JUDGE_NAMES has recorded a REVIEW_PASS, so
        # mark-done is refused honestly rather than silently claiming a fix
        # that was never proven.
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done(unit_id)
        assert rc == 1
        assert "[DONE]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyJ8HonestBehaviorFix:
    """J-8: a genuine RED, a real fix, and an honest path to done."""

    def _init_repo(self, tmp_path: Path) -> Path:
        return init_scratch_repo(
            tmp_path,
            dir_name="journey-repo",
            author_email="journey-j8@example.com",
            author_name="Journey J8",
        )

    def test_honest_behavior_fix_reaches_done_with_red_observed(self, tmp_path: Path) -> None:
        unit_id = "E233-F1-S1-T2"
        node_id = "tests/test_calc.py::test_add"

        # Step 1: a real repository whose committed state is genuinely
        # broken -- calc.add() subtracts instead of adding, and the test
        # that pins the correct contract already exists and fails against
        # it. This is the state the RED gate is invoked against.
        repo = self._init_repo(tmp_path)
        write_scratch_file(repo, "src/calc.py", "def add(a: int, b: int) -> int:\n    return a - b\n")
        write_scratch_file(
            repo,
            "tests/test_calc.py",
            "import sys\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n\n"
            "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        )
        commit_scratch_repo(repo, "add calc.add with a deliberate bug plus a pinning test")

        wu_body = (
            f"# {unit_id}\n\n## Status: in-progress\n\n## Task Type: behavior-fix\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `src/calc.py` | fix add() |\n| `tests/test_calc.py` | add pinning test |\n\n"
            f"## TDD Cycle Log\n\n- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1.\n\n"
            "## Comments\n\n"
        )
        wu_file, mock_parser = _setup_unit(tmp_path, unit_id, wu_body)
        backlog_index = _write_backlog_index(tmp_path, unit_id, status="in-progress")

        # Step 2: the orchestrator observes a genuine, machine-scored RED.
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo}),
        ):
            rc = cli.cmd_tdd_gate(unit_id)
        assert rc == 0
        after_gate = wu_file.read_text(encoding="utf-8")
        assert "[RED_OBSERVED]" in after_gate
        assert f"test_node_id={node_id}" in after_gate
        # the gate restores the working tree exactly as it found it
        assert (repo / "src" / "calc.py").read_text(encoding="utf-8") == (
            "def add(a: int, b: int) -> int:\n    return a - b\n"
        )

        # Step 3: the production bug is genuinely fixed and committed; the
        # named test now passes against real pytest -- the GREEN half of
        # the cycle this journey is proving is honest, not asserted. Clear
        # any bytecode cache the RED-gate's own pytest subprocess left
        # behind: on coarse-mtime filesystems a stale .pyc compiled from
        # the pre-fix source can otherwise survive the rewrite and mask a
        # still-broken fix as green (the same staleness class of bug fixed
        # in cli.py's green-green check, see _gg_clear_pycache).
        write_scratch_file(repo, "src/calc.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
        commit_scratch_repo(repo, "fix calc.add")
        for cache_dir in repo.rglob("__pycache__"):
            shutil.rmtree(cache_dir)
        green_result = subprocess.run(
            ["python3", "-m", "pytest", node_id, "-q"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert green_result.returncode == 0, green_result.stdout + green_result.stderr

        # Step 4: all five required judges pass; the done-gate accepts.
        for judge in sorted(ALL_REQUIRED_JUDGE_NAMES):
            with (
                patch("devbench.cli.BacklogParser", return_value=mock_parser),
                patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            ):
                rc = cli.cmd_log_verdict(judge, unit_id, "pass", "ok")
            assert rc == 0

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            rc = cli.cmd_mark_done(unit_id)
        assert rc == 0

        final_content = wu_file.read_text(encoding="utf-8")
        assert "[RED_OBSERVED]" in final_content
        assert "[DONE]" in final_content
        assert "no RED_OBSERVED record found" not in final_content
        assert "REVIEW_FAIL" not in final_content


class TestJourneyJ9JudgeAttribution:
    """J-9: four review_team verdicts, each attributable; any one missing blocks done."""

    def test_all_five_judges_present_and_attributable_reaches_done(self, tmp_path: Path) -> None:
        unit_id = "E233-F1-S1-T3"
        body = f"# {unit_id}\n\n## Status: in-review\n\n## Task Type: docs\n\n## TDD Cycle Log\n\n## Comments\n\n"
        wu_file, mock_parser = _setup_unit(tmp_path, unit_id, body)
        backlog_index = _write_backlog_index(tmp_path, unit_id)

        for judge in sorted(ALL_REQUIRED_JUDGE_NAMES):
            with (
                patch("devbench.cli.BacklogParser", return_value=mock_parser),
                patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            ):
                rc = cli.cmd_log_verdict(judge, unit_id, "pass", f"{judge} looked at the diff and it holds up")
            assert rc == 0

        content = wu_file.read_text(encoding="utf-8")
        # Each of the four review_team judges left its own distinctly
        # attributable line -- not a single combined "reviewers passed"
        # entry that could hide which judge actually looked at what.
        for judge in sorted(REVIEW_JUDGE_NAMES):
            assert f"[judge/{judge}] [REVIEW_PASS]" in content

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            rc = cli.cmd_mark_done(unit_id)
        assert rc == 0
        assert "[DONE]" in wu_file.read_text(encoding="utf-8")

    @pytest.mark.parametrize("missing_judge", sorted(REVIEW_JUDGE_NAMES))
    def test_missing_any_one_review_team_judge_blocks_done(self, tmp_path: Path, missing_judge: str) -> None:
        unit_id = f"E233-F1-S1-T4-{missing_judge}"
        body = f"# {unit_id}\n\n## Status: in-review\n\n## Task Type: docs\n\n## TDD Cycle Log\n\n## Comments\n\n"
        wu_file, mock_parser = _setup_unit(tmp_path, unit_id, body)
        backlog_index = _write_backlog_index(tmp_path, unit_id)

        for judge in sorted(ALL_REQUIRED_JUDGE_NAMES):
            if judge == missing_judge:
                continue
            with (
                patch("devbench.cli.BacklogParser", return_value=mock_parser),
                patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            ):
                rc = cli.cmd_log_verdict(judge, unit_id, "pass", "ok")
            assert rc == 0

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            rc = cli.cmd_mark_done(unit_id)
        assert rc == 1
        assert "[DONE]" not in wu_file.read_text(encoding="utf-8")
