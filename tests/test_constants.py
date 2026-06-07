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


class TestClaimBlockedPreclaim:
    """CLAIM_BLOCKED_PRECLAIM exit code constant (issue #241)."""

    @pytest.mark.unit
    def test_claim_blocked_preclaim_equals_44(self) -> None:
        """CLAIM_BLOCKED_PRECLAIM is defined as integer 44 (spec Section 5)."""
        from devbench.constants import CLAIM_BLOCKED_PRECLAIM

        assert CLAIM_BLOCKED_PRECLAIM == 44

    @pytest.mark.unit
    def test_claim_blocked_preclaim_is_int(self) -> None:
        """CLAIM_BLOCKED_PRECLAIM is an int, not a float or string."""
        from devbench.constants import CLAIM_BLOCKED_PRECLAIM

        assert isinstance(CLAIM_BLOCKED_PRECLAIM, int)

    @pytest.mark.unit
    def test_claim_blocked_preclaim_distinct_from_other_exit_codes(self) -> None:
        """CLAIM_BLOCKED_PRECLAIM is distinct from all other named exit codes."""
        from devbench.constants import (
            CLAIM_BLOCKED_PRECLAIM,
            GET_DIFF_NO_ATTRIBUTABLE,
            ORCHESTRATOR_RESTART_EXIT_CODE,
            SUBPROCESS_ERROR_EXIT_CODE,
        )

        assert CLAIM_BLOCKED_PRECLAIM != ORCHESTRATOR_RESTART_EXIT_CODE
        assert CLAIM_BLOCKED_PRECLAIM != SUBPROCESS_ERROR_EXIT_CODE
        assert CLAIM_BLOCKED_PRECLAIM != GET_DIFF_NO_ATTRIBUTABLE
        assert CLAIM_BLOCKED_PRECLAIM != 0
        assert CLAIM_BLOCKED_PRECLAIM != 1

    @pytest.mark.unit
    def test_blocked_target_repo_unresolved_marker_is_importable(self) -> None:
        """BLOCKED_TARGET_REPO_UNRESOLVED_MARKER is importable from devbench.constants."""
        import devbench.constants as _c

        assert hasattr(_c, "BLOCKED_TARGET_REPO_UNRESOLVED_MARKER")

    @pytest.mark.unit
    def test_blocked_target_repo_unresolved_marker_is_str(self) -> None:
        """BLOCKED_TARGET_REPO_UNRESOLVED_MARKER is a non-empty str."""
        from devbench.constants import BLOCKED_TARGET_REPO_UNRESOLVED_MARKER

        assert isinstance(BLOCKED_TARGET_REPO_UNRESOLVED_MARKER, str)
        assert len(BLOCKED_TARGET_REPO_UNRESOLVED_MARKER) > 0

    @pytest.mark.unit
    def test_blocked_target_repo_unresolved_marker_value(self) -> None:
        """BLOCKED_TARGET_REPO_UNRESOLVED_MARKER equals the verbatim tag string."""
        from devbench.constants import BLOCKED_TARGET_REPO_UNRESOLVED_MARKER

        assert BLOCKED_TARGET_REPO_UNRESOLVED_MARKER == "[BLOCKED_TARGET_REPO_UNRESOLVED]"


class TestGetDiffNoAttributableConstant:
    """GET_DIFF_NO_ATTRIBUTABLE exit code constant (issue #247)."""

    def test_get_diff_no_attributable_equals_45(self) -> None:
        """GET_DIFF_NO_ATTRIBUTABLE is defined as integer 45."""
        from devbench.constants import GET_DIFF_NO_ATTRIBUTABLE

        assert GET_DIFF_NO_ATTRIBUTABLE == 45

    def test_get_diff_no_attributable_is_int(self) -> None:
        """GET_DIFF_NO_ATTRIBUTABLE is an int, not a float or string."""
        from devbench.constants import GET_DIFF_NO_ATTRIBUTABLE

        assert isinstance(GET_DIFF_NO_ATTRIBUTABLE, int)

    def test_get_diff_no_attributable_distinct_from_other_exit_codes(self) -> None:
        """GET_DIFF_NO_ATTRIBUTABLE is distinct from all other named exit codes."""
        from devbench.constants import (
            GET_DIFF_NO_ATTRIBUTABLE,
            ORCHESTRATOR_RESTART_EXIT_CODE,
            SUBPROCESS_ERROR_EXIT_CODE,
        )

        assert GET_DIFF_NO_ATTRIBUTABLE != ORCHESTRATOR_RESTART_EXIT_CODE
        assert GET_DIFF_NO_ATTRIBUTABLE != SUBPROCESS_ERROR_EXIT_CODE
        assert GET_DIFF_NO_ATTRIBUTABLE != 0
        assert GET_DIFF_NO_ATTRIBUTABLE != 1


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


