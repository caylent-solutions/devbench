"""Unit tests for the file-based per-round review token (ADR-29).

These tests pin the public contract of ``devbench.review_token``:

- ``token_path`` resolves the token file under ``<workspace>/.devbench/``.
- ``new_token`` writes a fresh ``<unit-id>-r<n>-<rand>`` token, increments the
  per-unit round counter, and rejects an empty unit id.
- ``read_token`` returns exactly what ``new_token`` wrote.
- ``clear_token`` removes the file and reports whether anything was removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench import review_token


@pytest.mark.unit
class TestTokenPath:
    """token_path resolves to <workspace>/.devbench/review-round-token."""

    def test_token_path_is_under_devbench_dir(self, tmp_path: Path) -> None:
        path = review_token.token_path(tmp_path)
        assert path == tmp_path / ".devbench" / "review-round-token"

    def test_token_path_uses_module_filename_constant(self, tmp_path: Path) -> None:
        """The path's filename must be the module's published TOKEN_FILENAME."""
        path = review_token.token_path(tmp_path)
        assert path.name == review_token.TOKEN_FILENAME


@pytest.mark.unit
class TestNewToken:
    """new_token writes the token file and returns a unit-scoped round token."""

    def test_new_token_writes_file_and_returns_round_one_token(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        token = review_token.new_token(tmp_path, unit_id)

        assert token.startswith(f"{unit_id}-r1-"), (
            f"first token for {unit_id} must start with '{unit_id}-r1-'; got {token!r}"
        )
        token_file = review_token.token_path(tmp_path)
        assert token_file.is_file(), "new_token must write the token file"
        assert token_file.read_text(encoding="utf-8") == token, (
            "the on-disk token file must contain exactly the returned token"
        )

    def test_new_token_writes_counter_file(self, tmp_path: Path) -> None:
        """The per-unit round counter store must be created alongside the token."""
        review_token.new_token(tmp_path, "E0-F1-S1-T1")
        counter_file = tmp_path / ".devbench" / review_token.COUNTER_FILENAME
        assert counter_file.is_file(), "new_token must persist the round-counter store"

    def test_second_call_same_unit_increments_to_round_two(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        first = review_token.new_token(tmp_path, unit_id)
        second = review_token.new_token(tmp_path, unit_id)

        assert first.startswith(f"{unit_id}-r1-")
        assert second.startswith(f"{unit_id}-r2-"), (
            f"second token for the SAME unit must advance to round 2; got {second!r}"
        )
        assert review_token.read_token(tmp_path) == second, (
            "the second new_token call must overwrite the token file with the round-2 token"
        )

    def test_different_unit_starts_at_round_one(self, tmp_path: Path) -> None:
        review_token.new_token(tmp_path, "E0-F1-S1-T1")
        other = review_token.new_token(tmp_path, "E0-F1-S2-T9")

        assert other.startswith("E0-F1-S2-T9-r1-"), (
            f"a different unit must start its own counter at round 1; got {other!r}"
        )

    def test_random_suffix_differs_across_calls(self, tmp_path: Path) -> None:
        """Two tokens for distinct units (same round number) must differ only by
        their cryptographically random suffix, and that suffix must differ."""
        first = review_token.new_token(tmp_path, "UNIT-A")
        second = review_token.new_token(tmp_path, "UNIT-B")

        first_suffix = first.rsplit("-", 1)[-1]
        second_suffix = second.rsplit("-", 1)[-1]
        assert first.startswith("UNIT-A-r1-")
        assert second.startswith("UNIT-B-r1-")
        assert first_suffix != second_suffix, (
            "the random suffix must differ across calls so a token cannot be guessed/replayed; "
            f"both were {first_suffix!r}"
        )

    def test_new_token_strips_and_uses_unit_id(self, tmp_path: Path) -> None:
        """A surrounding-whitespace unit id is trimmed before being embedded."""
        token = review_token.new_token(tmp_path, "  E0-F1-S1-T1  ")
        assert token.startswith("E0-F1-S1-T1-r1-"), (
            f"new_token must strip surrounding whitespace from the unit id; got {token!r}"
        )

    def test_new_token_rejects_empty_unit_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            review_token.new_token(tmp_path, "")

    def test_new_token_rejects_whitespace_only_unit_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            review_token.new_token(tmp_path, "   ")

    def test_new_token_does_not_write_file_on_empty_unit_id(self, tmp_path: Path) -> None:
        """Fail-fast: a rejected call must not create the token file."""
        with pytest.raises(ValueError):
            review_token.new_token(tmp_path, "")
        assert not review_token.token_path(tmp_path).exists(), "a rejected new_token call must not write a token file"


@pytest.mark.unit
class TestReadToken:
    """read_token returns the current token or None when absent/empty."""

    def test_read_token_returns_written_value(self, tmp_path: Path) -> None:
        written = review_token.new_token(tmp_path, "E0-F1-S1-T1")
        assert review_token.read_token(tmp_path) == written

    def test_read_token_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert review_token.read_token(tmp_path) is None, (
            "read_token must return None when the token file does not exist"
        )

    def test_read_token_strips_trailing_whitespace(self, tmp_path: Path) -> None:
        """A token file written with trailing whitespace reads back stripped."""
        path = review_token.token_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("E0-F1-S1-T1-r1-deadbeef\n", encoding="utf-8")
        assert review_token.read_token(tmp_path) == "E0-F1-S1-T1-r1-deadbeef"

    def test_read_token_returns_none_when_empty(self, tmp_path: Path) -> None:
        """An empty/whitespace token file is treated as no token."""
        path = review_token.token_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("   \n", encoding="utf-8")
        assert review_token.read_token(tmp_path) is None


@pytest.mark.unit
class TestClearToken:
    """clear_token removes the file and reports whether anything was removed."""

    def test_clear_token_removes_existing_file_and_returns_true(self, tmp_path: Path) -> None:
        review_token.new_token(tmp_path, "E0-F1-S1-T1")
        assert review_token.token_path(tmp_path).is_file()

        removed = review_token.clear_token(tmp_path)
        assert removed is True, "clear_token must return True when it removed a file"
        assert not review_token.token_path(tmp_path).exists(), "clear_token must delete the token file"

    def test_clear_token_returns_false_when_already_absent(self, tmp_path: Path) -> None:
        review_token.new_token(tmp_path, "E0-F1-S1-T1")
        assert review_token.clear_token(tmp_path) is True

        second = review_token.clear_token(tmp_path)
        assert second is False, "a second clear_token call (file already gone) must return False"

    def test_clear_token_returns_false_on_fresh_workspace(self, tmp_path: Path) -> None:
        assert review_token.clear_token(tmp_path) is False, (
            "clear_token must return False when no token file was ever written"
        )

    def test_read_token_returns_none_after_clear(self, tmp_path: Path) -> None:
        review_token.new_token(tmp_path, "E0-F1-S1-T1")
        review_token.clear_token(tmp_path)
        assert review_token.read_token(tmp_path) is None, "read_token must return None after the token is cleared"
