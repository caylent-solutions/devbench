#!/usr/bin/env bash
# guard-work-unit-write.sh -- PreToolUse hook: block Write/Edit to work unit .md files.
#
# Receives JSON on stdin with structure:
#   { "tool_name": "Write"|"Edit", "tool_input": { "file_path": "...", "content": "..." } }
#
# Blocks writes to files matching backlog/**/*.md (work unit files).
# Allows:
#   - Files outside backlog/
#   - BACKLOG.md (top-level tracking index, managed separately)
#   - backlog/config/AGENT-INSTRUCTIONS.md and other non-.md files in backlog/
#   - Non-.md files anywhere under backlog/
#
# For Write/Edit calls targeting backlog/**/*.md, also validates content:
#   Rule 10: rejects content containing an em-dash (U+2014).
#   Rule 11: rejects content whose Changes Manifest table rows or Source: lines
#            are prefixed with a known checkout_directory from devbench.yaml.
#
# Work unit .md files are managed exclusively by the orchestrate skill.
# Executor agents must not modify them directly.
#
# Exit 0  -> operation is allowed (Claude proceeds)
# Exit 2  -> operation is blocked (stderr becomes Claude's feedback)

set -euo pipefail

# Prefer system /usr/bin python3 over asdf shims. asdf shims fail with exit 126
# when the caller's cwd does not declare a `python` entry in `.tool-versions`,
# which is true for every test fixture using a tmp_path workspace and for any
# operator running this hook from a directory without an asdf python pin. The
# old behaviour silently swallowed the asdf exit, leaving CONTENT empty and
# bypassing rules 10 + 11 -- a real-bug source. Fix: prepend /usr/bin so the
# system python3 (always present on Linux dev containers / CI) wins resolution.
PATH="/usr/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_hook_lib.sh
. "$SCRIPT_DIR/_hook_lib.sh"

INPUT=$(cat)
FILE_PATH=$(extract_file_path "$INPUT")
decode_json_escapes FILE_PATH

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Allow BACKLOG.md at any level -- this is the top-level tracking index,
# not a work unit file. Only backlog/**/*.md files are work units.
if [[ "$FILE_PATH" == "BACKLOG.md" ]] || [[ "$FILE_PATH" == */BACKLOG.md ]]; then
  exit 0
fi

# Allow writes to files inside backlog/config/ -- these are configuration artifacts,
# not work unit files. Config files are managed by the workspace setup.
if [[ "$FILE_PATH" == */backlog/config/* ]] || [[ "$FILE_PATH" == backlog/config/* ]]; then
  exit 0
fi

# Block writes to .md files under backlog/ -- these are work unit files.
# Work unit files are managed exclusively by the orchestrate skill.
if [[ "$FILE_PATH" == */backlog/*.md ]] || [[ "$FILE_PATH" == backlog/*.md ]]; then
  # Extract the proposed content from the tool input for content validation.
  CONTENT=$(printf '%s' "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('content', ''))
" 2>/dev/null || true)

  # Rule 10: reject content containing an em-dash (U+2014).
  if [[ -n "$CONTENT" ]]; then
    EM_DASH_LINE=$(printf '%s' "$CONTENT" | grep -n $'—' | head -n 1 || true)
    if [[ -n "$EM_DASH_LINE" ]]; then
      LINE_NUM="${EM_DASH_LINE%%:*}"
      echo "guard-work-unit-write: rule 10: em-dash detected at line ${LINE_NUM} in proposed content for ${FILE_PATH}" >&2
      echo "Fix: replace em-dash (U+2014) with '--' (double hyphen) -- validate-backlog rule 10 rejects em-dashes in work-unit files." >&2
      exit 2
    fi
  fi

  # Rule 11: reject content whose Manifest table rows or Source: lines are prefixed
  # with a known checkout_directory from devbench.yaml.
  if [[ -n "$CONTENT" ]] && [[ -n "${DEVBENCH_WORKSPACE_ROOT:-}" ]]; then
    YAML_PATH="${DEVBENCH_WORKSPACE_ROOT}/backlog/config/devbench.yaml"
    if [[ -f "$YAML_PATH" ]]; then
      CHECKOUT_DIRS=$(python3 - "$YAML_PATH" <<'PYEOF'
import sys, yaml
try:
    with open(sys.argv[1]) as f:
        cfg = yaml.safe_load(f) or {}
    repos = cfg.get('repos') or {}
    dirs = []
    for repo_data in repos.values():
        if isinstance(repo_data, dict):
            cd = repo_data.get('checkout_directory')
            if cd:
                dirs.append(cd)
    print('\n'.join(dirs))
except Exception:
    pass
PYEOF
      )
      for CHECKOUT_DIR in $CHECKOUT_DIRS; do
        # Match Manifest table rows (| `checkout_dir/...` |) or Source: lines.
        RULE11_LINE=$(printf '%s' "$CONTENT" | grep -n -E "^[[:space:]]*\|[[:space:]]*\`?${CHECKOUT_DIR}/" | head -n 1 || true)
        if [[ -z "$RULE11_LINE" ]]; then
          RULE11_LINE=$(printf '%s' "$CONTENT" | grep -n -E "Source:.*${CHECKOUT_DIR}/" | head -n 1 || true)
        fi
        if [[ -n "$RULE11_LINE" ]]; then
          LINE_NUM="${RULE11_LINE%%:*}"
          echo "guard-work-unit-write: rule 11: checkout_directory prefix '${CHECKOUT_DIR}/' on line ${LINE_NUM} in proposed content for ${FILE_PATH}" >&2
          echo "Fix: paths in the Changes Manifest must be repo-relative -- remove the '${CHECKOUT_DIR}/' prefix. validate-backlog rule 11 rejects checkout_directory-prefixed paths." >&2
          exit 2
        fi
      done
    fi
  fi

  # Issue #160: orchestrator-tier callers (DEVBENCH_AGENT_ROLE=orchestrator)
  # are allowed to perform corrective edits on work-unit .md files (the
  # rule-10 + rule-11 content checks above already fired and passed; this
  # final block-or-allow gate is what differentiates the two tiers).
  # Executor-tier callers (DEVBENCH_AGENT_ROLE=executor) and missing-role
  # callers default to BLOCK so the hook's original safety guarantee --
  # executors must not modify work-unit files directly -- holds.
  CALLER_ROLE=$(_resolve_caller_role)
  if [[ "$CALLER_ROLE" == "orchestrator" ]]; then
    # ALLOW: content rules above already passed; orchestrator may proceed.
    exit 0
  fi

  echo "guard-work-unit-write: blocked write to work unit file: ${FILE_PATH}" >&2
  echo "Fix: work unit .md files under backlog/ are managed exclusively by the orchestrate skill." >&2
  echo "Executors must not modify work unit files directly." >&2
  echo "(Issue #160: set DEVBENCH_AGENT_ROLE=orchestrator in the calling env to bypass for orchestrator-tier corrective edits.)" >&2
  exit 2
fi

exit 0
