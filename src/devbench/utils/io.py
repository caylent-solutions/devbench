"""Shared filesystem-IO utilities.

Currently exports the single public helper ``atomic_write_text`` which
guarantees a concurrent reader of *path* observes either the prior complete
file or the new complete file -- never a partial-write state. Used by every
work-unit ``.md`` writer in ``devbench.backlog.manager`` /
``devbench.backlog.proposal`` / ``devbench.backlog.amendment`` (and by the
``BACKLOG.md`` index writer) so that ``devbench report`` /
``devbench validate-backlog`` / external readers cannot trip the parser's
"No top-level heading found" check by reading mid-write.
"""

from __future__ import annotations

from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp-then-rename.

    Writes to ``<path>.tmp`` first, then ``Path.replace``-renames over the
    target. ``Path.replace`` is atomic on POSIX even when the destination
    already exists; the rename only flips the directory entry once the
    temp file is fully written + closed, so a concurrent reader of *path*
    sees either the old complete content or the new complete content,
    never partial.

    Same-filesystem requirement: ``<path>.tmp`` shares *path*'s parent
    directory so the rename does not cross filesystems.

    Args:
        path: Destination path. Parent directory MUST exist (the caller --
            typically a devbench CLI command -- already validated the
            workspace layout). Failing fast here surfaces a structural
            problem rather than masking it with a silent ``mkdir``.
        content: Text to write. UTF-8 encoded.

    Raises:
        FileNotFoundError: If *path*'s parent directory does not exist.
        OSError: For any other IO error (disk full, permission, etc.) --
            propagated unchanged for fail-fast diagnostics.
    """
    if not path.parent.is_dir():
        raise FileNotFoundError(f"Cannot atomic-write to '{path}': parent directory does not exist.")
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
