"""Orchestrator — main loop for autonomous backlog execution.

Parses the backlog, dispatches work to Claude Code agents,
runs judge reviews at every gate, and manages the lifecycle.
"""

import logging
from pathlib import Path

from devbench.backlog.manager import BacklogManagerJudge
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    MAX_RETRY_ATTEMPTS,
    ORCHESTRATOR_POLL_INTERVAL,
    OUTPUT_TRUNCATION_LIMIT,
    REPO_LOCAL_PATHS,
    validate_repo,
)
from devbench.constants import BRANCH_NAME_TEMPLATE, PR_BODY_TEMPLATE, STATUS_IN_PROGRESS
from devbench.execution import executor as claude_executor
from devbench.execution.executor import ExecutionStatus
from devbench.github.git_ops import GitOpsJudge
from devbench.github.security import setup_all_repos
from devbench.judges.base import JudgeResult, Verdict
from devbench.judges.blocker_resolver import BlockerResolverJudge
from devbench.judges.changes_manifest import ChangesManifestJudge
from devbench.judges.code_review import CodeReviewJudge
from devbench.judges.doc_review import DocReviewJudge
from devbench.judges.security_review import SecurityReviewJudge
from devbench.judges.test_review import TestReviewJudge
from devbench.log_setup import setup_logging

setup_logging()
logger = logging.getLogger("orchestrator")


def _format_judge_feedback(verdicts: list[tuple[str, JudgeResult]]) -> str:
    """Format feedback from all failed judges into a single string."""
    feedback_parts = []
    for judge_name, result in verdicts:
        if result.verdict == Verdict.FAIL:
            feedback_parts.append(f"## {judge_name} FAILED\nReasoning: {result.reasoning}\nFix: {result.feedback}\n")
    return "\n".join(feedback_parts)


def run_review_judges(
    work_unit: WorkUnit,
    repo_path: Path,
) -> list[tuple[str, JudgeResult]]:
    """Run all review judges on a work unit.

    Returns list of (judge_name, JudgeResult) tuples.
    """
    judges = [
        CodeReviewJudge(),
        TestReviewJudge(),
        DocReviewJudge(),
        ChangesManifestJudge(),
    ]

    results: list[tuple[str, JudgeResult]] = []
    for judge in judges:
        logger.info("Running %s judge on %s", judge.name, work_unit.id)
        result = judge.evaluate(
            work_unit_path=work_unit.file_path,
            repo_path=repo_path,
        )
        results.append((judge.name, result))
        logger.info(
            "%s judge verdict for %s: %s",
            judge.name,
            work_unit.id,
            result.verdict.value,
        )
        if result.verdict == Verdict.FAIL:
            work_unit.log_comment(
                agent_id=f"judge/{judge.name}",
                action="REVIEW_FAIL",
                message=f"{result.reasoning} | Fix: {result.feedback}",
            )
        else:
            work_unit.log_comment(
                agent_id=f"judge/{judge.name}",
                action="REVIEW_PASS",
                message=result.reasoning,
            )

    return results


