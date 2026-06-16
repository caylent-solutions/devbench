"""EnvSanitizer: mode-resolved deny set + AWS passthrough + bedrock exports (AC-4).

Covers Section 3.6.1 / FR-21 with the two-mode billing contract:

- SUBSCRIPTION mode (default) strips the direct-Anthropic-API vars AND the
  Bedrock/Vertex routing vars so inference bills against the Claude Code
  subscription, NOT the API. AWS WORKLOAD creds (AWS_ACCESS_KEY_ID,
  AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_PROFILE) and AWS_REGION /
  AWS_DEFAULT_REGION ALWAYS pass through -- the supervised orchestrator runs
  live AWS terratests and AWS creds do not route Claude billing.
- BEDROCK mode strips only the direct-Anthropic-API vars, ALLOWS the Bedrock
  route, and EXPORTS the claude-CLI Bedrock vars (CLAUDE_CODE_USE_BEDROCK +
  AWS_REGION + the bedrock model id). AWS creds pass through here too.

The deny set is non-removable in BOTH modes; the parser-level whitelist
fail-fast is covered in ``test_supervise_config.py``.
"""

from __future__ import annotations

import pytest

from devbench.constants import (
    SUPERVISE_AWS_PASSTHROUGH_ENV_VARS,
    SUPERVISE_BEDROCK_MODEL_VAR,
    SUPERVISE_BEDROCK_REGION_VAR,
    SUPERVISE_BEDROCK_USE_FLAG_VAR,
    SUPERVISE_BILLING_MODE_BEDROCK,
    SUPERVISE_BILLING_MODE_SUBSCRIPTION,
    resolve_supervise_deny_vars,
)
from devbench.supervise import EnvSanitizer


