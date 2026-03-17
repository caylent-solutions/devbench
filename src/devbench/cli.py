"""CLI entry point for the judges system.

Provides shell-callable commands so Claude Code (or any external process)
can invoke judge operations, query backlog status, and execute work units.

Usage::

    python3 -m devbench.cli [--config <path>] <command> [args]

Options::

    --config <path>         Path to devbench YAML config (sets JUDGE_CONFIG_PATH).
                            Overrides the JUDGE_CONFIG_PATH environment variable.

Commands::

    status [--detail]       Show backlog summary (counts by status).
                            With --detail: also lists all in-queue Tasks in
                            priority order and all blocked Tasks with their
                            unresolved dependency IDs.
    next [--claim]          Print the next actionable work unit ID and title.
                            Read-only by default; with --claim also sets the
                            unit status to in-progress.
    execute <id>            Spawn a Claude Code agent to execute a work unit
    review <id>             Run all review judges on a work unit, print JSON results
    security-review <id>    Run the security review judge on a work unit
    set-status <id> <s>     Force any status (no gate — use for recovery/lifecycle transitions)
    mark-done <id>          Mark unit as Done (enforces done-gate: all judges must have passed)
    validate-backlog        Check backlog integrity (file existence, status sync, orphans, deps, dep-status)
    sync-blocked            Scan in-queue units and mark those with unmet deps as blocked
    report [since]          Print progress report with velocity stats
    log <message>           Append a message to the orchestrator log file

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


# Pre-parse --config before any devbench imports so that config.py loads the
# correct YAML at module import time (config.py reads JUDGE_CONFIG_PATH on import).
def _pre_parse_config(argv: list[str]) -> None:
    """Extract --config <path> from argv and set JUDGE_CONFIG_PATH env var."""
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            os.environ["JUDGE_CONFIG_PATH"] = argv[i + 1]
            # Remove --config and its value so downstream parsing is unaffected.
            argv.pop(i + 1)
            argv.pop(i)
            return

_pre_parse_config(sys.argv)

from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    WORKSPACE_ROOT,
    resolve_repo,
    validate_repo,
)
from devbench.constants import (
    DISPLAY_STATUS_VALUES,
    STATUS_IN_PROGRESS,
    STATUS_IN_REVIEW,
    STATUS_SEPARATOR_WIDTH,
)
from devbench.judges.base import Verdict
from devbench.judges.changes_manifest import ChangesManifestJudge
from devbench.judges.code_review import CodeReviewJudge
from devbench.judges.doc_review import DocReviewJudge
from devbench.judges.security_review import SecurityReviewJudge
from devbench.judges.test_review import TestReviewJudge
from devbench.log_setup import setup_logging

logger = logging.getLogger("devbench.cli")


def cmd_status(detail: bool = False) -> int:
    """Print backlog summary grouped by status.

    When ``detail`` is ``True``, appends two extra sections after the summary:

    - **In Queue (N):** all actionable Task-level work units in priority order
      (in-progress first, then in-queue sorted by numeric ID), matching the
      order returned by ``get_parallel_candidates``.
    - **Blocked (N):** all blocked Task-level work units with the dependency
      IDs they are waiting on.

    Story, Feature, and Epic rollup rows are never shown in the detail sections.
    """
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

    if detail:
        _print_status_detail(actionable, units)

    return 0


def _print_status_detail(
    in_queue_candidates: list[WorkUnit],
    all_units: list[WorkUnit],
) -> None:
    """Print the --detail sections: in-queue Tasks and blocked Tasks.

    ``in_queue_candidates`` is already sorted by priority (from
    ``get_parallel_candidates``), so order is preserved as-is.

    Only Task-level work units are shown; Story/Feature/Epic rollups are excluded.
    """
    # In-queue / in-progress actionable tasks (already priority-sorted)
    task_candidates = [u for u in in_queue_candidates if u.unit_type is WorkUnitType.TASK]
    print(f"\nIn Queue ({len(task_candidates)}):")
    for i, unit in enumerate(task_candidates, start=1):
        print(f"  {i}. {unit.id}  {unit.title}")

    # Blocked tasks with their unresolved dep IDs
    done_ids = frozenset(u.id for u in all_units if u.status is WorkUnitStatus.DONE)
    blocked_tasks = [
        u for u in all_units
        if u.status is WorkUnitStatus.BLOCKED and u.unit_type is WorkUnitType.TASK
    ]
    print(f"\nBlocked ({len(blocked_tasks)}):")
    for unit in blocked_tasks:
        unmet = [dep for dep in unit.dependencies if dep not in done_ids]
        waiting = ", ".join(unmet) if unmet else "(no blocking dep — re-check deps)"
        print(f"  {unit.id}  waiting on: {waiting}")


def cmd_next(claim: bool = False) -> int:
    """Print the next actionable work unit.

    By default this command is read-only: it prints the next unit's JSON without
    mutating any status.  Pass ``claim=True`` (or ``--claim`` on the CLI) to
    additionally set the unit's status to ``in-progress``.

    Secondary dep guard: after ``get_parallel_candidates`` returns candidates,
    the top candidate's dependencies are re-verified against the full ``done``
    set.  If any dependency is not ``done``, the command exits 1 with an
    actionable error on stderr listing each unmet dep ID and its current status.
    This guard catches any unit that slips through when ``sync-blocked`` has not
    been run.
    """
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

    # Secondary dep guard: verify all deps are done before claiming the unit.
    unmet = _unmet_deps(unit, units)
    if unmet:
        dep_list = "; ".join(f"{dep_id} (status: {status})" for dep_id, status in unmet)
        print(
            f"ERROR: {unit.id} has unmet dependencies: {dep_list}",
            file=sys.stderr,
        )
        return 1

    if claim:
        wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
        if not wu_file.exists():
            wu_file = WORKSPACE_ROOT / unit.file_path
        if not wu_file.exists():
            print(
                f"Cannot claim {unit.id}: work unit file not found at {wu_file}",
                file=sys.stderr,
            )
            return 1
        mgr = BacklogManager()
        mgr.force_status(wu_file, BACKLOG_INDEX, unit.id, STATUS_IN_PROGRESS)
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
    """Spawn a Claude Code agent to execute a work unit.

    Pre-run dep guard: before spawning the agent, all listed dependencies of
    the target unit are checked against the full ``done`` set.  If any
    dependency is not ``done``, the command exits 1 with an actionable error
    on stderr that names each unmet dep ID and its current status.
    """
    from devbench.execution import executor as claude_executor

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    # Pre-run dep guard: refuse to execute if any dep is not done.
    unmet = _unmet_deps(target, units)
    if unmet:
        dep_list = "; ".join(f"{dep_id} (status: {status})" for dep_id, status in unmet)
        print(
            f"ERROR: {unit_id} has unmet dependencies: {dep_list}",
            file=sys.stderr,
        )
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

    repo_config = resolve_repo(target.repo)
    validate_repo(repo_config)
    repo_path = repo_config.local_path

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / target.file_path

    # Resolve file_path to the absolute path so log_comment writes to the right file
    target.file_path = wu_file

    # Automatically mark as in-review in both files
    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_IN_REVIEW)
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
        judge_result = judge.evaluate(work_unit_path=wu_file, repo_path=repo_path, repo=repo_config.name)
        passed = judge_result.verdict == Verdict.PASS
        if not passed:
            all_passed = False

        # Write judge verdict comment to the work-unit file so mark_done can verify all judges passed
        if passed:
            target.log_comment(
                agent_id=f"judge/{judge.name}",
                action="REVIEW_PASS",
                message=judge_result.reasoning,
            )
        else:
            target.log_comment(
                agent_id=f"judge/{judge.name}",
                action="REVIEW_FAIL",
                message=f"{judge_result.reasoning} | Fix: {judge_result.feedback}",
            )

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

    repo_config = resolve_repo(target.repo)
    validate_repo(repo_config)
    repo_path = repo_config.local_path

    wu_file = BACKLOG_ROOT / target.file_path if not target.file_path.is_absolute() else target.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / target.file_path

    judge = SecurityReviewJudge()
    logger.info("Running security_review judge on %s", unit_id)
    result = judge.evaluate(work_unit_path=wu_file, repo_path=repo_path, repo=repo_config.name)
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

    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, new_status)

    logger.info("Set %s to %s", unit_id, new_status)
    print(f"Set {unit_id} to {new_status}")
    return 0


def cmd_mark_done(unit_id: str) -> int:
    """Mark a work unit as Done, enforcing the done-gate check.

    Calls ``BacklogManager.mark_done()`` which verifies that all required
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

    mgr = BacklogManager()
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
    - Status Summary counts match actual per-status counts in the index.
    - Every work unit file contains a '## Comments' section header.
    - Every in-queue or in-progress unit has all deps with status 'done'.

    Exits 0 if the backlog is consistent; 1 with actionable error messages
    if any inconsistencies are found.
    """
    mgr = BacklogManager()
    errors = mgr.validate(BACKLOG_INDEX, BACKLOG_INDEX.parent)
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


def cmd_sync_blocked() -> int:
    """Scan all in-queue work units and mark those with unmet deps as blocked.

    Algorithm:

    1. Parse all units from the backlog index.
    2. For each unit whose status is ``in-queue``:

       a. Check whether all its deps have status ``done``.
       b. If any dep is not done, call ``BacklogManager.force_status`` with
          ``blocked`` and record the unit ID, title, and unmet dep IDs.

    3. Print a structured report listing each newly blocked unit and its
       unmet dependency IDs.
    4. Print a summary line::

           Blocked N unit(s). M already blocked. K in-queue units have all deps met.

    5. Exit 0 (changed units are not an error condition).

    The command is idempotent: running it twice produces the same final state.
    Already-blocked units and units in any other non-``in-queue`` status are
    not touched.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    done_ids = frozenset(u.id for u in units if u.status is WorkUnitStatus.DONE)

    mgr = BacklogManager()

    newly_blocked: list[tuple[str, str, list[str]]] = []  # (id, title, unmet_deps)
    already_blocked_count = 0
    satisfied_in_queue_count = 0

    for unit in units:
        if unit.status is WorkUnitStatus.BLOCKED:
            already_blocked_count += 1
            continue

        if unit.status is not WorkUnitStatus.IN_QUEUE:
            continue

        unmet = [dep for dep in unit.dependencies if dep not in done_ids]
        if not unmet:
            satisfied_in_queue_count += 1
            continue

        wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
        if not wu_file.exists():
            print(
                f"ERROR: cannot resolve file for unit {unit.id}: {wu_file} does not exist",
                file=sys.stderr,
            )
            return 1

        mgr.force_status(wu_file, BACKLOG_INDEX, unit.id, "blocked")
        newly_blocked.append((unit.id, unit.title, unmet))

    for unit_id, title, unmet_deps in newly_blocked:
        print(f"  BLOCKED {unit_id} ({title}) — unmet deps: {', '.join(unmet_deps)}")

    print(
        f"Blocked {len(newly_blocked)} unit(s). "
        f"{already_blocked_count} already blocked. "
        f"{satisfied_in_queue_count} in-queue units have all deps met."
    )
    return 0


