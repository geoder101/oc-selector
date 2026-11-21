"""Install an explicitly declared host dependency in a private workspace.

An npm profile declares exactly one host in ``dependencies``; the workspace's
own name is never an install target. Python profiles declare their distribution
in ``[project]`` and install into the profile-local ``python-packages`` tree.
Only verified package-manager output is published through a transaction.
There is no direct network I/O here; package managers perform downloads.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass
from email.parser import BytesParser
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from oc_selector.errors import MaintenanceError
from oc_selector.package_stage import stage_tree
from oc_selector.transaction_io import atomic_write
from oc_selector.transactions import Transaction


class PackageJson(BaseModel):
    """Parse the dependency declaration without treating root metadata as a host."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    dependencies: dict[str, str]


@dataclass(frozen=True, slots=True)
class HostPackageInfo:
    """An explicitly declared distribution and its installed version."""

    name: str
    version: str
    path: Path
    package_manager: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Result of a host-package sync operation."""

    success: bool
    package_name: str
    version: str
    message: str
    error: str | None = None


def _installed_version(root: Path, name: str, manager: str) -> str:
    """Read installed package metadata, never the workspace's own version."""
    if manager == "npm":
        path = root / "node_modules" / name / "package.json"
        if not path.exists():
            return "unknown"
        data = TypeAdapter(dict[str, object]).validate_json(path.read_bytes())
        if data.get("name") == name:
            version = data.get("version")
            if isinstance(version, str):
                return version
    else:
        normalized = name.lower().replace("_", "-")
        for path in (root / "python-packages").glob("*.dist-info/METADATA"):
            metadata = BytesParser().parsebytes(path.read_bytes())
            if str(metadata.get("Name", "")).lower().replace("_", "-") == normalized:
                return str(metadata.get("Version", "unknown"))
    return "unknown"


def detect_host_package(project_root: Path) -> HostPackageInfo | None:
    """Detect an unambiguous host declaration and read its installed version."""
    try:
        path = project_root / "package.json"
        if path.exists():
            manifest = PackageJson.model_validate_json(path.read_bytes())
            if len(manifest.dependencies) != 1:
                return None
            name = next(iter(manifest.dependencies))
            manager = "npm"
        else:
            path = project_root / "pyproject.toml"
            data = tomllib.loads(path.read_text())
            project = TypeAdapter(dict[str, object]).validate_python(
                data.get("project")
            )
            name_value = project.get("name")
            if not isinstance(name_value, str) or not name_value:
                return None
            name = name_value
            manager = "pip"
        return HostPackageInfo(
            name,
            _installed_version(project_root, name, manager),
            path,
            manager,
        )
    except OSError, ValueError, ValidationError:
        return None


def _install(cmd: list[str], workspace: Path) -> None:
    """Run the isolated installer with classified errors and a bounded timeout."""
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=workspace,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise MaintenanceError("Package sync timed out") from None
    except FileNotFoundError:
        raise MaintenanceError(f"Package manager not found: {cmd[0]}") from None
    if result.returncode != 0:
        raise MaintenanceError(f"Package sync failed: {result.stderr[:200]}")


def _prepare_install(
    info: HostPackageInfo,
    workspace: Path,
    target: str | None,
) -> list[str]:
    """Select explicit profile-local installation destinations."""
    if info.package_manager == "npm":
        atomic_write(workspace / "package.json", info.path.read_bytes())
        return [
            "npm",
            "install",
            "--ignore-scripts",
            "--save-exact",
            f"{info.name}@{target or 'latest'}",
        ]
    requirement = f"{info.name}=={target}" if target else info.name
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--target",
        str(workspace / "python-packages"),
        requirement,
    ]


def _verify_install(workspace: Path, info: HostPackageInfo, target: str | None) -> str:
    """Refuse missing metadata or an installer that did not produce the target."""
    version = _installed_version(workspace, info.name, info.package_manager)
    if version == "unknown" or (target is not None and version != target):
        raise MaintenanceError(f"Installed version verification failed: {version}")
    return version


def _locked_host(project_root: Path) -> HostPackageInfo:
    """Re-read the declaration under the transaction's maintenance lock."""
    info = detect_host_package(project_root)
    if info is None:
        raise MaintenanceError("Host package declaration changed before sync")
    return info


def sync_host_package(
    project_root: Path,
    state_root: Path,
    target_version: str | None = None,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Install, verify, then transactionally publish a profile-local package."""
    info = detect_host_package(project_root)
    if info is None:
        return SyncResult(
            success=False,
            package_name="unknown",
            version="unknown",
            message="No host package detected",
            error=(
                "Declare one npm dependency or a Python [project] name"
                if (project_root / "package.json").exists()
                or (project_root / "pyproject.toml").exists()
                else "No package.json or pyproject.toml found"
            ),
        )
    if target_version is not None and target_version == info.version:
        return SyncResult(
            success=True,
            package_name=info.name,
            version=info.version,
            message="Already at target version",
        )
    if dry_run:
        return SyncResult(
            success=True,
            package_name=info.name,
            version=info.version,
            message=f"Dry run: would sync {info.name} to {target_version or 'latest'}",
        )
    tx = Transaction(state_root, operation="sync")
    try:
        tx.begin()
        info = _locked_host(project_root)
        workspace = state_root / "staging" / tx.id / "workspace"
        workspace.mkdir(parents=True)
        cmd = _prepare_install(info, workspace, target_version)
        _install(cmd, workspace)
        version = _verify_install(workspace, info, target_version)
        tree = "node_modules" if info.package_manager == "npm" else "python-packages"
        stage_tree(tx, workspace / tree, project_root / tree)
        if info.package_manager == "npm":
            for filename in ("package.json", "package-lock.json"):
                source = workspace / filename
                if source.exists():
                    tx.stage_file(source, project_root / filename)
        tx.commit()
    except (OSError, MaintenanceError, ValueError) as exc:
        tx.rollback()
        return SyncResult(
            success=False,
            package_name=info.name,
            version=info.version,
            message="Sync failed",
            error=str(exc),
        )
    finally:
        tx.close()
    return SyncResult(
        success=True,
        package_name=info.name,
        version=version,
        message=f"Synced to {version}",
    )


def verify_host_package(project_root: Path, expected_version: str) -> bool:
    """Verify installed metadata against an expected exact version."""
    info = detect_host_package(project_root)
    return info is not None and info.version == expected_version
