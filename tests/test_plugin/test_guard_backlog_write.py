"""db-303 (E12-F1-S2-T1): guard-work-unit-write.sh's BACKLOG.md carve-out.

spec Section 0 items 4 and 13, Section 4.A, Section 4 FR-16: a raw
Edit/Write to BACKLOG.md bypassed flock_backlog, the Status-Summary
rollup, and the audit trail. The guard's BACKLOG.md carve-out (previously
an unconditional ``exit 0``) now blocks by default and only allows the
edit through when the operator sets ``DEVBENCH_ALLOW_BACKLOG_EDIT=1``
(modeled on ``DEVBENCH_ALLOW_DESTRUCTIVE_GIT=1``). Managed verbs
(``devbench remove``/``set-status``/``decline``/...) write BACKLOG.md via
Python I/O, not the Edit/Write tools, so they never reach this hook and
are unaffected.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "guard-work-unit-write.sh"
)


def _run_hook(payload: dict, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin.

    Strips ``DEVBENCH_WORKSPACE_ROOT`` / ``DEVBENCH_LOG_FILE`` /
    ``DEVBENCH_ALLOW_BACKLOG_EDIT`` from the ambient environment first so
    the executor's own live workspace state can never leak into a guard
    invocation under test (mirrors ``tests/unit/test_guard_work_unit_write.py``).
    """
    stripped = {"DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE", "DEVBENCH_ALLOW_BACKLOG_EDIT"}
    env = {k: v for k, v in os.environ.items() if k not in stripped}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _make_write_payload(file_path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path}}


def _make_edit_payload(file_path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": file_path}}


@pytest.mark.unit
class TestGuardBlocksBacklogMdEdit:
    """AC-E12-F1-S2-T1-3 / spec AC-37: a raw Edit/Write to BACKLOG.md is
    blocked with exit 2 and the verbatim FR-16 guard message.
    """

    EXPECTED_MESSAGE = (
        "guard-backlog: blocked Edit/Write to BACKLOG.md. Use a managed verb "
        "(devbench remove/set-status/decline/...) so the flock, Status-Summary rollup, "
        "and audit trail are preserved. Operator override: set DEVBENCH_ALLOW_BACKLOG_EDIT=1 "
        "to hand-repair."
    )

    @pytest.mark.parametrize(
        "file_path",
        [
            "BACKLOG.md",
            "/workspace/BACKLOG.md",
            "/home/user/project/BACKLOG.md",
        ],
    )
    def test_guard_blocks_backlog_md_edit(self, file_path: str) -> None:
        result = _run_hook(_make_write_payload(file_path))
        assert result.returncode == 2, (
            f"Expected exit 2 for Write to '{file_path}', got {result.returncode}. stderr: {result.stderr}"
        )
        assert self.EXPECTED_MESSAGE in result.stderr

    def test_guard_blocks_backlog_md_via_edit_tool(self) -> None:
        result = _run_hook(_make_edit_payload("BACKLOG.md"))
        assert result.returncode == 2
        assert self.EXPECTED_MESSAGE in result.stderr

    def test_nested_backlog_md_path_is_not_blocked_by_this_rule(self) -> None:
        """A work-unit file that merely happens to end in the substring
        'BACKLOG.md' is never produced by real callers, but the important
        boundary here is that a *sibling* file named similarly to BACKLOG.md
        under backlog/ is still governed by the work-unit .md block below,
        not this rule -- both rules land on exit 2, so this asserts the
        (distinct) work-unit-file message fires instead of the BACKLOG.md one.
        """
        result = _run_hook(_make_write_payload("backlog/E1-F1-S1-T1.md"))
        assert result.returncode == 2
        assert self.EXPECTED_MESSAGE not in result.stderr
        assert "guard-work-unit-write: blocked write to work unit file:" in result.stderr


@pytest.mark.unit
class TestGuardBacklogMdEditOverride:
    """AC-E12-F1-S2-T1-3 / spec AC-37: DEVBENCH_ALLOW_BACKLOG_EDIT=1 is the
    only escape hatch and it is not a code-level suppression of the hook.
    """

    def test_guard_backlog_md_edit_with_override(self) -> None:
        result = _run_hook(_make_write_payload("BACKLOG.md"), extra_env={"DEVBENCH_ALLOW_BACKLOG_EDIT": "1"})
        assert result.returncode == 0, (
            f"Expected exit 0 with DEVBENCH_ALLOW_BACKLOG_EDIT=1, got {result.returncode}. stderr: {result.stderr}"
        )

    def test_override_edit_tool_also_allowed(self) -> None:
        result = _run_hook(_make_edit_payload("/workspace/BACKLOG.md"), extra_env={"DEVBENCH_ALLOW_BACKLOG_EDIT": "1"})
        assert result.returncode == 0

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_override_requires_exact_value_1(self, value: str) -> None:
        """Any value other than the literal string '1' still blocks -- no
        truthy-string fallback logic (CLAUDE.md fail-fast: no silent
        acceptance of near-miss override values).
        """
        result = _run_hook(_make_write_payload("BACKLOG.md"), extra_env={"DEVBENCH_ALLOW_BACKLOG_EDIT": value})
        assert result.returncode == 2

    def test_override_does_not_bypass_work_unit_md_block(self) -> None:
        """DEVBENCH_ALLOW_BACKLOG_EDIT=1 is scoped to BACKLOG.md only; it must
        not become a general work-unit-file bypass.
        """
        result = _run_hook(
            _make_write_payload("backlog/E1-F1-S1-T1.md"),
            extra_env={"DEVBENCH_ALLOW_BACKLOG_EDIT": "1"},
        )
        assert result.returncode == 2
        assert "guard-work-unit-write: blocked write to work unit file:" in result.stderr


@pytest.mark.unit
class TestGuardBacklogWriteRegressionUnchanged:
    """spec Section 10 FR-16 regression (AC-E12-F1-S2-T1-5): managed writes
    are not hook-blocked, work-unit .md edits are still blocked, and rule 10
    (em-dash) / rule 11 (checkout-prefix) are unchanged by the BACKLOG.md flip.
    """

    def test_managed_writes_outside_backlog_are_not_hook_blocked(self, tmp_path: Path) -> None:
        """A managed verb like ``devbench remove`` writes BACKLOG.md via
        Python I/O directly (never through the Write/Edit tools), so it never
        invokes this PreToolUse hook at all. What the hook must still allow
        unblocked is every ordinary source-file write outside backlog/.
        """
        target = tmp_path / "src" / "devbench" / "cli.py"
        result = _run_hook(_make_write_payload(str(target)))
        assert result.returncode == 0

    def test_work_unit_md_edit_still_blocked(self) -> None:
        result = _run_hook(_make_write_payload("backlog/E12-F1-S2-T1.md"))
        assert result.returncode == 2
        assert "guard-work-unit-write: blocked write to work unit file:" in result.stderr

    def test_rule_10_em_dash_still_enforced(self) -> None:
        content = "## Changes Manifest\n| `src/foo—bar.py` | fix |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "backlog/E1-F1-S1-T1.md", "content": content},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "rule 10" in result.stderr

    def test_rule_11_checkout_prefix_still_enforced(self, tmp_path: Path) -> None:
        yaml_content = "repos:\n  org/kanon:\n    checkout_directory: kanon\n"
        config_dir = tmp_path / "backlog" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "devbench.yaml").write_text(yaml_content)

        content = "## Changes Manifest\n| `kanon/src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "backlog/E1-F1-S1-T2.md", "content": content},
        }
        result = _run_hook(payload, extra_env={"DEVBENCH_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2
        assert "rule 11" in result.stderr
