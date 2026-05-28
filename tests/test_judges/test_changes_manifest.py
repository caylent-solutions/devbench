"""Contract pins for the changes-manifest judge commit-attribution check.

The changes-manifest judge must validate two distinct assertions:
1. Every STAGED file appears in the task's Changes Manifest (pre-existing check).
2. Every file in the task's COMMITS (git log origin/main..HEAD) appears in the
   task's Changes Manifest -- the commit-attribution check.

Assertion 2 catches the scenario where the executor accidentally amended a
sibling task's commit and bundled the sibling's file under this task's commit.
Previously that surfaced only as a deadlocked manifest-mismatch on the
bundled-into task.

These tests pin the contract by:
- Verifying the judge prompt instructs the agent to run `git log --name-only`
  (or equivalent) to enumerate committed files.
- Verifying the required FAIL message template is present in the prompt.
- Building a fake git-history scenario and asserting the judge's prescribed
  diagnostic logic would surface the violation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

CHANGES_MANIFEST_JUDGE = (
    Path(__file__).parent.parent.parent
    / "plugin"
    / "devbench-orchestrate"
    / "agents"
    / "review_team"
    / "changes-manifest.md"
)


@pytest.mark.unit
class TestChangesManifestJudgeCommitAttributionContract:
    """Pins that the changes-manifest judge enforces commit-attribution (assertion 2)."""

    def test_judge_prompt_exists(self) -> None:
        assert CHANGES_MANIFEST_JUDGE.is_file(), (
            f"changes-manifest.md judge prompt not found at {CHANGES_MANIFEST_JUDGE}"
        )

    def test_judge_instructs_git_log_name_only(self) -> None:
        """The judge must instruct the agent to enumerate committed files via git log."""
        content = CHANGES_MANIFEST_JUDGE.read_text(encoding="utf-8")
        assert "git log" in content, (
            "changes-manifest.md must instruct the agent to run `git log` to enumerate "
            "committed files as the commit-attribution check (assertion 2)."
        )
        assert "--name-only" in content, (
            "changes-manifest.md must use `--name-only` in the git log command so the "
            "agent gets a per-file listing of committed paths."
        )

    def test_judge_mentions_origin_main_boundary(self) -> None:
        """The commit-attribution check must scope to commits since origin/main."""
        content = CHANGES_MANIFEST_JUDGE.read_text(encoding="utf-8")
        assert "origin/main..HEAD" in content, (
            "changes-manifest.md must reference `origin/main..HEAD` to scope the git "
            "log to this task's commits only (not the entire branch history)."
        )

    def test_judge_has_fail_message_for_commit_attribution_violation(self) -> None:
        """The judge must include the required FAIL message template for bundled files."""
        content = CHANGES_MANIFEST_JUDGE.read_text(encoding="utf-8")
        # The required message must reference both the commit sha and file path.
        assert "Changes Manifest" in content
        # Check for the amend-scenario explanation.
        assert "amend" in content.lower() or "bundled" in content.lower(), (
            "changes-manifest.md must explain the `git commit --amend` sibling-bundling "
            "scenario so the agent emits a diagnostic message that names the root cause."
        )

    def test_judge_commit_attribution_check_is_second_assertion(self) -> None:
        """The commit-attribution check must be framed as a SECOND assertion on top
        of the existing staged-file check, not a replacement."""
        content = CHANGES_MANIFEST_JUDGE.read_text(encoding="utf-8")
        # Both the staged-file scope contract (get-diff) and the commit log command
        # must be present -- the judge performs both checks.
        assert "get-diff" in content, (
            "The staged-file check (devbench get-diff) must still be present -- the "
            "commit-attribution check is additive, not a replacement."
        )
        assert "git log" in content, "The commit-attribution check (git log) must also be present."

    def test_commit_attribution_logic_detects_bundled_file(self, tmp_path: Path) -> None:
        """Structural contract: given a fake git repo where commit A touches two files
        but only one is declared in the manifest, the judge's prescribed diagnostic
        pattern (git log --name-only origin/main..HEAD) would surface the violation.

        This test runs the actual git commands the judge instructs the agent to run
        and asserts the output would expose the undeclared file, confirming the
        judge's diagnostic prescription is sound.
        """
        # Build a fake git repo simulating the bundled-commit scenario.
        repo = tmp_path / "repo"
        repo.mkdir()

        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()

        git("init")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        git("checkout", "-b", "main")

        # Initial commit on main (the origin/main baseline).
        (repo / "README.md").write_text("# repo\n", encoding="utf-8")
        git("add", "README.md")
        git("commit", "-m", "initial")

        # Create the task branch.
        git("checkout", "-b", "task-branch")

        # The executor committed two files but the manifest declares only one.
        declared_file = "src/feature.py"
        bundled_file = "src/sibling.py"  # from a sibling task, accidentally bundled

        (repo / "src").mkdir()
        (repo / declared_file).write_text("# feature\n", encoding="utf-8")
        (repo / bundled_file).write_text("# sibling\n", encoding="utf-8")
        git("add", declared_file, bundled_file)
        git("commit", "-m", "implement feature (accidentally bundled sibling)")

        # The judge's prescribed command: git log --name-only origin/main..HEAD
        # We simulate origin/main by pointing at main (the initial commit branch).
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--name-only",
                "--pretty=format:",
                "main..HEAD",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        committed_files = {line.strip() for line in result.stdout.splitlines() if line.strip()}

        # The manifest only declares the primary file.
        manifest_files = {declared_file}

        # Files in commits but not in manifest -- the judge must FAIL on these.
        undeclared = committed_files - manifest_files
        assert bundled_file in undeclared, (
            f"The git log command prescribed by the judge must surface '{bundled_file}' "
            f"as a file committed but not in the manifest. "
            f"Committed={committed_files!r}, Manifest={manifest_files!r}, "
            f"Undeclared={undeclared!r}"
        )
        # Confirm the declared file does NOT appear in undeclared.
        assert declared_file not in undeclared, (
            f"'{declared_file}' is in the manifest and must not appear in undeclared set."
        )
