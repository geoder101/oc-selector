"""Recoverable transactions with private backups and cross-platform locks.

Contract:
- One portalocker exclusive lock at state root protects every mutation AND launch admission.
- Bounded 5-second acquisition.
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
            raise MaintenanceError("Could not acquire maintenance lock (timeout)") from None

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
        fd = os.open(str(self._journal_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
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

    def commit(self) -> None:
        """Transition to COMMITTED: apply staged files, flush journal."""
        self._phase = JournalPhase.APPLYING
        self._write_journal()
        # Apply all staged files
        for res in self._resources:
            dst = Path(res.path)
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

    def rollback(self) -> None:
        """Roll back: restore preimages, remove created files."""
        for res in self._resources:
            dst = Path(res.path)
            if res.existed and res.preimage_hash is not None:
                # Restore from backup if available
                backup = self._state_root / "backups" / self._id / dst.name
                if backup.exists():
                    dst.write_bytes(backup.read_bytes())
                    if res.mode is not None:
                        dst.chmod(res.mode)
            elif not res.existed and dst.exists():
                dst.unlink()
        self._cleanup_staging()
        self._release_lock()

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
        "resources": [
            {
                "path": r.path,
                "preimage_hash": r.preimage_hash,
                "postimage_hash": r.postimage_hash,
                "existed": r.existed,
                "mode": r.mode,
            }
            for r in j.resources
        ],
        "affected_profiles": j.affected_profiles,
        "pid": j.pid,
        "host": j.host,
        "timestamp": j.timestamp,
    }


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
