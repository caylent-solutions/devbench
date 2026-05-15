"""Tests for constants module -- status string constants and VALID_STATUSES."""

from __future__ import annotations


class TestStatusStringConstants:
    """Status string constants are defined in constants.py and are correct."""

    def test_status_constants_exist(self) -> None:
        from devbench.constants import (
            STATUS_BLOCKED,
            STATUS_DECLINED,
            STATUS_DONE,
            STATUS_DRAFT,
            STATUS_HOLD,
            STATUS_IN_PROGRESS,
            STATUS_IN_QUEUE,
            STATUS_IN_REVIEW,
            STATUS_PROPOSED,
        )

        assert STATUS_IN_QUEUE == "in-queue"
        assert STATUS_IN_PROGRESS == "in-progress"
        assert STATUS_IN_REVIEW == "in-review"
        assert STATUS_DONE == "done"
        assert STATUS_BLOCKED == "blocked"
        assert STATUS_PROPOSED == "proposed"
        assert STATUS_DECLINED == "declined"
        assert STATUS_HOLD == "hold"
        assert STATUS_DRAFT == "draft"

    def test_valid_statuses_is_in_constants(self) -> None:
        from devbench.constants import VALID_STATUSES

        assert isinstance(VALID_STATUSES, dict)
        assert set(VALID_STATUSES.keys()) == {
            "in-queue",
            "in-progress",
            "in-review",
            "done",
            "blocked",
            "proposed",
            "declined",
            "hold",
            "draft",
        }

    def test_valid_statuses_keys_match_constants(self) -> None:
        from devbench.constants import (
            STATUS_BLOCKED,
            STATUS_DECLINED,
            STATUS_DONE,
            STATUS_DRAFT,
            STATUS_HOLD,
            STATUS_IN_PROGRESS,
            STATUS_IN_QUEUE,
            STATUS_IN_REVIEW,
            STATUS_PROPOSED,
            VALID_STATUSES,
        )

        assert STATUS_IN_QUEUE in VALID_STATUSES
        assert STATUS_IN_PROGRESS in VALID_STATUSES
        assert STATUS_IN_REVIEW in VALID_STATUSES
        assert STATUS_DONE in VALID_STATUSES
        assert STATUS_BLOCKED in VALID_STATUSES
        assert STATUS_PROPOSED in VALID_STATUSES
        assert STATUS_DECLINED in VALID_STATUSES
        assert STATUS_HOLD in VALID_STATUSES
        assert STATUS_DRAFT in VALID_STATUSES

    def test_valid_statuses_draft_maps_to_itself(self) -> None:
        """VALID_STATUSES['draft'] maps to 'draft' (canonical form)."""
        from devbench.constants import STATUS_DRAFT, VALID_STATUSES

        assert VALID_STATUSES[STATUS_DRAFT] == STATUS_DRAFT

    def test_valid_statuses_still_importable_from_manager(self) -> None:
        """Backward-compatible re-export from manager.py."""
        from devbench.backlog.manager import VALID_STATUSES

        assert "done" in VALID_STATUSES

    def test_valid_statuses_draft_importable_from_manager(self) -> None:
        """VALID_STATUSES re-exported from manager.py also contains 'draft'."""
        from devbench.backlog.manager import VALID_STATUSES

        assert "draft" in VALID_STATUSES


class TestCascadeDepthConstant:
    """DEFAULT_MAX_CASCADE_DEPTH equals 2 (issue #E8)."""

    def test_default_max_cascade_depth_equals_2(self) -> None:
        from devbench.constants import DEFAULT_MAX_CASCADE_DEPTH

        assert DEFAULT_MAX_CASCADE_DEPTH == 2