class TestDefaultModelRatesOpus48:
    """AC-254-2: DEFAULT_MODEL_RATES contains claude-opus-4-8 entry and mirror comment is updated."""

    @pytest.mark.unit
    def test_opus_48_in_default_model_rates(self) -> None:
        """DEFAULT_MODEL_RATES['claude-opus-4-8'] equals ModelRates(input=5.0, output=25.0)."""
        from devbench.constants import DEFAULT_MODEL_RATES, ModelRates

        assert "claude-opus-4-8" in DEFAULT_MODEL_RATES, (
            "claude-opus-4-8 must be present in DEFAULT_MODEL_RATES (AC-254-2)"
        )
        assert DEFAULT_MODEL_RATES["claude-opus-4-8"] == ModelRates(input=5.0, output=25.0), (
            "claude-opus-4-8 rate must be ModelRates(input=5.0, output=25.0) per D-254-1"
        )

    @pytest.mark.unit
    def test_mirror_comment_reads_opus_48(self) -> None:
        """The fallback mirror comment in constants.py reads 'mirrors Opus 4.8 list'."""
        import inspect

        import devbench.constants as _c

        source = inspect.getsource(_c)
        assert "mirrors Opus 4.8 list" in source, (
            "The fallback comment must read 'mirrors Opus 4.8 list' after the update (AC-254-2)"
        )
        assert "mirrors Opus 4.7 list" not in source, (
            "The stale 'mirrors Opus 4.7 list' comment must be replaced with 'mirrors Opus 4.8 list'"
        )

    @pytest.mark.unit
    def test_existing_opus_entries_retained(self) -> None:
        """AC-254a-1: 4.7/4.6/4.5 rate entries are retained unchanged."""
        from devbench.constants import DEFAULT_MODEL_RATES, ModelRates

        assert DEFAULT_MODEL_RATES["claude-opus-4-7"] == ModelRates(input=5.0, output=25.0)
        assert DEFAULT_MODEL_RATES["claude-opus-4-6"] == ModelRates(input=5.0, output=25.0)
        assert DEFAULT_MODEL_RATES["claude-opus-4-5"] == ModelRates(input=5.0, output=25.0)

    @pytest.mark.unit
    def test_issue_223_clause_preserved(self) -> None:
        """AC-254a-1: The #223 clause is preserved verbatim in the fallback comment."""
        import inspect

        import devbench.constants as _c

        source = inspect.getsource(_c)
        assert "#223" in source, "The #223 clause must be preserved in the fallback comment (AC-254a-1)"


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


