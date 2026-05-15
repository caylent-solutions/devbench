"""Tests for src/devbench/scope.py -- ScopeFilter dataclass and helpers.

Coverage requirement: 100% line + branch on devbench.scope.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from unittest import mock

import pytest

from devbench.scope import (
    InvalidScopeError,
    ScopeFilter,
    _current_user,
    _letter_prefix,
    _numeric_suffix,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backlog_ids() -> list[str]:
    """A representative flat list of work-unit IDs from a small backlog."""
    return [
        "E1-F1-S1-T1",
        "E1-F1-S1-T2",
        "E1-F1-S1-T3",
        "E1-F1-S2-T1",
        "E1-F1-S2-T2",
        "E1-F1-S1",
        "E1-F1-S2",
        "E1-F1",
        "E1-F2-S1-T1",
        "E1-F2-S1",
        "E1-F2",
        "E1",
        "E2-F1-S1-T1",
        "E2-F1-S1-T2",
        "E2-F1-S1",
        "E2-F1",
        "E2",
        "E3-F1-S1-T1",
        "E3-F1-S1",
        "E3-F1",
        "E3",
        "E4-F1-S1-T1",
        "E4-F1-S1",
        "E4-F1",
        "E4",
        "E5-F1-S1-T1",
        "E5-F1-S1",
        "E5-F1",
        "E5",
    ]


# ---------------------------------------------------------------------------
# AC-190-1: Single-ID tokens
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_single_id_exact_match_task(backlog_ids: list[str]) -> None:
    """Single task-level token matches only that exact WU (no children)."""
    sf = ScopeFilter.parse("E1-F1-S1-T1", "", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert not sf.allows("E1-F1-S1-T2")
    assert not sf.allows("E1-F1-S1")


@pytest.mark.unit
def test_single_id_story_matches_story_and_descendants(backlog_ids: list[str]) -> None:
    """Story-level token matches the story and all its task children."""
    sf = ScopeFilter.parse("E1-F1-S1", "", backlog_ids)
    assert sf.allows("E1-F1-S1")
    assert sf.allows("E1-F1-S1-T1")
    assert sf.allows("E1-F1-S1-T2")
    assert sf.allows("E1-F1-S1-T3")
    # S2 tasks must NOT be included
    assert not sf.allows("E1-F1-S2-T1")


@pytest.mark.unit
def test_single_id_feature_matches_feature_and_descendants(backlog_ids: list[str]) -> None:
    """Feature-level token matches all stories and tasks under it."""
    sf = ScopeFilter.parse("E1-F1", "", backlog_ids)
    assert sf.allows("E1-F1")
    assert sf.allows("E1-F1-S1")
    assert sf.allows("E1-F1-S1-T1")
    assert sf.allows("E1-F1-S2-T1")
    # F2 must NOT be included
    assert not sf.allows("E1-F2-S1-T1")


@pytest.mark.unit
def test_single_id_epic_matches_all_descendants(backlog_ids: list[str]) -> None:
    """Epic-level token matches everything under that epic."""
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    assert sf.allows("E1")
    assert sf.allows("E1-F1-S1-T1")
    assert sf.allows("E1-F2-S1-T1")
    # E2 must NOT be included
    assert not sf.allows("E2-F1-S1-T1")


# ---------------------------------------------------------------------------
# AC-190-2: Range tokens -- expand on final numeric segment
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_range_epic_level(backlog_ids: list[str]) -> None:
    """E1-E3 expands to E1, E2, E3 and all their descendants."""
    sf = ScopeFilter.parse("E1-E3", "", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert sf.allows("E2-F1-S1-T1")
    assert sf.allows("E3-F1-S1-T1")
    assert not sf.allows("E4-F1-S1-T1")
    assert not sf.allows("E5-F1-S1-T1")


@pytest.mark.unit
def test_range_story_level(backlog_ids: list[str]) -> None:
    """E1-F1-S1-S2 expands to both stories and their tasks."""
    sf = ScopeFilter.parse("E1-F1-S1-S2", "", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert sf.allows("E1-F1-S2-T1")
    assert not sf.allows("E1-F2-S1-T1")


@pytest.mark.unit
def test_range_task_level(backlog_ids: list[str]) -> None:
    """E1-F1-S1-T1-T3 expands to T1, T2, T3 (inclusive)."""
    sf = ScopeFilter.parse("E1-F1-S1-T1-T3", "", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert sf.allows("E1-F1-S1-T2")
    assert sf.allows("E1-F1-S1-T3")
    # Story-level row is not automatically included when only tasks are named
    assert not sf.allows("E1-F1-S1")


@pytest.mark.unit
def test_range_feature_level_spec_fixture() -> None:
    """E5-F1-F3 expands to F1, F2, F3 and their descendants -- spec section 4.2.1 example."""
    ids = [
        "E5",
        "E5-F1",
        "E5-F1-S1",
        "E5-F1-S1-T1",
        "E5-F2",
        "E5-F2-S1",
        "E5-F2-S1-T1",
        "E5-F3",
        "E5-F3-S1",
        "E5-F3-S1-T1",
        "E5-F4",
        "E5-F4-S1",
        "E5-F4-S1-T1",
    ]
    sf = ScopeFilter.parse("E5-F1-F3", "", ids)
    assert sf.allows("E5-F1-S1-T1"), "F1 descendants must be included"
    assert sf.allows("E5-F2-S1-T1"), "F2 descendants must be included"
    assert sf.allows("E5-F3-S1-T1"), "F3 descendants must be included"
    assert not sf.allows("E5-F4-S1-T1"), "F4 is outside the range and must be excluded"


@pytest.mark.unit
def test_range_task_level_mid_range_spec_fixture() -> None:
    """E5-F1-S1-T2-T5 expands to T2, T3, T4, T5 (inclusive) -- spec section 4.2.1 example."""
    ids = [
        "E5-F1-S1-T1",
        "E5-F1-S1-T2",
        "E5-F1-S1-T3",
        "E5-F1-S1-T4",
        "E5-F1-S1-T5",
        "E5-F1-S1-T6",
    ]
    sf = ScopeFilter.parse("E5-F1-S1-T2-T5", "", ids)
    assert not sf.allows("E5-F1-S1-T1"), "T1 is before the range start"
    assert sf.allows("E5-F1-S1-T2"), "T2 is the range start (inclusive)"
    assert sf.allows("E5-F1-S1-T3"), "T3 is within the range"
    assert sf.allows("E5-F1-S1-T4"), "T4 is within the range"
    assert sf.allows("E5-F1-S1-T5"), "T5 is the range end (inclusive)"
    assert not sf.allows("E5-F1-S1-T6"), "T6 is after the range end"


@pytest.mark.unit
def test_single_segment_range_same_value_is_single_id(backlog_ids: list[str]) -> None:
    """A range whose two endpoints are identical is treated as a single-ID token."""
    sf = ScopeFilter.parse("E2-E2", "", backlog_ids)
    assert sf.allows("E2-F1-S1-T1")
    assert not sf.allows("E1-F1-S1-T1")


# ---------------------------------------------------------------------------
# AC-190-3: Mixed tokens union correctly
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mixed_tokens_union(backlog_ids: list[str]) -> None:
    """Comma-separated mixed tokens produce the union of their expansions."""
    sf = ScopeFilter.parse("E1-F2, E2-F1-S1-T1", "", backlog_ids)
    # From E1-F2
    assert sf.allows("E1-F2-S1-T1")
    # From E2-F1-S1-T1
    assert sf.allows("E2-F1-S1-T1")
    # Not in either
    assert not sf.allows("E1-F1-S1-T1")
    assert not sf.allows("E3-F1-S1-T1")


# ---------------------------------------------------------------------------
# AC-190-4: --exclude subtracts from include
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exclude_subtracts_from_include(backlog_ids: list[str]) -> None:
    """Exclude tokens remove their expansion from the include set."""
    sf = ScopeFilter.parse("E1-E3", "E2", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert not sf.allows("E2-F1-S1-T1")
    assert sf.allows("E3-F1-S1-T1")


@pytest.mark.unit
def test_exclude_specific_feature(backlog_ids: list[str]) -> None:
    """Excluding a feature removes it and its children from an epic-wide include."""
    sf = ScopeFilter.parse("E1", "E1-F2", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert not sf.allows("E1-F2-S1-T1")
    # The parent epic itself stays (it matched the include token)
    assert sf.allows("E1")


# ---------------------------------------------------------------------------
# AC-190-5: Reverse ranges raise InvalidScopeError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reverse_range_raises(backlog_ids: list[str]) -> None:
    """Reverse ranges (E3-E1) raise InvalidScopeError with an actionable message."""
    with pytest.raises(InvalidScopeError, match="Reverse range"):
        ScopeFilter.parse("E3-E1", "", backlog_ids)


@pytest.mark.unit
def test_reverse_task_range_raises(backlog_ids: list[str]) -> None:
    """Reverse task-level range raises InvalidScopeError."""
    with pytest.raises(InvalidScopeError, match="Reverse range"):
        ScopeFilter.parse("E1-F1-S1-T3-T1", "", backlog_ids)


# ---------------------------------------------------------------------------
# AC-190-6: Out-of-range tokens warn but don't abort
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_out_of_range_token_warns_not_raises(backlog_ids: list[str], caplog: pytest.LogCaptureFixture) -> None:
    """Tokens with no matching WU in the backlog emit a warning and don't abort."""
    with caplog.at_level(logging.WARNING, logger="devbench.scope"):
        sf = ScopeFilter.parse("E99-F1-S1-T1", "", backlog_ids)
    assert any("E99-F1-S1-T1" in record.message for record in caplog.records)
    # The filter is still valid; no IDs matched
    assert not sf.allows("E99-F1-S1-T1")
    assert not sf.allows("E1-F1-S1-T1")


