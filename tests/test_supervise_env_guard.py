"""EnvSanitizer: strip always-deny API/Bedrock vars + export scope conveyance (AC-4).

Covers Section 3.6.1 / FR-21: the supervised session env must NOT carry any
API-key / Bedrock-routing var (or it would silently bill against the API and
defeat the whole feature), and MUST export the three scope-conveyance vars
(Section 5.6). The always-deny set is non-removable; the parser-level whitelist
fail-fast is covered in ``test_supervise_config.py``.
"""

from __future__ import annotations

import pytest

from devbench.constants import SUPERVISE_ALWAYS_DENY_ENV_VARS
from devbench.supervise import EnvSanitizer


@pytest.mark.unit
class TestEnvSanitizerDenies:
    """Every always-deny var is removed from the built session env (FR-21)."""

    def test_all_always_deny_vars_stripped(self) -> None:
        source = dict.fromkeys(SUPERVISE_ALWAYS_DENY_ENV_VARS, "secret-value")
        source["PATH"] = "/usr/bin"
        sanitizer = EnvSanitizer(extra_deny_vars=())
        result = sanitizer.build(
            source_env=source,
            workspace_root="/ws",
            session_name="nightly",
            import_model="claude-opus-4-8",
        )
        for var in SUPERVISE_ALWAYS_DENY_ENV_VARS:
            assert var not in result, f"{var} must be stripped from the session env"
        assert result["PATH"] == "/usr/bin", "non-denied vars are preserved"

    def test_extra_deny_vars_also_stripped(self) -> None:
        source = {"CUSTOM_SECRET": "x", "KEEP": "y"}
        sanitizer = EnvSanitizer(extra_deny_vars=("CUSTOM_SECRET",))
        result = sanitizer.build(
            source_env=source,
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert "CUSTOM_SECRET" not in result
        assert result["KEEP"] == "y"

    def test_specific_anthropic_api_key_stripped(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=())
        result = sanitizer.build(
            source_env={"ANTHROPIC_API_KEY": "sk-ant-xyz"},
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert "ANTHROPIC_API_KEY" not in result


@pytest.mark.unit
class TestEnvSanitizerExports:
    """The three scope-conveyance vars are exported (Section 5.6, FR-8)."""

    def test_scope_conveyance_vars_exported(self) -> None:
        sanitizer = EnvSanitizer(extra_deny_vars=())
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
        EnvSanitizer(extra_deny_vars=()).build(
            source_env=source,
            workspace_root="/ws",
            session_name="n",
            import_model="opus",
        )
        assert source["ANTHROPIC_API_KEY"] == "x", "source env must not be mutated"


@pytest.mark.unit
class TestEnvSanitizerFailFast:
    """A denied var that cannot be removed fails fast (no silent leak)."""

    def test_import_model_required(self) -> None:
        with pytest.raises(ValueError, match="import_model"):
            EnvSanitizer(extra_deny_vars=()).build(
                source_env={},
                workspace_root="/ws",
                session_name="n",
                import_model="",
            )

    def test_workspace_root_required(self) -> None:
        with pytest.raises(ValueError, match="workspace_root"):
            EnvSanitizer(extra_deny_vars=()).build(
                source_env={},
                workspace_root="",
                session_name="n",
                import_model="opus",
            )
