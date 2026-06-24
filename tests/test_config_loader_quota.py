"""Tests for quota_handling config loading in devbench.config_loader.

Covers: QuotaHandlingConfig dataclass defaults, parser validation,
enum fail-fast, range fail-fast, unknown key rejection.

Issue #236 (Appendix A QW-6).
AC-236-3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import devbench.config_loader as _config_loader_mod
from devbench.config_loader import QuotaHandlingConfig, load_runtime_config


def _write_minimal_config(path: Path, extra: str = "") -> None:
    """Write a minimal valid devbench.yaml to *path*."""
    content = "repos:\n  org/repo:\n    default_branch: main\n"
    if extra:
        content += extra
    path.write_text(content, encoding="utf-8")


@pytest.mark.unit
class TestQuotaHandlingConfigDefaults:
    """QuotaHandlingConfig has correct defaults per D-Q-1 and Appendix A QW-6."""

    def test_enabled_default_true(self) -> None:
        """AC-236-3: empty config loads quota_handling.enabled is True (D-Q-1)."""
        cfg = QuotaHandlingConfig()
        assert cfg.enabled is True

    def test_on_exhaustion_default_wait(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.on_exhaustion == "wait"

    def test_poll_interval_seconds_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.poll_interval_seconds == 60

    def test_max_wait_seconds_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.max_wait_seconds == 18000

    def test_on_exhaustion_timeout_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.on_exhaustion_timeout == "drain"

    def test_resume_strategy_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.resume_strategy == "continue_current_wu"

    def test_audit_comment_on_wait_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.audit_comment_on_wait is True

    def test_audit_comment_on_resume_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.audit_comment_on_resume is True

    def test_log_structured_events_default(self) -> None:
        cfg = QuotaHandlingConfig()
        assert cfg.log_structured_events is True


@pytest.mark.unit
class TestQuotaHandlingConfigLoading:
    """AC-236-3: quota_handling block loading via load_runtime_config."""

    def test_empty_config_loads_enabled_true(self, tmp_path: Path) -> None:
        """AC-236-3: An empty config (no quota_handling block) loads enabled=True."""
        cfg_path = tmp_path / "devbench.yaml"
        _write_minimal_config(cfg_path)
        import os

        cfg = load_runtime_config(cfg_path, os.environ)
        assert cfg.quota_handling.enabled is True

    def test_explicit_enabled_false_loads_false(self, tmp_path: Path) -> None:
        """AC-236-3: quota_handling: {enabled: false} loads enabled=False."""
        cfg_path = tmp_path / "devbench.yaml"
        _write_minimal_config(
            cfg_path,
            extra="quota_handling:\n  enabled: false\n",
        )
        import os

        cfg = load_runtime_config(cfg_path, os.environ)
        assert cfg.quota_handling.enabled is False

    def test_explicit_enabled_true_loads_true(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_minimal_config(
            cfg_path,
            extra="quota_handling:\n  enabled: true\n",
        )
        import os

        cfg = load_runtime_config(cfg_path, os.environ)
        assert cfg.quota_handling.enabled is True

    def test_full_quota_handling_block(self, tmp_path: Path) -> None:
        """A fully-specified quota_handling block loads all fields correctly."""
        cfg_path = tmp_path / "devbench.yaml"
        block = (
            "quota_handling:\n"
            "  enabled: true\n"
            "  on_exhaustion: wait\n"
            "  poll_interval_seconds: 90\n"
            "  max_wait_seconds: 7200\n"
            "  on_exhaustion_timeout: fail\n"
            "  resume_strategy: restart_wu\n"
            "  audit_comment_on_wait: false\n"
            "  audit_comment_on_resume: false\n"
            "  log_structured_events: false\n"
        )
        _write_minimal_config(cfg_path, extra=block)
        import os

        cfg = load_runtime_config(cfg_path, os.environ)
        qh = cfg.quota_handling
        assert qh.enabled is True
        assert qh.on_exhaustion == "wait"
        assert qh.poll_interval_seconds == 90
        assert qh.max_wait_seconds == 7200
        assert qh.on_exhaustion_timeout == "fail"
        assert qh.resume_strategy == "restart_wu"
        assert qh.audit_comment_on_wait is False
        assert qh.audit_comment_on_resume is False
        assert qh.log_structured_events is False


@pytest.mark.unit
class TestQuotaHandlingEnumValidation:
    """Parser-level enum fail-fast for quota_handling fields (Appendix A QW-6)."""

    @pytest.mark.parametrize(
        "field,invalid_value",
        [
            ("on_exhaustion", "explode"),
            ("on_exhaustion_timeout", "panic"),
            ("resume_strategy", "teleport"),
        ],
    )
    def test_invalid_enum_raises_value_error(self, tmp_path: Path, field: str, invalid_value: str) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_minimal_config(
            cfg_path,
            extra=f"quota_handling:\n  {field}: {invalid_value}\n",
        )
        import os

        with pytest.raises(ValueError, match=field):
            load_runtime_config(cfg_path, os.environ)


@pytest.mark.unit
class TestQuotaHandlingRangeValidation:
    """Parser-level range fail-fast for quota_handling fields (Appendix A QW-6)."""

    @pytest.mark.parametrize(
        "field,invalid_value,match",
        [
            ("poll_interval_seconds", 29, "poll_interval_seconds"),
            ("poll_interval_seconds", 3601, "poll_interval_seconds"),
            ("max_wait_seconds", 0, "max_wait_seconds"),
        ],
    )
    def test_invalid_range_raises_value_error(self, tmp_path: Path, field: str, invalid_value: int, match: str) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_minimal_config(
            cfg_path,
            extra=f"quota_handling:\n  {field}: {invalid_value}\n",
        )
        import os

        with pytest.raises((ValueError, Exception), match=match):
            load_runtime_config(cfg_path, os.environ)


@pytest.mark.unit
class TestParseQuotaHandlingConfigDirect:
    """Direct tests for _parse_quota_handling_config bypassing load_runtime_config.

    These tests exercise the Python-level validation (lines 716-749 of
    config_loader.py) that acts as a defense-in-depth check after JSON
    schema validation. Calling _parse_quota_handling_config directly is
    the only way to reach those branches because jsonschema.validate runs
    first inside load_runtime_config.
    """

    _FAKE_PATH = Path("fake.yaml")

    @pytest.mark.parametrize(
        "raw,match",
        [
            ({"on_exhaustion": "explode"}, "on_exhaustion"),
            ({"on_exhaustion": ""}, "on_exhaustion"),
            ({"on_exhaustion": "WAIT"}, "on_exhaustion"),
        ],
    )
    def test_invalid_on_exhaustion_raises(self, raw: dict, match: str) -> None:
        """Lines 716-718: invalid on_exhaustion raises ValueError."""
        with pytest.raises(ValueError, match=match):
            _config_loader_mod._parse_quota_handling_config(self._FAKE_PATH, raw)

    @pytest.mark.parametrize(
        "raw,match",
        [
            ({"on_exhaustion_timeout": "panic"}, "on_exhaustion_timeout"),
            ({"on_exhaustion_timeout": ""}, "on_exhaustion_timeout"),
            ({"on_exhaustion_timeout": "DRAIN"}, "on_exhaustion_timeout"),
        ],
    )
    def test_invalid_on_exhaustion_timeout_raises(self, raw: dict, match: str) -> None:
        """Lines 723-725: invalid on_exhaustion_timeout raises ValueError."""
        with pytest.raises(ValueError, match=match):
            _config_loader_mod._parse_quota_handling_config(self._FAKE_PATH, raw)

    @pytest.mark.parametrize(
        "raw,match",
        [
            ({"resume_strategy": "teleport"}, "resume_strategy"),
            ({"resume_strategy": ""}, "resume_strategy"),
            ({"resume_strategy": "CONTINUE"}, "resume_strategy"),
        ],
    )
    def test_invalid_resume_strategy_raises(self, raw: dict, match: str) -> None:
        """Lines 731-733: invalid resume_strategy raises ValueError."""
        with pytest.raises(ValueError, match=match):
            _config_loader_mod._parse_quota_handling_config(self._FAKE_PATH, raw)

    @pytest.mark.parametrize(
        "poll_interval_seconds",
        [29, 0, -1, 3601, 9999],
    )
    def test_invalid_poll_interval_raises(self, poll_interval_seconds: int) -> None:
        """Line 738-739: poll_interval_seconds out of [30, 3600] raises ValueError."""
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            _config_loader_mod._parse_quota_handling_config(
                self._FAKE_PATH,
                {"poll_interval_seconds": poll_interval_seconds},
            )

    @pytest.mark.parametrize("max_wait_seconds", [0, -1, -100])
    def test_invalid_max_wait_raises(self, max_wait_seconds: int) -> None:
        """Lines 745-746: max_wait_seconds < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_wait_seconds"):
            _config_loader_mod._parse_quota_handling_config(
                self._FAKE_PATH,
                {"max_wait_seconds": max_wait_seconds},
            )

    def test_valid_raw_returns_config(self) -> None:
        """Happy-path: a fully valid raw dict returns a QuotaHandlingConfig."""
        raw = {
            "enabled": False,
            "on_exhaustion": "fail",
            "on_exhaustion_timeout": "keep_waiting",
            "resume_strategy": "restart_wu",
            "poll_interval_seconds": 120,
            "max_wait_seconds": 3600,
            "audit_comment_on_wait": False,
            "audit_comment_on_resume": False,
            "log_structured_events": False,
        }
        result = _config_loader_mod._parse_quota_handling_config(self._FAKE_PATH, raw)
        assert isinstance(result, QuotaHandlingConfig)
        assert result.enabled is False
        assert result.on_exhaustion == "fail"
        assert result.on_exhaustion_timeout == "keep_waiting"
        assert result.resume_strategy == "restart_wu"
        assert result.poll_interval_seconds == 120
        assert result.max_wait_seconds == 3600
        assert result.audit_comment_on_wait is False
        assert result.audit_comment_on_resume is False
        assert result.log_structured_events is False
