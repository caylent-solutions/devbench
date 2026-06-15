"""CommandInjector: registry send + ack + unknown-name fail-fast (AC-11, FR-28).

Covers Section 5.3: ``CommandInjector.send(name)`` looks the literal up in the
config registry, ``sendline``s it through the PtyDriver, and waits for the
working-prompt ack. An unknown name fails fast; a command added ONLY via config
(no code change) is sendable -- proving FR-28 extensibility.
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


def _driver_with_ack() -> PtyDriver:
    child = FakePexpectChild(
        [
            # The ack: a working-prompt line emitted only after the orchestrate
            # command has been sent.
            _ScriptStep(emit="esc to interrupt", on_send="orchestrate"),
        ]
    )
    return PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))


@pytest.mark.unit
class TestCommandInjectorSend:
    """AC-11: send formats + sends a registry command and waits for ack."""

    def test_send_orchestrate(self) -> None:
        driver = _driver_with_ack()
        injector = CommandInjector(
            driver=driver,
            registry={"orchestrate": "/devbench-orchestrate:orchestrate"},
            ack_timeout_seconds=5,
        )
        injector.send("orchestrate")
        assert driver.child.sent == ["/devbench-orchestrate:orchestrate"]

    def test_send_returns_literal(self) -> None:
        driver = _driver_with_ack()
        injector = CommandInjector(
            driver=driver,
            registry={"orchestrate": "/devbench-orchestrate:orchestrate"},
            ack_timeout_seconds=5,
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
        )
        with pytest.raises(SuperviseUnknownCommandError, match="frobnicate"):
            injector.send("frobnicate")


@pytest.mark.unit
class TestCommandInjectorExtensible:
    """AC-11/FR-28: a command added ONLY in config is sendable (no code change)."""

    def test_config_only_command_sendable(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="thinking", on_send="/compact")])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        injector = CommandInjector(
            driver=driver,
            registry={"compact": "/compact"},  # operator-added, no supervisor code touched
            ack_timeout_seconds=5,
        )
        injector.send("compact")
        assert child.sent == ["/compact"]

    def test_placeholder_substitution(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="thinking", on_send="/model sonnet")])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        injector = CommandInjector(
            driver=driver,
            registry={"set_model": "/model {name}"},
            ack_timeout_seconds=5,
        )
        literal = injector.send("set_model", name="sonnet")
        assert literal == "/model sonnet"
        assert child.sent == ["/model sonnet"]
