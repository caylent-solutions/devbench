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
    set-status <id> <s>     Force any status (no gate — use for recovery/lifecycle transitions)
    mark-done <id>          Mark unit as Done (enforces done-gate: all judges must have passed)
    validate-backlog        Check backlog integrity (file existence, status sync, orphans, deps)
    ensure-branch <id>      Create or switch to work unit branch before executor runs
    git-ops <id>            Run full git operations sequence for a completed work unit
    report [since]          Print progress report with velocity stats
    log <message>           Append a message to the orchestrator log file
    start                   Run the orchestrate skill via the Claude Agent SDK (non-interactive)

Plugin agent bridge commands (used by devbench plugin agents)::

    read-unit <id>                          Return work unit content and repo path as JSON
    get-diff <id>                           Return combined git diff for the work unit's repo
    run-tests <id>                          Run test suite for the work unit's repo
    log-verdict <judge> <id> <v> [msg]      Log a judge verdict (pass|fail) to work unit Comments

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
    COMMENT_ENTRY_TEMPLATE,
    COMMENTS_SECTION_HEADER,
    DISPLAY_STATUS_VALUES,
    STATUS_IN_PROGRESS,
    STATUS_SEPARATOR_WIDTH,
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


def cmd_read_unit(unit_id: str) -> int:
    """Return work unit content and resolved repo path as JSON.

    Output::

        {
          "unit_id": "E0-F1-S1-T1",
          "work_unit_path": "/abs/path/to/unit.md",
          "repo_path": "/abs/path/to/repo",
          "repo": "org/repo",
          "content": "<full work unit markdown>"
        }

    Used by plugin agents to get repo context without knowing devbench.yaml.
    """
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
    print(json.dumps({
        "unit_id": unit.id,
        "work_unit_path": str(wu_file),
        "repo_path": str(repo_path),
        "repo": canonical_repo,
        "content": content,
    }))
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
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=repo_path,
        )
        if rc != 0 or not stdout.strip():
            print(
                f"ERROR: Cannot determine default branch for '{canonical_repo}'. "
                "Run 'git remote set-head origin --auto' to configure it.",
                file=sys.stderr,
            )
            return 1
        default_branch = stdout.strip().removeprefix("origin/")

    rc, stdout, _ = run_command(["git", "diff", default_branch], cwd=repo_path)
    if rc == 0 and stdout.strip():
        parts.append(stdout)

    rc, stdout, _ = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_path,
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
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    entry = COMMENT_ENTRY_TEMPLATE.format(
        timestamp=timestamp, agent_id=agent_id, action=action, message=feedback,
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

    branch = f"backlog/{unit_id.lower()}"
    ops = GitOpsJudge()
    ops.ensure_branch(canonical_repo, repo_path, branch)
    logger.info("Branch ready: %s on %s", branch, canonical_repo)
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

    branch = f"backlog/{unit_id.lower()}"
    commit_message = f"{unit_id}: {unit.title}"
    pr_title = f"{unit_id}: {unit.title}"
    pr_body = f"Automated PR for work unit {unit_id}.\n\n{unit.title}"

    ops = GitOpsJudge()

    ops.commit_and_push(canonical_repo, repo_path, branch, commit_message)
    logger.info("Committed and pushed %s", unit_id)

    pr_url = ops.create_pr(canonical_repo, branch, pr_title, pr_body, repo_path=repo_path)
    logger.info("Created PR: %s", pr_url)

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

    ops.merge_pr(canonical_repo, pr_number, repo_path=repo_path)
    logger.info("Merged PR #%d for %s", pr_number, unit_id)

    if UPDATE_SUBMODULE:
        ops.update_parent_submodule_ref(
            canonical_repo,
            repo_path,
            f"chore: update {repo_path.name} submodule after {unit_id}",
        )

    print(json.dumps({"unit_id": unit_id, "pr_url": pr_url, "pr_number": pr_number}))
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

    plugin_path = Path(__file__).parent.parent.parent / "plugin" / "devbench"

    async def _run() -> None:
        async for message in query(
            prompt="Run the devbench:orchestrate skill to process the backlog until complete",
            options=ClaudeAgentOptions(
                setting_sources=["project"],
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


# Command registry: name -> (handler, min_args, description)
_COMMANDS: dict[str, tuple[Callable[..., int], int, str]] = {
    "status": (cmd_status, 0, "Show backlog summary"),
    "next": (cmd_next, 0, "Print next actionable work unit"),
    "set-status": (cmd_set_status, 2, "Set status: set-status <id> <status>"),
    "mark-done": (cmd_mark_done, 1, "Mark done: mark-done <id>"),
    "validate-backlog": (cmd_validate_backlog, 0, "Validate backlog integrity"),
    "ensure-branch": (cmd_ensure_branch, 1, "Create or switch to work unit branch: ensure-branch <id>"),
    "git-ops": (cmd_git_ops, 1, "Run git operations for a work unit: git-ops <id>"),
    "log": (cmd_log, 1, "Log a message: log <message>"),
    "report": (cmd_report, 0, "Progress report: report [since-timestamp]"),
    "start": (cmd_start, 0, "Run orchestrate skill via Agent SDK (non-interactive)"),
    # Plugin agent bridge commands — used by devbench plugin agents
    "read-unit": (cmd_read_unit, 1, "Return work unit content and repo path as JSON: read-unit <id>"),
    "get-diff": (cmd_get_diff, 1, "Return combined git diff for work unit's repo: get-diff <id>"),
    "run-tests": (cmd_run_tests, 1, "Run test suite for work unit's repo: run-tests <id>"),
    "log-verdict": (cmd_log_verdict, 3, "Log judge verdict: log-verdict <judge> <id> <pass|fail> [feedback]"),
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
