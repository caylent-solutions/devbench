"""CLI entry point for the judges system.

Provides shell-callable commands so Claude Code (or any external process)
can invoke judge operations, query backlog status, and execute work units.

Usage::

    python3 -m devbench.cli <command> [args]

Commands::

    status                Show backlog summary (counts by status)
    next                  Print the next actionable work unit ID and title
    execute <id>          Spawn a Claude Code agent to execute a work unit
    review <id>           Run all review judges on a work unit, print JSON results
    log <message>         Append a message to the orchestrator log file

All commands exit 0 on success, non-zero on failure. Output is structured
for easy parsing by Claude Code or other automation.
"""

import json
import logging
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC
from pathlib import Path

from devbench.backlog.manager import BacklogManagerJudge
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    REPO_LOCAL_PATHS,
    WORKSPACE_ROOT,
    resolve_repo,
    validate_repo,
)
from devbench.constants import DISPLAY_STATUS_VALUES, STATUS_SEPARATOR_WIDTH
from devbench.judges.base import Verdict
from devbench.judges.changes_manifest import ChangesManifestJudge
from devbench.judges.code_review import CodeReviewJudge
from devbench.judges.doc_review import DocReviewJudge
from devbench.judges.security_review import SecurityReviewJudge
from devbench.judges.test_review import TestReviewJudge
from devbench.log_setup import setup_logging

logger = logging.getLogger("devbench.cli")


def cmd_status() -> int:
    """Print backlog summary grouped by status."""
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    counts: dict[str, int] = {}
    for unit in units:
        key = unit.status.value.lower()
        counts[key] = counts.get(key, 0) + 1

    total = len(units)
    print("Backlog Status Summary")
    print("=" * STATUS_SEPARATOR_WIDTH)
    for status_val in DISPLAY_STATUS_VALUES:
        count = counts.get(status_val.lower(), 0)
        print(f"  {status_val:<15} {count:>4}")
    print(f"  {'TOTAL':<15} {total:>4}")

    active = [
        u for u in units
        if u.status in (WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.IN_REVIEW)
    ]
    if active:
        print("\nActive work units:")
        for u in active:
            print(f"  [{u.status.value}] {u.id} — {u.title}")

    actionable = parser.get_parallel_candidates(units)
    if actionable:
        print(f"\nNext actionable: {actionable[0].id} — {actionable[0].title}")
    elif parser.all_done(units):
        print("\nAll work units are DONE.")
    else:
        blocked = parser.get_blocked_units(units)
        print(f"\nNo actionable units. {len(blocked)} blocked.")

    return 0


def cmd_next() -> int:
    """Print the next actionable work unit."""
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    candidates = parser.get_parallel_candidates(units)

    if not candidates:
        if parser.all_done(units):
            print("ALL_DONE")
        else:
            print("NO_ACTIONABLE")
        return 0

    unit = candidates[0]

    # Automatically mark as in-progress in both files
    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path
    if wu_file.exists():
        mgr = BacklogManagerJudge()
        mgr.force_status(wu_file, BACKLOG_INDEX, unit.id, "in-progress")
        logger.info("Set %s to in-progress", unit.id)

    print(
        json.dumps(
            {
                "id": unit.id,
                "title": unit.title,
                "repo": unit.repo,
                "file_path": str(unit.file_path),
                "dependencies": unit.dependencies,
            }
        )
    )
    return 0


def cmd_execute(unit_id: str, feedback: str = "") -> int:
    """Spawn a Claude Code agent to execute a work unit."""
    from devbench.execution import executor as claude_executor

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        # Try resolving relative to workspace
        wu_file = WORKSPACE_ROOT / target.file_path

    logger.info("Executing work unit %s (repo: %s)", unit_id, target.repo)

    result = claude_executor.execute(
        work_unit_path=wu_file,
        repo=target.repo,
        feedback=feedback,
    )

    logger.info("Execution result for %s: status=%s", unit_id, result.status.value)
    if result.blocker:
        logger.info("Execution blocker for %s: %s", unit_id, result.blocker[:500])

    output = {
        "unit_id": unit_id,
        "status": result.status.value,
        "blocker": result.blocker,
        "output_length": len(result.output),
    }
    print(json.dumps(output))
    return 0 if result.status.value == "in-review" else 1


