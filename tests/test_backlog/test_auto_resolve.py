"""Tests for src/devbench/backlog/auto_resolve.py.

Covers the auto-resolve engine entry point:
- Disabled path: engine returns advise-only payload byte-for-byte unchanged.
- Enabled + whitelisted remediation: applies and logs [AUTO_RESOLVED].
- Destructive verbs: hard-rejected by the whitelist guard, never applied.
- Whitelist membership predicate: reusable predicate is correct.
"""

from __future__ import annotations

import pytest

from devbench.constants import (
    AUTO_RESOLVE_AUDIT_STRING,
    AUTO_RESOLVE_DESTRUCTIVE_VERBS,
    AUTO_RESOLVE_WHITELIST,
)


@pytest.mark.unit
class TestAutoResolveWhitelistConstant:
    """The whitelist contains exactly the expected non-destructive verbs."""

    def test_whitelist_is_frozenset(self) -> None:
        assert isinstance(AUTO_RESOLVE_WHITELIST, frozenset)

    def test_whitelist_contains_requeue(self) -> None:
        assert "re-queue" in AUTO_RESOLVE_WHITELIST

    def test_whitelist_contains_set_status_in_queue(self) -> None:
        assert "set-status in-queue" in AUTO_RESOLVE_WHITELIST

    def test_whitelist_contains_reconcile_cascade(self) -> None:
        assert "reconcile-cascade" in AUTO_RESOLVE_WHITELIST

    def test_whitelist_contains_restart_signal(self) -> None:
        assert "restart-signal" in AUTO_RESOLVE_WHITELIST

    def test_whitelist_does_not_contain_decline(self) -> None:
        assert "decline" not in AUTO_RESOLVE_WHITELIST

    def test_whitelist_does_not_contain_mark_done(self) -> None:
        assert "mark-done" not in AUTO_RESOLVE_WHITELIST

    def test_whitelist_does_not_contain_force_status(self) -> None:
        assert "force-status" not in AUTO_RESOLVE_WHITELIST


@pytest.mark.unit
class TestAutoResolveDestructiveVerbs:
    """The destructive-verb exclusion set contains the correct members."""

    def test_destructive_verbs_is_frozenset(self) -> None:
        assert isinstance(AUTO_RESOLVE_DESTRUCTIVE_VERBS, frozenset)

    def test_decline_is_destructive(self) -> None:
        assert "decline" in AUTO_RESOLVE_DESTRUCTIVE_VERBS

    def test_mark_done_is_destructive(self) -> None:
        assert "mark-done" in AUTO_RESOLVE_DESTRUCTIVE_VERBS

    def test_force_status_is_destructive(self) -> None:
        assert "force-status" in AUTO_RESOLVE_DESTRUCTIVE_VERBS

    def test_destructive_and_whitelist_are_disjoint(self) -> None:
        """No verb can appear in both sets -- that would be a contradiction."""
        overlap = AUTO_RESOLVE_WHITELIST & AUTO_RESOLVE_DESTRUCTIVE_VERBS
        assert not overlap, f"Unexpected overlap between whitelist and destructive verbs: {overlap}"


@pytest.mark.unit
class TestAutoResolveAuditString:
    """The verbatim audit string constant is present and correct."""

    def test_audit_string_is_correct(self) -> None:
        assert AUTO_RESOLVE_AUDIT_STRING == "[AUTO_RESOLVED]"


