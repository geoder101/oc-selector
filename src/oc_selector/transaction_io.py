"""Durable private writes and resource identities for transaction recovery."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path


def fsync_dir(path: Path) -> None:
    """Flush directory entries where directory descriptors are supported."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_directory(path: Path) -> None:
    """Create private directories, flushing each new entry in its parent."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
        fsync_dir(directory.parent)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Flush a private sibling before atomically replacing the destination."""
    ensure_directory(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            temporary.chmod(mode)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_symlink(path: Path, target: str) -> None:
    """Replace a link atomically, retaining the old identity until publication."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}")
    try:
        temporary.symlink_to(target)
        temporary.replace(path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def resource_key(path: Path) -> str:
    """Return a collision-free storage key, including the full resource path."""
    return hashlib.sha256(str(path.absolute()).encode()).hexdigest()


def content_hash(path: Path) -> str | None:
    """Hash link identity rather than its referent, or return None for absence."""
    if path.is_symlink():
        data = os.fsencode(path.readlink())
    elif path.exists():
        data = path.read_bytes()
    else:
        return None
    return hashlib.sha256(data).hexdigest()
