#!/usr/bin/env bash
# Ensure the runtime dependencies of the plugin's PreToolUse guard hooks are
# present. `uv sync` covers the devbench Python package, but the hooks under
# plugin/devbench-orchestrate/scripts/ run OUTSIDE that venv, inside the
# operator's Claude Code session, and need:
#
#   1. jq            -- every hook parses its JSON payload with jq.
#   2. PyYAML        -- guard-work-unit-write.sh reads repos.*.checkout_directory
#                       from devbench.yaml (Rule 11) through the SYSTEM python3
#                       (the hook prepends /usr/bin to PATH so asdf shims cannot
#                       hijack resolution). Linux dev containers / CI runners ship
#                       PyYAML in the system python; a fresh macOS or minimal
#                       Linux host does not, and the hook then dies before Rule 11
#                       runs.
#
# Invoked by `make install` (and standalone: ./scripts/install-hook-deps.sh).
# Idempotent. Never uses sudo: when a non-invasive `pip install --user` is not
# possible (PEP 668 externally-managed interpreters, no pip), it prints the
# exact package-manager command and exits non-zero so the gap is loud.
set -euo pipefail

status=0

# --- 1. jq -----------------------------------------------------------------
if command -v jq >/dev/null 2>&1; then
  echo "install-hook-deps: jq $(jq --version 2>/dev/null || echo present)"
else
  echo "install-hook-deps: MISSING jq (hard dependency of every guard hook)." >&2
  echo "  install: brew install jq | sudo apt-get install -y jq | sudo dnf install -y jq" >&2
  status=1
fi

# --- 2. PyYAML for the hook's python3 ---------------------------------------
# Resolve python3 exactly the way guard-work-unit-write.sh does.
HOOK_PYTHON="$(PATH="/usr/bin:$PATH" command -v python3 || true)"
if [[ -z "$HOOK_PYTHON" ]]; then
  echo "install-hook-deps: MISSING python3 on PATH (guard-work-unit-write.sh needs it)." >&2
  status=1
else
  if "$HOOK_PYTHON" -c 'import yaml' >/dev/null 2>&1; then
    echo "install-hook-deps: PyYAML importable by $HOOK_PYTHON"
  else
    echo "install-hook-deps: PyYAML not importable by $HOOK_PYTHON; installing (pip --user)..."
    if "$HOOK_PYTHON" -m pip install --user --quiet --disable-pip-version-check pyyaml >/dev/null 2>&1 \
       && "$HOOK_PYTHON" -c 'import yaml' >/dev/null 2>&1; then
      echo "install-hook-deps: PyYAML installed for $HOOK_PYTHON"
    else
      echo "install-hook-deps: could not install PyYAML for $HOOK_PYTHON non-invasively." >&2
      echo "  Debian/Ubuntu: sudo apt-get install -y python3-yaml" >&2
      echo "  Fedora/RHEL:   sudo dnf install -y python3-pyyaml" >&2
      echo "  macOS:         /usr/bin/python3 -m pip install --user pyyaml" >&2
      echo "  Rule 11 of guard-work-unit-write.sh cannot run until 'python3 -c \"import yaml\"' succeeds." >&2
      status=1
    fi
  fi
fi

exit "$status"