@pytest.mark.unit
class TestEnvSanitizerSubscriptionDenies:
    """Subscription mode removes every routing var but keeps AWS creds (FR-21)."""

    def test_all_subscription_deny_vars_stripped(self) -> None:
        deny = resolve_supervise_deny_vars(SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        source = dict.fromkeys(deny, "secret-value")
        source["PATH"] = "/usr/bin"
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        result = sanitizer.build(
            source_env=source,
            workspace_root="/ws",
            session_name="nightly",
            import_model="claude-opus-4-8",
        )
        for var in deny:
            assert var not in result, f"{var} must be stripped from the subscription session env"
        assert result["PATH"] == "/usr/bin", "non-denied vars are preserved"

    def test_anthropic_api_key_stripped_in_subscription(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        result = sanitizer.build(
            source_env={"ANTHROPIC_API_KEY": "sk-ant-xyz"},
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert "ANTHROPIC_API_KEY" not in result

    def test_bedrock_routing_vars_stripped_in_subscription(self) -> None:
        # The claude-CLI Bedrock/Vertex routing vars are denied in subscription
        # mode so they cannot route inference off-subscription.
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        result = sanitizer.build(
            source_env={
                "DEVBENCH_USE_BEDROCK": "1",
                SUPERVISE_BEDROCK_USE_FLAG_VAR: "1",
                "CLAUDE_CODE_USE_VERTEX": "1",
                "PATH": "/usr/bin",
            },
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert "DEVBENCH_USE_BEDROCK" not in result
        assert SUPERVISE_BEDROCK_USE_FLAG_VAR not in result
        assert "CLAUDE_CODE_USE_VERTEX" not in result


@pytest.mark.unit
class TestEnvSanitizerAwsPassthrough:
    """AWS workload creds + region ALWAYS pass through (both modes)."""

    @pytest.mark.parametrize(
        "billing_mode",
        [SUPERVISE_BILLING_MODE_SUBSCRIPTION, SUPERVISE_BILLING_MODE_BEDROCK],
    )
    def test_aws_creds_and_region_pass_through(self, billing_mode: str) -> None:
        source = {var: f"value-{var}" for var in SUPERVISE_AWS_PASSTHROUGH_ENV_VARS}
        source["PATH"] = "/usr/bin"
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=billing_mode)
        result = sanitizer.build(
            source_env=source,
            workspace_root="/ws",
            session_name="n",
            import_model="claude-opus-4-8",
        )
        for var in SUPERVISE_AWS_PASSTHROUGH_ENV_VARS:
            assert result.get(var) == f"value-{var}", (
                f"{var} must pass through to the supervised session env in {billing_mode} mode"
            )

    def test_aws_creds_not_in_any_mode_deny_set(self) -> None:
        for mode in (SUPERVISE_BILLING_MODE_SUBSCRIPTION, SUPERVISE_BILLING_MODE_BEDROCK):
            deny = resolve_supervise_deny_vars(mode)
            for var in SUPERVISE_AWS_PASSTHROUGH_ENV_VARS:
                assert var not in deny, f"{var} must NOT be denied in {mode} mode"


@pytest.mark.unit
class TestEnvSanitizerBedrockMode:
    """Bedrock mode allows the Bedrock route and exports the claude-CLI vars."""

    def test_bedrock_mode_does_not_deny_bedrock_route(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_BEDROCK)
        result = sanitizer.build(
            source_env={"PATH": "/usr/bin", "AWS_REGION": "us-east-1"},
            workspace_root="/ws",
            session_name="n",
            import_model="us.anthropic.claude-opus-4-1-v1",
        )
        # CLAUDE_CODE_USE_BEDROCK is EXPORTED so the CLI routes inference to Bedrock.
        assert result[SUPERVISE_BEDROCK_USE_FLAG_VAR] == "1"

    def test_bedrock_mode_exports_region_and_model(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_BEDROCK)
        result = sanitizer.build(
            source_env={"PATH": "/usr/bin", "AWS_REGION": "eu-west-1"},
            workspace_root="/ws",
            session_name="n",
            import_model="us.anthropic.claude-opus-4-1-v1",
        )
        # The CLI reads ANTHROPIC_MODEL for the Bedrock model id; the region is the
        # operator's AWS_REGION (which already passed through).
        assert result[SUPERVISE_BEDROCK_MODEL_VAR] == "us.anthropic.claude-opus-4-1-v1"
        assert result[SUPERVISE_BEDROCK_REGION_VAR] == "eu-west-1"

    def test_bedrock_mode_strips_direct_anthropic_api_vars(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_BEDROCK)
        result = sanitizer.build(
            source_env={
                "ANTHROPIC_API_KEY": "sk-ant-xyz",
                "ANTHROPIC_AUTH_TOKEN": "tok",
                "ANTHROPIC_API_URL": "https://x",
                "ANTHROPIC_BASE_URL": "https://y",
                "PATH": "/usr/bin",
                "AWS_REGION": "us-east-1",
            },
            workspace_root="/ws",
            session_name="n",
            import_model="us.anthropic.claude-opus-4-1-v1",
        )
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_URL", "ANTHROPIC_BASE_URL"):
            assert var not in result, f"{var} (direct Anthropic API) must be stripped in bedrock mode"


@pytest.mark.unit
class TestEnvSanitizerExtraDeny:
    """Configured extra deny vars are stripped on top of the mode deny set."""

    def test_extra_deny_vars_also_stripped(self) -> None:
        source = {"CUSTOM_SECRET": "x", "KEEP": "y"}
        sanitizer = EnvSanitizer(extra_deny_vars=("CUSTOM_SECRET",), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        result = sanitizer.build(
            source_env=source,
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert "CUSTOM_SECRET" not in result
        assert result["KEEP"] == "y"


@pytest.mark.unit
class TestEnvSanitizerExports:
    """The three scope-conveyance vars are exported (Section 5.6, FR-8)."""

    def test_scope_conveyance_vars_exported(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        result = sanitizer.build(
            source_env={"PATH": "/usr/bin"},
            workspace_root="/ws/root",
            session_name="nightly",
            import_model="claude-opus-4-8",
        )
        assert result["DEVBENCH_WORKSPACE_ROOT"] == "/ws/root"
        assert result["DEVBENCH_SESSION_NAME"] == "nightly"
        assert result["DEVBENCH_CLAUDE_MODEL"] == "claude-opus-4-8"

    def test_does_not_mutate_source(self) -> None:
        source = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "x"}
        EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION).build(
            source_env=source,
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert source["ANTHROPIC_API_KEY"] == "x", "source env must not be mutated"


@pytest.mark.unit
class TestEnvSanitizerFailFast:
    """A required export is missing -> fail fast (no silent leak)."""

    def test_import_model_required(self) -> None:
        with pytest.raises(ValueError, match="import_model"):
            EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION).build(
                source_env={},
                workspace_root="/ws",
                session_name="n",
                import_model="",
            )

    def test_workspace_root_required(self) -> None:
        with pytest.raises(ValueError, match="workspace_root"):
            EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION).build(
                source_env={},
                workspace_root="",
                session_name="n",
                import_model="opus",
            )

    def test_bedrock_mode_requires_region_for_export(self) -> None:
        # Bedrock mode must export AWS_REGION to the CLI; if neither AWS_REGION nor
        # AWS_DEFAULT_REGION is present in the source env there is no region to
        # route Bedrock to -- fail fast rather than launch a session that cannot bill.
        with pytest.raises(ValueError, match="region"):
            EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_BEDROCK).build(
                source_env={"PATH": "/usr/bin"},
                workspace_root="/ws",
                session_name="n",
                import_model="us.anthropic.claude-opus-4-1-v1",
            )

    def test_invalid_billing_mode_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="billing_mode"):
            EnvSanitizer(extra_deny_vars=(), billing_mode="bogus")
