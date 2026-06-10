"""File-based per-round review token (ADR-29).

The H3 default-deny guard (``guard-verdict-format.sh``) requires a per-round,
unit-scoped token as the second factor for any canonical reviewer verdict. The
token's *transport* used to be the ``DEVBENCH_REVIEW_ROUND_TOKEN`` environment
variable carried via ``shell.env`` + ``BASH_ENV`` -- a mechanism that was never
implemented in code (each orchestrator run improvised it) and that twice failed
in production: a stale leftover token masked a missing fresh injection, and a
later run wrote the value into ``.claude/settings.local.json`` where the hook
never read it.

This module replaces that transport with a **file** under the workspace's
``.devbench/`` directory. The orchestrate skill calls ``devbench review-token
new <unit-id>`` before each review round (writing a fresh
``<unit-id>-r<n>-<rand>`` token) and ``devbench review-token clear`` after the
round; the guard reads the file directly. The mechanism is workspace-relative
and backlog-agnostic -- it works for any devbench workspace.

Design properties:

- **Deterministic + mid-session-safe:** the token is written by a CLI call at a
  known point, not re-sourced from a shell profile on every subprocess startup.
- **Unit-scoped:** the token begins with ``<unit-id>-`` so the guard can reject a
  token left over from a different unit's round (round-awareness).
- **Monotonic round counter:** per-unit round numbers persist in
  ``review-round-counters.json`` so each ``new`` call increments ``<n>``.
- **Fail-fast:** ``new`` rejects an empty unit id; callers that cannot resolve a
  workspace fail loudly rather than writing to an unexpected location.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

#: Token file name under ``<workspace>/.devbench/``. The guard reads this exact path.
TOKEN_FILENAME = "review-round-token"

#: Per-unit monotonic round-counter store under ``<workspace>/.devbench/``.
COUNTER_FILENAME = "review-round-counters.json"

#: Bytes of cryptographically secure randomness in the token suffix.
_RANDOM_SUFFIX_BYTES = 6


def _devbench_dir(workspace_root: Path) -> Path:
    """Return ``<workspace_root>/.devbench`` (not created)."""
    return workspace_root / ".devbench"


def token_path(workspace_root: Path) -> Path:
    """Return the absolute path of the review-round token file."""
    return _devbench_dir(workspace_root) / TOKEN_FILENAME


def _counter_path(workspace_root: Path) -> Path:
    """Return the absolute path of the per-unit round-counter store."""
    return _devbench_dir(workspace_root) / COUNTER_FILENAME


def _next_round(workspace_root: Path, unit_id: str) -> int:
    """Increment and persist the round counter for *unit_id*; return the new value.

    A corrupt or absent counter store is treated as "no rounds yet" -- the unit
    starts at round 1. This is the one tolerated recovery path (the counter is
    advisory metadata, not a correctness gate); it never silently masks a real
    failure because the token write itself still fails loudly on I/O errors.
    """
    path = _counter_path(workspace_root)
    counters: dict[str, int] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                counters = {str(k): int(v) for k, v in loaded.items()}
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            counters = {}
    nxt = counters.get(unit_id, 0) + 1
    counters[unit_id] = nxt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counters, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return nxt


def new_token(workspace_root: Path, unit_id: str) -> str:
    """Write a fresh unit-scoped round token to the token file and return it.

    Args:
        workspace_root: The devbench workspace root.
        unit_id: The work-unit id under review. Must be non-empty.

    Returns:
        The token string ``<unit-id>-r<n>-<rand>``.

    Raises:
        ValueError: *unit_id* is empty or whitespace-only.
    """
    if not unit_id or not unit_id.strip():
        raise ValueError("review-token new requires a non-empty unit id as the first argument")
    unit_id = unit_id.strip()
    round_n = _next_round(workspace_root, unit_id)
    token = f"{unit_id}-r{round_n}-{secrets.token_hex(_RANDOM_SUFFIX_BYTES)}"
    path = token_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    path.chmod(0o600)
    return token


def clear_token(workspace_root: Path) -> bool:
    """Remove the token file if present. Return ``True`` if a file was removed."""
    path = token_path(workspace_root)
    if path.is_file():
        path.unlink()
        return True
    return False


def read_token(workspace_root: Path) -> str | None:
    """Return the current token (stripped) or ``None`` when the file is absent/empty."""
    path = token_path(workspace_root)
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None
