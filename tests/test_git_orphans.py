"""Tests for ``devbench.git_orphans`` -- pattern matching, detection, cleanup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbench.git_orphans import (
    DEVBENCH_GITIGNORE_HEADER,
    OrphanReport,
    cleanup_tracked_orphans,
    configured_patterns,
    detect_staged_orphans,
    detect_tracked_orphans,
    path_matches_orphan,
)


class TestPathMatchesOrphan:
    """Pattern-matcher correctness across globstar and non-orphan paths."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("foo.tfstate", True),
            ("infra/sandbox/foo.tfstate", True),
            ("a/b/c/d.tfstate.backup", True),
            ("infra/.terragrunt-cache/abc/main.tf", True),
            ("svc/sub/.terragrunt-cache/x/y.tf", True),
            (".terraform.lock.hcl", True),
            ("infra/.terraform.lock.hcl", True),
            ("infra/.terraform/providers/aws/x", True),
            ("__pycache__/x.cpython-312.pyc", True),
            ("tests/unit/__pycache__/test_foo.cpython-312-pytest-9.0.3.pyc", True),
            ("foo.pyc", True),
            ("a/b/foo.pyo", True),
            (".coverage", True),
            (".coverage.42", True),
            (".coverage (1)", True),
            ("htmlcov/index.html", True),
            ("subdir/htmlcov/index.html", True),
            ("node_modules/pkg/index.js", True),
            ("packages/svc/node_modules/pkg/index.js", True),
            (".DS_Store", True),
            ("a/b/.DS_Store", True),
            ("src/main.py", False),
            ("infra/properties/common.yaml", False),
            ("infra/scripts/merge_properties.py", False),
            ("tests/unit/test_foo.py", False),
            ("README.md", False),
            ("coverage_report.md", False),
            ("docs/tfstate.md", False),
        ],
    )
    def test_match_or_skip(self, path: str, expected: bool) -> None:
        assert path_matches_orphan(path) is expected


class TestConfiguredPatterns:
    """Env-var override semantics for the active pattern list."""

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_ORPHAN_IGNORE_PATTERNS", raising=False)
        patterns = configured_patterns()
        assert any("tfstate" in p for p in patterns)
        assert any("__pycache__" in p for p in patterns)
        assert any("DS_Store" in p for p in patterns)

    def test_override_replaces_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_ORPHAN_IGNORE_PATTERNS", "**/*.foo,**/bar/**")
        assert configured_patterns() == ("**/*.foo", "**/bar/**")

    def test_override_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_ORPHAN_IGNORE_PATTERNS", "  **/*.foo  ,  **/bar  ")
        assert configured_patterns() == ("**/*.foo", "**/bar")

    def test_override_empty_falls_back_to_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_ORPHAN_IGNORE_PATTERNS", "   ")
        assert "**/*.tfstate" in configured_patterns()


