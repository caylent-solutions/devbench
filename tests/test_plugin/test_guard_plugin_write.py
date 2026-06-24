"""Guard the guards: ``guard-plugin-write.sh`` blocks self-modification.

An autonomous orchestrator session was able to edit a security guard script
(``guard-verdict-format.sh``) because no PreToolUse hook blocked Write/Edit to
the plugin's own scripts/hooks. ``guard-plugin-write.sh`` closes that gap.

This test invokes the shell hook via subprocess with crafted JSON payloads and
pins, per protected category:

  * a plugin guard-script path        -> BLOCKED (exit 2)
  * a plugin hooks.json path          -> BLOCKED (exit 2)
  * a workspace shadow-plugin path    -> BLOCKED (exit 2)
  * a .claude/settings.local.json     -> BLOCKED (exit 2)
  * the file named by $BASH_ENV       -> BLOCKED (exit 2)
  * the role bypass is REFUSED        -> still BLOCKED with
                                         DEVBENCH_AGENT_ROLE=orchestrator
  * a normal source file              -> ALLOWED (exit 0)
  * an unrelated tmp file             -> ALLOWED (exit 0)

The denial is GENERIC: the patterns carry no hardcoded workspace, backlog, or
plugin name, so the same hook protects any plugin in any workspace.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GUARD_SCRIPT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "scripts" / "guard-plugin-write.sh"


def _run(
    file_path: str, *, tool_name: str = "Write", env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the guard hook with a crafted PreToolUse payload.

    The payload mirrors the documented Write/Edit tool_input shape. A clean
    environment is used so the test never inherits an ambient BASH_ENV (which
    would otherwise add a spurious protected target).
    """
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
class TestGuardPluginWriteScriptExists:
    def test_script_present_and_executable(self) -> None:
        assert GUARD_SCRIPT.is_file(), f"guard-plugin-write.sh must exist at {GUARD_SCRIPT}"
        assert os.access(GUARD_SCRIPT, os.X_OK), "guard-plugin-write.sh must be executable"


@pytest.mark.unit
class TestGuardPluginWriteBlocks:
    """Each protected category must HARD-DENY with exit 2."""

    @pytest.mark.parametrize("tool_name", ["Write", "Edit"])
    @pytest.mark.parametrize(
        ("file_path", "label"),
        [
            (
                "/workspaces/telemetry/devbench/plugin/devbench-orchestrate/scripts/guard-verdict-format.sh",
                "plugin-script",
            ),
            (
                "/workspaces/telemetry/devbench/plugin/devbench-orchestrate/hooks/hooks.json",
                "hooks-json",
            ),
            (
                "plugin/devbench-orchestrate/scripts/guard-bash.sh",
                "plugin-script-relative",
            ),
            (
                "/workspaces/telemetry/tools-telemetry-backlog/.devbench/plugin-shadow/scripts/x.sh",
                "plugin-shadow",
            ),
            (
                "/workspaces/telemetry/devbench/.claude/settings.local.json",
                "claude-settings-local",
            ),
            (
                "/home/user/project/.claude/settings.json",
                "claude-settings",
            ),
        ],
    )
    def test_protected_path_blocked(self, file_path: str, label: str, tool_name: str) -> None:
        result = _run(file_path, tool_name=tool_name)
        assert result.returncode == 2, (
            f"[{label}/{tool_name}] guard-plugin-write.sh must exit 2 for {file_path!r}; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        assert "guard-plugin-write: BLOCKED" in result.stderr, (
            f"[{label}/{tool_name}] stderr must name the rule; got: {result.stderr!r}"
        )

    def test_bash_env_target_blocked(self, tmp_path: Path) -> None:
        """When BASH_ENV names a file, Write/Edit to that exact file is blocked
        (the generic env-injection vector).
        """
        injected = tmp_path / "evil-bashenv.sh"
        injected.write_text("# attacker-controlled bash startup file\n", encoding="utf-8")
        result = _run(str(injected), env_extra={"BASH_ENV": str(injected)})
        assert result.returncode == 2, (
            f"guard-plugin-write.sh must exit 2 for the $BASH_ENV target {injected}; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        assert "bash-env-target" in result.stderr, f"stderr must name the bash-env-target rule; got: {result.stderr!r}"

    def test_bash_env_unset_does_not_block_arbitrary_file(self, tmp_path: Path) -> None:
        """The BASH_ENV category must only fire when BASH_ENV is set; otherwise a
        plain file is allowed (guards against a false-positive that would block
        every Write when BASH_ENV is empty).
        """
        plain = tmp_path / "regular.txt"
        result = _run(str(plain))
        assert result.returncode == 0, (
            f"with BASH_ENV unset, a plain file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )


@pytest.mark.unit
class TestGuardPluginWriteNoRoleBypass:
    """There is NO role bypass: even DEVBENCH_AGENT_ROLE=orchestrator is blocked."""

    @pytest.mark.parametrize("tool_name", ["Write", "Edit"])
    def test_orchestrator_role_still_blocked(self, tool_name: str) -> None:
        protected = "/workspaces/telemetry/devbench/plugin/devbench-orchestrate/scripts/guard-verdict-format.sh"
        result = _run(
            protected,
            tool_name=tool_name,
            env_extra={"DEVBENCH_AGENT_ROLE": "orchestrator"},
        )
        assert result.returncode == 2, (
            f"DEVBENCH_AGENT_ROLE=orchestrator must NOT bypass guard-plugin-write.sh; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        assert "guard-plugin-write: BLOCKED" in result.stderr


@pytest.mark.unit
class TestGuardPluginWriteAllows:
    """Non-protected targets must be allowed (exit 0)."""

    def test_normal_source_file_allowed(self) -> None:
        result = _run("/repo/providers/aws/x/main.tf")
        assert result.returncode == 0, (
            f"a normal source file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )

    def test_tmp_file_allowed(self, tmp_path: Path) -> None:
        target = tmp_path / "scratch" / "notes.md"
        result = _run(str(target))
        assert result.returncode == 0, (
            f"a tmp file must be allowed; got rc={result.returncode}, stderr={result.stderr!r}"
        )

    def test_empty_file_path_allowed(self) -> None:
        """A payload with no file_path must not crash the guard (exit 0)."""
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
