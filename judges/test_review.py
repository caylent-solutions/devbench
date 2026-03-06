"""Test review judge that validates TDD cycle adherence and test quality.

Gathers test execution output, test file contents, and work-unit content,
then delegates the full review to the LLM which evaluates TDD discipline,
test quality, meaningful assertions, and stub detection.
"""

from pathlib import Path

from judges.config import (
    LLM_FILE_CONTEXT_LIMIT,
    LLM_FILE_PREVIEW_CHARS,
    TEST_TIMEOUT,
)
from judges.constants import TEST_OUTPUT_TAIL_CHARS
from judges.judges.base import BaseJudge, JudgeResult
from judges.prompts import load_prompt

_TEST_REVIEW_SYSTEM_PROMPT = load_prompt("test_review")


class TestReviewJudge(BaseJudge):
    """Validates TDD cycle compliance, test execution, and test quality."""

    def __init__(self) -> None:
        super().__init__("test_review")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Evaluate test quality by gathering evidence and delegating to the LLM."""
        work_unit_content = self._read_file(work_unit_path)

        # Gather test execution output
        test_output = self._run_tests(repo_path)

        # Gather test file contents
        test_files_content = self._collect_test_files(repo_path)

        evidence_sections: dict[str, str] = {
            "Work Unit": work_unit_content,
        }
        if test_output:
            evidence_sections["Test Output"] = test_output
        if test_files_content:
            evidence_sections["Test Files"] = test_files_content

        return self._llm_evaluate(
            system_prompt=_TEST_REVIEW_SYSTEM_PROMPT,
            evidence_sections=evidence_sections,
            cwd=repo_path,
        )

    def _run_tests(self, repo_path: Path) -> str:
        """Run the repo's test pipeline and return the output.

        Uses ``make test`` when a Makefile with a ``test`` target exists,
        so that the repo's configured environment variables, flags, and
        exclusions are respected.  Falls back to bare ``pytest`` otherwise.
        """
        if self._has_make_test_target(repo_path):
            cmd = ["make", "test"]
        else:
            changed = self._get_changed_test_files(repo_path)
            targets = [f for f in changed if (repo_path / f).exists()]
            cmd = ["pytest", "--no-header", "-q", "-p", "no:cacheprovider"]
            if targets:
                cmd.extend(targets)

        rc, stdout, stderr = self._run_command(cmd, cwd=repo_path, timeout=TEST_TIMEOUT)
        combined_output = (stdout + stderr).strip()
        return combined_output[-TEST_OUTPUT_TAIL_CHARS:] if combined_output else ""

    def _has_make_test_target(self, repo_path: Path) -> bool:
        """Check if the repo has a Makefile with a ``test`` target."""
        makefile = repo_path / "Makefile"
        if not makefile.exists():
            return False
        rc, stdout, _ = self._run_command(
            ["make", "-n", "test"], cwd=repo_path,
        )
        return rc == 0

    def _collect_test_files(self, repo_path: Path) -> str:
        """Collect test file contents for LLM context.

        Changed test files are prioritised so they appear first in the
        LLM context window.
        """
        changed_files = self._get_changed_test_files(repo_path)

        test_dirs = [repo_path / "tests", repo_path / "test"]
        all_test_files: list[Path] = []
        for test_dir in test_dirs:
            if test_dir.is_dir():
                all_test_files.extend(test_dir.rglob("test_*.py"))
                all_test_files.extend(test_dir.rglob("*_test.py"))
        all_test_files.extend(repo_path.glob("test_*.py"))

        changed_parts: list[str] = []
        other_parts: list[str] = []
        for test_file in all_test_files:
            file_content = test_file.read_text(encoding="utf-8")
            rel_path = test_file.relative_to(repo_path)
            preview = file_content[:LLM_FILE_PREVIEW_CHARS]
            if len(file_content) > LLM_FILE_PREVIEW_CHARS:
                preview += (
                    f"\n\n[... TRUNCATED — showing {LLM_FILE_PREVIEW_CHARS} of "
                    f"{len(file_content)} chars. File is complete on disk.]"
                )
            part = f"--- {rel_path} ---\n{preview}"

            if rel_path.as_posix() in changed_files:
                changed_parts.append(part)
            else:
                other_parts.append(part)

        content_parts = changed_parts + other_parts
        return "\n".join(content_parts[:LLM_FILE_CONTEXT_LIMIT])

    def _get_changed_test_files(self, repo_path: Path) -> set[str]:
        """Return the set of test file paths changed in the current work."""
        files: set[str] = set()
        for cmd in (
            ["git", "diff", "--name-only", "--cached"],
            ["git", "diff", "--name-only"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            rc, stdout, _ = self._run_command(cmd, cwd=repo_path)
            if rc == 0 and stdout.strip():
                for line in stdout.splitlines():
                    stripped = line.strip()
                    if stripped and ("test_" in stripped or "_test.py" in stripped):
                        files.add(stripped)

        default_branch = self._get_default_branch(repo_path)
        rc, stdout, _ = self._run_command(["git", "diff", "--name-only", default_branch], cwd=repo_path)
        if rc == 0 and stdout.strip():
            for line in stdout.splitlines():
                stripped = line.strip()
                if stripped and ("test_" in stripped or "_test.py" in stripped):
                    files.add(stripped)
        return files
