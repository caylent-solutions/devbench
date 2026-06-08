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


@pytest.mark.unit
class TestAutoResolveCatalogConsult:
    """Engine consults catalog for recurring signatures and records outcomes."""

    def setup_method(self) -> None:
        """Reset the module-level apply-count dict before each test."""
        from devbench.backlog import auto_resolve

        auto_resolve._apply_counts.clear()

    def test_apply_path_records_applied_outcome(self) -> None:
        """A learned (already-in-catalog) signature auto-applies and records 'applied'."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry, record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            # Pre-seed the catalog so the signature is already learned
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="cat-sig-apply",
                remediation="re-queue",
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=True, max_attempts=3)
            apply_auto_resolve(
                task_id="CAT-T1",
                signature="cat-sig-apply",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "cat-sig-apply")
            assert result is not None
            assert result.success_count == 2
            assert result.failure_count == 0

    def test_escalated_path_records_escalated_outcome(self) -> None:
        """Budget exhaustion records 'escalated' outcome in the catalog."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry, record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            # Pre-seed the catalog so the signature is already learned (max_attempts=1)
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="esc-sig",
                remediation="re-queue",
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=True, max_attempts=1)
            # Exhaust the budget (1 learned apply)
            apply_auto_resolve(
                task_id="CAT-ESC",
                signature="esc-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            # Trigger escalation on budget exhaustion
            apply_auto_resolve(
                task_id="CAT-ESC",
                signature="esc-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "esc-sig")
            assert result is not None
            # success_count is 2: the pre-seeded apply + the budget-exhausting apply
            assert result.success_count == 2
            assert result.failure_count == 0

    def test_disabled_path_does_not_record_to_catalog(self) -> None:
        """When disabled, no catalog entry is written."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=False, max_attempts=3)
            apply_auto_resolve(
                task_id="CAT-DISABLED",
                signature="dis-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "dis-sig")
            assert result is None

    def test_catalog_consult_recognizes_recurring_signature(self) -> None:
        """Engine recognizes a recurring (learned) signature and auto-applies it."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry, record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            # Pre-seed the catalog so the signature is already learned
            record_outcome(
                root,
                classification="AWAITING_DEPENDENCY",
                normalized_signature="recur-sig",
                remediation="re-queue",
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            # Apply 3 times on top of the seed
            for _ in range(3):
                apply_auto_resolve(
                    task_id="RECUR-T1",
                    signature="recur-sig",
                    remediation="re-queue",
                    advise_only_payload="payload",
                    config=cfg,
                    catalog_path=root,
                    classification="AWAITING_DEPENDENCY",
                )
            result = lookup_entry(root, "AWAITING_DEPENDENCY", "recur-sig")
            assert result is not None
            assert result.success_count == 4  # 1 seed + 3 auto-applied

    def test_catalog_consult_logs_recurring_true_on_second_apply(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Engine logs recurring=True when the signature was seen before (learned)."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            # Pre-seed so signature is already learned
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="recur-log-sig",
                remediation="re-queue",
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)

            # This call should auto-apply (recurring=True) since it is already in catalog
            apply_auto_resolve(
                task_id="RECUR-LOG-T1",
                signature="recur-log-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            captured = capsys.readouterr()
            combined = captured.out + captured.err
            assert "recurring=True" in combined

    def test_no_catalog_path_skips_catalog_silently(self) -> None:
        """When catalog_path is None, catalog consultation is skipped silently."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=3)
        # Must not raise when no catalog_path is given (backward compat)
        result = apply_auto_resolve(
            task_id="NO-CAT",
            signature="no-cat-sig",
            remediation="re-queue",
            advise_only_payload="payload",
            config=cfg,
        )
        assert result == "payload"

    def test_whitelist_miss_does_not_record_to_catalog(self) -> None:
        """Unknown non-destructive verbs do not create catalog entries."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=3)
            apply_auto_resolve(
                task_id="WL-MISS",
                signature="wl-sig",
                remediation="unknown-verb",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "wl-sig")
            assert result is None

    def test_composite_block_does_not_record_to_catalog(self) -> None:
        """Composite RUNTIME_DEGRADATION + structural blocker does not record catalog entry."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            apply_auto_resolve(
                task_id="COMP-CAT",
                signature="comp-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
                primary_blocker_state=BlockedTaskState.RUNTIME_DEGRADATION,
                structural_blocker_state=BlockedTaskState.AWAITING_DEPENDENCY,
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "comp-sig")
            assert result is None

    def test_novel_signature_routes_to_advise_only(self) -> None:
        """A novel (unrecognized) signature with catalog configured routes to advise-only."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            payload = "advise-only payload"
            result = apply_auto_resolve(
                task_id="NOVEL-ADVISE",
                signature="novel-unrecognized-sig",
                remediation="re-queue",
                advise_only_payload=payload,
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            assert result == payload

    def test_novel_signature_does_not_log_auto_resolved(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A novel signature must NOT log [AUTO_RESOLVED] -- it was routed to advise."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            apply_auto_resolve(
                task_id="NOVEL-NOLOG",
                signature="novel-nolog-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            captured = capsys.readouterr()
            combined = captured.out + captured.err
            assert AUTO_RESOLVE_AUDIT_STRING not in combined

    def test_novel_signature_records_novel_entry_in_catalog(self) -> None:
        """A novel signature is recorded in the catalog so the operator can confirm it."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import lookup_entry

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            apply_auto_resolve(
                task_id="NOVEL-REC",
                signature="novel-rec-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            result = lookup_entry(root, "RUNTIME_DEGRADATION", "novel-rec-sig")
            assert result is not None

    def test_learned_signature_proceeds_to_auto_apply(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A signature already in the catalog (learned) is auto-applied normally."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            # Pre-populate the catalog to simulate a learned signature
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="learned-sig",
                remediation="re-queue",
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            apply_auto_resolve(
                task_id="LEARNED-T1",
                signature="learned-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            captured = capsys.readouterr()
            combined = captured.out + captured.err
            assert AUTO_RESOLVE_AUDIT_STRING in combined

    def test_novel_signature_does_not_consume_budget(self) -> None:
        """Novel signature must not increment the per-(task, sig) apply counter."""
        import pathlib
        import tempfile

        from devbench.backlog import auto_resolve
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=2)
            apply_auto_resolve(
                task_id="NOVEL-BUDGET",
                signature="novel-budget-sig",
                remediation="re-queue",
                advise_only_payload="payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            key = ("NOVEL-BUDGET", "novel-budget-sig")
            assert auto_resolve._apply_counts.get(key, 0) == 0

    def test_novel_signature_without_catalog_still_auto_applies(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When no catalog_path is given, there is no novel-signature gate (backward compat)."""
        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        cfg = AutoResolveConfig(enabled=True, max_attempts=5)
        apply_auto_resolve(
            task_id="NO-CAT-APPLY",
            signature="any-sig",
            remediation="re-queue",
            advise_only_payload="payload",
            config=cfg,
        )
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert AUTO_RESOLVE_AUDIT_STRING in combined

    def test_novel_signature_second_call_still_advise_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A signature recorded as 'novel' on first call must still route to advise-only on second call.

        After the first encounter records a novel entry (success_count == 0), the
        second call must NOT auto-apply just because the entry exists in the catalog.
        Only entries with success_count > 0 (operator-confirmed learned patterns) are
        eligible for auto-apply (AC-1).
        """
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            # First call -- records novel entry and returns advise-only.
            result_first = apply_auto_resolve(
                task_id="NOVEL-SECOND",
                signature="novel-second-call-sig",
                remediation="re-queue",
                advise_only_payload="advise-payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            assert result_first == "advise-payload"
            captured = capsys.readouterr()
            assert AUTO_RESOLVE_AUDIT_STRING not in (captured.out + captured.err)

            # Second call -- novel entry exists in catalog but success_count == 0.
            # Must still return advise-only, never auto-apply.
            result_second = apply_auto_resolve(
                task_id="NOVEL-SECOND",
                signature="novel-second-call-sig",
                remediation="re-queue",
                advise_only_payload="advise-payload",
                config=cfg,
                catalog_path=root,
                classification="RUNTIME_DEGRADATION",
            )
            assert result_second == "advise-payload"
            captured = capsys.readouterr()
            assert AUTO_RESOLVE_AUDIT_STRING not in (captured.out + captured.err)


@pytest.mark.unit
class TestAutoResolveDestructiveNeverAutoInvariant:
    """Destructive remediations MUST never be auto-applied even when catalog marks them learned."""

    def setup_method(self) -> None:
        """Reset the module-level apply-count dict before each test."""
        from devbench.backlog import auto_resolve

        auto_resolve._apply_counts.clear()

    @pytest.mark.parametrize("destructive_verb", ["decline", "mark-done", "force-status"])
    def test_destructive_verb_with_catalog_entry_still_advises(self, destructive_verb: str) -> None:
        """Destructive verb is never auto-applied even when the catalog has a learned entry."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            # Manually inject a "learned" entry for the destructive verb into the catalog
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="destructive-sig",
                remediation=destructive_verb,
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            # Even with a learned catalog entry, a destructive verb must raise
            with pytest.raises(ValueError, match=destructive_verb):
                apply_auto_resolve(
                    task_id="DESTR-LEARNED",
                    signature="destructive-sig",
                    remediation=destructive_verb,
                    advise_only_payload="payload",
                    config=cfg,
                    catalog_path=root,
                    classification="RUNTIME_DEGRADATION",
                )

    @pytest.mark.parametrize("destructive_verb", ["decline", "mark-done", "force-status"])
    def test_destructive_verb_guard_precedes_catalog_lookup(self, destructive_verb: str) -> None:
        """Destructive guard fires before catalog consultation -- catalog is never consulted."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            cfg = AutoResolveConfig(enabled=True, max_attempts=5)
            # The catalog is empty -- yet a destructive verb still raises
            with pytest.raises(ValueError, match=destructive_verb):
                apply_auto_resolve(
                    task_id="DESTR-GUARD",
                    signature="destr-guard-sig",
                    remediation=destructive_verb,
                    advise_only_payload="payload",
                    config=cfg,
                    catalog_path=root,
                    classification="RUNTIME_DEGRADATION",
                )

    def test_destructive_guard_is_unconditional_regardless_of_config(self) -> None:
        """Destructive guard fires even when config.enabled is False."""
        import pathlib
        import tempfile

        from devbench.backlog.auto_resolve import AutoResolveConfig, apply_auto_resolve
        from devbench.backlog.operator_resolution_catalog import record_outcome

        with tempfile.TemporaryDirectory() as tmpdir:
            root = pathlib.Path(tmpdir)
            record_outcome(
                root,
                classification="RUNTIME_DEGRADATION",
                normalized_signature="destr-disabled-sig",
                remediation="decline",
                outcome="applied",
            )
            cfg = AutoResolveConfig(enabled=False, max_attempts=5)
            with pytest.raises(ValueError, match="decline"):
                apply_auto_resolve(
                    task_id="DESTR-DISABLED",
                    signature="destr-disabled-sig",
                    remediation="decline",
                    advise_only_payload="payload",
                    config=cfg,
                    catalog_path=root,
                    classification="RUNTIME_DEGRADATION",
                )