@pytest.fixture
def polluted_repo(tmp_path: Path) -> Path:
    """Build a real git repo with both clean files and orphan-pattern files staged + committed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)

    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n")
    (repo / "infra").mkdir()
    (repo / "infra" / "common.yaml").write_text("region: us-east-1\n")

    (repo / "infra" / "sandbox.tfstate").write_text("{}\n")
    cache_dir = repo / "infra" / ".terragrunt-cache" / "abc"
    cache_dir.mkdir(parents=True)
    (cache_dir / "module.tf").write_text("# module\n")
    pycache = repo / "tests" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "test_foo.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    (repo / ".coverage").write_bytes(b"\x00")

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "initial"],
        check=True,
    )
    return repo


class TestDetectTrackedOrphans:
    """``detect_tracked_orphans`` walks ``git ls-files`` and filters."""

    def test_finds_all_orphans_in_polluted_repo(self, polluted_repo: Path) -> None:
        detected = detect_tracked_orphans(polluted_repo)
        assert "infra/sandbox.tfstate" in detected
        assert "infra/.terragrunt-cache/abc/module.tf" in detected
        assert "tests/__pycache__/test_foo.cpython-312.pyc" in detected
        assert ".coverage" in detected
        assert "src/main.py" not in detected
        assert "infra/common.yaml" not in detected

    def test_clean_repo_returns_empty(self, tmp_path: Path) -> None:
        repo = tmp_path / "clean"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@y.z"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
        (repo / "README.md").write_text("hi\n")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
        assert detect_tracked_orphans(repo) == []

    def test_custom_pattern_override_via_argument(self, polluted_repo: Path) -> None:
        detected = detect_tracked_orphans(polluted_repo, patterns=("**/*.tfstate",))
        assert detected == ["infra/sandbox.tfstate"]


class TestDetectStagedOrphans:
    """``detect_staged_orphans`` filters ``git diff --cached``."""

    def test_unstaged_orphans_not_reported(self, polluted_repo: Path) -> None:
        assert detect_staged_orphans(polluted_repo) == []

    def test_newly_staged_orphan_reported(self, polluted_repo: Path) -> None:
        new_orphan = polluted_repo / "newcache" / "x.pyc"
        new_orphan.parent.mkdir()
        new_orphan.write_bytes(b"\x00")
        subprocess.run(
            ["git", "-C", str(polluted_repo), "add", str(new_orphan)],
            check=True,
        )
        staged = detect_staged_orphans(polluted_repo)
        assert "newcache/x.pyc" in staged


class TestCleanupTrackedOrphans:
    """End-to-end cleanup: ``git rm --cached`` + ``.gitignore`` write."""

    def test_dry_run_does_not_modify_state(self, polluted_repo: Path) -> None:
        report = cleanup_tracked_orphans(polluted_repo, dry_run=True)
        assert report.dry_run is True
        assert len(report.detected) >= 4
        assert report.removed == []
        assert report.gitignore_updated is False
        assert (polluted_repo / "infra" / "sandbox.tfstate").exists()
        result = subprocess.run(
            ["git", "-C", str(polluted_repo), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "infra/sandbox.tfstate" in result.stdout

    def test_apply_removes_index_entries_and_writes_gitignore(self, polluted_repo: Path) -> None:
        report = cleanup_tracked_orphans(polluted_repo)
        assert report.dry_run is False
        assert len(report.removed) >= 4
        ls = subprocess.run(
            ["git", "-C", str(polluted_repo), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "infra/sandbox.tfstate" not in ls
        assert ".coverage" not in ls
        assert "tests/__pycache__/test_foo.cpython-312.pyc" not in ls
        assert (polluted_repo / "infra" / "sandbox.tfstate").exists()
        assert (polluted_repo / ".coverage").exists()
        gitignore = (polluted_repo / ".gitignore").read_text(encoding="utf-8")
        assert DEVBENCH_GITIGNORE_HEADER in gitignore
        assert "*.tfstate" in gitignore
        assert "__pycache__/" in gitignore
        assert ".coverage" in gitignore
        assert ".DS_Store" in gitignore

    def test_idempotent_when_run_twice(self, polluted_repo: Path) -> None:
        first = cleanup_tracked_orphans(polluted_repo)
        second = cleanup_tracked_orphans(polluted_repo)
        assert len(first.removed) >= 4
        assert second.removed == []
        assert second.detected == []
        assert second.gitignore_updated is False
        gitignore = (polluted_repo / ".gitignore").read_text(encoding="utf-8")
        assert gitignore.count(DEVBENCH_GITIGNORE_HEADER) == 1

    def test_existing_gitignore_preserved_and_extended(self, polluted_repo: Path) -> None:
        existing = "# Project-specific\nbuild/\n"
        (polluted_repo / ".gitignore").write_text(existing, encoding="utf-8")
        cleanup_tracked_orphans(polluted_repo)
        new_content = (polluted_repo / ".gitignore").read_text(encoding="utf-8")
        assert "# Project-specific" in new_content
        assert "build/" in new_content
        assert DEVBENCH_GITIGNORE_HEADER in new_content

    def test_non_repo_raises(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        with pytest.raises(FileNotFoundError, match="not a git repo"):
            cleanup_tracked_orphans(not_a_repo)


class TestOrphanReportShape:
    """The dataclass surfaces the data callers need without leaking internals."""

    def test_apply_report_fields(self, polluted_repo: Path) -> None:
        report = cleanup_tracked_orphans(polluted_repo)
        assert isinstance(report, OrphanReport)
        assert report.repo_path == polluted_repo.resolve()
        assert report.gitignore_path == polluted_repo / ".gitignore"
        assert report.dry_run is False
        assert isinstance(report.detected, list)
        assert isinstance(report.removed, list)
