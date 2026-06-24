"""Tests for cascade_requeue_max_cycles config loading in devbench.config_loader.

Covers: BacklogConfig.cascade_requeue_max_cycles dataclass default (3),
parser validation, and fail-fast ValueError when the value is below 1.

Issue #248b.
AC-248-2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.config_loader import BacklogConfig, load_runtime_config


def _write_minimal_config(path: Path, extra: str = "") -> None:
    """Write a minimal valid devbench.yaml to *path*."""
    content = "repos:\n  org/repo:\n    default_branch: main\n"
    if extra:
        content += extra
    path.write_text(content, encoding="utf-8")


@pytest.mark.unit
class TestBacklogConfigCascadeDefault:
    """BacklogConfig.cascade_requeue_max_cycles defaults to 3."""

    def test_cascade_requeue_max_cycles_default(self) -> None:
        """Empty BacklogConfig defaults cascade_requeue_max_cycles to 3."""
        cfg = BacklogConfig()
        assert cfg.cascade_requeue_max_cycles == 3

    def test_cascade_requeue_max_cycles_custom(self) -> None:
        """BacklogConfig accepts a custom cascade_requeue_max_cycles value."""
        cfg = BacklogConfig(cascade_requeue_max_cycles=5)
        assert cfg.cascade_requeue_max_cycles == 5


@pytest.mark.unit
class TestLoadRuntimeConfigCascade:
    """load_runtime_config populates cascade_requeue_max_cycles from backlog: section."""

    def test_default_when_key_absent(self, tmp_path: Path) -> None:
        """When cascade_requeue_max_cycles is absent from YAML, default is 3."""
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_file)
        cfg = load_runtime_config(cfg_file, {})
        assert cfg.backlog.cascade_requeue_max_cycles == 3

    def test_custom_value_loaded(self, tmp_path: Path) -> None:
        """cascade_requeue_max_cycles: 7 is loaded and surfaced as 7."""
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_file, "backlog:\n  cascade_requeue_max_cycles: 7\n")
        cfg = load_runtime_config(cfg_file, {})
        assert cfg.backlog.cascade_requeue_max_cycles == 7

    def test_value_of_one_is_accepted(self, tmp_path: Path) -> None:
        """cascade_requeue_max_cycles: 1 is the minimum accepted value."""
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_file, "backlog:\n  cascade_requeue_max_cycles: 1\n")
        cfg = load_runtime_config(cfg_file, {})
        assert cfg.backlog.cascade_requeue_max_cycles == 1


@pytest.mark.unit
class TestCascadeRequeueMaxCyclesFailFast:
    """ValueError is raised with the verbatim message when the cap is below 1."""

    @pytest.mark.parametrize("bad_value", [0, -1, -100])
    def test_zero_and_negative_raise_value_error(self, tmp_path: Path, bad_value: int) -> None:
        """Values below 1 raise ValueError: cascade_requeue_max_cycles must be >= 1."""
        cfg_file = tmp_path / "devbench.yaml"
        _write_minimal_config(
            cfg_file,
            f"backlog:\n  cascade_requeue_max_cycles: {bad_value}\n",
        )
        with pytest.raises(ValueError, match="cascade_requeue_max_cycles must be >= 1"):
            load_runtime_config(cfg_file, {})
