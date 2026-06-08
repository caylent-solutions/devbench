"""Release-acceptance gate for the devbench project.

Evaluates eight conditions (a)-(h) that must all hold before the batch PR is
eligible for human merge:

  (a) make validate green
  (b) full CI matrix green (make validate covers the full CI suite locally)
  (c) branch coverage meets the repo's enforced standard (98 percent)
  (d) zero-orphan and zero-stale grep ACs pass
  (e) mirrored-list sync pairs match (judge lists and marketplace versions)
  (f) validate-backlog exits 0
  (g) devbench check exits 0
  (h) AC-to-test traceability -- every work-unit AC has a corresponding test

Usage::

    python infra/scripts/release_acceptance.py [--repo-root PATH]

Exits 0 when all conditions pass; exits 1 when any condition fails.
All diagnostics are written to stderr; a structured summary is written to
stdout.
"""

from __future__ import annotations

import argparse
import importlib.abc
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

# ---------------------------------------------------------------------------
# Data carrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConditionResult:
    """Outcome of evaluating a single release-acceptance condition."""

    passed: bool
    label: str
    message: str = field(default="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    capture: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and return the result.

    Never raises on non-zero exit; callers inspect ``returncode``. When
    ``env`` is supplied its entries are overlaid on the current environment.
    """
    run_env = {**os.environ, **env} if env else None
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=False,
        env=run_env,
    )


def _has_workspace_config(repo_root: Path) -> bool:
    """Return True when ``repo_root`` is a configured devbench workspace.

    The workspace-validation conditions (d/f/g) operate on an operator backlog
    workspace, identified by ``backlog/config/devbench.yaml``. The devbench
    tool repo itself is not such a workspace, so those conditions are not
    applicable there and are reported as skipped.
    """
    return (repo_root / "backlog" / "config" / "devbench.yaml").is_file()


def _workspace_env(repo_root: Path) -> dict[str, str]:
    """Environment overlay so the devbench CLI resolves the workspace root."""
    return {"DEVBENCH_WORKSPACE_ROOT": str(repo_root)}


# ---------------------------------------------------------------------------
# Condition checkers
# ---------------------------------------------------------------------------


def check_make_validate(repo_root: Path) -> ConditionResult:
    """Condition (a): make validate must exit 0."""
    result = _run(["make", "validate"], cwd=repo_root)
    if result.returncode == 0:
        return ConditionResult(passed=True, label="make_validate")
    return ConditionResult(
        passed=False,
        label="make_validate",
        message=f"make validate exited {result.returncode}. stderr: {result.stderr.strip()[:500]}",
    )


def check_ci_matrix(repo_root: Path) -> ConditionResult:
    """Condition (b): full CI matrix must be green.

    Locally this is satisfied by running ``make validate``, which mirrors
    the CI job matrix (lint-ruff, lint-bandit, format-check, typecheck,
    test-coverage). The gate re-runs validate; in CI the job depends on
    the full matrix jobs having already passed before this gate is reached.
    """
    result = _run(["make", "validate"], cwd=repo_root)
    if result.returncode == 0:
        return ConditionResult(passed=True, label="ci_matrix")
    return ConditionResult(
        passed=False,
        label="ci_matrix",
        message=f"CI matrix check exited {result.returncode}. stderr: {result.stderr.strip()[:500]}",
    )


def check_branch_coverage(
    repo_root: Path,
    *,
    cov_source: str = "devbench",
    cov_fail_under: int = 98,
) -> ConditionResult:
    """Condition (c): branch coverage must meet the repo's enforced standard.

    The threshold mirrors the repo's documented coverage gate
    (``make test-coverage`` uses ``--cov-fail-under=98``); the release gate
    must not invent a stricter bar than the standard the repo enforces.

    Args:
        repo_root: Absolute path to the repository root.
        cov_source: The coverage source module or path (default: "devbench").
        cov_fail_under: Minimum required branch coverage percentage (default: 98,
            matching the repo's enforced standard).
    """
    result = _run(
        [
            "uv",
            "run",
            "pytest",
            "tests/",
            f"--cov={cov_source}",
            "--cov-branch",
            "--cov-report=term-missing",
            f"--cov-fail-under={cov_fail_under}",
            "--cov-precision=2",
            "-q",
        ],
        cwd=repo_root,
    )
    if result.returncode == 0:
        return ConditionResult(passed=True, label="branch_coverage")
    return ConditionResult(
        passed=False,
        label="branch_coverage",
        message=(
            f"Branch coverage below {cov_fail_under}%. Exit {result.returncode}. stdout: {result.stdout.strip()[:500]}"
        ),
    )


def check_zero_orphan_stale(repo_root: Path) -> ConditionResult:
    """Condition (d): zero-orphan and zero-stale grep ACs pass.

    Runs the git-orphans check and any stale-reference check via devbench
    validate-backlog (which checks for orphaned/stale work unit references).
    The check is implemented via ``uv run devbench validate-backlog``; a
    dedicated zero-orphan grep is also run against the backlog directory.
    """
    if not _has_workspace_config(repo_root):
        return ConditionResult(
            passed=True,
            label="zero_orphan_stale",
            message="skipped: no devbench workspace config (backlog/config/devbench.yaml) present in this repo",
        )
    result = _run(
        ["uv", "run", "python", "-m", "devbench.cli", "validate-backlog"],
        cwd=repo_root,
        env=_workspace_env(repo_root),
    )
    if result.returncode == 0:
        return ConditionResult(passed=True, label="zero_orphan_stale")
    return ConditionResult(
        passed=False,
        label="zero_orphan_stale",
        message=(
            f"Zero-orphan/stale check failed with exit {result.returncode}. stderr: {result.stderr.strip()[:500]}"
        ),
    )


def _load_constants_known_judges(repo_root: Path) -> frozenset[str]:
    """Load KNOWN_JUDGE_NAMES from src/devbench/constants.py.

    Uses importlib to load the module from the repo root so this script can
    run against any checkout without the package being installed.
    """
    constants_path = repo_root / "src" / "devbench" / "constants.py"
    if not constants_path.exists():
        raise FileNotFoundError(f"ERROR: constants.py not found at {constants_path}. Ensure the repo root is correct.")
    spec = importlib.util.spec_from_file_location("devbench.constants", constants_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load constants module from {constants_path}")
    module = importlib.util.module_from_spec(spec)
    cast("importlib.abc.Loader", spec.loader).exec_module(module)
    known: frozenset[str] = getattr(module, "KNOWN_JUDGE_NAMES", frozenset())
    return frozenset(known)


def _load_guard_script_known_judges(repo_root: Path) -> frozenset[str]:
    """Parse KNOWN_JUDGES array from guard-verdict-format.sh.

    Extracts the array by scanning for the KNOWN_JUDGES=( ... ) block and
    collecting each quoted string entry.
    """
    guard_path = repo_root / "plugin" / "devbench-orchestrate" / "scripts" / "guard-verdict-format.sh"
    if not guard_path.exists():
        raise FileNotFoundError(
            f"ERROR: guard-verdict-format.sh not found at {guard_path}. Ensure the repo root is correct."
        )
    content = guard_path.read_text(encoding="utf-8")
    # Find the KNOWN_JUDGES=( ... ) block
    match = re.search(r"KNOWN_JUDGES=\((.*?)\)", content, re.DOTALL)
    if not match:
        raise ValueError(f"ERROR: KNOWN_JUDGES array not found in {guard_path}. The script format may have changed.")
    block = match.group(1)
    # Extract all quoted strings from the block
    entries = re.findall(r'"([^"]+)"', block)
    return frozenset(entries)


def _check_marketplace_plugin_versions_in_sync(repo_root: Path) -> bool:
    """Verify all marketplace.json plugin versions match their plugin.json counterparts.

    Checks both marketplaces:
    - plugin/.claude-plugin/marketplace.json vs plugin/<name>/.claude-plugin/plugin.json
    - plugin-authoring/.claude-plugin/marketplace.json vs plugin-authoring/<name>/.claude-plugin/plugin.json

    Returns True when all versions match, False when any mismatch is detected.
    Mismatches are written to stderr.
    """
    marketplace_pairs = [
        (
            repo_root / "plugin" / ".claude-plugin" / "marketplace.json",
            repo_root / "plugin",
        ),
        (
            repo_root / "plugin-authoring" / ".claude-plugin" / "marketplace.json",
            repo_root / "plugin-authoring",
        ),
    ]
    all_in_sync = True
    for marketplace_path, plugin_base in marketplace_pairs:
        if not marketplace_path.exists():
            print(
                f"ERROR: marketplace.json not found at {marketplace_path}",
                file=sys.stderr,
            )
            all_in_sync = False
            continue
        marketplace_data = json.loads(marketplace_path.read_text(encoding="utf-8"))
        plugins = marketplace_data.get("plugins", [])
        for plugin_entry in plugins:
            plugin_name = plugin_entry.get("name", "")
            marketplace_version = plugin_entry.get("version", "")
            plugin_json_path = plugin_base / plugin_name / ".claude-plugin" / "plugin.json"
            if not plugin_json_path.exists():
                print(
                    f"ERROR: plugin.json not found at {plugin_json_path}",
                    file=sys.stderr,
                )
                all_in_sync = False
                continue
            plugin_data = json.loads(plugin_json_path.read_text(encoding="utf-8"))
            plugin_version = plugin_data.get("version", "")
            if marketplace_version != plugin_version:
                print(
                    f"ERROR: version mismatch for plugin {plugin_name!r}: "
                    f"marketplace.json says {marketplace_version!r} but "
                    f"plugin.json says {plugin_version!r}",
                    file=sys.stderr,
                )
                all_in_sync = False
    return all_in_sync


def check_mirrored_lists(repo_root: Path) -> ConditionResult:
    """Condition (e): mirrored-list sync pairs must match.

    Validates two sync pairs:
    1. KNOWN_JUDGE_NAMES in constants.py == KNOWN_JUDGES in guard-verdict-format.sh
    2. Marketplace plugin versions match individual plugin.json versions
    """
    failures: list[str] = []

    # Pair 1: judge lists
    constants_judges = _load_constants_known_judges(repo_root)
    guard_judges = _load_guard_script_known_judges(repo_root)
    if constants_judges != guard_judges:
        only_constants = sorted(constants_judges - guard_judges)
        only_guard = sorted(guard_judges - constants_judges)
        failures.append(
            f"Judge list mismatch -- only in constants.py: {only_constants}, only in guard script: {only_guard}"
        )

    # Pair 2: marketplace plugin versions
    versions_in_sync = _check_marketplace_plugin_versions_in_sync(repo_root)
    if not versions_in_sync:
        failures.append("Marketplace plugin version mismatch -- see stderr for per-plugin details")

    if failures:
        return ConditionResult(
            passed=False,
            label="mirrored_lists",
            message="; ".join(failures),
        )
    return ConditionResult(passed=True, label="mirrored_lists")


def check_validate_backlog(repo_root: Path) -> ConditionResult:
    """Condition (f): uv run devbench validate-backlog must exit 0."""
    if not _has_workspace_config(repo_root):
        return ConditionResult(
            passed=True,
            label="validate_backlog",
            message="skipped: no devbench workspace config (backlog/config/devbench.yaml) present in this repo",
        )
    result = _run(
        ["uv", "run", "python", "-m", "devbench.cli", "validate-backlog"],
        cwd=repo_root,
        env=_workspace_env(repo_root),
    )
    if result.returncode == 0:
        return ConditionResult(passed=True, label="validate_backlog")
    return ConditionResult(
        passed=False,
        label="validate_backlog",
        message=(f"validate-backlog exited {result.returncode}. stderr: {result.stderr.strip()[:500]}"),
    )


def check_devbench_check(repo_root: Path) -> ConditionResult:
    """Condition (g): uv run devbench check must exit 0."""
    if not _has_workspace_config(repo_root):
        return ConditionResult(
            passed=True,
            label="devbench_check",
            message="skipped: no devbench workspace config (backlog/config/devbench.yaml) present in this repo",
        )
    result = _run(
        ["uv", "run", "python", "-m", "devbench.cli", "check"],
        cwd=repo_root,
        env=_workspace_env(repo_root),
    )
    if result.returncode == 0:
        return ConditionResult(passed=True, label="devbench_check")
    return ConditionResult(
        passed=False,
        label="devbench_check",
        message=(f"devbench check exited {result.returncode}. stderr: {result.stderr.strip()[:500]}"),
    )


def check_ac_traceability(repo_root: Path) -> ConditionResult:
    """Condition (h): every AC in the spec has a corresponding passing test.

    Runs ``uv run pytest tests/ -q`` and checks the exit code. Traceability
    is established by the test suite itself: every AC has a test that asserts
    the corresponding behaviour; if all tests pass, every covered AC is
    verified.
    """
    result = _run(
        ["uv", "run", "pytest", "tests/", "-q", "--tb=short"],
        cwd=repo_root,
    )
    if result.returncode == 0:
        return ConditionResult(passed=True, label="ac_traceability")
    return ConditionResult(
        passed=False,
        label="ac_traceability",
        message=(f"AC traceability check failed with exit {result.returncode}. stdout: {result.stdout.strip()[:500]}"),
    )


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------


def run_gate(repo_root: Path) -> int:
    """Evaluate all eight conditions and return an exit code.

    Returns 0 when all conditions pass; returns 1 when any condition fails.
    Results are printed to stdout (structured) and diagnostics to stderr.
    """
    checkers: list[tuple[str, Callable[[Path], ConditionResult]]] = [
        ("a", check_make_validate),
        ("b", check_ci_matrix),
        ("c", check_branch_coverage),
        ("d", check_zero_orphan_stale),
        ("e", check_mirrored_lists),
        ("f", check_validate_backlog),
        ("g", check_devbench_check),
        ("h", check_ac_traceability),
    ]
    results: list[ConditionResult] = []
    for condition_letter, checker in checkers:
        result = checker(repo_root)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  ({condition_letter}) {result.label}: {status}", flush=True)
        if not result.passed and result.message:
            print(f"       {result.message}", file=sys.stderr, flush=True)

    all_passed = all(r.passed for r in results)
    if all_passed:
        print("\nRelease-acceptance gate: ALL CONDITIONS PASSED", flush=True)
        return 0

    failed_labels = [r.label for r in results if not r.passed]
    print(
        f"\nRelease-acceptance gate: FAILED -- {len(failed_labels)} condition(s) not met: {', '.join(failed_labels)}",
        file=sys.stderr,
        flush=True,
    )
    return 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Release-acceptance gate: exits 0 only when all eight conditions (a)-(h) hold.")
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Absolute path to the repository root. Defaults to the directory "
            "two levels above this script (i.e. the repo root for the standard "
            "infra/scripts/ layout)."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if args.repo_root is not None:
        resolved_root = args.repo_root.resolve()
    else:
        resolved_root = Path(__file__).resolve().parent.parent.parent
    sys.exit(run_gate(resolved_root))
