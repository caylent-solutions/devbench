"""Regression guard: quota-dispatcher docs must not regress to pre-wiring wording.

Context (E2-F4-S3-T2, follow-up to its own test_review REVIEW_FAIL): E2-F4-S3-T1
wired the quota dispatcher (``cmd_start`` -> ``_drive_orchestrate_with_quota_resume``
-> ``_dispatch_quota_detection`` -> ``_handle_quota_pause`` in ``src/devbench/cli.py``),
and E2-F4-S3-T2 rewrote ``docs/devbench-yaml-reference.md``, ``sample-config.yaml``
and ``docs/slack-notifications.md`` to describe that dispatcher as live rather than
pending. That rewrite was previously verified only by a one-shot manual grep
recorded in a TDD Cycle Log entry -- nothing in the automated suite enforced it,
so the retired "not yet consumed" / "once wired" / "does not fire in a real run
until" phrasing could silently return on a future edit. This module makes that
check reproducible and CI-enforced (AC-VERIFY-001).

It also pins the field-level accuracy fix from the doc_review REVIEW_FAIL on the
same attempt: ``log_structured_events`` is parsed and validated but has no
runtime consumer anywhere in ``src/`` (``_handle_quota_pause`` and its callees
never read it), so both files must qualify it as such rather than folding it
into the blanket "live" / "consumed at runtime" claim made about the fields the
dispatcher actually reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
YAML_REFERENCE_DOC = REPO_ROOT / "docs" / "devbench-yaml-reference.md"
SAMPLE_CONFIG = REPO_ROOT / "sample-config.yaml"
SLACK_NOTIFICATIONS_DOC = REPO_ROOT / "docs" / "slack-notifications.md"

QUOTA_DOC_FILES = (YAML_REFERENCE_DOC, SAMPLE_CONFIG, SLACK_NOTIFICATIONS_DOC)

# Phrases that described the pre-E2-F4-S3-T1 (dispatcher-not-yet-wired) state.
# None of these may reappear anywhere in the three quota-doc surfaces.
STALE_PENDING_DISPATCH_PHRASES = (
    "not yet consumed",
    "lands in a follow-up work unit",
    "once wired",
    "once the dispatcher",
    "does not fire in a real run until",
    "inert until",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} must exist -- quota dispatcher docs were relocated or deleted."
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
class TestNoStalePendingDispatchWording:
    """AC-VERIFY-001: none of the retired pending-dispatch phrases may reappear."""

    @pytest.mark.parametrize("doc_path", QUOTA_DOC_FILES, ids=lambda p: p.name)
    @pytest.mark.parametrize("phrase", STALE_PENDING_DISPATCH_PHRASES)
    def test_stale_phrase_absent(self, doc_path: Path, phrase: str) -> None:
        text = _read(doc_path).lower()
        assert phrase not in text, (
            f"{doc_path} regressed: found retired pending-dispatch phrase {phrase!r}. "
            "The E2-F4-S3-T1 dispatcher is wired and live; this file must describe it "
            "as such, not as pending."
        )


@pytest.mark.unit
class TestPositiveLiveDispatcherClaimsPresent:
    """The docs must positively state the dispatcher is live, not just omit stale wording."""

    def test_yaml_reference_names_the_dispatch_entry_point(self) -> None:
        text = _read(YAML_REFERENCE_DOC)
        assert "_drive_orchestrate_with_quota_resume" in text, (
            "docs/devbench-yaml-reference.md's quota_handling section must name "
            "_drive_orchestrate_with_quota_resume as the live cmd_start entry point "
            "into the quota dispatcher."
        )

    def test_slack_notifications_quota_waiting_row_states_it_fires_in_a_real_run(self) -> None:
        text = _read(SLACK_NOTIFICATIONS_DOC)
        row = next(line for line in text.splitlines() if "`quota_waiting`" in line)
        assert "fires" in row.lower(), (
            "docs/slack-notifications.md's quota_waiting row must positively state that "
            f"the event fires in a real run, not merely omit the stale wording. Row: {row!r}"
        )

    def test_slack_notifications_quota_resumed_row_states_it_fires_in_a_real_run(self) -> None:
        text = _read(SLACK_NOTIFICATIONS_DOC)
        row = next(line for line in text.splitlines() if "`quota_resumed`" in line)
        assert "fires" in row.lower(), (
            "docs/slack-notifications.md's quota_resumed row must positively state that "
            f"the event fires in a real run, not merely omit the stale wording. Row: {row!r}"
        )


@pytest.mark.unit
class TestLogStructuredEventsDocSync:
    """log_structured_events documentation must track its actual runtime status.

    Regression pin for the doc_review REVIEW_FAIL on E2-F4-S3-T2 attempt 1: rewriting
    the blanket "not yet consumed" disclaimer into a whole-block "live" claim silently
    swept this field along, contradicting the code of the day (log_structured_events was
    parsed in config_loader.py and declared in config-schema.json, but no code path in
    src/ read it).

    E9-F1-S2-T1 (doc_review REVIEW_FAIL, second round) wired the flag end to end:
    ``_quota_structured_events_enabled()`` in cli.py now gates every one of the seven
    structured ``[QUOTA_*]`` markers on it. docs/devbench-yaml-reference.md was updated
    in that change to describe the flag as effective, so the "no runtime consumer"
    qualifier this class pins is retired for that file.

    sample-config.yaml carried the identical pre-wiring disclaimer (doc_review
    REVIEW_FAIL, third round): the "no runtime consumer" wording contradicted the
    same wired flag, so the comment block was rewritten to describe it as effective,
    matching docs/devbench-yaml-reference.md and docs/quota-handling.md. The
    "no runtime consumer" qualifier this class pinned for sample-config.yaml is
    retired too; both files now pin the "gates the seven structured" claim.
    """

    # Must be specific enough that trivial mentions of "text markers" in an
    # otherwise-unqualified sentence (e.g. "alongside the text markers") do not
    # false-positive; the marker must actually assert *no consumption*.
    NO_RUNTIME_CONSUMER_MARKER = "no runtime consumer"

    # Must be specific enough that a passing mention of "seven" elsewhere on the
    # page does not false-positive; the marker must name the gated-markers claim
    # in the same table row as the field.
    GATES_SEVEN_MARKERS_MARKER = "gates the seven structured"

    def test_yaml_reference_log_structured_events_row_documents_active_gating(self) -> None:
        text = _read(YAML_REFERENCE_DOC)
        row = next(line for line in text.splitlines() if "`log_structured_events`" in line and line.startswith("|"))
        assert self.GATES_SEVEN_MARKERS_MARKER in row.lower(), (
            "docs/devbench-yaml-reference.md's log_structured_events table row must state "
            f"it gates the seven structured [QUOTA_*] markers. Row: {row!r}"
        )
        assert self.NO_RUNTIME_CONSUMER_MARKER not in row.lower(), (
            "docs/devbench-yaml-reference.md's log_structured_events table row regressed to the "
            f"retired 'no runtime consumer' disclaimer; the flag is wired (E9-F1-S2-T1). Row: {row!r}"
        )

    def test_sample_config_log_structured_events_comment_documents_active_gating(self) -> None:
        text = _read(SAMPLE_CONFIG)
        lines = text.splitlines()
        idx = next(i for i, line in enumerate(lines) if line.strip().startswith("# log_structured_events:"))
        comment_block = "\n".join(lines[idx : idx + 3]).lower()
        assert self.GATES_SEVEN_MARKERS_MARKER in comment_block, (
            "sample-config.yaml's log_structured_events comment must state it gates the "
            f"seven structured [QUOTA_*] markers. Comment block: {comment_block!r}"
        )
        assert self.NO_RUNTIME_CONSUMER_MARKER not in comment_block, (
            "sample-config.yaml's log_structured_events comment regressed to the retired "
            f"'no runtime consumer' disclaimer; the flag is wired (E9-F1-S2-T1). Comment block: {comment_block!r}"
        )
