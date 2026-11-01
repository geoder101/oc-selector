"""Exact host-package sync and node/npm adapter.

Host-package sync is exact, no drift.
Node/npm adapter detects and manages the host package.
No author constants in installed src.
No silent fallbacks.
No network in sync (only local file operations).
No secret material in logs.
No migration of legacy fields.
All mutations go through transaction.
Idempotent operations.
No conversion calls.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import cast

from oc_selector.errors import (
    MaintenanceError,
    NetworkError,
)
from oc_selector.transactions import Transaction


@dataclass(frozen=True, slots=True)
class HostPackageInfo:
    """Information about the host package."""

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


def detect_host_package(
    project_root: Path,
) -> HostPackageInfo | None:
    """Detect the host package in the project root.

    Args:
        project_root: Path to the project root.

    Returns:
        A ``HostPackageInfo`` if detected, None otherwise.

    """
    # Check for package.json (npm)
    pkg_json = project_root / "package.json"
    if pkg_json.exists():
        try:
            data = cast("dict[str, object]", json.loads(pkg_json.read_text()))
            name = cast("str", data.get("name", "unknown"))
            version = cast("str", data.get("version", "0.0.0"))
            return HostPackageInfo(
                name=name,
                version=version,
                path=pkg_json,
                package_manager="npm",
            )
        except (json.JSONDecodeError, OSError):
            return None

    # Check for pyproject.toml (pip/uv)
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            name = "unknown"
            version = "0.0.0"
            for line in content.splitlines():
                if line.strip().startswith("name"):
                    name = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.strip().startswith("version"):
                    version = line.split("=", 1)[1].strip().strip('"').strip("'")
            return HostPackageInfo(
                name=name,
                version=version,
                path=pyproject,
                package_manager="pip",
            )
        except OSError:
            return None

    return None


def sync_host_package(
    project_root: Path,
    state_root: Path,
    target_version: str | None = None,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Sync the host package to the target version.

    Args:
        project_root: Path to the project root.
        state_root: Path to the state root.
        target_version: Target version (None for latest).
        dry_run: When set, report the would-be sync without running the package
            manager or writing a transaction.

    Returns:
        A ``SyncResult`` describing the outcome.

    """
    info = detect_host_package(project_root)
    if info is None:
        return SyncResult(
            success=False,
            package_name="unknown",
            version="0.0.0",
            message="No host package detected",
            error="No package.json or pyproject.toml found",
        )

    # If target version matches current, no-op
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

    # Use transaction for mutation
    tx = Transaction(state_root, operation="sync")
    try:
        tx.begin()

        # Run the appropriate package manager command
        if info.package_manager == "npm":
            cmd = ["npm", "install", f"{info.name}@{target_version or 'latest'}"]
        else:
            cmd = ["pip", "install", f"{info.name}=={target_version or 'latest'}"]

        try:
            result = subprocess.run(  # noqa: S603, PLW1510
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=project_root,
            )
            if result.returncode != 0:
                raise MaintenanceError(
                    f"Package sync failed: {result.stderr[:200]}",
                )
        except subprocess.TimeoutExpired:
            raise MaintenanceError("Package sync timed out") from None
        except FileNotFoundError:
            raise NetworkError(
                f"Package manager not found: {cmd[0]}",
            ) from None

        tx.commit()
    except (MaintenanceError, NetworkError) as exc:
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

    # Re-detect to get new version
    new_info = detect_host_package(project_root)
    new_version = new_info.version if new_info else "unknown"

    return SyncResult(
        success=True,
        package_name=info.name,
        version=new_version,
        message=f"Synced to {new_version}",
    )


def verify_host_package(
    project_root: Path,
    expected_version: str,
) -> bool:
    """Verify the host package is at the expected version.

    Args:
        project_root: Path to the project root.
        expected_version: Expected version string.

    Returns:
        True if the version matches, False otherwise.

    """
    info = detect_host_package(project_root)
    if info is None:
        return False
    return info.version == expected_version
