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
