"""Guard the HARNESS: ``guard-harness-write.sh`` blocks the orchestrate session
(and its sub-agents) from editing devbench's OWN source.

An autonomous orchestrate session ran from an editable source checkout could
Write/Edit ``src/devbench/cli.py`` (and the rest of the harness) mid-run -- it
once autonomously patched the harness to fix a bug, unreviewed, with no audit
marker. This guard closes that trust gap.

The hook HARD-DENIES (exit 2) any Write/Edit whose target resolves under the
devbench repo's protected harness surface:

  * the package source tree            -> BLOCKED (exit 2)
  * the package test tree              -> BLOCKED (exit 2)
  * pyproject.toml                     -> BLOCKED (exit 2)
  * the dependency lockfile (uv.lock)  -> BLOCKED (exit 2)
  * the Makefile                       -> BLOCKED (exit 2)
  * the role bypass is REFUSED         -> still BLOCKED with
                                          DEVBENCH_AGENT_ROLE=orchestrator
  * a target-repo / unrelated file     -> ALLOWED (exit 0)

The devbench repo root is resolved GENERICALLY by walking up from the guard
script's real location to the directory that contains both ``src/devbench`` and
``pyproject.toml`` -- no hardcoded workspace or absolute path. Every denial
emits the deterministic ``[HARNESS_SELF_EDIT_BLOCKED]`` marker.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GUARD_SCRIPT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "scripts" / "guard-harness-write.sh"


def _run(
    file_path: str, *, tool_name: str = "Write", env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the guard hook with a crafted PreToolUse payload."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    env = {k: v for k, v in os.environ.items() if k != "BASH_ENV"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(GUARD_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.mark.unit
class TestGuardHarnessWriteScriptExists:
    def test_script_present_and_executable(self) -> None:
        assert GUARD_SCRIPT.is_file(), f"guard-harness-write.sh must exist at {GUARD_SCRIPT}"
        assert os.access(GUARD_SCRIPT, os.X_OK), "guard-harness-write.sh must be executable"


@pytest.mark.unit
class TestGuardHarnessWriteBlocks:
    """Each protected harness path must HARD-DENY with exit 2 (AC-1)."""

    @pytest.mark.parametrize("tool_name", ["Write", "Edit"])
    @pytest.mark.parametrize(
        ("rel_path", "label"),
        [
            ("src/devbench/cli.py", "package-source-cli"),
            ("src/devbench/backlog/amendment.py", "package-source-nested"),
            ("src/devbench/config-schema.json", "package-source-schema"),
            ("tests/test_cli.py", "package-test-tree"),
            ("tests/test_plugin/test_guard_harness_write.py", "package-test-nested"),
            ("pyproject.toml", "pyproject"),
            ("uv.lock", "lockfile"),
            ("Makefile", "makefile"),
        ],
    )
    def test_protected_path_blocked(self, rel_path: str, label: str, tool_name: str) -> None:
        abs_path = str(REPO_ROOT / rel_path)
        result = _run(abs_path, tool_name=tool_name)
        assert result.returncode == 2, (
            f"[{label}/{tool_name}] guard-harness-write.sh must exit 2 for {abs_path!r}; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        assert "[HARNESS_SELF_EDIT_BLOCKED]" in result.stderr, (
            f"[{label}/{tool_name}] stderr must emit the deterministic marker; got: {result.stderr!r}"
        )


@pytest.mark.unit
class TestGuardHarnessWriteNoRoleBypass:
    """There is NO role bypass: even DEVBENCH_AGENT_ROLE=orchestrator is blocked (AC-2)."""

    @pytest.mark.parametrize("role", ["orchestrator", "executor"])
    def test_role_does_not_bypass(self, role: str) -> None:
        target = str(REPO_ROOT / "src" / "devbench" / "cli.py")
        result = _run(target, env_extra={"DEVBENCH_AGENT_ROLE": role})
        assert result.returncode == 2, (
            f"DEVBENCH_AGENT_ROLE={role} must NOT bypass guard-harness-write.sh; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        assert "[HARNESS_SELF_EDIT_BLOCKED]" in result.stderr


@pytest.mark.unit
class TestGuardHarnessWriteAllows:
    """Non-harness targets are allowed (exit 0) -- the guard is generically scoped (AC-2)."""

    def test_target_repo_source_allowed(self) -> None:
        result = _run("/workspaces/telemetry/tools-telemetry/providers/aws/kms/main.tf")
        assert result.returncode == 0, (
            f"a target-repo source file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )

    def test_unrelated_src_devbench_in_other_repo_allowed(self, tmp_path: Path) -> None:
        foreign = tmp_path / "some-other-repo" / "src" / "devbench" / "x.py"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("# foreign\n", encoding="utf-8")
        result = _run(str(foreign))
        assert result.returncode == 0, (
            f"a foreign src/devbench file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )

    def test_backlog_work_unit_allowed(self) -> None:
        result = _run("/workspaces/telemetry/some-workspace/backlog/E1-F1-S1-T1.md")
        assert result.returncode == 0, (
            f"a backlog work-unit file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )

    def test_empty_file_path_allowed(self) -> None:
        result = subprocess.run(
            ["bash", str(GUARD_SCRIPT)],
            input=json.dumps({"tool_name": "Write", "tool_input": {}}),
            capture_output=True,
            text=True,
            check=False,
            env={k: v for k, v in os.environ.items() if k != "BASH_ENV"},
        )
        assert result.returncode == 0, (
            f"a payload with no file_path must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )

    def test_devbench_docs_allowed(self) -> None:
        target = str(REPO_ROOT / "docs" / "plugin-architecture.md")
        result = _run(target)
        assert result.returncode == 0, (
            f"a devbench docs file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )
