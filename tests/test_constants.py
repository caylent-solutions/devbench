"""Tests for constants module -- status string constants and VALID_STATUSES."""

from __future__ import annotations

import pytest


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


class TestStatusSummaryTableHeader:
    """STATUS_SUMMARY_TABLE_HEADER constant includes Draft column (AC-189-7)."""

    def test_status_summary_table_header_includes_draft_column(self) -> None:
        """STATUS_SUMMARY_TABLE_HEADER contains a Draft column header cell."""
        from devbench.constants import STATUS_SUMMARY_TABLE_HEADER

        assert "Draft" in STATUS_SUMMARY_TABLE_HEADER

    def test_status_summary_table_header_is_two_line_markdown_table(self) -> None:
        """STATUS_SUMMARY_TABLE_HEADER is a two-line markdown table (header row + separator row)."""
        from devbench.constants import STATUS_SUMMARY_TABLE_HEADER

        lines = STATUS_SUMMARY_TABLE_HEADER.splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("|")
        assert lines[1].startswith("|")
        # Separator row contains only dashes and pipes
        assert all(c in "-| " for c in lines[1])

    def test_status_summary_table_header_draft_column_position(self) -> None:
        """STATUS_SUMMARY_TABLE_HEADER has Draft as the last data column before closing pipe."""
        from devbench.constants import STATUS_SUMMARY_TABLE_HEADER

        header_row = STATUS_SUMMARY_TABLE_HEADER.splitlines()[0]
        columns = [col.strip() for col in header_row.strip("|").split("|")]
        assert "Draft" in columns


class TestCascadeDepthConstant:
    """DEFAULT_MAX_CASCADE_DEPTH equals 2 (issue #E8)."""

    def test_default_max_cascade_depth_equals_2(self) -> None:
        from devbench.constants import DEFAULT_MAX_CASCADE_DEPTH

        assert DEFAULT_MAX_CASCADE_DEPTH == 2


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

    def test_session_flock_poll_interval_seconds_is_float(self) -> None:
        """SESSION_FLOCK_POLL_INTERVAL_SECONDS is a float (sub-second poll cadence for flock_backlog)."""
        from devbench.constants import SESSION_FLOCK_POLL_INTERVAL_SECONDS

        assert isinstance(SESSION_FLOCK_POLL_INTERVAL_SECONDS, float)

    def test_session_flock_poll_interval_seconds_value(self) -> None:
        """SESSION_FLOCK_POLL_INTERVAL_SECONDS equals 0.1 (100 ms poll cadence, spec 4.4.1)."""
        from devbench.constants import SESSION_FLOCK_POLL_INTERVAL_SECONDS

        assert SESSION_FLOCK_POLL_INTERVAL_SECONDS == 0.1


class TestSessionNameConstants:
    """AC-FIX-001 through AC-FIX-004: SESSION_DEFAULT_NAME, SESSION_STARTED_AT_FILENAME,
    and SESSION_STARTED_BY_FILENAME are exported from constants.py with the correct
    type (str) and spec-mandated values.
    """

    @pytest.mark.parametrize(
        ("constant_name", "expected_value"),
        [
            ("SESSION_DEFAULT_NAME", "default"),
            ("SESSION_STARTED_AT_FILENAME", "started_at"),
            ("SESSION_STARTED_BY_FILENAME", "started_by"),
        ],
    )
    def test_session_name_constant_type_and_value(
        self,
        constant_name: str,
        expected_value: str,
    ) -> None:
        """Each SESSION_*_NAME / SESSION_STARTED_* constant is importable from
        devbench.constants, is of type str, and matches its spec-mandated value.
        """
        import importlib

        module = importlib.import_module("devbench.constants")
        constant = getattr(module, constant_name)
        assert isinstance(constant, str), f"{constant_name} expected type 'str', got {type(constant).__name__!r}"
        assert constant == expected_value, f"{constant_name} expected {expected_value!r}, got {constant!r}"