# ---------------------------------------------------------------------------
# AC-190-7: Complex --include/--exclude intersection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_include_e1_e5_exclude_e2_e3(backlog_ids: list[str]) -> None:
    """--include E1-E5 --exclude E2,E3 produces exactly E1, E4, E5 descendants."""
    sf = ScopeFilter.parse("E1-E5", "E2,E3", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert not sf.allows("E2-F1-S1-T1")
    assert not sf.allows("E3-F1-S1-T1")
    assert sf.allows("E4-F1-S1-T1")
    assert sf.allows("E5-F1-S1-T1")


# ---------------------------------------------------------------------------
# AC-190-9: Empty include_str means include everything
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_include_means_all(backlog_ids: list[str]) -> None:
    """Empty include_str includes every backlog ID."""
    sf = ScopeFilter.parse("", "", backlog_ids)
    for wid in backlog_ids:
        assert sf.allows(wid)


@pytest.mark.unit
def test_empty_include_with_exclude(backlog_ids: list[str]) -> None:
    """Empty include with an exclude subtracts from everything."""
    sf = ScopeFilter.parse("", "E2", backlog_ids)
    assert sf.allows("E1-F1-S1-T1")
    assert not sf.allows("E2-F1-S1-T1")
    assert sf.allows("E3-F1-S1-T1")


# ---------------------------------------------------------------------------
# allows() -- O(1) set-membership check
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_allows_id_not_in_set_returns_false(backlog_ids: list[str]) -> None:
    """allows() returns False for an ID not in expanded_ids."""
    sf = ScopeFilter.parse("E1-F1-S1-T1", "", backlog_ids)
    assert not sf.allows("E99-F9-S9-T9")


@pytest.mark.unit
def test_allows_is_o1_membership(backlog_ids: list[str]) -> None:
    """expanded_ids is a set (not list) enabling O(1) membership check."""
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    assert isinstance(sf.expanded_ids, set)


# ---------------------------------------------------------------------------
# Persistence methods -- to_file, from_file, clear
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_file_writes_scope_json(tmp_path: Path, backlog_ids: list[str]) -> None:
    """to_file writes a valid scope.json under <workspace>/.devbench/scope.json."""
    sf = ScopeFilter.parse("E1-E2", "E2-F1", backlog_ids)
    sf.to_file(tmp_path)
    scope_path = tmp_path / ".devbench" / "scope.json"
    assert scope_path.exists()
    data = json.loads(scope_path.read_text())
    assert data["include"] == sf.include
    assert data["exclude"] == sf.exclude
    assert set(data["expanded_ids"]) == sf.expanded_ids
    # Optional metadata fields are present
    assert "started_at" in data
    assert "started_by" in data


@pytest.mark.unit
def test_to_file_creates_parent_dir(tmp_path: Path, backlog_ids: list[str]) -> None:
    """to_file creates .devbench directory if absent."""
    ws = tmp_path / "fresh-workspace"
    ws.mkdir()
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    sf.to_file(ws)
    assert (ws / ".devbench" / "scope.json").exists()


@pytest.mark.unit
def test_from_file_round_trips(tmp_path: Path, backlog_ids: list[str]) -> None:
    """from_file reads back the same ScopeFilter that was written by to_file."""
    original = ScopeFilter.parse("E1-E3", "E2", backlog_ids)
    original.to_file(tmp_path)
    loaded = ScopeFilter.from_file(tmp_path)
    assert loaded.include == original.include
    assert loaded.exclude == original.exclude
    assert loaded.expanded_ids == original.expanded_ids


@pytest.mark.unit
def test_from_file_missing_raises(tmp_path: Path) -> None:
    """from_file raises FileNotFoundError when scope.json does not exist."""
    with pytest.raises(FileNotFoundError):
        ScopeFilter.from_file(tmp_path)


@pytest.mark.unit
def test_clear_removes_scope_json(tmp_path: Path, backlog_ids: list[str]) -> None:
    """clear() deletes scope.json atomically."""
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    sf.to_file(tmp_path)
    scope_path = tmp_path / ".devbench" / "scope.json"
    assert scope_path.exists()
    ScopeFilter.clear(tmp_path)
    assert not scope_path.exists()


@pytest.mark.unit
def test_clear_idempotent_when_absent(tmp_path: Path) -> None:
    """clear() is a no-op (no exception) when scope.json does not exist."""
    # Should not raise
    ScopeFilter.clear(tmp_path)


# ---------------------------------------------------------------------------
# AC-190-8: scope.json includes raw + expanded sets (schema validation)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scope_json_schema_has_required_fields(tmp_path: Path, backlog_ids: list[str]) -> None:
    """scope.json output contains include, exclude, expanded_ids, started_at, started_by."""
    sf = ScopeFilter.parse("E1", "E1-F2", backlog_ids)
    sf.to_file(tmp_path)
    data = json.loads((tmp_path / ".devbench" / "scope.json").read_text())
    for required_key in ("include", "exclude", "expanded_ids", "started_at", "started_by"):
        assert required_key in data, f"Missing key: {required_key}"
    assert isinstance(data["include"], list)
    assert isinstance(data["exclude"], list)
    assert isinstance(data["expanded_ids"], list)
    assert isinstance(data["started_at"], str)
    assert isinstance(data["started_by"], str)


# ---------------------------------------------------------------------------
# ScopeFilter dataclass field types
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scopefilter_field_types(backlog_ids: list[str]) -> None:
    """ScopeFilter fields have correct types after parse()."""
    sf = ScopeFilter.parse("E1-E3", "E2", backlog_ids)
    assert isinstance(sf.include, list)
    assert isinstance(sf.exclude, list)
    assert isinstance(sf.expanded_ids, set)


@pytest.mark.unit
def test_scopefilter_include_stores_raw_tokens(backlog_ids: list[str]) -> None:
    """include and exclude fields store the raw tokenised strings, not expanded IDs."""
    sf = ScopeFilter.parse("E1-E3, E5", "E2", backlog_ids)
    # Raw tokens after stripping whitespace
    assert "E1-E3" in sf.include or any("E1" in t for t in sf.include)
    assert "E2" in sf.exclude or any("E2" in t for t in sf.exclude)


# ---------------------------------------------------------------------------
# Edge cases: whitespace tolerance
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "include_str",
    [
        "E1-E3",
        " E1-E3",
        "E1-E3 ",
        " E1-E3 ",
        "E1-E3,E5",
        "E1-E3, E5",
        "E1-E3 , E5",
        " E1-E3 , E5 ",
    ],
)
def test_whitespace_tolerant_parsing(include_str: str, backlog_ids: list[str]) -> None:
    """parse() strips whitespace from tokens regardless of spacing."""
    sf = ScopeFilter.parse(include_str, "", backlog_ids)
    # E1 range always produces E1 descendants
    assert sf.allows("E1-F1-S1-T1")


