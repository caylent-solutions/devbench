"""Changes manifest judge that verifies only expected files were modified.

Gathers the list of actually changed files and a diff summary, then delegates
the full review to the LLM which compares actual changes against the manifest
section in the work unit.
"""

from pathlib import Path

from devbench.judges.base import BaseJudge, JudgeResult
from devbench.prompts import load_prompt

_CHANGES_MANIFEST_SYSTEM_PROMPT = load_prompt("changes_manifest")


class ChangesManifestJudge(BaseJudge):
    """Verifies that actual file changes match the expected changes manifest."""

    def __init__(self) -> None:
        super().__init__("changes_manifest")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Evaluate change scope by gathering evidence and delegating to the LLM."""
        work_unit_content = self._read_file(work_unit_path)
        diff_summary = self._get_diff_summary(repo_path)
        changed_files = self._get_changed_files(repo_path)

        return self._llm_evaluate(
            system_prompt=_CHANGES_MANIFEST_SYSTEM_PROMPT,
            evidence_sections={
                "Work Unit": work_unit_content,
                "Git Diff Summary": diff_summary,
                "Changed Files": "\n".join(changed_files),
            },
            cwd=repo_path,
        )

    def _collect_files(self, repo_path: Path, cmd: list[str]) -> set[str]:
        """Run a git command and return the set of file paths from its output."""
        rc, stdout, _ = self._run_command(cmd, cwd=repo_path)
        if rc != 0 or not stdout.strip():
            return set()
        return {line.strip() for line in stdout.splitlines() if line.strip()}

    def _get_changed_files(self, repo_path: Path) -> list[str]:
        """Return list of ALL files changed: staged, unstaged, untracked, and committed on branch.

        Collects from all sources so evidence is complete whether the agent
        has staged, committed, or left changes in the working tree.
        """
        files: set[str] = set()

        # Staged changes (git add but not committed)
        files |= self._collect_files(repo_path, ["git", "diff", "--name-only", "--cached"])

        # Unstaged changes (modified but not staged)
        files |= self._collect_files(repo_path, ["git", "diff", "--name-only"])

        # Untracked new files
        files |= self._collect_files(repo_path, ["git", "ls-files", "--others", "--exclude-standard"])

        # All committed branch changes vs default branch
        default_branch = self._get_default_branch(repo_path)
        files |= self._collect_files(repo_path, ["git", "diff", "--name-only", default_branch])

        return sorted(files)

    def _get_diff_summary(self, repo_path: Path) -> str:
        """Return a summary of all changes for LLM context.

        Includes staged, unstaged, untracked, and committed branch changes.
        """
        parts: list[str] = []

        # Staged changes
        rc, stat_out, _ = self._run_command(
            ["git", "diff", "--stat", "--cached"], cwd=repo_path,
        )
        if rc == 0 and stat_out.strip():
            parts.append(f"Staged changes:\n{stat_out}")

        # Unstaged changes
        rc, stat_out, _ = self._run_command(
            ["git", "diff", "--stat"], cwd=repo_path,
        )
        if rc == 0 and stat_out.strip():
            parts.append(f"Unstaged changes:\n{stat_out}")

        # Untracked files
        rc, untracked, _ = self._run_command(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_path,
        )
        if rc == 0 and untracked.strip():
            parts.append(f"Untracked files:\n{untracked}")

        # All committed branch changes vs default branch
        default_branch = self._get_default_branch(repo_path)
        rc, stat_out, _ = self._run_command(
            ["git", "diff", "--stat", default_branch], cwd=repo_path,
        )
        if rc == 0 and stat_out.strip():
            parts.append(f"Committed changes vs {default_branch}:\n{stat_out}")

        all_files = self._get_changed_files(repo_path)
        parts.append("All changed files:\n" + "\n".join(all_files))

        return "\n\n".join(parts)
