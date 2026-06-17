"""Tests for orchestrate.presync_* config loading in devbench.config_loader.

TDI #016: pre-sync (warm-up) of each configured target repo environment at
orchestrator start. Covers the OrchestrateConfig dataclass defaults, schema
acceptance + parser population from YAML, and fail-fast validation of malformed
values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.config_loader import (
    OrchestrateConfig,
    _parse_orchestrate_config,
    _parse_presync_command,
    load_runtime_config,
)


def _write_minimal_config(path: Path, extra: str = "") -> None:
    """Write a minimal valid devbench.yaml to *path*."""
    content = "repos:\n  org/repo:\n    default_branch: main\n"
    if extra:
        content += extra
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# OrchestrateConfig dataclass defaults (None -> resolver falls through)
# ---------------------------------------------------------------------------


class TestOrchestrateConfigPresyncDefaults:
    def test_presync_fields_default_to_none(self) -> None:
        cfg = OrchestrateConfig()
        assert cfg.presync_environment is None
        assert cfg.presync_command is None
        assert cfg.presync_timeout_seconds is None


# ---------------------------------------------------------------------------
# load_runtime_config: presync_* from the orchestrate: section (schema-valid)
# ---------------------------------------------------------------------------


class TestLoadRuntimeConfigPresync:
    def test_absent_keys_stay_none(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_file)
        cfg = load_runtime_config(cfg_file, {})
        assert cfg.orchestrate.presync_environment is None
        assert cfg.orchestrate.presync_command is None
        assert cfg.orchestrate.presync_timeout_seconds is None

    def test_values_loaded_from_yaml(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(
            cfg_file,
            "orchestrate:\n"
            "  presync_environment: false\n"
            "  presync_command:\n"
            "    - uv\n"
            "    - sync\n"
            "    - --frozen\n"
            "  presync_timeout_seconds: 1800\n",
        )
        cfg = load_runtime_config(cfg_file, {})
        assert cfg.orchestrate.presync_environment is False
        assert cfg.orchestrate.presync_command == ["uv", "sync", "--frozen"]
        assert cfg.orchestrate.presync_timeout_seconds == 1800


# ---------------------------------------------------------------------------
# Fail-fast: malformed values raise ValueError
# ---------------------------------------------------------------------------


class TestPresyncFailFast:
    @pytest.mark.parametrize("bad_value", [0, -1])
    def test_non_positive_timeout_raises(self, tmp_path: Path, bad_value: int) -> None:
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_file, f"orchestrate:\n  presync_timeout_seconds: {bad_value}\n")
        # Fail-fast either at the schema layer (minimum: 1) or the parser layer
        # (must be >= 1) -- both reject a non-positive timeout with a ValueError
        # naming the field.
        with pytest.raises(ValueError, match="presync_timeout_seconds"):
            load_runtime_config(cfg_file, {})

    def test_empty_command_list_raises(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_file, "orchestrate:\n  presync_command: []\n")
        # The schema's minItems:1 rejects an empty list before the parser runs.
        with pytest.raises(ValueError):
            load_runtime_config(cfg_file, {})


# ---------------------------------------------------------------------------
# Parser-layer fail-fast (defense in depth: covers the branches the JSON schema
# would normally reject first, exercised by driving the parser directly).
# ---------------------------------------------------------------------------


class TestParseOrchestratePresyncDirect:
    _PATH = Path("/tmp/x/devbench.yaml")

    def test_parses_presync_fields(self) -> None:
        cfg = _parse_orchestrate_config(
            self._PATH,
            {
                "presync_environment": True,
                "presync_command": ["uv", "sync", "--frozen"],
                "presync_timeout_seconds": 1200,
            },
            use_bedrock=False,
        )
        assert cfg.presync_environment is True
        assert cfg.presync_command == ["uv", "sync", "--frozen"]
        assert cfg.presync_timeout_seconds == 1200

    @pytest.mark.parametrize("bad_value", [0, -1, -900])
    def test_non_positive_timeout_raises_at_parser(self, bad_value: int) -> None:
        with pytest.raises(ValueError, match="presync_timeout_seconds must be >= 1"):
            _parse_orchestrate_config(self._PATH, {"presync_timeout_seconds": bad_value}, use_bedrock=False)


class TestParsePresyncCommandDirect:
    _PATH = Path("/tmp/x/devbench.yaml")

    def test_absent_returns_none(self) -> None:
        assert _parse_presync_command(self._PATH, {}) is None

    def test_valid_list_returned(self) -> None:
        assert _parse_presync_command(self._PATH, {"presync_command": ["make", "deps"]}) == ["make", "deps"]

    @pytest.mark.parametrize(
        "bad_value",
        [
            "uv sync",  # a bare string is not a list
            [],  # empty
            ["uv", ""],  # contains an empty token
            ["uv", "  "],  # contains a whitespace-only token
            ["uv", 3],  # contains a non-string token
        ],
    )
    def test_malformed_command_raises(self, bad_value: object) -> None:
        with pytest.raises(ValueError, match="presync_command must be a non-empty list"):
            _parse_presync_command(self._PATH, {"presync_command": bad_value})