class TestQuotaHandlingConstants:
    """Issue #234, #254: RECOVERY_PROBE_MODEL and QUOTA_HANDLING_DEFAULT_ENABLED constants."""

    @pytest.mark.unit
    def test_recovery_probe_model_is_importable(self) -> None:
        """RECOVERY_PROBE_MODEL is importable from devbench.constants without error."""
        import devbench.constants as _c

        assert hasattr(_c, "RECOVERY_PROBE_MODEL")

    @pytest.mark.unit
    def test_recovery_probe_model_is_non_empty_string(self) -> None:
        """RECOVERY_PROBE_MODEL is a non-empty str naming the recovery-probe model."""
        from devbench.constants import RECOVERY_PROBE_MODEL

        assert isinstance(RECOVERY_PROBE_MODEL, str)
        assert len(RECOVERY_PROBE_MODEL) > 0

    @pytest.mark.unit
    def test_recovery_probe_model_value(self) -> None:
        """RECOVERY_PROBE_MODEL equals 'claude-opus-4-8' per issue #254."""
        from devbench.constants import RECOVERY_PROBE_MODEL

        assert RECOVERY_PROBE_MODEL == "claude-opus-4-8", "RECOVERY_PROBE_MODEL must be 'claude-opus-4-8' (issue #254)"

    @pytest.mark.unit
    def test_quota_handling_default_enabled_is_importable(self) -> None:
        """QUOTA_HANDLING_DEFAULT_ENABLED is importable from devbench.constants."""
        import devbench.constants as _c

        assert hasattr(_c, "QUOTA_HANDLING_DEFAULT_ENABLED")

    @pytest.mark.unit
    def test_quota_handling_default_enabled_is_bool(self) -> None:
        """QUOTA_HANDLING_DEFAULT_ENABLED is of type bool."""
        from devbench.constants import QUOTA_HANDLING_DEFAULT_ENABLED

        assert isinstance(QUOTA_HANDLING_DEFAULT_ENABLED, bool)

    @pytest.mark.unit
    def test_quota_handling_default_enabled_is_true(self) -> None:
        """QUOTA_HANDLING_DEFAULT_ENABLED equals True (issue #234: enabled by default)."""
        from devbench.constants import QUOTA_HANDLING_DEFAULT_ENABLED

        assert QUOTA_HANDLING_DEFAULT_ENABLED is True, (
            "QUOTA_HANDLING_DEFAULT_ENABLED must be True so quota wait-and-resume is active by default (issue #234)"
        )


