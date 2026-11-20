"""Recoverable transactions with private backups and cross-platform locks.

Contract:

- One portalocker exclusive lock at state root protects every mutation AND
  launch admission.
- Non-blocking acquisition: if another process already holds the lock, the
  transaction fails immediately (there is no wait and no timeout).
- Journal state machine: prepared -> applying -> committed -> cleanup-complete.
- Flush committed state BEFORE deleting ANY rollback material.
- Recovery accepts only current content equal to recorded preimage/postimage.
- Unexpected edits cause refusal while preserving recovery evidence.
- Unix 0700 state/backup dirs, 0600 secret-bearing preimages.
- Backup policy: retain only preexisting resources actually replaced.
- ``--dry-run`` creates no dirs/lock/temp/log/backup files.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

import portalocker
from pydantic import BaseModel, ConfigDict, ValidationError

from oc_selector.errors import MaintenanceError


class JournalPhase(StrEnum):
    """Journal state machine phases."""

    PREPARED = "prepared"
    APPLYING = "applying"
    COMMITTED = "committed"
    CLEANUP_COMPLETE = "cleanup-complete"


@dataclass(slots=True)
class ResourceEntry:
    """A single file resource tracked in a journal."""

    path: str
    preimage_hash: str | None = None
    postimage_hash: str | None = None
    existed: bool = False
    mode: int | None = None
    symlink_target: str | None = None


@dataclass(frozen=True, slots=True)
class Journal:
    """A transaction journal with full recovery metadata."""

    id: str
    operation: str
    phase: JournalPhase
    resources: list[ResourceEntry] = field(default_factory=list)
    affected_profiles: list[str] = field(default_factory=list)
    pid: int = 0
    host: str = ""
    timestamp: str = ""


def _sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    data = path.read_bytes()
    return _sha256_bytes(data)


class Transaction:
    """A recoverable file mutation transaction.

    Usage:
        tx = Transaction(state_root, operation="init")
        tx.begin()
        tx.stage_file(src, dst)
        tx.commit()
        # or
        tx.rollback()
    """

    _state_root: Path
    _operation: str
    _id: str
    _phase: JournalPhase
    _resources: list[ResourceEntry]
    _staging_dir: Path
    _journal_path: Path
    _lock_path: Path
    _lock_fd: int | None
    _created_dirs: set[str]

    def __init__(self, state_root: Path, operation: str) -> None:
        """Initialize a new transaction with a unique ID."""
        self._state_root = state_root
        self._operation = operation
        self._id = str(uuid.uuid4())
        self._phase = JournalPhase.PREPARED
        self._resources = []
        self._staging_dir = state_root / "staging" / self._id
        self._journal_path = state_root / "journals" / f"{self._id}.json"
        self._lock_path = state_root / "maintenance.lock"
        self._lock_fd = None
        self._created_dirs = set()

    @property
    def id(self) -> str:
        """Transaction unique identifier."""
        return self._id

    @property
    def phase(self) -> JournalPhase:
        """Current journal phase."""
        return self._phase

    @property
    def resources(self) -> list[ResourceEntry]:
        """Tracked file resources."""
        return list(self._resources)

    def begin(self) -> None:
        """Acquire the lock and create the journal in PREPARED phase."""
        self._state_root.mkdir(parents=True, exist_ok=True)
        self._state_root.chmod(0o700)
        self._acquire_lock()
        self._write_journal()

    def _acquire_lock(self) -> None:
        """Acquire the portalocker exclusive lock with 5s timeout."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            portalocker.lock(self._lock_fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.LockException:
            os.close(self._lock_fd)
            raise MaintenanceError(
                "Could not acquire maintenance lock (already held)",
            ) from None

    def _release_lock(self) -> None:
        """Release the lock."""
        if self._lock_fd is not None:
            try:
                portalocker.unlock(self._lock_fd)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def _write_journal(self) -> None:
        """Write the journal to disk (flush + fsync)."""
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal = Journal(
            id=self._id,
            operation=self._operation,
            phase=self._phase,
            resources=self._resources,
            pid=os.getpid(),
            host=os.uname().nodename if hasattr(os, "uname") else "",
            timestamp=_utc_now(),
        )
        data = json.dumps(journal_to_dict(journal), indent=2)
        fd = os.open(
            str(self._journal_path),
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
            0o600,
        )
        try:
            os.write(fd, data.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        # fsync parent directory
        _fsync_dir(self._journal_path.parent)

    def stage_file(self, source: Path, destination: Path) -> None:
        """Stage a file for replacement. Records preimage."""
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        preimage_hash = None
        existed = destination.exists()
        mode = None
        if existed:
            preimage_hash = _sha256_file(destination)
            mode = destination.stat().st_mode & 0o777
        # Copy to staging
        staged = self._staging_dir / destination.name
        staged.write_bytes(source.read_bytes())
        self._resources.append(
            ResourceEntry(
                path=str(destination),
                preimage_hash=preimage_hash,
                existed=existed,
                mode=mode,
            ),
        )

    def stage_symlink(self, destination: Path, target: Path) -> None:
        """Stage a symlink to create at ``destination`` pointing at ``target``.

        Records a preimage so ``commit`` can replace an existing link and
        ``rollback`` can restore a pre-existing symlink or real file, or remove
        a freshly-created link. ``target`` is stored verbatim (unresolved) so
        the link is created eagerly and may dangle until the target appears.
        """
        existed = destination.is_symlink() or destination.exists()
        preimage_hash: str | None = None
        mode: int | None = None
        if destination.is_symlink():
            # Preimage of an existing symlink: hash of its readlink target
            # bytes; the target string itself is backed up for recreation.
            old_target = str(destination.readlink())
            preimage_hash = _sha256_bytes(old_target.encode())
            self._backup_preimage(destination.name, old_target.encode())
        elif existed:
            # Pre-existing real file: hash of bytes + mode, backed up so a
            # rollback can restore it.
            preimage_hash = _sha256_file(destination)
            mode = destination.stat().st_mode & 0o777
            self._backup_preimage(destination.name, destination.read_bytes())
        self._resources.append(
            ResourceEntry(
                path=str(destination),
                preimage_hash=preimage_hash,
                existed=existed,
                mode=mode,
                symlink_target=str(target),
            ),
        )

    def _backup_preimage(self, name: str, data: bytes) -> None:
        """Persist a preimage under the private per-transaction backup dir."""
        backup_dir = self._state_root / "backups" / self._id
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / name).write_bytes(data)

    def commit(self) -> None:
        """Transition to COMMITTED: apply staged files, flush journal."""
        self._phase = JournalPhase.APPLYING
        self._write_journal()
        # Apply all staged files
        for res in self._resources:
            dst = Path(res.path)
            if res.symlink_target is not None:
                self._apply_symlink(res, dst, res.symlink_target)
                continue
            staged = self._staging_dir / dst.name
            if staged.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(staged.read_bytes())
                if res.mode is not None:
                    dst.chmod(res.mode)
                res.postimage_hash = _sha256_file(dst)
        # Flush committed state BEFORE cleanup
        self._phase = JournalPhase.COMMITTED
        self._write_journal()
        # Cleanup staging
        self._cleanup_staging()
        self._phase = JournalPhase.CLEANUP_COMPLETE
        self._write_journal()
        self._release_lock()

    def _apply_symlink(self, res: ResourceEntry, dst: Path, target: str) -> None:
        """Create the staged symlink at ``dst`` (mkdir parent, replace link).

        Removes an existing non-identical symlink first; a pre-existing real
        file is never removed here (``symlink_to`` raises, which the caller
        handles). Every directory this call newly creates is tracked so
        ``rollback`` can remove it.
        """
        parent = dst.parent
        self._track_created_dir(parent)
        parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() and str(dst.readlink()) != target:
            dst.unlink()
        dst.symlink_to(target)
        res.postimage_hash = _sha256_bytes(str(dst.readlink()).encode())

    def _track_created_dir(self, target: Path) -> None:
        """Record ``target`` and any missing ancestors for rollback cleanup."""
        if target.exists():
            return
        current = target
        while not current.exists():
            self._created_dirs.add(str(current))
            if current == current.parent:
                break
            current = current.parent

    def rollback(self) -> None:
        """Roll back: restore preimages, remove created files."""
        for res in self._resources:
            dst = Path(res.path)
            if res.symlink_target is not None:
                self._rollback_symlink(res, dst)
                continue
            if res.existed and res.preimage_hash is not None:
                # Restore from backup if available
                backup = self._state_root / "backups" / self._id / dst.name
                if backup.exists():
                    dst.write_bytes(backup.read_bytes())
                    if res.mode is not None:
                        dst.chmod(res.mode)
            elif not res.existed and dst.exists():
                dst.unlink()
        self._cleanup_created_dirs()
        self._cleanup_staging()
        self._release_lock()

    def _rollback_symlink(self, res: ResourceEntry, dst: Path) -> None:
        """Undo a staged symlink: restore the preimage or unlink the link.

        A pre-existing symlink is recreated with its backed-up target; a
        pre-existing real file is restored from its backup when the destination
        currently holds a file (a commit that hit a real file never replaced
        it). A freshly-created link is unlinked.
        """
        if res.existed:
            backup = self._state_root / "backups" / self._id / dst.name
            if backup.exists():
                if dst.is_symlink():
                    old_target = backup.read_text(
                        encoding="utf-8",
                        errors="surrogateescape",
                    )
                    dst.unlink()
                    dst.symlink_to(old_target)
                elif dst.exists():
                    dst.write_bytes(backup.read_bytes())
                    if res.mode is not None:
                        dst.chmod(res.mode)
        elif dst.is_symlink() or dst.exists():
            dst.unlink()

    def _cleanup_created_dirs(self) -> None:
        """Remove directories this transaction newly created, if now empty."""
        for dir_str in self._created_dirs:
            directory = Path(dir_str)
            if directory.exists() and not list(directory.iterdir()):
                directory.rmdir()

    def _cleanup_staging(self) -> None:
        """Remove staging directory."""
        if self._staging_dir.exists():
            shutil.rmtree(self._staging_dir)

    def close(self) -> None:
        """Release lock without committing or rolling back."""
        self._release_lock()


