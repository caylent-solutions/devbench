"""Compares the harness devbench install against the target checkout it edits (issue #301).

devbench self-hosts: the orchestrator executes from one checkout (the
harness install) while landing commits into another checkout of the same
repository (a configured target repo). Nothing compared the two before this
module existed, so the harness could run arbitrarily stale orchestrator code
indefinitely with no signal to the operator
(spec/harness-target-install-parity.md Section 1).

This module implements FR-1 (an install-identity resolver) and FR-2
(self-hosting detection) only. The ``devbench start`` pre-flight gate (FR-3)
and the ``report`` / ``status`` parity row (FR-4) are separate consumers
built on top of :func:`resolve_install_parity`.

Design constraints carried over from spec Section 3 ("existing primitives to
reuse -- do not reinvent"):

- The target checkout path is read from ``RepoConfig.resolved_checkout_path``
  (populated by ``config_loader.load_runtime_config``) and is never
  re-resolved inline (E213).
- Git is invoked exclusively through ``devbench.utils.process.run_command``,
  the shared subprocess helper every other git-touching module in this
  package already uses (``git_ops.py``, ``tdd_gate.py``, ``cli.py``). No
  second subprocess wrapper is introduced.
- No network call is made anywhere in this module: identities are read from
  whatever git objects and refs already exist locally. A stale local clone
  is exactly the condition this module exists to detect, not to paper over
  with a fetch.

Error handling (spec D-3): a checkout that cannot be introspected (not a
git repository, or an unreadable HEAD revision) raises :class:`InstallParityError`
naming the path. The resolver never guesses and never returns a default
"in sync" result. A checkout with no resolvable ``origin`` remote is
different: that is not an error (a workspace may legitimately contain a
checkout without a remote), so it is simply treated as "does not match" for
self-hosting purposes (FR-2, AC-5).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from devbench.constants import ORCHESTRATOR_SOURCE_PREFIX
from devbench.utils.process import run_command

logger = logging.getLogger(__name__)


class _RepoConfigLike(Protocol):
    """Structural shape this module reads off a per-repo config entry.

    Matches ``devbench.config_loader.RepoConfig`` field-for-field without
    importing that concrete dataclass, so this module depends on the
    abstraction it actually uses (DIP) rather than the full config-loader
    surface. Declared as a read-only property (not a plain attribute) so
    mypy's protocol matching is covariant: a concrete ``RepoConfig`` whose
    ``resolved_checkout_path`` is exactly ``Path | None`` satisfies this
    without requiring an invariant field-type match.
    """

    @property
    def resolved_checkout_path(self) -> Path | None: ...


class _RuntimeConfigLike(Protocol):
    """Structural shape this module reads off the loaded runtime config.

    Matches ``devbench.config_loader.RuntimeConfig`` field-for-field without
    importing that concrete dataclass (see :class:`_RepoConfigLike`).
    """

    @property
    def repos(self) -> Mapping[str, _RepoConfigLike]: ...


#: What ``git rev-parse --abbrev-ref HEAD`` reports (not an error) when the
#: checkout is on a detached HEAD (spec FR-1, AC-1).
_DETACHED_HEAD_MARKER: str = "HEAD"

#: Matches scp-like remote syntax (``[user@]host:path``), as distinct from a
#: URL carrying an explicit ``scheme://`` prefix. Used by
#: :func:`_canonical_origin` to extract the host/path pair from either form
#: so the two can be compared (spec FR-2, AC-4).
_SCP_LIKE_ORIGIN_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


@dataclass(frozen=True)
class InstallIdentity:
    """Git identity of a single devbench install (harness or target).

    Attributes:
        path: Absolute filesystem path to the checkout root.
        revision: Full ``git rev-parse HEAD`` SHA of the checked-out commit.
        branch: Current branch name, or ``None`` on a detached HEAD.
        origin_url: The checkout's ``origin`` remote URL, or ``None`` when
            no ``origin`` remote is configured (not an error -- FR-2, AC-5).
    """

    path: Path
    revision: str
    branch: str | None
    origin_url: str | None


@dataclass(frozen=True)
class ParityResult:
    """Outcome of comparing the harness install against a self-hosted target.

    Attributes:
        self_hosting: ``True`` when a configured repo's origin canonically
            matches the harness install's own origin (FR-2).
        harness: The harness install's identity. Always resolved -- a
            resolver failure raises rather than leaving this ``None``.
        target: The matching configured repo's identity, or ``None`` when
            ``self_hosting`` is ``False``.
        behind_count: Number of commits reachable from ``target`` and not
            from ``harness`` that touch
            :data:`devbench.constants.ORCHESTRATOR_SOURCE_PREFIX`. Always
            ``0`` when ``self_hosting`` is ``False`` (spec FR-2).
        in_sync: ``True`` when ``behind_count`` is ``0``.
    """

    self_hosting: bool
    harness: InstallIdentity | None
    target: InstallIdentity | None
    behind_count: int
    in_sync: bool


class InstallParityError(RuntimeError):
    """Raised when a checkout cannot be introspected for install identity.

    Covers a path that is not a git checkout, an unreadable HEAD revision,
    or any other failing git invocation required by FR-1. Never swallowed
    and never substituted with a default "in sync" result (spec D-3).
    """


def _run_git_or_raise(args: list[str], cwd: Path) -> str:
    """Run ``git <args>`` in *cwd* and return stripped stdout.

    Raises:
        InstallParityError: Naming *cwd* and the underlying git error when
            the command exits non-zero.
    """
    exit_code, stdout, stderr = run_command(["git", *args], cwd=cwd)
    if exit_code != 0:
        raise InstallParityError(
            "\n".join(
                [
                    f"ERROR: git {' '.join(args)} failed for checkout '{cwd}' (exit {exit_code}).",
                    f"  {stderr.strip() or stdout.strip() or 'no output from git'}",
                    "  Confirm the path is a valid git checkout with a readable HEAD revision.",
                ]
            )
        )
    return stdout.strip()


def _resolve_origin_url(path: Path) -> str | None:
    """Return the ``origin`` remote URL for the checkout at *path*, or ``None``.

    A missing ``origin`` remote is not an error (spec AC-5): a workspace may
    legitimately contain a checkout with no remote configured. Shared by
    :func:`resolve_install_identity` and :func:`_resolve_matching_target` so
    the "how do we read an origin URL" concern lives in exactly one place.
    """
    exit_code, stdout, _ = run_command(["git", "remote", "get-url", "origin"], cwd=path)
    return stdout.strip() if exit_code == 0 and stdout.strip() else None


def resolve_install_identity(path: Path) -> InstallIdentity:
    """Resolve :class:`InstallIdentity` for the git checkout rooted at *path*.

    Args:
        path: Absolute filesystem path to the checkout root.

    Returns:
        The checkout's path, full revision, branch (``None`` on detached
        HEAD), and origin URL (``None`` when no ``origin`` remote exists).

    Raises:
        InstallParityError: When *path* is not a git checkout or its HEAD
            revision cannot be read. A missing ``origin`` remote is NOT an
            error (spec AC-5) -- ``origin_url`` is simply ``None``.
    """
    revision = _run_git_or_raise(["rev-parse", "HEAD"], path)
    abbrev_ref = _run_git_or_raise(["rev-parse", "--abbrev-ref", "HEAD"], path)
    branch = None if abbrev_ref == _DETACHED_HEAD_MARKER else abbrev_ref
    origin_url = _resolve_origin_url(path)
    return InstallIdentity(path=path, revision=revision, branch=branch, origin_url=origin_url)


def _canonical_origin(url: str) -> str:
    """Return a scheme/credential/host-case/``.git``-suffix-insensitive form of *url*.

    Two origin URLs canonicalize equal when they differ only by scheme
    (``https://`` vs. the ``git@host:path`` scp-like form), embedded
    credentials (``user:pass@`` or ``user@``), a trailing ``.git``, or host
    letter case (spec FR-2, AC-4).
    """
    candidate = url.strip()
    scp_match = None if "://" in candidate else _SCP_LIKE_ORIGIN_RE.match(candidate)
    if scp_match is not None:
        host = scp_match.group("host")
        path = scp_match.group("path")
    else:
        parsed = urlsplit(candidate)
        host = parsed.hostname or ""
        path = parsed.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return f"{host.lower()}/{path}"


def _harness_root() -> Path:
    """Return the harness install's own checkout root.

    Resolved from this module's own file location -- three parents up from
    ``src/devbench/install_parity.py`` -- per spec FR-1: "resolves the
    harness install root from the running package's own location."
    """
    return Path(__file__).resolve().parent.parent.parent


def _resolve_matching_target(harness: InstallIdentity, runtime_config: _RuntimeConfigLike) -> InstallIdentity | None:
    """Return the configured repo's identity whose origin matches *harness*, or ``None``.

    Iterates ``runtime_config.repos`` in declaration order and returns the
    identity of the first entry whose resolved checkout has an ``origin``
    remote that canonically matches the harness's own origin (FR-2). Skips
    entries with no resolved checkout path and entries whose checkout has no
    readable origin -- neither case is an error (spec AC-5); the reason is
    logged at debug.
    """
    if harness.origin_url is None:
        logger.debug("install_parity: harness at '%s' has no resolvable origin; not self-hosting", harness.path)
        return None
    harness_canonical = _canonical_origin(harness.origin_url)
    for repo_name, repo_config in runtime_config.repos.items():
        checkout_path = repo_config.resolved_checkout_path
        if checkout_path is None:
            continue
        candidate_origin = _resolve_origin_url(checkout_path)
        if candidate_origin is None:
            logger.debug(
                "install_parity: repo '%s' at '%s' has no resolvable origin; not a self-hosting match",
                repo_name,
                checkout_path,
            )
            continue
        if _canonical_origin(candidate_origin) == harness_canonical:
            return resolve_install_identity(checkout_path)
    return None


def resolve_install_parity(runtime_config: _RuntimeConfigLike) -> ParityResult:
    """Compare the harness install against a self-hosted target checkout (FR-1, FR-2).

    Resolves the harness install's identity from its own package location,
    then checks every repo in ``runtime_config.repos`` for one whose
    ``origin`` canonically matches the harness's origin. When a match is
    found, ``self_hosting`` is ``True`` and ``behind_count`` is computed as
    the number of commits reachable from the target and not the harness
    that touch :data:`devbench.constants.ORCHESTRATOR_SOURCE_PREFIX` (spec
    D-2).

    Args:
        runtime_config: Loaded runtime configuration; only ``repos`` is
            consulted.

    Returns:
        A :class:`ParityResult`. ``target`` is ``None`` and
        ``behind_count``/``in_sync`` take their "not self-hosting" defaults
        (``0`` / ``True``) when no configured repo matches.

    Raises:
        InstallParityError: When the harness install itself cannot be
            introspected, or when a matched target's identity or the
            ``git rev-list`` behind-count query fails. The resolver never
            substitutes a default "in sync" result for a resolver failure
            (spec D-3).
    """
    harness = resolve_install_identity(_harness_root())
    target = _resolve_matching_target(harness, runtime_config)
    self_hosting = target is not None

    behind_count = 0
    if self_hosting and target is not None:
        count_output = _run_git_or_raise(
            ["rev-list", "--count", f"{harness.revision}..{target.revision}", "--", ORCHESTRATOR_SOURCE_PREFIX],
            target.path,
        )
        behind_count = int(count_output)

    return ParityResult(
        self_hosting=self_hosting,
        harness=harness,
        target=target,
        behind_count=behind_count,
        in_sync=behind_count == 0,
    )
