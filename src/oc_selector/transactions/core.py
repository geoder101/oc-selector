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
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from oc_selector.errors import MaintenanceError, TransactionError
from oc_selector.transaction_io import (
    atomic_symlink,
    atomic_write,
    content_hash,
    ensure_directory,
    fsync_dir,
    resource_key,
)
from oc_selector.transactions.journal import (
    Journal,
    JournalPhase,
    ResourceEntry,
    find_pending_journals,
    journal_to_dict,
    read_journal,
)


def _sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    data = path.read_bytes()
    return _sha256_bytes(data)


def _matches_preimage(resource: ResourceEntry) -> bool:
    """Match identity as well as bytes, distinguishing links from regular files."""
    path = Path(resource.path)
    return (
        content_hash(path) == resource.preimage_hash
        and path.is_symlink() == (resource.preimage_symlink is not None)
        and (path.exists() or path.is_symlink()) == resource.existed
    )


def _matches_postimage(resource: ResourceEntry) -> bool:
    """Match the intended file, symlink, or absence after a deletion."""
    path = Path(resource.path)
    return (
        content_hash(path) == resource.postimage_hash
        and path.is_symlink()
        == (resource.symlink_target is not None and not resource.delete)
        and (path.exists() or path.is_symlink()) != resource.delete
    )


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
        ensure_directory(self._state_root)
        self._state_root.chmod(0o700)
        self._acquire_lock()
        if find_pending_journals(self._state_root):
            raise MaintenanceError("Pending recovery; run 'oc-selector init --recover'")
        self._write_journal()

    def _acquire_lock(self) -> None:
        """Acquire the portalocker exclusive lock without waiting."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            portalocker.lock(self._lock_fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.LockException:
            os.close(self._lock_fd)
            self._lock_fd = None
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
        atomic_write(self._journal_path, data.encode())

    def stage_file(
        self,
        source: Path,
        destination: Path,
        *,
        mode: int | None = None,
    ) -> None:
        """Stage a file for replacement. Records preimage."""
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        preimage_hash = None
        existed = destination.exists() or destination.is_symlink()
        preimage_mode = None
        if existed:
            preimage_hash = content_hash(destination)
            if not destination.is_symlink():
                preimage_mode = destination.stat().st_mode & 0o777
            self._backup_preimage(
                resource_key(destination),
                os.fsencode(destination.readlink())
                if destination.is_symlink()
                else destination.read_bytes(),
            )
        # Copy to staging
        staged = self._staging_dir / resource_key(destination)
        data = source.read_bytes()
        atomic_write(
            staged, data, mode if mode is not None else (preimage_mode or 0o600)
        )
        self._resources.append(
            ResourceEntry(
                path=str(destination),
                preimage_hash=preimage_hash,
                existed=existed,
                mode=preimage_mode,
                postimage_hash=_sha256_bytes(data),
                preimage_symlink=(
                    str(destination.readlink()) if destination.is_symlink() else None
                ),
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
            self._backup_preimage(resource_key(destination), os.fsencode(old_target))
        elif existed:
            # Pre-existing real file: hash of bytes + mode, backed up so a
            # rollback can restore it.
            preimage_hash = _sha256_file(destination)
            mode = destination.stat().st_mode & 0o777
            self._backup_preimage(resource_key(destination), destination.read_bytes())
        self._resources.append(
            ResourceEntry(
                path=str(destination),
                preimage_hash=preimage_hash,
                existed=existed,
                mode=mode,
                symlink_target=str(target),
                postimage_hash=_sha256_bytes(os.fsencode(target)),
                preimage_symlink=(
                    str(destination.readlink()) if destination.is_symlink() else None
                ),
            ),
        )

    def _backup_preimage(self, name: str, data: bytes) -> None:
        """Persist a preimage under the private per-transaction backup dir."""
        backup_dir = self._state_root / "backups" / self._id
        ensure_directory(backup_dir)
        backup_dir.chmod(0o700)
        atomic_write(backup_dir / name, data)

    def stage_delete(self, destination: Path) -> None:
        """Record an existing file or link for recoverable deletion."""
        if destination.is_symlink():
            self.stage_symlink(destination, destination.readlink())
        else:
            self.stage_file(destination, destination)
        resource = self._resources[-1]
        resource.delete = True
        resource.postimage_hash = None

    def commit(self) -> None:
        """Transition to COMMITTED: apply staged files, flush journal."""
        self._require_lock()
        for res in self._resources:
            if not _matches_preimage(res):
                raise TransactionError(
                    f"Unexpected edits at {res.path}; commit refused"
                )
        self._phase = JournalPhase.APPLYING
        self._write_journal()
        # Apply all staged files
        for res in self._resources:
            dst = Path(res.path)
            if res.delete:
                dst.unlink(missing_ok=True)
                fsync_dir(dst.parent)
                continue
            if res.symlink_target is not None:
                self._apply_symlink(res, dst, res.symlink_target)
                continue
            staged = self._staging_dir / resource_key(dst)
            atomic_write(dst, staged.read_bytes(), staged.stat().st_mode & 0o777)
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
        ensure_directory(parent)
        if dst.is_symlink():
            if str(dst.readlink()) == target:
                return
        elif dst.exists():
            raise FileExistsError(str(dst))
        atomic_symlink(dst, target)
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
        if self._lock_fd is None:
            if not self._resources:
                return
            self._acquire_lock()
        if self._resources:
            self._phase = JournalPhase.APPLYING
            self._write_journal()
        self._restore_resources()
        self._cleanup_created_dirs()
        self._cleanup_staging()
        self._phase = JournalPhase.CLEANUP_COMPLETE
        self._write_journal()
        self._release_lock()

    def _restore_resources(self) -> None:
        """Validate every resource before restoring any preimage."""
        for res in self._resources:
            dst = Path(res.path)
            if res.existed:
                backup = self._state_root / "backups" / self._id / resource_key(dst)
                if not backup.exists() or _sha256_file(backup) != res.preimage_hash:
                    raise TransactionError(f"Missing or invalid backup for {dst}")
            if not (_matches_preimage(res) or _matches_postimage(res)):
                raise TransactionError(f"Unexpected edits at {dst}; recovery refused")
        for res in reversed(self._resources):
            dst = Path(res.path)
            if _matches_preimage(res):
                continue
            if not res.existed:
                dst.unlink()
            elif res.preimage_symlink is not None:
                atomic_symlink(dst, res.preimage_symlink)
            else:
                backup = self._state_root / "backups" / self._id / resource_key(dst)
                atomic_write(
                    dst,
                    backup.read_bytes(),
                    res.mode if res.mode is not None else 0o600,
                )
            fsync_dir(dst.parent)

    @classmethod
    def recover(cls, state_root: Path, journal_path: Path) -> None:
        """Recover under the maintenance lock, preserving evidence on refusal."""
        tx = cls(state_root, operation="recovery")
        try:
            tx._acquire_lock()
            journal = read_journal(journal_path)
            if journal is None or journal_path.stem != journal.id:
                raise TransactionError(f"Cannot read journal {journal_path}")
            tx._id = journal.id
            tx._journal_path = journal_path
            tx._staging_dir = state_root / "staging" / journal.id
            tx._operation = journal.operation
            tx._resources = journal.resources
            if journal.phase == JournalPhase.CLEANUP_COMPLETE:
                return
            if journal.phase == JournalPhase.COMMITTED:
                for resource in journal.resources:
                    if not _matches_postimage(resource):
                        raise TransactionError(
                            f"Unexpected edits at {resource.path}; cleanup refused",
                        )
            else:
                tx._restore_resources()
            tx._cleanup_staging()
            tx._phase = JournalPhase.CLEANUP_COMPLETE
            tx._write_journal()
        finally:
            tx.close()

    def _cleanup_created_dirs(self) -> None:
        """Remove directories this transaction newly created, if now empty."""
        for dir_str in sorted(
            self._created_dirs, key=lambda p: len(Path(p).parts), reverse=True
        ):
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

    def _require_lock(self) -> None:
        """Reject application outside an active maintenance transaction."""
        if self._lock_fd is None:
            raise TransactionError("Transaction has no maintenance lock")


def _utc_now() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(UTC).isoformat()
