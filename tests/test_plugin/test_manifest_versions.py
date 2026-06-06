"""Tests asserting version agreement across all plugin.json and marketplace manifest files.

The release sync rule (E9.F1.S1, AC-E9-1) requires:
- Every plugin.json version equals its marketplace-entry version.
- All plugin.json and marketplace metadata versions equal the unified release version.
- The descriptions name the new capabilities introduced in E9:
  quota wait-and-resume, operator-mode amendment, done-integrity guards, backlog-assistant.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

RELEASE_VERSION = "0.4.0"

ORCHESTRATE_PLUGIN_JSON = REPO_ROOT / "plugin" / "devbench-orchestrate" / ".claude-plugin" / "plugin.json"
ORCHESTRATE_MARKETPLACE_JSON = REPO_ROOT / "plugin" / ".claude-plugin" / "marketplace.json"
AUTHORING_PLUGIN_JSON = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / ".claude-plugin" / "plugin.json"
AUTHORING_MARKETPLACE_JSON = REPO_ROOT / "plugin-authoring" / ".claude-plugin" / "marketplace.json"
BACKLOG_ASSISTANT_PLUGIN_JSON = (
    REPO_ROOT / "plugin-authoring" / "devbench-backlog-assistant" / ".claude-plugin" / "plugin.json"
)

_ALL_PLUGIN_JSONS = [
    ("devbench-orchestrate", ORCHESTRATE_PLUGIN_JSON),
    ("devbench-authoring", AUTHORING_PLUGIN_JSON),
    ("devbench-backlog-assistant", BACKLOG_ASSISTANT_PLUGIN_JSON),
]

_NEW_CAPABILITY_KEYWORDS = [
    "quota wait-and-resume",
    "operator-mode amendment",
    "done-integrity guards",
    "backlog-assistant",
]


def _load_json(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{path} is not valid JSON: {exc}")
    assert isinstance(data, dict), f"{path} top-level value must be a JSON object."
    return data


def _marketplace_entry(marketplace: dict[str, Any], plugin_name: str) -> dict[str, Any]:
    plugins = marketplace.get("plugins", [])
    matches = [p for p in plugins if p.get("name") == plugin_name]
    assert matches, (
        f"plugin {plugin_name!r} not found in marketplace; present names: {[p.get('name') for p in plugins]}"
    )
    return matches[0]


@pytest.fixture(scope="session")
def orchestrate_plugin() -> dict[str, Any]:
    return _load_json(ORCHESTRATE_PLUGIN_JSON)


@pytest.fixture(scope="session")
def authoring_plugin() -> dict[str, Any]:
    return _load_json(AUTHORING_PLUGIN_JSON)


@pytest.fixture(scope="session")
def backlog_assistant_plugin() -> dict[str, Any]:
    return _load_json(BACKLOG_ASSISTANT_PLUGIN_JSON)


@pytest.fixture(scope="session")
def orchestrate_marketplace() -> dict[str, Any]:
    return _load_json(ORCHESTRATE_MARKETPLACE_JSON)


@pytest.fixture(scope="session")
def authoring_marketplace() -> dict[str, Any]:
    return _load_json(AUTHORING_MARKETPLACE_JSON)


@pytest.mark.unit
class TestAllFilesExist:
    """All five manifest files must exist on disk."""

    @pytest.mark.parametrize(
        "path",
        [
            ORCHESTRATE_PLUGIN_JSON,
            AUTHORING_PLUGIN_JSON,
            BACKLOG_ASSISTANT_PLUGIN_JSON,
            ORCHESTRATE_MARKETPLACE_JSON,
            AUTHORING_MARKETPLACE_JSON,
        ],
    )
    def test_manifest_file_exists(self, path: Path) -> None:
        assert path.exists(), f"Manifest file missing: {path}"


@pytest.mark.unit
class TestPluginVersionIsReleaseVersion:
    """Each plugin.json version must equal the unified release version."""

    @pytest.mark.parametrize(
        "name,path",
        _ALL_PLUGIN_JSONS,
    )
    def test_plugin_json_version(self, name: str, path: Path) -> None:
        data = _load_json(path)
        version = data.get("version", "")
        assert re.match(r"^\d+\.\d+\.\d+$", version), f"{name} plugin.json version must be semver; got {version!r}"
        assert version == RELEASE_VERSION, (
            f"{name} plugin.json version must equal release version {RELEASE_VERSION!r}; got {version!r}"
        )


@pytest.mark.unit
class TestMarketplaceMetadataVersionIsReleaseVersion:
    """Both marketplace.json metadata.version fields must equal the unified release version."""

    def test_orchestrate_marketplace_metadata_version(self, orchestrate_marketplace: dict[str, Any]) -> None:
        version = orchestrate_marketplace.get("metadata", {}).get("version", "")
        assert version == RELEASE_VERSION, (
            f"orchestrate marketplace metadata.version must equal {RELEASE_VERSION!r}; got {version!r}"
        )

    def test_authoring_marketplace_metadata_version(self, authoring_marketplace: dict[str, Any]) -> None:
        version = authoring_marketplace.get("metadata", {}).get("version", "")
        assert version == RELEASE_VERSION, (
            f"authoring marketplace metadata.version must equal {RELEASE_VERSION!r}; got {version!r}"
        )


@pytest.mark.unit
class TestPluginVersionEqualsMarketplaceEntryVersion:
    """Each plugin.json version must equal its corresponding marketplace entry version."""

    def test_orchestrate_version_agreement(
        self,
        orchestrate_plugin: dict[str, Any],
        orchestrate_marketplace: dict[str, Any],
    ) -> None:
        plugin_ver = orchestrate_plugin.get("version", "")
        entry = _marketplace_entry(orchestrate_marketplace, "devbench-orchestrate")
        marketplace_ver = entry.get("version", "")
        assert plugin_ver == marketplace_ver, (
            f"devbench-orchestrate version mismatch: plugin.json={plugin_ver!r}, marketplace entry={marketplace_ver!r}"
        )

    def test_authoring_version_agreement(
        self,
        authoring_plugin: dict[str, Any],
        authoring_marketplace: dict[str, Any],
    ) -> None:
        plugin_ver = authoring_plugin.get("version", "")
        entry = _marketplace_entry(authoring_marketplace, "devbench-authoring")
        marketplace_ver = entry.get("version", "")
        assert plugin_ver == marketplace_ver, (
            f"devbench-authoring version mismatch: plugin.json={plugin_ver!r}, marketplace entry={marketplace_ver!r}"
        )

    def test_backlog_assistant_version_agreement(
        self,
        backlog_assistant_plugin: dict[str, Any],
        authoring_marketplace: dict[str, Any],
    ) -> None:
        plugin_ver = backlog_assistant_plugin.get("version", "")
        entry = _marketplace_entry(authoring_marketplace, "devbench-backlog-assistant")
        marketplace_ver = entry.get("version", "")
        assert plugin_ver == marketplace_ver, (
            f"devbench-backlog-assistant version mismatch: "
            f"plugin.json={plugin_ver!r}, marketplace entry={marketplace_ver!r}"
        )


@pytest.mark.unit
class TestBacklogAssistantRegistered:
    """The backlog-assistant plugin must be registered in the authoring marketplace."""

    def test_backlog_assistant_in_authoring_marketplace(self, authoring_marketplace: dict[str, Any]) -> None:
        names = [p.get("name") for p in authoring_marketplace.get("plugins", [])]
        assert "devbench-backlog-assistant" in names, (
            f"devbench-backlog-assistant must be registered in authoring marketplace; present: {names}"
        )

    def test_backlog_assistant_plugin_json_exists(self) -> None:
        assert BACKLOG_ASSISTANT_PLUGIN_JSON.exists(), (
            f"devbench-backlog-assistant plugin.json missing at {BACKLOG_ASSISTANT_PLUGIN_JSON}"
        )


@pytest.mark.unit
class TestDescriptionsNameNewCapabilities:
    """Descriptions must name the four new E9 capabilities."""

    @pytest.mark.parametrize("keyword", _NEW_CAPABILITY_KEYWORDS)
    def test_orchestrate_plugin_description_names_capability(
        self, orchestrate_plugin: dict[str, Any], keyword: str
    ) -> None:
        desc = orchestrate_plugin.get("description", "")
        assert keyword.lower() in desc.lower(), (
            f"devbench-orchestrate plugin.json description must mention {keyword!r}; got: {desc!r}"
        )

    @pytest.mark.parametrize("keyword", ["quota wait-and-resume", "done-integrity guards"])
    def test_orchestrate_marketplace_entry_description_names_capability(
        self, orchestrate_marketplace: dict[str, Any], keyword: str
    ) -> None:
        entry = _marketplace_entry(orchestrate_marketplace, "devbench-orchestrate")
        desc = entry.get("description", "")
        assert keyword.lower() in desc.lower(), (
            f"orchestrate marketplace entry description must mention {keyword!r}; got: {desc!r}"
        )

    @pytest.mark.parametrize("keyword", ["operator-mode amendment"])
    def test_authoring_plugin_description_names_capability(
        self, authoring_plugin: dict[str, Any], keyword: str
    ) -> None:
        desc = authoring_plugin.get("description", "")
        assert keyword.lower() in desc.lower(), (
            f"devbench-authoring plugin.json description must mention {keyword!r}; got: {desc!r}"
        )

    @pytest.mark.parametrize("keyword", ["backlog-assistant"])
    def test_authoring_marketplace_metadata_description_names_capability(
        self, authoring_marketplace: dict[str, Any], keyword: str
    ) -> None:
        desc = authoring_marketplace.get("metadata", {}).get("description", "")
        assert keyword.lower() in desc.lower(), (
            f"authoring marketplace metadata description must mention {keyword!r}; got: {desc!r}"
        )
