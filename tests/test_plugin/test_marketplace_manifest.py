"""Unit tests for both plugin.json manifests after the issue #224 split.

After the split, this repo ships TWO marketplaces from sibling directory
roots:

- ``plugin/devbench-orchestrate/`` (the orchestrate plugin)
- ``plugin-authoring/devbench-authoring/`` (the authoring plugin)

These tests assert that each manifest has the full set of metadata
fields required for Claude Code marketplace discovery: version (semver),
keywords, repository, license, and homepage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

ORCHESTRATE_PLUGIN_JSON = REPO_ROOT / "plugin" / "devbench-orchestrate" / ".claude-plugin" / "plugin.json"
ORCHESTRATE_MARKETPLACE_JSON = REPO_ROOT / "plugin" / ".claude-plugin" / "marketplace.json"
AUTHORING_PLUGIN_JSON = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / ".claude-plugin" / "plugin.json"
AUTHORING_MARKETPLACE_JSON = REPO_ROOT / "plugin-authoring" / ".claude-plugin" / "marketplace.json"

REQUIRED_PLUGIN_FIELDS = (
    "name",
    "description",
    "version",
    "keywords",
    "repository",
    "license",
    "homepage",
)


def _load_json(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{path} is not valid JSON: {exc}")
    assert isinstance(data, dict), f"{path} top-level value must be a JSON object."
    return data


@pytest.fixture(scope="session")
def orchestrate_plugin_manifest() -> dict[str, Any]:
    return _load_json(ORCHESTRATE_PLUGIN_JSON)


@pytest.fixture(scope="session")
def authoring_plugin_manifest() -> dict[str, Any]:
    return _load_json(AUTHORING_PLUGIN_JSON)


@pytest.fixture(scope="session")
def orchestrate_marketplace_manifest() -> dict[str, Any]:
    return _load_json(ORCHESTRATE_MARKETPLACE_JSON)


@pytest.fixture(scope="session")
def authoring_marketplace_manifest() -> dict[str, Any]:
    return _load_json(AUTHORING_MARKETPLACE_JSON)


@pytest.mark.unit
class TestBothPluginJsonFilesExist:
    """Issue #224 AC-2 / AC-8: both plugin.json manifests must exist."""

    def test_orchestrate_plugin_json_exists(self) -> None:
        assert ORCHESTRATE_PLUGIN_JSON.exists(), f"orchestrate plugin.json missing at {ORCHESTRATE_PLUGIN_JSON}"

    def test_authoring_plugin_json_exists(self) -> None:
        assert AUTHORING_PLUGIN_JSON.exists(), f"authoring plugin.json missing at {AUTHORING_PLUGIN_JSON}"

    def test_orchestrate_marketplace_json_exists(self) -> None:
        assert ORCHESTRATE_MARKETPLACE_JSON.exists(), (
            f"orchestrate marketplace.json missing at {ORCHESTRATE_MARKETPLACE_JSON}"
        )

    def test_authoring_marketplace_json_exists(self) -> None:
        assert AUTHORING_MARKETPLACE_JSON.exists(), (
            f"authoring marketplace.json missing at {AUTHORING_MARKETPLACE_JSON}"
        )


