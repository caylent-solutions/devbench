"""Tests for `[GATE_PASS]` marker composition, parsing and scope-hash
primitives (spec `integration-reality-gates-hardening.md` sections 4.2, 5.3)
and the gate tier taxonomy (D-6) they are built on.

`devbench.gate_records` performs no work-unit-file or git I/O of its own
(mirroring `devbench.tdd_gate`'s pure observation engine), so every test here
drives the module with plain in-memory strings/mappings -- no scratch git
repos or work-unit fixtures are needed.

Every symbol under test here is new production source (`gate_records.py` is
a brand-new module; the tier constants are new additions to `constants.py`),
so -- mirroring the rest of `tests/test_constants.py` -- every import of
`devbench.constants` / `devbench.gate_records` is deferred inside the test
body rather than hoisted to module scope. This keeps the module importable
(and every test collectible) even when the orchestrator's RED gate stashes
those production files, so a genuinely failing assertion is reported as a
test FAILURE rather than a collection error.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

# Mirrors the private grammar in devbench.gate_records -- used here only to
# independently assert the shape of compose_gate_pass_record's output,
# without depending on that module's internals.
_MARKER_RE = re.compile(r"^\[GATE_PASS (?P<gate>[A-Za-z0-9_]+)\] (?P<timestamp>\S+) (?P<scope_hash>[0-9a-f]{64})$")


@pytest.mark.unit
class TestGatePassMarker:
    """Marker composition/parsing grammar (spec 5.3, AC-E2-F2-S1-T1-1/3/6)."""

    def test_compose_produces_exactly_one_line_matching_the_grammar(self) -> None:
        from devbench.gate_records import compose_gate_pass_record

        record = compose_gate_pass_record("reachability", "a" * 64)

        assert "\n" not in record
        match = _MARKER_RE.match(record)
        assert match is not None, record
        assert match.group("gate") == "reachability"
        assert match.group("scope_hash") == "a" * 64

    def test_compose_timestamp_defaults_to_current_timezone_aware_utc(self) -> None:
        from devbench.gate_records import compose_gate_pass_record

        record = compose_gate_pass_record("reachability", "b" * 64)
        match = _MARKER_RE.match(record)
        assert match is not None

        parsed = datetime.fromisoformat(match.group("timestamp"))

        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    def test_compose_accepts_an_explicit_utc_timestamp(self) -> None:
        from devbench.gate_records import compose_gate_pass_record

        ts = datetime(2026, 1, 1, 12, 30, 45, tzinfo=UTC)

        record = compose_gate_pass_record("reachability", "c" * 64, timestamp=ts)

        assert record == f"[GATE_PASS reachability] {ts.isoformat()} {'c' * 64}"

    def test_compose_converts_a_non_utc_timezone_to_utc(self) -> None:
        from devbench.gate_records import compose_gate_pass_record, parse_gate_pass_record

        eastern = timezone(timedelta(hours=-5))
        ts = datetime(2026, 1, 1, 7, 30, 0, tzinfo=eastern)

        record = compose_gate_pass_record("reachability", "d" * 64, timestamp=ts)
        parsed = parse_gate_pass_record(record)

        assert parsed.timestamp == ts.astimezone(UTC)
        assert parsed.timestamp.utcoffset() == timedelta(0)

    def test_compose_rejects_a_naive_timestamp(self) -> None:
        from devbench.gate_records import compose_gate_pass_record

        naive_timestamp = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)

        with pytest.raises(ValueError, match="naive"):
            compose_gate_pass_record("reachability", "e" * 64, timestamp=naive_timestamp)

    def test_compose_rejects_an_unknown_gate(self) -> None:
        from devbench.gate_records import compose_gate_pass_record

        with pytest.raises(ValueError, match="bogus_gate"):
            compose_gate_pass_record("bogus_gate", "f" * 64)

    def test_compose_rejects_a_malformed_scope_hash(self) -> None:
        from devbench.gate_records import compose_gate_pass_record

        with pytest.raises(ValueError, match="scope_hash"):
            compose_gate_pass_record("reachability", "not-a-hash")

    def test_compose_accepts_every_declared_gate(self) -> None:
        from devbench.constants import GATE_NAMES
        from devbench.gate_records import compose_gate_pass_record

        for gate in GATE_NAMES:
            record = compose_gate_pass_record(gate, "0" * 64)
            assert record.startswith(f"[GATE_PASS {gate}]")

    def test_parse_well_formed_record_returns_gate_timestamp_and_scope_hash(self) -> None:
        from devbench.gate_records import GatePassRecord, parse_gate_pass_record

        ts = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
        line = f"[GATE_PASS ancestry] {ts.isoformat()} {'1' * 64}"

        parsed = parse_gate_pass_record(line)

        assert parsed == GatePassRecord(gate="ancestry", timestamp=ts, scope_hash="1" * 64)

    def test_parse_round_trips_compose_output(self) -> None:
        from devbench.gate_records import compose_gate_pass_record, parse_gate_pass_record

        record = compose_gate_pass_record("shared_file_impact", "2" * 64)

        parsed = parse_gate_pass_record(record)

        assert parsed.gate == "shared_file_impact"
        assert parsed.scope_hash == "2" * 64

    def test_parse_tolerates_surrounding_whitespace(self) -> None:
        from devbench.gate_records import parse_gate_pass_record

        ts = datetime(2026, 5, 6, tzinfo=UTC)
        line = f"  [GATE_PASS reachability] {ts.isoformat()} {'9' * 64}  "

        parsed = parse_gate_pass_record(line)

        assert parsed.gate == "reachability"

    @pytest.mark.parametrize(
        ("line", "match"),
        [
            ("", "grammar"),
            ("not a gate pass record at all", "grammar"),
            ("[GATE_PASS reachability] 2026-01-01T00:00:00+00:00", "grammar"),
            ("[GATE_PASS reachability] 2026-01-01T00:00:00+00:00 tooshort", "grammar"),
            ("[GATE_PASS reachability] not-a-timestamp " + "3" * 64, "ISO-8601"),
            ("[GATE_PASS reachability] 2026-01-01T00:00:00 " + "4" * 64, "timezone-aware"),
        ],
    )
    def test_parse_malformed_record_raises_for_the_right_reason(self, line: str, match: str) -> None:
        from devbench.gate_records import parse_gate_pass_record

        with pytest.raises(ValueError, match=match):
            parse_gate_pass_record(line)

    def test_parse_malformed_record_error_names_the_offending_line(self) -> None:
        from devbench.gate_records import parse_gate_pass_record

        line = "[GATE_PASS reachability] not-a-timestamp " + "3" * 64

        with pytest.raises(ValueError) as exc_info:
            parse_gate_pass_record(line)

        assert line in str(exc_info.value)

    def test_parse_rejects_an_undeclared_gate(self) -> None:
        from devbench.gate_records import parse_gate_pass_record

        line = f"[GATE_PASS bogus_gate] 2026-01-01T00:00:00+00:00 {'5' * 64}"

        with pytest.raises(ValueError, match="bogus_gate"):
            parse_gate_pass_record(line)

    def test_parse_never_returns_a_partial_record_on_malformed_input(self) -> None:
        from devbench.gate_records import parse_gate_pass_record

        with pytest.raises(ValueError):
            parse_gate_pass_record("[GATE_PASS reachability] garbage")


@pytest.mark.unit
class TestLatestGatePassRecord:
    """Reading the most recent `[GATE_PASS <gate>]` record for a gate."""

    def test_returns_none_when_no_record_is_present(self) -> None:
        from devbench.gate_records import latest_gate_pass_record

        assert latest_gate_pass_record("## Comments\n\nnothing here\n", "reachability") is None

    def test_returns_the_most_recent_matching_record(self) -> None:
        from devbench.gate_records import compose_gate_pass_record, latest_gate_pass_record

        first = compose_gate_pass_record("reachability", "6" * 64, timestamp=datetime(2026, 1, 1, tzinfo=UTC))
        second = compose_gate_pass_record("reachability", "7" * 64, timestamp=datetime(2026, 2, 1, tzinfo=UTC))
        content = f"## Comments\n\n{first}\n{second}\n"

        latest = latest_gate_pass_record(content, "reachability")

        assert latest is not None
        assert latest.scope_hash == "7" * 64

    def test_ignores_records_for_other_gates(self) -> None:
        from devbench.gate_records import compose_gate_pass_record, latest_gate_pass_record

        other = compose_gate_pass_record("ancestry", "8" * 64)
        content = f"## Comments\n\n{other}\n"

        assert latest_gate_pass_record(content, "reachability") is None

    def test_finds_a_record_embedded_within_a_larger_audit_comment_line(self) -> None:
        from devbench.gate_records import compose_gate_pass_record, latest_gate_pass_record

        marker = compose_gate_pass_record("reachability", "9" * 64, timestamp=datetime(2026, 1, 1, tzinfo=UTC))
        content = f"## Comments\n\n[2026-08-14 02:58 UTC] [agent/check-reachability] {marker}\n"

        latest = latest_gate_pass_record(content, "reachability")

        assert latest is not None
        assert latest.scope_hash == "9" * 64

    def test_raises_on_a_malformed_record_for_the_requested_gate(self) -> None:
        from devbench.gate_records import latest_gate_pass_record

        content = "## Comments\n\n[GATE_PASS reachability] not-a-timestamp badhash\n"

        with pytest.raises(ValueError, match="reachability"):
            latest_gate_pass_record(content, "reachability")

    def test_does_not_raise_on_a_malformed_record_for_a_different_gate(self) -> None:
        from devbench.gate_records import latest_gate_pass_record

        content = "## Comments\n\n[GATE_PASS ancestry] not-a-timestamp badhash\n"

        assert latest_gate_pass_record(content, "reachability") is None

    def test_rejects_an_unknown_gate(self) -> None:
        from devbench.gate_records import latest_gate_pass_record

        with pytest.raises(ValueError, match="bogus_gate"):
            latest_gate_pass_record("anything", "bogus_gate")


@pytest.mark.unit
class TestComputeScopeHash:
    """SHA-256 scope hash over the changed-file list plus per-file blob hashes (AC-E2-F2-S1-T1-2)."""

    def test_stable_for_identical_inputs(self) -> None:
        from devbench.gate_records import compute_scope_hash

        inputs = {"src/devbench/a.py": "hash-a", "src/devbench/b.py": "hash-b"}

        assert compute_scope_hash(inputs) == compute_scope_hash(dict(inputs))

    def test_stable_regardless_of_mapping_insertion_order(self) -> None:
        from devbench.gate_records import compute_scope_hash

        first = {"a.py": "hash-a", "b.py": "hash-b"}
        second = {"b.py": "hash-b", "a.py": "hash-a"}

        assert compute_scope_hash(first) == compute_scope_hash(second)

    def test_changes_when_a_file_is_added_to_the_scope(self) -> None:
        from devbench.gate_records import compute_scope_hash

        base = {"a.py": "hash-a"}
        expanded = {"a.py": "hash-a", "b.py": "hash-b"}

        assert compute_scope_hash(base) != compute_scope_hash(expanded)

    def test_changes_when_a_files_blob_hash_changes(self) -> None:
        from devbench.gate_records import compute_scope_hash

        before = {"a.py": "hash-a"}
        after = {"a.py": "hash-a-modified"}

        assert compute_scope_hash(before) != compute_scope_hash(after)

    def test_empty_scope_raises(self) -> None:
        from devbench.gate_records import compute_scope_hash

        with pytest.raises(ValueError, match="empty"):
            compute_scope_hash({})

    def test_returns_a_sha256_hex_digest(self) -> None:
        from devbench.gate_records import compute_scope_hash

        result = compute_scope_hash({"a.py": "hash-a"})

        assert re.fullmatch(r"[0-9a-f]{64}", result)


@pytest.mark.unit
class TestGateTiers:
    """Gate tier taxonomy (spec 4.2, D-6; AC-E2-F2-S1-T1-4/5)."""

    _EXPECTED_MACHINE_BLOCKING = frozenset({"reachability", "ancestry", "shared_file_impact", "fixture_consistency"})

    def test_gate_tiers_covers_exactly_the_eight_declared_gates(self) -> None:
        from devbench.constants import GATE_NAMES, GATE_TIERS

        assert set(GATE_TIERS) == set(GATE_NAMES)

    def test_gate_tiers_values_are_one_of_the_three_declared_tiers(self) -> None:
        from devbench.constants import (
            GATE_TIER_ADVISORY,
            GATE_TIER_JUDGE_EVIDENCE,
            GATE_TIER_MACHINE_BLOCKING,
            GATE_TIERS,
        )

        declared = {GATE_TIER_MACHINE_BLOCKING, GATE_TIER_JUDGE_EVIDENCE, GATE_TIER_ADVISORY}

        for gate, tier in GATE_TIERS.items():
            assert tier in declared, f"{gate} has an undeclared tier {tier!r}"

    def test_machine_blocking_gates_are_exactly_the_d6_set(self) -> None:
        from devbench.constants import GATE_TIER_MACHINE_BLOCKING, GATE_TIERS

        machine_blocking = {gate for gate, tier in GATE_TIERS.items() if tier == GATE_TIER_MACHINE_BLOCKING}

        assert machine_blocking == self._EXPECTED_MACHINE_BLOCKING

    def test_judge_evidence_gates_are_the_remaining_four(self) -> None:
        from devbench.constants import GATE_NAMES, GATE_TIER_JUDGE_EVIDENCE, GATE_TIERS

        judge_evidence = {gate for gate, tier in GATE_TIERS.items() if tier == GATE_TIER_JUDGE_EVIDENCE}

        assert judge_evidence == set(GATE_NAMES) - self._EXPECTED_MACHINE_BLOCKING

    def test_no_gate_carries_the_advisory_tier_yet(self) -> None:
        from devbench.constants import GATE_TIER_ADVISORY, GATE_TIERS

        # D-6 assigns only machine-blocking and judge-evidence tiers today;
        # ADVISORY exists as a named symbol for a future gate but is not
        # applied to any of the eight currently-declared gates.
        assert not any(tier == GATE_TIER_ADVISORY for tier in GATE_TIERS.values())
