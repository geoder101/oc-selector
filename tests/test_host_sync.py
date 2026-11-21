"""Exercise isolated installation, verification and transactional publication."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from oc_selector.host_sync import (
    detect_host_package,
    sync_host_package,
    verify_host_package,
)
from oc_selector.transactions import Transaction, find_pending_journals

pytestmark = pytest.mark.usefixtures("isolated_home")


@pytest.fixture
def npm_profile(tmp_path: Path) -> Path:
    """Declare a dependency distinct from the workspace's own package name."""
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "workspace",
                "version": "99.0.0",
                "dependencies": {"host-package": "1.0.0"},
            }
        )
    )
    installed = tmp_path / "node_modules" / "host-package"
    installed.mkdir(parents=True)
    (installed / "package.json").write_text(
        json.dumps(
            {
                "name": "host-package",
                "version": "1.0.0",
            }
        )
    )
    (installed / "obsolete.js").write_text("old")
    return tmp_path


def _npm_output(workspace: Path) -> None:
    """Write representative npm output in the installer working directory."""
    package = workspace / "node_modules" / "host-package"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "host-package",
                "version": "2.0.0",
            }
        )
    )
    (package / "index.js").write_text("new")
    (workspace / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"host-package": "2.0.0"},
            }
        )
    )
    (workspace / "package-lock.json").write_text('{"lockfileVersion": 3}')


def test_detect_installed_dependency(npm_profile: Path) -> None:
    """Root package name/version are not installed dependency metadata."""
    info = detect_host_package(npm_profile)
    assert info is not None
    assert info.name == "host-package"
    assert info.version == "1.0.0"
    assert verify_host_package(npm_profile, "1.0.0")
    assert not verify_host_package(npm_profile, "99.0.0")


@pytest.mark.parametrize(
    "manifest",
    [
        "not json",
        '{"name":"workspace","version":"1.0.0"}',
        '{"dependencies":{}}',
        '{"dependencies":{"a":"1","b":"2"}}',
    ],
)
def test_reject_ambiguous_manifest(tmp_path: Path, manifest: str) -> None:
    """Never guess an install target from incomplete workspace metadata."""
    (tmp_path / "package.json").write_text(manifest)
    assert detect_host_package(tmp_path) is None


def test_no_package(tmp_path: Path) -> None:
    """Absent manifests are a classified sync failure."""
    assert not sync_host_package(tmp_path, tmp_path / "state").success


def test_dry_run_and_exact_noop(npm_profile: Path) -> None:
    """A preview and an already-installed version produce no state writes."""
    state = npm_profile / "state"
    assert sync_host_package(npm_profile, state, dry_run=True).success
    assert sync_host_package(npm_profile, state, "1.0.0").success
    assert not state.exists()


@pytest.fixture
def npm_install(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Simulate npm writing files in its actual supplied working directory."""
    commands: list[list[str]] = []

    def install(
        cmd: list[str], *, cwd: Path, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        _npm_output(cwd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("oc_selector.host_sync.subprocess.run", install)
    return commands


def test_npm_verified_publication(
    npm_profile: Path, npm_install: list[list[str]]
) -> None:
    """Publish verified artifacts, exact pins and deletions through the journal."""
    state = npm_profile / "state"
    result = sync_host_package(npm_profile, state, "2.0.0")
    assert result.success
    assert result.version == "2.0.0"
    assert npm_install == [
        ["npm", "install", "--ignore-scripts", "--save-exact", "host-package@2.0.0"]
    ]
    assert verify_host_package(npm_profile, "2.0.0")
    assert not (npm_profile / "node_modules/host-package/obsolete.js").exists()
    assert (npm_profile / "node_modules/host-package/index.js").read_text() == "new"
    assert find_pending_journals(state) == []


def test_installer_partial_failure_preserves_profile(
    npm_profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manager that mutates files and then fails only dirties its workspace."""
    before = (npm_profile / "package.json").read_bytes()

    def install(
        cmd: list[str], *, cwd: Path, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        _npm_output(cwd)
        return subprocess.CompletedProcess(cmd, 1, "", "failed after writes")

    monkeypatch.setattr("oc_selector.host_sync.subprocess.run", install)
    result = sync_host_package(npm_profile, npm_profile / "state", "2.0.0")
    assert not result.success
    assert (npm_profile / "package.json").read_bytes() == before
    assert verify_host_package(npm_profile, "1.0.0")


def test_publication_failure_restores_all_resources(
    npm_profile: Path,
    npm_install: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An apply failure after deletion restores the previous installed tree."""
    assert not npm_install
    original = Transaction.commit

    def fail(tx: Transaction) -> None:
        # Remove one real destination, then fail while publishing another.
        original_write = Path.replace

        def replace(path: Path, target: Path) -> Path:
            if target == npm_profile / "package.json":
                raise OSError("injected publish failure")
            return original_write(path, target)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "replace", replace)
            original(tx)

    monkeypatch.setattr(Transaction, "commit", fail)
    result = sync_host_package(npm_profile, npm_profile / "state", "2.0.0")
    assert not result.success
    assert verify_host_package(npm_profile, "1.0.0")
    assert (npm_profile / "node_modules/host-package/obsolete.js").read_text() == "old"
    assert not (npm_profile / "node_modules/host-package/index.js").exists()


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), subprocess.TimeoutExpired("npm", 300)]
)
def test_installer_errors(
    npm_profile: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Missing managers and timeouts leave the installation untouched."""

    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    monkeypatch.setattr("oc_selector.host_sync.subprocess.run", fail)
    result = sync_host_package(npm_profile, npm_profile / "state")
    assert not result.success
    assert verify_host_package(npm_profile, "1.0.0")


def test_python_latest_is_profile_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the current interpreter, valid upgrade syntax and an isolated target."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="host-package"\nversion="99.0.0"\n'
    )
    commands: list[list[str]] = []

    def install(
        cmd: list[str], *, cwd: Path, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        metadata = cwd / "python-packages/host_package-2.0.0.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: host-package\nVersion: 2.0.0\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("oc_selector.host_sync.subprocess.run", install)
    result = sync_host_package(tmp_path, tmp_path / "state")
    assert result.success
    assert result.version == "2.0.0"
    assert commands[0][:5] == [sys.executable, "-m", "pip", "install", "--upgrade"]
    assert commands[0][-1] == "host-package"
    assert "--target" in commands[0]
    assert verify_host_package(tmp_path, "2.0.0")


def test_missing_installed_output_is_failure(
    npm_profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero exit status alone is insufficient proof of a successful install."""

    def install(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("oc_selector.host_sync.subprocess.run", install)
    result = sync_host_package(npm_profile, npm_profile / "state")
    assert not result.success
    assert verify_host_package(npm_profile, "1.0.0")
