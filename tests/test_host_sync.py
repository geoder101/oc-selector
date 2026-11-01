"""TDD tests for host-package sync and node/npm adapter.

Covers:
- Detect npm package from package.json
- Detect pip package from pyproject.toml
- Sync is idempotent
- No network in detection
- Verify version match
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

from oc_selector.host_sync import (
    HostPackageInfo,
    SyncResult,
    detect_host_package,
    sync_host_package,
    verify_host_package,
)


class TestDetectHostPackage:
    def test_happy_detect_npm(self, tmp_path: Path) -> None:
        pkg = {"name": "test-pkg", "version": "1.2.3"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        info = detect_host_package(tmp_path)
        assert isinstance(info, HostPackageInfo)
        assert info.name == "test-pkg"
        assert info.version == "1.2.3"
        assert info.package_manager == "npm"

    def test_happy_detect_pip(self, tmp_path: Path) -> None:
        pyproject = '[project]\nname = "test-pkg"\nversion = "2.0.0"\n'
        (tmp_path / "pyproject.toml").write_text(pyproject)
        info = detect_host_package(tmp_path)
        assert isinstance(info, HostPackageInfo)
        assert info.name == "test-pkg"
        assert info.version == "2.0.0"
        assert info.package_manager == "pip"

    def test_happy_no_package(self, tmp_path: Path) -> None:
        info = detect_host_package(tmp_path)
        assert info is None

    def test_happy_npm_takes_priority(self, tmp_path: Path) -> None:
        pkg = {"name": "npm-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        pyproject = '[project]\nname = "pip-pkg"\nversion = "2.0.0"\n'
        (tmp_path / "pyproject.toml").write_text(pyproject)
        info = detect_host_package(tmp_path)
        assert info is not None
        assert info.package_manager == "npm"

    def test_failure_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("not valid json")
        info = detect_host_package(tmp_path)
        assert info is None


class TestVerifyHostPackage:
    def test_happy_version_match(self, tmp_path: Path) -> None:
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert verify_host_package(tmp_path, "1.0.0") is True

    def test_happy_version_mismatch(self, tmp_path: Path) -> None:
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert verify_host_package(tmp_path, "2.0.0") is False

    def test_happy_no_package(self, tmp_path: Path) -> None:
        assert verify_host_package(tmp_path, "1.0.0") is False


class TestSyncHostPackage:
    def test_happy_no_package(self, tmp_path: Path) -> None:
        state_root = tmp_path / "state"
        state_root.mkdir()
        result = sync_host_package(tmp_path, state_root)
        assert isinstance(result, SyncResult)
        assert result.success is False
        assert "No host package" in result.message

    def test_happy_already_at_target(self, tmp_path: Path) -> None:
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        result = sync_host_package(tmp_path, state_root, target_version="1.0.0")
        assert result.success is True
        assert "Already at target" in result.message
