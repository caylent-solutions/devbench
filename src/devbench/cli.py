"""CLI entry point for the devbench system.

Provides shell-callable commands so Claude Code (or any external process)
can query backlog status and bridge plugin agents to repo context.

Usage::

    python3 -m devbench.cli [--config <path>] <command> [args]

Options::

    --config <path>         Path to devbench YAML config (sets JUDGE_CONFIG_PATH).
                            Overrides the JUDGE_CONFIG_PATH environment variable.

Commands::

    status                  Show backlog summary (counts by status)
    next                    Print the next actionable work unit ID and title
    claim <id>              Claim a work unit (transition to in-progress)
    set-status <id> <s>     Force any status (no gate — use for recovery/lifecycle transitions)
    mark-done <id>          Mark unit as Done (enforces done-gate: all judges must have passed)
    validate-backlog        Check backlog integrity (file existence, status sync, orphans, deps, summary)
    ensure-branch <id>      Create or switch to work unit branch before executor runs
    git-ops <id>            Run git operations for a work unit (commit-only when defer_pr is set)
    git-ops-finalize <repo> Push single branch and create PR (after all deferred commits)
    report [since]          Print progress report with velocity stats
    log <message>           Append a message to the orchestrator log file
    start                   Run the orchestrate skill via the Claude Agent SDK (non-interactive)

Plugin agent bridge commands (used by devbench plugin agents)::

    read-unit <id>                          Return work unit content and repo path as JSON
    get-diff <id>                           Return combined git diff for the work unit's repo
    run-tests <id>                          Run test suite for the work unit's repo
    log-verdict <judge> <id> <v> [msg]      Log a judge verdict (pass|fail) to work unit Comments
    log-comment <agent> <id> <message>      Log a non-verdict agent comment to work unit Comments
    log-tdd <id> <phase> <message>          Log a TDD phase entry (RED|GREEN|REFACTOR) to TDD Cycle Log

All commands exit 0 on success, non-zero on failure. Output is structured
for easy parsing by Claude Code or other automation.
"""

import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
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
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    REPO_LOCAL_PATHS,
    RUNTIME_CONFIG,
    UPDATE_SUBMODULE,
    WORKSPACE_ROOT,
    resolve_repo,
    validate_repo,
)
from devbench.config_loader import get_configured_default_branch
from devbench.constants import (
    COMMENT_AGENT_TEMPLATE,
    COMMENT_ENTRY_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_SUBDIR,
    DEFAULT_PLUGIN_SUBPATH,
    DISPLAY_STATUS_VALUES,
    EM_DASH,
    FINALIZE_COMMIT_TEMPLATE,
    FINALIZE_PR_TITLE_TEMPLATE,
    STATUS_IN_PROGRESS,
    STATUS_SEPARATOR_WIDTH,
    VALID_TDD_PHASES,
)
from devbench.log_setup import setup_logging
from devbench.utils.process import run_command

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

    active = [u for u in units if u.status in (WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.IN_REVIEW)]
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