def process_work_unit(work_unit: WorkUnit) -> bool:
    """Process a single work unit through the full lifecycle.

    Returns True if the work unit was completed successfully.
    """
    validate_repo(work_unit.repo)
    repo_path = REPO_LOCAL_PATHS.get(work_unit.repo)
    if repo_path is None:
        raise ValueError(f"No local path for repo: {work_unit.repo}")

    git_ops = GitOpsJudge()
    security_judge = SecurityReviewJudge()
    backlog_mgr = BacklogManagerJudge()
    blocker_judge = BlockerResolverJudge()

    backlog_mgr.force_status(work_unit.file_path, BACKLOG_INDEX, work_unit.id, STATUS_IN_PROGRESS)
    work_unit.log_comment("orchestrator", "START", f"Beginning work on {work_unit.id}")

    feedback = ""

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        logger.info(
            "Attempt %d/%d for %s",
            attempt,
            MAX_RETRY_ATTEMPTS,
            work_unit.id,
        )

        # Execute the work unit
        result = claude_executor.execute(
            work_unit_path=work_unit.file_path,
            repo=work_unit.repo,
            feedback=feedback,
        )

        if result.status == ExecutionStatus.BLOCKED:
            work_unit.log_comment(
                "orchestrator",
                "BLOCKED",
                f"Agent reported blocker: {result.blocker}",
            )
            blocker_result = blocker_judge.evaluate(
                work_unit_path=work_unit.file_path,
                repo_path=repo_path,
            )
            if blocker_result.verdict == Verdict.FAIL:
                logger.warning(
                    "Blocker unresolvable for %s: %s",
                    work_unit.id,
                    blocker_result.reasoning,
                )
                feedback = f"Blocker could not be resolved: {blocker_result.reasoning}"
                continue
            work_unit.log_comment("orchestrator", "BLOCKER_RESOLVED", blocker_result.reasoning)
            feedback = f"Blocker resolved: {blocker_result.reasoning}"
            continue

        if result.status == ExecutionStatus.FAILED:
            work_unit.log_comment("orchestrator", "AGENT_FAILED", f"Attempt {attempt} failed")
            feedback = f"Previous attempt failed. Output: {result.output[:OUTPUT_TRUNCATION_LIMIT]}"
            continue

        # Status is IN_REVIEW — run judges
        verdicts = run_review_judges(work_unit, repo_path)
        all_passed = all(v.verdict == Verdict.PASS for _, v in verdicts)

        if not all_passed:
            feedback = _format_judge_feedback(verdicts)
            work_unit.log_comment(
                "orchestrator",
                "REVIEW_REJECTED",
                f"Attempt {attempt}: judges rejected, retrying",
            )
            continue

        # All judges passed — security check
        logger.info("All review judges passed for %s, checking security", work_unit.id)
        security_result = security_judge.evaluate(
            work_unit_path=work_unit.file_path,
            repo_path=repo_path,
        )
        if security_result.verdict == Verdict.FAIL:
            work_unit.log_comment(
                "judge/security_review",
                "SECURITY_FAIL",
                security_result.feedback,
            )
            # Reset the done-gate window so mark_done requires a fresh judge re-run
            # after the dev fixes the security issue and the code changes.
            work_unit.log_comment(
                "orchestrator",
                "REVIEW_REJECTED",
                f"Security review failed on attempt {attempt} — judge re-review required",
            )
            feedback = f"Security review failed: {security_result.feedback}"
            continue

        # Commit, push, create PR, wait for checks, merge
        branch = BRANCH_NAME_TEMPLATE.format(unit_id=work_unit.id.lower())
        try:
            git_ops.commit_and_push(
                repo=work_unit.repo,
                repo_path=repo_path,
                branch=branch,
                message=f"{work_unit.id}: {work_unit.title}",
            )
            pr_url = git_ops.create_pr(
                repo=work_unit.repo,
                branch=branch,
                title=f"{work_unit.id}: {work_unit.title}",
                body=PR_BODY_TEMPLATE.format(
                    unit_id=work_unit.id,
                    description=work_unit.description[:OUTPUT_TRUNCATION_LIMIT],
                ),
                repo_path=repo_path,
            )
            work_unit.log_comment("judge/git_ops", "PR_CREATED", pr_url)

            # Extract PR number from URL
            pr_number = int(pr_url.rstrip("/").split("/")[-1])

            checks_passed = git_ops.wait_for_checks(
                repo=work_unit.repo,
                pr_number=pr_number,
                repo_path=repo_path,
            )
            if not checks_passed:
                work_unit.log_comment("judge/git_ops", "CHECKS_FAILED", "GitHub checks failed")
                feedback = "GitHub CI checks failed on PR. Fix the issues."
                continue

            git_ops.merge_pr(repo=work_unit.repo, pr_number=pr_number, repo_path=repo_path)
            work_unit.log_comment("judge/git_ops", "PR_MERGED", pr_url)

            # Update parent repo's submodule reference
            git_ops.update_parent_submodule_ref(
                repo=work_unit.repo,
                repo_path=repo_path,
                message=f"{work_unit.id}: update {repo_path.name} submodule ref",
            )
            work_unit.log_comment(
                "judge/git_ops", "SUBMODULE_UPDATED", repo_path.name,
            )

        except Exception as exc:
            work_unit.log_comment("orchestrator", "GIT_ERROR", str(exc))
            feedback = f"Git operations failed: {exc}"
            continue

        # Mark done — gated path verifies all judges passed
        backlog_mgr.mark_done(work_unit.file_path, BACKLOG_INDEX, work_unit.id)
        work_unit.log_comment("orchestrator", "DONE", f"Work unit {work_unit.id} completed")
        logger.info("COMPLETED: %s", work_unit.id)
        return True

    # Exhausted retries — single code path updates both files
    backlog_mgr.mark_blocked(
        work_unit.file_path, BACKLOG_INDEX, work_unit.id,
        f"Failed after {MAX_RETRY_ATTEMPTS} attempts",
    )
    logger.warning("BLOCKED: %s after %d attempts", work_unit.id, MAX_RETRY_ATTEMPTS)
    return False


