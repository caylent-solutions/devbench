"""Tests for judges.changes_manifest module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from testing import make_llm_fail_result, make_llm_pass_result

from devbench.config_loader import RepoConfig
from devbench.judges.base import Verdict
from devbench.judges.changes_manifest import ChangesManifestJudge


def _make_repo_config(local_path: Path) -> RepoConfig:
    return RepoConfig(name="org/repo", short_name="repo", local_path=local_path)


class TestChangesManifestJudgeInit:
    """Test ChangesManifestJudge basic properties."""

    def test_name(self) -> None:
        judge = ChangesManifestJudge()
        assert judge.name == "changes_manifest"


class TestEvaluate:
    """Test evaluate delegates to LLM with file change evidence."""

    def test_passes_when_llm_passes(
        self, tmp_work_unit_file: Path, tmp_repo_dir: Path, mock_llm_pass: None
    ) -> None:
        src = tmp_repo_dir / "src" / "main.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("# main\n")

        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Match"], cwd=tmp_repo_dir, capture_output=True, check=True)

        judge = ChangesManifestJudge()
        result = judge.evaluate(work_unit_path=tmp_work_unit_file, repo_config=_make_repo_config(tmp_repo_dir))
        assert result.verdict is Verdict.PASS

    def test_fails_when_llm_fails(self, tmp_work_unit_file: Path, tmp_repo_dir: Path) -> None:
        src = tmp_repo_dir / "src" / "main.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("# main\n")

        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Match"], cwd=tmp_repo_dir, capture_output=True, check=True)

        judge = ChangesManifestJudge()
        with patch.object(judge, "_llm_evaluate", return_value=make_llm_fail_result("changes_manifest")):
            result = judge.evaluate(work_unit_path=tmp_work_unit_file, repo_config=_make_repo_config(tmp_repo_dir))

        assert result.verdict is Verdict.FAIL

    def test_sends_changed_files_to_llm(self, tmp_work_unit_file: Path, tmp_repo_dir: Path) -> None:
        src = tmp_repo_dir / "src" / "main.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("# main\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feat"], cwd=tmp_repo_dir, capture_output=True, check=True)

        judge = ChangesManifestJudge()
        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("changes_manifest")) as mock_llm:
            judge.evaluate(work_unit_path=tmp_work_unit_file, repo_config=_make_repo_config(tmp_repo_dir))

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Work Unit" in evidence
        assert "Changed Files" in evidence
        assert "Git Diff Summary" in evidence


class TestGetChangedFiles:
    """Test _get_changed_files collects modified files."""

    def test_collects_committed_files(self, tmp_repo_dir: Path) -> None:
        subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=tmp_repo_dir, capture_output=True, check=True)
        (tmp_repo_dir / "new.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=tmp_repo_dir, capture_output=True, check=True)

        judge = ChangesManifestJudge()
        files = judge._get_changed_files(tmp_repo_dir)
        assert "new.py" in files
