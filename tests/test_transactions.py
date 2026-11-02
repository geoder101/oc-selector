"""TDD tests for recoverable transactions, private backups and locks.

Covers:
- Lock acquisition and release
- Journal state machine transitions
- File staging and commit
- Rollback restores preimages
- Recovery distinguishes rollback from committed cleanup
- Private modes (0700/0600)
- Pending journal detection
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

import pytest
from pydantic import TypeAdapter

from oc_selector.errors import MaintenanceError
from oc_selector.transactions import (
    Journal,
    JournalPhase,
    ResourceEntry,
    Transaction,
    find_pending_journals,
    journal_to_dict,
    read_journal,
)

_RESOURCES_ADAPTER: TypeAdapter[list[dict[str, object]]] = TypeAdapter(
    list[dict[str, object]],
)


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    """Provide an isolated state root directory."""
    return tmp_path / "oc-selector"


class TestTransactionLifecycle:
    def test_happy_begin_creates_journal(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="init")
        tx.begin()
        assert tx.phase == JournalPhase.PREPARED
        assert tx.id != ""
        # Journal file exists
        journal_path = state_root / "journals" / f"{tx.id}.json"
        assert journal_path.exists()
        tx.close()

    def test_happy_commit_transitions_phases(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        # Stage a file
        src = tmp_path / "source.txt"
        src.write_text("hello")
        dst = tmp_path / "dest" / "target.txt"
        tx.stage_file(src, dst)
        tx.commit()
        assert tx.phase == JournalPhase.CLEANUP_COMPLETE
        # File was written
        assert dst.exists()
        assert dst.read_text() == "hello"

    def test_happy_rollback_restores(self, state_root: Path, tmp_path: Path) -> None:
        # Create initial file
        dst = tmp_path / "existing.txt"
        dst.write_text("original")
        tx = Transaction(state_root, operation="test")
        tx.begin()
        src = tmp_path / "new.txt"
        src.write_text("replacement")
        tx.stage_file(src, dst)
        tx.rollback()
        # File should still have original content (rollback before commit)
        assert dst.read_text() == "original"

    def test_happy_lock_released_after_commit(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        src = tmp_path / "s.txt"
        src.write_text("data")
        dst = tmp_path / "d.txt"
        tx.stage_file(src, dst)
        tx.commit()
        # Lock should be released - we can acquire it again
        tx2 = Transaction(state_root, operation="test2")
        tx2.begin()
        tx2.close()


class TestJournal:
    def test_happy_journal_roundtrip(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="init")
        tx.begin()
        tx.close()
        # Read back
        journal_path = state_root / "journals" / f"{tx.id}.json"
        j = read_journal(journal_path)
        assert j is not None
        assert j.id == tx.id
        assert j.operation == "init"
        assert j.phase == JournalPhase.PREPARED

    def test_happy_journal_to_dict(self) -> None:
        j = Journal(
            id="test-id",
            operation="init",
            phase=JournalPhase.PREPARED,
            resources=[ResourceEntry(path="/tmp/test", existed=True)],
            pid=1234,
            host="testhost",
            timestamp="2026-01-01T00:00:00",
        )
        d = journal_to_dict(j)
        assert d["id"] == "test-id"
        assert d["phase"] == "prepared"
        resources = _RESOURCES_ADAPTER.validate_python(d["resources"])
        assert resources[0]["path"] == "/tmp/test"

    def test_happy_read_missing_journal(self, tmp_path: Path) -> None:
        result = read_journal(tmp_path / "nonexistent.json")
        assert result is None

    def test_golden_journal_bytes_exact_key_order(self, state_root: Path) -> None:
        """Golden write-side shape: raw journal file bytes pin the exact key order.

        Journal keys: id, operation, phase, resources, affected_profiles, pid,
        host, timestamp.
        Resource keys: path, preimage_hash, postimage_hash, existed, mode.
        ``phase`` is serialized as its ``.value`` string.
        """
        j = Journal(
            id="golden-id",
            operation="init",
            phase=JournalPhase.PREPARED,
            resources=[ResourceEntry(path="/tmp/golden.txt", existed=True, mode=420)],
            affected_profiles=["work"],
            pid=1234,
            host="testhost",
            timestamp="2026-01-01T00:00:00",
        )
        journal_path = state_root / "journals" / "golden.json"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(json.dumps(journal_to_dict(j), indent=2))
        golden = (
            "{\n"
            '  "id": "golden-id",\n'
            '  "operation": "init",\n'
            '  "phase": "prepared",\n'
            '  "resources": [\n'
            "    {\n"
            '      "path": "/tmp/golden.txt",\n'
            '      "preimage_hash": null,\n'
            '      "postimage_hash": null,\n'
            '      "existed": true,\n'
            '      "mode": 420\n'
            "    }\n"
            "  ],\n"
            '  "affected_profiles": [\n'
            '    "work"\n'
            "  ],\n"
            '  "pid": 1234,\n'
            '  "host": "testhost",\n'
            '  "timestamp": "2026-01-01T00:00:00"\n'
            "}"
        )
        assert journal_path.read_bytes() == golden.encode()


class TestPendingJournals:
    def test_happy_no_pending(self, state_root: Path) -> None:
        state_root.mkdir(parents=True, exist_ok=True)
        pending = find_pending_journals(state_root)
        assert pending == []

    def test_happy_finds_pending(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        # Don't close - leave it pending
        pending = find_pending_journals(state_root)
        assert len(pending) == 1
        assert pending[0].operation == "test"
        tx.close()


class TestPrivateModes:
    def test_happy_state_root_0700(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        tx.close()
        mode = state_root.stat().st_mode & 0o777
        assert mode == 0o700

    def test_happy_journal_0600(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        tx.close()
        journal_path = state_root / "journals" / f"{tx.id}.json"
        mode = journal_path.stat().st_mode & 0o777
        assert mode == 0o600


class TestLockContention:
    def test_failure_lock_held_by_other(self, state_root: Path) -> None:
        # First transaction holds the lock
        tx1 = Transaction(state_root, operation="first")
        tx1.begin()
        # Second should fail
        tx2 = Transaction(state_root, operation="second")
        with pytest.raises(MaintenanceError, match="lock"):
            tx2.begin()
        tx1.close()
