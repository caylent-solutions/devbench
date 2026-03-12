"""Documentation review judge that validates documentation completeness.

Gathers the work-unit content, git diff, and documentation file contents,
then delegates the full review to the LLM which evaluates documentation
accuracy, completeness, and sync with code changes.
"""

from pathlib import Path

from devbench.config import LLM_FILE_CONTEXT_LIMIT, LLM_FILE_PREVIEW_CHARS
from devbench.judges.base import BaseJudge, JudgeResult
from devbench.prompts import load_prompt

_DOC_REVIEW_SYSTEM_PROMPT = load_prompt("doc_review")


class DocReviewJudge(BaseJudge):
    """Validates documentation completeness against work-unit acceptance criteria."""

    def __init__(self) -> None:
        super().__init__("doc_review")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Evaluate documentation by gathering evidence and delegating to the LLM."""
        repo: str = str(kwargs.get("repo", ""))
        work_unit_content = self._read_file(work_unit_path)
        diff = self._get_diff(repo_path, repo=repo)
        doc_contents = self._collect_doc_files(repo_path)

        evidence_sections: dict[str, str] = {
            "Work Unit": work_unit_content,
        }
        if diff:
            evidence_sections["Git Diff"] = diff
        if doc_contents:
            evidence_sections["Documentation Files"] = doc_contents

        return self._llm_evaluate(
            system_prompt=_DOC_REVIEW_SYSTEM_PROMPT,
            evidence_sections=evidence_sections,
            cwd=repo_path,
        )

    def _collect_doc_files(self, repo_path: Path) -> str:
        """Collect documentation file contents for LLM context."""
        doc_patterns = ["*.md", "*.rst", "*.txt", "*.adoc"]
        doc_files: list[Path] = []
        for pattern in doc_patterns:
            doc_files.extend(repo_path.glob(pattern))
            doc_files.extend((repo_path / "docs").glob(f"**/{pattern}") if (repo_path / "docs").is_dir() else [])

        parts: list[str] = []
        for doc_file in doc_files:
            content = doc_file.read_text(encoding="utf-8")
            rel_path = doc_file.relative_to(repo_path)
            parts.append(f"--- {rel_path} ---\n{content[:LLM_FILE_PREVIEW_CHARS]}")

        return "\n".join(parts[:LLM_FILE_CONTEXT_LIMIT])


