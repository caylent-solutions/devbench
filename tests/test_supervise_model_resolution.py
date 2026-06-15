"""Model + effort resolution: --model > supervise.model > orchestrate.model (AC-8).

Covers Section 5.4 / FR-19 / D-3: the resolution order is the CLI flag, then
``supervise.model``, then ``orchestrate.model``, then fail-fast.
``DEVBENCH_CLAUDE_MODEL`` is NOT consulted (it routes API billing). ``haiku`` is
rejected via the reused ``validate_agent_model_value``. Effort resolves
``--effort`` > ``supervise.effort`` > ``xhigh``.
"""

from __future__ import annotations

import pytest

from devbench.supervise import (
    SuperviseModelUnsetError,
    resolve_supervise_effort,
    resolve_supervise_model,
)


@pytest.mark.unit
class TestModelResolutionOrder:
    """AC-8: --model > supervise.model > orchestrate.model."""

    def test_cli_flag_wins(self) -> None:
        model = resolve_supervise_model(
            cli_model="opus",
            supervise_model="sonnet",
            orchestrate_model="claude-opus-4-8",
            use_bedrock=False,
        )
        assert model == "opus"

    def test_supervise_model_when_no_flag(self) -> None:
        model = resolve_supervise_model(
            cli_model=None,
            supervise_model="sonnet",
            orchestrate_model="claude-opus-4-8",
            use_bedrock=False,
        )
        assert model == "sonnet"

    def test_orchestrate_model_when_neither(self) -> None:
        model = resolve_supervise_model(
            cli_model=None,
            supervise_model=None,
            orchestrate_model="claude-opus-4-8",
            use_bedrock=False,
        )
        assert model == "claude-opus-4-8"

    def test_all_unset_fails_fast(self) -> None:
        with pytest.raises(SuperviseModelUnsetError, match="no model"):
            resolve_supervise_model(
                cli_model=None,
                supervise_model=None,
                orchestrate_model=None,
                use_bedrock=False,
            )

    def test_empty_strings_treated_as_unset(self) -> None:
        with pytest.raises(SuperviseModelUnsetError):
            resolve_supervise_model(
                cli_model="",
                supervise_model="   ",
                orchestrate_model="",
                use_bedrock=False,
            )


@pytest.mark.unit
class TestModelValidation:
    """AC-8: haiku is rejected (reuses validate_agent_model_value)."""

    def test_haiku_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"[Hh]aiku"):
            resolve_supervise_model(
                cli_model="haiku",
                supervise_model=None,
                orchestrate_model=None,
                use_bedrock=False,
            )

    def test_valid_short_name_ok(self) -> None:
        assert (
            resolve_supervise_model(cli_model="opus", supervise_model=None, orchestrate_model=None, use_bedrock=False)
            == "opus"
        )


@pytest.mark.unit
class TestEffortResolution:
    """AC-8: --effort > supervise.effort > xhigh; invalid effort fails fast."""

    def test_cli_effort_wins(self) -> None:
        assert resolve_supervise_effort(cli_effort="high", supervise_effort="xhigh") == "high"

    def test_supervise_effort_when_no_flag(self) -> None:
        assert resolve_supervise_effort(cli_effort=None, supervise_effort="medium") == "medium"

    def test_invalid_effort_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="effort"):
            resolve_supervise_effort(cli_effort="turbo", supervise_effort="xhigh")