# ---------------------------------------------------------------------------
# Edge case: empty exclude_str
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_exclude_str_no_subtraction(backlog_ids: list[str]) -> None:
    """Empty exclude_str results in no IDs being excluded."""
    sf_with = ScopeFilter.parse("E1", "", backlog_ids)
    sf_without = ScopeFilter.parse("E1", "", backlog_ids)
    assert sf_with.expanded_ids == sf_without.expanded_ids


# ---------------------------------------------------------------------------
# Integration-style: full workflow against a constructed fixture workspace
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Internal helper coverage -- _letter_prefix, _numeric_suffix, _current_user
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_letter_prefix_all_letters_returns_none() -> None:
    """_letter_prefix returns None when the segment has no trailing digit."""
    # Segment is purely alphabetic -- no trailing integer
    assert _letter_prefix("abc") is None


@pytest.mark.unit
def test_letter_prefix_starts_with_digit_returns_none() -> None:
    """_letter_prefix returns None when the segment starts with a digit (no letter prefix)."""
    assert _letter_prefix("123") is None


@pytest.mark.unit
def test_numeric_suffix_no_digits_returns_none() -> None:
    """_numeric_suffix returns None when the segment has no trailing digits."""
    assert _numeric_suffix("abc") is None


@pytest.mark.unit
def test_numeric_suffix_pure_digits_returns_int() -> None:
    """_numeric_suffix returns the integer for a purely numeric segment."""
    assert _numeric_suffix("42") == 42


