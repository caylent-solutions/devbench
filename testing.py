"""Shared test helper functions for the judges test suite.

These are plain functions (not fixtures) used by test modules.
Importable as ``from judges.testing import make_llm_pass_result``.
"""

from __future__ import annotations

from judges.judges.base import JudgeResult, Verdict


def make_llm_pass_result(judge_name: str) -> JudgeResult:
    """Create a PASS JudgeResult as if returned by _llm_evaluate."""
    return JudgeResult(
        judge_name=judge_name,
        verdict=Verdict.PASS,
        reasoning="LLM review passed all checks.",
        feedback="",
        evidence=["LLM review complete"],
    )


def make_llm_fail_result(judge_name: str, feedback: str = "LLM found issues.") -> JudgeResult:
    """Create a FAIL JudgeResult as if returned by _llm_evaluate."""
    return JudgeResult(
        judge_name=judge_name,
        verdict=Verdict.FAIL,
        reasoning="LLM review found issues.",
        feedback=feedback,
        evidence=["LLM review found problems"],
    )
