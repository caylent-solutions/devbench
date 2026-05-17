"""Regression tests for ``_hook_lib.sh`` strict rejection of legacy JUDGE_* env vars.

AC-197-9: Shell-side hook helpers (``plugin/devbench/scripts/_hook_lib.sh``) read
``DEVBENCH_*`` only and exit non-zero with a ``[E197] JUDGE_X no longer accepted.
Run 'devbench migrate-env'.`` message when any legacy var is detected.

Issue: #197. Spec: section 4.9.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "_hook_lib.sh"


def _source_hook_lib(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Source ``_hook_lib.sh`` in a subshell with the given environment.

    Returns the CompletedProcess so callers can inspect returncode, stdout, stderr.
    """
    cmd = [
        "bash",
        "-c",
        f". {SCRIPT_PATH}; true",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _minimal_clean_env() -> dict[str, str]:
    """Return a minimal environment with no JUDGE_* or DEVBENCH_* hook vars set."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
    }


class TestJudgeWorkspaceRootRejection:
    """Tests for rejection of the legacy JUDGE_WORKSPACE_ROOT env var."""

    def test_judge_workspace_root_only_exits_nonzero(self) -> None:
        """Sourcing _hook_lib.sh with JUDGE_WORKSPACE_ROOT set must exit non-zero."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/some/path"
        result = _source_hook_lib(env)
        assert result.returncode != 0, (
            "Expected non-zero exit when JUDGE_WORKSPACE_ROOT is set, "
            f"got returncode={result.returncode}"
        )

    def test_judge_workspace_root_only_prints_e197_code(self) -> None:
        """The [E197] error code appears in stderr when JUDGE_WORKSPACE_ROOT is set."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/some/path"
        result = _source_hook_lib(env)
        assert "[E197]" in result.stderr, (
            f"Expected '[E197]' in stderr, got: {result.stderr!r}"
        )

    def test_judge_workspace_root_only_names_legacy_var(self) -> None:
        """The error message names JUDGE_WORKSPACE_ROOT as the rejected var."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/some/path"
        result = _source_hook_lib(env)
        assert "JUDGE_WORKSPACE_ROOT" in result.stderr, (
            f"Expected 'JUDGE_WORKSPACE_ROOT' in stderr, got: {result.stderr!r}"
        )

    def test_judge_workspace_root_only_instructs_migrate_env(self) -> None:
        """The error message tells the operator to run 'devbench migrate-env'."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/some/path"
        result = _source_hook_lib(env)
        assert "devbench migrate-env" in result.stderr, (
            f"Expected 'devbench migrate-env' in stderr, got: {result.stderr!r}"
        )

    def test_judge_workspace_root_with_devbench_also_set_still_rejects(self) -> None:
        """When both JUDGE_WORKSPACE_ROOT and DEVBENCH_WORKSPACE_ROOT are set, hard rejection applies."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/legacy/path"
        env["DEVBENCH_WORKSPACE_ROOT"] = "/new/path"
        result = _source_hook_lib(env)
        assert result.returncode != 0, (
            "Expected non-zero exit when JUDGE_WORKSPACE_ROOT is set (even with DEVBENCH_WORKSPACE_ROOT present), "
            f"got returncode={result.returncode}"
        )
        assert "[E197]" in result.stderr


class TestJudgeLogFileRejection:
    """Tests for rejection of the legacy JUDGE_LOG_FILE env var."""

    def test_judge_log_file_only_exits_nonzero(self) -> None:
        """Sourcing _hook_lib.sh with JUDGE_LOG_FILE set must exit non-zero."""
        env = _minimal_clean_env()
        env["JUDGE_LOG_FILE"] = "/some/log.jsonl"
        result = _source_hook_lib(env)
        assert result.returncode != 0, (
            "Expected non-zero exit when JUDGE_LOG_FILE is set, "
            f"got returncode={result.returncode}"
        )

    def test_judge_log_file_only_prints_e197_code(self) -> None:
        """The [E197] error code appears in stderr when JUDGE_LOG_FILE is set."""
        env = _minimal_clean_env()
        env["JUDGE_LOG_FILE"] = "/some/log.jsonl"
        result = _source_hook_lib(env)
        assert "[E197]" in result.stderr, (
            f"Expected '[E197]' in stderr, got: {result.stderr!r}"
        )

    def test_judge_log_file_only_names_legacy_var(self) -> None:
        """The error message names JUDGE_LOG_FILE as the rejected var."""
        env = _minimal_clean_env()
        env["JUDGE_LOG_FILE"] = "/some/log.jsonl"
        result = _source_hook_lib(env)
        assert "JUDGE_LOG_FILE" in result.stderr, (
            f"Expected 'JUDGE_LOG_FILE' in stderr, got: {result.stderr!r}"
        )

    def test_judge_log_file_only_instructs_migrate_env(self) -> None:
        """The error message tells the operator to run 'devbench migrate-env'."""
        env = _minimal_clean_env()
        env["JUDGE_LOG_FILE"] = "/some/log.jsonl"
        result = _source_hook_lib(env)
        assert "devbench migrate-env" in result.stderr, (
            f"Expected 'devbench migrate-env' in stderr, got: {result.stderr!r}"
        )

    def test_judge_log_file_with_devbench_also_set_still_rejects(self) -> None:
        """When both JUDGE_LOG_FILE and DEVBENCH_LOG_FILE are set, hard rejection applies."""
        env = _minimal_clean_env()
        env["JUDGE_LOG_FILE"] = "/legacy/log.jsonl"
        env["DEVBENCH_LOG_FILE"] = "/new/log.jsonl"
        result = _source_hook_lib(env)
        assert result.returncode != 0, (
            "Expected non-zero exit when JUDGE_LOG_FILE is set (even with DEVBENCH_LOG_FILE present), "
            f"got returncode={result.returncode}"
        )
        assert "[E197]" in result.stderr


class TestBothLegacyVarsRejected:
    """Tests for rejection when multiple legacy JUDGE_* vars are set simultaneously."""

    def test_both_judge_vars_set_exits_nonzero(self) -> None:
        """When both JUDGE_WORKSPACE_ROOT and JUDGE_LOG_FILE are set, exits non-zero."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/some/path"
        env["JUDGE_LOG_FILE"] = "/some/log.jsonl"
        result = _source_hook_lib(env)
        assert result.returncode != 0

    def test_both_judge_vars_set_prints_e197(self) -> None:
        """When both JUDGE_WORKSPACE_ROOT and JUDGE_LOG_FILE are set, [E197] appears in stderr."""
        env = _minimal_clean_env()
        env["JUDGE_WORKSPACE_ROOT"] = "/some/path"
        env["JUDGE_LOG_FILE"] = "/some/log.jsonl"
        result = _source_hook_lib(env)
        assert "[E197]" in result.stderr


class TestHappyPath:
    """Tests for the success path when no legacy JUDGE_* vars are present."""

    def test_no_legacy_vars_exits_zero(self) -> None:
        """When no legacy JUDGE_* vars are set, sourcing _hook_lib.sh exits 0."""
        env = _minimal_clean_env()
        result = _source_hook_lib(env)
        assert result.returncode == 0, (
            f"Expected exit 0 with no legacy vars, got returncode={result.returncode}. "
            f"stderr={result.stderr!r}"
        )

    def test_devbench_workspace_root_only_exits_zero(self) -> None:
        """When only DEVBENCH_WORKSPACE_ROOT is set (no legacy vars), exits 0."""
        env = _minimal_clean_env()
        env["DEVBENCH_WORKSPACE_ROOT"] = "/new/path"
        result = _source_hook_lib(env)
        assert result.returncode == 0, (
            f"Expected exit 0 when only DEVBENCH_WORKSPACE_ROOT is set, "
            f"got returncode={result.returncode}. stderr={result.stderr!r}"
        )

    def test_devbench_log_file_only_exits_zero(self) -> None:
        """When only DEVBENCH_LOG_FILE is set (no legacy vars), exits 0."""
        env = _minimal_clean_env()
        env["DEVBENCH_LOG_FILE"] = "/new/log.jsonl"
        result = _source_hook_lib(env)
        assert result.returncode == 0, (
            f"Expected exit 0 when only DEVBENCH_LOG_FILE is set, "
            f"got returncode={result.returncode}. stderr={result.stderr!r}"
        )

    def test_both_devbench_vars_only_exits_zero(self) -> None:
        """When both DEVBENCH_* vars are set and no legacy vars exist, exits 0."""
        env = _minimal_clean_env()
        env["DEVBENCH_WORKSPACE_ROOT"] = "/new/path"
        env["DEVBENCH_LOG_FILE"] = "/new/log.jsonl"
        result = _source_hook_lib(env)
        assert result.returncode == 0, (
            f"Expected exit 0 when both DEVBENCH_* vars are set, "
            f"got returncode={result.returncode}. stderr={result.stderr!r}"
        )


@pytest.mark.parametrize(
    ("legacy_var", "legacy_value"),
    [
        ("JUDGE_WORKSPACE_ROOT", "/workspace"),
        ("JUDGE_WORKSPACE_ROOT", "/another/workspace"),
        ("JUDGE_LOG_FILE", "/logs/hook.jsonl"),
        ("JUDGE_LOG_FILE", "/tmp/devbench.log"),
    ],
)
def test_any_legacy_var_triggers_rejection(legacy_var: str, legacy_value: str) -> None:
    """Parametrized: any set legacy JUDGE_* var causes non-zero exit with [E197]."""
    env = _minimal_clean_env()
    env[legacy_var] = legacy_value
    result = _source_hook_lib(env)
    assert result.returncode != 0, (
        f"Expected non-zero exit for {legacy_var}={legacy_value!r}, "
        f"got returncode={result.returncode}"
    )
    assert "[E197]" in result.stderr, (
        f"Expected '[E197]' in stderr for {legacy_var}={legacy_value!r}, "
        f"got stderr={result.stderr!r}"
    )
    assert legacy_var in result.stderr, (
        f"Expected legacy var name '{legacy_var}' in stderr, got: {result.stderr!r}"
    )
    assert "devbench migrate-env" in result.stderr, (
        f"Expected 'devbench migrate-env' in stderr for {legacy_var}, got: {result.stderr!r}"
    )


@pytest.mark.parametrize(
    ("legacy_var", "devbench_var"),
    [
        ("JUDGE_WORKSPACE_ROOT", "DEVBENCH_WORKSPACE_ROOT"),
        ("JUDGE_LOG_FILE", "DEVBENCH_LOG_FILE"),
    ],
)
def test_legacy_presence_overrides_new_name_still_rejects(legacy_var: str, devbench_var: str) -> None:
    """When both legacy and new name are set, legacy presence is the disqualifier."""
    env = _minimal_clean_env()
    env[legacy_var] = "/legacy"
    env[devbench_var] = "/new"
    result = _source_hook_lib(env)
    assert result.returncode != 0, (
        f"Expected non-zero exit when both {legacy_var} and {devbench_var} are set"
    )
    assert "[E197]" in result.stderr


def test_no_legacy_var_present_no_error_message() -> None:
    """When no legacy vars are set, no [E197] message appears in stderr."""
    env = _minimal_clean_env()
    result = _source_hook_lib(env)
    assert "[E197]" not in result.stderr, (
        f"Unexpected [E197] in stderr when no legacy vars present: {result.stderr!r}"
    )