def cmd_review(unit_id: str) -> int:
    """Run all review judges on a work unit and print JSON results."""
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    full_repo = resolve_repo(target.repo)
    validate_repo(full_repo)
    repo_path = REPO_LOCAL_PATHS.get(full_repo)
    if repo_path is None:
        print(f"ERROR: No local path for repo '{target.repo}'", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / target.file_path

    # Automatically mark as in-review in both files
    mgr = BacklogManagerJudge()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, "in-review")
    logger.info("Set %s to in-review", unit_id)

    judges = [
        CodeReviewJudge(),
        TestReviewJudge(),
        DocReviewJudge(),
        ChangesManifestJudge(),
    ]

    results: list[dict[str, object]] = []
    all_passed = True

    prior_feedback = _get_prior_feedback(unit_id)

    for judge in judges:
        judge.previous_feedback = prior_feedback.get(judge.name, "")
        logger.info("Running %s judge on %s", judge.name, unit_id)
        judge_result = judge.evaluate(work_unit_path=wu_file, repo_path=repo_path)
        passed = judge_result.verdict == Verdict.PASS
        if not passed:
            all_passed = False

        logger.info(
            "%s judge verdict for %s: %s", judge.name, unit_id, judge_result.verdict.value,
        )
        if not passed:
            logger.info(
                "%s judge reasoning for %s: %s", judge.name, unit_id, judge_result.reasoning,
            )
            logger.info(
                "%s judge feedback for %s: %s", judge.name, unit_id, judge_result.feedback[:2000],
            )
        if judge_result.evidence:
            logger.info(
                "%s judge evidence for %s: %s", judge.name, unit_id, "; ".join(judge_result.evidence),
            )

        results.append(
            {
                "judge": judge.name,
                "verdict": judge_result.verdict.value,
                "reasoning": judge_result.reasoning,
                "feedback": judge_result.feedback,
                "evidence": judge_result.evidence,
            }
        )

    passed_names = [str(r["judge"]) for r in results if r["verdict"] == "pass"]
    failed_names = [str(r["judge"]) for r in results if r["verdict"] == "fail"]
    logger.info(
        "Review complete for %s: %s. Passed: [%s] Failed: [%s]",
        unit_id,
        "ALL PASSED" if all_passed else "FAILED",
        ", ".join(passed_names),
        ", ".join(failed_names),
    )

    output = {
        "unit_id": unit_id,
        "all_passed": all_passed,
        "results": results,
    }
    print(json.dumps(output, indent=2))
    return 0 if all_passed else 1


