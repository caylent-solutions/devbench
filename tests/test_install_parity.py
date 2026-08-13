"""Tests for src/devbench/install_parity.py (#301 FR-1, FR-2).

Coverage requirement: 100% line + branch on devbench.install_parity.

Covers:
- resolve_install_identity: resolves path, full revision, branch, origin URL
  for a real git checkout; branch is None on detached HEAD; a missing
  origin remote yields origin_url=None (not an error); a non-git path
  raises InstallParityError naming the path.
- resolve_install_parity: behind_count only counts commits touching
  ORCHESTRATOR_SOURCE_PREFIX (a docs-only commit does not increment it);
  self-hosting detection treats origins differing only by scheme, embedded
  credentials, a trailing .git, or host case as the same repository; a
  genuinely different repository yields self_hosting=False; a checkout
  with no origin does not match and does not raise; a resolver failure on
  the harness itself raises naming the path rather than returning a
  default "in sync" result.

Every import of ``devbench.install_parity`` symbols is local to the test
body (not at module scope) so this file collects cleanly before the module
exists -- required for the pre-change RED observation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbench.config_loader import RepoConfig, RuntimeConfig

_HARNESS_ORIGIN = "https://github.com/caylent-solutions/devbench.git"


def _git(args: list[str], cwd: Path) -> str:
    """Run a git subcommand in *cwd* and return its stripped stdout."""
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _configure_identity(path: Path) -> None:
    """Configure a deterministic repo-local git identity for commits under *path*.

    Required because CI runners have no global git identity (unlike local
    developer machines) and ``git clone`` does not copy the source repo's
    local ``.git/config`` identity into the destination checkout.
    """
    _git(["config", "user.email", "install-parity-test@example.com"], path)
    _git(["config", "user.name", "Install Parity Test"], path)
    _git(["config", "commit.gpgsign", "false"], path)


def _init_repo(path: Path, *, origin_url: str | None = None) -> None:
    """Initialise a git repo at *path* with a deterministic identity and one commit."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "--initial-branch=main"], path)
    _configure_identity(path)
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(["add", "README.md"], path)
    _git(["commit", "-m", "initial commit"], path)
    if origin_url is not None:
        _git(["remote", "add", "origin", origin_url], path)


