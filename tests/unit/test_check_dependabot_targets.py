"""Unit tests for `tools.check_dependabot_targets`.

Covers _parse_version, _compare_versions (including unequal-length version
tuples), _extract_locked_versions' three raise paths, build_matrix's exact
spec G-6 line text and field widths against a tmp_path lock fixture, and
main()'s error-to-exit-1 mapping for a missing lock, invalid TOML, a lock
with no [[package]] table, and a declared target absent from the lock --
each asserting stdout stayed empty on the failure paths.
"""

from __future__ import annotations

from pathlib import Path

import check_dependabot_targets as cdt
import pytest


def _write_lock(tmp_path: Path, body: str, name: str = "uv.lock") -> Path:
    lock_path = tmp_path / name
    lock_path.write_text(body, encoding="utf-8")
    return lock_path


# The eight package/version pairs below reproduce spec G-6's worked example
# verbatim: the six mcp-family targets locked exactly at their target
# version (SATISFIED), and idna/urllib3 locked below their target (NEEDS
# BUMP). Used to build a full, valid tmp_path uv.lock fixture.
_SATISFYING_LOCK_VERSIONS: dict[str, str] = {
    "mcp": "1.28.1",
    "pydantic-settings": "2.14.2",
    "starlette": "1.3.1",
    "cryptography": "48.0.1",
    "python-multipart": "0.0.31",
    "pyjwt": "2.13.0",
    "idna": "3.11",
    "urllib3": "2.6.3",
}


def _build_full_lock_body(versions: dict[str, str]) -> str:
    """Render a minimal but valid uv.lock TOML body from a name/version map."""
    entries = "\n\n".join(f'[[package]]\nname = "{name}"\nversion = "{version}"' for name, version in versions.items())
    return entries + "\n"


@pytest.mark.unit
class TestParseVersion:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("1.28.1", (1, 28, 1)),
            ("2.7.0", (2, 7, 0)),
            ("0", (0,)),
            ("10.20.30.40", (10, 20, 30, 40)),
        ],
    )
    def test_parses_dotted_numeric_versions(self, version: str, expected: tuple[int, ...]) -> None:
        assert cdt._parse_version(version) == expected

    def test_rejects_non_numeric_segment(self) -> None:
        with pytest.raises(ValueError, match=r"cannot parse version '1\.3\.1b1'"):
            cdt._parse_version("1.3.1b1")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="cannot parse version"):
            cdt._parse_version("")


@pytest.mark.unit
class TestCompareVersions:
    @pytest.mark.parametrize(
        ("locked", "target", "expected_operator", "expected_satisfied"),
        [
            # Equal versions: satisfied.
            ((1, 28, 1), (1, 28, 1), cdt.GTE_OPERATOR, True),
            # Locked strictly greater: satisfied.
            ((1, 29, 0), (1, 28, 1), cdt.GTE_OPERATOR, True),
            # Locked strictly less: needs bump.
            ((3, 11), (3, 15), cdt.LT_OPERATOR, False),
            ((2, 6, 3), (2, 7, 0), cdt.LT_OPERATOR, False),
            # Unequal-length tuples: locked shorter than target, padded with
            # trailing zeros before comparison.
            ((1, 3), (1, 3, 0), cdt.GTE_OPERATOR, True),
            ((1, 3), (1, 3, 1), cdt.LT_OPERATOR, False),
            # Unequal-length tuples: locked longer than target.
            ((0, 0, 32), (0, 0, 31), cdt.GTE_OPERATOR, True),
            ((2,), (1, 9, 9), cdt.GTE_OPERATOR, True),
            ((1, 9, 9), (2,), cdt.LT_OPERATOR, False),
        ],
    )
    def test_compares_padded_version_tuples(
        self,
        locked: tuple[int, ...],
        target: tuple[int, ...],
        expected_operator: str,
        expected_satisfied: bool,
    ) -> None:
        operator, satisfied = cdt._compare_versions(locked, target)
        assert operator == expected_operator
        assert satisfied is expected_satisfied


@pytest.mark.unit
class TestLoadLockData:
    def test_raises_file_not_found_for_missing_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.lock"
        with pytest.raises(FileNotFoundError, match=r"lock file not found: .*does-not-exist\.lock"):
            cdt._load_lock_data(missing)

    def test_raises_value_error_for_invalid_toml(self, tmp_path: Path) -> None:
        bad_lock = _write_lock(tmp_path, "this is not [ valid toml", name="bad.lock")
        with pytest.raises(ValueError, match=r"lock file is not valid TOML: .*bad\.lock"):
            cdt._load_lock_data(bad_lock)

    def test_loads_valid_toml(self, tmp_path: Path) -> None:
        lock_path = _write_lock(tmp_path, '[[package]]\nname = "mcp"\nversion = "1.28.1"\n')
        data = cdt._load_lock_data(lock_path)
        assert data["package"] == [{"name": "mcp", "version": "1.28.1"}]


@pytest.mark.unit
class TestExtractLockedVersions:
    def test_raises_when_package_table_missing(self, tmp_path: Path) -> None:
        lock_path = Path("unused.lock")
        with pytest.raises(ValueError, match=r"no \[\[package\]\] entries: unused\.lock"):
            cdt._extract_locked_versions({}, lock_path)

    def test_raises_when_package_table_is_wrong_type(self, tmp_path: Path) -> None:
        lock_path = Path("unused.lock")
        with pytest.raises(ValueError, match=r"no \[\[package\]\] entries"):
            cdt._extract_locked_versions({"package": "not-a-list"}, lock_path)

    def test_raises_when_entry_is_not_a_table(self, tmp_path: Path) -> None:
        lock_path = Path("unused.lock")
        with pytest.raises(ValueError, match=r"malformed \[\[package\]\] entry"):
            cdt._extract_locked_versions({"package": ["not-a-dict"]}, lock_path)

    def test_raises_when_entry_missing_name_or_version(self, tmp_path: Path) -> None:
        lock_path = Path("unused.lock")
        with pytest.raises(ValueError, match=r"entry missing name/version"):
            cdt._extract_locked_versions({"package": [{"name": "mcp"}]}, lock_path)

    def test_returns_name_to_version_map(self) -> None:
        data: dict[str, object] = {
            "package": [
                {"name": "mcp", "version": "1.28.1"},
                {"name": "idna", "version": "3.11"},
            ]
        }
        versions = cdt._extract_locked_versions(data, Path("unused.lock"))
        assert versions == {"mcp": "1.28.1", "idna": "3.11"}