def cmd_claim(unit_id: str) -> int:
    """Transition a work unit to in-progress (claim it for execution).

    Use after ``devbench next`` to explicitly mark the unit as owned before
    the executor begins work.
    """
    units = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX).parse_index()
    unit = next((u for u in units if u.id == unit_id), None)
    if unit is None:
        print(f"ERROR: unit '{unit_id}' not found in backlog index", file=sys.stderr)
        return 1
    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        print(f"ERROR: work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1
    mgr = BacklogManager()
    mgr.force_status(wu_file, BACKLOG_INDEX, unit_id, STATUS_IN_PROGRESS)
    logger.info("Claimed %s (set to in-progress)", unit_id)
    print(f"Claimed {unit_id}")
    return 0


def cmd_set_status(unit_id: str, new_status: str) -> int:
    """Set the status of a work unit in both the work-unit file and BACKLOG.md."""
    from devbench.backlog.manager import VALID_STATUSES

    if new_status.lower() not in VALID_STATUSES:
        print(
            f"ERROR: Invalid status '{new_status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return 1

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    target = _find_unit(units, unit_id)
    if target is None:
        print(f"ERROR: Work unit '{unit_id}' not found", file=sys.stderr)
        return 1

    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1

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

    wu_file = _resolve_unit_file(target)
    if wu_file is None:
        print(f"ERROR: Work unit file not found for '{unit_id}'", file=sys.stderr)
        return 1

    mgr = BacklogManager()
    try:
        mgr.mark_done(wu_file, BACKLOG_INDEX, unit_id)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mgr._append_agent_comment(wu_file, "orchestrator", f"[DONE] Work unit {unit_id} completed")

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
    - Status Summary table exists and counts match the Full Work Unit Index.

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


def cmd_report(since: str = "", watch_interval: int = 0) -> int:
    """Print a formatted progress report with velocity and completion stats."""
    from datetime import datetime

    from devbench.reporting.report import generate_report

    log_file = Path(
        os.environ.get(
            "JUDGE_LOG_FILE",
            str(Path(__file__).resolve().parent / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME),
        )
    )

    since_dt = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    if watch_interval > 0:
        import time

        # Capture watch loop start once so the "This run" column tracks activity
        # since the user kicked off `devbench report --watch`, not since each tick.
        report_started_at = datetime.now(UTC)
        try:
            while True:
                print("\033[H\033[J", end="")  # clear screen
                report = generate_report(log_path=log_file, since=since_dt, report_started_at=report_started_at)
                print(report)
                time.sleep(watch_interval)
        except KeyboardInterrupt:
            return 0
    else:
        report = generate_report(log_path=log_file, since=since_dt)
        print(report)
        return 0


def cmd_log(message: str) -> int:
    """Append a message to the orchestrator log."""
    logger.info("[USER] %s", message)
    print(f"Logged: {message}")
    return 0


def cmd_read_unit(first_arg: str, second_arg: str = "") -> int:
    """Return work unit content and resolved repo path as JSON.

    Usage::

        read-unit <id>
        read-unit --strip-comments <id>

    Output::

        {
          "unit_id": "E0-F1-S1-T1",
          "work_unit_path": "/abs/path/to/unit.md",
          "repo_path": "/abs/path/to/repo",
          "repo": "org/repo",
          "content": "<full work unit markdown>"
        }

    When ``--strip-comments`` is passed, the ``content`` field contains the
    work unit text up to (but not including) the ``\\n## Comments`` marker.
    This prevents reviewer agents from seeing prior verdict entries.

    Used by plugin agents to get repo context without knowing devbench.yaml.
    """
    if first_arg == "--strip-comments":
        strip_comments = True
        unit_id = second_arg
        if not unit_id:
            print("ERROR: unit_id required after --strip-comments", file=sys.stderr)
            return 1
    else:
        strip_comments = False
        unit_id = first_arg

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    content = wu_file.read_text(encoding="utf-8")
    if strip_comments:
        marker = f"\n{COMMENTS_SECTION_HEADER}"
        idx = content.find(marker)
        if idx != -1:
            content = content[:idx]

    print(
        json.dumps(
            {
                "unit_id": unit.id,
                "work_unit_path": str(wu_file),
                "repo_path": str(repo_path),
                "repo": canonical_repo,
                "content": content,
            }
        )
    )
    return 0


def cmd_get_diff(unit_id: str) -> int:
    """Return the combined git diff for the work unit's target repo.

    Includes staged changes, unstaged changes, branch diff vs default branch,
    and untracked files formatted as synthetic diff hunks.

    Used by plugin agents instead of running raw git commands so they do not
    need to know the repo path.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    parts: list[str] = []

    rc, stdout, _ = run_command(["git", "diff", "--cached"], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    rc, stdout, _ = run_command(["git", "diff"], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    configured = get_configured_default_branch(canonical_repo, RUNTIME_CONFIG)
    if configured:
        default_branch = configured
    else:
        rc, stdout, _ = run_command(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
            cwd=repo_path,
        )
        if rc != 0 or not stdout.strip():
            print(
                f"ERROR: Cannot determine default branch for '{canonical_repo}'. "
                "Run 'git remote set-head origin --auto' to configure it.",
                file=sys.stderr,
            )
            return 1
        default_branch = stdout.strip().removeprefix("origin/")

    rc, stdout, _ = run_command(["git", "diff", f"origin/{default_branch}"], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    rc, stdout, _ = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo_path,
    )
    if rc == 0 and stdout.strip():
        for raw_filepath in stdout.splitlines():
            filepath = raw_filepath.strip()
            if not filepath:
                continue
            abs_path = repo_path / filepath
            try:
                file_content = abs_path.read_text(encoding="utf-8")
            except OSError:
                continue
            lines = file_content.splitlines(keepends=True)
            added = "".join(f"+{line}" for line in lines)
            hunk = (
                f"diff --git a/{filepath} b/{filepath}\n"
                f"new file mode 100644\n"
                f"--- /dev/null\n"
                f"+++ b/{filepath}\n"
                f"@@ -0,0 +1,{len(lines)} @@\n"
                f"{added}"
            )
            parts.append(hunk)

    print("\n".join(parts) if parts else "(no changes)")
    return 0


def cmd_run_tests(unit_id: str) -> int:
    """Run the test suite for the work unit's target repo and return the output.

    Uses ``make test`` when the repo has a Makefile with a ``test`` target,
    otherwise falls back to ``pytest``.  Exits non-zero if the test run fails.

    Used by the test-reviewer agent to obtain test execution evidence.
    """
    from devbench.config import TEST_TIMEOUT

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    rc, stdout, _ = run_command(["make", "-n", "test"], cwd=repo_path)
    cmd = ["make", "test"] if rc == 0 else ["pytest", "--no-header", "-q", "-p", "no:cacheprovider"]

    rc, stdout, stderr = run_command(cmd, cwd=repo_path, timeout=TEST_TIMEOUT)
    combined = "\n".join(part for part in (stdout, stderr) if part.strip())
    print(combined if combined else "(no output)")
    return rc


def _reject_em_dash(field_name: str, text: str) -> int | None:
    """Reject any agent-supplied text containing U+2014 before it reaches the backlog.

    The validate-backlog Check 10 rejects work-unit files containing em-dash,
    so any CLI writer that accepts free-form agent text must reject em-dash at
    the input boundary — otherwise LLM-written verdict feedback (which naturally
    uses em-dashes) poisons the file and blocks the next validate-backlog run.

    Returns:
        ``1`` (non-zero exit code) with stderr populated when em-dash is found;
        ``None`` when the text is clean. Callers return the int on non-None.
    """
    if EM_DASH in text:
        print(
            f"ERROR: {field_name} contains em-dash character (U+2014); use '--' (double hyphen) instead.",
            file=sys.stderr,
        )
        return 1
    return None


def cmd_log_verdict(judge_name: str, unit_id: str, verdict: str, feedback: str = "") -> int:
    """Append a judge verdict to the work unit's Comments section and log feedback.

    Arguments:
        judge_name:  Judge identifier, e.g. ``code_review`` (matches REVIEW_JUDGE_NAMES).
        unit_id:     Work unit ID, e.g. ``E0-F1-S1-T1``.
        verdict:     ``pass`` or ``fail``.
        feedback:    One-line summary of the verdict (required for ``fail``).

    The entry written to the work unit uses the same format as the orchestrator:
    ``[judge/<name>] [REVIEW_PASS|REVIEW_FAIL] <feedback>``

    Feedback is also written to the orchestrator log for audit purposes.
    """
    verdict_lower = verdict.strip().lower()
    if verdict_lower not in ("pass", "fail"):
        print(f"ERROR: verdict must be 'pass' or 'fail', got '{verdict}'", file=sys.stderr)
        return 1

    rc = _reject_em_dash("feedback", feedback)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    action = "REVIEW_PASS" if verdict_lower == "pass" else "REVIEW_FAIL"
    agent_id = f"judge/{judge_name}"
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    entry = COMMENT_ENTRY_TEMPLATE.format(
        timestamp=timestamp,
        agent_id=agent_id,
        action=action,
        message=feedback,
    )

    content = wu_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    wu_file.write_text(content, encoding="utf-8")

    # Log feedback for audit trail.
    if feedback:
        logger.info("%s judge feedback for %s: %s", judge_name, unit_id, feedback)

    print(json.dumps({"unit_id": unit_id, "judge": judge_name, "verdict": verdict_lower}))
    return 0


def cmd_log_comment(agent_name: str, unit_id: str, message: str) -> int:
    """Append a non-verdict agent comment to the work unit's Comments section.

    Writes: ``[YYYY-MM-DD HH:MM UTC] [agent/<name>] <message>``

    Use for non-judge actors (executor, blocker-resolver, review-supervisor summary)
    that need to log progress without emitting a REVIEW_PASS/REVIEW_FAIL token.
    """
    rc = _reject_em_dash("message", message)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    entry = COMMENT_AGENT_TEMPLATE.format(
        timestamp=timestamp,
        name=agent_name,
        message=message,
    )

    content = wu_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    wu_file.write_text(content, encoding="utf-8")

    logger.info("agent/%s comment for %s: %s", agent_name, unit_id, message)
    print(json.dumps({"unit_id": unit_id, "agent": agent_name}))
    return 0


def cmd_log_tdd(unit_id: str, phase: str, message: str) -> int:
    """Append a TDD phase entry to the work unit's TDD Cycle Log section.

    Writes: ``- [<PHASE>] <ISO-8601 timestamp> -- <message>``

    Arguments:
        unit_id:  Work unit ID, e.g. ``E0-F1-S1-T1``.
        phase:    TDD phase, one of ``RED``, ``GREEN``, ``REFACTOR`` (case-insensitive).
        message:  Description of the TDD phase outcome.

    Exits 0 on success, non-zero on any error.  The ``## TDD Cycle Log``
    section must already exist in the work unit file; this command fails fast
    if the section is absent rather than silently creating it.
    """
    phase_upper = phase.upper()
    if phase_upper not in VALID_TDD_PHASES:
        print(
            f"ERROR: Invalid TDD phase '{phase}'. Valid phases: {', '.join(sorted(VALID_TDD_PHASES))}",
            file=sys.stderr,
        )
        return 1

    rc = _reject_em_dash("message", message)
    if rc is not None:
        return rc

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if not wu_file.exists():
        wu_file = WORKSPACE_ROOT / unit.file_path

    mgr = BacklogManager()
    try:
        mgr._append_tdd_entry(wu_file, phase_upper, message)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    logger.info("TDD %s entry logged for %s", phase_upper, unit_id)
    print(json.dumps({"unit_id": unit_id, "phase": phase_upper}))
    return 0


def cmd_ensure_branch(unit_id: str) -> int:
    """Create or switch to the feature branch for a work unit before executor runs.

    Derives the branch name from the work unit ID (``backlog/<id-lower>``) and
    calls :meth:`~devbench.github.git_ops.GitOpsJudge.ensure_branch` to switch
    to it, stashing and popping if the working tree is dirty.

    Used by the orchestrate skill immediately after ``devbench next`` and before
    invoking the executor agent.
    """
    from devbench.github.git_ops import GitOpsJudge

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        return 1

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    from devbench.config import SINGLE_BRANCH

    branch = SINGLE_BRANCH if SINGLE_BRANCH else f"backlog/{unit_id.lower()}"
    ops = GitOpsJudge()
    ops.ensure_branch(canonical_repo, repo_path, branch)
    logger.info("Branch ready: %s on %s", branch, canonical_repo)
    return 0


def _resolve_git_ops_context(unit_id: str) -> tuple[WorkUnit, str, Path]:
    """Resolve unit, canonical repo, and local path for git-ops commands.

    Returns:
        Tuple of (unit, canonical_repo, repo_path).

    Raises:
        SystemExit: If the unit is not found or the repo path is not configured.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    unit = _find_unit(units, unit_id)
    if unit is None:
        print(f"ERROR: Work unit '{unit_id}' not found in backlog", file=sys.stderr)
        sys.exit(1)

    canonical_repo = resolve_repo(unit.repo)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        sys.exit(1)

    return unit, canonical_repo, repo_path


def _git_ops_deferred(unit_id: str, unit: WorkUnit, canonical_repo: str, repo_path: Path, branch: str) -> int:
    """Commit locally only (no push/PR/merge) for single-branch deferred mode."""
    from devbench.github.git_ops import GitOpsJudge

    ops = GitOpsJudge()
    commit_message = f"{unit_id}: {unit.title}"

    wu_file = _resolve_unit_file(unit)
    mgr = BacklogManager()

    ops.commit_local(canonical_repo, repo_path, branch, commit_message)
    logger.info("Committed locally (deferred PR): %s on %s", unit_id, branch)
    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[COMMIT_DEFERRED] {commit_message}")
    print(json.dumps({"unit_id": unit_id, "mode": "deferred", "branch": branch}))
    return 0


def cmd_git_ops(unit_id: str) -> int:
    """Run the full git operations sequence for a completed work unit.

    Sequence:
    1. Resolve repo and local path from the work unit.
    2. Determine branch name from work unit ID (``backlog/<id-lower>``).
    3. Commit and push staged changes.
    4. Create a pull request.
    5. Wait for CI checks to pass.
    6. Merge the pull request.
    7. Update the parent submodule reference (only when ``UPDATE_SUBMODULE`` is ``True``).

    Used by the orchestrate skill after all review judges have passed.
    """
    from devbench.github.git_ops import GitOpsJudge

    unit, canonical_repo, repo_path = _resolve_git_ops_context(unit_id)

    from devbench.config import DEFER_PR, SINGLE_BRANCH

    branch = SINGLE_BRANCH if SINGLE_BRANCH else f"backlog/{unit_id.lower()}"

    if DEFER_PR:
        return _git_ops_deferred(unit_id, unit, canonical_repo, repo_path, branch)

    # Standard mode: commit, push, PR, CI, merge.
    commit_message = f"{unit_id}: {unit.title}"
    pr_title = f"{unit_id}: {unit.title}"
    pr_body = f"Automated PR for work unit {unit_id}.\n\n{unit.title}"

    ops = GitOpsJudge()

    wu_file = _resolve_unit_file(unit)
    if wu_file is None:
        logger.warning("Could not resolve work unit file for %s -- audit comments will be skipped", unit_id)

    mgr = BacklogManager()

    from devbench.github.git_ops import ConflictingPRError

    ops.commit_and_push(canonical_repo, repo_path, branch, commit_message)
    logger.info("Committed and pushed %s", unit_id)

    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[PR_CREATED] {pr_url}")

    # Extract PR number from URL (e.g. https://github.com/org/repo/pull/42)
    pr_number_str = pr_url.rstrip("/").split("/")[-1]
    if not pr_number_str.isdigit():
        print(f"ERROR: Could not parse PR number from URL: {pr_url}", file=sys.stderr)
        return 1
    pr_number = int(pr_number_str)

    checks_passed = ops.wait_for_checks(canonical_repo, pr_number, repo_path=repo_path)
    if not checks_passed:
        print(f"ERROR: CI checks failed for PR #{pr_number} on {canonical_repo}", file=sys.stderr)
        return 1

    try:
        ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)
    except ConflictingPRError:
        logger.warning(
            "PR #%d on %s is CONFLICTING -- rebasing and retrying merge once",
            pr_number,
            canonical_repo,
        )
        ops.rebase_and_force_push(canonical_repo, repo_path, branch)
        try:
            ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)
        except Exception as retry_exc:
            print(
                f"ERROR: Merge retry failed for PR #{pr_number} on {canonical_repo}: {retry_exc}",
                file=sys.stderr,
            )
            return 1

    if wu_file is not None:
        mgr._append_agent_comment(wu_file, "git_ops", f"[PR_MERGED] {pr_url}")

    logger.info("Merged PR #%d for %s", pr_number, unit_id)

    ops.checkout_default_branch(canonical_repo, repo_path)
    logger.info("Checked out default branch after merge for %s", unit_id)

    if UPDATE_SUBMODULE:
        ops.update_parent_submodule_ref(
            canonical_repo,
            repo_path,
            f"chore: update {repo_path.name} submodule after {unit_id}",
        )

    print(json.dumps({"unit_id": unit_id, "pr_url": pr_url, "pr_number": pr_number}))
    return 0


