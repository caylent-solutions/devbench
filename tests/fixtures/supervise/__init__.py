"""Test doubles and fixtures for the ``devbench supervise`` feature (Section 10.0).

Exports :class:`FakePexpectChild`, a scriptable ``pexpect.spawn`` double the unit
tests use to drive the supervisor state machine deterministically with NO real
``claude`` and NO real ``screen``. The functional layer (Phase 5) uses the real
``pexpect`` against the executable ``stub-claude.py`` in this directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pexpect


@dataclass
class _ScriptStep:
    """One scripted interaction in a :class:`FakePexpectChild` session.

    Attributes:
        on_send: When set, this step only becomes active once a ``sendline``
            whose payload matches this regex has been issued (so a step can be
            gated on the supervisor injecting a specific command). ``None`` means
            the step is available from the start.
        emit: The text the child "prints"; it is matched against the pattern an
            :meth:`FakePexpectChild.expect` call supplies. The matched span lands
            in ``before``/``after`` exactly as ``pexpect`` populates them.
        eof: When ``True`` this step ends the session (subsequent ``expect``
            raises :class:`pexpect.EOF`).
        exitstatus: The child exit status reported after ``eof`` (``None`` while
            alive; an int once the child has exited).
    """

    emit: str
    on_send: str | None = None
    eof: bool = False
    exitstatus: int | None = None


class FakePexpectChild:
    """A scriptable ``pexpect.spawn`` double (Section 10.0).

    The double is driven by an ordered list of :class:`_ScriptStep`. Each
    :meth:`expect` call consumes the next *available* step (a step gated on
    ``on_send`` is skipped until the matching ``sendline`` has been issued) and
    matches its ``emit`` text against the supplied pattern list, returning the
    index of the first matching pattern (mirroring ``pexpect.spawn.expect``). An
    ``eof`` step makes ``expect`` raise :class:`pexpect.EOF`; when no step matches
    a pattern within the script, ``expect`` raises :class:`pexpect.TIMEOUT` so the
    supervisor's timeout handling is exercised without real time passing.

    It records every ``sendline`` payload in :attr:`sent` so a test can assert the
    exact slash command the :class:`~devbench.supervise.CommandInjector` injected.
    """

    def __init__(self, script: list[_ScriptStep] | None = None) -> None:
        self._script: list[_ScriptStep] = list(script or [])
        self._cursor = 0
        self.sent: list[str] = []
        self.before: str = ""
        self.after: str = ""
        self.exitstatus: int | None = None
        self.signalstatus: int | None = None
        self._alive = True
        self.closed = False
        self.terminated = False
        self.terminate_force: bool | None = None

    # ------------------------------------------------------------------
    # pexpect.spawn API surface the supervisor uses
    # ------------------------------------------------------------------

    def expect(self, patterns: list[str], timeout: int | None = None) -> int:
        """Return the index of the first *patterns* entry the next step matches.

        Args:
            patterns: Ordered regex patterns (same shape ``pexpect`` accepts).
            timeout: Accepted for API parity; the double never sleeps.

        Returns:
            The index into *patterns* of the first pattern matching the next
            scripted step's emitted text.

        Raises:
            pexpect.EOF: The next step is an ``eof`` step.
            pexpect.TIMEOUT: No remaining step matches any of *patterns*.
        """
        step = self._next_available_step()
        if step is None:
            raise pexpect.TIMEOUT("FakePexpectChild: no scripted step matched (timeout)")
        if step.eof:
            self._alive = False
            self.exitstatus = step.exitstatus
            self.before = step.emit
            self.after = ""
            self._cursor += 1
            raise pexpect.EOF("FakePexpectChild: scripted EOF")
        for index, pattern in enumerate(patterns):
            if re.search(pattern, step.emit):
                self.before = step.emit
                self.after = step.emit
                self._cursor += 1
                return index
        # The step exists but matched none of the requested patterns: from the
        # caller's perspective this expect found nothing -> a timeout.
        raise pexpect.TIMEOUT("FakePexpectChild: scripted step matched no requested pattern")

    def sendline(self, payload: str = "") -> int:
        """Record *payload* (as the supervisor would write it) and report bytes."""
        self.sent.append(payload)
        return len(payload) + 1

    def send(self, payload: str = "") -> int:
        """Record a raw ``send`` payload (no newline) and report bytes."""
        self.sent.append(payload)
        return len(payload)

    def terminate(self, force: bool = False) -> bool:
        """Mark the child terminated (records *force*); report success."""
        self.terminated = True
        self.terminate_force = force
        self._alive = False
        return True

    def close(self, force: bool = True) -> None:
        """Mark the child closed."""
        self.closed = True
        self._alive = False

    def isalive(self) -> bool:
        """Return whether the scripted child is still alive."""
        return self._alive

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _next_available_step(self) -> _ScriptStep | None:
        """Return the next step whose ``on_send`` gate (if any) has been met."""
        while self._cursor < len(self._script):
            step = self._script[self._cursor]
            if step.on_send is not None and not any(re.search(step.on_send, s) for s in self.sent):
                # The gate is unmet: this expect cannot consume the gated step
                # yet, so it sees nothing -> the caller will time out.
                return None
            return step
        return None


@dataclass
class FakePexpectScript:
    """Convenience builder for the common ready -> inject -> terminal scripts."""

    steps: list[_ScriptStep] = field(default_factory=list)

    def ready(self, prompt: str) -> FakePexpectScript:
        """Append a step that emits the ready *prompt*."""
        self.steps.append(_ScriptStep(emit=prompt))
        return self

    def working(self, text: str) -> FakePexpectScript:
        """Append a step that emits working-prompt *text*."""
        self.steps.append(_ScriptStep(emit=text))
        return self

    def terminal_eof(self, *, exitstatus: int, emit: str = "") -> FakePexpectScript:
        """Append an ``eof`` step with the given child *exitstatus*."""
        self.steps.append(_ScriptStep(emit=emit, eof=True, exitstatus=exitstatus))
        return self

    def build(self) -> FakePexpectChild:
        """Return a :class:`FakePexpectChild` driven by the accumulated steps."""
        return FakePexpectChild(self.steps)
