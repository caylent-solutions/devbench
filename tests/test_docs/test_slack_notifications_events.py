"""Regression pin: docs/slack-notifications.md's '## Event reference' table
must stay in lockstep with devbench.notifications.ALL_EVENTS.

Context (E2-F3-S1-T3, follow-up to E2-F3-S1-T1's doc_review REVIEW_FAIL): the
'## Event reference' table documents every dispatchable Slack event -- toggle
name, trigger condition, and payload fields. When a new event is registered
in ``ALL_EVENTS`` but the table isn't updated, operators reading the
canonical event catalogue can't discover the new event or its payload shape.
This module derives both sides of the comparison dynamically (imports
``ALL_EVENTS`` at runtime; parses the table out of the doc at runtime) so it
catches drift automatically instead of relying on a hardcoded event list that
itself could go stale.

AC-E2-F3-S1-T3-1/2/3/4.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.notifications import ALL_EVENTS

REPO_ROOT = Path(__file__).parent.parent.parent
SLACK_NOTIFICATIONS_DOC = REPO_ROOT / "docs" / "slack-notifications.md"
EVENT_REFERENCE_HEADING = "## Event reference"

# Matches a markdown table data row whose first column is a backtick-quoted
# event token, e.g. '| `quota_waiting` | ... | ... |'. The header row
# ('| Toggle | ...') and the '|---|---|---|' separator row have no
# backtick-quoted first column, so neither matches.
_TABLE_ROW_EVENT_PATTERN = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _extract_event_reference_section(markdown_text: str) -> str:
    """Return the body of the '## Event reference' section, up to the next '## ' heading.

    Raises:
        AssertionError: When the heading is not present, so a missing section
            fails loudly instead of silently comparing against an empty set.
    """
    lines = markdown_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == EVENT_REFERENCE_HEADING)
    except StopIteration as exc:
        raise AssertionError(
            f"{SLACK_NOTIFICATIONS_DOC} has no '{EVENT_REFERENCE_HEADING}' heading -- "
            "cannot locate the event catalogue table."
        ) from exc
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _extract_documented_events(markdown_text: str) -> set[str]:
    """Parse the '## Event reference' table and return the documented event-name set.

    Extracts the backtick-quoted event token from the first column of every
    data row. Header and separator rows are skipped automatically because
    they don't match ``_TABLE_ROW_EVENT_PATTERN``.
    """
    section = _extract_event_reference_section(markdown_text)
    return {match.group(1) for line in section.splitlines() if (match := _TABLE_ROW_EVENT_PATTERN.match(line))}


@pytest.mark.unit
class TestEventReferenceTablePinsAllEvents:
    """docs/slack-notifications.md's Event reference table must match ALL_EVENTS exactly."""

    def test_event_reference_table_matches_all_events(self) -> None:
        """The documented event set equals devbench.notifications.ALL_EVENTS (AC-E2-F3-S1-T3-3)."""
        text = SLACK_NOTIFICATIONS_DOC.read_text(encoding="utf-8")
        documented = _extract_documented_events(text)
        code_events = set(ALL_EVENTS)

        missing_from_docs = code_events - documented
        stale_in_docs = documented - code_events

        assert not missing_from_docs, (
            "docs/slack-notifications.md's '## Event reference' table is missing rows for "
            f"events registered in devbench.notifications.ALL_EVENTS: {sorted(missing_from_docs)}. "
            "Add a table row documenting the trigger and payload fields for each."
        )
        assert not stale_in_docs, (
            "docs/slack-notifications.md's '## Event reference' table documents events that are "
            f"not registered in devbench.notifications.ALL_EVENTS: {sorted(stale_in_docs)}. "
            "Remove the stale row or register the event in ALL_EVENTS."
        )

    def test_quota_waiting_row_is_documented(self) -> None:
        """`quota_waiting` has a table row (AC-E2-F3-S1-T3-1)."""
        text = SLACK_NOTIFICATIONS_DOC.read_text(encoding="utf-8")
        assert "quota_waiting" in _extract_documented_events(text)

    def test_quota_resumed_row_is_documented(self) -> None:
        """`quota_resumed` has a table row (AC-E2-F3-S1-T3-2)."""
        text = SLACK_NOTIFICATIONS_DOC.read_text(encoding="utf-8")
        assert "quota_resumed" in _extract_documented_events(text)

    def test_quota_waiting_row_names_actual_payload_fields(self) -> None:
        """The `quota_waiting` row's payload column names the fields notify_quota_waiting passes.

        notify_quota_waiting(reason, reset_at) passes slack_fields=[("Reason", reason),
        ("Resets at", reset_at)] to _dispatch (AC-E2-F3-S1-T3-1) -- the row must name
        both, not a guessed payload shape.
        """
        text = SLACK_NOTIFICATIONS_DOC.read_text(encoding="utf-8")
        section = _extract_event_reference_section(text)
        row = next(line for line in section.splitlines() if "`quota_waiting`" in line)
        assert "reason" in row.lower()
        assert "reset_at" in row.lower()

    def test_quota_resumed_row_names_actual_payload_fields(self) -> None:
        """The `quota_resumed` row's payload column names the field notify_quota_resumed passes.

        notify_quota_resumed(waited_seconds) passes slack_fields=[("Waited", ...)] to
        _dispatch (AC-E2-F3-S1-T3-2) -- only waited_seconds, matching the real signature.
        """
        text = SLACK_NOTIFICATIONS_DOC.read_text(encoding="utf-8")
        section = _extract_event_reference_section(text)
        row = next(line for line in section.splitlines() if "`quota_resumed`" in line)
        assert "waited_seconds" in row.lower()