@pytest.mark.unit
class TestOrchestratePluginJsonShape:
    """Orchestrate plugin.json content (issue #224)."""

    @pytest.mark.parametrize("field", REQUIRED_PLUGIN_FIELDS)
    def test_required_field_present(self, orchestrate_plugin_manifest: dict[str, Any], field: str) -> None:
        assert field in orchestrate_plugin_manifest, (
            f"orchestrate plugin.json missing required field {field!r}; "
            f"present fields: {sorted(orchestrate_plugin_manifest.keys())}"
        )

    def test_name_is_devbench_orchestrate(self, orchestrate_plugin_manifest: dict[str, Any]) -> None:
        assert orchestrate_plugin_manifest["name"] == "devbench-orchestrate", (
            f"orchestrate plugin.json name must be 'devbench-orchestrate' "
            f"(issue #224 AC-2); got {orchestrate_plugin_manifest['name']!r}"
        )

    def test_version_is_0_4_0_or_higher(self, orchestrate_plugin_manifest: dict[str, Any]) -> None:
        version = orchestrate_plugin_manifest["version"]
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"orchestrate plugin.json version must be semver; got {version!r}"
        major, minor, _ = (int(part) for part in version.split("."))
        assert (major, minor) >= (0, 4), (
            f"orchestrate plugin.json version must be >= 0.4.0 (issue #224 split bump); got {version!r}"
        )

    def test_repository_references_caylent_solutions(self, orchestrate_plugin_manifest: dict[str, Any]) -> None:
        repo = orchestrate_plugin_manifest["repository"]
        assert "caylent-solutions" in repo and "devbench" in repo, (
            f"orchestrate plugin.json repository must reference caylent-solutions/devbench; got {repo!r}"
        )

    def test_homepage_is_https(self, orchestrate_plugin_manifest: dict[str, Any]) -> None:
        assert orchestrate_plugin_manifest["homepage"].startswith("https://"), (
            f"orchestrate plugin.json homepage must be https://; got {orchestrate_plugin_manifest['homepage']!r}"
        )


@pytest.mark.unit
class TestAuthoringPluginJsonShape:
    """Authoring plugin.json content (issue #224)."""

    @pytest.mark.parametrize("field", REQUIRED_PLUGIN_FIELDS)
    def test_required_field_present(self, authoring_plugin_manifest: dict[str, Any], field: str) -> None:
        assert field in authoring_plugin_manifest, (
            f"authoring plugin.json missing required field {field!r}; "
            f"present fields: {sorted(authoring_plugin_manifest.keys())}"
        )

    def test_name_is_devbench_authoring(self, authoring_plugin_manifest: dict[str, Any]) -> None:
        assert authoring_plugin_manifest["name"] == "devbench-authoring", (
            f"authoring plugin.json name must be 'devbench-authoring' "
            f"(issue #224 AC-2); got {authoring_plugin_manifest['name']!r}"
        )

    def test_version_is_semver(self, authoring_plugin_manifest: dict[str, Any]) -> None:
        version = authoring_plugin_manifest["version"]
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"authoring plugin.json version must be semver; got {version!r}"


@pytest.mark.unit
class TestOrchestrateMarketplaceManifest:
    """Orchestrate marketplace lists exactly one plugin: devbench-orchestrate (issue #224 AC-2)."""

    def test_marketplace_lists_exactly_one_plugin(self, orchestrate_marketplace_manifest: dict[str, Any]) -> None:
        plugins = orchestrate_marketplace_manifest.get("plugins", [])
        assert isinstance(plugins, list)
        assert len(plugins) == 1, (
            f"orchestrate marketplace must list exactly one plugin (issue #224 AC-2); got {len(plugins)}"
        )

    def test_plugin_entry_is_devbench_orchestrate(self, orchestrate_marketplace_manifest: dict[str, Any]) -> None:
        plugin = orchestrate_marketplace_manifest["plugins"][0]
        assert plugin["name"] == "devbench-orchestrate"
        assert plugin["source"].rstrip("/") == "./devbench-orchestrate"


@pytest.mark.unit
class TestAuthoringMarketplaceManifest:
    """Authoring marketplace lists exactly one plugin: devbench-authoring (issue #224 AC-2)."""

    def test_marketplace_lists_exactly_one_plugin(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        plugins = authoring_marketplace_manifest.get("plugins", [])
        assert isinstance(plugins, list)
        assert len(plugins) == 1, (
            f"authoring marketplace must list exactly one plugin (issue #224 AC-2); got {len(plugins)}"
        )

    def test_plugin_entry_is_devbench_authoring(self, authoring_marketplace_manifest: dict[str, Any]) -> None:
        plugin = authoring_marketplace_manifest["plugins"][0]
        assert plugin["name"] == "devbench-authoring"
        assert plugin["source"].rstrip("/") == "./devbench-authoring"
