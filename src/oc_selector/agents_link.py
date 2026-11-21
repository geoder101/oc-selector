"""Pure helpers for linking a profile's ``AGENTS.md`` to a global guard.

Contract:

- The link lives at ``<profile-config-dir>/AGENTS.md`` (see link_path).
- decide_link never creates, removes, or modifies anything (a pure decision).
- ensure_link performs the non-dry_run creation of an absolute, eager symlink.
- ensure_link never overwrites a pre-existing real file (it reports warn).
- dry_run performs no I/O whatsoever.

These helpers are intentionally side-effect-light: they are the standalone
primitive (T3). Transactional creation is the caller's concern (T1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

AGENTS_FILE = "AGENTS.md"


@dataclass(frozen=True, slots=True)
class LinkResult:
    """Outcome of a link decision or creation attempt.

    ``status`` is ``"ok"``, ``"dangling"``, or ``"warn"``. ``reason`` carries a
    human-readable explanation for non-``ok`` statuses, else ``None``.
    """

    status: str
    reason: str | None = None


def link_path(profile_config_dir: Path) -> Path:
    """Return the link path inside a profile config dir: ``<dir>/AGENTS.md``."""
    return profile_config_dir / AGENTS_FILE


def is_identical_symlink(link: Path, target: Path) -> bool:
    """Return ``True`` iff ``link`` is a symlink whose target equals ``target``.

    Normalize Windows extended-length path prefixes after resolving the target.
    Relative targets are interpreted from the link's parent, including dangling
    targets, rather than from the working directory.
    """
    return link.is_symlink() and _normalized_target(
        link.parent / link.readlink()
    ) == _normalized_target(target)


def _normalized_target(path: Path) -> str:
    """Compare resolved targets consistently, including Windows UNC paths."""
    resolved = str(path.resolve())
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved.removeprefix("\\\\?\\UNC\\")
        else:
            resolved = resolved.removeprefix("\\\\?\\")
    return os.path.normcase(resolved)


def decide_link(profile_config_dir: Path, target: Path) -> LinkResult:
    """Decide, without creating anything, what a link operation should do.

    Returns ``ok`` when an identical symlink already exists or when nothing
    blocks creation (a link should be made); ``dangling`` when the link path is
    a symlink pointing at a missing target; ``warn``/``pre-existing-real-file``
    when the link path is a real (non-symlink) file.
    """
    link = link_path(profile_config_dir)
    abs_target = target.resolve()
    if link.is_symlink():
        # A symlink to a missing target is dangling, never "ok" -- this check
        # precedes the identical-symlink test, which only compares the stored
        # target string and would otherwise report a dangling link as "ok".
        if not abs_target.exists():
            return LinkResult("dangling")
        return LinkResult("ok")
    if link.exists():
        return LinkResult("warn", "pre-existing-real-file")
    return LinkResult("ok")


def ensure_link(
    profile_config_dir: Path, target: Path, *, dry_run: bool = False
) -> LinkResult:
    """Create the absolute eager symlink, or report why it was not created.

    No-op (``ok``) when ``dry_run`` is set or an identical symlink already
    exists. Replaces a non-identical existing symlink. If the link path is a
    real (non-symlink) file, returns ``warn``/``pre-existing-real-file`` and
    leaves that file untouched.
    """
    if dry_run:
        return LinkResult("ok")
    link = link_path(profile_config_dir)
    abs_target = target.resolve()
    if is_identical_symlink(link, abs_target):
        return LinkResult("ok")
    decision = decide_link(profile_config_dir, abs_target)
    if decision.status == "warn":
        return decision
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(str(abs_target))
    return decide_link(profile_config_dir, abs_target)
