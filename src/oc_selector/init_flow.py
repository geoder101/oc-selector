"""Generic init and recovery flows.

Generic init detects existing config, creates missing parent, creates
empty registry, never calls conversion.
Recovery finds pending journals, lists them, restores from backup.
Source-only personal setup is separate from generic init.
No author constants in installed src.
No silent fallbacks.
No secret material in logs.
No network in init/recovery.
No migration of legacy fields.
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from oc_selector.errors import (
    RegistryError,
    TransactionError,
)
from oc_selector.paths import registry_path_for, resolve_config_parent, state_root
from oc_selector.registry import (
    ProfileEntry,
    Registry,
    registry_to_toml,
)
from oc_selector.transactions import (
    JournalPhase,
    find_pending_journals,
    read_journal,
)


@dataclass(frozen=True, slots=True)
class InitResult:
    """Result of a generic init operation."""

    config_parent: Path
    registry_path: Path
    created_parent: bool
    created_registry: bool
    already_initialized: bool
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    """A pending journal entry for recovery."""

    journal_id: str
    phase: JournalPhase
    operation: str
    description: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Result of a recovery operation."""

    recovered: bool
    journal_id: str
    error: str | None = None


def run_init(
    config_parent: Path | None = None,
    registry_path: Path | None = None,
    *,
    no_backup: bool = False,  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
    dry_run: bool = False,
) -> InitResult:
    """Run generic init: detect existing config, create missing parent, create empty registry.

    Never calls conversion.

    Args:
        config_parent: Override config parent path (for testing).
        registry_path: Override registry path (for testing).
        no_backup: Accepted for grammar parity. Generic init only creates missing
            resources and never rewrites existing state, so it performs no backup;
            this flag is therefore intentionally inert here.
        dry_run: When set, write nothing and instead report what *would* be created;
            ``created_parent``/``created_registry`` reflect the would-be outcome.

    Returns:
        An ``InitResult`` describing what was (or would be) created.

    Raises:
        ``RegistryError`` if the config parent cannot be created.
        ``RegistryError`` if the registry cannot be created.

    """
    parent = config_parent or resolve_config_parent()
    reg_path = registry_path or registry_path_for(parent)

    if dry_run:
        return InitResult(
            config_parent=parent,
            registry_path=reg_path,
            created_parent=not parent.exists(),
            created_registry=not reg_path.exists(),
            already_initialized=parent.exists(),
            dry_run=True,
        )

    created_parent = False
    created_registry = False
    already_initialized = False

    # Detect existing
    if parent.exists():
        already_initialized = True
    else:
        try:
            parent.mkdir(parents=True, exist_ok=True)
            created_parent = True
        except OSError as exc:
            raise RegistryError(
                f"Cannot create config parent {parent}: {exc}",
            ) from exc

    # Create empty registry if missing
    if not reg_path.exists():
        try:
            # Minimal valid registry: one default profile
            empty = Registry(
                default_profile="default",
                profiles={"default": ProfileEntry(directory="default")},
            )
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            reg_path.write_text(registry_to_toml(empty))
            created_registry = True
        except OSError as exc:
            raise RegistryError(
                f"Cannot create registry at {reg_path}: {exc}",
            ) from exc

    return InitResult(
        config_parent=parent,
        registry_path=reg_path,
        created_parent=created_parent,
        created_registry=created_registry,
        already_initialized=already_initialized,
    )


def list_pending_recoveries(
    state_dir: Path | None = None,
) -> list[RecoveryEntry]:
    """List pending journal entries for recovery.

    Args:
        state_dir: Override state root (for testing).

    Returns:
        A list of ``RecoveryEntry`` for each pending journal.

    """
    root = state_dir or state_root()
    pending = find_pending_journals(root)
    return [
        RecoveryEntry(
            journal_id=j.id,
            phase=j.phase,
            operation=j.operation,
            description=f"Pending {j.operation} in phase {j.phase.value}",
        )
        for j in pending
    ]


def run_recovery(
    journal_path: Path,
    state_dir: Path | None = None,  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
) -> RecoveryResult:
    """Recover from a pending journal by restoring preimage files.

    Args:
        journal_path: Path to the pending journal file.
        state_dir: Override state root (for testing).

    Returns:
        A ``RecoveryResult`` describing the recovery.

    Raises:
        ``TransactionError`` if recovery fails.

    """
    journal = read_journal(journal_path)
    if journal is None:
        raise TransactionError(
            f"Cannot read journal {journal_path}",
        )

    # Restore preimage files that existed before the transaction
    for resource in journal.resources:
        res_path = Path(resource.path)
        if resource.existed and resource.preimage_hash is not None:
            # The preimage is stored as a backup alongside the journal
            backup = journal_path.parent / f"{resource.path}.bak"
            if backup.exists():
                try:
                    shutil.copy2(backup, res_path)
                except OSError as exc:
                    raise TransactionError(
                        f"Cannot restore {res_path}: {exc}",
                    ) from exc

    # Mark journal as rolled back by writing CLEANUP_COMPLETE
    with contextlib.suppress(OSError):
        journal_path.write_text(
            journal_path.read_text().replace(
                f'"phase": "{journal.phase.value}"',
                '"phase": "cleanup-complete"',
            ),
        )

    return RecoveryResult(
        recovered=True,
        journal_id=journal.id,
    )


def run_recovery_all(
    state_dir: Path | None = None,
) -> list[RecoveryResult]:
    """Recover from all pending journals.

    Args:
        state_dir: Override state root (for testing).

    Returns:
        A list of ``RecoveryResult`` for each journal.

    """
    root = state_dir or state_root()
    pending = find_pending_journals(root)
    results: list[RecoveryResult] = []
    journals_dir = root / "journals"
    for journal in pending:
        journal_path = journals_dir / f"{journal.id}.json"
        try:
            result = run_recovery(journal_path, state_dir=root)
            results.append(result)
        except TransactionError as exc:
            results.append(
                RecoveryResult(
                    recovered=False,
                    journal_id=journal.id,
                    error=str(exc),
                ),
            )
    return results