def _wait_for_status_change(
    parser: BacklogParser,
    old_units: list[WorkUnit],
    timeout: int,
) -> None:
    """Poll the backlog index until a status change is detected or timeout expires.

    Uses filesystem mtime polling to detect when work-unit files have been updated,
    avoiding fixed sleep delays. Falls back to timeout if no changes are detected.

    Args:
        parser: BacklogParser to re-read the index.
        old_units: Previous list of work units with their statuses.
        timeout: Maximum seconds to wait before returning.

    Raises:
        TimeoutError: If no status change is detected within *timeout* seconds.
    """
    import time as _time

    old_statuses = {u.id: u.status for u in old_units}
    deadline = _time.monotonic() + timeout
    poll_step = min(1, timeout)  # Check every 1 second, or less if timeout < 1

    while _time.monotonic() < deadline:
        new_units = parser.parse_index()
        new_statuses = {u.id: u.status for u in new_units}
        if new_statuses != old_statuses:
            return
        _time.sleep(poll_step)

    raise TimeoutError(
        f"No backlog status change detected within {timeout}s. "
        f"Check that in-progress agents are updating work-unit files."
    )


def main() -> None:
    """Main orchestrator loop."""
    logger.info("Starting autonomous backlog execution")

    # Pre-flight: validate backlog integrity before doing any work
    backlog_mgr_preflight = BacklogManagerJudge()
    preflight_errors = backlog_mgr_preflight.validate(BACKLOG_INDEX, BACKLOG_INDEX.parent)
    if preflight_errors:
        logger.error("Backlog integrity check failed — aborting:")
        for err in preflight_errors:
            logger.error("  %s", err)
        return

    # Phase 1: Setup GitHub security
    logger.info("Setting up GitHub security features on all repos")
    security_results = setup_all_repos()
    for repo, features in security_results.items():
        logger.info("Security setup for %s: %s", repo, features)

    # Phase 2: Process work units
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    completed = 0
    blocked = 0

    while True:
        units = parser.parse_index()
        actionable = parser.find_next_actionable(units)

        if actionable is None:
            if parser.all_done(units):
                logger.info("ALL WORK UNITS COMPLETE (%d total)", completed)
                break

            blocked_units = parser.get_blocked_units(units)
            if blocked_units:
                logger.warning(
                    "No actionable units. %d blocked: %s",
                    len(blocked_units),
                    [u.id for u in blocked_units],
                )
            else:
                in_progress = [u for u in units if u.status == WorkUnitStatus.IN_PROGRESS]
                if in_progress:
                    logger.info("Waiting for in-progress units: %s", [u.id for u in in_progress])
                    # Re-parse backlog on next iteration to detect file-based status changes.
                    # The poll interval is configurable via JUDGE_ORCHESTRATOR_POLL_INTERVAL.
                    try:
                        _wait_for_status_change(
                            parser,
                            units,
                            timeout=ORCHESTRATOR_POLL_INTERVAL,
                        )
                    except TimeoutError:
                        logger.warning(
                            "No status change after %ds poll; re-checking backlog",
                            ORCHESTRATOR_POLL_INTERVAL,
                        )
                    continue

            logger.error("DEADLOCKED — no actionable, no in-progress units")
            break

        success = process_work_unit(actionable)
        if success:
            completed += 1
        else:
            blocked += 1

    logger.info("Orchestrator finished. Completed: %d, Blocked: %d", completed, blocked)


if __name__ == "__main__":
    main()