class TestContinuationBudgetConstants:
    """Issue #262 (E10-F1-S3): bounded continuation budget constants.

    AC-1: DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS is an int with a
          positive unset-safe default value.
    AC-2: ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX is the
          verbatim audit string.
    AC-2: ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE is a
          distinct non-zero int differing from ORCHESTRATOR_RESTART_EXIT_CODE.
    """

    @pytest.mark.unit
    def test_default_max_turn_end_continuations_is_importable(self) -> None:
        """DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS is importable from devbench.constants."""
        import devbench.constants as _c

        assert hasattr(_c, "DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS")

    @pytest.mark.unit
    def test_default_max_turn_end_continuations_is_positive_int(self) -> None:
        """DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS is a positive int."""
        from devbench.constants import DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS

        assert isinstance(DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS, int)
        assert DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS > 0

    @pytest.mark.unit
    def test_exhausted_audit_prefix_is_importable(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX is importable."""
        import devbench.constants as _c

        assert hasattr(_c, "ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX")

    @pytest.mark.unit
    def test_exhausted_audit_prefix_is_verbatim_string(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX equals the verbatim audit tag."""
        from devbench.constants import ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX

        assert ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX == (
            "[ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED]"
        )

    @pytest.mark.unit
    def test_exhausted_exit_code_is_importable(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE is importable."""
        import devbench.constants as _c

        assert hasattr(_c, "ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE")

    @pytest.mark.unit
    def test_exhausted_exit_code_is_nonzero_int(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE is a non-zero int."""
        from devbench.constants import ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE

        assert isinstance(ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE, int)
        assert ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE != 0

    @pytest.mark.unit
    def test_exhausted_exit_code_differs_from_restart_exit_code(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE must differ from ORCHESTRATOR_RESTART_EXIT_CODE."""
        from devbench.constants import (
            ORCHESTRATOR_RESTART_EXIT_CODE,
            ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE,
        )

        assert ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE != ORCHESTRATOR_RESTART_EXIT_CODE, (
            "Exhaustion exit code must differ from auto-restart exit code so the wrapping loop "
            "never misclassifies fail-fast as auto-restart"
        )

    @pytest.mark.unit
    def test_exhausted_exit_code_differs_from_claim_blocked_preclaim(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE must differ from CLAIM_BLOCKED_PRECLAIM."""
        from devbench.constants import (
            CLAIM_BLOCKED_PRECLAIM,
            ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE,
        )

        assert ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE != CLAIM_BLOCKED_PRECLAIM

    @pytest.mark.unit
    def test_exhausted_exit_code_differs_from_get_diff_no_attributable(self) -> None:
        """ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE must differ from GET_DIFF_NO_ATTRIBUTABLE."""
        from devbench.constants import (
            GET_DIFF_NO_ATTRIBUTABLE,
            ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE,
        )

        assert ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE != GET_DIFF_NO_ATTRIBUTABLE


class TestInactivityTimeoutConstants:
    """Issue #262 (E10-F2-S1): per-message inactivity timeout constants.

    AC-2: DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS is a positive float
          serving as the unset-safe default when neither env nor YAML supplies a value.
    AC-3: ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX is the verbatim audit tag.
    """

    @pytest.mark.unit
    def test_default_inactivity_timeout_is_importable(self) -> None:
        """DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS is importable from devbench.constants."""
        import devbench.constants as _c

        assert hasattr(_c, "DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS"), (
            "DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS must be defined in devbench.constants"
        )

    @pytest.mark.unit
    def test_default_inactivity_timeout_is_positive_float(self) -> None:
        """DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS is a positive float."""
        from devbench.constants import DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS

        assert isinstance(DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS, float), (
            f"Expected float, got {type(DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS).__name__}"
        )
        assert DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS > 0.0, (
            f"Expected a positive float; got {DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS}"
        )

    @pytest.mark.unit
    def test_inactivity_timeout_audit_prefix_is_importable(self) -> None:
        """ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX is importable from devbench.constants."""
        import devbench.constants as _c

        assert hasattr(_c, "ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX"), (
            "ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX must be defined in devbench.constants"
        )

    @pytest.mark.unit
    def test_inactivity_timeout_audit_prefix_is_verbatim_string(self) -> None:
        """ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX equals the verbatim audit tag."""
        from devbench.constants import ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX

        assert ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX == "[ORCHESTRATOR_INACTIVITY_TIMEOUT]", (
            f"Audit prefix must equal '[ORCHESTRATOR_INACTIVITY_TIMEOUT]'; "
            f"got {ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX!r}"
        )


@pytest.mark.unit
class TestAutoResolveConstants:
    """E11-F1-S1-T1: auto-resolve engine constants (spec Section 4 E11-F1-S1)."""

    def test_env_var_name_is_importable(self) -> None:
        from devbench.constants import DEVBENCH_AUTO_RESOLVE_ENABLED_ENV

        assert DEVBENCH_AUTO_RESOLVE_ENABLED_ENV == "DEVBENCH_AUTO_RESOLVE_ENABLED"

    def test_default_enabled_is_false(self) -> None:
        from devbench.constants import DEFAULT_AUTO_RESOLVE_ENABLED

        assert DEFAULT_AUTO_RESOLVE_ENABLED is False

    def test_audit_string_is_correct(self) -> None:
        from devbench.constants import AUTO_RESOLVE_AUDIT_STRING

        assert AUTO_RESOLVE_AUDIT_STRING == "[AUTO_RESOLVED]"

    def test_whitelist_is_frozenset_of_strings(self) -> None:
        from devbench.constants import AUTO_RESOLVE_WHITELIST

        assert isinstance(AUTO_RESOLVE_WHITELIST, frozenset)
        assert all(isinstance(v, str) for v in AUTO_RESOLVE_WHITELIST)

    def test_whitelist_only_contains_non_destructive_verbs(self) -> None:
        from devbench.constants import AUTO_RESOLVE_DESTRUCTIVE_VERBS, AUTO_RESOLVE_WHITELIST

        assert not AUTO_RESOLVE_WHITELIST & AUTO_RESOLVE_DESTRUCTIVE_VERBS

    def test_destructive_verbs_is_frozenset_of_strings(self) -> None:
        from devbench.constants import AUTO_RESOLVE_DESTRUCTIVE_VERBS

        assert isinstance(AUTO_RESOLVE_DESTRUCTIVE_VERBS, frozenset)
        assert all(isinstance(v, str) for v in AUTO_RESOLVE_DESTRUCTIVE_VERBS)

    def test_default_max_attempts_is_positive_int(self) -> None:
        from devbench.constants import DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS

        assert isinstance(DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS, int)
        assert DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS > 0

    def test_max_attempts_env_var_name_is_correct(self) -> None:
        from devbench.constants import DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV

        assert DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV == "DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS"

    def test_escalated_string_is_correct(self) -> None:
        from devbench.constants import AUTO_RESOLVE_ESCALATED_STRING

        assert AUTO_RESOLVE_ESCALATED_STRING == "[AUTO_RESOLVE_ESCALATED]", (
            f"Escalation audit string must equal '[AUTO_RESOLVE_ESCALATED]'; got {AUTO_RESOLVE_ESCALATED_STRING!r}"
        )
