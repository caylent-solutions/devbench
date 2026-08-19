"""Tests for the machine-observed RED gate (FR-4.2, E4-F3-S1-T2).

All git behavior under test is exercised against real temporary git
repositories created per-test via ``tmp_path`` -- no mocked git. The
``test_runner`` callable accepted by ``observe_red`` is the one seam this
module is designed to inject at (per the work unit's Approach, used to prove
pop-on-raise); everywhere else the real ``default_pytest_runner`` is used
against real pytest subprocess invocations.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from devbench.tdd_gate import (
    REMEDY_1,
    REMEDY_2,
    REMEDY_3,
    RedObservation,
    TddGateRejectionError,
    TestObservation,
    _build_rejection_message,
    _exit_code_reason,
    classify_production_paths,
    default_pytest_runner,
    find_named_test_node_id,
    find_paths_outside_manifest,
    observe_red,
    stash_push_scoped,
)

# ---------------------------------------------------------------------------
# Scratch-repo helpers -- also imported by tests/test_cli.py's TestCmdTddGate
# class, which drives the same real-git scaffolding to exercise the CLI
# wiring around devbench.tdd_gate.observe_red. Promoted here (rather than
# each module rolling its own copy) per the code-review DRY finding: the two
# modules previously carried byte-for-byte-identical local copies of this
# scaffolding, differing only in the target subdirectory name and the git
# author identity.
# ---------------------------------------------------------------------------


def run_scratch_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command in *cwd* against a real git binary (no mocks)."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_scratch_repo(
    tmp_path: Path,
    *,
    dir_name: str = "repo",
    author_email: str = "gate-test@example.com",
    author_name: str = "Gate Test",
) -> Path:
    """Create and ``git init`` a throwaway repository under *tmp_path*."""
    repo = tmp_path / dir_name
    repo.mkdir()
    run_scratch_git(["init"], repo)
    run_scratch_git(["config", "user.email", author_email], repo)
    run_scratch_git(["config", "user.name", author_name], repo)
    return repo


def write_scratch_file(base: Path, relative: str, content: str) -> Path:
    """Write *content* to *relative* under *base*, creating parent directories as needed."""
    target = base / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def commit_scratch_repo(repo: Path, message: str) -> None:
    """Stage every change in *repo* and commit it with *message* (real git, no mocks)."""
    run_scratch_git(["add", "-A"], repo)
    run_scratch_git(["commit", "-m", message], repo)


# Module-local aliases so this file's many existing call sites (parametrized
# across nearly every test class below) stay unchanged.
_run_git = run_scratch_git
_write = write_scratch_file
_commit_all = commit_scratch_repo


def _init_repo(tmp_path: Path) -> Path:
    return init_scratch_repo(tmp_path)


def _work_unit_content(red_entries: list[str]) -> str:
    body = "\n".join(red_entries)
    return f"# T: Example\n\n## Status: in-progress\n\n## TDD Cycle Log\n\n{body}\n\n## Comments\n"


# ---------------------------------------------------------------------------
# classify_production_paths -- AC-E4-F3-S1-T2-8 (Rule 14 reuse, no second
# classifier)
# ---------------------------------------------------------------------------
class TestClassifyProductionPaths:
    @pytest.mark.parametrize(
        ("paths", "expected"),
        [
            (["src/devbench/tdd_gate.py"], ["src/devbench/tdd_gate.py"]),
            (["tests/test_tdd_gate.py"], []),
            (["src/devbench/__init__.py"], []),
            (["docs/adr/1-foo.md"], []),
            (
                ["src/devbench/tdd_gate.py", "tests/test_tdd_gate.py", "docs/foo.md"],
                ["src/devbench/tdd_gate.py"],
            ),
        ],
    )
    def test_classification_matches_rule_14(self, paths: list[str], expected: list[str]) -> None:
        assert classify_production_paths(paths) == expected


# ---------------------------------------------------------------------------
# find_named_test_node_id
# ---------------------------------------------------------------------------
class TestFindNamedTestNodeId:
    def test_extracts_node_id_from_most_recent_red_entry(self) -> None:
        content = _work_unit_content(
            [
                "- [RED] 2026-01-01T00:00:00+00:00 -- Tests: tests/test_a.py created. "
                "Command: pytest tests/test_a.py::test_old -q. Exit: 2.",
                "- [RED] 2026-01-02T00:00:00+00:00 -- Tests: tests/test_b.py created. "
                "Command: pytest tests/test_b.py::test_new -q. Exit: 1.",
            ]
        )
        assert find_named_test_node_id(content) == "tests/test_b.py::test_new"

    def test_returns_last_token_within_the_chosen_entry(self) -> None:
        content = _work_unit_content(
            [
                "- [RED] 2026-01-01T00:00:00+00:00 -- Ran tests/test_a.py::test_one then "
                "tests/test_a.py::test_two, only the second is the named target."
            ]
        )
        assert find_named_test_node_id(content) == "tests/test_a.py::test_two"

    def test_strips_trailing_sentence_punctuation(self) -> None:
        content = _work_unit_content(["- [RED] 2026-01-01T00:00:00+00:00 -- See tests/test_a.py::test_one."])
        assert find_named_test_node_id(content) == "tests/test_a.py::test_one"

    def test_returns_none_when_no_red_entry_has_a_node_id(self) -> None:
        content = _work_unit_content(
            ["- [RED] 2026-01-01T00:00:00+00:00 -- Ran the whole suite, no specific node named."]
        )
        assert find_named_test_node_id(content) is None

    def test_returns_none_when_tdd_cycle_log_section_is_absent(self) -> None:
        content = "# T: Example\n\n## Status: in-progress\n\n## Comments\n"
        assert find_named_test_node_id(content) is None

    def test_ignores_node_id_shaped_text_outside_red_entries(self) -> None:
        content = (
            "# T: Example\n\n"
            "## TDD Cycle Log\n\n"
            "- [GREEN] 2026-01-01T00:00:00+00:00 -- Files changed: "
            "tests/test_a.py::test_one is mentioned here.\n\n"
            "## Comments\n"
        )
        assert find_named_test_node_id(content) is None


# ---------------------------------------------------------------------------
# find_paths_outside_manifest
# ---------------------------------------------------------------------------
class TestFindPathsOutsideManifest:
    def test_clean_tree_matching_manifest_returns_empty(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        assert find_paths_outside_manifest(repo, ["src/prod.py", "tests/test_prod.py"]) == []

    def test_untracked_file_outside_manifest_is_reported(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "README.md", "hello\n")
        _commit_all(repo, "baseline")
        _write(repo, "scratch/rogue.py", "y = 2\n")
        outside = find_paths_outside_manifest(repo, ["src/prod.py"])
        assert outside == ["scratch/rogue.py"]

    def test_modified_tracked_file_outside_manifest_is_reported(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "src/other.py", "x = 1\n")
        _commit_all(repo, "baseline")
        _write(repo, "src/other.py", "x = 2\n")
        outside = find_paths_outside_manifest(repo, ["src/prod.py"])
        assert outside == ["src/other.py"]

    def test_files_within_manifest_are_not_reported_even_when_dirty(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "src/prod.py", "x = 1\n")
        _commit_all(repo, "baseline")
        _write(repo, "src/prod.py", "x = 2\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        outside = find_paths_outside_manifest(repo, ["src/prod.py", "tests/test_prod.py"])
        assert outside == []

    def test_staged_rename_outside_manifest_reports_both_old_and_new_path(self, tmp_path: Path) -> None:
        """A real `git status --porcelain=v1` rename line ('R  old -> new') reports both paths."""
        repo = _init_repo(tmp_path)
        _write(repo, "src/original.py", "x = 1\n")
        _commit_all(repo, "baseline")
        _run_git(["mv", "src/original.py", "src/renamed.py"], repo)
        outside = find_paths_outside_manifest(repo, ["tests/test_unrelated.py"])
        assert outside == ["src/original.py", "src/renamed.py"]

    def test_staged_rename_within_manifest_is_not_reported(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "src/original.py", "x = 1\n")
        _commit_all(repo, "baseline")
        _run_git(["mv", "src/original.py", "src/renamed.py"], repo)
        outside = find_paths_outside_manifest(repo, ["src/original.py", "src/renamed.py"])
        assert outside == []

    def test_git_status_failure_raises_rejection_error(self, tmp_path: Path) -> None:
        """A real (non-mocked) `git status` failure -- a corrupted index -- fails closed."""
        repo = _init_repo(tmp_path)
        _write(repo, "README.md", "baseline\n")
        _commit_all(repo, "baseline")
        index_path = repo / ".git" / "index"
        index_path.write_text("not a valid git index\n", encoding="utf-8")
        with pytest.raises(TddGateRejectionError, match="git status"):
            find_paths_outside_manifest(repo, [])


# ---------------------------------------------------------------------------
# default_pytest_runner -- measured exit-code semantics (spec AC-50, [V])
# ---------------------------------------------------------------------------
class TestDefaultPytestRunner:
    def test_genuine_failure_reports_exit_1_and_failed_outcome(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert False\n")
        observation = default_pytest_runner("tests/test_x.py::test_x", tmp_path)
        assert observation.exit_code == 1
        assert observation.node_outcome == "FAILED"

    def test_passing_test_reports_exit_0_and_passed_outcome(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
        observation = default_pytest_runner("tests/test_x.py::test_x", tmp_path)
        assert observation.exit_code == 0
        assert observation.node_outcome == "PASSED"

    def test_setup_error_reports_exit_1_but_error_outcome_not_failed(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "tests/test_x.py",
            "import pytest\n\n"
            "@pytest.fixture\n"
            "def broken():\n"
            "    raise RuntimeError('boom')\n\n"
            "def test_x(broken):\n"
            "    assert True\n",
        )
        observation = default_pytest_runner("tests/test_x.py::test_x", tmp_path)
        assert observation.exit_code == 1
        assert observation.node_outcome == "ERROR"

    def test_collection_error_reports_exit_2_and_no_outcome(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "tests/test_x.py",
            "import nonexistent_module_xyz_gate_probe\n\n\ndef test_x():\n    assert True\n",
        )
        observation = default_pytest_runner("tests/test_x.py::test_x", tmp_path)
        assert observation.exit_code == 2
        assert observation.node_outcome is None

    def test_missing_file_reports_exit_4_and_no_outcome(self, tmp_path: Path) -> None:
        observation = default_pytest_runner("tests/test_missing.py::test_x", tmp_path)
        assert observation.exit_code == 4
        assert observation.node_outcome is None


# ---------------------------------------------------------------------------
# _exit_code_reason / _build_rejection_message
# ---------------------------------------------------------------------------
class TestExitCodeReason:
    def test_known_exit_codes_have_specific_reasons(self) -> None:
        assert "passed" in _exit_code_reason(0)
        assert "failed" in _exit_code_reason(1)
        assert "collection" in _exit_code_reason(2) or "import" in _exit_code_reason(2)
        assert "not found" in _exit_code_reason(4)

    def test_unknown_exit_code_falls_back_to_generic_reason(self) -> None:
        assert "127" in _exit_code_reason(127)


class TestBuildRejectionMessage:
    def test_message_contains_task_node_exit_code_expected_and_remedies(self) -> None:
        message = _build_rejection_message("T-10", "tests/test_x.py::test_x", 2, "collection error")
        assert "T-10" in message
        assert "tests/test_x.py::test_x" in message
        assert "2" in message
        assert "Expected" in message
        assert REMEDY_1 in message
        assert REMEDY_2 in message
        assert REMEDY_3 in message

    def test_handles_missing_node_id_and_exit_code_gracefully(self) -> None:
        message = _build_rejection_message("T-11", None, None, "dirty tree")
        assert "T-11" in message
        assert "no named test" in message.lower() or "not observed" in message.lower()


class TestTddGateRejectionIsException:
    def test_is_a_plain_exception_subclass(self) -> None:
        assert issubclass(TddGateRejectionError, Exception)
        error = TddGateRejectionError("boom")
        assert str(error) == "boom"


# ---------------------------------------------------------------------------
# observe_red -- pre-flight rejections (AC-53, "no named test", "no
# production paths")
# ---------------------------------------------------------------------------
class TestObserveRedDirtyTreeOutsideManifest:
    def test_rejects_and_names_offending_paths_without_touching_git_stash(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "src/prod.py", "x = 1\n")
        _commit_all(repo, "baseline")
        _write(repo, "scratch/rogue.py", "y = 2\n")
        content = _work_unit_content(
            ["- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest tests/test_prod.py::test_x -q. Exit: 1."]
        )

        def _unused_runner(node_id: str, repo_path: Path) -> TestObservation:
            raise AssertionError("test runner must not run while the tree is dirty outside the manifest")

        with pytest.raises(TddGateRejectionError) as excinfo:
            observe_red(
                "T-1",
                repo,
                ["src/prod.py", "tests/test_prod.py"],
                content,
                test_runner=_unused_runner,
            )
        message = str(excinfo.value)
        assert "scratch/rogue.py" in message
        assert "T-1" in message


class TestObserveRedNoNamedTest:
    def test_rejects_when_tdd_log_has_no_named_test(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "src/prod.py", "x = 1\n")
        _commit_all(repo, "baseline")
        content = _work_unit_content(
            ["- [RED] 2026-01-01T00:00:00+00:00 -- Ran the whole suite manually, no node named."]
        )

        def _unused_runner(node_id: str, repo_path: Path) -> TestObservation:
            raise AssertionError("test runner must not run when no named test exists")

        with pytest.raises(TddGateRejectionError, match="no named test"):
            observe_red("T-2", repo, ["src/prod.py"], content, test_runner=_unused_runner)


class TestObserveRedNoProductionPaths:
    def test_rejects_when_manifest_has_no_production_rows(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        content = _work_unit_content(
            ["- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest tests/test_prod.py::test_x -q. Exit: 1."]
        )

        def _unused_runner(node_id: str, repo_path: Path) -> TestObservation:
            raise AssertionError("test runner must not run with no production paths")

        with pytest.raises(TddGateRejectionError, match="no production-source"):
            observe_red("T-3", repo, ["tests/test_prod.py"], content, test_runner=_unused_runner)


# ---------------------------------------------------------------------------
# observe_red -- the three-part assertion (spec AC-50, spec AC-51)
# ---------------------------------------------------------------------------
class TestObserveRedExitCodeClassification:
    @pytest.mark.parametrize(
        ("exit_code", "node_outcome", "accepted"),
        [
            (1, "FAILED", True),
            (1, "ERROR", False),
            (0, "PASSED", False),
            (2, None, False),
            (4, None, False),
        ],
    )
    def test_three_part_assertion(
        self,
        tmp_path: Path,
        exit_code: int,
        node_outcome: str | None,
        accepted: bool,
    ) -> None:
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content(
            [f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: {exit_code}."]
        )
        canned = TestObservation(exit_code=exit_code, node_outcome=node_outcome, raw_output="canned output")

        def _fake_runner(observed_node_id: str, repo_path: Path) -> TestObservation:
            assert observed_node_id == node_id
            return canned

        if accepted:
            result = observe_red("T-4", repo, ["src/prod.py", "tests/test_prod.py"], content, test_runner=_fake_runner)
            assert result == RedObservation(
                exit_code=exit_code,
                test_node_id=node_id,
                failure_digest=hashlib.sha256(b"canned output").hexdigest(),
            )
        else:
            with pytest.raises(TddGateRejectionError) as excinfo:
                observe_red("T-4", repo, ["src/prod.py", "tests/test_prod.py"], content, test_runner=_fake_runner)
            message = str(excinfo.value)
            assert "T-4" in message
            assert node_id in message
            for remedy in (REMEDY_1, REMEDY_2, REMEDY_3):
                assert remedy in message

        # Restore happens regardless of the accept/reject outcome.
        assert prod_file.read_text(encoding="utf-8") == "x = 2\n"


class TestObserveRedNothingStashedDiagnostic:
    """When nothing was stashed, the rejection must name the cause.

    "Nothing to stash" is legitimate on its own: the committed baseline can BE
    the before-state, which is canonical test-first TDD (a pinning test
    committed alongside still-broken production source genuinely fails here,
    and TestJourneyJ8HonestBehaviorFix covers exactly that). So the run still
    happens and the outcome still decides.

    But when the named test PASSES with nothing stashed, the cause is
    specifically that no production change was removed -- the fix is already in
    the committed baseline. Reporting only "named test outcome was PASSED"
    describes the symptom and leaves the operator to reverse-engineer why. An
    observed run stranded a complete task this way after an operator commit
    snapshotted its in-flight production file.
    """

    def _clean_repo_with_passing_test(self, tmp_path: Path) -> tuple[Path, str, str]:
        """Repo whose committed state already satisfies the named test."""
        repo = _init_repo(tmp_path)
        _write(repo, "src/prod.py", "x = 2\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "production fix already committed")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])
        return repo, node_id, content

    def _reject_message(self, tmp_path: Path) -> str:
        repo, node_id, content = self._clean_repo_with_passing_test(tmp_path)

        def _passing_runner(observed_node_id: str, repo_path: Path) -> TestObservation:
            return TestObservation(exit_code=0, node_outcome="PASSED", raw_output="1 passed")

        with pytest.raises(TddGateRejectionError) as excinfo:
            observe_red("T-9", repo, ["src/prod.py", "tests/test_prod.py"], content, test_runner=_passing_runner)
        return str(excinfo.value)

    def test_names_the_empty_stash_as_the_cause(self, tmp_path: Path) -> None:
        message = self._reject_message(tmp_path)

        assert "removed nothing" in message
        assert "already committed or absent" in message

    def test_states_the_remedy(self, tmp_path: Path) -> None:
        """The operator must be told how to re-derive an observable RED."""
        message = self._reject_message(tmp_path)

        assert "commit the removal of the production change" in message
        assert "staged, uncommitted state" in message

    def test_explains_why_this_state_is_anomalous(self, tmp_path: Path) -> None:
        """Naming the normal contract is what makes the anomaly recognisable."""
        message = self._reject_message(tmp_path)

        assert "leaves committing to 'devbench git-ops'" in message
        assert "committed out of band" in message

    def test_still_reports_the_underlying_outcome_and_remedies(self, tmp_path: Path) -> None:
        """The diagnostic is additive: the standard rejection shape is intact."""
        message = self._reject_message(tmp_path)

        assert "PASSED" in message
        assert "T-9" in message
        for remedy in (REMEDY_1, REMEDY_2, REMEDY_3):
            assert remedy in message

    def test_diagnostic_is_absent_when_a_stash_did_happen(self, tmp_path: Path) -> None:
        """A genuine uncommitted change that still fails to produce RED must not
        be misdiagnosed as an out-of-band commit."""
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")  # real uncommitted change to stash
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])

        def _passing_runner(observed_node_id: str, repo_path: Path) -> TestObservation:
            return TestObservation(exit_code=0, node_outcome="PASSED", raw_output="1 passed")

        with pytest.raises(TddGateRejectionError) as excinfo:
            observe_red("T-10", repo, ["src/prod.py", "tests/test_prod.py"], content, test_runner=_passing_runner)

        assert "removed nothing" not in str(excinfo.value)


class TestObserveRedPathScopedStashWithU:
    def test_test_file_present_and_new_prod_file_absent_during_observation(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "README.md", "baseline\n")
        _commit_all(repo, "baseline")
        test_file = _write(repo, "tests/test_new.py", "def test_new(): assert True\n")
        new_prod_file = repo / "src" / "new_prod.py"
        new_prod_file.parent.mkdir(parents=True, exist_ok=True)
        new_prod_file.write_text("VALUE = 1\n", encoding="utf-8")
        node_id = "tests/test_new.py::test_new"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])
        observed_state: dict[str, bool] = {}

        def _observing_runner(observed_node_id: str, repo_path: Path) -> TestObservation:
            observed_state["test_file_present"] = (repo_path / "tests" / "test_new.py").exists()
            observed_state["prod_file_present"] = (repo_path / "src" / "new_prod.py").exists()
            return TestObservation(exit_code=1, node_outcome="FAILED", raw_output="observed")

        observe_red(
            "T-5",
            repo,
            ["src/new_prod.py", "tests/test_new.py"],
            content,
            test_runner=_observing_runner,
        )

        assert observed_state == {"test_file_present": True, "prod_file_present": False}
        assert new_prod_file.exists()
        assert test_file.exists()


class TestObserveRedNewFileGenuineRed:
    def test_new_production_file_yields_genuine_red_with_default_runner(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _write(repo, "README.md", "baseline\n")
        # A root conftest.py that puts the repo's "src/" layout on
        # sys.path, so a production module living under "src/" (the only
        # path shape the Rule 14 classifier treats as production source) is
        # importable by the test below without a packaging layer.
        _write(
            repo,
            "conftest.py",
            "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent / 'src'))\n",
        )
        _commit_all(repo, "baseline")
        # A well-formed test for a not-yet-existing module: the import is
        # deferred into the test body and converted into an assertion
        # failure, rather than a bare module-level import (which would
        # instead surface as a pytest collection error -- exit code 2, the
        # false-RED trap this gate exists to close).
        _write(
            repo,
            "tests/test_greeter.py",
            "def test_greeter_returns_hello() -> None:\n"
            "    try:\n"
            "        from greeter import greet\n"
            "    except ImportError:\n"
            "        greet = None\n"
            "    assert greet is not None, 'greeter.greet not implemented yet'\n"
            "    assert greet() == 'hello'\n",
        )
        # Uncommitted, untracked new production file -- exactly the file the
        # task is adding. Rule 14 only classifies paths under "src/" (or
        # nested "/src/") as production source, so it lives there.
        _write(repo, "src/greeter.py", "def greet() -> str:\n    return 'hello'\n")
        node_id = "tests/test_greeter.py::test_greeter_returns_hello"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])

        result = observe_red("T-6", repo, ["src/greeter.py", "tests/test_greeter.py"], content)

        assert result.exit_code == 1
        assert result.test_node_id == node_id
        assert len(result.failure_digest) >= 8
        assert (repo / "src" / "greeter.py").read_text(encoding="utf-8") == "def greet() -> str:\n    return 'hello'\n"


# ---------------------------------------------------------------------------
# stash_push_scoped -- direct unit coverage of the three return branches
#
# PUBLIC as of E4-F4-S1-T2 (round 4): promoted from `_stash_push_scoped` so
# `devbench.cli`'s green-green-check can import it instead of duplicating
# it; this class (and its imported name above) was updated to match.
# ---------------------------------------------------------------------------
class TestStashPushScoped:
    def test_no_local_changes_returns_pushed_false_with_no_error(self, tmp_path: Path) -> None:
        """When the scoped path has no uncommitted diff, `git stash push` reports
        'No local changes to save' and stash_push_scoped reports pushed=False,
        error=None (distinct from a genuine failure, which sets error)."""
        repo = _init_repo(tmp_path)
        _write(repo, "src/prod.py", "x = 1\n")
        _commit_all(repo, "baseline")
        pushed, error = stash_push_scoped(repo, ["src/prod.py"])
        assert pushed is False
        assert error is None

    def test_dirty_path_returns_pushed_true_with_no_error(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        pushed, error = stash_push_scoped(repo, ["src/prod.py"])
        assert pushed is True
        assert error is None
        assert prod_file.read_text(encoding="utf-8") == "x = 1\n"
        subprocess.run(["git", "stash", "pop"], cwd=repo, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# observe_red -- fail-closed stash/restore handling (spec AC-52)
# ---------------------------------------------------------------------------
class TestObserveRedStashFailureFailsClosed:
    def test_stash_push_failure_fails_closed_and_names_path(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])
        # A real git failure: a stale index.lock makes any git operation
        # requiring the index fail with git's own error, not a mocked one.
        lock_path = repo / ".git" / "index.lock"
        lock_path.touch()
        try:
            with pytest.raises(TddGateRejectionError, match="stash push"):
                observe_red(
                    "T-7",
                    repo,
                    ["src/prod.py", "tests/test_prod.py"],
                    content,
                    test_runner=lambda node_id, repo_path: TestObservation(
                        exit_code=1, node_outcome="FAILED", raw_output="unused"
                    ),
                )
        finally:
            lock_path.unlink(missing_ok=True)

    def test_stash_pop_failure_fails_closed_and_names_path(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])
        lock_path = repo / ".git" / "index.lock"

        def _lock_before_pop(observed_node_id: str, repo_path: Path) -> TestObservation:
            lock_path.touch()
            return TestObservation(exit_code=1, node_outcome="FAILED", raw_output="unused")

        try:
            with pytest.raises(TddGateRejectionError, match="stash pop"):
                observe_red(
                    "T-8",
                    repo,
                    ["src/prod.py", "tests/test_prod.py"],
                    content,
                    test_runner=_lock_before_pop,
                )
        finally:
            lock_path.unlink(missing_ok=True)
            # Clean up the stash entry that the failed pop left behind.
            subprocess.run(["git", "stash", "drop"], cwd=repo, check=False, capture_output=True)

    def test_stash_pop_failure_and_test_step_exception_both_reported(self, tmp_path: Path) -> None:
        """When the test step raises AND the subsequent pop also fails, the pop failure
        wins (fail-closed on the restore) but both failures are named in the message
        and the original exception is chained as the cause (spec AC-52)."""
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])
        lock_path = repo / ".git" / "index.lock"

        def _lock_and_raise(observed_node_id: str, repo_path: Path) -> TestObservation:
            lock_path.touch()
            raise RuntimeError("simulated test-runner crash during pop-failure scenario")

        try:
            with pytest.raises(TddGateRejectionError, match="stash pop") as exc_info:
                observe_red(
                    "T-8b",
                    repo,
                    ["src/prod.py", "tests/test_prod.py"],
                    content,
                    test_runner=_lock_and_raise,
                )
            assert "test step also raised" in str(exc_info.value)
            assert isinstance(exc_info.value.__cause__, RuntimeError)
        finally:
            lock_path.unlink(missing_ok=True)
            subprocess.run(["git", "stash", "drop"], cwd=repo, check=False, capture_output=True)


class TestObserveRedPopRunsEvenWhenTestStepRaises:
    def test_pop_restores_tree_and_original_exception_propagates(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])

        def _raising_runner(observed_node_id: str, repo_path: Path) -> TestObservation:
            raise RuntimeError("simulated test-runner crash")

        with pytest.raises(RuntimeError, match="simulated test-runner crash"):
            observe_red(
                "T-9",
                repo,
                ["src/prod.py", "tests/test_prod.py"],
                content,
                test_runner=_raising_runner,
            )

        # The stash MUST have been popped even though the test step raised.
        assert prod_file.read_text(encoding="utf-8") == "x = 2\n"
        stash_list = subprocess.run(["git", "stash", "list"], cwd=repo, check=True, capture_output=True, text=True)
        assert stash_list.stdout.strip() == ""

    def test_pop_restores_tree_when_test_step_raises_base_exception(self, tmp_path: Path) -> None:
        """``KeyboardInterrupt`` is a ``BaseException`` subclass, not an ``Exception``
        subclass. The pop-on-raise guarantee (AC-E4-F3-S1-T2-6) must cover it too: an
        operator interrupting a long test run (a realistic case given TEST_TIMEOUT-length
        pytest subprocess runs) must never leave production source stashed out of the
        tree with no indication that ``git stash pop`` is needed."""
        repo = _init_repo(tmp_path)
        prod_file = _write(repo, "src/prod.py", "x = 1\n")
        _write(repo, "tests/test_prod.py", "def test_x(): assert True\n")
        _commit_all(repo, "baseline")
        prod_file.write_text("x = 2\n", encoding="utf-8")
        node_id = "tests/test_prod.py::test_x"
        content = _work_unit_content([f"- [RED] 2026-01-01T00:00:00+00:00 -- Command: pytest {node_id} -q. Exit: 1."])

        def _interrupting_runner(observed_node_id: str, repo_path: Path) -> TestObservation:
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            observe_red(
                "T-10",
                repo,
                ["src/prod.py", "tests/test_prod.py"],
                content,
                test_runner=_interrupting_runner,
            )

        # The stash MUST have been popped even though the test step raised a
        # BaseException that is NOT an Exception subclass.
        assert prod_file.read_text(encoding="utf-8") == "x = 2\n"
        stash_list = subprocess.run(["git", "stash", "list"], cwd=repo, check=True, capture_output=True, text=True)
        assert stash_list.stdout.strip() == ""