@pytest.mark.unit
class TestEventReferenceDriftDetectionIsSymmetric:
    """The extraction+comparison logic must catch drift in both directions (AC-E2-F3-S1-T3-4).

    Uses synthetic table snippets (not the real doc) so this regression pin
    protects the *comparison logic itself* -- proving it is a real set
    equality check, not a one-sided subset check that could silently pass
    when the doc drifts in the "documents something code doesn't have"
    direction.
    """

    _COMPLETE_TABLE = (
        f"{EVENT_REFERENCE_HEADING}\n\n"
        "| Toggle | Fires when | Payload fields |\n"
        "|---|---|---|\n" + "\n".join(f"| `{event}` | test | test |" for event in ALL_EVENTS) + "\n\n## Next heading\n"
    )

    def test_removing_a_row_is_detected_as_missing(self) -> None:
        """Deleting one event's row makes the documented set a strict subset of ALL_EVENTS."""
        target = ALL_EVENTS[0]
        broken_table = "\n".join(line for line in self._COMPLETE_TABLE.splitlines() if f"`{target}`" not in line)

        documented = _extract_documented_events(broken_table)

        assert target not in documented
        assert documented != set(ALL_EVENTS)
        assert documented < set(ALL_EVENTS)

    def test_adding_a_bogus_row_is_detected_as_stale(self) -> None:
        """A row for a nonexistent event makes the documented set a strict superset of ALL_EVENTS."""
        bogus_event = "quota_bogus_nonexistent_event"
        assert bogus_event not in ALL_EVENTS
        broken_table = self._COMPLETE_TABLE.replace(
            "\n\n## Next heading\n",
            f"\n| `{bogus_event}` | test | test |\n\n## Next heading\n",
        )

        documented = _extract_documented_events(broken_table)

        assert bogus_event in documented
        assert documented != set(ALL_EVENTS)
        assert documented > set(ALL_EVENTS)

    def test_complete_synthetic_table_matches_exactly(self) -> None:
        """Sanity check: the synthetic fixture with no drift compares equal (control case)."""
        documented = _extract_documented_events(self._COMPLETE_TABLE)
        assert documented == set(ALL_EVENTS)