def cmd_git_ops_finalize(repo_name: str) -> int:
    """Push the single branch and create a PR after all deferred commits.

    Used after all work units are complete in single-branch / defer-PR mode.
    Pushes the accumulated commits to the remote and creates a pull request.

    Arguments:
        repo_name: Repository name (short or fully-qualified).
    """
    from devbench.config import DEFER_PR, SINGLE_BRANCH

    if not SINGLE_BRANCH:
        print(
            "ERROR: git-ops-finalize requires git_ops.single_branch to be set in devbench.yaml",
            file=sys.stderr,
        )
        return 1
    if not DEFER_PR:
        print(
            "ERROR: git-ops-finalize requires git_ops.defer_pr to be true in devbench.yaml",
            file=sys.stderr,
        )
        return 1

    from devbench.github.git_ops import GitOpsJudge

    canonical_repo = resolve_repo(repo_name)
    validate_repo(canonical_repo)
    repo_path = REPO_LOCAL_PATHS.get(canonical_repo)
    if repo_path is None:
        print(f"ERROR: No local path configured for repo '{canonical_repo}'", file=sys.stderr)
        return 1

    branch = SINGLE_BRANCH
    pr_title = FINALIZE_PR_TITLE_TEMPLATE.format(branch=branch)
    pr_body = (
        f"Accumulated commits from DevBench single-branch execution.\n\nBranch: `{branch}`\nRepo: `{canonical_repo}`"
    )

    ops = GitOpsJudge()

    ops.commit_and_push(canonical_repo, repo_path, branch, FINALIZE_COMMIT_TEMPLATE.format(branch=branch))
    logger.info("Pushed branch %s to %s", branch, canonical_repo)

    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

    print(json.dumps({"repo": canonical_repo, "branch": branch, "pr_url": pr_url}))
    return 0


