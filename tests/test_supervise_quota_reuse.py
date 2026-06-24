"""AC-32: the interactive quota path REUSES the shared quota primitives (DRY).

Spec Section 4.9 / FR-15: the supervisor quota adapter is a THIN ADAPTER over the
existing SDK-path primitives -- it must NOT carry local copies of the wait loop,
the classifier, the resume cap, or the checkpoint. This module proves the SAME
callables are invoked by patching the REAL functions in their defining modules
and asserting the supervisor called them. If a future change reimplemented any
of these locally, these tests fail.

The callables proven reused:
- ``devbench.quota.wait_for_reset``           (the wait loop)
- ``devbench.quota.detect_quota_error``       (classification)
- ``devbench.quota.QuotaCheckpoint`` + ``save_checkpoint`` (checkpoint)
- ``devbench.cli._resolve_max_quota_resumes`` (the resume cap precedence)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.config_loader import SuperviseConfig
from devbench.supervise import DetectionPatterns, build_quota_waiter


def _patterns() -> DetectionPatterns:
    return DetectionPatterns(SuperviseConfig().detection_patterns)


@pytest.mark.unit
class TestQuotaWaiterReusesSharedPrimitives:
    """build_quota_waiter wires the REAL shared callables (no local copies, AC-32)."""

    def test_wait_for_reset_is_the_shared_callable(self, tmp_path: Path) -> None:
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        with (
            patch("devbench.quota.wait_for_reset") as mock_wait,
            patch("devbench.cli._resolve_max_quota_resumes", return_value=1000) as mock_cap,
            patch("devbench.quota.save_checkpoint") as mock_save,
        ):

            async def _coro(**_kwargs):
                return True

            mock_wait.side_effect = _coro

            waiter = build_quota_waiter(
                patterns=_patterns(),
                config=SuperviseConfig(),
                workspace_root=tmp_path,
                session_name="nightly",
            )
            decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=0)

        assert mock_wait.called
        assert mock_cap.called
        assert mock_save.called
        assert decision.expected_resume == reset_at

    def test_detect_quota_error_is_the_shared_callable(self, tmp_path: Path) -> None:
        with patch("devbench.quota.detect_quota_error", return_value=None) as mock_detect:
            waiter = build_quota_waiter(
                patterns=_patterns(),
                config=SuperviseConfig(),
                workspace_root=tmp_path,
                session_name="nightly",
            )
            waiter.classify_exit("some claude exit text")
        assert mock_detect.called

    def test_resume_cap_uses_shared_resolver(self, tmp_path: Path) -> None:
        reset_at = datetime.now(UTC) + timedelta(hours=1)
        with (
            patch("devbench.quota.wait_for_reset") as mock_wait,
            patch("devbench.cli._resolve_max_quota_resumes", return_value=1) as mock_cap,
            patch("devbench.quota.save_checkpoint"),
        ):

            async def _coro(**_kwargs):
                return True

            mock_wait.side_effect = _coro

            waiter = build_quota_waiter(
                patterns=_patterns(),
                config=SuperviseConfig(),
                workspace_root=tmp_path,
                session_name="nightly",
            )
            from devbench.supervise import QuotaDecision

            decision = waiter.wait_and_decide(reset_at=reset_at, resumes_used=1)
        assert mock_cap.called
        assert decision.action is QuotaDecision.FAULT


@pytest.mark.unit
class TestSuperviseQuotaConfigFallthrough:
    """supervise.quota.max_quota_resumes overrides via the shared resolver path."""

    def test_config_cap_is_honoured(self, tmp_path: Path) -> None:
        cfg = SuperviseConfig()
        with (
            patch("devbench.quota.wait_for_reset") as mock_wait,
            patch("devbench.cli._resolve_max_quota_resumes", return_value=5) as mock_cap,
            patch("devbench.quota.save_checkpoint"),
        ):

            async def _coro(**_kwargs):
                return True

            mock_wait.side_effect = _coro

            waiter = build_quota_waiter(
                patterns=_patterns(),
                config=cfg,
                workspace_root=tmp_path,
                session_name="nightly",
            )
            from devbench.supervise import QuotaDecision

            decision = waiter.wait_and_decide(
                reset_at=datetime.now(UTC) + timedelta(hours=1),
                resumes_used=4,
            )
        assert mock_cap.called
        assert decision.action is QuotaDecision.RESUME

    def test_config_override_exports_env_for_shared_resolver(self, tmp_path: Path, monkeypatch) -> None:
        from dataclasses import replace

        from devbench.config_loader import SuperviseQuotaConfig

        monkeypatch.delenv("DEVBENCH_MAX_QUOTA_RESUMES", raising=False)
        cfg = replace(SuperviseConfig(), quota=SuperviseQuotaConfig(max_quota_resumes=7))
        with (
            patch("devbench.quota.wait_for_reset") as mock_wait,
            patch("devbench.quota.save_checkpoint"),
        ):

            async def _coro(**_kwargs):
                return True

            mock_wait.side_effect = _coro

            waiter = build_quota_waiter(
                patterns=_patterns(),
                config=cfg,
                workspace_root=tmp_path,
                session_name="nightly",
            )
            from devbench.supervise import QuotaDecision

            decision = waiter.wait_and_decide(
                reset_at=datetime.now(UTC) + timedelta(hours=1),
                resumes_used=6,
            )
        assert os.environ.get("DEVBENCH_MAX_QUOTA_RESUMES") == "7"
        assert decision.action is QuotaDecision.RESUME


@pytest.mark.unit
class TestBuildQuotaWaiterBedrockMode:
    """build_quota_waiter propagates billing_mode; bedrock disables the wait."""

    def test_bedrock_mode_disables_subscription_wait(self, tmp_path: Path) -> None:
        from devbench.constants import SUPERVISE_BILLING_MODE_BEDROCK
        from devbench.supervise import QuotaDecision

        with (
            patch("devbench.quota.wait_for_reset") as mock_wait,
            patch("devbench.cli._resolve_max_quota_resumes", return_value=1000),
            patch("devbench.quota.save_checkpoint") as mock_save,
        ):
            waiter = build_quota_waiter(
                patterns=_patterns(),
                config=SuperviseConfig(),
                workspace_root=tmp_path,
                session_name="nightly",
                billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
            )
            decision = waiter.wait_and_decide(
                reset_at=datetime.now(UTC) + timedelta(hours=1),
                resumes_used=0,
            )
        assert not mock_wait.called
        assert not mock_save.called
        assert decision.action is QuotaDecision.FAULT
        assert decision.exit_reason == "quota-wait-disabled-bedrock"
