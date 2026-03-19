"""Blocker resolver judge that assesses and resolves runtime blockers.

Passes the work-unit content (which includes the Blocked By section) to the
LLM, which evaluates blocker severity and suggests resolution strategies.
"""

from pathlib import Path

from devbench.config_loader import RepoConfig
from devbench.judges.base import BaseJudge, JudgeResult, Verdict
from devbench.prompts import load_prompt

_BLOCKER_RESOLVER_SYSTEM_PROMPT = load_prompt("blocker_resolver")


class BlockerResolverJudge(BaseJudge):
    """Assesses runtime blockers and checks whether they have been resolved."""

    def __init__(self) -> None:
        super().__init__("blocker_resolver")

    def evaluate(self, work_unit_path: Path, repo_config: RepoConfig, **kwargs: object) -> JudgeResult:
        """Evaluate blockers by delegating to the LLM."""
        work_unit_content = self._read_work_unit(work_unit_path)

        # Quick check: if no "Blocked By" section exists, pass immediately
        if "blocked by" not in work_unit_content.lower():
            return JudgeResult(
                judge_name=self.name,
                verdict=Verdict.PASS,
                reasoning="No blockers listed in the work unit.",
                feedback="",
                evidence=["No 'Blocked By' section found"],
            )

        return self._llm_evaluate(
            system_prompt=_BLOCKER_RESOLVER_SYSTEM_PROMPT,
            evidence_sections={
                "Work Unit": work_unit_content,
            },
            cwd=repo_config.local_path,
        )
