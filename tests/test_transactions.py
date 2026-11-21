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
import sys
from pathlib import Path  # noqa: TC003
from typing import override

import pytest
from pydantic import TypeAdapter

from oc_selector.errors import MaintenanceError
from oc_selector.transactions import (
    Journal,
    JournalPhase,
    ResourceEntry,
    Transaction,
    _fsync_dir,
    find_pending_journals,
    journal_to_dict,
    read_journal,
)

_RESOURCES_ADAPTER: TypeAdapter[list[dict[str, object]]] = TypeAdapter(
    list[dict[str, object]],
)


class _RecordingTransaction(Transaction):
    """Transaction that records each journal-write phase for assertions."""

    def __init__(self, state_root: Path, operation: str) -> None:
        super().__init__(state_root, operation)
        self.recorded_phases: list[JournalPhase] = []

    @override
    def _write_journal(self) -> None:
        self.recorded_phases.append(self.phase)
        super()._write_journal()


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

    def test_happy_resources_property_returns_copy(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """The resources property returns a defensive copy of tracked entries."""
        tx = Transaction(state_root, operation="test")
        tx.begin()
        src = tmp_path / "source.txt"
        src.write_text("hello")
        dst = tmp_path / "dest.txt"
        tx.stage_file(src, dst)
        resources = tx.resources
        assert len(resources) == 1
        assert resources[0].path == str(dst)
        assert resources[0].existed is False
        resources.clear()
        assert len(tx.resources) == 1
        tx.rollback()

    def test_failure_commit_refuses_missing_staged_file(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Lost staging must not be reported as a successful commit."""
        tx = Transaction(state_root, operation="test")
        tx.begin()
        src = tmp_path / "source.txt"
        src.write_text("hello")
        dst = tmp_path / "dest.txt"
        tx.stage_file(src, dst)
        # Simulate staging loss (partial crash) before commit.
        for staged in (state_root / "staging" / tx.id).iterdir():
            staged.unlink()
        with pytest.raises(FileNotFoundError):
            tx.commit()
        tx.rollback()
        assert not dst.exists()

    def test_happy_rollback_restores_backup(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Rollback restores the preimage from the private backup when present."""
        dst = tmp_path / "existing.txt"
        dst.write_text("original")
        tx = Transaction(state_root, operation="test")
        tx.begin()
        src = tmp_path / "new.txt"
        src.write_text("replacement")
        tx.stage_file(src, dst)
        # Simulate a partial apply: the destination now holds the new content.
        dst.write_text("replacement")
        tx.rollback()
        assert dst.read_text() == "original"

    def test_happy_rollback_removes_created_file(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Rollback unlinks a destination that did not exist before staging."""
        tx = Transaction(state_root, operation="test")
        tx.begin()
        src = tmp_path / "new.txt"
        src.write_text("fresh")
        dst = tmp_path / "created.txt"
        tx.stage_file(src, dst)
        # Simulate a crash after a partial apply wrote the destination.
        dst.write_text("fresh")
        tx.rollback()
        assert not dst.exists()

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

    def test_happy_journal_to_dict_carries_symlink_target(self) -> None:
        """A symlink resource carries symlink_target; byte-copy entries do not."""
        j = Journal(
            id="test-id",
            operation="link",
            phase=JournalPhase.PREPARED,
            resources=[
                ResourceEntry(
                    path="/tmp/agents",
                    existed=True,
                    symlink_target="/tmp/target",
                ),
                ResourceEntry(path="/tmp/plain", existed=False),
            ],
            pid=1234,
            host="testhost",
            timestamp="2026-01-01T00:00:00",
        )
        d = journal_to_dict(j)
        assert d["id"] == "test-id"
        resources = _RESOURCES_ADAPTER.validate_python(d["resources"])
        assert resources[0]["symlink_target"] == "/tmp/target"
        assert "symlink_target" not in resources[1]

    def test_happy_read_missing_journal(self, tmp_path: Path) -> None:
        result = read_journal(tmp_path / "nonexistent.json")
        assert result is None

    def test_happy_read_invalid_journal_returns_none(self, state_root: Path) -> None:
        """A journal file with invalid JSON is treated as unreadable (None)."""
        journals = state_root / "journals"
        journals.mkdir(parents=True)
        bad = journals / "bad.json"
        bad.write_text("{not json")
        assert read_journal(bad) is None

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

    def test_happy_ignores_non_json_files(self, state_root: Path) -> None:
        """Non-.json files in the journals directory are ignored."""
        journals = state_root / "journals"
        journals.mkdir(parents=True)
        (journals / "notes.txt").write_text("not a journal")
        assert find_pending_journals(state_root) == []


class TestPrivateModes:
    def test_happy_state_root_0700(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        tx.close()
        # Windows st_mode does not reflect POSIX permission bits; the
        # mode-bit equality is only meaningful on POSIX.
        if sys.platform != "win32":
            mode = state_root.stat().st_mode & 0o777
            assert mode == 0o700

    def test_happy_journal_0600(self, state_root: Path) -> None:
        tx = Transaction(state_root, operation="test")
        tx.begin()
        tx.close()
        journal_path = state_root / "journals" / f"{tx.id}.json"
        # Windows st_mode does not reflect POSIX permission bits; the
        # mode-bit equality is only meaningful on POSIX.
        if sys.platform != "win32":
            mode = journal_path.stat().st_mode & 0o777
            assert mode == 0o600


class TestLockContention:
    def test_failure_lock_held_by_other(self, state_root: Path) -> None:
        # Cross-platform: portalocker LOCK_EX | LOCK_NB maps to flock(2) on
        # POSIX and LockFileEx(LOCKFILE_FAIL_IMMEDIATELY) on Windows; both
        # fail immediately when a second open file description holds the
        # exclusive lock, so no platform skip is required.
        # First transaction holds the lock
        tx1 = Transaction(state_root, operation="first")
        tx1.begin()
        # Second should fail
        tx2 = Transaction(state_root, operation="second")
        with pytest.raises(MaintenanceError, match="lock"):
            tx2.begin()
        tx2.rollback()
        tx2.close()
        tx1.close()


class TestFsyncDir:
    """Directory fsync helper tolerates unsupported platforms."""

    def test_failure_missing_path_reported(self, tmp_path: Path) -> None:
        """Real directory flush failures are reported on supporting platforms."""
        if sys.platform == "win32":
            assert _fsync_dir(tmp_path / "missing") is None
        else:
            with pytest.raises(FileNotFoundError):
                _fsync_dir(tmp_path / "missing")


class TestSymlinkTransactions:
    """stage_symlink / commit / rollback for symlink resources."""

    def test_happy_symlink_commits_as_symlink(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """A staged symlink commits as a symlink pointing at the target."""
        tx = Transaction(state_root, operation="test")
        tx.begin()
        target = tmp_path / "target.txt"
        target.write_text("hello")
        dst = tmp_path / "existing_dir" / "AGENTS.md"
        dst.parent.mkdir(parents=True)
        tx.stage_symlink(dst, target)
        tx.commit()
        assert dst.is_symlink() is True
        assert str(dst.readlink()) == str(target)

    def test_happy_symlink_rollback_removes_link_and_created_dir(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Rollback after a fresh create removes the link AND the dir it made."""
        tx = Transaction(state_root, operation="test")
        tx.begin()
        target = tmp_path / "target.txt"
        target.write_text("hello")
        dst = tmp_path / "brand_new_dir" / "AGENTS.md"
        assert not dst.parent.exists()
        tx.stage_symlink(dst, target)
        tx.commit()
        assert dst.parent.exists()
        tx.rollback()
        assert not dst.exists()
        assert not dst.parent.exists()

    def test_happy_symlink_rollback_restores_preexisting_symlink(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Rollback restores a pre-existing symlink to its old target."""
        old_target = tmp_path / "old_target.txt"
        old_target.write_text("old")
        dst = tmp_path / "dir" / "AGENTS.md"
        dst.parent.mkdir(parents=True)
        dst.symlink_to(str(old_target))
        tx = Transaction(state_root, operation="test")
        tx.begin()
        new_target = tmp_path / "new_target.txt"
        new_target.write_text("new")
        tx.stage_symlink(dst, new_target)
        tx.commit()
        assert str(dst.readlink()) == str(new_target)
        tx.rollback()
        assert dst.is_symlink() is True
        assert str(dst.readlink()) == str(old_target)

    def test_happy_symlink_rollback_preserves_preexisting_real_file(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Commit never removes a real file; rollback leaves it intact."""
        dst = tmp_path / "dir" / "AGENTS.md"
        dst.parent.mkdir(parents=True)
        dst.write_text("real content")
        tx = Transaction(state_root, operation="test")
        tx.begin()
        target = tmp_path / "target.txt"
        target.write_text("t")
        tx.stage_symlink(dst, target)
        with pytest.raises(FileExistsError):
            tx.commit()
        tx.rollback()
        assert dst.is_symlink() is False
        assert dst.read_text() == "real content"

    def test_happy_symlink_rollback_keeps_nonempty_dir(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """A tracked dir that is not empty is left intact on rollback."""
        tx = Transaction(state_root, operation="test")
        tx.begin()
        target = tmp_path / "target.txt"
        target.write_text("t")
        dst = tmp_path / "newdir" / "AGENTS.md"
        tx.stage_symlink(dst, target)
        tx.commit()
        (dst.parent / "other.txt").write_text("x")
        tx.rollback()
        assert not dst.exists()
        assert dst.parent.exists()
        assert (dst.parent / "other.txt").read_text() == "x"

    def test_happy_symlink_journal_roundtrip(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """A symlink resource survives a journal write/read with symlink_target."""
        tx = Transaction(state_root, operation="link")
        tx.begin()
        target = tmp_path / "target.txt"
        target.write_text("t")
        dst = tmp_path / "dir" / "AGENTS.md"
        tx.stage_symlink(dst, target)
        tx.commit()
        journal_path = state_root / "journals" / f"{tx.id}.json"
        j = read_journal(journal_path)
        assert j is not None
        assert len(j.resources) == 1
        res = j.resources[0]
        assert res.symlink_target == str(target)
        assert res.existed is False
        assert res.path == str(dst)

    def test_happy_commit_journal_phase_sequence(
        self,
        state_root: Path,
        tmp_path: Path,
    ) -> None:
        """Journal phase sequence for a symlink commit.

        Pins the module's documented state machine so a stray tail leaking
        into a helper (double COMMITTED/CLEANUP_COMPLETE) is caught.
        """
        tx = _RecordingTransaction(state_root, operation="link")
        tx.begin()
        target = tmp_path / "target.txt"
        target.write_text("t")
        dst = tmp_path / "dir" / "AGENTS.md"
        tx.stage_symlink(dst, target)
        tx.commit()
        assert tx.recorded_phases == [
            JournalPhase.PREPARED,
            JournalPhase.APPLYING,
            JournalPhase.COMMITTED,
            JournalPhase.CLEANUP_COMPLETE,
        ]