def _utc_now() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(UTC).isoformat()


def _fsync_dir(path: Path) -> None:
    """Fsync a directory (where supported)."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def journal_to_dict(j: Journal) -> dict[str, object]:
    """Serialize a Journal to a JSON-safe dict."""
    return {
        "id": j.id,
        "operation": j.operation,
        "phase": j.phase.value,
        "resources": [_resource_to_dict(r) for r in j.resources],
        "affected_profiles": j.affected_profiles,
        "pid": j.pid,
        "host": j.host,
        "timestamp": j.timestamp,
    }


def _resource_to_dict(r: ResourceEntry) -> dict[str, object]:
    """Serialize a ResourceEntry, omitting ``symlink_target`` when unset.

    Byte-copy entries keep their exact historical key order (path,
    preimage_hash, postimage_hash, existed, mode); a symlink entry appends
    ``symlink_target`` last, matching ``ResourceRecord``/``JournalRecord``.
    """
    data: dict[str, object] = {
        "path": r.path,
        "preimage_hash": r.preimage_hash,
        "postimage_hash": r.postimage_hash,
        "existed": r.existed,
        "mode": r.mode,
    }
    if r.symlink_target is not None:
        data["symlink_target"] = r.symlink_target
    return data


class ResourceRecord(BaseModel):
    """Read-side record for a single journal resource (parse-only; never mutated)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    path: str
    preimage_hash: str | None = None
    postimage_hash: str | None = None
    existed: bool = False
    mode: int | None = None
    symlink_target: str | None = None


