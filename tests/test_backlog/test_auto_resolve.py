"""Tests for src/devbench/backlog/auto_resolve.py.

Covers the auto-resolve engine entry point:
- Disabled path: engine returns advise-only payload byte-for-byte unchanged.
- Enabled + whitelisted remediation: applies and logs [AUTO_RESOLVED].
- Destructive verbs: hard-rejected by the whitelist guard, never applied.
- Whitelist membership predicate: reusable predicate is correct.
- Budget enforcement: per (task, signature) attempt counter caps at max_attempts.
- Escalation audit: [AUTO_RESOLVE_ESCALATED] fires on budget exhaustion.
- Composite-block detection: RUNTIME_DEGRADATION + structural blocker routes to advise.
"""

from __future__ import annotations

import pytest

from devbench.backlog.proposal import BlockedTaskState
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

    def setup_method(self) -> None:
        """Reset the module-level apply-count dict before each test."""
        from devbench.backlog import auto_resolve

        auto_resolve._apply_counts.clear()

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
class TestAutoResolveUnknownVerbPath:
    """Unknown non-destructive verbs (not whitelisted, not destructive) stay advisory."""

    def setup_method(self) -> None:
        """Reset the module-level apply-count dict before each test."""
        from devbench.backlog import auto_resolve

        auto_resolve._apply_counts.clear()

    def test_unknown_verb_returns_advise_only(self) -> None:
        """An unknown non-destructive verb returns the advise-only payload unchanged."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        payload = "advise-only payload"
        result = apply_auto_resolve(
            task_id="TASK-UNK",
            signature="unk-sig",
            remediation="unknown-action",
            advise_only_payload=payload,
            config=cfg,
        )
        assert result == payload

    def test_unknown_verb_logs_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """An unknown non-destructive verb emits a WARNING to stderr."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        apply_auto_resolve(
            task_id="TASK-UNK",
            signature="unk-sig",
            remediation="unknown-action",
            advise_only_payload="payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "unknown-action" in captured.err


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


