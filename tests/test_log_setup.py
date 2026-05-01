"""Tests for judges.log_setup module."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import devbench.log_setup as log_setup_mod


class TestSetupLogging:
    """Test setup_logging configures handlers correctly."""

    def setup_method(self) -> None:
        """Reset the configured flag before each test."""
        log_setup_mod._state[0] = False
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

    def test_adds_stderr_and_file_handlers(self, tmp_path: Path) -> None:
        """B6: stream handler must target stderr, not stdout, so commands like
        `devbench report` can be piped/redirected without log lines polluting
        the data stream on stdout.
        """
        import sys

        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            log_setup_mod.setup_logging()

        root = logging.getLogger()
        handler_types = {type(h).__name__ for h in root.handlers}
        assert "StreamHandler" in handler_types
        assert "FileHandler" in handler_types

        # `type(h).__name__ == "StreamHandler"` matches *only* the base StreamHandler
        # (not FileHandler, which inherits from it). Use isinstance + exact type check
        # so mypy narrows `handler` to StreamHandler and can see the `.stream` attribute.
        stream_handlers = [
            h for h in root.handlers if isinstance(h, logging.StreamHandler) and type(h) is logging.StreamHandler
        ]
        assert stream_handlers, "expected at least one StreamHandler"
        for handler in stream_handlers:
            assert handler.stream is sys.stderr, (
                f"StreamHandler must target stderr (got {handler.stream}); stdout is reserved for command data output."
            )

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
        # The fallback chain is now: JUDGE_LOG_FILE > YAML log_file >
        # JUDGE_WORKSPACE_ROOT/logs/orchestrator.log > source-tree
        # _DEFAULT_LOG_FILE. To exercise the source-tree default this
        # test must unset BOTH JUDGE_LOG_FILE and JUDGE_WORKSPACE_ROOT
        # AND ensure the YAML config layer cannot resolve a log_file
        # (the conftest fixture's test_devbench.yaml has none).
        log_setup_mod._state[0] = False
        default_log = tmp_path / "logs" / "orchestrator.log"
        with patch.object(log_setup_mod, "_DEFAULT_LOG_FILE", str(default_log)):
            with patch.dict("os.environ", {}, clear=False):
                import os

                os.environ.pop("JUDGE_LOG_FILE", None)
                os.environ.pop("JUDGE_WORKSPACE_ROOT", None)
                result = log_setup_mod.setup_logging()

        assert "orchestrator.log" in str(result)
        assert str(tmp_path) in str(result)

    def teardown_method(self) -> None:
        """Clean up after each test."""
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()


class TestStartupBannerDemoted:
    """Issue #132 regression: ``setup_logging`` must NOT emit the
    "Logging to stderr and ..." banner at INFO level on every CLI invocation.

    Bug: every devbench command (most visibly ``hook-tail``,
    ``get-diff``, ``status``) emitted a banner like::

        2026-05-01T22:10:24Z [judges.log_setup] INFO Logging to stderr and ...

    on stderr at startup. For stream-rendering commands the banner was
    interleaved with the actual output stream and operators piping the
    output through filters had to add ``2>/dev/null`` to keep the stream
    clean.

    Fix: demote the banner to DEBUG. Operators who want it back can set
    JUDGE_LOG_LEVEL=DEBUG. The default INFO level is silent.
    """

    def setup_method(self) -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def teardown_method(self) -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def test_banner_not_emitted_at_default_info_level(self, tmp_path: Path) -> None:
        """At the default INFO level the banner must be silent.

        ``setup_logging`` clears all handlers on entry (line ``handlers.clear()``)
        so pytest's caplog cannot observe the call -- assert against the
        file-handler output instead, which captures every record above the
        configured level for the run.
        """
        import os

        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            os.environ.pop("JUDGE_LOG_LEVEL", None)
            log_setup_mod.setup_logging()
            logging.shutdown()
        log_contents = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
        assert "Logging to stderr" not in log_contents, (
            "judges.log_setup must not emit 'Logging to stderr and ...' at "
            "INFO level (issue #132). Log file contents: " + repr(log_contents)
        )

    def test_banner_emitted_at_debug_level(self, tmp_path: Path) -> None:
        """At DEBUG level the banner is still available for diagnostics."""
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file), "JUDGE_LOG_LEVEL": "DEBUG"}):
            log_setup_mod.setup_logging()
            logging.shutdown()
        log_contents = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
        assert "Logging to stderr" in log_contents, (
            "At JUDGE_LOG_LEVEL=DEBUG the banner must remain available for "
            "operator diagnostics. Log file contents: " + repr(log_contents)
        )
