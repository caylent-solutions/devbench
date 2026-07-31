"""Regression tests for test-suite workspace isolation (issue #292).

devbench is developed with devbench, so the executor runs this suite from
inside a live workspace with ``DEVBENCH_WORKSPACE_ROOT`` and
``DEVBENCH_LOG_FILE`` exported. ``conftest.py`` previously used
``os.environ.setdefault`` for both, which means it INHERITED those values and
tests resolved live paths.

The observed damage: fixture work-unit state written into the real
``.devbench/ci-failures/`` and ``.devbench/pr-bot-feedback/`` under IDs that
exist only in ``tests/``, and fabricated lifecycle records appended to the
live orchestrator log -- ``[ORCHESTRATOR_TERMINAL_EXIT]``,
``[QUOTA_WAITING]``, ``[ORCHESTRATOR_AUTO_RESTART]``, ``Merged PR #42`` --
for events that never occurred. Those are precisely the markers the reporting
layer parses, so a test run could drive an operator's status and report
output.

These tests pin the isolation itself, not a symptom of it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ISOLATION_VARS = ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_PROJECT_ROOT", "DEVBENCH_LOG_FILE")


@pytest.mark.unit
class TestConftestForcesAnIsolatedWorkspace:
    @pytest.mark.parametrize("var", _ISOLATION_VARS)
    def test_isolation_var_points_into_a_temporary_directory(self, var: str) -> None:
        """Every path devbench writes through must live under the OS temp root."""
        value = os.environ.get(var)
        assert value, f"{var} must be set by conftest"
        temp_root = Path(tempfile.gettempdir()).resolve()
        assert Path(value).resolve().is_relative_to(temp_root), (
            f"{var}={value!r} resolves outside {temp_root}; a test run could write to a real workspace"
        )

    def test_workspace_root_is_a_devbench_test_workspace(self) -> None:
        root = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"])
        assert root.name.startswith("devbench-test-workspace-"), (
            f"DEVBENCH_WORKSPACE_ROOT={root} is not the per-run isolated workspace"
        )

    def test_log_file_is_inside_the_isolated_workspace(self) -> None:
        root = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]).resolve()
        log = Path(os.environ["DEVBENCH_LOG_FILE"]).resolve()
        assert log.is_relative_to(root), f"log {log} escapes the isolated workspace {root}"

    def test_config_path_is_the_test_fixture_not_an_inherited_one(self) -> None:
        config = Path(os.environ["DEVBENCH_CONFIG_PATH"]).resolve()
        assert config.name == "test_devbench.yaml"
        assert config.is_relative_to(Path(__file__).parent.resolve())

    def test_session_name_is_not_inherited(self) -> None:
        """A session name routes logs into a per-session tree under the workspace root."""
        assert "DEVBENCH_SESSION_NAME" not in os.environ


@pytest.mark.unit
class TestAmbientEnvironmentCannotSteerTheSuite:
    """The core guarantee: a hostile ambient env must not be honoured."""

    def test_inherited_workspace_root_is_overridden_not_adopted(self, tmp_path: Path) -> None:
        """Re-run the isolation assertions in a child pytest with a decoy workspace exported.

        This is the exact failure mode: the value was exported, ``setdefault``
        saw it already present, and every test then resolved paths beneath it.
        The child runs the sibling class in this module, so it loads the real
        ``tests/conftest.py`` rather than a synthetic probe that pytest would
        collect without it.
        """
        decoy = tmp_path / "live-workspace"
        (decoy / ".devbench").mkdir(parents=True)
        decoy_log = decoy / "orchestrator.log"
        decoy_log.write_text("pre-existing operator log line\n", encoding="utf-8")

        env = dict(os.environ)
        env["DEVBENCH_WORKSPACE_ROOT"] = str(decoy)
        env["DEVBENCH_PROJECT_ROOT"] = str(decoy)
        env["DEVBENCH_LOG_FILE"] = str(decoy_log)
        env["DEVBENCH_SESSION_NAME"] = "decoy-session"
        env.pop("DEVBENCH_ISOLATION_CHILD", None)
        env["DEVBENCH_ISOLATION_CHILD"] = "1"
        repo_root = Path(__file__).resolve().parents[1]
        target = f"tests/{Path(__file__).name}::TestConftestForcesAnIsolatedWorkspace"

        result = subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

        assert result.returncode == 0, "conftest adopted the ambient workspace root:\n" + result.stdout + result.stderr
        # The decoy must be untouched: identical content, no new files.
        assert decoy_log.read_text(encoding="utf-8") == "pre-existing operator log line\n"
        assert list((decoy / ".devbench").iterdir()) == []