@pytest.mark.unit
class TestFormatLine:
    def test_satisfied_line_matches_spec_g6_worked_example(self) -> None:
        target = cdt.DependabotTarget("mcp", "1.28.1", 287, "E1")
        line = cdt._format_line(target, "1.28.1", cdt.GTE_OPERATOR, True)
        assert line == "mcp                  1.28.1 >= 1.28.1  SATISFIED (by E1)"

    def test_needs_bump_line_matches_spec_g6_worked_example(self) -> None:
        target = cdt.DependabotTarget("urllib3", "2.7.0", 179, "E6-F1-S1-T2")
        line = cdt._format_line(target, "2.6.3", cdt.LT_OPERATOR, False)
        assert line == "urllib3              2.6.3  <  2.7.0   NEEDS BUMP"


@pytest.mark.unit
class TestBuildMatrix:
    def test_returns_full_eight_line_matrix_with_exact_text(self, tmp_path: Path) -> None:
        lock_path = _write_lock(tmp_path, _build_full_lock_body(_SATISFYING_LOCK_VERSIONS))
        lines = cdt.build_matrix(lock_path)
        assert lines == [
            "mcp                  1.28.1 >= 1.28.1  SATISFIED (by E1)",
            "pydantic-settings    2.14.2 >= 2.14.2  SATISFIED (by E1)",
            "starlette            1.3.1  >= 1.3.1   SATISFIED (by E1)",
            "cryptography         48.0.1 >= 48.0.1  SATISFIED (by E1)",
            "python-multipart     0.0.31 >= 0.0.31  SATISFIED (by E1)",
            "pyjwt                2.13.0 >= 2.13.0  SATISFIED (by E1)",
            "idna                 3.11   <  3.15    NEEDS BUMP",
            "urllib3              2.6.3  <  2.7.0   NEEDS BUMP",
        ]

    def test_raises_when_a_declared_target_is_absent_from_lock(self, tmp_path: Path) -> None:
        # Drop "urllib3" from the lock body entirely: a target declared in
        # TARGETS but absent from the parsed lock must raise, naming the
        # package and its source PR number, rather than silently skipping it.
        incomplete_versions = {k: v for k, v in _SATISFYING_LOCK_VERSIONS.items() if k != "urllib3"}
        lock_path = _write_lock(tmp_path, _build_full_lock_body(incomplete_versions))
        with pytest.raises(ValueError, match=r"package 'urllib3' \(PR #179\) not found"):
            cdt.build_matrix(lock_path)


@pytest.mark.unit
class TestMain:
    def test_prints_full_matrix_and_returns_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        lock_path = _write_lock(tmp_path, _build_full_lock_body(_SATISFYING_LOCK_VERSIONS))
        rc = cdt.main(["--lock-path", str(lock_path)])
        out, err = capsys.readouterr()
        assert rc == 0
        assert err == ""
        printed_lines = out.splitlines()
        assert len(printed_lines) == 8
        assert printed_lines[0] == "mcp                  1.28.1 >= 1.28.1  SATISFIED (by E1)"
        assert printed_lines[-1] == "urllib3              2.6.3  <  2.7.0   NEEDS BUMP"

    def test_returns_1_and_prints_no_matrix_for_missing_lock(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "missing.lock"
        rc = cdt.main(["--lock-path", str(missing)])
        out, err = capsys.readouterr()
        assert rc == 1
        assert out == ""
        assert f"ERROR: lock file not found: {missing}" in err

    def test_returns_1_and_prints_no_matrix_for_invalid_toml(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad_lock = _write_lock(tmp_path, "not [ valid toml at all", name="bad.lock")
        rc = cdt.main(["--lock-path", str(bad_lock)])
        out, err = capsys.readouterr()
        assert rc == 1
        assert out == ""
        assert "ERROR: lock file is not valid TOML" in err

    def test_returns_1_and_prints_no_matrix_for_missing_package_table(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        no_packages_lock = _write_lock(tmp_path, 'version = 1\nrevision = 2\nrequires-python = ">=3.12"\n')
        rc = cdt.main(["--lock-path", str(no_packages_lock)])
        out, err = capsys.readouterr()
        assert rc == 1
        assert out == ""
        assert "ERROR: lock file has no [[package]] entries" in err

    def test_returns_1_and_prints_no_matrix_for_target_absent_from_lock(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        incomplete_versions = {k: v for k, v in _SATISFYING_LOCK_VERSIONS.items() if k != "idna"}
        lock_path = _write_lock(tmp_path, _build_full_lock_body(incomplete_versions))
        rc = cdt.main(["--lock-path", str(lock_path)])
        out, err = capsys.readouterr()
        assert rc == 1
        assert out == ""
        assert "ERROR: package 'idna' (PR #216) not found" in err

    def test_defaults_to_uv_lock_in_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        parser = cdt._build_parser()
        args = parser.parse_args([])
        assert args.lock_path == cdt.DEFAULT_LOCK_PATH
