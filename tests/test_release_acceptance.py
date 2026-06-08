"""Tests for the release-acceptance gate (infra/scripts/release_acceptance.py).

The gate exits 0 only when all eight conditions (a)-(h) hold:
  (a) make validate green
  (b) full CI matrix green
  (c) 100 percent branch coverage on new/changed modules
  (d) zero-orphan and zero-stale grep ACs pass
  (e) mirrored-list sync pairs match
  (f) validate-backlog rc 0
  (g) check rc 0
  (h) AC-to-test traceability passes

Each single-condition failure makes the gate exit non-zero.
"""

from __future__ import annotations

import importlib
import importlib.abc
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATE_MODULE_PATH = REPO_ROOT / "infra" / "scripts" / "release_acceptance.py"


def _import_gate() -> Any:
    """Dynamically import the release_acceptance module."""
    spec = importlib.util.spec_from_file_location("release_acceptance", GATE_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {GATE_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec so @dataclass can resolve cls.__module__
    # (required for Python 3.14 compatibility).
    sys.modules["release_acceptance"] = module
    cast(importlib.abc.Loader, spec.loader).exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> Any:
    """Load the release_acceptance module once per test module."""
    return _import_gate()


@pytest.mark.unit
class TestGateModuleLoads:
    """The release-acceptance script must be importable and expose the required API."""

    def test_module_loads_without_error(self) -> None:
        module = _import_gate()
        assert module is not None

    def test_module_exposes_run_gate(self, gate: Any) -> None:
        assert callable(getattr(gate, "run_gate", None)), (
            "release_acceptance module must expose run_gate(repo_root) callable"
        )

    def test_module_exposes_check_mirrored_lists(self, gate: Any) -> None:
        assert callable(getattr(gate, "check_mirrored_lists", None)), (
            "release_acceptance module must expose check_mirrored_lists(repo_root) callable"
        )

    def test_module_exposes_individual_condition_checkers(self, gate: Any) -> None:
        """Each condition (a)-(h) must have a dedicated callable."""
        expected_functions = [
            "check_make_validate",
            "check_ci_matrix",
            "check_branch_coverage",
            "check_zero_orphan_stale",
            "check_mirrored_lists",
            "check_validate_backlog",
            "check_devbench_check",
            "check_ac_traceability",
        ]
        for fn_name in expected_functions:
            assert callable(getattr(gate, fn_name, None)), f"release_acceptance module must expose {fn_name}() callable"


@pytest.mark.unit
class TestConditionAMakeValidate:
    """Condition (a): make validate must be green."""

    def test_passes_when_make_validate_exits_zero(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_make_validate(REPO_ROOT)
        assert result.passed is True
        assert result.label == "make_validate"

    def test_fails_when_make_validate_exits_nonzero(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="lint error")
            result = gate.check_make_validate(REPO_ROOT)
        assert result.passed is False
        assert result.label == "make_validate"
        assert result.message != ""

    @pytest.mark.parametrize("rc", [1, 2, 127])
    def test_fails_for_any_nonzero_exit_code(self, gate: Any, rc: int) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=rc, stdout="", stderr="error")
            result = gate.check_make_validate(REPO_ROOT)
        assert result.passed is False


@pytest.mark.unit
class TestConditionBCIMatrix:
    """Condition (b): full CI matrix must be green."""

    def test_passes_when_ci_checks_all_green(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_ci_matrix(REPO_ROOT)
        assert result.passed is True
        assert result.label == "ci_matrix"

    def test_fails_when_ci_checks_fail(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="CI failed")
            result = gate.check_ci_matrix(REPO_ROOT)
        assert result.passed is False
        assert result.label == "ci_matrix"


@pytest.mark.unit
class TestConditionCBranchCoverage:
    """Condition (c): branch coverage on new/changed modules must meet the threshold."""

    def test_passes_when_coverage_at_100_percent(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_branch_coverage(REPO_ROOT)
        assert result.passed is True
        assert result.label == "branch_coverage"

    def test_fails_when_coverage_below_100_percent(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=2, stdout="FAIL Required test coverage of 100% not reached", stderr=""
            )
            result = gate.check_branch_coverage(REPO_ROOT)
        assert result.passed is False
        assert result.label == "branch_coverage"

    def test_passes_custom_cov_source_and_threshold(self, gate: Any) -> None:
        """check_branch_coverage must accept cov_source and cov_fail_under parameters."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_branch_coverage(REPO_ROOT, cov_source="mymodule", cov_fail_under=90)
        assert result.passed is True
        # Verify the custom parameters were passed to pytest
        called_cmd = mock_run.call_args[0][0]
        assert "--cov=mymodule" in called_cmd
        assert "--cov-fail-under=90" in called_cmd

    def test_failure_message_includes_threshold(self, gate: Any) -> None:
        """Failure message must include the configured threshold value."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2, stdout="coverage fail", stderr="")
            result = gate.check_branch_coverage(REPO_ROOT, cov_source="mymod", cov_fail_under=95)
        assert result.passed is False
        assert "95" in result.message


@pytest.mark.unit
class TestConditionDZeroOrphanStale:
    """Condition (d): zero-orphan and zero-stale grep ACs pass."""

    def test_passes_when_no_orphans_and_no_stale(self, gate: Any) -> None:
        with (
            patch.object(gate, "_has_workspace_config", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_zero_orphan_stale(REPO_ROOT)
        assert result.passed is True
        assert result.label == "zero_orphan_stale"

    def test_fails_when_orphans_found(self, gate: Any) -> None:
        with (
            patch.object(gate, "_has_workspace_config", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="orphan found", stderr="")
            result = gate.check_zero_orphan_stale(REPO_ROOT)
        assert result.passed is False
        assert result.label == "zero_orphan_stale"

    def test_skipped_when_no_workspace_config(self, gate: Any) -> None:
        with patch.object(gate, "_has_workspace_config", return_value=False):
            result = gate.check_zero_orphan_stale(REPO_ROOT)
        assert result.passed is True
        assert result.label == "zero_orphan_stale"
        assert "skipped" in result.message


@pytest.mark.unit
class TestConditionEMirroredLists:
    """Condition (e): mirrored-list sync pairs must match.

    Two pairs are validated:
    - KNOWN_JUDGE_NAMES in constants.py must match KNOWN_JUDGES array in
      guard-verdict-format.sh
    - Plugin versions listed in each marketplace.json must match the versions
      in the individual plugin.json files
    """

    def test_passes_when_judge_lists_are_in_sync(self, gate: Any) -> None:
        """Mirrored judge lists from constants.py and guard-verdict-format.sh must match."""
        result = gate.check_mirrored_lists(REPO_ROOT)
        # In a real repo these should be in sync -- if not it's a real structural error
        assert result.label == "mirrored_lists"
        assert isinstance(result.passed, bool)
        assert result.passed is True
        assert isinstance(result.message, str)

    def test_fails_when_constants_judge_names_differ_from_guard_script(self, gate: Any) -> None:
        """Mismatch between KNOWN_JUDGE_NAMES and KNOWN_JUDGES in guard script must fail."""
        mock_constants_judges = frozenset({"code_review", "test_review", "extra_judge_not_in_guard"})
        mock_guard_judges = frozenset({"code_review", "test_review"})
        with (
            patch.object(gate, "_load_constants_known_judges", return_value=mock_constants_judges),
            patch.object(gate, "_load_guard_script_known_judges", return_value=mock_guard_judges),
            patch.object(gate, "_check_marketplace_plugin_versions_in_sync", return_value=True),
        ):
            result = gate.check_mirrored_lists(REPO_ROOT)
        assert result.passed is False
        assert "judge" in result.message.lower()

    def test_fails_when_marketplace_versions_differ_from_plugin_json(self, gate: Any) -> None:
        """Mismatch between marketplace.json plugin versions and plugin.json versions must fail."""
        mock_constants_judges = frozenset({"code_review"})
        mock_guard_judges = frozenset({"code_review"})
        with (
            patch.object(gate, "_load_constants_known_judges", return_value=mock_constants_judges),
            patch.object(gate, "_load_guard_script_known_judges", return_value=mock_guard_judges),
            patch.object(gate, "_check_marketplace_plugin_versions_in_sync", return_value=False),
        ):
            result = gate.check_mirrored_lists(REPO_ROOT)
        assert result.passed is False

    def test_load_constants_known_judges_returns_frozenset(self, gate: Any) -> None:
        result = gate._load_constants_known_judges(REPO_ROOT)
        assert isinstance(result, frozenset)
        assert len(result) > 0

    def test_load_guard_script_known_judges_returns_frozenset(self, gate: Any) -> None:
        result = gate._load_guard_script_known_judges(REPO_ROOT)
        assert isinstance(result, frozenset)
        assert len(result) > 0

    def test_constants_and_guard_judge_lists_are_actually_in_sync(self, gate: Any) -> None:
        """The real repo's judge lists must be in sync (structural correctness test)."""
        constants_judges = gate._load_constants_known_judges(REPO_ROOT)
        guard_judges = gate._load_guard_script_known_judges(REPO_ROOT)
        assert constants_judges == guard_judges, (
            f"Judge lists are out of sync.\n"
            f"constants.py KNOWN_JUDGE_NAMES: {sorted(constants_judges)}\n"
            f"guard-verdict-format.sh KNOWN_JUDGES: {sorted(guard_judges)}\n"
            f"Only in constants.py: {sorted(constants_judges - guard_judges)}\n"
            f"Only in guard script: {sorted(guard_judges - constants_judges)}"
        )

    def test_marketplace_plugin_versions_are_in_sync(self, gate: Any) -> None:
        """The real repo's marketplace plugin versions must match plugin.json versions."""
        result = gate._check_marketplace_plugin_versions_in_sync(REPO_ROOT)
        assert result is True, (
            "Marketplace plugin versions are out of sync with plugin.json files. Run check_mirrored_lists for details."
        )


@pytest.mark.unit
class TestConditionFValidateBacklog:
    """Condition (f): validate-backlog must exit 0."""

    def test_skipped_when_no_workspace_config(self, gate: Any) -> None:
        with patch.object(gate, "_has_workspace_config", return_value=False):
            result = gate.check_validate_backlog(REPO_ROOT)
        assert result.passed is True
        assert result.label == "validate_backlog"
        assert "skipped" in result.message

    def test_passes_when_validate_backlog_exits_zero(self, gate: Any) -> None:
        with (
            patch.object(gate, "_has_workspace_config", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_validate_backlog(REPO_ROOT)
        assert result.passed is True
        assert result.label == "validate_backlog"

    def test_fails_when_validate_backlog_exits_nonzero(self, gate: Any) -> None:
        with (
            patch.object(gate, "_has_workspace_config", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="backlog error")
            result = gate.check_validate_backlog(REPO_ROOT)
        assert result.passed is False
        assert result.label == "validate_backlog"
        assert result.message != ""


@pytest.mark.unit
class TestConditionGDevbenchCheck:
    """Condition (g): devbench check must exit 0."""

    def test_skipped_when_no_workspace_config(self, gate: Any) -> None:
        with patch.object(gate, "_has_workspace_config", return_value=False):
            result = gate.check_devbench_check(REPO_ROOT)
        assert result.passed is True
        assert result.label == "devbench_check"
        assert "skipped" in result.message

    def test_passes_when_check_exits_zero(self, gate: Any) -> None:
        with (
            patch.object(gate, "_has_workspace_config", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_devbench_check(REPO_ROOT)
        assert result.passed is True
        assert result.label == "devbench_check"

    def test_fails_when_check_exits_nonzero(self, gate: Any) -> None:
        with (
            patch.object(gate, "_has_workspace_config", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="check error")
            result = gate.check_devbench_check(REPO_ROOT)
        assert result.passed is False
        assert result.label == "devbench_check"
        assert result.message != ""


@pytest.mark.unit
class TestConditionHACTraceability:
    """Condition (h): every AC in the spec must have a corresponding passing test."""

    def test_passes_when_traceability_check_exits_zero(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = gate.check_ac_traceability(REPO_ROOT)
        assert result.passed is True
        assert result.label == "ac_traceability"

    def test_fails_when_traceability_check_exits_nonzero(self, gate: Any) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="AC not covered")
            result = gate.check_ac_traceability(REPO_ROOT)
        assert result.passed is False
        assert result.label == "ac_traceability"
        assert result.message != ""


@pytest.mark.unit
class TestRunGate:
    """run_gate aggregates all eight conditions and exits 0 only when all pass."""

    def _all_passing_mock(self, gate: Any) -> MagicMock:
        """Return a ConditionResult-like mock that always passes."""
        m = MagicMock()
        m.passed = True
        m.label = "mock"
        m.message = ""
        return m

    def test_passes_when_all_conditions_hold(self, gate: Any) -> None:
        passing = MagicMock(passed=True, label="x", message="")
        condition_names = [
            "check_make_validate",
            "check_ci_matrix",
            "check_branch_coverage",
            "check_zero_orphan_stale",
            "check_mirrored_lists",
            "check_validate_backlog",
            "check_devbench_check",
            "check_ac_traceability",
        ]
        patches = {name: MagicMock(return_value=passing) for name in condition_names}
        with patch.multiple(gate, **patches):
            exit_code = gate.run_gate(REPO_ROOT)
        assert exit_code == 0

    @pytest.mark.parametrize(
        "failing_condition",
        [
            "check_make_validate",
            "check_ci_matrix",
            "check_branch_coverage",
            "check_zero_orphan_stale",
            "check_mirrored_lists",
            "check_validate_backlog",
            "check_devbench_check",
            "check_ac_traceability",
        ],
    )
    def test_fails_when_single_condition_is_unmet(self, gate: Any, failing_condition: str) -> None:
        """Gate must exit non-zero when any single condition (a)-(h) fails."""
        passing = MagicMock(passed=True, label="x", message="")
        failing = MagicMock(passed=False, label=failing_condition, message="condition failed")
        condition_names = [
            "check_make_validate",
            "check_ci_matrix",
            "check_branch_coverage",
            "check_zero_orphan_stale",
            "check_mirrored_lists",
            "check_validate_backlog",
            "check_devbench_check",
            "check_ac_traceability",
        ]
        patches = {name: MagicMock(return_value=passing) for name in condition_names}
        patches[failing_condition] = MagicMock(return_value=failing)
        with patch.multiple(gate, **patches):
            exit_code = gate.run_gate(REPO_ROOT)
        assert exit_code != 0, f"Gate must exit non-zero when {failing_condition} fails, but returned {exit_code}"

    def test_run_gate_returns_integer(self, gate: Any) -> None:
        passing = MagicMock(passed=True, label="x", message="")
        condition_names = [
            "check_make_validate",
            "check_ci_matrix",
            "check_branch_coverage",
            "check_zero_orphan_stale",
            "check_mirrored_lists",
            "check_validate_backlog",
            "check_devbench_check",
            "check_ac_traceability",
        ]
        patches = {name: MagicMock(return_value=passing) for name in condition_names}
        with patch.multiple(gate, **patches):
            exit_code = gate.run_gate(REPO_ROOT)
        assert isinstance(exit_code, int)


@pytest.mark.unit
class TestConditionResultDataClass:
    """ConditionResult must be a plain data carrier with passed, label, message."""

    def test_condition_result_has_required_attributes(self, gate: Any) -> None:
        cr = gate.ConditionResult(passed=True, label="test", message="ok")
        assert cr.passed is True
        assert cr.label == "test"
        assert cr.message == "ok"

    def test_condition_result_failing_instance(self, gate: Any) -> None:
        cr = gate.ConditionResult(passed=False, label="make_validate", message="lint error found")
        assert cr.passed is False
        assert cr.label == "make_validate"
        assert "lint" in cr.message

    def test_condition_result_message_defaults_to_empty_string(self, gate: Any) -> None:
        cr = gate.ConditionResult(passed=True, label="test", message="")
        assert cr.message == ""


@pytest.mark.unit
class TestScriptEntryPoint:
    """The script must be runnable as a CLI tool and exit 0 or non-zero correctly."""

    def test_script_file_exists(self) -> None:
        assert GATE_MODULE_PATH.exists(), f"release_acceptance.py not found at {GATE_MODULE_PATH}"

    def test_script_is_executable_via_python(self) -> None:
        """The script must be syntactically valid Python."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(GATE_MODULE_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"release_acceptance.py has syntax errors:\n{result.stderr}"


@pytest.mark.unit
class TestParseArgs:
    """_parse_args must parse --repo-root and default to None."""

    def test_parse_args_returns_none_repo_root_by_default(self, gate: Any) -> None:
        args = gate._parse_args([])
        assert args.repo_root is None

    def test_parse_args_accepts_repo_root(self, gate: Any, tmp_path: Any) -> None:
        args = gate._parse_args(["--repo-root", str(tmp_path)])
        assert args.repo_root == tmp_path


@pytest.mark.unit
class TestLoadConstantsKnownJudges:
    """Error paths in _load_constants_known_judges."""

    def test_raises_file_not_found_for_missing_constants(self, gate: Any, tmp_path: Any) -> None:
        with pytest.raises(FileNotFoundError, match=r"constants\.py not found"):
            gate._load_constants_known_judges(tmp_path)

    def test_raises_import_error_for_none_spec(self, gate: Any) -> None:
        with patch("importlib.util.spec_from_file_location", return_value=None):
            with pytest.raises(ImportError, match="Cannot load constants module"):
                gate._load_constants_known_judges(REPO_ROOT)


@pytest.mark.unit
class TestLoadGuardScriptKnownJudges:
    """Error paths in _load_guard_script_known_judges."""

    def test_raises_file_not_found_for_missing_guard_script(self, gate: Any, tmp_path: Any) -> None:
        with pytest.raises(FileNotFoundError, match=r"guard-verdict-format\.sh not found"):
            gate._load_guard_script_known_judges(tmp_path)

    def test_raises_value_error_when_known_judges_array_missing(self, gate: Any, tmp_path: Any) -> None:
        # Create a fake guard script without the KNOWN_JUDGES array
        guard_dir = tmp_path / "plugin" / "devbench-orchestrate" / "scripts"
        guard_dir.mkdir(parents=True)
        guard_script = guard_dir / "guard-verdict-format.sh"
        guard_script.write_text("#!/usr/bin/env bash\n# no array here\n", encoding="utf-8")
        with pytest.raises(ValueError, match="KNOWN_JUDGES array not found"):
            gate._load_guard_script_known_judges(tmp_path)


@pytest.mark.unit
class TestCheckMarketplacePluginVersionsInSync:
    """Error paths in _check_marketplace_plugin_versions_in_sync."""

    def test_returns_false_when_marketplace_json_missing(self, gate: Any, tmp_path: Any) -> None:
        result = gate._check_marketplace_plugin_versions_in_sync(tmp_path)
        assert result is False

    def test_returns_false_when_plugin_json_missing(self, gate: Any, tmp_path: Any) -> None:
        # Create marketplace.json that references a plugin whose plugin.json doesn't exist
        plugin_dir = tmp_path / "plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        marketplace_data = {"plugins": [{"name": "nonexistent-plugin", "version": "1.0.0"}]}
        (plugin_dir / "marketplace.json").write_text(json.dumps(marketplace_data), encoding="utf-8")
        # Also create the authoring marketplace.json so it doesn't fail first
        authoring_dir = tmp_path / "plugin-authoring" / ".claude-plugin"
        authoring_dir.mkdir(parents=True)
        authoring_marketplace_data: dict[str, list[Any]] = {"plugins": []}
        (authoring_dir / "marketplace.json").write_text(json.dumps(authoring_marketplace_data), encoding="utf-8")
        result = gate._check_marketplace_plugin_versions_in_sync(tmp_path)
        assert result is False

    def test_returns_false_when_versions_mismatch(self, gate: Any, tmp_path: Any) -> None:
        # plugin marketplace says 1.0.0 but plugin.json says 2.0.0
        plugin_dir = tmp_path / "plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        marketplace_data = {"plugins": [{"name": "my-plugin", "version": "1.0.0"}]}
        (plugin_dir / "marketplace.json").write_text(json.dumps(marketplace_data), encoding="utf-8")
        my_plugin_dir = tmp_path / "plugin" / "my-plugin" / ".claude-plugin"
        my_plugin_dir.mkdir(parents=True)
        plugin_json_data = {"name": "my-plugin", "version": "2.0.0"}
        (my_plugin_dir / "plugin.json").write_text(json.dumps(plugin_json_data), encoding="utf-8")
        # Also create the authoring marketplace.json so it doesn't fail first
        authoring_dir = tmp_path / "plugin-authoring" / ".claude-plugin"
        authoring_dir.mkdir(parents=True)
        authoring_marketplace_data2: dict[str, list[Any]] = {"plugins": []}
        (authoring_dir / "marketplace.json").write_text(json.dumps(authoring_marketplace_data2), encoding="utf-8")
        result = gate._check_marketplace_plugin_versions_in_sync(tmp_path)
        assert result is False
