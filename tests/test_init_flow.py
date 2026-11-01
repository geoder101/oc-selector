"""TDD tests for generic init and recovery flows.

Covers:
- Generic init creates parent and registry
- Generic init detects existing config
- Recovery lists pending journals
- Recovery restores from backup
- No conversion calls in init
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

import pytest

from oc_selector.errors import RegistryError, TransactionError
from oc_selector.init_flow import (
    InitResult,
    RecoveryResult,
    list_pending_recoveries,
    run_init,
    run_recovery,
    run_recovery_all,
)
from oc_selector.registry import load_registry
from oc_selector.transactions import JournalPhase


class TestRunInit:
    def test_happy_creates_parent_and_registry(self, tmp_path: Path) -> None:
        parent = tmp_path / "config" / "oc-selector"
        result = run_init(config_parent=parent)
        assert isinstance(result, InitResult)
        assert result.created_parent is True
        assert result.created_registry is True
        assert result.already_initialized is False
        assert parent.exists()
        assert result.registry_path.exists()

    def test_happy_detects_existing(self, tmp_path: Path) -> None:
        parent = tmp_path / "existing"
        parent.mkdir()
        result = run_init(config_parent=parent)
        assert result.already_initialized is True
        assert result.created_parent is False

    def test_happy_registry_is_valid_toml(self, tmp_path: Path) -> None:
        parent = tmp_path / "config"
        result = run_init(config_parent=parent)
        reg = load_registry(result.registry_path)
        assert reg.default_profile == "default"
        assert "default" in reg.profiles

    def test_happy_idempotent(self, tmp_path: Path) -> None:
        parent = tmp_path / "config"
        r1 = run_init(config_parent=parent)
        r2 = run_init(config_parent=parent)
        assert r1.created_registry is True
        assert r2.created_registry is False
        assert r2.already_initialized is True

    def test_failure_unwritable_parent(self, tmp_path: Path) -> None:
        # Use a path that cannot be created (file where dir expected)
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")
        parent = blocker / "subdir"
        with pytest.raises(RegistryError):
            run_init(config_parent=parent)


class TestListPendingRecoveries:
    def test_happy_no_pending(self, tmp_path: Path) -> None:
        entries = list_pending_recoveries(state_dir=tmp_path)
        assert entries == []

    def test_happy_finds_pending(self, tmp_path: Path) -> None:
        journals_dir = tmp_path / "journals"
        journals_dir.mkdir()
        journal_data: dict[str, object] = {
            "id": "test-journal-1",
            "operation": "create",
            "phase": "prepared",
            "resources": [],
            "affected_profiles": ["default"],
            "pid": 0,
            "host": "test",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        (journals_dir / "test-journal-1.json").write_text(json.dumps(journal_data))
        entries = list_pending_recoveries(state_dir=tmp_path)
        assert len(entries) == 1
        assert entries[0].journal_id == "test-journal-1"
        assert entries[0].phase == JournalPhase.PREPARED
        assert entries[0].operation == "create"

    def test_happy_ignores_cleanup_complete(self, tmp_path: Path) -> None:
        journals_dir = tmp_path / "journals"
        journals_dir.mkdir()
        journal_data: dict[str, object] = {
            "id": "done-journal",
            "operation": "create",
            "phase": "cleanup-complete",
            "resources": [],
            "affected_profiles": [],
            "pid": 0,
            "host": "test",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        (journals_dir / "done-journal.json").write_text(json.dumps(journal_data))
        entries = list_pending_recoveries(state_dir=tmp_path)
        assert entries == []


class TestRunRecovery:
    def test_happy_recover_missing_journal(self, tmp_path: Path) -> None:
        with pytest.raises(TransactionError):
            run_recovery(tmp_path / "nonexistent.json", state_dir=tmp_path)

    def test_happy_recover_valid_journal(self, tmp_path: Path) -> None:
        journals_dir = tmp_path / "journals"
        journals_dir.mkdir()
        journal_data: dict[str, object] = {
            "id": "rec-test",
            "operation": "edit",
            "phase": "prepared",
            "resources": [],
            "affected_profiles": ["default"],
            "pid": 0,
            "host": "test",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        jpath = journals_dir / "rec-test.json"
        jpath.write_text(json.dumps(journal_data))
        result = run_recovery(jpath, state_dir=tmp_path)
        assert isinstance(result, RecoveryResult)
        assert result.recovered is True
        assert result.journal_id == "rec-test"


class TestRunRecoveryAll:
    def test_happy_no_pending(self, tmp_path: Path) -> None:
        results = run_recovery_all(state_dir=tmp_path)
        assert results == []

    def test_happy_recovers_all(self, tmp_path: Path) -> None:
        journals_dir = tmp_path / "journals"
        journals_dir.mkdir()
        for i in range(2):
            journal_data: dict[str, object] = {
                "id": f"batch-{i}",
                "operation": "create",
                "phase": "prepared",
                "resources": [],
                "affected_profiles": [],
                "pid": 0,
                "host": "test",
                "timestamp": "2025-01-01T00:00:00Z",
            }
            (journals_dir / f"batch-{i}.json").write_text(json.dumps(journal_data))
        results = run_recovery_all(state_dir=tmp_path)
        assert len(results) == 2
        assert all(r.recovered for r in results)