def _commit_file(path: Path, relative: str, content: str, message: str) -> str:
    """Write *content* to *relative* inside *path*, commit it, and return the new revision."""
    target = path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(["add", relative], path)
    _git(["commit", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path)


def _clone(src: Path, dest: Path) -> None:
    """Clone *src* into *dest* on the local filesystem (no network) with a repo-local identity.

    Configures the target checkout's identity immediately after cloning, before
    any caller can commit into it (git clone does not inherit the source repo's
    local identity).
    """
    subprocess.run(["git", "clone", str(src), str(dest)], check=True, capture_output=True, text=True)
    _configure_identity(dest)


def _patch_harness_root(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Point ``install_parity._harness_root`` at *path* for the duration of the test."""
    import devbench.install_parity as install_parity_module

    monkeypatch.setattr(install_parity_module, "_harness_root", lambda: path)


def _self_hosting_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Build a harness checkout and a target checkout cloned from it, both on ``_HARNESS_ORIGIN``."""
    harness = tmp_path / "harness"
    _init_repo(harness, origin_url=_HARNESS_ORIGIN)
    target = tmp_path / "target"
    _clone(harness, target)
    _git(["remote", "set-url", "origin", _HARNESS_ORIGIN], target)
    return harness, target


class TestResolveInstallIdentity:
    """FR-1: InstallIdentity resolves path, full revision, branch, and origin URL."""

    @pytest.mark.unit
    def test_resolves_path_revision_branch_and_origin(self, tmp_path: Path) -> None:
        from devbench.install_parity import InstallIdentity, resolve_install_identity

        repo = tmp_path / "harness"
        _init_repo(repo, origin_url=_HARNESS_ORIGIN)
        expected_revision = _git(["rev-parse", "HEAD"], repo)

        identity = resolve_install_identity(repo)

        assert isinstance(identity, InstallIdentity)
        assert identity.path == repo
        assert identity.revision == expected_revision
        assert len(identity.revision) == 40
        assert identity.branch == "main"
        assert identity.origin_url == _HARNESS_ORIGIN

    @pytest.mark.unit
    def test_detached_head_yields_branch_none(self, tmp_path: Path) -> None:
        from devbench.install_parity import resolve_install_identity

        repo = tmp_path / "detached"
        _init_repo(repo)
        revision = _git(["rev-parse", "HEAD"], repo)
        _git(["checkout", revision], repo)

        identity = resolve_install_identity(repo)

        assert identity.branch is None
        assert identity.revision == revision

    @pytest.mark.unit
    def test_non_git_path_raises_naming_the_path(self, tmp_path: Path) -> None:
        from devbench.install_parity import InstallParityError, resolve_install_identity

        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        with pytest.raises(InstallParityError) as exc_info:
            resolve_install_identity(not_a_repo)

        assert str(not_a_repo) in str(exc_info.value)

    @pytest.mark.unit
    def test_missing_origin_remote_is_none_not_an_error(self, tmp_path: Path) -> None:
        from devbench.install_parity import resolve_install_identity

        repo = tmp_path / "no-origin"
        _init_repo(repo)

        identity = resolve_install_identity(repo)

        assert identity.origin_url is None


class TestBehindCount:
    """FR-1/D-2: behind_count counts only commits touching ORCHESTRATOR_SOURCE_PREFIX."""

    @pytest.mark.unit
    def test_docs_only_commit_does_not_increment_behind_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.install_parity import resolve_install_parity

        harness, target = _self_hosting_pair(tmp_path)
        _commit_file(target, "docs/CHANGELOG.md", "docs only change\n", "docs: note")
        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"caylent-solutions/devbench": RepoConfig(resolved_checkout_path=target)})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is True
        assert result.behind_count == 0
        assert result.in_sync is True

    @pytest.mark.unit
    def test_orchestrator_source_commit_increments_behind_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.install_parity import resolve_install_parity

        harness, target = _self_hosting_pair(tmp_path)
        _commit_file(target, "src/devbench/new_module.py", "x = 1\n", "feat: new module")
        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"caylent-solutions/devbench": RepoConfig(resolved_checkout_path=target)})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is True
        assert result.behind_count == 1
        assert result.in_sync is False

    @pytest.mark.unit
    def test_behind_count_ignores_docs_but_counts_source_commits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.install_parity import resolve_install_parity

        harness, target = _self_hosting_pair(tmp_path)
        _commit_file(target, "docs/CHANGELOG.md", "docs only change\n", "docs: note")
        _commit_file(target, "src/devbench/new_module.py", "x = 1\n", "feat: new module")
        _commit_file(target, "README.md", "more docs\n", "docs: readme touch-up")
        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"caylent-solutions/devbench": RepoConfig(resolved_checkout_path=target)})

        result = resolve_install_parity(runtime_config)

        assert result.behind_count == 1


