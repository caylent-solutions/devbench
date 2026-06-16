"""Tests for the ``supervise:`` config block (devbench supervise feature).

Covers AC-12: the ``supervise:`` block parses with defaults, validates against
config-schema.json, an unknown ``supervise.*`` key is rejected by
``jsonschema.validate``, and a config that tries to whitelist an always-deny env
var fails fast (Section 5.1, 5.2, FR-19, FR-21).

All inputs are config/fixture-driven, not hardcoded against magic literals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.config_loader import (
    SuperviseConfig,
    _parse_supervise_config,
    load_runtime_config,
)
from devbench.constants import (
    SUPERVISE_ALWAYS_DENY_ENV_VARS,
    SUPERVISE_AWS_PASSTHROUGH_ENV_VARS,
    SUPERVISE_BILLING_MODE_BEDROCK,
    SUPERVISE_BILLING_MODE_SUBSCRIPTION,
    SUPERVISE_DEFAULT_BILLING_MODE,
    SUPERVISE_DETECTION_PATTERNS_DEFAULT,
    SUPERVISE_EFFORT_DEFAULT,
    SUPERVISE_INJECTABLE_COMMANDS_DEFAULT,
    SUPERVISE_RESTART_MAX_ATTEMPTS_DEFAULT,
    SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT,
    SUPERVISE_TIMEOUT_READY_PROMPT_SECONDS_DEFAULT,
)


def _write_config(path: Path, extra: str = "") -> None:
    """Write a minimal valid devbench.yaml, optionally with an extra block."""
    content = "repos:\n  org/repo:\n    default_branch: main\n"
    if extra:
        content += extra
    path.write_text(content, encoding="utf-8")


@pytest.mark.unit
class TestSuperviseConfigDefaults:
    """SuperviseConfig defaults mirror Section 5.1."""

    def test_default_model_is_none(self) -> None:
        assert SuperviseConfig().model is None

    def test_default_effort(self) -> None:
        assert SuperviseConfig().effort == SUPERVISE_EFFORT_DEFAULT

    def test_default_screen_name_prefix(self) -> None:
        assert SuperviseConfig().screen_name_prefix == SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT

    def test_default_ready_prompt_timeout(self) -> None:
        assert SuperviseConfig().timeouts.ready_prompt_seconds == SUPERVISE_TIMEOUT_READY_PROMPT_SECONDS_DEFAULT

    def test_default_poll_interval(self) -> None:
        # The stop-wait registry-poll / attach tail re-read cadence (Section 4.2/4.7).
        from devbench.constants import SUPERVISE_TIMEOUT_POLL_INTERVAL_SECONDS_DEFAULT

        assert SuperviseConfig().timeouts.poll_interval_seconds == SUPERVISE_TIMEOUT_POLL_INTERVAL_SECONDS_DEFAULT

    def test_default_command_invocation_timeout(self) -> None:
        # The safety timeout (seconds) bounding the short, non-interactive
        # subprocess.run command invocations (screen -ls / screen -X quit /
        # <tool> --version). Config-driven per FR-19 / Section 7.4 (no literals).
        from devbench.constants import SUPERVISE_TIMEOUT_COMMAND_INVOCATION_SECONDS_DEFAULT

        assert (
            SuperviseConfig().timeouts.command_invocation_seconds
            == SUPERVISE_TIMEOUT_COMMAND_INVOCATION_SECONDS_DEFAULT
        )

    def test_default_command_submit_quiet_timeout(self) -> None:
        # The no-output quiet window (seconds) the slash-command submission waits
        # for the autocomplete menu render to settle before sending Enter.
        from devbench.constants import SUPERVISE_TIMEOUT_COMMAND_SUBMIT_QUIET_SECONDS_DEFAULT

        assert (
            SuperviseConfig().timeouts.command_submit_quiet_seconds
            == SUPERVISE_TIMEOUT_COMMAND_SUBMIT_QUIET_SECONDS_DEFAULT
        )

    def test_default_command_submit_settle_timeout(self) -> None:
        # The max render-settle wait (seconds) bounding the slash-command submission
        # quiescence loop before Enter is sent regardless.
        from devbench.constants import SUPERVISE_TIMEOUT_COMMAND_SUBMIT_SETTLE_SECONDS_DEFAULT

        assert (
            SuperviseConfig().timeouts.command_submit_settle_seconds
            == SUPERVISE_TIMEOUT_COMMAND_SUBMIT_SETTLE_SECONDS_DEFAULT
        )

    def test_default_progress_stall_timeout(self) -> None:
        # The progress watchdog stall window: how long the orchestrator log may go
        # without growing (and no long-op heartbeat) before the supervisor
        # classifies a work-progress stall and auto-restarts (design point 2).
        from devbench.constants import SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT

        assert SuperviseConfig().timeouts.progress_stall_seconds == SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT

    def test_default_progress_stall_timeout_is_ten_minutes(self) -> None:
        from devbench.constants import SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT

        assert SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT == 600

    def test_default_long_op_heartbeat_interval(self) -> None:
        # The long-running-command heartbeat cadence: the verify-ac runner emits a
        # benign [LONG_OP_HEARTBEAT] line to the orchestrator log on this interval
        # so a genuine 30-60 min terraform apply / go test keeps the progress
        # watchdog's log-growth signal advancing (design point 4, mechanism a).
        from devbench.constants import SUPERVISE_LONG_OP_HEARTBEAT_SECONDS_DEFAULT

        assert SuperviseConfig().timeouts.long_op_heartbeat_seconds == SUPERVISE_LONG_OP_HEARTBEAT_SECONDS_DEFAULT

    def test_long_op_heartbeat_is_strictly_below_progress_stall(self) -> None:
        # The heartbeat MUST fire before the watchdog trips, else a genuine long op
        # would false-stall. The defaults must satisfy heartbeat < progress_stall.
        from devbench.constants import (
            SUPERVISE_LONG_OP_HEARTBEAT_SECONDS_DEFAULT,
            SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT,
        )

        assert SUPERVISE_LONG_OP_HEARTBEAT_SECONDS_DEFAULT < SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT

    def test_default_restart_max_attempts(self) -> None:
        assert SuperviseConfig().restart.max_attempts == SUPERVISE_RESTART_MAX_ATTEMPTS_DEFAULT

    def test_default_quota_max_resumes_is_none(self) -> None:
        # null -> falls through to DEFAULT_MAX_QUOTA_RESUMES / env (Section 5.1).
        assert SuperviseConfig().quota.max_quota_resumes is None

    def test_default_detection_patterns(self) -> None:
        patterns = SuperviseConfig().detection_patterns
        assert patterns.ready_prompt == SUPERVISE_DETECTION_PATTERNS_DEFAULT["ready_prompt"]
        assert patterns.reset_at == SUPERVISE_DETECTION_PATTERNS_DEFAULT["reset_at"]

    def test_default_injectable_commands(self) -> None:
        cmds = SuperviseConfig().injectable_commands
        assert cmds["orchestrate"] == SUPERVISE_INJECTABLE_COMMANDS_DEFAULT["orchestrate"]

    def test_default_injectable_commands_include_loop_continuation(self) -> None:
        # The turn-continuation re-inject (design point 6): when claude's
        # interactive turn ends with a unit still in progress, the supervisor
        # re-injects this command to deterministically re-drive the orchestrate loop
        # across the turn boundary (the root-cause gap was no re-inject at all).
        cmds = SuperviseConfig().injectable_commands
        assert "loop_continuation" in cmds
        assert cmds["loop_continuation"] == SUPERVISE_INJECTABLE_COMMANDS_DEFAULT["loop_continuation"]

    def test_default_detection_patterns_include_idle_input_prompt(self) -> None:
        # The idle-input / turn-end-awaiting-input prompt (design point 6): distinct
        # from working_prompt (esc to interrupt). Recognizing it lets the supervisor
        # re-inject the continuation instead of silently waiting for the watchdog.
        patterns = SuperviseConfig().detection_patterns
        assert patterns.idle_input_prompt == SUPERVISE_DETECTION_PATTERNS_DEFAULT["idle_input_prompt"]


@pytest.mark.unit
class TestSuperviseConfigParse:
    """_parse_supervise_config applies YAML overrides and validates."""

    def test_empty_raw_yields_defaults(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {})
        assert cfg.effort == SUPERVISE_EFFORT_DEFAULT
        assert cfg.model is None

    def test_model_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"model": "opus"})
        assert cfg.model == "opus"

    def test_effort_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"effort": "high"})
        assert cfg.effort == "high"

    def test_invalid_effort_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="effort"):
            _parse_supervise_config(Path("cfg.yaml"), {"effort": "ludicrous"})

    def test_invalid_resume_mode_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="resume_mode"):
            _parse_supervise_config(Path("cfg.yaml"), {"restart": {"resume_mode": "rewind"}})

    def test_timeout_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="ready_prompt_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"ready_prompt_seconds": 0}})

    def test_restart_max_attempts_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            _parse_supervise_config(Path("cfg.yaml"), {"restart": {"max_attempts": 0}})

    def test_timeout_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"ready_prompt_seconds": 300}})
        assert cfg.timeouts.ready_prompt_seconds == 300

    def test_optional_quota_timeouts_default_none(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {})
        assert cfg.timeouts.quota_poll_interval_seconds is None
        assert cfg.timeouts.quota_max_wait_seconds is None

    def test_optional_quota_timeouts_override(self) -> None:
        cfg = _parse_supervise_config(
            Path("cfg.yaml"),
            {"timeouts": {"quota_poll_interval_seconds": 120, "quota_max_wait_seconds": 7200}},
        )
        assert cfg.timeouts.quota_poll_interval_seconds == 120
        assert cfg.timeouts.quota_max_wait_seconds == 7200

    def test_progress_stall_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"progress_stall_seconds": 1200}})
        assert cfg.timeouts.progress_stall_seconds == 1200

    def test_progress_stall_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="progress_stall_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"progress_stall_seconds": 0}})

    def test_long_op_heartbeat_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"long_op_heartbeat_seconds": 90}})
        assert cfg.timeouts.long_op_heartbeat_seconds == 90

    def test_long_op_heartbeat_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="long_op_heartbeat_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"long_op_heartbeat_seconds": 0}})

    def test_poll_interval_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"poll_interval_seconds": 5}})
        assert cfg.timeouts.poll_interval_seconds == 5

    def test_poll_interval_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"poll_interval_seconds": 0}})

    def test_command_invocation_timeout_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"command_invocation_seconds": 45}})
        assert cfg.timeouts.command_invocation_seconds == 45

    def test_command_invocation_timeout_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="command_invocation_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"command_invocation_seconds": 0}})

    def test_command_submit_quiet_timeout_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"command_submit_quiet_seconds": 3}})
        assert cfg.timeouts.command_submit_quiet_seconds == 3

    def test_command_submit_quiet_timeout_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="command_submit_quiet_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"command_submit_quiet_seconds": 0}})

    def test_command_submit_settle_timeout_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"command_submit_settle_seconds": 15}})
        assert cfg.timeouts.command_submit_settle_seconds == 15

    def test_command_submit_settle_timeout_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="command_submit_settle_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"command_submit_settle_seconds": 0}})

    def test_optional_quota_timeout_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="quota_poll_interval_seconds"):
            _parse_supervise_config(Path("cfg.yaml"), {"timeouts": {"quota_poll_interval_seconds": 0}})

    def test_max_quota_resumes_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"quota": {"max_quota_resumes": 50}})
        assert cfg.quota.max_quota_resumes == 50

    def test_max_quota_resumes_below_minimum_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="max_quota_resumes"):
            _parse_supervise_config(Path("cfg.yaml"), {"quota": {"max_quota_resumes": 0}})

    def test_restart_max_attempts_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"restart": {"max_attempts": 9}})
        assert cfg.restart.max_attempts == 9

    def test_resume_mode_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"restart": {"resume_mode": "resume"}})
        assert cfg.restart.resume_mode == "resume"

    def test_log_tail_and_logging_overrides(self) -> None:
        cfg = _parse_supervise_config(
            Path("cfg.yaml"),
            {
                "log_tail": {"markers_clean": ["DONE"], "orchestrator_log_relpath": "x/y.log"},
                "logging": {"pty_log_relpath": "out.log", "redact_patterns": ["secret"]},
            },
        )
        assert cfg.log_tail.markers_clean == ("DONE",)
        assert cfg.log_tail.orchestrator_log_relpath == "x/y.log"
        assert cfg.logging.pty_log_relpath == "out.log"
        assert cfg.logging.redact_patterns == ("secret",)

    def test_screen_name_prefix_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"screen_name_prefix": "sv-"})
        assert cfg.screen_name_prefix == "sv-"

    def test_detection_pattern_override(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"detection_patterns": {"ready_prompt": "CUSTOM>"}})
        assert cfg.detection_patterns.ready_prompt == "CUSTOM>"
        # Unset patterns keep their defaults.
        assert cfg.detection_patterns.reset_at == SUPERVISE_DETECTION_PATTERNS_DEFAULT["reset_at"]

    def test_injectable_commands_extend_via_config(self) -> None:
        # FR-28: a new injectable command added only via config (no code change).
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"injectable_commands": {"compact": "/compact"}})
        assert cfg.injectable_commands["compact"] == "/compact"
        # Built-in defaults remain available.
        assert cfg.injectable_commands["orchestrate"] == SUPERVISE_INJECTABLE_COMMANDS_DEFAULT["orchestrate"]


@pytest.mark.unit
class TestSuperviseEnvDenyGuard:
    """Always-deny env vars cannot be whitelisted via config (FR-21, Section 5.2)."""

    @pytest.mark.parametrize("always_deny", list(SUPERVISE_ALWAYS_DENY_ENV_VARS))
    def test_whitelist_always_deny_via_negation_fails_fast(self, always_deny: str) -> None:
        # A config that tries to remove an always-deny var by negating it in the
        # deny-list is a fail-fast config error (Section 3.6.1). The always-deny set
        # is the UNION of both billing modes' routing vars; it is non-removable
        # regardless of the active billing mode.
        with pytest.raises(ValueError, match="always-deny"):
            _parse_supervise_config(Path("cfg.yaml"), {"env": {"deny_vars": [f"!{always_deny}"]}})

    @pytest.mark.parametrize("aws_var", list(SUPERVISE_AWS_PASSTHROUGH_ENV_VARS))
    def test_aws_creds_are_not_in_the_non_removable_guard(self, aws_var: str) -> None:
        # AWS workload creds + region are in NEITHER mode's deny set, so they are
        # NOT non-removable: a config may list them in deny_vars without tripping
        # the whitelist guard (they pass through by default).
        assert aws_var not in SUPERVISE_ALWAYS_DENY_ENV_VARS

    def test_extra_deny_vars_accepted(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"env": {"deny_vars": ["MY_SECRET_VAR"]}})
        assert "MY_SECRET_VAR" in cfg.env.deny_vars


@pytest.mark.unit
class TestSuperviseBillingMode:
    """supervise.billing_mode parses, validates, and defaults to subscription."""

    def test_default_billing_mode_is_subscription(self) -> None:
        assert SuperviseConfig().billing_mode == SUPERVISE_DEFAULT_BILLING_MODE
        assert SUPERVISE_DEFAULT_BILLING_MODE == SUPERVISE_BILLING_MODE_SUBSCRIPTION

    def test_absent_block_yields_subscription(self) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {})
        assert cfg.billing_mode == SUPERVISE_BILLING_MODE_SUBSCRIPTION

    @pytest.mark.parametrize("mode", [SUPERVISE_BILLING_MODE_SUBSCRIPTION, SUPERVISE_BILLING_MODE_BEDROCK])
    def test_valid_billing_mode_parsed(self, mode: str) -> None:
        cfg = _parse_supervise_config(Path("cfg.yaml"), {"billing_mode": mode})
        assert cfg.billing_mode == mode

    def test_invalid_billing_mode_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="billing_mode"):
            _parse_supervise_config(Path("cfg.yaml"), {"billing_mode": "free-lunch"})

    def test_billing_mode_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  billing_mode: bedrock\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.billing_mode == SUPERVISE_BILLING_MODE_BEDROCK

    def test_invalid_billing_mode_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  billing_mode: ludicrous\n")
        with pytest.raises(Exception, match=r"ludicrous|billing_mode|enum"):
            load_runtime_config(cfg_path, {})


@pytest.mark.unit
class TestSuperviseConfigSchema:
    """The supervise block validates through jsonschema in load_runtime_config (AC-12)."""

    def test_supervise_block_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  effort: high\n  screen_name_prefix: my-prefix-\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.effort == "high"
        assert runtime.supervise.screen_name_prefix == "my-prefix-"

    def test_unknown_supervise_key_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  not_a_real_key: 1\n")
        with pytest.raises(Exception, match=r"not_a_real_key|additional"):
            load_runtime_config(cfg_path, {})

    def test_invalid_effort_enum_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  effort: ludicrous\n")
        with pytest.raises(Exception, match=r"ludicrous|effort|enum"):
            load_runtime_config(cfg_path, {})

    def test_absent_supervise_block_yields_defaults(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path)
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.effort == SUPERVISE_EFFORT_DEFAULT

    def test_command_invocation_timeout_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    command_invocation_seconds: 90\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.timeouts.command_invocation_seconds == 90

    def test_command_invocation_timeout_below_minimum_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    command_invocation_seconds: 0\n")
        with pytest.raises(Exception, match=r"command_invocation_seconds|minimum|0"):
            load_runtime_config(cfg_path, {})

    def test_command_submit_quiet_timeout_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    command_submit_quiet_seconds: 4\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.timeouts.command_submit_quiet_seconds == 4

    def test_command_submit_quiet_timeout_below_minimum_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    command_submit_quiet_seconds: 0\n")
        with pytest.raises(Exception, match=r"command_submit_quiet_seconds|minimum|0"):
            load_runtime_config(cfg_path, {})

    def test_command_submit_settle_timeout_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    command_submit_settle_seconds: 20\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.timeouts.command_submit_settle_seconds == 20

    def test_command_submit_settle_timeout_below_minimum_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    command_submit_settle_seconds: 0\n")
        with pytest.raises(Exception, match=r"command_submit_settle_seconds|minimum|0"):
            load_runtime_config(cfg_path, {})

    def test_progress_stall_timeout_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    progress_stall_seconds: 1500\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.timeouts.progress_stall_seconds == 1500

    def test_progress_stall_timeout_below_minimum_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    progress_stall_seconds: 0\n")
        with pytest.raises(Exception, match=r"progress_stall_seconds|minimum|0"):
            load_runtime_config(cfg_path, {})

    def test_long_op_heartbeat_loads_via_runtime_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    long_op_heartbeat_seconds: 120\n")
        runtime = load_runtime_config(cfg_path, {})
        assert runtime.supervise.timeouts.long_op_heartbeat_seconds == 120

    def test_long_op_heartbeat_below_minimum_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "devbench.yaml"
        _write_config(cfg_path, "supervise:\n  timeouts:\n    long_op_heartbeat_seconds: 0\n")
        with pytest.raises(Exception, match=r"long_op_heartbeat_seconds|minimum|0"):
            load_runtime_config(cfg_path, {})