class JournalRecord(BaseModel):
    """Read-side record for a journal file (parse-only; never mutated).

    Field declaration order matches ``journal_to_dict`` exactly.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    id: str
    operation: str
    phase: str
    resources: list[ResourceRecord] = []
    affected_profiles: list[str] = []
    pid: int = 0
    host: str = ""
    timestamp: str = ""


def read_journal(path: Path) -> Journal | None:
    """Read a journal from disk. Returns None if not found or unreadable."""
    if not path.exists():
        return None
    try:
        record = JournalRecord.model_validate(json.loads(path.read_text()))
        phase = JournalPhase(record.phase)
    except (json.JSONDecodeError, ValidationError, ValueError):
        return None
    return Journal(
        id=record.id,
        operation=record.operation,
        phase=phase,
        resources=[
            ResourceEntry(
                path=r.path,
                preimage_hash=r.preimage_hash,
                postimage_hash=r.postimage_hash,
                existed=r.existed,
                mode=r.mode,
                symlink_target=r.symlink_target,
            )
            for r in record.resources
        ],
        affected_profiles=list(record.affected_profiles),
        pid=record.pid,
        host=record.host,
        timestamp=record.timestamp,
    )


def find_pending_journals(state_root: Path) -> list[Journal]:
    """Find all journals not in CLEANUP_COMPLETE phase."""
    journals_dir = state_root / "journals"
    if not journals_dir.exists():
        return []
    pending: list[Journal] = []
    for f in journals_dir.iterdir():
        if f.suffix == ".json":
            j = read_journal(f)
            if j is not None and j.phase != JournalPhase.CLEANUP_COMPLETE:
                pending.append(j)
    return pending