def cmd_start() -> int:
    """Run the devbench orchestrate skill non-interactively via the Claude Agent SDK.

    Loads the devbench plugin from the plugin directory adjacent to this package
    and invokes the orchestrate skill, which processes the backlog until all
    work units are complete or blocked.

    Equivalent to running ``claude --plugin-dir <plugin>`` and invoking
    the orchestrate skill interactively, but suitable for CI/unattended runs.
    """
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions, query

    plugin_path = Path(__file__).parent.parent.parent / DEFAULT_PLUGIN_SUBPATH

    async def _run() -> None:
        async for message in query(
            prompt="Run the devbench:orchestrate skill to process the backlog until complete",
            options=ClaudeAgentOptions(
                plugins=[{"type": "local", "path": str(plugin_path)}],
                permission_mode="bypassPermissions",
            ),
        ):
            logger.info("sdk message: %s", message)

    asyncio.run(_run())
    return 0


def _find_unit(units: list[WorkUnit], unit_id: str) -> WorkUnit | None:
    """Find a work unit by ID (case-insensitive)."""
    for unit in units:
        if unit.id.lower() == unit_id.lower():
            return unit
    return None


def _resolve_unit_file(unit: WorkUnit) -> Path | None:
    """Return the absolute path to the work unit file, or None if not found.

    Tries BACKLOG_ROOT / unit.file_path first, then WORKSPACE_ROOT / unit.file_path.

    Args:
        unit: WorkUnit whose file_path must be resolved.

    Returns:
        The resolved :class:`pathlib.Path` if the file exists, ``None`` otherwise.
    """
    wu_file = BACKLOG_ROOT / unit.file_path if not unit.file_path.is_absolute() else unit.file_path
    if wu_file.exists():
        return wu_file
    wu_file = WORKSPACE_ROOT / unit.file_path
    return wu_file if wu_file.exists() else None


