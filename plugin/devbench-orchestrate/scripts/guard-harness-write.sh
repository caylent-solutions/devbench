#!/usr/bin/env bash
# guard-harness-write.sh -- PreToolUse hook: "guard the HARNESS source".
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Write"|"Edit", "tool_input": { "file_path": "...", ... } }
#
# The orchestrate session (and its sub-agents) run devbench from an editable
# source checkout, so the harness's own Python package (`src/devbench/**`),
# build files (`pyproject.toml`, the lockfile, `Makefile`), and package test
# tree (`tests/**`) are writable from inside the session. An autonomous session
# once patched `src/devbench/cli.py` mid-run -- unreviewed, no audit marker.
# The harness must never be editable by the very session it is running.
#
# This hook HARD-DENIES (exit 2) any Write/Edit whose target resolves UNDER the
# devbench repo's protected harness surface. There is NO role bypass (not even
# DEVBENCH_AGENT_ROLE=orchestrator). On a denial it emits the deterministic
# `[HARNESS_SELF_EDIT_BLOCKED]` marker to stderr (Claude's feedback) with the
# sanctioned alternative: BLOCK the unit and record the harness bug as a
# `tracked-devbench-issues/*.md` for the operator.
#
# GENERIC resolution -- no hardcoded workspace/absolute path:
#   The devbench repo root is found by walking up from this script's REAL
#   location (resolving symlinks, so the shadow plugin's symlinked copy points
#   back here) until a directory contains BOTH `src/devbench` AND
#   `pyproject.toml`. Only paths under THAT root are protected, so a target
#   repo that merely happens to contain a `src/devbench/`-shaped tree, or a
#   foreign checkout, is never wrongly blocked.
#
# Protected categories under the resolved devbench repo root (target under ANY
# -> exit 2):
#   1. The package source tree:   <root>/src/devbench/**
#   2. The package test tree:     <root>/tests/**
#   3. Build / dependency files:  <root>/pyproject.toml, <root>/uv.lock,
#                                 <root>/poetry.lock, <root>/Makefile
#
# Everything else -> exit 0 (allow). Docs under the repo are NOT harness logic
# and stay editable (docs-in-sync rule).
#
# Exit 0  -> operation is allowed (Claude proceeds)
# Exit 2  -> operation is blocked (stderr becomes Claude's feedback)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
FILE_PATH=$(extract_file_path "$INPUT")
decode_json_escapes FILE_PATH

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Resolve a path to absolute form without requiring it to exist.
abspath() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *)  printf '%s/%s' "$PWD" "$1" ;;
  esac
}

# Resolve the devbench repo root GENERICALLY: walk up from this script's real
# directory until we find a dir holding both `src/devbench` and
# `pyproject.toml`. ``readlink -f`` resolves the shadow plugin's symlinked
# script back to the canonical checkout, so the guard anchors on the real
# devbench repo regardless of whether the canonical or shadow plugin loaded.
resolve_devbench_root() {
  local start dir
  if command -v readlink >/dev/null 2>&1 && readlink -f "${BASH_SOURCE[0]}" >/dev/null 2>&1; then
    start="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
  else
    start="$SCRIPT_DIR"
  fi
  dir="$start"
  while [[ "$dir" != "/" && -n "$dir" ]]; do
    if [[ -d "$dir/src/devbench" && -f "$dir/pyproject.toml" ]]; then
      printf '%s' "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  return 1
}

# If the devbench root cannot be resolved (unexpected layout) we cannot decide
# scope safely; fail OPEN here is unacceptable for a guard, but a hard deny on
# every write would break the session. The canonical layout always resolves;
# an unresolvable layout means the hook is mis-installed -- surface it loudly
# and deny so the misconfiguration is caught rather than silently bypassed.
if ! DEVBENCH_ROOT="$(resolve_devbench_root)"; then
  echo "[HARNESS_SELF_EDIT_BLOCKED] guard-harness-write: cannot resolve the devbench repo root" >&2
  echo "from this script's location; the hook is mis-installed. Refusing the write to fail safe." >&2
  exit 2
fi

FILE_PATH_ABS="$(abspath "$FILE_PATH")"

deny() {
  # $1 = short rule label, $2 = human-readable category description.
  echo "[HARNESS_SELF_EDIT_BLOCKED] guard-harness-write: BLOCKED ${1}: ${FILE_PATH}" >&2
  echo "Reason: ${2}." >&2
  echo "The orchestrate session must NEVER edit the harness it is running." >&2
  echo "Sanctioned path: BLOCK this unit and record the harness bug as a" >&2
  echo "tracked-devbench-issues/*.md for the operator to resolve at a stop-window." >&2
  echo "There is NO role bypass (not even DEVBENCH_AGENT_ROLE=orchestrator)." >&2
  exit 2
}

# Category 1: the package source tree.
if [[ "$FILE_PATH_ABS" == "$DEVBENCH_ROOT/src/devbench/"* ]]; then
  deny "package-source" "target is under the devbench package source (src/devbench/**)"
fi

# Category 2: the package test tree.
if [[ "$FILE_PATH_ABS" == "$DEVBENCH_ROOT/tests/"* ]]; then
  deny "package-test" "target is under the devbench package test tree (tests/**)"
fi

# Category 3: build / dependency files at the repo root.
if [[ "$FILE_PATH_ABS" == "$DEVBENCH_ROOT/pyproject.toml" ]]; then
  deny "pyproject" "target is the devbench package manifest (pyproject.toml)"
fi
if [[ "$FILE_PATH_ABS" == "$DEVBENCH_ROOT/uv.lock" ]] \
  || [[ "$FILE_PATH_ABS" == "$DEVBENCH_ROOT/poetry.lock" ]]; then
  deny "lockfile" "target is the devbench dependency lockfile"
fi
if [[ "$FILE_PATH_ABS" == "$DEVBENCH_ROOT/Makefile" ]]; then
  deny "makefile" "target is the devbench Makefile"
fi

exit 0