class TestSessionSessionsBaseDirConstant:
    """AC-CONST-01 through AC-CONST-03: SESSION_SESSIONS_BASE_DIR constant in constants.py.

    The constant must be importable from devbench.constants, must be a str,
    and must equal '.devbench/sessions' without any absolute-path separators.
    """

    def test_session_sessions_base_dir_is_importable(self) -> None:
        """AC-CONST-03: SESSION_SESSIONS_BASE_DIR is importable from devbench.constants without error."""
        import devbench.constants as _c

        assert hasattr(_c, "SESSION_SESSIONS_BASE_DIR")

    def test_session_sessions_base_dir_is_non_empty_string(self) -> None:
        """AC-CONST-02: SESSION_SESSIONS_BASE_DIR is a non-empty str."""
        from devbench.constants import SESSION_SESSIONS_BASE_DIR

        assert isinstance(SESSION_SESSIONS_BASE_DIR, str)
        assert len(SESSION_SESSIONS_BASE_DIR) > 0

    def test_session_sessions_base_dir_equals_expected_value(self) -> None:
        """AC-CONST-01: SESSION_SESSIONS_BASE_DIR equals '.devbench/sessions'."""
        from devbench.constants import SESSION_SESSIONS_BASE_DIR

        assert SESSION_SESSIONS_BASE_DIR == ".devbench/sessions"

    def test_session_sessions_base_dir_is_relative_path(self) -> None:
        """AC-CONST-02: SESSION_SESSIONS_BASE_DIR does not begin with '/' (relative path only)."""
        from devbench.constants import SESSION_SESSIONS_BASE_DIR

        assert not SESSION_SESSIONS_BASE_DIR.startswith("/")

    @pytest.mark.parametrize(
        "constant_name",
        ["SESSION_SESSIONS_BASE_DIR"],
    )
    def test_session_sessions_base_dir_via_importlib(self, constant_name: str) -> None:
        """AC-CONST-03: constant is dynamically importable via importlib (parametrized form)."""
        import importlib

        module = importlib.import_module("devbench.constants")
        constant = getattr(module, constant_name)
        assert isinstance(constant, str), f"{constant_name} expected type 'str', got {type(constant).__name__!r}"
        assert constant == ".devbench/sessions", f"{constant_name} expected '.devbench/sessions', got {constant!r}"


class TestSessionDrainSignalFilenameConstant:
    """AC-192-CONST-DRAIN: SESSION_DRAIN_SIGNAL_FILENAME is exported from constants.py
    with the correct type and spec-mandated value.

    The constant names the drain signal file written inside a per-session state
    directory (spec section 4.4.5 / 4.3.1).  Both drain.py and cli.py import it
    from here -- any change to the filename must be made in exactly one place.
    """

    @pytest.mark.unit
    def test_session_drain_signal_filename_is_importable(self) -> None:
        """SESSION_DRAIN_SIGNAL_FILENAME is importable from devbench.constants without error."""
        import devbench.constants as _c

        assert hasattr(_c, "SESSION_DRAIN_SIGNAL_FILENAME")

    @pytest.mark.unit
    def test_session_drain_signal_filename_is_str(self) -> None:
        """SESSION_DRAIN_SIGNAL_FILENAME is of type str."""
        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME

        assert isinstance(SESSION_DRAIN_SIGNAL_FILENAME, str)

    @pytest.mark.unit
    def test_session_drain_signal_filename_is_non_empty(self) -> None:
        """SESSION_DRAIN_SIGNAL_FILENAME is a non-empty string (no accidental empty reset)."""
        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME

        assert len(SESSION_DRAIN_SIGNAL_FILENAME) > 0

    @pytest.mark.unit
    def test_session_drain_signal_filename_value(self) -> None:
        """SESSION_DRAIN_SIGNAL_FILENAME equals 'drain.signal' (spec 4.4.5 / 4.3.1)."""
        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME

        assert SESSION_DRAIN_SIGNAL_FILENAME == "drain.signal"

    @pytest.mark.unit
    def test_session_drain_signal_filename_has_no_path_separator(self) -> None:
        """SESSION_DRAIN_SIGNAL_FILENAME contains no '/' -- it is a bare filename, not a path."""
        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME

        assert "/" not in SESSION_DRAIN_SIGNAL_FILENAME


