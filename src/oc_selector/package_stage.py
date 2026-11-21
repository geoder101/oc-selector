"""Publish package-manager output through journaled file transactions.

Package managers run only in private workspaces. Existing installation trees
are replaced file-by-file so every published resource has a recoverable
preimage. Empty directories are harmless and are retained on removal.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from oc_selector.errors import MaintenanceError

if TYPE_CHECKING:
    from oc_selector.transactions import Transaction


def _files(root: Path) -> dict[Path, Path]:
    """Enumerate files and links without descending through directory links."""
    result: dict[Path, Path] = {}
    if not root.exists():
        return result
    for entry in root.iterdir():
        if entry.is_symlink() or entry.is_file():
            result[Path(entry.name)] = entry
        elif entry.is_dir():
            result.update(
                (Path(entry.name) / relative, path)
                for relative, path in _files(entry).items()
            )
    return result


def stage_tree(tx: Transaction, source: Path, destination: Path) -> None:
    """Stage new contents and removals of a package-manager-owned tree."""
    if destination.is_symlink():
        raise MaintenanceError(f"Installation tree is a symlink: {destination}")
    previous = _files(destination)
    incoming = _files(source)
    for relative in incoming:
        for parent in relative.parents:
            existing = previous.get(parent)
            if existing is not None and existing.is_symlink():
                raise MaintenanceError(
                    f"Installation crosses directory link: {existing}"
                )
    for relative in previous.keys() - incoming.keys():
        tx.stage_delete(previous[relative])
    for relative, path in incoming.items():
        target = destination / relative
        if path.is_symlink():
            tx.stage_symlink(target, path.readlink())
        else:
            tx.stage_file(path, target, mode=path.stat().st_mode & 0o777)
