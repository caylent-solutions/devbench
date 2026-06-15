"""Tests for supervise system/Python dependencies (FR-22, FR-23, AC-22).

AC-22: ``pexpect`` is declared in pyproject.toml and importable.
FR-23: ``screen`` (a system dependency) is probed at launch and fails fast with
an install hint when absent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from devbench.constants import SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT
from devbench.supervise import (
    ScreenUnavailableError,
    require_screen,
    screen_session_name,
)


@pytest.mark.unit
class TestPexpectDependency:
    """AC-22: pexpect is importable (declared in pyproject.toml dependencies)."""

    def test_pexpect_importable(self) -> None:
        import pexpect

        assert hasattr(pexpect, "spawn")


@pytest.mark.unit
class TestRequireScreen:
    """FR-23: screen is a system dependency probed at launch."""

    def test_returns_path_when_present(self) -> None:
        with patch("devbench.supervise.shutil.which", return_value="/usr/bin/screen"):
            assert require_screen() == "/usr/bin/screen"

    def test_fails_fast_when_absent(self) -> None:
        with patch("devbench.supervise.shutil.which", return_value=None):
            with pytest.raises(ScreenUnavailableError, match="not installed"):
                require_screen()

    def test_error_carries_install_hint(self) -> None:
        with patch("devbench.supervise.shutil.which", return_value=None):
            with pytest.raises(ScreenUnavailableError, match="apt-get install -y screen"):
                require_screen()


@pytest.mark.unit
class TestScreenSessionName:
    """The screen session name is <prefix><name> (FR-6)."""

    def test_default_prefix(self) -> None:
        assert screen_session_name("nightly") == f"{SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT}nightly"

    def test_custom_prefix(self) -> None:
        assert screen_session_name("bulk", prefix="my-") == "my-bulk"