class TestAllowedAgentModelShortNamesHaikuRemoval:
    """AC-198-1: ALLOWED_AGENT_MODEL_SHORT_NAMES must equal frozenset({'opus', 'sonnet'}).

    Haiku must not be present -- any value containing 'haiku' in the
    per-agent YAML block is rejected at config-load time (caylent-solutions/devbench#198).
    """

    @pytest.mark.unit
    def test_haiku_not_in_allowed_short_names(self) -> None:
        """AC-198-1: 'haiku' must not be in ALLOWED_AGENT_MODEL_SHORT_NAMES."""
        from devbench.constants import ALLOWED_AGENT_MODEL_SHORT_NAMES

        assert "haiku" not in ALLOWED_AGENT_MODEL_SHORT_NAMES, (
            "ALLOWED_AGENT_MODEL_SHORT_NAMES must not contain 'haiku'. "
            "Haiku is rejected at config-load time (caylent-solutions/devbench#198)."
        )

    @pytest.mark.unit
    def test_allowed_short_names_equals_opus_sonnet(self) -> None:
        """AC-198-1: ALLOWED_AGENT_MODEL_SHORT_NAMES must equal exactly {'opus', 'sonnet'}."""
        from devbench.constants import ALLOWED_AGENT_MODEL_SHORT_NAMES

        expected = frozenset({"opus", "sonnet"})
        assert expected == ALLOWED_AGENT_MODEL_SHORT_NAMES, (
            f"ALLOWED_AGENT_MODEL_SHORT_NAMES must equal frozenset({{'opus', 'sonnet'}}); "
            f"got {ALLOWED_AGENT_MODEL_SHORT_NAMES!r} (caylent-solutions/devbench#198)."
        )

    @pytest.mark.unit
    def test_opus_in_allowed_short_names(self) -> None:
        """'opus' must remain in ALLOWED_AGENT_MODEL_SHORT_NAMES."""
        from devbench.constants import ALLOWED_AGENT_MODEL_SHORT_NAMES

        assert "opus" in ALLOWED_AGENT_MODEL_SHORT_NAMES

    @pytest.mark.unit
    def test_sonnet_in_allowed_short_names(self) -> None:
        """'sonnet' must remain in ALLOWED_AGENT_MODEL_SHORT_NAMES."""
        from devbench.constants import ALLOWED_AGENT_MODEL_SHORT_NAMES

        assert "sonnet" in ALLOWED_AGENT_MODEL_SHORT_NAMES


class TestSkillIterateUntilPerfectConstants:
    """Issue #204: constants that bound the skill iterate-until-perfect loop."""

    @pytest.mark.unit
    def test_max_iterations_is_positive_int(self) -> None:
        from devbench.constants import SKILL_MAX_ITERATIONS

        assert isinstance(SKILL_MAX_ITERATIONS, int)
        assert SKILL_MAX_ITERATIONS > 0, "SKILL_MAX_ITERATIONS must bound the loop with a positive value"

    @pytest.mark.unit
    def test_quality_threshold_is_non_negative_int(self) -> None:
        from devbench.constants import SKILL_QUALITY_THRESHOLD

        assert isinstance(SKILL_QUALITY_THRESHOLD, int)
        assert SKILL_QUALITY_THRESHOLD >= 0

    @pytest.mark.unit
    def test_state_dir_name_is_under_devbench(self) -> None:
        from devbench.constants import SKILL_STATE_DIR_NAME

        assert isinstance(SKILL_STATE_DIR_NAME, str)
        assert SKILL_STATE_DIR_NAME.startswith(".devbench/"), (
            "SKILL_STATE_DIR_NAME must live under .devbench/ to share the state-directory convention"
        )

    @pytest.mark.unit
    def test_audit_tags_match_skill_grammar(self) -> None:
        from devbench.constants import (
            SKILL_AUDIT_MAX_ITERATIONS_REACHED,
            SKILL_AUDIT_QUALITY_THRESHOLD_REACHED,
        )

        for tag in (SKILL_AUDIT_MAX_ITERATIONS_REACHED, SKILL_AUDIT_QUALITY_THRESHOLD_REACHED):
            assert isinstance(tag, str)
            assert tag.startswith("[SKILL_"), f"audit tag must start with '[SKILL_' (got {tag!r})"
            assert tag.endswith("]"), f"audit tag must end with ']' (got {tag!r})"


