"""Issue #224 AC-11: ``guard-work-unit-write.sh`` stderr format is pinned.

The orchestrate plugin's ``guard-work-unit-write.sh`` hook fires on any
Write / Edit to ``backlog/**/*.md``.  Its stderr is operator-facing and
integration tests across the project match on substrings of this format,
so any drift would silently break downstream matching.

This test invokes the script with a fixture stdin payload and asserts
the exact stderr captured from the pre-split baseline.  The shell guard
ships in ``plugin/devbench-orchestrate/scripts/`` post-split (issue #224);
the script body itself is byte-for-byte identical to the pre-split version.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
GUARD_SCRIPT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "scripts" / "guard-work-unit-write.sh"

EXPECTED_STDERR_LINES = (
    "guard-work-unit-write: blocked write to work unit file: ",
    "Fix: work unit .md files under backlog/ are managed exclusively by the orchestrate skill.",
    "Executors must not modify work unit files directly.",
    "(Issue #160: set DEVBENCH_AGENT_ROLE=orchestrator in the calling env to bypass",
    "for orchestrator-tier corrective edits.)",
)


@pytest.mark.unit
class TestWorkUnitWriteBlockMessage:
    """Issue #224 AC-11: the guard's stderr format is contract.  Any
    deliberate change must update this test in the same commit.
    """

    def test_blocks_with_exact_message(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        payload = '{"tool_name":"Write","tool_input":{"file_path":"' + str(fake_path) + '"}}'
        result = subprocess.run(
            ["bash", str(GUARD_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2, (
            f"guard-work-unit-write.sh must exit 2 on a backlog/*.md write; "
            f"got rc={result.returncode}, stderr={result.stderr!r}"
        )
        stderr = result.stderr
        for expected_line in EXPECTED_STDERR_LINES:
            assert expected_line in stderr, (
                f"guard-work-unit-write.sh stderr must contain: {expected_line!r}; got full stderr:\n{stderr}"
            )

    def test_no_bypass_env_var_renamed(self) -> None:
        """The bypass env var ``DEVBENCH_AGENT_ROLE=orchestrator`` is part
        of the operator-facing contract.  If the env var name is renamed
        in the script, this assertion fails and the operator-facing docs /
        integration tests get updated in the same change.
        """
        body = GUARD_SCRIPT.read_text(encoding="utf-8")
        assert "DEVBENCH_AGENT_ROLE" in body, (
            "guard-work-unit-write.sh must still honour DEVBENCH_AGENT_ROLE bypass env var (issue #160)."
        )
