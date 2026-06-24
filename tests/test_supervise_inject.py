"""CommandInjector: registry send + ack + unknown-name fail-fast (AC-11, FR-28).

Covers Section 5.3: ``CommandInjector.send(name)`` looks the literal up in the
config registry and drives it through the PtyDriver, then waits for the
working-prompt ack. An unknown name fails fast; a command added ONLY via config
(no code change) is sendable -- proving FR-28 extensibility.

A SLASH literal is submitted via the type -> render-settle -> single-Enter flow
(the ``/`` autocomplete menu swallows a premature newline from ``sendline``), so
the recorded ``sent`` sequence for a slash command is ``[literal, "\\r"]``. A
NON-slash literal keeps the legacy ``sendline`` (no trailing ``\\r``).
"""

from __future__ import annotations

import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench.config_loader import SuperviseDetectionPatternsConfig
from devbench.supervise import (
    CommandInjector,
    DetectionPatterns,
    PtyDriver,
    SuperviseUnknownCommandError,
)


def _slash_ack_child(*, ack_gate: str = r"\r") -> FakePexpectChild:
    """A child that emits the working-prompt ack only AFTER the submit Enter.

    The ack step is gated on the submit ``\\r`` (not the typed literal) so the
    ``wait_until_quiescent`` ``expect([r".+"])`` does NOT prematurely consume it:
    while only the typed literal is in ``sent`` the gate is unmet, so quiescence
    sees no step -> TIMEOUT -> settles (returns True); once ``submit`` records
    ``\\r`` the gated ack step becomes available for ``expect_working``.
    """
    return FakePexpectChild([_ScriptStep(emit="esc to interrupt", on_send=ack_gate)])


def _driver_with_ack() -> PtyDriver:
    return PtyDriver(child=_slash_ack_child(), patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))


@pytest.mark.unit
class TestCommandInjectorSend:
    """AC-11: send formats + sends a registry command and waits for ack."""

    def test_send_orchestrate(self) -> None:
        driver = _driver_with_ack()
        injector = CommandInjector(
            driver=driver,
            registry={"orchestrate": "/devbench-orchestrate:orchestrate"},
            ack_timeout_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        injector.send("orchestrate")
        assert driver.child.sent == ["/devbench-orchestrate:orchestrate", "\r"]

    def test_send_returns_literal(self) -> None:
        driver = _driver_with_ack()
        injector = CommandInjector(
            driver=driver,
            registry={"orchestrate": "/devbench-orchestrate:orchestrate"},
            ack_timeout_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        literal = injector.send("orchestrate")
        assert literal == "/devbench-orchestrate:orchestrate"


@pytest.mark.unit
class TestCommandInjectorUnknown:
    """AC-11: an unknown command name fails fast (no silent no-op)."""

    def test_unknown_name_raises(self) -> None:
        driver = _driver_with_ack()
        injector = CommandInjector(
            driver=driver,
            registry={"orchestrate": "/devbench-orchestrate:orchestrate"},
            ack_timeout_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        with pytest.raises(SuperviseUnknownCommandError, match="frobnicate"):
            injector.send("frobnicate")


@pytest.mark.unit
class TestCommandInjectorNonSlash:
    """A NON-slash literal keeps the legacy sendline (no trailing Enter)."""

    def test_non_slash_literal_uses_sendline(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="esc to interrupt", on_send="continue working")])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        injector = CommandInjector(
            driver=driver,
            registry={"nudge": "continue working"},
            ack_timeout_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        injector.send("nudge")
        assert child.sent == ["continue working"]


@pytest.mark.unit
class TestCommandInjectorExtensible:
    """AC-11/FR-28: a command added ONLY in config is sendable (no code change)."""

    def test_config_only_command_sendable(self) -> None:
        child = _slash_ack_child()
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        injector = CommandInjector(
            driver=driver,
            registry={"compact": "/compact"},
            ack_timeout_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        injector.send("compact")
        assert child.sent == ["/compact", "\r"]

    def test_placeholder_substitution(self) -> None:
        child = _slash_ack_child()
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        injector = CommandInjector(
            driver=driver,
            registry={"set_model": "/model {name}"},
            ack_timeout_seconds=5,
            command_submit_quiet_seconds=1,
            command_submit_settle_seconds=8,
        )
        literal = injector.send("set_model", name="sonnet")
        assert literal == "/model sonnet"
        assert child.sent == ["/model sonnet", "\r"]