class TestRecoveryProbeConstants:
    """AC-T5-3: RECOVERY_PROBE_* constants in constants.py have correct types and spec values (spec 4.5.1)."""

    def test_recovery_probe_model_is_non_empty_string(self) -> None:
        """RECOVERY_PROBE_MODEL is a non-empty str naming the cheapest Anthropic probe model."""
        from devbench.constants import RECOVERY_PROBE_MODEL

        assert isinstance(RECOVERY_PROBE_MODEL, str)
        assert len(RECOVERY_PROBE_MODEL) > 0

    def test_recovery_probe_default_timeout_seconds_equals_10(self) -> None:
        """RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS equals 10.0 seconds (spec 4.5.1)."""
        from devbench.constants import RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS

        assert isinstance(RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS, float)
        assert RECOVERY_PROBE_DEFAULT_TIMEOUT_SECONDS == 10.0

    def test_recovery_probe_default_request_size_tokens_equals_1(self) -> None:
        """RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS equals 1 (spec 4.5.1: request_size_tokens=1)."""
        from devbench.constants import RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS

        assert isinstance(RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS, int)
        assert RECOVERY_PROBE_DEFAULT_REQUEST_SIZE_TOKENS == 1

    def test_recovery_probe_message_content_is_non_empty_string(self) -> None:
        """RECOVERY_PROBE_MESSAGE_CONTENT is a non-empty str used as the probe message body."""
        from devbench.constants import RECOVERY_PROBE_MESSAGE_CONTENT

        assert isinstance(RECOVERY_PROBE_MESSAGE_CONTENT, str)
        assert len(RECOVERY_PROBE_MESSAGE_CONTENT) > 0


class TestSessionConstants:
    """AC-T6-2: SESSION_* constants in constants.py have correct types and spec-mandated values (spec 4.4.1)."""

    def test_session_registry_path_is_non_empty_string(self) -> None:
        """SESSION_REGISTRY_PATH is a non-empty str naming the relative path to the session registry JSON."""
        from devbench.constants import SESSION_REGISTRY_PATH

        assert isinstance(SESSION_REGISTRY_PATH, str)
        assert len(SESSION_REGISTRY_PATH) > 0

    def test_session_registry_path_value(self) -> None:
        """SESSION_REGISTRY_PATH equals the spec-mandated relative path (spec 4.4.1)."""
        from devbench.constants import SESSION_REGISTRY_PATH

        assert SESSION_REGISTRY_PATH == ".devbench/sessions/registry.json"

    def test_session_backlog_lock_name_is_non_empty_string(self) -> None:
        """SESSION_BACKLOG_LOCK_NAME is a non-empty str naming the flock file under .devbench/."""
        from devbench.constants import SESSION_BACKLOG_LOCK_NAME

        assert isinstance(SESSION_BACKLOG_LOCK_NAME, str)
        assert len(SESSION_BACKLOG_LOCK_NAME) > 0

    def test_session_backlog_lock_name_value(self) -> None:
        """SESSION_BACKLOG_LOCK_NAME equals 'BACKLOG.lock' (spec 4.4.1: <workspace>/.devbench/BACKLOG.lock)."""
        from devbench.constants import SESSION_BACKLOG_LOCK_NAME

        assert SESSION_BACKLOG_LOCK_NAME == "BACKLOG.lock"

    def test_session_default_flock_timeout_seconds_is_int(self) -> None:
        """SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS is an int representing the default flock timeout."""
        from devbench.constants import SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS

        assert isinstance(SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS, int)

    def test_session_default_flock_timeout_seconds_value(self) -> None:
        """SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS equals 30 (spec 4.4.1: Default timeout 30 s)."""
        from devbench.constants import SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS

        assert SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS == 30

    def test_session_pid_filename_is_non_empty_string(self) -> None:
        """SESSION_PID_FILENAME is a non-empty str naming the PID file within a session state dir."""
        from devbench.constants import SESSION_PID_FILENAME

        assert isinstance(SESSION_PID_FILENAME, str)
        assert len(SESSION_PID_FILENAME) > 0

    def test_session_pid_filename_value(self) -> None:
        """SESSION_PID_FILENAME equals 'pid' (spec 4.4.4: pid -- the orchestrator process's PID)."""
        from devbench.constants import SESSION_PID_FILENAME

        assert SESSION_PID_FILENAME == "pid"

    def test_session_registry_tmp_suffix_is_non_empty_string(self) -> None:
        """SESSION_REGISTRY_TMP_SUFFIX is a non-empty str used as the suffix for atomic registry writes."""
        from devbench.constants import SESSION_REGISTRY_TMP_SUFFIX

        assert isinstance(SESSION_REGISTRY_TMP_SUFFIX, str)
        assert len(SESSION_REGISTRY_TMP_SUFFIX) > 0

    def test_session_registry_tmp_suffix_value(self) -> None:
        """SESSION_REGISTRY_TMP_SUFFIX equals '.tmp' (atomic-write convention for registry.json)."""
        from devbench.constants import SESSION_REGISTRY_TMP_SUFFIX

        assert SESSION_REGISTRY_TMP_SUFFIX == ".tmp"
