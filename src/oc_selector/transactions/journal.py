"""Strict journal records and fail-closed discovery of pending recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from oc_selector.errors import TransactionError

if TYPE_CHECKING:
    from pathlib import Path


class JournalPhase(StrEnum):
    """Durable transaction state machine phases."""

    PREPARED = "prepared"
    APPLYING = "applying"
    COMMITTED = "committed"
    CLEANUP_COMPLETE = "cleanup-complete"


@dataclass(slots=True)
class ResourceEntry:
    """Preimage and intended postimage of one file, link or deletion."""

    path: str
    preimage_hash: str | None = None
    postimage_hash: str | None = None
    existed: bool = False
    mode: int | None = None
    symlink_target: str | None = None
    preimage_symlink: str | None = None
    delete: bool = False


@dataclass(frozen=True, slots=True)
class Journal:
    """Transaction journal with recovery metadata."""

    id: str
    operation: str
    phase: JournalPhase
    resources: list[ResourceEntry] = field(default_factory=list)
    affected_profiles: list[str] = field(default_factory=list)
    pid: int = 0
    host: str = ""
    timestamp: str = ""


def journal_to_dict(j: Journal) -> dict[str, object]:
    """Serialize a journal with stable historical key order."""
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
    """Keep byte-copy keys stable, appending optional resource metadata."""
    data: dict[str, object] = {
        "path": r.path,
        "preimage_hash": r.preimage_hash,
        "postimage_hash": r.postimage_hash,
        "existed": r.existed,
        "mode": r.mode,
    }
    if r.symlink_target is not None:
        data["symlink_target"] = r.symlink_target
    if r.preimage_symlink is not None:
        data["preimage_symlink"] = r.preimage_symlink
    if r.delete:
        data["delete"] = True
    return data


class ResourceRecord(BaseModel):
    """Parse-only resource record, rejecting unknown keys and coercion."""

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
    preimage_symlink: str | None = None
    delete: bool = False


class JournalRecord(BaseModel):
    """Parse-only journal record with the write-side declaration order."""

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
    """Parse a journal; absence or invalid content returns None."""
    try:
        record = JournalRecord.model_validate_json(path.read_bytes())
        phase = JournalPhase(record.phase)
    except FileNotFoundError, ValidationError, ValueError:
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
                preimage_symlink=r.preimage_symlink,
                delete=r.delete,
            )
            for r in record.resources
        ],
        affected_profiles=list(record.affected_profiles),
        pid=record.pid,
        host=record.host,
        timestamp=record.timestamp,
    )


def find_pending_journals(state_root: Path) -> list[Journal]:
    """List unresolved journals; corrupt evidence is an explicit recovery error."""
    pending: list[Journal] = []
    for path in sorted((state_root / "journals").glob("*.json")):
        journal = read_journal(path)
        if journal is None:
            raise TransactionError(f"Unreadable transaction journal: {path}")
        if journal.phase != JournalPhase.CLEANUP_COMPLETE:
            pending.append(journal)
    return pending
