"""Tests for judges.log_setup module."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import devbench.log_setup as log_setup_mod
from devbench.constants import DEFAULT_LOG_FILENAME, SESSION_SESSIONS_BASE_DIR


class TestSetupLogging:
    """Test setup_logging configures handlers correctly."""

    def setup_method(self) -> None:
        """Reset the configured flag before each test."""
        log_setup_mod._state[0] = False
        # Clear root handlers
        logging.getLogger().handlers.clear()

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        log_file = tmp_path / "subdir" / "test.log"
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file)}):
            result = log_setup_mod.setup_logging()

        assert log_file.parent.exists()
        assert result == log_file

    def test_returns_log_file_path(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file)}):
            result = log_setup_mod.setup_logging()

        assert result == log_file

    def test_adds_stderr_and_file_handlers(self, tmp_path: Path) -> None:
        """B6: stream handler must target stderr, not stdout, so commands like
        `devbench report` can be piped/redirected without log lines polluting
        the data stream on stdout.
        """
        import sys

        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file)}):
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
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file)}):
            log_setup_mod.setup_logging()
            handler_count = len(logging.getLogger().handlers)
            log_setup_mod.setup_logging()

        assert len(logging.getLogger().handlers) == handler_count

    def test_writes_to_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file)}):
            log_setup_mod.setup_logging()

        test_logger = logging.getLogger("test.write")
        test_logger.info("Hello from test")

        # Flush handlers
        for handler in logging.getLogger().handlers:
            handler.flush()

        content = log_file.read_text()
        assert "Hello from test" in content

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
    DEVBENCH_LOG_LEVEL=DEBUG. The default INFO level is silent.
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

        log_file = tmp_path / "test.log"
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file)}):
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
        with patch.dict("os.environ", {"DEVBENCH_LOG_FILE": str(log_file), "DEVBENCH_LOG_LEVEL": "DEBUG"}):
            log_setup_mod.setup_logging()
            logging.shutdown()
        log_contents = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
        assert "Logging to stderr" in log_contents, (
            "At DEVBENCH_LOG_LEVEL=DEBUG the banner must remain available for "
            "operator diagnostics. Log file contents: " + repr(log_contents)
        )


class TestPerSessionLogRouting:
    """AC-192-14: when DEVBENCH_SESSION_NAME is set, setup_logging() adds a
    FileHandler routing to <workspace>/.devbench/sessions/<name>/orchestrator.log
    in addition to the aggregate <workspace>/logs/orchestrator.log.
    """

    def setup_method(self) -> None:
        """Reset logging state before each test."""
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def _session_file_handlers(self, session_name: str, workspace: str) -> list[logging.FileHandler]:
        """Return FileHandlers whose baseFilename points inside the session dir."""
        session_dir = str(Path(workspace) / SESSION_SESSIONS_BASE_DIR / session_name)
        return [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler) and session_dir in h.baseFilename
        ]

    def test_session_file_handler_added_when_env_var_set(self, tmp_path: Path) -> None:
        """When DEVBENCH_SESSION_NAME is set, exactly one session FileHandler is added."""
        log_file = tmp_path / "logs" / "orchestrator.log"
        env = {
            "DEVBENCH_LOG_FILE": str(log_file),
            "DEVBENCH_SESSION_NAME": "mysession",
            "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
        }
        with patch.dict("os.environ", env, clear=False):
            log_setup_mod.setup_logging()

        session_handlers = self._session_file_handlers("mysession", str(tmp_path))
        assert len(session_handlers) == 1, f"expected exactly 1 session FileHandler; got {session_handlers}"

    def test_session_log_directory_created_automatically(self, tmp_path: Path) -> None:
        """The per-session directory is created if it does not exist yet."""
        log_file = tmp_path / "logs" / "orchestrator.log"
        env = {
            "DEVBENCH_LOG_FILE": str(log_file),
            "DEVBENCH_SESSION_NAME": "newsession",
            "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
        }
        with patch.dict("os.environ", env, clear=False):
            log_setup_mod.setup_logging()

        expected_dir = tmp_path / SESSION_SESSIONS_BASE_DIR / "newsession"
        assert expected_dir.exists(), f"session directory {expected_dir} was not created"

    def test_session_log_filename_is_orchestrator_log(self, tmp_path: Path) -> None:
        """The per-session log file is named orchestrator.log (DEFAULT_LOG_FILENAME)."""
        log_file = tmp_path / "logs" / "orchestrator.log"
        session_name = "alpha"
        env = {
            "DEVBENCH_LOG_FILE": str(log_file),
            "DEVBENCH_SESSION_NAME": session_name,
            "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
        }
        with patch.dict("os.environ", env, clear=False):
            log_setup_mod.setup_logging()

        session_handlers = self._session_file_handlers(session_name, str(tmp_path))
        assert len(session_handlers) == 1
        assert session_handlers[0].baseFilename.endswith(DEFAULT_LOG_FILENAME), (
            f"session log must be named {DEFAULT_LOG_FILENAME}; got {session_handlers[0].baseFilename}"
        )

    def test_messages_written_to_both_aggregate_and_session_log(self, tmp_path: Path) -> None:
        """A logged message appears in both the aggregate log and the session log."""
        log_file = tmp_path / "logs" / "orchestrator.log"
        session_name = "beta"
        env = {
            "DEVBENCH_LOG_FILE": str(log_file),
            "DEVBENCH_SESSION_NAME": session_name,
            "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
        }
        with patch.dict("os.environ", env, clear=False):
            log_setup_mod.setup_logging()

        logging.getLogger("test.session").info("dual-routing-marker")
        for h in logging.getLogger().handlers:
            h.flush()

        aggregate_text = log_file.read_text(encoding="utf-8")
        session_log = tmp_path / SESSION_SESSIONS_BASE_DIR / session_name / DEFAULT_LOG_FILENAME
        session_text = session_log.read_text(encoding="utf-8")

        assert "dual-routing-marker" in aggregate_text, "message not found in aggregate log"
        assert "dual-routing-marker" in session_text, "message not found in session log"

    @pytest.mark.parametrize("session_env_value", ["", None])
    def test_no_session_handler_when_env_absent_or_empty(self, tmp_path: Path, session_env_value: str | None) -> None:
        """When DEVBENCH_SESSION_NAME is absent or empty, no session handler is added."""
        log_file = tmp_path / "logs" / "orchestrator.log"
        env: dict[str, str] = {
            "DEVBENCH_LOG_FILE": str(log_file),
            "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
        }
        if session_env_value is not None:
            env["DEVBENCH_SESSION_NAME"] = session_env_value

        with patch.dict("os.environ", env, clear=False):
            os.environ.pop("DEVBENCH_SESSION_NAME", None) if session_env_value is None else None
            log_setup_mod.setup_logging()

        # Only the aggregate FileHandler should exist; no session-scoped handler
        file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        # Should have exactly 1 FileHandler (the aggregate log, not a session log)
        session_dir_path = str(tmp_path / SESSION_SESSIONS_BASE_DIR)
        session_handlers = [h for h in file_handlers if session_dir_path in h.baseFilename]
        assert session_handlers == [], (
            f"no session handler expected when DEVBENCH_SESSION_NAME={session_env_value!r}; got {session_handlers}"
        )

    @pytest.mark.parametrize("session_name", ["alpha", "beta-2", "my_session"])
    def test_session_log_path_constructed_from_session_name(self, tmp_path: Path, session_name: str) -> None:
        """The session log path uses the exact value of DEVBENCH_SESSION_NAME."""
        log_file = tmp_path / "logs" / "orchestrator.log"
        env = {
            "DEVBENCH_LOG_FILE": str(log_file),
            "DEVBENCH_SESSION_NAME": session_name,
            "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
        }
        with patch.dict("os.environ", env, clear=False):
            log_setup_mod.setup_logging()

        expected_path = tmp_path / SESSION_SESSIONS_BASE_DIR / session_name / DEFAULT_LOG_FILENAME
        session_handlers = self._session_file_handlers(session_name, str(tmp_path))
        assert len(session_handlers) == 1
        assert Path(session_handlers[0].baseFilename) == expected_path.resolve()


class TestResolveLogFile:
    """Path-resolution chain when DEVBENCH_LOG_FILE is unset.

    ``devbench.config`` is imported at module load time and reads
    ``DEVBENCH_WORKSPACE_ROOT`` then; we never re-import it.  These tests
    override env vars via monkeypatch (no ``clear=True``) and patch the
    cached ``RUNTIME_CONFIG`` attribute on the already-loaded module.
    """

    def setup_method(self) -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def teardown_method(self) -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def test_workspace_only_uses_default_subdir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Workspace set, no DEVBENCH_LOG_FILE, no configured log_file → <workspace>/logs/orchestrator.log."""
        from devbench import config as cfg
        from devbench.constants import DEFAULT_LOG_SUBDIR

        monkeypatch.setenv("DEVBENCH_LOG_FILE", "")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(tmp_path))

        class _NoLogFile:
            log_file = ""

        monkeypatch.setattr(cfg, "RUNTIME_CONFIG", _NoLogFile())
        result = log_setup_mod._resolve_log_file()
        assert result == tmp_path / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME

    def test_no_workspace_no_configured_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No workspace, no configured log_file → legacy default under devbench source tree."""
        from devbench import config as cfg

        monkeypatch.setenv("DEVBENCH_LOG_FILE", "")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", "")

        class _NoLogFile:
            log_file = ""

        monkeypatch.setattr(cfg, "RUNTIME_CONFIG", _NoLogFile())
        result = log_setup_mod._resolve_log_file()
        assert result == Path(log_setup_mod._DEFAULT_LOG_FILE)

    def test_configured_absolute_path_wins_over_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RUNTIME_CONFIG.log_file absolute path is used verbatim."""
        from devbench import config as cfg

        absolute_path = tmp_path / "configured" / "my.log"
        monkeypatch.setenv("DEVBENCH_LOG_FILE", "")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(tmp_path))

        class _AbsoluteCfg:
            log_file = str(absolute_path)

        monkeypatch.setattr(cfg, "RUNTIME_CONFIG", _AbsoluteCfg())
        result = log_setup_mod._resolve_log_file()
        assert result == absolute_path

    def test_configured_relative_with_workspace_resolves_under_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RUNTIME_CONFIG.log_file relative + workspace set → workspace/<relative>."""
        from devbench import config as cfg

        monkeypatch.setenv("DEVBENCH_LOG_FILE", "")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(tmp_path))

        class _RelativeCfg:
            log_file = "rel/my.log"

        monkeypatch.setattr(cfg, "RUNTIME_CONFIG", _RelativeCfg())
        result = log_setup_mod._resolve_log_file()
        assert result == tmp_path / "rel" / "my.log"

    def test_configured_relative_without_workspace_returns_relative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RUNTIME_CONFIG.log_file relative + no workspace → returns relative as-is."""
        from devbench import config as cfg

        monkeypatch.setenv("DEVBENCH_LOG_FILE", "")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", "")

        class _RelativeCfg:
            log_file = "rel/my.log"

        monkeypatch.setattr(cfg, "RUNTIME_CONFIG", _RelativeCfg())
        result = log_setup_mod._resolve_log_file()
        assert result == Path("rel/my.log")

    def test_runtime_config_attribute_error_falls_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Object without ``log_file`` raises AttributeError; configured stays empty."""
        from devbench import config as cfg
        from devbench.constants import DEFAULT_LOG_SUBDIR

        monkeypatch.setenv("DEVBENCH_LOG_FILE", "")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(tmp_path))

        class _NoLogFile:
            pass

        monkeypatch.setattr(cfg, "RUNTIME_CONFIG", _NoLogFile())
        result = log_setup_mod._resolve_log_file()
        assert result == tmp_path / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME


class TestResolveSessionLogFile:
    """RuntimeError contract when SESSION_NAME is set without WORKSPACE_ROOT."""

    def setup_method(self) -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def teardown_method(self) -> None:
        log_setup_mod._state[0] = False
        logging.getLogger().handlers.clear()

    def test_raises_when_session_name_set_but_workspace_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEVBENCH_SESSION_NAME set without DEVBENCH_WORKSPACE_ROOT must raise RuntimeError."""
        monkeypatch.setenv("DEVBENCH_SESSION_NAME", "mysession")
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", "")
        with pytest.raises(
            RuntimeError,
            match=r"DEVBENCH_WORKSPACE_ROOT must be set when DEVBENCH_SESSION_NAME is set",
        ):
            log_setup_mod._resolve_session_log_file()