@pytest.mark.unit
def test_current_user_fallback_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_current_user falls back to USER env var when getpass.getuser() raises."""
    monkeypatch.setenv("USER", "testoperator")
    with mock.patch("devbench.scope.getpass.getuser", side_effect=OSError("no tty")):
        result = _current_user()
    assert result == "testoperator"


@pytest.mark.unit
def test_current_user_fallback_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """_current_user returns 'unknown' when getpass raises and USER is unset."""
    monkeypatch.delenv("USER", raising=False)
    with mock.patch("devbench.scope.getpass.getuser", side_effect=OSError("no tty")):
        result = _current_user()
    assert result == "unknown"


@pytest.mark.unit
def test_range_token_matches_nothing_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A range token that expands but matches no backlog IDs emits a warning."""
    # Use a range that references E90-E91, which don't exist in a tiny backlog
    tiny_ids = ["E1-F1-S1-T1", "E2-F1-S1-T1"]
    with caplog.at_level(logging.WARNING, logger="devbench.scope"):
        sf = ScopeFilter.parse("E90-E91", "", tiny_ids)
    assert any("E90-E91" in record.message for record in caplog.records)
    assert not sf.allows("E1-F1-S1-T1")


@pytest.mark.unit
def test_non_numeric_shared_letter_prefix_treated_as_single_id(backlog_ids: list[str]) -> None:
    """When two adjacent segments share a letter prefix but the last segment has no
    trailing integer, the token is treated as a single-ID (not a range).

    Example: 'E1-E2a' -- 'E1' and 'E2a' both start with 'E', but 'E2a' has no
    trailing integer (_numeric_suffix returns None), so the ``pass`` branch in
    _expand_token is taken and the whole token is used as a single-ID prefix.
    """
    # 'E1' and 'E2a' share letter prefix 'E' but 'E2a' has no pure numeric suffix
    sf = ScopeFilter.parse("E1-E2a", "", backlog_ids)
    # 'E1-E2a' as a prefix matches nothing in backlog_ids
    assert len(sf.expanded_ids) == 0


