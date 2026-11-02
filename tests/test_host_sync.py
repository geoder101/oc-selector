"""TDD tests for host-package sync and node/npm adapter.

Covers:
- Detect npm package from package.json
- Detect pip package from pyproject.toml
- Sync is idempotent
- No network in detection
- Verify version match
- Subprocess sync path with fake npm/pip on PATH (success, failure, timeout,
  missing package manager, dry-run, re-detect fallback)
- Write-failure probe against a read-only destination
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from oc_selector.host_sync import (
    HostPackageInfo,
    SyncResult,
    detect_host_package,
    sync_host_package,
    verify_host_package,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest
    from helpers.process_harness import ProcessHarness

_JOURNAL_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


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

    def test_failure_pyproject_unreadable_returns_none(self, tmp_path: Path) -> None:
        """A pyproject.toml that cannot be read (a directory) is treated as absent."""
        (tmp_path / "pyproject.toml").mkdir()
        info = detect_host_package(tmp_path)
        assert info is None


class TestSyncHostPackageSubprocess:
    """Characterization of the package-manager subprocess path.

    ``sync_host_package`` invokes ``subprocess.run`` without an ``env=``
    argument, so it inherits the test process environment. The harness puts
    fake ``npm``/``pip`` executables in ``bin_dir`` and the test prepends that
    directory to ``PATH`` (test-side PATH injection; no product change).
    """

    def test_happy_npm_sync_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A successful npm install records argv and commits the transaction."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        fixture_executable("npm", stdout="added 1 package in 1s\n")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = sync_host_package(tmp_path, state_root, target_version="2.0.0")
        assert result.success is True
        assert result.package_name == "test-pkg"
        # The fake npm does not mutate the manifest, so re-detect reports 1.0.0.
        assert result.version == "1.0.0"
        assert result.message == "Synced to 1.0.0"
        record = process_harness.record_for("npm")
        argv = record["argv"]
        assert isinstance(argv, list)
        assert argv[1:] == ["install", "test-pkg@2.0.0"]
        journals = sorted((state_root / "journals").glob("*.json"))
        assert len(journals) == 1
        journal = _JOURNAL_ADAPTER.validate_python(
            json.loads(journals[0].read_text(encoding="utf-8")),
        )
        assert journal["phase"] == "cleanup-complete"

    def test_happy_pip_sync_success_latest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A successful pip install with no target version uses 'latest'."""
        pyproject = '[project]\nname = "test-pkg"\nversion = "2.0.0"\n'
        (tmp_path / "pyproject.toml").write_text(pyproject)
        state_root = tmp_path / "state"
        state_root.mkdir()
        fixture_executable("pip", stdout="Successfully installed test-pkg\n")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = sync_host_package(tmp_path, state_root)
        assert result.success is True
        assert result.package_name == "test-pkg"
        assert result.version == "2.0.0"
        record = process_harness.record_for("pip")
        argv = record["argv"]
        assert isinstance(argv, list)
        assert argv[1:] == ["install", "test-pkg==latest"]

    def test_happy_dry_run_reports_without_launch(
        self,
        tmp_path: Path,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """dry_run=True reports the would-be sync without running npm or writing."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        fixture_executable("npm")
        result = sync_host_package(tmp_path, state_root, dry_run=True)
        assert result.success is True
        assert result.message == "Dry run: would sync test-pkg to latest"
        # No launch and no transaction material.
        assert not (process_harness.records_dir / "npm.record.json").exists()
        assert not (state_root / "journals").exists()

    def test_happy_redetect_fallback_unknown(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
    ) -> None:
        """When the manifest disappears during sync, re-detect falls back to unknown."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        body = (
            "import os\n"
            "path = os.path.join(os.getcwd(), 'package.json')\n"
            "if os.path.exists(path):\n"
            "    os.remove(path)\n"
        )
        script = process_harness.bin_dir / "npm"
        script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        script.chmod(0o700)
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = sync_host_package(tmp_path, state_root, target_version="2.0.0")
        assert result.success is True
        assert result.version == "unknown"
        assert result.message == "Synced to unknown"

    def test_failure_npm_nonzero_exit_rolls_back(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A non-zero npm exit takes the documented sync-failure branch."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        fixture_executable(
            "npm",
            stderr="npm ERR! EACCES: permission denied\n",
            exit_code=1,
        )
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = sync_host_package(tmp_path, state_root, target_version="2.0.0")
        assert result.success is False
        assert result.message == "Sync failed"
        assert result.error is not None
        assert "Package sync failed" in result.error
        assert "EACCES" in result.error
        # Rollback leaves the journal in the prepared phase (recovery evidence).
        journals = sorted((state_root / "journals").glob("*.json"))
        assert len(journals) == 1
        journal = _JOURNAL_ADAPTER.validate_python(
            json.loads(journals[0].read_text(encoding="utf-8")),
        )
        assert journal["phase"] == "prepared"

    def test_failure_readonly_destination(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
    ) -> None:
        """A read-only sync destination makes the package manager fail (write probe)."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        # Fake npm that, like the real one, must write into the destination.
        body = (
            "import os, sys\n"
            "probe = os.path.join(os.getcwd(), '.npm-write-probe')\n"
            "try:\n"
            "    with open(probe, 'w', encoding='utf-8') as handle:\n"
            "        handle.write('x')\n"
            "    os.remove(probe)\n"
            "except OSError as exc:\n"
            "    sys.stderr.write(f'npm ERR! cannot write: {exc}\\n')\n"
            "    sys.exit(1)\n"
        )
        script = process_harness.bin_dir / "npm"
        script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        script.chmod(0o700)
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        try:
            tmp_path.chmod(0o500)  # read+execute only: the destination is unwritable
            result = sync_host_package(tmp_path, state_root, target_version="2.0.0")
        finally:
            tmp_path.chmod(0o700)
        assert result.success is False
        assert result.message == "Sync failed"
        assert result.error is not None
        assert result.error.startswith("Package sync failed:")
        assert "cannot write" in result.error

    def test_failure_package_manager_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
    ) -> None:
        """An npm absent from PATH maps to the documented not-found failure."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()
        # bin_dir holds no npm, so PATH resolution fails.
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = sync_host_package(tmp_path, state_root, target_version="2.0.0")
        assert result.success is False
        assert result.error == "Package manager not found: npm"

    def test_failure_sync_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A subprocess timeout maps to the documented timeout failure."""
        pkg = {"name": "test-pkg", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        state_root = tmp_path / "state"
        state_root.mkdir()

        def _timeout(
            *_args: object,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired("npm", 300)

        monkeypatch.setattr("oc_selector.host_sync.subprocess.run", _timeout)
        result = sync_host_package(tmp_path, state_root, target_version="2.0.0")
        assert result.success is False
        assert result.error == "Package sync timed out"