def cmd_security_review(unit_id: str) -> int:
    """Run the security review judge on a work unit."""
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    full_repo = resolve_repo(target.repo)
    validate_repo(full_repo)
    repo_path = REPO_LOCAL_PATHS.get(full_repo)
    if repo_path is None:
        print(f"ERROR: No local path for repo '{target.repo}'", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / target.file_path

    judge = SecurityReviewJudge()
    logger.info("Running security_review judge on %s", unit_id)
    result = judge.evaluate(work_unit_path=wu_file, repo_path=repo_path, repo=full_repo)
    logger.info("security_review verdict for %s: %s", unit_id, result.verdict.value)
    if result.verdict != Verdict.PASS:
        logger.info("security_review feedback for %s: %s", unit_id, result.feedback[:500])

    output = {
        "judge": "security_review",
        "verdict": result.verdict.value,
        "reasoning": result.reasoning,
        "feedback": result.feedback,
        "evidence": result.evidence,
    }
    print(json.dumps(output, indent=2))
    return 0 if result.verdict == Verdict.PASS else 1


def cmd_set_status(unit_id: str, new_status: str) -> int:
    """Set the status of a work unit in both the work-unit file and BACKLOG.md."""
    from devbench.backlog.manager import VALID_STATUSES

    if new_status.lower() not in VALID_STATUSES:
        print(
            f"ERROR: Invalid status '{new_status}'. "
            f"Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return 1

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / target.file_path

    mgr = BacklogManagerJudge()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, new_status)

    logger.info("Set %s to %s", unit_id, new_status)
    print(f"Set {unit_id} to {new_status}")
    return 0


def cmd_mark_done(unit_id: str) -> int:
    """Mark a work unit as Done, enforcing the done-gate check.

    Calls ``BacklogManagerJudge.mark_done()`` which verifies that all required
    review judges passed in the most recent round before allowing the transition.
    Raises ``RuntimeError`` if the gate check fails.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / target.file_path

    mgr = BacklogManagerJudge()
    try:
        mgr.mark_done(wu_file, BACKLOG_INDEX, unit_id)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    logger.info("Marked %s as done", unit_id)
    print(f"Marked {unit_id} as done")
    return 0


def cmd_validate_backlog() -> int:
    """Validate backlog integrity and print any inconsistencies.

    Checks:
    - Every index row has a corresponding work unit file.
    - Every work unit file's status matches the index.
    - No orphaned work unit files.
    - All dependency IDs reference real work unit IDs.

    Exits 0 if the backlog is consistent; 1 with actionable error messages
    if any inconsistencies are found.
    """
    mgr = BacklogManagerJudge()
    errors = mgr.validate(BACKLOG_INDEX, BACKLOG_ROOT)
    if not errors:
        print("Backlog integrity check passed.")
        return 0
    print(f"Backlog integrity check FAILED ({len(errors)} error(s)):")
    for error in errors:
        print(f"  ERROR: {error}")
    return 1


def cmd_report(since: str = "") -> int:
    """Print a formatted progress report with velocity and completion stats."""
    from datetime import datetime

    from devbench.reporting.report import generate_report

    log_file = Path(os.environ.get(
        "JUDGE_LOG_FILE",
        str(Path(__file__).resolve().parent / "logs" / "orchestrator.log"),
    ))

    since_dt = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    report = generate_report(log_path=log_file, since=since_dt)
    print(report)
    return 0


def cmd_log(message: str) -> int:
    """Append a message to the orchestrator log."""
    logger.info("[USER] %s", message)
    print(f"Logged: {message}")
    return 0


def _find_unit(units: list[WorkUnit], unit_id: str) -> WorkUnit | None:
    """Find a work unit by ID (case-insensitive)."""
    for unit in units:
        if unit.id.lower() == unit_id.lower():
            return unit
    return None


def _get_prior_feedback(unit_id: str) -> dict[str, str]:
    """Extract the most recent review feedback per judge from the orchestrator log.

    Parses log lines matching the pattern::

        <timestamp> [judges.cli] INFO <judge_name> judge feedback for <unit_id>: <text>

    Returns a dict mapping judge name to its most recent feedback string.
    Only the last feedback entry per judge is kept (most recent review round).
    """
    log_file = Path(os.environ.get(
        "JUDGE_LOG_FILE",
        str(Path(__file__).resolve().parent / "logs" / "orchestrator.log"),
    ))
    if not log_file.exists():
        return {}

    pattern = re.compile(
        rf"(\S+) judge feedback for {re.escape(unit_id)}: (.+)",
    )

    feedback: dict[str, str] = {}
    for line in log_file.read_text(encoding="utf-8").splitlines():
        match = pattern.search(line)
        if match:
            judge_name = match.group(1)
            feedback[judge_name] = match.group(2)

    return feedback


# Command registry: name -> (handler, min_args, description)
_COMMANDS: dict[str, tuple[Callable[..., int], int, str]] = {
    "status": (cmd_status, 0, "Show backlog summary"),
    "next": (cmd_next, 0, "Print next actionable work unit"),
    "execute": (cmd_execute, 1, "Execute a work unit: execute <id> [feedback]"),
    "review": (cmd_review, 1, "Review a work unit: review <id>"),
    "security-review": (cmd_security_review, 1, "Security review: security-review <id>"),
    "set-status": (cmd_set_status, 2, "Set status: set-status <id> <status>"),
    "mark-done": (cmd_mark_done, 1, "Mark done: mark-done <id>"),
    "validate-backlog": (cmd_validate_backlog, 0, "Validate backlog integrity"),
    "log": (cmd_log, 1, "Log a message: log <message>"),
    "report": (cmd_report, 0, "Progress report: report [since-timestamp]"),
}


def main() -> int:
    """Parse arguments and dispatch to the appropriate command."""
    setup_logging()

    if len(sys.argv) < 2:
        print("Usage: python3 -m devbench.cli <command> [args]")
        print("\nCommands:")
        for name, (_, _, desc) in sorted(_COMMANDS.items()):
            print(f"  {name:<20} {desc}")
        return 1

    command = sys.argv[1]
    if command not in _COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(_COMMANDS))}", file=sys.stderr)
        return 1

    func, min_args, _ = _COMMANDS[command]
    args = sys.argv[2:]

    if len(args) < min_args:
        print(f"Command '{command}' requires at least {min_args} argument(s)", file=sys.stderr)
        return 1

    return func(*args[: min_args + 1]) if len(args) > min_args else func(*args[:min_args])


if __name__ == "__main__":
    sys.exit(main())