# ---------------------------------------------------------------------------
# AC-190-5 extension: malformed token syntax raises InvalidScopeError (fail-fast)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_token",
    [
        "-E1",
        "E1-",
        "-",
        "E1--E3",
    ],
)
def test_malformed_token_leading_or_trailing_hyphen_raises(bad_token: str, backlog_ids: list[str]) -> None:
    """Tokens with leading/trailing hyphens or consecutive hyphens are syntactically
    invalid and must raise InvalidScopeError with an actionable message (fail-fast).

    A leading or trailing hyphen produces an empty segment when split on '-', which
    is not a valid work-unit ID segment (all valid segments are non-empty alphanumeric).
    Out-of-range warnings apply only to well-formed tokens that happen to match nothing;
    they do not apply to structurally malformed input.

    Spec: section 4.2.1, fail-fast principle in Code Standards.
    """
    with pytest.raises(InvalidScopeError, match=r"(?i)malformed"):
        ScopeFilter.parse(bad_token, "", backlog_ids)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_token",
    [
        "-E1",
        "E1-",
    ],
)
def test_malformed_token_in_exclude_str_raises(bad_token: str, backlog_ids: list[str]) -> None:
    """Malformed tokens in exclude_str also raise InvalidScopeError (fail-fast).

    Spec: section 4.2.1, fail-fast principle in Code Standards.
    """
    with pytest.raises(InvalidScopeError, match=r"(?i)malformed"):
        ScopeFilter.parse("E1", bad_token, backlog_ids)


