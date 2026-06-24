"""Notification coverage contract (keystone -- operator-block Slack gap spec, AC-6).

This is the regression backstop for the notification state machine.  It asserts
the two structural invariants that, left unenforced, let an enabled-but-never-
fired event, an unmapped block bucket, or a dispatcher-routed event drift out of
sync independently:

- **Inv-1 (every event fires):** every member of :data:`notifications.ALL_EVENTS`
  has at least one engine call site reachable from a lifecycle path.  The scan is
  a static AST walk of every ``.py`` module under ``src/devbench`` EXCLUDING the
  notifications module's own definitions (where the helpers live) and the test
  tree.  Dispatcher-routed events (the per-class ``work_unit_blocked_*`` buckets)
  are tolerated by asserting event-kind reachability through the shared
  transition dispatcher, not a literal per-helper grep -- a legitimately
  dispatcher-routed event is not a false failure (spec Section 11).

- **Inv-2 (every bucket maps):** every :class:`BlockedTaskState` member is either
  a key in :data:`notifications._EVENT_BY_CLASSIFICATION` or appears in the
  explicit :data:`notifications._DELIBERATELY_SILENT_CLASSIFICATIONS` allow-list
  with a documented rationale.  The two sets must partition ``BlockedTaskState``
  exactly -- no overlap, no gap.

The test inspects the engine's own constants and call graph only; it references
no domain, stack, or backlog, so it holds for every workspace (spec Section 7).
Adding a new event, bucket, or transition without wiring it (or listing it as
silent) fails this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from devbench import notifications
from devbench.backlog.proposal import BlockedTaskState

_SRC_ROOT: Path = Path(__file__).resolve().parents[1] / "src" / "devbench"

_NOTIFICATIONS_MODULE: Path = (_SRC_ROOT / "notifications.py").resolve()


def _direct_helper_name(event_kind: str) -> str:
    """Return the canonical ``notify_<event_kind>`` helper name for an event."""
    return f"notify_{event_kind}"


_BLOCKED_DISPATCHER: str = "notify_blocked_classification_transition"

_DISPATCHER_ROUTED_EVENTS: frozenset[str] = frozenset(notifications._EVENT_BY_CLASSIFICATION.values())


def _reachability_symbol(event_kind: str) -> str:
    """Return the call-graph symbol whose presence proves *event_kind* is reachable.

    Dispatcher-routed blocked events resolve to the shared transition dispatcher;
    every other event resolves to its own ``notify_<event_kind>`` helper.
    """
    if event_kind in _DISPATCHER_ROUTED_EVENTS:
        return _BLOCKED_DISPATCHER
    return _direct_helper_name(event_kind)


def _iter_engine_modules() -> list[Path]:
    """Return every ``.py`` module under ``src/devbench`` except the notifier itself.

    The notifications module is excluded because it is where the ``notify_*``
    helpers are DEFINED; a name reference there is the definition, not a
    lifecycle call site.  Inv-1 requires a reference from elsewhere in the
    engine.
    """
    modules: list[Path] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if path.resolve() == _NOTIFICATIONS_MODULE:
            continue
        modules.append(path)
    return modules


def _collect_referenced_names(modules: list[Path]) -> set[str]:
    """Return every identifier referenced (as ``Name`` or attribute) in *modules*.

    Walks each module's AST and records:

    - ``ast.Name`` ids (e.g. a directly-imported ``notify_work_unit_promoted``).
    - ``ast.Attribute`` attrs (e.g. ``notifications.notify_work_unit_promoted``).

    Recording both forms keeps the scan agnostic to whether a call site imports
    the helper by name or references it through the module object.  A
    ``SyntaxError`` in any module fails the test loudly (a broken engine module
    is a real defect, never silently skipped).
    """
    referenced: set[str] = set()
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
    return referenced


@pytest.fixture(scope="module")
def engine_referenced_names() -> set[str]:
    """Module-scoped cache of every name referenced across the engine source."""
    return _collect_referenced_names(_iter_engine_modules())


@pytest.mark.unit
class TestInv1EveryEventFires:
    """Every ``ALL_EVENTS`` member is reachable from a lifecycle call site."""

    @pytest.mark.parametrize("event_kind", list(notifications.ALL_EVENTS))
    def test_event_has_reachable_call_site(self, event_kind: str, engine_referenced_names: set[str]) -> None:
        """Each event's reachability symbol is referenced outside the notifier definition.

        For a direct event this is its ``notify_<event>`` helper; for a
        dispatcher-routed blocked bucket it is the shared transition dispatcher.
        A failure here means the event is enabled-but-never-fires (the
        materialised / promoted gap the spec closes) -- it must be wired or the
        event removed from ``ALL_EVENTS``.
        """
        symbol = _reachability_symbol(event_kind)
        assert symbol in engine_referenced_names, (
            f"notification event {event_kind!r} has NO engine call site: "
            f"expected a reference to {symbol!r} outside notifications.py, found none. "
            "Wire it into a lifecycle path or remove it from ALL_EVENTS."
        )

    def test_blocked_dispatcher_itself_is_wired(self, engine_referenced_names: set[str]) -> None:
        """The shared blocked dispatcher must itself have a lifecycle call site.

        Inv-1 for the seven blocked buckets reduces to "the dispatcher is wired".
        Pin that directly so a refactor that orphans the dispatcher fails loudly
        rather than silently passing every blocked-bucket assertion.
        """
        assert _BLOCKED_DISPATCHER in engine_referenced_names, (
            f"the blocked-classification dispatcher {_BLOCKED_DISPATCHER!r} has no engine "
            "call site; every per-class blocked event is unreachable."
        )


@pytest.mark.unit
class TestInv2EveryBucketMaps:
    """Every ``BlockedTaskState`` is a mapped event or on the silent allow-list."""

    def test_buckets_partition_exactly(self) -> None:
        """The mapped set and the silent allow-list partition BlockedTaskState exactly.

        No bucket may be both mapped AND silent (overlap), and no bucket may be
        neither (gap).  ``INTERRUPTED_ON_STOP`` regression-guards G4: it must
        appear in exactly one of the two sets.
        """
        all_buckets = {member.name for member in BlockedTaskState}
        mapped = set(notifications._EVENT_BY_CLASSIFICATION)
        silent = set(notifications._DELIBERATELY_SILENT_CLASSIFICATIONS)

        overlap = mapped & silent
        assert not overlap, (
            f"blocked buckets {sorted(overlap)} are BOTH mapped to an event AND on the "
            "silent allow-list; each bucket must be in exactly one set."
        )

        gap = all_buckets - (mapped | silent)
        assert not gap, (
            f"blocked buckets {sorted(gap)} are neither mapped in _EVENT_BY_CLASSIFICATION "
            "nor listed in _DELIBERATELY_SILENT_CLASSIFICATIONS; map them to an event or "
            "add them to the silent allow-list with a documented rationale."
        )

        extra = (mapped | silent) - all_buckets
        assert not extra, (
            f"{sorted(extra)} appear in the event map / silent allow-list but are not real "
            "BlockedTaskState members; remove the stale entries."
        )

    def test_interrupted_on_stop_is_accounted_for(self) -> None:
        """``INTERRUPTED_ON_STOP`` is mapped or silent (closes G4 directly)."""
        name = BlockedTaskState.INTERRUPTED_ON_STOP.name
        mapped = set(notifications._EVENT_BY_CLASSIFICATION)
        silent = set(notifications._DELIBERATELY_SILENT_CLASSIFICATIONS)
        assert name in mapped or name in silent, (
            f"{name!r} is neither mapped to an event nor explicitly silent; it can never page."
        )

    def test_every_mapped_event_is_a_known_all_event(self) -> None:
        """Each event-kind in the classification map is a real ``ALL_EVENTS`` member.

        Guards against a typo'd event-kind in ``_EVENT_BY_CLASSIFICATION`` that
        would silently never resolve to a config toggle.
        """
        for bucket, event_kind in notifications._EVENT_BY_CLASSIFICATION.items():
            assert event_kind in notifications.ALL_EVENTS, (
                f"classification {bucket!r} maps to event {event_kind!r} which is not in ALL_EVENTS"
            )
