#!/usr/bin/env python3
"""check_dependabot_targets: reconcile open dependabot PRs against uv.lock.

Compares the eight open dependabot PR targets recorded in spec S1.10 (the
dependency-state table of ``spec/devbench-modernization.md``) against the
versions actually resolved in ``uv.lock``, and prints one line per target in
the exact format of spec G-6's worked example: package name, locked version,
comparison operator against the target, target version, and a verdict
(``SATISFIED (by <attribution>)`` or ``NEEDS BUMP``).

Per spec FR-6.1, a dependabot branch whose target is already satisfied by the
resolved lock MUST NOT be merged -- the matrix printed here is the evidence
used to close those PRs unmerged instead. Per spec FR-6.3, idna (#216) and
urllib3 (#179) are independent of the mcp cascade and are attributed to the
work unit that bumps them explicitly.

Usage:
    uv run python tools/check_dependabot_targets.py [--lock-path PATH]

Exit codes:
    0  the full eight-line matrix printed to stdout
    1  the lock file is missing, unparseable, or missing an expected
       package; no partial matrix is printed
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tomllib
from pathlib import Path

DEFAULT_LOCK_PATH = Path("uv.lock")

NAME_FIELD_WIDTH = 21
LOCKED_FIELD_WIDTH = 7
OPERATOR_FIELD_WIDTH = 3
TARGET_FIELD_WIDTH = 8

GTE_OPERATOR = ">="
LT_OPERATOR = "<"


@dataclasses.dataclass(frozen=True)
class DependabotTarget:
    """One row of the spec S1.10 eight-target dependency-state matrix."""

    package: str
    target_display: str
    pr_number: int
    satisfied_by: str


# Declarative eight-target matrix transcribed from spec S1.10 (package,
# target version, source PR) and spec G-6 (the "SATISFIED (by ...)"
# attribution label). Six targets sit beneath mcp and are attributed to E1
# per the spec worked example; idna and urllib3 are independent of the mcp
# cascade (spec FR-6.3) and are attributed to the work unit that bumps them.
TARGETS: tuple[DependabotTarget, ...] = (
    DependabotTarget("mcp", "1.28.1", 287, "E1"),
    DependabotTarget("pydantic-settings", "2.14.2", 278, "E1"),
    DependabotTarget("starlette", "1.3.1", 277, "E1"),
    DependabotTarget("cryptography", "48.0.1", 276, "E1"),
    DependabotTarget("python-multipart", "0.0.31", 275, "E1"),
    DependabotTarget("pyjwt", "2.13.0", 274, "E1"),
    DependabotTarget("idna", "3.15", 216, "E6-F1-S1-T2"),
    DependabotTarget("urllib3", "2.7.0", 179, "E6-F1-S1-T2"),
)


def _load_lock_data(lock_path: Path) -> dict[str, object]:
    """Read and parse ``lock_path`` as TOML.

    Raises FileNotFoundError if the path does not exist, or ValueError if
    the file is not valid TOML. Never returns a partial result.
    """
    if not lock_path.is_file():
        raise FileNotFoundError(f"lock file not found: {lock_path}")
    try:
        with lock_path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"lock file is not valid TOML: {lock_path} ({exc})") from exc


def _extract_locked_versions(data: dict[str, object], lock_path: Path) -> dict[str, str]:
    """Build a package-name to locked-version map from parsed uv.lock data."""
    packages = data.get("package")
    if not isinstance(packages, list):
        raise ValueError(f"lock file has no [[package]] entries: {lock_path}")
    versions: dict[str, str] = {}
    for entry in packages:
        if not isinstance(entry, dict):
            raise ValueError(f"malformed [[package]] entry in {lock_path}: {entry!r}")
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError(f"[[package]] entry missing name/version in {lock_path}: {entry!r}")
        versions[name] = version
    return versions


def _parse_version(version: str) -> tuple[int, ...]:
    """Parse a dotted numeric version string into a tuple of ints."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as exc:
        raise ValueError(f"cannot parse version {version!r} as a numeric dotted version") from exc


def _compare_versions(locked: tuple[int, ...], target: tuple[int, ...]) -> tuple[str, bool]:
    """Compare two version tuples, padding the shorter with trailing zeros."""
    length = max(len(locked), len(target))
    padded_locked = locked + (0,) * (length - len(locked))
    padded_target = target + (0,) * (length - len(target))
    if padded_locked >= padded_target:
        return GTE_OPERATOR, True
    return LT_OPERATOR, False


def _format_line(target: DependabotTarget, locked_display: str, operator: str, satisfied: bool) -> str:
    """Render one matrix line in spec G-6's worked-example format."""
    verdict = f"SATISFIED (by {target.satisfied_by})" if satisfied else "NEEDS BUMP"
    return (
        f"{target.package:<{NAME_FIELD_WIDTH}}"
        f"{locked_display:<{LOCKED_FIELD_WIDTH}}"
        f"{operator:<{OPERATOR_FIELD_WIDTH}}"
        f"{target.target_display:<{TARGET_FIELD_WIDTH}}"
        f"{verdict}"
    )


def build_matrix(lock_path: Path) -> list[str]:
    """Build the full eight-line reconciliation matrix, or raise.

    Every one of the declared TARGETS must resolve to a formatted line; if
    any target's package is absent from the lock, the whole call raises
    rather than returning a partial matrix.
    """
    data = _load_lock_data(lock_path)
    locked_versions = _extract_locked_versions(data, lock_path)
    lines: list[str] = []
    for target in TARGETS:
        locked_display = locked_versions.get(target.package)
        if locked_display is None:
            raise ValueError(f"package {target.package!r} (PR #{target.pr_number}) not found in {lock_path}")
        operator, satisfied = _compare_versions(_parse_version(locked_display), _parse_version(target.target_display))
        lines.append(_format_line(target, locked_display, operator, satisfied))
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_dependabot_targets",
        description="Reconcile the eight open dependabot PR targets against uv.lock.",
    )
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=DEFAULT_LOCK_PATH,
        help=f"path to uv.lock (default: {DEFAULT_LOCK_PATH})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        lines = build_matrix(args.lock_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