# ---------------------------------------------------------------------------
# Integration-style: full workflow against a constructed fixture workspace
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_integration_parse_write_read_allows(tmp_path: Path, backlog_ids: list[str]) -> None:
    """End-to-end: parse -> write scope.json -> read back -> allows() works."""
    # Parse with include E1-E2, exclude E2-F1
    sf = ScopeFilter.parse("E1-E2", "E2-F1", backlog_ids)
    sf.to_file(tmp_path)

    # Read back
    loaded = ScopeFilter.from_file(tmp_path)

    # E1 descendants present
    assert loaded.allows("E1-F1-S1-T1")
    # E2-F1 descendants excluded
    assert not loaded.allows("E2-F1-S1-T1")
    # E2 epic itself still in expanded if it matched include before exclude stripped F1
    # (E2 epic row stays; only E2-F1* subtree is removed)
    assert loaded.allows("E2")

    # Cleanup
    ScopeFilter.clear(tmp_path)
    assert not (tmp_path / ".devbench" / "scope.json").exists()


# ---------------------------------------------------------------------------
# AC-190-8 extension: to_file return value, from_file error paths, format checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_file_returns_scope_json_path(tmp_path: Path, backlog_ids: list[str]) -> None:
    """to_file returns the Path to the written scope.json file."""
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    result = sf.to_file(tmp_path)
    expected = tmp_path / ".devbench" / "scope.json"
    assert result == expected


