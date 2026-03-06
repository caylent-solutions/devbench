"""Code review judge that validates implementation against acceptance criteria and standards.

Gathers the git diff and work-unit content, then delegates the full review to
the LLM which evaluates code quality, SOLID/DRY compliance, AC satisfaction,
and prohibited-pattern detection.
"""

from pathlib import Path

from judges.judges.base import BaseJudge, JudgeResult, Verdict
from judges.prompts import load_prompt

_CODE_REVIEW_SYSTEM_PROMPT = load_prompt("code_review")


class CodeReviewJudge(BaseJudge):
    """Reviews implementation changes against acceptance criteria and coding standards."""

    def __init__(self) -> None:
        super().__init__("code_review")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Evaluate code changes by gathering evidence and delegating to the LLM."""
        work_unit_content = self._read_file(work_unit_path)
        diff = self._get_diff(repo_path)

        if not diff.strip():
            return JudgeResult(
                judge_name=self.name,
                verdict=Verdict.FAIL,
                reasoning="No code changes detected in the repository.",
                feedback="Stage code changes (git add) before running the code review judge.",
                evidence=["No diff found"],
            )

        return self._llm_evaluate(
            system_prompt=_CODE_REVIEW_SYSTEM_PROMPT,
            evidence_sections={
                "Work Unit": work_unit_content,
                "Git Diff": diff,
            },
            cwd=repo_path,
        )

    def _get_diff(self, repo_path: Path) -> str:
        """Return the combined diff of all changes: staged, unstaged, and committed."""
        parts: list[str] = []

        rc, stdout, _ = self._run_command(["git", "diff", "--cached"], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

        rc, stdout, _ = self._run_command(["git", "diff"], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

        default_branch = self._get_default_branch(repo_path)
        rc, stdout, _ = self._run_command(["git", "diff", default_branch], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

        return "\n".join(parts)
