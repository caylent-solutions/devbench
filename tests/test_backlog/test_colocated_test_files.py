"""Colocated test files must classify as tests, not production source.

``_is_test_source_path`` recognised only paths under a ``tests/`` directory.
A repository that colocates its tests beside the code it exercises -- the
layout pytest's default discovery is built around (``python_files =
test_*.py *_test.py``) -- therefore had every one of those files classified
as PRODUCTION source by Rule 14, which then demanded a test for the test:

    ERROR: production source '.../arc-runners/test_render.py' has no matching
    test in the same Manifest (expected ... tests/unit/test_test_render.py)

That demand is unsatisfiable by construction, so every affected work unit
failed validate-backlog and the whole run stopped.
"""

import pytest

from devbench.backlog.manager import BacklogManager


@pytest.mark.unit
class TestColocatedTestFiles:
    @pytest.mark.parametrize(
        "path",
        [
            "servers/hp-dl580-g7/k8s/workloads/arc-runners/test_render.py",
            "servers/hp-dl580-g7/k8s/addons/velero/test_render.py",
            "scripts/test_helpers.py",
            "packages/ilo-bridge/render_test.py",
        ],
    )
    def test_pytest_naming_convention_is_a_test(self, path):
        assert BacklogManager._is_test_source_path(path), path
        assert not BacklogManager._is_production_source(path), path

    @pytest.mark.parametrize(
        "path",
        [
            "tests/test_arc.py",
            "tests/unit/test_render.py",
            "packages/ilo-bridge/tests/test_bridge.py",
        ],
    )
    def test_tests_directory_still_recognised(self, path):
        """The pre-existing directory rule must keep working."""
        assert BacklogManager._is_test_source_path(path), path

    @pytest.mark.parametrize(
        "path",
        [
            "scripts/arc.py",
            "servers/hp-dl580-g7/k8s/addons/velero/render.py",
            "scripts/latest_test_results.py",
        ],
    )
    def test_real_production_source_is_unaffected(self, path):
        """A file merely containing 'test' in its name is still production."""
        assert not BacklogManager._is_test_source_path(path), path