class TestSelfHostingDetection:
    """FR-2: origin canonicalization and self-hosting match/no-match cases."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("target_origin", "expected_self_hosting"),
        [
            pytest.param("git@github.com:caylent-solutions/devbench.git", True, id="scheme"),
            pytest.param(
                "https://token:x-oauth-basic@github.com/caylent-solutions/devbench.git",
                True,
                id="embedded-credentials",
            ),
            pytest.param("https://github.com/caylent-solutions/devbench", True, id="no-trailing-dot-git"),
            pytest.param("https://GitHub.COM/caylent-solutions/devbench.git", True, id="host-case"),
            pytest.param("https://github.com/caylent-solutions/other-repo.git", False, id="different-repo"),
        ],
    )
    def test_origin_canonicalization_matrix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        target_origin: str,
        expected_self_hosting: bool,
    ) -> None:
        from devbench.install_parity import resolve_install_parity

        harness = tmp_path / "harness"
        _init_repo(harness, origin_url=_HARNESS_ORIGIN)
        target = tmp_path / "target"
        _clone(harness, target)
        _git(["remote", "set-url", "origin", target_origin], target)

        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"some-org/some-repo": RepoConfig(resolved_checkout_path=target)})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is expected_self_hosting
        if expected_self_hosting:
            assert result.target is not None
            assert result.target.path == target
        else:
            assert result.target is None
            assert result.behind_count == 0
            assert result.in_sync is True

    @pytest.mark.unit
    def test_checkout_with_no_origin_does_not_match_and_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.install_parity import resolve_install_parity

        harness = tmp_path / "harness"
        _init_repo(harness, origin_url=_HARNESS_ORIGIN)
        target = tmp_path / "target"
        _init_repo(target)  # deliberately no origin remote

        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"some-org/some-repo": RepoConfig(resolved_checkout_path=target)})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is False
        assert result.target is None
        assert result.behind_count == 0
        assert result.in_sync is True

    @pytest.mark.unit
    def test_harness_with_no_origin_is_not_self_hosting(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.install_parity import resolve_install_parity

        harness = tmp_path / "harness"
        _init_repo(harness)  # deliberately no origin remote
        target = tmp_path / "target"
        _init_repo(target, origin_url=_HARNESS_ORIGIN)

        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"some-org/some-repo": RepoConfig(resolved_checkout_path=target)})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is False
        assert result.target is None

    @pytest.mark.unit
    def test_no_configured_repos_is_not_self_hosting(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.install_parity import resolve_install_parity

        harness = tmp_path / "harness"
        _init_repo(harness, origin_url=_HARNESS_ORIGIN)

        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is False
        assert result.target is None
        assert result.harness is not None
        assert result.harness.revision == _git(["rev-parse", "HEAD"], harness)

    @pytest.mark.unit
    def test_repo_with_unresolved_checkout_path_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.install_parity import resolve_install_parity

        harness = tmp_path / "harness"
        _init_repo(harness, origin_url=_HARNESS_ORIGIN)

        _patch_harness_root(monkeypatch, harness)
        runtime_config = RuntimeConfig(repos={"some-org/some-repo": RepoConfig(resolved_checkout_path=None)})

        result = resolve_install_parity(runtime_config)

        assert result.self_hosting is False
        assert result.target is None

    @pytest.mark.unit
    def test_resolver_failure_on_harness_raises_naming_path_not_default_in_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.install_parity import InstallParityError, resolve_install_parity

        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()

        _patch_harness_root(monkeypatch, not_a_repo)
        runtime_config = RuntimeConfig(repos={})

        with pytest.raises(InstallParityError) as exc_info:
            resolve_install_parity(runtime_config)

        assert str(not_a_repo) in str(exc_info.value)


class TestCanonicalOriginDirect:
    """Direct unit coverage for ``_canonical_origin``'s branches.

    This devcontainer's global git config carries a
    ``url.https://github.com/.insteadOf=git@github.com:`` rewrite, so a real
    ``git remote get-url origin`` round-trip (as exercised through
    :func:`resolve_install_parity` in ``TestSelfHostingDetection``) never
    observes the raw scp-like form -- git itself rewrites it first. Calling
    the pure comparison function directly exercises the scp-like branch
    regardless of the ambient git configuration.
    """

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            pytest.param(
                "https://github.com/caylent-solutions/devbench.git",
                "git@github.com:caylent-solutions/devbench.git",
                id="scheme",
            ),
            pytest.param(
                "https://github.com/caylent-solutions/devbench.git",
                "https://token:x-oauth-basic@github.com/caylent-solutions/devbench.git",
                id="embedded-credentials",
            ),
            pytest.param(
                "https://github.com/caylent-solutions/devbench.git",
                "https://github.com/caylent-solutions/devbench",
                id="no-trailing-dot-git",
            ),
            pytest.param(
                "https://github.com/caylent-solutions/devbench.git",
                "https://GitHub.COM/caylent-solutions/devbench.git",
                id="host-case",
            ),
        ],
    )
    def test_equivalent_forms_canonicalize_equal(self, left: str, right: str) -> None:
        from devbench.install_parity import _canonical_origin

        assert _canonical_origin(left) == _canonical_origin(right)

    @pytest.mark.unit
    def test_different_repo_canonicalizes_unequal(self) -> None:
        from devbench.install_parity import _canonical_origin

        assert _canonical_origin("https://github.com/caylent-solutions/devbench.git") != _canonical_origin(
            "https://github.com/caylent-solutions/other-repo.git"
        )


class TestHarnessRoot:
    """``_harness_root`` resolves the harness install root from this
    module's own file location.

    Every other test in this file monkeypatches ``_harness_root`` so its
    body is never exercised unmocked; this test calls it directly.
    """

    @pytest.mark.unit
    def test_resolves_three_parents_above_this_module(self) -> None:
        import devbench.install_parity as install_parity_module
        from devbench.install_parity import _harness_root

        expected = Path(install_parity_module.__file__).resolve().parent.parent.parent

        assert _harness_root() == expected