# Command registry: name -> (handler, min_args, description)
_COMMANDS: dict[str, tuple[Callable[..., int], int, str]] = {
    "status": (cmd_status, 0, "Show backlog summary"),
    "next": (cmd_next, 0, "Print next actionable work unit"),
    "claim": (cmd_claim, 1, "Claim a work unit: claim <id>"),
    "set-status": (cmd_set_status, 2, "Set status: set-status <id> <status>"),
    "mark-done": (cmd_mark_done, 1, "Mark done: mark-done <id>"),
    "validate-backlog": (cmd_validate_backlog, 0, "Validate backlog integrity"),
    "ensure-branch": (cmd_ensure_branch, 1, "Create or switch to work unit branch: ensure-branch <id>"),
    "git-ops": (cmd_git_ops, 1, "Run git operations for a work unit: git-ops <id>"),
    "git-ops-finalize": (cmd_git_ops_finalize, 1, "Push single branch and create PR: git-ops-finalize <repo>"),
    "log": (cmd_log, 1, "Log a message: log <message>"),
    "report": (
        cmd_report,
        0,
        "Progress report — renders All-time + Current run windows by default: report [--watch N] [since-timestamp]",
    ),
    "start": (cmd_start, 0, "Run orchestrate skill via Agent SDK (non-interactive)"),
    # Plugin agent bridge commands — used by devbench plugin agents
    "read-unit": (cmd_read_unit, 1, "Work unit content + repo path as JSON: read-unit [--strip-comments] <id>"),
    "get-diff": (cmd_get_diff, 1, "Return combined git diff for work unit's repo: get-diff <id>"),
    "run-tests": (cmd_run_tests, 1, "Run test suite for work unit's repo: run-tests <id>"),
    "log-verdict": (cmd_log_verdict, 3, "Log judge verdict: log-verdict <judge> <id> <pass|fail> [feedback]"),
    "log-comment": (cmd_log_comment, 3, "Log agent comment: log-comment <agent> <id> <message>"),
    "log-tdd": (cmd_log_tdd, 3, "Log TDD phase: log-tdd <id> <RED|GREEN|REFACTOR> <message>"),
}


