"""Tests for judges.log_setup module."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import judges.log_setup as log_setup_mod


class TestSetupLogging:
    """Test setup_logging configures handlers correctly."""

    def setup_method(self) -> None:
        """Reset the configured flag before each test."""
        log_setup_mod._configured = False
        # Clear root handlers
        logging.getLogger().handlers.clear()

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        log_file = tmp_path / "subdir" / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            result = log_setup_mod.setup_logging()

        assert log_file.parent.exists()
        assert result == log_file

    def test_returns_log_file_path(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            result = log_setup_mod.setup_logging()

        assert result == log_file

    def test_adds_stdout_and_file_handlers(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            log_setup_mod.setup_logging()

        root = logging.getLogger()
        handler_types = {type(h).__name__ for h in root.handlers}
        assert "StreamHandler" in handler_types
        assert "FileHandler" in handler_types

    def test_idempotent_on_second_call(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            log_setup_mod.setup_logging()
            handler_count = len(logging.getLogger().handlers)
            log_setup_mod.setup_logging()

        assert len(logging.getLogger().handlers) == handler_count

    def test_writes_to_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            log_setup_mod.setup_logging()

        test_logger = logging.getLogger("test.write")
        test_logger.info("Hello from test")

        # Flush handlers
        for handler in logging.getLogger().handlers:
            handler.flush()

        content = log_file.read_text()
        assert "Hello from test" in content

    def test_uses_default_path_when_env_not_set(self, tmp_path: Path) -> None:
        log_setup_mod._configured = False
        default_log = tmp_path / "logs" / "orchestrator.log"
        with patch.object(log_setup_mod, "_DEFAULT_LOG_FILE", str(default_log)):
            with patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("JUDGE_LOG_FILE", None)
                result = log_setup_mod.setup_logging()

        assert "orchestrator.log" in str(result)
        assert str(tmp_path) in str(result)

    def teardown_method(self) -> None:
        """Clean up after each test."""
        log_setup_mod._configured = False
        logging.getLogger().handlers.clear()