@pytest.mark.unit
def test_to_file_started_at_is_iso8601_utc(tmp_path: Path, backlog_ids: list[str]) -> None:
    """to_file writes started_at in ISO-8601 UTC format (YYYY-MM-DDTHH:MM:SSZ)."""
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    sf.to_file(tmp_path)
    data = json.loads((tmp_path / ".devbench" / "scope.json").read_text())
    iso8601_utc_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert iso8601_utc_re.match(data["started_at"]), (
        f"started_at '{data['started_at']}' does not match ISO-8601 UTC format YYYY-MM-DDTHH:MM:SSZ"
    )


@pytest.mark.unit
def test_from_file_invalid_json_raises(tmp_path: Path) -> None:
    """from_file raises json.JSONDecodeError when scope.json contains invalid JSON."""
    scope_dir = tmp_path / ".devbench"
    scope_dir.mkdir(parents=True)
    (scope_dir / "scope.json").write_text("{ not valid json }")
    with pytest.raises(json.JSONDecodeError):
        ScopeFilter.from_file(tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize("missing_key", ["include", "exclude", "expanded_ids"])
def test_from_file_missing_key_raises_with_key_name(tmp_path: Path, missing_key: str) -> None:
    """from_file raises KeyError naming the missing key for each required field."""
    scope_dir = tmp_path / ".devbench"
    scope_dir.mkdir(parents=True)
    all_keys = {"include": ["E1"], "exclude": [], "expanded_ids": ["E1"]}
    del all_keys[missing_key]
    (scope_dir / "scope.json").write_text(json.dumps(all_keys))
    with pytest.raises(KeyError):
        ScopeFilter.from_file(tmp_path)


@pytest.mark.unit
def test_to_file_overwrites_existing_scope_json(tmp_path: Path, backlog_ids: list[str]) -> None:
    """to_file overwrites any pre-existing scope.json atomically (idempotent)."""
    sf1 = ScopeFilter.parse("E1", "", backlog_ids)
    sf1.to_file(tmp_path)

    sf2 = ScopeFilter.parse("E2", "", backlog_ids)
    sf2.to_file(tmp_path)

    loaded = ScopeFilter.from_file(tmp_path)
    assert loaded.include == ["E2"]
    assert not loaded.allows("E1-F1-S1-T1")
    assert loaded.allows("E2-F1-S1-T1")


@pytest.mark.unit
def test_to_file_no_temp_file_left_on_success(tmp_path: Path, backlog_ids: list[str]) -> None:
    """to_file leaves no .json.tmp artefact after a successful write."""
    sf = ScopeFilter.parse("E1", "", backlog_ids)
    sf.to_file(tmp_path)
    tmp_file = tmp_path / ".devbench" / "scope.json.tmp"
    assert not tmp_file.exists(), "Stale .json.tmp file found after successful to_file()"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_payload",
    [
        {"include": "E1", "exclude": [], "expanded_ids": []},  # include is str not list
        {"include": [], "exclude": "E2", "expanded_ids": []},  # exclude is str not list
        {"include": [], "exclude": [], "expanded_ids": "E1-F1-S1-T1"},  # expanded_ids is str not list
    ],
)
def test_from_file_invalid_field_type_raises(tmp_path: Path, bad_payload: dict) -> None:
    """from_file raises TypeError when a required field has the wrong type.

    The spec schema requires include, exclude, and expanded_ids to be lists.
    A scope.json with wrong types (e.g. a bare string) must fail fast with
    a TypeError rather than silently producing a broken ScopeFilter.
    """
    scope_dir = tmp_path / ".devbench"
    scope_dir.mkdir(parents=True)
    (scope_dir / "scope.json").write_text(json.dumps(bad_payload))
    with pytest.raises(TypeError):
        ScopeFilter.from_file(tmp_path)