@pytest.mark.unit
class TestIsWhitelisted:
    """The is_whitelisted predicate correctly classifies remediations."""

    def test_whitelisted_requeue(self) -> None:
        from devbench.backlog.auto_resolve import is_whitelisted

        assert is_whitelisted("re-queue") is True

    def test_whitelisted_set_status_in_queue(self) -> None:
        from devbench.backlog.auto_resolve import is_whitelisted

        assert is_whitelisted("set-status in-queue") is True

    def test_whitelisted_reconcile_cascade(self) -> None:
        from devbench.backlog.auto_resolve import is_whitelisted

        assert is_whitelisted("reconcile-cascade") is True

    def test_whitelisted_restart_signal(self) -> None:
        from devbench.backlog.auto_resolve import is_whitelisted

        assert is_whitelisted("restart-signal") is True

    @pytest.mark.parametrize("destructive_verb", ["decline", "mark-done", "force-status"])
    def test_destructive_verb_not_whitelisted(self, destructive_verb: str) -> None:
        from devbench.backlog.auto_resolve import is_whitelisted

        assert is_whitelisted(destructive_verb) is False

    def test_unknown_verb_not_whitelisted(self) -> None:
        from devbench.backlog.auto_resolve import is_whitelisted

        assert is_whitelisted("unknown-action") is False


@pytest.mark.unit
class TestAutoResolveDisabledPath:
    """When auto_resolve.enabled is False the engine returns the payload unchanged."""

    def test_disabled_returns_payload_unchanged(self) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=False, max_attempts=3)
        payload = "advise-only output: re-queue task T-001"
        result = apply_auto_resolve(
            task_id="T-001",
            signature="abc123",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        assert result == payload

    def test_disabled_does_not_log_audit_string(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=False, max_attempts=3)
        apply_auto_resolve(
            task_id="T-001",
            signature="abc123",
            remediation="re-queue",
            advise_only_payload="payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        assert AUTO_RESOLVE_AUDIT_STRING not in captured.out
        assert AUTO_RESOLVE_AUDIT_STRING not in captured.err


@pytest.mark.unit
class TestAutoResolveEnabledWhitelistedPath:
    """When enabled and remediation is whitelisted, engine logs [AUTO_RESOLVED]."""

    @pytest.mark.parametrize("remediation", ["re-queue", "set-status in-queue", "reconcile-cascade", "restart-signal"])
    def test_whitelisted_remediation_logs_audit_string(
        self, remediation: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        apply_auto_resolve(
            task_id="TASK-123",
            signature="sig456",
            remediation=remediation,
            advise_only_payload="original payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        assert AUTO_RESOLVE_AUDIT_STRING in captured.out or AUTO_RESOLVE_AUDIT_STRING in captured.err

    def test_audit_log_contains_task_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        apply_auto_resolve(
            task_id="TASK-456",
            signature="sig789",
            remediation="re-queue",
            advise_only_payload="payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "TASK-456" in combined

    def test_audit_log_contains_signature(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        apply_auto_resolve(
            task_id="TASK-789",
            signature="my-sig-xyz",
            remediation="re-queue",
            advise_only_payload="payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "my-sig-xyz" in combined

    def test_audit_log_contains_remediation(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        apply_auto_resolve(
            task_id="TASK-001",
            signature="sig001",
            remediation="reconcile-cascade",
            advise_only_payload="payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "reconcile-cascade" in combined


@pytest.mark.unit
class TestAutoResolveDestructiveVerbRejection:
    """Destructive verbs MUST raise and never be applied, even when enabled=True."""

    @pytest.mark.parametrize("destructive_verb", ["decline", "mark-done", "force-status"])
    def test_destructive_verb_raises_when_enabled(self, destructive_verb: str) -> None:
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        with pytest.raises(ValueError, match=destructive_verb):
            apply_auto_resolve(
                task_id="TASK-001",
                signature="sig001",
                remediation=destructive_verb,
                advise_only_payload="payload",
                config=cfg,
            )

    @pytest.mark.parametrize("destructive_verb", ["decline", "mark-done", "force-status"])
    def test_destructive_verb_raises_when_disabled(self, destructive_verb: str) -> None:
        """Destructive verb guard fires regardless of enabled flag."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=False, max_attempts=3)
        with pytest.raises(ValueError, match=destructive_verb):
            apply_auto_resolve(
                task_id="TASK-001",
                signature="sig001",
                remediation=destructive_verb,
                advise_only_payload="payload",
                config=cfg,
            )