_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})


def _print_usage() -> None:
    """Print top-level usage and command list. Shared by the `-h`/`--help` path and the no-args path."""
    print("Usage: devbench <command> [args]")
    print("       devbench <command> --help    (per-command usage)")
    print("       devbench --help              (this message)")
    print("\nCommands:")
    for name, (_, _, desc) in sorted(_COMMANDS.items()):
        print(f"  {name:<20} {desc}")


def main() -> int:
    """Parse arguments and dispatch to the appropriate command."""
    setup_logging()

    # Top-level: `devbench`, `devbench --help`, `devbench -h` all print usage and exit 0.
    # Only a typo'd command (e.g. `devbench foo`) returns 1.
    if len(sys.argv) < 2 or sys.argv[1] in _HELP_FLAGS:
        _print_usage()
        return 0

    command = sys.argv[1]
    if command not in _COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(_COMMANDS))}", file=sys.stderr)
        return 1

    # Per-command help: `devbench <cmd> --help` / `-h` prints the registry
    # description (single source of truth) and exits 0. The description
    # strings already embed the "<summary>: <syntax>" form, so we print
    # them plainly rather than wrapping them in another "Usage:" prefix.
    if any(arg in _HELP_FLAGS for arg in sys.argv[2:]):
        _, _, desc = _COMMANDS[command]
        print(desc)
        return 0

    func, min_args, _ = _COMMANDS[command]

    # Handle --watch/-w flag for the report command
    watch_interval = 0
    if command == "report":
        filtered_args: list[str] = []
        i = 0
        raw_args = sys.argv[2:]
        while i < len(raw_args):
            if raw_args[i] in ("--watch", "-w") and i + 1 < len(raw_args):
                watch_interval = int(raw_args[i + 1])
                i += 2
            else:
                filtered_args.append(raw_args[i])
                i += 1
        args = filtered_args
    else:
        args = sys.argv[2:]

    if len(args) < min_args:
        print(f"Command '{command}' requires at least {min_args} argument(s)", file=sys.stderr)
        return 1

    if command == "report" and watch_interval > 0:
        since_arg = args[0] if args else ""
        return cmd_report(since=since_arg, watch_interval=watch_interval)

    if len(args) > min_args + 1:
        print(f"Warning: ignoring {len(args) - min_args - 1} extra argument(s)", file=sys.stderr)
    return func(*args[: min_args + 1]) if len(args) > min_args else func(*args[:min_args])


if __name__ == "__main__":
    sys.exit(main())
