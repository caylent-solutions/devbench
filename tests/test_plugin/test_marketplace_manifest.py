"""Unit tests for plugin/devbench/.claude-plugin/plugin.json marketplace manifest.

AC-191-1: Plugin registers in Claude Code marketplace OR is installable via
local plugin path with name `devbench`.

These tests verify the manifest has the full set of metadata fields required
for Claude Code marketplace discovery: version (bumped from 0.1.0), keywords,
repository, license, and homepage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

PLUGIN_JSON_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / ".claude-plugin" / "plugin.json"

REQUIRED_FIELDS = (
    "name",
    "description",
    "version",
    "keywords",
    "repository",
    "license",
    "homepage",
)


@pytest.fixture(scope="session")
def plugin_manifest() -> dict[str, Any]:
    """Load and parse plugin.json once per test session -- shared across all test classes."""
    raw = PLUGIN_JSON_PATH.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(f"plugin.json is not valid JSON: {exc}")
    assert isinstance(data, dict), "plugin.json top-level value must be a JSON object."
    return data


@pytest.mark.unit
class TestPluginJsonExists:
    """Sanity: the manifest file must exist at the expected path."""

    def test_plugin_json_file_exists(self) -> None:
        """plugin.json must exist at plugin/devbench/.claude-plugin/plugin.json."""
        assert PLUGIN_JSON_PATH.exists(), f"plugin.json not found at expected path: {PLUGIN_JSON_PATH}"


@pytest.mark.unit
class TestPluginJsonIsValid:
    """plugin.json must be valid JSON."""

    def test_plugin_json_is_parseable(self, plugin_manifest: dict[str, Any]) -> None:
        """plugin.json must be valid, parseable JSON -- not empty or malformed."""
        assert isinstance(plugin_manifest, dict), "plugin.json top-level value must be a JSON object."


@pytest.mark.unit
class TestPluginJsonRequiredFields:
    """plugin.json must contain all required marketplace-discovery fields."""

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_required_field_present(self, plugin_manifest: dict[str, Any], field: str) -> None:
        """Each required marketplace metadata field must be present in plugin.json."""
        assert field in plugin_manifest, (
            f"plugin.json is missing required field '{field}'. Present fields: {sorted(plugin_manifest.keys())}"
        )

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_required_field_non_empty(self, plugin_manifest: dict[str, Any], field: str) -> None:
        """Each required field must have a non-empty value."""
        value = plugin_manifest.get(field)
        if isinstance(value, list):
            assert len(value) > 0, f"plugin.json field '{field}' must be a non-empty list, got: {value!r}"
        else:
            assert value, f"plugin.json field '{field}' must be a non-empty string, got: {value!r}"


@pytest.mark.unit
class TestPluginJsonName:
    """plugin.json 'name' field must equal 'devbench'."""

    def test_name_is_devbench(self, plugin_manifest: dict[str, Any]) -> None:
        """AC-191-1: plugin name must be 'devbench' for local plugin path install."""
        assert plugin_manifest["name"] == "devbench", (
            f"plugin.json 'name' must be 'devbench', got: {plugin_manifest.get('name')!r}"
        )


@pytest.mark.unit
class TestPluginJsonVersion:
    """plugin.json version must be bumped beyond 0.1.0."""

    def test_version_bumped_from_stub(self, plugin_manifest: dict[str, Any]) -> None:
        """version must be bumped from the old 3-field stub value (0.1.0)."""
        version = plugin_manifest.get("version", "")
        assert version != "0.1.0", (
            f"plugin.json version must be bumped from '0.1.0' (the pre-expansion stub). Got: {version!r}"
        )

    def test_version_is_semver_like(self, plugin_manifest: dict[str, Any]) -> None:
        """version must match a semver-like pattern (X.Y.Z)."""
        version = plugin_manifest.get("version", "")
        assert re.match(r"^\d+\.\d+\.\d+$", version), (
            f"plugin.json version must follow semantic versioning (X.Y.Z). Got: {version!r}"
        )


@pytest.mark.unit
class TestPluginJsonKeywords:
    """plugin.json 'keywords' field must be a non-empty list of strings."""

    def test_keywords_is_list(self, plugin_manifest: dict[str, Any]) -> None:
        """keywords must be a JSON array."""
        assert isinstance(plugin_manifest.get("keywords"), list), (
            f"plugin.json 'keywords' must be a list, got: {type(plugin_manifest.get('keywords'))}"
        )

    def test_keywords_contains_only_strings(self, plugin_manifest: dict[str, Any]) -> None:
        """Each keyword must be a non-empty string."""
        keywords = plugin_manifest.get("keywords", [])
        for idx, kw in enumerate(keywords):
            assert isinstance(kw, str) and kw, f"plugin.json 'keywords[{idx}]' must be a non-empty string, got: {kw!r}"

    def test_keywords_includes_devbench(self, plugin_manifest: dict[str, Any]) -> None:
        """keywords must include 'devbench' for marketplace search relevance."""
        keywords = plugin_manifest.get("keywords", [])
        assert "devbench" in keywords, f"plugin.json 'keywords' must include 'devbench'. Got: {keywords!r}"


@pytest.mark.unit
class TestPluginJsonRepository:
    """plugin.json 'repository' field must be a non-empty string (URL or shorthand)."""

    def test_repository_is_string(self, plugin_manifest: dict[str, Any]) -> None:
        """repository must be a string."""
        assert isinstance(plugin_manifest.get("repository"), str), (
            f"plugin.json 'repository' must be a string, got: {type(plugin_manifest.get('repository'))}"
        )

    def test_repository_references_caylent_solutions(self, plugin_manifest: dict[str, Any]) -> None:
        """repository must reference the canonical caylent-solutions/devbench repo."""
        repository = plugin_manifest.get("repository", "")
        assert "caylent-solutions" in repository and "devbench" in repository, (
            f"plugin.json 'repository' must reference 'caylent-solutions/devbench'. Got: {repository!r}"
        )


@pytest.mark.unit
class TestPluginJsonLicense:
    """plugin.json 'license' field must be a non-empty string."""

    def test_license_is_string(self, plugin_manifest: dict[str, Any]) -> None:
        """license must be a string."""
        assert isinstance(plugin_manifest.get("license"), str), (
            f"plugin.json 'license' must be a string, got: {type(plugin_manifest.get('license'))}"
        )


@pytest.mark.unit
class TestPluginJsonHomepage:
    """plugin.json 'homepage' field must be a non-empty string (URL)."""

    def test_homepage_is_string(self, plugin_manifest: dict[str, Any]) -> None:
        """homepage must be a string."""
        assert isinstance(plugin_manifest.get("homepage"), str), (
            f"plugin.json 'homepage' must be a string, got: {type(plugin_manifest.get('homepage'))}"
        )

    def test_homepage_is_url(self, plugin_manifest: dict[str, Any]) -> None:
        """homepage must look like a URL (starts with https://)."""
        homepage = plugin_manifest.get("homepage", "")
        assert homepage.startswith("https://"), f"plugin.json 'homepage' must be an https:// URL. Got: {homepage!r}"