class TestRecoveryProbeModelConstant:
    """AC-E2-F1-S2-T2-4: RECOVERY_PROBE_MODEL lands as claude-opus-5, deliberately
    diverging from the source branch's claude-opus-4-8 per spec decision D-2."""

    @pytest.mark.unit
    def test_recovery_probe_model_is_opus_5(self) -> None:
        from devbench.constants import RECOVERY_PROBE_MODEL

        assert RECOVERY_PROBE_MODEL == "claude-opus-5"

    @pytest.mark.unit
    def test_recovery_probe_model_is_str(self) -> None:
        from devbench.constants import RECOVERY_PROBE_MODEL

        assert isinstance(RECOVERY_PROBE_MODEL, str)

    @pytest.mark.unit
    def test_source_comment_records_d2_divergence_from_branch_value(self) -> None:
        """The constant's comment must name the superseded branch value
        claude-opus-4-8 so the D-2 divergence is not lost to a future editor."""
        from pathlib import Path

        module_path = Path(__file__).resolve().parent.parent / "src" / "devbench" / "constants.py"
        source_text = module_path.read_text(encoding="utf-8")
        marker_index = source_text.index('RECOVERY_PROBE_MODEL: str = "claude-opus-5"')
        preceding_comment = source_text[max(0, marker_index - 800) : marker_index]
        assert "claude-opus-4-8" in preceding_comment, (
            "RECOVERY_PROBE_MODEL's comment must name the superseded branch value "
            "'claude-opus-4-8' to record the deliberate D-2 divergence."
        )
        assert "D-2" in preceding_comment, (
            "RECOVERY_PROBE_MODEL's comment must cite spec decision D-2, which "
            "moved the default model lineup to Opus 5."
        )


class TestRecoveryProbeTimeoutAndRequestSizeConstants:
    """AC-E2-F4-S2-T2-1, AC-E2-F4-S2-T2-2, AC-E2-F4-S2-T2-4: recovery probe
    timeout and request-size constants exist, carry the correct type, and
    satisfy the argument guards devbench.quota.recovery_probe enforces
    (timeout_seconds > 0, request_size_tokens >= 1)."""

    @pytest.mark.unit
    def test_recovery_probe_timeout_seconds_value_and_type(self) -> None:
        from devbench.constants import RECOVERY_PROBE_TIMEOUT_SECONDS

        assert RECOVERY_PROBE_TIMEOUT_SECONDS == 30.0
        assert isinstance(RECOVERY_PROBE_TIMEOUT_SECONDS, float)

    @pytest.mark.unit
    def test_recovery_probe_request_size_tokens_value_and_type(self) -> None:
        from devbench.constants import RECOVERY_PROBE_REQUEST_SIZE_TOKENS

        assert RECOVERY_PROBE_REQUEST_SIZE_TOKENS == 1
        assert isinstance(RECOVERY_PROBE_REQUEST_SIZE_TOKENS, int)

    @pytest.mark.unit
    def test_constants_satisfy_recovery_probe_argument_guards(self) -> None:
        """devbench.quota.recovery_probe raises ValueError when
        timeout_seconds <= 0 or request_size_tokens < 1 (quota.py lines
        999-1001). The exported constants must actually satisfy those
        guards, not merely restate the literals."""
        from devbench.constants import (
            RECOVERY_PROBE_REQUEST_SIZE_TOKENS,
            RECOVERY_PROBE_TIMEOUT_SECONDS,
        )

        assert RECOVERY_PROBE_TIMEOUT_SECONDS > 0
        assert RECOVERY_PROBE_REQUEST_SIZE_TOKENS >= 1