@pytest.mark.unit
class TestAutoResolveBudgetEnforcement:
    """Per-(task, signature) budget is honored exactly at max_attempts."""

    def setup_method(self) -> None:
        """Reset the module-level apply-count dict before each test."""
        from devbench.backlog import auto_resolve

        auto_resolve._apply_counts.clear()

    def test_budget_allows_up_to_max_attempts(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Engine auto-applies exactly max_attempts times before escalating."""
        from devbench.backlog.auto_resolve import (
            AUTO_RESOLVE_ESCALATED_STRING,
            AutoResolveConfig,
            apply_auto_resolve,
        )

        cfg = AutoResolveConfig(enabled=True, max_attempts=2)
        payload = "advise-only payload"

        # First two calls should succeed and log [AUTO_RESOLVED]
        for _ in range(2):
            result = apply_auto_resolve(
                task_id="BUDGET-T1",
                signature="budget-sig",
                remediation="re-queue",
                advise_only_payload=payload,
                config=cfg,
            )
            assert result == payload

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_AUDIT_STRING in combined
        assert AUTO_RESOLVE_ESCALATED_STRING not in combined

    def test_budget_escalates_on_attempt_exceeding_max(self, capsys: pytest.CaptureFixture[str]) -> None:
        """On the (max_attempts+1)th call the engine escalates and returns advise-only."""
        from devbench.backlog.auto_resolve import (
            AUTO_RESOLVE_ESCALATED_STRING,
            AutoResolveConfig,
            apply_auto_resolve,
        )

        cfg = AutoResolveConfig(enabled=True, max_attempts=2)
        payload = "advise-only payload"

        # Exhaust the budget
        for _ in range(2):
            apply_auto_resolve(
                task_id="BUDGET-T2",
                signature="exhaust-sig",
                remediation="re-queue",
                advise_only_payload=payload,
                config=cfg,
            )
        capsys.readouterr()  # Discard prior output

        # Next call must escalate
        result = apply_auto_resolve(
            task_id="BUDGET-T2",
            signature="exhaust-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        assert result == payload
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_ESCALATED_STRING in combined

    def test_escalation_audit_contains_task_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The escalation audit line includes the task_id."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=1)
        payload = "payload"

        # Exhaust the budget (1 attempt)
        apply_auto_resolve(
            task_id="AUDIT-TASK-99",
            signature="audit-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        capsys.readouterr()

        # Trigger escalation
        apply_auto_resolve(
            task_id="AUDIT-TASK-99",
            signature="audit-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "AUDIT-TASK-99" in combined

    def test_escalation_audit_contains_signature(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The escalation audit line includes the signature."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=1)
        payload = "payload"

        apply_auto_resolve(
            task_id="AUDIT-TASK-X",
            signature="unique-sig-abc123",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        capsys.readouterr()

        apply_auto_resolve(
            task_id="AUDIT-TASK-X",
            signature="unique-sig-abc123",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "unique-sig-abc123" in combined

    def test_escalation_audit_contains_attempt_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The escalation audit line includes the attempt count."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=1)
        payload = "payload"

        apply_auto_resolve(
            task_id="AUDIT-TASK-CNT",
            signature="cnt-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        capsys.readouterr()

        apply_auto_resolve(
            task_id="AUDIT-TASK-CNT",
            signature="cnt-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Attempt count should appear in the escalation log
        assert "1" in combined

    def test_budget_is_per_task_signature_pair(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Different (task, signature) pairs have independent budgets."""
        from devbench.backlog.auto_resolve import (
            AUTO_RESOLVE_ESCALATED_STRING,
            AutoResolveConfig,
            apply_auto_resolve,
        )

        cfg = AutoResolveConfig(enabled=True, max_attempts=1)
        payload = "payload"

        # Exhaust budget for pair A
        apply_auto_resolve(
            task_id="PAIR-T1",
            signature="sig-A",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        capsys.readouterr()

        # Pair B should still have its full budget
        apply_auto_resolve(
            task_id="PAIR-T1",
            signature="sig-B",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # sig-B's first call must NOT trigger escalation
        assert AUTO_RESOLVE_ESCALATED_STRING not in combined

    def test_escalated_call_returns_advise_only_not_error(self) -> None:
        """Escalation returns the advise-only payload -- it does not raise."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=1)
        payload = "my-advise-payload"

        apply_auto_resolve(
            task_id="ESC-T1",
            signature="esc-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        result = apply_auto_resolve(
            task_id="ESC-T1",
            signature="esc-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        assert result == payload


@pytest.mark.unit
class TestAutoResolveCompositeBlockDetection:
    """Composite RUNTIME_DEGRADATION + structural blocker routes to advise-only."""

    def setup_method(self) -> None:
        """Reset the module-level apply-count dict before each test."""
        from devbench.backlog import auto_resolve

        auto_resolve._apply_counts.clear()

    def test_composite_block_is_not_auto_applied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When primary=RUNTIME_DEGRADATION and structural blocker exists, do NOT auto-apply."""
        from devbench.backlog.auto_resolve import (
            AUTO_RESOLVE_ESCALATED_STRING,
            AutoResolveConfig,
            apply_auto_resolve,
        )

        cfg = AutoResolveConfig(enabled=True, max_attempts=5)
        payload = "advise payload"

        result = apply_auto_resolve(
            task_id="COMP-T1",
            signature="comp-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
            primary_blocker_state=BlockedTaskState.RUNTIME_DEGRADATION,
            structural_blocker_state=BlockedTaskState.AWAITING_DEPENDENCY,
        )
        assert result == payload
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Must NOT log [AUTO_RESOLVED] -- it was routed to advise
        assert AUTO_RESOLVE_AUDIT_STRING not in combined
        # Must NOT log escalation -- budget was not consumed
        assert AUTO_RESOLVE_ESCALATED_STRING not in combined

    def test_composite_block_does_not_consume_budget(self) -> None:
        """Composite-blocked calls must not increment the per-(task, sig) counter."""
        from devbench.backlog import auto_resolve
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=1)
        payload = "payload"

        # One composite-blocked call -- must not consume budget
        apply_auto_resolve(
            task_id="COMP-BUDGET-T1",
            signature="comp-budget-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
            primary_blocker_state=BlockedTaskState.RUNTIME_DEGRADATION,
            structural_blocker_state=BlockedTaskState.AWAITING_DEPENDENCY,
        )
        key = ("COMP-BUDGET-T1", "comp-budget-sig")
        assert auto_resolve._apply_counts.get(key, 0) == 0

    @pytest.mark.parametrize(
        "structural_state",
        [
            BlockedTaskState.AWAITING_DEPENDENCY,
            BlockedTaskState.HELD,
            BlockedTaskState.BLOCKED_ON_HELD,
            BlockedTaskState.OPERATOR_ACTION_REQUIRED,
        ],
    )
    def test_composite_block_with_various_structural_blockers(
        self, structural_state: BlockedTaskState, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Any structural blocker co-existing with RUNTIME_DEGRADATION blocks auto-apply."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=5)
        payload = "advise payload"

        result = apply_auto_resolve(
            task_id="COMP-VARIOUS",
            signature=f"sig-{structural_state.value}",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
            primary_blocker_state=BlockedTaskState.RUNTIME_DEGRADATION,
            structural_blocker_state=structural_state,
        )
        assert result == payload
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_AUDIT_STRING not in combined

    def test_runtime_degradation_without_structural_is_auto_applied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """RUNTIME_DEGRADATION alone (no structural co-blocker) is not a composite -- auto-apply proceeds."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=5)
        payload = "payload"

        apply_auto_resolve(
            task_id="DEGRADED-ONLY",
            signature="deg-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
            primary_blocker_state=BlockedTaskState.RUNTIME_DEGRADATION,
            structural_blocker_state=None,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_AUDIT_STRING in combined

    def test_non_composite_structural_block_is_auto_applied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A non-composite block (primary is not RUNTIME_DEGRADATION) is auto-applied normally."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=5)
        payload = "payload"

        apply_auto_resolve(
            task_id="NON-COMPOSITE",
            signature="nc-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
            primary_blocker_state=BlockedTaskState.AWAITING_DEPENDENCY,
            structural_blocker_state=None,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_AUDIT_STRING in combined

    def test_no_blocker_state_is_auto_applied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When no blocker state is supplied the engine auto-applies normally (backward compat)."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=5)
        payload = "payload"

        apply_auto_resolve(
            task_id="NO-STATE",
            signature="no-state-sig",
            remediation="re-queue",
            advise_only_payload=payload,
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_AUDIT_STRING in combined