def _find_unit(units: list[WorkUnit], unit_id: str) -> WorkUnit | None:
    """Find a work unit by ID (case-insensitive)."""
    for unit in units:
        if unit.id.lower() == unit_id.lower():
            return unit
    return None


def _unmet_deps(unit: WorkUnit, all_units: list[WorkUnit]) -> list[tuple[str, str]]:
    """Return a list of ``(dep_id, status_string)`` for each unmet dependency.

    A dependency is *met* only when its status is ``done``.  If a dep ID is
    not found in ``all_units``, it is reported with status ``"unknown"``.

    Returns an empty list when all deps are satisfied.
    """
    status_by_id = {u.id: u.status.value.lower() for u in all_units}
    done_ids = frozenset(u.id for u in all_units if u.status is WorkUnitStatus.DONE)
    return [
        (dep_id, status_by_id.get(dep_id, "unknown"))
        for dep_id in unit.dependencies
        if dep_id not in done_ids
    ]


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

    marker = f" judge feedback for {unit_id}: "
    feedback: dict[str, str] = {}
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if marker not in line:
            continue
        match = pattern.search(line)
        if match:
            judge_name = match.group(1)
            feedback[judge_name] = match.group(2)

    return feedback


# Command registry: name -> (handler, min_args, description)
_COMMANDS: dict[str, tuple[Callable[..., int], int, str]] = {
    "status": (cmd_status, 0, "Show backlog summary"),
    "next": (cmd_next, 0, "Print next actionable work unit (read-only; --claim sets in-progress)"),
    "execute": (cmd_execute, 1, "Execute a work unit: execute <id> [feedback]"),
    "review": (cmd_review, 1, "Review a work unit: review <id>"),
    "security-review": (cmd_security_review, 1, "Security review: security-review <id>"),
    "set-status": (cmd_set_status, 2, "Set status: set-status <id> <status>"),
    "mark-done": (cmd_mark_done, 1, "Mark done: mark-done <id>"),
    "validate-backlog": (cmd_validate_backlog, 0, "Validate backlog integrity"),
    "sync-blocked": (cmd_sync_blocked, 0, "Mark in-queue units with unmet deps as blocked"),
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
    raw_args = sys.argv[2:]
    detail = "--detail" in raw_args
    claim = "--claim" in raw_args
    args = [a for a in raw_args if a not in ("--detail", "--claim")]

    if len(args) < min_args:
        print(f"Command '{command}' requires at least {min_args} argument(s)", file=sys.stderr)
        return 1

    if command == "status" and detail:
        return func(detail=True)

    if command == "next" and claim:
        return func(claim=True)

    return func(*args[: min_args + 1]) if len(args) > min_args else func(*args[:min_args])


if __name__ == "__main__":
    sys.exit(main())
